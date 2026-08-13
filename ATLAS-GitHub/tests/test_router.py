"""
router.py tests. Network calls to Ollama are mocked out — these tests
check (a) that the prompt sent to the router model actually contains the
EXACT parameter names for every tool (the root cause fix for the
FIX_NOTES.md "app" vs "app_name" bug lives in the prompt, not just in
main.py's normalize_params safety net), and (b) that route()'s output
normalization handles both the current and legacy response shapes.
"""
import json
from types import SimpleNamespace

import router
from tools import TOOLS


def test_describe_tool_uses_exact_param_names():
    desc = router._describe_tool("open_app", TOOLS["open_app"])
    assert "app_name" in desc
    assert "params: {app_name}" in desc


def test_system_prompt_lists_exact_params_for_every_tool():
    tools_desc = "\n".join(router._describe_tool(name, info) for name, info in TOOLS.items())
    prompt = router.SYSTEM_PROMPT.format(tools_desc=tools_desc)
    # Spot-check a few tools whose param names have historically drifted.
    assert "app_name" in prompt
    assert "project_path" in prompt
    assert "ticker" in prompt
    # The worked "wrong" example should still show the bad key as a warning,
    # not as something the model should copy — just confirm it's present so
    # a future edit doesn't accidentally delete the cautionary example.
    assert '"app": "Spotify"' in prompt


def test_normalize_decision_passes_through_current_shape():
    decision = {"steps": [{"tool": "get_battery", "params": {}}], "needs_reasoning": False}
    assert router._normalize_decision(decision) == decision


def test_normalize_decision_upgrades_legacy_single_tool_shape():
    legacy = {"tool": "open_app", "params": {"app_name": "Spotify"}, "needs_reasoning": False}
    result = router._normalize_decision(legacy)
    assert result == {"steps": [{"tool": "open_app", "params": {"app_name": "Spotify"}}],
                       "needs_reasoning": False}


def test_normalize_decision_defaults_to_needs_reasoning_on_empty():
    result = router._normalize_decision({})
    assert result["steps"] == []
    assert result["needs_reasoning"] is True


def test_route_calls_ollama_with_json_mode_and_zero_temperature(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"response": '{"steps": [{"tool": "get_battery", "params": {}}], "needs_reasoning": false}'},
        )

    monkeypatch.setattr(router.requests, "post", fake_post)
    result = router.route("what's my battery", TOOLS)

    assert result == {"steps": [{"tool": "get_battery", "params": {}}], "needs_reasoning": False}
    assert captured["json"]["options"]["temperature"] == 0
    assert captured["json"]["format"] == "json"
    assert "app_name" in captured["json"]["system"]  # exact param names reached the actual prompt sent
