"""
Integration tests for Milestone 0.5's wiring into main.py's /command
endpoint. Unlike test_decision_engine.py / test_request_context.py (unit
tests for the modules in isolation), these boot the actual FastAPI app via
TestClient and check that:

  - a deterministic match is logged/returned as L0
  - a router-routed single-tool command is logged/returned as L2
  - a router-routed multi-step chain is logged/returned as L3
  - needs_reasoning (no attachment) is logged/returned as L4
  - a ZIP/PDF/large attachment short-circuits straight to L4 WITHOUT ever
    calling router.route() at all — the actual new capability this
    milestone adds (spec §3.1)

Ollama itself is never called — router.route and main.run_big_model are
monkeypatched, same pattern as tests/test_router.py's fake_post.
"""
import os

os.environ.setdefault("ASSISTANT_API_KEY", "test")

from fastapi.testclient import TestClient

import main
import router as router_module


client = TestClient(main.app)
HEADERS = {"X-API-Key": "test"}


def test_deterministic_match_is_logged_as_l0():
    resp = client.post("/command", json={"text": "what's my battery"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "deterministic"
    assert body["level"] == "L0"


def test_router_single_step_is_logged_as_l2(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "open_project", "params": {"project_path": "/tmp"}}],
                "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post("/command", json={"text": "open my project"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "router_llm"
    assert body["level"] == "L2"


def test_router_multi_step_chain_is_logged_as_l3(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [
            {"tool": "open_project", "params": {"project_path": "/tmp"}},
            {"tool": "get_battery", "params": {}},
        ], "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    # Deliberately avoids any word matcher.py's regexes key on (spotify,
    # vscode, battery, google/youtube/gmail/github, "search", "stock
    # price") so this exercises the router_llm path, not deterministic.
    resp = client.post("/command", json={"text": "get my morning routine going"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["level"] == "L3"


def test_needs_reasoning_without_attachment_is_l4_and_calls_router_first(monkeypatch):
    calls = {"route_called": False}

    def fake_route(text, tools, keep_alive="0"):
        calls["route_called"] = True
        return {"steps": [], "needs_reasoning": True}

    monkeypatch.setattr(router_module, "route", fake_route)
    monkeypatch.setattr(main, "run_big_model", lambda text: "a thoughtful reasoning response")

    resp = client.post("/command", json={"text": "write me a short poem"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "text"
    assert body["level"] == "L4"
    assert calls["route_called"] is True  # router WAS consulted — no attachment signal to skip it


def test_zip_attachment_forces_l4_without_ever_calling_router(monkeypatch):
    """The actual new capability in this milestone: a non-text signal
    (attachment) skips router-model classification entirely."""
    calls = {"route_called": False}

    def fake_route(text, tools, keep_alive="0"):
        calls["route_called"] = True
        return {"steps": [{"tool": "get_battery", "params": {}}], "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    monkeypatch.setattr(main, "run_big_model", lambda text: "here is a project review")

    resp = client.post(
        "/command",
        json={"text": "open vscode", "attachments": [{"type": "zip", "size_mb": 4.2}]},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "text"
    assert body["route"] == "big_model"
    assert body["level"] == "L4"
    # The whole point: router.route() must never have been called, even
    # though "open vscode" would deterministically match matcher.py on its
    # own — the attachment signal wins and skips straight to L4.
    assert calls["route_called"] is False


def test_large_non_zip_attachment_also_forces_l4(monkeypatch):
    monkeypatch.setattr(main, "run_big_model", lambda text: "a summary")
    resp = client.post(
        "/command",
        json={"text": "summarize this", "attachments": [{"type": "txt", "size_mb": 3.5}]},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["level"] == "L4"


def test_small_attachment_does_not_force_l4(monkeypatch):
    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "get_battery", "params": {}}], "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post(
        "/command",
        json={"text": "some novel phrasing", "attachments": [{"type": "txt", "size_mb": 0.01}]},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["level"] == "L2"


# ---------------------------------------------------------------------------
# 2026-07-27: regression tests for _fill_missing_project_path, closing the
# 2026-07-22 session's open thread.
#
# Each test below resets main._request_times before posting. Real bug found
# while adding these: check_rate_limit()'s _request_times deque is a single
# module-level global shared by every TestClient across the WHOLE pytest
# session (main.app is one singleton module-level object), so /command
# posts from unrelated test files elsewhere in the suite count against the
# same 20-per-60s budget. Nothing previously reset it between tests, so the
# full suite was already order-dependent on staying under that ceiling —
# these new tests' own /command posts were enough to tip it into a real
# 429 on a full-suite run (confirmed: passes in isolation, fails at full-
# suite scale). Resetting the deque here fixes these tests deterministically
# without changing check_rate_limit()'s real production behavior; the
# suite's pre-existing shared-global fragility for every OTHER test that
# calls /command is a separate finding, flagged below, not fixed here.
# ---------------------------------------------------------------------------
import os

import memory


def test_dependency_graph_missing_project_path_falls_back_to_current_project(monkeypatch):
    monkeypatch.setattr(main, "_request_times", type(main._request_times)())
    real_project_dir = os.getcwd()
    memory.remember_project_visit(real_project_dir, {"total_files": 40})

    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "dependency_graph",
                            "params": {"focus_file": "main.py"}}],
                "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post("/command", json={"text": "is main.py in a circular import"},
                        headers=HEADERS)
    assert resp.status_code == 200
    result = resp.json()["results"][0]["result"]
    assert result["status"] == "ok"
    assert result["files_scanned"] > 0
    assert result["project_path"] == os.path.abspath(real_project_dir)


def test_index_project_missing_project_path_falls_back_to_current_project(monkeypatch):
    monkeypatch.setattr(main, "_request_times", type(main._request_times)())
    real_project_dir = os.getcwd()
    memory.remember_project_visit(real_project_dir, {"total_files": 40})

    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "index_project", "params": {}}], "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post("/command", json={"text": "index my project"}, headers=HEADERS)
    assert resp.status_code == 200
    result = resp.json()["results"][0]["result"]
    assert result["status"] == "ok"
    assert result["total_files"] > 0


def test_project_path_fallback_not_applied_without_a_current_project(monkeypatch):
    monkeypatch.setattr(main, "_request_times", type(main._request_times)())
    memory.set_preference(memory.CURRENT_PROJECT_PREF_KEY, "")

    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "dependency_graph", "params": {"focus_file": "main.py"}}],
                "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post("/command", json={"text": "is main.py in a circular import"},
                        headers=HEADERS)
    assert resp.status_code == 200
    result = resp.json()["results"][0]["result"]
    assert result["status"] == "error"


def test_project_path_fallback_does_not_apply_to_open_project(monkeypatch):
    monkeypatch.setattr(main, "_request_times", type(main._request_times)())
    memory.remember_project_visit(os.getcwd(), {"total_files": 40})

    def fake_route(text, tools, keep_alive="0"):
        return {"steps": [{"tool": "open_project", "params": {}}], "needs_reasoning": False}

    monkeypatch.setattr(router_module, "route", fake_route)
    resp = client.post("/command", json={"text": "open my project"}, headers=HEADERS)
    assert resp.status_code == 200
    result = resp.json()["results"][0]["result"]
    assert result["status"] == "error"