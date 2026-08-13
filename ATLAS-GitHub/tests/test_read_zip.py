"""
Tests for tools.read_zip() — Milestone 1's ZIP-reader tool.

Builds real small zip files under tmp_path with Python's zipfile module
(same one read_zip itself uses) so this is a real round-trip, not a mock.
"""
import zipfile

import tools


def _make_zip(path, files: dict):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def test_path_not_found_returns_error():
    result = tools.read_zip("/definitely/not/a/real.zip")
    assert result["status"] == "error"


def test_non_zip_file_returns_error(tmp_path):
    f = tmp_path / "not_a_zip.txt"
    f.write_text("hello")
    result = tools.read_zip(str(f))
    assert result["status"] == "error"
    assert "not a valid zip" in result["message"]


def test_corrupt_zip_returns_error(tmp_path):
    f = tmp_path / "broken.zip"
    # Valid enough header to pass is_zipfile's cheap check in some cases is
    # tricky to fake reliably, so just confirm garbage bytes are rejected
    # by is_zipfile up front (the common real-world case: a truncated upload).
    f.write_bytes(b"not actually a zip at all")
    result = tools.read_zip(str(f))
    assert result["status"] == "error"


def test_counts_files_and_classifies_languages(tmp_path):
    zpath = _make_zip(tmp_path / "project.zip", {
        "main.py": "print('hi')",
        "utils.py": "x = 1",
        "index.js": "console.log('hi')",
        "README.md": "# hi",
    })
    result = tools.read_zip(str(zpath))
    assert result["status"] == "ok"
    assert result["total_files"] == 4
    assert result["languages"]["Python"] == 2
    assert result["languages"]["JavaScript"] == 1


def test_excluded_dirs_inside_zip_are_skipped(tmp_path):
    zpath = _make_zip(tmp_path / "project.zip", {
        "app.py": "x = 1",
        "node_modules/leftpad/index.js": "module.exports = {}",
        "venv/lib/site.py": "pass",
        ".git/HEAD": "ref: refs/heads/main",
    })
    result = tools.read_zip(str(zpath))
    assert result["status"] == "ok"
    assert result["total_files"] == 1
    assert "JavaScript" not in result["languages"]


def test_directory_entries_are_not_counted_as_files(tmp_path):
    zpath = tmp_path / "project.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("src/", "")  # explicit directory entry, no content
        zf.writestr("src/main.py", "x = 1")
    result = tools.read_zip(str(zpath))
    assert result["status"] == "ok"
    assert result["total_files"] == 1


def test_read_zip_is_registered_as_safe_tier():
    assert tools.TOOLS["read_zip"]["tier"] == "safe"