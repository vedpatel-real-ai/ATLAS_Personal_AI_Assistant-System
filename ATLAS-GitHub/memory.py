"""
Context Manager — Milestone 2 (see ATLAS spec §3.3, §4).

Wires request_context.py's memory-backed placeholder fields
(current_project, current_directory, conversation_history, preferences)
to real, durable, SQLite-backed storage (storage.py's `conversations`,
`projects`, and `preferences` tables), replacing the hardcoded empty
defaults Milestone 0.5 shipped as an explicit stopgap.

Three concepts, one storage.py table each:

1. Conversation memory (`conversations`) — a rolling, append-only log of
   what was said, on both sides, per turn. get_conversation_history()
   only ever returns the last CONVERSATION_HISTORY_LIMIT turns; nothing
   here means every request's prompt grows to include the whole history
   — that would burn context tokens and RAM for no benefit on a 16GB
   machine. Older turns stay on disk (cheap — SQLite rows, not a
   resident model) for a possible future semantic-recall feature, not
   loaded by default.

2. Project memory (`projects`) — the durable replacement for
   tools.py's index_project() in-memory TTL cache, which shipped in
   Milestone 1 as an explicit stopgap ("Milestone 2 (real memory) can
   replace this with something durable later"). Visiting a project
   (successfully indexing it, or opening it in VS Code) marks it
   "current" via the `current_project_path` preference, so a later
   "continue yesterday's project" has something concrete to resolve
   even across a process restart — the in-memory cache didn't survive
   one.

3. Preferences (`preferences`) — a small durable key/value store for
   anything the assistant should just remember without being told again
   each session. `current_project_path` is itself stored here.

This module is the ONLY thing that should read/write those three
tables — request_context.py calls recall_context() once per request,
and main.py calls remember_exchange()/remember_project_visit() as
requests are handled. Nothing else should query storage.py's
conversations/projects/preferences tables directly, same "one obvious
place" discipline Milestone 0.5 established for RequestContext
construction itself.

Deliberately out of scope for Milestone 2: `current_app` (no macOS
active-app-tracking tool exists yet to populate it from — inventing one
wasn't asked for by this milestone and isn't needed by any tool today);
memory consolidation/summarization of old conversation turns; and
semantic/embedding-based recall (spec §4 flags both as later work, only
worth doing once there's an actual usage pattern that needs them).
"""
from typing import Optional

import storage

CONVERSATION_HISTORY_LIMIT = 10  # turns returned to a fresh RequestContext, not the whole history

CURRENT_PROJECT_PREF_KEY = "current_project_path"


def remember_exchange(role: str, text: str, project: Optional[str] = None) -> None:
    """Append one turn (role is 'user' or 'assistant') to durable
    conversation memory. Best-effort: a memory write failing should
    never break the request it's logging — same posture as the JSONL
    tail in storage.log_event()."""
    if not text:
        return
    try:
        storage.add_conversation_turn(role=role, text=text, project=project)
    except Exception:
        pass


def get_conversation_history(limit: int = CONVERSATION_HISTORY_LIMIT) -> list:
    try:
        return storage.get_recent_conversation(limit=limit)
    except Exception:
        return []


def remember_project_visit(path: str, index_result: Optional[dict] = None) -> None:
    """Call whenever a request successfully touches a project (today:
    index_project or open_project succeeding). Upserts the project's
    latest known summary (if index_result — e.g. index_project()'s
    return value — is available) and marks it as the current project."""
    if not path:
        return
    try:
        storage.upsert_project(
            path=path,
            total_files=(index_result or {}).get("total_files"),
            total_size_mb=(index_result or {}).get("total_size_mb"),
            languages=(index_result or {}).get("languages"),
            top_level=(index_result or {}).get("top_level_entries"),
        )
        storage.set_preference(CURRENT_PROJECT_PREF_KEY, path)
    except Exception:
        pass


def get_current_project() -> Optional[dict]:
    """Returns the most recently visited project's durable summary, or
    None if nothing's been visited yet. Shape matches (a subset of)
    index_project()'s result so callers don't need to handle two
    different formats."""
    try:
        path = storage.get_preference(CURRENT_PROJECT_PREF_KEY)
        if not path:
            return None
        return storage.get_project(path)
    except Exception:
        return None


def get_preferences() -> dict:
    try:
        return storage.get_all_preferences()
    except Exception:
        return {}


def set_preference(key: str, value) -> None:
    try:
        storage.set_preference(key, value)
    except Exception:
        pass


def recall_context() -> dict:
    """The single call site request_context.py uses to populate its
    memory-backed fields. Returns a dict shaped exactly like the fields
    RequestContext expects. Never raises — a memory-layer problem should
    degrade to Milestone 0.5's empty defaults, not break the request."""
    try:
        project = get_current_project()
    except Exception:
        project = None
    return {
        "current_project": project["path"] if project else None,
        "current_directory": project["path"] if project else None,
        "conversation_history": get_conversation_history(),
        "preferences": get_preferences(),
    }