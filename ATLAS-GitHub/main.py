"""
Always-on listener. This is the ONLY process that stays resident all day —
FastAPI + Tailscale together cost tens of MB, not gigabytes.

A single threading.Lock serializes requests, so the router model and the big
model can never both be loading at once, even if two phone commands land
close together.

execute_tool() centralizes exception handling, timing, and RAM tracking for
every tool call in one place, instead of scattering try/except everywhere.

Set ASSISTANT_API_KEY as an environment variable before running. Requests
must send it as the X-API-Key header. Tailscale keeps strangers off your
network; this is the defense-in-depth layer on top of that.
(As of Milestone 0, start.sh generates and loads this key from a local,
gitignored .secrets/api_key file instead of hardcoding it in the script —
see IMPLEMENTATION_STATE.md.)

Confirmation model (Milestone 0): a `dangerous`/`caution`-tier step is never
executed on a client's word alone. The first call returns a server-issued,
one-time `confirm_token` bound to a hash of the exact steps shown to the
user (storage.py). The second (confirmed) call must present that same
token; it's rejected if it's missing, expired, already used, or bound to a
different step list than the one about to run. `confirmed: bool` alone is
no longer sufficient — see storage.py's module docstring for the full
rationale.

Routing model (Milestone 0.5): every request now gets a RequestContext
built once (request_context.py) and is classified into one of five levels,
L0-L4, by the Decision Engine (decision_engine.py) — see spec §3.1. This
doesn't change the underlying matcher -> router -> big-model chain, it
formalizes and labels it, and adds one real new capability: a `Command`
may carry `attachments`, and a large/ZIP/PDF attachment short-circuits
straight to L4 (the reasoning model) without waking the router model to
guess from wording alone. The assigned level is logged on every request
so traffic can be spot-checked later (storage.query_level_distribution()).

Memory (Milestone 2): the RequestContext built at the top of every
request already carries memory-backed fields (memory.recall_context(),
called inside build_request_context()). This module's job is the write
side — remembering the user's utterance (and, where there's a
meaningful one, the assistant's response) via memory.remember_exchange(),
and marking a project as "current" via memory.remember_project_visit()
whenever index_project/open_project succeeds. See memory.py's module
docstring for the full design.

Planner (Milestone 4): once a step list is settled (deterministic or
router-planned) and past the confirmation gate, it's wrapped into
structured PlannedSteps (planner.py) and populated onto
ctx.tool_candidates before execution, instead of executing the raw
{"tool","params"} dicts directly. planner.execute_plan() replaces the
old inline execution loop and adds one thing the loop didn't have: a
single retry for the one tool that makes a real network call
(play_youtube_video). See planner.py's module docstring for what this
milestone deliberately did NOT build (cancellation, live progress) and
why.

Async execution (Milestone 5.6): /command is unchanged and stays fully
synchronous — nothing above this line changed. /command/async is a new,
separate endpoint for a mobile client that shouldn't have to block on a
long reasoning-model call or sit waiting through a multi-step chain: it
runs the exact same classification logic (_route_command(), extracted
below so both endpoints share one routing decision instead of two copies
that could drift) but hands the actual execution off to a background
thread (jobs.py) and returns a job_id immediately. GET /jobs/{id} polls
status/progress; POST /jobs/{id}/cancel requests best-effort
cancellation between steps; POST /jobs/{id}/confirm is the approval
callback for a job that came back `awaiting_confirmation` instead of
`running`. See jobs.py's module docstring for the full design and its
deliberate limitations (no cross-restart job resumption, cancellation is
step-granular not mid-step).

Run with: uvicorn main:app --host 0.0.0.0 --port 8420
Reach it from your phone at: http://<your-tailscale-mac-name>:8420/command
"""
import inspect
import os
import subprocess
import threading
import time
from collections import deque
from typing import Optional

import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

try:
    import psutil
except ImportError:
    psutil = None

import storage
import decision_engine
import memory
import planner
import jobs
from request_context import build_request_context
from tools import TOOLS
from matcher import match_deterministic
import router as router_module

app = FastAPI()

# Milestone 0: SQLite tables (logs + confirmations) are created here, once,
# on process start. Idempotent, so restarts are safe.
storage.init_db()
storage.prune_expired_confirmations()

# Milestone 5.6: any job still "running"/"awaiting_confirmation" from
# before this process started has no live thread behind it anymore (see
# jobs.py's module docstring) — mark it failed instead of leaving it
# stuck at a status that will never change again.
jobs.reconcile_orphaned_jobs()


@app.get("/")
def chat_ui():
    return FileResponse("chat.html")


@app.post("/shutdown")
def shutdown(x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    subprocess.Popen(
        ["/bin/bash", "stop.sh"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return JSONResponse({"status": "stopping"})


MODEL_LOCK = threading.Lock()  # only one model loads/generates at a time

API_KEY = os.environ.get("ASSISTANT_API_KEY", "")  # set this before running
CODER_MODEL = "qwen3:4b-instruct"  # temporary — swap to the 9B coder model once pulled
OLLAMA_URL = "http://localhost:11434/api/generate"

# Simple in-memory rate limiter: max N requests per WINDOW seconds.
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60
_request_times = deque()
_rate_lock = threading.Lock()


class Command(BaseModel):
    text: str
    confirmed: bool = False  # phone sets this true on the second call, after user taps confirm
    confirm_token: Optional[str] = None  # server-issued token from the first call's confirm_required response
    # Milestone 0.5: not consumed by any tool yet (Milestone 1's ZIP/project
    # tools aren't built), but the Decision Engine already reads this to
    # short-circuit straight to L4 on a large/ZIP/PDF attachment instead of
    # guessing from wording alone. e.g. [{"type": "zip", "size_mb": 4.2}].
    attachments: list = []


def log_event(**kwargs):
    storage.log_event(**kwargs)


def check_rate_limit():
    now = time.time()
    with _rate_lock:
        while _request_times and now - _request_times[0] > RATE_LIMIT_WINDOW:
            _request_times.popleft()
        if len(_request_times) >= RATE_LIMIT_MAX:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        _request_times.append(now)


def rss_mb() -> float:
    if psutil is None:
        return -1
    return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)


# The router LLM sometimes drifts from a tool's exact parameter names
# (e.g. writes "app" instead of "app_name"). router.py's prompt now spells
# the exact keys out, which fixes this at the source — but small local
# models still occasionally guess wrong, so this is a second line of
# defense: known synonyms get remapped to the real parameter name before
# the call, instead of crashing with "unexpected keyword argument".
PARAM_ALIASES = {
    "app": "app_name", "application": "app_name", "app_id": "app_name",
    "path": "project_path", "folder": "project_path", "project": "project_path",
    "dir": "project_path", "directory": "project_path", "folder_path": "project_path",
    "cmd": "command", "script": "command",
    "recipient": "to", "to_email": "to", "email": "to",
    "message": "body", "text": "body", "content": "body",
    "symbol": "ticker", "ticker_symbol": "ticker", "stock": "ticker",
    "command_id": "command_name", "name": "command_name",
    "file": "path", "file_path": "path", "filepath": "path",
    "video_url": "url", "youtube_url": "url", "video": "url", "link": "url",
    "src": "source", "source_path": "source", "from": "source", "from_path": "source",
    "dst": "destination", "dest": "destination", "destination_path": "destination",
    "to_path": "destination", "target": "destination", "target_path": "destination",
    "filename": "new_name", "new_filename": "new_name", "rename_to": "new_name",
    "target_file": "focus_file", "specific_file": "focus_file", "for_file": "focus_file",
    "which_file": "focus_file", "about_file": "focus_file",
}


def normalize_params(fn, params: dict) -> dict:
    """Best-effort alignment of LLM-provided kwargs onto fn's real parameter
    names. Exact matches pass through untouched; near-misses are remapped
    via PARAM_ALIASES; if exactly one required param is still unfilled and
    exactly one extra kwarg is left over, they're assumed to be the same
    thing (covers aliases we haven't thought to list yet)."""
    valid_names = list(inspect.signature(fn).parameters.keys())

    normalized = {k: v for k, v in params.items() if k in valid_names}
    leftover = {k: v for k, v in params.items() if k not in valid_names}

    for k in list(leftover.keys()):
        target = PARAM_ALIASES.get(k)
        if target and target in valid_names and target not in normalized:
            normalized[target] = leftover.pop(k)

    missing = [p for p in valid_names if p not in normalized]
    if len(missing) == 1 and len(leftover) == 1:
        normalized[missing[0]] = next(iter(leftover.values()))

    return normalized


def execute_tool(tool_name: str, params: dict) -> dict:
    """Central wrapper: every tool call goes through here for timing,
    RAM tracking, and exception safety. A crashing tool can never take
    down the listener."""
    ram_before = rss_mb()
    start = time.time()
    try:
        fn = TOOLS[tool_name]["fn"]
        safe_params = normalize_params(fn, params)
        result = fn(**safe_params)
        success = result.get("status") != "error"
    except Exception as e:
        result = {"status": "error", "message": str(e)}
        success = False
    ram_after = rss_mb()
    return {
        "result": result,
        "success": success,
        "latency_s": round(time.time() - start, 2),
        "ram_before_mb": ram_before,
        "ram_after_mb": ram_after,
    }


def run_big_model(text: str) -> str:
    resp = requests.post(OLLAMA_URL, json={
        "model": CODER_MODEL,
        "prompt": text,
        "stream": False,
        "keep_alive": "0",
    }, timeout=120)
    resp.raise_for_status()
    return resp.json()["response"]


def _confirmation_payload(valid_steps: list, token: str) -> dict:
    return {
        "type": "confirm_required",
        "confirm_token": token,
        "steps": [{"tool": s["tool"], "params": s.get("params", {}),
                   "tier": TOOLS[s["tool"]]["tier"]} for s in valid_steps],
    }


def _route_command(cmd: "Command", ctx) -> dict:
    """Runs matcher.py, then router.py if needed, to turn cmd.text into a
    classification decision. Extracted (Milestone 5.6) so /command and
    /command/async share this exact logic instead of maintaining two
    copies that could silently drift apart — the decision of *what* to do
    is identical either way; only what happens after (execute inline vs.
    hand to a background job) differs between the two endpoints.

    Mutates ctx.estimated_complexity as a side effect, same as the
    original inline code did. Returns one of two shapes:
      {"kind": "reasoning"} — router said needs_reasoning; caller should
        run the big model itself (this function does NOT call it, so
        both endpoints stay in control of how they run/log/return that
        path, which differs between them).
      {"kind": "steps", "valid_steps": [...], "unknown_steps": [...],
       "source": "deterministic" | "router_llm"}
    """
    steps = match_deterministic(cmd.text)
    source = "deterministic"

    if steps is not None:
        ctx.estimated_complexity = decision_engine.level_value(decision_engine.classify_deterministic())
    else:
        with MODEL_LOCK:
            decision = router_module.route(cmd.text, TOOLS)

        level = decision_engine.classify_router_decision(decision)
        ctx.estimated_complexity = decision_engine.level_value(level)

        if decision.get("needs_reasoning"):
            return {"kind": "reasoning"}

        steps = decision.get("steps", [])
        source = "router_llm"

    valid_steps, unknown_steps = [], []
    for step in steps:
        (valid_steps if step.get("tool") in TOOLS else unknown_steps).append(step)

    _fill_missing_project_path(valid_steps, ctx)

    return {"kind": "steps", "valid_steps": valid_steps, "unknown_steps": unknown_steps, "source": source}


# Tools whose `project_path` param has no usable default (unlike
# search_code's project_path=".") and whose real-world phrasing often
# omits the directory entirely once a project is already "in context"
# (e.g. "is chat.html in a circular import" — no path mentioned at all).
# Diagnosed 2026-07-27, following up the 2026-07-22 open thread: a
# dependency_graph question phrased around just a filename reported
# "0 files scanned" instead of the real ~38, because router.route() is
# only ever given cmd.text — it has no notion of "the project we were
# just looking at" to fall back on, so a phrasing with no path in it
# left dependency_graph's required project_path either missing (a clean
# error) or resolved against whatever the small model guessed, not
# against the project actually in play. request_context.py's own
# docstring already says a component bypassing RequestContext for
# "what's going on right now" is a bug, not a style choice — nothing
# was actually reading ctx.current_project for this before now.
PROJECT_CONTEXT_TOOLS = {"dependency_graph", "index_project"}


def _fill_missing_project_path(valid_steps: list, ctx) -> None:
    """Mutates valid_steps in place: for any PROJECT_CONTEXT_TOOLS step
    missing a truthy project_path, fills it from ctx.current_project
    (the durably-remembered last project visited via index_project/
    open_project — see memory.py) if one is available. Deliberately
    does NOT touch open_project/run_localhost — those name a specific
    destination on purpose, and silently substituting a remembered
    project there would be surprising, not helpful. Deliberately does
    NOT invent a "." fallback when ctx.current_project is also None —
    that would risk silently scanning the server's cwd, which is a
    different, harder-to-notice failure mode than today's clean
    missing-argument error or an honest 0-files result against a path
    the user actually named.
    """
    if not ctx.current_project:
        return
    for step in valid_steps:
        if step.get("tool") not in PROJECT_CONTEXT_TOOLS:
            continue
        params = step.setdefault("params", {})
        if not params.get("project_path"):
            params["project_path"] = ctx.current_project


@app.post("/command")
def handle_command(cmd: Command, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    check_rate_limit()

    start = time.time()

    # Milestone 0.5: build the shared RequestContext once, up front, and
    # thread it through the rest of this function instead of letting each
    # stage below re-derive its own notion of "what's going on right now"
    # (spec §3.3). Memory-backed fields stay empty until Milestone 2 —
    # see request_context.py's module docstring.
    ctx = build_request_context(cmd.text, cmd.attachments)

    # Milestone 2: remember the user's side of this exchange as early as
    # possible — before any confirmation gate or error path, since "what
    # was asked" is worth recalling even if the request itself fails or
    # ends up waiting on a phone confirmation tap.
    memory.remember_exchange("user", cmd.text, project=ctx.current_project)

    # Decision Engine pre-check: a large/ZIP/PDF attachment is strong
    # enough evidence on its own to skip straight to L4, without paying
    # for a router-model call that would just be guessing from wording
    # (spec §3.1 — "a ZIP attached + project analysis intent should route
    # straight to L4 ... regardless of how the sentence is worded").
    if decision_engine.attachment_forces_l4(ctx.attachments):
        ctx.estimated_complexity = decision_engine.level_value(decision_engine.Level.L4)
        ram_before = rss_mb()
        with MODEL_LOCK:
            result_text = run_big_model(cmd.text)
        log_event(intent=cmd.text, route="big_model", tool=None,
                  latency_s=round(time.time() - start, 2), success=True,
                  ram_before_mb=ram_before, ram_after_mb=rss_mb(),
                  level=ctx.estimated_complexity)
        memory.remember_exchange("assistant", result_text, project=ctx.current_project)
        return {"type": "text", "response": result_text, "route": "big_model",
                "model": CODER_MODEL, "level": ctx.estimated_complexity}

    # 1. Deterministic first (zero RAM, zero model load, sub-second), then
    #    the router model if nothing matched, then the big model if the
    #    router says this genuinely needs reasoning — see _route_command's
    #    own docstring. Extracted (Milestone 5.6) so /command/async runs
    #    the identical classification logic instead of a second copy.
    routed = _route_command(cmd, ctx)

    if routed["kind"] == "reasoning":
        # Genuine reasoning/writing/coding — escalate to the big model.
        ram_before = rss_mb()
        with MODEL_LOCK:
            result_text = run_big_model(cmd.text)
        log_event(intent=cmd.text, route="big_model", tool=None,
                  latency_s=round(time.time() - start, 2), success=True,
                  ram_before_mb=ram_before, ram_after_mb=rss_mb(),
                  level=ctx.estimated_complexity)
        memory.remember_exchange("assistant", result_text, project=ctx.current_project)
        return {"type": "text", "response": result_text, "route": "big_model",
                "model": CODER_MODEL, "level": ctx.estimated_complexity}

    valid_steps, unknown_steps, source = routed["valid_steps"], routed["unknown_steps"], routed["source"]

    if not valid_steps:
        log_event(intent=cmd.text, route=source, tool=None,
                  latency_s=round(time.time() - start, 2), success=False,
                  error="unmatched intent", level=ctx.estimated_complexity)
        return {"type": "error", "response": f"No tool matched: {cmd.text}"}

    # If ANY step in the chain needs confirmation, confirm the whole batch
    # up front rather than partially executing some steps and then
    # stalling mid-chain waiting on a tap.
    needs_confirmation = [s for s in valid_steps if TOOLS[s["tool"]]["tier"] != "safe"]

    if needs_confirmation:
        if not cmd.confirmed:
            # First call: issue a server-side token bound to these exact
            # steps instead of trusting a client-set bool on the next call.
            token = storage.create_confirmation(valid_steps)
            return _confirmation_payload(valid_steps, token)

        # Second call: the client claims this was confirmed — prove it.
        ok, reason = storage.verify_and_consume_confirmation(cmd.confirm_token, valid_steps)
        if not ok:
            log_event(intent=cmd.text, route=source, tool=None,
                      latency_s=round(time.time() - start, 2), success=False,
                      error=f"confirmation rejected: {reason}", level=ctx.estimated_complexity)
            return {"type": "error", "response": f"Confirmation rejected: {reason}"}

    # Milestone 4: wrap the raw step list into structured PlannedSteps and
    # expose them on the shared RequestContext (tool_candidates has been
    # reserved for this since Milestone 0.5) — see planner.py's module
    # docstring for what this milestone deliberately does and doesn't add.
    planned = planner.build_plan(valid_steps)
    ctx.tool_candidates = [p.as_dict() for p in planned]

    # Milestone 2: tools whose successful result identifies a project
    # worth remembering as "current". index_project's result carries a
    # real summary (file count/languages/etc.); open_project only proves
    # the path is real, so its visit doesn't overwrite a previously
    # captured summary — see storage.upsert_project's COALESCE behavior.
    # (PROJECT_RESULT_KEYS is a module-level constant, defined once below
    # near the Milestone 5.6 section, shared with _run_tool_job's
    # background-execution path so the two don't drift.)

    planner.execute_plan(planned, execute_tool)

    results = []
    overall_success = True
    for p in planned:
        exec_info = p.result
        overall_success = overall_success and exec_info["success"]
        results.append({"tool": p.tool, "result": exec_info["result"], "success": exec_info["success"],
                         "attempts": p.attempts})
        log_event(intent=cmd.text, route=source, tool=p.tool, params=p.params,
                  latency_s=exec_info["latency_s"], success=exec_info["success"],
                  ram_before_mb=exec_info["ram_before_mb"],
                  ram_after_mb=exec_info["ram_after_mb"], level=ctx.estimated_complexity)

        if exec_info["success"] and p.tool in PROJECT_RESULT_KEYS:
            path_key = PROJECT_RESULT_KEYS[p.tool]
            project_path = exec_info["result"].get(path_key) or p.params.get("project_path")
            index_result = exec_info["result"] if p.tool == "index_project" else None
            memory.remember_project_visit(project_path, index_result)

    for step in unknown_steps:
        results.append({"tool": step.get("tool"),
                         "result": {"status": "error", "message": "unknown tool"}, "success": False,
                         "attempts": 0})
        overall_success = False

    summary = "; ".join(f"{r['tool']}: {'ok' if r['success'] else 'failed'}" for r in results)
    memory.remember_exchange("assistant", summary, project=ctx.current_project)

    return {"type": "tool_result", "results": results, "route": source,
            "success": overall_success, "level": ctx.estimated_complexity}


# ---------------------------------------------------------------------------
# Milestone 5.6: async execution & task progress. See jobs.py's module
# docstring and this file's own docstring (Async execution section) for
# the design. Nothing above this line changed behavior — /command is
# still exactly what it was.
# ---------------------------------------------------------------------------

PROJECT_RESULT_KEYS = {"index_project": "project_path", "open_project": "opened_project"}


def _run_big_model_job(job_id: str, text: str, project: Optional[str]) -> None:
    """Background worker for the big-model (needs_reasoning / attachment-
    forced-L4) path. Cancellation isn't checked here — a single Ollama
    call can't be interrupted mid-generation once sent (see jobs.py's
    docstring), so a cancel request against this kind of job can only
    take effect before it's dispatched, which /jobs/{id}/cancel can't
    catch once run_in_background has already started this function."""
    start = time.time()
    ram_before = rss_mb()
    try:
        with MODEL_LOCK:
            result_text = run_big_model(text)
        log_event(intent=text, route="big_model", tool=None,
                  latency_s=round(time.time() - start, 2), success=True,
                  ram_before_mb=ram_before, ram_after_mb=rss_mb(), level="L4")
        memory.remember_exchange("assistant", result_text, project=project)
        jobs.update_progress(job_id, status="done",
                              result={"type": "text", "response": result_text,
                                      "route": "big_model", "model": CODER_MODEL, "level": "L4"})
    except Exception as e:
        log_event(intent=text, route="big_model", tool=None,
                  latency_s=round(time.time() - start, 2), success=False,
                  ram_before_mb=ram_before, ram_after_mb=rss_mb(), level="L4", error=str(e))
        jobs.update_progress(job_id, status="failed", error=str(e))


def _run_tool_job(job_id: str, valid_steps: list, unknown_steps: list,
                   source: str, level: str, intent_text: str, project: Optional[str]) -> None:
    """Background worker for a deterministic/router step chain — the
    tool-execution equivalent of _run_big_model_job. Reuses
    planner.build_plan/execute_plan exactly as /command does, adding only
    the on_progress/should_cancel hooks Milestone 5.6 added to
    execute_plan for this purpose."""
    planned = planner.build_plan(valid_steps)

    def on_progress(planned_step) -> None:
        jobs.update_progress(job_id, current_step_index=planned_step.step_index + 1,
                              progress=[p.as_dict() for p in planned])

    def should_cancel() -> bool:
        return jobs.is_cancel_requested(job_id)

    planner.execute_plan(planned, execute_tool, on_progress=on_progress, should_cancel=should_cancel)

    results = []
    overall_success = True
    any_cancelled = False
    for p in planned:
        if p.status == "cancelled":
            any_cancelled = True
            results.append({"tool": p.tool, "result": None, "success": False, "attempts": p.attempts})
            continue
        exec_info = p.result
        overall_success = overall_success and exec_info["success"]
        results.append({"tool": p.tool, "result": exec_info["result"], "success": exec_info["success"],
                         "attempts": p.attempts})
        log_event(intent=intent_text, route=source, tool=p.tool, params=p.params,
                  latency_s=exec_info["latency_s"], success=exec_info["success"],
                  ram_before_mb=exec_info["ram_before_mb"], ram_after_mb=exec_info["ram_after_mb"],
                  level=level)
        if exec_info["success"] and p.tool in PROJECT_RESULT_KEYS:
            path_key = PROJECT_RESULT_KEYS[p.tool]
            project_path = exec_info["result"].get(path_key) or p.params.get("project_path")
            index_result = exec_info["result"] if p.tool == "index_project" else None
            memory.remember_project_visit(project_path, index_result)

    for step in unknown_steps:
        results.append({"tool": step.get("tool"),
                         "result": {"status": "error", "message": "unknown tool"}, "success": False,
                         "attempts": 0})
        overall_success = False

    summary = "; ".join(f"{r['tool']}: {'ok' if r['success'] else 'failed'}" for r in results)
    memory.remember_exchange("assistant", summary, project=project)

    final_status = "cancelled" if any_cancelled else "done"
    jobs.update_progress(
        job_id, status=final_status,
        progress=[p.as_dict() for p in planned],
        result={"type": "tool_result", "results": results, "route": source,
                "success": overall_success and not any_cancelled, "level": level},
    )


@app.post("/command/async")
def handle_command_async(cmd: Command, x_api_key: str = Header(default="")):
    """Same classification as /command (via _route_command) but never
    blocks on model load, generation, or tool execution — returns a
    job_id immediately and does the actual work on a background thread.
    Response shapes:
      {"type": "job_accepted", "job_id", "status": "running", "route"}
      {"type": "confirm_required", "job_id", "confirm_token", "steps"} —
        same as /command's confirm_required, plus a job_id; POST that
        confirm_token to /jobs/{job_id}/confirm to actually start it.
      {"type": "error", "response"} — unmatched intent, same as /command.
    """
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    check_rate_limit()

    ctx = build_request_context(cmd.text, cmd.attachments)
    memory.remember_exchange("user", cmd.text, project=ctx.current_project)

    if decision_engine.attachment_forces_l4(ctx.attachments):
        ctx.estimated_complexity = decision_engine.level_value(decision_engine.Level.L4)
        job_id = jobs.new_job_id()
        jobs.create_job(job_id, text=cmd.text, route="big_model", level=ctx.estimated_complexity,
                         project=ctx.current_project, total_steps=1, status="running")
        jobs.run_in_background(_run_big_model_job, job_id, cmd.text, ctx.current_project)
        return {"type": "job_accepted", "job_id": job_id, "status": "running", "route": "big_model"}

    routed = _route_command(cmd, ctx)

    if routed["kind"] == "reasoning":
        job_id = jobs.new_job_id()
        jobs.create_job(job_id, text=cmd.text, route="big_model", level=ctx.estimated_complexity,
                         project=ctx.current_project, total_steps=1, status="running")
        jobs.run_in_background(_run_big_model_job, job_id, cmd.text, ctx.current_project)
        return {"type": "job_accepted", "job_id": job_id, "status": "running", "route": "big_model"}

    valid_steps, unknown_steps, source = routed["valid_steps"], routed["unknown_steps"], routed["source"]

    if not valid_steps:
        log_event(intent=cmd.text, route=source, tool=None,
                  latency_s=0, success=False, error="unmatched intent", level=ctx.estimated_complexity)
        return {"type": "error", "response": f"No tool matched: {cmd.text}"}

    needs_confirmation = [s for s in valid_steps if TOOLS[s["tool"]]["tier"] != "safe"]

    if needs_confirmation:
        token = storage.create_confirmation(valid_steps)
        job_id = jobs.new_job_id()
        jobs.create_job(job_id, text=cmd.text, route=source, level=ctx.estimated_complexity,
                         project=ctx.current_project, total_steps=len(valid_steps),
                         status="awaiting_confirmation", steps=valid_steps, unknown_steps=unknown_steps)
        payload = _confirmation_payload(valid_steps, token)
        payload["job_id"] = job_id
        return payload

    job_id = jobs.new_job_id()
    jobs.create_job(job_id, text=cmd.text, route=source, level=ctx.estimated_complexity,
                     project=ctx.current_project, total_steps=len(valid_steps), status="running")
    jobs.run_in_background(_run_tool_job, job_id, valid_steps, unknown_steps, source,
                            ctx.estimated_complexity, cmd.text, ctx.current_project)
    return {"type": "job_accepted", "job_id": job_id, "status": "running", "route": source}


class ConfirmJobRequest(BaseModel):
    confirm_token: str


@app.post("/jobs/{job_id}/confirm")
def confirm_job(job_id: str, body: ConfirmJobRequest, x_api_key: str = Header(default="")):
    """Approval callback for a job created by /command/async that came
    back `awaiting_confirmation`. Verifies confirm_token exactly as
    /command's second call does (same storage.verify_and_consume_
    confirmation — one confirmation-token implementation, not two), then
    starts background execution."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")

    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    if job["status"] != "awaiting_confirmation":
        return {"type": "error", "response": f"job is not awaiting confirmation (status: {job['status']})"}

    ok, reason = storage.verify_and_consume_confirmation(body.confirm_token, job["steps"])
    if not ok:
        return {"type": "error", "response": f"Confirmation rejected: {reason}"}

    jobs.update_progress(job_id, status="running")
    jobs.run_in_background(_run_tool_job, job_id, job["steps"], job["unknown_steps"] or [],
                            job["route"], job["level"], job["text"], job["project"])
    return {"type": "job_accepted", "job_id": job_id, "status": "running", "route": job["route"]}


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str, x_api_key: str = Header(default="")):
    """Poll endpoint — current status, progress (per-step outcomes so
    far), and, once terminal, the final result in the exact same shape
    /command would have returned synchronously."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return job


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, x_api_key: str = Header(default="")):
    """Requests best-effort cancellation. Only meaningful for a `running`
    tool-chain job — see planner.execute_plan()'s docstring for exactly
    when this takes effect (between steps, never mid-step or mid-
    generation). Returns whether a live job was actually signaled, not
    whether it has stopped yet — the caller should poll /jobs/{id} to see
    the eventual status."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    signaled = jobs.request_cancel(job_id)
    return {"job_id": job_id, "cancel_requested": signaled, "status": job["status"]}


@app.get("/jobs")
def list_jobs(x_api_key: str = Header(default=""), limit: int = 20):
    """Most recent jobs, newest first — a debugging/status convenience
    (same spirit as storage.query_recent_failures()), not something the
    phone client necessarily needs day to day."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    return {"jobs": storage.query_recent_jobs(limit=limit)}