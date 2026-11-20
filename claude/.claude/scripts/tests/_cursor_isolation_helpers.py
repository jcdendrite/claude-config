"""Helpers for the interpreter-isolation rows of the Cursor adapter tests, shared by the shim and package test files."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def canary_writer(path: Path) -> str:
    return f"from pathlib import Path\nPath({str(path)!r}).write_text('ran')\n"


def imported_non_standard_names(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add((node.module or "").split(".")[0])
    return names - set(sys.stdlib_module_names)
