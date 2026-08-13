"""
Integration tests for Milestone 5.6's /command/async, GET /jobs/{id},
POST /jobs/{id}/cancel, POST /jobs/{id}/confirm, and GET /jobs — booting
the actual FastAPI app via TestClient, same pattern as
test_main_decision_engine_integration.py. router.route and
main.execute_tool/main.run_big_model are monkeypatched so nothing here
depends on Ollama or real tool side effects being available.

Because the actual work happens on a background thread, tests poll
GET /jobs/{id} (via _wait_terminal below) instead of asserting
immediately on the /command/async response — the response only proves
the job was *accepted*, not that it finished.
"""
import os
import threading
import time

os.environ.setdefault("ASSISTANT_API_KEY", "test")

from fastapi.testclient import TestClient

import main
import router as router_module


client = TestClient(main.app)
HEADERS = {"X-API-Key": "test"}

TERMINAL = {"done", "failed", "cancelled"}


def _wait_terminal(job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    job = client.get(f"/jobs/{job_id}", headers=HEADERS).json()
    while job["status"] not in TERMINAL and time.time() < deadline:
        time.sleep(0.01)
        job = client.get(f"/jobs/{job_id}", headers=HEADERS).json()
    return job


def _fake_execute_tool_ok(tool_name, params):
    return {"result": {"status": "ok"}, "success": True, "latency_s": 0.01,
            "ram_before_mb": 1.0, "ram_after_mb": 1.0}


def test_async_deterministic_safe_command_runs_in_background_and_completes():
    resp = client.post("/command/async", json={"text": "what's my battery"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "job_accepted"
    assert body["status"] == "running"
    assert body["route"] == "deterministic"

    job = _wait_terminal(body["job_id"])
    assert job["status"] == "done"
    assert job["route"] == "deterministic"
    assert job["level"] == "L0"
    assert job["result"]["type"] == "tool_result"


def test_async_router_single_safe_step_runs_without_confirmation(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "open_project", "params": {"project_path": "/tmp"}}],
                "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    monkeypatch.setattr(main, "execute_tool", _fake_execute_tool_ok)

    resp = client.post("/command/async", json={"text": "open my project please"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "job_accepted"
    assert body["route"] == "router_llm"

    job = _wait_terminal(body["job_id"])
    assert job["status"] == "done"
    assert job["level"] == "L2"
    assert job["result"]["success"] is True


def test_async_needs_reasoning_runs_big_model_in_background(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [], "needs_reasoning": True}

    monkeypatch.setattr(router_module, "route", fake_route)
    monkeypatch.setattr(main, "run_big_model", lambda text: "a thoughtful async response")

    resp = client.post("/command/async", json={"text": "write me a short poem"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "job_accepted"
    assert body["route"] == "big_model"

    job = _wait_terminal(body["job_id"])
    assert job["status"] == "done"
    assert job["level"] == "L4"
    assert job["result"]["response"] == "a thoughtful async response"


def test_async_attachment_forces_l4_big_model_job(monkeypatch):
    monkeypatch.setattr(main, "run_big_model", lambda text: "an async project summary")
    resp = client.post(
        "/command/async",
        json={"text": "summarize this project", "attachments": [{"type": "zip", "size_mb": 4.2}]},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "big_model"
    job = _wait_terminal(body["job_id"])
    assert job["status"] == "done"
    assert job["level"] == "L4"
    assert job["result"]["response"] == "an async project summary"


def test_async_unmatched_intent_returns_error_type_no_job(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "not_a_real_tool", "params": {}}], "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post("/command/async", json={"text": "do something totally unrecognized"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "error"
    assert "job_id" not in body


def test_async_dangerous_step_requires_confirmation_then_confirm_executes(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "delete_file", "params": {"path": "/tmp/whatever.txt"}}],
                "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    monkeypatch.setattr(main, "execute_tool", _fake_execute_tool_ok)

    resp = client.post("/command/async", json={"text": "delete that file"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "confirm_required"
    assert "job_id" in body
    assert "confirm_token" in body
    job_id = body["job_id"]
    token = body["confirm_token"]

    pending = client.get(f"/jobs/{job_id}", headers=HEADERS).json()
    assert pending["status"] == "awaiting_confirmation"
    assert pending["steps"] == [{"tool": "delete_file", "params": {"path": "/tmp/whatever.txt"}}]

    confirm_resp = client.post(f"/jobs/{job_id}/confirm", json={"confirm_token": token}, headers=HEADERS)
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["status"] == "running"

    job = _wait_terminal(job_id)
    assert job["status"] == "done"
    assert job["result"]["success"] is True


def test_async_confirm_with_wrong_token_is_rejected(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "delete_file", "params": {"path": "/tmp/other.txt"}}],
                "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post("/command/async", json={"text": "delete another file"}, headers=HEADERS)
    job_id = resp.json()["job_id"]

    bad_resp = client.post(f"/jobs/{job_id}/confirm", json={"confirm_token": "not-the-real-token"},
                            headers=HEADERS)
    assert bad_resp.status_code == 200
    assert bad_resp.json()["type"] == "error"
    # Job should still be sitting exactly where it was — a bad token
    # must not be able to start execution.
    assert client.get(f"/jobs/{job_id}", headers=HEADERS).json()["status"] == "awaiting_confirmation"


def test_confirm_unknown_job_id_404():
    resp = client.post("/jobs/does-not-exist/confirm", json={"confirm_token": "x"}, headers=HEADERS)
    assert resp.status_code == 404


def test_get_job_unknown_id_404():
    resp = client.get("/jobs/does-not-exist", headers=HEADERS)
    assert resp.status_code == 404


def test_cancel_unknown_job_id_404():
    resp = client.post("/jobs/does-not-exist/cancel", headers=HEADERS)
    assert resp.status_code == 404


def test_list_jobs_endpoint_returns_recent_jobs():
    resp = client.post("/command/async", json={"text": "what's my battery"}, headers=HEADERS)
    job_id = resp.json()["job_id"]
    _wait_terminal(job_id)

    listing = client.get("/jobs", headers=HEADERS)
    assert listing.status_code == 200
    job_ids = [j["job_id"] for j in listing.json()["jobs"]]
    assert job_id in job_ids


def test_cancel_stops_remaining_steps_but_not_the_in_flight_one(monkeypatch):
    """The actual point of the should_cancel hook: a cancel request made
    while step 1 is still running lets step 1 finish (nothing can
    interrupt a dispatched tool call) but skips step 2 entirely."""

    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [
            {"tool": "open_project", "params": {"project_path": "/tmp"}},
            {"tool": "get_battery", "params": {}},
        ], "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)

    proceed = threading.Event()
    call_log = []

    def fake_execute_tool(tool_name, params):
        call_log.append(tool_name)
        if tool_name == "open_project":
            proceed.wait(timeout=2)
        return {"result": {"status": "ok"}, "success": True, "latency_s": 0.01,
                "ram_before_mb": 1.0, "ram_after_mb": 1.0}

    monkeypatch.setattr(main, "execute_tool", fake_execute_tool)

    resp = client.post("/command/async", json={"text": "get my morning routine going please"}, headers=HEADERS)
    job_id = resp.json()["job_id"]

    deadline = time.time() + 2
    while not call_log and time.time() < deadline:
        time.sleep(0.01)
    assert call_log == ["open_project"]  # first step has started, is blocked on `proceed`

    cancel_resp = client.post(f"/jobs/{job_id}/cancel", headers=HEADERS)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["cancel_requested"] is True

    proceed.set()  # let the already-dispatched first step finish

    job = _wait_terminal(job_id)
    assert job["status"] == "cancelled"
    assert call_log == ["open_project"]  # get_battery's tool fn was never actually called
    assert job["progress"][0]["status"] == "ok"
    assert job["progress"][1]["status"] == "cancelled"


def test_cancel_on_a_job_that_already_finished_returns_not_signaled():
    resp = client.post("/command/async", json={"text": "what's my battery"}, headers=HEADERS)
    job_id = resp.json()["job_id"]
    _wait_terminal(job_id)  # let it finish first

    cancel_resp = client.post(f"/jobs/{job_id}/cancel", headers=HEADERS)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["cancel_requested"] is False


def test_missing_api_key_rejected_on_all_new_endpoints():
    assert client.post("/command/async", json={"text": "hi"}).status_code == 401
    assert client.get("/jobs/whatever").status_code == 401
    assert client.post("/jobs/whatever/cancel").status_code == 401
    assert client.post("/jobs/whatever/confirm", json={"confirm_token": "x"}).status_code == 401
    assert client.get("/jobs").status_code == 401