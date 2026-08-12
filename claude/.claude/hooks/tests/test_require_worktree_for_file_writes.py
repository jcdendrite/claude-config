"""Tests for require-worktree-for-file-writes.sh."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from conftest import _dead_pid, _worktree_lock_reason
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    multiedit_input,
    run_hook,
    run_hook_reason,
    write_input,
)

FILE_WRITES_HOOK = HOOKS_DIR / "require-worktree-for-file-writes.sh"


class TestRequireWorktreeForFileWrites:
    def test_no_sentinel_allows_edit(self, non_opted_repo, isolated_home):
        """Repo without the sentinel: Edit passes through unconditionally."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_no_sentinel_allows_write(self, non_opted_repo, isolated_home):
        """Repo without the sentinel: Write passes through unconditionally."""
        path = str(non_opted_repo / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_opted_in_main_tree_denies_edit(self, opted_in_repo):
        """Edit targeting an existing file in the main tree is denied."""
        path = str(opted_in_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "deny"

    def test_opted_in_main_tree_denies_write(self, opted_in_repo):
        """Write targeting the main tree is denied even for a new file."""
        path = str(opted_in_repo / "newfile.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_opted_in_main_tree_denies_multiedit(self, opted_in_repo):
        """MultiEdit targeting the main tree is denied."""
        path = str(opted_in_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, multiedit_input(path)) == "deny"

    def test_stray_untracked_marker_still_denies(self, stray_marker_repo):
        """GH-427: an untracked .claude/worktree-required still activates
        enforcement — this test locks that existence-based behavior in place;
        the fix for GH-427 is a deny-message hint, not a logic change."""
        path = str(stray_marker_repo / "f.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "deny"

    def test_stray_untracked_marker_deny_includes_hint(self, stray_marker_repo):
        path = str(stray_marker_repo / "f.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "untracked" in reason
        # The unique hint phrase, not a bare path substring — the boilerplate
        # deny text already mentions .claude/worktree-required twice regardless
        # of whether the hint fired, so that alone wouldn't prove the hint ran.
        assert "present but untracked" in reason

    def test_committed_marker_deny_omits_stray_hint(self, opted_in_repo):
        """The hint is GH-427-specific noise for the normal opted-in case —
        a committed marker must not trigger it."""
        path = str(opted_in_repo / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "untracked" not in reason

    def test_staged_not_committed_marker_deny_omits_stray_hint(self, staged_marker_repo):
        """The hint's actual gate is index-tracked (git ls-files --error-unmatch),
        not HEAD-committed — a staged-but-uncommitted marker already satisfies it."""
        path = str(staged_marker_repo / "f.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "untracked" not in reason

    def test_machine_marker_only_deny_omits_stray_hint(self, non_opted_repo, user_marker_home):
        """Enforcement active via the machine-level marker alone (no repo-root
        .claude/worktree-required at all) must not surface the untracked-repo-
        sentinel hint — the hint's first check ([ -f repo-root marker ]) should
        short-circuit before ever reaching the git ls-files branch."""
        path = str(non_opted_repo / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "untracked" not in reason

    def test_opted_in_worktree_allows_edit(self, isolated_home, opted_in_with_worktree):
        """Edit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_opted_in_worktree_allows_write(self, isolated_home, opted_in_with_worktree):
        """Write targeting a new file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_opted_in_worktree_allows_multiedit(self, isolated_home, opted_in_with_worktree):
        """MultiEdit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, multiedit_input(path)) == "allow"

    def test_non_git_path_allows_edit(self, tmp_path, isolated_home):
        """Edit to a path outside any git repo is allowed."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        path = str(non_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_new_file_nested_path_denied_in_main_tree(self, opted_in_repo):
        """Write to a not-yet-existing nested path whose ancestor is in the
        main tree is denied — the hook must walk up to the existing dir."""
        path = str(opted_in_repo / "subdir" / "deeply" / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_bash_tool_allowed(self, isolated_home, opted_in_repo):
        """Non-file-write tool (Bash) passes through: the hook is scoped to
        Edit/Write/MultiEdit only."""
        assert run_hook(FILE_WRITES_HOOK, bash_input("echo hi")) == "allow"

    def test_deny_message_names_relative_path(self, opted_in_repo):
        """Deny message should include the relative worktree path hint."""
        path = str(opted_in_repo / "src" / "main.sh")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "src/main.sh" in reason

    def test_deny_message_names_tool(self, opted_in_repo):
        """Deny message should name the tool that was blocked."""
        path = str(opted_in_repo / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, write_input(path))
        assert reason is not None
        assert "Write" in reason

    def test_malformed_json_stdin_denies(self):
        """Malformed JSON input must produce a deny, not a silent allow."""
        result = subprocess.run(
            [str(FILE_WRITES_HOOK)],
            input="not-json{{{",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip(), "Expected deny output on malformed JSON, got silent allow"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestRequireWorktreeForFileWritesHomeExemption:
    """The hook must not block writes to ~/.claude/ even when $HOME resolves
    into an opted-in repo via stow directory-folding."""

    @pytest.fixture
    def opted_in_home(self, tmp_path, monkeypatch):
        """Sandboxed $HOME that is itself a git repo with worktree-required
        committed — reproduces the stow directory-fold scenario where
        ~/.claude/ resolves into an opted-in claude-config checkout."""
        home = tmp_path / "home"
        home.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=home, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=home, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=home, check=True)
        (home / ".claude").mkdir()
        (home / ".claude" / "worktree-required").write_text("# sentinel\n")
        (home / ".claude" / "plans").mkdir()
        (home / ".claude" / "plans" / "existing.md").write_text("plan content\n")
        subprocess.run(["git", "add", "."], cwd=home, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=home, check=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        return home

    def test_home_claude_existing_file_allowed(self, opted_in_home):
        """Write to an existing ~/.claude/ file is allowed despite the repo
        being opted into worktree discipline."""
        path = str(opted_in_home / ".claude" / "plans" / "existing.md")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_home_claude_new_file_allowed(self, opted_in_home):
        """Write to a not-yet-existing ~/.claude/ file is allowed. The
        original failure manifested on new-file writes where the hook's
        dirname-walk ascended out of the missing path into the repo root."""
        path = str(opted_in_home / ".claude" / "plans" / "new.md")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_non_claude_path_in_opted_in_home_denied(self, opted_in_home):
        """A write to a non-.claude path inside the same opted-in home repo
        is still denied — the exemption is scoped to ~/.claude/, not the
        entire $HOME repo."""
        path = str(opted_in_home / "some-project-file.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_adjacent_prefix_not_exempt(self, opted_in_home):
        """~/.claude-foo/ must not match the ~/.claude/ exemption — the
        case glob has a literal '/' after '.claude', so .claude-foo cannot
        satisfy it."""
        (opted_in_home / ".claude-foo").mkdir()
        (opted_in_home / ".claude-foo" / "file.md").write_text("")
        path = str(opted_in_home / ".claude-foo" / "file.md")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_exact_dotclaude_dir_not_exempt(self, opted_in_home):
        """A write path of exactly $HOME/.claude (no trailing segment) does
        not satisfy the '/.claude/*' glob and falls through to repo-walk
        denial."""
        path = str(opted_in_home / ".claude")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_dot_dot_traversal_not_exempt(self, opted_in_home):
        """A file_path using '..' traversal through .claude/ must not be
        exempted. The case glob matches on the raw string, so
        $HOME/.claude/../other-file would satisfy the prefix without
        actually resolving inside $HOME/.claude/. The traversal guard
        rejects any path containing '/..' before the prefix check."""
        path = str(opted_in_home / ".claude" / ".." / "project-file.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_stow_symlinked_claude_dir_allows(self, opted_in_home, tmp_path):
        """When ~/.claude/ is a stow-managed symlink to a repo's .claude/
        directory (directory-fold), writes through the symlink path must
        still be allowed. realpath would resolve the path into the repo root,
        breaking the fix — the hook intentionally uses the raw string."""
        # Simulate stow directory-fold: ~/.claude is a symlink to another
        # opted-in repo's .claude/ dir.
        target_repo = tmp_path / "target-repo"
        target_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=target_repo, check=True)
        dot_claude = target_repo / ".claude"
        dot_claude.mkdir()
        (dot_claude / "worktree-required").write_text("# sentinel\n")
        (dot_claude / "settings.json").write_text("{}\n")
        subprocess.run(["git", "add", "."], cwd=target_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=target_repo, check=True)
        # Replace ~/.claude dir with a symlink to target_repo/.claude/
        (opted_in_home / ".claude").rename(opted_in_home / ".claude-orig")
        (opted_in_home / ".claude").symlink_to(dot_claude)
        # Write to ~/.claude/settings.json via the raw symlink path
        path = str(opted_in_home / ".claude" / "settings.json")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"


class TestRequireWorktreeForFileWritesConfigDirExemption:
    """CLAUDE_CONFIG_DIR relocates the harness-infrastructure exemption to
    the resolved config dir — a union with the literal $HOME/.claude arm
    above, not a swap (ledger row 5)."""

    def test_config_dir_outside_home_claude_exempted(self, opted_in_repo, isolated_home, monkeypatch):
        """A resolved config dir nested inside an opted-in main tree but
        outside $HOME/.claude is exempt via the new union arm — without it
        this write would deny like any other main-tree write (see
        test_opted_in_main_tree_denies_write)."""
        config_dir = opted_in_repo / "profile-config"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        path = str(config_dir / "plans" / "x.md")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_non_config_dir_path_in_same_repo_still_denied(self, opted_in_repo, isolated_home, monkeypatch):
        """The config-dir exemption is scoped to the resolved config dir
        prefix, not the whole opted-in repo it happens to sit inside — a
        sibling path outside that prefix still denies."""
        config_dir = opted_in_repo / "profile-config"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        path = str(opted_in_repo / "some-project-file.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"


class TestMachineLevelMarker:
    """Tests for the machine-level ~/.claude/worktree-required marker."""

    def test_machine_marker_enforces_on_main_tree(self, non_opted_repo, user_marker_home):
        """Machine marker active + main tree → deny."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "deny"

    def test_machine_marker_plus_optout_allows(self, repo_with_optout, user_marker_home):
        """Machine marker active + repo opt-out → allow."""
        path = str(repo_with_optout / "f.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_repo_marker_plus_optout_still_enforces(self, opted_in_repo, user_marker_home):
        """Committed repo marker + opt-out → still deny (opt-out can't defeat committed marker)."""
        (opted_in_repo / ".claude" / "worktree-optout").write_text("# opt-out\n")
        path = str(opted_in_repo / "f.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_neither_marker_allows(self, non_opted_repo, isolated_home):
        """No markers at all → allow."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_optout_alone_is_inert(self, repo_with_optout, isolated_home):
        """Opt-out present but no machine marker and no repo marker → allow."""
        path = str(repo_with_optout / "f.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_machine_marker_outside_git_repo_allows(self, tmp_path, user_marker_home):
        """Machine marker active + path outside any git repo → allow."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        path = str(non_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_machine_marker_home_path_still_exempt(self, non_opted_repo, user_marker_home):
        """Machine marker active but write targets ~/.claude/foo → allow (HOME exemption holds)."""
        # user_marker_home is the sandboxed HOME; write to something under it
        path = str(user_marker_home / ".claude" / "some-file.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"


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
    hook at its 'already in a linked worktree' allow point. See
    .claude/plans/worktree-collision-guard.md 'Critical files' for the case
    list this class covers.

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
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"
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
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"
        reason_after_first_write = _worktree_lock_reason(worktree)
        assert reason_after_first_write is not None

        assert run_hook(FILE_WRITES_HOOK, write_input(str(worktree / "other.txt"))) == "allow"
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

        path = str(worktree / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
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

        path = str(worktree / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
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

        path = str(worktree / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "git worktree unlock" in reason
        assert _worktree_lock_reason(worktree) is not None, (
            "hook must not auto-evict an unparseable-reason lock"
        )
