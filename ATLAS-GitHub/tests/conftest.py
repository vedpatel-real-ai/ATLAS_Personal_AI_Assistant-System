"""Isolate storage before main is imported, including during --collect-only."""
import os
import socket
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import storage


def pytest_configure(config):
    scratch = tempfile.TemporaryDirectory(prefix="atlas-collection-")
    patch = pytest.MonkeyPatch()
    config.add_cleanup(scratch.cleanup)
    config.add_cleanup(patch.undo)
    patch.setenv("ASSISTANT_API_KEY", "test")
    patch.setattr(storage, "DB_PATH", Path(scratch.name) / "atlas.db")
    patch.setattr(storage, "JSONL_PATH", Path(scratch.name) / "events.jsonl")


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, tmp_path_factory, monkeypatch):
    import main
    import tools

    runtime = tmp_path_factory.mktemp("runtime")
    monkeypatch.setattr(storage, "DB_PATH", runtime / "atlas.db")
    monkeypatch.setattr(storage, "JSONL_PATH", runtime / "events.jsonl")
    storage.init_db()
    monkeypatch.setattr(main, "_request_times", deque())
    monkeypatch.setattr(tools, "_index_cache", {})
    monkeypatch.setattr(tools.psutil, "sensors_battery",
                        lambda: SimpleNamespace(percent=50, power_plugged=False))
    project = runtime / "workspace"
    project.mkdir()
    (project / "main.py").write_text("import math\n")
    monkeypatch.chdir(project)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    violations = []
    original_popen = subprocess.Popen
    original_run = subprocess.run

    def guarded_popen(argv, *args, **kwargs):
        if isinstance(argv, list) and argv[0] == "git" and not kwargs.get("shell"):
            if Path(kwargs.get("cwd") or project).resolve().is_relative_to(tmp_path.resolve()):
                return original_popen(argv, *args, **kwargs)
        if argv in (["vm_stat"], ["sysctl", "vm.swapusage"],
                    ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]):
            return original_popen(argv, *args, **kwargs)
        violations.append("unexpected subprocess")
        raise AssertionError(violations[-1])

    def guarded_run(argv, *args, **kwargs):
        if argv == ["code", "/tmp"]:
            return subprocess.CompletedProcess(argv, 0)
        return original_run(argv, *args, **kwargs)

    def offline(*args, **kwargs):
        violations.append("unexpected network connection")
        raise AssertionError(violations[-1])

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(socket.socket, "connect", offline)
    yield
    assert not violations, violations
