"""Tests for deny-repo-relocation.sh.

REPO_ROOT below is what the hook itself computes at runtime (readlink -f on
its own physical path, minus the /claude/.claude/hooks/deny-repo-relocation.sh
suffix) — this worktree's own root, since the hook file lives inside it and
these tests run it directly (unsymlinked), exactly like
test_hook_alignment.py's missing-_lib.sh case does. No command below is
actually executed; the hook only pattern-matches command TEXT, so exercising
it against this repo's own real root is safe.
"""
from __future__ import annotations

from pathlib import Path

from helpers import HOOKS_DIR, bash_input, run_hook, run_hook_reason

HOOK = HOOKS_DIR / "deny-repo-relocation.sh"
REPO_ROOT = Path(__file__).resolve().parents[4]


class TestMvSourceIsRepoRoot:
    def test_mv_repo_root_denied(self):
        assert run_hook(HOOK, bash_input(f"mv {REPO_ROOT} /tmp/elsewhere")) == "deny"

    def test_mv_ancestor_of_repo_root_denied(self):
        assert run_hook(HOOK, bash_input(f"mv {REPO_ROOT.parent} /tmp/elsewhere")) == "deny"

    def test_deny_reason_names_relocate_claude_config(self):
        reason = run_hook_reason(HOOK, bash_input(f"mv {REPO_ROOT} /tmp/elsewhere"))
        assert reason is not None
        assert "relocate-claude-config" in reason


class TestMvAllowedCases:
    def test_mv_unrelated_dir_allowed(self):
        assert run_hook(HOOK, bash_input("mv /tmp/some-unrelated-dir /tmp/elsewhere")) == "allow"

    def test_mv_repo_subdirectory_rename_allowed(self):
        """Renaming a subdirectory of the repo (not the repo root itself)
        does not carry the whole checkout with it."""
        src = REPO_ROOT / "docs"
        dst = REPO_ROOT / "documentation"
        assert run_hook(HOOK, bash_input(f"mv {src} {dst}")) == "allow"

    def test_non_mv_rsync_command_allowed(self):
        assert run_hook(HOOK, bash_input("ls -la")) == "allow"

    def test_empty_bash_command_allowed(self):
        assert run_hook(HOOK, bash_input("")) == "allow"


class TestRsyncRemoveSourceFiles:
    def test_rsync_remove_source_files_repo_root_denied(self):
        cmd = f"rsync -a --remove-source-files {REPO_ROOT}/ /tmp/elsewhere/"
        assert run_hook(HOOK, bash_input(cmd)) == "deny"

    def test_plain_rsync_without_remove_source_files_allowed(self):
        """A copy (no source removal) is not a relocation."""
        cmd = f"rsync -a {REPO_ROOT}/ /tmp/elsewhere/"
        assert run_hook(HOOK, bash_input(cmd)) == "allow"


class TestKnownGapBypass:
    """row1c: source-argument resolution cannot see through shell variables
    or thread cwd across a preceding `cd` in the same command — both fail
    OPEN (allow) by design, not deny. Pinned here so a future change to
    either behavior is visible, not silent."""

    def test_variable_indirected_source_allowed(self):
        cmd = f'REPO={REPO_ROOT}; mv "$REPO" /tmp/elsewhere'
        assert run_hook(HOOK, bash_input(cmd)) == "allow"

    def test_cd_prefixed_relative_path_move_allowed(self):
        cmd = f"cd {REPO_ROOT.parent} && mv {REPO_ROOT.name} /tmp/elsewhere"
        assert run_hook(HOOK, bash_input(cmd)) == "allow"


class TestChainOperators:
    def test_relocation_in_second_fragment_denied(self):
        cmd = f"ls -la && mv {REPO_ROOT} /tmp/elsewhere"
        assert run_hook(HOOK, bash_input(cmd)) == "deny"

    def test_unrelated_pipe_allowed(self):
        assert run_hook(HOOK, bash_input("ls -la | grep mv")) == "allow"


class TestMultipleSources:
    def test_mv_multiple_sources_last_one_is_destination(self):
        """mv src1 src2 ... dir/ treats every argument but the last as a
        source; a benign leading source alongside the repo root as a later
        source must still deny."""
        cmd = f"mv /tmp/unrelated {REPO_ROOT} /tmp/dest-dir/"
        assert run_hook(HOOK, bash_input(cmd)) == "deny"

    def test_mv_repo_root_as_sole_destination_allowed(self):
        """The repo root appearing only as the FINAL (destination)
        argument must not be judged as a source."""
        cmd = f"mv /tmp/unrelated-source {REPO_ROOT}"
        assert run_hook(HOOK, bash_input(cmd)) == "allow"
