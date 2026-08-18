"""Tests for require-worktree-for-git-writes.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from conftest import _dead_pid, _seed_session, _worktree_lock_reason
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    run_hook,
    run_hook_reason,
)

WORKTREE_HOOK = HOOKS_DIR / "require-worktree-for-git-writes.sh"


class TestRequireWorktreeForGitWrites:
    def test_no_sentinel_allows_commit(self, non_opted_repo, isolated_home):
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_opted_repo) == "allow"

    def test_no_sentinel_allows_push(self, non_opted_repo, isolated_home):
        assert run_hook(WORKTREE_HOOK, bash_input("git push origin main"), cwd=non_opted_repo) == "allow"

    def test_opted_in_main_tree_denies_commit(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_push(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git push origin main"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_rebase(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git rebase origin/main"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_reset(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git reset --hard HEAD~1"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_checkout(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git checkout main"), cwd=opted_in_repo) == "deny"

    def test_stray_untracked_marker_still_denies(self, stray_marker_repo):
        """GH-427: an untracked .claude/worktree-required still activates
        enforcement — this test locks that existence-based behavior in place;
        the fix for GH-427 is a deny-message hint, not a logic change."""
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=stray_marker_repo) == "deny"

    def test_stray_untracked_marker_deny_includes_hint(self, stray_marker_repo):
        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=stray_marker_repo)
        assert reason is not None
        assert "untracked" in reason
        # The unique hint phrase, not a bare path substring — the boilerplate
        # deny text already mentions .claude/worktree-required twice regardless
        # of whether the hint fired, so that alone wouldn't prove the hint ran.
        assert "present but untracked" in reason

    def test_stray_untracked_marker_hint_survives_neither_timeout_nor_gtimeout_present(
        self, stray_marker_repo, tmp_path
    ):
        """Fail-open regression for _lib_stray_marker_hint's own _lib_capped
        git ls-files call: with neither binary present, the untracked-marker
        hint must still reach the deny reason rather than silently dropping
        out (see _stub_bin_without_timeout below for the PATH shape)."""
        stub_bin = self._stub_bin_without_timeout(tmp_path)
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo"),
            cwd=stray_marker_repo,
            extra_env={"PATH": str(stub_bin)},
        )
        assert reason is not None
        assert "present but untracked" in reason

    def test_committed_marker_deny_omits_stray_hint(self, opted_in_repo):
        """The hint is GH-427-specific noise for the normal opted-in case —
        a committed marker must not trigger it."""
        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=opted_in_repo)
        assert reason is not None
        assert "untracked" not in reason

    def test_staged_not_committed_marker_deny_omits_stray_hint(self, staged_marker_repo):
        """The hint's actual gate is index-tracked (git ls-files --error-unmatch),
        not HEAD-committed — a staged-but-uncommitted marker already satisfies it."""
        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=staged_marker_repo)
        assert reason is not None
        assert "untracked" not in reason

    def test_machine_marker_only_deny_omits_stray_hint(self, non_opted_repo, user_marker_home):
        """Enforcement active via the machine-level marker alone (no repo-root
        .claude/worktree-required at all) must not surface the untracked-repo-
        sentinel hint — the hint's first check ([ -f repo-root marker ]) should
        short-circuit before ever reaching the git ls-files branch."""
        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_opted_repo)
        assert reason is not None
        assert "untracked" not in reason

    def test_stray_marker_omitted_from_unrelated_deny_path(self, stray_marker_repo):
        """The hint is scoped to the 'not on read-only allowlist' deny site only
        — a stray marker present alongside an unrelated deny (unparseable git
        subcommand) must not pull the hint into that message too."""
        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git -C /tmp"), cwd=stray_marker_repo)
        assert reason is not None
        assert "could not determine the git subcommand" in reason
        assert "untracked" not in reason

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git diff HEAD~1",
            "git show HEAD",
            "git fetch origin",
            "git branch",
            "git rev-parse --show-toplevel",
            "git remote -v",
            "git blame file.txt",
        ],
    )
    def test_opted_in_main_tree_allows_readonly(self, isolated_home, opted_in_repo, command):
        assert run_hook(WORKTREE_HOOK, bash_input(command), cwd=opted_in_repo) == "allow"

    def test_opted_in_chained_write_denies(self, opted_in_repo):
        """A read-only fragment followed by a write still denies — the
        write fragment alone is enough."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git status && git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_opted_in_chained_readonly_allows(self, isolated_home, opted_in_repo):
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git status && git log --oneline"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_opted_in_worktree_allows_commit(self, isolated_home, opted_in_with_worktree):
        _, worktree = opted_in_with_worktree
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=worktree) == "allow"

    def test_opted_in_worktree_allows_push(self, isolated_home, opted_in_with_worktree):
        _, worktree = opted_in_with_worktree
        assert run_hook(WORKTREE_HOOK, bash_input("git push origin feature"), cwd=worktree) == "allow"

    def test_non_git_command_allowed(self, isolated_home, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("ls -la"), cwd=opted_in_repo) == "allow"

    def test_outside_git_repo_allowed(self, tmp_path, isolated_home):
        """Not in a git repo — nothing to enforce."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"

    def test_git_dash_C_flag_stripped(self, opted_in_repo):
        """`git -C /tmp commit` should parse as `commit` — flag and path stripped."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -C /tmp commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_git_no_pager_log_allowed(self, isolated_home, opted_in_repo):
        """`git --no-pager log` parses as `log` — flag stripped."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git --no-pager log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_parse_failure_denies(self, opted_in_repo):
        """Fail-closed: if we can't identify the subcommand, deny with a
        recognizable reason (distinguishable from an allowlist miss)."""
        result = subprocess.run(
            [str(WORKTREE_HOOK)],
            input=json.dumps(bash_input("git -C /tmp")),
            capture_output=True,
            text=True,
            cwd=opted_in_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected a deny verdict"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "could not determine the git subcommand" in payload["hookSpecificOutput"]["permissionDecisionReason"]

    def test_worktree_add_allowed_on_main_tree(self, opted_in_repo):
        """`git worktree add` is the bootstrap for this whole mechanism.
        Denying it would strand users whose only escape hatch is creating
        a worktree from the main tree. Explicitly allowlisted."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git worktree add .claude/worktrees/feature -b feature"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_git_config_denied_on_main_tree(self, opted_in_repo):
        """`git config` can install malicious aliases, pagers, credential
        helpers that execute arbitrary code on next git invocation. Not
        safe as 'read-only' even though it doesn't touch the working tree."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git config --get user.email"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_env_prefix_command_denied(self, opted_in_repo):
        """`env FOO=1 git commit` — after-git strip still yields `commit`."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("env FOO=1 git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_sudo_prefix_command_denied(self, opted_in_repo):
        """Sudo prefix doesn't change subcommand extraction."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("sudo git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_pipe_readonly_allowed(self, isolated_home, opted_in_repo):
        """Pipe-chained read-only commands pass; each fragment parsed separately."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git log --oneline | grep foo"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_pipe_then_write_denied(self, opted_in_repo):
        """A write after a pipe+&& is still caught — pipe and && both split."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git status | head && git commit -m x"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_background_write_denied(self, opted_in_repo):
        """`git push &` — the & isn't split but `push` is still extracted."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git push &"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_empty_command_allowed(self, isolated_home, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input(""), cwd=opted_in_repo) == "allow"

    def test_whitespace_only_command_allowed(self, isolated_home, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("   "), cwd=opted_in_repo) == "allow"

    def test_git_dash_c_inline_config_allowed(self, isolated_home, opted_in_repo):
        """`git -c key=val log` — the -c inline config flag consumes the
        next word; subcommand `log` is on the allowlist."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -c user.email=t@t.com log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_git_dir_flag_allowed(self, isolated_home, opted_in_repo):
        """`git --git-dir /tmp/.git log` — --git-dir consumes next word."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git --git-dir /tmp/.git log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_sentinel_as_directory_treated_as_unopted(self, tmp_path, isolated_home):
        """`-f` is false for directories, so a directory at
        .claude/worktree-required leaves the repo effectively unopted."""
        repo = tmp_path / "weird"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "worktree-required").mkdir()
        (repo / "f.txt").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"

    def test_malformed_json_stdin_denies(self, opted_in_repo):
        """jq parse failure → fail-closed deny. We skip `run_hook` and feed
        raw non-JSON directly."""
        result = subprocess.run(
            [str(WORKTREE_HOOK)],
            input="this is not JSON at all{",
            capture_output=True,
            text=True,
            cwd=opted_in_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected a deny verdict on malformed JSON"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_git_dir_env_var_does_not_bypass(self, opted_in_repo):
        """GIT_DIR=/anything/worktrees/x must NOT make the main tree look
        like a linked worktree. The hook unsets GIT_DIR defensively."""
        env = {**os.environ, "GIT_DIR": "/tmp/fake/worktrees/spoofed"}
        result = subprocess.run(
            [str(WORKTREE_HOOK)],
            input=json.dumps(bash_input("git commit -m foo")),
            capture_output=True,
            text=True,
            cwd=opted_in_repo,
            env=env,
            check=False,
        )
        assert result.stdout.strip(), "expected deny despite GIT_DIR spoof"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_non_bash_tool_allowed(self, opted_in_repo):
        """Edit tool inputs have no .tool_input.command — hook no-ops."""
        assert run_hook(WORKTREE_HOOK, edit_input("/tmp/foo.txt"), cwd=opted_in_repo) == "allow"

    # -- Word-boundary false-positive regression ----------------------------
    # Regression: the hook originally used `*git*` substring checks that
    # matched `.github`, `.gitignore`, `github.com`, and similar, blocking
    # harmless `ls .github/workflows/` reads. The fix requires `git` to
    # appear as a command word (bounded by non-alnum or string edges),
    # and each fragment must have a word equal to `git` or ending in
    # `/git` to be treated as a git invocation.

    @pytest.mark.parametrize(
        "command",
        [
            "ls .github/workflows/",
            "cat .gitignore",
            "grep -r github.com /src",
            "find . -name '*.git'",
            "./git-foo",
            "gitk master",
        ],
        ids=[
            "ls-dotgithub",
            "cat-dotgitignore",
            "grep-githubcom",
            "find-dotgit",
            "git-foo-extension",
            "gitk-alnum-trailing",
        ],
    )
    def test_git_substring_in_non_git_command_allowed(self, isolated_home, opted_in_repo, command):
        """Commands that mention `git` only as a path/URL/prefix substring
        must not be treated as git invocations. `gitk` pins the regex's
        both-sides non-alnum requirement — a change that only kept the
        leading boundary would regress this case."""
        assert run_hook(WORKTREE_HOOK, bash_input(command), cwd=opted_in_repo) == "allow"

    def test_chained_dotgithub_read_and_git_log_allowed(self, isolated_home, opted_in_repo):
        """Read-only fragment touching `.github` followed by a read-only
        git command: both fragments must resolve correctly."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("ls .github/workflows/ && git log --oneline"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_chained_dotgitignore_read_and_git_commit_denied(self, opted_in_repo):
        """Fragment mentioning `.gitignore` must not mask a real `git
        commit` in a later fragment."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("cat .gitignore && git commit -m x"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_git_log_with_dotgithub_path_arg_allowed(self, isolated_home, opted_in_repo):
        """Real read-only git command whose arguments reference a `.github`
        path must still parse as its subcommand — `git log -- .github/...`
        is `log`, not denied."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git log -- .github/workflows/tests.yml"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_git_dash_C_readonly_ignores_cwd(self, isolated_home, opted_in_repo):
        """`git -C /tmp log` is a read — the allowlist governs regardless of
        cwd, so an out-of-repo -C target on a read is still allowed. Pins
        that reads never enter cwd/-C resolution at all."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -C /tmp log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_readonly_with_unresolved_dash_C_still_allows(self, isolated_home, opted_in_repo):
        """`git -C "$VAR" log` — the allowlist check must run BEFORE any
        c_status check, so an UNRESOLVED -C on a read still allows. Pins
        the branch order: a future refactor that checks c_status first
        would start denying legitimate reads like this one, and only this
        test (not the sibling literal-`-C` test above) would catch it."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input('git -C "$VAR" log'),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_subcommand_dash_C_is_not_global_flag(self, opted_in_repo):
        """`git commit -C HEAD` uses -C as commit's reuse-message flag, not
        the global working-dir flag — the parser must not treat it as a
        cwd-relocating -C, so this denies as a plain main-tree write."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git commit -C HEAD"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_cd_worktree_amp_git_commit_allowed_from_main_tree(self, isolated_home, opted_in_repo, tmp_path):
        """`cd <worktree> && git commit` from a MAIN-tree session — the
        literal cd resolves cleanly to a linked worktree, so the write is
        allowed. This is the headline flip: the old hook blanket-denied
        every `cd ... && git ...` chain regardless of where it led."""
        worktree = tmp_path / "feature-tree"
        subprocess.run(
            ["git", "worktree", "add", str(worktree), "-b", "feature-flip"],
            cwd=opted_in_repo,
            check=True,
            capture_output=True,
        )
        _seed_session(isolated_home, "cd-worktree-amp-commit-session")
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {worktree} && git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_cd_expansion_target_denies_write(self, opted_in_repo):
        """`cd "$REPO" && git commit` — shlex never expands $VAR, so the cd
        target is literal text, not a real path. The write must deny
        rather than silently keep judging at the stale session cwd."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input('cd "$REPO" && git commit -m foo'),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_cd_tilde_target_denies_write(self, opted_in_repo):
        """`cd ~/repo && git commit` — tilde is never expanded by the
        parser either; same unresolved-target deny as $VAR."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("cd ~/repo && git commit"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_dash_C_var_target_denies_write(self, opted_in_repo):
        """`git -C "$VAR" reset` — an unresolved global -C value must deny
        the write outright, not fall back to the session cwd (falling back
        would silently ignore a flag that genuinely retargets git)."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input('git -C "$VAR" reset --hard'),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_multiple_dash_C_flags_deny_write(self, opted_in_repo):
        """`git -C /a -C /b commit` — more than one global -C is ambiguous
        about which path git actually resolves against; deny rather than
        pick one."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -C /a -C /b commit"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_subshell_cd_does_not_relocate_write(self, opted_in_repo, tmp_path):
        """`(cd <worktree>) && git commit` from a main-tree session — the
        cd is scoped to the subshell and never affects the parent shell's
        cwd, so the write still runs (and is judged) at the main tree."""
        worktree = tmp_path / "feature-tree"
        subprocess.run(
            ["git", "worktree", "add", str(worktree), "-b", "feature-subshell"],
            cwd=opted_in_repo,
            check=True,
            capture_output=True,
        )
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"(cd {worktree}) && git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_command_substitution_write_denied_on_main_tree(self, opted_in_repo):
        """`$(git reset --hard)` on the main tree — a write reached only
        through command substitution is denied outright, regardless of
        session cwd, since nothing inside the group can be trusted to have
        relocated it."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("$(git reset --hard)"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_or_chain_write_denied(self, opted_in_repo):
        """`cd /bad || git commit` — a write reached via `||` is denied
        regardless of whether the preceding cd would have succeeded or
        failed in a real shell; the parser cannot evaluate that condition,
        so it refuses to guess rather than allow."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("cd /bad || git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_backgrounded_cd_write_denied(self, opted_in_repo, tmp_path):
        """`cd <worktree> & git push` — the cd is backgrounded (forks a
        subshell that never changes the parent shell's cwd), so the write
        actually runs at the real, unrelocated shell cwd. Verified bypass:
        `bash -c 'cd /tmp & pwd; wait'` prints the original directory, not
        the cd target. Denied regardless of the (wrongly-threaded) cwd."""
        worktree = tmp_path / "feature-tree"
        subprocess.run(
            ["git", "worktree", "add", str(worktree), "-b", "feature-bg"],
            cwd=opted_in_repo,
            check=True,
            capture_output=True,
        )
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {worktree} & git push"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_delimiter_injection_in_subcommand_denied(self, opted_in_repo):
        """A quoted git subcommand token containing an embedded tab/newline
        that spells out a fake CD record (`CD\\t<real-worktree>\\t;\\t0`)
        must not spoof `running_cwd` for a later real write in the same
        command — a confirmed live bypass before the parser guarded the
        subcommand field the same way it already guarded cd-target/-C."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input('git "status\nCD\t/some/real/worktree\t;\t0"\n; git reset --hard'),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_time_wrapper_write_still_caught(self, opted_in_repo):
        """`time git commit` — no wrapper allowlist is needed: the parser
        finds `git` as a token wherever it appears in the segment, so a
        wrapper command in front of it doesn't hide the write."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("time git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_heredoc_prose_mentioning_git_allowed(self, isolated_home, opted_in_repo):
        """A heredoc body that merely mentions `git commit` in prose is not
        a real invocation — the heredoc-aware parser strips the body
        before tokenizing, so this allows instead of denying on a phantom
        match."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("cat <<EOF\nthis mentions git commit but is prose\nEOF"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_quoted_prose_mentioning_git_allowed(self, isolated_home, opted_in_repo):
        """`echo "git subcommands are neat"` — the quoted argument
        tokenizes as one word, which can never equal the bare token `git`,
        so this is not treated as a git invocation."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input('echo "git subcommands are neat"'),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_readonly_allowlist_additions_allowed(self, isolated_home, opted_in_repo):
        """The allowlist completion (merge-base, symbolic-ref, diff-tree,
        grep, ...) added to close the 157-FP read-only bucket."""
        for command in (
            "git merge-base HEAD origin/main",
            "git symbolic-ref HEAD",
            "git diff-tree HEAD~1 HEAD",
            "git grep foo",
        ):
            assert run_hook(WORKTREE_HOOK, bash_input(command), cwd=opted_in_repo) == "allow", command

    def test_python3_absent_denies(self, opted_in_repo, tmp_path):
        """Toolchain failure (python3 missing from PATH) must fail closed —
        this is a gate, not an advisory nudge, unlike nudge-error-mode-
        analysis.sh's fail-open python3 handling. PATH is replaced entirely
        with a stub directory containing only symlinks to the other tools
        this hook's code path actually invokes before ever reaching the
        python3 check (`cat` via _lib_parse_tool_input_or_deny, `dirname`
        to locate _lib.sh/the parser, `git`, `jq`, `timeout`) — not
        sha256sum/awk, which only _marker_lib_repo_hash uses and this hook
        never calls — so python3 is genuinely absent regardless of where
        the real binary lives on this machine. Mirrors test_lib.py's
        pytest.skip precedent: skip (don't silently under-symlink) when a
        required tool isn't found on the test machine."""
        stub_bin = tmp_path / "_stub_bin"
        stub_bin.mkdir()
        for tool in ("cat", "dirname", "git", "jq", "timeout"):
            real_path = shutil.which(tool)
            if not real_path:
                pytest.skip(f"{tool} not found in PATH")
            (stub_bin / tool).symlink_to(real_path)
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo"),
            cwd=opted_in_repo,
            extra_env={"PATH": str(stub_bin)},
        )
        assert reason is not None
        assert "python3" in reason

    def _stub_bin_without_timeout(self, tmp_path):
        """Stub PATH with only the binaries this hook's code path invokes
        (`cat`/`jq` via _lib.sh's JSON parsing, `dirname` to locate
        _lib.sh/the parser, `git`, `python3` for the command parser, `ps`
        and `tr` for _lib_worktree_collision_guard's session-identity
        ancestor walk on the worktree-allow path, `bash` for the guard's own
        noclobber lock-acquisition write), omitting both timeout(1) and
        gtimeout(1). Mirrors test_python3_absent_denies's shape; skips (does
        not silently under-symlink) when a needed real binary is itself
        absent."""
        stub_bin = tmp_path / "_stub_bin"
        stub_bin.mkdir()
        for tool in ("bash", "cat", "dirname", "git", "jq", "ps", "python3", "tr"):
            real_path = shutil.which(tool)
            if not real_path:
                pytest.skip(f"{tool} not found in PATH")
            (stub_bin / tool).symlink_to(real_path)
        return stub_bin

    def test_main_tree_write_denies_when_neither_timeout_nor_gtimeout_present(
        self, opted_in_repo, tmp_path
    ):
        """Fail-open regression: with neither binary present, _lib_capped
        runs the git/python3 calls uncapped (see _lib.sh) rather than
        silently skipping — the gate must still catch a main-tree write."""
        stub_bin = self._stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git commit -m foo"),
                cwd=opted_in_repo,
                extra_env={"PATH": str(stub_bin)},
            )
            == "deny"
        )

    def test_worktree_write_allowed_when_neither_timeout_nor_gtimeout_present(
        self, opted_in_repo, tmp_path, isolated_home
    ):
        """Companion allow case for the deny above: under the same PATH, a
        write that legitimately resolves to a linked worktree must still
        pass — without this, a fallback branch that always returns nonzero
        would masquerade as a working gate. Exercises the full parser path
        (cd resolution, python3 parse, effective-cwd rev-parse), unlike the
        deny case above which returns before the parser ever runs.

        Seeds an explicit session because this path also runs
        _lib_worktree_collision_guard, whose _lib_resolve_claude_pid needs
        a session file in this subprocess's real process ancestry — not
        guaranteed under CI or a backgrounded test runner."""
        worktree = tmp_path / "feature-tree"
        subprocess.run(
            ["git", "worktree", "add", str(worktree), "-b", "feature-flip"],
            cwd=opted_in_repo,
            check=True,
            capture_output=True,
        )
        _seed_session(isolated_home, "worktree-allow-timeout-fallback-session")
        stub_bin = self._stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {worktree} && git commit -m foo"),
                cwd=opted_in_repo,
                home=isolated_home,
                extra_env={"PATH": str(stub_bin)},
            )
            == "allow"
        )

    # -- Worktree-cwd bypass: chained cd to main tree from worktree session --
    # Regression: a session whose persisted cwd is a linked worktree could run
    # `cd /main-repo && git merge ...` and bypass the hook — the hook read the
    # persisted cwd (worktree shape → exit 0) before ever inspecting the
    # command. The relocation-aware fast path only skips the parser when the
    # command has no cd/-C/group token at all, so any `cd`-containing command
    # still gets threaded — the write here resolves to the main tree and is
    # denied there, not via a blanket deny of every chained cd.

    def test_worktree_cwd_chained_cd_to_main_merge_denied(self, opted_in_with_worktree):
        """Bug regression: persisted cwd = worktree, `cd /main && git merge`
        must be denied — the hook cannot tell the git op runs on the main tree."""
        opted_in_repo, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {str(opted_in_repo)} && git merge feature"),
                cwd=worktree,
            )
            == "deny"
        )

    def test_worktree_cwd_chained_cd_to_main_push_denied(self, opted_in_with_worktree):
        """Persisted cwd = worktree, `cd /main && git push` → denied."""
        opted_in_repo, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {str(opted_in_repo)} && git push origin main"),
                cwd=worktree,
            )
            == "deny"
        )

    def test_worktree_cwd_chained_cd_semicolon_denied(self, opted_in_with_worktree):
        """`;` chain operator — same bypass shape, same denial."""
        opted_in_repo, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {str(opted_in_repo)}; git merge feature"),
                cwd=worktree,
            )
            == "deny"
        )

    def test_worktree_cwd_chained_cd_or_denied(self, opted_in_with_worktree):
        """`||` chain operator — same bypass shape, same denial."""
        opted_in_repo, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {str(opted_in_repo)} || git merge feature"),
                cwd=worktree,
            )
            == "deny"
        )

    def test_worktree_cwd_chained_cd_readonly_allowed(self, isolated_home, opted_in_with_worktree):
        """Intentional flip from the old blanket cd-chain deny: a READ is
        cwd-independent regardless of where the cd resolves, so
        `cd <main> && git status` from a worktree session now allows. Only
        writes need the effective-cwd resolution that denies the sibling
        `git merge`/`git push` tests above."""
        opted_in_repo, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {str(opted_in_repo)} && git status"),
                cwd=worktree,
            )
            == "allow"
        )

    def test_worktree_cwd_plain_git_still_allowed(self, isolated_home, opted_in_with_worktree):
        """No inline cd — hook reads the worktree-persisted cwd and allows."""
        _, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git merge feature"),
                cwd=worktree,
            )
            == "allow"
        )

    def test_worktree_cwd_chain_without_cd_still_allowed(self, isolated_home, opted_in_with_worktree):
        """Non-cd chain from worktree — no inline cd, so hook reads persisted
        cwd (worktree) and allows."""
        _, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git status && git log --oneline"),
                cwd=worktree,
            )
            == "allow"
        )


class TestMachineLevelMarker:
    """Tests for the machine-level ~/.claude/worktree-required marker."""

    def test_machine_marker_enforces_on_main_tree(self, non_opted_repo, user_marker_home):
        """Machine marker active + main tree → deny."""
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_opted_repo) == "deny"

    def test_machine_marker_plus_optout_allows(self, repo_with_optout, user_marker_home):
        """Machine marker active + repo opt-out → allow."""
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=repo_with_optout) == "allow"

    def test_repo_marker_plus_optout_still_enforces(self, opted_in_repo, user_marker_home):
        """Committed repo marker + opt-out → still deny (opt-out can't defeat committed marker)."""
        (opted_in_repo / ".claude" / "worktree-optout").write_text("# opt-out\n")
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=opted_in_repo) == "deny"

    def test_neither_marker_allows(self, non_opted_repo, isolated_home):
        """No markers at all → allow."""
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_opted_repo) == "allow"

    def test_optout_alone_is_inert(self, repo_with_optout, isolated_home):
        """Opt-out present but no machine marker and no repo marker → allow."""
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=repo_with_optout) == "allow"

    def test_machine_marker_outside_git_repo_allows(self, tmp_path, user_marker_home):
        """Machine marker active + command outside any git repo → allow."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"


class TestMachineMarkerUnderConfigDir:
    """CLAUDE_CONFIG_DIR relocates the machine-level marker lookup via
    _lib_worktree_enforcement_active, which already resolves through
    _lib_config_dir."""

    def test_machine_marker_under_config_dir_enforces(self, non_opted_repo, isolated_home, tmp_path):
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / "worktree-required").write_text("# sentinel\n")
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git commit -m foo"),
                cwd=non_opted_repo,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_legacy_home_claude_marker_still_enforces_once_config_dir_set(
        self, non_opted_repo, user_marker_home, tmp_path
    ):
        """A $HOME/.claude/worktree-required marker (user_marker_home) still
        enforces even when CLAUDE_CONFIG_DIR points at a directory holding no
        copy of it — union, not swap, at this call site: a machine-wide
        sentinel armed before CLAUDE_CONFIG_DIR adoption must not silently go
        dark under a differentiated profile, matching the guard-config hooks'
        legacy-fallback fix for the same enforcement-invariant-regression
        shape."""
        empty_config_dir = tmp_path / "empty-profile"
        empty_config_dir.mkdir()
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git commit -m foo"),
                cwd=non_opted_repo,
                extra_env={"CLAUDE_CONFIG_DIR": str(empty_config_dir)},
            )
            == "deny"
        )

    def test_config_dir_marker_takes_precedence_over_legacy(
        self, non_opted_repo, user_marker_home, tmp_path
    ):
        """When the resolved config dir DOES hold its own worktree-required
        marker, that arm short-circuits — the legacy $HOME/.claude fallback
        is only consulted when the resolved config dir has no marker of its
        own, not layered on top of it."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / "worktree-required").write_text("# sentinel\n")
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git commit -m foo"),
                cwd=non_opted_repo,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )


def _lock_worktree(worktree, reason: str) -> None:
    """Fabricate a `git worktree lock` state on `worktree` with an arbitrary
    reason string, standing in for a lock a different (real or fictitious)
    session already holds — the collision-guard tests below need this state
    to exist BEFORE the hook under test ever runs."""
    subprocess.run(
        ["git", "-C", str(worktree), "worktree", "lock", str(worktree), "--reason", reason],
        check=True,
    )


class TestWorktreeCollisionGuard:
    """Coverage for _lib_worktree_collision_guard (_lib.sh), called by this
    hook at both the fast-path and slow-path linked-worktree allow points.
    See .claude/plans/worktree-collision-guard.md 'Critical files' for the
    case list this class covers.

    Two branches of the guard cannot be forced deterministically from a
    full-hook, black-box subprocess invocation: the re-read-shows-unlocked
    race (the lock must clear in the narrow window between the guard's own
    failed `lock` attempt and its diagnosis re-read) and a pruned/missing
    worktree root (the guard's own worktree-root resolution would have to
    fail right after this hook's identical-shaped resolution, moments
    earlier, already succeeded against the same path). Both require a live
    race or a git-mocking seam that isn't available here; both are covered
    directly at the function level instead, in
    test_lib_worktree_collision_guard.py, where the target state can be set
    up before the single call under test.
    """

    def test_base_case_acquires_lock_and_allows(self, isolated_home, opted_in_with_worktree):
        """First write into a virgin (never-locked) worktree acquires the
        lock and allows; the lock's reason names this session's own
        resolved pid — the value _lib_resolve_claude_pid found by walking
        up from the hook's own PPID, which for a hook run as a subprocess
        of this test is this test process itself."""
        _, worktree = opted_in_with_worktree
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=worktree) == "allow"
        reason = _worktree_lock_reason(worktree)
        assert reason is not None
        assert f"pid {os.getpid()}" in reason

    def test_self_lock_reentry_is_idempotent(self, isolated_home, opted_in_with_worktree):
        """A second write in the same session reads the lock this session
        already holds and allows without re-locking. Whether a second `git
        worktree lock` call was even attempted isn't observable without
        mocking git on PATH; an unchanged lock reason across both writes is
        the closest black-box signal that the guard took the read-only
        self-lock branch rather than releasing and re-acquiring."""
        _, worktree = opted_in_with_worktree
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=worktree) == "allow"
        reason_after_first_write = _worktree_lock_reason(worktree)
        assert reason_after_first_write is not None

        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m bar"), cwd=worktree) == "allow"
        assert _worktree_lock_reason(worktree) == reason_after_first_write

    def test_foreign_live_lock_denies_naming_pid(self, isolated_home, opted_in_with_worktree, live_pid):
        """A worktree already locked by a different live process denies,
        naming that process's pid. live_pid (a real process this fixture
        owns and terminates on teardown) stands in for "some other live
        session" — distinct from os.getpid(), which opted_in_with_worktree
        already registered as this session's own identity, so it can't
        double as the foreign pid without the guard reading it back as a
        self-lock instead."""
        _, worktree = opted_in_with_worktree
        foreign_pid = live_pid
        _lock_worktree(worktree, f"claude-code pid {foreign_pid}")

        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=worktree)
        assert reason is not None
        assert str(foreign_pid) in reason
        assert "live" in reason

    def test_foreign_dead_lock_denies_with_manual_remedy(self, isolated_home, opted_in_with_worktree):
        """A lock naming a pid that is no longer running denies, naming that
        pid and pointing at the manual `git worktree unlock` remedy — and
        the worktree is still locked afterward, confirming the hook itself
        never ran that unlock (no auto-eviction)."""
        _, worktree = opted_in_with_worktree
        dead_pid = _dead_pid()
        _lock_worktree(worktree, f"claude-code pid {dead_pid}")

        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=worktree)
        assert reason is not None
        assert str(dead_pid) in reason
        assert "no longer running" in reason
        assert "git worktree unlock" in reason
        assert _worktree_lock_reason(worktree) is not None, (
            "hook must not auto-evict a dead-pid lock"
        )

    def test_unparseable_reason_lock_denies_with_manual_remedy(self, isolated_home, opted_in_with_worktree):
        """A lock reason with no parseable pid (e.g. a human ran `git
        worktree lock` by hand for an unrelated reason) denies with the same
        manual-unlock remedy, since there is no pid to diagnose."""
        _, worktree = opted_in_with_worktree
        _lock_worktree(worktree, "reviewing")

        reason = run_hook_reason(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=worktree)
        assert reason is not None
        assert "git worktree unlock" in reason
        assert _worktree_lock_reason(worktree) is not None, (
            "hook must not auto-evict an unparseable-reason lock"
        )

    def test_foreign_live_lock_still_allows_read_via_fast_path(
        self, isolated_home, opted_in_with_worktree, live_pid, tmp_path
    ):
        """A worktree collision-locked by a different live session must not
        block a read-only command on the fast path -- the guard exists to
        gate writes, not reads. A `git` wrapper counts `worktree list
        --porcelain` calls, proving the guard was actually invoked and fell
        through to the parser's read exemption rather than being bypassed
        entirely -- a bare allow assertion on hook stdout can't distinguish
        that from the rejected simpler design (drop the guard from the
        fast path altogether), which would produce the same allow verdict
        with zero guard invocations."""
        _, worktree = opted_in_with_worktree
        foreign_pid = live_pid
        _lock_worktree(worktree, f"claude-code pid {foreign_pid}")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        counter_file = tmp_path / "_porcelain_read_count"
        counter_file.write_text("0")
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{worktree}" ] && [ "$3" = "worktree" ] && [ "$4" = "list" ] && [ "$5" = "--porcelain" ]; then
  count=$(cat "{counter_file}")
  count=$((count + 1))
  printf '%s' "$count" > "{counter_file}"
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = run_hook(
            WORKTREE_HOOK, bash_input("git status"), cwd=worktree, home=isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result == "allow"
        assert int(counter_file.read_text()) == 2, (
            "the guard makes exactly two `worktree list --porcelain` calls "
            "for this scenario (self-lock check, then the post-failed-lock "
            "diagnosis re-read -- see test_reread_shows_unlocked_still_denies "
            "in test_lib_worktree_collision_guard.py for the same count "
            "pinned at the function level); a different count means either "
            "the guard was bypassed (0) or invoked extra times by a "
            "regression in the fallthrough"
        )

    def test_foreign_dead_lock_still_allows_read_via_fast_path(self, isolated_home, opted_in_with_worktree):
        """Same as above for a dead-pid foreign lock -- a read must be
        allowed regardless of which deny branch the guard would have taken
        for a write."""
        _, worktree = opted_in_with_worktree
        dead_pid = _dead_pid()
        _lock_worktree(worktree, f"claude-code pid {dead_pid}")

        assert run_hook(WORKTREE_HOOK, bash_input("git status"), cwd=worktree) == "allow"

    def test_read_in_freshly_unlocked_worktree_still_acquires_lock(self, isolated_home, opted_in_with_worktree):
        """Pre-existing, unchanged-by-this-diff side effect made explicit:
        the fast path calls the collision guard unconditionally, even for a
        read, so a read against a virgin (never-locked) worktree still
        claims the exclusive `git worktree lock` as a byproduct of the
        guard diagnosing "unlocked" and acquiring it -- the guard has no
        way to know in advance that the caller only intends a read. The
        "reads are always allowed" invariant this fix restores is about the
        allow/deny decision, not about the guard's own lock-acquisition
        side effect, which this diff does not change."""
        _, worktree = opted_in_with_worktree
        assert _worktree_lock_reason(worktree) is None, "fixture worktree must start unlocked"

        assert run_hook(WORKTREE_HOOK, bash_input("git status"), cwd=worktree) == "allow"

        reason = _worktree_lock_reason(worktree)
        assert reason is not None, "the guard's own unconditional call is expected to lock the worktree"
        assert f"pid {os.getpid()}" in reason
