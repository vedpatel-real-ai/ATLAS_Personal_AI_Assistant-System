"""
Tests for decision_engine.py (Milestone 0.5). Covers the L0-L4 labeling
logic and the attachment-based L4 short-circuit — the one genuinely new
routing capability this milestone adds (spec §3.1).
"""
import decision_engine
from decision_engine import Level


def test_classify_deterministic_is_always_l0():
    assert decision_engine.classify_deterministic() == Level.L0


def test_classify_router_decision_single_step_is_l2():
    decision = {"steps": [{"tool": "get_battery", "params": {}}], "needs_reasoning": False}
    assert decision_engine.classify_router_decision(decision) == Level.L2


def test_classify_router_decision_multi_step_is_l3():
    decision = {"steps": [
        {"tool": "open_app", "params": {"app_name": "Spotify"}},
        {"tool": "get_battery", "params": {}},
    ], "needs_reasoning": False}
    assert decision_engine.classify_router_decision(decision) == Level.L3


def test_classify_router_decision_needs_reasoning_is_l4_regardless_of_steps():
    decision = {"steps": [], "needs_reasoning": True}
    assert decision_engine.classify_router_decision(decision) == Level.L4


def test_classify_router_decision_needs_reasoning_wins_even_with_steps_present():
    # Defensive: needs_reasoning should always win over step count, even if
    # the router model inconsistently returned both.
    decision = {"steps": [{"tool": "get_battery", "params": {}}], "needs_reasoning": True}
    assert decision_engine.classify_router_decision(decision) == Level.L4


def test_no_attachments_does_not_force_l4():
    assert decision_engine.attachment_forces_l4([]) is False
    assert decision_engine.attachment_forces_l4(None) is False


def test_small_generic_attachment_does_not_force_l4():
    assert decision_engine.attachment_forces_l4([{"type": "txt", "size_mb": 0.01}]) is False


def test_zip_attachment_forces_l4_regardless_of_size():
    assert decision_engine.attachment_forces_l4([{"type": "zip", "size_mb": 0.01}]) is True


def test_pdf_attachment_forces_l4_regardless_of_size():
    assert decision_engine.attachment_forces_l4([{"type": "pdf", "size_mb": 0.01}]) is True


def test_large_attachment_of_any_type_forces_l4():
    assert decision_engine.attachment_forces_l4([{"type": "txt", "size_mb": 5.0}]) is True


def test_attachment_at_exact_threshold_forces_l4():
    assert decision_engine.attachment_forces_l4(
        [{"type": "other", "size_mb": decision_engine.ATTACHMENT_FORCE_L4_MIN_MB}]
    ) is True


def test_level_value_returns_plain_string():
    assert decision_engine.level_value(Level.L2) == "L2"