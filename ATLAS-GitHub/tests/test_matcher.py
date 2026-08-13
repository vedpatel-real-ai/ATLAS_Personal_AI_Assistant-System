from matcher import match_deterministic


def test_open_vscode_single_match():
    steps = match_deterministic("open vscode")
    assert steps == [{"tool": "open_app", "params": {"app_name": "Visual Studio Code"}}]


def test_open_spotify_single_match():
    steps = match_deterministic("open spotify")
    assert steps == [{"tool": "open_app", "params": {"app_name": "Spotify"}}]


def test_battery_single_match():
    steps = match_deterministic("what's my battery at")
    assert steps == [{"tool": "get_battery", "params": {}}]


def test_read_clipboard_single_match():
    steps = match_deterministic("what's on my clipboard")
    assert steps == [{"tool": "read_clipboard", "params": {}}]


def test_no_match_returns_none():
    assert match_deterministic("write me a haiku about the ocean") is None


def test_chained_commands_split_into_multiple_steps():
    steps = match_deterministic("open spotify and check my battery and open vscode")
    assert steps == [
        {"tool": "open_app", "params": {"app_name": "Spotify"}},
        {"tool": "get_battery", "params": {}},
        {"tool": "open_app", "params": {"app_name": "Visual Studio Code"}},
    ]


def test_open_youtube_with_embedded_search():
    steps = match_deterministic("open youtube and search spaghetti recipe")
    assert steps == [
        {
            "tool": "open_url",
            "params": {"url": "https://youtube.com", "search": "spaghetti recipe"},
        }
    ]


def test_search_containing_and_is_not_split():
    """'search salt and pepper' must NOT be chain-split on the word 'and' —
    it's one search query, not two actions."""
    steps = match_deterministic("open google and search salt and pepper")
    assert len(steps) == 1
    assert steps[0]["tool"] == "open_url"
    assert steps[0]["params"]["search"] == "salt and pepper"


def test_play_video_bails_out_to_router():
    """Commands combining 'play' + 'video' need real semantic understanding
    (topic + creator name) that fixed regex can't reliably do — matcher
    must return None so main.py falls through to the router LLM."""
    assert (
        match_deterministic("search spaghetti and play the You Food Lab video") is None
    )


def test_stock_price_extracts_ticker():
    steps = match_deterministic("stock price of AAPL")
    assert steps == [{"tool": "get_stock_price", "params": {"ticker": "AAPL"}}]


def test_partial_chain_falls_back_to_whole_text_match():
    """If not every segment of a comma/and-split resolves deterministically,
    _try_chain must return None so the caller retries the WHOLE text as one
    single-command match, rather than silently dropping the unmatched part."""
    steps = match_deterministic("open google and search best laptops")
    assert len(steps) == 1
    assert steps[0]["tool"] == "open_url"
