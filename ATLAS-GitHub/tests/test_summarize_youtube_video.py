"""
Tests for tools.summarize_youtube_video() — Milestone 3's second Knowledge
Companion slice.

Two things get mocked here, same reasoning as test_summarize_document.py:
- tools._call_reasoning_model (the LLM call) — this suite should never
  depend on Ollama actually running.
- tools.YouTubeTranscriptApi (the transcript fetch) — this suite should
  never depend on network access or a real YouTube video existing.

Video-ID extraction itself is deterministic and exercised directly against
real URL strings, no mocking needed.
"""
import pytest

import tools


def _mock_model(monkeypatch, response="a concise summary", capture=None):
    def fake_call(prompt):
        if capture is not None:
            capture.append(prompt)
        return response
    monkeypatch.setattr(tools, "_call_reasoning_model", fake_call)


class _FakeSnippet:
    def __init__(self, text):
        self.text = text


class _FakeFetchedTranscript:
    def __init__(self, snippets):
        self._snippets = snippets

    def __iter__(self):
        return iter(self._snippets)


def _mock_transcript(monkeypatch, texts=("Hello there.", "This is the transcript."),
                      raise_error=None):
    def fake_fetch(self, video_id, languages=("en",)):
        if raise_error is not None:
            raise raise_error
        return _FakeFetchedTranscript([_FakeSnippet(t) for t in texts])

    class _FakeApi:
        def __init__(self):
            pass

        fetch = fake_fetch

    monkeypatch.setattr(tools, "YouTubeTranscriptApi", _FakeApi)


# ---------------------------------------------------------------------------
# Video ID extraction (deterministic, no mocking)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected_id", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),  # bare video ID
])
def test_extract_video_id_handles_common_formats(url, expected_id):
    assert tools._extract_youtube_video_id(url) == expected_id


def test_extract_video_id_returns_none_for_garbage():
    assert tools._extract_youtube_video_id("https://example.com/not-a-video") is None


# ---------------------------------------------------------------------------
# summarize_youtube_video
# ---------------------------------------------------------------------------

def test_unrecognized_url_returns_error(monkeypatch):
    _mock_transcript(monkeypatch)
    result = tools.summarize_youtube_video("https://example.com/nope")
    assert result["status"] == "error"
    assert "couldn't find a YouTube video ID" in result["message"]


def test_transcripts_disabled_returns_clean_error(monkeypatch):
    _mock_transcript(monkeypatch, raise_error=tools.CouldNotRetrieveTranscript("no captions"))
    result = tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ")
    assert result["status"] == "error"
    assert "no transcript available" in result["message"]


def test_unexpected_fetch_error_returns_clean_error(monkeypatch):
    _mock_transcript(monkeypatch, raise_error=RuntimeError("network blew up"))
    result = tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ")
    assert result["status"] == "error"
    assert "transcript fetch failed" in result["message"]
    assert "network blew up" in result["message"]


def test_empty_transcript_returns_error(monkeypatch):
    _mock_transcript(monkeypatch, texts=("   ", ""))
    result = tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ")
    assert result["status"] == "error"
    assert "empty" in result["message"]


def test_successful_summary(monkeypatch):
    _mock_transcript(monkeypatch, texts=("Talks about X.", "Then concludes with Y."))
    _mock_model(monkeypatch, response="Covers X and concludes with Y.")
    result = tools.summarize_youtube_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert result["status"] == "ok"
    assert result["video_id"] == "dQw4w9WgXcQ"
    assert result["summary"] == "Covers X and concludes with Y."
    assert result["truncated"] is False


def test_question_param_changes_the_prompt(monkeypatch):
    captured = []
    _mock_transcript(monkeypatch, texts=("The price is $10/month.",))
    _mock_model(monkeypatch, response="Yes, $10/month.", capture=captured)
    result = tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ",
                                            question="does this mention pricing?")
    assert result["status"] == "ok"
    assert result["summary"] == "Yes, $10/month."
    assert len(captured) == 1
    assert "does this mention pricing?" in captured[0]
    assert "Answer the following question" in captured[0]


def test_generic_summary_prompt_when_no_question(monkeypatch):
    captured = []
    _mock_transcript(monkeypatch)
    _mock_model(monkeypatch, capture=captured)
    tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ")
    assert "Summarize the following video transcript" in captured[0]


def test_long_transcript_is_truncated(monkeypatch):
    _mock_transcript(monkeypatch, texts=("x" * (tools.SUMMARIZE_MAX_CHARS + 5000),))
    _mock_model(monkeypatch)
    result = tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ")
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["chars_read"] == tools.SUMMARIZE_MAX_CHARS


def test_model_call_failure_returns_error(monkeypatch):
    _mock_transcript(monkeypatch)

    def raise_err(prompt):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(tools, "_call_reasoning_model", raise_err)
    result = tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ")
    assert result["status"] == "error"
    assert "connection refused" in result["message"]


def test_summarize_youtube_video_is_registered_as_safe_tier():
    assert tools.TOOLS["summarize_youtube_video"]["tier"] == "safe"


def test_summarize_youtube_video_registered_with_correct_fn():
    assert tools.TOOLS["summarize_youtube_video"]["fn"] is tools.summarize_youtube_video


def test_successful_summary_stores_knowledge(monkeypatch):
    """Milestone 3's knowledge-storage slice: a successful
    summarize_youtube_video call should record the result via
    storage.store_knowledge()."""
    _mock_transcript(monkeypatch)
    _mock_model(monkeypatch, response="a concise summary")
    calls = []
    monkeypatch.setattr(
        tools.storage, "store_knowledge",
        lambda **kwargs: calls.append(kwargs) or 1,
    )
    url = "https://youtu.be/dQw4w9WgXcQ"
    result = tools.summarize_youtube_video(url, question="what's the main point?")
    assert result["status"] == "ok"
    assert len(calls) == 1
    assert calls[0]["path"] == url
    assert calls[0]["summary"] == "a concise summary"
    assert calls[0]["source_type"] == "youtube"
    assert calls[0]["question"] == "what's the main point?"


def test_storage_failure_does_not_break_summary(monkeypatch):
    """Knowledge storage is best-effort — a storage exception must not turn
    an otherwise-successful summary into an error response."""
    _mock_transcript(monkeypatch)
    _mock_model(monkeypatch, response="a concise summary")

    def raise_err(**kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(tools.storage, "store_knowledge", raise_err)
    result = tools.summarize_youtube_video("https://youtu.be/dQw4w9WgXcQ")
    assert result["status"] == "ok"
    assert result["summary"] == "a concise summary"