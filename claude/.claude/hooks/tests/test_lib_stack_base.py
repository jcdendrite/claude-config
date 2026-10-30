"""Unit tests for _lib.sh's _lib_configured_stack_base, _lib_print_unusable_stack_base, _lib_stack_base_name_shape
and _lib_open_pr_base."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _make_git_exiting_with_status,
    init_git_repo_with_commit,
)

from ._lib_sh_helpers import (
    _LIB_SH,
)
from .test_config_lib import _locale_that_widens_ascii_ranges

# --- _lib_configured_stack_base / _lib_print_unusable_stack_base -------------

_STACK_BASE_STDERR_PREFIX = "some-script.sh"


def _configured_stack_base(
    repo_root: Path, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", '. "$1"; _lib_configured_stack_base "$2" "$3"', "bash",
         str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _repo_on_branch_with_key(tmp_path: Path, branch: str, key_value: str | None) -> Path:
    repo = init_git_repo_with_commit(tmp_path / "repo", branch="main")
    subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True)
    if key_value is not None:
        subprocess.run(
            ["git", "config", f"branch.{branch}.gh-merge-base", key_value], cwd=repo, check=True
        )
    return repo


def _assert_stack_base_stop(result: subprocess.CompletedProcess, branch: str) -> None:
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith(f"{_STACK_BASE_STDERR_PREFIX}: ")
    assert f"branch.{branch}.gh-merge-base" in result.stderr
    assert "The engineer runs the fix, not the agent" in result.stderr
    assert result.stderr.count("\n") == 1


class TestLibConfiguredStackBase:
    """Exit 0 prints "<branch> <stack-base>", exit 1 is "no key or detached
    HEAD", exit 2 is a stop whose one stderr line comes from
    _lib_print_unusable_stack_base. Every non-zero exit has empty stdout."""

    def test_unset_key_exits_1_with_no_output(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", None)
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    def test_valid_slashed_value_prints_branch_and_value(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "feat/base")
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (0, "feat/x feat/base\n", "")

    def test_dotted_branch_name_reads_its_own_key(self, tmp_path: Path) -> None:
        """Git's subsection grammar keeps every dot between the first and last in the
        branch name, so a name such as `release/1.2` must still resolve to its key."""
        repo = _repo_on_branch_with_key(tmp_path, "release/1.2", "feat/base")
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (0, "release/1.2 feat/base\n", "")

    def test_detached_head_exits_1_even_with_a_key_on_some_branch(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "stack")
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    def test_key_set_in_main_worktree_is_read_from_a_linked_worktree(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        worktree = tmp_path / "linked-wt"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "linked-branch", str(worktree)], cwd=git_repo, check=True
        )
        subprocess.run(
            ["git", "config", "branch.linked-branch.gh-merge-base", "stack"], cwd=git_repo, check=True
        )
        result = _configured_stack_base(worktree)
        assert (result.returncode, result.stdout) == (0, "linked-branch stack\n")

    def test_key_set_only_in_the_global_config_file_is_read(self, tmp_path: Path) -> None:
        """The key is read from every config scope, not only the repository's."""
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", None)
        global_config = tmp_path / "global.gitconfig"
        subprocess.run(
            ["git", "config", "--file", str(global_config), "branch.feat/x.gh-merge-base", "stack"],
            check=True,
        )
        env = {**os.environ, "GIT_CONFIG_GLOBAL": str(global_config)}
        result = _configured_stack_base(repo, env)
        assert (result.returncode, result.stdout, result.stderr) == (0, "feat/x stack\n", "")

    def test_rejects_a_non_ascii_letter_under_a_locale_that_widens_bracket_ranges(
        self, tmp_path: Path
    ) -> None:
        """With `globasciiranges` off, a collating locale widens `[A-Za-z]` to admit an
        accented letter; the charset check lists its letters, so it still rejects one.
        The script first asserts, in the same shell and locale, that the range form
        would accept the probe, so a locale that does not widen fails the premise."""
        premise_failed = 3
        accented_value = "caf" + chr(0xE9)
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", accented_value)
        script = (
            '. "$1"; shopt -u globasciiranges; '
            f'case "$4" in *[!A-Za-z0-9._/-]*) exit {premise_failed} ;; esac; '
            '_lib_configured_stack_base "$2" "$3"'
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo), accented_value],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": _locale_that_widens_ascii_ranges()},
        )
        assert result.returncode != premise_failed, "the range form rejects the probe, so this locale does not widen it"
        _assert_stack_base_stop(result, "feat/x")
        assert "outside A-Z" in result.stderr

    @pytest.mark.parametrize(
        ("value", "reason_fragment"),
        [
            pytest.param("", "the value is empty", id="empty"),
            pytest.param("-x", "starts with '-'", id="leading-dash"),
            pytest.param("a..b", "not a valid branch name", id="double-dot"),
            pytest.param("@{-1}", "not a valid branch name", id="reflog-expression"),
            pytest.param("a b", "not a valid branch name", id="space"),
            pytest.param("feat/x", "this branch itself", id="own-branch-name"),
            pytest.param("origin/stack", "starts with 'origin/'", id="origin-prefix"),
            pytest.param("HEAD", "the value is HEAD", id="head"),
            pytest.param("caf" + chr(0xE9), "outside A-Z", id="non-ascii"),
            pytest.param("a" + chr(0x202E) + "b", "outside A-Z", id="right-to-left-override"),
            pytest.param("a:b", "not a valid branch name", id="colon"),
            pytest.param("a~1", "not a valid branch name", id="tilde"),
            pytest.param("a^", "not a valid branch name", id="caret"),
            pytest.param("a?", "not a valid branch name", id="question-mark"),
            pytest.param("a*", "not a valid branch name", id="asterisk"),
            pytest.param("a[", "not a valid branch name", id="open-bracket"),
            pytest.param("a\\", "not a valid branch name", id="backslash"),
            pytest.param("a\tb", "not a valid branch name", id="tab"),
            pytest.param("a\x1bb", "not a valid branch name", id="escape-control"),
            pytest.param("a.lock", "not a valid branch name", id="lock-suffix"),
            # Values built only from allowed characters, so check-ref-format alone rejects them.
            pytest.param("/stack", "not a valid branch name", id="leading-slash"),
            pytest.param("stack/", "not a valid branch name", id="trailing-slash"),
            pytest.param("a//b", "not a valid branch name", id="double-slash"),
            pytest.param("stack.", "not a valid branch name", id="trailing-dot"),
            pytest.param("a/.b", "not a valid branch name", id="component-starting-with-dot"),
        ],
    )
    def test_unusable_value_stops_naming_key_and_reason(
        self, tmp_path: Path, value: str, reason_fragment: str
    ) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", value)
        result = _configured_stack_base(repo)
        _assert_stack_base_stop(result, "feat/x")
        assert reason_fragment in result.stderr

    @pytest.mark.parametrize("git_status", [128, 124])
    @pytest.mark.parametrize(
        ("failing_subcommand", "failed_call"),
        [
            pytest.param("symbolic-ref", "git symbolic-ref HEAD", id="head-read"),
            pytest.param("config", "git config branch.feat/x.gh-merge-base", id="key-read"),
            pytest.param(
                "check-ref-format",
                "git check-ref-format on the value of branch.feat/x.gh-merge-base",
                id="value-check",
            ),
        ],
    )
    def test_git_read_failure_stops_with_its_own_line_instead_of_reading_as_unset(
        self, tmp_path: Path, failing_subcommand: str, failed_call: str, git_status: int
    ) -> None:
        """A failed read prints one line naming the git call and its status; it carries
        no key value, no key-edit fix and no placeholder branch."""
        key_value = "sentinel-base"
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", key_value)
        bin_dir = tmp_path / "bin-git-read-fails"
        _make_git_exiting_with_status(bin_dir, failing_subcommand, git_status)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _configured_stack_base(repo, env)
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith(f"{_STACK_BASE_STDERR_PREFIX}: ")
        assert result.stderr.count("\n") == 1
        assert f"{failed_call} failed (status {git_status})" in result.stderr
        assert "retry the command" in result.stderr
        assert "run that git call by hand" in result.stderr
        assert "replace-all" not in result.stderr
        assert "branch.unknown" not in result.stderr
        assert key_value not in result.stderr

    def test_stop_status_survives_a_set_e_caller_capturing_with_or(self, tmp_path: Path) -> None:
        """The documented capture shape must reach the explicit status test
        inside a `set -e` script rather than aborting on the function's non-zero return."""
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "-x")
        script = (
            'set -euo pipefail\n. "$1"\nrc=0\n'
            'out=$(_lib_configured_stack_base "$2" "$3") || rc=$?\n'
            'printf "rc=%s out=[%s]\\n" "$rc" "$out"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert result.stdout == "rc=2 out=[]\n"
        assert "branch.feat/x.gh-merge-base" in result.stderr

    def test_value_with_embedded_newline_is_rejected_with_one_stderr_line(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "evil\nsecond line")
        result = _configured_stack_base(repo)
        _assert_stack_base_stop(result, "feat/x")

    def test_stop_for_a_branch_holding_a_single_quote_quotes_the_key_so_it_round_trips(
        self, tmp_path: Path
    ) -> None:
        """An unquoted `'` leaves shlex.split with no closing quotation, so
        dropping the formatter's %q quoting of the branch fails this test."""
        branch = "feat/a'b"
        repo = _repo_on_branch_with_key(tmp_path, branch, "-x")
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout) == (2, "")
        assert result.stderr.count("\n") == 1
        replace_all_fix = re.search(r"git config --replace-all (\S+) <stack-base>", result.stderr)
        assert replace_all_fix is not None
        assert shlex.split(replace_all_fix.group(1)) == [f"branch.{branch}.gh-merge-base"]

    def test_every_printable_ascii_character_is_allowed_only_inside_the_documented_set(
        self, tmp_path: Path
    ) -> None:
        """The value `a<c>b` for each printable ASCII c passes when c is in the documented
        set and stops otherwise, quotes and backticks included. One repo serves every
        character to bound the runtime."""
        allowed_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", None)
        for code_point in range(0x20, 0x7F):
            character = chr(code_point)
            value = f"a{character}b"
            subprocess.run(
                ["git", "config", "--replace-all", "branch.feat/x.gh-merge-base", value],
                cwd=repo, check=True,
            )
            result = _configured_stack_base(repo)
            if character in allowed_characters:
                assert (result.returncode, result.stdout, result.stderr) == (
                    0, f"feat/x {value}\n", ""
                ), f"{character!r} should be allowed"
            else:
                assert (result.returncode, result.stdout) == (2, ""), f"{character!r} should stop"
                assert result.stderr.count("\n") == 1, f"{character!r} should stop with one line"

    @pytest.mark.parametrize(
        ("value", "rendered_value_clause"),
        [
            pytest.param("", "is '', which", id="empty-value-reads-as-two-quotes"),
            pytest.param("a;b", "is a\\;b, which", id="semicolon-is-backslash-escaped"),
        ],
    )
    def test_stop_line_renders_the_value_percent_q_quoted(
        self, tmp_path: Path, value: str, rendered_value_clause: str
    ) -> None:
        """An empty value must not read as blank and a metacharacter must not read as shell syntax."""
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", value)
        result = _configured_stack_base(repo)
        _assert_stack_base_stop(result, "feat/x")
        assert rendered_value_clause in result.stderr

    def test_name_shape_status_with_no_named_stop_is_still_a_stop(self, tmp_path: Path) -> None:
        """A status `_lib_stack_base_name_shape` gains later must not let its value through."""
        unmapped_status = 5
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "feat/base")
        script = f'. "$1"; _lib_stack_base_name_shape() {{ return {unmapped_status}; }}; _lib_configured_stack_base "$2" "$3"'
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        _assert_stack_base_stop(result, "feat/x")
        assert f"status {unmapped_status}" in result.stderr


def _stack_base_name_shape(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", '. "$1"; _lib_stack_base_name_shape "$2"', "bash", str(_LIB_SH), name],
        capture_output=True,
        text=True,
        check=False,
    )


class TestLibStackBaseNameShape:
    """Exit 0 for a name of allowed characters that has no leading `-`, no `origin/` prefix and
    is not `HEAD`, exit 1 for a leading `-`, exit 2 for a character outside [A-Za-z0-9._/-],
    exit 3 for an `origin/` prefix, exit 4 for `HEAD`. The first failing test in that order
    wins. It prints nothing."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("main", id="plain"),
            pytest.param("feat/base-2.x_y", id="every-allowed-punctuation"),
            pytest.param("ABCxyz019", id="letters-and-digits"),
            pytest.param("origin", id="origin-without-the-slash"),
            pytest.param("feat/origin/x", id="origin-not-at-the-start"),
            pytest.param("head", id="lowercase-head"),
            pytest.param("HEAD2", id="head-prefix-only"),
        ],
    )
    def test_allowed_name_exits_0_with_no_output(self, name: str) -> None:
        result = _stack_base_name_shape(name)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")

    def test_empty_name_exits_0_with_no_output(self) -> None:
        result = _stack_base_name_shape("")
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("-x", id="dash-then-letter"),
            pytest.param("--upload-pack=x", id="option-lookalike"),
            pytest.param("-a b", id="dash-wins-over-a-disallowed-character"),
        ],
    )
    def test_leading_dash_exits_1_with_no_output(self, name: str) -> None:
        result = _stack_base_name_shape(name)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("a;b", id="semicolon"),
            pytest.param("a b", id="space"),
            pytest.param("a$(x)", id="command-substitution"),
            pytest.param("caf" + chr(0xE9), id="non-ascii-letter"),
            pytest.param("a:b", id="colon"),
            pytest.param("re+x", id="plus"),
            pytest.param("a*", id="asterisk"),
            pytest.param("a?", id="question-mark"),
            pytest.param("a[", id="open-bracket"),
            pytest.param("a~1", id="tilde"),
            pytest.param("a^", id="caret"),
            pytest.param("a@b", id="at-sign"),
            pytest.param("a\\b", id="backslash"),
            pytest.param("a\nb", id="newline"),
            pytest.param("a\tb", id="tab"),
            pytest.param("a\rb", id="carriage-return"),
        ],
    )
    def test_disallowed_character_exits_2_with_no_output(self, name: str) -> None:
        result = _stack_base_name_shape(name)
        assert (result.returncode, result.stdout, result.stderr) == (2, "", "")

    def test_origin_prefix_exits_3_with_no_output(self) -> None:
        result = _stack_base_name_shape("origin/x")
        assert (result.returncode, result.stdout, result.stderr) == (3, "", "")

    def test_head_exits_4_with_no_output(self) -> None:
        result = _stack_base_name_shape("HEAD")
        assert (result.returncode, result.stdout, result.stderr) == (4, "", "")

    def test_origin_prefix_with_a_disallowed_character_exits_2_not_3(self) -> None:
        result = _stack_base_name_shape("origin/x;y")
        assert (result.returncode, result.stdout, result.stderr) == (2, "", "")


def _open_pr_base(reply: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", '. "$1"; _lib_open_pr_base "$2"', "bash", str(_LIB_SH), reply],
        capture_output=True,
        text=True,
        check=False,
    )


class TestLibOpenPrBase:
    """Exit 0 prints the base only for a two-line reply whose first line is `OPEN` and
    whose second line is non-empty; every other reply exits 1 with empty stdout."""

    def test_open_two_line_reply_prints_the_base(self) -> None:
        result = _open_pr_base("OPEN\nstack")
        assert (result.returncode, result.stdout, result.stderr) == (0, "stack", "")

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param("", id="empty-reply"),
            pytest.param("OPEN", id="one-line"),
            pytest.param("OPEN\nstack\nextra", id="three-lines"),
            pytest.param("OPEN\n\nstack", id="three-lines-with-empty-second"),
            pytest.param("MERGED\nstack", id="merged"),
            pytest.param("CLOSED\nstack", id="closed"),
            pytest.param("OPEN\n", id="open-with-empty-base"),
        ],
    )
    def test_any_other_reply_exits_1_with_empty_stdout(self, reply: str) -> None:
        result = _open_pr_base(reply)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")
