"""
Router — wakes the small model, asks it to classify intent into a tool call,
then lets it unload. This model's job is routing only, never writing prose.
temperature=0 because routing should be deterministic, not creative.

MODEL NAME IS A PLACEHOLDER: check `ollama list` / ollama.com/library at
setup time for whatever ~3-4B instruct model is current, and swap it in below.
"""

import inspect
import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
ROUTER_MODEL = "qwen3:4b-instruct"  # <-- verify/replace at install time

SYSTEM_PROMPT = """You are an intent router for a personal automation system.
A command can require ONE action or SEVERAL unrelated actions chained
together (e.g. "open spotify and check my battery and open vscode" is
THREE separate steps). Output ONLY valid JSON in this shape:
{{"steps": [{{"tool": "<tool_name>", "params": {{}}}}, ...], "needs_reasoning": false}}

Available tools (the "params" list shows the EXACT key names you must use —
copy them character-for-character, do not paraphrase or invent your own names):
{tools_desc}

Worked example — single action:
command: "open spotify"
{{"steps": [{{"tool": "open_app", "params": {{"app_name": "Spotify"}}}}], "needs_reasoning": false}}

Worked example — multiple chained actions:
command: "open spotify and check my battery and open vscode"
{{"steps": [
  {{"tool": "open_app", "params": {{"app_name": "Spotify"}}}},
  {{"tool": "get_battery", "params": {{}}}},
  {{"tool": "open_app", "params": {{"app_name": "Visual Studio Code"}}}}
], "needs_reasoning": false}}

Worked example — a compound phrase that is really ONE action, not two:
command: "search for spaghetti recipes and play the You Food Lab video"
{{"steps": [{{"tool": "play_youtube_video", "params": {{"query": "You Food Lab spaghetti recipe"}}}}], "needs_reasoning": false}}
(Use judgment: only split into multiple steps when the actions are
genuinely separate/unrelated. Don't split a single intent — like
searching for something and then playing the matching video — into
fake separate steps.)

Worked example — WRONG (do not do this — "app" is not a valid key for open_app):
{{"steps": [{{"tool": "open_app", "params": {{"app": "Spotify"}}}}], "needs_reasoning": false}}

If the request needs real reasoning, writing, summarizing, or code generation
(not just calling one or more tools with fixed arguments), set "needs_reasoning":
true and "steps": []. If the message is casual conversation, a greeting, or a
general question that doesn't clearly match any tool above, ALSO set
"needs_reasoning": true and "steps": [] — never return an empty "steps" list
with needs_reasoning false.
Respond with JSON only, nothing else."""


def _param_names(fn) -> list:
    """Exact keyword names the tool function accepts, in order."""
    return list(inspect.signature(fn).parameters.keys())


def _describe_tool(name: str, info: dict) -> str:
    params = _param_names(info["fn"])
    params_str = "{" + ", ".join(params) + "}" if params else "{} (no params)"
    return f"- {name}: {info['desc']}\n    params: {params_str}"


def _normalize_decision(decision: dict) -> dict:
    """Defensive: if the small model reverts to the old single-tool shape
    ({"tool": ..., "params": ...}) despite the prompt, upgrade it to the
    current {"steps": [...]} shape instead of breaking callers."""
    if "steps" in decision:
        return decision
    if decision.get("tool"):
        return {"steps": [{"tool": decision["tool"], "params": decision.get("params", {})}],
                "needs_reasoning": decision.get("needs_reasoning", False)}
    return {"steps": [], "needs_reasoning": decision.get("needs_reasoning", True)}


def route(text: str, tools: dict, keep_alive: str = "0") -> dict:
    tools_desc = "\n".join(_describe_tool(name, info) for name, info in tools.items())
    prompt = SYSTEM_PROMPT.format(tools_desc=tools_desc)
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": ROUTER_MODEL,
            "system": prompt,
            "prompt": text,
            "stream": False,
            "keep_alive": keep_alive,
            "format": "json",
            "options": {"temperature": 0},
        },
        timeout=30,
    )
    resp.raise_for_status()
    decision = json.loads(resp.json()["response"])
    return _normalize_decision(decision)