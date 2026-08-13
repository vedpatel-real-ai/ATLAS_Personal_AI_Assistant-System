"""
System baseline snapshot for Milestone 5's "Benchmark Validation Report"
methodology (spec'd by the user directly — see IMPLEMENTATION_STATE.md's
session 3 log entry). Implements Phase 2 ("baseline system") of that
methodology: swap usage, compressed memory, memory pressure level, and
CPU load, captured before and after each model's block of trials so a
report can show what that specific model's run measurably changed.

macOS-only, and deliberately no sudo anywhere: every value here comes from
`sysctl`/`vm_stat` or Python's own `os.getloadavg()`, none of which need
elevated privileges. GPU usage and thermal state are NOT captured here —
the only real source for either on Apple Silicon is `powermetrics`, which
does need sudo, and every other tool in this codebase avoids privilege
escalation (spec's Engineering Principles #3, "prefer existing macOS
APIs", and #5, "every subsystem must justify its cost" — sudo is a cost).
If you want GPU/thermal signal, judge it by eye in Activity Monitor or
the menu-bar thermal/battery indicators alongside a benchmark run.

Session 4: added compressed memory (`vm_stat`'s "Pages occupied by
compressor") alongside swap. macOS compresses memory before it swaps, so
compressed-memory growth is a leading indicator of pressure that swap
alone can miss entirely on a run that doesn't push far enough to swap.
Session 4 also changed WHO calls capture_snapshot() and how often: a
single before/after pair spanning multiple models run back-to-back
(session 3's approach) can't attribute a swap/pressure change to either
model specifically — see benchmark_models.py's docstring for the fix
(one before/after pair per model instead of one per run).

Every function degrades to None on failure — non-macOS, a missing binary,
an unparseable output format on some future macOS version — rather than
raising. This is a real, tested constraint of this file, not a shortcut:
this review environment (Linux) and any CI can't run `sysctl`/`vm_stat` at
all, so these functions are exercised here only with subprocess mocked;
the None-degradation path is what makes that safe to ship without ever
having run it on real hardware from this environment. Session 3's
real-Mac run confirmed the `sysctl`-based functions (swap, pressure, CPU
load) parse correctly on real output; `get_compressed_memory_mb()` (new
this session) hasn't been confirmed on real hardware yet — verify it
against Activity Monitor's "Compressed" figure once before trusting it.
"""

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Optional


def _run(cmd: list) -> Optional[str]:
    """Runs a command and returns its stdout, or None on any failure
    (command not found, non-zero exit, timeout) — never raises."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def get_swap_used_mb() -> Optional[float]:
    """Parses `sysctl vm.swapusage`, e.g.:
    'vm.swapusage: total = 2048.00M  used = 512.00M  free = 1536.00M (encrypted)'
    Returns None if the command is unavailable or the output doesn't match
    the expected format (e.g. a future macOS version changes it — this
    should not be silently misread as zero swap)."""
    out = _run(["sysctl", "vm.swapusage"])
    if not out:
        return None
    match = re.search(r"used\s*=\s*([\d.]+)M", out)
    return float(match.group(1)) if match else None


# kern.memorystatus_vm_pressure_level: 1=normal, 2=warn, 4=critical (Apple's
# own scale, confirmed against kernel-internals references -- NOT 0-indexed.
# An earlier version of this file had {0: normal, 1: warn, 4: critical},
# off by one; a real level-2 reading came back as "unknown(2)" on the
# user's actual machine because of it. The unit tests for this function
# didn't catch it because they asserted against the same wrong assumption
# instead of Apple's real values -- fixed both here.
MEMORY_PRESSURE_LEVELS = {1: "normal", 2: "warn", 4: "critical"}


def get_memory_pressure_level() -> Optional[str]:
    """Reads kern.memorystatus_vm_pressure_level directly via sysctl — no
    sudo, and no dependency on the separate `memory_pressure` CLI tool.
    Returns 'normal'/'warn'/'critical', or None if unavailable."""
    out = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
    if not out:
        return None
    try:
        level = int(out.strip())
    except ValueError:
        return None
    return MEMORY_PRESSURE_LEVELS.get(level, f"unknown({level})")


def get_cpu_load_avg() -> Optional[tuple]:
    """1/5/15-minute load averages via os.getloadavg() — pure Python
    stdlib, no subprocess needed. Returns None on platforms without it
    (e.g. Windows; not a concern here, but this should degrade cleanly
    rather than raise if this script is ever run somewhere unexpected)."""
    try:
        return os.getloadavg()
    except (OSError, AttributeError):
        return None


def get_compressed_memory_mb() -> Optional[float]:
    """Parses `vm_stat`'s 'Pages occupied by compressor' line and its page
    size header ('Mach Virtual Memory Statistics: (page size of 16384
    bytes)') to get actual compressed-memory usage in MB — the same number
    Activity Monitor's memory tab shows as 'Compressed'. Distinct from
    swap: compression happens first, swapping only once compression isn't
    enough, so this is a leading indicator swap alone misses. Returns None
    if the command is unavailable or either line doesn't match the
    expected format."""
    out = _run(["vm_stat"])
    if not out:
        return None
    page_size_match = re.search(r"page size of (\d+) bytes", out)
    pages_match = re.search(r"Pages occupied by compressor:\s+(\d+)\.", out)
    if not page_size_match or not pages_match:
        return None
    page_size = int(page_size_match.group(1))
    pages = int(pages_match.group(1))
    return round(pages * page_size / (1024 * 1024), 1)


def capture_snapshot(label: str) -> dict:
    """One combined reading — swap, compressed memory, memory pressure,
    CPU load — tagged with a label for a benchmark run's Environment
    section. Missing values are recorded as None, never silently defaulted
    to zero — a None in a report means 'not measured', not 'measured as
    empty'.

    Session 4: labels changed from a single 'before'/'after' pair per run
    to 'before:<model>'/'after:<model>' per model (see benchmark_models.py)
    — a single before/after pair spanning multiple models back-to-back
    couldn't isolate which model caused a given swap/pressure change, only
    that something did during the whole run. This function itself doesn't
    care what the label means, it just tags whatever string it's given."""
    load = get_cpu_load_avg()
    return {
        "label": label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "swap_used_mb": get_swap_used_mb(),
        "compressed_memory_mb": get_compressed_memory_mb(),
        "memory_pressure": get_memory_pressure_level(),
        "cpu_load_1m": load[0] if load else None,
        "cpu_load_5m": load[1] if load else None,
        "cpu_load_15m": load[2] if load else None,
    }