"""
Tests for storage.py's `knowledge` table (Milestone 3 — knowledge storage/
cross-referencing, spec §8's final Knowledge Companion slice): store_knowledge()
and search_knowledge(). Exercises the real SQLite round-trip (same shared
atlas.db the rest of the suite uses, same convention as
test_storage_confirmations.py — no isolated test DB fixture exists for this
module today).
"""

import storage


def test_store_knowledge_returns_an_id():
    row_id = storage.store_knowledge(
        path="/tmp/one-off-test-doc.md",
        summary="a unique test summary xyz123",
        source_type="document",
    )
    assert isinstance(row_id, int)
    assert row_id > 0


def test_search_matches_on_summary_text():
    storage.store_knowledge(
        path="/tmp/pricing-test.md",
        summary="the plan costs ten dollars a month, unique-marker-aaa",
        source_type="document",
    )
    results = storage.search_knowledge("unique-marker-aaa")
    assert len(results) >= 1
    assert any("unique-marker-aaa" in r["summary"] for r in results)


def test_search_matches_on_path():
    storage.store_knowledge(
        path="/tmp/unique-marker-bbb.md",
        summary="nothing special here",
        source_type="document",
    )
    results = storage.search_knowledge("unique-marker-bbb")
    assert len(results) >= 1
    assert any("unique-marker-bbb" in r["path"] for r in results)


def test_search_is_case_insensitive():
    storage.store_knowledge(
        path="/tmp/case-test.md",
        summary="Contains UniqueMarkerCCC in mixed case",
        source_type="document",
    )
    results = storage.search_knowledge("uniquemarkerccc")
    assert len(results) >= 1


def test_search_respects_limit():
    for i in range(5):
        storage.store_knowledge(
            path=f"/tmp/limit-test-{i}.md",
            summary="shared-limit-marker-ddd",
            source_type="document",
        )
    results = storage.search_knowledge("shared-limit-marker-ddd", limit=2)
    assert len(results) == 2


def test_search_no_match_returns_empty_list():
    results = storage.search_knowledge("this-string-should-never-appear-anywhere-zzz")
    assert results == []


def test_search_results_ordered_most_recent_first():
    storage.store_knowledge(
        path="/tmp/order-test-old.md",
        summary="order-marker-eee first",
        source_type="document",
    )
    storage.store_knowledge(
        path="/tmp/order-test-new.md",
        summary="order-marker-eee second",
        source_type="document",
    )
    results = storage.search_knowledge("order-marker-eee")
    assert len(results) >= 2
    # The most recently inserted row should come first.
    assert results[0]["path"] == "/tmp/order-test-new.md"


def test_store_knowledge_records_question_and_source_type():
    storage.store_knowledge(
        path="https://youtu.be/unique-marker-fff",
        summary="a video about testing",
        source_type="youtube",
        question="what is this video about?",
    )
    results = storage.search_knowledge("unique-marker-fff")
    assert len(results) == 1
    assert results[0]["source_type"] == "youtube"
    assert results[0]["question"] == "what is this video about?"


def test_store_knowledge_question_defaults_to_none():
    storage.store_knowledge(
        path="/tmp/no-question-marker-ggg.md",
        summary="a generic summary",
        source_type="document",
    )
    results = storage.search_knowledge("no-question-marker-ggg")
    assert len(results) == 1
    assert results[0]["question"] is None
