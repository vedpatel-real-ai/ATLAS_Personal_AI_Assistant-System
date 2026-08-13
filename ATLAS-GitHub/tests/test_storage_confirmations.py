"""
Tests for the server-side confirmation-token gap fix (ATLAS spec §1.3):
previously `confirmed: bool` was entirely client-set, so a compromised or
buggy client could self-confirm a dangerous-tier action. These tests check
that a token only works for the exact steps it was issued for, can't be
reused, and is rejected once expired.
"""
import time

import storage


def test_valid_token_for_matching_steps_succeeds():
    steps = [{"tool": "delete_file", "params": {"path": "/tmp/x"}}]
    token = storage.create_confirmation(steps)
    ok, reason = storage.verify_and_consume_confirmation(token, steps)
    assert ok is True
    assert reason == "ok"


def test_token_cannot_be_reused():
    steps = [{"tool": "delete_file", "params": {"path": "/tmp/x"}}]
    token = storage.create_confirmation(steps)
    storage.verify_and_consume_confirmation(token, steps)
    ok, reason = storage.verify_and_consume_confirmation(token, steps)
    assert ok is False
    assert "already used" in reason


def test_token_rejected_for_different_steps():
    """The core fix: a token issued for one action can't be silently
    reused/escalated to confirm a DIFFERENT action."""
    original_steps = [{"tool": "delete_file", "params": {"path": "/tmp/harmless.txt"}}]
    token = storage.create_confirmation(original_steps)

    escalated_steps = [{"tool": "run_shell_command", "params": {"command_name": "docker_ps"}}]
    ok, reason = storage.verify_and_consume_confirmation(token, escalated_steps)
    assert ok is False
    assert "does not match" in reason


def test_missing_token_rejected():
    steps = [{"tool": "delete_file", "params": {"path": "/tmp/x"}}]
    ok, reason = storage.verify_and_consume_confirmation(None, steps)
    assert ok is False
    assert "missing" in reason


def test_unknown_token_rejected():
    steps = [{"tool": "delete_file", "params": {"path": "/tmp/x"}}]
    ok, reason = storage.verify_and_consume_confirmation("not-a-real-token", steps)
    assert ok is False
    assert "unknown" in reason


def test_expired_token_rejected(monkeypatch):
    steps = [{"tool": "delete_file", "params": {"path": "/tmp/x"}}]
    token = storage.create_confirmation(steps)
    # Fast-forward past the TTL instead of sleeping in the test suite.
    future = time.time() + storage.CONFIRMATION_TTL_SECONDS + 1
    monkeypatch.setattr(storage.time, "time", lambda: future)
    ok, reason = storage.verify_and_consume_confirmation(token, steps)
    assert ok is False
    assert "expired" in reason
