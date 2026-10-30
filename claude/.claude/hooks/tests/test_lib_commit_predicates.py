"""Unit tests for _lib.sh's _lib_length_ratchet_exceeded, _lib_command_concludes_commit,
_lib_command_concludes_marker_gated_commit, _lib_split_fragments' pipefail contract and _lib_fragment_concludes_commit."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from helpers import (
    build_path_without,
)

from ._lib_sh_helpers import (
    _LIB_SH,
)

# --- _lib_length_ratchet_exceeded -----------------------------------------
#
# The growth-comparison boundary (NEW > LIMIT && NEW > OLD) is reachable here
# without a real git repo. _lib_staged_length_gate calls this same predicate
# for both its line-count check and its byte-count check, so the boundary
# matrix below covers the (new, old, limit) cases for both.
# test_check_claude_md_length.py and test_check_skill_length.py cover path
# pattern, command parsing, message text and fail-closed posture end-to-end.


def _length_ratchet_exceeded(new: int, old: int, limit: int | str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_length_ratchet_exceeded "$@"',
            "bash",
            str(new),
            str(old),
            str(limit),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


class TestLengthRatchetExceeded:
    def test_new_at_exactly_limit_not_exceeded(self) -> None:
        assert not _length_ratchet_exceeded(200, 190, 200)

    def test_new_over_limit_and_over_old_exceeded(self) -> None:
        assert _length_ratchet_exceeded(201, 190, 200)

    def test_new_over_limit_growing_further_while_already_over_exceeded(self) -> None:
        assert _length_ratchet_exceeded(215, 210, 200)

    def test_new_over_limit_but_shrinking_from_old_not_exceeded(self) -> None:
        assert not _length_ratchet_exceeded(205, 210, 200)

    def test_new_over_limit_same_size_as_old_not_exceeded(self) -> None:
        """Different content, same count, still over limit: not growing, so
        allowed."""
        assert not _length_ratchet_exceeded(210, 210, 200)

    def test_new_under_limit_growing_not_exceeded(self) -> None:
        assert not _length_ratchet_exceeded(190, 180, 200)

    def test_new_zero_not_exceeded(self) -> None:
        """NEW=0 (a capped-timeout read or a staged deletion) can never be
        "over" a positive limit, regardless of OLD -- boundary case not
        pinned by name in either hook's own test file, since there NEW=0
        arises only as a side effect of git plumbing (deletion, timeout, or
        a missing HEAD) rather than as a directly-asserted value."""
        assert not _length_ratchet_exceeded(0, 300, 200)

    def test_empty_limit_not_exceeded(self) -> None:
        """Non-integer/empty LIMIT makes the underlying `[ -gt ]` test exit 2
        ("integer expression expected") rather than 0 or 1 -- characterizes
        the current fail-open behavior documented on the function's header:
        every caller's `if ...; then deny; fi` treats that exit 2 the same
        as "not exceeded", i.e. allow."""
        assert not _length_ratchet_exceeded(250, 300, "")


# --- _lib_command_concludes_commit / _lib_command_concludes_marker_gated_commit --
#
# The rebase carve-out's two named tri-state predicates: both delegate to
# one private shape-set matcher, so the narrow predicate excludes exactly
# one case (git rebase --continue) from the broad one.
# Pure string processors, no repo needed -- same fast, no-repo,
# fixed-string shape TestCommandInvokesGitSubcmd already uses in test_lib_tool_argv.py.


def _command_concludes_commit(command: str, env: dict | None = None) -> int:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_command_concludes_commit "$1"', "bash", command],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


def _command_concludes_marker_gated_commit(command: str, env: dict | None = None) -> int:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_command_concludes_marker_gated_commit "$1"', "bash", command],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


class TestCommandConcludesCommit:
    @pytest.mark.parametrize(
        "command",
        [
            "git commit",
            "git -c core.editor=true commit",
            "GIT_EDITOR=true git merge --continue",
            "git merge --continue",
            "git rebase --continue",
            "git cherry-pick --continue",
            "git revert --continue",
            # git's own option parser accepts any unambiguous prefix of a
            # long option (gitcli(7)) -- these must match the same way.
            "git merge --cont",
            "git rebase --cont",
            "git cherry-pick --cont",
            "git revert --cont",
        ],
    )
    def test_broad_predicate_true_for_concluding_shapes(self, command: str) -> None:
        assert _command_concludes_commit(command) == 0

    @pytest.mark.parametrize(
        "command",
        [
            "git merge origin/main",
            "git rebase --abort",
            "git rebase --skip",
            "git commit-tree abc123",
            "git status",
            # A clean cherry-pick or revert creates its commit inside the
            # initiating command with no separate `git commit` call, so no
            # gate keyed on this predicate ever sees one -- pinned here as
            # suite fact rather than only as prose.
            "git cherry-pick abc123",
            "git revert abc123",
            # A real git merge option, not a prefix of --continue.
            "git merge --commit",
            # Longer than --continue, not a proper prefix of it.
            "git merge --continued",
            "git merge --continue-foo",
        ],
    )
    def test_broad_predicate_false_for_non_concluding_shapes(self, command: str) -> None:
        assert _command_concludes_commit(command) == 1

    @pytest.mark.parametrize(
        "command",
        [
            "git commit",
            "git -c core.editor=true commit",
            "GIT_EDITOR=true git merge --continue",
            "git merge --continue",
            "git cherry-pick --continue",
            "git revert --continue",
            "git merge origin/main",
            "git rebase --abort",
            "git rebase --skip",
            "git commit-tree abc123",
            "git status",
        ],
    )
    def test_narrow_predicate_agrees_with_broad_except_rebase_continue(
        self, command: str
    ) -> None:
        assert _command_concludes_marker_gated_commit(command) == _command_concludes_commit(command)

    @pytest.mark.parametrize(
        "command",
        [
            "git --attr-source HEAD commit -m x",
            "git --attr-source=HEAD commit -m x",
        ],
    )
    def test_both_predicates_recognize_commit_behind_attr_source_global_flag(
        self, command: str
    ) -> None:
        """`--attr-source <tree-ish>` takes a separate-word value, so the
        subcommand walk must skip `HEAD` and find `commit` behind it."""
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0

    def test_narrow_predicate_excludes_rebase_continue_while_broad_includes_it(self) -> None:
        """The one input where the two predicates must diverge -- a
        fixture asserting only the narrow predicate's false here would not
        catch a regression that accidentally narrowed both."""
        assert _command_concludes_commit("git rebase --continue") == 0
        assert _command_concludes_marker_gated_commit("git rebase --continue") == 1

    def test_narrow_predicate_excludes_abbreviated_rebase_continue_too(self) -> None:
        """The rebase carve-out must exclude every abbreviated spelling of
        `--continue`, not just the exact one."""
        assert _command_concludes_commit("git rebase --cont") == 0
        assert _command_concludes_marker_gated_commit("git rebase --cont") == 1

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_command_concludes_commit'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2

    def test_sed_absent_returns_could_not_determine(self, tmp_path: Path) -> None:
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        assert _command_concludes_commit("git commit -m x", env=env) == 2
        assert _command_concludes_marker_gated_commit("git commit -m x", env=env) == 2

    def test_tr_absent_returns_could_not_determine(self, tmp_path: Path) -> None:
        farm_dir = tmp_path / "path-without-tr"
        farm_dir.mkdir()
        restricted_path = build_path_without("tr", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        assert _command_concludes_commit("git commit -m x", env=env) == 2
        assert _command_concludes_marker_gated_commit("git commit -m x", env=env) == 2

    @pytest.mark.parametrize(
        ("command", "expected_status_without_shim"),
        [("git commit -m x", 0), ("", 1), ("ls", 1)],
        ids=["concluding-commit", "empty-command", "non-git-command"],
    )
    def test_failed_fragment_read_returns_could_not_determine(
        self, command: str, expected_status_without_shim: int
    ) -> None:
        """The matcher must report undetermined, not no-match, when a here-string
        redirect fails (e.g. an unwritable TMPDIR) and skips the fragment loop.
        The `read` shim stands in for that failure without touching the
        filesystem. It prints a marker so a shell syntax error, which also
        exits 2, cannot pass for the sentinel.
        Without the shim, the sentinel must not over-deny."""
        shim_marker = "read-shim-ran"

        def run(prelude: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    f'. {_LIB_SH}; {prelude or ":"}; _lib_command_concludes_marker_gated_commit "$1"',
                    "bash",
                    command,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        shimmed = run(f"read() {{ echo {shim_marker}; return 1; }}")
        assert shimmed.returncode == 2
        assert shim_marker in shimmed.stdout
        assert shimmed.stderr == ""
        assert run("").returncode == expected_status_without_shim

    def test_continue_flag_outside_matched_verb_does_not_conclude_commit(self) -> None:
        """A `--continue` word elsewhere in COMMAND, outside the matched
        verb's own arguments, must not read as concluding a commit."""
        assert _command_concludes_commit("git rebase origin/main && echo --continue") == 1

    # The cases below cover the remaining `--continue` abbreviation
    # alternatives in _lib_fragment_concludes_commit_shape's case statement,
    # plus case-sensitivity and the empty command.

    @pytest.mark.parametrize(
        "abbreviation",
        ["--c", "--co", "--con", "--conti", "--contin", "--continu"],
    )
    def test_broad_predicate_true_for_remaining_continue_abbreviations(
        self, abbreviation: str
    ) -> None:
        assert _command_concludes_commit(f"git merge {abbreviation}") == 0

    def test_broad_predicate_case_sensitive_on_continue_flag(self) -> None:
        """`--Continue` is not one of the case statement's alternatives --
        the match is case-sensitive, and a differently-cased spelling must
        not be silently accepted."""
        assert _command_concludes_commit("git merge --Continue") == 1

    def test_broad_predicate_false_for_empty_command(self) -> None:
        assert _command_concludes_commit("") == 1

    # _lib_split_fragments leaves a bare `&` unsplit (GH-1063), so the shared
    # matcher tries each `&`-separated piece of a fragment as its own command.

    @pytest.mark.parametrize(
        "command",
        [
            "git add . & git commit -m x",
            "git status & git merge --continue",
        ],
    )
    def test_both_predicates_true_for_concluding_shape_after_single_ampersand(
        self, command: str
    ) -> None:
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0

    def test_rebase_continue_after_single_ampersand_keeps_the_rebase_carve_out(self) -> None:
        command = "git status & git rebase --continue"
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 1

    def test_ampersand_inside_a_quoted_global_flag_value_still_reaches_commit(self) -> None:
        assert _command_concludes_commit('git -c "user.name=a&b" commit -m x') == 0
        assert _command_concludes_marker_gated_commit('git -c "user.name=a&b" commit -m x') == 0

    def test_fd_redirection_ampersand_does_not_conclude_a_commit(self) -> None:
        assert _command_concludes_commit("git log 2>&1") == 1
        assert _command_concludes_marker_gated_commit("git log 2>&1") == 1

    @pytest.mark.parametrize(
        "command",
        [
            "sleep 1 & (git commit -m x)",
            "(git add) & (git commit -m x)",
            "git status &(git commit -m x)",
            "git status & ( git commit -m x )",
        ],
    )
    def test_both_predicates_true_for_parenthesized_commit_after_single_ampersand(
        self, command: str
    ) -> None:
        """Each `&`-separated piece gets the same leading-`(` / trailing-`)`
        strip a top-level fragment gets."""
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0

    @pytest.mark.parametrize("initial_noglob", [False, True], ids=["glob-on", "noglob-on"])
    @pytest.mark.parametrize(
        ("command", "expected_status"),
        [("git status & git commit -m x", 0), ("git status & git log", 1)],
        ids=["concluding-commit", "no-match"],
    )
    def test_ampersand_pass_restores_the_callers_ifs_and_noglob_state(
        self, initial_noglob: bool, command: str, expected_status: int
    ) -> None:
        """Pass 2 word-splits on newlines with globbing off. It must hand
        both settings back as it found them, on a match and on a no-match
        input alike, or every later word-split in the sourcing hook changes."""
        noglob_setup = "set -f; " if initial_noglob else ""
        harness = (
            f". {_LIB_SH}; {noglob_setup}IFS=$' \\t\\n'; "
            '_lib_command_concludes_commit "$1"; status=$?; '
            'case $- in *f*) glob_state=noglob;; *) glob_state=glob;; esac; '
            '[ "$IFS" = $\' \\t\\n\' ] && ifs_restored=true || ifs_restored=false; '
            'printf "%s|%s|%s" "$status" "$glob_state" "$ifs_restored"'
        )
        result = subprocess.run(
            ["bash", "-c", harness, "bash", command], capture_output=True, text=True, check=False
        )
        expected_glob_state = "noglob" if initial_noglob else "glob"
        assert result.stdout == f"{expected_status}|{expected_glob_state}|true"

    @pytest.mark.parametrize("nounset", [False, True], ids=["nounset-off", "nounset-on"])
    def test_ampersand_pass_leaves_an_unset_ifs_unset(self, nounset: bool) -> None:
        """An unset IFS word-splits like the default; restoring it as an empty
        string instead would disable word-splitting for every later loop in
        the sourcing hook."""
        nounset_setup = "set -u; " if nounset else ""
        harness = (
            f". {_LIB_SH}; unset IFS; {nounset_setup}"
            '_lib_command_concludes_commit "$1"; status=$?; '
            '[ -z "${IFS+set}" ] && ifs_state=unset || ifs_state=set; '
            'printf "%s|%s" "$status" "$ifs_state"'
        )
        result = subprocess.run(
            ["bash", "-c", harness, "bash", "git status & git commit -m x"],
            capture_output=True, text=True, check=False,
        )
        assert result.stdout == "0|unset"

    @pytest.mark.parametrize(
        "command",
        ['bash -c "git commit -m x"', 'eval "git commit -m x"'],
    )
    def test_quoted_wrapper_text_naming_a_commit_is_recognized(self, command: str) -> None:
        """Quote-stripping leaves `git commit` as bare words inside a
        `bash -c` or `eval` string, so the wrapper does not hide it."""
        assert _command_concludes_commit(command) == 0

    @pytest.mark.parametrize(
        "command",
        [
            "c=commit; git $c -m x",
            "git ci -m x",
            "bash ./do-commit.sh",
            "git \\\ncommit -m x",
            'git -c "user.name=A B" commit -m x',
            'git -C "dir with space" commit -m x',
            'git -C "$(pwd)" commit -m x',
            'git -C "$(git rev-parse --show-toplevel)" commit -m x',
            'git -C $(pwd) commit -m x',
            'git --git-dir="a b" commit -m x',
        ],
        ids=[
            "variable-word",
            "git-alias",
            "script-file",
            "backslash-newline-continuation",
            "quoted-space-in-global-flag-value",
            "quoted-space-in-dash-C-value",
            "command-substitution-in-dash-C-value",
            "rev-parse-toplevel-substitution-in-dash-C-value",
            "unquoted-command-substitution-in-dash-C-value",
            "quoted-space-in-attached-git-dir-value",
        ],
    )
    def test_known_bypass_commit_text_assembled_outside_the_command_string_is_not_recognized(
        self, command: str
    ) -> None:
        """A commit whose words never appear together in the command text
        (a variable, an alias, a script file, a line continuation, a
        whitespace or fragment-operator value of a global flag that precedes
        the subcommand) is a documented residual of matching command text;
        pinned so a later fix flips it deliberately."""
        assert _command_concludes_commit(command) == 1

    # The matcher is a quote-blind word walk over quote-stripped text, so a
    # command that only names a commit command as an argument, or quotes an
    # `&` inside another command's argument, is read as concluding one. That
    # over-match is the accepted fail-toward-deny posture; each case below pins
    # it so a later narrowing flips these deliberately.

    @pytest.mark.parametrize(
        "command",
        [
            "echo git commit",
            'grep "git commit" docs/x.md',
            "man git commit",
            'git log --grep "fix & git commit hook"',
        ],
        ids=["echo-argument", "grep-quoted-argument", "man-argument", "quoted-ampersand-in-argument"],
    )
    def test_accepted_over_match_command_naming_a_commit_reaches_both_predicates(
        self, command: str
    ) -> None:
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0


class TestSplitFragmentsPipefailContract:
    """_lib_split_fragments' first sed stage failing leaves the second stage
    reading empty input and succeeding, so the pipeline reports the failure
    only under `set -o pipefail`; the call-site contract in its header
    requires a sourcing caller to set it."""

    @pytest.mark.parametrize(
        ("pipefail_setting", "expected_status"),
        [("set -o pipefail", 1), ("set +o pipefail", 0)],
        ids=["pipefail-on-reports-failure", "pipefail-off-hides-failure"],
    )
    def test_first_stage_only_failure_is_reported_only_under_pipefail(
        self, pipefail_setting: str, expected_status: int
    ) -> None:
        harness = (
            f". {_LIB_SH}; {pipefail_setting}; "
            # Fails only the first stage, keyed on its script starting `s/;/`.
            "sed() { case \"$2\" in 's/;/'*) return 1;; esac; command sed \"$@\"; }; "
            'fragments=$(_lib_split_fragments "git add . ; git status"); '
            'printf "%s|%s" "$?" "$fragments"'
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)
        assert result.stdout == f"{expected_status}|"


def _fragment_concludes_commit_via_extraction(fragment: str, env: dict | None = None) -> int:
    """Mirrors what deny-invisible-commit-content.sh's arm 1 and arm 2 each
    do: extract the fragment's own subcommand, then feed FRAGMENT and that
    SUBCMD to _lib_fragment_concludes_commit -- rather than a hand-picked
    SUBCMD, so a regression in the extraction step itself would also show
    up here."""
    result = subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; subcmd=$(_lib_extract_git_subcmd "$1"); _lib_fragment_concludes_commit "$1" "$subcmd"',
            "bash", fragment,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


class TestFragmentConcludesCommit:
    """Differential coverage for _lib_fragment_concludes_commit_shape, which
    deny-invisible-commit-content.sh's arm 1 (raw/stripped-text SUBCMD
    extraction) and arm 2 (masked-text SUBCMD extraction) share. Asserts the
    fragment-level answer, via its public _lib_fragment_concludes_commit
    wrapper, agrees with the command-level predicate's answer for the
    equivalent single-fragment command."""

    @pytest.mark.parametrize(
        "fragment",
        [
            "git commit",
            "git -c core.editor=true commit",
            "git merge --continue",
            "git rebase --continue",
            "git cherry-pick --continue",
            "git revert --continue",
            "git merge --c",
            "git merge --continu",
        ],
    )
    def test_true_matches_command_level_predicate(self, fragment: str) -> None:
        assert _fragment_concludes_commit_via_extraction(fragment) == 0
        assert _command_concludes_commit(fragment) == 0

    @pytest.mark.parametrize(
        "fragment",
        [
            "git status",
            "git rebase --abort",
            "git rebase --skip",
            "git merge origin/main",
            "git cherry-pick abc123",
        ],
    )
    def test_false_matches_command_level_predicate(self, fragment: str) -> None:
        assert _fragment_concludes_commit_via_extraction(fragment) == 1
        assert _command_concludes_commit(fragment) == 1


def _fragment_concludes_commit_via_arm(fragment: str, transform: str, env: dict | None = None) -> int:
    """Runs one of deny-invisible-commit-content.sh's two real arm
    pipelines: TRANSFORM='strip' applies _lib_strip_shell_quotes (arm 1's
    own transform) and TRANSFORM='mask' applies _lib_mask_shell_quotes
    (arm 2's own transform) to FRAGMENT before extracting SUBCMD and
    feeding both to _lib_fragment_concludes_commit -- so a quoted fixture
    actually exercises the transform each arm runs, not a bypass of both."""
    transform_fn = "_lib_strip_shell_quotes" if transform == "strip" else "_lib_mask_shell_quotes"
    result = subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; transformed=$({transform_fn} "$1"); '
            'subcmd=$(_lib_extract_git_subcmd "$transformed"); '
            '_lib_fragment_concludes_commit "$transformed" "$subcmd"',
            "bash", fragment,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


class TestFragmentConcludesCommitQuotedDifferential:
    """Fragment-level answers agree with the whole-command predicate on
    quoted input. These fixtures carry a quoted `git` word and a quoted,
    abbreviated `--continue` form for a non-merge/rebase verb, so arm 1's
    quote-stripped extraction and arm 2's quote-masked extraction each run
    for real and must agree with each other and with the command-level
    predicate's answer for the equivalent whole command."""

    @pytest.mark.parametrize(
        "fragment,expected",
        [
            ('"git" cherry-pick "--c"', 0),
            ('"git" revert "--c"', 0),
            ('"git" cherry-pick abc123', 1),
        ],
    )
    def test_stripped_and_masked_arms_agree_with_command_level_predicate(
        self, fragment: str, expected: int
    ) -> None:
        stripped_result = _fragment_concludes_commit_via_arm(fragment, "strip")
        masked_result = _fragment_concludes_commit_via_arm(fragment, "mask")
        assert stripped_result == masked_result == _command_concludes_commit(fragment) == expected
