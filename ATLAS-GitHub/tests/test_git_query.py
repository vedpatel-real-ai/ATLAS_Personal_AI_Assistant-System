"""
Tests for tools.git_query() — Milestone 1's read-only git tool.

Uses a real throwaway git repo under tmp_path (git itself is a safe local
dependency to assume, same as the rest of this project already does via
run_shell_command's git_status/git_pull entries) rather than mocking
subprocess, so the actual git plumbing is what's under test.
"""
import subprocess

import pytest

import tools


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "file.txt").write_text("hello\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo)
    return repo


def test_unknown_command_name_is_rejected(git_repo):
    result = tools.git_query("git_rebase_everything", cwd=str(git_repo))
    assert result["status"] == "error"
    assert "not a recognized" in result["message"]


def test_path_not_found_returns_error():
    result = tools.git_query("git_log", cwd="/definitely/not/a/real/path")
    assert result["status"] == "error"


def test_git_log_returns_the_commit(git_repo):
    result = tools.git_query("git_log", cwd=str(git_repo))
    assert result["status"] == "ok"
    assert "initial commit" in result["stdout"]


def test_git_status_short_reports_untracked_file(git_repo):
    (git_repo / "untracked.txt").write_text("new\n")
    result = tools.git_query("git_status_short", cwd=str(git_repo))
    assert result["status"] == "ok"
    assert "untracked.txt" in result["stdout"]


def test_git_diff_reports_unstaged_change(git_repo):
    (git_repo / "file.txt").write_text("changed\n")
    result = tools.git_query("git_diff", cwd=str(git_repo))
    assert result["status"] == "ok"
    assert "changed" in result["stdout"]


def test_git_branch_returns_current_branch_name(git_repo):
    result = tools.git_query("git_branch", cwd=str(git_repo))
    assert result["status"] == "ok"
    # Default branch name varies by git config (main vs master) — just
    # confirm something non-empty came back, not a specific name.
    assert result["stdout"].strip() != ""


def test_git_query_is_registered_as_safe_tier():
    """The whole point of splitting this out from run_shell_command: it
    should never require a phone confirmation."""
    assert tools.TOOLS["git_query"]["tier"] == "safe"