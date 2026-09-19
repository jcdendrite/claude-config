"""Zero-reference tripwire for the shared shell libraries.

Every function defined at column 0 in `_lib.sh` and `_config.sh` must have its
name appear somewhere in the reference corpus outside its own definition line.
The corpus is every tracked shell file (via `scripts/list-shell-files.sh`) plus
every tracked `.py` file. Any mention satisfies the check, including one in a
test file, so a function consumed only by tests passes. This is a dead-code
check, not a test-coverage requirement and not a production-consumer check.

Scan rules:

  - Definitions match `^([a-z_][a-z0-9_]*)\\(\\)`, so `function foo() {`,
    `foo () {`, and indented helpers are not definitions.
  - Any path with a `plugins` segment relative to the corpus root is excluded
    from the corpus. Plugin hooks source their own vendored `_lib.sh`, so a
    same-named function there is a different function.
  - Comment lines (`^[[:space:]]*#`) are dropped before matching, in shell and
    Python alike, so a comment naming a function is not a caller.
  - This file is excluded from the corpus, so an example or diagnostic naming
    a function cannot satisfy the predicate it reports on.
  - A name matches only when the next character is not `[A-Za-z0-9_]`, so a
    longer sibling (`_lib_capped_for`) does not stand in for a shorter name
    (`_lib_capped`).

The scan is a name-reference check, not a call graph. It reports a suspicious
absence and does not prove reachability. These all still count as a reference:

  - A name that appears only in a Python docstring or a shell heredoc.
  - A trailing comment on a code line, since only whole-line comments are dropped.
  - A name embedded in a longer identifier on its left (`test_lib_x` for `_lib_x`),
    since only the right boundary is checked.
  - A function that only calls itself.
  - A function referenced only by another function that no production code
    references, so a dead-caller chain passes.
  - A function referenced only from test files.
  - An indented or mid-line `name() {` in a corpus file, such as a stub inside a
    Python string, since only column-0 definitions are excluded.

A function built by dynamic name concatenation is reported even when live,
since its literal name never appears.

The corpus boundary is a source of false violations. A consumer that appears
only in a workflow `run:` block, a JSON hook command string, or a fenced block
in a markdown skill body is invisible to the scan, so its function reads as
unreferenced.

The two lib-path constants are built from `HOOKS_DIR`, imported by bare name so
`TestCrossDomainReadCompleteness` can resolve them. Corpus enumeration is
invisible to that resolver, so the real-tree test selection is covered only by
this file living under `HOOKS_DIR`.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, REPO_ROOT

_LIB_SH = HOOKS_DIR / "_lib.sh"
_CONFIG_SH = HOOKS_DIR / "_config.sh"
_LIST_SHELL_FILES = REPO_ROOT / "scripts" / "list-shell-files.sh"
_SELF_PATH = Path(__file__).resolve()

_DEFINITION_RE = re.compile(r"^([a-z_][a-z0-9_]*)\(\)", re.MULTILINE)
_COMMENT_LINE_RE = re.compile(r"^[ \t]*#")
_EXCLUDED_NAMESPACE_SEGMENT = "plugins"


def _defined_functions(lib_text: str) -> list[str]:
    return _DEFINITION_RE.findall(lib_text)


def _without_comment_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _COMMENT_LINE_RE.match(line))


def _is_reference(corpus_text: str, name: str) -> bool:
    """True when `name` occurs in corpus_text anywhere but as a column-0 definition."""
    for match in re.finditer(rf"{re.escape(name)}(?![A-Za-z0-9_])", corpus_text):
        at_line_start = match.start() == 0 or corpus_text[match.start() - 1] == "\n"
        is_definition = at_line_start and corpus_text.startswith("()", match.end())
        if not is_definition:
            return True
    return False


def unreferenced_functions(
    lib_paths: list[Path],
    corpus_paths: list[Path],
    *,
    corpus_root: Path = REPO_ROOT,
    self_path: Path = _SELF_PATH,
) -> list[str]:
    """Names defined in lib_paths that no corpus file references outside a definition line.

    corpus_root anchors the `plugins/` exclusion to a path relative to the
    corpus, so a checkout that itself lives under a `plugins` directory is not
    excluded wholesale.
    """
    defined = [name for path in lib_paths for name in _defined_functions(path.read_text(errors="replace"))]
    resolved_self_path = self_path.resolve()
    corpus_text = "\n".join(
        _without_comment_lines(path.read_text(errors="replace"))
        for path in corpus_paths
        if path.resolve() != resolved_self_path
        and _EXCLUDED_NAMESPACE_SEGMENT not in path.relative_to(corpus_root).parts
        and path.is_file()
    )
    return [name for name in defined if not _is_reference(corpus_text, name)]


def _tracked_reference_corpus() -> list[Path]:
    shell_files = subprocess.run(
        ["bash", str(_LIST_SHELL_FILES)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    python_files = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    relative_paths = {p for p in (shell_files + python_files).split("\0") if p}
    return [REPO_ROOT / relative for relative in sorted(relative_paths)]


def _shell_declared_functions(lib_path: Path) -> set[str]:
    """Function names the shell defines after sourcing lib_path, including anything it sources.

    The environment is reduced to PATH so an exported function (`BASH_FUNC_*`) or a `BASH_ENV`
    file in the caller's shell cannot add names to `declare -F` that the libs never define.
    """
    declared = subprocess.run(
        ["bash", "-c", '. "$1"; declare -F', "bash", str(lib_path)],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.split()[-1] for line in declared.splitlines() if line}


@pytest.mark.parametrize("lib_path", [_LIB_SH, _CONFIG_SH], ids=lambda path: path.name)
def test_scanned_definitions_match_the_functions_the_shell_declares(lib_path: Path) -> None:
    """A function in a definition shape the scan skips (`function foo {`) would be exempt from the guard.

    `_lib.sh` sources `_config.sh`, so its declared set also holds config's functions.
    """
    scanned_from_lib = set(_defined_functions(lib_path.read_text(errors="replace")))
    scanned_from_either_lib = {
        name
        for scanned_lib in (_LIB_SH, _CONFIG_SH)
        for name in _defined_functions(scanned_lib.read_text(errors="replace"))
    }
    declared = _shell_declared_functions(lib_path)

    assert scanned_from_lib <= declared, (
        f"Names the scan extracts but the shell does not declare: {sorted(scanned_from_lib - declared)}"
    )
    assert declared <= scanned_from_either_lib, (
        f"Functions the shell declares but the scan skips, so the guard exempts them: "
        f"{sorted(declared - scanned_from_either_lib)}"
    )


def test_every_shell_lib_function_is_referenced_outside_its_definition() -> None:
    violations = unreferenced_functions([_LIB_SH, _CONFIG_SH], _tracked_reference_corpus())
    assert not violations, (
        "Functions defined in _lib.sh/_config.sh with no reference anywhere in the tracked "
        f"shell and Python corpus outside their own definition: {violations}. "
        "A function that is truly dead should be deleted. "
        "A function whose only consumer lives outside the scanned corpus (a workflow `run:` block, "
        "a JSON hook command, a markdown skill body) cannot be exempted, because no allowlist exists. "
        "For that case, add a tracked test in the scanned corpus that exercises the function and names it; "
        "the module docstring lists which mentions count. "
        "These do not count as a reference: a comment-only mention, a mention in this guard file, "
        "and a mention in any path with a `plugins` segment."
    )


class TestUnreferencedFunctions:
    """Pins the scan's rules against small synthetic trees, no real files involved."""

    @staticmethod
    def _write(root: Path, relative: str, text: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_reports_unreferenced_function_and_clears_referenced_one(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "used_fn() {\n  :\n}\nunused_fn() {\n  :\n}\n")
        caller = self._write(tmp_path, "caller.sh", "used_fn\n")

        assert unreferenced_functions([lib], [lib, caller], corpus_root=tmp_path) == ["unused_fn"]

    def test_reports_the_unreferenced_function_of_every_lib_path(self, tmp_path: Path) -> None:
        first_lib = self._write(tmp_path, "first_lib.sh", "unused_in_first_fn() {\n  :\n}\n")
        second_lib = self._write(tmp_path, "second_lib.sh", "unused_in_second_fn() {\n  :\n}\n")

        assert unreferenced_functions(
            [first_lib, second_lib], [first_lib, second_lib], corpus_root=tmp_path
        ) == ["unused_in_first_fn", "unused_in_second_fn"]

    def test_definition_line_alone_does_not_count_as_a_reference(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "lonely_fn() {\n  :\n}\n")

        assert unreferenced_functions([lib], [lib], corpus_root=tmp_path) == ["lonely_fn"]

    def test_reference_inside_the_lib_itself_counts(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "helper_fn() {\n  :\n}\nwrapper_fn() {\n  helper_fn\n}\n")

        assert unreferenced_functions([lib], [lib], corpus_root=tmp_path) == ["wrapper_fn"]

    def test_comment_only_mention_still_reports_the_function(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "commented_fn() {\n  :\n}\n")
        shell_comment = self._write(tmp_path, "caller.sh", "  # commented_fn is described here\n")
        python_comment = self._write(tmp_path, "helper.py", "# commented_fn is described here\n")

        assert unreferenced_functions(
            [lib], [lib, shell_comment, python_comment], corpus_root=tmp_path
        ) == ["commented_fn"]

    def test_mention_only_under_plugins_still_reports_the_function(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "vendored_fn() {\n  :\n}\n")
        plugin_caller = self._write(tmp_path, "plugins/some-plugin/hooks/caller.sh", "vendored_fn\n")

        assert unreferenced_functions([lib], [lib, plugin_caller], corpus_root=tmp_path) == ["vendored_fn"]

    def test_checkout_located_under_a_plugins_directory_is_not_excluded_wholesale(self, tmp_path: Path) -> None:
        checkout = tmp_path / "plugins" / "checkout"
        lib = self._write(checkout, "lib.sh", "live_fn() {\n  :\n}\n")
        caller = self._write(checkout, "caller.sh", "live_fn\n")

        assert unreferenced_functions([lib], [lib, caller], corpus_root=checkout) == []

    @pytest.mark.parametrize(
        "non_definition_line",
        [
            "function keyword_fn() {",
            "space_before_paren_fn () {",
            "  indented_fn() {",
        ],
    )
    def test_non_column_zero_paren_definition_shapes_are_not_picked_up(
        self, non_definition_line: str
    ) -> None:
        assert _defined_functions(f"{non_definition_line}\n  :\n}}\n") == []

    def test_column_zero_paren_definition_is_picked_up(self) -> None:
        assert _defined_functions("plain_fn() {\n  :\n}\n") == ["plain_fn"]

    def test_corpus_files_are_joined_with_a_newline_so_a_definition_stays_at_line_start(
        self, tmp_path: Path
    ) -> None:
        preceding_file = self._write(tmp_path, "other.sh", "echo done")
        lib = self._write(tmp_path, "lib.sh", "lonely_fn() {\n  :\n}\n")

        assert unreferenced_functions(
            [lib], [preceding_file, lib], corpus_root=tmp_path
        ) == ["lonely_fn"]

    @pytest.mark.parametrize(
        "stub_line",
        [
            "  stubbed_fn() { return 1; }",
            "setup; stubbed_fn() { return 1; }",
        ],
    )
    def test_indented_or_mid_line_definition_stub_in_corpus_counts_as_a_reference(
        self, tmp_path: Path, stub_line: str
    ) -> None:
        lib = self._write(tmp_path, "lib.sh", "stubbed_fn() {\n  :\n}\n")
        stub_holder = self._write(tmp_path, "test_stub.py", f"{stub_line}\n")

        assert unreferenced_functions([lib], [lib, stub_holder], corpus_root=tmp_path) == []

    def test_missing_corpus_file_is_skipped_rather_than_raising(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "kept_fn() {\n  :\n}\n")
        deleted_in_worktree = tmp_path / "deleted.sh"

        assert unreferenced_functions([lib], [lib, deleted_in_worktree], corpus_root=tmp_path) == ["kept_fn"]

    def test_self_path_is_excluded_when_spelled_non_normalized(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "exempted_fn() {\n  :\n}\n")
        guard = self._write(tmp_path, "test_guard.py", 'ALLOWLIST = ["exempted_fn"]\n')
        non_normalized_guard = tmp_path / "nested" / ".." / "test_guard.py"
        (tmp_path / "nested").mkdir()

        assert unreferenced_functions(
            [lib], [lib, guard], corpus_root=tmp_path, self_path=non_normalized_guard
        ) == ["exempted_fn"]

    def test_mention_only_in_the_guards_own_file_still_reports_the_function(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "exempted_fn() {\n  :\n}\n")
        guard = self._write(tmp_path, "test_guard.py", 'ALLOWLIST = ["exempted_fn"]\n')

        assert unreferenced_functions(
            [lib], [lib, guard], corpus_root=tmp_path, self_path=guard
        ) == ["exempted_fn"]

    def test_longer_sibling_name_does_not_satisfy_a_shorter_names_reference(self, tmp_path: Path) -> None:
        lib = self._write(tmp_path, "lib.sh", "foo() {\n  :\n}\nfoo_bar() {\n  :\n}\n")
        caller = self._write(tmp_path, "caller.sh", "foo_bar\n")

        assert unreferenced_functions([lib], [lib, caller], corpus_root=tmp_path) == ["foo"]
