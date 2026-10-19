"""Pins the transcript_analysis package's import graph: no module-level import cycle, and every
module imports cleanly as the first import of a fresh interpreter.

The static graph check is the only guard for a cycle built from module-binding imports
(`from transcript_analysis import x`): CPython tolerates those at runtime in any import order.
The fresh-interpreter check backs it up for name-binding imports (`from transcript_analysis.x
import NAME`). The shim's own load is covered by
test_transcript_cli_bootstrap.py. Imports inside function bodies are outside the graph, though a
function called at import time can still run one.
"""
from __future__ import annotations

import ast
import subprocess
import sys

import pytest
from helpers import REPO_ROOT

SCRIPTS_DIR = REPO_ROOT / "claude" / ".claude" / "scripts"
PACKAGE_DIR = SCRIPTS_DIR / "transcript_analysis"
PACKAGE_NAME = "transcript_analysis"
# Generous bound for one interpreter start plus one module's import closure; a hang fails the test.
SUBPROCESS_TIMEOUT_SECONDS = 60
PACKAGE_MODULE_NAMES = sorted(path.stem for path in PACKAGE_DIR.glob("*.py") if path.stem != "__init__")


def _module_level_imports(node: ast.AST):
    """Yield Import/ImportFrom nodes outside any function body."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(child, ast.Import | ast.ImportFrom):
            yield child
        yield from _module_level_imports(child)


def _import_targets(tree: ast.Module) -> set[str]:
    """Package-relative module names a parsed module imports at module level, absolute or relative."""
    targets: set[str] = set()
    for node in _module_level_imports(tree):
        if isinstance(node, ast.Import):
            targets.update(a.name.split(".")[1] for a in node.names if a.name.startswith(f"{PACKAGE_NAME}."))
        elif (node.level == 0 and node.module == PACKAGE_NAME) or (node.level == 1 and node.module is None):
            targets.update(a.name for a in node.names)
        elif node.level == 1:
            targets.add(node.module.split(".")[0])
        elif node.module and node.module.startswith(f"{PACKAGE_NAME}."):
            targets.add(node.module.split(".")[1])
    return targets & set(PACKAGE_MODULE_NAMES)


def _package_imports(module_name: str) -> set[str]:
    return _import_targets(ast.parse((PACKAGE_DIR / f"{module_name}.py").read_text()))


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    finished: set[str] = set()

    def visit(name: str, path: list[str]) -> list[str] | None:
        if name in path:
            return [*path[path.index(name):], name]
        if name in finished:
            return None
        for target in sorted(graph[name]):
            cycle = visit(target, [*path, name])
            if cycle:
                return cycle
        finished.add(name)
        return None

    return next((cycle for name in graph if (cycle := visit(name, []))), None)


def test_package_discovers_modules():
    """Non-vacuity guard: an empty glob would pass the graph check trivially."""
    assert {"scope", "corpus", "skill_invocation"} <= set(PACKAGE_MODULE_NAMES)
    assert any(_package_imports(name) for name in PACKAGE_MODULE_NAMES)


def test_package_has_no_unmodeled_structure():
    """The graph models flat top-level modules only; extend it if either assertion fails."""
    assert not [p for p in PACKAGE_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"]
    assert not list(_module_level_imports(ast.parse((PACKAGE_DIR / "__init__.py").read_text())))


@pytest.mark.parametrize(
    "source",
    [
        "from transcript_analysis import scope",
        "from transcript_analysis.scope import X",
        "import transcript_analysis.scope as s",
        "from . import scope",
        "from .scope import X",
        "try:\n    from transcript_analysis import scope\nexcept ImportError:\n    pass",
    ],
)
def test_import_targets_sees_each_import_form(source):
    assert _import_targets(ast.parse(source)) == {"scope"}


def test_import_targets_ignores_function_body_import():
    assert _import_targets(ast.parse("def f():\n    from transcript_analysis import scope")) == set()


def test_find_cycle_reports_a_two_module_cycle():
    assert _find_cycle({"a": {"b"}, "b": {"a"}}) == ["a", "b", "a"]


def test_find_cycle_omits_modules_that_lead_into_the_cycle():
    assert _find_cycle({"entry": {"a"}, "a": {"b"}, "b": {"a"}}) == ["a", "b", "a"]


def test_find_cycle_accepts_a_diamond():
    assert _find_cycle({"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set()}) is None


def test_no_module_level_import_cycle():
    cycle = _find_cycle({name: _package_imports(name) for name in PACKAGE_MODULE_NAMES})
    assert cycle is None, f"module-level import cycle: {' -> '.join(cycle)}"


def _import_in_fresh_interpreter(search_dir, dotted_name: str) -> subprocess.CompletedProcess:
    code = f"import sys; sys.path.insert(0, {str(search_dir)!r}); import {dotted_name}"
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SECONDS
    )


def test_fresh_interpreter_import_fails_on_a_name_binding_cycle(tmp_path):
    """Shows the runtime layer can fail: `from pkg.x import NAME` in both directions raises."""
    package_dir = tmp_path / "cyclepkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "a.py").write_text("from cyclepkg.b import B\nA = 1\n")
    (package_dir / "b.py").write_text("from cyclepkg.a import A\nB = 1\n")
    result = _import_in_fresh_interpreter(tmp_path, "cyclepkg.a")
    assert result.returncode != 0
    assert "partially initialized module" in result.stderr


@pytest.mark.parametrize("module_name", PACKAGE_MODULE_NAMES)
def test_module_imports_first_in_fresh_interpreter(module_name):
    result = _import_in_fresh_interpreter(SCRIPTS_DIR, f"{PACKAGE_NAME}.{module_name}")
    assert result.returncode == 0, result.stderr
