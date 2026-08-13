"""
Tests for planner.py (Milestone 4). Covers step-list -> PlannedStep
wrapping, the retry-once-for-retryable-tools policy, and that
non-retryable tools are never retried even when they fail.
"""

import planner


def _ok(tool, params):
    return {
        "success": True,
        "result": {"status": "ok"},
        "latency_s": 0.01,
        "ram_before_mb": 100.0,
        "ram_after_mb": 100.0,
    }


def _fail(tool, params):
    return {
        "success": False,
        "result": {"status": "error", "message": "boom"},
        "latency_s": 0.01,
        "ram_before_mb": 100.0,
        "ram_after_mb": 100.0,
    }


def test_build_plan_preserves_order_and_shape():
    steps = [
        {"tool": "open_app", "params": {"app_name": "Spotify"}},
        {"tool": "get_battery", "params": {}},
    ]
    planned = planner.build_plan(steps)
    assert [p.step_index for p in planned] == [0, 1]
    assert [p.tool for p in planned] == ["open_app", "get_battery"]
    assert planned[0].params == {"app_name": "Spotify"}
    assert all(p.status == "pending" and p.attempts == 0 for p in planned)


def test_build_plan_as_dict_shape():
    planned = planner.build_plan([{"tool": "get_battery", "params": {}}])
    d = planned[0].as_dict()
    assert d == {
        "step_index": 0,
        "tool": "get_battery",
        "params": {},
        "status": "pending",
        "attempts": 0,
    }


def test_execute_plan_success_marks_ok_with_one_attempt():
    planned = planner.build_plan([{"tool": "get_battery", "params": {}}])
    planner.execute_plan(planned, _ok)
    assert planned[0].status == "ok"
    assert planned[0].attempts == 1


def test_execute_plan_non_retryable_tool_fails_after_one_attempt():
    planned = planner.build_plan([{"tool": "get_battery", "params": {}}])
    planner.execute_plan(planned, _fail)
    assert planned[0].status == "failed"
    assert planned[0].attempts == 1  # get_battery isn't in RETRYABLE_TOOLS


def test_execute_plan_retryable_tool_retries_once_then_fails():
    calls = []

    def flaky_always_fails(tool, params):
        calls.append(tool)
        return _fail(tool, params)

    planned = planner.build_plan(
        [{"tool": "play_youtube_video", "params": {"query": "x"}}]
    )
    planner.execute_plan(planned, flaky_always_fails)
    assert planned[0].status == "failed"
    assert planned[0].attempts == 2  # original + one retry
    assert calls == ["play_youtube_video", "play_youtube_video"]


def test_execute_plan_retryable_tool_succeeds_on_retry_stops_early():
    call_count = {"n": 0}

    def fails_once_then_ok(tool, params):
        call_count["n"] += 1
        return _fail(tool, params) if call_count["n"] == 1 else _ok(tool, params)

    planned = planner.build_plan(
        [{"tool": "play_youtube_video", "params": {"query": "x"}}]
    )
    planner.execute_plan(planned, fails_once_then_ok)
    assert planned[0].status == "ok"
    assert planned[0].attempts == 2
    assert call_count["n"] == 2


def test_execute_plan_multiple_steps_independent_retry_policy():
    def route(tool, params):
        return _fail(tool, params) if tool == "get_battery" else _ok(tool, params)

    planned = planner.build_plan(
        [
            {"tool": "get_battery", "params": {}},
            {"tool": "play_youtube_video", "params": {"query": "x"}},
        ]
    )
    planner.execute_plan(planned, route)
    assert planned[0].status == "failed" and planned[0].attempts == 1
    assert planned[1].status == "ok" and planned[1].attempts == 1


# --- Milestone 5.6: on_progress / should_cancel hooks -----------------------
# These are additive, optional kwargs (both default None) — every test above
# this line calls execute_plan exactly as Milestone 4 left it, with neither
# hook, and must keep passing unmodified to prove this was backward-compatible.


def test_execute_plan_without_hooks_behaves_exactly_as_before():
    """Explicit regression guard: omitting on_progress/should_cancel entirely
    (as every pre-5.6 caller does) must be indistinguishable from before."""
    planned = planner.build_plan([{"tool": "get_battery", "params": {}}])
    result = planner.execute_plan(planned, _ok)
    assert result is planned
    assert planned[0].status == "ok" and planned[0].attempts == 1


def test_execute_plan_calls_on_progress_after_each_step():
    seen = []
    planned = planner.build_plan(
        [
            {"tool": "get_battery", "params": {}},
            {"tool": "open_app", "params": {"app_name": "Spotify"}},
        ]
    )
    planner.execute_plan(planned, _ok, on_progress=lambda p: seen.append(p.tool))
    assert seen == ["get_battery", "open_app"]


def test_execute_plan_on_progress_receives_the_actual_planned_step_with_final_state():
    captured = []
    planned = planner.build_plan([{"tool": "get_battery", "params": {}}])
    planner.execute_plan(
        planned, _fail, on_progress=lambda p: captured.append((p.status, p.attempts))
    )
    assert captured == [("failed", 1)]


def test_execute_plan_should_cancel_stops_before_next_step_not_mid_step():
    """Cancellation is checked BEFORE a step starts, never interrupts a
    step already dispatched — the first step here always runs to
    completion even though should_cancel is already True by the time
    it's checked for step 2."""
    calls = []

    def route(tool, params):
        calls.append(tool)
        return _ok(tool, params)

    planned = planner.build_plan(
        [
            {"tool": "get_battery", "params": {}},
            {"tool": "open_app", "params": {"app_name": "Spotify"}},
            {"tool": "open_url", "params": {"url": "https://example.com"}},
        ]
    )
    # Cancel takes effect starting from the 2nd step onward.
    call_count = {"n": 0}

    def should_cancel():
        return call_count["n"] >= 1

    def route_and_count(tool, params):
        call_count["n"] += 1
        return route(tool, params)

    planner.execute_plan(planned, route_and_count, should_cancel=should_cancel)
    assert planned[0].status == "ok"
    assert planned[1].status == "cancelled"
    assert planned[2].status == "cancelled"
    assert calls == ["get_battery"]  # only the first step's tool ever ran


def test_execute_plan_should_cancel_true_from_the_start_cancels_everything():
    planned = planner.build_plan([{"tool": "get_battery", "params": {}}])
    planner.execute_plan(planned, _ok, should_cancel=lambda: True)
    assert planned[0].status == "cancelled"
    assert planned[0].result is None  # never executed, so no exec_info to hold


def test_execute_plan_cancelled_steps_still_trigger_on_progress():
    seen_statuses = []
    planned = planner.build_plan([{"tool": "get_battery", "params": {}}])
    planner.execute_plan(
        planned,
        _ok,
        on_progress=lambda p: seen_statuses.append(p.status),
        should_cancel=lambda: True,
    )
    assert seen_statuses == ["cancelled"]
