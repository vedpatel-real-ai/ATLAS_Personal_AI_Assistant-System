# Examples and limitations

## Inspect a small repository

Suppose `api.py` and `worker.py` both contain `import storage`, and `storage.py` imports neither. A focused dependency scan of `storage.py` can return both callers in `depended_on_by` without reporting a cycle. Add `import api` to `storage.py` and there is now a path from `api.py` back to itself. Use the relative project path to distinguish files with identical names.

The scanner considers up to 3,000 source files and skips import parsing for files larger than 300,000 bytes. External packages, runtime imports and bundler aliases can fall outside its graph. [Implementation](../../tools.py) · [Executable examples](../../tests/test_dependency_graph.py)

## Summarize a document or video

Give the document tool a local path to a supported text file, text-bearing PDF or DOCX. A question can focus the summary on a topic. Text beyond the first 40,000 extracted characters is excluded from the prompt. An answer near the end of a long file can therefore be missed. The cap applies after extraction. A scanned PDF may have no extractable text.

The video tool takes a YouTube URL or video ID and fetches captions before applying the same excerpt limit. A missing transcript or unavailable service can prevent summarization. Tests with supplied transcripts check parsing and handoff to the model. They do not grade factual accuracy. [Document tests](../../tests/test_summarize_document.py) · [Transcript tests](../../tests/test_summarize_youtube_video.py)

## Follow a background job

An API client can submit a request, inspect any required approval, then poll the returned job ID. The response records progress as steps finish. Cancellation is cooperative, so a currently running tool may finish. A restart marks interrupted jobs failed. Jobs use threads without a bounded worker pool. The model lock covers routing and direct reasoning in one process, while summary tools issue independent calls. [API](../../main.py) · [Workers](../../jobs.py)

## Open evaluation questions

These experiments are proposed work.

- **Routing accuracy.** Label realistic requests with their expected tool, arguments and approval tier. Report errors for each field and include paraphrases that miss deterministic patterns.
- **Human review.** Observe whether people notice incorrect paths or unexpected actions in the displayed step list before approving a batch.
- **Summary quality.** Score statements against the actual source excerpt and compare grounded and model-only responses on the same questions.
- **Failure diagnosis.** Group logged failures by tool and cause. Check whether grouping repeated symptoms obscures uncommon failures.
- **Resource use and reproducibility.** Repeat matched model trials with the same prompts, settings and sufficient memory headroom. Record model digests, run order, latency and host pressure. The November run stopped after a warm-up. [Benchmark protocol](../verification/BENCHMARK_VERIFICATION.md)
