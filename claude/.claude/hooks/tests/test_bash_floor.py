"""Runtime bash floor check: the predicate, its copies, and who reaches it.

`_config.sh` opens with the floor check, and each hook-bearing plugin's `_lib.sh`
carries a copy of the same constants, predicate and call. Below the floor the
sourcing file returns 1, which every gate bootstrap turns into a deny.

Sections:

- the predicate over a version matrix, in every copy;
- parity of the copies, as sourced values and as normalized call text;
- placement: the check is the first command, and no other shipped file defines it;
- a text tripwire for post-3.2 syntax over these regions:
  - the top of `_config.sh`, through the `return 1` that closes the floor check;
  - the top of each plugin `_lib.sh` copy, through the same `return 1`;
  - the top of `install.sh`, through its `_config.sh` source line;
- the documentation headings the floor messages and floor docs cite;
- sourcing `_config.sh` below, at and above the floor;
- the scripts that source `_config.sh` and stop when it fails;
- sourcing the copies under a real below-floor bash, where one is available;
- the CI runner's bash equals the floor.

The platform-specific message sections are asserted in `test_install_sh_bash_floor.py`.
The tripwire does not scan the gate pre-bootstrap region or the script pre-source region,
which `.claude/rules/shell-floor.md` also binds to bash 3.2 syntax.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from helpers import HOOKS_DIR, REPO_ROOT, SCRIPTS_DIR, heading_texts, normalize_heading

from .test_shell_dialect import _shipped_shell_files

_BASH = shutil.which("bash") or "bash"

_CONFIG_SH = HOOKS_DIR / "_config.sh"
_INSTALL_SH = REPO_ROOT / "install.sh"
_PLUGIN_LIBS = sorted((REPO_ROOT / "plugins").glob("*/hooks/_lib.sh"))
_FLOOR_COPIES = [_CONFIG_SH, *_PLUGIN_LIBS]
_FLOOR_COPY_PARAMS = [pytest.param(path, id=str(path.relative_to(REPO_ROOT))) for path in _FLOOR_COPIES]

_EXPECTED_PLUGINS_WITH_A_FLOOR_COPY = frozenset({"skill-management", "npm-semver", "plugin-semver", "lovable-cloud"})

_CALL_LINE = 'if ! _bash_version_meets_floor "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"; then'


class Citation(NamedTuple):
    """A heading in a repo document that a floor message or floor doc cites."""

    document: str
    heading: str

    @property
    def text(self) -> str:
        return f'{self.document} § "{self.heading}"'


README_REQUIREMENTS = Citation("README.md", "Requirements")
HOOKS_DOC_GATE_DEADLOCK_RECOVERY = Citation("docs/hooks.md", "Gate deadlock recovery")
HOOKS_DOC_BASH_VERSION = Citation("docs/hooks.md", "Bash version")


def exact_output_env() -> dict[str, str]:
    """An environment for runs whose output is compared exactly: no startup
    files injected into the child bash, and a fixed locale."""
    env = {k: v for k, v in os.environ.items() if k not in {"BASH_ENV", "ENV"}}
    env["LC_ALL"] = "C"
    return env


def _run_in_bash(script: str, *args: str, bash: str = _BASH) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bash, "-c", script, "_", *args],
        capture_output=True,
        text=True,
        check=False,
        env=exact_output_env(),
    )


def _floor_constants(path: Path) -> tuple[int, int]:
    text = path.read_text()
    major = re.search(r"^_BASH_FLOOR_MAJOR=(\d+)$", text, re.MULTILINE)
    minor = re.search(r"^_BASH_FLOOR_MINOR=(\d+)$", text, re.MULTILINE)
    assert major and minor, f"{path} does not assign both floor constants"
    return int(major.group(1)), int(minor.group(1))


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _first_command_line(path: Path) -> str:
    for line in path.read_text().splitlines()[1:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    raise AssertionError(f"{path} has no command line")


_FLOOR_DEFINITION_PATTERNS = (
    # An assignment-like form of a floor constant, including `:=` defaults and `+=`.
    re.compile(r"_BASH_FLOOR_\w+(?::?=|\+=)"),
    # A declaration of a floor constant, with or without a value.
    re.compile(r"^\s*(?:readonly|declare|typeset|local|export)\b[^#\n]*\b_BASH_FLOOR_", re.MULTILINE),
    # A definition of the predicate, in either function syntax.
    re.compile(r"^\s*(?:function\s+_bash_version_meets_floor\b|_bash_version_meets_floor\s*\(\))", re.MULTILINE),
)


def defines_floor(text: str) -> bool:
    """True when a non-comment line of `text` defines a floor constant or the predicate."""
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return any(pattern.search(code) for pattern in _FLOOR_DEFINITION_PATTERNS)


def _sub_floor_bash() -> str | None:
    """A bash older than the floor: `/bin/bash` (3.2 on macOS) or the first `bash` on PATH, else None."""
    floor = _floor_constants(_CONFIG_SH)
    for candidate in ("/bin/bash", shutil.which("bash")):
        if candidate is None or not Path(candidate).is_file():
            continue
        probe = _run_in_bash('printf "%s %s" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"', bash=candidate)
        if probe.returncode != 0:
            continue
        major, minor = (int(part) for part in probe.stdout.split())
        if (major, minor) < floor:
            return candidate
    return None


SUB_FLOOR_BASH = _sub_floor_bash()
requires_sub_floor_bash = pytest.mark.skipif(
    SUB_FLOOR_BASH is None, reason="no bash below the floor is available (macOS /bin/bash is one)"
)


class TestFloorCopyDiscovery:
    def test_every_hook_bearing_plugin_has_a_floor_copy(self) -> None:
        plugins_with_a_copy = {path.parent.parent.name for path in _PLUGIN_LIBS}
        assert plugins_with_a_copy == _EXPECTED_PLUGINS_WITH_A_FLOOR_COPY


class TestPredicate:
    @pytest.mark.parametrize("copy", _FLOOR_COPY_PARAMS)
    @pytest.mark.parametrize(
        ("major", "minor", "expected_status"),
        [
            pytest.param("3", "2", 1, id="3.2-below"),
            pytest.param("4", "4", 1, id="4.4-below"),
            pytest.param("5", "0", 1, id="5.0-below"),
            pytest.param("5", "1", 1, id="5.1-below"),
            pytest.param("5", "2", 0, id="5.2-at-floor"),
            pytest.param("5", "3", 0, id="5.3-above"),
            pytest.param("5", "10", 0, id="5.10-two-digit-minor-above"),
            pytest.param("5", "20", 0, id="5.20-two-digit-minor-above"),
            pytest.param("4", "10", 1, id="4.10-two-digit-minor-below"),
            pytest.param("6", "0", 0, id="6.0-above"),
            pytest.param("", "", 1, id="empty-both"),
            pytest.param("5", "", 1, id="empty-minor"),
            pytest.param("", "2", 1, id="empty-major"),
            pytest.param("five", "2", 1, id="non-numeric-major"),
            pytest.param("5", "x", 1, id="non-numeric-minor"),
            pytest.param("5.2", "0", 1, id="dotted-major"),
        ],
    )
    def test_version_matrix(self, copy: Path, major: str, minor: str, expected_status: int) -> None:
        result = _run_in_bash(
            '. "$1" >/dev/null 2>&1; _bash_version_meets_floor "$2" "$3"; echo "$?"',
            str(copy),
            major,
            minor,
        )
        assert result.stdout.strip() == str(expected_status), (
            f"{copy.name} [{major!r}, {minor!r}]: stdout={result.stdout!r} stderr={result.stderr!r}"
        )


class TestCopyParity:
    @staticmethod
    def _sourced_definition(path: Path) -> str:
        major, minor = _floor_constants(path)
        result = _run_in_bash(
            '. "$1" >/dev/null 2>&1; printf "%s.%s\\n" "$_BASH_FLOOR_MAJOR" "$_BASH_FLOOR_MINOR"; '
            "declare -f _bash_version_meets_floor",
            str(path),
        )
        assert result.stdout.startswith(f"{major}.{minor}\n"), (
            f"{path} sourced to different floor constants than it assigns: {result.stdout!r}"
        )
        assert "_bash_version_meets_floor ()" in result.stdout, f"{path} does not define the predicate"
        return result.stdout

    @pytest.mark.parametrize("plugin_lib", [pytest.param(p, id=p.parent.parent.name) for p in _PLUGIN_LIBS])
    def test_constants_and_predicate_match_config_sh(self, plugin_lib: Path) -> None:
        assert self._sourced_definition(plugin_lib) == self._sourced_definition(_CONFIG_SH), (
            f"{plugin_lib.relative_to(REPO_ROOT)} diverges from {_CONFIG_SH.relative_to(REPO_ROOT)} "
            "in its floor constants or _bash_version_meets_floor"
        )

    @pytest.mark.parametrize("copy", _FLOOR_COPY_PARAMS)
    def test_call_line_and_return_are_the_pinned_text(self, copy: Path) -> None:
        """Only a text pin catches a call site that passes literal numbers or
        a parsed `$BASH_VERSION`: while CI's bash equals the floor, no
        behavioral test can tell those from `BASH_VERSINFO`."""
        text = _normalized(copy.read_text())
        assert text.count(_CALL_LINE) == 1, f"{copy.name}: expected exactly one pinned call line"
        invocations = re.findall(r"\b_bash_version_meets_floor (?!\()", text)
        assert len(invocations) == 1, f"{copy.name}: the predicate must be invoked only by the pinned call line"
        after_call = text.split(_CALL_LINE, 1)[1]
        first_return = after_call.find("return 1")
        assert first_return != -1, f"{copy.name}: no `return 1` after the call line"
        assert after_call[first_return:].startswith("return 1 fi"), (
            f"{copy.name}: the first `return 1` after the call line must close the `if` with `fi`"
        )


class TestPlacement:
    @pytest.mark.parametrize("copy", _FLOOR_COPY_PARAMS)
    def test_floor_constants_are_the_first_command(self, copy: Path) -> None:
        assert _first_command_line(copy).startswith("_BASH_FLOOR_MAJOR="), (
            f"{copy.name}: the floor check must precede every other command"
        )

    def test_lib_sh_first_command_sources_config_sh(self) -> None:
        first_command = _first_command_line(HOOKS_DIR / "_lib.sh")
        assert first_command.startswith("if ! . ") and first_command.endswith('/_config.sh"; then'), first_command

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("_BASH_FLOOR_MAJOR=5\n", True, id="plain-assignment"),
            pytest.param("readonly _BASH_FLOOR_MAJOR=5\n", True, id="readonly-assignment"),
            pytest.param("declare -r _BASH_FLOOR_MINOR=2\n", True, id="declare-assignment"),
            pytest.param('  : "${_BASH_FLOOR_MAJOR:=5}"\n', True, id="default-assignment"),
            pytest.param("export _BASH_FLOOR_MAJOR\n", True, id="export-without-value"),
            pytest.param("_bash_version_meets_floor() {\n", True, id="function-with-parens"),
            pytest.param("_bash_version_meets_floor () {\n", True, id="function-with-spaced-parens"),
            pytest.param("function _bash_version_meets_floor {\n", True, id="function-keyword"),
            pytest.param("# _BASH_FLOOR_MAJOR=5\n", False, id="comment-only-mention"),
            pytest.param('echo "$_BASH_FLOOR_MAJOR"\n', False, id="read-of-constant"),
            pytest.param('[ "$x" -ge "$_BASH_FLOOR_MINOR" ]\n', False, id="comparison-against-constant"),
            pytest.param('if ! _bash_version_meets_floor "$a" "$b"; then\n', False, id="call-of-predicate"),
            pytest.param("echo hi\n", False, id="unrelated-line"),
        ],
    )
    def test_floor_definition_matcher_control_table(self, text: str, expected: bool) -> None:
        assert defines_floor(text) is expected

    def test_shell_corpus_sentinel_is_not_empty(self) -> None:
        corpus = _shipped_shell_files()
        assert "install.sh" in corpus
        assert "claude/.claude/hooks/_lib.sh" in corpus

    def test_no_other_shipped_file_defines_the_floor(self) -> None:
        definition_files = {str(path.relative_to(REPO_ROOT)) for path in _FLOOR_COPIES}
        definers = [
            rel_path
            for rel_path in _shipped_shell_files()
            if rel_path not in definition_files
            and defines_floor((REPO_ROOT / rel_path).read_text(encoding="utf-8", errors="replace"))
        ]
        assert definers == [], f"floor constants or predicate defined outside the floor copies: {definers}"


_BANNED_PRE_FLOOR_PATTERNS = {
    "associative array": re.compile(r"\b(?:declare|local|typeset|readonly)\s+-\w*A"),
    "mapfile/readarray": re.compile(r"\b(?:mapfile|readarray)\b"),
    "case-modifying expansion": re.compile(
        r"\$\{!?(?:[A-Za-z_]\w*(?:\[[^\]]*\])?|[0-9]+|[@*?$!#-])(?:,,?|\^\^?)[^}]*\}"
    ),
    "[[ -v": re.compile(r"(?:\[\[?|&&|\|\|)\s+(?:!\s+)?-v\s"),
    "case fall-through terminator": re.compile(r";;?&"),
    "|&": re.compile(r"\|&"),
    "&>>": re.compile(r"&>>"),
    "nameref": re.compile(r"\b(?:local|declare|typeset)\s+-[A-Za-z]*n"),
}


def banned_pre_floor_constructs(text: str) -> list[str]:
    """Names of the post-3.2 constructs found on a non-comment line of `text`."""
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return [name for name, pattern in _BANNED_PRE_FLOOR_PATTERNS.items() if pattern.search(code)]


_FLOOR_CALL_RE = re.compile(r"if\s+!\s+_bash_version_meets_floor\s")
_RETURN_1_RE = re.compile(r"return\s+1\b")
_INSTALL_SH_CONFIG_SOURCE_RE = re.compile(r'\.\s+"\$REPO_DIR/claude/\.claude/hooks/_config\.sh"\s+\|\|\s+exit\s+1')

_FLOOR_REGION_FILES = [
    *_FLOOR_COPY_PARAMS,
    pytest.param(_INSTALL_SH, id="install.sh"),
]


def _floor_region(path: Path) -> str:
    """The text from the top of `path` through the end of its floor check.

    A floor copy ends at the `return 1` that closes its check. install.sh ends at the line
    that ends the run when `_config.sh` fails to source. Whitespace between tokens is
    matched loosely, as `test_call_line_and_return_are_the_pinned_text` normalizes it."""
    text = path.read_text()
    if path == _INSTALL_SH:
        region_end = _INSTALL_SH_CONFIG_SOURCE_RE.search(text)
    else:
        call = _FLOOR_CALL_RE.search(text)
        region_end = call and _RETURN_1_RE.search(text, call.end())
    assert region_end, f"{path.relative_to(REPO_ROOT)}: cannot find the end of the floor-check region to scan"
    return text[: region_end.end()]


class TestFloorCheckRegionAvoidsPost32Syntax:
    """Text-level tripwire: a bash below the floor can only be run where one is installed,
    and CI never has one, so these constructs would otherwise reach a user as a parse error
    instead of the fix message.

    The module docstring lists the scanned and unscanned regions.

    The banned list is a recall net for the common spellings of each construct, not a parser."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("declare -A seen\n", ["associative array"], id="declare-associative"),
            pytest.param("local -A seen\n", ["associative array"], id="local-associative"),
            pytest.param("mapfile -t lines < f\n", ["mapfile/readarray"], id="mapfile"),
            pytest.param("readarray -t lines < f\n", ["mapfile/readarray"], id="readarray"),
            pytest.param('echo "${value,,}"\n', ["case-modifying expansion"], id="lowercase-all"),
            pytest.param('echo "${value^^}"\n', ["case-modifying expansion"], id="uppercase-all"),
            pytest.param('echo "${arr[0],}"\n', ["case-modifying expansion"], id="lowercase-first-of-element"),
            pytest.param('echo "${1,,}"\n', ["case-modifying expansion"], id="lowercase-all-of-positional"),
            pytest.param('echo "${@^^}"\n', ["case-modifying expansion"], id="uppercase-all-of-special-parameter"),
            pytest.param('[[ -v name ]] && echo set\n', ["[[ -v"], id="double-bracket-v"),
            pytest.param('[ -v name ] && echo set\n', ["[[ -v"], id="single-bracket-v"),
            pytest.param("[[ ! -v name ]]\n", ["[[ -v"], id="double-bracket-negated-v"),
            pytest.param("[ ! -v name ]\n", ["[[ -v"], id="single-bracket-negated-v"),
            pytest.param('[[ -n "$a" && -v name ]]\n', ["[[ -v"], id="v-joined-by-and"),
            pytest.param('[[ -z "$a" || -v name ]]\n', ["[[ -v"], id="v-joined-by-or"),
            pytest.param('[[ -n "$a" && ! -v name ]]\n', ["[[ -v"], id="negated-v-joined-by-and"),
            pytest.param("case $x in a) echo a ;& b) echo b ;; esac\n", ["case fall-through terminator"], id="case-fall-through"),
            pytest.param(
                "case $x in a) echo a ;;& b) echo b ;; esac\n", ["case fall-through terminator"], id="case-continue-testing"
            ),
            pytest.param("a |& b\n", ["|&"], id="pipe-both"),
            pytest.param("cmd &>> log\n", ["&>>"], id="append-both"),
            pytest.param("local -n ref=target\n", ["nameref"], id="local-nameref"),
            pytest.param("declare -n ref=target\n", ["nameref"], id="declare-nameref"),
            pytest.param("declare -a plain\nlocal -a words=()\n", [], id="indexed-arrays"),
            pytest.param('printf "%s" "${1-}" "${2:-a,b}"\n', [], id="plain-expansions-with-a-comma-in-the-default"),
            pytest.param('[ -n "$x" ] && [[ "$x" =~ ^[0-9]+$ ]]\n', [], id="tests-available-on-3.2"),
            pytest.param("command -v foo >/dev/null 2>&1\n", [], id="command-v-is-not-a-variable-test"),
            pytest.param('[ -n "$x" ] && command -v foo\n', [], id="command-v-after-a-joiner"),
            pytest.param("case $x in a) echo a ;; b) echo b ;; esac\n", [], id="case-terminators-on-3.2"),
            pytest.param('printf "%s" "${1-}" "${@:-x}"\n', [], id="positional-expansions-without-case-modification"),
            pytest.param("a >log 2>&1 | b\n", [], id="redirects-and-pipes"),
            pytest.param("# declare -A mapfile ${x,,} [[ -v |& &>> local -n\n", [], id="comment-only-mentions"),
        ],
    )
    def test_banned_construct_control_table(self, text: str, expected: list[str]) -> None:
        assert banned_pre_floor_constructs(text) == expected

    @pytest.mark.parametrize("path", _FLOOR_REGION_FILES)
    def test_the_region_holds_no_post_3_2_construct(self, path: Path) -> None:
        region = _floor_region(path)
        assert len(region.splitlines()) > 5, (
            f"{path.relative_to(REPO_ROOT)}: the region is vacuous, the slice found nothing to scan"
        )
        assert banned_pre_floor_constructs(region) == []


class TestFloorMessageCitationsResolve:
    """The floor messages and floor docs are the only on-ramp while every gate denies,
    so a renamed heading they cite must fail here. The README Requirements and
    docs/hooks.md Gate deadlock recovery citations are also asserted against rendered
    output in test_install_sh_bash_floor.py. The "Bash version" citation appears only in
    static docs and comments, so for it this test checks only that the heading exists."""

    @pytest.mark.parametrize(
        "citation",
        [
            pytest.param(README_REQUIREMENTS, id="readme-requirements"),
            pytest.param(HOOKS_DOC_GATE_DEADLOCK_RECOVERY, id="hooks-doc-gate-deadlock-recovery"),
            pytest.param(HOOKS_DOC_BASH_VERSION, id="hooks-doc-bash-version"),
        ],
    )
    def test_cited_heading_exists_in_the_cited_document(self, citation: Citation) -> None:
        document_text = (REPO_ROOT / citation.document).read_text()
        assert normalize_heading(citation.heading) in heading_texts(document_text), (
            f"{citation.document} has no heading {citation.heading!r}"
        )


class TestSourcingConfigSh:
    def test_below_the_floor_returns_1_with_the_fix_on_stderr(self, tmp_path: Path) -> None:
        raised_floor = tmp_path / "_config.sh"
        raised_text = _CONFIG_SH.read_text()
        for constant, value in (("_BASH_FLOOR_MAJOR", "99"), ("_BASH_FLOOR_MINOR", "0")):
            raised_text, replaced = re.subn(rf"^{constant}=\d+$", f"{constant}={value}", raised_text, flags=re.MULTILINE)
            assert replaced == 1
        raised_floor.write_text(raised_text)

        result = _run_in_bash('printf "%s\\n%s\\n" "$BASH_VERSION" "$BASH"; . "$1"; echo "status=$?" >&2', str(raised_floor))

        running_version, running_bash = result.stdout.splitlines()
        assert result.stderr.endswith("status=1\n"), result.stderr
        assert running_version in result.stderr
        assert running_bash in result.stderr
        assert "99.0" in result.stderr
        assert "terminal, not from inside Claude Code" in result.stderr

    def test_at_the_real_floor_returns_0_silently(self) -> None:
        result = _run_in_bash('. "$1"; echo "status=$?"', str(_CONFIG_SH))

        assert result.stdout == "status=0\n"
        assert result.stderr == ""

    @pytest.mark.parametrize("copy", _FLOOR_COPY_PARAMS)
    def test_an_inherited_floor_value_does_not_raise_the_floor(self, copy: Path) -> None:
        """The constants are assigned unconditionally, so an exported 99 would
        fail the check if it were honored: sourcing still returns 0 at the real floor."""
        result = _run_in_bash(
            'export _BASH_FLOOR_MAJOR=99; . "$1" >/dev/null 2>&1; echo "status=$?"',
            str(copy),
        )
        assert result.stdout == "status=0\n", f"{copy.name}: {result.stdout!r} {result.stderr!r}"


class TestCallersStopWhenConfigShFails:
    """Each script reaches `_config.sh` before any other library. The copy sits beside a stub
    `hooks/_config.sh` that prints a marker and returns 1, and `_worktree-lib.sh` is
    deliberately not copied, so a source line moved below it fails with different stderr.
    A script that reaches `_config.sh` through `hooks/_lib.sh` also gets the real `_lib.sh` copied."""

    _MARKER = "stub _config.sh was sourced"
    _SCRIPTS_SOURCING_LIB_SH_FIRST = frozenset({"cleanup-merged-branches.sh"})

    @pytest.mark.parametrize(
        ("script_name", "expected_status"),
        [
            pytest.param("worktree-removal-status.sh", 1, id="worktree-removal-status"),
            pytest.param("cleanup-idle-open-pr-worktrees.sh", 1, id="cleanup-idle-open-pr-worktrees"),
            pytest.param("cleanup-merged-branches.sh", 1, id="cleanup-merged-branches"),
            pytest.param("config-get.sh", 3, id="config-get"),
        ],
    )
    def test_script_stops_on_a_failed_config_sh_source(self, tmp_path: Path, script_name: str, expected_status: int) -> None:
        scripts_dir = tmp_path / "scripts"
        hooks_dir = tmp_path / "hooks"
        scripts_dir.mkdir()
        hooks_dir.mkdir()
        script_copy = scripts_dir / script_name
        shutil.copy2(SCRIPTS_DIR / script_name, script_copy)
        (hooks_dir / "_config.sh").write_text(f"printf '%s' '{self._MARKER}' >&2\nreturn 1\n")
        if script_name in self._SCRIPTS_SOURCING_LIB_SH_FIRST:
            shutil.copy2(HOOKS_DIR / "_lib.sh", hooks_dir / "_lib.sh")

        result = subprocess.run(
            [_BASH, str(script_copy)],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp_path,
            env=exact_output_env(),
        )

        assert result.returncode == expected_status, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stderr == self._MARKER


@requires_sub_floor_bash
class TestCopiesUnderARealBelowFloorBash:
    """Everything down to each copy's `return 1` must parse and run on the older bash,
    which only a bash below the floor can show (macOS `/bin/bash` is one)."""

    @pytest.mark.parametrize("copy", _FLOOR_COPY_PARAMS)
    def test_sourcing_returns_1(self, copy: Path) -> None:
        result = _run_in_bash('. "$1" >/dev/null 2>&1; echo "status=$?"', str(copy), bash=SUB_FLOOR_BASH)

        assert result.stdout == "status=1\n", f"{copy.name}: stdout={result.stdout!r} stderr={result.stderr!r}"

    def test_config_sh_prints_the_fix_naming_the_running_bash(self) -> None:
        identity = _run_in_bash('printf "%s\\n%s\\n" "$BASH_VERSION" "$BASH"', bash=SUB_FLOOR_BASH)
        running_version, running_bash = identity.stdout.splitlines()

        result = _run_in_bash('. "$1"; echo "status=$?" >&2', str(_CONFIG_SH), bash=SUB_FLOOR_BASH)

        assert result.stderr.endswith("status=1\n"), result.stderr
        assert running_version in result.stderr
        assert running_bash in result.stderr
        assert "terminal, not from inside Claude Code" in result.stderr


@pytest.mark.skipif(
    not os.environ.get("GITHUB_ACTIONS"),
    reason="CI-only: compares the floor to the runner image's bash",
)
def test_ci_bash_equals_the_floor() -> None:
    result = _run_in_bash('printf "%s.%s" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"')
    floor_major, floor_minor = _floor_constants(_CONFIG_SH)
    assert result.stdout == f"{floor_major}.{floor_minor}", (
        f"CI's bash is {result.stdout} but the floor is {floor_major}.{floor_minor}. "
        "A runner-image change must revisit the floor (README.md Requirements, the _BASH_FLOOR_ "
        "constants, and the plugin deny text)."
    )
