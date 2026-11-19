"""
Async Execution & Task Progress — Milestone 5.6.

Scope call worth stating up front, same discipline as planner.py's own
docstring: this is deliberately NOT a general task queue. There's no
worker pool, no retry-with-backoff at the job level (planner.py already
owns retry at the step level), no persistence of *which thread* was
running a job, and no resumption of an in-flight job across a process
restart. What this module adds is exactly what the roadmap's dependency
graph asked for — job IDs, background execution, progress updates,
cancellation, and an approval callback — sized to this project's actual
need (a phone that shouldn't have to block on a long `/command` call),
not a generic job-scheduling framework.

Two kinds of state, split deliberately:

1. Durable state (storage.py's `jobs` table) — status, progress, result,
   error, and (for a confirmation-gated job) the pending step list. This
   survives a process restart, so `/jobs/{id}` can always answer "what
   happened to that job" even if the Mac rebooted mid-run.
2. In-process state (this module's `_cancel_events` dict) — a
   `threading.Event` per in-flight job, used purely to signal
   cancellation into the background thread running it. This does NOT
   survive a restart, and can't: the thread itself is gone. A job that
   was "running" when the process died will sit at its last-persisted
   status forever unless something explicitly re-derives it — that's a
   known, accepted gap (see `reconcile_orphaned_jobs()` below), not an
   oversight. This project's listener is meant to stay up all day (see
   main.py's own docstring), so a mid-job restart is expected to be rare,
   not a normal-operation case worth over-engineering around.

Execution model: `run_in_background()` is a thin `threading.Thread`
wrapper — no pool, no queue, no max-concurrency limit. The process-local model lock in main.py
covers classification and direct reasoning. Tools that summarize content
make their own model calls. The SQLite layer locks its writes.

Cancellation is best-effort and step-granular, not mid-step: see
`planner.execute_plan()`'s own docstring for exactly when a cancel
request takes effect (between steps only — nothing can interrupt an
in-flight tool call or an in-flight Ollama generation).
"""
import threading
import uuid
from typing import Callable, Optional

import storage

# One threading.Event per currently-tracked job, used only to signal a
# cancellation request into that job's background thread. Entries are
# added when a job starts running and removed once it reaches a terminal
# status (done/failed/cancelled) — see cleanup_job(). Deliberately NOT
# persisted (see module docstring, point 2).
_cancel_events: dict = {}
_events_lock = threading.Lock()

TERMINAL_STATUSES = {"done", "failed", "cancelled"}


def new_job_id() -> str:
    """Short, URL-friendly id — doesn't need full UUID entropy (this isn't
    a security token like storage.py's confirmation tokens), just
    uniqueness for a handful of concurrent jobs on one Mac."""
    return uuid.uuid4().hex[:12]


def create_job(job_id: str, text: Optional[str] = None, route: Optional[str] = None,
               level: Optional[str] = None, project: Optional[str] = None,
               total_steps: int = 0, status: str = "queued",
               steps: Optional[list] = None, unknown_steps: Optional[list] = None) -> None:
    """Persists a new job row and registers a fresh cancellation flag for
    it. Call this once per job_id, before handing it to run_in_background
    (or before returning an `awaiting_confirmation` response, for a job
    that won't start running until /jobs/{id}/confirm)."""
    storage.create_job(job_id, text=text, route=route, level=level, project=project,
                        total_steps=total_steps, status=status, steps=steps,
                        unknown_steps=unknown_steps)
    with _events_lock:
        _cancel_events[job_id] = threading.Event()


def update_progress(job_id: str, current_step_index: Optional[int] = None,
                     status: Optional[str] = None, progress: Optional[list] = None,
                     result: Optional[dict] = None, error: Optional[str] = None) -> None:
    """Thin pass-through to storage.update_job — kept here (rather than
    having callers import storage directly for this) so main.py's worker
    functions have one obvious module to depend on for all job state,
    same as they already do for planner.py/memory.py."""
    storage.update_job(job_id, status=status, current_step_index=current_step_index,
                        progress=progress, result=result, error=error)
    if status in TERMINAL_STATUSES:
        cleanup_job(job_id)


def get_job(job_id: str) -> Optional[dict]:
    return storage.get_job(job_id)


def request_cancel(job_id: str) -> bool:
    """Signals cancellation into job_id's background thread. Returns False
    if job_id isn't currently tracked — either it's already finished (and
    cleanup_job already removed its event), it's still awaiting
    confirmation (never started running, so there's no thread to signal —
    the caller should just not confirm it instead), or it never existed.
    True only means the flag was set, not that the job actually stopped —
    see planner.execute_plan()'s docstring for why that's inherently
    best-effort."""
    with _events_lock:
        event = _cancel_events.get(job_id)
    if event is None:
        return False
    event.set()
    return True


def is_cancel_requested(job_id: str) -> bool:
    with _events_lock:
        event = _cancel_events.get(job_id)
    return event.is_set() if event is not None else False


def cleanup_job(job_id: str) -> None:
    """Drops job_id's in-process cancellation flag once it's reached a
    terminal status. The durable jobs row is left alone — /jobs/{id}
    should still answer for a finished job, only the in-memory
    cancellation machinery (which has nothing left to signal into) is
    freed."""
    with _events_lock:
        _cancel_events.pop(job_id, None)


def run_in_background(fn: Callable, *args, **kwargs) -> threading.Thread:
    """Starts fn(*args, **kwargs) on a daemon thread and returns it. A
    daemon thread so an unclean process exit (e.g. `kill -9` from
    stop.sh's fallback path) can't hang shutdown waiting on a stuck job —
    consistent with this always-on listener's own "never let one bad
    request take down the process" principle (see main.py's
    execute_tool())."""
    thread = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


def reconcile_orphaned_jobs() -> int:
    """Best-effort cleanup for the restart gap described in this module's
    docstring: any job still marked 'running' or 'awaiting_confirmation'
    at process start has no live thread behind it (this process just
    started) and never will again. Marks each as 'failed' with a clear
    error instead of leaving it silently stuck forever, so a poller gets
    an honest terminal answer rather than an indefinite 'running'. Meant
    to be called once from main.py at startup, same spot as
    storage.init_db()/prune_expired_confirmations(). Returns the count of
    jobs reconciled, for an optional startup log line."""
    orphaned = [j for j in storage.query_recent_jobs(limit=1000)
                if j["status"] in ("running", "awaiting_confirmation")]
    for job in orphaned:
        storage.update_job(job["job_id"], status="failed",
                            error="orphaned: no live process across a restart")
    return len(orphaned)