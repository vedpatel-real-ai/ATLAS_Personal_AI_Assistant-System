"""
Tests for tools.summarize_document() — Milestone 3's first Knowledge
Companion tool.

Two things get mocked here:
- tools._call_reasoning_model (the LLM call) — this suite should never
  depend on Ollama actually running, same reasoning as test_search_code.py
  mocking subprocess.run for ripgrep.
- tools._extract_pdf_text / tools._extract_docx_text (the extraction
  boundaries) — mocked for PDF/DOCX-path tests so this suite doesn't need
  to fabricate real binary fixtures or depend on pypdf's/python-docx's
  actual parsing; those are third-party libraries, not something this
  suite needs to re-test.

All the deterministic parts for plain-text files (path validation,
extension check, reading, truncation) are exercised against real files
under tmp_path, not mocked.
"""

import pytest

import tools


def _mock_model(monkeypatch, response="a concise summary", capture=None):
    def fake_call(prompt):
        if capture is not None:
            capture.append(prompt)
        return response

    monkeypatch.setattr(tools, "_call_reasoning_model", fake_call)


def _mock_pdf_extraction(monkeypatch, text=None, error=None):
    monkeypatch.setattr(tools, "_extract_pdf_text", lambda path: (text, error))


def _mock_docx_extraction(monkeypatch, text=None, error=None):
    monkeypatch.setattr(tools, "_extract_docx_text", lambda path: (text, error))


def test_path_not_found_returns_error():
    result = tools.summarize_document("/definitely/not/a/real/file.md")
    assert result["status"] == "error"
    assert "not found" in result["message"]


def test_directory_path_returns_error(tmp_path):
    result = tools.summarize_document(str(tmp_path))
    assert result["status"] == "error"
    assert "directory" in result["message"]


def test_unsupported_extension_returns_not_implemented(tmp_path):
    f = tmp_path / "report.odt"
    f.write_bytes(b"PK\x03\x04 fake odt bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "not_implemented"
    assert "odt" in result["message"] or ".odt" in result["message"]


def test_empty_file_returns_error(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text("   \n  ")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "empty" in result["message"]


def test_successful_summary_of_text_file(monkeypatch, tmp_path):
    _mock_model(monkeypatch, response="This doc covers X, Y, and Z.")
    f = tmp_path / "notes.md"
    f.write_text("# Notes\n\nSome important content about X, Y, and Z.")
    result = tools.summarize_document(str(f))
    assert result["status"] == "ok"
    assert result["summary"] == "This doc covers X, Y, and Z."
    assert result["truncated"] is False
    assert result["file_path"] == str(f)


def test_question_param_changes_the_prompt(monkeypatch, tmp_path):
    captured = []
    _mock_model(monkeypatch, response="Yes, it mentions pricing.", capture=captured)
    f = tmp_path / "doc.txt"
    f.write_text("The pricing is $10/month.")
    result = tools.summarize_document(str(f), question="does this mention pricing?")
    assert result["status"] == "ok"
    assert result["summary"] == "Yes, it mentions pricing."
    assert len(captured) == 1
    assert "does this mention pricing?" in captured[0]
    assert "Answer the following question" in captured[0]


def test_generic_summary_prompt_when_no_question(monkeypatch, tmp_path):
    captured = []
    _mock_model(monkeypatch, capture=captured)
    f = tmp_path / "doc.txt"
    f.write_text("Some content.")
    tools.summarize_document(str(f))
    assert "Summarize the following document" in captured[0]


def test_large_file_is_truncated(monkeypatch, tmp_path):
    _mock_model(monkeypatch)
    f = tmp_path / "big.log"
    f.write_text("x" * (tools.SUMMARIZE_MAX_CHARS + 5000))
    result = tools.summarize_document(str(f))
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["chars_read"] == tools.SUMMARIZE_MAX_CHARS


def test_model_call_failure_returns_error(monkeypatch, tmp_path):
    def raise_err(prompt):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(tools, "_call_reasoning_model", raise_err)
    f = tmp_path / "doc.txt"
    f.write_text("some content")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "connection refused" in result["message"]


def test_summarize_document_is_registered_as_safe_tier():
    assert tools.TOOLS["summarize_document"]["tier"] == "safe"


def test_summarize_document_registered_with_correct_fn():
    assert tools.TOOLS["summarize_document"]["fn"] is tools.summarize_document


# ---------------------------------------------------------------------------
# PDF support (this session) — extraction itself mocked, see module docstring
# ---------------------------------------------------------------------------


def test_pdf_successful_extraction_and_summary(monkeypatch, tmp_path):
    _mock_pdf_extraction(monkeypatch, text="This report covers Q3 results and outlook.")
    _mock_model(monkeypatch, response="Q3 results and outlook summary.")
    f = tmp_path / "report.pdf"
    f.write_bytes(
        b"%PDF-1.4 fake pdf bytes"
    )  # never actually parsed — extraction is mocked
    result = tools.summarize_document(str(f))
    assert result["status"] == "ok"
    assert result["summary"] == "Q3 results and outlook summary."
    assert result["file_path"] == str(f)


def test_pdf_extraction_failure_returns_clean_error(monkeypatch, tmp_path):
    _mock_pdf_extraction(
        monkeypatch,
        error="PDF is password-protected; can't extract " "text without the password.",
    )
    f = tmp_path / "locked.pdf"
    f.write_bytes(b"%PDF-1.4 fake pdf bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "password-protected" in result["message"]


def test_pdf_scanned_no_text_returns_clean_error(monkeypatch, tmp_path):
    _mock_pdf_extraction(
        monkeypatch,
        error="no extractable text found (this may be a "
        "scanned/image-only PDF — OCR isn't "
        "supported yet).",
    )
    f = tmp_path / "scanned.pdf"
    f.write_bytes(b"%PDF-1.4 fake pdf bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "scanned" in result["message"]


def test_pdf_question_param_changes_the_prompt(monkeypatch, tmp_path):
    captured = []
    _mock_pdf_extraction(monkeypatch, text="The contract renews annually at $500.")
    _mock_model(monkeypatch, response="Yes, $500/year.", capture=captured)
    f = tmp_path / "contract.pdf"
    f.write_bytes(b"%PDF-1.4 fake pdf bytes")
    result = tools.summarize_document(str(f), question="what is the renewal cost?")
    assert result["status"] == "ok"
    assert len(captured) == 1
    assert "what is the renewal cost?" in captured[0]


def test_pdf_large_extracted_text_is_truncated(monkeypatch, tmp_path):
    _mock_pdf_extraction(monkeypatch, text="x" * (tools.SUMMARIZE_MAX_CHARS + 5000))
    _mock_model(monkeypatch)
    f = tmp_path / "big.pdf"
    f.write_bytes(b"%PDF-1.4 fake pdf bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["chars_read"] == tools.SUMMARIZE_MAX_CHARS


def test_pypdf_not_installed_returns_clean_error(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "PdfReader", None)
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 fake pdf bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "pypdf is not installed" in result["message"]


def test_extract_pdf_text_skips_bad_pages_but_keeps_good_ones(monkeypatch, tmp_path):
    """Exercises _extract_pdf_text itself (not summarize_document) to confirm
    a page that raises during extraction is skipped rather than aborting
    the whole file — a partial summary beats no summary."""

    class _GoodPage:
        def extract_text(self):
            return "good page text"

    class _BadPage:
        def extract_text(self):
            raise RuntimeError("malformed page object")

    class _FakeReader:
        def __init__(self, path):
            self.is_encrypted = False
            self.pages = [_GoodPage(), _BadPage(), _GoodPage()]

    monkeypatch.setattr(tools, "PdfReader", _FakeReader)
    f = tmp_path / "partial.pdf"
    f.write_bytes(b"%PDF-1.4 fake pdf bytes")
    text, error = tools._extract_pdf_text(f)
    assert error is None
    assert text.count("good page text") == 2


# ---------------------------------------------------------------------------
# DOCX tests — mirrors the PDF section above. Uses the same
# extraction-boundary mocking for summarize_document-level tests, plus one
# unmocked end-to-end test against a real python-docx-generated file so the
# actual extraction logic (paragraphs + tables) gets exercised at least once.
# ---------------------------------------------------------------------------


def test_docx_successful_extraction_and_summary(monkeypatch, tmp_path):
    _mock_docx_extraction(
        monkeypatch, text="This report covers Q3 results and outlook."
    )
    _mock_model(monkeypatch, response="Q3 results and outlook summary.")
    f = tmp_path / "report.docx"
    f.write_bytes(
        b"PK\x03\x04 fake docx bytes"
    )  # never actually parsed — extraction is mocked
    result = tools.summarize_document(str(f))
    assert result["status"] == "ok"
    assert result["summary"] == "Q3 results and outlook summary."
    assert result["file_path"] == str(f)


def test_docx_extraction_failure_returns_clean_error(monkeypatch, tmp_path):
    _mock_docx_extraction(
        monkeypatch,
        error="couldn't open DOCX (corrupt, password-protected, or not a valid .docx file): bad magic",
    )
    f = tmp_path / "locked.docx"
    f.write_bytes(b"PK\x03\x04 fake docx bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "password-protected" in result["message"]


def test_docx_no_text_returns_clean_error(monkeypatch, tmp_path):
    _mock_docx_extraction(
        monkeypatch,
        error="no extractable text found in this DOCX file.",
    )
    f = tmp_path / "empty.docx"
    f.write_bytes(b"PK\x03\x04 fake docx bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "no extractable text" in result["message"]


def test_docx_question_param_changes_the_prompt(monkeypatch, tmp_path):
    captured = []
    _mock_docx_extraction(monkeypatch, text="The contract renews annually at $500.")
    _mock_model(monkeypatch, response="Yes, $500/year.", capture=captured)
    f = tmp_path / "contract.docx"
    f.write_bytes(b"PK\x03\x04 fake docx bytes")
    result = tools.summarize_document(str(f), question="what is the renewal cost?")
    assert result["status"] == "ok"
    assert len(captured) == 1
    assert "what is the renewal cost?" in captured[0]


def test_docx_large_extracted_text_is_truncated(monkeypatch, tmp_path):
    _mock_docx_extraction(monkeypatch, text="x" * (tools.SUMMARIZE_MAX_CHARS + 5000))
    _mock_model(monkeypatch)
    f = tmp_path / "big.docx"
    f.write_bytes(b"PK\x03\x04 fake docx bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["chars_read"] == tools.SUMMARIZE_MAX_CHARS


def test_python_docx_not_installed_returns_clean_error(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "docx_lib", None)
    f = tmp_path / "report.docx"
    f.write_bytes(b"PK\x03\x04 fake docx bytes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert "python-docx is not installed" in result["message"]


def test_extract_docx_text_real_file_paragraphs_and_tables(tmp_path):
    """Unmocked end-to-end test: builds a real .docx with python-docx and
    confirms _extract_docx_text pulls both paragraph text and table cell
    text, in that order."""
    document = tools.docx_lib.Document()
    document.add_paragraph("This is the introduction paragraph.")
    document.add_paragraph("A second paragraph with more detail.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Item"
    table.cell(0, 1).text = "Price"
    table.cell(1, 0).text = "Widget"
    table.cell(1, 1).text = "$9.99"
    f = tmp_path / "real.docx"
    document.save(str(f))

    text, error = tools._extract_docx_text(f)
    assert error is None
    assert "introduction paragraph" in text
    assert "second paragraph" in text
    assert "Widget" in text and "$9.99" in text
    # paragraphs should come before table content
    assert text.index("second paragraph") < text.index("Widget")


def test_successful_summary_stores_knowledge(monkeypatch, tmp_path):
    """Milestone 3's knowledge-storage slice: a successful summarize_document
    call should record the result via storage.store_knowledge()."""
    _mock_model(monkeypatch, response="a concise summary")
    calls = []
    monkeypatch.setattr(
        tools.storage, "store_knowledge",
        lambda **kwargs: calls.append(kwargs) or 1,
    )
    f = tmp_path / "notes.md"
    f.write_text("some notes")
    result = tools.summarize_document(str(f), question="what does this say?")
    assert result["status"] == "ok"
    assert len(calls) == 1
    assert calls[0]["path"] == str(f)
    assert calls[0]["summary"] == "a concise summary"
    assert calls[0]["source_type"] == "document"
    assert calls[0]["question"] == "what does this say?"


def test_storage_failure_does_not_break_summary(monkeypatch, tmp_path):
    """Knowledge storage is best-effort — a storage exception must not turn
    an otherwise-successful summary into an error response."""
    _mock_model(monkeypatch, response="a concise summary")

    def raise_err(**kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(tools.storage, "store_knowledge", raise_err)
    f = tmp_path / "notes.md"
    f.write_text("some notes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "ok"
    assert result["summary"] == "a concise summary"


def test_failed_summary_does_not_store_knowledge(monkeypatch, tmp_path):
    """A model-call failure shouldn't produce a stored (garbage) summary."""
    calls = []
    monkeypatch.setattr(
        tools.storage, "store_knowledge",
        lambda **kwargs: calls.append(kwargs) or 1,
    )

    def raise_err(prompt):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(tools, "_call_reasoning_model", raise_err)
    f = tmp_path / "notes.md"
    f.write_text("some notes")
    result = tools.summarize_document(str(f))
    assert result["status"] == "error"
    assert calls == []


def test_extract_docx_text_corrupt_file_returns_clean_error(tmp_path):
    f = tmp_path / "corrupt.docx"
    f.write_bytes(b"not actually a docx file at all")
    text, error = tools._extract_docx_text(f)
    assert text is None
    assert "couldn't open DOCX" in error