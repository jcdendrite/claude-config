"""Pins transcript-analysis.py to a re-export shim: its module body holds no command logic, only the
re-export of `build_parser` and `main` from transcript_analysis.cli and the `__main__` call to `main()`.
Logic added to the shim would sit outside the package, where the package's per-module test-file and
import-direction guards never look.
"""
from __future__ import annotations

import ast

import pytest
from helpers import REPO_ROOT

SHIM_PATH = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript-analysis.py"
SHIM_IMPORT_SOURCE_MODULE = "transcript_analysis.cli"
SHIM_REEXPORTED_NAMES = {"build_parser", "main"}


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_cli_reexport_import(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module == SHIM_IMPORT_SOURCE_MODULE
        and {alias.name for alias in node.names} == SHIM_REEXPORTED_NAMES
        and all(alias.asname is None for alias in node.names)
    )


def _is_all_assignment(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__all__"
    )


def _is_main_guard_calling_main(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or node.orelse:
        return False
    if ast.unparse(node.test) != "__name__ == '__main__'":
        return False
    if len(node.body) != 1:
        return False
    call_statement = node.body[0]
    return (
        isinstance(call_statement, ast.Expr)
        and isinstance(call_statement.value, ast.Call)
        and ast.unparse(call_statement.value) == "main()"
    )


def _is_reexport_only_shim(tree: ast.Module) -> bool:
    """True when the module body is, in order: an optional docstring, the cli re-export import,
    the `__all__` assignment, and the `__main__` guard that calls `main()`."""
    body = tree.body
    if body and _is_docstring(body[0]):
        body = body[1:]
    if len(body) != 3:
        return False
    import_node, all_node, main_guard_node = body
    return (
        _is_cli_reexport_import(import_node)
        and _is_all_assignment(all_node)
        and _is_main_guard_calling_main(main_guard_node)
    )


def test_shim_module_body_is_reexport_only():
    tree = ast.parse(SHIM_PATH.read_text())
    found_statements = [ast.unparse(statement).split("\n", 1)[0] for statement in tree.body]
    assert _is_reexport_only_shim(tree), (
        f"{SHIM_PATH.name} module body is not exactly an optional docstring, the "
        f"`from transcript_analysis.cli import build_parser, main` re-export, the `__all__` assignment, "
        f"and the `if __name__ == \"__main__\": main()` guard. Found statements: {found_statements}. "
        f"Command logic belongs in a transcript_analysis/ module."
    )


_VALID_SHIM_SNIPPET = (
    '"""doc"""\n'
    "from transcript_analysis.cli import build_parser, main\n"
    '__all__ = ["build_parser", "main"]\n'
    'if __name__ == "__main__":\n'
    "    main()\n"
)


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(_VALID_SHIM_SNIPPET, True, id="valid-shim"),
        pytest.param(_VALID_SHIM_SNIPPET.split("\n", 1)[1], True, id="valid-shim-without-docstring"),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("__all__", "from transcript_analysis.cost import X\n__all__"),
            False,
            id="stray-package-import",
        ),
        pytest.param(_VALID_SHIM_SNIPPET + "\ndef helper():\n    pass\n", False, id="extra-function-def"),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("build_parser, main", "main"), False, id="import-missing-build_parser"
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("    main()", "    main()\n    print('x')"), False, id="extra-main-guard-statement"
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("transcript_analysis.cli", "transcript_analysis.cost"),
            False,
            id="wrong-source-module",
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("from transcript_analysis.cli", "from .transcript_analysis.cli"),
            False,
            id="relative-import-keeping-module-string",
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("build_parser, main\n", "build_parser, main as run\n"),
            False,
            id="aliased-imported-name",
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("build_parser, main\n", "build_parser, main, extra\n"),
            False,
            id="extra-imported-name",
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace('__all__ = ["build_parser", "main"]', '__version__ = "1"'),
            False,
            id="slot-two-assigns-name-other-than-all",
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET + "else:\n    other()\n",
            False,
            id="main-guard-has-else-branch",
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace('"__main__"', '"other"'),
            False,
            id="main-guard-tests-other-than-main-name",
        ),
        pytest.param(
            _VALID_SHIM_SNIPPET.replace("    main()", "    other()"),
            False,
            id="main-guard-calls-other-than-main",
        ),
    ],
)
def test_is_reexport_only_shim_accepts_only_the_reexport_shape(snippet, expected):
    assert _is_reexport_only_shim(ast.parse(snippet)) is expected
