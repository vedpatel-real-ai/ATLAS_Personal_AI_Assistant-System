"""
Tests for request_context.py. Milestone 0.5 covers the base shape
(raw_text/attachments/estimated_complexity); Milestone 2 wires the
memory-backed fields (current_project, current_directory,
conversation_history, preferences) to memory.recall_context() instead of
hardcoded empty defaults.

memory.recall_context() is monkeypatched in every test here (via the
autouse stub_memory fixture) so these stay unit tests of RequestContext's
construction, not integration tests of the real SQLite-backed memory
layer — see tests/test_memory.py for those.
"""

import pytest

import request_context
from request_context import build_request_context


def _stub_recall(
    current_project=None,
    current_directory=None,
    conversation_history=None,
    preferences=None,
):
    return {
        "current_project": current_project,
        "current_directory": current_directory,
        "conversation_history": list(conversation_history or []),
        "preferences": dict(preferences or {}),
    }


@pytest.fixture(autouse=True)
def stub_memory(monkeypatch):
    """Default every test in this file to an empty-memory stub. Individual
    tests override via monkeypatch.setattr(...) when checking that
    non-empty memory is threaded through correctly."""
    monkeypatch.setattr(
        request_context.memory, "recall_context", lambda: _stub_recall()
    )


def test_build_request_context_sets_raw_text():
    ctx = build_request_context("open spotify")
    assert ctx.raw_text == "open spotify"


def test_build_request_context_defaults_attachments_to_empty_list():
    ctx = build_request_context("open spotify")
    assert ctx.attachments == []


def test_build_request_context_accepts_attachments():
    attachments = [{"type": "zip", "size_mb": 4.2}]
    ctx = build_request_context("review this project", attachments)
    assert ctx.attachments == attachments


def test_build_request_context_none_attachments_becomes_empty_list():
    ctx = build_request_context("open spotify", None)
    assert ctx.attachments == []


def test_memory_backed_fields_empty_when_nothing_remembered_yet():
    """A fresh assistant with no conversation/project history should
    present exactly this shape — the stub_memory fixture above mimics
    memory.recall_context()'s real return value for a brand-new atlas.db."""
    ctx = build_request_context("continue yesterday's project")
    assert ctx.current_project is None
    assert ctx.current_directory is None
    assert (
        ctx.current_app is None
    )  # still no macOS active-app tool — genuinely out of scope
    assert ctx.conversation_history == []
    assert ctx.preferences == {}


def test_memory_backed_fields_come_from_recall_context(monkeypatch):
    monkeypatch.setattr(
        request_context.memory,
        "recall_context",
        lambda: _stub_recall(
            current_project="/tmp/proj",
            current_directory="/tmp/proj",
            conversation_history=[{"role": "user", "text": "hi"}],
            preferences={"theme": "dark"},
        ),
    )
    ctx = build_request_context("continue yesterday's project")
    assert ctx.current_project == "/tmp/proj"
    assert ctx.current_directory == "/tmp/proj"
    assert ctx.conversation_history == [{"role": "user", "text": "hi"}]
    assert ctx.preferences == {"theme": "dark"}


def test_estimated_complexity_starts_unset():
    ctx = build_request_context("open spotify")
    assert ctx.estimated_complexity is None


def test_as_dict_matches_spec_shape():
    ctx = build_request_context("open spotify", [{"type": "zip", "size_mb": 1.0}])
    ctx.estimated_complexity = "L0"
    d = ctx.as_dict()
    expected_keys = {
        "raw_text",
        "current_project",
        "current_directory",
        "current_app",
        "conversation_history",
        "preferences",
        "attachments",
        "estimated_complexity",
        "tool_candidates",
    }
    assert set(d.keys()) == expected_keys
    assert d["estimated_complexity"] == "L0"


def test_two_contexts_are_independent_instances():
    """Guards against a mutable-default-argument bug — each request must
    get its own attachments/history/preferences, not a shared list/dict,
    even though both come from the same (stubbed) memory.recall_context()."""
    ctx1 = build_request_context("first")
    ctx2 = build_request_context("second")
    ctx1.attachments.append({"type": "zip", "size_mb": 1.0})
    ctx1.conversation_history.append("turn 1")
    assert ctx2.attachments == []
    assert ctx2.conversation_history == []
