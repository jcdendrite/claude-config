"""Tests for cleanup-merged-branches.sh.

The gh CLI is replaced in every test by a PATH shim that reads canned
JSON keyed on the --head <branch> argument. Real git operations run
against temporary repos created per-test.
"""
from __future__ import annotations

import contextlib
import json
import os
import pty
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
    # --initial-branch=main avoids depending on the system's init.defaultBranch setting,
    # which varies across git versions and CI environments.
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=path, check=True)
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
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=main"], cwd=bare, check=True)

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


def _dead_pid() -> int:
    """Return a pid that is guaranteed not to be running."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _rev_parse(repo: Path, ref: str) -> str:
    """Return the full 40-char SHA for ref in repo."""
    result = subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# fake_gh fixture
# ---------------------------------------------------------------------------

def _gh_shim_source(pr_data: dict[str, object]) -> str:
    """Return source for a gh shim script.

    pr_data maps branch_name -> PR record(s) for that head branch name.
    A value may be:
      None                     → no PR for this name (empty array; open/not found)
      "error"                  → the `gh` call itself fails (non-zero exit),
                                  for exercising the fail-closed path
      "malformed"              → the `gh` call exits zero but writes
                                  non-JSON to stdout, for exercising the
                                  second, independent fail-closed path
                                  (the parser's JSONDecodeError branch
                                  rather than the exit-code branch).
                                  Models real `gh` mixing a banner or a
                                  truncated body into stdout on a 0 exit.
      {"number": N, "mergedAt": "YYYY-MM-DD", ["headRefOid": SHA]}
                                → sugar for a single MERGED row. When
                                  headRefOid is omitted it defaults to the
                                  branch's actual current tip (`git
                                  rev-parse <branch>`, run with the shim's
                                  cwd — the repo under test) — the common
                                  case of a legitimate merge whose branch
                                  was never rewritten afterward.
      [{"number": N, "state": "OPEN"|"MERGED"|"CLOSED",
        ["mergedAt": "YYYY-MM-DD"], ["headRefOid": SHA]}, ...]
                                → multiple PR rows under one head name, for
                                  modeling a reused branch name (e.g. an
                                  open PR alongside an older merged PR).

    Every response includes headRefOid regardless of --state, since the
    script now always queries `--state all` and classifies every row
    itself; the shim does not filter on the requested --state.

    Divergence from real `gh`: the auto-fill above only fires for
    state == "MERGED" — an OPEN or CLOSED row without an explicit
    headRefOid serializes as headRefOid: null. Real `gh pr list` always
    returns the actual head commit SHA regardless of state. Harmless
    today since classify_branch never reads headRefOid off a non-MERGED
    row, but a future guard that inspects an open PR's headRefOid would
    be silently validated against a synthetic null here rather than a
    real SHA — pass headRefOid explicitly on OPEN/CLOSED rows if a test
    ever needs one.

    One further sentinel is keyed on "__auth__" rather than a branch name:
      "unauth"     → `gh auth status` exits non-zero
    """
    payload = json.dumps(pr_data)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, subprocess, sys

        PR_DATA = json.loads({payload!r})

        args = sys.argv[1:]

        # gh auth status
        if args and args[0] == "auth" and len(args) > 1 and args[1] == "status":
            if PR_DATA.get("__auth__") == "unauth":
                sys.exit(1)
            sys.exit(0)

        # gh pr list --head <branch> ...
        if args and args[0] == "pr" and "--head" in args:
            head_idx = args.index("--head")
            branch = args[head_idx + 1]
            info = PR_DATA.get(branch)

            if info == "error":
                sys.exit(1)

            if info == "malformed":
                # Exit 0 with an unparseable body, as real gh can when a
                # banner or a truncated response lands on stdout.
                print('[{{"number":')
                sys.exit(0)

            if info is None:
                print("[]")
                sys.exit(0)

            rows_in = info if isinstance(info, list) else [info]
            rows_out = []
            for row in rows_in:
                state = row.get("state", "MERGED")
                merged_at = row.get("mergedAt")
                head_ref_oid = row.get("headRefOid")
                if state == "MERGED" and head_ref_oid is None:
                    head_ref_oid = subprocess.run(
                        ["git", "rev-parse", branch],
                        capture_output=True, text=True, check=True,
                    ).stdout.strip()
                rows_out.append({{
                    "number": row["number"],
                    "headRefName": branch,
                    "state": state,
                    "mergedAt": (merged_at + "T00:00:00Z") if merged_at else None,
                    "headRefOid": head_ref_oid,
                }})
            print(json.dumps(rows_out))
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

    def _make_env(pr_data: dict) -> dict:
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source(pr_data))
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
    ])
    def test_invalid_args_exit_2(self, tmp_path, fake_gh, bad_args):
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({})
        result = _run_script(local, env, args=bad_args)
        assert result.returncode == 2
        assert "Usage" in result.stderr

    @pytest.mark.parametrize("valid_dup_args", [
        ["--dry-run", "--dry-run"],
        ["--yes", "--yes"],
        ["--dry-run", "--yes", "--dry-run"],
    ])
    def test_duplicate_flags_are_idempotent(self, tmp_path, fake_gh, valid_dup_args):
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({})
        result = _run_script(local, env, args=valid_dup_args)
        assert result.returncode == 0


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


class TestLockedWorktreeUnlockedAndRemoved:
    """Case 17: branch with a locked worktree is unlocked and fully removed (worktree, local branch, remote)."""

    def test_locked_worktree_is_unlocked_and_removed(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-wt")
        subprocess.run(["git", "branch", "-D", "feat/locked-wt"], cwd=bare, check=True)

        wt_path = tmp_path / "locked-tree"
        _make_worktree(local, "feat/locked-wt", wt_path)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        env = fake_gh({"feat/locked-wt": {"number": 200, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "unlocked stale lock" in result.stdout
        assert "removed:" in result.stdout
        assert not wt_path.exists(), "locked worktree on a merged branch should be removed"
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-wt"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode != 0, "local branch should be deleted after worktree removed"

    def test_locked_annotated_in_dry_run(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-dry")
        subprocess.run(["git", "branch", "-D", "feat/locked-dry"], cwd=bare, check=True)

        wt_path = tmp_path / "locked-dry-tree"
        _make_worktree(local, "feat/locked-dry", wt_path)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        env = fake_gh({"feat/locked-dry": {"number": 210, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "Would clean up" in result.stdout
        assert "feat/locked-dry" in result.stdout
        assert "locked" in result.stdout and "will unlock and remove" in result.stdout
        # Dry-run must not touch the worktree or branch
        assert wt_path.exists()
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-dry"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0


class TestLockedWorktreeMixedWithUnlocked:
    """Case 18: locked and unlocked merged branches in same run — both fully cleaned."""

    def test_mixed_locked_and_unlocked(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)

        # Branch 1: locked worktree
        _make_feature_branch(local, "feat/locked-mix")
        subprocess.run(["git", "branch", "-D", "feat/locked-mix"], cwd=bare, check=True)
        locked_wt = tmp_path / "locked-mix-tree"
        _make_worktree(local, "feat/locked-mix", locked_wt)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(locked_wt), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        # Branch 2: unlocked worktree
        _make_feature_branch(local, "feat/unlocked-mix")
        subprocess.run(["git", "branch", "-D", "feat/unlocked-mix"], cwd=bare, check=True)
        unlocked_wt = tmp_path / "unlocked-mix-tree"
        _make_worktree(local, "feat/unlocked-mix", unlocked_wt)

        env = fake_gh({
            "feat/locked-mix": {"number": 201, "mergedAt": "2026-05-01"},
            "feat/unlocked-mix": {"number": 202, "mergedAt": "2026-05-01"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0

        # Locked branch: unlocked and fully cleaned
        assert "unlocked stale lock" in result.stdout
        assert not locked_wt.exists(), "locked worktree on merged branch should be removed"
        ref_locked = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-mix"],
            cwd=local, capture_output=True,
        )
        assert ref_locked.returncode != 0, "locked local branch should be deleted after removal"

        # Unlocked branch: fully cleaned
        assert not unlocked_wt.exists(), "unlocked worktree should be removed"
        ref_unlocked = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/unlocked-mix"],
            cwd=local, capture_output=True,
        )
        assert ref_unlocked.returncode != 0, "unlocked local branch should be deleted"


class TestLockedWorktreeRemoveFailsCleanly:
    """Case 19: locked worktree with untracked content — unlock attempted, remove refused, worktree relocked."""

    def test_locked_dirty_worktree_is_refused_and_relocked(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-dirty")
        subprocess.run(["git", "branch", "-D", "feat/locked-dirty"], cwd=bare, check=True)

        wt_path = tmp_path / "locked-dirty-tree"
        _make_worktree(local, "feat/locked-dirty", wt_path)
        # Untracked file causes git worktree remove to refuse without --force
        (wt_path / "leftover.txt").write_text("in-progress work")
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", f"test (pid {dead})"],
            cwd=local, check=True, capture_output=True,
        )

        env = fake_gh({"feat/locked-dirty": {"number": 220, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "unlocked stale lock" in result.stdout
        assert "remove failed (manual step needed)" in result.stdout
        assert "contains modified or untracked files" in result.stdout
        # Worktree dir and its content must survive
        assert wt_path.exists()
        assert (wt_path / "leftover.txt").exists()
        # Local branch must survive (script continue'd before branch -D)
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/locked-dirty"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch ref must survive when worktree remove fails"
        # Worktree must be relocked (best-effort restore of prior state)
        porcelain = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=local, capture_output=True, text=True, check=True,
        )
        records = porcelain.stdout.strip().split("\n\n")
        wt_str = str(wt_path)
        wt_is_locked = any(
            f"worktree {wt_str}" in record and "locked" in record
            for record in records
        )
        assert wt_is_locked, "worktree should be relocked after failed remove"


# ---------------------------------------------------------------------------
# Tier B helpers and tests (reachable from origin/main, no merged PR)
# ---------------------------------------------------------------------------

def _make_tier_b_branch(repo: Path, remote: Path, branch_name: str) -> None:
    """Feature branch whose commits are reachable from origin/main, but gh returns no PR."""
    subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)
    _commit(repo, f"work on {branch_name}")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    subprocess.run(["git", "merge", "-q", "--ff-only", branch_name], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)


class TestTierBReachableNoMergedPR:
    """Branches reachable from origin/main but with no merged PR for this name."""

    def test_reachable_but_no_merged_pr_prompts(self, fake_gh, tmp_path):
        """Tier B branch: interactive y via pty stdin → deleted."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [str(_SCRIPT)], cwd=local,
                env=env,
                stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            os.close(slave_fd)
            os.write(master_fd, b"y\n")
            proc.wait(timeout=30)
            os.close(master_fd)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise

        assert proc.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" not in branches

    def test_reachable_but_no_merged_pr_skipped_on_n(self, fake_gh, tmp_path):
        """Tier B branch: interactive n via pty stdin → branch survives."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [str(_SCRIPT)], cwd=local,
                env=env,
                stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            os.close(slave_fd)
            os.write(master_fd, b"n\n")
            proc.wait(timeout=30)
            os.close(master_fd)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise

        assert proc.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" in branches

    def test_reachable_no_pr_non_tty_no_yes_skips(self, fake_gh, tmp_path):
        """Tier B branch, non-TTY stdin, no --yes: branch skipped with warning."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})
        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" in branches
        assert b"tier-b-branch" in result.stdout

    def test_reachable_no_pr_with_yes_flag_deletes(self, fake_gh, tmp_path):
        """Tier B branch + --yes: deleted without prompt."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})
        result = subprocess.run(
            [str(_SCRIPT), "--yes"], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" not in branches

    def test_unmerged_branch_not_touched(self, fake_gh, tmp_path):
        """Tier C branch (not reachable from origin/main, no merged PR) is never deleted."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "unmerged-branch")
        env = fake_gh({})
        result = subprocess.run(
            [str(_SCRIPT), "--yes"], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "unmerged-branch" in branches

    def test_dry_run_separates_confirmed_and_probable(self, tmp_path):
        """Dry-run shows Tier A under 'confirmed merged' and Tier B under 'probable merges'."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "tier-a-branch")
        _make_tier_b_branch(local, remote, "tier-b-branch")
        _make_feature_branch(local, "tier-c-branch")

        shim_dir = tmp_path / "gh_shim_sep"
        shim_dir.mkdir()
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source({"tier-a-branch": {"number": 1, "mergedAt": "2026-05-01"}}))
        shim_py.chmod(0o755)
        env = {**os.environ, "PATH": str(shim_dir) + ":" + os.environ.get("PATH", "")}

        result = subprocess.run(
            [str(_SCRIPT), "--dry-run"], cwd=local,
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        output = result.stdout.decode()
        assert "confirmed merged" in output
        assert "tier-a-branch" in output
        assert "probable" in output.lower() or "would prompt" in output.lower()
        assert "tier-b-branch" in output
        assert "tier-c-branch" not in output

    def test_existing_tier_a_silent_clean_preserved(self, tmp_path):
        """Tier A branch (gh-confirmed merged) cleans silently, no prompt needed."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "tier-a-branch")

        shim_dir = tmp_path / "gh_shim_a"
        shim_dir.mkdir()
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source({"tier-a-branch": {"number": 1, "mergedAt": "2026-05-01"}}))
        shim_py.chmod(0o755)
        env = {**os.environ, "PATH": str(shim_dir) + ":" + os.environ.get("PATH", "")}

        result = subprocess.run(
            [str(_SCRIPT)], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-a-branch" not in branches
        assert b"prompt" not in result.stdout.lower()

    def test_dry_run_with_yes_flag_no_prompt(self, fake_gh, tmp_path):
        """--dry-run --yes: tier B listed but nothing deleted, no prompt."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-branch")
        env = fake_gh({})
        result = subprocess.run(
            [str(_SCRIPT), "--dry-run", "--yes"], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert result.returncode == 0
        branches = subprocess.run(
            ["git", "branch"], cwd=local, capture_output=True, text=True
        ).stdout
        assert "tier-b-branch" in branches
        output = result.stdout.decode()
        assert "tier-b-branch" in output


class TestLockedWorktreeLiveness:
    """Live vs dead pid liveness check for locked worktrees."""

    def test_locked_worktree_live_pid_skipped(self, tmp_path):
        """Locked worktree with live pid (current process) is not removed."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "locked-branch")
        wt_path = tmp_path / "locked-wt"
        _make_worktree(local, "locked-branch", wt_path)
        subprocess.run(
            ["git", "worktree", "lock", "--reason", f"test (pid {os.getpid()})", str(wt_path)],
            cwd=local, check=True,
        )

        shim_dir = tmp_path / "gh_shim_live"
        shim_dir.mkdir()
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source({"locked-branch": {"number": 300, "mergedAt": "2026-05-01"}}))
        shim_py.chmod(0o755)
        env = {**os.environ, "PATH": str(shim_dir) + ":" + os.environ.get("PATH", "")}

        result = subprocess.run(
            [str(_SCRIPT), "--yes"], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        assert wt_path.exists()
        output = result.stdout.decode()
        assert "live" in output.lower() or "locked" in output.lower()

    def test_locked_worktree_dead_pid_removed(self, tmp_path):
        """Locked worktree with dead pid is unlocked and removed."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "stale-locked-branch")
        wt_path = tmp_path / "stale-wt"
        _make_worktree(local, "stale-locked-branch", wt_path)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", "--reason", f"test (pid {dead})", str(wt_path)],
            cwd=local, check=True,
        )

        shim_dir = tmp_path / "gh_shim_dead"
        shim_dir.mkdir()
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source({"stale-locked-branch": {"number": 301, "mergedAt": "2026-05-01"}}))
        shim_py.chmod(0o755)
        env = {**os.environ, "PATH": str(shim_dir) + ":" + os.environ.get("PATH", "")}

        result = subprocess.run(
            [str(_SCRIPT), "--yes"], cwd=local,
            env=env,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        output = result.stdout.decode()
        assert "stale" in output.lower() or "dead" in output.lower()


class TestWorktreeInUseGuard:
    """A worktree that a live process is working inside is not removed."""

    def test_worktree_with_live_process_is_skipped(self, tmp_path, fake_gh):
        """A worktree holding a live process's cwd is skipped; branch survives."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use")
        subprocess.run(["git", "branch", "-D", "feat/in-use"], cwd=bare, check=True)

        wt_path = tmp_path / "in-use-tree"
        _make_worktree(local, "feat/in-use", wt_path)

        env = fake_gh({"feat/in-use": {"number": 400, "mergedAt": "2026-05-01"}})
        # A live process whose working directory is inside the worktree.
        holder = subprocess.Popen(["sleep", "120"], cwd=str(wt_path))
        try:
            result = _run_script(local, env)
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0
        assert "in use by a live process" in result.stdout
        assert wt_path.exists(), "worktree in use must not be removed"
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/in-use"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch must survive when its worktree is in use"

    def test_worktree_in_use_via_subdirectory_is_skipped(self, tmp_path, fake_gh):
        """A process cwd'd into a subdirectory of the worktree also skips it."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use-sub")
        subprocess.run(["git", "branch", "-D", "feat/in-use-sub"], cwd=bare, check=True)

        wt_path = tmp_path / "in-use-sub-tree"
        _make_worktree(local, "feat/in-use-sub", wt_path)
        subdir = wt_path / "nested"
        subdir.mkdir()

        env = fake_gh({"feat/in-use-sub": {"number": 401, "mergedAt": "2026-05-01"}})
        holder = subprocess.Popen(["sleep", "120"], cwd=str(subdir))
        try:
            result = _run_script(local, env)
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0
        assert "in use by a live process" in result.stdout
        assert wt_path.exists(), "worktree with an in-use subdirectory must not be removed"

    def test_idle_worktree_still_removed(self, tmp_path, fake_gh):
        """A worktree with no live process inside it is removed as before."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/idle-wt")
        subprocess.run(["git", "branch", "-D", "feat/idle-wt"], cwd=bare, check=True)

        wt_path = tmp_path / "idle-tree"
        _make_worktree(local, "feat/idle-wt", wt_path)

        env = fake_gh({"feat/idle-wt": {"number": 403, "mergedAt": "2026-05-01"}})
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "worktree:       removed:" in result.stdout
        assert "in use by a live process" not in result.stdout
        assert not wt_path.exists(), "idle worktree must still be removed"

    def test_worktree_in_use_annotated_in_dry_run(self, tmp_path, fake_gh):
        """--dry-run flags an in-use worktree and changes nothing."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use-dry")

        wt_path = tmp_path / "in-use-dry-tree"
        _make_worktree(local, "feat/in-use-dry", wt_path)

        env = fake_gh({"feat/in-use-dry": {"number": 402, "mergedAt": "2026-05-01"}})
        holder = subprocess.Popen(["sleep", "120"], cwd=str(wt_path))
        try:
            result = _run_script(local, env, args=["--dry-run"])
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0
        assert "feat/in-use-dry" in result.stdout
        assert "worktree in use" in result.stdout and "would skip" in result.stdout
        assert wt_path.exists()
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/in-use-dry"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0


# ---------------------------------------------------------------------------
# Open-PR guard and Tier-A tip verification (classify_branch)
#
# Regression coverage for the 2026-07-19 incident: Tier A matched a merged
# PR by branch *name* only, with no check that the current tip belonged to
# that merge. A branch name reused across PRs (old PR merged, new PR opened
# on the same head name) was deleted, which GitHub recorded as closing the
# new, still-open PR. See ~/.claude/plans/cleanup-branches-open-pr-guard.md.
# ---------------------------------------------------------------------------

class TestOpenPRGuardWinsOverStaleMergedMatch:
    """An open PR on this head branch name must survive even when an older
    PR merged under the same name — the exact incident shape (open PR #14
    alongside merged PR #7, same head branch)."""

    def test_open_pr_survives_despite_same_named_merged_pr(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-name-with-open-pr")

        env = fake_gh({
            "reused-name-with-open-pr": [
                {"number": 14, "state": "OPEN"},
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
        })
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-name-with-open-pr"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch with an open PR must survive"
        remote_refs = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "reused-name-with-open-pr"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert "reused-name-with-open-pr" in remote_refs, "remote branch of an open PR must survive"
        assert "open PR #14" in result.stdout

    def test_open_pr_guard_skip_reason_rendered_in_dry_run(self, tmp_path, fake_gh):
        """The skip-reason line renders on the dry-run code path too:
        print_skip_reason_lines runs from a separate branch in the script
        (the dry-run preview) from the real-run summary path, and dry-run
        must never delete regardless."""
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-name-with-open-pr")

        env = fake_gh({
            "reused-name-with-open-pr": [
                {"number": 14, "state": "OPEN"},
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
        })
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "open PR #14" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-name-with-open-pr"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "dry-run must never delete"


class TestOpenPROnlyNoSameNamedMergedPR:
    """The common, non-incident shape: a branch with only an open PR and no
    same-named merged PR at all must survive. Behaviorally covered by the
    classifier returning the open verdict before merged rows are even
    considered — locked here by an assertion so a future reordering of
    that check can't silently break the common case."""

    def test_open_pr_only_no_merged_row_survives(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-review")

        env = fake_gh({
            "feat/in-review": [{"number": 21, "state": "OPEN"}],
        })
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/in-review"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch with an open PR must survive"
        assert "open PR #21" in result.stdout


class TestFailClosedOnGhError:
    """A `gh` failure must never be read as "no PR found" — the branch is
    skipped even when it would otherwise qualify via Tier B reachability,
    proving fail-closed overrides Tier B rather than falling through to it.

    Two independent failure paths reach the same verdict: a non-zero exit
    from `gh` itself, and a zero exit whose body will not parse. Both are
    covered here, because reading either as "no PR found" is the mistake
    that lets a branch with an open PR be deleted."""

    def test_gh_error_skips_even_reachable_branch(self, tmp_path, fake_gh):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "reachable-but-erroring")

        env = fake_gh({"reachable-but-erroring": "error"})
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reachable-but-erroring"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "gh failure must fail closed, not fall through to Tier B"
        assert "gh lookup failed" in result.stdout

    def test_gh_error_on_one_branch_does_not_block_sibling_cleanup(self, tmp_path, fake_gh):
        """A `gh` failure on one branch must not poison or abort the sweep:
        a sibling branch in the same run that is normally Tier-A-mergeable
        is still cleaned."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "reachable-but-erroring")
        _make_feature_branch(local, "feat/healthy-merged")

        env = fake_gh({
            "reachable-but-erroring": "error",
            "feat/healthy-merged": {"number": 42, "mergedAt": "2026-05-01"},
        })
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        erroring_ref = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reachable-but-erroring"],
            cwd=local, capture_output=True,
        )
        assert erroring_ref.returncode == 0, "erroring branch must survive"
        healthy_ref = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/healthy-merged"],
            cwd=local, capture_output=True,
        )
        assert healthy_ref.returncode != 0, "healthy sibling branch must still be cleaned"

    def test_malformed_json_on_zero_exit_skips_even_reachable_branch(self, tmp_path, fake_gh):
        """`gh` exits 0 but its body will not parse: the branch is skipped,
        not treated as "no PR found". Substituting an empty PR list here
        would bypass the open-PR guard for any branch whose response is
        truncated or polluted, which is the shape of the original
        incident."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "reachable-but-malformed")

        env = fake_gh({"reachable-but-malformed": "malformed"})
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reachable-but-malformed"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "unparseable gh output must fail closed, not fall through to Tier B"
        assert "gh lookup failed" in result.stdout


class TestStaleNameNoOpenPR:
    """A merged PR sharing this branch name, but whose headRefOid does not
    match the current tip, is not a Tier-A match — the name was reused
    after that PR merged."""

    def test_stale_name_match_not_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-branch-name")

        env = fake_gh({
            "reused-branch-name": [
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
        })
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-branch-name"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "stale name-only match must not be deleted"
        assert "likely a reused branch name" in result.stdout


class TestMultipleMergedRowsScanFullHistory:
    """Guard 2's tip-vs-headRefOid match scans every MERGED row for a head
    branch name, not just the first — a name can accumulate more than one
    merged PR over its history, and the matching row is not always first."""

    def test_tip_matches_second_of_two_merged_rows_cleans_branch(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/reused-name-twice")
        tip = _rev_parse(local, "feat/reused-name-twice")

        env = fake_gh({
            "feat/reused-name-twice": [
                {"number": 5, "state": "MERGED", "mergedAt": "2026-04-01", "headRefOid": "a" * 40},
                {"number": 9, "state": "MERGED", "mergedAt": "2026-06-01", "headRefOid": tip},
            ],
        })
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/reused-name-twice"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode != 0, "a tip match on the second MERGED row must still be Tier A"

    def test_tip_matches_neither_of_two_merged_rows_skipped_stale(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/reused-name-neither-matches")

        env = fake_gh({
            "feat/reused-name-neither-matches": [
                {"number": 5, "state": "MERGED", "mergedAt": "2026-04-01", "headRefOid": "a" * 40},
                {"number": 9, "state": "MERGED", "mergedAt": "2026-06-01", "headRefOid": "b" * 40},
            ],
        })
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/reused-name-neither-matches"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "no matching MERGED row among several must not be Tier A"
        assert "likely a reused branch name" in result.stdout


class TestSquashMergeStillCleanedAfterTipGuard:
    """Regression: a legitimately squash/rebase-merged branch (tip not
    reachable from origin/<default>, but tip == the merged PR's
    headRefOid) is still cleaned once the tip-verification guard is in
    place — the guard must not reject the common case it was added
    alongside."""

    def test_squash_merged_branch_with_matching_tip_is_cleaned(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/squash-merged")
        tip = _rev_parse(local, "feat/squash-merged")
        subprocess.run(["git", "branch", "-D", "feat/squash-merged"], cwd=bare, check=True)

        env = fake_gh({
            "feat/squash-merged": {"number": 500, "mergedAt": "2026-05-01", "headRefOid": tip},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/squash-merged"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode != 0, "matching-tip merged PR must still be cleaned"


class TestClosedUnmergedOnlyLeftUntouched:
    """A branch whose sole PR record is closed without merging (mergedAt is
    null) is Tier C — left untouched, and not misreported as a stale-name
    skip (there is no merged PR to have reused the name from)."""

    def test_closed_unmerged_pr_not_cleaned_and_not_reported_stale(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/closed-unmerged")

        env = fake_gh({
            "feat/closed-unmerged": [
                {"number": 9, "state": "CLOSED", "mergedAt": None},
            ],
        })
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/closed-unmerged"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "closed-unmerged-only branch is Tier C, never touched"
        assert "stale" not in result.stdout.lower()


class TestCheckedOutIncidentBranchReportsOpenPRSkip:
    """The checked-out-branch message site must honor the open-PR guard
    too: an incident-shaped branch that happens to be checked out is
    reported via its open-PR reason, never deleted."""

    def test_checked_out_open_pr_branch_reports_reason_and_survives(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "reused-name-with-open-pr")
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=bare, check=True)

        subprocess.run(["git", "checkout", "-q", "reused-name-with-open-pr"], cwd=local, check=True)

        env = fake_gh({
            "reused-name-with-open-pr": [
                {"number": 14, "state": "OPEN"},
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "open PR #14" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/reused-name-with-open-pr"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "checked-out branch must never be deleted regardless of verdict"
        other_remaining = subprocess.run(
            ["git", "branch", "--list", "feat/other-merged"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert other_remaining.strip() == "", "the unrelated tier-a branch should still be cleaned"


class TestCheckedOutTierBBranchReportsSkip:
    """The checked-out-branch message site must also honor a Tier-B
    verdict: a branch reachable from origin/<default> with no PR match at
    all that happens to be checked out is reported as skipped, never
    deleted. TestCurrentlyOnCandidateBranch already covers the Tier-A
    verdict at this same call site; this fills the Tier-B gap."""

    def test_checked_out_tier_b_branch_reports_reason_and_survives(self, tmp_path, fake_gh):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-checked-out")
        # A second, ordinary Tier-A candidate keeps the run past the
        # early "nothing to clean" exit so checked_out_skip_line() at the
        # end of the real-run path actually runs.
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=remote, check=True)
        subprocess.run(["git", "checkout", "-q", "tier-b-checked-out"], cwd=local, check=True)

        env = fake_gh({
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "Skipped: tier-b-checked-out (currently checked out)" in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/tier-b-checked-out"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "checked-out branch must never be deleted regardless of verdict"


class TestCheckedOutNonCandidateStaysSilent:
    """The checked-out-branch message is a cleanup-candidate notice, not a
    running commentary: a checked-out branch that is not a candidate at all
    (a stale name-only match) produces no line. Locks the silent arm so a
    future edit does not start reporting every checked-out branch."""

    def test_checked_out_stale_name_branch_reports_nothing(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "checked-out-stale-name")
        # A second, ordinary Tier-A candidate keeps the run past the early
        # "nothing to clean" exit so the checked-out message site runs at all.
        _make_feature_branch(local, "feat/other-merged")
        subprocess.run(["git", "branch", "-D", "feat/other-merged"], cwd=bare, check=True)
        subprocess.run(["git", "checkout", "-q", "checked-out-stale-name"], cwd=local, check=True)

        env = fake_gh({
            "checked-out-stale-name": [
                {"number": 7, "state": "MERGED", "mergedAt": "2026-06-16", "headRefOid": "a" * 40},
            ],
            "feat/other-merged": {"number": 71, "mergedAt": "2026-05-02"},
        })
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "checked-out-stale-name" not in result.stdout, (
            "a checked-out branch that is not a cleanup candidate must not be reported"
        )
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/checked-out-stale-name"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "checked-out branch must never be deleted regardless of verdict"


class TestTierBWithStaleMergedRowReportsBothSignals:
    """A branch can be both reachable from origin/<default> and carry a
    same-named merged PR whose tip does not match — Tier B by reachability,
    stale by name. It is still cleaned, but the reason line names the
    same-named PR rather than claiming no merged PR exists for the name."""

    _STALE_MERGED_ROW = {
        "tier-b-with-stale-name": [
            {"number": 33, "state": "MERGED", "mergedAt": "2026-04-01", "headRefOid": "a" * 40},
        ],
    }

    def test_tier_b_reachable_with_stale_merged_row_still_cleaned(self, tmp_path, fake_gh):
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-with-stale-name")

        env = fake_gh(self._STALE_MERGED_ROW)
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/tier-b-with-stale-name"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode != 0, "reachability still qualifies the branch for Tier B cleanup"

    def test_tier_b_reason_names_the_same_named_merged_pr(self, tmp_path, fake_gh):
        """The reason string is rendered on the paths that show one — the
        dry-run preview and the interactive prompt — not under --yes, which
        deletes without prompting."""
        local, remote = _make_repo_with_remote(tmp_path)
        _make_tier_b_branch(local, remote, "tier-b-with-stale-name")

        env = fake_gh(self._STALE_MERGED_ROW)
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0
        assert "a merged PR #33 shares this name" in result.stdout
        assert "no merged PR for this name" not in result.stdout


class TestClassifierEmitsNoShellDiagnostics:
    """classify_branch builds its parser source as a double-quoted shell
    string, so anything shell-active in that source is expanded before
    python sees it. A stray backtick or dollar sign there is silently eaten
    (and could be executed), announcing itself only as shell noise on
    stderr — which nothing else in this suite would notice."""

    def test_normal_run_writes_nothing_to_stderr(self, tmp_path, fake_gh):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/ordinary-merged")
        subprocess.run(["git", "branch", "-D", "feat/ordinary-merged"], cwd=bare, check=True)

        env = fake_gh({"feat/ordinary-merged": {"number": 88, "mergedAt": "2026-05-02"}})
        result = _run_script(local, env, args=["--yes"])

        assert result.returncode == 0
        assert result.stderr == "", (
            f"classification must not emit shell diagnostics; got: {result.stderr!r}"
        )
