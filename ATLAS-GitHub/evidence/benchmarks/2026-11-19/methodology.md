# Measurement protocol

The source revision was `697a9f7`. The experiment started at 17:11:08 UTC on 19 November 2026. A dedicated loopback Ollama server on port 11479 used the installed models, with cloud access disabled, one concurrent inference slot and one resident model. The server was stopped when the runner exited. The original public source contained no paired historical trial export.

The planned order was 4B then 8B. Each block began with a separate one-word warm-up, followed by three rounds of tool selection phrasing, code reasoning and passage summarization. The tool-selection prompt asked for text only. The same settings and prompt order applied to both models. Each block required normal pressure, an empty resident-model list and estimated available memory exceeding the stored model size by 1 GiB. This is a conservative screening choice. Each request had a 60-second timeout. The harness checked pressure between trials, captured host snapshots on both sides and stopped on failure or warning pressure.

Elapsed request time used a monotonic clock. Ollama supplied nanosecond load and evaluation durations. Output throughput divides generated tokens by evaluation seconds. The sampler polled loaded size and VRAM separately every 0.2 seconds. Fixed prompts and per-trial counters are in the adjacent JSON files. Host delta means post-trial minus pre-trial. The final trial of a completed block would request immediate unload. Average and median latency use successful measured requests only. Failed rows and warm-ups stay in the raw data.

[Runner](../../../verify_models.py) · [Configuration](model-configurations.json) · [Trial](raw-results.json)
