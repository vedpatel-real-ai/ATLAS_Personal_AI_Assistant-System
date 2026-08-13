"""
Planner — spec §5 (Milestone 4).

Scope call made up front, worth stating before the code: this is
deliberately NOT a task-graph engine. matcher.py's chain-splitting and
router.py's multi-step planning already turn "open spotify and check my
battery and open vscode" into an ordered list of steps, and per the
spec's own Deliverable 12 ("things NOT to build") and the recommended-
next-milestone note in IMPLEMENTATION_STATE.md, the real daily commands
this system exists for rarely need more than 2-3 sequential steps with
no branching or fan-out. Building dependency-graph resolution, parallel
execution, or a generic scheduler for that workload would be exactly the
kind of overengineering the project's own principles (#1, #5, #6) argue
against — complexity with no corresponding weekly time savings.

What THIS module adds on top of the existing matcher/router step list,
because each has a concrete, currently-missing consumer:

1. A stable, structured shape for a step (`PlannedStep`) instead of a
   bare {"tool", "params"} dict — carries step_index, attempt count, and
   status, so `RequestContext.tool_candidates` (reserved since Milestone
   0.5, unused until now) has something real to hold, and so the
   /command response can show per-step outcome instead of only a joined
   summary string.
2. One retry, for one specific, real failure mode: `play_youtube_video`
   is the only tool that makes an external network call (a public
   YouTube search-page fetch — see tools.py's own docstring on this).
   Every other tool is a local subprocess/filesystem/psutil call, where a
   retry buys nothing (a missing path or a bad app name isn't going to
   fix itself half a second later). So retry policy is a named,
   maintained set (`RETRYABLE_TOOLS`), not a blanket "retry anything
   that failed" rule that would silently double-run tools like
   `delete_file` or `run_shell_command` if they were ever miscategorized
   as retryable. Capped at one retry, no backoff — a second immediate
   attempt is enough to ride out a transient network hiccup without
   turning a failed command into a multi-second stall waiting for the
   phone response.
3. Cancellation and live progress streaming were both considered and
   deliberately NOT built this milestone. main.py's /command endpoint is
   a single synchronous request/response — the phone is already blocked
   waiting on the reply, so a "cancel mid-chain" primitive would need
   background task execution plus a second endpoint to signal
   cancellation into it, and a live progress surface would need
   SSE/websockets. That's real new infrastructure, not a drive-by
   addition, and nothing about today's 2-3-step chains runs long enough
   to need interrupting — the whole point of Milestone 0's L0 path and
   this system's performance budget is that these stay sub-second to a
   few seconds. Revisit only if a genuinely long-running chain (e.g. a
   real Knowledge Companion workflow, Milestone 3) shows up with an
   actual need to interrupt it, not preemptively.

Milestone 5.6 update: that "revisit" point has come — jobs.py/main.py's
`/command/async` now needs exactly the two hooks flagged above as out of
scope. `execute_plan()` below gained two optional keyword-only-in-spirit
params, `on_progress` and `should_cancel`, both defaulting to `None` so
every existing caller (the synchronous /command path, every test in
tests/test_planner.py) is unaffected — this is additive, not a rewrite.
This module still does NOT do task-graph resolution, parallelism, or
per-step timeouts; it only lets an external caller (jobs.py) observe
progress after each step and ask the loop to stop before the next one,
which is the actual shape the mobile "monitor and manage a long-running
task" requirement needs — see jobs.py's own docstring for the rest of
the async design (job store, background thread, confirmation resume).
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


# The only tool that makes a real external network call today (a YouTube
# search-page fetch inside play_youtube_video — see tools.py). Every other
# registered tool is local (subprocess/filesystem/psutil), where a retry
# can't fix the underlying problem. Keep this list explicit and small; add
# a tool here only when it genuinely does its own outbound network call.
RETRYABLE_TOOLS = {"play_youtube_video"}
MAX_ATTEMPTS = 2  # one original attempt + one retry


@dataclass
class PlannedStep:
    step_index: int
    tool: str
    params: dict
    status: str = "pending"  # pending | ok | failed | unknown_tool
    attempts: int = 0
    result: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "step_index": self.step_index,
            "tool": self.tool,
            "params": self.params,
            "status": self.status,
            "attempts": self.attempts,
        }


def build_plan(steps: list) -> list:
    """Wraps a raw {"tool", "params"} step list (from matcher.py or
    router.py, in that order) into PlannedStep objects, in the same
    order — this module doesn't reorder or branch, it only adds
    structure on top of the sequence already decided upstream."""
    return [
        PlannedStep(step_index=i, tool=step.get("tool"), params=step.get("params", {}))
        for i, step in enumerate(steps)
    ]


def execute_plan(planned_steps: list, execute_fn: Callable[[str, dict], dict],
                  on_progress: Optional[Callable[["PlannedStep"], None]] = None,
                  should_cancel: Optional[Callable[[], bool]] = None) -> list:
    """Runs each PlannedStep via execute_fn(tool_name, params) -> exec_info
    (the same shape main.py's execute_tool already returns), in order.
    Retries once, immediately, only for tools in RETRYABLE_TOOLS that
    failed. Returns the same list, mutated in place with final
    status/attempts/result, for the caller to log/summarize/return.

    Milestone 5.6: two optional hooks for jobs.py's background execution,
    both no-ops by default so the existing synchronous /command path (and
    every pre-5.6 test) behaves exactly as before:
      - `should_cancel()`, checked before each step starts. If it returns
        True, remaining steps are marked "cancelled" (not "pending" —
        makes it unambiguous in the response which steps never got a
        chance to run vs. which ones genuinely weren't reached because an
        earlier one failed) and the loop stops. A step already in
        progress when cancellation is requested still finishes — there's
        no way to interrupt a tool call already inside execute_fn.
      - `on_progress(planned)`, called after each step finishes (success,
        failure, or being marked cancelled), so a caller can persist
        incremental progress (e.g. jobs.storage.update_job) as the chain
        runs instead of only learning the outcome once every step is
        done.
    """
    for planned in planned_steps:
        if should_cancel is not None and should_cancel():
            planned.status = "cancelled"
            if on_progress is not None:
                on_progress(planned)
            continue

        attempts_allowed = MAX_ATTEMPTS if planned.tool in RETRYABLE_TOOLS else 1
        exec_info = None
        for attempt in range(1, attempts_allowed + 1):
            planned.attempts = attempt
            exec_info = execute_fn(planned.tool, planned.params)
            if exec_info["success"]:
                break
        planned.status = "ok" if exec_info["success"] else "failed"
        planned.result = exec_info
        if on_progress is not None:
            on_progress(planned)
    return planned_steps