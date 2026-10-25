"""Unit tests for _lib.sh's fragment matchers (_lib_fragment_command_word, _lib_fragment_invokes_tool
and the primitives through _lib_commit_fragment_has_worktree_target), their quote-blindness
composition with _lib_strip_shell_quotes, and _lib_strip_shell_quotes itself.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from helpers import build_path_without

from ._lib_sh_helpers import _LIB_SH, _run_lib_call

# --- _lib_fragment_command_word / _lib_fragment_invokes_tool /
#     _lib_fragment_has_token --------------------------------------------
#
# Promoted out of deny-reviewer-tree-mutation.sh into _lib.sh once
# deny-repo-relocation.sh needed the identical "does this fragment invoke
# tool X" check. Previously covered only indirectly through
# deny-reviewer-tree-mutation.sh's black-box tests, which stopped being
# sufficient once a second hook depends on the same shared functions.


def _fragment_command_word(fragment: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_fragment_command_word "$1"', "bash", fragment],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _fragment_invokes_tool(fragment: str, tool: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_fragment_invokes_tool "$1" "$2"', "bash", fragment, tool],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _fragment_has_token(fragment: str, token: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_fragment_has_token "$1" "$2"', "bash", fragment, token],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


class TestFragmentCommandWord:
    def test_bare_command_returns_itself(self) -> None:
        assert _fragment_command_word("black src/x.py") == "black"

    def test_skips_leading_env_var_assignment(self) -> None:
        assert _fragment_command_word("FOO=1 black src/x.py") == "black"

    def test_skips_sudo_runner(self) -> None:
        assert _fragment_command_word("sudo black src/x.py") == "black"

    def test_skips_runner_and_connector_subtoken(self) -> None:
        assert _fragment_command_word("poetry run black src/x.py") == "black"

    def test_skips_npx_flag_before_command(self) -> None:
        assert _fragment_command_word("npx --yes prettier --write x.ts") == "prettier"

    def test_resolves_absolute_path_runner_and_command(self) -> None:
        assert _fragment_command_word("/usr/bin/python -m black src/x.py") == "black"

    def test_argument_word_is_not_mistaken_for_command_word(self) -> None:
        """The whole reason this is a command-word scan, not an any-word
        scan: a tool name appearing only as an argument must not resolve as
        the command."""
        assert _fragment_command_word("grep -rn black .") == "grep"

    def test_venv_run_wrapper_is_the_command_word_not_a_shared_runner(self) -> None:
        """deny-reviewer-tree-mutation.sh's header says why the wrapper stays out of the shared runner words."""
        assert _fragment_command_word("scripts/venv-run.sh ruff format x.py") == "scripts/venv-run.sh"

    def test_empty_fragment_returns_empty(self) -> None:
        assert _fragment_command_word("") == ""

    def test_quoted_command_word_not_matched_by_contract(self) -> None:
        """Caller contract (GH-783): this function is quote-blind by
        design -- bash word-splitting does not remove quote characters, so
        a quoted command word is returned verbatim, quotes and all, never
        matching the bare tool name a caller compares it against. The
        caller is responsible for stripping via _lib_strip_shell_quotes
        first; pinned here so a future "fix" inside this function isn't
        mistaken for closing a real gap."""
        assert _fragment_command_word('"black" src/x.py') != "black"


class TestFragmentInvokesTool:
    def test_exact_match(self) -> None:
        assert _fragment_invokes_tool("mv /a /b", "mv")

    def test_path_qualified_match(self) -> None:
        assert _fragment_invokes_tool("/usr/bin/mv /a /b", "mv")

    def test_no_match_for_different_tool(self) -> None:
        assert not _fragment_invokes_tool("cp /a /b", "mv")

    def test_no_match_when_tool_name_is_only_an_argument(self) -> None:
        assert not _fragment_invokes_tool("grep mv notes.txt", "mv")

    def test_runner_wrapped_match(self) -> None:
        assert _fragment_invokes_tool("sudo env X=1 rsync -a /a /b", "rsync")

    def test_quoted_tool_name_not_matched_by_contract(self) -> None:
        """Caller contract (GH-783): inherits _lib_fragment_command_word's
        quote-blindness."""
        assert not _fragment_invokes_tool('"mv" /a /b', "mv")


class TestFragmentHasToken:
    def test_standalone_token_matches(self) -> None:
        assert _fragment_has_token("rsync -a --remove-source-files /a /b", "--remove-source-files")

    def test_token_as_substring_of_longer_word_does_not_match(self) -> None:
        assert not _fragment_has_token("rsync --remove-source-files-extra /a /b", "--remove-source-files")

    def test_token_at_string_start_matches(self) -> None:
        assert _fragment_has_token("fmt x.tf", "fmt")

    def test_token_at_string_end_matches(self) -> None:
        assert _fragment_has_token("terraform fmt", "fmt")

    def test_missing_token_does_not_match(self) -> None:
        assert not _fragment_has_token("rsync -a /a /b", "--remove-source-files")

    def test_quoted_token_not_matched_by_contract(self) -> None:
        """Caller contract (GH-783): a quoted token is a different literal
        string than its bare form, so the boundary-match regex never
        matches it -- quote-blind by design, same as the other matchers
        in this family."""
        assert not _fragment_has_token('rsync "--remove-source-files" /a /b', "--remove-source-files")


# --- _lib_git_argv_from_subcmd / _lib_extract_git_subcmd /
#     _lib_extract_git_subcmd_args -----------------------------------------
#
# Direct coverage arrives now because a second consumer of the word walk
# (_lib_extract_git_subcmd_args) means _lib_extract_git_subcmd's contract can
# no longer be inferred from its callers' black-box tests, the same
# rationale as the _lib_fragment_command_word banner above.


def _extract_git_subcmd(fragment: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_extract_git_subcmd "$1"', "bash", fragment],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _extract_git_subcmd_args(fragment: str) -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_extract_git_subcmd_args "$1"', "bash", fragment],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


@pytest.mark.parametrize(
    "fragment, expected",
    [
        ("git push", "push"),
        ("git -C /wt push", "push"),
        ("git -c user.name=x commit", "commit"),
        ("git --git-dir=/wt/.git push", "push"),
        ("git --git-dir /wt/.git push", "push"),
        ("git --work-tree /wt push", "push"),
        ("git --namespace ns push", "push"),
        ("git --super-prefix /pre push", "push"),
        ("git --config-env foo=BAR push", "push"),
        ("git -c push.default=simple push origin feature", "push"),
        ("git -C /wt -c user.name=x push --tags origin", "push"),
        ("GIT_DIR=x git push", "push"),
        ("/usr/bin/git status", "status"),
        ("git push)", "push"),
        ("git --version", ""),
        ("", ""),
    ],
)
class TestExtractGitSubcmd:
    """Characterization tests: they pin _lib_extract_git_subcmd's observable
    contract independent of its implementation."""

    def test_extract_git_subcmd(self, fragment: str, expected: str) -> None:
        assert _extract_git_subcmd(fragment) == expected


@pytest.mark.parametrize(
    "fragment, expected",
    [
        ("git push --tags origin", ["--tags", "origin"]),
        ("git -C /wt push --tags origin feature", ["--tags", "origin", "feature"]),
        ("git -c user.name=x push origin feature", ["origin", "feature"]),
        ("git -c push.default=simple push origin feature", ["origin", "feature"]),
        (
            "git --git-dir=/wt/.git push --tags origin feature",
            ["--tags", "origin", "feature"],
        ),
        (
            "git -C /wt -c user.name=x push --tags origin",
            ["--tags", "origin"],
        ),
        (
            "git -C /wt push origin :old-branch new-feature:new-feature",
            ["origin", ":old-branch", "new-feature:new-feature"],
        ),
        ("git push --tags origin feature)", ["--tags", "origin", "feature)"]),
        ("git push", []),
        ("git --version", []),
        ("", []),
    ],
)
class TestExtractGitSubcmdArgs:
    def test_extract_git_subcmd_args(self, fragment: str, expected: list[str]) -> None:
        assert _extract_git_subcmd_args(fragment) == expected


# --- _lib_fragment_invokes_git ------------------------------------------
#
# Parametrized over _lib_fragment_invokes_git's own Accepts/Rejects doc
# comment in _lib.sh, turning that comment into an executable spec rather
# than inventing new cases.


@pytest.mark.parametrize(
    "fragment",
    [
        "git log",
        "sudo git commit",
        "GIT_DIR=x git push",
        "/usr/bin/git status",
    ],
)
def test_lib_fragment_invokes_git_accepts_documented_invocations(fragment: str) -> None:
    result = _run_lib_call(f'_lib_fragment_invokes_git "{fragment}"', env=dict(os.environ))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "fragment",
    [
        "ls .github/",
        "cat .gitignore",
        "grep github.com",
        "./git-foo",
    ],
)
def test_lib_fragment_invokes_git_rejects_documented_look_alikes(fragment: str) -> None:
    result = _run_lib_call(f'_lib_fragment_invokes_git "{fragment}"', env=dict(os.environ))
    assert result.returncode != 0, result.stderr


# --- _lib_commit_fragment_has_worktree_target ---------------------------
#
# Shared by deny-invisible-commit-content.sh (denies outright) and
# deny-pii-in-commits.sh (widens the scan to git diff HEAD) -- the two
# callers read this helper's return value for different purposes, so the
# tool-missing fail-safe direction gets its own direct test here rather
# than relying on either caller's own behavioral pinning alone.


@pytest.mark.parametrize("missing_binary", ["xargs", "awk"])
def test_commit_fragment_has_worktree_target_fails_safe_when_tool_missing(
    missing_binary: str, tmp_path: Path
) -> None:
    """A commit fragment with no real worktree target (no -a/--all, no --
    separator, no bare pathspec) still reports "target found" when xargs or
    awk is missing from PATH -- the safe direction for both callers."""
    farm_dir = tmp_path / f"path-without-{missing_binary}"
    farm_dir.mkdir()
    env = dict(os.environ)
    env["PATH"] = build_path_without(missing_binary, farm_dir)
    result = _run_lib_call('_lib_commit_fragment_has_worktree_target "git commit -m x"', env=env)
    assert result.returncode == 0, result.stderr


# --- GH-783: caller-contract quote-blindness + composition -------------
#
# Not added to the Accepts list above: that list mirrors _lib.sh's own doc
# comment, and _lib_fragment_invokes_git's quote-blind behavior is
# unchanged by GH-783 -- the fix is at each caller's own input boundary
# (COMMAND_UNQUOTED), not inside this function. These rows pin the
# quote-blindness as an intentional caller contract and prove the
# strip-then-match composition every caller now uses actually works.


def test_lib_fragment_invokes_git_is_quote_blind_by_contract() -> None:
    """A quoted git word matches nothing here by design -- bash
    word-splitting does not remove quote characters. GH-783's fix strips
    via _lib_strip_shell_quotes before calling this function, not inside
    it; pinned here so a future "fix" placed inside this function isn't
    mistaken for closing a real gap."""
    result = _run_lib_call('_lib_fragment_invokes_git \'"git" log\'', env=dict(os.environ))
    assert result.returncode != 0, result.stderr


def test_lib_strip_shell_quotes_composed_with_invokes_git_detects_quoted_git_word() -> None:
    """Composition test for the GH-783 caller idiom: strip once at the
    hook's input boundary, then match -- proves `"git" log` is detected
    once stripped, closing the bypass _lib_fragment_invokes_git's own
    quote-blindness would otherwise leave open."""
    call = "stripped=$(_lib_strip_shell_quotes '\"git\" log'); _lib_fragment_invokes_git \"$stripped\""
    result = _run_lib_call(call, env=dict(os.environ))
    assert result.returncode == 0, result.stderr


def test_lib_strip_shell_quotes_composed_with_extract_git_subcmd_detects_quoted_subcommand() -> None:
    """Composition test for the subcommand-quoted shape: the subcommand
    word itself is quoted (`git "commit"`), not just the git word --
    proves the same strip-then-match idiom closes it too."""
    call = "stripped=$(_lib_strip_shell_quotes 'git \"commit\"'); _lib_extract_git_subcmd \"$stripped\""
    result = _run_lib_call(call, env=dict(os.environ))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "commit"


# --- _lib_strip_shell_quotes -----------------------------------------------
#
# End-to-end coverage of this function's effect lives in
# test_deny_credential_bash_reads.py and the credential-value cases in
# test_deny_pii_in_commits.py (both callers). The tests here pin the
# transformation itself in isolation, so a future edit to the sed pipeline
# that mis-orders or partially breaks one of its four steps is caught here
# even for an input shape neither caller's own fixtures happens to exercise.


def _strip_shell_quotes(text: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_strip_shell_quotes "$1"', "bash", text],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_lib_strip_shell_quotes_removes_bare_single_quotes() -> None:
    assert _strip_shell_quotes("id_r'sa'") == "id_rsa"


def test_lib_strip_shell_quotes_removes_bare_double_quotes() -> None:
    assert _strip_shell_quotes('id_r"sa"') == "id_rsa"


def test_lib_strip_shell_quotes_joins_adjacent_quote_split() -> None:
    """The bug this function was introduced to fix: bash executes
    `~/.ssh/config"_backup"` identically to its unquoted form."""
    assert _strip_shell_quotes('~/.ssh/config"_backup"') == "~/.ssh/config_backup"


def test_lib_strip_shell_quotes_removes_single_char_backslash_escape() -> None:
    assert _strip_shell_quotes(r"id_r\sa") == "id_rsa"


def test_lib_strip_shell_quotes_leaves_multi_digit_escape_undecoded() -> None:
    """Root cause pinned at the unit layer: the backslash-removal step
    (`s/\\\\(.)/\\1/g`) consumes exactly one character after each `\\`, so a
    multi-digit ANSI-C octal/hex escape survives as leftover digits instead
    of decoding to the character bash itself would produce
    (`$'\\x69\\x64\\x5f\\x72\\x73\\x61'` -> `id_rsa` under real bash, but
    only the backslashes are stripped here, leaving the hex digits intact).
    This is the actual mechanism behind
    test_deny_credential_bash_reads.py::test_ansi_c_multichar_escape_bypass_allowed
    and test_deny_pii_in_commits.py::test_ansi_c_octal_escape_credential_value_allowed
    -- both pin the same root cause at their own (more expensive) hook layer;
    this test pins the string-transformation property directly."""
    assert _strip_shell_quotes(r"\147\150\160") == "147150160"


def test_lib_strip_shell_quotes_strips_ansi_c_quote_opener() -> None:
    """Drops the leading `$` of a `$'...'` opener so its content
    reassembles the same way a plain `'...'` segment does."""
    assert _strip_shell_quotes("id_r$'sa'") == "id_rsa"


def test_lib_strip_shell_quotes_strips_locale_quote_opener() -> None:
    """Drops the leading `$` of a `$"..."` opener the same way."""
    assert _strip_shell_quotes('id_r$"sa"') == "id_rsa"


def test_lib_strip_shell_quotes_handles_combined_forms_in_one_string() -> None:
    """A single string mixing an ANSI-C opener and a bare double-quoted
    segment -- exercises step ordering (the `$'`/`$"` opener strip must run
    before the final quote-character strip, or the leading `$` would survive
    as a stray character), not just each step in isolation."""
    assert _strip_shell_quotes("""id_r$'s'"a\"""") == "id_rsa"


def test_lib_strip_shell_quotes_over_strips_double_quoted_literal_apostrophe() -> None:
    """Documented accepted false positive, same direction as the
    single-quoted-literal-backslash case pinned in
    test_deny_credential_bash_reads.py: real bash resolves
    `~/.ssh/id_r"'"sa` to the literal filename `id_r'sa` (the double-quoted
    segment's content is one literal apostrophe, never a delimiter), but
    this function's final `tr -d` step removes quote characters
    unconditionally regardless of whether they're delimiters or literal
    content, joining `id_r` and `sa` across the apostrophe into `id_rsa`."""
    assert _strip_shell_quotes("""~/.ssh/id_r"'"sa""") == "~/.ssh/id_rsa"


def test_lib_strip_shell_quotes_sed_absent_returns_nonzero(tmp_path: Path) -> None:
    """Pins the fail-closed contract at the layer that actually owns it:
    every downstream caller (_lib_command_invokes_git_subcmd,
    _lib_command_invokes_tool_subcmd, and every hook's own
    COMMAND_UNQUOTED computation) relies on _lib_strip_shell_quotes itself
    returning non-zero when its sed stage fails, not on the shape of
    whatever pipeline composes it. Without pipefail (deliberately absent
    from this file), a naive `sed ... | tr ...` pipeline's exit status is
    tr's, not sed's -- tr still runs (on empty stdin from the broken pipe)
    and exits 0 even though sed failed, so this must be checked at this
    function's own two-stage boundary, not inferred from a caller's
    pipeline shape."""
    farm_dir = tmp_path / "path-without-sed"
    farm_dir.mkdir()
    restricted_path = build_path_without("sed", farm_dir)
    env = {"PATH": restricted_path, "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_strip_shell_quotes "$1"', "bash", "id_r'sa'"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
