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
import time

import pytest
from helpers import HOOKS_DIR, bash_input, read_input, run_hook, run_hook_reason

DENY_PII_IN_COMMITS_HOOK = HOOKS_DIR / "deny-pii-in-commits.sh"

SSN = "123-45-6789"
CARD_VALID = "4111111111111111"
CARD_BAD_LUHN = "4111111111111112"
GHP_TOKEN = "ghp_abcdefghijklmnopqrstuvwx1234"


def _stage(repo, name, content):
    """Write `content` to `repo/name` and stage it."""
    (repo / name).write_text(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def _commit(repo, name, content, message="seed"):
    """Write, stage, and commit `repo/name` so it becomes tracked HEAD content."""
    _stage(repo, name, content)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


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

    # ------------------------------------------------------------------ #
    # Credential-value sub-check — unconditional, no pii-patterns.md      #
    # ------------------------------------------------------------------ #
    # No ~/.claude/pii-patterns.md is created for any test in this section:
    # that is the point being pinned (the credential-value scan does not
    # wait for arming, unlike the SSN/credit-card/user-pattern tier above).

    def test_unarmed_credential_value_in_diff_denied(self, isolated_home, git_repo):
        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo) == "deny"

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

    def test_unarmed_f_pseudo_file_still_denied(self, isolated_home, git_repo):
        """The `-F`/pseudo-file fail-closed check used to run only for armed
        users, since the whole commit-detection/extraction path lived
        behind the arming check. Hoisting that machinery above the arming
        check makes this reachable for unarmed users too — pinned so a
        slip that leaves this check under the old `if` doesn't silently
        reopen a fail-closed path with nothing catching it."""
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -F -"), cwd=git_repo) == "deny"

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

    def test_staged_diff_git_timeout_denied(self, isolated_home, git_repo, tmp_path):
        """Required regression test for a High-severity finding: `git diff
        --cached`'s _lib_capped exit status previously went unchecked, so a
        timeout silently left STAGED_DIFF empty/truncated and the always-on
        credential-value tier scanned nothing — the commit landed with no
        scan, no error, no signal. Fails closed (deny) now instead. A fake
        `git` shadows only the `diff` subcommand (sleeping past the 5s cap)
        and passes every other subcommand through to the real binary."""
        real_git = shutil.which("git")
        if not real_git:
            pytest.skip("git not found in PATH")
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        fake_git = tmp_path / "git"
        fake_git.write_text(f'#!/bin/bash\nif [ "$1" = "diff" ]; then sleep 10; fi\nexec {real_git} "$@"\n')
        fake_git.chmod(0o755)

        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
        start = time.monotonic()
        decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
        elapsed = time.monotonic() - start
        assert decision == "deny"
        assert elapsed < 9.5, f"expected the 5s _lib_capped timeout to fire (shim sleeps 10s if it does not), took {elapsed:.1f}s"

    def test_work_tree_check_git_timeout_denied(self, isolated_home, git_repo, tmp_path):
        """Required regression test: `git rev-parse --is-inside-work-tree`'s
        _lib_capped exit status must also fail closed on timeout (exit 124)
        rather than exiting 0 and skipping every scan tier, including the
        always-on credential-value one."""
        real_git = shutil.which("git")
        if not real_git:
            pytest.skip("git not found in PATH")
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        fake_git = tmp_path / "git"
        fake_git.write_text(
            f'#!/bin/bash\nif [ "$1" = "rev-parse" ] && [ "$2" = "--is-inside-work-tree" ]; then sleep 10; fi\n'
            f'exec {real_git} "$@"\n'
        )
        fake_git.chmod(0o755)

        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
        start = time.monotonic()
        decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
        elapsed = time.monotonic() - start
        assert decision == "deny"
        assert elapsed < 9.5, f"expected the 5s _lib_capped timeout to fire (shim sleeps 10s if it does not), took {elapsed:.1f}s"

    def test_head_rev_parse_git_timeout_denied(self, isolated_home, git_repo, tmp_path):
        """Required regression test: `git rev-parse HEAD`'s _lib_capped exit
        status must fail closed on timeout, distinct from the legitimate
        no-HEAD-yet (unborn branch) skip. `git commit -a` triggers
        HEAD_SCAN_NEEDED so this call site is reached."""
        real_git = shutil.which("git")
        if not real_git:
            pytest.skip("git not found in PATH")
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        fake_git = tmp_path / "git"
        fake_git.write_text(
            f'#!/bin/bash\nif [ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]; then sleep 10; fi\n'
            f'exec {real_git} "$@"\n'
        )
        fake_git.chmod(0o755)

        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
        start = time.monotonic()
        decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=git_repo, extra_env=env)
        elapsed = time.monotonic() - start
        assert decision == "deny"
        assert elapsed < 9.5, f"expected the 5s _lib_capped timeout to fire (shim sleeps 10s if it does not), took {elapsed:.1f}s"

    def test_head_diff_git_timeout_denied(self, isolated_home, git_repo, tmp_path):
        """Required regression test: `git diff HEAD`'s _lib_capped exit
        status must fail closed on timeout, mirroring the STAGED_DIFF fix
        for the HEAD-relative diff specifically."""
        real_git = shutil.which("git")
        if not real_git:
            pytest.skip("git not found in PATH")
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        fake_git = tmp_path / "git"
        fake_git.write_text(
            f'#!/bin/bash\nif [ "$1" = "diff" ] && [ "$2" = "HEAD" ]; then sleep 10; fi\n'
            f'exec {real_git} "$@"\n'
        )
        fake_git.chmod(0o755)

        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")
        env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
        start = time.monotonic()
        decision = run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -a -m wip"), cwd=git_repo, extra_env=env)
        elapsed = time.monotonic() - start
        assert decision == "deny"
        assert elapsed < 9.5, f"expected the 5s _lib_capped timeout to fire (shim sleeps 10s if it does not), took {elapsed:.1f}s"

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

    def test_all_long_flag_scans_worktree(self, isolated_home, git_repo, pii_patterns):
        """`--all` is the long form of `-a`; it must trigger the HEAD scan too."""
        pii_patterns("# no user patterns\n")
        (git_repo / "file.txt").write_text(f"first\nsecond\nSSN {SSN}\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit --all -m wip"), cwd=git_repo) == "deny"

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

    def test_F_pseudo_file_rejected(self, isolated_home, git_repo, pii_patterns):
        pii_patterns("# no user patterns\n")
        _stage(git_repo, "f.txt", "x\nclean\n")
        assert run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -F -"), cwd=git_repo) == "deny"

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
