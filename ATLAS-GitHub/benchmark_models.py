"""
Milestone 5 -- 9B model evaluation (spec S5 "Model Strategy").
"9B" was never a real Qwen3 tag (checked ollama.com/library/qwen3/tags --
sizes are 0.6b/1.7b/4b/8b/14b/30b/32b/235b); the real comparison this
project has been running is qwen3:4b-instruct vs qwen3:8b.

Standalone CLI script, NOT a chat tool registered in tools.py -- gets run
a handful of times total, not a recurring daily capability (spec S6's ROI
bar), and legitimately blocks on real model calls for minutes at a time.

===========================================================================
SESSION 4: Environment Health vs. Model Impact
===========================================================================
Session 3's report had a real methodological flaw, found by the user from
its own first real-hardware output, not from a test failure: swap and
memory pressure were captured ONCE before all models ran and ONCE after,
spanning both models back-to-back. That meant a swap/pressure change
couldn't be attributed to either model specifically -- and worse, if the
machine's ambient workload (Chrome, VS Code, Spotify) had already pushed
swap past the threshold before any model ran, EVERY candidate would fail
that criterion regardless of what it actually did, because the "after"
number was dominated by pre-existing load, not the model's own effect.

The fix, per the user's explicit design: split every environment-related
measurement into two different questions that get asked separately.

  ENVIRONMENT HEALTH (informational, does not gate a verdict): "was the
  baseline system state healthy enough to trust this benchmark at all?"
  Answered from the single snapshot taken before the first model in the
  run -- see environment_health(). If the machine was already under
  memory pressure before anything ran, that's flagged prominently in the
  report as a caveat on the whole run, not blamed on any model.

  MODEL IMPACT (the actual comparison): "what did THIS model's block of
  trials specifically change?" Answered from a before/after snapshot pair
  taken around EACH model's own trials (not once for the whole run) --
  see compute_model_impact(). Swap delta, compressed-memory delta, and
  memory-pressure-worsened are all THIS model's own before->after change,
  independent of whatever the ambient baseline already was.

This mirrors how the earlier GPU investigation this project referenced
was structured, and matches this project's Engineering Principles more
generally: separate the state of the system from the effect of the
change being tested, rather than conflating them into one number.

Also this session: `--rate` changed from a bare 1-5 integer (nobody could
remember what "4" meant) to a labeled checklist --
smooth / slight_lag / noticeable_lag / unusable -- matching the "small
amount of subjective feedback is appropriate" framing this was spec'd
with. Internally mapped to 4/3/2/1 for threshold comparison; both the
label and the number are stored (see storage.py).

===========================================================================
SESSION 3: "Benchmark Validation Report" methodology (context for the above)
===========================================================================
Restructured around a methodology the user spec'd directly, modeled on a
prior GPU investigation: hypothesis, verify the measurement tool before
trusting it, system baseline, realistic-workload trials with repeats,
explicit decision criteria, written report -- not just "model X is
better."

WHAT'S AUTOMATED vs. WHAT ISN'T, AND WHY (a real design fork, not a
default -- see IMPLEMENTATION_STATE.md's session-3 log for the reasoning):
  - Swap, compressed memory, memory pressure, CPU load (system_snapshot.py):
    automated, no sudo -- all readable via `sysctl`/`vm_stat`/
    `os.getloadavg()`.
  - GPU usage, thermal state: NOT automated -- the only real source on
    Apple Silicon is `powermetrics`, which needs sudo, and every other
    tool in this codebase avoids privilege escalation.
  - "Real-world workload" (Chrome, VS Code open): NOT automated -- a
    `--workload-note` free-text field for the record, not simulated load.
  - "UI responsiveness": NOT automated -- a human judgment call via
    `--rate`, recorded as a separate, later invocation after real use.

WHY THIS POLLS OLLAMA'S `/api/ps`, NOT A LOCAL PROCESS (session 2): the
first cut sampled `psutil` for a process named "ollama" and used its RSS
as a RAM proxy -- wrong, it read ~55-60MB regardless of actual model size,
because Ollama runs inference in a separate runner subprocess from the
main daemon. `/api/ps` is Ollama's own authoritative account of what's
loaded (`size`/`size_vram` in bytes), no OS-level guessing.

WHY latency here won't exactly match production latency: main.py's
run_big_model() calls Ollama with `"keep_alive": "0"` -- the model
unloads immediately after every response, so every real request pays a
full reload cost. This script mirrors that by default (see --keep-alive).

THINKING-MODE CONFOUND: some Qwen3 tags (e.g. `qwen3:8b`) default to
extended "thinking" output, others (`-instruct-2507` tags) don't.
`--think {default,true,false}` pins it explicitly to avoid conflating
thinking-mode latency with parameter-count latency.

Usage:
    # Run a benchmark (with Ollama running, both models pulled):
    python3 benchmark_models.py --models qwen3:4b-instruct qwen3:8b --think false

    # After using the candidate model for real work for a while, attach a
    # manual responsiveness checklist entry to that run:
    python3 benchmark_models.py --rate 20260718T082538Z-b461d8:slight_lag:"one pause on a long response"

Results accumulate in atlas.db (`model_benchmarks`, `benchmark_runs`,
`system_snapshots` -- see storage.py) across runs. --report-only RUN_ID
regenerates a report from stored data alone (e.g. after attaching --rate).
"""

import argparse
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import requests

import storage
import system_snapshot


DEFAULT_PROMPTS = [
    ("short_factual", "What's today's date? Answer in one short sentence."),
    ("tool_style", "The user said: 'open vscode'. Respond with what tool "
                    "call this maps to and why, in one sentence."),
    ("code_reasoning", "Explain in 2-3 sentences what a race condition is "
                        "and give one concrete way to avoid one in Python."),
    ("summarization", "Summarize the following in 2 sentences: 'The "
                       "Decision Engine formalizes escalation logic into "
                       "five explicit levels, L0 through L4, so the level "
                       "that handled a request can be logged and reported "
                       "on later. It does not change what the underlying "
                       "matcher-then-router-then-big-model chain actually "
                       "does; it only labels which step handled a given "
                       "request.'"),
    ("planning", "Break 'set up a new FastAPI project with tests' into "
                 "3-5 concrete ordered steps, one line each."),
]

MEMORY_PRESSURE_ORDER = {"normal": 0, "warn": 1, "critical": 2}

# Ordered low -> high; "slight_lag" being the default minimum bar reflects
# that a bit of lag is tolerable, "noticeable_lag" and worse aren't.
RESPONSIVENESS_LABELS = {"unusable": 1, "noticeable_lag": 2, "slight_lag": 3, "smooth": 4}

# Gates a candidate's verdict -- what did THIS model specifically change.
DEFAULT_THRESHOLDS = {
    "max_latency_increase_pct": 40.0,
    "max_swap_delta_mb": 200.0,
    "max_compressed_memory_delta_mb": 300.0,
    "allow_memory_pressure_worsening": False,
    "min_responsiveness_label": "slight_lag",
}

# Informational only -- "was this run's baseline healthy enough to trust,"
# never gates a model's verdict. See environment_health().
ENV_HEALTH_THRESHOLDS = {
    "max_baseline_swap_mb": 1024.0,
    "max_baseline_compressed_mb": 2048.0,
    "max_baseline_memory_pressure": "warn",
}

REPORTS_DIR = Path(__file__).parent / "benchmark_reports"


# ---------------------------------------------------------------------------
# Ollama /api/ps polling (session 2 fix)
# ---------------------------------------------------------------------------

def _ps_url_from_generate_url(generate_url: str) -> str:
    parts = urlsplit(generate_url)
    return urlunsplit((parts.scheme, parts.netloc, "/api/ps", "", ""))


def _sample_loaded_model_size(ps_url: str, model: str) -> Optional[dict]:
    try:
        resp = requests.get(ps_url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    for entry in data.get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            size = entry.get("size")
            size_vram = entry.get("size_vram")
            return {
                "size_mb": round(size / (1024 * 1024), 1) if size is not None else None,
                "size_vram_mb": round(size_vram / (1024 * 1024), 1) if size_vram is not None else None,
            }
    return None


class _PeakSizeSampler:
    def __init__(self, ps_url: str, model: str, interval_s: float = 0.2):
        self.ps_url = ps_url
        self.model = model
        self.interval_s = interval_s
        self.peak_size_mb = None
        self.peak_size_vram_mb = None
        self._stop = False
        self._thread = None

    def __enter__(self):
        import threading

        def _poll():
            while not self._stop:
                sample = _sample_loaded_model_size(self.ps_url, self.model)
                if sample:
                    if sample["size_mb"] is not None and (
                            self.peak_size_mb is None or sample["size_mb"] > self.peak_size_mb):
                        self.peak_size_mb = sample["size_mb"]
                    if sample["size_vram_mb"] is not None and (
                            self.peak_size_vram_mb is None or sample["size_vram_mb"] > self.peak_size_vram_mb):
                        self.peak_size_vram_mb = sample["size_vram_mb"]
                time.sleep(self.interval_s)

        self._thread = threading.Thread(target=_poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1)


def run_trial(model: str, prompt_label: str, prompt_text: str, url: str,
              keep_alive: str, timeout_s: int, think: Optional[bool] = None,
              options: Optional[dict] = None) -> dict:
    ps_url = _ps_url_from_generate_url(url)
    payload = {"model": model, "prompt": prompt_text, "stream": False, "keep_alive": keep_alive}
    if options is not None:
        payload["options"] = options
    if think is not None:
        payload["think"] = think

    with _PeakSizeSampler(ps_url, model) as sampler:
        start = time.perf_counter()
        try:
            resp = requests.post(url, json=payload, timeout=timeout_s)
            resp.raise_for_status()
            data = resp.json()
            if data.get("error") or data.get("done") is False:
                raise ValueError("Ollama did not finish generation")
        except Exception as e:
            latency_s = round(time.perf_counter() - start, 2)
            return {"model": model, "prompt_label": prompt_label,
                    "latency_s": latency_s, "success": False,
                    "error": str(e), "eval_count": None,
                    "eval_tokens_per_s": None,
                    "ollama_size_mb": sampler.peak_size_mb,
                    "ollama_size_vram_mb": sampler.peak_size_vram_mb}
        latency_s = round(time.perf_counter() - start, 2)

    eval_count = data.get("eval_count")
    eval_duration_ns = data.get("eval_duration")
    tokens_per_s = None
    if eval_count and eval_duration_ns:
        tokens_per_s = round(eval_count / (eval_duration_ns / 1e9), 1)

    return {"model": model, "prompt_label": prompt_label,
            "latency_s": latency_s, "success": True, "error": None,
            "eval_count": eval_count, "eval_tokens_per_s": tokens_per_s,
            "load_duration_ns": data.get("load_duration"), "eval_duration_ns": eval_duration_ns,
            "prompt_eval_count": data.get("prompt_eval_count"),
            "ollama_size_mb": sampler.peak_size_mb,
            "ollama_size_vram_mb": sampler.peak_size_vram_mb}


# ---------------------------------------------------------------------------
# Phase 4: repeated trials -> real statistics
# ---------------------------------------------------------------------------

def compute_model_stats(model: str, run_id: str) -> dict:
    latencies = storage.query_benchmark_raw_latencies(model, run_id=run_id)
    summary_rows = storage.query_benchmark_summary(model=model, run_id=run_id)
    summary = summary_rows[0] if summary_rows else {
        "n_trials": 0, "n_success": 0, "avg_tokens_per_s": None,
        "max_size_mb": None, "max_size_vram_mb": None,
    }
    return {
        "model": model,
        "n_trials": summary["n_trials"],
        "n_success": summary["n_success"],
        "mean_latency_s": round(statistics.mean(latencies), 2) if latencies else None,
        "median_latency_s": round(statistics.median(latencies), 2) if latencies else None,
        "min_latency_s": round(min(latencies), 2) if latencies else None,
        "max_latency_s": round(max(latencies), 2) if latencies else None,
        "stdev_latency_s": round(statistics.stdev(latencies), 2) if len(latencies) >= 2 else None,
        "avg_tokens_per_s": summary["avg_tokens_per_s"],
        "max_size_mb": summary["max_size_mb"],
        "max_size_vram_mb": summary["max_size_vram_mb"],
    }


# ---------------------------------------------------------------------------
# Session 4: Environment Health (baseline, informational) vs.
# Model Impact (per-model delta, gates the verdict)
# ---------------------------------------------------------------------------

def environment_health(run_id: str, first_model: str) -> dict:
    """Reads the snapshot taken before the FIRST model in the run (i.e.
    before anything this script did could have affected the machine) and
    checks it against ENV_HEALTH_THRESHOLDS. This never gates a model's
    verdict -- it answers 'was this run's baseline healthy enough to
    trust,' not 'did a model do something bad.' A loaded baseline (e.g.
    swap already high from Chrome/VS Code/Spotify) is flagged as a caveat
    on the whole run, not blamed on whichever model happened to run."""
    snapshots = {s["label"]: s for s in storage.query_system_snapshots(run_id)}
    baseline = snapshots.get(f"before:{first_model}")
    if baseline is None:
        return {"available": False, "concerns": [], "trustworthy": None}

    concerns = []
    swap = baseline.get("swap_used_mb")
    if swap is not None and swap > ENV_HEALTH_THRESHOLDS["max_baseline_swap_mb"]:
        concerns.append(f"Baseline swap ({swap}MB) already exceeded "
                         f"{ENV_HEALTH_THRESHOLDS['max_baseline_swap_mb']}MB before any model ran.")
    compressed = baseline.get("compressed_memory_mb")
    if compressed is not None and compressed > ENV_HEALTH_THRESHOLDS["max_baseline_compressed_mb"]:
        concerns.append(f"Baseline compressed memory ({compressed}MB) already exceeded "
                         f"{ENV_HEALTH_THRESHOLDS['max_baseline_compressed_mb']}MB before any model ran.")
    pressure = baseline.get("memory_pressure")
    max_pressure = ENV_HEALTH_THRESHOLDS["max_baseline_memory_pressure"]
    if pressure in MEMORY_PRESSURE_ORDER and MEMORY_PRESSURE_ORDER[pressure] >= MEMORY_PRESSURE_ORDER[max_pressure]:
        concerns.append(f"Baseline memory pressure was already '{pressure}' before any model ran.")

    return {
        "available": True,
        "baseline_swap_mb": swap,
        "baseline_compressed_mb": compressed,
        "baseline_memory_pressure": pressure,
        "concerns": concerns,
        "trustworthy": len(concerns) == 0,
    }


def compute_model_impact(run_id: str, model: str) -> dict:
    """What THIS model's own block of trials changed, isolated from
    whatever the ambient system was already doing -- the before/after
    snapshot pair is taken around this model specifically (see
    run_benchmark()), not shared with any other model in the run."""
    snapshots = {s["label"]: s for s in storage.query_system_snapshots(run_id)}
    before = snapshots.get(f"before:{model}")
    after = snapshots.get(f"after:{model}")

    if before is None or after is None:
        return {"swap_delta_mb": None, "compressed_memory_delta_mb": None,
                "memory_pressure_before": before.get("memory_pressure") if before else None,
                "memory_pressure_after": after.get("memory_pressure") if after else None,
                "memory_pressure_worsened": None}

    swap_delta = None
    if before.get("swap_used_mb") is not None and after.get("swap_used_mb") is not None:
        swap_delta = round(after["swap_used_mb"] - before["swap_used_mb"], 1)

    compressed_delta = None
    if before.get("compressed_memory_mb") is not None and after.get("compressed_memory_mb") is not None:
        compressed_delta = round(after["compressed_memory_mb"] - before["compressed_memory_mb"], 1)

    pressure_before = before.get("memory_pressure")
    pressure_after = after.get("memory_pressure")
    worsened = None
    if pressure_before in MEMORY_PRESSURE_ORDER and pressure_after in MEMORY_PRESSURE_ORDER:
        worsened = MEMORY_PRESSURE_ORDER[pressure_after] > MEMORY_PRESSURE_ORDER[pressure_before]

    return {"swap_delta_mb": swap_delta, "compressed_memory_delta_mb": compressed_delta,
            "memory_pressure_before": pressure_before, "memory_pressure_after": pressure_after,
            "memory_pressure_worsened": worsened}


# ---------------------------------------------------------------------------
# Phase 6: explicit decision criteria, all delta/comparison-based -- none
# of them depend on the ambient baseline state (see environment_health()
# for that, kept separate on purpose).
# ---------------------------------------------------------------------------

def evaluate_thresholds(run_id: str, baseline_model: str, candidate_model: str,
                         thresholds: dict) -> dict:
    """Compares `candidate_model` against `baseline_model` within one run.
    Each criterion is 'pass', 'fail', or 'not_evaluated' (missing data is
    never silently treated as a pass or a fail). Verdict is 'switch' only
    if every evaluated criterion passes AND the four core ones (latency,
    swap delta, compressed-memory delta, memory-pressure-worsened) were
    all evaluated; 'stay' if anything evaluated failed; 'inconclusive' if
    core data's missing."""
    baseline_stats = compute_model_stats(baseline_model, run_id)
    candidate_stats = compute_model_stats(candidate_model, run_id)
    candidate_impact = compute_model_impact(run_id, candidate_model)
    run = storage.query_benchmark_run(run_id) or {}

    criteria = {}

    baseline_mean = baseline_stats["mean_latency_s"]
    candidate_mean = candidate_stats["mean_latency_s"]
    # Explicit is-not-None + >0, not truthiness -- a genuine 0.0s mean is
    # falsy in Python and would otherwise be silently misread as "missing
    # data" (session 3 bug, found via an end-to-end smoke test).
    if baseline_mean is not None and baseline_mean > 0 and candidate_mean is not None:
        increase_pct = ((candidate_mean - baseline_mean) / baseline_mean) * 100
        criteria["latency_increase_pct"] = {
            "value": round(increase_pct, 1), "threshold": thresholds["max_latency_increase_pct"],
            "status": "pass" if increase_pct <= thresholds["max_latency_increase_pct"] else "fail",
        }
    else:
        criteria["latency_increase_pct"] = {"value": None, "threshold": thresholds["max_latency_increase_pct"],
                                             "status": "not_evaluated"}

    if candidate_impact["swap_delta_mb"] is not None:
        delta = candidate_impact["swap_delta_mb"]
        criteria["swap_delta_mb"] = {
            "value": delta, "threshold": thresholds["max_swap_delta_mb"],
            "status": "pass" if delta <= thresholds["max_swap_delta_mb"] else "fail",
        }
    else:
        criteria["swap_delta_mb"] = {"value": None, "threshold": thresholds["max_swap_delta_mb"],
                                      "status": "not_evaluated"}

    if candidate_impact["compressed_memory_delta_mb"] is not None:
        delta = candidate_impact["compressed_memory_delta_mb"]
        criteria["compressed_memory_delta_mb"] = {
            "value": delta, "threshold": thresholds["max_compressed_memory_delta_mb"],
            "status": "pass" if delta <= thresholds["max_compressed_memory_delta_mb"] else "fail",
        }
    else:
        criteria["compressed_memory_delta_mb"] = {"value": None,
                                                    "threshold": thresholds["max_compressed_memory_delta_mb"],
                                                    "status": "not_evaluated"}

    worsened = candidate_impact["memory_pressure_worsened"]
    allow_worsening = thresholds["allow_memory_pressure_worsening"]
    if worsened is not None:
        criteria["memory_pressure_worsened"] = {
            "value": worsened, "threshold": f"worsening allowed: {allow_worsening}",
            "status": "pass" if (not worsened or allow_worsening) else "fail",
        }
    else:
        criteria["memory_pressure_worsened"] = {"value": None,
                                                  "threshold": f"worsening allowed: {allow_worsening}",
                                                  "status": "not_evaluated"}

    rating = run.get("responsiveness_rating")
    min_label = thresholds["min_responsiveness_label"]
    min_rating = RESPONSIVENESS_LABELS[min_label]
    if rating is not None:
        criteria["responsiveness_rating"] = {
            "value": run.get("responsiveness_label"), "threshold": min_label,
            "status": "pass" if rating >= min_rating else "fail",
        }
    else:
        criteria["responsiveness_rating"] = {"value": None, "threshold": min_label, "status": "not_evaluated"}

    core = ["latency_increase_pct", "swap_delta_mb", "compressed_memory_delta_mb", "memory_pressure_worsened"]
    core_evaluated = all(criteria[c]["status"] != "not_evaluated" for c in core)
    any_fail = any(c["status"] == "fail" for c in criteria.values())

    if any_fail:
        verdict = "stay"
    elif core_evaluated:
        verdict = "switch"
    else:
        verdict = "inconclusive"

    return {"baseline_model": baseline_model, "candidate_model": candidate_model,
            "criteria": criteria, "verdict": verdict}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_benchmark(run_id: str, models: list, prompts: list, url: str, keep_alive: str,
                   repeats: int, timeout_s: int, think: Optional[bool]) -> None:
    """One before/after system_snapshot pair PER MODEL (session 4), not
    one pair for the whole run -- see module docstring for why a single
    pair spanning multiple models couldn't attribute a change to either
    one."""
    total = len(models) * len(prompts) * repeats
    done = 0
    for model in models:
        before = system_snapshot.capture_snapshot(f"before:{model}")
        storage.store_system_snapshot(run_id=run_id, **before)

        for label, text in prompts:
            for _ in range(repeats):
                result = run_trial(model, label, text, url, keep_alive, timeout_s, think=think)
                storage.store_benchmark_result(
                    model=result["model"], prompt_label=result["prompt_label"],
                    latency_s=result["latency_s"], success=result["success"],
                    eval_count=result["eval_count"],
                    eval_tokens_per_s=result["eval_tokens_per_s"],
                    ollama_size_mb=result["ollama_size_mb"],
                    ollama_size_vram_mb=result["ollama_size_vram_mb"],
                    error=result["error"], run_id=run_id,
                )
                done += 1
                status = "ok" if result["success"] else f"FAILED ({result['error']})"
                size_note = (f"size={result['ollama_size_mb']}MB"
                             if result["ollama_size_mb"] is not None
                             else "size=n/a (not observed loaded during this call)")
                print(f"[{done}/{total}] {model} / {label}: "
                      f"{result['latency_s']}s, {size_note} -- {status}")

        after = system_snapshot.capture_snapshot(f"after:{model}")
        storage.store_system_snapshot(run_id=run_id, **after)


def print_summary(run_id: str, models: list) -> None:
    print(f"\n--- Summary for run {run_id} ---")
    header = (f"{'model':<25} {'n':>4} {'mean_s':>8} {'median_s':>9} "
              f"{'min_s':>7} {'max_s':>7} {'stdev_s':>8} {'tok/s':>7} {'size_mb':>8} {'vram_mb':>8}")
    print(header)
    print("-" * len(header))
    for model in models:
        s = compute_model_stats(model, run_id)

        def fmt(v, nd=2):
            return f"{v:.{nd}f}" if v is not None else "n/a"

        print(f"{model:<25} {s['n_success']:>4} {fmt(s['mean_latency_s']):>8} "
              f"{fmt(s['median_latency_s']):>9} {fmt(s['min_latency_s']):>7} "
              f"{fmt(s['max_latency_s']):>7} {fmt(s['stdev_latency_s']):>8} "
              f"{fmt(s['avg_tokens_per_s'], 1):>7} {fmt(s['max_size_mb'], 0):>8} "
              f"{fmt(s['max_size_vram_mb'], 0):>8}")

    health = environment_health(run_id, models[0])
    if health["available"] and health["concerns"]:
        print("\n[environment health] Baseline concerns (not a mark against any model):")
        for c in health["concerns"]:
            print(f"  - {c}")


def generate_report(run_id: str, models: list, thresholds: dict) -> Path:
    run = storage.query_benchmark_run(run_id) or {}
    baseline_model = models[0]
    candidates = models[1:]

    def fmt(v, nd=2):
        return f"{v:.{nd}f}" if isinstance(v, (int, float)) else ("n/a" if v is None else str(v))

    lines = [f"# Benchmark Validation Report -- {run_id}", ""]

    lines += ["## Hypothesis", "",
              f"A candidate model can replace `{baseline_model}` as `CODER_MODEL` "
              f"(main.py) only if its own before/after impact doesn't cost more than "
              f"the thresholds below in latency, swap, compressed memory, and memory "
              f"pressure, and (if rated) perceived responsiveness while working.", ""]

    lines += ["## Method", "",
              f"- Models compared: {', '.join(models)} (baseline: `{baseline_model}`)",
              f"- Prompts: {len(DEFAULT_PROMPTS)} fixed prompts spanning L0-L4 request shapes",
              f"- keep_alive: `{run.get('keep_alive', 'unknown')}` (matches main.py's real production setting)",
              f"- think mode: `{run.get('think_mode', 'default')}`",
              f"- Model size/VRAM measured via Ollama's own `/api/ps`",
              f"- System snapshots taken before/after EACH model's own block of trials, "
              f"not once for the whole run -- see this script's docstring (session 4)", ""]

    lines += ["## Environment Health (baseline -- informational, does not gate a verdict)", ""]
    if run.get("workload_note"):
        lines.append(f"- Workload during run: {run['workload_note']}")
    else:
        lines.append("- Workload during run: not recorded (see --workload-note)")
    health = environment_health(run_id, baseline_model)
    if health["available"]:
        lines.append(f"- Baseline swap: {fmt(health['baseline_swap_mb'])} MB")
        lines.append(f"- Baseline compressed memory: {fmt(health['baseline_compressed_mb'])} MB")
        lines.append(f"- Baseline memory pressure: {health['baseline_memory_pressure']}")
        if health["concerns"]:
            lines.append("")
            lines.append("**\u26a0\ufe0f Caveats on this run's baseline (not a mark against any model):**")
            for c in health["concerns"]:
                lines.append(f"- {c}")
        else:
            lines.append("- No baseline concerns -- this run's numbers should be trustworthy.")
    else:
        lines.append("- No baseline snapshot available for this run.")
    lines.append("")

    lines += ["## Measurements (absolute, per model)", "",
              "| model | n | mean_s | median_s | min_s | max_s | stdev_s | tok/s | size_mb | vram_mb |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for model in models:
        s = compute_model_stats(model, run_id)
        lines.append(f"| {model} | {s['n_success']} | {fmt(s['mean_latency_s'])} | "
                     f"{fmt(s['median_latency_s'])} | {fmt(s['min_latency_s'])} | "
                     f"{fmt(s['max_latency_s'])} | {fmt(s['stdev_latency_s'])} | "
                     f"{fmt(s['avg_tokens_per_s'], 1)} | {fmt(s['max_size_mb'], 0)} | "
                     f"{fmt(s['max_size_vram_mb'], 0)} |")
    lines.append("")

    lines += ["## Model Impact (delta -- what each model's OWN before/after changed)", "",
              "| model | swap_delta_mb | compressed_delta_mb | pressure_before | pressure_after | worsened |",
              "|---|---|---|---|---|---|"]
    for model in models:
        impact = compute_model_impact(run_id, model)
        lines.append(f"| {model} | {fmt(impact['swap_delta_mb'])} | "
                     f"{fmt(impact['compressed_memory_delta_mb'])} | "
                     f"{impact['memory_pressure_before']} | {impact['memory_pressure_after']} | "
                     f"{impact['memory_pressure_worsened']} |")
    lines.append("")

    lines += ["## Results", ""]
    verdicts = []
    for candidate in candidates:
        result = evaluate_thresholds(run_id, baseline_model, candidate, thresholds)
        verdicts.append(result)
        lines.append(f"### {candidate} vs. {baseline_model}")
        lines.append("")
        lines.append("| criterion | value | threshold | status |")
        lines.append("|---|---|---|---|")
        for name, c in result["criteria"].items():
            lines.append(f"| {name} | {c['value']} | {c['threshold']} | {c['status']} |")
        lines.append("")
        lines.append(f"**Verdict: {result['verdict']}**")
        lines.append("")

    lines += ["## Limitations", "",
              "- GPU usage and thermal state are not measured (would need "
              "`powermetrics`, which needs sudo). Judge these by eye in Activity "
              "Monitor during a run if they matter to you.",
              "- Responsiveness is a manual checklist entry attached via `--rate` "
              "after real use, not an automated metric -- `not_evaluated` above means "
              "no rating had been recorded yet when this report was generated.",
              "- Single machine, single session per run -- repeated real trials on "
              "your actual hardware, not a statistically powered study.",
              f"- {len(DEFAULT_PROMPTS)} fixed prompts, representative of this "
              "assistant's real request shape, not an exhaustive sample.",
              "- Environment Health above is informational only and never gates a "
              "verdict -- see this script's docstring for why (session 4).", ""]

    lines += ["## Conclusion", ""]
    if health["available"] and health["concerns"]:
        lines.append("- This run's baseline had concerns (see Environment Health above) -- "
                     "the Model Impact deltas below are still meaningful (they isolate each "
                     "model's own effect), but be aware the machine wasn't in a clean state "
                     "when this ran.")
    for result in verdicts:
        verdict_text = {
            "switch": f"Switching to `{result['candidate_model']}` is supported by "
                      f"its own measured impact against the evaluated thresholds.",
            "stay": f"Staying on `{result['baseline_model']}` is recommended -- "
                    f"`{result['candidate_model']}` failed at least one evaluated threshold.",
            "inconclusive": f"Inconclusive for `{result['candidate_model']}` -- "
                            f"one or more core criteria weren't evaluated, most likely "
                            f"because this ran on a non-macOS machine or a required "
                            f"command wasn't found.",
        }[result["verdict"]]
        lines.append(f"- {verdict_text}")
    lines.append("")

    lines += ["## Recommendation", "",
              "This report doesn't auto-apply anything -- `CODER_MODEL` in main.py "
              "is unchanged by running this script. If a verdict above says "
              "'switch', that's a recommendation to make the change yourself.", ""]

    lines += ["## Future work", "",
              "- If GPU/thermal signal ever becomes worth the sudo cost, "
              "`powermetrics --samplers gpu_power,thermal -i 1000 -n 30` is the "
              "relevant command -- not wired in here on purpose.",
              "- More repeats (`--repeats`) narrow the stdev if a verdict is close "
              "to a threshold.",
              "- Re-run with `--rate` after a longer real work session for a more "
              "trustworthy responsiveness signal than a short benchmark window can give.", ""]

    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{run_id}.md"
    report_path.write_text("\n".join(lines))
    return report_path


def print_raw_snapshot() -> None:
    """Prints one system_snapshot.capture_snapshot() reading alongside the
    raw command each figure comes from, so it can be checked against
    Activity Monitor / a manual `vm_stat` run in one command instead of
    two separate tools. Session 6: added specifically because
    compressed_memory_mb has never been independently cross-checked
    against Activity Monitor's own 'Compressed' figure -- every real run
    so far has produced physically plausible numbers, which is supportive
    but not the same as a direct side-by-side check. Doesn't touch
    storage or run any Ollama calls; purely a measurement sanity check."""
    snap = system_snapshot.capture_snapshot("manual-check")

    def fmt(v):
        return "n/a (command unavailable or unparseable)" if v is None else v

    print("Snapshot (compare against Activity Monitor's Memory tab, or run the raw commands yourself):")
    print(f"  swap_used_mb:         {fmt(snap['swap_used_mb'])}   <- `sysctl vm.swapusage`")
    print(f"  compressed_memory_mb: {fmt(snap['compressed_memory_mb'])}   <- `vm_stat` ('Pages occupied by compressor')")
    print(f"  memory_pressure:      {fmt(snap['memory_pressure'])}   <- `sysctl kern.memorystatus_vm_pressure_level`")
    print(f"  cpu_load (1/5/15m):   {fmt(snap['cpu_load_1m'])}, {fmt(snap['cpu_load_5m'])}, {fmt(snap['cpu_load_15m'])}")
    print()
    print("To verify compressed_memory_mb by hand:")
    print("  vm_stat | grep -E 'page size|occupied by compressor'")
    print("  Then compare against Activity Monitor > (this Mac's icon in the Dock or Applications/Utilities)")
    print("  > Memory tab > 'Compressed' figure at the bottom of the window.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", nargs="+",
                         help="Ollama model tags to compare, first is the baseline, "
                              "e.g. qwen3:4b-instruct qwen3:8b")
    parser.add_argument("--url", default="http://localhost:11434/api/generate")
    parser.add_argument("--keep-alive", default="0",
                         help="Ollama keep_alive setting. Defaults to '0' to match "
                              "main.py's real production setting.")
    parser.add_argument("--think", choices=["default", "true", "false"], default="default",
                         help="Pin Ollama's 'think' field explicitly. 'default' sends "
                              "no think field at all (the tag's own default).")
    parser.add_argument("--repeats", type=int, default=3,
                         help="How many times to repeat each prompt per model (default 3).")
    parser.add_argument("--timeout", type=int, default=180,
                         help="Per-call timeout in seconds (default 180).")
    parser.add_argument("--workload-note", default=None,
                         help="Free-text note on what else was running during the benchmark.")
    parser.add_argument("--max-latency-increase-pct", type=float,
                         default=DEFAULT_THRESHOLDS["max_latency_increase_pct"])
    parser.add_argument("--max-swap-delta-mb", type=float,
                         default=DEFAULT_THRESHOLDS["max_swap_delta_mb"],
                         help="Max allowed swap INCREASE caused by this model's own "
                              "before/after block, not an absolute swap ceiling.")
    parser.add_argument("--max-compressed-memory-delta-mb", type=float,
                         default=DEFAULT_THRESHOLDS["max_compressed_memory_delta_mb"])
    parser.add_argument("--allow-memory-pressure-worsening", action="store_true",
                         help="By default, a model's own block worsening memory pressure "
                              "level (e.g. normal->warn) fails that criterion. Pass this "
                              "to allow it.")
    parser.add_argument("--min-responsiveness-label", choices=list(RESPONSIVENESS_LABELS),
                         default=DEFAULT_THRESHOLDS["min_responsiveness_label"])
    parser.add_argument("--report-only", metavar="RUN_ID", default=None,
                         help="Skip running trials; regenerate the report for a previous run_id.")
    parser.add_argument("--rate", metavar="RUN_ID:LABEL[:NOTE]", default=None,
                         help="Attach a manual responsiveness checklist entry to a previous "
                              f"run after real use. LABEL is one of {list(RESPONSIVENESS_LABELS)}. "
                              "Skips everything else.")
    parser.add_argument("--snapshot-only", action="store_true",
                         help="Print one system snapshot (swap, compressed memory, memory "
                              "pressure, CPU load) and exit -- no trials, no Ollama calls, "
                              "nothing stored. For sanity-checking system_snapshot.py's "
                              "measurements against Activity Monitor/vm_stat directly.")
    args = parser.parse_args()

    if args.snapshot_only:
        print_raw_snapshot()
        return 0

    storage.init_db()

    if args.rate:
        parts = args.rate.split(":", 2)
        if len(parts) < 2:
            print("ERROR: --rate must be RUN_ID:LABEL[:NOTE]")
            return 1
        run_id, label = parts[0], parts[1]
        note = parts[2] if len(parts) == 3 else None
        if label not in RESPONSIVENESS_LABELS:
            print(f"ERROR: label must be one of {list(RESPONSIVENESS_LABELS)}, got {label!r}")
            return 1
        if storage.query_benchmark_run(run_id) is None:
            print(f"ERROR: no such run_id {run_id!r} in atlas.db")
            return 1
        rating = RESPONSIVENESS_LABELS[label]
        storage.update_benchmark_run_responsiveness(run_id, rating=rating, label=label, note=note)
        print(f"Recorded responsiveness '{label}' ({rating}/4) for run {run_id}.")
        return 0

    thresholds = {
        "max_latency_increase_pct": args.max_latency_increase_pct,
        "max_swap_delta_mb": args.max_swap_delta_mb,
        "max_compressed_memory_delta_mb": args.max_compressed_memory_delta_mb,
        "allow_memory_pressure_worsening": args.allow_memory_pressure_worsening,
        "min_responsiveness_label": args.min_responsiveness_label,
    }

    if args.report_only:
        run = storage.query_benchmark_run(args.report_only)
        if run is None:
            print(f"ERROR: no such run_id {args.report_only!r} in atlas.db")
            return 1
        models = run["models"].split(",")
        path = generate_report(args.report_only, models, thresholds)
        print_summary(args.report_only, models)
        print(f"\nReport written to {path}")
        return 0

    if not args.models:
        parser.error("--models is required unless using --report-only or --rate")

    think = {"default": None, "true": True, "false": False}[args.think]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started_at = datetime.now(timezone.utc).isoformat()

    storage.store_benchmark_run(run_id=run_id, started_at=started_at, models=args.models,
                                 keep_alive=args.keep_alive, think_mode=args.think,
                                 workload_note=args.workload_note)

    print(f"Run ID: {run_id}  (use this with --rate after real-world use, or --report-only to regenerate the report)")

    run_benchmark(run_id, args.models, DEFAULT_PROMPTS, args.url, args.keep_alive,
                  args.repeats, args.timeout, think)

    print_summary(run_id, args.models)
    path = generate_report(run_id, args.models, thresholds)
    print(f"\nReport written to {path}")
    print(f"Run ID: {run_id}")


if __name__ == "__main__":
    sys.exit(main())