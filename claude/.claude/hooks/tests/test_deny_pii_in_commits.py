"""Tests for deny-pii-in-commits.sh.

Synthetic PII/credential values used in these tests — all invented, none
belongs to a real person or a live credential:
  SSN  123-45-6789      (the canonical example-only US SSN)
  Card 4111111111111111 (a Luhn-valid card test number; 4111111111111112
                         is the same string with a broken Luhn checksum)
  Token ghp_abcdefghijklmnopqrstuvwx1234 (GitHub classic-PAT shape only)
This test file lives under claude/.claude/hooks/tests/**, which the hook
always excludes from its diff scan — so committing these fixtures into
claude-config does not trip the hook on a developer machine that has armed
it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap

import pytest
from helpers import (
    HOOKS_DIR,
    assert_cap_engaged,
    bash_input,
    build_path_without,
    read_input,
    run_hook,
    run_hook_reason,
)

DENY_PII_IN_COMMITS_HOOK = HOOKS_DIR / "deny-pii-in-commits.sh"

SSN = "123-45-6789"
CARD_VALID = "4111111111111111"
CARD_BAD_LUHN = "4111111111111112"
GHP_TOKEN = "ghp_abcdefghijklmnopqrstuvwx1234"


def _thousands_grouped(digits):
    """Inserts a quote before every third digit counted from the right, and inside each hyphen-separated group for an SSN."""
    if "-" in digits:
        return "-".join(_thousands_grouped(group) for group in digits.split("-"))
    reversed_digits = digits[::-1]
    reversed_chunks = [reversed_digits[i : i + 3] for i in range(0, len(reversed_digits), 3)]
    return "'".join(reversed_chunks)[::-1]


CARD_VALID_THOUSANDS = _thousands_grouped(CARD_VALID)  # GH-1108: thousands-grouped form of CARD_VALID
# Thousands-grouped form of the 13-digit literal in test_luhn_valid_13_digit_card_denied.
CARD_13_THOUSANDS = _thousands_grouped("4222222222222")
# Thousands-grouped form of the 19-digit literal in test_luhn_valid_19_digit_card_denied.
CARD_19_THOUSANDS = _thousands_grouped("1111111111111111113")
# Thousands-grouped form of SSN, last group only. The SSN's first two groups are always
# below 1000, so the last group alone is the only thousands spelling that strips to the
# SSN shape.
SSN_LAST_GROUP_THOUSANDS = _thousands_grouped(SSN)


def _stage(repo, name, content):
    """Write `content` to `repo/name` and stage it."""
    (repo / name).write_text(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def _commit(repo, name, content, message="seed"):
    """Write, stage, and commit `repo/name` so it becomes tracked HEAD content."""
    _stage(repo, name, content)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _modify_unstaged(repo, name, content):
    """Overwrite an already-tracked `repo/name` without staging the change,
    so the new content is worktree-only -- invisible to `git diff --cached`
    and visible only to a HEAD-relative scan (`git diff HEAD`).

    A test that must isolate a HEAD probe or HEAD diff branch puts its
    credential here. A staged credential is caught by the staged-diff scan
    whether or not that branch denies, so the case would pass for the wrong
    reason."""
    (repo / name).write_text(content)


# (config key, config value, `.git/info/attributes` line or None, hook runs
# from a repo subdirectory). Each config exits 0 while altering the diff text.
DIFF_RENDERING_CONFIGS = [
    ("color.diff", "always", None, False),
    ("diff.external", "true", None, False),
    ("diff.hide.textconv", "true", "*.txt diff=hide", False),
    ("diff.relative", "true", None, True),
]
DIFF_RENDERING_CONFIG_IDS = [
    "color-diff-always",
    "diff-external-true",
    "textconv-prints-nothing",
    "diff-relative-true",
]


# Both diff calls lead with `-c diff.relative=false`, so the subcommand is
# argv position 3. A shim predicate that pins `$1 = diff` never matches.
DIFF_CALL_PREDICATE = '[ "$1" = "-c" ] && [ "$3" = "diff" ]'


def _configure_diff_rendering(repo, config_key, config_value, attributes_line, run_from_subdirectory):
    """Set one diff-rendering config in `repo`'s local git config, write the
    optional attributes line in `.git/info/attributes` (so no tracked file
    carries it), and return the directory the hook should run from: the repo
    root, or a subdirectory of it."""
    subprocess.run(["git", "config", config_key, config_value], cwd=repo, check=True)
    if attributes_line is not None:
        attributes_file = repo / ".git" / "info" / "attributes"
        attributes_file.parent.mkdir(parents=True, exist_ok=True)
        attributes_file.write_text(attributes_line + "\n")
    if not run_from_subdirectory:
        return repo
    subdirectory = repo / "sub"
    subdirectory.mkdir()
    return subdirectory


class TestDenyPiiInCommits:
    @pytest.fixture
    def pii_patterns(self, isolated_home):
        """Writer for ~/.claude/pii-patterns.md inside the isolated $HOME.

        Returns a function taking the file content; calling it arms the
        hook. Tests that never call it run against an unarmed hook (the
        file is absent)."""
        patterns_file = isolated_home / ".claude" / "pii-patterns.md"

        def _write(content: str):
            patterns_file.write_text(content)
            return patterns_file

        return _write

    # ------------------------------------------------------------------ #
    # Arming — opt-in via ~/.claude/pii-patterns.md presence              #
    # ------------------------------------------------------------------ #

    def test_unarmed_ssn_in_diff_allowed(self, isolated_home, git_repo):
        """No pii-patterns.md — the hook is a no-op even with PII staged."""
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_armed_ssn_in_diff_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_armed_clean_diff_allowed(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nno secrets here\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_non_regular_config_file_treated_as_unarmed(self, isolated_home, git_repo):
        """A broken symlink at the config path is not a regular file: `[ -f ]`
        is false, so the hook treats the machine as unarmed (allow) rather than
        erroring — the same guard that keeps a FIFO from blocking the read."""
        patterns_file = isolated_home / ".claude" / "pii-patterns.md"
        patterns_file.symlink_to("/nonexistent/pii-patterns-target")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_armed_at_config_dir_only_ssn_denied(self, isolated_home, git_repo, tmp_path):
        """Pattern file armed only at the resolved CLAUDE_CONFIG_DIR location
        (no legacy copy) -- confirms the new path is read."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / "pii-patterns.md").write_text("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git commit -m wip"),
            cwd=git_repo,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "deny"

    def test_armed_at_legacy_location_falls_back_ssn_denied(self, isolated_home, git_repo, pii_patterns, tmp_path):
        """Regression test: a pattern file armed only at the legacy
        $HOME/.claude location must still fire when CLAUDE_CONFIG_DIR points
        at a directory with no copy of the file -- proves continuity for a
        user who armed the guard before CLAUDE_CONFIG_DIR support existed."""
        pii_patterns("# no user patterns\n")
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git commit -m wip"),
            cwd=git_repo,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "deny"

    # ------------------------------------------------------------------ #
    # Credential-value sub-check — unconditional, no pii-patterns.md      #
    # ------------------------------------------------------------------ #
    # No ~/.claude/pii-patterns.md is created for any test in this section:
    # that is the point being pinned (the credential-value scan does not
    # wait for arming, unlike the SSN/credit-card/user-pattern tier above).

    def test_unarmed_credential_value_in_diff_denied(self, isolated_home, git_repo):
        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    @pytest.mark.parametrize(
        ("config_key", "config_value", "attributes_line", "run_from_subdirectory"),
        DIFF_RENDERING_CONFIGS,
        ids=DIFF_RENDERING_CONFIG_IDS,
    )
    @pytest.mark.parametrize("scan_worktree_content", [False, True], ids=["staged-diff", "head-diff"])
    def test_credential_denied_when_diff_rendering_config_would_hide_it(
        self,
        isolated_home,
        git_repo,
        config_key,
        config_value,
        attributes_line,
        run_from_subdirectory,
        scan_worktree_content,
    ):
        """Repo diff-rendering config exits 0 while altering the output the
        scan reads: `color.diff=always` prefixes each added line with an ANSI
        escape so the `^+` filter drops it, an always-succeeding
        `diff.external` prints nothing, a `textconv` driver that prints
        nothing replaces the file's bytes with empty text, and
        `diff.relative=true` drops every file outside the hook's cwd
        subdirectory. The hook's `--no-color --no-ext-diff --no-textconv`
        flags and `-c diff.relative=false` must keep the committed bytes in
        the scanned text at both diff sites. The staged case reaches the `git diff
        --cached` scan. The worktree case adds the commit-all flag and keeps
        the credential worktree-only, so only the `git diff HEAD` scan can
        catch it. The deny reason must name the credential-value match: a
        fail-closed diff-failure deny would otherwise satisfy the test."""
        hook_cwd = _configure_diff_rendering(git_repo, config_key, config_value, attributes_line, run_from_subdirectory)
        if scan_worktree_content:
            _modify_unstaged(git_repo, "file.txt", f"first\nsecond\ntoken {GHP_TOKEN}\n")
            commit_command = "git commit -a -m wip"
        else:
            _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
            commit_command = "git commit -m wip"
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input(commit_command), cwd=hook_cwd)
        assert reason is not None and "Credential value" in reason, reason

    @pytest.mark.parametrize(
        ("config_key", "config_value", "attributes_line", "run_from_subdirectory"),
        DIFF_RENDERING_CONFIGS,
        ids=DIFF_RENDERING_CONFIG_IDS,
    )
    @pytest.mark.parametrize("scan_worktree_content", [False, True], ids=["staged-diff", "head-diff"])
    def test_clean_content_allowed_when_diff_rendering_config_set(
        self,
        isolated_home,
        git_repo,
        config_key,
        config_value,
        attributes_line,
        run_from_subdirectory,
        scan_worktree_content,
    ):
        """Counterpart of the deny case above with clean content under the
        same config at the same two diff sites: the deny there is driven by
        the credential, not by the mere presence of diff-rendering config."""
        hook_cwd = _configure_diff_rendering(git_repo, config_key, config_value, attributes_line, run_from_subdirectory)
        if scan_worktree_content:
            _modify_unstaged(git_repo, "file.txt", "first\nsecond\nclean\n")
            commit_command = "git commit -a -m wip"
        else:
            _stage(git_repo, "f.txt", "x\nclean\n")
            commit_command = "git commit -m wip"
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(commit_command), cwd=hook_cwd) == "allow"

    def test_credential_in_binary_classified_file_allowed(self, isolated_home, git_repo):
        """Pins an accepted, documented residual (see the hook header's
        "Binary files differ" bullet): a file git classifies as binary, here
        through a `-diff` attribute in `.git/info/attributes`, renders as
        "Binary files differ" with no added lines, so the credential-value
        scan does not see it. Flip this to a deny test if the residual is
        closed."""
        attributes_file = git_repo / ".git" / "info" / "attributes"
        attributes_file.parent.mkdir(parents=True, exist_ok=True)
        attributes_file.write_text("*.bin -diff\n")
        _stage(git_repo, "f.bin", f"x\ntoken {GHP_TOKEN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_quote_split_credential_value_in_commit_message_denied(self, isolated_home, git_repo):
        """Required regression test for a Critical finding: bash reassembles
        an adjacent-quote split like `-m "gh""p_<token>"` into the single
        literal `-m ghp_<token>` before executing `git commit`, but a
        raw-text `grep -E` scan of the unexpanded $COMMAND previously saw
        the quote characters as a hard break and missed the reassembled
        credential-value token — permanently committing a live-looking
        secret to git history with no error surfaced. Closed by
        quote-stripping the $COMMAND component of SCAN_TARGET
        (_lib_strip_shell_quotes) before matching. Split the token via
        Python string concatenation so the source itself carries no
        contiguous credential-shaped literal."""
        split_ghp_token = 'gh""p_abcdefghijklmnopqrstuvwx1234'
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert (
            run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(f'git commit -m "{split_ghp_token}"'), cwd=git_repo)
            == "deny"
        )

    def test_backslash_split_credential_value_in_commit_message_denied(self, isolated_home, git_repo):
        """Required regression test for a Critical finding found during
        adversarial re-verification of the quote-splitting fix above: an
        unquoted backslash-escaped character is a second, distinct
        character-removal-based literal-reassembly mechanism bash executes
        identically to the unescaped form (`gh\\p_<token>` -> `ghp_<token>`,
        confirmed via `bash -c`), which the initial quote-only strip
        missed. _lib_strip_shell_quotes now also removes backslash-escapes.
        The token is backslash-split via raw string concatenation so the
        source itself carries no contiguous credential-shaped literal."""
        backslash_split_ghp_token = "gh" + r"\p_abcdefghijklmnopqrstuvwx1234"
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert (
            run_hook(
                DENY_PII_IN_COMMITS_HOOK,
                bash_input(f'git commit -m "{backslash_split_ghp_token}"'),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_ansi_c_octal_escape_credential_value_allowed(self, isolated_home, git_repo):
        """Required regression test pinning a documented residual found
        during adversarial re-verification of the quote/backslash-escape
        fix above -- the same root cause as
        test_deny_credential_bash_reads.py::test_ansi_c_multichar_escape_bypass_allowed:
        bash's ANSI-C octal escapes (`$'\\NNN...'`) reassemble into the
        literal token when executed, but _lib_strip_shell_quotes's
        backslash removal only ever consumes one character after each
        `\\` -- correct for single-char escapes, wrong for multi-digit
        octal ones. Accepted as a deliberate-obfuscation residual, same
        category as the bash-reads gate's pinned case; this test pins the
        credential-value sub-check's identical exposure so the documented
        shared-residual claim in docs/security-hardening.md stays
        test-backed for both callers, not just the bash-reads one. See
        docs/security-hardening.md's Limitations section."""
        octal_escaped_token = "".join(f"\\{ord(c):03o}" for c in GHP_TOKEN)
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert (
            run_hook(
                DENY_PII_IN_COMMITS_HOOK,
                bash_input(f"git commit -m $'{octal_escaped_token}'"),
                cwd=git_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "pseudo_path",
        ["-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"],
        ids=["bare-dash", "dev-stdin", "dev-fd", "proc-fd"],
    )
    def test_unarmed_f_pseudo_file_still_denied(self, isolated_home, git_repo, pseudo_path):
        """The `-F`/pseudo-file fail-closed check used to run only for armed
        users, since the whole commit-detection/extraction path lived
        behind the arming check. Hoisting that machinery above the arming
        check makes this reachable for unarmed users too — pinned so a
        slip that leaves this check under the old `if` doesn't silently
        reopen a fail-closed path with nothing catching it."""
        _stage(git_repo, "f.txt", "x\nclean\n")
        reason = run_hook_reason(
            DENY_PII_IN_COMMITS_HOOK, bash_input(f"git commit -F {pseudo_path}"), cwd=git_repo
        )
        assert reason is not None
        assert "pseudo-file path" in reason

    def test_unarmed_f_unreadable_file_still_denied(self, isolated_home, git_repo):
        """Same hoist as above, for the unreadable-message-source-file
        fail-closed check specifically (distinct code path from the
        pseudo-file check)."""
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input(f"git commit -F {git_repo / 'nonexistent-msg.txt'}"),
            cwd=git_repo,
        ) == "deny"

    @pytest.mark.timing
    def test_staged_diff_git_timeout_denied(self, isolated_home, git_repo, git_timeout_shim, tmp_path):
        """Required regression test for a High-severity finding: `git diff
        --cached`'s _lib_capped exit status previously went unchecked, so a
        timeout silently left STAGED_DIFF empty/truncated and the always-on
        credential-value tier scanned nothing — the commit landed with no
        scan, no error, no signal. Fails closed (deny) now instead. A fake
        `git` shadows only the `diff` subcommand (sleeping past the 5s cap)
        and passes every other subcommand through to the real binary."""
        env = git_timeout_shim(DIFF_CALL_PREDICATE)
        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        with assert_cap_engaged(tmp_path, production_cap=5):
            decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
        assert decision == "deny"

    @pytest.mark.timing
    def test_work_tree_check_git_timeout_denied(self, isolated_home, git_repo, git_timeout_shim, tmp_path):
        """Required regression test: `git rev-parse --is-inside-work-tree`'s
        _lib_capped exit status must also fail closed on timeout (exit 124)
        rather than exiting 0 and skipping every scan tier, including the
        always-on credential-value one. The staged content is clean: a staged
        credential would be caught by the staged-diff scan even if the probe
        fell through, so only the probe's own deny branch can produce `deny`
        here."""
        env = git_timeout_shim('[ "$1" = "rev-parse" ] && [ "$2" = "--is-inside-work-tree" ]')
        _stage(git_repo, "f.txt", "x\nclean\n")
        with assert_cap_engaged(tmp_path, production_cap=5):
            decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
        assert decision == "deny"

    @pytest.mark.timing
    def test_head_rev_parse_git_timeout_denied(self, isolated_home, git_repo, git_timeout_shim, tmp_path):
        """Required regression test: `git rev-parse HEAD`'s _lib_capped exit
        status must fail closed on timeout, distinct from the legitimate
        no-HEAD-yet (unborn branch) skip. `git commit -a` triggers
        HEAD_SCAN_NEEDED so this call site is reached. Worktree-only
        credential (see `_modify_unstaged`)."""
        env = git_timeout_shim('[ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]')
        _modify_unstaged(git_repo, "file.txt", f"first\nsecond\ntoken {GHP_TOKEN}\n")
        with assert_cap_engaged(tmp_path, production_cap=5):
            decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=git_repo, extra_env=env)
        assert decision == "deny"

    @pytest.mark.timing
    def test_head_diff_git_timeout_denied(self, isolated_home, git_repo, git_timeout_shim, tmp_path):
        """Required regression test: `git diff HEAD`'s _lib_capped exit
        status must fail closed on timeout, mirroring the STAGED_DIFF fix
        for the HEAD-relative diff specifically. Worktree-only credential
        (see `_modify_unstaged`)."""
        env = git_timeout_shim(f'{DIFF_CALL_PREDICATE} && [ "$4" = "HEAD" ]')
        _modify_unstaged(git_repo, "file.txt", f"first\nsecond\ntoken {GHP_TOKEN}\n")
        with assert_cap_engaged(tmp_path, production_cap=5):
            decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=git_repo, extra_env=env)
        assert decision == "deny"

    @pytest.mark.timing
    def test_work_tree_check_sigterm_immune_denied(self, isolated_home, git_repo, git_timeout_shim, tmp_path):
        """SIGKILL-after-grace path: a work-tree probe whose child ignores
        SIGTERM outright must deny once the -k grace escalates to SIGKILL, not
        skip every scan tier, credential-value tier included. The deny reason
        must carry exit 137: without the grace the cap kill would surface as
        124 or 143. The staged content is clean, same reason as the
        timeout-path case above."""
        env = git_timeout_shim(
            '[ "$1" = "rev-parse" ] && [ "$2" = "--is-inside-work-tree" ]',
            sigterm_immune=True,
        )
        _stage(git_repo, "f.txt", "x\nclean\n")
        with assert_cap_engaged(tmp_path, production_cap=5):
            reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
        assert reason is not None and "(exit 137)" in reason, reason

    @pytest.mark.timing
    def test_head_rev_parse_sigterm_immune_denied(self, isolated_home, git_repo, git_timeout_shim, tmp_path):
        """SIGKILL-after-grace path: a HEAD probe whose child ignores SIGTERM
        outright must deny, not be treated as the benign unborn-HEAD skip. The
        deny reason must carry exit 137: without the grace the cap kill would
        surface as 124 or 143. Worktree-only credential (see
        `_modify_unstaged`)."""
        env = git_timeout_shim(
            '[ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]',
            sigterm_immune=True,
        )
        _modify_unstaged(git_repo, "file.txt", f"first\nsecond\ntoken {GHP_TOKEN}\n")
        with assert_cap_engaged(tmp_path, production_cap=5):
            reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=git_repo, extra_env=env)
        assert reason is not None and "(exit 137)" in reason, reason

    @pytest.mark.parametrize("probe_status", [1, 3, 125, 126, 127, 129, 137, 143])
    def test_work_tree_check_unanticipated_status_denied(
        self, isolated_home, git_repo, git_timeout_shim, probe_status
    ):
        """Any work-tree probe status other than 0 or 128 must deny, because
        only git's own 128 is a benign skip. 127 is a missing git, 143 a
        BusyBox SIGTERM cap kill, 137 a SIGKILL after the grace, 125 and 126
        are GNU timeout's own "timeout failed" and "cannot invoke" statuses,
        129 is git's usage-error status (a probe has no 129-specific arm, so
        it must not be mistaken for 128), and 1 and 3 sit outside every
        cap-kill status set. The timeout and SIGTERM-immune
        cases above cover 124 and 137 against a real cap.
        No cap is engaged here: the shim exits with the status directly. The
        staged content is clean, so only the probe's own deny branch can
        produce `deny`. The deny reason must name the injected status."""
        env = git_timeout_shim(
            '[ "$1" = "rev-parse" ] && [ "$2" = "--is-inside-work-tree" ]',
            exit_status=probe_status,
        )
        _stage(git_repo, "f.txt", "x\nclean\n")
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
        assert reason is not None and f"(exit {probe_status})" in reason, reason

    @pytest.mark.parametrize("probe_status", [1, 3, 125, 126, 127, 129, 137, 143])
    def test_head_rev_parse_unanticipated_status_denied(
        self, isolated_home, git_repo, git_timeout_shim, probe_status
    ):
        """Any HEAD probe status other than 0 or 128 must deny, for the same
        reason as the work-tree probe case above, 129 included. The timeout and
        SIGTERM-immune HEAD probe cases above cover 124 and 137 against a
        real cap. Worktree-only credential (see `_modify_unstaged`). The deny
        reason must name the injected status: a shim that stopped matching
        would fall through to the real scan and deny on the credential
        alone."""
        env = git_timeout_shim(
            '[ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]',
            exit_status=probe_status,
        )
        _modify_unstaged(git_repo, "file.txt", f"first\nsecond\ntoken {GHP_TOKEN}\n")
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=git_repo, extra_env=env)
        assert reason is not None and f"(exit {probe_status})" in reason, reason

    @pytest.mark.parametrize(
        ("diff_status", "expected_reason_texts", "absent_reason_texts"),
        [
            (1, ("failed",), ("killed",)),
            (3, ("failed",), ("killed",)),
            (125, ("failed",), ("killed",)),
            (126, ("failed",), ("killed",)),
            (127, ("failed",), ("killed",)),
            (128, ("failed",), ("killed",)),
            (129, ("failed",), ("killed",)),
            (124, ("killed",), ()),
            (137, ("killed",), ()),
            (143, ("killed",), ()),
        ],
        ids=[
            "status-1",
            "status-3",
            "status-125",
            "status-126",
            "status-127",
            "status-128",
            "status-129",
            "status-124-killed",
            "status-137-killed",
            "status-143-killed",
        ],
    )
    def test_staged_diff_nonzero_status_denied(
        self, isolated_home, git_repo, git_timeout_shim, diff_status, expected_reason_texts, absent_reason_texts
    ):
        """`git diff --cached` failing with any nonzero status must deny: an
        empty STAGED_DIFF would otherwise leave the always-on credential-value
        tier scanning nothing. The cap-kill statuses (124, 137, 143) must say
        "killed", and every other status, 129 (git's usage-error status)
        included, must not. The shim exits the parametrized status on any
        `git -c diff.relative=false diff --cached`, so this pins the message,
        not flag rejection. No cap is engaged: the
        shim exits with the status directly. The credential is staged, so the
        deny reason must name the injected status: a shim that stopped
        matching would deny on the credential alone."""
        env = git_timeout_shim(
            f'{DIFF_CALL_PREDICATE} && [ "$4" = "--cached" ]',
            exit_status=diff_status,
        )
        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
        assert reason is not None and f"(exit {diff_status})" in reason, reason
        for expected_reason_text in expected_reason_texts:
            assert expected_reason_text in reason, reason
        for absent_reason_text in absent_reason_texts:
            assert absent_reason_text not in reason, reason

    @pytest.mark.parametrize(
        ("diff_status", "expected_reason_texts", "absent_reason_texts"),
        [
            (1, ("failed",), ("killed",)),
            (3, ("failed",), ("killed",)),
            (125, ("failed",), ("killed",)),
            (126, ("failed",), ("killed",)),
            (127, ("failed",), ("killed",)),
            (128, ("failed",), ("killed",)),
            (129, ("failed",), ("killed",)),
            (124, ("killed",), ()),
            (137, ("killed",), ()),
            (143, ("killed",), ()),
        ],
        ids=[
            "status-1",
            "status-3",
            "status-125",
            "status-126",
            "status-127",
            "status-128",
            "status-129",
            "status-124-killed",
            "status-137-killed",
            "status-143-killed",
        ],
    )
    def test_head_diff_nonzero_status_denied(
        self, isolated_home, git_repo, git_timeout_shim, diff_status, expected_reason_texts, absent_reason_texts
    ):
        """Same invariant as the staged-diff case above, for `git diff HEAD`.
        Worktree-only credential (see `_modify_unstaged`): the staged-diff
        scan sees nothing. The deny reason must name the injected status and
        carry the status-specific text and lack the other branch's, for the
        same reason as above."""
        env = git_timeout_shim(
            f'{DIFF_CALL_PREDICATE} && [ "$4" = "HEAD" ]',
            exit_status=diff_status,
        )
        _modify_unstaged(git_repo, "file.txt", f"first\nsecond\ntoken {GHP_TOKEN}\n")
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=git_repo, extra_env=env)
        assert reason is not None and f"(exit {diff_status})" in reason, reason
        for expected_reason_text in expected_reason_texts:
            assert expected_reason_text in reason, reason
        for absent_reason_text in absent_reason_texts:
            assert absent_reason_text not in reason, reason

    def test_commit_outside_work_tree_allowed(self, isolated_home, tmp_path):
        """git's own fatal status (128), not a cap kill: a `git commit`
        issued from a cwd that is no work tree at all must still skip
        cleanly. GIT_CEILING_DIRECTORIES stops git's upward search below
        tmp_path's parent, so an ancestor repository cannot make the probe
        succeed and the case pass for the wrong reason. No shim or
        assert_cap_engaged -- nothing here is capped or killed, so attaching
        either would break on the assertion helper itself rather than on the
        behavior under test."""
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git commit -m wip"),
            cwd=not_a_repo,
            extra_env={"GIT_CEILING_DIRECTORIES": str(tmp_path.parent)},
        ) == "allow"

    def test_head_rev_parse_no_commits_allowed(self, isolated_home, tmp_path):
        """git's own fatal status (128): an unresolvable HEAD in a repo with
        no commits yet must skip the HEAD scan cleanly rather than the
        fail-closed guard misreading it as a cap kill. Needs its own `git
        init` -- `git_repo` already commits a file. No shim or
        assert_cap_engaged, and only clean content: this pins the skip on
        clean content and is NOT evidence that worktree-only content is
        scanned when HEAD is unborn."""
        repo = tmp_path / "no-commits-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        _stage(repo, "f.txt", "x\nclean\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=repo) == "allow"

    def test_head_rev_parse_no_commits_still_scans_staged_credential(self, isolated_home, tmp_path):
        """Pairs the allow case above: with HEAD unborn, the HEAD probe's 128
        skip covers only the HEAD-relative diff. A credential staged in the
        index of a first `git commit -a` must still be denied by the staged
        diff scan, so a future edit cannot widen that skip into an early exit
        that drops the always-on credential-value tier. Real `git init`, no
        shim or assert_cap_engaged. Worktree-only content on an unborn HEAD
        stays the documented residual and is not asserted here."""
        repo = tmp_path / "no-commits-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        _stage(repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=repo)
        assert reason is not None and "Credential value" in reason, reason

    # ------------------------------------------------------------------ #
    # Built-in generic patterns                                           #
    # ------------------------------------------------------------------ #

    def test_ssn_in_commit_message_denied(self, isolated_home, git_repo, pii_patterns):
        """The commit message (command string) is scanned, not just the diff."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(f"git commit -m 'ref {SSN}'"), cwd=git_repo) == "deny"

    def test_luhn_valid_card_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\ncard {CARD_VALID}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_luhn_invalid_run_allowed(self, isolated_home, git_repo, pii_patterns):
        """A 16-digit run that fails the Luhn checksum is not card-shaped."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nnum {CARD_BAD_LUHN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_luhn_valid_13_digit_card_denied(self, isolated_home, git_repo, pii_patterns):
        """13 digits is the lower bound of the card-length window (4222222222222
        is Luhn-valid)."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\ncard 4222222222222\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_luhn_valid_19_digit_card_denied(self, isolated_home, git_repo, pii_patterns):
        """19 digits is the upper bound of the card-length window
        (1111111111111111113 is Luhn-valid)."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\ncard 1111111111111111113\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_digit_run_below_13_allowed(self, isolated_home, git_repo, pii_patterns):
        """A 12-digit run is shorter than any card; the length window excludes it
        before the Luhn check runs."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nref 412345678901\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_digit_run_above_19_allowed(self, isolated_home, git_repo, pii_patterns):
        """A 20-digit run is longer than any card; the length window excludes it."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nref 41234567890123456789\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_word_adjacent_card_quote_split_denied(self, isolated_home, git_repo, pii_patterns):
        """GH-783: a credit-card-shaped digit run whose only preceding word
        boundary is an adjacent quote character (no space, e.g.
        `x"4111111111111111"`) must still deny under the raw+stripped union
        scan. SCAN_TARGET_UNQUOTED alone would miss this -- stripping the
        quote merges `x` and the digit run into `x4111111111111111` with no
        \\b boundary between them -- but the raw $SCAN_TARGET side of the
        SCAN_TARGET_BOTH union still has the quote and still matches.
        Mirrors deny-private-project-refs.sh's
        test_word_adjacent_tracker_id_quote_split_denied."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input(f'git commit -m \'card x"{CARD_VALID}" on file\''),
            cwd=git_repo,
        ) == "deny"

    # ------------------------------------------------------------------ #
    # Apostrophe-grouped thousands numerals (GH-1108) -- an ordinary       #
    # Swiss/Liechtenstein-style or C++-literal-style grouped numeral must  #
    # be allowed, unless stripping quotes would join its digits to        #
    # another digit                                                       #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "append_site",
        ["staged-diff", "head-diff", "quoted-message", "file-source"],
        ids=["staged-diff", "head-diff", "quoted-message", "file-source"],
    )
    def test_apostrophe_thousands_card_allowed_at_every_append_site(
        self, isolated_home, git_repo, pii_patterns, append_site
    ):
        """GH-1108: an apostrophe-grouped thousands numeral (a routine
        C++-style digit-separated integer literal) is not a credit-card
        number, so it must be allowed at every append site that feeds the
        SSN/credit-card scan -- the staged diff, a HEAD-relative diff (`git
        commit -a`), a double-quoted -m message, and a -F message-source
        file."""
        pii_patterns("# no user patterns\n")
        line = f"literal = {CARD_VALID_THOUSANDS};\n"
        if append_site == "staged-diff":
            _stage(git_repo, "f.txt", line)
            command = "git commit -m wip"
        elif append_site == "head-diff":
            _modify_unstaged(git_repo, "file.txt", f"first\nsecond\n{line}")
            command = "git commit -a -m wip"
        elif append_site == "quoted-message":
            _stage(git_repo, "f.txt", "x\nclean\n")
            command = f'git commit -m "literal is {CARD_VALID_THOUSANDS}"'
        else:
            _stage(git_repo, "f.txt", "x\nclean\n")
            msg_file = git_repo / "msg.txt"
            msg_file.write_text(f"commit summary\n\nliteral {CARD_VALID_THOUSANDS}\n")
            command = f"git commit -F {msg_file}"
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(command), cwd=git_repo) == "allow"

    @pytest.mark.parametrize(
        "row",
        ["card-13", "card-19", "both-on-one-line", "file-content-exact"],
        ids=["card-13", "card-19", "both-on-one-line", "file-content-exact"],
    )
    def test_apostrophe_thousands_card_length_and_line_boundaries_allowed(
        self, isolated_home, git_repo, pii_patterns, row
    ):
        """GH-1108: the mask must cover both ends of the credit-card length window and the
        two-numerals-on-one-line and file-content-exact boundary shapes.
          - card-13 / card-19: CARD_13_THOUSANDS and CARD_19_THOUSANDS cover both ends of
            the credit-card length window.
          - both-on-one-line: two distinct thousands numerals on one line separated by a
            single space pins that the mask's two global passes catch a numeral whose
            leading bound character the previous match on the same line consumed -- one
            pass alone would leave the second numeral unmasked.
          - file-content-exact: a numeral that is the entire content of a -F file with
            nothing before or after it exercises the mask's `^`/`$` line-edge boundary
            alternative, which no other row in this file reaches -- every other append
            site's content is prefixed by something, be it git's diff marker, `git
            commit`, or an existing -F fixture's own leading text."""
        pii_patterns("# no user patterns\n")
        if row == "card-13":
            _stage(git_repo, "f.txt", f"card {CARD_13_THOUSANDS}\n")
            command = "git commit -m wip"
        elif row == "card-19":
            _stage(git_repo, "f.txt", f"card {CARD_19_THOUSANDS}\n")
            command = "git commit -m wip"
        elif row == "both-on-one-line":
            _stage(git_repo, "f.txt", f"{CARD_13_THOUSANDS} {CARD_19_THOUSANDS}\n")
            command = "git commit -m wip"
        else:
            msg_file = git_repo / "msg.txt"
            msg_file.write_text(CARD_VALID_THOUSANDS)
            _stage(git_repo, "f.txt", "x\nclean\n")
            command = f"git commit -F {msg_file}"
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(command), cwd=git_repo) == "allow"

    @pytest.mark.parametrize(
        "append_site",
        ["staged-diff", "head-diff"],
        ids=["staged-diff", "head-diff"],
    )
    def test_apostrophe_thousands_ssn_last_group_allowed(
        self, isolated_home, git_repo, pii_patterns, append_site
    ):
        """GH-1108: an SSN whose last group is written as an apostrophe-grouped
        thousands numeral (SSN_LAST_GROUP_THOUSANDS) is not a real SSN. It must be
        allowed at the staged-diff and HEAD-diff append sites."""
        pii_patterns("# no user patterns\n")
        line = f"ref {SSN_LAST_GROUP_THOUSANDS}\n"
        if append_site == "staged-diff":
            _stage(git_repo, "f.txt", line)
            command = "git commit -m wip"
        else:
            _modify_unstaged(git_repo, "file.txt", f"first\nsecond\n{line}")
            command = "git commit -a -m wip"
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(command), cwd=git_repo) == "allow"

    @pytest.mark.parametrize(
        "row",
        [
            "apostrophe-every-four-digits",
            "four-digit-leading-group",
            "four-digit-trailing-group",
            "quote-split-first-digit",
            "backslash-split-first-digit",
            "dollar-quote-split-first-digit",
            "quote-split-last-digit",
            "double-quote-splice-in-message",
            "raw-half-match-alongside-thousands-copy",
            "ssn-non-thousands-join",
        ],
        ids=[
            "apostrophe-every-four-digits",
            "four-digit-leading-group",
            "four-digit-trailing-group",
            "quote-split-first-digit",
            "backslash-split-first-digit",
            "dollar-quote-split-first-digit",
            "quote-split-last-digit",
            "double-quote-splice-in-message",
            "raw-half-match-alongside-thousands-copy",
            "ssn-non-thousands-join",
        ],
    )
    def test_shapes_that_are_not_apostrophe_thousands_numerals_still_denied(
        self, isolated_home, git_repo, pii_patterns, row
    ):
        """GH-1108: none of these shapes is a bare apostrophe-grouped
        thousands numeral, so the mask must leave every one of them
        untouched and the pre-existing deny behavior must be unchanged --
        these rows pass identically before and after the fix.
          - apostrophe-every-four-digits: not a thousands grouping at all.
          - four-digit-leading-group / four-digit-trailing-group: the mask
            requires a non-digit, non-joining-character bound on each side,
            and here that bound is a digit, not a valid boundary.
          - quote-split-first-digit / backslash-split-first-digit /
            dollar-quote-split-first-digit / quote-split-last-digit: a
            joining character -- a quote, backslash, or dollar-quote --
            sits directly between a lone digit and the grouped numeral, so
            the boundary the mask needs is itself a joining character, not
            a true bound.
          - double-quote-splice-in-message: an ordinary double-quote
            splice with no apostrophe to mask -- the existing quote-splice
            deny is untouched by this change.
          - raw-half-match-alongside-thousands-copy: a contiguous,
            unmasked copy of the card elsewhere in the same file still
            matches on the raw half regardless of a masked copy nearby.
          - ssn-non-thousands-join: `NN'N-NN-NNNN` strips back to an
            ordinary `NNN-NN-NNNN` SSN shape; it was never a thousands
            numeral for the mask to recognize."""
        pii_patterns("# no user patterns\n")
        command = "git commit -m wip"
        if row == "apostrophe-every-four-digits":
            grouped_by_four = "'".join(CARD_VALID[i : i + 4] for i in range(0, len(CARD_VALID), 4))
            _stage(git_repo, "f.txt", f"num {grouped_by_four}\n")
        elif row == "four-digit-leading-group":
            value = CARD_VALID[:4] + "'" + _thousands_grouped(CARD_VALID[4:])
            _stage(git_repo, "f.txt", f"num {value}\n")
        elif row == "four-digit-trailing-group":
            value = _thousands_grouped(CARD_VALID[:12]) + "'" + CARD_VALID[12:]
            _stage(git_repo, "f.txt", f"num {value}\n")
        elif row == "quote-split-first-digit":
            value = CARD_VALID[0] + '"' + _thousands_grouped(CARD_VALID[1:])
            _stage(git_repo, "f.txt", f"num {value}\n")
        elif row == "backslash-split-first-digit":
            # A dropped backslash in mask_thousands_numerals's own bracket-
            # expression escaping would surface here (and in
            # dollar-quote-split-first-digit below) as this value wrongly
            # allowed instead of denied.
            value = CARD_VALID[0] + "\\" + _thousands_grouped(CARD_VALID[1:])
            _stage(git_repo, "f.txt", f"num {value}\n")
        elif row == "dollar-quote-split-first-digit":
            value = CARD_VALID[0] + "$'" + _thousands_grouped(CARD_VALID[1:])
            _stage(git_repo, "f.txt", f"num {value}\n")
        elif row == "quote-split-last-digit":
            value = _thousands_grouped(CARD_VALID[:15]) + '"' + CARD_VALID[15]
            _stage(git_repo, "f.txt", f"num {value}\n")
        elif row == "double-quote-splice-in-message":
            _stage(git_repo, "f.txt", "x\nclean\n")
            first_half, second_half = CARD_VALID[:8], CARD_VALID[8:]
            command = f'git commit -m "{first_half}""{second_half}"'
        elif row == "raw-half-match-alongside-thousands-copy":
            _stage(git_repo, "f.txt", f"{CARD_VALID} {CARD_VALID_THOUSANDS}\n")
        else:
            value = SSN[:2] + "'" + SSN[2:]
            _stage(git_repo, "f.txt", f"ref {value}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(command), cwd=git_repo) == "deny"

    @pytest.mark.parametrize(
        "companion",
        ["none", "staged-file", "second-message"],
        ids=["no-companion", "companion-staged-file", "companion-second-message"],
    )
    def test_apostrophe_idiom_splice_card_denied_regardless_of_companion(
        self, isolated_home, git_repo, pii_patterns, companion
    ):
        """GH-1108: a card value written with the bash `'\\''` idiom inside
        a single-quoted -m has no bare apostrophe left in the raw command
        text (the idiom replaces each `'` with a four-character escape
        sequence), so the mask cannot recognize it as a thousands numeral.
        It still joins into a contiguous Luhn-valid run once
        _lib_strip_shell_quotes simulates the shell's own quote removal, and
        must still deny. A genuine, maskable copy of the same value elsewhere
        in the commit -- staged in a file, or in a second double-quoted -m on
        the same command line -- does not change this occurrence's verdict,
        because masking is judged per occurrence."""
        pii_patterns("# no user patterns\n")
        idiom_value = CARD_VALID_THOUSANDS.replace("'", "'\\''")
        command = f"git commit -m 'card {idiom_value}'"
        if companion == "none":
            _stage(git_repo, "f.txt", "x\nclean\n")
        elif companion == "staged-file":
            _stage(git_repo, "companion.txt", f"companion {CARD_VALID_THOUSANDS}\n")
        else:
            _stage(git_repo, "f.txt", "x\nclean\n")
            command += f' -m "companion {CARD_VALID_THOUSANDS}"'
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(command), cwd=git_repo) == "deny"

    def test_apostrophe_idiom_splice_ssn_denied_with_genuine_copy_staged(
        self, isolated_home, git_repo, pii_patterns
    ):
        """GH-1108: SSN counterpart of the card case above -- a genuine
        SSN_LAST_GROUP_THOUSANDS staged in a file does not vouch for its
        own `'\\''`-idiom-spliced copy in the commit message. A splice
        spelled any other way than the idiom still denies at each
        occurrence, whatever copies of the value exist elsewhere in the
        commit: masking is judged per occurrence, not per value."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"ref {SSN_LAST_GROUP_THOUSANDS}\n")
        idiom_value = SSN_LAST_GROUP_THOUSANDS.replace("'", "'\\''")
        command = f"git commit -m 'ref {idiom_value}'"
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(command), cwd=git_repo) == "deny"

    @pytest.mark.parametrize(
        "row",
        ["card", "ssn"],
        ids=["card", "ssn"],
    )
    def test_masked_numeral_allows_but_distinct_spliced_value_still_denied(
        self, isolated_home, git_repo, pii_patterns, row
    ):
        """GH-1108: masking a thousands numeral to allow it must not blind
        the credit-card/SSN scan to a distinct, genuinely quote-spliced
        value elsewhere in the same commit -- pinning that the new
        raw+masked+stripped union still carries a stripped half the splice
        needs to join on. The SSN row is the card row's counterpart."""
        pii_patterns("# no user patterns\n")
        if row == "card":
            _stage(git_repo, "f.txt", f"card {CARD_13_THOUSANDS}\n")
            first_half, second_half = CARD_VALID[:8], CARD_VALID[8:]
            command = f'git commit -m "card {first_half}""{second_half}"'
        else:
            _stage(git_repo, "f.txt", f"ref {SSN_LAST_GROUP_THOUSANDS}\n")
            second_ssn = "987-65-4321"
            first_half, second_half = second_ssn[:7], second_ssn[7:]
            command = f'git commit -m "ref {first_half}""{second_half}"'
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input(command),
            cwd=git_repo,
        ) == "deny"

    def test_mask_sed_shim_curly_brace_failure_denied(self, isolated_home, git_repo, pii_patterns, tmp_path):
        """GH-1108: fail-closed pin for the mask's own sed call --
        mask_thousands_numerals's expression is the only sed invocation in
        this hook containing the interval quantifier `{1,3}` (_lib.sh has
        no such token), so a shim that fails only on an argument
        containing that token isolates the mask's own SSN_CC_MASKED_EXIT
        check from every other sed call site in the hook. The shim
        otherwise execs the real sed."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nclean\n")

        shim_dir = tmp_path / "sed-fails-on-mask-expression-only"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            for arg in "$@"; do
              case "$arg" in
                *'{{1,3}}'*) exit 1 ;;
              esac
            done
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        reason = run_hook_reason(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git commit -m wip"),
            cwd=git_repo,
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        assert reason is not None and "could not mask thousands numerals" in reason, reason

    def test_ssn_cc_strip_sed_shim_failure_denied(self, isolated_home, git_repo, pii_patterns, tmp_path):
        """GH-1108: fail-closed pin for the SSN/credit-card union's own
        strip call, isolated from the mask's own sed call above. The shim
        exits 1 only when its stdin already carries the masked shape
        (`<marker> <marker>`, the numeral replaced by a space). Only the
        strip-of-already-masked-text call ever sees that shape: the mask's
        own call sees the pre-mask `<marker>N'NNN<marker>` and passes
        through untouched, and every other _lib_strip_shell_quotes call
        site in the hook sees neither shape in its own stdin."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"
        pii_patterns("# no user patterns\n")
        marker = "GH1108STRIPMARKER"
        _stage(git_repo, "f.txt", f"{marker}1'234{marker}\n")

        shim_dir = tmp_path / "sed-fails-on-masked-marker-pair"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            if [ "$2" = "-e" ]; then
              input=$(cat)
              case "$input" in
                *"{marker} {marker}"*) exit 1 ;;
              esac
              printf '%s' "$input" | "{real_sed}" "$@"
            else
              exec "{real_sed}" "$@"
            fi
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        reason = run_hook_reason(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git commit -m wip"),
            cwd=git_repo,
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        assert reason is not None and "could not quote-strip the SSN/credit-card scan target" in reason, reason

    # ------------------------------------------------------------------ #
    # Added lines only — removing PII must never be blocked               #
    # ------------------------------------------------------------------ #

    def test_removed_ssn_line_allowed(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _commit(git_repo, "legacy.txt", f"old\nSSN {SSN}\n")
        _stage(git_repo, "legacy.txt", "old\n")  # removes the SSN line
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m cleanup"), cwd=git_repo) == "allow"

    # ------------------------------------------------------------------ #
    # User patterns from pii-patterns.md                                  #
    # ------------------------------------------------------------------ #

    def test_user_pattern_match_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("MRN: [0-9]{8}\n")
        _stage(git_repo, "f.txt", "x\npatient MRN 80675309\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_user_pattern_no_match_allowed(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("MRN: [0-9]{8}\n")
        _stage(git_repo, "f.txt", "x\nshort id 4242\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    # ------------------------------------------------------------------ #
    # Deny message — label only, never the value or the user regex        #
    # ------------------------------------------------------------------ #

    def test_deny_message_names_builtin_label_not_value(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo)
        assert reason is not None
        assert "US Social Security number" in reason
        assert SSN not in reason

    def test_deny_message_names_user_label_not_regex(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("MRN: [0-9]{8}\n")
        _stage(git_repo, "f.txt", "x\npatient MRN 80675309\n")
        reason = run_hook_reason(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo)
        assert reason is not None
        assert "MRN" in reason
        assert "[0-9]{8}" not in reason  # the regex is itself a fingerprint
        assert "80675309" not in reason  # the matched value is PII

    # ------------------------------------------------------------------ #
    # Working-tree forms — content committed outside the index            #
    # ------------------------------------------------------------------ #

    def test_commit_all_flag_scans_worktree(self, isolated_home, git_repo, pii_patterns):
        """`git commit -am` stages tracked modifications after the hook fires,
        so the hook must additionally scan `git diff HEAD`."""
        pii_patterns("# no user patterns\n")
        # file.txt is tracked; add an unstaged modification carrying an SSN.
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -am wip"), cwd=git_repo) == "deny"

    def test_pathspec_form_scans_worktree(self, isolated_home, git_repo, pii_patterns):
        """`git commit -- <path>` commits working-tree content of the path."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip -- file.txt"), cwd=git_repo) == "deny"

    def test_amend_pathspec_scans_worktree(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit --amend file.txt"), cwd=git_repo) == "deny"

    def test_plain_commit_ignores_unstaged_worktree_pii(self, isolated_home, git_repo, pii_patterns):
        """A plain `git commit` (no -a, no pathspec) commits only the index;
        unstaged tracked PII must not block it."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_multiword_message_does_not_trigger_head_scan(self, isolated_home, git_repo, pii_patterns):
        """GH-783: a naive uniform quote-strip (feeding the -m
        message to _lib_commit_fragment_has_worktree_target pre-stripped)
        would split a multi-word message into bare words, and the awk
        pathspec check would read the second word as a worktree target --
        widening the scan to `git diff HEAD` on an ordinary commit.
        Confirms the raw fragment still reaches that xargs-tokenizing
        consumer: unstaged tracked PII outside the index must not deny a
        plain, multi-word `-m` commit."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git commit -m 'fix the thing'"),
            cwd=git_repo,
        ) == "allow"

    def test_all_long_flag_scans_worktree(self, isolated_home, git_repo, pii_patterns):
        """`--all` is the long form of `-a`; it must trigger the HEAD scan too."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit --all -m wip"), cwd=git_repo) == "deny"

    def test_missing_awk_still_scans_worktree_for_pii(self, isolated_home, git_repo, pii_patterns, tmp_path):
        """`_lib_commit_fragment_has_worktree_target`'s shared fail-safe
        (missing awk/xargs reads as "target found") must still force the
        `git diff HEAD` scan at this call site, not just deny outright the
        way deny-invisible-commit-content.sh does at its own call site. A
        plain `git commit -m wip` (no -a) would normally skip this scan --
        see test_plain_commit_ignores_unstaged_worktree_pii -- so catching
        this fixture with awk absent pins the fail-safe direction
        specifically for this hook's own consequence (widen the scan), not
        just the other consumer's (deny outright)."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        farm_dir = tmp_path / "path-without-awk"
        farm_dir.mkdir()
        restricted_path = build_path_without("awk", farm_dir)
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git commit -m wip"),
            cwd=git_repo,
            extra_env={"PATH": restricted_path},
        ) == "deny"

    def test_all_flag_non_terminal_in_bundle_scans_worktree(self, isolated_home, git_repo, pii_patterns):
        """`-vam` carries `a` mid-bundle; detection must not require `a` last."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -vam wip"), cwd=git_repo) == "deny"

    def test_verbose_message_bundle_without_all_ignores_worktree(self, isolated_home, git_repo, pii_patterns):
        """`-vm` has no `a`: a plain commit, index only — unstaged PII must not
        block it, proving the bundle scan does not over-match."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -vm wip"), cwd=git_repo) == "allow"

    # ------------------------------------------------------------------ #
    # -F / --file message-source files                                    #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "pseudo_path",
        ["-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"],
        ids=["bare-dash", "dev-stdin", "dev-fd", "proc-fd"],
    )
    def test_F_pseudo_file_rejected(self, isolated_home, git_repo, pii_patterns, pseudo_path):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        reason = run_hook_reason(
            DENY_PII_IN_COMMITS_HOOK, bash_input(f"git commit -F {pseudo_path}"), cwd=git_repo
        )
        assert reason is not None
        assert "pseudo-file path" in reason

    def test_F_file_with_pii_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        msg_file = git_repo / "msg.txt"
        msg_file.write_text(f"commit summary\n\nseen SSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(f"git commit -F {msg_file}"), cwd=git_repo) == "deny"

    def test_F_clean_file_allowed(self, isolated_home, git_repo, pii_patterns):
        """`-F <clean file>` with a clean diff must pass — the deny paths above
        must not turn into a blanket block on the -F form."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        msg_file = git_repo / "msg.txt"
        msg_file.write_text("a clean commit summary\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input(f"git commit -F {msg_file}"), cwd=git_repo) == "allow"

    # ------------------------------------------------------------------ #
    # exclude: globs                                                      #
    # ------------------------------------------------------------------ #

    def test_exclude_glob_suppresses_fixture_match(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("exclude: fixtures/**\n")
        (git_repo / "fixtures").mkdir()
        _stage(git_repo, "fixtures/data.txt", f"synthetic\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "allow"

    def test_excluded_path_still_denies_other_paths(self, isolated_home, git_repo, pii_patterns):
        """An exclude: glob suppresses only the named path, not the rest of
        the diff."""
        pii_patterns("exclude: fixtures/**\n")
        (git_repo / "fixtures").mkdir()
        _stage(git_repo, "fixtures/data.txt", f"synthetic\nSSN {SSN}\n")
        _stage(git_repo, "leak.txt", f"oops\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_exclude_glob_applies_to_worktree_scan(self, isolated_home, git_repo, pii_patterns):
        """An exclude: glob must drop the path from the `git diff HEAD` scan,
        not only the staged scan — verified via the `-a` working-tree form."""
        pii_patterns("exclude: fixtures/**\n")
        (git_repo / "fixtures").mkdir()
        _commit(git_repo, "fixtures/data.txt", "synthetic baseline\n")
        (git_repo / "fixtures" / "data.txt").write_text(f"synthetic\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -am wip"), cwd=git_repo) == "allow"

    # ------------------------------------------------------------------ #
    # Globally-flagged commit forms still dispatch                        #
    # ------------------------------------------------------------------ #

    def test_git_c_config_flag_commit_detected(self, isolated_home, git_repo, pii_patterns):
        """`git -c key=val commit` is an ordinary, executable commit form;
        the global `-c` flag must not let it slip past detection."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git -c user.name=tester commit -m wip"),
            cwd=git_repo,
        ) == "deny"

    def test_git_C_path_flag_commit_detected(self, isolated_home, git_repo, pii_patterns):
        """`git -C <path> commit` likewise must dispatch; for a path inside
        the session repo the staged-diff scan still covers it."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git -C . commit -m wip"),
            cwd=git_repo,
        ) == "deny"

    def test_quoted_git_word_commit_detected(self, isolated_home, git_repo, pii_patterns):
        """GH-783 (git-word form): `"git" commit` must still dispatch --
        the per-fragment strip feeding the two matcher calls catches a
        quoted git word even though the -m message reaches
        _lib_commit_fragment_has_worktree_target unstripped."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input('"git" commit -m wip'),
            cwd=git_repo,
        ) == "deny"

    def test_quoted_subcommand_word_commit_detected(self, isolated_home, git_repo, pii_patterns):
        """GH-783 (subcommand-word form): `git "commit"` must independently
        dispatch too -- both _lib_fragment_invokes_git and
        _lib_extract_git_subcmd have to pass on the stripped fragment, so a
        fix closing only one word would miss this case."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input('git "commit" -m wip'),
            cwd=git_repo,
        ) == "deny"

    def test_git_fragment_unquoted_sed_failure_denied(self, isolated_home, git_repo, tmp_path):
        """GH-783: the per-fragment git_fragment_unquoted strip must fail
        closed on its own, immediately for the fragment carrying the
        commit -- not `continue` past it, which would silently skip
        scanning and let an unscanned commit through. A sed shim fails
        only on _lib_strip_shell_quotes's own `-e`-flagged invocation
        (unique to that function in this codebase), so
        _lib_split_fragments's own sed calls still succeed -- isolating
        this call site from the pre-existing, unchecked
        _lib_split_fragments failure mode, which would otherwise yield no
        fragments at all and bypass the fragment loop before this fix
        ever runs."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        shim_dir = tmp_path / "sed-fails-on-strip-shell-quotes-only"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            if [ "$2" = "-e" ]; then
              exit 1
            fi
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        assert (
            run_hook(
                DENY_PII_IN_COMMITS_HOOK,
                bash_input("git commit -m wip"),
                cwd=git_repo,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )

    def test_total_sed_absence_denied(self, isolated_home, git_repo, pii_patterns, tmp_path):
        """GH-783: with sed entirely absent from PATH, _lib_split_fragments's
        own unchecked failure (the sibling gap the shim-isolated test above
        deliberately avoids exercising) must still deny -- not silently
        yield zero fragments and fall through to the hook's normal allow
        path with a real, armed-pattern-matching SSN staged."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        assert (
            run_hook(
                DENY_PII_IN_COMMITS_HOOK,
                bash_input("git commit -m wip"),
                cwd=git_repo,
                extra_env={"PATH": restricted_path},
            )
            == "deny"
        )

    def test_scan_target_sed_failure_denied(self, isolated_home, git_repo, tmp_path):
        """GH-783: SCAN_TARGET_UNQUOTED_EXIT must fail closed on its own,
        isolated from GIT_FRAGMENTS_SPLIT_EXIT and git_fragment_unquoted_exit
        above -- all three checks call sed against text drawn from the same
        $COMMAND, so a total sed-absent test can't tell which one is
        catching the failure. A sed shim fails only on a `-e`-flagged call
        (_lib_strip_shell_quotes's own shape, shared by the per-fragment
        strip and this SCAN_TARGET_UNQUOTED strip) whose stdin carries a
        marker that straddles a `&&` fragment-split point -- present in the
        raw, unsplit $COMMAND that feeds SCAN_TARGET, but absent from
        every individual fragment the per-fragment strip sees, since
        _lib_split_fragments already broke the marker apart at that `&&`
        before the per-fragment strip ever runs. GIT_FRAGMENTS_SPLIT_EXIT's
        own sed calls (not `-e`-flagged) are left untouched by the shim
        regardless of marker content."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"
        marker = "SCANTARGETEXIT&&ISOLATIONMARKER"
        command = f"{marker} && git commit -m wip"

        shim_dir = tmp_path / "sed-fails-on-scan-target-only"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            if [ "$2" = "-e" ]; then
              input=$(cat)
              case "$input" in
                *"{marker}"*) exit 1 ;;
              esac
              printf '%s' "$input" | "{real_sed}" "$@"
            else
              exec "{real_sed}" "$@"
            fi
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        assert (
            run_hook(
                DENY_PII_IN_COMMITS_HOOK,
                bash_input(command),
                cwd=git_repo,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )

    # ------------------------------------------------------------------ #
    # --no-verify does not disable a PreToolUse hook                      #
    # ------------------------------------------------------------------ #

    def test_no_verify_still_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit --no-verify -m wip"), cwd=git_repo) == "deny"

    # ------------------------------------------------------------------ #
    # Malformed pii-patterns.md — fail closed                             #
    # ------------------------------------------------------------------ #

    def test_unlabelled_config_line_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("this line has no colon\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_uncompilable_regex_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("BadPat: [unclosed\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    def test_empty_value_config_line_denied(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("MRN:\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

    # ------------------------------------------------------------------ #
    # Dispatch — non-commit and non-Bash pass through                     #
    # ------------------------------------------------------------------ #

    def test_non_commit_bash_passthrough(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git status"), cwd=git_repo) == "allow"

    def test_non_bash_tool_passthrough(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, read_input(f"/tmp/{SSN}.txt"), cwd=git_repo) == "allow"

    def test_non_string_command_field_handled(self, isolated_home, git_repo, pii_patterns):
        """A non-string `command` field is a malformed Bash tool call that the
        Bash tool itself cannot execute — there is no git commit to gate, so
        the hook produces a clean allow without erroring under `set -u`."""
        pii_patterns("# no user patterns\n")
        payload = {"tool_name": "Bash", "tool_input": {"command": {"unexpected": "object"}}}
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, payload, cwd=git_repo) == "allow"

    def test_chained_add_then_commit_detected(self, isolated_home, git_repo, pii_patterns):
        """`git add . && git commit` — the commit fragment past `&&` must still
        dispatch. The SSN is already staged at hook time, so the staged scan
        catches it regardless of where `git add` runs in the chain."""
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", f"x\nSSN {SSN}\n")
        assert run_hook(
            DENY_PII_IN_COMMITS_HOOK,
            bash_input("git add . && git commit -m wip"),
            cwd=git_repo,
        ) == "deny"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(DENY_PII_IN_COMMITS_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        # Assert the fail-closed path specifically fired — not some other deny.
        assert "could not parse" in payload["hookSpecificOutput"]["permissionDecisionReason"]
