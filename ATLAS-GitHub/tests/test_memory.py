"""
Tests for memory.py (Milestone 2 — Context Manager).

Uses an isolated, temporary SQLite file per test (monkeypatching
storage.DB_PATH) rather than the shared atlas.db other test files write
to — unlike confirmation tokens (random, collision-free) or log rows
(never read back by assertion), conversation/project/preference state is
exactly the kind of thing a leftover row from an earlier test run would
silently corrupt an assertion like "nothing remembered yet".
"""
import pytest

import memory
import storage


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test_atlas.db")
    storage.init_db()
    yield


def test_recall_context_is_empty_before_anything_is_remembered():
    recalled = memory.recall_context()
    assert recalled == {
        "current_project": None,
        "current_directory": None,
        "conversation_history": [],
        "preferences": {},
    }


def test_get_current_project_is_none_before_any_visit():
    assert memory.get_current_project() is None


def test_remember_exchange_then_recall_returns_turns_oldest_first():
    memory.remember_exchange("user", "open my project")
    memory.remember_exchange("assistant", "open_project: ok")
    history = memory.get_conversation_history()
    assert [h["role"] for h in history] == ["user", "assistant"]
    assert [h["text"] for h in history] == ["open my project", "open_project: ok"]


def test_remember_exchange_ignores_empty_text():
    memory.remember_exchange("user", "")
    memory.remember_exchange("user", None)
    assert memory.get_conversation_history() == []


def test_conversation_history_respects_limit():
    for i in range(5):
        memory.remember_exchange("user", f"turn {i}")
    history = memory.get_conversation_history(limit=2)
    assert [h["text"] for h in history] == ["turn 3", "turn 4"]


def test_remember_project_visit_sets_current_project():
    index_result = {
        "total_files": 42, "total_size_mb": 3.1,
        "languages": {"Python": 40, "Markdown": 2},
        "top_level_entries": ["main.py", "tests/"],
    }
    memory.remember_project_visit("/tmp/atlas-example", index_result)

    current = memory.get_current_project()
    assert current["path"] == "/tmp/atlas-example"
    assert current["total_files"] == 42
    assert current["languages"] == {"Python": 40, "Markdown": 2}

    recalled = memory.recall_context()
    assert recalled["current_project"] == "/tmp/atlas-example"
    assert recalled["current_directory"] == "/tmp/atlas-example"


def test_remember_project_visit_without_index_result_preserves_prior_summary():
    """A bare open_project visit (no index_project result handy) must not
    blow away a previously-known summary — see storage.upsert_project's
    COALESCE behavior."""
    memory.remember_project_visit("/tmp/atlas-example", {
        "total_files": 42, "total_size_mb": 3.1,
        "languages": {"Python": 40}, "top_level_entries": ["main.py"],
    })
    memory.remember_project_visit("/tmp/atlas-example", None)  # e.g. open_project, no index handy

    current = memory.get_current_project()
    assert current["total_files"] == 42
    assert current["languages"] == {"Python": 40}


def test_remember_project_visit_ignores_empty_path():
    memory.remember_project_visit("", {"total_files": 1})
    memory.remember_project_visit(None, {"total_files": 1})
    assert memory.get_current_project() is None


def test_visiting_a_second_project_becomes_current():
    memory.remember_project_visit("/tmp/first", {"total_files": 1})
    memory.remember_project_visit("/tmp/second", {"total_files": 2})
    current = memory.get_current_project()
    assert current["path"] == "/tmp/second"


def test_set_and_get_preferences():
    memory.set_preference("voice_persona", "concise")
    memory.set_preference("theme", "dark")
    prefs = memory.get_preferences()
    assert prefs == {"voice_persona": "concise", "theme": "dark"}


def test_recall_context_includes_everything_remembered():
    memory.remember_project_visit("/tmp/proj", {"total_files": 3})
    memory.remember_exchange("user", "what's in this project")
    memory.set_preference("theme", "dark")

    recalled = memory.recall_context()
    assert recalled["current_project"] == "/tmp/proj"
    assert recalled["current_directory"] == "/tmp/proj"
    assert recalled["conversation_history"][0]["text"] == "what's in this project"
    # preferences includes "theme" plus the current-project pointer itself
    # (remember_project_visit stores it via set_preference too — see
    # memory.CURRENT_PROJECT_PREF_KEY).
    assert recalled["preferences"]["theme"] == "dark"
    assert recalled["preferences"][memory.CURRENT_PROJECT_PREF_KEY] == "/tmp/proj"


def test_remember_exchange_is_best_effort_on_storage_failure(monkeypatch):
    def raise_disk_full(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(storage, "add_conversation_turn", raise_disk_full)
    memory.remember_exchange("user", "should not raise")  # must not propagate


def test_recall_context_is_best_effort_on_storage_failure(monkeypatch):
    monkeypatch.setattr(storage, "get_preference", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    recalled = memory.recall_context()  # must not propagate
    assert recalled["current_project"] is None