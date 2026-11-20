"""How `cursor-adapter.py` loads its `cursor_adapter` package, and the namespace the adapter tests read it through.

The entry finds the package in its own directory under `python3 -I -S`, imports it with bytecode writing off, and leaves
`sys.path` and the interpreter's bytecode setting as it found them.
`AdapterNamespace` reads and patches a name through every module of one load that binds it, which is unambiguous only while
each name has one defining file. The uniqueness rows keep that."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    ADAPTER_PACKAGE_NAME,
    ADAPTER_SOURCE,
    IS_ROOT,
    SHIM_INTERPRETER_FLAGS,
    AdapterNamespace,
    adapter_under_empty_home,  # noqa: F401 (the `adapter` fixture)
    copy_adapter,
    load_adapter,
)

from ._cursor_isolation_helpers import canary_writer, imported_non_standard_names

FAULTED_MODULE_FILE_NAME = "sandbox.py"
PACKAGE_INIT_FILE_NAME = "__init__.py"
ENTRY_FILE_NAME = ADAPTER_SOURCE.name


def run_check_hook_env(
    adapter_copy: Path,
    home_dir: Path,
    *,
    interpreter_flags: tuple[str, ...] = SHIM_INTERPRETER_FLAGS,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *interpreter_flags, str(adapter_copy / ENTRY_FILE_NAME), "check", "--hook-env"],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home_dir), **(extra_env or {})},
        timeout=60,
    )


def package_directory_of(adapter_copy: Path) -> Path:
    return adapter_copy / ADAPTER_PACKAGE_NAME


def bytecode_entries_under(directory: Path) -> list[Path]:
    return [*directory.rglob("__pycache__"), *directory.rglob("*.pyc")]


def names_defined_in_more_than_one_file(sources: dict[str, str]) -> dict[str, list[str]]:
    """Each name a module-level `def`, `class` or assignment binds, dunders excluded, mapped to the files when more than one
    binds it. An import binds the importer to the same object, so it is not a definition."""
    files_by_name: dict[str, list[str]] = {}
    for file_name, source in sources.items():
        names: set[str] = set()
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                # A top-level assignment to a non-name target, such as a subscript, counts as a definition. No module has one.
                for target in node.targets:
                    names.update(name.id for name in ast.walk(target) if isinstance(name, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        for name in names:
            if not (name.startswith("__") and name.endswith("__")):
                files_by_name.setdefault(name, []).append(file_name)
    return {name: files for name, files in files_by_name.items() if len(files) > 1}


def renamed_from_imports(sources: dict[str, str]) -> dict[str, list[str]]:
    """Each file's module-level `from` imports that bind an imported name under a different one, as `name as asname`.
    A function-local import is not scanned."""
    renames_by_file: dict[str, list[str]] = {}
    for file_name, source in sources.items():
        renames = [
            f"{alias.name} as {alias.asname}"
            for node in ast.parse(source).body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.asname is not None and alias.asname != alias.name
        ]
        if renames:
            renames_by_file[file_name] = renames
    return renames_by_file


def adapter_source_files(adapter_dir: Path) -> dict[str, str]:
    """The entry and every module of the package beside it, keyed by their path under `adapter_dir`."""
    paths = [adapter_dir / ENTRY_FILE_NAME, *sorted(package_directory_of(adapter_dir).glob("*.py"))]
    return {str(path.relative_to(adapter_dir)): path.read_text(encoding="utf-8") for path in paths}


def spanned_real_paths(adapter: AdapterNamespace) -> set[str]:
    return {os.path.realpath(module.__file__) for module in adapter.spanned_modules}


class TestAModuleThatFailsToLoad:
    def test_a_check_hook_env_run_exits_zero_with_a_document_when_no_module_is_faulted(self, tmp_path: Path) -> None:
        adapter_copy = copy_adapter(tmp_path).parent

        result = run_check_hook_env(adapter_copy, tmp_path / "home")

        assert (result.returncode, result.stdout != "") == (0, True), result.stderr

    @pytest.mark.parametrize("fault", ["missing", "unreadable", "syntax-error", "empty", "fifo"])
    def test_a_check_hook_env_run_exits_nonzero_with_empty_stdout_when_a_module_is_missing_unreadable_broken_empty_or_a_fifo(
        self, tmp_path: Path, fault: str
    ) -> None:
        if fault == "unreadable" and IS_ROOT:
            pytest.skip("absent: root reads a mode-000 file")
        adapter_copy = copy_adapter(tmp_path).parent
        faulted_module = package_directory_of(adapter_copy) / FAULTED_MODULE_FILE_NAME
        if fault == "missing":
            faulted_module.unlink()
        elif fault == "unreadable":
            faulted_module.chmod(0)
        elif fault == "syntax-error":
            faulted_module.write_text("def broken(:\n")
        elif fault == "empty":
            faulted_module.write_text("")
        else:
            faulted_module.unlink()
            os.mkfifo(faulted_module)

        result = run_check_hook_env(adapter_copy, tmp_path / "home")

        assert (result.returncode != 0, result.stdout) == (True, "")
        assert re.search(rf"{ADAPTER_PACKAGE_NAME}[./]sandbox", result.stderr), result.stderr


class TestAPackageThatFailsToLoad:
    @pytest.mark.parametrize("fault", ["directory-removed", "init-removed", "init-fifo"])
    def test_a_check_hook_env_run_exits_nonzero_with_empty_stdout_when_the_package_directory_or_its_init_is_missing_or_a_fifo(
        self, tmp_path: Path, fault: str
    ) -> None:
        adapter_copy = copy_adapter(tmp_path).parent
        package_directory = package_directory_of(adapter_copy)
        if fault == "directory-removed":
            shutil.rmtree(package_directory)
        else:
            (package_directory / PACKAGE_INIT_FILE_NAME).unlink()
            if fault == "init-fifo":
                os.mkfifo(package_directory / PACKAGE_INIT_FILE_NAME)

        result = run_check_hook_env(adapter_copy, tmp_path / "home")

        assert (result.returncode != 0, result.stdout) == (True, "")
        assert ADAPTER_PACKAGE_NAME in result.stderr, result.stderr


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


class TestInterpreterIsolation:
    def test_a_json_py_beside_the_entry_is_imported_by_an_unflagged_run_and_not_by_a_flagged_one(self, tmp_path: Path) -> None:
        adapter_copy = copy_adapter(tmp_path).parent
        canary = tmp_path / "beside-canary"
        (adapter_copy / "json.py").write_text(canary_writer(canary))

        run_check_hook_env(adapter_copy, tmp_path / "home", interpreter_flags=())
        assert canary.exists(), "the control run did not import the json.py beside the entry"
        canary.unlink()
        flagged = run_check_hook_env(adapter_copy, tmp_path / "home")

        assert flagged.returncode == 0, flagged.stderr
        assert not canary.exists()

    def test_a_json_py_inside_the_package_is_imported_by_an_unflagged_run_and_not_by_a_flagged_one(self, tmp_path: Path) -> None:
        adapter_copy = copy_adapter(tmp_path).parent
        package_directory = package_directory_of(adapter_copy)
        canary = tmp_path / "package-canary"
        (package_directory / "json.py").write_text(canary_writer(canary))
        environment = {"PYTHONPATH": str(package_directory)}

        run_check_hook_env(adapter_copy, tmp_path / "home", interpreter_flags=(), extra_env=environment)
        assert canary.exists(), "the control run did not import the json.py inside the package"
        canary.unlink()
        flagged = run_check_hook_env(adapter_copy, tmp_path / "home", extra_env=environment)

        assert flagged.returncode == 0, flagged.stderr
        assert not canary.exists()

    def test_the_entry_and_every_module_of_the_package_import_only_standard_library_modules(self) -> None:
        sources = adapter_source_files(ADAPTER_DIR)

        assert {file_name: imported_non_standard_names(source) for file_name, source in sources.items()} == {
            file_name: set() for file_name in sources
        }

    def test_a_seeded_non_standard_import_is_found_by_the_scan(self) -> None:
        assert imported_non_standard_names("import json\ndef f():\n    import requests\n") == {"requests"}
        assert imported_non_standard_names("from yaml import safe_load\nfrom . import sibling\n") == {"yaml"}


class TestProcessStateAfterALoad:
    def test_the_bytecode_setting_is_false_after_a_load_that_started_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter_entry = copy_adapter(tmp_path).parent / ENTRY_FILE_NAME
        monkeypatch.setattr(sys, "dont_write_bytecode", False)

        load_adapter(adapter_entry)

        assert sys.dont_write_bytecode is False

    def test_sys_path_is_what_it_was_before_the_load(self, tmp_path: Path) -> None:
        adapter_entry = copy_adapter(tmp_path).parent / ENTRY_FILE_NAME
        path_before = list(sys.path)

        load_adapter(adapter_entry)

        assert sys.path == path_before

    def test_a_load_writes_no_bytecode_under_the_copied_package(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter_copy = copy_adapter(tmp_path).parent
        monkeypatch.setattr(sys, "dont_write_bytecode", False)

        load_adapter(adapter_copy / ENTRY_FILE_NAME)

        assert bytecode_entries_under(package_directory_of(adapter_copy)) == []

    def test_a_syntax_broken_module_raises_naming_its_file_and_leaves_the_bytecode_setting_and_sys_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter_copy = copy_adapter(tmp_path).parent
        broken_module = package_directory_of(adapter_copy) / FAULTED_MODULE_FILE_NAME
        broken_module.write_text("def broken(:\n")
        monkeypatch.setattr(sys, "dont_write_bytecode", False)
        path_before = list(sys.path)

        with pytest.raises(SyntaxError) as raised:
            load_adapter(adapter_copy / ENTRY_FILE_NAME)

        assert os.path.realpath(raised.value.filename) == os.path.realpath(broken_module)
        assert (sys.dont_write_bytecode, sys.path) == (False, path_before)

    def test_a_check_hook_env_run_under_the_isolation_flags_writes_no_bytecode_under_the_copied_package(
        self, tmp_path: Path
    ) -> None:
        adapter_copy = copy_adapter(tmp_path).parent

        result = run_check_hook_env(adapter_copy, tmp_path / "home")

        assert result.returncode == 0, result.stderr
        assert bytecode_entries_under(package_directory_of(adapter_copy)) == []


class TestFreshLoads:
    def test_two_loads_give_different_durable_write_objects(self) -> None:
        assert load_adapter().durable_write is not load_adapter().durable_write

    def test_each_load_names_the_directory_its_own_entry_sits_in(self, tmp_path: Path) -> None:
        adapter_copy = copy_adapter(tmp_path).parent

        first_checkout_load = load_adapter()
        copy_load = load_adapter(adapter_copy / ENTRY_FILE_NAME)
        second_checkout_load = load_adapter()

        assert os.path.realpath(copy_load.adapter_directory()) == os.path.realpath(adapter_copy)
        assert os.path.realpath(first_checkout_load.adapter_directory()) == os.path.realpath(ADAPTER_DIR)
        assert os.path.realpath(second_checkout_load.adapter_directory()) == os.path.realpath(ADAPTER_DIR)


class TestTheNamespaceOverTheRealPackage:
    def test_it_spans_the_entry_and_every_module_file_of_the_package(self, adapter: AdapterNamespace) -> None:
        expected_files = {ADAPTER_SOURCE} | {
            path for path in package_directory_of(ADAPTER_DIR).glob("*.py") if path.name != PACKAGE_INIT_FILE_NAME
        }

        assert spanned_real_paths(adapter) == {os.path.realpath(path) for path in expected_files}

    def test_every_name_a_spanned_module_binds_reads_through_the_namespace(self, adapter: AdapterNamespace) -> None:
        bound_names = {
            name
            for module in adapter.spanned_modules
            for name in module.__dict__
            if not (name.startswith("__") and name.endswith("__"))
        }

        assert sorted(name for name in bound_names if not hasattr(adapter, name)) == []

    def test_no_module_level_from_import_in_the_entry_or_the_package_renames_what_it_imports(self) -> None:
        assert renamed_from_imports(adapter_source_files(ADAPTER_DIR)) == {}

    def test_a_seeded_aliased_from_import_is_found_by_the_rename_scan(self) -> None:
        sources = {
            "seeded.py": "from .core import durable_write as _write\nfrom .core import kept as kept\n",
            "local.py": "def f():\n    from .core import durable_write as _write\n",
        }

        assert renamed_from_imports(sources) == {"seeded.py": ["durable_write as _write"]}

    def test_it_lacks_a_module_file_that_no_module_imports(self, tmp_path: Path) -> None:
        adapter_copy = copy_adapter(tmp_path).parent
        package_directory = package_directory_of(adapter_copy)
        (package_directory / "unimported.py").write_text("UNIMPORTED = 1\n")

        copy_load = load_adapter(adapter_copy / ENTRY_FILE_NAME)

        listed_files = {adapter_copy / ENTRY_FILE_NAME} | {
            path for path in package_directory.glob("*.py") if path.name != PACKAGE_INIT_FILE_NAME
        }
        assert spanned_real_paths(copy_load) == {os.path.realpath(path) for path in listed_files} - {
            os.path.realpath(package_directory / "unimported.py")
        }

    def test_a_patch_rebinds_the_name_in_every_module_that_bound_the_original(
        self, adapter: AdapterNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = adapter.durable_write
        binding_modules = [module for module in adapter.spanned_modules if module.__dict__.get("durable_write") is original]
        sentinel = object()

        monkeypatch.setattr(adapter, "durable_write", sentinel)

        assert len(binding_modules) >= 2
        assert [module.__dict__["durable_write"] for module in binding_modules] == [sentinel] * len(binding_modules)

    def test_undoing_a_patch_restores_the_original_in_every_module_that_bound_it(
        self, adapter: AdapterNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = adapter.durable_write
        binding_modules = [module for module in adapter.spanned_modules if module.__dict__.get("durable_write") is original]
        monkeypatch.setattr(adapter, "durable_write", object())

        monkeypatch.undo()

        assert [module.__dict__["durable_write"] for module in binding_modules] == [original] * len(binding_modules)

    def test_a_patch_through_an_earlier_namespace_reaches_its_own_load_and_leaves_a_later_load_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first_load = load_adapter()
        second_load = load_adapter()
        first_original = first_load.durable_write
        second_original = second_load.durable_write
        first_binding_modules = [
            module for module in first_load.spanned_modules if module.__dict__.get("durable_write") is first_original
        ]
        second_binding_modules = [
            module for module in second_load.spanned_modules if module.__dict__.get("durable_write") is second_original
        ]
        sentinel = object()

        monkeypatch.setattr(first_load, "durable_write", sentinel)
        patched_first = [module.__dict__["durable_write"] for module in first_binding_modules]
        patched_second = [module.__dict__["durable_write"] for module in second_binding_modules]
        monkeypatch.undo()
        restored_first = [module.__dict__["durable_write"] for module in first_binding_modules]
        restored_second = [module.__dict__["durable_write"] for module in second_binding_modules]

        assert patched_first == [sentinel] * len(first_binding_modules)
        assert patched_second == [second_original] * len(second_binding_modules)
        assert restored_first == [first_original] * len(first_binding_modules)
        assert restored_second == [second_original] * len(second_binding_modules)


class TestTheNamespaceOverSyntheticModules:
    @staticmethod
    def two_modules_binding_shared_names_to_different_objects() -> tuple[types.ModuleType, types.ModuleType, AdapterNamespace]:
        first_module = types.ModuleType("first")
        second_module = types.ModuleType("second")
        first_module.shared = 1
        second_module.shared = 2
        first_module.only_in_first = 3
        return first_module, second_module, AdapterNamespace("synthetic", [first_module, second_module], "synthetic.py")

    def test_a_read_of_a_name_two_modules_bind_to_different_objects_raises_attribute_error(self) -> None:
        *_, namespace = self.two_modules_binding_shared_names_to_different_objects()

        with pytest.raises(AttributeError):
            namespace.shared  # noqa: B018 (the read is the behavior under test)

    def test_a_write_of_a_name_two_modules_bind_to_different_objects_raises_attribute_error(self) -> None:
        *_, namespace = self.two_modules_binding_shared_names_to_different_objects()

        with pytest.raises(AttributeError):
            namespace.shared = 4

    def test_a_write_of_a_name_no_module_binds_raises_attribute_error(self) -> None:
        *_, namespace = self.two_modules_binding_shared_names_to_different_objects()

        with pytest.raises(AttributeError):
            namespace.bound_nowhere = 4

    def test_a_write_of_a_name_one_module_binds_leaves_the_other_module_without_it(self) -> None:
        first_module, second_module, namespace = self.two_modules_binding_shared_names_to_different_objects()

        namespace.only_in_first = 5

        assert (first_module.only_in_first, "only_in_first" in second_module.__dict__) == (5, False)


class TestEachNameIsDefinedInOneFile:
    def test_no_name_is_defined_in_more_than_one_file_of_the_entry_and_the_package(self) -> None:
        assert names_defined_in_more_than_one_file(adapter_source_files(ADAPTER_DIR)) == {}

    def test_the_scanned_files_are_the_entry_and_every_entry_in_the_package_directory(self) -> None:
        package_directory = package_directory_of(ADAPTER_DIR)
        listed_names = {
            path.name for path in package_directory.iterdir() if path.name != "__pycache__" and not path.name.startswith(".")
        }

        assert set(adapter_source_files(ADAPTER_DIR)) == {ENTRY_FILE_NAME} | {
            f"{ADAPTER_PACKAGE_NAME}/{name}" for name in listed_names
        }

    def test_a_function_defined_in_two_sources_is_found_by_the_scan(self) -> None:
        sources = {
            "first.py": "def seeded_defined_twice():\n    return 1\n",
            "second.py": "def seeded_defined_twice():\n    return 2\n",
        }

        assert names_defined_in_more_than_one_file(sources) == {"seeded_defined_twice": ["first.py", "second.py"]}

    def test_a_constant_assigned_in_two_sources_is_found_by_the_scan(self) -> None:
        sources = {"first.py": "SEEDED_LIMIT = 10\n", "second.py": "SEEDED_LIMIT = 10\n"}

        assert names_defined_in_more_than_one_file(sources) == {"SEEDED_LIMIT": ["first.py", "second.py"]}
