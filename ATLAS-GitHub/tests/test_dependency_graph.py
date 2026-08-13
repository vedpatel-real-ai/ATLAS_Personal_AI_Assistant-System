"""
Tests for Milestone 5.7's dependency_graph tool: Python (via ast) and
JS/TS (via regex) internal-import resolution, most-depended-on ranking,
and circular-dependency detection. Uses pytest's tmp_path fixture to build
small real file trees, same convention as test_index_project.py — import
resolution is exactly the kind of logic that's easy to get subtly wrong
with a mock.
"""

import tools


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- basic validation ----------------------------------------------------


def test_path_not_found_returns_error():
    result = tools.dependency_graph("/definitely/does/not/exist/anywhere")
    assert result["status"] == "error"


def test_path_that_is_a_file_returns_error(tmp_path):
    f = tmp_path / "not_a_dir.py"
    f.write_text("x = 1")
    result = tools.dependency_graph(str(f))
    assert result["status"] == "error"


def test_invalid_file_type_returns_error(tmp_path):
    result = tools.dependency_graph(str(tmp_path), file_type="rust")
    assert result["status"] == "error"


# --- Python resolution -----------------------------------------------------


def test_resolves_absolute_dotted_import_within_project(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "utils.py", "x = 1")
    _write(tmp_path / "pkg" / "main.py", "import pkg.utils\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["status"] == "ok"
    assert result["files_scanned"] == 3
    assert result["total_internal_dependency_edges"] == 1


def test_resolves_from_import_of_a_submodule(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "")
    _write(tmp_path / "pkg" / "b.py", "from pkg import a\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["total_internal_dependency_edges"] == 1
    assert result["most_depended_on"][0]["file"] == "pkg/a.py"


def test_resolves_relative_from_import(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "")
    _write(tmp_path / "pkg" / "b.py", "from . import a\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["total_internal_dependency_edges"] == 1


def test_external_package_import_is_not_resolved_into_an_edge(tmp_path):
    _write(
        tmp_path / "main.py",
        "import os\nimport numpy as np\nfrom collections import OrderedDict\n",
    )

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["status"] == "ok"
    assert result["total_internal_dependency_edges"] == 0


def test_syntax_error_in_one_file_does_not_crash_the_whole_scan(tmp_path):
    _write(tmp_path / "good.py", "import os\n")
    _write(tmp_path / "bad.py", "def broken(:\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["status"] == "ok"
    assert result["files_scanned"] == 2


# --- JS/TS resolution ------------------------------------------------------


def test_resolves_js_relative_import_without_extension(tmp_path):
    _write(tmp_path / "src" / "utils.js", "")
    _write(tmp_path / "src" / "index.js", "import { f } from './utils';\n")

    result = tools.dependency_graph(str(tmp_path), file_type="js")
    assert result["total_internal_dependency_edges"] == 1


def test_resolves_js_require_call(tmp_path):
    _write(tmp_path / "utils.js", "")
    _write(tmp_path / "index.js", "const u = require('./utils');\n")

    result = tools.dependency_graph(str(tmp_path), file_type="js")
    assert result["total_internal_dependency_edges"] == 1


def test_resolves_ts_relative_import_across_extensions(tmp_path):
    _write(tmp_path / "utils.ts", "")
    _write(tmp_path / "app.tsx", "import Utils from './utils';\n")

    result = tools.dependency_graph(str(tmp_path), file_type="js")
    assert result["total_internal_dependency_edges"] == 1


def test_bare_package_import_in_js_is_not_resolved(tmp_path):
    _write(tmp_path / "app.js", "import React from 'react';\n")

    result = tools.dependency_graph(str(tmp_path), file_type="js")
    assert result["total_internal_dependency_edges"] == 0


# --- most_depended_on ranking ----------------------------------------------


def test_most_depended_on_ranks_by_in_degree(tmp_path):
    _write(tmp_path / "hub.py", "")
    _write(tmp_path / "a.py", "import hub\n")
    _write(tmp_path / "b.py", "import hub\n")
    _write(tmp_path / "c.py", "import hub\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["most_depended_on"][0]["file"] == "hub.py"
    assert result["most_depended_on"][0]["depended_on_by_count"] == 3


def test_files_with_zero_incoming_edges_are_excluded_from_most_depended_on(tmp_path):
    _write(tmp_path / "lonely.py", "x = 1\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["most_depended_on"] == []


# --- cycle detection ---------------------------------------------------


def test_detects_a_simple_two_file_cycle(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\n")
    _write(tmp_path / "pkg" / "b.py", "from pkg import a\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["cycles_found"] >= 1
    assert any("pkg/a.py" in c and "pkg/b.py" in c for c in result["example_cycles"])


def test_no_cycles_in_a_simple_linear_dependency_chain(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\n")
    _write(tmp_path / "pkg" / "b.py", "from pkg import c\n")
    _write(tmp_path / "pkg" / "c.py", "")

    result = tools.dependency_graph(str(tmp_path), file_type="py")
    assert result["cycles_found"] == 0


# --- combined scan / registration --------------------------------------


def test_omitting_file_type_scans_both_ecosystems(tmp_path):
    _write(tmp_path / "a.py", "")
    _write(tmp_path / "main.py", "import a\n")
    _write(tmp_path / "utils.js", "")
    _write(tmp_path / "index.js", "import u from './utils';\n")

    result = tools.dependency_graph(str(tmp_path))
    assert result["files_scanned"] == 4
    assert result["total_internal_dependency_edges"] == 2


def test_dependency_graph_is_registered_as_safe_tier():
    assert tools.TOOLS["dependency_graph"]["tier"] == "safe"


# --- focus_file: per-file lookup -----------------------------------------
# Added after a real gap surfaced in use: without focus_file, every
# per-file question ("what does X depend on", "what depends on X") got
# the same generic project-wide summary back, since the aggregate result
# has no per-file lookup in it at all.


def test_focus_file_by_bare_filename_returns_its_own_edges(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\n")
    _write(tmp_path / "pkg" / "b.py", "")
    _write(tmp_path / "pkg" / "c.py", "from pkg import a\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py", focus_file="a.py")
    assert result["status"] == "ok"
    assert result["focus_file"] == "pkg/a.py"
    assert result["depends_on"] == ["pkg/b.py"]
    assert result["depended_on_by"] == ["pkg/c.py"]


def test_focus_file_by_full_relative_path(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "")
    _write(tmp_path / "pkg" / "b.py", "from pkg import a\n")

    result = tools.dependency_graph(
        str(tmp_path), file_type="py", focus_file="pkg/a.py"
    )
    assert result["status"] == "ok"
    assert result["depended_on_by"] == ["pkg/b.py"]


def test_focus_file_not_found_returns_error_but_keeps_aggregate_stats(tmp_path):
    _write(tmp_path / "a.py", "import os\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py", focus_file="nope.py")
    assert result["status"] == "error"
    assert "not found" in result["message"] or "nope.py" in result["message"]
    # aggregate fields are still present — a focus-file miss shouldn't
    # throw away information the caller could otherwise use
    assert result["files_scanned"] == 1


def test_focus_file_ambiguous_bare_name_across_two_directories_errors_rather_than_guessing(
    tmp_path,
):
    _write(tmp_path / "a" / "utils.py", "")
    _write(tmp_path / "b" / "utils.py", "")
    _write(tmp_path / "main.py", "")

    result = tools.dependency_graph(
        str(tmp_path), file_type="py", focus_file="utils.py"
    )
    assert result["status"] == "error"
    assert "multiple" in result["message"].lower()


def test_focus_file_with_no_dependencies_or_dependents_reports_empty_lists(tmp_path):
    _write(tmp_path / "lonely.py", "x = 1\n")

    result = tools.dependency_graph(
        str(tmp_path), file_type="py", focus_file="lonely.py"
    )
    assert result["status"] == "ok"
    assert result["depends_on"] == []
    assert result["depended_on_by"] == []


def test_focus_file_message_lists_the_actual_dependency_names(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\n")
    _write(tmp_path / "pkg" / "b.py", "")

    result = tools.dependency_graph(str(tmp_path), file_type="py", focus_file="a.py")
    assert "pkg/b.py" in result["message"]


def test_focus_file_reports_in_cycle_true_when_the_file_is_actually_in_one(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\n")
    _write(tmp_path / "pkg" / "b.py", "from pkg import a\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py", focus_file="a.py")
    assert result["in_cycle"] is True
    assert "circular" in result["message"].lower()
    assert "IS part of a circular" in result["message"]


def test_focus_file_reports_in_cycle_false_when_it_merely_depends_on_a_cyclic_file(
    tmp_path,
):
    """c.py imports a.py, and a.py/b.py cycle with each other — but c.py
    itself is not part of that cycle. This is exactly the case that a
    naive 'does this file appear anywhere in example_cycles' check would
    get right by luck but a naive 'any cycles found in this project'
    check would get wrong."""
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\n")
    _write(tmp_path / "pkg" / "b.py", "from pkg import a\n")
    _write(tmp_path / "pkg" / "c.py", "from pkg import a\n")

    result = tools.dependency_graph(str(tmp_path), file_type="py", focus_file="c.py")
    assert result["in_cycle"] is False
    assert "not part of any detected circular" in result["message"]


def test_focus_file_in_cycle_false_when_project_has_no_cycles_at_all(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\n")
    _write(tmp_path / "pkg" / "b.py", "")

    result = tools.dependency_graph(str(tmp_path), file_type="py", focus_file="a.py")
    assert result["in_cycle"] is False


def test_file_reaches_itself_helper_directly():
    graph = {"a": {"b"}, "b": {"a"}, "c": {"a"}, "d": set()}
    assert tools._file_reaches_itself(graph, "a") is True
    assert tools._file_reaches_itself(graph, "b") is True
    assert tools._file_reaches_itself(graph, "c") is False
    assert tools._file_reaches_itself(graph, "d") is False
    assert tools._file_reaches_itself(graph, "not-in-graph") is False
