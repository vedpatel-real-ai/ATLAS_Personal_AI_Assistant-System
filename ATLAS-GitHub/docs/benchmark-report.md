# Tool-assisted and model-only requests

**Historical report · July 29, 2026.** Three comparisons explored what happens when a local model can use repository data, a transcript or a bounded document excerpt. The retained report gives the observations below. The raw captures it named were unavailable during November verification, so the timings and counts are reported observations from July.

| Task | Tool-assisted path | Direct model path |
|---|---|---|
| Repository dependencies | About 4.4 s. The scan reported 40 files, 49 internal edges and zero cycles. It counted 13 dependents for `tools.py` and 12 for `storage.py`. The request still returned `success: false` because the router passed an empty focus filename. | About 30 s. The model described the architecture and suggested possible cycles without scanning the tree. The report recorded discrepancies. |
| YouTube summary | 75.67 s. The tool retrieved 22,128 transcript characters and generated a summary. | 2.97 s. The model said it could not access the video. |
| Document summary | 61.63 s. The tool capped the excerpt and sent 40,181 prompt characters. Ollama reported 2,050 evaluated prompt tokens. | 52.37 s. The prompt included the full document and contained 140,837 characters. Ollama again reported 2,050 evaluated prompt tokens. |

The repository and video examples illustrate access to information. The scan had no independently annotated reference graph, and the transcript summary received no accuracy score. The video timings describe different outcomes.

The source document, `IMPLEMENTATION_STATE_FULL.md`, contained 140,782 characters. Applying the excerpt cap reduced the **prompt character count by about 71.5%**. Both paths had the same evaluated-token count, and the shorter prompt took longer in this run. Token savings, the cause of the equal counters and a general latency advantage remain unestablished.

## Capture provenance

The July report named `atlas_tools_depgraph_v2.json`, `atlas_tools_doc_run1.json`, `atlas_notools_doc_run1.json`, `tokens_tool_run1.json` and `tokens_raw_run1.json`. The November experiment has its own raw observations and tests a different question. [Benchmark verification](verification/BENCHMARK_VERIFICATION.md) · [Token probe](../probe_tool_tokens.py)
