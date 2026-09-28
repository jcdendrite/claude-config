"""Pins cost.py's public function surface to exactly its sanctioned set, and pins
the shim's back-import channel from transcript_analysis.cost fully closed -- no
`_`-non-prefixed, non-`cmd_`-prefixed name crosses back from cost.py into the shim.
Part (a) below guards cost.py's public *function* surface only, not top-level
constants/classes -- safe, since part (b) independently checks the shim's actual
imported names regardless of what kind of object each one denotes, so the
reverse-import boundary itself stays covered either way. This guard pins the
production import-direction exception only, not the separate whole-module `from
transcript_analysis import ... cost` bind that test files read as `_mod.cost.<name>`
to reach cost.py's private helpers for patching -- that channel predates this
guard, stays open by design, and restricting it would break legitimate existing
test patterns.
"""
from __future__ import annotations

import ast

from helpers import REPO_ROOT

COST_MODULE = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript_analysis" / "cost.py"
SHIM_SCRIPT = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript-analysis.py"
SHIM_IMPORT_SOURCE_MODULE = "transcript_analysis.cost"
COST_PUBLIC_FUNCTION_NAMES = {"compute_cost_trend_data"}
SHIM_BACK_IMPORTED_COST_NAMES: set[str] = set()


def _is_guarded_name(name: str) -> bool:
    """A name counts toward the exception's surface unless it's `_`-prefixed
    (private) or `cmd_`-prefixed (a CLI entry point never called across the
    shim/cost.py boundary by name)."""
    return not name.startswith("_") and not name.startswith("cmd_")


def _cost_public_function_names() -> set[str]:
    """Top-level FunctionDef/AsyncFunctionDef names in cost.py that are
    neither `_`-prefixed nor `cmd_`-prefixed -- cost.py's public function
    surface reachable from the shim's back-import."""
    tree = ast.parse(COST_MODULE.read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_guarded_name(node.name)
    }


def _shim_imported_cost_names() -> set[str]:
    """Names imported from transcript_analysis.cost in the shim's own AST,
    keyed by alias.name -- the name as exported by cost.py -- never
    alias.asname, the shim's local binding."""
    tree = ast.parse(SHIM_SCRIPT.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == SHIM_IMPORT_SOURCE_MODULE:
            names.update(alias.name for alias in node.names if _is_guarded_name(alias.name))
    return names


def test_cost_module_public_function_surface_matches_sanctioned_set():
    actual = _cost_public_function_names()
    assert actual == COST_PUBLIC_FUNCTION_NAMES, (
        f"cost.py's public (non-`_`, non-`cmd_`) function surface is {actual}, expected "
        f"{COST_PUBLIC_FUNCTION_NAMES} -- a new function leaked onto the surface the shim's "
        f"back-import can reach (update docs/transcript-analysis-architecture.md's exception "
        f"language to match if this is deliberate)"
    )


def test_shim_back_import_from_cost_matches_sanctioned_set():
    actual = _shim_imported_cost_names()
    assert actual == SHIM_BACK_IMPORTED_COST_NAMES, (
        f"transcript-analysis.py imports {actual} (non-`_`, non-`cmd_` names, by cost.py's "
        f"own export name) from transcript_analysis.cost, expected {SHIM_BACK_IMPORTED_COST_NAMES} "
        f"-- a new name leaked backward across the shim/cost.py boundary (update "
        f"docs/transcript-analysis-architecture.md's exception language to match if this is "
        f"deliberate)"
    )


def test_shim_still_imports_from_cost_module():
    """Non-vacuity guard for the test above: an empty SHIM_BACK_IMPORTED_COST_NAMES would pass
    it even on a module-name typo in SHIM_IMPORT_SOURCE_MODULE. Pins that the shim genuinely
    still has at least one `from transcript_analysis.cost import ...` statement (cmd_cost,
    cmd_cost_trend, and _compute_workstream_dollars) -- the same guard
    test_package_directory_is_not_empty gives the architecture-doc test."""
    tree = ast.parse(SHIM_SCRIPT.read_text())
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == SHIM_IMPORT_SOURCE_MODULE
        for node in ast.walk(tree)
    )
