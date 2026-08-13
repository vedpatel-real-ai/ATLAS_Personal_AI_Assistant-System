"""
Tests for tools.search_knowledge() — Milestone 3's final Knowledge
Companion slice (knowledge storage/cross-referencing, spec §8).

storage.search_knowledge() itself (the actual SQLite LIKE query) is
mocked here, same reasoning as mocking tools._call_reasoning_model for
Ollama or tools._extract_pdf_text for pypdf — the SQL query correctness
is this suite's job, but exercising the real DB round-trip belongs to
test_storage_knowledge.py, not here.
"""
import tools


def _mock_results(monkeypatch, rows):
    monkeypatch.setattr(tools.storage, "search_knowledge", lambda query, limit=10: rows)


def test_empty_query_returns_error():
    result = tools.search_knowledge("")
    assert result["status"] == "error"
    assert "empty" in result["message"]


def test_whitespace_only_query_returns_error():
    result = tools.search_knowledge("   ")
    assert result["status"] == "error"


def test_no_matches_returns_ok_with_empty_list(monkeypatch):
    _mock_results(monkeypatch, [])
    result = tools.search_knowledge("nonexistent topic")
    assert result["status"] == "ok"
    assert result["count"] == 0
    assert result["matches"] == []
    assert "0 stored summaries" in result["message"]


def test_single_match_uses_singular_message(monkeypatch):
    _mock_results(monkeypatch, [{"id": 1, "path": "/tmp/report.pdf",
                                  "source_type": "document",
                                  "summary": "quarterly numbers", "question": None,
                                  "extracted_at": "2026-07-14T00:00:00+00:00"}])
    result = tools.search_knowledge("quarterly")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert "1 stored summary matching" in result["message"]
    assert result["matches"][0]["path"] == "/tmp/report.pdf"


def test_multiple_matches_uses_plural_message(monkeypatch):
    _mock_results(monkeypatch, [
        {"id": 2, "path": "a.md", "source_type": "document", "summary": "x",
         "question": None, "extracted_at": "2026-07-14T00:00:01+00:00"},
        {"id": 1, "path": "b.md", "source_type": "document", "summary": "y",
         "question": None, "extracted_at": "2026-07-14T00:00:00+00:00"},
    ])
    result = tools.search_knowledge("something")
    assert result["count"] == 2
    assert "2 stored summaries matching" in result["message"]


def test_query_is_stripped_before_being_passed_to_storage(monkeypatch):
    captured = []
    monkeypatch.setattr(
        tools.storage, "search_knowledge",
        lambda query, limit=10: captured.append((query, limit)) or [],
    )
    tools.search_knowledge("  pricing  ")
    assert captured == [("pricing", 10)]


def test_limit_param_is_forwarded(monkeypatch):
    captured = []
    monkeypatch.setattr(
        tools.storage, "search_knowledge",
        lambda query, limit=10: captured.append((query, limit)) or [],
    )
    tools.search_knowledge("pricing", limit=3)
    assert captured == [("pricing", 3)]


def test_search_knowledge_is_registered_as_safe_tier():
    assert tools.TOOLS["search_knowledge"]["tier"] == "safe"


def test_search_knowledge_registered_with_correct_fn():
    assert tools.TOOLS["search_knowledge"]["fn"] is tools.search_knowledge