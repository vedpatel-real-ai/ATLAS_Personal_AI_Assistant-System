"""
Tests for Milestone 5.7's Clipboard tools: read_clipboard, write_clipboard.

subprocess.run is mocked throughout, same convention as test_search_code.py
(ripgrep) — pbcopy/pbpaste are macOS-only, so these tests must not depend
on actually running on a Mac. The "binary not found" path is checked via a
real, unmocked FileNotFoundError, same posture as test_search_code.py's
missing-ripgrep test.
"""
from types import SimpleNamespace

import tools


# --- read_clipboard ------------------------------------------------------

def test_read_clipboard_returns_contents(monkeypatch):
    def fake_run(cmd, capture_output, timeout):
        assert cmd == ["pbpaste"]
        return SimpleNamespace(returncode=0, stdout=b"hello clipboard", stderr=b"")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.read_clipboard()
    assert result["status"] == "ok"
    assert result["content"] == "hello clipboard"
    assert result["truncated"] is False


def test_read_clipboard_empty_is_not_an_error(monkeypatch):
    def fake_run(cmd, capture_output, timeout):
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.read_clipboard()
    assert result["status"] == "ok"
    assert result["content"] == ""


def test_read_clipboard_missing_pbpaste_gives_clear_message(monkeypatch):
    def fake_run(cmd, capture_output, timeout):
        raise FileNotFoundError()

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.read_clipboard()
    assert result["status"] == "error"
    assert "macos" in result["message"].lower()


def test_read_clipboard_nonzero_exit_is_an_error(monkeypatch):
    def fake_run(cmd, capture_output, timeout):
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.read_clipboard()
    assert result["status"] == "error"
    assert "boom" in result["message"]


def test_read_clipboard_non_utf8_binary_content_returns_clean_error(monkeypatch):
    def fake_run(cmd, capture_output, timeout):
        return SimpleNamespace(returncode=0, stdout=b"\xff\xfe\x00\x01", stderr=b"")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.read_clipboard()
    assert result["status"] == "error"
    assert "binary" in result["message"].lower() or "utf-8" in result["message"].lower()


def test_read_clipboard_truncates_at_the_cap(monkeypatch):
    monkeypatch.setattr(tools, "CLIPBOARD_MAX_CHARS", 10)

    def fake_run(cmd, capture_output, timeout):
        return SimpleNamespace(returncode=0, stdout=(b"x" * 50), stderr=b"")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.read_clipboard()
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["chars_read"] == 10


# --- write_clipboard -------------------------------------------------------

def test_write_clipboard_sends_text_to_pbcopy(monkeypatch):
    captured = {}

    def fake_run(cmd, input, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.write_clipboard("hello there")
    assert result["status"] == "ok"
    assert result["chars_written"] == len("hello there")
    assert captured["cmd"] == ["pbcopy"]
    assert captured["input"] == b"hello there"


def test_write_clipboard_rejects_empty_text(monkeypatch):
    result = tools.write_clipboard("")
    assert result["status"] == "error"


def test_write_clipboard_missing_pbcopy_gives_clear_message(monkeypatch):
    def fake_run(cmd, input, timeout):
        raise FileNotFoundError()

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.write_clipboard("hello")
    assert result["status"] == "error"
    assert "macos" in result["message"].lower()


def test_write_clipboard_nonzero_exit_is_an_error(monkeypatch):
    def fake_run(cmd, input, timeout):
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.write_clipboard("hello")
    assert result["status"] == "error"


# --- registration / tiers -----------------------------------------------

def test_clipboard_tools_are_registered_with_expected_tiers():
    assert tools.TOOLS["read_clipboard"]["tier"] == "safe"
    assert tools.TOOLS["write_clipboard"]["tier"] == "caution"