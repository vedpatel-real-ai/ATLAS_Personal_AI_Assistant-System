"""
Regression coverage for normalize_params() (main.py). This is the exact
class of bug documented in FIX_NOTES.md: the router LLM guessed "app"
instead of "app_name" for open_app(), and nothing caught it before it hit
open_app(**params) and crashed with "unexpected keyword argument".

These tests exist so that bug — or a sibling of it in a different tool —
fails a test run instead of shipping silently again.
"""

from main import normalize_params
from tools import TOOLS


def test_exact_param_name_passes_through_untouched():
    fn = TOOLS["open_app"]["fn"]
    assert normalize_params(fn, {"app_name": "Spotify"}) == {"app_name": "Spotify"}


def test_known_alias_is_remapped_app_to_app_name():
    """This is the literal FIX_NOTES.md bug: {"app": "YouTube"} must land
    on open_app's real parameter, app_name."""
    fn = TOOLS["open_app"]["fn"]
    result = normalize_params(fn, {"app": "YouTube"})
    assert result == {"app_name": "YouTube"}


def test_known_alias_project_path_variants():
    fn = TOOLS["open_project"]["fn"]
    for alias in ("path", "folder", "project", "dir", "directory", "folder_path"):
        result = normalize_params(fn, {alias: "/tmp/some-project"})
        assert result == {
            "project_path": "/tmp/some-project"
        }, f"alias '{alias}' failed to normalize"


def test_single_leftover_fallback_covers_unlisted_aliases():
    """If exactly one required param is missing and exactly one unexpected
    kwarg is left over, they're assumed to be the same thing — this is the
    safety net for aliases nobody has thought to list yet."""
    fn = TOOLS["get_stock_price"]["fn"]
    result = normalize_params(
        fn, {"symbl": "AAPL"}
    )  # deliberate typo, not in PARAM_ALIASES
    assert result == {"ticker": "AAPL"}


def test_no_params_needed_returns_empty():
    fn = TOOLS["get_battery"]["fn"]
    assert normalize_params(fn, {}) == {}


def test_index_project_accepts_project_path_aliases():
    """Same class of alias coverage as open_project — index_project takes
    the identical param name, so it should benefit from the same aliases
    without needing its own PARAM_ALIASES entries."""
    fn = TOOLS["index_project"]["fn"]
    for alias in ("path", "folder", "project", "dir", "directory"):
        result = normalize_params(fn, {alias: "/tmp/some-project"})
        assert result == {
            "project_path": "/tmp/some-project"
        }, f"alias '{alias}' failed to normalize"


def test_read_zip_falls_back_to_single_leftover_for_zip_path():
    """read_zip's real param is zip_path, which isn't in PARAM_ALIASES at
    all — this exercises the single-missing/single-leftover safety net
    rather than an explicit alias entry."""
    fn = TOOLS["read_zip"]["fn"]
    result = normalize_params(fn, {"path": "/tmp/project.zip"})
    assert result == {"zip_path": "/tmp/project.zip"}


def test_git_query_command_name_alias():
    fn = TOOLS["git_query"]["fn"]
    result = normalize_params(fn, {"command_id": "git_log", "cwd": "/tmp/repo"})
    assert result == {"command_name": "git_log", "cwd": "/tmp/repo"}


def test_summarize_document_accepts_exact_path_and_file_aliases():
    """summarize_document's real param is literally `path` (chosen on purpose
    to match delete_file's convention), so an exact 'path' kwarg needs no
    aliasing, and file/file_path/filepath — which PARAM_ALIASES already maps
    onto 'path' — work for free too."""
    fn = TOOLS["summarize_document"]["fn"]
    assert normalize_params(fn, {"path": "/tmp/doc.md"}) == {"path": "/tmp/doc.md"}
    for alias in ("file", "file_path", "filepath"):
        result = normalize_params(fn, {alias: "/tmp/doc.md"})
        assert result == {"path": "/tmp/doc.md"}, f"alias '{alias}' failed to normalize"


def test_summarize_youtube_video_accepts_exact_url_and_common_aliases():
    """summarize_youtube_video's real param is `url`; video_url/youtube_url/
    video/link are common router guesses mapped onto it via PARAM_ALIASES
    (the same explicit-entry approach summarize_document's file aliases use,
    not the single-leftover fallback — that fallback doesn't apply here
    since `question`'s default still counts it as 'missing')."""
    fn = TOOLS["summarize_youtube_video"]["fn"]
    assert normalize_params(fn, {"url": "https://youtu.be/abc"}) == {"url": "https://youtu.be/abc"}
    for alias in ("video_url", "youtube_url", "video", "link"):
        result = normalize_params(fn, {alias: "https://youtu.be/abc"})
        assert result == {"url": "https://youtu.be/abc"}, f"alias '{alias}' failed to normalize"


def test_ambiguous_extra_params_are_dropped_not_guessed():
    """Two unexpected kwargs with one missing required param is genuinely
    ambiguous — normalize_params should NOT guess, just drop what it can't
    place, leaving execute_tool's exception handling to catch the resulting
    missing-argument TypeError."""
    fn = TOOLS["open_app"]["fn"]
    result = normalize_params(fn, {"foo": "x", "bar": "y"})
    assert result == {}


def test_copy_file_and_move_file_accept_src_dst_aliases():
    """copy_file/move_file's real params are `source`/`destination`;
    src/dst (and a few other common router guesses) are mapped onto them
    via PARAM_ALIASES, same explicit-entry approach as the file/path and
    url aliases above."""
    for tool_name in ("copy_file", "move_file"):
        fn = TOOLS[tool_name]["fn"]
        assert normalize_params(fn, {"source": "/tmp/a.txt", "destination": "/tmp/b.txt"}) == {
            "source": "/tmp/a.txt", "destination": "/tmp/b.txt"}
        result = normalize_params(fn, {"src": "/tmp/a.txt", "dst": "/tmp/b.txt"})
        assert result == {"source": "/tmp/a.txt", "destination": "/tmp/b.txt"}, tool_name


def test_rename_file_accepts_filename_alias_for_new_name():
    fn = TOOLS["rename_file"]["fn"]
    assert normalize_params(fn, {"path": "/tmp/a.txt", "new_name": "b.txt"}) == {
        "path": "/tmp/a.txt", "new_name": "b.txt"}
    result = normalize_params(fn, {"path": "/tmp/a.txt", "filename": "b.txt"})
    assert result == {"path": "/tmp/a.txt", "new_name": "b.txt"}


def test_dependency_graph_accepts_focus_file_aliases():
    """dependency_graph's real per-file param is `focus_file`; a handful
    of natural-language guesses (target_file, specific_file, for_file,
    which_file, about_file) are mapped onto it. Deliberately does NOT
    include a bare `file` alias here — that key already means `path` for
    read_file/rename_file/delete_file elsewhere, and dependency_graph has
    no `path` param, so a `file` alias would silently vanish rather than
    resolve; the router is shown `focus_file` directly via
    inspect.signature and is expected to use it as-is."""
    fn = TOOLS["dependency_graph"]["fn"]
    for alias in ("target_file", "specific_file", "for_file", "which_file", "about_file"):
        result = normalize_params(fn, {"project_path": "/tmp/proj", alias: "storage.py"})
        assert result == {"project_path": "/tmp/proj", "focus_file": "storage.py"}, alias