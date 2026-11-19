# ATLAS verification

ATLAS is a macOS assistant that connects natural-language requests to local tools. A battery question can be handled by a pattern and a Python function. A repository question can invoke an import scan. A local model helps interpret requests that need more flexible phrasing. [Project explanation](../mitacs/ATLAS_SIMPLE_EXPLANATION.md)

### Verified implementation

The registry contains 24 entries. Twenty-two have substantive implementations. Email and stock pricing are unfinished. The router and reasoning paths configure the same 4B tag. Five labels describe routing decisions. L1 has no separate execution branch. Python AST parsing, heuristic JS/TS import extraction, iterative cycle detection, SQLite state and step-bound confirmation tokens can all be inspected in source. [Claim-by-claim source audit](CLAIM_MATRIX.md)

### Automated tests

On 19 November 2026 the **355 original cases passed** with isolated storage and OS boundaries. The final suite passed **378 of 378**, with zero failures, skips or errors and one dependency warning. That local run used macOS 27.0 arm64, Python 3.12.13 and pytest 9.1.1 at `ac5d7ed12629028ba54a6be4d1b2c66b4a795de0`. A first fixture attempt failed 16 cases and is preserved alongside both passing runs. [Test verification](TEST_VERIFICATION.md) · [Terminal transcript](../../evidence/tests/2026-11-19/final/pytest-output.txt) · [JUnit XML](../../evidence/tests/2026-11-19/final/junit.xml)

GitHub Actions independently passed **378 of 378** on macOS 15.8.2 arm64 with Python 3.12.10. Its pull-request merge revision was `306cfd39cddd0b902abaf3e765df468a67ca30f3`. Open the job summary or download the `pytest-macos` artifact to inspect the hosted transcript and JUnit XML. [Completed GitHub Actions run](https://github.com/vedpatel-real-ai/ATLAS_Personal_AI_Assistant-System/actions/runs/44576110069)

### Model benchmarking

**The 4B-versus-8B selection remains inconclusive.** Both named models were installed. In a new 16 GiB Mac experiment, one 4B warm-up completed in 3.64 seconds and memory pressure rose from normal to warning. The runner stopped before measured repetitions and before loading 8B. The accessible project history contained no paired historical raw data. [Full comparison and limits](BENCHMARK_VERIFICATION.md) · [Actual warm-up row](../../evidence/benchmarks/2026-11-19/raw-results.json)

### Research evaluation

A July report described repository inspection and successful transcript retrieval. Its document example recorded about 71.5% fewer prompt characters, identical evaluated token counts (2,050) and a longer tool-path duration. This distinguishes external access and input handling from answer accuracy and speed. The original raw files named in that report were unavailable. [Retained report](../benchmark-report.md)

### Reproduce

From `ATLAS-GitHub/` in a Git checkout, activate a fresh Python 3.12 environment and install the locked dependencies. Use a new output directory for each run.

```sh
python -m pip install -r requirements-test.txt
python scripts/verify_tests.py /tmp/atlas-test-evidence
# On a quiet Mac with both models installed and an idle loopback Ollama service
python verify_models.py /tmp/atlas-model-evidence
```

The tests exercise local files, Git, SQLite and in-process HTTP. External calls and selected hardware responses use fixtures. Live model accuracy, end-user approval behavior and crash resumption were outside the test claim. [Test inventory](TEST_VERIFICATION.md) · [Benchmark protocol](../../evidence/benchmarks/2026-11-19/methodology.md) · [Interview guide](../mitacs/ATLAS_INTERVIEW_QA.md)
