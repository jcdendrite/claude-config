"""Tests for worktree-removal-status.sh.

No `gh` dependency -- the script's only external dependency is git, run
against local bare-remote fixtures built by conftest.py's helpers, plus
`collect_process_cwds`/`worktree_in_use` from _worktree-lib.sh for the
live-process case (real OS-level detection, no shim needed -- see
test_cleanup_merged_branches.py's TestWorktreeInUseGuard for the same
pattern: a real `sleep` process cwd'd into the target directory).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

from .conftest import (
    _dead_pid,
    _make_feature_branch,
    _make_repo_with_remote,
    _make_worktree,
)

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "worktree-removal-status.sh"

# A PATH-shimmed readlink/lsof that always fails -- same source
# test_worktree_lib.py's readlink_always_fails_path/lsof_always_fails_path
# fixtures use, reused inline here (not imported) since those fixtures are
# scoped to that file's own module.
_ALWAYS_FAILS_SOURCE = textwrap.dedent("""\
    #!/usr/bin/env bash
    exit 1
""")


def _run_script(
    repo: Path, args: list[str] | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    cmd = [str(_SCRIPT)] + (args or [])
    return subprocess.run(
        cmd,
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestCleanWorktreeIsRemovable:
    def test_clean_worktree_reports_removable(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/clean")
        wt_path = tmp_path / "clean-tree"
        _make_worktree(local, "feat/clean", wt_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert f"Worktree: {wt_path}" in result.stdout
        assert "Status: clean" in result.stdout
        assert "Verdict: git worktree remove would succeed (no --force needed)." in result.stdout
        assert "1 worktrees: 1 removable, 0 dirty, 0 locked, 0 prunable, 0 in-use, 0 unverifiable" in result.stdout


class TestDirtyTrackedFileNeedsForce:
    def test_tracked_file_modification_reports_dirty_and_needs_force(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dirty-tracked")
        wt_path = tmp_path / "dirty-tracked-tree"
        _make_worktree(local, "feat/dirty-tracked", wt_path)
        (wt_path / "file.txt").write_text("modified\n")

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Status: dirty" in result.stdout
        assert "M file.txt" in result.stdout or " M file.txt" in result.stdout
        assert "Verdict: git worktree remove would fail without --force (dirty working tree)." in result.stdout


class TestUntrackedFileOnlyNeedsForce:
    def test_untracked_file_reports_dirty_and_needs_force(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/untracked")
        wt_path = tmp_path / "untracked-tree"
        _make_worktree(local, "feat/untracked", wt_path)
        (wt_path / "scratch.txt").write_text("wip\n")

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Status: dirty" in result.stdout
        assert "?? scratch.txt" in result.stdout
        assert "Verdict: git worktree remove would fail without --force (dirty working tree)." in result.stdout


class TestLockedWorktreeShowsFullReasonText:
    def test_locked_worktree_needs_force_and_shows_full_reason(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked")
        wt_path = tmp_path / "locked-tree"
        _make_worktree(local, "feat/locked", wt_path)
        dead = _dead_pid()
        reason = f"held for review (pid {dead})"
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", reason],
            cwd=local, check=True,
        )

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert f"Locked: yes ({reason})" in result.stdout
        assert "Verdict: git worktree remove would fail without --force specified twice (locked)." in result.stdout


class TestLockedWorktreeWithNoReasonShowsBareLocked:
    """`git worktree lock` with no --reason produces a bare `locked` porcelain
    line -- Locked: yes with no parenthetical, and the verdict still cites
    `locked` as the reason."""

    def test_lock_with_no_reason_shows_bare_locked_and_needs_force_twice(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-no-reason")
        wt_path = tmp_path / "locked-no-reason-tree"
        _make_worktree(local, "feat/locked-no-reason", wt_path)
        subprocess.run(["git", "worktree", "lock", str(wt_path)], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Locked: yes\n" in result.stdout
        assert "Locked: yes (" not in result.stdout
        assert "Verdict: git worktree remove would fail without --force specified twice (locked)." in result.stdout


class TestCombinedDirtyLockedVerdictListsBothReasons:
    """A worktree that is simultaneously dirty and locked lists both reasons,
    comma-joined in the order the script produces them (dirty before
    locked), and the force clause reflects the stricter locked requirement
    (--force specified twice, not once)."""

    def test_dirty_and_locked_worktree_lists_both_reasons(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dirty-locked")
        wt_path = tmp_path / "dirty-locked-tree"
        _make_worktree(local, "feat/dirty-locked", wt_path)
        (wt_path / "scratch.txt").write_text("wip\n")
        subprocess.run(["git", "worktree", "lock", str(wt_path)], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Status: dirty" in result.stdout
        assert "Locked: yes" in result.stdout
        assert (
            "Verdict: git worktree remove would fail without --force specified twice "
            "(dirty working tree, locked)."
        ) in result.stdout


class TestSubmoduleWorktreeNeedsForce:
    """A clean, unlocked worktree with an initialized submodule still needs
    --force -- `git worktree remove` refuses on submodules too, a third
    reason beyond dirty/locked."""

    def test_initialized_submodule_reports_and_needs_force(self, tmp_path):
        submodule_dir = tmp_path / "submodule-src"
        submodule_dir.mkdir()
        submodule_source, _ = _make_repo_with_remote(submodule_dir)

        outer_dir = tmp_path / "outer"
        outer_dir.mkdir()
        local, _ = _make_repo_with_remote(outer_dir)
        _make_feature_branch(local, "feat/submodule")
        wt_path = tmp_path / "submodule-tree"
        _make_worktree(local, "feat/submodule", wt_path)
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add",
             str(submodule_source), "sub"],
            cwd=wt_path, check=True, capture_output=True,
        )
        # Commit the submodule addition so the worktree is clean again --
        # isolating "has submodules" as the sole reason force is needed.
        subprocess.run(["git", "commit", "-q", "-m", "add submodule"], cwd=wt_path, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Status: clean" in result.stdout
        assert "Submodules: yes" in result.stdout
        assert "Verdict: git worktree remove would fail without --force (has submodules)." in result.stdout


class TestDetachedHeadWorktreeReportsBranchLabel:
    """A worktree checked out with --detach (no branch) reports
    `Branch: (detached)`, and is otherwise unaffected: a clean detached
    worktree still reports removable."""

    def test_detached_worktree_shows_detached_branch_and_is_removable(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=local, capture_output=True, text=True, check=True,
        ).stdout.strip()
        wt_path = tmp_path / "detached-tree"
        subprocess.run(["git", "worktree", "add", "--detach", str(wt_path), head_sha], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Branch: (detached)" in result.stdout
        assert "Verdict: git worktree remove would succeed (no --force needed)." in result.stdout
        assert "1 worktrees: 1 removable, 0 dirty, 0 locked, 0 prunable, 0 in-use, 0 unverifiable" in result.stdout


class TestPrunableWorktreeReportsPruneRemedy:
    """A worktree whose directory was deleted out from under git (not via
    `git worktree remove`) is prunable, not force-removable -- the remedy
    is `git worktree prune`."""

    def test_deleted_worktree_directory_reports_prunable(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/prunable")
        wt_path = tmp_path / "prunable-tree"
        _make_worktree(local, "feat/prunable", wt_path)
        shutil.rmtree(wt_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Prunable: yes" in result.stdout
        assert "Verdict: prunable -- run 'git worktree prune', not --force." in result.stdout
        assert "1 worktrees: 0 removable, 0 dirty, 0 locked, 1 prunable, 0 in-use, 0 unverifiable" in result.stdout


class TestLiveProcessReportsInUse:
    """A worktree a live process is cwd'd into is reported in-use, and its
    verdict never claims it is safe to force-remove."""

    def test_worktree_with_live_process_reports_in_use(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use")
        wt_path = tmp_path / "in-use-tree"
        _make_worktree(local, "feat/in-use", wt_path)

        holder = subprocess.Popen(["sleep", "30"], cwd=str(wt_path))
        try:
            result = _run_script(local)
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0, result.stderr
        assert "In use: yes (live process)" in result.stdout
        assert "Verdict: do not remove -- a live process is working in this worktree" in result.stdout
        assert "1 worktrees: 0 removable, 0 dirty, 0 locked, 0 prunable, 1 in-use, 0 unverifiable" in result.stdout


class TestUnverifiableInUseCheckReportsCouldNotBeDetermined:
    """When neither /proc (readlink shimmed to fail) nor lsof (shimmed to
    fail) can report process cwds -- the same dual-probe-unavailable
    technique test_worktree_lib.py's TestCollectProcessCwdsBothProbesUnavailable
    uses on worktree_in_use directly -- the full script must surface this as
    its own verdict and tally, not silently report idle."""

    def test_both_probes_unavailable_reports_could_not_be_determined(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/unverifiable")
        wt_path = tmp_path / "unverifiable-tree"
        _make_worktree(local, "feat/unverifiable", wt_path)

        shim_dir = tmp_path / "always-fails-shim"
        shim_dir.mkdir()
        for tool in ("readlink", "lsof"):
            shim = shim_dir / tool
            shim.write_text(_ALWAYS_FAILS_SOURCE)
            shim.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}

        result = _run_script(local, env=env)

        assert result.returncode == 0, result.stderr
        assert "In use: could not be determined" in result.stdout
        assert "Verdict: could not be determined; inspect this worktree manually." in result.stdout
        assert "1 worktrees: 0 removable, 0 dirty, 0 locked, 0 prunable, 0 in-use, 1 unverifiable" in result.stdout


class TestFilterByBranchName:
    def test_branch_name_filter_narrows_to_matching_worktree(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/one")
        _make_worktree(local, "feat/one", tmp_path / "one-tree")
        _make_feature_branch(local, "feat/two")
        _make_worktree(local, "feat/two", tmp_path / "two-tree")

        result = _run_script(local, args=["feat/one"])

        assert result.returncode == 0, result.stderr
        assert "Branch: feat/one" in result.stdout
        assert "Branch: feat/two" not in result.stdout
        assert "1 worktrees:" in result.stdout


class TestFilterByPath:
    def test_path_filter_narrows_to_matching_worktree(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/one")
        wt_path = tmp_path / "one-tree"
        _make_worktree(local, "feat/one", wt_path)
        _make_feature_branch(local, "feat/two")
        _make_worktree(local, "feat/two", tmp_path / "two-tree")

        result = _run_script(local, args=[str(wt_path)])

        assert result.returncode == 0, result.stderr
        assert f"Worktree: {wt_path}" in result.stdout
        assert "Branch: feat/two" not in result.stdout
        assert "1 worktrees:" in result.stdout


class TestFilterByRelativePathCanonicalizes:
    """A relative-path filter argument is canonicalized before comparison,
    so it still matches the worktree's canonical porcelain path -- the
    canonicalize-both-sides fix, not just an absolute-path coincidence."""

    def test_relative_path_filter_matches_after_canonicalization(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/relative")
        wt_path = tmp_path / "relative-tree"
        _make_worktree(local, "feat/relative", wt_path)

        result = _run_script(local, args=[os.path.relpath(wt_path, local)])

        assert result.returncode == 0, result.stderr
        assert f"Worktree: {wt_path}" in result.stdout
        assert "1 worktrees:" in result.stdout


class TestUnmatchedFilterArgReported:
    def test_unmatched_filter_arg_produces_no_worktree_found_line(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/real")
        _make_worktree(local, "feat/real", tmp_path / "real-tree")

        result = _run_script(local, args=["feat/does-not-exist"])

        assert result.returncode == 0, result.stderr
        assert "No worktree found for: feat/does-not-exist" in result.stdout
        assert "0 worktrees:" in result.stdout


class TestZeroLinkedWorktrees:
    def test_no_linked_worktrees_prints_single_message(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "No linked worktrees found."


class TestCurrentWorktreeIsTagged:
    def test_worktree_invoked_from_is_tagged_current(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/self")
        wt_path = tmp_path / "self-tree"
        _make_worktree(local, "feat/self", wt_path)

        result = _run_script(wt_path)

        assert result.returncode == 0, result.stderr
        assert f"Worktree: {wt_path} (current)" in result.stdout


class TestMainWorktreeExcluded:
    def test_main_worktree_never_appears_in_report(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/linked")
        _make_worktree(local, "feat/linked", tmp_path / "linked-tree")

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert f"Worktree: {local}" not in result.stdout


class TestNotAGitRepository:
    def test_exits_nonzero_with_message(self, tmp_path):
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        result = _run_script(not_a_repo)

        assert result.returncode == 1
        assert "not inside a git repository" in result.stderr
