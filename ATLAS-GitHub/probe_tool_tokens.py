#!/usr/bin/env python3
"""
Tiny harness to get REAL token counts for ATLAS's tool-cleaned prompt vs. a
naive no-tool prompt, using Ollama's own /api/generate response fields
(prompt_eval_count, eval_count).

Why this exists: neither main.py's run_big_model() nor tools.py's
_call_reasoning_model() currently log token counts to atlas.db — they only
keep resp.json()["response"]. So atlas.db alone cannot answer "how many
tokens did the tool path use." This script does NOT call ATLAS or patch its
code; it reconstructs the exact prompt tools.summarize_document() builds
(same SUMMARIZE_MAX_CHARS cap, same template string) and sends it straight to
Ollama, so the token count reflects what the tool path actually sends.

If tools.py's SUMMARIZE_MAX_CHARS or prompt template ever changes, update the
constants below to match — this is a deliberate manual mirror, not an import,
so it also works from a machine that hasn't got the ATLAS repo on PYTHONPATH.

Usage:
    python3 probe_tool_tokens.py --file /path/to/document.md --mode tool
    python3 probe_tool_tokens.py --file /path/to/document.md --mode raw
"""
import argparse
import json

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen3:4b-instruct"
SUMMARIZE_MAX_CHARS = 40000  # must match tools.py's real constant


def build_tool_prompt(text: str, filename: str) -> str:
    """Mirrors tools.summarize_document()'s generic-summary prompt exactly."""
    excerpt = text[:SUMMARIZE_MAX_CHARS]
    return (f"Summarize the following document concisely, covering only what "
            f"actually matters — key points, decisions, or conclusions. Skip "
            f"boilerplate.\n\nDocument ({filename}):\n{excerpt}")


def build_raw_prompt(text: str) -> str:
    """The naive no-tool prompt: whole file, no cap, no extraction pipeline."""
    return f"Summarize the following document concisely.\n\nDocument:\n{text}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", required=True, help="path to the document to summarize")
    ap.add_argument("--mode", choices=["tool", "raw"], required=True,
                     help="'tool' = ATLAS's real capped/templated prompt; "
                          "'raw' = the whole file pasted in, no cap")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8", errors="replace") as f:
        text = f.read()
    filename = args.file.rsplit("/", 1)[-1]

    prompt = build_tool_prompt(text, filename) if args.mode == "tool" else build_raw_prompt(text)

    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "0",
    }, timeout=180)
    resp.raise_for_status()
    d = resp.json()

    print(json.dumps({
        "mode": args.mode,
        "file": args.file,
        "source_file_chars": len(text),
        "prompt_chars_sent": len(prompt),
        "prompt_eval_count": d.get("prompt_eval_count"),   # real input tokens, from Ollama
        "eval_count": d.get("eval_count"),                  # real output tokens, from Ollama
        "total_duration_s": round(d.get("total_duration", 0) / 1e9, 2) if d.get("total_duration") else None,
        "eval_duration_s": round(d.get("eval_duration", 0) / 1e9, 2) if d.get("eval_duration") else None,
    }, indent=2))


if __name__ == "__main__":
    main()