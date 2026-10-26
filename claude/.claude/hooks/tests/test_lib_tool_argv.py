"""Unit tests for _lib.sh's _lib_command_invokes_git_subcmd, _lib_command_invokes_tool_subcmd,
_lib_tool_argv_from_subcmd (with its gh --help drift guard) and _lib_words_start_with.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import build_path_without

from ._lib_sh_helpers import _LIB_SH

# --- _lib_command_invokes_git_subcmd / _lib_command_invokes_tool_subcmd -
#
# Composed tri-state matchers (0 matched / 1 did not / 2 could not
# determine) built from the fragment-matcher primitives in test_lib_fragment_matchers.py.
# Characterization tests, including the status-2 path each of the eight
# gate hooks depends on to decide its own fail posture.


def _command_invokes_git_subcmd(command: str, subcmd: str, env: dict | None = None) -> int:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_command_invokes_git_subcmd "$1" "$2"', "bash", command, subcmd],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


# --- _lib_tool_argv_from_subcmd -------------------------------------------
#
# _lib_tool_argv_from_subcmd has two independent consumers
# (_lib_command_invokes_tool_subcmd and deny-private-project-refs.sh's
# fragment_gh_gated_surface), so its contract needs its own direct test
# coverage rather than relying on either consumer's black-box tests -- the
# same rationale as the _lib_extract_git_subcmd_args banner in test_lib_fragment_matchers.py.


def _tool_argv_from_subcmd(fragment: str, tool: str) -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_tool_argv_from_subcmd "$1" "$2"', "bash", fragment, tool],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


class TestToolArgvFromSubcmd:
    @pytest.mark.parametrize(
        "fragment",
        [
            "gh pr --repo o/r create",
            "gh --repo o/r pr create",
            "gh pr --repo=o/r create",
            "gh pr -Ro/r create",
            "gh pr -R o/r create",
        ],
        ids=["repo-between", "repo-before", "repo-equals-glued", "repo-short-glued", "repo-short-between"],
    )
    def test_repo_flag_variants_yield_same_leading_stream(self, fragment: str) -> None:
        """All five -R/--repo spellings and positions must be skipped
        identically, leaving the same `pr`, `create` leading stream -- the
        shared test that ties both consumers (_lib_command_invokes_tool_subcmd
        and deny-private-project-refs.sh's own walker) to one flag grammar."""
        assert _tool_argv_from_subcmd(fragment, "gh")[:2] == ["pr", "create"]

    def test_leaf_flag_registered_at_root_scope_consumes_the_next_word(self) -> None:
        """A long flag not glued with "=" consumes the next token per gh's
        cobra resolution: `--web` (a leaf flag, unregistered at gh's root
        scope) takes `pr` as its value while gh resolves the subcommand,
        so only `create` remains in the stream -- gh itself never reaches
        `pr create` in this form."""
        assert _tool_argv_from_subcmd("gh --web pr create", "gh") == ["create"]

    @pytest.mark.parametrize(
        "fragment, want_leading_pair",
        [
            ("gh pr --title x create", ("pr", "create")),
            ("gh pr --title x edit 42", ("pr", "edit")),
            ("gh pr --body x merge 42", ("pr", "merge")),
            ("gh issue --title x create", ("issue", "create")),
            ("gh issue --body placeholder comment 42", ("issue", "comment")),
            ("gh issue --title x edit 42", ("issue", "edit")),
            ("gh pr -t x create", ("pr", "create")),
        ],
        ids=[
            "pr-title-create",
            "pr-title-edit",
            "pr-body-merge",
            "issue-title-create",
            "issue-body-comment",
            "issue-title-edit",
            "pr-short-flag-create",
        ],
    )
    def test_leaf_flag_interposed_before_subcommand_leads_the_stream(
        self, fragment: str, want_leading_pair: tuple[str, str]
    ) -> None:
        """A leaf flag written between the surface word and its subcommand
        is consumed by gh, along with its value, while gh resolves the
        subcommand -- so the pair still leads the emitted stream.
        `pr merge` is included here, not only in test_block_gh_pr_merge.py,
        since nothing else in this matrix exercised the `merge` verb at
        the shared-helper level before this addition."""
        assert tuple(_tool_argv_from_subcmd(fragment, "gh")[:2]) == want_leading_pair

    def test_two_sequential_leaf_flags_interposed_before_subcommand_leads_the_stream(self) -> None:
        """Two leaf flags in a row, each consuming its own value, must both
        be skipped before the subcommand still leads the emitted stream --
        not just a single interposed flag."""
        assert tuple(_tool_argv_from_subcmd("gh pr --title x --body y create", "gh")[:2]) == ("pr", "create")

    def test_leaf_flag_value_shaped_like_a_flag_is_still_consumed_as_a_value(self) -> None:
        """A leaf flag's value is skipped unconditionally by position, even
        when the value word itself starts with "-" and would otherwise be
        read as a flag of its own."""
        assert tuple(_tool_argv_from_subcmd("gh pr --title -x create", "gh")[:2]) == ("pr", "create")

    def test_leaf_help_flag_swallows_the_subcommand_word(self) -> None:
        """Pins the accepted gap _lib.sh's _lib_tool_argv_from_subcmd header
        comment documents: -h has no value placeholder, so gh's cobra
        resolution consumes `create` as -h's value while resolving the
        subcommand. This is a safe gap, not a bug, because gh prints help
        and exits before any create/comment/edit network call happens."""
        assert _tool_argv_from_subcmd("gh pr -h create", "gh") == ["pr"]

    @pytest.mark.parametrize(
        "fragment, want",
        [
            ("gh pr --title=x create", ["pr", "create"]),
            ("gh issue --title=x create", ["issue", "create"]),
        ],
        ids=["pr-equals-glued", "issue-equals-glued"],
    )
    def test_equals_glued_flag_does_not_consume_the_next_word(self, fragment: str, want: list[str]) -> None:
        """A flag containing "=" carries its value in the same word, the
        first of cobra's three non-consuming shapes -- the next word stays
        a genuine positional rather than being skipped as a flag's value."""
        assert _tool_argv_from_subcmd(fragment, "gh") == want

    @pytest.mark.parametrize(
        "fragment, want",
        [
            ("gh pr -tx create", ["pr", "create"]),
            ("gh pr -tx merge", ["pr", "merge"]),
        ],
        ids=["pr-short-glued-create", "pr-short-glued-merge"],
    )
    def test_short_flag_longer_than_two_characters_does_not_consume_the_next_word(
        self, fragment: str, want: list[str]
    ) -> None:
        """A short flag longer than two characters (its value glued into
        the same word, e.g. -tx) is dropped whole -- the second of cobra's
        three non-consuming shapes."""
        assert _tool_argv_from_subcmd(fragment, "gh") == want

    @pytest.mark.parametrize(
        "fragment, want",
        [
            ("gh pr -- create", ["pr"]),
            ("gh issue -- comment 42", ["issue"]),
        ],
        ids=["pr-double-dash", "issue-double-dash"],
    )
    def test_bare_double_dash_ends_the_stream(self, fragment: str, want: list[str]) -> None:
        """A bare "--" ends cobra's positional scan entirely, so no word
        after it -- including the subcommand -- is ever emitted; the third
        of cobra's three non-consuming shapes."""
        assert _tool_argv_from_subcmd(fragment, "gh") == want

    def test_no_subcommand_words_yields_empty(self) -> None:
        assert _tool_argv_from_subcmd("gh --repo o/r", "gh") == []

    def test_unrecognized_tool_has_no_pinned_flags(self) -> None:
        """A TOOL other than gh falls to the never-consume default, so
        every "-*" word is dropped as a bare flag rather than having its
        value skipped. `dir` is never treated as --chdir's value and is
        emitted as a positional word. This can miss a real subcommand
        match but never over-consumes a positional word as a flag's
        value. This is the executable proof that the gh-only cobra grammar
        (gated on `tool = gh`) leaves every other TOOL's never-consume
        default untouched, which is what keeps
        enforce-marker-script-shape.sh's fail-closed `marker.sh` gate from
        regressing."""
        assert _tool_argv_from_subcmd("terraform --chdir dir apply", "terraform") == ["dir", "apply"]

    def test_repo_shaped_flag_against_non_gh_tool_is_not_skipped(self) -> None:
        """Demonstrates the documented non-gh-tool limitation as a real
        boundary, not merely an assertion that happens not to contradict
        it: an -R/--repo-shaped flag against a non-gh tool drops the flag
        word itself (the never-consume default), but does NOT skip its
        would-be value (`fake`) as gh's grammar would. `fake` lands as a
        stray positional ahead of `write`, which would make a caller
        checking for a leading `write` subcommand miss a real invocation.
        This is the exact miss shape enforce-marker-script-shape.sh's
        marker.sh consumer would hit if marker.sh ever grew a leading flag
        of its own."""
        assert _tool_argv_from_subcmd("faketool -R fake write", "faketool") == ["fake", "write"]


def _command_invokes_tool_subcmd(command: str, tool: str, *subcmd: str, env: dict | None = None) -> int:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_command_invokes_tool_subcmd "$1" "$2" "${{@:3}}"',
            "bash",
            command,
            tool,
            *subcmd,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


class TestCommandInvokesGitSubcmd:
    def test_bare_match(self) -> None:
        assert _command_invokes_git_subcmd("git commit -m x", "commit") == 0

    def test_chained_match(self) -> None:
        assert _command_invokes_git_subcmd("git add . && git commit -m x", "commit") == 0

    def test_quote_split_match(self) -> None:
        """A quote-adjacent split (`"git" commit`) must not evade this
        matcher, unlike a raw regex over unstripped $COMMAND."""
        assert _command_invokes_git_subcmd('"git" commit -m x', "commit") == 0

    def test_no_match(self) -> None:
        assert _command_invokes_git_subcmd("git status", "commit") == 1

    def test_different_subcommand_prefix_does_not_match(self) -> None:
        """Word-boundary, not substring: `commit-tree` must not match a
        `commit` subcommand query."""
        assert _command_invokes_git_subcmd("git commit-tree x", "commit") == 1

    def test_empty_command_does_not_match(self) -> None:
        assert _command_invokes_git_subcmd("", "commit") == 1

    def test_global_dash_c_flag_prefix_still_matches(self) -> None:
        """A `-c key=val` global flag ahead of the subcommand must not hide
        it: the word-walk skips the flag and its value."""
        assert _command_invokes_git_subcmd("git -c core.editor=true commit", "commit") == 0

    def test_global_dash_cap_c_flag_prefix_still_matches(self) -> None:
        """A `-C <path>` global flag ahead of the subcommand must not hide
        it, same rationale as the `-c` case above."""
        assert _command_invokes_git_subcmd("git -C /tmp commit -m x", "commit") == 0

    def test_continue_form_does_not_match_commit(self) -> None:
        """`git merge --continue`'s subcommand genuinely is `merge`, not
        `commit` -- this predicate correctly says no, which is why
        _lib_command_concludes_commit is a separate predicate from this
        one."""
        assert _command_invokes_git_subcmd("git merge --continue", "commit") == 1

    def test_rebase_continue_form_does_not_match_commit(self) -> None:
        assert _command_invokes_git_subcmd("git rebase --continue", "commit") == 1

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_command_invokes_git_subcmd "git commit"'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2

    def test_sed_absent_returns_could_not_determine(self, tmp_path: Path) -> None:
        """Tri-state status 2: the underlying quote-strip fork failed, so
        the matcher could not evaluate the command at all -- distinct from
        status 1 (evaluated, no match). Every checked caller must deny on
        this status rather than silently treating it as "no match"."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        assert _command_invokes_git_subcmd("git commit -m x", "commit", env=env) == 2

    def test_tr_absent_returns_could_not_determine(self, tmp_path: Path) -> None:
        farm_dir = tmp_path / "path-without-tr"
        farm_dir.mkdir()
        restricted_path = build_path_without("tr", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        assert _command_invokes_git_subcmd("git commit -m x", "commit", env=env) == 2


def _words_start_with(words: tuple[str, ...], prefix: tuple[str, ...]) -> int:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_words_start_with "$@"', "bash", *words, "--", *prefix],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode


class TestWordsStartWith:
    """Direct unit tests of _lib_words_start_with through the harness above,
    unit-speed and bypassing _lib_command_invokes_tool_subcmd's own
    fragment-parsing machinery."""

    def test_full_length_exact_match(self) -> None:
        assert _words_start_with(("pr", "merge"), ("pr", "merge")) == 0

    def test_mismatch_at_interior_index(self) -> None:
        """Mismatch at a non-zero index, not index 0 -- exercises the loop
        continuing past an already-matched leading word before it finds
        the disagreement."""
        assert _words_start_with(("pr", "merge", "291"), ("pr", "edit")) == 1

    def test_empty_prefix_always_matches(self) -> None:
        """An empty PREFIX gives the while loop nothing to disagree on, so
        it returns success without ever comparing a word."""
        assert _words_start_with(("pr", "merge"), ()) == 0

    def test_words_shorter_than_prefix_returns_no_match(self) -> None:
        """WORDS having fewer words than PREFIX is a normal, safe case: the
        function bounds-checks WORDS against PREFIX's length itself and
        returns 1 before the comparison loop can index out of bounds."""
        assert _words_start_with(("pr",), ("pr", "merge")) == 1

    def test_words_shorter_than_prefix_is_safe_under_set_dash_u(self) -> None:
        """Every real caller sources _lib.sh under set -uo pipefail, where
        indexing an array past its length aborts the script instead of
        returning 1 -- this reproduces that shell option directly, unlike
        the harness above which sources _lib.sh with no set -u at all."""
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'set -uo pipefail; . {_LIB_SH}; _lib_words_start_with "$@"',
                "bash",
                "pr",
                "--",
                "pr",
                "merge",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "unbound variable" not in result.stderr

    def test_full_length_exact_match_is_safe_under_set_dash_u(self) -> None:
        """The match branch is the one that actually fires emit_deny in a
        calling gate hook like block-gh-pr-merge.sh -- this reproduces the
        set -uo pipefail shell options every real caller runs under, unlike
        the harness above which sources _lib.sh with no set -u at all."""
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'set -uo pipefail; . {_LIB_SH}; _lib_words_start_with "$@"',
                "bash",
                "pr",
                "merge",
                "--",
                "pr",
                "merge",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "unbound variable" not in result.stderr


class TestCommandInvokesToolSubcmd:
    def test_bare_match(self) -> None:
        assert _command_invokes_tool_subcmd("gh pr merge 291 --squash", "gh", "pr", "merge") == 0

    def test_chained_match(self) -> None:
        assert _command_invokes_tool_subcmd("git push && gh pr merge 291", "gh", "pr", "merge") == 0

    def test_no_match_different_subcommand(self) -> None:
        assert _command_invokes_tool_subcmd("gh pr mergefoo", "gh", "pr", "merge") == 1

    def test_no_match_different_verb(self) -> None:
        assert _command_invokes_tool_subcmd("gh pr create --title x", "gh", "pr", "edit") == 1

    def test_command_word_resolution_rejects_quoted_wrapper(self) -> None:
        """WHY command-word, not any-word: on the quote-stripped fragment
        `echo gh pr merge`, the command word resolves to `echo`, not `gh` --
        preserving block-gh-pr-merge.sh's documented `echo "gh pr merge"`
        allow, but via command-word resolution rather than quote
        preservation (see block-gh-pr-merge.sh's header)."""
        assert _command_invokes_tool_subcmd('echo "gh pr merge"', "gh", "pr", "merge") == 1

    def test_quote_split_subcommand_word_matches(self) -> None:
        """Closes a real gap the prior raw regex missed: `gh pr "merge"`
        quote-strips to a bare `merge` token, which this word-sequence
        match now catches."""
        assert _command_invokes_tool_subcmd('gh pr "merge"', "gh", "pr", "merge") == 0

    def test_full_path_invocation_matches(self) -> None:
        assert _command_invokes_tool_subcmd("/usr/bin/gh pr merge 291", "gh", "pr", "merge") == 0

    def test_value_taking_global_flag_before_subcommand(self) -> None:
        """Row 4's exact naive-implementation failure case: without
        skipping -R/--repo's own value, `o/r` would misread as the
        subcommand and the match would miss."""
        assert _command_invokes_tool_subcmd("gh --repo o/r pr merge", "gh", "pr", "merge") == 0

    def test_value_taking_global_flag_between_subcommand_words(self) -> None:
        assert _command_invokes_tool_subcmd("gh pr --repo o/r merge", "gh", "pr", "merge") == 0

    def test_glued_repo_flag_value_before_subcommand(self) -> None:
        assert _command_invokes_tool_subcmd("gh --repo=o/r pr merge", "gh", "pr", "merge") == 0

    def test_glued_short_repo_flag_value_before_subcommand(self) -> None:
        """The short-flag glued form (-Rowner/repo, no '='), distinct
        from --repo=owner/repo -- a dropped or mistyped -R?* arm would
        misread the glued value as the subcommand word and silently miss
        a real self-merge attempt."""
        assert _command_invokes_tool_subcmd("gh -Ro/r pr merge", "gh", "pr", "merge") == 0

    def test_short_flag_with_space_value_before_subcommand(self) -> None:
        """The short-flag space-separated form (-R o/r), distinct from
        both --repo=o/r and the glued -Ro/r form above -- exercises the
        skip_next branch that consumes the separate value word."""
        assert _command_invokes_tool_subcmd("gh -R o/r pr merge", "gh", "pr", "merge") == 0

    def test_shorter_subcommand_sequence_does_not_match(self) -> None:
        """Covers the untested `[ "${#got_subcmd[@]}" -lt "${#want_subcmd[@]}" ]
        && continue` guard: a fragment whose subcommand sequence is shorter
        than the wanted sequence must not match, even though every word it
        does have agrees with the wanted sequence's prefix."""
        assert _command_invokes_tool_subcmd("gh pr", "gh", "pr", "merge") == 1

    def test_literal_double_dash_in_words_misaligns_the_prefix_sentinel(self) -> None:
        """A literal `--` inside WORDS collides with the caller's own `--`
        sentinel, truncating WORDS and shifting PREFIX. This produces a
        silent wrong answer rather than a crash: WORDS actually starts with
        `foo`, but the collision misreports it as a mismatch. See
        _lib_words_start_with's own header comment for why this is
        unreachable via _lib_command_invokes_tool_subcmd today."""
        assert _words_start_with(("foo", "--", "bar"), ("foo",)) == 1

    def test_literal_double_dash_in_a_real_gh_command_still_matches(self) -> None:
        """End-to-end lock on the invariant the test above documents as
        currently unreachable: _lib_tool_argv_from_subcmd strips a literal
        `--` before it reaches got_subcmd, so it never collides with
        _lib_words_start_with's own `--` sentinel. A future change to that
        stripping that lets `--` through would regress this rather than
        the isolated helper test."""
        assert _command_invokes_tool_subcmd("gh pr merge -- 291", "gh", "pr", "merge") == 0

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_command_invokes_tool_subcmd "gh pr merge" gh'],
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
        assert _command_invokes_tool_subcmd("gh pr merge 291", "gh", "pr", "merge", env=env) == 2

    def test_tr_absent_returns_could_not_determine(self, tmp_path: Path) -> None:
        farm_dir = tmp_path / "path-without-tr"
        farm_dir.mkdir()
        restricted_path = build_path_without("tr", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        assert _command_invokes_tool_subcmd("gh pr merge 291", "gh", "pr", "merge", env=env) == 2


# --- gh --help drift guard -----------------------------------------------
#
# The only way _lib_tool_argv_from_subcmd can silently drop a real surface
# word is a future gh flag with no value placeholder, registered at a
# traversal scope gh actually walks while resolving a gated leaf (root,
# `pr`, or `issue`). INHERITED FLAGS is exactly that scope's flagset for a
# leaf; FLAGS is that scope's flagset for the root. A failure here means a
# new gh flag has reopened GH-559/GH-430's interposed-flag bypass, not a
# routine CI flake.


def _require_gh() -> str:
    """Resolve gh, failing rather than skipping when running in CI.

    Skipping locally is right for a contributor who has not installed gh.
    Skipping in CI is not: this is the only test in the suite whose entire
    purpose is catching a future gh flag with no value placeholder at a
    gated traversal scope, so a silently-absent gh would leave that
    residual unguarded in practice while the job still reports green.
    """
    gh_path = shutil.which("gh")
    if gh_path is None:
        if os.environ.get("CI"):
            pytest.fail(
                "gh is not on PATH in CI -- this is the only test guarding "
                "against a future gh flag with no value placeholder at a "
                "gated traversal scope reopening GH-559/GH-430's "
                "interposed-flag bypass; failing rather than skipping so "
                "this cannot silently degrade to a no-op."
            )
        pytest.skip("gh not found in PATH")
    return gh_path


def test_require_gh_fails_in_ci_when_gh_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_require_gh` is a small test-infrastructure helper, not production
    logic -- this pins that its CI branch actually calls pytest.fail rather
    than silently skipping, which would degrade every gh-drift-guard test
    below to a silent no-op in CI."""
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(pytest.fail.Exception):
        _require_gh()


def _gh_help_flags_by_section(help_text: str, section_heading: str) -> tuple[set[str], set[str]]:
    """Parses a `gh help ...` section (INHERITED FLAGS or FLAGS) into
    (value_taking_flags, no_value_placeholder_flags) -- distinguished by
    whether the flag-spec column carries an extra non-flag token before the
    2+-space gap that starts the description column (e.g. `-R, --repo
    [HOST/]OWNER/REPO` has the placeholder `[HOST/]OWNER/REPO`; `--help` has
    none). "No value placeholder" names gh help's own observable property --
    the underlying cobra mechanism (NoOptDefVal) also covers count-style
    flags, not only bool-typed ones."""
    in_section = False
    section_lines: list[str] = []
    for line in help_text.splitlines():
        if line.strip() == section_heading:
            in_section = True
            continue
        if not in_section:
            continue
        if line.strip() == "":
            break
        section_lines.append(line)
    value_taking_flags: set[str] = set()
    no_value_placeholder_flags: set[str] = set()
    for line in section_lines:
        spec = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
        tokens = spec.split()
        flag_tokens = {t.rstrip(",") for t in tokens if t.startswith("-")}
        has_value_placeholder = any(not t.startswith("-") for t in tokens)
        if has_value_placeholder:
            value_taking_flags |= flag_tokens
        else:
            no_value_placeholder_flags |= flag_tokens
    return value_taking_flags, no_value_placeholder_flags


@pytest.mark.parametrize(
    "gh_help_args",
    [
        ("pr", "create"),
        ("pr", "edit"),
        ("pr", "merge"),
        ("issue", "create"),
        ("issue", "comment"),
        ("issue", "edit"),
    ],
    ids=["pr-create", "pr-edit", "pr-merge", "issue-create", "issue-comment", "issue-edit"],
)
def test_gh_gated_leaf_inherited_no_value_placeholder_flags_stay_within_help(
    gh_help_args: tuple[str, str],
) -> None:
    """Each gated leaf's INHERITED FLAGS section is the flagset cobra scans
    with while resolving that leaf -- a flag there with no value
    placeholder is exactly the shape _lib_tool_argv_from_subcmd's grammar
    would over-consume. See the module-level comment above for what a
    failure here means."""
    gh_path = _require_gh()
    result = subprocess.run(
        [gh_path, "help", *gh_help_args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    _, no_value_placeholder_flags = _gh_help_flags_by_section(result.stdout, "INHERITED FLAGS")
    gh_help_command = " ".join(("gh", "help", *gh_help_args))
    assert no_value_placeholder_flags, (
        f"parsed no flags from `{gh_help_command}`'s INHERITED FLAGS section "
        "-- gh's help output format may have changed (e.g. a different "
        "'INHERITED FLAGS' heading), silently degrading this test's subset "
        "check below to a no-op; update _gh_help_flags_by_section's parser."
    )
    assert no_value_placeholder_flags <= {"-h", "--help"}, (
        f"{gh_help_command}'s INHERITED FLAGS lists a flag with no value "
        f"placeholder outside {{-h, --help}}: "
        f"{no_value_placeholder_flags - {'-h', '--help'}} -- gh has reopened "
        "GH-559/GH-430's interposed-flag bypass: this flag would be "
        "over-consumed by _lib_tool_argv_from_subcmd's cobra grammar, "
        "swallowing a real subcommand word."
    )


def test_gh_help_root_no_value_placeholder_flags_stay_within_help_and_version() -> None:
    """The root traversal step's own FLAGS section (INHERITED FLAGS is
    empty at the root) -- see the gated-leaf test above for what property
    this guards and what a failure here means."""
    gh_path = _require_gh()
    result = subprocess.run([gh_path, "help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    _, no_value_placeholder_flags = _gh_help_flags_by_section(result.stdout, "FLAGS")
    assert no_value_placeholder_flags, (
        "parsed no flags from `gh help`'s FLAGS section -- gh's help output "
        "format may have changed; update _gh_help_flags_by_section's parser."
    )
    assert no_value_placeholder_flags <= {"-h", "--help", "--version"}, (
        "`gh help`'s FLAGS section lists a flag with no value placeholder "
        f"outside {{-h, --help, --version}}: "
        f"{no_value_placeholder_flags - {'-h', '--help', '--version'}} -- gh "
        "has reopened GH-559/GH-430's interposed-flag bypass at the root "
        "traversal scope."
    )
