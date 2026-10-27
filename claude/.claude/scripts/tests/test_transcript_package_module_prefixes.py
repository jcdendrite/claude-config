"""Pins each module in PRODUCTION_MODULES to the by-module-attribute import
discipline scope.py's own top-of-file comment documents, and pins every test
file in TEST_FILES two ways. First, every `<module>.<name>` read it makes --
directly through an unaliased `from transcript_analysis import <module>`
binding, or through a shim copy's `_mod.<module>.<name>` -- names a binding
the target module actually has. Second, no test_*.py file that imports from
transcript_analysis and loads no shim copy may be missing from TEST_FILES, so
a newly converted file keeps the coverage above instead of going unchecked
silently. Committed as a permanent test rather than run once by hand, so a
later edit that reintroduces a stale reference, or drops a converted file off
the list, still fails CI. Scope is those modules, those test files, and the
shim-loading test files. No earlier package module carries an equivalent check
yet.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

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
    "judgment_pair.py",
    "skill_invocation.py",
    "skill_pair.py",
    "buckets.py",
    "fail_seq.py",
    "struggle.py",
    "user_input.py",
    "duration.py",
    "friction_count.py",
    "period_cost.py",
    "turn_shape.py",
    "commit_gate.py",
    "pr_link.py",
    "sessions.py",
    "context_distribution.py",
    "context_composition.py",
    "cache_efficiency.py",
    "edit_format.py",
    "instrument_authoring.py",
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
    "test_transcript_period_cost.py",
    "test_transcript_audit_routing.py",
    "test_transcript_audit_routing_samples.py",
    "test_transcript_audit_routing_shape.py",
    "test_transcript_cache_rebuild_attribution.py",
    "test_transcript_cache_rebuild_switch_delta.py",
    "test_transcript_cache_rebuild_ttl_accumulation.py",
    "test_transcript_cache_rebuild_ttl_footing.py",
    "test_transcript_denials.py",
    "test_transcript_gh_cli.py",
    "test_transcript_pr_cost.py",
    "test_transcript_pr_cost_export_accounts.py",
    "test_transcript_pr_cost_ledger.py",
    "test_transcript_read_scope.py",
    "test_transcript_review_trace.py",
    "test_transcript_reviewer_yield.py",
    "test_transcript_shared_fixtures.py",
    "test_transcript_judgment_pair.py",
    "test_transcript_skill_invocation.py",
    "test_transcript_skill_pair.py",
    "test_transcript_buckets.py",
    "test_transcript_fail_seq.py",
    "test_transcript_struggle.py",
    "test_transcript_user_input.py",
    "test_transcript_duration.py",
    "test_transcript_friction_count.py",
    "test_transcript_analysis_branch_attribution.py",
    "test_transcript_pricing_session_helpers.py",
    "test_transcript_session_pricing.py",
    "test_transcript_turn_shape.py",
    "test_transcript_commit_gate.py",
    "test_transcript_pr_link.py",
    "test_transcript_context_distribution.py",
    "test_transcript_context_composition.py",
    "test_transcript_cache_efficiency.py",
    "test_transcript_edit_format.py",
    "test_transcript_instrument_authoring.py",
    "test_transcript_analysis.py",
    "test_transcript_cost.py",
    "test_transcript_review_rounds.py",
    "test_transcript_cache_rebuild_ttl_rules.py",
    "test_transcript_ledger_common.py",
    "test_transcript_pr_cost_gh.py",
    "test_transcript_pr_cost_export.py",
    "test_transcript_cache_rebuild.py",
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
    # Walks tree.body only, so a name bound inside a module-level if/try block
    # (e.g. a platform-gated def, or a try/except ImportError fallback) is missed.
    # That only makes this guard stricter, never laxer, for a module using that shape.
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


def _import_aliases(node: ast.ImportFrom) -> str:
    return ", ".join(alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in node.names)


def _unresolvable_absolute_package_imports(tree: ast.Module, package_names: set[str]) -> list[str]:
    """Absolute import statements that bind a sibling package module in a shape the attribute check
    cannot resolve.

    Flagged shapes, one per line:
    - `from transcript_analysis import <m> as <a>` for any <m> in package_names
    - every `import transcript_analysis` and `import transcript_analysis.<m>`, with or without `as`

    Both are flagged because the attribute check resolves only absolute, unaliased
    `from transcript_analysis import <m>` bindings.

    The no-op spelling `from transcript_analysis import scope as scope` is flagged too, since forbidding every
    `as` on a sibling binding is a simpler rule than telling the no-op spelling apart."""
    unresolvable: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "transcript_analysis":
            unresolvable.extend(
                f"from transcript_analysis import {alias.name} as {alias.asname}"
                for alias in node.names
                if alias.asname is not None and alias.name in package_names
            )
        elif isinstance(node, ast.Import):
            unresolvable.extend(
                f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
                for alias in node.names
                if alias.name == "transcript_analysis" or alias.name.startswith("transcript_analysis.")
            )
    return unresolvable


def _unresolvable_sibling_imports(tree: ast.Module, package_names: set[str]) -> list[str]:
    """Import statements that bind a sibling package module in a shape the attribute check cannot resolve.

    Flagged shapes, one per line:
    - every relative `from` import (`level > 0`), e.g. `from . import scope` or `from .scope import X`
    - every shape `_unresolvable_absolute_package_imports` flags

    All are flagged because the attribute check resolves only absolute, unaliased
    `from transcript_analysis import <m>` bindings. `_unresolvable_absolute_package_imports` omits the
    relative-import arm on purpose, so a test file's `from .conftest import ...` line still passes a
    check built on that narrower helper alone."""
    unresolvable: list[str] = [
        f"from {'.' * node.level}{node.module or ''} import {_import_aliases(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    unresolvable.extend(_unresolvable_absolute_package_imports(tree, package_names))
    return unresolvable


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


def test_production_modules_bind_siblings_only_by_unaliased_from_import():
    """test_production_modules_reference_only_real_sibling_attributes resolves only unaliased absolute
    `from transcript_analysis import <m>` bindings, so no module in PRODUCTION_MODULES may bind a sibling
    in any shape `_unresolvable_sibling_imports` flags."""
    package_names = _package_module_names()
    for filename in PRODUCTION_MODULES:
        tree = ast.parse((PACKAGE_DIR / filename).read_text())
        unresolvable = _unresolvable_sibling_imports(tree, package_names)
        assert not unresolvable, (
            f"{filename} binds a sibling package module via {unresolvable} -- "
            f"use an unaliased `from transcript_analysis import <m>` so the attribute check can resolve it"
        )


def test_unresolvable_sibling_imports_flags_aliased_and_plain_import_shapes():
    """The aliased `from` shape, every `ast.Import` shape of the package, and every relative `from` import
    are flagged; the unaliased absolute `from` shape, an aliased non-module name from the package, and
    unrelated aliased imports are not."""
    package_names = {"scope"}

    def flagged(source: str) -> list[str]:
        return _unresolvable_sibling_imports(ast.parse(source), package_names)

    assert flagged("from transcript_analysis import scope as s") == ["from transcript_analysis import scope as s"]
    assert flagged("from transcript_analysis import scope as scope") == [
        "from transcript_analysis import scope as scope"
    ]
    assert flagged("import transcript_analysis.scope as s") == ["import transcript_analysis.scope as s"]
    assert flagged("import transcript_analysis.scope") == ["import transcript_analysis.scope"]
    assert flagged("import transcript_analysis") == ["import transcript_analysis"]
    assert flagged("from . import scope as s") == ["from . import scope as s"]
    assert flagged("from . import scope") == ["from . import scope"]
    assert flagged("from .scope import X") == ["from .scope import X"]
    assert flagged("from transcript_analysis import scope") == []
    assert flagged("from transcript_analysis import not_a_module as x") == []
    assert flagged("import os as o") == []
    assert flagged("from collections import defaultdict as dd") == []


def test_unresolvable_absolute_package_imports_flags_only_absolute_shapes():
    """`_unresolvable_absolute_package_imports` ignores relative imports -- including the test tree's
    `from .conftest import ...` shape -- and the unaliased absolute `from transcript_analysis import <m>`
    shape, but flags an aliased absolute `from` import and any `ast.Import` of the package."""
    package_names = {"scope"}

    def flagged(source: str) -> list[str]:
        return _unresolvable_absolute_package_imports(ast.parse(source), package_names)

    assert flagged("from .conftest import x") == []
    assert flagged("from . import scope") == []
    assert flagged("from transcript_analysis import scope") == []
    assert flagged("from transcript_analysis import scope as s") == ["from transcript_analysis import scope as s"]
    assert flagged("import transcript_analysis.scope") == ["import transcript_analysis.scope"]


def _package_module_attribute_reads(tree: ast.Module, package_names: set[str]) -> list[tuple[str, ast.Attribute]]:
    """Every `<module>.<attr>` Attribute node in tree whose base Name is bound by an unaliased
    `from transcript_analysis import <module>` line, paired with the module name. Returns every such
    node regardless of Store/Load context -- a caller that needs to forbid rebinding filters ctx itself."""
    imported_modules = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "transcript_analysis"
        for alias in node.names
    } & package_names
    return [
        (node.value.id, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in imported_modules
    ]


def _mod_prefixed_module_attribute_reads(tree: ast.Module, package_names: set[str]) -> list[tuple[str, ast.Attribute]]:
    """Every `_mod.<module>.<name>` Attribute node in tree, paired with the module name -- the read shape
    a test file still loading a shim copy uses instead of a direct package import."""
    reads: list[tuple[str, ast.Attribute]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Attribute):
            continue
        inner = node.value
        if not isinstance(inner.value, ast.Name) or inner.value.id != "_mod":
            continue
        if inner.attr not in package_names:
            continue
        reads.append((inner.attr, node))
    return reads


def test_production_modules_reference_only_real_sibling_attributes():
    """Every `<module>.<name>` attribute a module in PRODUCTION_MODULES reads
    on an imported sibling package module names something that module
    actually binds at top level, and is never the Store side of an
    assignment -- no module in PRODUCTION_MODULES rebinds a sibling module's
    attribute (`mod.x = ...`, `mod.x += ...`)."""
    package_names = _package_module_names()
    for filename in PRODUCTION_MODULES:
        tree = ast.parse((PACKAGE_DIR / filename).read_text())
        reads = _package_module_attribute_reads(tree, package_names)
        sibling_names = {module: _top_level_names(PACKAGE_DIR / f"{module}.py") for module in {m for m, _ in reads}}

        for module_name, node in reads:
            assert not isinstance(node.ctx, ast.Store), (
                f"{filename} assigns through {module_name}.{node.attr} -- "
                f"no module in PRODUCTION_MODULES should rebind a sibling module's attribute"
            )
            assert node.attr in sibling_names[module_name], (
                f"{filename} reads {module_name}.{node.attr}, which {module_name}.py does not bind "
                f"at module level -- stale rename or typo"
            )


def test_test_files_reference_only_real_module_attributes():
    """Every `<module>.<name>` read in a TEST_FILES file names a binding the target module
    actually has, whether read directly through an unaliased package import or through a
    shim copy's `_mod.<module>.<name>`. This catches a stale rename or typo that a
    passing-but-wrong monkeypatch/assert would otherwise hide silently. A typo'd attribute
    on a real module raises AttributeError at collection or call time, not merely a failed
    assertion."""
    package_names = _package_module_names()
    top_level_names_by_module = {module: _top_level_names(PACKAGE_DIR / f"{module}.py") for module in package_names}
    for filename in TEST_FILES:
        tree = ast.parse((TESTS_DIR / filename).read_text())
        reads = _package_module_attribute_reads(tree, package_names) + _mod_prefixed_module_attribute_reads(
            tree, package_names
        )
        for module_name, node in reads:
            assert node.attr in top_level_names_by_module[module_name], (
                f"{filename} reads {module_name}.{node.attr}, which {module_name}.py does not bind "
                f"at module level -- stale rename or typo"
            )


def test_test_files_bind_package_modules_only_by_unaliased_from_import():
    """test_test_files_reference_only_real_module_attributes resolves a direct read only through an
    unaliased absolute `from transcript_analysis import <m>` binding, so no file in TEST_FILES may bind a
    package module in any shape `_unresolvable_absolute_package_imports` flags."""
    package_names = _package_module_names()
    for filename in TEST_FILES:
        tree = ast.parse((TESTS_DIR / filename).read_text())
        unresolvable = _unresolvable_absolute_package_imports(tree, package_names)
        assert not unresolvable, (
            f"{filename} binds a package module via {unresolvable} -- "
            f"use an unaliased `from transcript_analysis import <m>` so the attribute check can resolve it"
        )


def test_test_files_import_package_modules_by_module_only():
    """No file in TEST_FILES has a `from transcript_analysis.<m> import ...` line -- each reads a
    package module's names by attribute (e.g. `cost.compute_cost_trend_data`, not a bare
    `compute_cost_trend_data` bound at import time)."""
    for filename in TEST_FILES:
        tree = ast.parse((TESTS_DIR / filename).read_text())
        submodule_imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("transcript_analysis.")
        ]
        assert not submodule_imports, (
            f"{filename} imports {submodule_imports} via `from transcript_analysis.<m> import ...` -- "
            f"read the package module's name by attribute instead"
        )


def test_test_files_each_have_a_resolvable_module_attribute_read():
    """Every file in TEST_FILES yields at least one read the attribute check above can see (direct
    `<module>.<name>` or shim `_mod.<module>.<name>`) -- a file with neither would pass that check
    vacuously, leaving it silently uncovered."""
    package_names = _package_module_names()
    for filename in TEST_FILES:
        tree = ast.parse((TESTS_DIR / filename).read_text())
        reads = _package_module_attribute_reads(tree, package_names) + _mod_prefixed_module_attribute_reads(
            tree, package_names
        )
        assert reads, f"{filename} has no `<module>.<name>` or `_mod.<module>.<name>` read for the guard to check"


def _imports_package(tree: ast.Module) -> bool:
    """True if tree has an `import transcript_analysis[.<module>]`, or an absolute
    `from transcript_analysis[.<module>] import ...` (a relative import never counts)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "transcript_analysis" or alias.name.startswith("transcript_analysis.")
                for alias in node.names
            ):
                return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and (node.module == "transcript_analysis" or node.module.startswith("transcript_analysis."))
        ):
            return True
    return False


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_spec_from_file_location_call(node: ast.AST) -> TypeGuard[ast.Call]:
    return isinstance(node, ast.Call) and _called_name(node) == "spec_from_file_location"


def _calls_spec_from_file_location(tree: ast.Module) -> bool:
    """True if tree calls spec_from_file_location, as a bare name or as an attribute (e.g.
    `importlib.util.spec_from_file_location(...)`, the shim loader's own call shape)."""
    return any(_is_spec_from_file_location_call(node) for node in ast.walk(tree))


def test_loader_free_package_importing_test_files_are_in_test_files():
    """Every test_*.py file that imports from transcript_analysis and loads no shim copy of it (through
    spec_from_file_location) must be listed in TEST_FILES -- otherwise a newly converted file's reads go
    unchecked silently."""
    package_importing_loader_free: set[str] = set()
    for path in TESTS_DIR.glob("test_*.py"):
        tree = ast.parse(path.read_text())
        if _imports_package(tree) and not _calls_spec_from_file_location(tree):
            package_importing_loader_free.add(path.name)
    assert package_importing_loader_free, "expected at least one loader-free package-importing test file"
    missing = sorted(package_importing_loader_free - set(TEST_FILES))
    assert not missing, f"{missing} import transcript_analysis without loading the shim but are missing from TEST_FILES"


SHIM_COPY_ALLOWED_READS = frozenset({"build_parser", "main"})
_SCOPE_BODY_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _module_scope_nodes(tree: ast.Module) -> list[ast.AST]:
    found: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop()
        found.append(node)
        if not isinstance(node, _SCOPE_BODY_NODES):
            pending.extend(ast.iter_child_nodes(node))
    return found


def _contains_string_constant(expression: ast.AST, text: str) -> bool:
    return any(isinstance(node, ast.Constant) and node.value == text for node in ast.walk(expression))


def _loads_spec_named_transcript_analysis(call: ast.Call) -> bool:
    first_argument = call.args[0] if call.args else None
    return isinstance(first_argument, ast.Constant) and first_argument.value == "transcript_analysis"


def _locates_shim_script(call: ast.Call, module_assignments: dict[str, ast.expr]) -> bool:
    location_arguments = call.args[1:2] + [keyword.value for keyword in call.keywords if keyword.arg == "location"]
    for argument in location_arguments:
        bound_values = [
            module_assignments[node.id]
            for node in ast.walk(argument)
            if isinstance(node, ast.Name) and node.id in module_assignments
        ]
        if any(_contains_string_constant(candidate, "transcript-analysis.py") for candidate in [argument, *bound_values]):
            return True
    return False


def _module_assignments(module_nodes: list[ast.AST]) -> dict[str, ast.expr]:
    return {
        target.id: node.value
        for node in module_nodes
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _shim_copy_names(module_nodes: list[ast.AST]) -> set[str]:
    return {
        target.id
        for node in module_nodes
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _called_name(node.value) == "module_from_spec"
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _shim_copy_uses(tree: ast.Module, copy_names: set[str]) -> tuple[int, list[str]]:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    allowed_reads = 0
    misuses: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id in copy_names and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents[node]
        if isinstance(parent, ast.Attribute) and parent.value is node:
            if isinstance(parent.ctx, ast.Load) and parent.attr in SHIM_COPY_ALLOWED_READS:
                allowed_reads += 1
            else:
                cause = "read of non-allowed attribute" if isinstance(parent.ctx, ast.Load) else "write or delete"
                misuses.append(f"{node.lineno}: {node.id}.{parent.attr} ({cause})")
        elif (
            isinstance(parent, ast.Call)
            and parent.args == [node]
            and not parent.keywords
            and isinstance(parent.func, ast.Attribute)
            and parent.func.attr == "exec_module"
        ):
            continue
        else:
            misuses.append(f"{node.lineno}: bare use of {node.id}")
    return allowed_reads, misuses


def test_shim_copy_reads_only_build_parser_and_main():
    """A read of any other name couples a test to a shim binding, even one production code also reads."""
    loads_by_spec_name: dict[str, ast.Module] = {}
    locates_shim_script: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        module_nodes = _module_scope_nodes(tree)
        module_assignments = _module_assignments(module_nodes)
        spec_calls = [node for node in ast.walk(tree) if _is_spec_from_file_location_call(node)]
        if any(_loads_spec_named_transcript_analysis(call) for call in spec_calls):
            loads_by_spec_name[path.name] = tree
        if any(_locates_shim_script(call, module_assignments) for call in spec_calls):
            locates_shim_script.add(path.name)

    shim_loading_files = set(loads_by_spec_name)
    assert shim_loading_files and shim_loading_files == locates_shim_script, (
        f"files passing the spec name transcript_analysis only: {sorted(shim_loading_files - locates_shim_script)}; "
        f"files locating transcript-analysis.py only: {sorted(locates_shim_script - shim_loading_files)}; "
        f"the two sets must be equal and non-empty"
    )

    violations: list[str] = []
    for filename, tree in loads_by_spec_name.items():
        copy_names = _shim_copy_names(_module_scope_nodes(tree))
        if not copy_names:
            violations.append(f"{filename} loads a shim copy but assigns no module_from_spec(...) result at module level")
            continue
        allowed_reads, misuses = _shim_copy_uses(tree, copy_names)
        violations.extend(f"{filename}:{misuse}" for misuse in misuses)
        if not allowed_reads:
            violations.append(f"{filename} makes no allowed read of build_parser or main through its copy")
    assert not violations, f"shim copy violations: {violations}"
