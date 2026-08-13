"""
Decision Engine — formalizes the escalation logic described in spec §3.1
into five explicit levels (L0–L4). This is Milestone 0.5.

This module does NOT change what matcher.py / router.py / run_big_model
actually do — main.py's matcher-first, router-second, big-model-last
chain is still exactly the mechanism that runs. What this module adds:

1. A name and a label (L0–L4) for each step of that chain, so the level
   that handled a request can be logged and later reported on (spec §9.1
   / §10 Milestone 0.5 checkpoint: "verify most traffic sits at L0–L2").
2. A non-text signal the old implicit chain couldn't see at all: whether
   an attachment is present, and how big/what type it is. Per spec §3.1,
   "a ZIP attached + project analysis intent should route straight to
   L4, skipping the 4B's classification step entirely — regardless of
   how the sentence is worded." No ZIP-analysis tool exists yet
   (Milestone 1 is not started, per IMPLEMENTATION_STATE.md), so today
   this still just means "hand the raw text to the reasoning model
   directly" — but the routing decision itself is real, tested, and
   ready for Milestone 1 to attach a real tool to.

Level reference (spec §3.1):
  L0 — deterministic regex match (matcher.py). ~0 cost, <10ms.
  L1 — a tool call whose own output IS the answer, no LLM needed to
       phrase the reply. This system has no Response Generator yet (see
       spec §3.3) — every route already returns raw tool output
       un-phrased, so L1 has no separate code path from L0/L2 today.
       The label is kept anyway so it's ready the day a Response
       Generator exists, rather than retrofitted then.
  L2 — 4B router model + a single tool call.
  L3 — 4B router model planning a short flat chain of tool calls.
  L4 — reasoning model (today: CODER_MODEL, the documented qwen3:4b
       placeholder in main.py — swapping it for a real 9B model is
       Milestone 5, not this one).

Governing rule this module exists to serve (spec §3.1, restated):
"Never wake a more expensive component if a cheaper component can
produce an acceptable result." Concretely: L0 is tried before L1–L3 are
even possible (matcher.py runs first); the router model (L2/L3) is only
woken if the matcher misses; the reasoning model (L4) is only woken if
the router says it genuinely needs to be, OR a non-text signal
(attachment) already tells us it will.
"""
from enum import Enum
from typing import Optional


class Level(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


# Attachments at/above this size, or of these types, are treated as
# strong enough evidence of a project-analysis-class request that we
# skip router-model classification entirely and go straight to L4 —
# per spec §3.1. Deliberately conservative/cheap: a file-size check and
# a type check, not a content inspection.
ATTACHMENT_FORCE_L4_TYPES = {"zip", "pdf"}
ATTACHMENT_FORCE_L4_MIN_MB = 1.0


def attachment_forces_l4(attachments: Optional[list]) -> bool:
    """True if a non-text signal alone is enough to route straight to L4,
    without waiting on router-model text classification."""
    for a in attachments or []:
        if a.get("type") in ATTACHMENT_FORCE_L4_TYPES:
            return True
        if (a.get("size_mb") or 0) >= ATTACHMENT_FORCE_L4_MIN_MB:
            return True
    return False


def classify_deterministic() -> Level:
    """Called once matcher.py has already found a match. There's only one
    outcome here today (see L1's docstring note above for why)."""
    return Level.L0


def classify_router_decision(decision: dict) -> Level:
    """Called once router.py has returned a decision dict — i.e. after a
    router-model call has already happened. This only labels the result;
    it doesn't change what main.py does with it."""
    if decision.get("needs_reasoning"):
        return Level.L4
    steps = decision.get("steps") or []
    return Level.L3 if len(steps) > 1 else Level.L2


def level_value(level: Level) -> str:
    """Plain string for logging — storage.log_event stores level as TEXT."""
    return level.value