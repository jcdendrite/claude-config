"""Pins cost.py's one deliberate reverse-import exception
(docs/transcript-analysis-architecture.md's "one-directional exception") to
exactly the single name it's documented to cover, so a name added to either
side of the exception can't silently widen it. Part (a) below guards
cost.py's public *function* surface only, not top-level constants/classes --
safe, since part (b) independently checks the shim's actual imported names
regardless of what kind of object each one denotes, so the reverse-import
boundary itself stays covered either way. This guard pins the production
import-direction exception only, not the separate whole-module `from
transcript_analysis import ... cost` bind (transcript-analysis.py:37) that
test files read as `_mod.cost.<name>` to reach cost.py's private helpers for
patching -- that channel predates this guard, stays open by design, and
restricting it would break legitimate existing test patterns. The hardcoded
module-path/name constants below need updating once cost-ledger's own
migration phase lands (docs/transcript-analysis-architecture.md:16-20); a
red run at that point means the exception was intentionally closed or
widened, not that this guard broke.
"""
from __future__ import annotations

import ast

from helpers import REPO_ROOT

COST_MODULE = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript_analysis" / "cost.py"
SHIM_SCRIPT = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript-analysis.py"
SHIM_IMPORT_SOURCE_MODULE = "transcript_analysis.cost"
SANCTIONED_NAMES = {"compute_cost_trend_data"}


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
    alias.asname, the shim's local binding: the one sanctioned import is
    itself aliased (`compute_cost_trend_data as _compute_cost_trend_data`,
    transcript-analysis.py:54), so filtering on asname would exclude it and
    silently compare against an empty set instead of failing loud."""
    tree = ast.parse(SHIM_SCRIPT.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == SHIM_IMPORT_SOURCE_MODULE:
            names.update(alias.name for alias in node.names if _is_guarded_name(alias.name))
    return names


def test_cost_module_public_function_surface_matches_sanctioned_set():
    actual = _cost_public_function_names()
    assert actual == SANCTIONED_NAMES, (
        f"cost.py's public (non-`_`, non-`cmd_`) function surface is {actual}, expected "
        f"{SANCTIONED_NAMES} -- either a new function leaked onto the surface the shim's "
        f"back-import can reach, or this is cost-ledger's migration intentionally widening "
        f"the exception (update docs/transcript-analysis-architecture.md's exception "
        f"language to match)"
    )


def test_shim_back_import_from_cost_matches_sanctioned_set():
    actual = _shim_imported_cost_names()
    assert actual == SANCTIONED_NAMES, (
        f"transcript-analysis.py imports {actual} (non-`_`, non-`cmd_` names, by cost.py's "
        f"own export name) from transcript_analysis.cost, expected {SANCTIONED_NAMES} -- "
        f"either a new name leaked backward across the shim/cost.py boundary, or this is "
        f"cost-ledger's migration intentionally closing or widening the exception (update "
        f"docs/transcript-analysis-architecture.md's exception language to match)"
    )
