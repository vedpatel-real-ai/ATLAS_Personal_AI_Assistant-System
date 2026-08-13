"""
Deterministic matcher — checked BEFORE any model loads. If your phrasing
matches one of these, zero extra RAM and sub-second response. Add your own
common phrasings here as you notice what you actually say.

Contract: match_deterministic(text) returns a list of {"tool", "params"}
step dicts (possibly with more than one entry, for chained commands like
"open spotify and check battery and open vscode"), or None if nothing
matched — in which case main.py falls through to the router LLM, which
now also plans multi-step sequences and can additionally combine
fragments the regex here can't (see the play+video bail-out below).
"""
import re

# Looks for "search <query>" / "search for <query>" ANYWHERE in the
# original text, independent of where the site name appears — so "open
# youtube and search spaghetti recipe" and "please open youtube for me,
# search spaghetti recipe" both work, and plain "open youtube" (no search
# anywhere) still matches without needing an end-of-string anchor.
_SEARCH_RE = re.compile(r"search(?:\s+for)?\s+(.+?)\s*[.!?]*$", re.I)


def _extract_search(full_text: str):
    m = _SEARCH_RE.search(full_text)
    return m.group(1).strip() if m else None


def _url_with_optional_search(url: str):
    def extractor(m):
        params = {"url": url}
        query = _extract_search(m.string)
        if query:
            params["search"] = query
        return params
    return extractor


# (pattern, tool_name, params_extractor)
PATTERNS = [
    (re.compile(r"open vs\s*code|open vscode", re.I),
     "open_app", lambda m: {"app_name": "Visual Studio Code"}),

    (re.compile(r"open spotify", re.I),
     "open_app", lambda m: {"app_name": "Spotify"}),

    # Websites/web services — not installed macOS apps, so these go to
    # open_url, not open_app. Each pulls in a "search <query>" phrase from
    # anywhere in the sentence if present. Add more as you notice yourself
    # asking for them; each one caught here never has to guess through the
    # router LLM.
    (re.compile(r"open (?:the )?you\s*?tube|open yt\b", re.I),
     "open_url", _url_with_optional_search("https://youtube.com")),

    (re.compile(r"open google\b", re.I),
     "open_url", _url_with_optional_search("https://google.com")),

    (re.compile(r"open gmail\b", re.I),
     "open_url", lambda m: {"url": "https://mail.google.com"}),

    (re.compile(r"open github\b", re.I),
     "open_url", _url_with_optional_search("https://github.com")),

    (re.compile(r"battery", re.I),
     "get_battery", lambda m: {}),

    # Milestone 5.7 — Clipboard. Only read_clipboard gets a deterministic
    # pattern (zero params, same precedent as get_battery above);
    # write_clipboard takes free-form text and is left to the router LLM,
    # same posture as copy_file/move_file/rename_file's free-form paths.
    (re.compile(r"what'?s (?:on|in) (?:my )?clipboard|read (?:my )?clipboard|clipboard contents", re.I),
     "read_clipboard", lambda m: {}),

    (re.compile(r"stock price(?: of)? ([a-zA-Z]+)", re.I),
     "get_stock_price", lambda m: {"ticker": m.group(1)}),
]


_PLAY_HINT = re.compile(r"\bplay\b", re.I)
_VIDEO_HINT = re.compile(r"\bvideo\b", re.I)

# Splits on "and", "and then", "then", or a bare comma between clauses —
# used only for chain-splitting (see _try_chain). Deliberately NOT used
# for single-command parsing, since e.g. "search salt and pepper" needs
# "and" to survive intact inside the search query.
_CONNECTORS = re.compile(r"\s*,?\s+(?:and\s+then|and|then)\s+|\s*,\s*", re.I)


def _match_single(text: str):
    """Old single-command matcher: first PATTERNS entry that matches the
    given text, or (None, None)."""
    for pattern, tool, extractor in PATTERNS:
        m = pattern.search(text)
        if m:
            return tool, extractor(m)
    return None, None


def _try_chain(text: str):
    """Attempt to resolve text as a sequence of independently-matchable
    deterministic actions. Only returns a chain if EVERY segment matches
    on its own and there's more than one segment — otherwise returns None
    so the caller falls back to whole-text single matching, which is what
    preserves existing behavior for things like "open google and search
    best laptops" (a single action, not two)."""
    segments = [s.strip() for s in _CONNECTORS.split(text) if s.strip()]
    if len(segments) < 2:
        return None
    steps = []
    for seg in segments:
        tool, params = _match_single(seg)
        if tool is None:
            return None
        steps.append({"tool": tool, "params": params})
    return steps


def match_deterministic(text: str):
    """Returns a list of {"tool", "params"} steps, or None if nothing
    matched (falls through to the router LLM)."""
    # "search X and play Y's video" style commands need real semantic
    # understanding to combine multiple fragments (topic + creator name)
    # into one sensible query — fixed regex can't do that reliably, so
    # bail out early and let router.py's model handle the whole sentence
    # (it can plan multi-step sequences too, so this doesn't lose any
    # other actions mentioned in the same command).
    if _PLAY_HINT.search(text) and _VIDEO_HINT.search(text):
        return None

    chain = _try_chain(text)
    if chain:
        return chain

    tool, params = _match_single(text)
    if tool is None:
        return None
    return [{"tool": tool, "params": params}]