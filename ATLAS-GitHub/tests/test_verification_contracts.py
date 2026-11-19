import errno
import os

import pytest
from fastapi.testclient import TestClient

import benchmark_models
import main
import tools


@pytest.mark.parametrize("tool,params", [
    ("send_email", {"to": "fixture", "subject": "s", "body": "b"}),
    ("get_stock_price", {"ticker": "TEST"}),
])
def test_placeholder_result_is_unsuccessful(tool, params):
    result = main.execute_tool(tool, params)
    assert result["result"]["status"] == "not_implemented"
    assert result["success"] is False


def test_successful_read_passes_through_wrapper(tmp_path):
    p = tmp_path / "text"
    p.write_text("from disk")
    result = main.execute_tool("read_file", {"path": str(p)})
    assert result["success"] and result["result"]["content"] == "from disk"


@pytest.mark.parametrize("method,path,body", [
    ("post", "/command", {"text": "battery"}),
    ("post", "/command/async", {"text": "battery"}),
    ("get", "/jobs", None), ("get", "/jobs/missing", None),
    ("post", "/jobs/missing/cancel", None),
    ("post", "/jobs/missing/confirm", {"confirm_token": "test"}),
    ("post", "/shutdown", None),
])
def test_unconfigured_service_refuses_privileged_routes(monkeypatch, method, path, body):
    monkeypatch.setattr(main, "API_KEY", "")
    with TestClient(main.app) as client:
        assert client.request(method, path, json=body, headers={"X-API-Key": "test"}).status_code == 503


def test_only_configured_key_authorizes_job_listing():
    with TestClient(main.app) as client:
        for key in ("", "wrong"):
            assert client.get("/jobs", headers={"X-API-Key": key}).status_code == 401
        assert client.get("/jobs", headers={"X-API-Key": "test"}).status_code == 200


@pytest.mark.parametrize("operation", ["move", "rename"])
@pytest.mark.parametrize("alias", ["same", "hardlink", "symlink"])
def test_same_file_overwrite_preserves_both_names(tmp_path, operation, alias):
    source = tmp_path / "source"
    source.write_text("keep")
    dest = source if alias == "same" else tmp_path / alias
    if alias == "hardlink":
        dest.hardlink_to(source)
    elif alias == "symlink":
        dest.symlink_to(source)
    result = (tools.move_file(str(source), str(dest), True) if operation == "move"
              else tools.rename_file(str(source), dest.name, True))
    assert result["status"] == "error"
    assert dest.samefile(source) and source.read_text() == "keep"


@pytest.mark.parametrize("operation", ["move", "rename"])
def test_failed_replace_keeps_both_originals(tmp_path, monkeypatch, operation):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_text("new")
    dst.write_text("old")

    def fail(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "replace", fail)
    result = (tools.move_file(str(src), str(dst), True) if operation == "move"
              else tools.rename_file(str(src), dst.name, True))
    assert result["status"] == "error"
    assert (src.read_text(), dst.read_text()) == ("new", "old")


@pytest.mark.parametrize("copy_fails", [True, False])
def test_cross_device_overwrite_stages_before_replacing(tmp_path, monkeypatch, copy_fails):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_text("new")
    dst.write_text("old")
    replace = os.replace

    def cross_device(source, destination):
        if source == src:
            raise OSError(errno.EXDEV, "different volumes")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", cross_device)
    if copy_fails:
        def fail_copy(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(tools.shutil, "copy2", fail_copy)
    result = tools.move_file(str(src), str(dst), True)
    if copy_fails:
        assert result["status"] == "error"
        assert (src.read_text(), dst.read_text()) == ("new", "old")
    else:
        assert result["status"] == "ok" and not src.exists()
        assert dst.read_text() == "new"


def test_focus_path_resolves_from_project_even_when_cwd_is_nested(tmp_path, monkeypatch):
    nested = tmp_path / "pkg"
    nested.mkdir()
    (tmp_path / "entry.py").write_text("import math")
    monkeypatch.chdir(nested)
    result = tools.dependency_graph(str(tmp_path), focus_file="entry.py")
    assert result["focus_file"] == "entry.py"


def test_observed_zero_vram_is_preserved(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "test", "size": 1024, "size_vram": 0}]}

    monkeypatch.setattr(benchmark_models.requests, "get", lambda *a, **kw: Response())
    assert benchmark_models._sample_loaded_model_size("unused", "test")["size_vram_mb"] == 0
