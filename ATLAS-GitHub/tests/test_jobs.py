"""
Tests for jobs.py (Milestone 5.6): the cancellation-event registry,
run_in_background's thread wrapper, and reconcile_orphaned_jobs. Does NOT
re-test storage.py's jobs-table CRUD itself — see test_storage_jobs.py
for that; this file tests jobs.py's own logic on top of it.
"""
import threading
import time

import jobs
import storage


def test_new_job_id_is_unique_and_short():
    ids = {jobs.new_job_id() for _ in range(50)}
    assert len(ids) == 50  # no collisions across 50 draws
    assert all(len(i) == 12 for i in ids)


def test_create_job_registers_a_cancel_event():
    job_id = jobs.new_job_id()
    assert jobs.is_cancel_requested(job_id) is False  # not tracked yet
    jobs.create_job(job_id, text="x", status="running", total_steps=1)
    assert jobs.is_cancel_requested(job_id) is False  # tracked, not yet cancelled
    jobs.cleanup_job(job_id)  # tidy up so this test doesn't leak into others


def test_request_cancel_on_tracked_job_sets_the_flag():
    job_id = jobs.new_job_id()
    jobs.create_job(job_id, text="x", status="running")
    assert jobs.request_cancel(job_id) is True
    assert jobs.is_cancel_requested(job_id) is True
    jobs.cleanup_job(job_id)


def test_request_cancel_on_untracked_job_returns_false():
    assert jobs.request_cancel("never-created-this-one") is False


def test_is_cancel_requested_false_for_untracked_job():
    assert jobs.is_cancel_requested("also-never-created") is False


def test_cleanup_job_removes_tracking_so_cancel_no_longer_signals():
    job_id = jobs.new_job_id()
    jobs.create_job(job_id, text="x", status="running")
    jobs.cleanup_job(job_id)
    assert jobs.request_cancel(job_id) is False


def test_update_progress_persists_to_storage_and_cleans_up_on_terminal_status():
    job_id = jobs.new_job_id()
    jobs.create_job(job_id, text="x", status="running", total_steps=1)
    jobs.update_progress(job_id, current_step_index=1)
    assert storage.get_job(job_id)["current_step_index"] == 1
    assert jobs.is_cancel_requested(job_id) is False  # still tracked, not terminal yet

    jobs.update_progress(job_id, status="done", result={"type": "tool_result"})
    job = storage.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"type": "tool_result"}
    # Terminal status should have released the in-memory cancel event.
    assert jobs.request_cancel(job_id) is False


def test_get_job_passes_through_to_storage():
    job_id = jobs.new_job_id()
    jobs.create_job(job_id, text="hello", status="running")
    job = jobs.get_job(job_id)
    assert job["text"] == "hello"
    jobs.cleanup_job(job_id)


def test_run_in_background_actually_runs_the_function_off_the_caller_thread():
    caller_thread = threading.current_thread()
    seen = {}

    def target(x, y=None):
        seen["thread"] = threading.current_thread()
        seen["args"] = (x, y)

    t = jobs.run_in_background(target, 1, y=2)
    t.join(timeout=2)
    assert seen["args"] == (1, 2)
    assert seen["thread"] is not caller_thread
    assert t.daemon is True


def test_reconcile_orphaned_jobs_marks_stale_running_and_awaiting_jobs_failed():
    running_id = jobs.new_job_id()
    awaiting_id = jobs.new_job_id()
    done_id = jobs.new_job_id()
    storage.create_job(running_id, text="a", status="running")
    storage.create_job(awaiting_id, text="b", status="awaiting_confirmation")
    storage.create_job(done_id, text="c", status="done")

    count = jobs.reconcile_orphaned_jobs()
    assert count >= 2  # at least our two; other tests' leftovers may add more

    assert storage.get_job(running_id)["status"] == "failed"
    assert storage.get_job(awaiting_id)["status"] == "failed"
    assert storage.get_job(done_id)["status"] == "done"  # untouched — already terminal


def test_reconcile_orphaned_jobs_is_idempotent_on_already_reconciled_jobs():
    job_id = jobs.new_job_id()
    storage.create_job(job_id, text="a", status="running")
    jobs.reconcile_orphaned_jobs()
    first_error = storage.get_job(job_id)["error"]
    jobs.reconcile_orphaned_jobs()  # running it again shouldn't touch an already-failed job further
    assert storage.get_job(job_id)["error"] == first_error
    assert storage.get_job(job_id)["status"] == "failed"