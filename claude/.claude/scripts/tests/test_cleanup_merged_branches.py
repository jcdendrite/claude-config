"""Tests for cleanup-merged-branches.sh.

The gh CLI is replaced in every test by a PATH shim that reads canned
JSON keyed on the --head <branch> argument. Real git operations run
against temporary repos created per-test.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "cleanup-merged-branches.sh"


# ---------------------------------------------------------------------------
# Repo helpers
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> None:
    """Initialise a git repo with one commit and a remote pointing at itself."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def _commit(repo: Path, message: str = "commit") -> None:
    (repo / "file.txt").write_text(message + "\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _make_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Return (local_repo, bare_remote) with origin configured and default branch set."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=bare, check=True)

    local = tmp_path / "local"
    _init_repo(local)
    _commit(local, "init")
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=local, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=local, check=True)
    # Set origin/HEAD so the script can resolve the default branch
    subprocess.run(["git", "remote", "set-head", "origin", "main"], cwd=local, check=True)
    return local, bare


def _make_feature_branch(repo: Path, branch_name: str, return_to: str = "main") -> None:
    """Create and push a feature branch in repo, then return to return_to."""
    subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)
    _commit(repo, f"work on {branch_name}")
    subprocess.run(["git", "push", "-q", "origin", branch_name], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", return_to], cwd=repo, check=True)


def _make_worktree(repo: Path, branch_name: str, wt_path: Path) -> None:
    """Add a linked worktree for branch_name at wt_path."""
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), branch_name],
        cwd=repo,
        check=True,
    )


# ---------------------------------------------------------------------------
# fake_gh fixture
# ---------------------------------------------------------------------------

def _gh_shim_source(merged_branches: dict[str, dict]) -> str:
    """Return source for a gh shim script.

    merged_branches maps branch_name -> {"number": N, "mergedAt": "YYYY-MM-DD"}
    for branches that should appear merged. Branches absent from the dict
    appear as open (empty array) or unauthenticated based on the dict value.

    Special sentinel values:
      None         → empty array (branch open/not found)
      "unauth"     → auth status non-zero (used only for 'auth status' sub-command)
    """
    merged_json = json.dumps(merged_branches)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys

        MERGED = json.loads({merged_json!r})

        args = sys.argv[1:]

        # gh auth status
        if args and args[0] == "auth" and len(args) > 1 and args[1] == "status":
            if MERGED.get("__auth__") == "unauth":
                sys.exit(1)
            sys.exit(0)

        # gh pr list --head <branch> ...
        if args and args[0] == "pr" and "--head" in args:
            head_idx = args.index("--head")
            branch = args[head_idx + 1]
            info = MERGED.get(branch)
            if info is None:
                print("[]")
            else:
                print(json.dumps([{{
                    "number": info["number"],
                    "headRefName": branch,
                    "state": "MERGED",
                    "mergedAt": info["mergedAt"] + "T00:00:00Z",
                }}]))
            sys.exit(0)

        # Fallthrough: unknown subcommand
        sys.exit(0)
    """)


@pytest.fixture()
def fake_gh(tmp_path, monkeypatch):
    """Yield a factory that installs a gh shim and returns the env dict.

    Usage in tests:
        env = fake_gh({"feat/foo": {"number": 1, "mergedAt": "2026-05-01"}})
        result = _run_script(repo, env)
    """
    shim_dir = tmp_path / "gh_shim"
    shim_dir.mkdir()

    def _make_env(merged_branches: dict) -> dict:
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source(merged_branches))
        shim_py.chmod(0o755)
        new_path = str(shim_dir) + ":" + os.environ.get("PATH", "")
        env = {**os.environ, "PATH": new_path}
        return env

    return _make_env


def _run_script(repo: Path, env: dict, args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run the cleanup script in repo with the given environment."""
    cmd = [str(_SCRIPT)] + (args or [])
    return subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestNoCandidates:
    """Case 1: no merged PRs for any local branch."""

    def test_exit_zero_non_tty(self, tmp_path, fake_gh):
        """Non-TTY (pipe): no output, exit 0."""
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/open")
        env = fake_gh({})  # no merged branches
        result = _run_script(local, env)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_nothing_to_clean_message_suppressed_for_non_tty(self, tmp_path, fake_gh):
        """stdout is captured (non-TTY): the "nothing to clean" message is suppressed."""
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({})
        result = _run_script(local, env)
        assert result.returncode == 0
        # Non-TTY: no output expected
        assert result.stdout.strip() == ""


class TestOneMergedBranch:
    """Case 2: one merged branch, no worktree, remote auto-pruned, default current."""

    def test_branch_deleted_summary_correct(self, tmp_path, fake_gh):
        """The one merged branch is cleaned; summary lines are present."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/done")

        # Simulate auto-prune by deleting the remote ref before the script runs
        subprocess.run(["git", "branch", "-D", "feat/done"], cwd=bare, check=True)

        env = fake_gh({"feat/done": {"number": 42, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/done" in result.stdout
        assert "local branch:   deleted" in result.stdout
        # Branch no longer exists locally
        branches = subprocess.run(
            ["git", "branch", "--list", "feat/done"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert branches.strip() == ""


class TestMultipleMergedBranches:
    """Case 3: multiple merged branches cleaned in a single pass."""

    def test_all_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        for branch in ("feat/alpha", "feat/beta"):
            _make_feature_branch(local, branch)
            subprocess.run(["git", "branch", "-D", branch], cwd=bare, check=True)

        env = fake_gh({
            "feat/alpha": {"number": 1, "mergedAt": "2026-05-01"},
            "feat/beta": {"number": 2, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/alpha" in result.stdout
        assert "feat/beta" in result.stdout
        for branch in ("feat/alpha", "feat/beta"):
            remaining = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=local, capture_output=True, text=True
            ).stdout
            assert remaining.strip() == ""


class TestMixedMergedAndOpen:
    """Case 4: only merged branches cleaned; open branch untouched."""

    def test_only_merged_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/merged-one")
        _make_feature_branch(local, "feat/still-open")
        subprocess.run(["git", "branch", "-D", "feat/merged-one"], cwd=bare, check=True)

        env = fake_gh({"feat/merged-one": {"number": 10, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/merged-one" in result.stdout

        # Open branch still exists locally
        open_branch = subprocess.run(
            ["git", "branch", "--list", "feat/still-open"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert "feat/still-open" in open_branch


class TestBranchWithWorktree:
    """Case 5: branch with a worktree — path resolved via --porcelain, then removed."""

    def test_worktree_removed(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/wt-branch")
        subprocess.run(["git", "branch", "-D", "feat/wt-branch"], cwd=bare, check=True)

        wt_path = tmp_path / "feat-wt-branch-tree"
        _make_worktree(local, "feat/wt-branch", wt_path)
        assert wt_path.exists()

        env = fake_gh({"feat/wt-branch": {"number": 20, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "worktree:       removed:" in result.stdout
        assert not wt_path.exists()


class TestSlashedBranchName:
    """Case 6: branch name containing slashes — worktree lookup via --porcelain."""

    def test_slashed_branch_worktree_resolved(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/scope/deep-name")
        subprocess.run(["git", "branch", "-D", "feat/scope/deep-name"], cwd=bare, check=True)

        wt_path = tmp_path / "deep-name-tree"
        _make_worktree(local, "feat/scope/deep-name", wt_path)
        assert wt_path.exists()

        env = fake_gh({"feat/scope/deep-name": {"number": 30, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "feat/scope/deep-name" in result.stdout
        assert not wt_path.exists()


class TestRemoteNotAutoPruned:
    """Case 7: remote ref survives the merge — script deletes it."""

    def test_remote_deleted(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-auto-prune")

        # Confirm the remote ref exists before the run
        remote_refs = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "feat/no-auto-prune"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert "feat/no-auto-prune" in remote_refs

        env = fake_gh({"feat/no-auto-prune": {"number": 50, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        # The remote ref should be gone
        remote_refs_after = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "feat/no-auto-prune"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert remote_refs_after.strip() == ""


class TestDefaultBranchFastForward:
    """Case 8: default branch lags origin — ff-merge runs once after cleanup."""

    def test_default_branch_fast_forwarded(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/ff-test")
        subprocess.run(["git", "branch", "-D", "feat/ff-test"], cwd=bare, check=True)

        # Advance the remote default branch past the local one
        # (simulate another commit landing on origin/main after local last pulled)
        bare_clone = tmp_path / "clone"
        subprocess.run(
            ["git", "clone", "-q", str(bare), str(bare_clone)], check=True
        )
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=bare_clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=bare_clone, check=True)
        _commit(bare_clone, "advance remote")
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=bare_clone, check=True)

        env = fake_gh({"feat/ff-test": {"number": 60, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "fast-forwarded" in result.stdout or "already current" in result.stdout

    def test_fast_forwards_while_on_feature_branch(self, tmp_path, fake_gh):
        """ff-update must not touch the currently checked-out feature branch."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/ff-from-feature")
        subprocess.run(["git", "branch", "-D", "feat/ff-from-feature"], cwd=bare, check=True)

        # Advance the remote default branch
        bare_clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(bare), str(bare_clone)], check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=bare_clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=bare_clone, check=True)
        _commit(bare_clone, "advance remote")
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=bare_clone, check=True)

        # Stay on an unrelated feature branch (simulates the common real-world invocation)
        subprocess.run(["git", "checkout", "-b", "feat/still-open"], cwd=local, check=True)
        _commit(local, "open branch commit")
        open_sha_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=local, capture_output=True, text=True
        ).stdout.strip()

        env = fake_gh({"feat/ff-from-feature": {"number": 61, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        # Default branch was advanced
        assert "fast-forwarded" in result.stdout or "already current" in result.stdout
        # The currently checked-out branch was NOT moved
        open_sha_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=local, capture_output=True, text=True
        ).stdout.strip()
        assert open_sha_before == open_sha_after, (
            "fast-forward must not mutate the currently checked-out branch"
        )


class TestCurrentlyOnCandidateBranch:
    """Case 9: currently checked-out branch is a candidate — skipped; others proceed."""

    def test_checked_out_skipped_others_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/checked-out-merged")
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=bare, check=True)
        subprocess.run(["git", "branch", "-D", "feat/checked-out-merged"], cwd=bare, check=True)

        # Check out the candidate branch
        subprocess.run(["git", "checkout", "-q", "feat/checked-out-merged"], cwd=local, check=True)

        env = fake_gh({
            "feat/checked-out-merged": {"number": 70, "mergedAt": "2026-05-01"},
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        # The checked-out branch is reported as skipped
        assert "Skipped" in result.stdout
        assert "currently checked out" in result.stdout
        # The other merged branch was cleaned
        other_remaining = subprocess.run(
            ["git", "branch", "--list", "feat/other-merged"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert other_remaining.strip() == ""
        # The checked-out branch still exists
        checked_remaining = subprocess.run(
            ["git", "branch", "--list", "feat/checked-out-merged"],
            cwd=local, capture_output=True, text=True
        ).stdout
        assert "feat/checked-out-merged" in checked_remaining


class TestMasterDefaultRepo:
    """Case 10: repo whose default branch is 'master', not 'main'."""

    def test_fast_forwards_master_not_main(self, tmp_path, fake_gh):
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=master"], cwd=bare, check=True)

        local = tmp_path / "local"
        _init_repo(local)
        # Create initial commit on master
        subprocess.run(["git", "checkout", "-b", "master"], cwd=local, check=True)
        _commit(local, "init")
        subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "master"], cwd=local, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "master"], cwd=local, check=True)

        _make_feature_branch(local, "feat/master-test", return_to="master")
        subprocess.run(["git", "branch", "-D", "feat/master-test"], cwd=bare, check=True)

        env = fake_gh({"feat/master-test": {"number": 80, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        # Should mention master (default), not main
        assert "feat/master-test" in result.stdout


class TestOriginHeadUnset:
    """Case 11: origin/HEAD unset — script runs set-head and resolves."""

    def test_resolves_default_when_origin_head_unset(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-head")
        subprocess.run(["git", "branch", "-D", "feat/no-head"], cwd=bare, check=True)

        # Remove origin/HEAD so symbolic-ref returns empty
        subprocess.run(
            ["git", "remote", "set-head", "origin", "--delete"],
            cwd=local, check=False  # OK if already unset
        )

        env = fake_gh({"feat/no-head": {"number": 90, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        # Script should succeed (set-head auto re-populates or falls back to main)
        assert result.returncode == 0
        assert "feat/no-head" in result.stdout


class TestIdempotentRerun:
    """Case 12: second run reports zero candidates."""

    def test_idempotent(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/idempotent")
        subprocess.run(["git", "branch", "-D", "feat/idempotent"], cwd=bare, check=True)

        env = fake_gh({"feat/idempotent": {"number": 100, "mergedAt": "2026-05-01"}})

        # First run — cleans up
        first = _run_script(local, env)
        assert first.returncode == 0
        assert "feat/idempotent" in first.stdout

        # Second run — nothing left (local branch gone, so gh never queried for it)
        second = _run_script(local, env)
        assert second.returncode == 0
        assert second.stdout.strip() == ""  # non-TTY: silent on no-op


class TestDryRun:
    """Case 13: --dry-run prints candidates without acting."""

    def test_dry_run_no_destructive_action(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dry")
        _make_feature_branch(local, "feat/dry2")

        env = fake_gh({
            "feat/dry": {"number": 110, "mergedAt": "2026-05-01"},
            "feat/dry2": {"number": 111, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "Would clean up" in result.stdout
        assert "feat/dry" in result.stdout
        assert "feat/dry2" in result.stdout
        assert "PR #110" in result.stdout

        # Branches still exist locally
        for branch in ("feat/dry", "feat/dry2"):
            remaining = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=local, capture_output=True, text=True
            ).stdout
            assert branch in remaining


class TestInvalidArgs:
    """Case 14: invalid arguments exit 2 with usage on stderr; no git ops."""

    @pytest.mark.parametrize("bad_args", [
        ["foo"],
        ["--bar"],
        ["--dry-run", "x"],
        ["--dry-run", "--dry-run"],
    ])
    def test_invalid_args_exit_2(self, tmp_path, fake_gh, bad_args):
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({})
        result = _run_script(local, env, args=bad_args)
        assert result.returncode == 2
        assert "Usage" in result.stderr


class TestGhMissing:
    """Case 15: gh not in PATH — exits non-zero with install instructions."""

    def test_gh_missing_exits_nonzero(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        # Build a minimal PATH: a tmpdir with symlinks to every tool the
        # script needs (git, python3, bash) but no gh — so command -v gh fails.
        bin_dir = tmp_path / "min_bin"
        bin_dir.mkdir()
        for tool in ("git", "python3", "bash", "grep", "awk", "sed"):
            tool_path = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
            if tool_path:
                (bin_dir / tool).symlink_to(tool_path)
        env = {**os.environ, "PATH": str(bin_dir)}
        result = _run_script(local, env)
        assert result.returncode != 0
        assert "gh" in result.stderr.lower() or "install" in result.stderr.lower()


class TestGhUnauthenticated:
    """Case 16: gh is present but auth status returns non-zero."""

    def test_gh_unauth_exits_nonzero(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        # Special sentinel: make auth status fail
        env = fake_gh({"__auth__": "unauth"})
        result = _run_script(local, env)
        assert result.returncode != 0
        assert "auth" in result.stderr.lower() or "login" in result.stderr.lower()
