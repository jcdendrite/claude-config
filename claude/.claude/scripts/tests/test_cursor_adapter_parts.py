"""cursor-adapter.py and its parts: the part list, the names across the files, a part that fails to load, and a link to
`cursor-adapter.py`.

`cursor-adapter.py` executes each `cursor-adapter-*.py` part into its own namespace, so no per-file check sees a name the
parts share.
These tests run the same checks over the files as one source.

The scan finds an unbound name, an unused import, and a function, class or import defined twice.
It misses a constant assigned twice, and a function, class or import that a top-level statement reads
between its two definitions, such as a base class.
In each missed case the later binding replaces the earlier one in the shared namespace, as it would in one file."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from helpers import CLAUDE_DIR

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    ADAPTER_SOURCE,
    IS_ROOT,
    SHIM_INTERPRETER_FLAGS,
    adapter_under_empty_home,  # noqa: F401 (the `adapter` fixture)
    copy_adapter,
)

NAME_CHECK_RULES = "F821,F401,F811"
ENTRY_EXECUTION_LINE = "\nexecute_adapter_parts()\n"
PART_FILE_GLOB = "cursor-adapter*.py"  # must equal the cursor-adapter per-file-ignores glob in pyproject.toml
FAULTED_PART_NAME = "cursor-adapter-sandbox.py"
LAST_PART_NAME = "cursor-adapter-check.py"
FIRST_PART_NAME = "cursor-adapter-core.py"


def combined_adapter_source(
    adapter: types.ModuleType, *, entry_head_extra: str = "", part_extras: dict[str, str] | None = None
) -> str:
    """`cursor-adapter.py` up to its `execute_adapter_parts()` call, each part in load order, then the rest of it."""
    entry_head, entry_execution_line, entry_rest = ADAPTER_SOURCE.read_text(encoding="utf-8").partition(ENTRY_EXECUTION_LINE)
    assert entry_execution_line, f"{ADAPTER_SOURCE.name} has no {ENTRY_EXECUTION_LINE.strip()!r} line to split on"
    for part_name in part_extras or {}:
        assert part_name in adapter.ADAPTER_PART_FILE_NAMES, f"{part_name} is not in ADAPTER_PART_FILE_NAMES"
    part_sources = [
        (ADAPTER_DIR / part_name).read_text(encoding="utf-8") + (part_extras or {}).get(part_name, "")
        for part_name in adapter.ADAPTER_PART_FILE_NAMES
    ]
    return "\n\n".join([entry_head + entry_head_extra, *part_sources, entry_rest])


def ruff_findings(source: str, tmp_path: Path) -> list[dict[str, Any]]:
    combined_path = tmp_path / "cursor_adapter_combined.py"
    combined_path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--no-cache",
            "--output-format=json",
            "--select",
            NAME_CHECK_RULES,
            str(combined_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    # `python -m ruff` exits 1 with empty stdout when ruff is not installed, the same status as "findings reported".
    assert result.returncode in (0, 1) and result.stdout, (
        f"ruff did not run under {sys.executable} (exit {result.returncode}): {result.stderr}"
    )
    return json.loads(result.stdout)


def finding_codes(findings: list[dict[str, Any]]) -> set[str]:
    return {finding["code"] for finding in findings}


def run_check_hook_env(adapter_copy: Path, home_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *SHIM_INTERPRETER_FLAGS, str(adapter_copy / "cursor-adapter.py"), "check", "--hook-env"],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home_dir)},
        timeout=60,
    )


class TestPartList:
    def test_the_part_files_beside_the_entry_are_exactly_the_files_the_entry_loads(self, adapter: types.ModuleType) -> None:
        part_files_on_disk = {path.name for path in ADAPTER_DIR.glob(PART_FILE_GLOB)} - {ADAPTER_SOURCE.name}

        assert part_files_on_disk == set(adapter.ADAPTER_PART_FILE_NAMES)

    def test_the_entry_lists_no_part_twice(self, adapter: types.ModuleType) -> None:
        assert len(adapter.ADAPTER_PART_FILE_NAMES) == len(set(adapter.ADAPTER_PART_FILE_NAMES))


class TestNamesAcrossTheFiles:
    def test_the_files_read_as_one_source_have_no_unbound_name_unused_import_or_redefined_function_class_or_import(
        self, adapter: types.ModuleType, tmp_path: Path
    ) -> None:
        findings = ruff_findings(combined_adapter_source(adapter), tmp_path)

        assert [f"{finding['code']} {finding['message']}" for finding in findings] == []

    def test_a_name_no_file_defines_is_found_as_unbound(self, adapter: types.ModuleType, tmp_path: Path) -> None:
        seeded = combined_adapter_source(adapter, part_extras={LAST_PART_NAME: "\nseeded_value = seeded_unbound_name\n"})

        assert finding_codes(ruff_findings(seeded, tmp_path)) == {"F821"}

    def test_an_import_no_file_reads_is_found_as_unused(self, adapter: types.ModuleType, tmp_path: Path) -> None:
        seeded = combined_adapter_source(adapter, entry_head_extra="\nimport seeded_unused_module\n")

        assert finding_codes(ruff_findings(seeded, tmp_path)) == {"F401"}

    def test_a_function_defined_in_two_parts_is_found_as_a_redefinition(self, adapter: types.ModuleType, tmp_path: Path) -> None:
        seeded_definition = "\n\ndef seeded_defined_twice():\n    return 1\n"
        seeded = combined_adapter_source(
            adapter, part_extras={FIRST_PART_NAME: seeded_definition, LAST_PART_NAME: seeded_definition}
        )

        assert finding_codes(ruff_findings(seeded, tmp_path)) == {"F811"}


class TestAPartThatFailsToLoad:
    def test_a_check_hook_env_run_exits_zero_with_a_document_when_no_part_is_faulted(self, tmp_path: Path) -> None:
        adapter_copy = copy_adapter(tmp_path).parent

        result = run_check_hook_env(adapter_copy, tmp_path / "home")

        assert (result.returncode, result.stdout != "") == (0, True), result.stderr

    @pytest.mark.parametrize("fault", ["missing", "unreadable", "syntax-error"])
    def test_a_check_hook_env_run_exits_nonzero_with_empty_stdout_when_a_part_is_missing_unreadable_or_broken(
        self, adapter: types.ModuleType, tmp_path: Path, fault: str
    ) -> None:
        if fault == "unreadable" and IS_ROOT:
            pytest.skip("absent: root reads a mode-000 file")
        assert FAULTED_PART_NAME in adapter.ADAPTER_PART_FILE_NAMES, f"{FAULTED_PART_NAME} is not in ADAPTER_PART_FILE_NAMES"
        adapter_copy = copy_adapter(tmp_path).parent
        faulted_part = adapter_copy / FAULTED_PART_NAME
        if fault == "missing":
            faulted_part.unlink()
        elif fault == "unreadable":
            faulted_part.chmod(0)
        else:
            faulted_part.write_text("def broken(:\n")

        result = run_check_hook_env(adapter_copy, tmp_path / "home")

        assert (result.returncode != 0, result.stdout) == (True, "")
        assert FAULTED_PART_NAME in result.stderr, result.stderr


class TestALinkToTheEntryAlone:
    def test_the_entry_run_through_a_link_finds_its_parts_beside_the_real_file(self, tmp_path: Path) -> None:
        entry_link = tmp_path / "cursor-adapter.py"
        os.symlink(ADAPTER_SOURCE, entry_link)

        result = subprocess.run(
            [sys.executable, *SHIM_INTERPRETER_FLAGS, str(entry_link), "--help"],
            capture_output=True,
            text=True,
            check=False,
            env={"HOME": str(tmp_path / "home")},
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("usage: cursor-adapter.py")
