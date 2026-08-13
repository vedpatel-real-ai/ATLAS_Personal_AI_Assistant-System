"""
SQLite-backed storage for ATLAS — Milestone 0 (see ATLAS spec §10).

Two responsibilities, both closing gaps called out in the spec's §1.3 gap
analysis:

1. Structured, queryable logging. Replaces the unbounded, grep-only
   `assistant_log.jsonl` with a real `logs` table you can actually query
   ("what failed this week"). The JSONL file is still written alongside it
   as a cheap, optional live-tail stream (`tail -f assistant_log.jsonl`) —
   per the spec, SQLite and JSONL aren't mutually exclusive.

2. Server-side confirmation tokens. Previously, `confirmed: bool` was a
   field the *client* set, with no server-side proof a human actually
   tapped "confirm" — a compromised or buggy client could self-confirm a
   `dangerous`-tier action. Now, the first (unconfirmed) call to a
   confirmation-requiring command gets a short-lived, one-time,
   server-issued token bound to a hash of the exact steps shown to the
   user. The second (confirmed) call must present that same token, and the
   token is only accepted if the steps about to execute hash to the same
   value — so a stale token, a tampered step list, or a replayed token are
   all rejected.

Milestone 0.5 addition: `logs` gained a `level` column (L0–L4, see
decision_engine.py) so the Decision Engine's routing decisions are
queryable after the fact — spec §10's Milestone 0.5 checkpoint asks to
"verify by spot-checking a week of logged L0–L4 assignments against what
you'd have picked by hand." `query_level_distribution()` below is that
spot-check query. Migration is additive (ALTER TABLE ... ADD COLUMN,
guarded by a PRAGMA check) so it's safe to run against a pre-0.5
atlas.db with existing rows — old rows just get level = NULL.

Milestone 2 addition: three new tables backing memory.py's Context
Manager (spec §4) — `conversations` (rolling turn-by-turn history),
`projects` (durable version of tools.py's index_project() in-memory TTL
cache — "Milestone 2 (real memory) can replace this with something
durable later" per that module's own comment), and `preferences` (a
small durable key/value store, also used to remember which project path
is "current"). All three are brand-new tables (CREATE TABLE IF NOT
EXISTS is sufficient; no ALTER TABLE migration needed since no prior
release ever wrote to them). Nothing outside memory.py should query
these three tables directly — see memory.py's module docstring.

Milestone 3 addition (knowledge storage/cross-referencing, spec §8's last
Knowledge Companion slice): one new table, `knowledge`, recording each
summarize_document()/summarize_youtube_video() result — {path, source_type,
question, summary, extracted_at}. Plain SQLite + LIKE search (see
search_knowledge() below), deliberately not an embedding store — the
spec's own rule to justify RAM/complexity cost on a 16GB machine applies
directly here, and there's no usage data yet showing keyword search over
stored summaries is insufficient. tools.py is the only caller (it owns
both the writes, from the two summarize_* tools, and the read, via its
own search_knowledge tool) — same "one obvious place per table"
discipline as `confirmations` (owned by main.py) and
conversations/projects/preferences (owned by memory.py).

Milestone 5 addition (9B model evaluation, spec §5 "Model Strategy"): one
new table, `model_benchmarks`, recording each trial from
`benchmark_models.py` (project root) — {model, prompt_label, latency_s,
eval_count, eval_tokens_per_s, ollama_size_mb, ollama_size_vram_mb}. That
script, not this module, owns both the writes and the summary read — same
"one obvious place per table" discipline as `knowledge`. See
`benchmark_models.py`'s own docstring for why this is a standalone script
rather than a chat tool.

Milestone 5, session 2 correction: the first cut of this table had an
`ollama_peak_rss_mb` column populated by polling `psutil` for a process
named "ollama" on the machine running the benchmark. Real-machine testing
showed this column was silently wrong — it consistently read ~55-60MB
regardless of a 2.5GB or 5.2GB model, i.e. it was reading some other
"ollama"-named process's memory, not the model runner's. Replaced with
`ollama_size_mb`/`ollama_size_vram_mb`, sourced from Ollama's own
`/api/ps` endpoint (which reports each currently-loaded model's real size
and VRAM usage directly — no OS-level process-matching guesswork). The
old column is left in place (SQLite has no cheap column drop) but is no
longer written to; treat any pre-existing `ollama_peak_rss_mb` values in
an existing `atlas.db` as invalid and ignore them.

Milestone 5, session 3 addition: two new tables implementing the
"Benchmark Validation Report" methodology (hypothesis/method/environment/
measurements/results/limitations/conclusion) — `benchmark_runs` (one row
per `benchmark_models.py` invocation: which models, keep_alive/think
settings, an optional free-text workload note, and an optional manual
1-5 responsiveness rating) and `system_snapshots` (before/after readings
of swap, memory pressure, and CPU load — see `system_snapshot.py` for
why GPU/thermal aren't included). `model_benchmarks` gained a nullable
`run_id` column linking trials to a run; pre-session-3 trials have
`run_id = NULL` and are still valid data, just outside any run's report.

Milestone 5, session 4 correction: a real run exposed that session 3's
single before/after snapshot pair per run couldn't isolate which model
caused a given swap/pressure change when multiple models ran back-to-back
between those two snapshots — see benchmark_models.py's docstring. Fixed
by tagging snapshots 'before:<model>'/'after:<model>' (one pair per
model). Also added `system_snapshots.compressed_memory_mb` (leading
indicator swap alone can miss) and `benchmark_runs.responsiveness_label`
(the qualitative checklist — smooth/slight_lag/noticeable_lag/unusable —
replacing a bare 1-5 number nobody could remember the meaning of).
`responsiveness_rating` is kept as the numeric mapping of that label for
threshold math, not a second independent input.

Milestone 5.6 addition (Async Execution & Task Progress, spec's planner
async/progress layer): one new table, `jobs`, giving `/command/async`
(main.py) a durable record of a background job's status/progress that
survives process restarts — even though the actual execution thread does
NOT survive a restart (see jobs.py's module docstring for that
limitation). `steps_json`/`unknown_steps_json` let an
`awaiting_confirmation` job be resumed later by `/jobs/{id}/confirm`
without main.py needing to keep the pending step list anywhere in
memory. `progress_json` is a list of per-step outcomes (same shape as the
`results` list `/command` already returns), updated after each step so a
poller sees partial progress on a multi-step chain, not just a final
answer. Same "one obvious place per table, one obvious owner module"
discipline as `confirmations` (main.py) and `knowledge` (tools.py) —
main.py and jobs.py are the only callers here.
"""

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).parent / "atlas.db"
JSONL_PATH = Path(__file__).parent / "assistant_log.jsonl"  # optional live-tail debug stream

CONFIRMATION_TTL_SECONDS = 5 * 60  # a confirm prompt sitting unanswered longer than this expires

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Idempotent — safe to call on every process start."""
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                intent TEXT,
                route TEXT,
                tool TEXT,
                params_json TEXT,
                success INTEGER,
                latency_s REAL,
                ram_before_mb REAL,
                ram_after_mb REAL,
                error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_tool ON logs(tool)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_success ON logs(success)")

        # Milestone 0.5: add `level` (L0-L4) if upgrading from a pre-0.5 db.
        # SQLite has no "ALTER TABLE ... ADD COLUMN IF NOT EXISTS", so guard
        # it with a PRAGMA check instead of relying on a try/except around
        # a duplicate-column error.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(logs)").fetchall()}
        if "level" not in existing_cols:
            conn.execute("ALTER TABLE logs ADD COLUMN level TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS confirmations (
                token TEXT PRIMARY KEY,
                steps_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Milestone 2: conversation/project memory — see memory.py.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                project TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                path TEXT PRIMARY KEY,
                last_accessed TEXT NOT NULL,
                total_files INTEGER,
                total_size_mb REAL,
                languages_json TEXT,
                top_level_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_last_accessed ON projects(last_accessed)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Milestone 3: knowledge storage — see tools.py's summarize_document /
        # summarize_youtube_video / search_knowledge.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                source_type TEXT NOT NULL,
                question TEXT,
                summary TEXT NOT NULL,
                extracted_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_extracted_at ON knowledge(extracted_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_source_type ON knowledge(source_type)")

        # Milestone 5: 9B model evaluation — see benchmark_models.py (project
        # root, run manually, not a chat tool — see that file's own docstring
        # for why). One row per (model, prompt_label) trial.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_benchmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                prompt_label TEXT NOT NULL,
                latency_s REAL NOT NULL,
                eval_count INTEGER,
                eval_tokens_per_s REAL,
                ollama_peak_rss_mb REAL,
                success INTEGER NOT NULL,
                error TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_model_benchmarks_model ON model_benchmarks(model)")

        # Milestone 5, session 2: add ollama_size_mb/ollama_size_vram_mb if
        # upgrading from the first cut of this table (which only had the
        # since-deprecated, verified-wrong ollama_peak_rss_mb column — see
        # this module's docstring). Same guarded-ALTER-TABLE pattern as
        # logs.level above.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(model_benchmarks)").fetchall()}
        if "ollama_size_mb" not in existing_cols:
            conn.execute("ALTER TABLE model_benchmarks ADD COLUMN ollama_size_mb REAL")
        if "ollama_size_vram_mb" not in existing_cols:
            conn.execute("ALTER TABLE model_benchmarks ADD COLUMN ollama_size_vram_mb REAL")

        # Milestone 5, session 3: "Benchmark Validation Report" methodology
        # (spec'd by the user directly — hypothesis/method/environment/
        # measurements/results/limitations/conclusion, same structure as the
        # earlier GPU investigation this project referenced). A "run" groups
        # one invocation of benchmark_models.py: its before/after system
        # snapshots, its own metadata (workload note, manual responsiveness
        # rating), and the trial rows it produced. Existing trial rows from
        # before this session have run_id=NULL — they're still valid trial
        # data, just not part of a grouped run (no snapshots/report for them).
        if "run_id" not in existing_cols:
            conn.execute("ALTER TABLE model_benchmarks ADD COLUMN run_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_model_benchmarks_run_id ON model_benchmarks(run_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                models TEXT NOT NULL,
                keep_alive TEXT NOT NULL,
                think_mode TEXT,
                workload_note TEXT,
                responsiveness_rating INTEGER,
                responsiveness_note TEXT
            )
        """)

        # No sudo anywhere here (see system_snapshot.py's docstring) — swap,
        # compressed memory, memory pressure level, and CPU load are all
        # readable through sysctl/vm_stat/os.getloadavg(). GPU/thermal
        # deliberately not captured.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                label TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                swap_used_mb REAL,
                memory_pressure TEXT,
                cpu_load_1m REAL,
                cpu_load_5m REAL,
                cpu_load_15m REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_system_snapshots_run_id ON system_snapshots(run_id)")

        # Session 4: "Environment Health" (baseline) vs. "Model Impact"
        # (delta) split — see this module's docstring. compressed_memory_mb
        # is a leading indicator swap alone can miss; responsiveness_label
        # replaces the old bare 1-5 rating with the qualitative checklist
        # ("smooth"/"slight_lag"/"noticeable_lag"/"unusable") the user
        # asked for — responsiveness_rating is kept as the numeric mapping
        # benchmark_models.py derives from that label, for threshold math.
        existing_snapshot_cols = {row[1] for row in conn.execute("PRAGMA table_info(system_snapshots)").fetchall()}
        if "compressed_memory_mb" not in existing_snapshot_cols:
            conn.execute("ALTER TABLE system_snapshots ADD COLUMN compressed_memory_mb REAL")
        existing_run_cols = {row[1] for row in conn.execute("PRAGMA table_info(benchmark_runs)").fetchall()}
        if "responsiveness_label" not in existing_run_cols:
            conn.execute("ALTER TABLE benchmark_runs ADD COLUMN responsiveness_label TEXT")

        # Milestone 5.6: async job tracking — see jobs.py and this module's
        # docstring. Brand-new table (CREATE TABLE IF NOT EXISTS is
        # sufficient; no prior release ever wrote to it).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                text TEXT,
                route TEXT,
                level TEXT,
                project TEXT,
                total_steps INTEGER NOT NULL DEFAULT 0,
                current_step_index INTEGER NOT NULL DEFAULT 0,
                steps_json TEXT,
                unknown_steps_json TEXT,
                progress_json TEXT,
                result_json TEXT,
                error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)")

        conn.commit()


def _steps_hash(steps: list) -> str:
    """Canonical hash of a step list — used to bind a confirmation token to
    the EXACT actions the user was shown, not just "something got confirmed"."""
    canonical = json.dumps(steps, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def log_event(**kwargs) -> None:
    kwargs["timestamp"] = datetime.now(timezone.utc).isoformat()
    params_json = json.dumps(kwargs.get("params")) if kwargs.get("params") is not None else None
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO logs (timestamp, intent, route, tool, params_json,
                                  success, latency_s, ram_before_mb, ram_after_mb, error, level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kwargs.get("timestamp"), kwargs.get("intent"), kwargs.get("route"),
             kwargs.get("tool"), params_json, int(bool(kwargs.get("success"))),
             kwargs.get("latency_s"), kwargs.get("ram_before_mb"),
             kwargs.get("ram_after_mb"), kwargs.get("error"), kwargs.get("level")),
        )
        conn.commit()
    try:
        with open(JSONL_PATH, "a") as f:
            f.write(json.dumps(kwargs) + "\n")
    except OSError:
        pass  # JSONL tail is best-effort; the SQLite write above already succeeded


def query_recent_failures(limit: int = 20) -> list:
    """Small convenience query — the actual point of migrating off JSONL
    ("what failed this week" without grepping)."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM logs WHERE success = 0 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def query_level_distribution(since_iso: str = None) -> dict:
    """Count of logged requests per Decision Engine level (L0-L4), plus a
    "null" bucket for pre-Milestone-0.5 rows that predate the level column.
    This is the actual spot-check query for the Milestone 0.5 checkpoint in
    spec §10 ("verify most traffic sits at L0-L2"). Optionally filter to
    rows at/after `since_iso` (an ISO timestamp string) for a "past week"
    style check."""
    query = "SELECT level, COUNT(*) as n FROM logs"
    params = ()
    if since_iso:
        query += " WHERE timestamp >= ?"
        params = (since_iso,)
    query += " GROUP BY level"
    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return {(r["level"] or "null"): r["n"] for r in rows}


def create_confirmation(steps: list) -> str:
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO confirmations (token, steps_hash, created_at, expires_at, used) "
            "VALUES (?, ?, ?, ?, 0)",
            (token, _steps_hash(steps), now, now + CONFIRMATION_TTL_SECONDS),
        )
        conn.commit()
    return token


def verify_and_consume_confirmation(token: str, steps: list):
    """Returns (ok: bool, reason: str). Consumes the token on success (one-time
    use) so it can't be replayed against a later, different confirm prompt."""
    if not token:
        return False, "missing confirm_token"
    now = time.time()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM confirmations WHERE token = ?", (token,)).fetchone()
        if row is None:
            return False, "unknown confirm_token"
        if row["used"]:
            return False, "confirm_token already used"
        if now > row["expires_at"]:
            return False, "confirm_token expired — please retry the command"
        if row["steps_hash"] != _steps_hash(steps):
            return False, "confirm_token does not match the steps being executed — please retry"
        conn.execute("UPDATE confirmations SET used = 1 WHERE token = ?", (token,))
        conn.commit()
    return True, "ok"


def prune_expired_confirmations() -> None:
    """Best-effort housekeeping; cheap enough to call on every process start."""
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM confirmations WHERE expires_at < ?", (time.time(),))
        conn.commit()


# ---------------------------------------------------------------------------
# Milestone 2 — conversation/project memory. Only memory.py should call
# these directly; see that module's docstring for why.
# ---------------------------------------------------------------------------

def add_conversation_turn(role: str, text: str, project: Optional[str] = None) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (timestamp, role, text, project) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), role, text, project),
        )
        conn.commit()


def get_recent_conversation(limit: int = 10) -> list:
    """Returns up to `limit` most recent turns, oldest-first (natural
    reading order) — not the whole history, per memory.py's RAM/context
    budget reasoning."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, role, text, project FROM conversations "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def upsert_project(path: str, total_files: Optional[int] = None,
                    total_size_mb: Optional[float] = None,
                    languages: Optional[dict] = None,
                    top_level: Optional[list] = None) -> None:
    """Insert or refresh a project's durable summary. Any field left as
    None (e.g. a bare `open_project` visit with no index_project result
    handy) preserves whatever was already stored — a visit should never
    blow away a previously-known summary just because this particular
    visit didn't recompute one."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO projects
                   (path, last_accessed, total_files, total_size_mb, languages_json, top_level_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   last_accessed = excluded.last_accessed,
                   total_files = COALESCE(excluded.total_files, projects.total_files),
                   total_size_mb = COALESCE(excluded.total_size_mb, projects.total_size_mb),
                   languages_json = COALESCE(excluded.languages_json, projects.languages_json),
                   top_level_json = COALESCE(excluded.top_level_json, projects.top_level_json)
            """,
            (path, now, total_files, total_size_mb,
             json.dumps(languages) if languages is not None else None,
             json.dumps(top_level) if top_level is not None else None),
        )
        conn.commit()


def get_project(path: str) -> Optional[dict]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE path = ?", (path,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["languages"] = json.loads(d.pop("languages_json")) if d.get("languages_json") else {}
    d["top_level_entries"] = json.loads(d.pop("top_level_json")) if d.get("top_level_json") else []
    return d


def set_preference(key: str, value) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO preferences (key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_preference(key: str, default=None):
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value_json FROM preferences WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value_json"])


def get_all_preferences() -> dict:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT key, value_json FROM preferences").fetchall()
    return {r["key"]: json.loads(r["value_json"]) for r in rows}


# ---------------------------------------------------------------------------
# Milestone 3 — knowledge storage/cross-referencing. Only tools.py should
# call these directly (writes from summarize_document/summarize_youtube_video,
# reads from search_knowledge) — see this module's docstring.
# ---------------------------------------------------------------------------

def store_knowledge(path: str, summary: str, source_type: str,
                     question: Optional[str] = None) -> int:
    """Records one summarize_document/summarize_youtube_video result.
    `path` is the file path (document) or URL/video ID (YouTube) the
    summary is about; `source_type` is a short tag ('document' or
    'youtube') for filtering/cross-referencing later. Returns the new
    row's id."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO knowledge (path, source_type, question, summary, extracted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (path, source_type, question, summary, now),
        )
        conn.commit()
        return cursor.lastrowid


def search_knowledge(query: str, limit: int = 10) -> list:
    """Keyword/LIKE search over stored summaries — matches against both
    `path` and `summary`, case-insensitive (SQLite's default LIKE behavior
    for ASCII). Most-recent-first. Deliberately plain SQL, not an
    embedding/semantic search — see this module's docstring and
    tools.py's search_knowledge() for the reasoning."""
    like_query = f"%{query}%"
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge WHERE path LIKE ? OR summary LIKE ? "
            "ORDER BY extracted_at DESC LIMIT ?",
            (like_query, like_query, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Milestone 5 — 9B model evaluation. Only benchmark_models.py should call
# these directly. See this module's docstring and that script's own
# docstring for why the benchmark lives as a standalone script.
# ---------------------------------------------------------------------------

def store_benchmark_result(model: str, prompt_label: str, latency_s: float,
                            success: bool, eval_count: Optional[int] = None,
                            eval_tokens_per_s: Optional[float] = None,
                            ollama_size_mb: Optional[float] = None,
                            ollama_size_vram_mb: Optional[float] = None,
                            error: Optional[str] = None,
                            run_id: Optional[str] = None) -> int:
    """Records one (model, prompt) benchmark trial. Returns the new row's id.
    `ollama_size_mb`/`ollama_size_vram_mb` come from Ollama's own `/api/ps`
    (see benchmark_models.py) — NOT the deprecated `ollama_peak_rss_mb`
    column (see this module's docstring for why that was wrong). `run_id`
    groups this trial with a `benchmark_runs` row and its before/after
    `system_snapshots` — omit it for an ungrouped one-off trial."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO model_benchmarks
               (model, prompt_label, latency_s, eval_count, eval_tokens_per_s,
                ollama_size_mb, ollama_size_vram_mb, success, error, timestamp, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (model, prompt_label, latency_s, eval_count, eval_tokens_per_s,
             ollama_size_mb, ollama_size_vram_mb, int(bool(success)), error, now, run_id),
        )
        conn.commit()
        return cursor.lastrowid


def query_benchmark_summary(model: Optional[str] = None, run_id: Optional[str] = None) -> list:
    """Per-model aggregate stats (avg latency, avg tokens/s, model size and
    VRAM usage as reported by Ollama itself, success rate) — the actual
    comparison table for the Milestone 5 decision. Filters to one model
    and/or one run if given; with neither, aggregates across every trial
    ever stored (mixes runs/settings — use `run_id` for a trustworthy
    single-run comparison)."""
    query = """
        SELECT model,
               COUNT(*) AS n_trials,
               SUM(success) AS n_success,
               AVG(latency_s) AS avg_latency_s,
               AVG(eval_tokens_per_s) AS avg_tokens_per_s,
               MAX(ollama_size_mb) AS max_size_mb,
               MAX(ollama_size_vram_mb) AS max_size_vram_mb
        FROM model_benchmarks
    """
    conditions, params = [], []
    if model:
        conditions.append("model = ?")
        params.append(model)
    if run_id:
        conditions.append("run_id = ?")
        params.append(run_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY model ORDER BY model"
    with _lock, _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def query_benchmark_raw_latencies(model: str, run_id: Optional[str] = None) -> list:
    """Raw latency_s values for every successful trial of `model` (optionally
    scoped to one run) — the input query_benchmark_summary's AVG can't
    answer: median/min/max/stdev need the individual values, not just the
    mean. SQLite has no MEDIAN/STDEV aggregate, so this hands the list back
    for benchmark_models.py to compute with Python's statistics module."""
    query = "SELECT latency_s FROM model_benchmarks WHERE model = ? AND success = 1"
    params = [model]
    if run_id:
        query += " AND run_id = ?"
        params.append(run_id)
    with _lock, _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [r["latency_s"] for r in rows]


def store_benchmark_run(run_id: str, started_at: str, models: list,
                         keep_alive: str, think_mode: Optional[str] = None,
                         workload_note: Optional[str] = None) -> None:
    """Records one benchmark_models.py invocation's metadata — the
    'Method'/'Environment' context a report needs alongside its trials.
    `models` is stored as a comma-separated string (simple, matches this
    table's read pattern — no need for a join for a handful of tags)."""
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO benchmark_runs
               (run_id, started_at, models, keep_alive, think_mode, workload_note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, started_at, ",".join(models), keep_alive, think_mode, workload_note),
        )
        conn.commit()


def update_benchmark_run_responsiveness(run_id: str, rating: int, label: str,
                                         note: Optional[str] = None) -> None:
    """Records the user's manual responsiveness checklist entry for a run
    (spec's Phase 3/6 — 'no beachballs', 'Chrome scrolling smooth' etc.
    aren't machine-measurable, so this is a deliberate human judgment
    call, not an automated metric). Session 4: `label` is the qualitative
    checklist value ('smooth'/'slight_lag'/'noticeable_lag'/'unusable');
    `rating` is benchmark_models.py's numeric mapping of that label, kept
    alongside it so threshold comparisons stay simple numeric comparisons
    without this module needing to know the label ordering itself."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE benchmark_runs SET responsiveness_rating = ?, responsiveness_label = ?, "
            "responsiveness_note = ? WHERE run_id = ?",
            (rating, label, note, run_id),
        )
        conn.commit()


def query_benchmark_run(run_id: str) -> Optional[dict]:
    """Fetches one run's metadata row, or None if run_id is unknown."""
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM benchmark_runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def store_system_snapshot(run_id: str, label: str, timestamp: str,
                           swap_used_mb: Optional[float] = None,
                           compressed_memory_mb: Optional[float] = None,
                           memory_pressure: Optional[str] = None,
                           cpu_load_1m: Optional[float] = None,
                           cpu_load_5m: Optional[float] = None,
                           cpu_load_15m: Optional[float] = None) -> int:
    """Records one system_snapshot.capture_snapshot() reading (spec's
    Phase 2 'baseline system'), tagged with a run and a label. Session 4:
    label is now 'before:<model>'/'after:<model>' (one pair per model in
    the run, not one pair for the whole run — see benchmark_models.py's
    docstring for why a single before/after pair couldn't attribute a
    change to a specific model). Returns the new row's id."""
    with _lock, _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO system_snapshots
               (run_id, label, timestamp, swap_used_mb, compressed_memory_mb,
                memory_pressure, cpu_load_1m, cpu_load_5m, cpu_load_15m)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, label, timestamp, swap_used_mb, compressed_memory_mb,
             memory_pressure, cpu_load_1m, cpu_load_5m, cpu_load_15m),
        )
        conn.commit()
        return cursor.lastrowid


def query_system_snapshots(run_id: str) -> list:
    """All snapshots for one run, oldest first (session 4: one before/after
    pair per model, so this returns 2x len(models) rows for a normal run,
    not just 2)."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM system_snapshots WHERE run_id = ? ORDER BY timestamp ASC",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Milestone 5.6: async job tracking. jobs.py and main.py are the only
# intended callers — see this module's docstring for the table's shape and
# `jobs.py`'s docstring for the execution/cancellation model built on top.
# ---------------------------------------------------------------------------

def create_job(job_id: str, text: Optional[str] = None, route: Optional[str] = None,
                level: Optional[str] = None, project: Optional[str] = None,
                total_steps: int = 0, status: str = "queued",
                steps: Optional[list] = None, unknown_steps: Optional[list] = None) -> None:
    """Creates a new job row. `steps`/`unknown_steps` (raw {"tool","params"}
    lists, same shape main.py already works with) are stored as JSON so an
    `awaiting_confirmation` job can be resumed by job_id alone later,
    without main.py keeping a separate in-memory pending-steps dict."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, created_at, updated_at, status, text, route, level,
                project, total_steps, current_step_index, steps_json,
                unknown_steps_json, progress_json, result_json, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (job_id, now, now, status, text, route, level, project, total_steps,
             json.dumps(steps) if steps is not None else None,
             json.dumps(unknown_steps) if unknown_steps is not None else None,
             json.dumps([]), None, None),
        )
        conn.commit()


def update_job(job_id: str, status: Optional[str] = None,
               current_step_index: Optional[int] = None,
               progress: Optional[list] = None, result: Optional[dict] = None,
               error: Optional[str] = None) -> None:
    """Partial update — only columns actually passed are touched (a
    progress-only update from a mid-chain step shouldn't need to also know
    the job's final result). `status`/`current_step_index` are the two
    fields a poller checks most often, so most calls pass at least one."""
    fields, params = [], []
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if current_step_index is not None:
        fields.append("current_step_index = ?")
        params.append(current_step_index)
    if progress is not None:
        fields.append("progress_json = ?")
        params.append(json.dumps(progress))
    if result is not None:
        fields.append("result_json = ?")
        params.append(json.dumps(result))
    if error is not None:
        fields.append("error = ?")
        params.append(error)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(job_id)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", tuple(params))
        conn.commit()


def get_job(job_id: str) -> Optional[dict]:
    """Fetches one job, with its JSON columns parsed back into
    lists/dicts, or None if job_id is unknown."""
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    for col in ("steps_json", "unknown_steps_json", "progress_json", "result_json"):
        key = col[: -len("_json")]
        job[key] = json.loads(job[col]) if job[col] else ([] if col == "progress_json" else None)
        del job[col]
    return job


def query_recent_jobs(limit: int = 20) -> list:
    """Most recent jobs first — a debugging/status convenience, same spirit
    as query_recent_failures() for the logs table."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT job_id, created_at, updated_at, status, text, route, level, "
            "total_steps, current_step_index FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]