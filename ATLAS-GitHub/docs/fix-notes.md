# Fix: open_app() got an unexpected keyword argument 'app'

## Root cause (confirmed from your assistant_log.jsonl)
"can you open YouTube for me" -> router_llm -> params: {"app": "YouTube"} -> success: false

Your deterministic matcher (matcher.py) hardcodes the correct key
`app_name` for "open vscode" / "open spotify", so those always worked.
Anything else ("open YouTube", "open Chrome", etc.) falls through to the
small router model (qwen3:4b), which was never told the exact parameter
names each tool expects — only a plain-English description. So it guessed
"app" instead of "app_name", and open_app(**{"app": ...}) crashed.

This wasn't unique to open_app — every tool call routed through the LLM
was exposed to the same risk of key-name drift.

## What changed

**router.py** — the prompt sent to the small model now lists the exact
param names for every tool (e.g. `open_app params: {app_name}`), plus a
worked right/wrong example, so the model has much less room to guess.

**main.py** — added a `normalize_params()` safety net that runs right
before every tool call. If the LLM still drifts (e.g. sends "app" instead
of "app_name", or "path" instead of "project_path"), it gets silently
remapped to the real parameter name via an alias table, with a fallback
for the single-leftover-param case. This protects every tool in the
registry, not just open_app, against future param-naming drift from the
router model.

## What to do
1. Replace `main.py`, `router.py`, and `tools.py` in `~/Desktop/files/`
   with these versions (same folder start.sh already cd's into).
2. Delete the old `__pycache__` folder in that same directory so Python
   doesn't load stale bytecode:
   `rm -rf ~/Desktop/files/__pycache__`
3. Restart the assistant: `./stop.sh` then `./start.sh`
   (or however start_ai.applescript/status.sh drive it).
4. Test with something that previously broke it, e.g. "open YouTube" or
   "open Chrome" (not the hardcoded vscode/spotify phrases) — check
   assistant_log.jsonl to confirm `"success": true`.

No changes needed to tools.py itself — open_app's signature (`app_name`)
was correct all along; the callers just weren't matching it.
