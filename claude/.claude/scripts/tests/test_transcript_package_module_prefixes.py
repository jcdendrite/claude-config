"""Pins each module in PRODUCTION_MODULES to the by-module-attribute import
discipline scope.py's own top-of-file comment documents, and pins every
`_mod.<module>.<name>` read in the test files in TEST_FILES to a name that
module actually binds. Committed as a permanent test rather than run once by
hand, so a later edit that reintroduces a stale reference still fails CI.
Scope stays those modules and test files; no earlier package module carries an
equivalent check yet.
"""
from __future__ import annotations

import ast
from pathlib import Path

from helpers import REPO_ROOT

SCRIPTS_DIR = REPO_ROOT / "claude" / ".claude" / "scripts"
PACKAGE_DIR = SCRIPTS_DIR / "transcript_analysis"
TESTS_DIR = SCRIPTS_DIR / "tests"

PRODUCTION_MODULES = ("cost_ledger.py", "workstream_cost.py", "subagents.py", "subagent_mix.py")
TEST_FILES = (
    "test_transcript_cost_ledger.py",
    "test_transcript_cost_ledger_record_gates.py",
    "test_transcript_workstream_cost.py",
    "test_transcript_subagents.py",
    "test_transcript_subagent_mix.py",
    "test_transcript_subagent_mix_dollars.py",
    "test_transcript_cost_counts.py",
)

# Every module object carries these regardless of what its own source assigns --
# set by the import machinery itself, not by a top-level def/class/Assign this
# file's own AST walk would otherwise see.
IMPLICIT_MODULE_ATTRS = {"__file__", "__name__", "__doc__", "__package__", "__loader__", "__spec__", "__path__"}


def _package_module_names() -> set[str]:
    return {p.stem for p in PACKAGE_DIR.glob("*.py") if p.name != "__init__.py"}


def _assign_target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        names: set[str] = set()
        for elt in target.elts:
            names.update(_assign_target_names(elt))
        return names
    return set()


def _top_level_names(module_path: Path) -> set[str]:
    """Every name module_path binds at module level: function/class defs,
    assignment targets (plain, tuple-unpacked, or annotated), and import
    aliases -- the full set a `module.<name>` attribute access from outside
    the module could legitimately resolve, plus IMPLICIT_MODULE_ATTRS."""
    tree = ast.parse(module_path.read_text())
    names: set[str] = set(IMPLICIT_MODULE_ATTRS)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_assign_target_names(target))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update((alias.asname or alias.name) for alias in node.names)
    return names


def test_production_modules_import_package_siblings_by_module_only():
    """No module in PRODUCTION_MODULES has a
    `from transcript_analysis.<m> import ...` line -- each reads a sibling
    package module's names by attribute (e.g. `cost.compute_cost_trend_data`,
    not a bare `compute_cost_trend_data` bound at import time), per scope.py's
    own top-of-file comment."""
    for filename in PRODUCTION_MODULES:
        tree = ast.parse((PACKAGE_DIR / filename).read_text())
        submodule_imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("transcript_analysis.")
        ]
        assert not submodule_imports, (
            f"{filename} imports {submodule_imports} via `from transcript_analysis.<m> import ...` -- "
            f"read the sibling module's name by attribute instead"
        )


def test_production_modules_reference_only_real_sibling_attributes():
    """Every `<module>.<name>` attribute a module in PRODUCTION_MODULES reads
    on an imported sibling package module names something that module
    actually binds at top level, and is never the Store side of an
    assignment -- no module in PRODUCTION_MODULES rebinds a sibling module's
    attribute (`mod.x = ...`, `mod.x += ...`)."""
    package_names = _package_module_names()
    for filename in PRODUCTION_MODULES:
        module_path = PACKAGE_DIR / filename
        tree = ast.parse(module_path.read_text())
        imported_modules = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "transcript_analysis"
            for alias in node.names
        } & package_names
        sibling_names = {m: _top_level_names(PACKAGE_DIR / f"{m}.py") for m in imported_modules}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            if node.value.id not in imported_modules:
                continue
            assert not isinstance(node.ctx, ast.Store), (
                f"{filename} assigns through {node.value.id}.{node.attr} -- "
                f"no module in PRODUCTION_MODULES should rebind a sibling module's attribute"
            )
            assert node.attr in sibling_names[node.value.id], (
                f"{filename} reads {node.value.id}.{node.attr}, which {node.value.id}.py does not bind "
                f"at module level -- stale rename or typo"
            )


def test_test_files_reference_only_real_module_attributes_through_mod():
    """Every `_mod.<module>.<name>` read in the migrated test files names a
    binding the target module actually has -- catches a stale rename or typo
    that a passing-but-wrong monkeypatch/assert would otherwise hide silently,
    since a typo'd attribute on a real module raises AttributeError at
    collection or call time, never merely fails an assertion."""
    package_names = _package_module_names()
    for filename in TEST_FILES:
        tree = ast.parse((TESTS_DIR / filename).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Attribute):
                continue
            inner = node.value
            if not isinstance(inner.value, ast.Name) or inner.value.id != "_mod":
                continue
            module_name = inner.attr
            if module_name not in package_names:
                continue
            names = _top_level_names(PACKAGE_DIR / f"{module_name}.py")
            assert node.attr in names, (
                f"{filename} reads _mod.{module_name}.{node.attr}, which {module_name}.py does not bind "
                f"at module level -- stale rename or typo"
            )
