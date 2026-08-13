"""
Tests for storage.py's `model_benchmarks` table (Milestone 5 — 9B model
evaluation): store_benchmark_result() and query_benchmark_summary().
Exercises the real SQLite round-trip, same convention as
test_storage_knowledge.py / test_storage_confirmations.py.

Session 2 note: this file originally exercised `ollama_peak_rss_mb`, which
real-hardware testing showed was reading the wrong process's memory (see
storage.py's module docstring). Rewritten to match the corrected
`ollama_size_mb`/`ollama_size_vram_mb` fields, sourced from Ollama's own
/api/ps in benchmark_models.py.

Session 3 additions: run_id-scoped queries, query_benchmark_raw_latencies
(for median/min/max/stdev in benchmark_models.py's report), and the new
benchmark_runs/system_snapshots tables (the "Benchmark Validation Report"
methodology).
"""

import storage


def test_store_benchmark_result_returns_an_id():
    row_id = storage.store_benchmark_result(
        model="unique-marker-model-a",
        prompt_label="short_factual",
        latency_s=1.23,
        success=True,
        eval_count=40,
        eval_tokens_per_s=32.5,
        ollama_size_mb=4200.0,
        ollama_size_vram_mb=4000.0,
    )
    assert isinstance(row_id, int)
    assert row_id > 0


def test_query_benchmark_summary_aggregates_per_model():
    storage.store_benchmark_result(
        model="unique-marker-model-b", prompt_label="p1", latency_s=1.0,
        success=True, eval_count=10, eval_tokens_per_s=10.0,
        ollama_size_mb=1000.0, ollama_size_vram_mb=900.0,
    )
    storage.store_benchmark_result(
        model="unique-marker-model-b", prompt_label="p2", latency_s=3.0,
        success=True, eval_count=30, eval_tokens_per_s=20.0,
        ollama_size_mb=1500.0, ollama_size_vram_mb=1400.0,
    )
    rows = storage.query_benchmark_summary(model="unique-marker-model-b")
    assert len(rows) == 1
    row = rows[0]
    assert row["n_trials"] == 2
    assert row["n_success"] == 2
    assert row["avg_latency_s"] == 2.0
    assert row["avg_tokens_per_s"] == 15.0
    assert row["max_size_mb"] == 1500.0
    assert row["max_size_vram_mb"] == 1400.0


def test_query_benchmark_summary_counts_failures_separately():
    storage.store_benchmark_result(
        model="unique-marker-model-c", prompt_label="p1", latency_s=0.5,
        success=False, error="connection refused",
    )
    rows = storage.query_benchmark_summary(model="unique-marker-model-c")
    assert len(rows) == 1
    assert rows[0]["n_trials"] == 1
    assert rows[0]["n_success"] == 0


def test_query_benchmark_summary_handles_missing_optional_fields():
    # eval_count / eval_tokens_per_s / ollama_size_mb / ollama_size_vram_mb
    # are all optional (e.g. a failed call, or the model never observed
    # loaded during the call — see benchmark_models.py) — must not crash
    # the aggregate query.
    row_id = storage.store_benchmark_result(
        model="unique-marker-model-d", prompt_label="p1", latency_s=2.0,
        success=True,
    )
    assert row_id > 0
    rows = storage.query_benchmark_summary(model="unique-marker-model-d")
    assert rows[0]["avg_tokens_per_s"] is None
    assert rows[0]["max_size_mb"] is None
    assert rows[0]["max_size_vram_mb"] is None


def test_query_benchmark_summary_without_model_filter_returns_all():
    storage.store_benchmark_result(
        model="unique-marker-model-e", prompt_label="p1", latency_s=1.0,
        success=True,
    )
    rows = storage.query_benchmark_summary()
    models_seen = {r["model"] for r in rows}
    assert "unique-marker-model-e" in models_seen


# ---------------------------------------------------------------------------
# Session 3: run_id scoping, raw latencies, benchmark_runs, system_snapshots
# ---------------------------------------------------------------------------

def test_store_benchmark_result_scoped_to_run_id():
    storage.store_benchmark_result(
        model="unique-marker-model-f", prompt_label="p1", latency_s=1.0,
        success=True, run_id="run-alpha",
    )
    storage.store_benchmark_result(
        model="unique-marker-model-f", prompt_label="p1", latency_s=9.0,
        success=True, run_id="run-beta",
    )
    alpha_rows = storage.query_benchmark_summary(model="unique-marker-model-f", run_id="run-alpha")
    assert len(alpha_rows) == 1
    assert alpha_rows[0]["n_trials"] == 1
    assert alpha_rows[0]["avg_latency_s"] == 1.0


def test_query_benchmark_raw_latencies_only_successful():
    storage.store_benchmark_result(
        model="unique-marker-model-g", prompt_label="p1", latency_s=2.0,
        success=True, run_id="run-gamma",
    )
    storage.store_benchmark_result(
        model="unique-marker-model-g", prompt_label="p2", latency_s=4.0,
        success=True, run_id="run-gamma",
    )
    storage.store_benchmark_result(
        model="unique-marker-model-g", prompt_label="p3", latency_s=0.1,
        success=False, error="boom", run_id="run-gamma",
    )
    latencies = storage.query_benchmark_raw_latencies("unique-marker-model-g", run_id="run-gamma")
    assert sorted(latencies) == [2.0, 4.0]


def test_store_and_query_benchmark_run():
    storage.store_benchmark_run(
        run_id="run-delta", started_at="2026-07-15T10:00:00+00:00",
        models=["qwen3:4b-instruct", "qwen3:8b"], keep_alive="0",
        think_mode="default", workload_note="Chrome + VS Code open",
    )
    run = storage.query_benchmark_run("run-delta")
    assert run["models"] == "qwen3:4b-instruct,qwen3:8b"
    assert run["keep_alive"] == "0"
    assert run["workload_note"] == "Chrome + VS Code open"
    assert run["responsiveness_rating"] is None


def test_query_benchmark_run_returns_none_for_unknown_run():
    assert storage.query_benchmark_run("no-such-run") is None


def test_update_benchmark_run_responsiveness():
    storage.store_benchmark_run(
        run_id="run-epsilon", started_at="2026-07-15T10:00:00+00:00",
        models=["qwen3:4b-instruct"], keep_alive="0",
    )
    storage.update_benchmark_run_responsiveness(
        "run-epsilon", rating=3, label="slight_lag", note="one minor stutter",
    )
    run = storage.query_benchmark_run("run-epsilon")
    assert run["responsiveness_rating"] == 3
    assert run["responsiveness_label"] == "slight_lag"
    assert run["responsiveness_note"] == "one minor stutter"


def test_store_and_query_system_snapshots_ordered_by_timestamp():
    storage.store_system_snapshot(
        run_id="run-zeta", label="before:qwen3:8b", timestamp="2026-07-15T10:00:00+00:00",
        swap_used_mb=100.0, compressed_memory_mb=200.0, memory_pressure="normal",
        cpu_load_1m=1.0, cpu_load_5m=1.2, cpu_load_15m=1.1,
    )
    storage.store_system_snapshot(
        run_id="run-zeta", label="after:qwen3:8b", timestamp="2026-07-15T10:05:00+00:00",
        swap_used_mb=150.0, compressed_memory_mb=250.0, memory_pressure="warn",
        cpu_load_1m=2.0, cpu_load_5m=1.8, cpu_load_15m=1.3,
    )
    snapshots = storage.query_system_snapshots("run-zeta")
    assert len(snapshots) == 2
    assert [s["label"] for s in snapshots] == ["before:qwen3:8b", "after:qwen3:8b"]
    assert snapshots[1]["swap_used_mb"] == 150.0
    assert snapshots[1]["compressed_memory_mb"] == 250.0


def test_query_system_snapshots_empty_for_unknown_run():
    assert storage.query_system_snapshots("no-such-run") == []