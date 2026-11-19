# Does the evidence support choosing 4B over 8B?

**Insufficient evidence.** On 19 November 2026 both Qwen3 tags were installed. A real 4B warm-up finished on a 16 GiB Apple silicon Mac. The operating system reported warning-level memory pressure immediately afterward, so the harness stopped before measured repetitions or any 8B generation. This establishes that headroom mattered on this occasion. It leaves the performance and quality comparison open. [Raw trial and host readings](../../evidence/benchmarks/2026-11-19/raw-results.json) · [Run status](../../evidence/benchmarks/2026-11-19/environment.json)

**Historical model-selection claim: Not independently verified from retained artifacts.** The supplied project workspace, public checkout and reachable Git history were searched for databases, captures, JSON/CSV, benchmark reports, snapshots and test logs before generating these results. The original public history contained one commit. It included the benchmark code, tests with synthetic measurements, dated narrative observations and a tool-versus-no-tool report. No original database, paired per-trial export or listed July raw JSON was recovered. This finding applies to the searched locations. [July report](../benchmark-report.md) · [Historical benchmark harness](../../benchmark_models.py) · [Storage schema](../../storage.py)

## New experiment

The question was whether the larger model could handle the same short tasks within the memory and latency budget of one laptop. Both tags used Q4_K_M quantization. Ollama 0.32.0 ran locally with one resident model allowed. The requested context was 2,048 tokens, with temperature 0, seed 42, thinking disabled and at most 64 output tokens. The configured context limit differs from each model’s advertised maximum. [Full model identities, digests, settings and prompts](../../evidence/benchmarks/2026-11-19/model-configurations.json)

| Observation | qwen3:4b-instruct | qwen3:8b |
|---|---:|---:|
| Parameter count reported by Ollama | 4.0B | 8.2B |
| Completed warm-ups / measured trials | 1 / 0 | 0 / 0 |
| Mean latency / median latency / output tokens per second across measured trials | N/A | N/A |
| Warm-up request latency / model load time | 3.64 s / 3.02 s | N/A |
| Warm-up generation | 2 output tokens at 28.3 tokens/s | N/A |
| Maximum loaded size observed / VRAM component | 2,732.9 / 2,732.9 MiB | N/A |
| Host swap before → after warm-up | 486.25 → 1,347.75 MiB (+861.50) | N/A |
| Host compressor before → after warm-up | 5,137.5 → 5,597.9 MiB (+460.4) | N/A |
| Host available memory before → after warm-up | 3,620.5 → 2,220.9 MiB | N/A |
| Memory pressure | normal → warn | N/A |
| Failed inference requests | 0 of 1 attempted | N/A (no attempts) |

The warm-up asked for the word “ready.” Two output tokens give a poor throughput estimate. Host readings include unrelated processes. CPU load averages report runnable workload, not CPU utilization. Checked-in raw JSON includes nested host observations, while the CSV lists request counters. The loaded-model size and VRAM allocation overlap on unified memory. Adding them would double-count memory. A historical process-RSS sampler could observe the wrong process. The current sampler polls the matching model in Ollama’s running-model endpoint and can miss short-lived peaks. [Raw CSV](../../evidence/benchmarks/2026-11-19/raw-results.csv) · [Sampler](../../benchmark_models.py) · [macOS snapshots](../../system_snapshot.py) · [Ollama running-model fields](https://docs.ollama.com/api/ps) · [Generation timing fields](https://docs.ollama.com/api/generate)

## Repeat the experiment

Use the test guide’s Python environment. From the application directory, on a quiet Mac with both exact tags already installed and an idle loopback Ollama service, run the commands below. The verification runner sends only the fixed public prompts. It does not execute generated text. The output directory must be new.

```sh
ollama list
python verify_models.py /tmp/atlas-models-first
# In a separate session after memory returns to baseline, counterbalance the order.
python verify_models.py /tmp/atlas-models-reverse --reverse
```

If the service is stopped, start a dedicated terminal with `OLLAMA_HOST=127.0.0.1:11479 OLLAMA_NO_CLOUD=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 ollama serve`, then add `--port 11479` to the experiment command. Stop that dedicated server after the run. Review the status and raw rows even if the command exits nonzero. [Protocol](../../evidence/benchmarks/2026-11-19/methodology.md) · [Limitations](../../evidence/benchmarks/2026-11-19/limitations.md) · [Runner](../../verify_models.py)

## What July’s tool experiment showed

- **Repository analysis.** The report described 40 scanned files and 49 internal edges. The tool could inspect the tree. The prose also records a failed focused lookup, and there is no retained ground-truth annotation or independent score.
- **Video.** The report described retrieving 22,128 transcript characters. Transcript access gives the model evidence it can read. The 75.67-second tool path and 2.97-second direct reply accomplished different tasks. Summary accuracy needs a separate evaluation.
- **Document.** The report recorded 40,181 versus 140,837 prompt characters, about 71.5% fewer. Both paths reported **2,050 evaluated prompt tokens**. The shorter input took **61.63 s**, compared with **52.37 s** for the direct path. These observations support a payload-size claim only. A context limit or caching effect would need its own controlled test. [July write-up](../benchmark-report.md) · [Token probe](../../probe_tool_tokens.py)
