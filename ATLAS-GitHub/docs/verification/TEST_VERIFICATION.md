# What did pytest actually verify?

On 19 November 2026, pytest collected **355 cases from the original 26 test modules**. They all passed after isolating the test environment. The application and those modules matched audited commit `13471edf6b1701065382f263bdb92d39bfc80712`. A separate test module added 23 cases. The final local run collected and passed **378**.

| Local run on Apple silicon | Tested revision | Passed / collected | Pytest exit | Terminal duration |
|---|---|---:|---:|---:|
| First isolation attempt | `b12de4c7b1c7210d2efee6a8a565a9977ca12434` | 339 / 355 | 1 | 9.64 s |
| Corrected fixture, original suite | `90691affe1bd240532a05728ec9e931db88504d7` | 355 / 355 | 0 | 9.86 s |
| Engineering corrections and regressions | `ac5d7ed12629028ba54a6be4d1b2c66b4a795de0` | 378 / 378 | 0 | 10.48 s |

All three runs used macOS 27.0 arm64, Python 3.12.13 and pytest 9.1.1. The last run began at 17:12:24 UTC. It recorded zero failures, errors, skips, expected failures or exclusions. One warning concerns Starlette’s deprecated HTTPX TestClient backend. The original source contained 350 test functions. Six URL forms execute one of them six times, adding five cases. These runs establish what the preserved tests do today. Their dates do not establish an earlier passing run.

The first fixture added a database, log and sample project under a directory whose contents were being counted or searched. That changed file counts and relative focus paths in 16 tests. Moving runtime data to a sibling temporary directory restored the intended conditions. The later production change also fixes project-relative lookup when someone runs ATLAS from a subdirectory. Compare the first failure report with the passing collection. [Initial run](../../evidence/tests/2026-11-19/initial/) · [Original suite](../../evidence/tests/2026-11-19/original/) · [Final terminal output](../../evidence/tests/2026-11-19/final/pytest-output.txt) · [JUnit](../../evidence/tests/2026-11-19/final/junit.xml) · [Final manifest](../../evidence/tests/2026-11-19/final/manifest.json)

## Reproduce from a Git checkout

Run from the repository root. The exporter archives the selected commit, enters its application directory in temporary storage, collects cases, then runs the suite. The output path must be new. Uncommitted edits are outside the selected revision.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r ATLAS-GitHub/requirements-test.txt
cd ATLAS-GitHub
python scripts/verify_tests.py /tmp/atlas-final --revision ac5d7ed12629028ba54a6be4d1b2c66b4a795de0
python scripts/verify_tests.py /tmp/atlas-original --revision 90691affe1bd240532a05728ec9e931db88504d7
(cd /tmp/atlas-final && shasum -a 256 -c checksums.sha256)
```

For the current committed code omit `--revision`. For current working-tree edits run `python -m pytest -v tests` from the application directory. Each manifest records exact arguments, source file hashes, dependency versions, exit codes and UTC times. Public logs normalize local paths and omit the JUnit hostname. Checksums identify the exported files. A reviewer can independently execute the same snapshot or inspect the hosted run. [Workflow definition](../../../.github/workflows/atlas-tests.yml)

## Independent GitHub verification

The first hosted attempt stopped in setup because the macOS runner lacked the exact 3.12.13 build. It ran zero tests. The workflow now requests the available Python 3.12 patch. The completed pull-request run tested GitHub’s temporary merge revision `306cfd39cddd0b902abaf3e765df468a67ca30f3` for branch head `b18956de79164e4d350ae08eb2b6c1a95dd143bb`. The downloaded artifact reported **378 collected, 378 passed**, no failures, errors, skips or exclusions and one warning in 20.21 seconds. Collection and pytest both exited 0. It used Python 3.12.10, pytest 9.1.1 and macOS 15.8.2 arm64 on 19 November 2026 at 17:20:47 UTC. The JUnit case count and six file checksums were verified again from the artifact. Later branch tips have their own checks. [First setup failure](https://github.com/vedpatel-real-ai/ATLAS_Personal_AI_Assistant-System/actions/runs/44574830547) · [Completed run and downloadable artifact](https://github.com/vedpatel-real-ai/ATLAS_Personal_AI_Assistant-System/actions/runs/44576110069)

## What the cases exercise

Storage paths change before importing the app, including during collection. Each case gets an initialized SQLite database and its own workspace. Tests can traverse real files, run local Git on temporary repositories and call FastAPI in-process. The fixture resets the limiter and index cache, supplies a battery reading, intercepts the specific VS Code invocation and rejects unexpected network connections or subprocess launches. Threads, progress updates and confirmation consumption still execute. [Isolation fixture](../../tests/conftest.py) · [Collected case names](../../evidence/tests/2026-11-19/final/collection.txt)

| Module | Cases | Evidence boundary |
|---|---:|---|
| [test_async_command](../../tests/test_async_command.py) | 14 | In-process HTTP, real job threads and SQLite. Routing, reasoning, selected executors and battery hardware are substituted. |
| [test_benchmark_models](../../tests/test_benchmark_models.py) | 47 | Synthetic trials and model API replies exercise aggregation, threshold logic, reports and SQLite. No inference. |
| [test_clipboard](../../tests/test_clipboard.py) | 11 | Controlled subprocess replies model pbcopy and pbpaste success, errors and binary data. |
| [test_decision_engine](../../tests/test_decision_engine.py) | 12 | Pure routing labels and attachment rules. |
| [test_dependency_graph](../../tests/test_dependency_graph.py) | 28 | Real temporary Python and JS/TS trees, focus lookup and cycle traversal. |
| [test_file_management](../../tests/test_file_management.py) | 19 | Disposable file reads, copies, moves, renames and validation. |
| [test_git_query](../../tests/test_git_query.py) | 7 | Real Git commands and commits in per-case temporary repositories. |
| [test_index_project](../../tests/test_index_project.py) | 7 | Real file trees, exclusions and cache lifetime. |
| [test_jobs](../../tests/test_jobs.py) | 11 | Real worker threads, cancellation registry, persisted progress and restart reconciliation. |
| [test_main_decision_engine_integration](../../tests/test_main_decision_engine_integration.py) | 11 | In-process HTTP, SQLite and local scans. Router, reasoning, editor launch and battery boundaries use fixtures. |
| [test_matcher](../../tests/test_matcher.py) | 11 | Pure matching, chaining and fallback. |
| [test_memory](../../tests/test_memory.py) | 13 | Real SQLite preference, project and conversation round trips. |
| [test_normalize_params](../../tests/test_normalize_params.py) | 14 | Real tool signatures and argument mapping. |
| [test_planner](../../tests/test_planner.py) | 13 | Flat sequences with injected executors and progress callbacks. |
| [test_read_zip](../../tests/test_read_zip.py) | 7 | Real archives built in temporary storage. |
| [test_request_context](../../tests/test_request_context.py) | 9 | Context construction with substituted memory lookup. |
| [test_router](../../tests/test_router.py) | 6 | Fake Ollama replies, real prompt construction and response normalization. |
| [test_search_code](../../tests/test_search_code.py) | 6 | Controlled ripgrep subprocess results and missing binary. |
| [test_search_knowledge](../../tests/test_search_knowledge.py) | 9 | Search tool formats injected storage results. |
| [test_storage_benchmarks](../../tests/test_storage_benchmarks.py) | 12 | Real SQLite with invented trial and snapshot rows. |
| [test_storage_confirmations](../../tests/test_storage_confirmations.py) | 6 | Real SQLite token lifecycle and controlled time for expiry. |
| [test_storage_jobs](../../tests/test_storage_jobs.py) | 9 | Real SQLite job creation, queries and state updates. |
| [test_storage_knowledge](../../tests/test_storage_knowledge.py) | 9 | Real SQLite keyword search and record ordering. |
| [test_summarize_document](../../tests/test_summarize_document.py) | 29 | Real temporary documents, substituted model and PDF/DOCX boundaries in selected cases. |
| [test_summarize_youtube_video](../../tests/test_summarize_youtube_video.py) | 20 | Real URL parsing, substituted transcript and model. |
| [test_system_snapshot](../../tests/test_system_snapshot.py) | 15 | Controlled macOS command output and failure paths. |
| [test_verification_contracts](../../tests/test_verification_contracts.py) | 23 | Real file aliases, authentication and tool dispatch. Injected failure, cross-volume errors and model allocation response. |

A macOS runner exercises the suite on that OS. Live clipboard, editor launch, battery hardware, delivery, third-party transcript availability and generated answer quality are outside this test result.
