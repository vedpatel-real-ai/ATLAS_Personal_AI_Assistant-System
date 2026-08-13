"""
Request Context object — spec §3.3 (Milestone 0.5).

A single shared structure, built once per request right after the Client
Communication Layer, and threaded through every downstream component
(today: the Decision Engine; later: the Context Manager and Planner once
Milestones 2 and 4 exist) instead of each component re-deriving its own
ad hoc view of "what's going on right now".

Milestone 2 wires the memory-backed fields below (current_project,
current_directory, conversation_history, preferences) to real,
SQLite-backed memory via memory.recall_context() — see memory.py's
module docstring for the Context Manager design (spec §3.3: "the
Context Manager populates current_project/conversation_history/
preferences"). `current_app` stays an unpopulated placeholder: no macOS
active-app-tracking tool exists yet to populate it from, and inventing
one wasn't asked for by this milestone.

Any component that needs "what's going on right now" should read from a
RequestContext instance rather than querying storage/memory
independently — per IMPLEMENTATION_STATE.md's Milestone 0.5 checkpoint,
a component that bypasses this is a bug, not a style choice.
"""
from dataclasses import dataclass, field
from typing import Optional

import memory



@dataclass
class RequestContext:
    raw_text: str

    # Memory-backed — placeholders until Milestone 2 wires up a real
    # Context Manager. Always empty/None today; that's expected, not a bug.
    current_project: Optional[str] = None
    current_directory: Optional[str] = None
    current_app: Optional[str] = None
    conversation_history: list = field(default_factory=list)
    preferences: dict = field(default_factory=dict)

    # Request-scoped — populated within this request's lifecycle.
    attachments: list = field(default_factory=list)  # e.g. [{"type": "zip", "size_mb": 4.2}]
    estimated_complexity: Optional[str] = None  # filled in by the Decision Engine (an L0–L4 label)
    tool_candidates: list = field(default_factory=list)  # reserved for the Planner (Milestone 4)

    def as_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "current_project": self.current_project,
            "current_directory": self.current_directory,
            "current_app": self.current_app,
            "conversation_history": self.conversation_history,
            "preferences": self.preferences,
            "attachments": self.attachments,
            "estimated_complexity": self.estimated_complexity,
            "tool_candidates": self.tool_candidates,
        }


def build_request_context(raw_text: str, attachments: Optional[list] = None) -> RequestContext:
    """Builds the RequestContext for a single incoming request.

    Memory-backed fields are populated from memory.recall_context() — see
    the module docstring. This function is intentionally the ONLY place a
    RequestContext gets constructed, so it's also the one call site
    memory-backed fields flow through, rather than several to hunt down.
    """
    recalled = memory.recall_context()
    return RequestContext(
        raw_text=raw_text,
        attachments=list(attachments or []),
        current_project=recalled.get("current_project"),
        current_directory=recalled.get("current_directory"),
        conversation_history=recalled.get("conversation_history", []),
        preferences=recalled.get("preferences", {}),
    )