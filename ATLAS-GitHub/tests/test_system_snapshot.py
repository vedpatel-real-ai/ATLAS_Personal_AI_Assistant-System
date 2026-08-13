"""
Tests for system_snapshot.py. subprocess.run is mocked throughout — this
review/CI environment is Linux and has none of `sysctl`/`vm_stat`
available, and the whole point of this module's design (see its
docstring) is that it degrades to None cleanly when they're missing, so
that degradation path is exactly what's exercised here.
"""

from unittest.mock import MagicMock, patch

import system_snapshot


def _fake_run(stdout, returncode=0):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


def test_get_swap_used_mb_parses_sysctl_output():
    fake = _fake_run("vm.swapusage: total = 2048.00M  used = 512.00M  free = 1536.00M (encrypted)\n")
    with patch("system_snapshot.subprocess.run", return_value=fake):
        assert system_snapshot.get_swap_used_mb() == 512.00


def test_get_swap_used_mb_returns_none_on_command_not_found():
    with patch("system_snapshot.subprocess.run", side_effect=FileNotFoundError()):
        assert system_snapshot.get_swap_used_mb() is None


def test_get_swap_used_mb_returns_none_on_unparseable_output():
    fake = _fake_run("some unexpected future format\n")
    with patch("system_snapshot.subprocess.run", return_value=fake):
        assert system_snapshot.get_swap_used_mb() is None


def test_get_swap_used_mb_returns_none_on_nonzero_exit():
    fake = _fake_run("", returncode=1)
    with patch("system_snapshot.subprocess.run", return_value=fake):
        assert system_snapshot.get_swap_used_mb() is None


def test_get_memory_pressure_level_maps_known_levels():
    # Regression test: Apple's real scale is 1=normal, 2=warn, 4=critical
    # (confirmed via kernel-internals references, not assumed). An earlier
    # version of this test asserted 0/1/4 -> normal/warn/critical, which
    # baked in a wrong assumption instead of checking it -- a real level-2
    # reading on the user's actual machine came back "unknown(2)" as a
    # direct result. This test now checks the real values.
    for raw, expected in [("1\n", "normal"), ("2\n", "warn"), ("4\n", "critical")]:
        fake = _fake_run(raw)
        with patch("system_snapshot.subprocess.run", return_value=fake):
            assert system_snapshot.get_memory_pressure_level() == expected


def test_get_memory_pressure_level_handles_unknown_level():
    # 0 and 3 aren't real values on Apple's scale (1/2/4) -- must degrade
    # to a labeled "unknown" rather than crash or silently mislabel.
    fake = _fake_run("0\n")
    with patch("system_snapshot.subprocess.run", return_value=fake):
        assert system_snapshot.get_memory_pressure_level() == "unknown(0)"


def test_get_memory_pressure_level_returns_none_on_bad_output():
    fake = _fake_run("not-a-number\n")
    with patch("system_snapshot.subprocess.run", return_value=fake):
        assert system_snapshot.get_memory_pressure_level() is None


def test_get_cpu_load_avg_returns_none_when_unavailable():
    with patch("system_snapshot.os.getloadavg", side_effect=AttributeError()):
        assert system_snapshot.get_cpu_load_avg() is None


def test_get_cpu_load_avg_returns_tuple_when_available():
    with patch("system_snapshot.os.getloadavg", return_value=(1.5, 1.2, 1.0)):
        assert system_snapshot.get_cpu_load_avg() == (1.5, 1.2, 1.0)


VM_STAT_SAMPLE = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              123456.
Pages active:                            234567.
Pages inactive:                           12345.
Pages speculative:                         1234.
Pages throttled:                              0.
Pages wired down:                        123456.
Pages purgeable:                           1234.
"Translation faults":                123456789.
Pages copy-on-write:                    1234567.
Pages zero filled:                     12345678.
Pages reactivated:                       123456.
Pages purged:                             12345.
File-backed pages:                       123456.
Anonymous pages:                         123456.
Pages stored in compressor:              123456.
Pages occupied by compressor:             12345.
Decompressions:                         1234567.
Compressions:                           2345678.
Pageins:                               12345678.
Pageouts:                                     0.
Swapins:                                      0.
Swapouts:                                     0.
"""


def test_get_compressed_memory_mb_parses_vm_stat():
    fake = _fake_run(VM_STAT_SAMPLE)
    with patch("system_snapshot.subprocess.run", return_value=fake):
        result = system_snapshot.get_compressed_memory_mb()
    assert result == round(12345 * 16384 / (1024 * 1024), 1)


def test_get_compressed_memory_mb_returns_none_on_command_not_found():
    with patch("system_snapshot.subprocess.run", side_effect=FileNotFoundError()):
        assert system_snapshot.get_compressed_memory_mb() is None


def test_get_compressed_memory_mb_returns_none_on_unparseable_output():
    fake = _fake_run("some unexpected future format\n")
    with patch("system_snapshot.subprocess.run", return_value=fake):
        assert system_snapshot.get_compressed_memory_mb() is None


def test_get_compressed_memory_mb_returns_none_if_page_size_missing():
    fake = _fake_run("Pages occupied by compressor:             12345.\n")
    with patch("system_snapshot.subprocess.run", return_value=fake):
        assert system_snapshot.get_compressed_memory_mb() is None


def test_capture_snapshot_includes_label_and_all_fields():
    with patch("system_snapshot.get_swap_used_mb", return_value=512.0), \
         patch("system_snapshot.get_compressed_memory_mb", return_value=800.0), \
         patch("system_snapshot.get_memory_pressure_level", return_value="normal"), \
         patch("system_snapshot.get_cpu_load_avg", return_value=(1.0, 1.5, 2.0)):
        snap = system_snapshot.capture_snapshot("before")
    assert snap["label"] == "before"
    assert snap["swap_used_mb"] == 512.0
    assert snap["compressed_memory_mb"] == 800.0
    assert snap["memory_pressure"] == "normal"
    assert snap["cpu_load_1m"] == 1.0
    assert snap["cpu_load_5m"] == 1.5
    assert snap["cpu_load_15m"] == 2.0
    assert "timestamp" in snap


def test_capture_snapshot_handles_all_unavailable():
    with patch("system_snapshot.get_swap_used_mb", return_value=None), \
         patch("system_snapshot.get_compressed_memory_mb", return_value=None), \
         patch("system_snapshot.get_memory_pressure_level", return_value=None), \
         patch("system_snapshot.get_cpu_load_avg", return_value=None):
        snap = system_snapshot.capture_snapshot("after")
    assert snap["swap_used_mb"] is None
    assert snap["compressed_memory_mb"] is None
    assert snap["cpu_load_1m"] is None