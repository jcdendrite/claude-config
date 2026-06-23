"""Tests for require-worktree-for-git-writes.sh."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
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

    # The cwd-anchor note is appended to the deny reason ONLY when the
    # command shows a `cd ... && git ...` (or `;` / `||`) pattern. This
    # is the precise failure mode where the agent expected its inline cd
    # to put it in a worktree, but the hook reads cwd from the JSON
    # tool_input — Claude Code's persisted bash cwd from prior calls,
    # not the cwd the inline cd would produce after this hook returns.
    # Tests cover three positive cases (each chain operator) and a
    # negative case (no chained cd → no note appended).

    def test_chained_cd_amp_git_appends_anchor_note(self, opted_in_repo):
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp && git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" in reason
        assert "Anchor cwd" in reason

    def test_chained_cd_semicolon_git_appends_anchor_note(self, opted_in_repo):
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp; git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" in reason

    def test_chained_cd_or_git_appends_anchor_note(self, opted_in_repo):
        """`||` chain (run-if-fail) is unusual but parses the same way —
        cwd note still appended so the agent gets the hint."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp || git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" in reason

    def test_plain_git_no_anchor_note(self, opted_in_repo):
        """No chained cd → cwd note not appended; deny message stays
        short for the common case."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" not in reason
        assert "Anchor cwd" not in reason

    def test_cd_after_git_no_anchor_note(self, opted_in_repo):
        """`git ... && cd ...` is the reverse of the trigger pattern —
        the cd is AFTER the git, not before. The note targets the
        chained-cd-before-git mistake, so this case must NOT match."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo && cd /tmp"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" not in reason

    # Tests for git_C_note_if_present: the corrective note appended when
    # the agent used `git -C <path> <write-op>` from the main tree and
    # expected the -C path to be treated as the working directory.
    # Assertion phrases:
    #   chained-cd note (existing) → unique substring: "chained 'cd"
    #   -C note (new)              → unique substring: "-C path"

    def test_git_dash_C_write_appends_C_note(self, opted_in_repo):
        """`git -C /tmp commit` → denied; -C note appended."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git -C /tmp commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "-C path" in reason

    def test_git_dash_C_readonly_no_C_note(self, isolated_home, opted_in_repo):
        """`git -C /tmp log` is read-only → allowed; no deny reason."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -C /tmp log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_plain_git_no_C_note(self, opted_in_repo):
        """Plain `git commit` without -C → denied; -C note NOT appended."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "-C path" not in reason

    def test_subcommand_dash_C_no_C_note(self, opted_in_repo):
        """`git commit -C HEAD` uses -C as commit's reuse-message flag,
        not as the global working-dir flag. The note must NOT fire —
        the hint about working directories doesn't apply here."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -C HEAD"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "-C path" not in reason

    def test_chained_cd_and_git_C_only_cd_chain_deny(self, opted_in_repo):
        """Command with both cd chain and -C: the cd-chain deny fires before
        the fragment loop, so the -C note is never appended."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp && git -C /tmp commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "chains 'cd" in reason    # cd-chain deny fires first
        assert "-C path" not in reason   # -C note never reached

    # -- Worktree-cwd bypass: chained cd to main tree from worktree session --
    # Regression: a session whose persisted cwd is a linked worktree could run
    # `cd /main-repo && git merge ...` and bypass the hook — the hook read the
    # persisted cwd (worktree shape → exit 0) before ever inspecting the
    # fragments. The fix denies all chained `cd ... && git ...` commands
    # regardless of the persisted cwd, since the effective cwd at the time
    # git runs cannot be determined from the tool-input JSON alone.

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

    def test_worktree_cwd_chained_cd_readonly_also_denied(self, opted_in_with_worktree):
        """Chained cd+git is denied regardless of subcommand — the hook
        cannot determine which cwd the git op runs against, so it refuses
        to evaluate rather than guess."""
        opted_in_repo, worktree = opted_in_with_worktree
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input(f"cd {str(opted_in_repo)} && git status"),
                cwd=worktree,
            )
            == "deny"
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
