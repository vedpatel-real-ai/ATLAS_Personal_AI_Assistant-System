# ATLAS — Implementation State

> Historical milestone notes from July 2026. For the current implementation and dated measurements see the [verification guide](verification/README.md).

**Last updated:** 2026-07-12 (this session)
**Spec revision this tracks against:** v4

---

## How this file works

Before touching code, validate the uploaded zip against the state below —
flag discrepancies before making changes. After finishing work, update the
"Milestone status" table and "Session log" so the next session doesn't have
to re-derive what happened.

---

## Milestone status

| Milestone | Status | Notes |
|---|---|---|
| **0 — Foundation** | ✅ Done (this session) | Plaintext API key removed from `start.sh`; SQLite log migration; server-side confirmation tokens; test harness. See below. |
| **0.5 — Decision Engine + Request Context** | ⬜ Not started | Escalation logic today is still the original implicit `matcher.py` → `router.py` → `needs_reasoning` chain. No L0–L4 labeling, no attachment-aware routing, no `RequestContext` object. Recommended next milestone. |
| **1 — Project understanding (ZIP/git/code search)** | ⬜ Not started | Depends on 0.5 (attachment-aware routing should exist before a ZIP-analysis tool does). |
| **2 — Conversation/project memory** | ⬜ Not started | No `memory.py`, no SQLite `conversations`/`projects`/`preferences` tables yet. |
| **3 — Knowledge Companion** | ⬜ Not started | |
| **4 — Task macros / planner** | ⬜ Not started | |
| **5 — 9B model evaluation** | ⬜ Not started | `CODER_MODEL` is still the documented placeholder (`qwen3:4b-instruct`) in `main.py`. |
| **6 — Mobile companion** | ⬜ Not started | |
| **7 — Adaptive learning** | ⬜ Not started (optional) | |

---

## What Milestone 0 actually changed (this session)

Starting point matched the spec's "current state" description exactly:
plaintext `ASSISTANT_API_KEY` hardcoded in `start.sh`, JSONL-only logging
with no rotation/query surface, `confirmed: bool` as the *only* proof of
user approval for dangerous-tier tools (client-settable, no server-side
check), and no `tests/` directory at all.

| File | Change |
|---|---|
| `storage.py` | **New.** SQLite `logs` table (queryable — see `query_recent_failures()`) written alongside the existing `assistant_log.jsonl` (kept as a cheap live-tail stream, per spec §9). SQLite `confirmations` table backing one-time, 5-minute-TTL, steps-hash-bound confirmation tokens. |
| `main.py` | Logging now goes through `storage.log_event()`. `Command` model gained `confirm_token: Optional[str]`. The confirm-required branch now issues a server-side token (`storage.create_confirmation`) instead of trusting `confirmed: bool` alone; the confirmed branch verifies and consumes that token (`storage.verify_and_consume_confirmation`) before executing any `caution`/`dangerous` step. Rejects with a clear reason (expired / already used / steps mismatch / missing) instead of silently executing. |
| `chat.html` | Threads `confirm_token` from the `confirm_required` response through to the confirmed resend (`callAssistant`/`send`/the Confirm button's `onclick`). No other UI changes. |
| `start.sh` | No longer hardcodes the API key. Generates a random key into `.secrets/api_key` (chmod 600) on first run, loads it on every run after. Prints the key location once so it can be pasted into `chat.html`'s key box. |
| `.gitignore` | **New.** Ignores `.secrets/`, `venv/`, `__pycache__/`, `logs/`, `assistant_log.jsonl`, `atlas.db`. |
| `requirements.txt` | Added `pytest`. |
| `tests/` | **New.** `conftest.py`, `test_matcher.py` (10 cases incl. the play+video bail-out and the "search salt and pepper" chain-split guard), `test_normalize_params.py` (6 cases, including the literal `FIX_NOTES.md` `app`→`app_name` regression), `test_router.py` (6 cases, including a prompt-content check that `app_name`/`project_path`/`ticker` actually reach the system prompt), `test_storage_confirmations.py` (6 cases covering reuse, mismatch, expiry, missing/unknown token). **28/28 passing** as of this session (verified: `ASSISTANT_API_KEY=test python3 -m pytest tests/ -v`). |

**Not changed / deliberately out of scope for Milestone 0:**
- `matcher.py`, `tools.py`, `router.py`'s actual routing logic — untouched, no behavior change beyond what the tests above pin down.
- `voice_to_text.py` — not in this bundle; separate, currently-broken work per the spec.
- The confirmation-token TTL (5 min) and rate limit (20/60s) are unchanged defaults; revisit only if real usage shows they're wrong.

**Verification performed this session** (see Session log): booted the actual
FastAPI app in-process (`TestClient`) and exercised the full confirm flow —
safe command executes immediately; dangerous command returns a token; wrong
token is rejected; correct token executes and is then rejected on replay;
missing API key returns 401. All matched expected behavior.

---

## Performance budget check (§9.1)

Milestone 0 adds: one SQLite file (`atlas.db`, disk-backed, negligible RAM),
no new resident processes, no new background wakeups, no change to
model-loading behavior. **Inside budget.**

---

## Recommended next milestone

**Milestone 0.5 — Decision Engine + Request Context object.**

Rationale: it's next in the spec's own implementation order (§11), it's
cheap and mostly rule-based (no new model residency), and — per the spec's
governing rule ("never wake a more expensive component if a cheaper one
suffices") — it's what keeps every tool built in Milestone 1 onward routed
correctly from the moment it exists, rather than retrofitting routing logic
later. Concretely this means: formalizing today's implicit
`matcher.py → router.py → needs_reasoning` chain into the explicit L0–L4
levels from spec §3.1, introducing the `RequestContext` object from §3.3,
and adding non-text signals (attachment presence/size) to the escalation
decision so a future ZIP-analysis tool can route straight to L4 without
waiting on wording alone.

Not recommending Milestone 1 yet, even though it's the first "real skill"
milestone, because the spec explicitly sequences 0.5 before it for that
reason.

---

## Session log

### 2026-07-12 — Milestone 0 (this session)
- Reviewed the uploaded zip against the spec's "current state" section —
  matched exactly (plaintext key, JSONL-only logs, client-set `confirmed`
  bool, no tests, no `IMPLEMENTATION_STATE.md`).
- No prior `IMPLEMENTATION_STATE.md` existed — created this file.
- Implemented Milestone 0 in full: see table above.
- Ran the new test suite (28 passed) and a manual end-to-end check of the
  confirmation-token flow via `TestClient` — both clean.
- Files modified: `main.py`, `chat.html`, `start.sh`, `requirements.txt`.
- Files added: `storage.py`, `.gitignore`, `tests/conftest.py`,
  `tests/test_matcher.py`, `tests/test_normalize_params.py`,
  `tests/test_router.py`, `tests/test_storage_confirmations.py`,
  `IMPLEMENTATION_STATE.md`.
- Files NOT touched: `matcher.py`, `tools.py`, `router.py`, `stop.sh`,
  `status.sh`, `*.applescript`, `README.md`, `FIX_NOTES.md`,
  `PATCH_main_py.md`.
