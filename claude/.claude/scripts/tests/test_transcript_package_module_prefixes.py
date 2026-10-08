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

PRODUCTION_MODULES = (
    "cost_ledger.py",
    "workstream_cost.py",
    "subagents.py",
    "subagent_mix.py",
    "handoff_nudge.py",
    "rearm_backtest.py",
    "spend_over_threshold.py",
    "plan_boundary.py",
    "handoff_signal_response.py",
)
TEST_FILES = (
    "test_transcript_cost_ledger.py",
    "test_transcript_cost_ledger_record_gates.py",
    "test_transcript_workstream_cost.py",
    "test_transcript_subagents.py",
    "test_transcript_subagent_mix.py",
    "test_transcript_subagent_mix_dollars.py",
    "test_transcript_cost_counts.py",
    "test_transcript_handoff_nudge.py",
    "test_transcript_rearm_backtest.py",
    "test_transcript_rearm_backtest_nudge_log.py",
    "test_transcript_spend_over_threshold.py",
    "test_transcript_plan_boundary.py",
    "test_transcript_handoff_signal_response_detection.py",
    "test_transcript_handoff_signal_response.py",
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


def _lazy_module_attrs(tree: ast.Module) -> set[str]:
    """String literals a module-level `__getattr__` compares its own name parameter against.

    Assumes the allow-list shape (`if name != "X": raise AttributeError`), the
    one scope.py uses: only `!=` comparisons count, so a deny-list shape
    (`if name == "X": raise AttributeError`) never adds "X"."""
    lazy_names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "__getattr__":
            continue
        parameters = node.args.posonlyargs + node.args.args
        if not parameters:
            continue
        for compare in ast.walk(node):
            if not isinstance(compare, ast.Compare):
                continue
            if not all(isinstance(op, ast.NotEq) for op in compare.ops):
                continue
            operands = [compare.left, *compare.comparators]
            if any(isinstance(operand, ast.Name) and operand.id == parameters[0].arg for operand in operands):
                lazy_names.update(
                    operand.value for operand in operands if isinstance(operand, ast.Constant) and isinstance(operand.value, str)
                )
    return lazy_names


def _top_level_names(module_path: Path) -> set[str]:
    """Every name module_path binds at module level: function/class defs,
    assignment targets (plain, tuple-unpacked, or annotated), and import
    aliases -- the full set a `module.<name>` attribute access from outside
    the module could legitimately resolve, plus IMPLICIT_MODULE_ATTRS, plus
    each name a module-level PEP 562 `__getattr__` resolves (the string
    literals it compares its own parameter against)."""
    tree = ast.parse(module_path.read_text())
    names: set[str] = set(IMPLICIT_MODULE_ATTRS) | _lazy_module_attrs(tree)
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


def test_scope_lazy_module_attrs_are_exactly_projects_dir():
    """scope.py's `__getattr__` resolves PROJECTS_DIR and nothing else, so
    _lazy_module_attrs derives exactly that -- pins against over-derivation
    that would let a typo'd `scope.<name>` read pass."""
    tree = ast.parse((PACKAGE_DIR / "scope.py").read_text())
    assert _lazy_module_attrs(tree) == {"PROJECTS_DIR"}


def test_lazy_module_attrs_ignores_deny_list_shaped_getattr():
    """A `__getattr__` that raises AttributeError for the name it compares
    against denies that name rather than resolving it, so it adds nothing."""
    deny_list_source = (
        "def __getattr__(name):\n"
        "    if name == 'DENIED':\n"
        "        raise AttributeError(name)\n"
        "    return 1\n"
    )
    assert _lazy_module_attrs(ast.parse(deny_list_source)) == set()


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
