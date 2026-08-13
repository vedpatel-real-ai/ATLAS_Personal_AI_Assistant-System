"""
Tests for tools.search_code() — Milestone 1's rg-backed code search tool.

subprocess.run is mocked for the behavioral tests (exit-code handling,
--type flag, truncation) so these don't depend on ripgrep actually being
installed on whatever machine runs the suite. The "rg not installed" path
is checked for real, since it's the one behavior that's actually easy to
exercise honestly (rg not being on PATH is a normal, unmocked FileNotFoundError).
"""
from types import SimpleNamespace

import tools


def test_path_not_found_returns_error():
    result = tools.search_code("foo", project_path="/definitely/not/real")
    assert result["status"] == "error"


def test_missing_ripgrep_gives_clear_install_message(tmp_path, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        raise FileNotFoundError()

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.search_code("TODO", project_path=str(tmp_path))
    assert result["status"] == "error"
    assert "ripgrep" in result["message"].lower()
    assert "brew install" in result["message"]


def test_successful_search_returns_matches(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="file.py:3:def foo():\n", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.search_code("def foo", project_path=str(tmp_path))
    assert result["status"] == "ok"
    assert result["match_count"] == 1
    assert "file.py:3:def foo():" in result["matches"]
    assert "def foo" in captured["cmd"]


def test_file_type_flag_is_passed_through(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=1, stdout="", stderr="")  # rg's "no matches" exit code

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.search_code("foo", project_path=str(tmp_path), file_type="py")
    assert result["status"] == "ok"  # exit code 1 (no matches) is NOT an error
    assert result["match_count"] == 0
    assert "--type" in captured["cmd"]
    assert "py" in captured["cmd"]


def test_real_ripgrep_failure_exit_code_is_reported_as_error(tmp_path, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return SimpleNamespace(returncode=2, stdout="", stderr="regex parse error")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.search_code("[invalid(regex", project_path=str(tmp_path))
    assert result["status"] == "error"
    assert "regex parse error" in result["message"]


def test_match_output_is_truncated_past_the_cap(tmp_path, monkeypatch):
    many_lines = "\n".join(f"file.py:{i}:match" for i in range(tools.CODE_SEARCH_MAX_MATCHES + 50))

    def fake_run(cmd, capture_output, text, timeout):
        return SimpleNamespace(returncode=0, stdout=many_lines, stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.search_code("match", project_path=str(tmp_path))
    assert result["truncated"] is True
    assert result["match_count"] == tools.CODE_SEARCH_MAX_MATCHES