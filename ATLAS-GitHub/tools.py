"""
Tool registry — the single source of truth for every action the assistant can perform.
Deterministic matching, the router LLM, and the big model all call into this same registry.

safety tier: "safe" (no confirmation) | "caution" (confirm optional, default off) |
             "dangerous" (always confirm from the phone before executing)
"""
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests

import storage

try:
    import psutil
except ImportError:
    psutil = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import CouldNotRetrieveTranscript
except ImportError:
    YouTubeTranscriptApi = None

    class CouldNotRetrieveTranscript(Exception):
        """Fallback exception type when youtube-transcript-api is unavailable."""
        pass

try:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
except ImportError:
    PdfReader = None

    class PdfReadError(Exception):
        """Fallback exception type when pypdf is unavailable."""
        pass

try:
    import docx as docx_lib  # python-docx; module name docx, package name python-docx
except ImportError:
    docx_lib = None


def open_app(app_name: str) -> dict:
    try:
        subprocess.run(["open", "-a", app_name], check=True, timeout=10)
        return {"status": "ok", "opened": app_name, "message": f"Opened {app_name}."}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": str(e)}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timed out opening app"}


# Known sites that have a real search endpoint. Keyed by bare domain (no
# scheme/www) so lookups stay simple regardless of how the url was passed
# in. Add more here as you find yourself asking to search other sites.
SEARCH_TEMPLATES = {
    "youtube.com": "https://www.youtube.com/results?search_query={q}",
    "google.com": "https://www.google.com/search?q={q}",
    "github.com": "https://github.com/search?q={q}",
    "amazon.com": "https://www.amazon.com/s?k={q}",
}


def open_url(url: str, search: str = None) -> dict:
    """For websites/services (YouTube, Gmail, GitHub, etc.) that are not
    installed macOS apps — opens the URL in the default browser via the
    same `open` command open_app uses, so behavior stays consistent.

    If `search` is given and `url` is one of the known SEARCH_TEMPLATES
    sites, builds that site's actual search-results URL instead of just
    opening its homepage (e.g. open_url("youtube.com", search="spaghetti
    recipe") opens a YouTube search for "spaghetti recipe", not just
    youtube.com)."""
    domain = url.replace("https://", "").replace("http://", "") \
                .split("/")[0].removeprefix("www.").removeprefix("mail.")
    if search and domain in SEARCH_TEMPLATES:
        url = SEARCH_TEMPLATES[domain].format(q=quote(search))
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        subprocess.run(["open", url], check=True, timeout=10)
        label = f'Searched "{search}" on {domain}.' if (search and domain in SEARCH_TEMPLATES) \
            else f"Opened {url}."
        return {"status": "ok", "opened": url, "message": label}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": str(e)}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timed out opening url"}


def play_youtube_video(query: str) -> dict:
    """Finds the first YouTube result for `query` and opens ITS watch page
    directly, so it actually starts playing — unlike open_url(search=...),
    which only opens the search-results list.

    This scrapes YouTube's public search page for the first video ID (no
    API key needed). It's inherently best-effort: YouTube's page markup
    can change and break the regex below, and there's no guarantee the
    first result is the one you meant. If scraping fails for any reason,
    it falls back to opening the search-results page instead, same as
    open_url(search=...) would."""
    search_url = f"https://www.youtube.com/results?search_query={quote(query)}"
    video_id = None
    try:
        resp = requests.get(search_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        match = re.search(r'"videoId":"([\w-]{11})"', resp.text)
        if match:
            video_id = match.group(1)
    except Exception:
        pass  # fall through to the search-page fallback below

    target = f"https://www.youtube.com/watch?v={video_id}" if video_id else search_url
    try:
        subprocess.run(["open", target], check=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": str(e)}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timed out opening video"}

    if video_id:
        return {"status": "ok", "opened": target, "message": f'Playing the top result for "{query}".'}
    return {"status": "ok", "opened": target,
            "message": f'Couldn\'t pin down a specific video, opened search results for "{query}" instead.'}


def get_battery() -> dict:
    if psutil is None:
        return {"status": "error", "message": "pip install psutil"}
    battery = psutil.sensors_battery()
    if battery is None:
        return {"status": "error", "message": "no battery info available"}
    state = "charging" if battery.power_plugged else "on battery"
    return {"status": "ok", "battery_percent": battery.percent, "charging": battery.power_plugged,
            "message": f"Battery is at {battery.percent}% ({state})."}


def open_project(project_path: str) -> dict:
    path = Path(project_path).expanduser()
    if not path.exists():
        return {"status": "error", "message": f"path not found: {path}"}
    try:
        subprocess.run(["code", str(path)], timeout=10)
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timed out opening in VS Code"}
    return {"status": "ok", "opened_project": str(path), "message": f"Opened {path} in VS Code."}


def run_localhost(project_path: str, command: str = "npm run dev") -> dict:
    path = Path(project_path).expanduser()
    if not path.exists():
        return {"status": "error", "message": f"path not found: {path}"}
    subprocess.Popen(command.split(), cwd=str(path))
    return {"status": "ok", "started": command, "in": str(path),
            "message": f"Started `{command}` in {path}."}


def get_stock_price(ticker: str) -> dict:
    # Wire up a real quote API here (Finnhub, Alpha Vantage, yfinance).
    return {"status": "not_implemented", "ticker": ticker.upper(),
            "message": f"Stock price lookup isn't wired up yet (asked for {ticker.upper()}).",
            "note": "plug in a quote API of your choice"}


def send_email(to: str, subject: str, body: str) -> dict:
    # Wire up Gmail API or SMTP here. "dangerous" tier — always confirmed first.
    return {"status": "not_implemented", "to": to, "subject": subject,
            "message": f"Email sending isn't wired up yet (would have sent to {to})."}


# ---------------------------------------------------------------------------
# Milestone 1 — Project understanding (ZIP/git/code search)
#
# Per IMPLEMENTATION_STATE.md's plan: a deterministic project-indexing tool,
# git status/diff/log tools, an rg-backed code-search tool, and a ZIP-reader
# tool. Every tool below is read-only/non-mutating ("safe" tier, no
# confirmation) and does the deterministic scan itself — the LLM is only
# ever invoked afterward, to summarize/plan on top of these results, never
# to do the scan.
# ---------------------------------------------------------------------------

# Directories that are never walked/counted — noisy, huge, and never what
# someone means by "my project" (installed deps, build output, VCS
# internals). os.walk lets us prune these in place so they're never even
# descended into, not just filtered after the fact — this matters a lot on
# a 16GB machine: this very repo ships a multi-hundred-MB venv/ that would
# otherwise get walked file-by-file for no reason. Dirs starting with "."
# are skipped too (also quietly keeps things like .secrets/ out of scans).
EXCLUDED_DIRS = {
    ".git", "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", ".next", ".nuxt", "target", ".pytest_cache",
    ".mypy_cache", "site-packages", ".idea", ".vscode", "coverage",
    ".turbo", ".cache", "vendor", "eggs", ".eggs",
}

LANGUAGE_EXTENSIONS = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java",
    ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".cc": "C++",
    ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".swift": "Swift", ".kt": "Kotlin", ".m": "Objective-C",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".json": "JSON",
    ".yml": "YAML", ".yaml": "YAML", ".md": "Markdown", ".sql": "SQL",
    ".sh": "Shell", ".vue": "Vue", ".dart": "Dart", ".applescript": "AppleScript",
}

# Re-walking a large project on every follow-up question about it would be
# wasteful CPU/battery for no benefit — the tree rarely changes second to
# second. A short in-memory TTL cache (spec §7.1: "cache file tree +
# language breakdown") covers the common "ask a few things about the same
# project in a row" pattern without needing a persistent store; Milestone 2
# (real memory) can replace this with something durable later if needed.
INDEX_CACHE_TTL_SECONDS = 300
MAX_INDEX_FILES = 20000  # safety cap so a pathological tree can't run away
_index_cache: dict = {}


def _classify_extension(suffix: str) -> str:
    if not suffix:
        return "other"
    return LANGUAGE_EXTENSIONS.get(suffix.lower(), suffix.lstrip(".").upper())


def _walk_project(root: Path):
    """Yields (Path, size_bytes) for every file under root, pruning
    EXCLUDED_DIRS and dot-dirs before descending into them."""
    file_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
        for fname in filenames:
            if file_count >= MAX_INDEX_FILES:
                return
            fpath = Path(dirpath) / fname
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            file_count += 1
            yield fpath, size


def index_project(project_path: str) -> dict:
    """Walk a project once and summarize it: file count, total size,
    language breakdown by file count, and top-level structure. Cached per
    resolved path for INDEX_CACHE_TTL_SECONDS so asking follow-up questions
    about the same project doesn't re-walk the tree each time."""
    root = Path(project_path).expanduser().resolve()
    if not root.exists():
        return {"status": "error", "message": f"path not found: {root}"}
    if not root.is_dir():
        return {"status": "error", "message": f"not a directory: {root}"}

    cache_key = str(root)
    cached = _index_cache.get(cache_key)
    if cached and (time.time() - cached["cached_at"]) < INDEX_CACHE_TTL_SECONDS:
        result = dict(cached["result"])
        result["cached"] = True
        return result

    total_files = 0
    total_size = 0
    languages: dict = {}
    for fpath, size in _walk_project(root):
        total_files += 1
        total_size += size
        lang = _classify_extension(fpath.suffix)
        languages[lang] = languages.get(lang, 0) + 1

    top_level = sorted(
        p.name + ("/" if p.is_dir() else "")
        for p in root.iterdir()
        if p.name not in EXCLUDED_DIRS and not p.name.startswith(".")
    )
    top_languages = dict(sorted(languages.items(), key=lambda kv: -kv[1])[:8])
    truncated = total_files >= MAX_INDEX_FILES

    result = {
        "status": "ok",
        "project_path": str(root),
        "total_files": total_files,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "languages": top_languages,
        "top_level_entries": top_level,
        "truncated": truncated,
        "cached": False,
        "message": f"Indexed {total_files} files "
                    f"({round(total_size / (1024 * 1024), 1)} MB) under {root}."
                    + (" (capped — very large tree)" if truncated else ""),
    }
    _index_cache[cache_key] = {"cached_at": time.time(), "result": result}
    return result


# Read-only git introspection. Deliberately a SEPARATE allowlist+tool from
# run_shell_command's ALLOWED_COMMANDS below, even though it's the exact
# same fixed-argv pattern: run_shell_command is "dangerous" tier because it
# also holds git_pull/npm_install/docker_ps, which mutate state and should
# always be confirmed from the phone first. These commands are read-only —
# gating every `git log` behind a confirmation tap would make checking your
# own repo slower than just typing the command, which works against the
# whole point of this assistant. Splitting them out keeps
# run_shell_command's confirm-by-default posture intact for anything that
# writes, while making read-only checks instant.
GIT_READONLY_COMMANDS = {
    "git_log": ["git", "log", "--oneline", "-20"],
    "git_diff": ["git", "diff"],
    "git_diff_staged": ["git", "diff", "--staged"],
    "git_branch": ["git", "branch", "--show-current"],
    "git_status_short": ["git", "status", "--short"],
}


def git_query(command_name: str, cwd: str = ".") -> dict:
    if command_name not in GIT_READONLY_COMMANDS:
        return {"status": "error",
                "message": f"'{command_name}' is not a recognized read-only git command. "
                            f"Available: {list(GIT_READONLY_COMMANDS.keys())}"}
    path = Path(cwd).expanduser()
    if not path.exists():
        return {"status": "error", "message": f"path not found: {path}"}
    try:
        result = subprocess.run(GIT_READONLY_COMMANDS[command_name], cwd=str(path),
                                 capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "git command timed out"}
    except FileNotFoundError:
        return {"status": "error", "message": "git is not installed or not on PATH"}
    ok = result.returncode == 0
    return {"status": "ok" if ok else "error",
            "stdout": result.stdout[-4000:], "stderr": result.stderr[-2000:],
            "message": f"`{' '.join(GIT_READONLY_COMMANDS[command_name])}` "
                        f"{'succeeded' if ok else 'failed'} (exit {result.returncode})."}


CODE_SEARCH_TIMEOUT_S = 15
CODE_SEARCH_MAX_MATCHES = 200


def search_code(query: str, project_path: str = ".", file_type: str = None) -> dict:
    """ripgrep-backed code search — deterministic, no LLM involved in the
    search itself. Requires ripgrep (`brew install ripgrep`); macOS doesn't
    ship it by default the way it ships grep. Fails with a clear message
    rather than silently falling back to a weaker search if it's missing,
    since rg's --type handling materially changes result quality."""
    path = Path(project_path).expanduser()
    if not path.exists():
        return {"status": "error", "message": f"path not found: {path}"}

    cmd = ["rg", "--line-number", "--no-heading", "--max-count", "20", "--max-columns", "300"]
    if file_type:
        cmd += ["--type", file_type]
    cmd += [query, str(path)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=CODE_SEARCH_TIMEOUT_S)
    except FileNotFoundError:
        return {"status": "error",
                "message": "ripgrep (rg) is not installed. Install with `brew install ripgrep`."}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "code search timed out"}

    # rg exits 1 (not an error) when the search ran fine but found nothing.
    if result.returncode not in (0, 1):
        return {"status": "error", "message": result.stderr[-2000:] or "ripgrep failed"}

    all_lines = result.stdout.splitlines()
    lines = all_lines[:CODE_SEARCH_MAX_MATCHES]
    truncated = len(all_lines) > CODE_SEARCH_MAX_MATCHES
    return {"status": "ok", "query": query, "match_count": len(lines),
            "matches": lines, "truncated": truncated,
            "message": f"{len(lines)} match(es) for '{query}' in {path}"
                        + (" (truncated)" if truncated else "")}


ZIP_LANGUAGE_PREVIEW_LIMIT = 8


def read_zip(zip_path: str) -> dict:
    """Reads a ZIP's manifest — file count, uncompressed size, language
    breakdown — WITHOUT extracting anything to disk. Reuses the same
    extension->language classification as index_project() so a zipped
    project and an already-unzipped one summarize the same way."""
    path = Path(zip_path).expanduser()
    if not path.exists():
        return {"status": "error", "message": f"path not found: {path}"}
    if not zipfile.is_zipfile(path):
        return {"status": "error", "message": f"not a valid zip file: {path}"}

    languages: dict = {}
    total_files = 0
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = Path(info.filename).parts
                if any(p in EXCLUDED_DIRS or p.startswith(".") for p in parts[:-1]):
                    continue
                total_files += 1
                total_uncompressed += info.file_size
                languages[_classify_extension(Path(info.filename).suffix)] = \
                    languages.get(_classify_extension(Path(info.filename).suffix), 0) + 1
    except zipfile.BadZipFile:
        return {"status": "error", "message": "corrupt or unreadable zip file"}

    top_languages = dict(sorted(languages.items(), key=lambda kv: -kv[1])[:ZIP_LANGUAGE_PREVIEW_LIMIT])
    return {"status": "ok", "zip_path": str(path), "total_files": total_files,
            "total_uncompressed_mb": round(total_uncompressed / (1024 * 1024), 2),
            "languages": top_languages,
            "message": f"{total_files} files "
                        f"({round(total_uncompressed / (1024 * 1024), 1)} MB uncompressed) "
                        f"in {path.name}."}


# ---------------------------------------------------------------------------
# Milestone 5.7 — Tier-1 Tool Ecosystem, first slice: Dependency Graph.
#
# Deterministic only — no reasoning-model call, same posture as
# index_project/git_query/search_code/read_zip. Scoped to Python (via the
# stdlib `ast` module — more robust than regex against multi-line imports,
# aliases, and imports that only *look* like imports inside a string or
# comment) and JavaScript/TypeScript (regex-based; there's no equivalent
# zero-dependency JS parser in the stdlib, and a full JS parser is a real
# new-dependency decision not worth making for this). Other ecosystems
# (Go, Rust, Java, etc.) are deliberately NOT built this slice — same
# "extend the allowlist once there's a concrete need" pattern DOCX/PDF
# support followed in Milestone 3.
#
# Only INTERNAL (project-local) dependencies are resolved into edges —
# external packages (numpy, react, lodash, etc.) are recognized as imports
# but deliberately not resolved or counted, since the graph's whole value
# is showing how *this project's own files* depend on each other (e.g.
# "what would break if I deleted this file"), not re-deriving
# requirements.txt/package.json.
# ---------------------------------------------------------------------------

import ast

DEPENDENCY_GRAPH_EXTENSIONS = {
    "py": {".py"},
    "js": {".js", ".jsx", ".ts", ".tsx"},
}
MAX_DEPENDENCY_FILES = 3000  # same spirit as MAX_INDEX_FILES — a safety cap, not a real limit
MAX_DEPENDENCY_FILE_CHARS = 300_000  # skip parsing pathologically large generated files
DEPENDENCY_GRAPH_PREVIEW_LIMIT = 15  # how many hub files / cycles to show in the summary

_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"]*?\s+from\s+)?|require\s*\(\s*)['"](\.[^'"]*)['"]""",
)


def _resolve_python_module_to_file(module: str, level: int, current_file: Path,
                                    root: Path, all_files: set) -> str | None:
    """Resolves a Python import (either `import a.b.c` — level 0 — or a
    relative `from .a import b` — level >= 1) to one of the project's own
    scanned files, or None if it doesn't point at a file we walked (i.e.
    it's an external package, or the target simply doesn't exist).
    Returns a path string relative to `root`, using forward slashes, to
    match how `all_files` keys are built below."""
    if level == 0:
        if not module:
            return None
        base = root
    else:
        base = current_file.parent
        for _ in range(level - 1):
            base = base.parent
        if not module:
            # `from . import x` / `from .. import x` — module itself is
            # the package dir; nothing further to descend for the module
            # part, the imported *names* would be submodules of `base`,
            # which ast gives us as separate alias entries elsewhere — not
            # handled here since we only look at the `from X import` module
            # path, not each imported name.
            return None

    for part in module.split("."):
        base = base / part
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        try:
            rel = candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if rel in all_files:
            return rel
    return None


def _parse_python_dependencies(file_path: Path, root: Path, all_files: set) -> set:
    try:
        if file_path.stat().st_size > MAX_DEPENDENCY_FILE_CHARS:
            return set()
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError, ValueError):
        return set()

    deps = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_python_module_to_file(alias.name, 0, file_path, root, all_files)
                if resolved:
                    deps.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # `from X import Y` may mean "submodule Y of package X" (try
            # X.Y as a file first) or "name Y defined inside X/__init__.py"
            # (fall back to resolving X itself) — ast can't tell us which
            # without actually importing the code, so try the more likely
            # candidate first and degrade gracefully to the other.
            for alias in node.names:
                submodule = f"{module}.{alias.name}" if module else alias.name
                resolved = _resolve_python_module_to_file(submodule, node.level, file_path, root, all_files)
                if not resolved:
                    resolved = _resolve_python_module_to_file(module, node.level, file_path, root, all_files)
                if resolved:
                    deps.add(resolved)
    return deps


def _resolve_js_relative_import(spec: str, current_file: Path, root: Path, all_files: set) -> str | None:
    base = (current_file.parent / spec).resolve()
    try:
        root_resolved = root.resolve()
        base.relative_to(root_resolved)
    except ValueError:
        return None  # escapes the project root — not something we track

    candidates = [base]
    for ext in (".js", ".jsx", ".ts", ".tsx"):
        candidates.append(Path(str(base) + ext))
    for ext in (".js", ".jsx", ".ts", ".tsx"):
        candidates.append(base / f"index{ext}")

    root_resolved = root.resolve()
    for candidate in candidates:
        try:
            rel = candidate.relative_to(root_resolved).as_posix()
        except ValueError:
            continue
        if rel in all_files:
            return rel
    return None


def _parse_js_dependencies(file_path: Path, root: Path, all_files: set) -> set:
    try:
        if file_path.stat().st_size > MAX_DEPENDENCY_FILE_CHARS:
            return set()
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()

    deps = set()
    for m in _JS_IMPORT_RE.finditer(source):
        spec = m.group(1)
        resolved = _resolve_js_relative_import(spec, file_path, root, all_files)
        if resolved:
            deps.add(resolved)
    return deps


def _find_cycles(graph: dict, limit: int) -> list:
    """Iterative (non-recursive, so a large project can't blow the
    interpreter's recursion limit) DFS cycle detection using the standard
    white/gray/black coloring. Returns up to `limit` example cycles (each a
    list of file paths), not every cycle in the graph — for a real project
    this is meant as a 'here's a smell worth looking at' signal, not an
    exhaustive enumeration."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    cycles = []

    for start in graph:
        if color[start] != WHITE or len(cycles) >= limit:
            continue
        stack = [(start, iter(graph.get(start, ())))]
        path = [start]
        color[start] = GRAY
        while stack and len(cycles) < limit:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in color:
                    continue
                if color[nxt] == GRAY:
                    cycle_start = path.index(nxt)
                    cycles.append(path[cycle_start:] + [nxt])
                elif color[nxt] == WHITE:
                    color[nxt] = GRAY
                    path.append(nxt)
                    stack.append((nxt, iter(graph.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                path.pop()
                color[node] = BLACK
        # Any node left GRAY after exhausting its branch is a bug in the
        # coloring above, not a real state — defensively repaired rather
        # than left to corrupt later starts.
        for node in list(color):
            if color[node] == GRAY:
                color[node] = BLACK

    return cycles[:limit]


def _file_reaches_itself(graph: dict, node: str) -> bool:
    """Is `node` part of ANY circular-dependency chain — i.e. starting
    from one of its own dependencies, can you eventually get back to
    `node`? Deliberately independent of _find_cycles' capped example
    list (DEPENDENCY_GRAPH_PREVIEW_LIMIT) so a focused per-file question
    gets a real yes/no answer even in a large graph where the aggregate
    scan stopped early."""
    if node not in graph:
        return False
    stack = list(graph.get(node, ()))
    visited = set()
    while stack:
        current = stack.pop()
        if current == node:
            return True
        if current in visited:
            continue
        visited.add(current)
        stack.extend(graph.get(current, ()))
    return False


def dependency_graph(project_path: str, file_type: str = None, focus_file: str = None) -> dict:
    """Builds an internal-dependency graph for a project: which of the
    project's own files import which other of the project's own files.
    External packages are recognized as imports but not resolved into
    edges — see this section's module comment for why.

    `file_type` narrows the scan to one ecosystem ('py' or 'js' — the
    latter covers .js/.jsx/.ts/.tsx together, since they're commonly mixed
    in one JS/TS project). Omit it to scan both.

    `focus_file` answers a specific "what does X depend on / what depends
    on X" question — pass a path (absolute, or relative to project_path,
    or just a filename/suffix like 'storage.py' — matched against the
    scanned files, erroring if that's ambiguous) to get that one file's
    own outgoing dependencies (`depends_on`) and incoming dependents
    (`depended_on_by`) back explicitly. Without it, only the project-wide
    aggregate (most-depended-on ranking, cycles) is returned — there's no
    per-file lookup in the aggregate view, so a focused question needs
    this parameter, not just a differently-worded prompt against the
    unfocused result.

    Returns edge counts, the most-depended-on files (useful before
    deleting or refactoring one — these are the files with the most
    "blast radius"), and up to a handful of example circular-dependency
    chains if any were found."""
    root = Path(project_path).expanduser().resolve()
    if not root.exists():
        return {"status": "error", "message": f"path not found: {root}"}
    if not root.is_dir():
        return {"status": "error", "message": f"not a directory: {root}"}

    if file_type is not None and file_type not in DEPENDENCY_GRAPH_EXTENSIONS:
        return {"status": "error",
                "message": f"file_type must be one of {sorted(DEPENDENCY_GRAPH_EXTENSIONS)} "
                            f"or omitted to scan both; got {file_type!r}"}
    ecosystems = [file_type] if file_type else list(DEPENDENCY_GRAPH_EXTENSIONS)
    extensions = {ext for eco in ecosystems for ext in DEPENDENCY_GRAPH_EXTENSIONS[eco]}

    all_files = set()
    file_count = 0
    for fpath, _size in _walk_project(root):
        if fpath.suffix.lower() not in extensions:
            continue
        if file_count >= MAX_DEPENDENCY_FILES:
            break
        try:
            rel = fpath.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        all_files.add(rel)
        file_count += 1
    truncated = file_count >= MAX_DEPENDENCY_FILES

    graph: dict = {rel: set() for rel in all_files}
    for rel in all_files:
        fpath = root / rel
        if fpath.suffix.lower() == ".py":
            graph[rel] = _parse_python_dependencies(fpath, root, all_files)
        else:
            graph[rel] = _parse_js_dependencies(fpath, root, all_files)

    total_edges = sum(len(deps) for deps in graph.values())

    in_degree: dict = {rel: 0 for rel in all_files}
    for deps in graph.values():
        for dep in deps:
            in_degree[dep] = in_degree.get(dep, 0) + 1
    most_depended_on = [
        {"file": f, "depended_on_by_count": c}
        for f, c in sorted(in_degree.items(), key=lambda kv: -kv[1])[:DEPENDENCY_GRAPH_PREVIEW_LIMIT]
        if c > 0
    ]

    cycles = _find_cycles(graph, DEPENDENCY_GRAPH_PREVIEW_LIMIT)

    result = {
        "status": "ok",
        "project_path": str(root),
        "files_scanned": len(all_files),
        "total_internal_dependency_edges": total_edges,
        "most_depended_on": most_depended_on,
        "cycles_found": len(cycles),
        "example_cycles": [" -> ".join(c) for c in cycles],
        "truncated": truncated,
        "message": f"Scanned {len(all_files)} files under {root}: {total_edges} internal "
                    f"dependency edges, {len(cycles)} circular-dependency chain(s) found."
                    + (" (capped — very large tree)" if truncated else ""),
    }

    if focus_file is None:
        return result

    # Resolve focus_file against the scanned set: try an exact relative
    # match first (handles both a path relative to project_path and an
    # absolute path under it), then fall back to a suffix match (handles
    # a bare filename like 'storage.py' or a partial path) — but only if
    # that suffix match is unambiguous, since silently picking one of
    # several same-named files in different directories would be a wrong
    # answer dressed up as a right one.
    focus_path = Path(focus_file).expanduser()
    candidate = None
    try:
        candidate = focus_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        candidate = focus_file.replace("\\", "/").lstrip("./")

    if candidate in all_files:
        matched = candidate
    else:
        suffix_matches = [f for f in all_files if f == candidate or f.endswith("/" + candidate)
                           or Path(f).name == candidate]
        if len(suffix_matches) == 1:
            matched = suffix_matches[0]
        elif len(suffix_matches) > 1:
            result["status"] = "error"
            result["message"] = (f"focus_file {focus_file!r} matches multiple scanned files: "
                                  f"{sorted(suffix_matches)} — give a more specific path.")
            return result
        else:
            result["status"] = "error"
            result["message"] = (f"focus_file {focus_file!r} was not found among the "
                                  f"{len(all_files)} files scanned under {root}.")
            return result

    result["focus_file"] = matched
    result["depends_on"] = sorted(graph.get(matched, set()))
    result["depended_on_by"] = sorted(f for f, deps in graph.items() if matched in deps)

    # Whether `matched` is part of ANY circular-dependency chain — checked
    # independently of `cycles` above, since that list is capped at
    # DEPENDENCY_GRAPH_PREVIEW_LIMIT and could stop before ever reaching
    # this file's own cycle in a large graph. This is the actual answer to
    # "is X involved in a circular import", not an inference from the
    # capped example list.
    in_cycle = _file_reaches_itself(graph, matched)
    result["in_cycle"] = in_cycle
    matching_example = next((c for c in cycles if matched in c), None)

    cycle_sentence = (
        f" It IS part of a circular dependency chain"
        + (f" ({' -> '.join(matching_example)})" if matching_example else "")
        + "." if in_cycle else
        " It is not part of any detected circular dependency chain."
    )
    result["message"] = (
        f"{matched} depends on {len(result['depends_on'])} file(s) in this project"
        + (f" ({', '.join(result['depends_on'])})" if result["depends_on"] else "")
        + f", and is depended on by {len(result['depended_on_by'])} file(s)"
        + (f" ({', '.join(result['depended_on_by'])})" if result["depended_on_by"] else "")
        + "."
        + cycle_sentence
    )
    return result


# ---------------------------------------------------------------------------
# Milestone 3 — Knowledge Companion (narrow first slice, extended)
#
# Per IMPLEMENTATION_STATE.md's recommendation: one tool, not the whole
# Knowledge Companion at once. summarize_document is the first thing that
# genuinely needs the reasoning model rather than deterministic scanning —
# but the deterministic 80% still happens entirely in this function before
# the LLM is ever touched: path validation, extension check, extracting the
# text (plain read for text files, pypdf for PDFs), and truncating it to a
# sane size. The LLM call at the end is the only non-deterministic step,
# and it's the *reasoning* model (same one main.py's run_big_model uses for
# L4), not the small router model — summarization is real reasoning, not
# tool-call classification.
#
# Originally scoped to plain-text files only. PDF support (a prior session)
# added `pypdf`. DOCX support (this session) adds `python-docx` — same
# reasoning as pypdf: pure Python, no native deps, acceptable RAM cost on
# 16GB, and directly in service of the "read these PDFs"-style examples in
# spec §8 (DOCX is the other common document-type gap). Genuinely
# unimplemented formats (.doc, .odt, .rtf, etc.) still return a clear
# "not implemented yet" message rather than a wrong/silent result.
# ---------------------------------------------------------------------------

# A local, one-off call to the reasoning model. Deliberately NOT imported
# from main.py (main.py already imports tools.py — from tools import TOOLS —
# so the reverse import would be circular). Small, intentional duplication
# of main.py's run_big_model()/OLLAMA_URL/CODER_MODEL rather than a shared
# module: this is the only tool that needs it today, and a real shared
# module is worth introducing the day a second tool needs the same call,
# not before (principle #4 — prefer modifying existing components over
# introducing new abstractions).
SUMMARIZE_OLLAMA_URL = "http://localhost:11434/api/generate"
SUMMARIZE_REASONING_MODEL = "qwen3:4b-instruct"  # keep in sync with main.py's CODER_MODEL

# Plain-text-readable extensions. Deliberately a conservative allowlist
# rather than "anything read_text() doesn't choke on" — a binary file that
# happens to decode without raising isn't the same as a document worth
# summarizing.
SUMMARIZABLE_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log",
    ".py", ".js", ".ts", ".json", ".yml", ".yaml",
}

# PDF is handled separately (needs pypdf, not a plain text read) but is
# still a "supported" extension for summarize_document's purposes.
PDF_EXTENSIONS = {".pdf"}

# Same story as PDF: needs python-docx, not a plain text read. Only modern
# .docx (Office Open XML) — the legacy binary .doc format needs a different
# library entirely and isn't in scope here.
DOCX_EXTENSIONS = {".docx"}

# Cap on how much raw text is read before summarizing — keeps the prompt
# (and therefore RAM/latency on a 16GB machine) bounded regardless of how
# large the source file is. A summary of the first ~40K chars is still a
# useful summary; reading an entire multi-MB log file (or a hundred-page
# PDF) into one prompt would not be.
SUMMARIZE_MAX_CHARS = 40000


def _call_reasoning_model(prompt: str) -> str:
    resp = requests.post(SUMMARIZE_OLLAMA_URL, json={
        "model": SUMMARIZE_REASONING_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "0",
    }, timeout=120)
    resp.raise_for_status()
    return resp.json()["response"]


def _store_knowledge_best_effort(path: str, summary: str, source_type: str,
                                  question: str = None) -> None:
    """Records a summarize_document/summarize_youtube_video result into
    storage.py's `knowledge` table (Milestone 3 — knowledge storage/
    cross-referencing, see search_knowledge() below). Deliberately
    best-effort: a storage hiccup should never turn an otherwise-successful
    summary into an error response, so failures here are swallowed rather
    than propagated — the summary the user asked for still got produced."""
    try:
        storage.store_knowledge(path=path, summary=summary,
                                 source_type=source_type, question=question)
    except Exception:
        pass


def _extract_pdf_text(path: Path) -> tuple:
    """Deterministic PDF text extraction via pypdf. Returns (text, error) —
    exactly one of the two is non-None, so callers don't need to catch
    exceptions themselves. Pages that individually fail to extract (some
    PDFs have malformed page objects) are skipped rather than aborting the
    whole extraction — a partial summary beats no summary."""
    if PdfReader is None:
        return None, "pypdf is not installed; add it to requirements.txt and pip install."
    try:
        reader = PdfReader(str(path))
    except PdfReadError as e:
        return None, f"couldn't parse PDF: {e}"
    except Exception as e:
        return None, f"couldn't open PDF: {e}"

    if reader.is_encrypted:
        # Try an empty password first (common for "restricted but not
        # really locked" PDFs) before giving up — pypdf's own convention.
        try:
            if reader.decrypt("") == 0:
                return None, "PDF is password-protected; can't extract text without the password."
        except Exception:
            return None, "PDF is password-protected; can't extract text without the password."

    pages_text = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            continue  # skip the one bad page rather than aborting the whole file
    text = "\n".join(pages_text)
    if not text.strip():
        return None, ("no extractable text found (this may be a scanned/image-only PDF — "
                       "OCR isn't supported yet).")
    return text, None


def _extract_docx_text(path: Path) -> tuple:
    """Deterministic DOCX text extraction via python-docx. Same (text, error)
    contract as _extract_pdf_text — exactly one of the two is non-None.
    Pulls both paragraph text and table cell text (business documents often
    put the substantive content in tables), in document order for
    paragraphs followed by tables, which is good enough for a summarization
    use case without needing to track XML-level element ordering."""
    if docx_lib is None:
        return None, "python-docx is not installed; add it to requirements.txt and pip install."
    try:
        document = docx_lib.Document(str(path))
    except Exception as e:
        # Covers a corrupt/non-docx file, an unsupported legacy .doc saved
        # with a .docx extension, and password-protected files (python-docx
        # can't open an encrypted OOXML package at all — it isn't a
        # "wrong password" case like pypdf's empty-password retry, just an
        # unreadable zip from this library's point of view).
        return None, f"couldn't open DOCX (corrupt, password-protected, or not a valid .docx file): {e}"

    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)

    text = "\n".join(parts)
    if not text.strip():
        return None, "no extractable text found in this DOCX file."
    return text, None


def summarize_document(path: str, question: str = None) -> dict:
    """Reads a local document (plain-text, PDF, or DOCX) and produces a
    summary via the reasoning model. All deterministic work (path/extension
    validation, text extraction, truncation) happens before the single LLM
    call at the end.

    Parameter is named `path` (not `file_path`) deliberately — main.py's
    PARAM_ALIASES already maps common router guesses (file, file_path,
    filepath) onto `path`, the same convention delete_file() uses, so this
    tool inherits that aliasing for free instead of needing new entries.

    If `question` is given, the summary is focused on answering it instead
    of producing a generic overview — e.g. "what does this document say
    about pricing?" instead of "summarize this document"."""
    path = Path(path).expanduser()
    if not path.exists():
        return {"status": "error", "message": f"path not found: {path}"}
    if path.is_dir():
        return {"status": "error", "message": f"{path} is a directory, not a file"}

    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        text, error = _extract_pdf_text(path)
        if error:
            return {"status": "error", "message": error}
    elif suffix in DOCX_EXTENSIONS:
        text, error = _extract_docx_text(path)
        if error:
            return {"status": "error", "message": error}
    elif suffix in SUMMARIZABLE_EXTENSIONS:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"status": "error", "message": str(e)}
        if not text.strip():
            return {"status": "error", "message": f"{path} is empty"}
    else:
        return {"status": "not_implemented",
                "message": f"summarize_document reads plain-text files, PDFs, and DOCX today "
                            f"({sorted(SUMMARIZABLE_EXTENSIONS | PDF_EXTENSIONS | DOCX_EXTENSIONS)}); "
                            f"'{suffix or 'no extension'}' isn't wired up yet "
                            f"(legacy .doc, .odt, .rtf, etc. would each need their own "
                            f"extraction dependency)."}

    truncated = len(text) > SUMMARIZE_MAX_CHARS
    excerpt = text[:SUMMARIZE_MAX_CHARS]

    if question:
        prompt = (f"Answer the following question using only the document below. "
                  f"If the document doesn't address it, say so plainly.\n\n"
                  f"Question: {question}\n\nDocument ({path.name}):\n{excerpt}")
    else:
        prompt = (f"Summarize the following document concisely, covering only what "
                  f"actually matters — key points, decisions, or conclusions. Skip "
                  f"boilerplate.\n\nDocument ({path.name}):\n{excerpt}")

    try:
        summary = _call_reasoning_model(prompt)
    except Exception as e:
        return {"status": "error", "message": f"summarization model call failed: {e}"}

    _store_knowledge_best_effort(path=str(path), summary=summary,
                                  source_type="document", question=question)

    return {"status": "ok", "file_path": str(path), "chars_read": len(excerpt),
            "truncated": truncated, "summary": summary,
            "message": f"Summarized {path.name} ({len(excerpt)} chars read"
                        + (", truncated" if truncated else "") + ")."}


# ---------------------------------------------------------------------------
# Milestone 3 — Knowledge Companion, second slice: summarize_youtube_video.
#
# Same shape as summarize_document: deterministic work first (extract the
# video ID, fetch the transcript, truncate it), one reasoning-model call at
# the end. The transcript fetch itself is deterministic — no LLM involved —
# via youtube_transcript_api, which scrapes YouTube's public caption tracks
# (no API key needed, same "no official API" posture as play_youtube_video's
# search scraping above). Reuses _call_reasoning_model and
# SUMMARIZE_MAX_CHARS from summarize_document rather than duplicating them,
# since this is now the second tool that needs the same reasoning-model
# call (see the note on _call_reasoning_model above — a second consumer is
# exactly the point at which duplication should stop).
# ---------------------------------------------------------------------------

# Matches the video ID out of the common URL shapes (watch?v=, youtu.be/,
# /embed/, /shorts/); a bare 11-char ID passed directly is also accepted
# (the fallback pattern below) so callers don't have to build a full URL.
_YOUTUBE_URL_PATTERNS = [
    re.compile(r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([\w-]{11})"),
]


def _extract_youtube_video_id(url: str) -> str | None:
    for pattern in _YOUTUBE_URL_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    # Bare video ID (no URL wrapper at all) — 11 chars, YouTube's ID alphabet.
    if re.fullmatch(r"[\w-]{11}", url.strip()):
        return url.strip()
    return None


def summarize_youtube_video(url: str, question: str = None) -> dict:
    """Fetches a YouTube video's transcript (deterministic, no API key) and
    summarizes it via the reasoning model. Accepts a full YouTube URL
    (watch/youtu.be/embed/shorts) or a bare 11-char video ID.

    If `question` is given, the summary is focused on answering it instead
    of producing a generic overview — same convention as summarize_document.

    Videos without captions (auto-generated or manual) return a clean error;
    this tool does not do audio transcription itself."""
    if YouTubeTranscriptApi is None:
        return {"status": "error",
                "message": "youtube-transcript-api is not installed; "
                            "add it to requirements.txt and pip install."}

    video_id = _extract_youtube_video_id(url)
    if not video_id:
        return {"status": "error",
                "message": f"couldn't find a YouTube video ID in '{url}'"}

    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=("en",))
    except CouldNotRetrieveTranscript as e:
        return {"status": "error",
                "message": f"no transcript available for {video_id}: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"transcript fetch failed: {e}"}

    text = " ".join(snippet.text for snippet in fetched if snippet.text.strip())
    if not text.strip():
        return {"status": "error", "message": f"transcript for {video_id} was empty"}

    truncated = len(text) > SUMMARIZE_MAX_CHARS
    excerpt = text[:SUMMARIZE_MAX_CHARS]

    if question:
        prompt = (f"Answer the following question using only the video transcript "
                  f"below. If the transcript doesn't address it, say so plainly.\n\n"
                  f"Question: {question}\n\nTranscript (video {video_id}):\n{excerpt}")
    else:
        prompt = (f"Summarize the following video transcript concisely — tell me only "
                  f"what actually matters (key points, conclusions, any actionable "
                  f"detail). Skip filler and repetition typical of spoken "
                  f"transcripts.\n\nTranscript (video {video_id}):\n{excerpt}")

    try:
        summary = _call_reasoning_model(prompt)
    except Exception as e:
        return {"status": "error", "message": f"summarization model call failed: {e}"}

    _store_knowledge_best_effort(path=url, summary=summary,
                                  source_type="youtube", question=question)

    return {"status": "ok", "video_id": video_id, "url": url,
            "chars_read": len(excerpt), "truncated": truncated, "summary": summary,
            "message": f"Summarized video {video_id} ({len(excerpt)} chars of transcript"
                        + (", truncated" if truncated else "") + ")."}


# ---------------------------------------------------------------------------
# Milestone 3 — Knowledge Companion, final slice: knowledge storage/
# cross-referencing (spec §8's last piece). Every successful
# summarize_document/summarize_youtube_video call above now writes its
# result to storage.py's `knowledge` table (best-effort — see
# _store_knowledge_best_effort). This tool is the read side: a plain
# keyword/LIKE search over those stored summaries, not semantic/embedding
# search — an embedding store is a real RAM/complexity cost the spec says
# to justify on a 16GB machine, and there's no usage data yet showing
# keyword search is insufficient. Revisit only if that changes.
# ---------------------------------------------------------------------------

def search_knowledge(query: str, limit: int = 10) -> dict:
    """Deterministic keyword search over previously stored
    summarize_document/summarize_youtube_video results. Matches against
    both the source path/URL and the summary text itself, most-recent
    match first. Use this when asked something like "did I already look
    at/summarize X" or "what have I read about Y" — this searches past
    summaries, it does not re-summarize or re-fetch anything."""
    if not query or not query.strip():
        return {"status": "error", "message": "query must not be empty"}

    matches = storage.search_knowledge(query.strip(), limit=limit)
    return {
        "status": "ok",
        "query": query,
        "count": len(matches),
        "matches": matches,
        "message": (f"Found {len(matches)} stored summary matching '{query}'."
                    if len(matches) == 1
                    else f"Found {len(matches)} stored summaries matching '{query}'."),
    }


# Allowlist only — no arbitrary shell execution through the HTTP endpoint.
# Add entries as you find you need them; each maps a safe name to a fixed argv list.
ALLOWED_COMMANDS = {
    "git_status": ["git", "status"],
    "git_pull": ["git", "pull"],
    "npm_install": ["npm", "install"],
    "docker_ps": ["docker", "ps"],
}


def run_shell_command(command_name: str, cwd: str = ".") -> dict:
    if command_name not in ALLOWED_COMMANDS:
        return {"status": "error",
                "message": f"'{command_name}' is not allowlisted. "
                            f"Allowed: {list(ALLOWED_COMMANDS.keys())}"}
    path = Path(cwd).expanduser()
    if not path.exists():
        return {"status": "error", "message": f"path not found: {path}"}
    try:
        result = subprocess.run(ALLOWED_COMMANDS[command_name], cwd=str(path),
                                 capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "command timed out"}
    ok = result.returncode == 0
    return {"status": "ok" if ok else "error",
            "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:],
            "message": f"`{command_name}` {'succeeded' if ok else 'failed'} (exit {result.returncode})."}


def delete_file(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        return {"status": "error", "message": "not found"}
    p.unlink()
    return {"status": "ok", "deleted": str(p), "message": f"Deleted {p}."}


# ---------------------------------------------------------------------------
# Milestone 5.7 — Tier-1 Tool Ecosystem: File Management, expanded past
# delete-only. `delete_file` (above, Milestone 0) is the only irreversible
# one and stays "dangerous". These four are all recoverable mistakes (a
# bad copy/move/rename just needs redoing, nothing is destroyed) so they're
# "caution" tier — confirmed from the phone like delete_file, but a real
# safety distinction if this project's tiers are ever split further (see
# main.py's confirmation-model docstring — today caution and dangerous are
# gated identically, but the label itself is the honest one).
#
# All four share the same no-silent-overwrite posture: an existing
# destination is a real "are you sure" moment, not something to clobber
# quietly, so each requires an explicit `overwrite=True` rather than
# guessing what the user meant.
# ---------------------------------------------------------------------------

READ_FILE_MAX_CHARS = 100_000  # generous but bounded — same cap philosophy as SUMMARIZE_MAX_CHARS


def read_file(path: str) -> dict:
    """Reads a local text file's raw contents (no summarization, no
    reasoning-model call — that's summarize_document's job). Use this when
    the request wants to see/inspect a file's actual content, not a
    summary of it."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"status": "error", "message": f"path not found: {p}"}
    if p.is_dir():
        return {"status": "error", "message": f"{p} is a directory, not a file"}
    try:
        text = p.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"status": "error",
                "message": f"{p} doesn't look like a text file (failed to decode as UTF-8). "
                            f"Binary files aren't supported by read_file."}
    except OSError as e:
        return {"status": "error", "message": str(e)}

    truncated = len(text) > READ_FILE_MAX_CHARS
    content = text[:READ_FILE_MAX_CHARS]
    return {"status": "ok", "path": str(p), "content": content,
            "chars_read": len(content), "truncated": truncated,
            "message": f"Read {p.name} ({len(content)} chars"
                        + (", truncated" if truncated else "") + ")."}


def _validate_file_op_paths(source: str, destination: str):
    """Shared preflight for copy_file/move_file: source must exist and be
    a file (not a directory — a real decision, see each function's own
    docstring for why), destination's parent must already exist (no
    silent mkdir -p — creating directory structure implicitly is its own
    kind of surprising side effect, not bundled into a file-management
    call). Returns (src_path, dst_path, error_dict_or_None)."""
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.exists():
        return src, dst, {"status": "error", "message": f"source not found: {src}"}
    if src.is_dir():
        return src, dst, {"status": "error",
                           "message": f"{src} is a directory — copy_file/move_file handle "
                                       f"single files only, not directory trees, this slice."}
    if not dst.parent.exists():
        return src, dst, {"status": "error",
                           "message": f"destination directory does not exist: {dst.parent}"}
    return src, dst, None


def copy_file(source: str, destination: str, overwrite: bool = False) -> dict:
    """Copies a single file (contents + metadata, via shutil.copy2) to a
    new location. Directory trees are out of scope this slice — shutil's
    own recursive copy has its own set of edge cases (symlinks, partial
    failures partway through a big tree) worth a deliberate look before
    exposing it here, not bundled into the same tool as a single-file
    copy."""
    src, dst, error = _validate_file_op_paths(source, destination)
    if error:
        return error
    if dst.exists() and not overwrite:
        return {"status": "error",
                "message": f"destination already exists: {dst} (pass overwrite=true to replace it)"}
    shutil.copy2(src, dst)
    return {"status": "ok", "source": str(src), "destination": str(dst),
            "message": f"Copied {src.name} to {dst}."}


def move_file(source: str, destination: str, overwrite: bool = False) -> dict:
    """Moves (renames across directories) a single file via shutil.move.
    Same single-file, no-silent-overwrite, parent-must-already-exist rules
    as copy_file — see _validate_file_op_paths and this section's module
    comment."""
    src, dst, error = _validate_file_op_paths(source, destination)
    if error:
        return error
    if dst.exists():
        if not overwrite:
            return {"status": "error",
                    "message": f"destination already exists: {dst} (pass overwrite=true to replace it)"}
        dst.unlink()
    shutil.move(str(src), str(dst))
    return {"status": "ok", "source": str(src), "destination": str(dst),
            "message": f"Moved {src.name} to {dst}."}


def rename_file(path: str, new_name: str, overwrite: bool = False) -> dict:
    """Renames a file IN PLACE (same directory) to `new_name`. Deliberately
    a distinct, narrower tool from move_file rather than move_file with a
    same-directory destination: `new_name` is validated to be a bare
    filename with no path separators, so a rename request can never
    accidentally relocate a file to an unrelated directory through a
    crafted new_name — use move_file for that, explicitly."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"status": "error", "message": f"path not found: {p}"}
    if p.is_dir():
        return {"status": "error", "message": f"{p} is a directory — rename_file handles files only."}
    if "/" in new_name or "\\" in new_name or new_name in ("", ".", ".."):
        return {"status": "error",
                "message": f"new_name must be a bare filename with no path separators, got {new_name!r}. "
                            f"Use move_file to relocate a file to a different directory."}
    new_path = p.parent / new_name
    if new_path.exists():
        if not overwrite:
            return {"status": "error",
                    "message": f"a file named {new_name} already exists in {p.parent} "
                                f"(pass overwrite=true to replace it)"}
        new_path.unlink()
    p.rename(new_path)
    return {"status": "ok", "old_path": str(p), "new_path": str(new_path),
            "message": f"Renamed {p.name} to {new_name}."}


# ---------------------------------------------------------------------------
# Milestone 5.7 — Tier-1 Tool Ecosystem: Clipboard. macOS-native via
# pbpaste/pbcopy (same `open`/AppleScript-adjacent, no-new-pip-dependency
# posture as open_app/open_url) rather than a cross-platform library —
# this project only targets macOS today, so pbcopy/pbpaste is the
# deterministic, zero-new-dependency choice, same reasoning that kept
# play_youtube_video off a real YouTube API.
# ---------------------------------------------------------------------------

CLIPBOARD_MAX_CHARS = 100_000  # same cap philosophy as READ_FILE_MAX_CHARS/SUMMARIZE_MAX_CHARS


def read_clipboard() -> dict:
    """Reads the current macOS clipboard's text contents via `pbpaste`.
    Read-only, no side effects — "safe" tier, same as read_file. An empty
    clipboard is not an error (returns content="")."""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, timeout=5)
    except FileNotFoundError:
        return {"status": "error", "message": "pbpaste not found — clipboard tools require macOS"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timed out reading clipboard"}
    if result.returncode != 0:
        return {"status": "error",
                "message": f"pbpaste failed: {result.stderr.decode(errors='replace').strip()}"}
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "error",
                "message": "clipboard contents aren't valid UTF-8 text (binary/image "
                            "clipboard content isn't supported)"}
    truncated = len(text) > CLIPBOARD_MAX_CHARS
    content = text[:CLIPBOARD_MAX_CHARS]
    return {"status": "ok", "content": content, "chars_read": len(content), "truncated": truncated,
            "message": f"Read {len(content)} chars from clipboard"
                        + (", truncated" if truncated else "") + "."}


def write_clipboard(text: str) -> dict:
    """Overwrites the macOS clipboard with `text` via `pbcopy`, replacing
    whatever's there now. "caution" tier (confirmed from the phone) —
    same reasoning as copy_file/move_file/rename_file: not destructive to
    any file, but a real "are you sure" moment since it silently clobbers
    something the user may have deliberately copied for later, and there's
    no undo once something new has been copied over it."""
    if not text:
        return {"status": "error", "message": "text must be a non-empty string"}
    try:
        result = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5)
    except FileNotFoundError:
        return {"status": "error", "message": "pbcopy not found — clipboard tools require macOS"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timed out writing clipboard"}
    if result.returncode != 0:
        return {"status": "error", "message": "pbcopy failed"}
    return {"status": "ok", "chars_written": len(text), "message": f"Copied {len(text)} chars to clipboard."}


TOOLS = {
    "open_app":          {"fn": open_app,         "tier": "safe",
                           "desc": "Open a macOS app by name, e.g. 'Spotify'"},
    "open_url":          {"fn": open_url,          "tier": "safe",
                           "desc": "Open a website in the default browser, e.g. 'youtube.com'. "
                                   "Use this instead of open_app for websites/web services "
                                   "(YouTube, Gmail, GitHub, Google, etc.) that aren't installed Mac apps. "
                                   "Pass the optional 'search' param with a query to search that site "
                                   "directly (e.g. url='youtube.com', search='spaghetti recipe') instead "
                                   "of just opening its homepage."},
    "summarize_youtube_video": {"fn": summarize_youtube_video, "tier": "safe",
                           "desc": "Fetch a YouTube video's transcript and summarize it "
                                   "(or answer a question about it). Takes a YouTube URL "
                                   "(watch/youtu.be/embed/shorts) or bare video ID. "
                                   "Fails cleanly if the video has no captions."},
    "play_youtube_video": {"fn": play_youtube_video, "tier": "safe",
                           "desc": "Search YouTube for a query and open/play the FIRST matching video "
                                   "directly (not just a search-results page). Use this whenever the "
                                   "request says 'play' a video, even if it also mentions searching for "
                                   "something first — combine everything relevant (topic, creator/channel "
                                   "name, etc.) into one natural search query for the 'query' param. "
                                   "e.g. 'search spaghetti and play the You Food Lab video' -> "
                                   "query='You Food Lab spaghetti'."},
    "get_battery":       {"fn": get_battery,       "tier": "safe",
                           "desc": "Get battery percent and charging state"},
    "open_project":      {"fn": open_project,      "tier": "safe",
                           "desc": "Open a project folder in VS Code"},
    "run_localhost":     {"fn": run_localhost,     "tier": "caution",
                           "desc": "Start a dev server for a project path"},
    "get_stock_price":   {"fn": get_stock_price,   "tier": "safe",
                           "desc": "Get the current price for a stock ticker"},
    "send_email":        {"fn": send_email,        "tier": "dangerous",
                           "desc": "Send an email to someone"},
    "run_shell_command": {"fn": run_shell_command, "tier": "dangerous",
                           "desc": "Run an allowlisted shell command by name"},
    "delete_file":       {"fn": delete_file,       "tier": "dangerous",
                           "desc": "Delete a file at a given path"},
    "read_file":         {"fn": read_file,         "tier": "safe",
                           "desc": "Read a local text file's raw contents (not a summary — use "
                                   "summarize_document for that). Fails cleanly on binary files "
                                   "or files that aren't UTF-8 text."},
    "copy_file":         {"fn": copy_file,         "tier": "caution",
                           "desc": "Copy a single file to a new path. Destination's parent "
                                   "directory must already exist. Fails if the destination "
                                   "already exists unless 'overwrite' is set to true."},
    "move_file":         {"fn": move_file,         "tier": "caution",
                           "desc": "Move (relocate, possibly across directories) a single file "
                                   "to a new path. Destination's parent directory must already "
                                   "exist. Fails if the destination already exists unless "
                                   "'overwrite' is set to true."},
    "rename_file":       {"fn": rename_file,       "tier": "caution",
                           "desc": "Rename a file IN PLACE (same directory) — 'new_name' must be "
                                   "a bare filename, not a path. Use move_file instead if the "
                                   "request is to relocate a file to a different directory."},
    "dependency_graph":  {"fn": dependency_graph,  "tier": "safe",
                           "desc": "Build an internal-dependency graph for a Python and/or "
                                   "JavaScript/TypeScript project: which of the project's own "
                                   "files import which other of its own files (external packages "
                                   "aren't resolved). Reports the most-depended-on files (highest "
                                   "'blast radius' if changed or deleted) and any circular-"
                                   "dependency chains found. Optional file_type ('py' or 'js') "
                                   "narrows the scan to one ecosystem. IMPORTANT: for any question "
                                   "about ONE specific file — 'what does X depend on', 'what "
                                   "depends on X', 'is X involved in a circular import' — always "
                                   "pass focus_file=<that file's name or path>, otherwise only the "
                                   "project-wide aggregate is returned and the specific question "
                                   "goes unanswered."},
    "index_project":     {"fn": index_project,     "tier": "safe",
                           "desc": "Walk a project directory once and summarize it: total file "
                                   "count, total size, language breakdown by file count, and "
                                   "top-level folder structure. Skips node_modules/venv/.git/"
                                   "build noise automatically. Cached for 5 minutes per path, so "
                                   "asking again about the same project is instant."},
    "git_query":         {"fn": git_query,          "tier": "safe",
                           "desc": "Read-only git introspection — status, log, diff, or current "
                                   "branch. Never needs confirmation since nothing is mutated. "
                                   f"command_name must be one of: {list(GIT_READONLY_COMMANDS.keys())}"},
    "search_code":       {"fn": search_code,        "tier": "safe",
                           "desc": "Search a project's source files for a text/regex pattern "
                                   "(ripgrep-backed). Optional file_type narrows to one language, "
                                   "e.g. 'py' or 'js'."},
    "read_zip":          {"fn": read_zip,           "tier": "safe",
                           "desc": "Inspect a .zip file's contents WITHOUT extracting it: file "
                                   "count, uncompressed size, and language breakdown. Use this "
                                   "when asked to review or analyze an uploaded project zip."},
    "summarize_document": {"fn": summarize_document, "tier": "safe",
                           "desc": "Summarize a local document: plain-text (.txt/.md/.rst/.log/"
                                   ".py/.js/.ts/.json/.yml), PDF, or DOCX. Pass an optional "
                                   "'question' param to focus the summary on answering a "
                                   "specific question about the document instead of a generic "
                                   "overview. Use this when asked to summarize, read, or answer "
                                   "questions about a specific file's contents."},
    "search_knowledge":  {"fn": search_knowledge,  "tier": "safe",
                           "desc": "Search PAST summaries already produced by summarize_document "
                                   "or summarize_youtube_video (keyword search over stored "
                                   "path/URL and summary text). Use this for requests like 'did "
                                   "I already summarize/read X' or 'what have I looked at about "
                                   "Y' — it does not summarize or fetch anything new."},
    "read_clipboard":    {"fn": read_clipboard,    "tier": "safe",
                           "desc": "Read the current macOS clipboard's text contents. Read-only, "
                                   "no params. Use this for requests like 'what's on my "
                                   "clipboard' or 'read what I copied'."},
    "write_clipboard":   {"fn": write_clipboard,   "tier": "caution",
                           "desc": "Overwrite the macOS clipboard with the given 'text', "
                                   "replacing whatever's there now. Use this for requests like "
                                   "'copy <text> to my clipboard' or 'put <text> on the "
                                   "clipboard'."},
}