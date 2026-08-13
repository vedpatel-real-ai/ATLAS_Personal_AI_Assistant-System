"""
Tests for benchmark_models.py (Milestone 5 — 9B model evaluation). No real
Ollama dependency: requests.post (the /api/generate call) and requests.get
(the /api/ps poll) are both mocked — same convention as
test_summarize_document.py mocking the reasoning-model call.

Session 2 note: the first cut of this test file mocked psutil.process_iter,
matching benchmark_models.py's first (verified-wrong-on-real-hardware) RSS
sampler. That sampler was replaced with one that polls Ollama's own
/api/ps endpoint — see storage.py's and benchmark_models.py's docstrings
for why. This file was rewritten to match.

Session 3 additions: compute_model_stats, evaluate_thresholds,
generate_report, and the --rate/--report-only CLI paths (the "Benchmark
Validation Report" methodology). These use real storage.py round-trips
(same convention as test_storage_benchmarks.py) rather than mocking
storage, since the whole point is verifying the aggregation math and
report content against real stored rows.
"""

import sys
from unittest.mock import MagicMock, patch

import benchmark_models
import storage


def _fake_generate_response(json_data, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_data
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = Exception("boom")
    return resp


def _fake_ps_response(models):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"models": models}
    return resp


def test_ps_url_derived_from_generate_url():
    assert (
        benchmark_models._ps_url_from_generate_url(
            "http://localhost:11434/api/generate"
        )
        == "http://localhost:11434/api/ps"
    )


def test_sample_loaded_model_size_finds_matching_model():
    fake = _fake_ps_response(
        [
            {"name": "qwen3:8b", "size": 5_200_000_000, "size_vram": 5_000_000_000},
        ]
    )
    with patch("benchmark_models.requests.get", return_value=fake):
        result = benchmark_models._sample_loaded_model_size(
            "http://fake/api/ps", "qwen3:8b"
        )
    assert result["size_mb"] == round(5_200_000_000 / (1024 * 1024), 1)
    assert result["size_vram_mb"] == round(5_000_000_000 / (1024 * 1024), 1)


def test_sample_loaded_model_size_returns_none_when_not_loaded():
    fake = _fake_ps_response([])
    with patch("benchmark_models.requests.get", return_value=fake):
        result = benchmark_models._sample_loaded_model_size(
            "http://fake/api/ps", "qwen3:8b"
        )
    assert result is None


def test_sample_loaded_model_size_ignores_other_loaded_models():
    fake = _fake_ps_response([{"name": "qwen3:4b-instruct", "size": 2_500_000_000}])
    with patch("benchmark_models.requests.get", return_value=fake):
        result = benchmark_models._sample_loaded_model_size(
            "http://fake/api/ps", "qwen3:8b"
        )
    assert result is None


def test_sample_loaded_model_size_returns_none_on_request_failure():
    with patch(
        "benchmark_models.requests.get", side_effect=Exception("connection refused")
    ):
        result = benchmark_models._sample_loaded_model_size(
            "http://fake/api/ps", "qwen3:8b"
        )
    assert result is None


def test_run_trial_success_computes_tokens_per_second():
    fake_gen = _fake_generate_response(
        {"response": "hi", "eval_count": 100, "eval_duration": 2_000_000_000}
    )  # 2s in ns
    fake_ps = _fake_ps_response(
        []
    )  # no observed size this trial — still a valid outcome
    with patch("benchmark_models.requests.post", return_value=fake_gen), patch(
        "benchmark_models.requests.get", return_value=fake_ps
    ):
        result = benchmark_models.run_trial(
            model="test-model",
            prompt_label="short_factual",
            prompt_text="hello",
            url="http://fake/api/generate",
            keep_alive="0",
            timeout_s=10,
        )
    assert result["success"] is True
    assert result["eval_count"] == 100
    assert result["eval_tokens_per_s"] == 50.0
    assert result["error"] is None
    assert result["ollama_size_mb"] is None  # sampler never saw it loaded


def test_run_trial_records_observed_size():
    fake_gen = _fake_generate_response(
        {"response": "hi", "eval_count": 1, "eval_duration": 1}
    )
    fake_ps = _fake_ps_response(
        [{"name": "test-model", "size": 1_000_000, "size_vram": 900_000}]
    )
    with patch("benchmark_models.requests.post", return_value=fake_gen), patch(
        "benchmark_models.requests.get", return_value=fake_ps
    ):
        result = benchmark_models.run_trial(
            model="test-model",
            prompt_label="p",
            prompt_text="hello",
            url="http://fake/api/generate",
            keep_alive="0",
            timeout_s=10,
        )
    assert result["ollama_size_mb"] == round(1_000_000 / (1024 * 1024), 1)
    assert result["ollama_size_vram_mb"] == round(900_000 / (1024 * 1024), 1)


def test_run_trial_handles_missing_eval_fields_gracefully():
    fake_gen = _fake_generate_response(
        {"response": "hi"}
    )  # no eval_count/eval_duration
    fake_ps = _fake_ps_response([])
    with patch("benchmark_models.requests.post", return_value=fake_gen), patch(
        "benchmark_models.requests.get", return_value=fake_ps
    ):
        result = benchmark_models.run_trial(
            model="test-model",
            prompt_label="short_factual",
            prompt_text="hello",
            url="http://fake/api/generate",
            keep_alive="0",
            timeout_s=10,
        )
    assert result["success"] is True
    assert result["eval_tokens_per_s"] is None


def test_run_trial_records_failure_without_raising():
    fake_ps = _fake_ps_response([])
    with patch(
        "benchmark_models.requests.post", side_effect=Exception("connection refused")
    ), patch("benchmark_models.requests.get", return_value=fake_ps):
        result = benchmark_models.run_trial(
            model="test-model",
            prompt_label="short_factual",
            prompt_text="hello",
            url="http://fake/api/generate",
            keep_alive="0",
            timeout_s=10,
        )
    assert result["success"] is False
    assert "connection refused" in result["error"]
    assert result["eval_tokens_per_s"] is None


def test_run_trial_sends_configured_keep_alive():
    fake_gen = _fake_generate_response(
        {"response": "hi", "eval_count": 1, "eval_duration": 1}
    )
    fake_ps = _fake_ps_response([])
    with patch(
        "benchmark_models.requests.post", return_value=fake_gen
    ) as mock_post, patch("benchmark_models.requests.get", return_value=fake_ps):
        benchmark_models.run_trial(
            model="test-model",
            prompt_label="p",
            prompt_text="x",
            url="http://fake/api/generate",
            keep_alive="5m",
            timeout_s=10,
        )
    sent_json = mock_post.call_args.kwargs["json"]
    assert sent_json["keep_alive"] == "5m"
    assert sent_json["model"] == "test-model"


def test_run_trial_sends_think_field_when_pinned():
    fake_gen = _fake_generate_response(
        {"response": "hi", "eval_count": 1, "eval_duration": 1}
    )
    fake_ps = _fake_ps_response([])
    with patch(
        "benchmark_models.requests.post", return_value=fake_gen
    ) as mock_post, patch("benchmark_models.requests.get", return_value=fake_ps):
        benchmark_models.run_trial(
            model="test-model",
            prompt_label="p",
            prompt_text="x",
            url="http://fake/api/generate",
            keep_alive="0",
            timeout_s=10,
            think=False,
        )
    assert mock_post.call_args.kwargs["json"]["think"] is False


def test_run_trial_omits_think_field_by_default():
    fake_gen = _fake_generate_response(
        {"response": "hi", "eval_count": 1, "eval_duration": 1}
    )
    fake_ps = _fake_ps_response([])
    with patch(
        "benchmark_models.requests.post", return_value=fake_gen
    ) as mock_post, patch("benchmark_models.requests.get", return_value=fake_ps):
        benchmark_models.run_trial(
            model="test-model",
            prompt_label="p",
            prompt_text="x",
            url="http://fake/api/generate",
            keep_alive="0",
            timeout_s=10,
        )
    assert "think" not in mock_post.call_args.kwargs["json"]


# ---------------------------------------------------------------------------
# compute_model_stats — real storage round-trip
# ---------------------------------------------------------------------------


def test_compute_model_stats_computes_real_statistics():
    run_id = "test-run-stats-1"
    for latency in [2.0, 4.0, 6.0]:
        storage.store_benchmark_result(
            model="stats-model-a",
            prompt_label="p",
            latency_s=latency,
            success=True,
            eval_tokens_per_s=10.0,
            run_id=run_id,
        )
    stats = benchmark_models.compute_model_stats("stats-model-a", run_id)
    assert stats["n_success"] == 3
    assert stats["mean_latency_s"] == 4.0
    assert stats["median_latency_s"] == 4.0
    assert stats["min_latency_s"] == 2.0
    assert stats["max_latency_s"] == 6.0
    assert stats["stdev_latency_s"] is not None


def test_compute_model_stats_stdev_none_with_single_trial():
    run_id = "test-run-stats-2"
    storage.store_benchmark_result(
        model="stats-model-b",
        prompt_label="p",
        latency_s=3.0,
        success=True,
        run_id=run_id,
    )
    stats = benchmark_models.compute_model_stats("stats-model-b", run_id)
    assert stats["stdev_latency_s"] is None


def test_compute_model_stats_handles_no_data():
    stats = benchmark_models.compute_model_stats("no-such-model", "no-such-run")
    assert stats["n_success"] == 0
    assert stats["mean_latency_s"] is None


# ---------------------------------------------------------------------------
# environment_health / compute_model_impact / evaluate_thresholds
# (session 4: per-model before/after snapshots, delta-based criteria)
# ---------------------------------------------------------------------------


def _seed_run_with_stats(
    run_id,
    baseline_model,
    baseline_latencies,
    candidate_model,
    candidate_latencies,
    candidate_swap_delta=50.0,
    candidate_compressed_delta=50.0,
    candidate_pressure_before="normal",
    candidate_pressure_after="normal",
    baseline_swap_before=400.0,
    baseline_pressure_before="normal",
    responsiveness_label=None,
):
    """Seeds a run with per-model before/after snapshots (session 4 shape)
    -- baseline model gets its own flat before/after (no change, since
    tests generally don't care about baseline's own impact), candidate
    model's snapshots are built from the requested deltas so tests can
    target one criterion at a time without the others interfering."""
    storage.store_benchmark_run(
        run_id=run_id,
        started_at="2026-07-15T10:00:00+00:00",
        models=[baseline_model, candidate_model],
        keep_alive="0",
    )
    for lat in baseline_latencies:
        storage.store_benchmark_result(
            model=baseline_model,
            prompt_label="p",
            latency_s=lat,
            success=True,
            run_id=run_id,
        )
    for lat in candidate_latencies:
        storage.store_benchmark_result(
            model=candidate_model,
            prompt_label="p",
            latency_s=lat,
            success=True,
            run_id=run_id,
        )

    storage.store_system_snapshot(
        run_id=run_id,
        label=f"before:{baseline_model}",
        timestamp="2026-07-15T10:00:00+00:00",
        swap_used_mb=baseline_swap_before,
        compressed_memory_mb=100.0,
        memory_pressure=baseline_pressure_before,
    )
    storage.store_system_snapshot(
        run_id=run_id,
        label=f"after:{baseline_model}",
        timestamp="2026-07-15T10:01:00+00:00",
        swap_used_mb=baseline_swap_before,
        compressed_memory_mb=100.0,
        memory_pressure=baseline_pressure_before,
    )
    storage.store_system_snapshot(
        run_id=run_id,
        label=f"before:{candidate_model}",
        timestamp="2026-07-15T10:02:00+00:00",
        swap_used_mb=500.0,
        compressed_memory_mb=200.0,
        memory_pressure=candidate_pressure_before,
    )
    storage.store_system_snapshot(
        run_id=run_id,
        label=f"after:{candidate_model}",
        timestamp="2026-07-15T10:03:00+00:00",
        swap_used_mb=500.0 + candidate_swap_delta,
        compressed_memory_mb=200.0 + candidate_compressed_delta,
        memory_pressure=candidate_pressure_after,
    )
    if responsiveness_label is not None:
        rating = benchmark_models.RESPONSIVENESS_LABELS[responsiveness_label]
        storage.update_benchmark_run_responsiveness(
            run_id, rating=rating, label=responsiveness_label
        )


def test_environment_health_available_with_no_concerns():
    run_id = "test-run-envhealth-ok"
    _seed_run_with_stats(
        run_id,
        "env-baseline-a",
        [4.0],
        "env-candidate-a",
        [4.1],
        baseline_swap_before=200.0,
        baseline_pressure_before="normal",
    )
    health = benchmark_models.environment_health(run_id, "env-baseline-a")
    assert health["available"] is True
    assert health["trustworthy"] is True
    assert health["concerns"] == []


def test_environment_health_flags_high_baseline_swap():
    run_id = "test-run-envhealth-swap"
    _seed_run_with_stats(
        run_id,
        "env-baseline-b",
        [4.0],
        "env-candidate-b",
        [4.1],
        baseline_swap_before=5000.0,
        baseline_pressure_before="normal",
    )
    health = benchmark_models.environment_health(run_id, "env-baseline-b")
    assert health["trustworthy"] is False
    assert any("swap" in c.lower() for c in health["concerns"])


def test_environment_health_flags_high_baseline_pressure():
    run_id = "test-run-envhealth-pressure"
    _seed_run_with_stats(
        run_id,
        "env-baseline-c",
        [4.0],
        "env-candidate-c",
        [4.1],
        baseline_swap_before=200.0,
        baseline_pressure_before="critical",
    )
    health = benchmark_models.environment_health(run_id, "env-baseline-c")
    assert health["trustworthy"] is False
    assert any("pressure" in c.lower() for c in health["concerns"])


def test_environment_health_unavailable_without_snapshot():
    storage.store_benchmark_run(
        run_id="test-run-envhealth-none",
        started_at="now",
        models=["no-snap-model"],
        keep_alive="0",
    )
    health = benchmark_models.environment_health(
        "test-run-envhealth-none", "no-snap-model"
    )
    assert health["available"] is False
    assert health["trustworthy"] is None


def test_compute_model_impact_computes_deltas():
    run_id = "test-run-impact-basic"
    _seed_run_with_stats(
        run_id,
        "impact-baseline-a",
        [4.0],
        "impact-candidate-a",
        [4.1],
        candidate_swap_delta=75.0,
        candidate_compressed_delta=120.0,
        candidate_pressure_before="normal",
        candidate_pressure_after="normal",
    )
    impact = benchmark_models.compute_model_impact(run_id, "impact-candidate-a")
    assert impact["swap_delta_mb"] == 75.0
    assert impact["compressed_memory_delta_mb"] == 120.0
    assert impact["memory_pressure_worsened"] is False


def test_compute_model_impact_detects_worsened_pressure():
    run_id = "test-run-impact-worsened"
    _seed_run_with_stats(
        run_id,
        "impact-baseline-b",
        [4.0],
        "impact-candidate-b",
        [4.1],
        candidate_pressure_before="normal",
        candidate_pressure_after="warn",
    )
    impact = benchmark_models.compute_model_impact(run_id, "impact-candidate-b")
    assert impact["memory_pressure_worsened"] is True


def test_compute_model_impact_none_without_snapshots():
    storage.store_benchmark_run(
        run_id="test-run-impact-none",
        started_at="now",
        models=["no-snap-model-2"],
        keep_alive="0",
    )
    impact = benchmark_models.compute_model_impact(
        "test-run-impact-none", "no-snap-model-2"
    )
    assert impact["swap_delta_mb"] is None
    assert impact["memory_pressure_worsened"] is None


def test_evaluate_thresholds_switch_when_all_pass():
    run_id = "test-run-threshold-switch"
    _seed_run_with_stats(
        run_id,
        "baseline-model-a",
        [4.0, 4.0],
        "candidate-model-a",
        [4.5, 4.5],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-a",
        "candidate-model-a",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["verdict"] == "switch"
    assert result["criteria"]["latency_increase_pct"]["status"] == "pass"


def test_evaluate_thresholds_stay_when_latency_fails():
    run_id = "test-run-threshold-stay"
    # candidate is 10x slower — way past the default 40% threshold
    _seed_run_with_stats(
        run_id,
        "baseline-model-b",
        [2.0, 2.0],
        "candidate-model-b",
        [20.0, 20.0],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-b",
        "candidate-model-b",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["verdict"] == "stay"
    assert result["criteria"]["latency_increase_pct"]["status"] == "fail"


def test_evaluate_thresholds_stay_when_swap_delta_exceeds_threshold():
    run_id = "test-run-threshold-swap"
    _seed_run_with_stats(
        run_id,
        "baseline-model-c",
        [4.0],
        "candidate-model-c",
        [4.1],
        candidate_swap_delta=5000.0,
        candidate_compressed_delta=10.0,
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-c",
        "candidate-model-c",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["swap_delta_mb"]["status"] == "fail"
    assert result["verdict"] == "stay"


def test_evaluate_thresholds_does_not_fail_on_high_ambient_baseline():
    # This is the actual bug the user found from real hardware: a heavily
    # loaded ambient baseline (Chrome/VS Code/Spotify) must NOT fail a
    # candidate whose own delta is small, even though the OLD absolute
    # after-snapshot check would have failed it.
    run_id = "test-run-threshold-ambient-baseline"
    _seed_run_with_stats(
        run_id,
        "baseline-model-ambient",
        [4.0],
        "candidate-model-ambient",
        [4.1],
        candidate_swap_delta=4.0,
        candidate_compressed_delta=4.0,
        baseline_swap_before=1166.75,
        baseline_pressure_before="warn",
        candidate_pressure_before="warn",
        candidate_pressure_after="warn",
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-ambient",
        "candidate-model-ambient",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["swap_delta_mb"]["status"] == "pass"
    assert result["criteria"]["memory_pressure_worsened"]["status"] == "pass"
    assert result["verdict"] == "switch"
    # but environment_health should still flag the ambient baseline as a caveat
    health = benchmark_models.environment_health(run_id, "baseline-model-ambient")
    assert health["trustworthy"] is False


def test_evaluate_thresholds_stay_when_compressed_memory_delta_exceeds_threshold():
    run_id = "test-run-threshold-compressed"
    _seed_run_with_stats(
        run_id,
        "baseline-model-comp",
        [4.0],
        "candidate-model-comp",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=5000.0,
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-comp",
        "candidate-model-comp",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["compressed_memory_delta_mb"]["status"] == "fail"
    assert result["verdict"] == "stay"


def test_evaluate_thresholds_stay_when_memory_pressure_worsens():
    run_id = "test-run-threshold-pressure"
    _seed_run_with_stats(
        run_id,
        "baseline-model-d",
        [4.0],
        "candidate-model-d",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
        candidate_pressure_before="normal",
        candidate_pressure_after="critical",
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-d",
        "candidate-model-d",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["memory_pressure_worsened"]["status"] == "fail"
    assert result["verdict"] == "stay"


def test_evaluate_thresholds_memory_pressure_worsening_allowed_via_threshold():
    run_id = "test-run-threshold-pressure-allowed"
    _seed_run_with_stats(
        run_id,
        "baseline-model-d2",
        [4.0],
        "candidate-model-d2",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
        candidate_pressure_before="normal",
        candidate_pressure_after="critical",
    )
    thresholds = dict(benchmark_models.DEFAULT_THRESHOLDS)
    thresholds["allow_memory_pressure_worsening"] = True
    result = benchmark_models.evaluate_thresholds(
        run_id, "baseline-model-d2", "candidate-model-d2", thresholds
    )
    assert result["criteria"]["memory_pressure_worsened"]["status"] == "pass"
    assert result["verdict"] == "switch"


def test_evaluate_thresholds_inconclusive_when_no_snapshots():
    run_id = "test-run-threshold-inconclusive"
    storage.store_benchmark_run(
        run_id=run_id,
        started_at="2026-07-15T10:00:00+00:00",
        models=["baseline-model-e", "candidate-model-e"],
        keep_alive="0",
    )
    storage.store_benchmark_result(
        model="baseline-model-e",
        prompt_label="p",
        latency_s=4.0,
        success=True,
        run_id=run_id,
    )
    storage.store_benchmark_result(
        model="candidate-model-e",
        prompt_label="p",
        latency_s=4.1,
        success=True,
        run_id=run_id,
    )
    # deliberately no system snapshots stored for this run
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-e",
        "candidate-model-e",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["swap_delta_mb"]["status"] == "not_evaluated"
    assert result["verdict"] == "inconclusive"


def test_evaluate_thresholds_responsiveness_not_evaluated_without_rating():
    run_id = "test-run-threshold-no-rating"
    _seed_run_with_stats(
        run_id,
        "baseline-model-f",
        [4.0],
        "candidate-model-f",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-f",
        "candidate-model-f",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["responsiveness_rating"]["status"] == "not_evaluated"
    # a not_evaluated non-core criterion shouldn't block an otherwise-passing verdict
    assert result["verdict"] == "switch"


def test_evaluate_thresholds_fails_on_low_responsiveness_label():
    run_id = "test-run-threshold-bad-rating"
    _seed_run_with_stats(
        run_id,
        "baseline-model-g",
        [4.0],
        "candidate-model-g",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
        responsiveness_label="noticeable_lag",
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-g",
        "candidate-model-g",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["responsiveness_rating"]["status"] == "fail"
    assert result["verdict"] == "stay"


def test_evaluate_thresholds_passes_on_smooth_responsiveness_label():
    run_id = "test-run-threshold-good-rating"
    _seed_run_with_stats(
        run_id,
        "baseline-model-g2",
        [4.0],
        "candidate-model-g2",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
        responsiveness_label="smooth",
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-g2",
        "candidate-model-g2",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["responsiveness_rating"]["status"] == "pass"
    assert result["verdict"] == "switch"


def test_evaluate_thresholds_zero_baseline_latency_does_not_crash_or_misfire():
    # Regression test: mean_latency_s of exactly 0.0 is falsy in Python,
    # which previously caused evaluate_thresholds to mislabel a real
    # (if degenerate) 0.0 baseline as "not_evaluated" via a truthy check
    # instead of an explicit is-not-None check. Found via an end-to-end
    # smoke test with instant mocked responses -- see session 3's log.
    run_id = "test-run-threshold-zero-baseline"
    _seed_run_with_stats(
        run_id,
        "baseline-model-zero",
        [0.0, 0.0],
        "candidate-model-zero",
        [1.0],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
    )
    result = benchmark_models.evaluate_thresholds(
        run_id,
        "baseline-model-zero",
        "candidate-model-zero",
        benchmark_models.DEFAULT_THRESHOLDS,
    )
    assert result["criteria"]["latency_increase_pct"]["status"] == "not_evaluated"


# ---------------------------------------------------------------------------
# generate_report — writes a real file, check the key sections exist
# ---------------------------------------------------------------------------


def test_generate_report_writes_expected_sections(tmp_path):
    run_id = "test-run-report-1"
    _seed_run_with_stats(
        run_id,
        "report-baseline",
        [4.0, 4.2],
        "report-candidate",
        [4.5, 4.6],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
    )
    with patch("benchmark_models.REPORTS_DIR", tmp_path):
        path = benchmark_models.generate_report(
            run_id,
            ["report-baseline", "report-candidate"],
            benchmark_models.DEFAULT_THRESHOLDS,
        )
    assert path.exists()
    content = path.read_text()
    for section in [
        "## Hypothesis",
        "## Method",
        "## Environment Health",
        "## Measurements",
        "## Model Impact",
        "## Results",
        "## Limitations",
        "## Conclusion",
        "## Recommendation",
        "## Future work",
    ]:
        assert section in content
    assert "report-candidate" in content
    assert "**Verdict: switch**" in content


def test_generate_report_flags_ambient_baseline_caveat():
    run_id = "test-run-report-ambient"
    _seed_run_with_stats(
        run_id,
        "report-baseline-amb",
        [4.0],
        "report-candidate-amb",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
        baseline_swap_before=5000.0,
        baseline_pressure_before="critical",
    )
    import tempfile
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("benchmark_models.REPORTS_DIR", P(tmpdir)):
            path = benchmark_models.generate_report(
                run_id,
                ["report-baseline-amb", "report-candidate-amb"],
                benchmark_models.DEFAULT_THRESHOLDS,
            )
        content = path.read_text()
    assert "Caveats on this run's baseline" in content


def test_generate_report_handles_missing_workload_note():
    run_id = "test-run-report-2"
    _seed_run_with_stats(
        run_id, "report-baseline-2", [4.0], "report-candidate-2", [4.1]
    )
    import tempfile
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("benchmark_models.REPORTS_DIR", P(tmpdir)):
            path = benchmark_models.generate_report(
                run_id,
                ["report-baseline-2", "report-candidate-2"],
                benchmark_models.DEFAULT_THRESHOLDS,
            )
        content = path.read_text()
    assert "not recorded" in content


# ---------------------------------------------------------------------------
# CLI paths: --rate and --report-only
# ---------------------------------------------------------------------------


def test_main_rate_records_responsiveness(monkeypatch, capsys):
    run_id = "test-run-cli-rate"
    storage.store_benchmark_run(
        run_id=run_id,
        started_at="2026-07-15T10:00:00+00:00",
        models=["m1"],
        keep_alive="0",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark_models.py", "--rate", f"{run_id}:slight_lag:looked mostly fine"],
    )
    benchmark_models.main()
    run = storage.query_benchmark_run(run_id)
    assert run["responsiveness_label"] == "slight_lag"
    assert run["responsiveness_rating"] == 3
    assert run["responsiveness_note"] == "looked mostly fine"
    assert "Recorded responsiveness" in capsys.readouterr().out


def test_main_rate_rejects_invalid_label(monkeypatch, capsys):
    run_id = "test-run-cli-rate-bad"
    storage.store_benchmark_run(
        run_id=run_id,
        started_at="2026-07-15T10:00:00+00:00",
        models=["m1"],
        keep_alive="0",
    )
    monkeypatch.setattr(
        sys, "argv", ["benchmark_models.py", "--rate", f"{run_id}:super_duper_smooth"]
    )
    result = benchmark_models.main()
    assert result == 1
    assert "must be one of" in capsys.readouterr().out


def test_main_rate_rejects_unknown_run_id(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["benchmark_models.py", "--rate", "no-such-run:smooth"]
    )
    result = benchmark_models.main()
    assert result == 1
    assert "no such run_id" in capsys.readouterr().out


def test_main_report_only_regenerates_from_stored_run(monkeypatch, capsys, tmp_path):
    run_id = "test-run-cli-report-only"
    _seed_run_with_stats(
        run_id,
        "cli-baseline",
        [4.0],
        "cli-candidate",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
    )
    monkeypatch.setattr(sys, "argv", ["benchmark_models.py", "--report-only", run_id])
    with patch("benchmark_models.REPORTS_DIR", tmp_path):
        result = benchmark_models.main()
    assert result == 0
    assert "Report written to" in capsys.readouterr().out


def test_main_report_only_rejects_unknown_run_id(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["benchmark_models.py", "--report-only", "no-such-run"]
    )
    result = benchmark_models.main()
    assert result == 1
    assert "no such run_id" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# print_raw_snapshot / --snapshot-only (session 6: one-command cross-check
# against Activity Monitor/vm_stat, since compressed_memory_mb has never
# been independently verified against real Activity Monitor output)
# ---------------------------------------------------------------------------


def test_print_raw_snapshot_prints_values(capsys):
    with patch("system_snapshot.get_swap_used_mb", return_value=512.0), patch(
        "system_snapshot.get_compressed_memory_mb", return_value=2048.5
    ), patch("system_snapshot.get_memory_pressure_level", return_value="warn"), patch(
        "system_snapshot.get_cpu_load_avg", return_value=(1.1, 1.2, 1.3)
    ):
        benchmark_models.print_raw_snapshot()
    out = capsys.readouterr().out
    assert "512.0" in out
    assert "2048.5" in out
    assert "warn" in out
    assert "vm_stat" in out
    assert "Activity Monitor" in out


def test_print_raw_snapshot_handles_unavailable_values(capsys):
    with patch("system_snapshot.get_swap_used_mb", return_value=None), patch(
        "system_snapshot.get_compressed_memory_mb", return_value=None
    ), patch("system_snapshot.get_memory_pressure_level", return_value=None), patch(
        "system_snapshot.get_cpu_load_avg", return_value=None
    ):
        benchmark_models.print_raw_snapshot()
    out = capsys.readouterr().out
    assert "n/a" in out


def test_main_snapshot_only_exits_without_running_trials(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["benchmark_models.py", "--snapshot-only"])
    with patch("system_snapshot.get_swap_used_mb", return_value=100.0), patch(
        "system_snapshot.get_compressed_memory_mb", return_value=200.0
    ), patch("system_snapshot.get_memory_pressure_level", return_value="normal"), patch(
        "system_snapshot.get_cpu_load_avg", return_value=(1.0, 1.0, 1.0)
    ), patch(
        "benchmark_models.requests.post"
    ) as mock_post:
        result = benchmark_models.main()
    assert result == 0
    mock_post.assert_not_called()
    assert "100.0" in capsys.readouterr().out


def test_main_requires_models_unless_rate_or_report_only(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["benchmark_models.py"])
    try:
        benchmark_models.main()
        raised = False
    except SystemExit:
        raised = True
    assert (
        raised
    ), "main() should refuse to proceed without --models/--rate/--report-only"


def test_main_allow_memory_pressure_worsening_flag_wired_through(
    monkeypatch, capsys, tmp_path
):
    run_id = "test-run-cli-allow-worsening"
    _seed_run_with_stats(
        run_id,
        "cli-baseline-2",
        [4.0],
        "cli-candidate-2",
        [4.1],
        candidate_swap_delta=10.0,
        candidate_compressed_delta=10.0,
        candidate_pressure_before="normal",
        candidate_pressure_after="critical",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_models.py",
            "--report-only",
            run_id,
            "--allow-memory-pressure-worsening",
        ],
    )
    with patch("benchmark_models.REPORTS_DIR", tmp_path):
        benchmark_models.main()
    content = (tmp_path / f"{run_id}.md").read_text()
    assert "**Verdict: switch**" in content
