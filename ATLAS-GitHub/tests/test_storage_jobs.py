"""
Tests for storage.py's `jobs` table (Milestone 5.6 — Async Execution &
Task Progress): create_job(), update_job(), get_job(), and
query_recent_jobs(). Exercises the real SQLite round-trip, same convention
as test_storage_knowledge.py / test_storage_confirmations.py /
test_storage_benchmarks.py. Does NOT re-test jobs.py's own cancellation
registry or background-thread logic — see test_jobs.py for that.
"""
import uuid

import storage


def _new_id():
    # Unique per test so repeated suite runs against the same shared
    # atlas.db (this project's documented test-isolation gap — see
    # IMPLEMENTATION_STATE.md) don't collide on job_id, a PRIMARY KEY.
    return f"test-job-{uuid.uuid4().hex[:12]}"


def test_create_job_then_get_job_round_trips_basic_fields():
    job_id = _new_id()
    storage.create_job(job_id, text="check my battery", route="deterministic",
                        level="L0", project=None, total_steps=1, status="queued")
    job = storage.get_job(job_id)
    assert job["job_id"] == job_id
    assert job["text"] == "check my battery"
    assert job["route"] == "deterministic"
    assert job["level"] == "L0"
    assert job["status"] == "queued"
    assert job["total_steps"] == 1
    assert job["current_step_index"] == 0


def test_create_job_stores_steps_and_unknown_steps_as_json():
    job_id = _new_id()
    steps = [{"tool": "get_battery", "params": {}}]
    unknown_steps = [{"tool": "delete_file", "params": {"path": "/tmp/x"}}]
    storage.create_job(job_id, text="x", total_steps=2,
                        steps=steps, unknown_steps=unknown_steps)
    job = storage.get_job(job_id)
    assert job["steps"] == steps
    assert job["unknown_steps"] == unknown_steps


def test_create_job_defaults_steps_and_unknown_steps_to_none_and_progress_to_empty_list():
    job_id = _new_id()
    storage.create_job(job_id, text="x")
    job = storage.get_job(job_id)
    assert job["steps"] is None
    assert job["unknown_steps"] is None
    assert job["progress"] == []
    assert job["result"] is None


def test_get_job_returns_none_for_unknown_job_id():
    assert storage.get_job("no-such-job-id") is None


def test_update_job_status_only_touches_the_status_column():
    job_id = _new_id()
    storage.create_job(job_id, text="x", status="queued", total_steps=1)
    storage.update_job(job_id, status="running")
    job = storage.get_job(job_id)
    assert job["status"] == "running"
    assert job["current_step_index"] == 0  # untouched


def test_update_job_progress_persists_and_parses_back_as_a_list():
    job_id = _new_id()
    storage.create_job(job_id, text="x", total_steps=2)
    progress = [{"step": 0, "tool": "get_battery", "status": "done"}]
    storage.update_job(job_id, current_step_index=1, progress=progress)
    job = storage.get_job(job_id)
    assert job["current_step_index"] == 1
    assert job["progress"] == progress


def test_update_job_result_and_error_round_trip():
    job_id = _new_id()
    storage.create_job(job_id, text="x")

    storage.update_job(job_id, status="done", result={"type": "tool_result", "ok": True})
    job = storage.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"type": "tool_result", "ok": True}
    assert job["error"] is None

    storage.update_job(job_id, status="failed", error="tool raised an exception")
    job = storage.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "tool raised an exception"


def test_update_job_with_no_fields_passed_is_a_harmless_no_op():
    job_id = _new_id()
    storage.create_job(job_id, text="x", status="queued")
    before = storage.get_job(job_id)
    storage.update_job(job_id)  # nothing passed
    after = storage.get_job(job_id)
    assert after["status"] == before["status"]
    assert after["current_step_index"] == before["current_step_index"]


def test_query_recent_jobs_returns_most_recent_first_and_respects_limit():
    ids = [_new_id() for _ in range(3)]
    for jid in ids:
        storage.create_job(jid, text="x", status="queued")

    recent = storage.query_recent_jobs(limit=len(ids))
    recent_ids = [r["job_id"] for r in recent]
    # All three of ours should appear, most-recently-created first among
    # themselves (created_at is set at insert time).
    ours_in_order = [jid for jid in recent_ids if jid in ids]
    assert ours_in_order == list(reversed(ids))

    limited = storage.query_recent_jobs(limit=1)
    assert len(limited) == 1