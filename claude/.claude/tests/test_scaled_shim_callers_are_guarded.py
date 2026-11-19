"""Structural test: every file that can install the scaled timeout shim runs under helpers.install_unwarmed_stub_guard."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from helpers import REPO_ROOT

SCALED_SHIM_NAMES = (
    "write_scaled_timeout_shim",
    "_write_conditional_sleep_shim",
    "git_timeout_shim",
    "gh_timeout_shim",
)
GUARD_INSTALL_NAME = "install_unwarmed_stub_guard"
THIS_FILE = Path(__file__).resolve()

UNCOVERED_FIX = (
    f"Call {GUARD_INSTALL_NAME}(monkeypatch, tmp_path_factory.getbasetemp()) from that tree's autouse fixture. "
    "In a collected test file in a tree with no conftest.py, such as claude/.claude/tests/, "
    "call it from an autouse fixture in the file itself. "
    "In an imported module such as a shared helper, call it inside the helper that references the shim, "
    "taking monkeypatch and the base temp directory as parameters."
)


class ShimScan(NamedTuple):
    reference_lines: dict[Path, list[int]]
    uncovered_lines: dict[Path, list[int]]


def _parse_when_naming(path: Path, names: tuple[str, ...]) -> ast.Module | None:
    """The file's AST, or None when it cannot be read, does not parse, or holds none of `names`."""
    try:
        source = path.read_bytes()
        if not any(name.encode() in source for name in names):
            return None
        return ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


def _reference_lines(tree: ast.Module) -> list[int]:
    reference_lines = []
    for node in ast.walk(tree):
        if (
            (isinstance(node, ast.Name) and node.id in SCALED_SHIM_NAMES)
            or (isinstance(node, ast.Attribute) and node.attr in SCALED_SHIM_NAMES)
            or (isinstance(node, ast.arg) and node.arg in SCALED_SHIM_NAMES)
            or (isinstance(node, ast.alias) and node.name in SCALED_SHIM_NAMES)
            or (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in SCALED_SHIM_NAMES)
        ):
            reference_lines.append(node.lineno)
    return sorted(reference_lines)


def _calls_guard_install(path: Path) -> bool:
    tree = _parse_when_naming(path, (GUARD_INSTALL_NAME,))
    if tree is None:
        return False
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == GUARD_INSTALL_NAME)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == GUARD_INSTALL_NAME)
        )
        for node in ast.walk(tree)
    )


def _conftest_directories(path: Path, root: Path) -> list[Path]:
    """`path`'s directory and each ancestor up to and including `root`; only its own directory when it lies outside `root`."""
    if not path.parent.is_relative_to(root):
        return [path.parent]
    directories = [path.parent]
    while directories[-1] != root:
        directories.append(directories[-1].parent)
    return directories


# References match lexically per file, so a wrapper under another name is not seen.
def scan_shim_references(python_files: list[Path], root: Path) -> ShimScan:
    reference_lines: dict[Path, list[int]] = {}
    for path in python_files:
        if path.resolve() == THIS_FILE:
            continue
        tree = _parse_when_naming(path, SCALED_SHIM_NAMES)
        lines = _reference_lines(tree) if tree is not None else []
        if lines:
            reference_lines[path] = lines
    guard_call_by_path: dict[Path, bool] = {}

    def calls_guard_install(path: Path) -> bool:
        if path not in guard_call_by_path:
            guard_call_by_path[path] = _calls_guard_install(path)
        return guard_call_by_path[path]

    uncovered_lines = {
        path: lines
        for path, lines in reference_lines.items()
        if not calls_guard_install(path)
        and not any(calls_guard_install(directory / "conftest.py") for directory in _conftest_directories(path, root))
    }
    return ShimScan(reference_lines, uncovered_lines)


def _tracked_python_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [REPO_ROOT / name for name in listing.split("\0") if name.endswith(".py")]


class TestEveryScaledShimCallerIsGuarded:
    def test_no_tracked_file_that_can_install_the_scaled_shim_lacks_a_guard(self):
        scan = scan_shim_references(_tracked_python_files(), REPO_ROOT)

        uncovered = [
            f"{path.relative_to(REPO_ROOT)}:{line}" for path, lines in scan.uncovered_lines.items() for line in lines
        ]
        assert not uncovered, f"{UNCOVERED_FIX}\nUncovered references:\n" + "\n".join(uncovered)

    @pytest.mark.parametrize(
        "anchor",
        [
            "claude/.claude/hooks/tests/test_require_ready_for_review.py",
            "claude/.claude/scripts/tests/test_ci_watch.py",
        ],
    )
    def test_scan_reaches_a_known_reference_in_each_tree(self, anchor: str):
        scan = scan_shim_references(_tracked_python_files(), REPO_ROOT)

        assert REPO_ROOT / anchor in scan.reference_lines, f"anchor {anchor} is no longer a referencing file; update the anchor"


class TestSeededControls:
    @pytest.fixture
    def root(self, tmp_path: Path) -> Path:
        root = tmp_path / "root"
        root.mkdir()
        return root

    @staticmethod
    def _write(path: Path, source: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        return path

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("def test_it(git_timeout_shim):\n    pass\n", id="fixture-request-argument"),
            pytest.param("write_scaled_timeout_shim(tmp_path)\n", id="bare-name-call"),
            pytest.param("helpers.write_scaled_timeout_shim(tmp_path)\n", id="attribute-call"),
            pytest.param("from helpers import write_scaled_timeout_shim\n", id="import-alias-without-call"),
            pytest.param(
                'import pytest\n\n@pytest.mark.usefixtures("git_timeout_shim")\ndef test_it():\n    pass\n',
                id="usefixtures-string",
            ),
        ],
    )
    def test_reference_shape_lands_in_the_referencing_set(self, root: Path, source: str):
        seeded = self._write(root / "test_seeded.py", source)

        scan = scan_shim_references([seeded], root)

        assert seeded in scan.reference_lines

    def test_docstring_mention_is_not_a_reference(self, root: Path):
        seeded = self._write(root / "test_seeded.py", '"""Mentions write_scaled_timeout_shim and git_timeout_shim."""\n')

        scan = scan_shim_references([seeded], root)

        assert seeded not in scan.reference_lines

    def test_definition_is_not_a_reference(self, root: Path):
        seeded = self._write(root / "test_seeded.py", "def write_scaled_timeout_shim(bin_dir):\n    return True\n")

        scan = scan_shim_references([seeded], root)

        assert seeded not in scan.reference_lines

    def test_unparseable_file_and_listed_missing_file_are_skipped(self, root: Path):
        unparseable = self._write(root / "test_broken.py", "write_scaled_timeout_shim(\n")
        missing = root / "test_missing.py"

        scan = scan_shim_references([unparseable, missing], root)

        assert scan == ShimScan({}, {})

    def test_referencing_file_is_uncovered_with_no_conftest(self, root: Path):
        seeded = self._write(root / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")

        scan = scan_shim_references([seeded], root)

        assert scan.uncovered_lines[seeded] == [1]

    def test_imported_helper_in_a_conftest_less_directory_is_uncovered_until_it_calls_the_install(self, root: Path):
        uncovered_helper = self._write(
            root / "shared" / "uncovered_helper.py",
            "def write_wrapper(bin_dir):\n    return write_scaled_timeout_shim(bin_dir)\n",
        )
        covered_helper = self._write(
            root / "shared" / "covered_helper.py",
            "def write_wrapper(bin_dir, monkeypatch, basetemp):\n"
            f"    {GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n"
            "    return write_scaled_timeout_shim(bin_dir)\n",
        )

        scan = scan_shim_references([uncovered_helper, covered_helper], root)

        assert scan.uncovered_lines == {uncovered_helper: [2]}
        assert "shared helper" in UNCOVERED_FIX

    def test_referencing_file_is_covered_by_a_calling_conftest_beside_it(self, root: Path):
        seeded = self._write(root / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(root / "conftest.py", f"{GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n")

        scan = scan_shim_references([seeded], root)

        assert seeded in scan.reference_lines
        assert seeded not in scan.uncovered_lines

    def test_referencing_file_is_covered_by_a_call_in_its_own_code(self, root: Path):
        seeded = self._write(
            root / "test_seeded.py", f"write_scaled_timeout_shim(tmp_path)\n{GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n"
        )

        scan = scan_shim_references([seeded], root)

        assert seeded in scan.reference_lines
        assert seeded not in scan.uncovered_lines

    def test_calling_conftest_in_an_ancestor_within_the_root_covers_a_nested_file(self, root: Path):
        seeded = self._write(root / "tree" / "nested" / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(root / "tree" / "conftest.py", f"{GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n")

        scan = scan_shim_references([seeded], root)

        assert seeded in scan.reference_lines
        assert seeded not in scan.uncovered_lines

    def test_attribute_form_guard_call_in_a_conftest_covers_the_file(self, root: Path):
        seeded = self._write(root / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(root / "conftest.py", f"helpers.{GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n")

        scan = scan_shim_references([seeded], root)

        assert seeded in scan.reference_lines
        assert seeded not in scan.uncovered_lines

    @pytest.mark.parametrize(
        "conftest_relative_path",
        [
            pytest.param("sibling/conftest.py", id="sibling-of-the-tree-directory"),
            pytest.param("tree/other/conftest.py", id="sibling-of-the-files-own-directory"),
            pytest.param("tree/nested/descendant/conftest.py", id="descendant-directory"),
            pytest.param("../conftest.py", id="above-the-root"),
        ],
    )
    def test_calling_conftest_outside_the_ancestor_chain_leaves_the_file_uncovered(
        self, root: Path, conftest_relative_path: str
    ):
        seeded = self._write(root / "tree" / "nested" / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(root / conftest_relative_path, f"{GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n")

        scan = scan_shim_references([seeded], root)

        assert scan.uncovered_lines[seeded] == [1]

    def test_conftest_that_imports_the_install_without_calling_it_leaves_the_file_uncovered(self, root: Path):
        seeded = self._write(root / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(root / "conftest.py", f"from helpers import {GUARD_INSTALL_NAME}\n")

        scan = scan_shim_references([seeded], root)

        assert scan.uncovered_lines[seeded] == [1]

    def test_conftest_that_mentions_the_install_without_calling_it_leaves_the_file_uncovered(self, root: Path):
        seeded = self._write(root / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(root / "conftest.py", f"guard_install = {GUARD_INSTALL_NAME}\n")

        scan = scan_shim_references([seeded], root)

        assert scan.uncovered_lines[seeded] == [1]

    def test_calling_conftest_in_the_directory_of_a_file_outside_the_root_covers_it(self, root: Path):
        seeded = self._write(root.parent / "outside" / "nested" / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(seeded.parent / "conftest.py", f"{GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n")

        scan = scan_shim_references([seeded], root)

        assert seeded in scan.reference_lines
        assert seeded not in scan.uncovered_lines

    def test_calling_conftest_above_a_file_outside_the_root_leaves_it_uncovered(self, root: Path):
        seeded = self._write(root.parent / "outside" / "nested" / "test_seeded.py", "write_scaled_timeout_shim(tmp_path)\n")
        self._write(seeded.parent.parent / "conftest.py", f"{GUARD_INSTALL_NAME}(monkeypatch, basetemp)\n")

        scan = scan_shim_references([seeded], root)

        assert scan.uncovered_lines[seeded] == [1]
