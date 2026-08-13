"""
Tests for Milestone 5.7's File Management expansion: read_file, copy_file,
move_file, rename_file. Uses pytest's tmp_path fixture to exercise the real
filesystem, same convention as test_index_project.py — these tools' whole
job is filesystem side effects, which a mock would just re-describe rather
than actually verify. delete_file (Milestone 0) is intentionally not
retested here — it already predates this file and isn't part of this
slice's scope.
"""
import tools


# --- read_file ---------------------------------------------------------

def test_read_file_returns_contents(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world")
    result = tools.read_file(str(f))
    assert result["status"] == "ok"
    assert result["content"] == "hello world"
    assert result["truncated"] is False


def test_read_file_missing_path_returns_error():
    result = tools.read_file("/definitely/does/not/exist.txt")
    assert result["status"] == "error"


def test_read_file_on_a_directory_returns_error(tmp_path):
    result = tools.read_file(str(tmp_path))
    assert result["status"] == "error"


def test_read_file_on_binary_file_returns_clean_error_not_a_crash(tmp_path):
    f = tmp_path / "bin.dat"
    f.write_bytes(b"\xff\xfe\x00\x01\x02")
    result = tools.read_file(str(f))
    assert result["status"] == "error"
    assert "binary" in result["message"].lower() or "decode" in result["message"].lower()


def test_read_file_truncates_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "READ_FILE_MAX_CHARS", 10)
    f = tmp_path / "big.txt"
    f.write_text("x" * 50)
    result = tools.read_file(str(f))
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["chars_read"] == 10


# --- copy_file -----------------------------------------------------------

def test_copy_file_creates_destination_with_same_content(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    dst = tmp_path / "b.txt"
    result = tools.copy_file(str(src), str(dst))
    assert result["status"] == "ok"
    assert dst.read_text() == "hello"
    assert src.exists()  # copy, not move — source survives


def test_copy_file_missing_source_returns_error(tmp_path):
    result = tools.copy_file(str(tmp_path / "nope.txt"), str(tmp_path / "b.txt"))
    assert result["status"] == "error"


def test_copy_file_source_is_a_directory_returns_error(tmp_path):
    (tmp_path / "adir").mkdir()
    result = tools.copy_file(str(tmp_path / "adir"), str(tmp_path / "b.txt"))
    assert result["status"] == "error"


def test_copy_file_missing_destination_parent_returns_error(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    result = tools.copy_file(str(src), str(tmp_path / "no_such_dir" / "b.txt"))
    assert result["status"] == "error"


def test_copy_file_existing_destination_requires_explicit_overwrite(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("new")
    dst = tmp_path / "b.txt"
    dst.write_text("old")

    result = tools.copy_file(str(src), str(dst))
    assert result["status"] == "error"
    assert dst.read_text() == "old"  # untouched

    result = tools.copy_file(str(src), str(dst), overwrite=True)
    assert result["status"] == "ok"
    assert dst.read_text() == "new"


# --- move_file -------------------------------------------------------------

def test_move_file_relocates_and_removes_source(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    dst = tmp_path / "sub"
    dst.mkdir()
    result = tools.move_file(str(src), str(dst / "a.txt"))
    assert result["status"] == "ok"
    assert not src.exists()
    assert (dst / "a.txt").read_text() == "hello"


def test_move_file_missing_source_returns_error(tmp_path):
    result = tools.move_file(str(tmp_path / "nope.txt"), str(tmp_path / "b.txt"))
    assert result["status"] == "error"


def test_move_file_existing_destination_requires_explicit_overwrite(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("new")
    dst = tmp_path / "b.txt"
    dst.write_text("old")

    result = tools.move_file(str(src), str(dst))
    assert result["status"] == "error"
    assert src.exists()  # nothing happened
    assert dst.read_text() == "old"

    result = tools.move_file(str(src), str(dst), overwrite=True)
    assert result["status"] == "ok"
    assert not src.exists()
    assert dst.read_text() == "new"


# --- rename_file -------------------------------------------------------

def test_rename_file_renames_in_place(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    result = tools.rename_file(str(src), "b.txt")
    assert result["status"] == "ok"
    assert not src.exists()
    assert (tmp_path / "b.txt").read_text() == "hello"


def test_rename_file_rejects_new_name_with_path_separator(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    result = tools.rename_file(str(src), "../escape.txt")
    assert result["status"] == "error"
    assert src.exists()  # untouched


def test_rename_file_rejects_dot_and_dotdot_as_new_name(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    assert tools.rename_file(str(src), ".")["status"] == "error"
    assert tools.rename_file(str(src), "..")["status"] == "error"
    assert tools.rename_file(str(src), "")["status"] == "error"


def test_rename_file_missing_source_returns_error(tmp_path):
    result = tools.rename_file(str(tmp_path / "nope.txt"), "b.txt")
    assert result["status"] == "error"


def test_rename_file_existing_target_requires_explicit_overwrite(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("new")
    (tmp_path / "b.txt").write_text("old")

    result = tools.rename_file(str(src), "b.txt")
    assert result["status"] == "error"
    assert src.exists()

    result = tools.rename_file(str(src), "b.txt", overwrite=True)
    assert result["status"] == "ok"
    assert (tmp_path / "b.txt").read_text() == "new"


# --- registration / tiers -----------------------------------------------

def test_new_file_management_tools_are_registered_with_expected_tiers():
    assert tools.TOOLS["read_file"]["tier"] == "safe"
    assert tools.TOOLS["copy_file"]["tier"] == "caution"
    assert tools.TOOLS["move_file"]["tier"] == "caution"
    assert tools.TOOLS["rename_file"]["tier"] == "caution"
    assert tools.TOOLS["delete_file"]["tier"] == "dangerous"  # unchanged