"""
Tests for tools.index_project() — Milestone 1's project-indexing tool.

Uses pytest's tmp_path fixture to build small real directory trees rather
than mocking the filesystem, since the behavior under test (exclusion
pruning, language classification, caching) is exactly the kind of thing
that's easy to get subtly wrong with a mock and cheap to test for real.
"""
import time

import tools


def _touch(path, content="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_path_not_found_returns_error():
    result = tools.index_project("/definitely/does/not/exist/anywhere")
    assert result["status"] == "error"


def test_path_that_is_a_file_returns_error(tmp_path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("hello")
    result = tools.index_project(str(f))
    assert result["status"] == "error"


def test_counts_files_and_classifies_languages(tmp_path):
    _touch(tmp_path / "main.py")
    _touch(tmp_path / "utils.py")
    _touch(tmp_path / "index.js")
    _touch(tmp_path / "README.md")

    result = tools.index_project(str(tmp_path))
    assert result["status"] == "ok"
    assert result["total_files"] == 4
    assert result["languages"]["Python"] == 2
    assert result["languages"]["JavaScript"] == 1
    assert result["languages"]["Markdown"] == 1


def test_excluded_dirs_are_never_walked(tmp_path):
    _touch(tmp_path / "app.py")
    # A huge-in-spirit node_modules/venv tree — should be pruned entirely,
    # not just filtered after counting (the whole point is never walking it).
    _touch(tmp_path / "node_modules" / "leftpad" / "index.js")
    _touch(tmp_path / "venv" / "lib" / "site.py")
    _touch(tmp_path / ".git" / "HEAD")

    result = tools.index_project(str(tmp_path))
    assert result["status"] == "ok"
    assert result["total_files"] == 1
    assert "JavaScript" not in result["languages"]


def test_top_level_entries_exclude_noise_dirs(tmp_path):
    _touch(tmp_path / "src" / "main.py")
    _touch(tmp_path / "node_modules" / "pkg" / "index.js")
    (tmp_path / ".secrets").mkdir()

    result = tools.index_project(str(tmp_path))
    assert "src/" in result["top_level_entries"]
    assert "node_modules/" not in result["top_level_entries"]
    assert ".secrets/" not in result["top_level_entries"]


def test_repeated_call_within_ttl_is_served_from_cache(tmp_path, monkeypatch):
    _touch(tmp_path / "a.py")
    tools._index_cache.clear()

    first = tools.index_project(str(tmp_path))
    assert first["cached"] is False

    # Add a file that would change the result if the tree were re-walked —
    # the second call should still report the OLD count, proving it came
    # from cache rather than a fresh walk.
    _touch(tmp_path / "b.py")
    second = tools.index_project(str(tmp_path))
    assert second["cached"] is True
    assert second["total_files"] == first["total_files"] == 1


def test_cache_expires_after_ttl(tmp_path, monkeypatch):
    _touch(tmp_path / "a.py")
    tools._index_cache.clear()
    tools.index_project(str(tmp_path))

    # Simulate TTL expiry without actually sleeping in the test suite.
    key = str(tmp_path.resolve())
    tools._index_cache[key]["cached_at"] = time.time() - (tools.INDEX_CACHE_TTL_SECONDS + 1)

    _touch(tmp_path / "b.py")
    result = tools.index_project(str(tmp_path))
    assert result["cached"] is False
    assert result["total_files"] == 2