"""Pins cost.py's public function surface to exactly its sanctioned set, and pins
that no `_`-non-prefixed, non-`cmd_`-prefixed name crosses back from cost.py into
cli.py by a by-name import (`from transcript_analysis.cost import X`, or its
level-1 relative form). Module-attribute reads such as `from transcript_analysis
import cost` followed by `cost.X` are not detected.
Part (a) below guards cost.py's public *function* surface only, not top-level
constants/classes -- safe, since part (b) independently checks cli.py's actual
imported names regardless of what kind of object each one denotes, so the
by-name import boundary itself stays covered either way. This guard pins the
production import-direction exception only. Test files' own `from
transcript_analysis import cost` imports reach cost.py's private helpers for
patching and are left unguarded on purpose.
"""
from __future__ import annotations

import ast

import pytest
from helpers import REPO_ROOT

COST_MODULE = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript_analysis" / "cost.py"
CLI_MODULE = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript_analysis" / "cli.py"
CLI_IMPORT_SOURCE_MODULE = "transcript_analysis.cost"
COST_PUBLIC_FUNCTION_NAMES = {"compute_cost_trend_data"}
CLI_BACK_IMPORTED_COST_NAMES: set[str] = set()


def _is_guarded_name(name: str) -> bool:
    """A name counts toward the exception's surface unless it's `_`-prefixed
    (private) or `cmd_`-prefixed. The `cmd_` names are the subcommand handlers
    cli.py binds via `set_defaults(func=...)`, a sanctioned crossing."""
    return not name.startswith("_") and not name.startswith("cmd_")


def _is_cost_module_import(node: ast.AST) -> bool:
    """True for `from transcript_analysis.cost import ...` and its level-1
    relative form `from .cost import ...`, which cli.py may use as a package module."""
    if not isinstance(node, ast.ImportFrom):
        return False
    is_absolute_form = node.level == 0 and node.module == CLI_IMPORT_SOURCE_MODULE
    is_relative_form = node.level == 1 and node.module == "cost"
    return is_absolute_form or is_relative_form


def _cost_public_function_names() -> set[str]:
    """Top-level FunctionDef/AsyncFunctionDef names in cost.py that are
    neither `_`-prefixed nor `cmd_`-prefixed -- cost.py's public function
    surface reachable from cli.py's back-import."""
    tree = ast.parse(COST_MODULE.read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_guarded_name(node.name)
    }


def _cost_names_imported_by(tree: ast.AST) -> set[str]:
    """Guarded names `tree` imports from the cost module, keyed by alias.name --
    the name as exported by cost.py -- never alias.asname, the importer's local binding."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if _is_cost_module_import(node):
            names.update(alias.name for alias in node.names if _is_guarded_name(alias.name))
    return names


def _cli_imported_cost_names() -> set[str]:
    return _cost_names_imported_by(ast.parse(CLI_MODULE.read_text()))


def test_cost_module_public_function_surface_matches_sanctioned_set():
    actual = _cost_public_function_names()
    assert actual == COST_PUBLIC_FUNCTION_NAMES, (
        f"cost.py's public (non-`_`, non-`cmd_`) function surface is {actual}, expected "
        f"{COST_PUBLIC_FUNCTION_NAMES} -- a new function leaked onto the surface cli.py's "
        f"back-import can reach (update docs/transcript-analysis-architecture.md's exception "
        f"language to match if this is deliberate)"
    )


def test_cli_back_import_from_cost_matches_sanctioned_set():
    actual = _cli_imported_cost_names()
    assert actual == CLI_BACK_IMPORTED_COST_NAMES, (
        f"cli.py imports {actual} (non-`_`, non-`cmd_` names, by cost.py's "
        f"own export name) from transcript_analysis.cost, expected {CLI_BACK_IMPORTED_COST_NAMES} "
        f"-- a new name leaked backward across the cli.py/cost.py boundary (update "
        f"docs/transcript-analysis-architecture.md's exception language to match if this is "
        f"deliberate)"
    )


def test_cli_still_imports_from_cost_module():
    """Non-vacuity guard for the test above: an empty CLI_BACK_IMPORTED_COST_NAMES would pass
    it even on a module-name typo in CLI_IMPORT_SOURCE_MODULE. Pins that cli.py genuinely
    still imports from the cost module, in either the absolute or the relative form -- the
    same guard test_package_directory_is_not_empty gives the architecture-doc test."""
    tree = ast.parse(CLI_MODULE.read_text())
    assert any(_is_cost_module_import(node) for node in ast.walk(tree)), (
        "cli.py no longer has a by-name import from transcript_analysis.cost, so the back-import "
        "guard is vacuous -- retire it together with CLI_BACK_IMPORTED_COST_NAMES"
    )


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param("from transcript_analysis.cost import cmd_cost", True, id="absolute"),
        pytest.param("from .cost import cmd_cost", True, id="relative-level-1"),
        pytest.param("from transcript_analysis.cost_ledger import x", False, id="absolute-sibling-module"),
        pytest.param("from .cost_ledger import x", False, id="relative-sibling-module"),
        pytest.param("from ..cost import x", False, id="relative-level-2"),
        pytest.param("from .transcript_analysis.cost import x", False, id="relative-with-package-prefix"),
        pytest.param("from . import cost", False, id="relative-bare-package-import"),
        pytest.param("from transcript_analysis import cost", False, id="absolute-module-as-name"),
        pytest.param("import transcript_analysis.cost", False, id="plain-import"),
        pytest.param("from other.cost import x", False, id="other-package"),
    ],
)
def test_is_cost_module_import_detects_the_two_by_name_import_forms(snippet, expected):
    assert _is_cost_module_import(ast.parse(snippet).body[0]) is expected


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(
            "from .cost import compute_cost_trend_data",
            {"compute_cost_trend_data"},
            id="relative-guarded-name",
        ),
        pytest.param(
            "from transcript_analysis.cost import compute_cost_trend_data as _alias",
            {"compute_cost_trend_data"},
            id="alias-keyed-by-exported-name",
        ),
        pytest.param("from .cost import cmd_cost, _x", set(), id="cmd-and-private-names-excluded"),
        pytest.param(
            "def f():\n    from .cost import compute_cost_trend_data",
            {"compute_cost_trend_data"},
            id="function-body-import",
        ),
        pytest.param("from .cost import *", {"*"}, id="star-import"),
    ],
)
def test_cost_names_imported_by_returns_guarded_exported_names(snippet, expected):
    assert _cost_names_imported_by(ast.parse(snippet)) == expected
