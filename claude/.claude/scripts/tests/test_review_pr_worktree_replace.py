"""Tests for review-pr-worktree-replace.py -- the fcntl.flock-based mutual
exclusion around review-pr-checkout.sh's worktree remove/prune/add sequence.

The full CLI is shelled out to for the git-worktree-sequence behavior only
a real subprocess invocation exercises, including the mutual-exclusion and
SIGKILL-release tests below, which load this script directly (via
importlib, since the module's filename is not a valid Python identifier) to
reach the same acquire_lock it imports from _review_pr_worktree.py.
test_review_pr_worktree.py holds acquire_lock's own direct unit tests,
since that primitive is shared with review-pr-worktree-remove.py, not owned
by this script.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .conftest import _git_shim_that_fails_on_worktree_prune

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "review-pr-worktree-replace.py"


def _run_cli(
    repo_root: Path, worktree_dir: Path, sha: str, session_id: str, deadline_seconds
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(repo_root), str(worktree_dir), sha, session_id, str(deadline_seconds)],
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> tuple[str, str]:
    """A repo with two commits, so a test can replace a worktree checked
    out at the first SHA with one at the second -- returns (first_sha,
    second_sha)."""
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=repo, check=True)
    first_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "second.txt").write_text("second\n")
    subprocess.run(["git", "add", "second.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo, check=True)
    second_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return first_sha, second_sha


class TestUsageErrors:
    def test_wrong_argc_exits_two(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "one", "two"], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()

    def test_non_numeric_deadline_exits_two(self, tmp_path):
        result = _run_cli(tmp_path, tmp_path / "wt", "abc123", "session-a", "not-a-number")
        assert result.returncode == 2
        assert "DEADLINE_SECONDS" in result.stderr


class TestWorktreeReplaceHappyPath:
    def test_creates_a_fresh_worktree_at_the_given_sha(self, tmp_path):
        repo = tmp_path / "repo"
        first_sha, _ = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)

        result = _run_cli(repo, worktree_dir, first_sha, "session-a", 5)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(worktree_dir)
        assert (worktree_dir / "file.txt").exists()

    def test_second_run_against_a_different_sha_replaces_the_prior_worktree(self, tmp_path):
        repo = tmp_path / "repo"
        first_sha, second_sha = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)

        first = _run_cli(repo, worktree_dir, first_sha, "session-a", 5)
        assert first.returncode == 0, first.stderr
        assert not (worktree_dir / "second.txt").exists()

        second = _run_cli(repo, worktree_dir, second_sha, "session-b", 5)
        assert second.returncode == 0, second.stderr
        assert (worktree_dir / "second.txt").exists(), "worktree must now hold the second commit's own content"
        checked_out_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree_dir, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert checked_out_head == second_sha


class TestWorktreeReplaceOwnershipSidecar:
    """The WORKTREE_DIR.owner sidecar must be written inside this script's
    own locked section, atomic with worktree creation, rather than by a
    caller after the lock is released -- a caller-side write leaves a
    window in which a queued removal call could acquire the lock and
    observe a stale owner value before the new owner file lands."""

    def test_writes_the_owner_sidecar_with_the_given_session_id(self, tmp_path):
        repo = tmp_path / "repo"
        first_sha, _ = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)

        result = _run_cli(repo, worktree_dir, first_sha, "session-a", 5)
        assert result.returncode == 0, result.stderr
        owner_file = Path(f"{worktree_dir}.owner")
        assert owner_file.read_text() == "session-a\n"

    def test_second_run_with_a_different_session_id_overwrites_the_sidecar(self, tmp_path):
        repo = tmp_path / "repo"
        first_sha, second_sha = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)

        first = _run_cli(repo, worktree_dir, first_sha, "session-a", 5)
        assert first.returncode == 0, first.stderr

        second = _run_cli(repo, worktree_dir, second_sha, "session-b", 5)
        assert second.returncode == 0, second.stderr
        owner_file = Path(f"{worktree_dir}.owner")
        assert owner_file.read_text() == "session-b\n"

    def test_removal_call_reads_back_the_sidecar_this_script_wrote(self, tmp_path):
        """Contract test between the writer and the reader: a removal call
        for the same session id this script just wrote to the sidecar must
        match and proceed, proving the two scripts agree on the sidecar's
        path and content format. TestOwnershipCheckAtomicWithLock in
        test_review_pr_worktree_remove.py covers the lock's own mutual-
        exclusion guarantee (a removal call blocked on the lock observes
        whatever the sidecar holds once it finally acquires it); this test
        only pins the writer/reader contract, not the locking mechanism
        itself."""
        repo = tmp_path / "repo"
        first_sha, _ = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)

        result = _run_cli(repo, worktree_dir, first_sha, "session-a", 5)
        assert result.returncode == 0, result.stderr

        remove_script = SCRIPT_PATH.parent / "review-pr-worktree-remove.py"
        removal = subprocess.run(
            [sys.executable, str(remove_script), str(repo), str(worktree_dir), "session-a", "5"],
            capture_output=True, text=True,
        )
        assert removal.returncode == 0, removal.stderr
        assert not worktree_dir.exists()

    def test_sidecar_write_failure_rolls_back_the_just_created_worktree(self, tmp_path):
        """If the sidecar write itself fails, the worktree `git worktree add`
        just created must not survive -- an un-rolled-back worktree with no
        owner recorded would be free for any concurrent removal call to
        delete out from under whichever session actually requested it,
        reopening the exact race this whole change closes. Forces the
        failure deterministically by pre-creating the owner-sidecar path as
        a directory, so `os.open(..., O_WRONLY)` raises IsADirectoryError."""
        repo = tmp_path / "repo"
        first_sha, _ = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)
        Path(f"{worktree_dir}.owner").mkdir()

        result = _run_cli(repo, worktree_dir, first_sha, "session-a", 5)
        assert result.returncode == 2
        assert "could not write ownership sidecar" in result.stderr
        assert not worktree_dir.exists(), "the just-created worktree must be rolled back"


class TestGitFailureSurfaces:
    """remove_worktree's RuntimeError on a failed `git worktree prune` (unit-
    tested directly against remove_worktree in test_review_pr_worktree.py)
    must also surface through this script's own CLI: a nonzero exit and the
    underlying git failure on stderr, not a false-success worktree path on
    stdout."""

    def test_failed_prune_exits_nonzero_with_the_git_failure_on_stderr(self, tmp_path):
        repo = tmp_path / "repo"
        first_sha, _ = _init_repo(repo)
        # Never created: os.path.exists is False, so only the unconditional
        # prune call runs, isolating its own failure before `worktree add`
        # is ever attempted.
        worktree_dir = repo / ".claude" / "worktrees" / "never-created"
        worktree_dir.parent.mkdir(parents=True)

        shim_dir = _git_shim_that_fails_on_worktree_prune(tmp_path)
        env = {**os.environ, "PATH": str(shim_dir) + os.pathsep + os.environ.get("PATH", "")}
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(repo), str(worktree_dir), first_sha, "session-a", "5"],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 2
        assert "synthetic prune failure" in result.stderr
        assert not worktree_dir.exists(), "a prune failure must abort before `worktree add` runs"


def _write_holder_script(tmp_path: Path) -> Path:
    """A standalone process that acquires WORKTREE_DIR.lock via the same
    acquire_lock the production script imports, prints "locked" once it holds
    it (so the parent test can synchronize on that instead of a sleep-based
    guess), then sleeps for the given duration before releasing -- long
    enough in the SIGKILL test to be killed mid-hold, and in the deadline
    test to outlive the contending invocation's own deadline."""
    holder = tmp_path / "lock_holder.py"
    holder.write_text(
        "import fcntl, sys, time\n"
        "sys.path.insert(0, sys.argv[3])\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('review_pr_worktree_replace', sys.argv[4])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "lock_f = mod.acquire_lock(sys.argv[1], 30.0)\n"
        "assert lock_f is not None, 'holder itself failed to acquire the lock'\n"
        "print('locked', flush=True)\n"
        "time.sleep(float(sys.argv[2]))\n"
        "fcntl.flock(lock_f, fcntl.LOCK_UN)\n"
    )
    return holder


class TestWorktreeReplaceMutualExclusion:
    def test_concurrent_invocations_never_overlap_in_the_critical_section(self, tmp_path):
        """Two processes recording their own enter/exit timestamps around a
        deliberately-slowed critical section -- proving exclusion, not just
        eventual success. A weaker "both eventually finish" assertion would
        pass even against a silently no-op lock."""
        lock_path = tmp_path / "wt.lock"
        results_path = tmp_path / "results.jsonl"
        recorder = tmp_path / "recorder.py"
        recorder.write_text(
            "import fcntl, json, sys, time\n"
            "sys.path.insert(0, sys.argv[4])\n"
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('review_pr_worktree_replace', sys.argv[5])\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "label, lock_path, hold_seconds, results_path = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[6]\n"
            "lock_f = mod.acquire_lock(lock_path, 10.0)\n"
            "assert lock_f is not None\n"
            "enter = time.monotonic()\n"
            "time.sleep(hold_seconds)\n"
            "exit_ = time.monotonic()\n"
            "with open(results_path, 'a') as f:\n"
            "    f.write(json.dumps({'label': label, 'enter': enter, 'exit': exit_}) + chr(10))\n"
            "fcntl.flock(lock_f, fcntl.LOCK_UN)\n"
        )
        module_dir = str(SCRIPT_PATH.parent)
        procs = [
            subprocess.Popen(
                [sys.executable, str(recorder), label, str(lock_path), "0.3", module_dir, str(SCRIPT_PATH), str(results_path)]
            )
            for label in ("a", "b")
        ]
        for proc in procs:
            assert proc.wait(timeout=10) == 0

        records = [json.loads(line) for line in results_path.read_text().splitlines() if line]
        assert len(records) == 2
        (r1, r2) = sorted(records, key=lambda r: r["enter"])
        assert r1["exit"] <= r2["enter"], (
            f"critical sections overlapped: {r1} vs {r2} -- the lock did not "
            "serialize the two processes"
        )


class TestWorktreeReplaceSigkilledHolderAutoReleases:
    def test_dead_holder_releases_the_lock_without_waiting_the_deadline(self, tmp_path):
        """The case the pre-refactor directory mutex spent ~40 lines of
        dead-PID/aged-mtime heuristics on: fcntl.flock releases automatically
        when its holder's process dies, including a SIGKILL, with no
        heuristic needed on the acquiring side."""
        repo = tmp_path / "repo"
        first_sha, _ = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)
        lock_path = Path(f"{worktree_dir}.lock")

        holder_script = _write_holder_script(tmp_path)
        holder = subprocess.Popen(
            [sys.executable, str(holder_script), str(lock_path), "60", str(SCRIPT_PATH.parent), str(SCRIPT_PATH)],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert holder.stdout.readline().strip() == "locked"
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=5)

        started = time.monotonic()
        result = _run_cli(repo, worktree_dir, first_sha, "session-a", 30)
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr
        assert elapsed < 25, f"a dead holder's lock must be reclaimed immediately, not waited out ({elapsed}s elapsed)"
        assert (worktree_dir / "file.txt").exists()


class TestWorktreeReplaceDeadlineExceeded:
    def test_live_holder_denies_at_the_deadline_without_touching_the_worktree(self, tmp_path):
        repo = tmp_path / "repo"
        first_sha, _ = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        worktree_dir.parent.mkdir(parents=True)
        lock_path = Path(f"{worktree_dir}.lock")

        holder_script = _write_holder_script(tmp_path)
        holder = subprocess.Popen(
            [sys.executable, str(holder_script), str(lock_path), "10", str(SCRIPT_PATH.parent), str(SCRIPT_PATH)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "locked"
            started = time.monotonic()
            result = _run_cli(repo, worktree_dir, first_sha, "session-a", 1)
            elapsed = time.monotonic() - started
            assert result.returncode != 0
            assert "could not acquire the worktree lock" in result.stderr
            assert elapsed < 5, f"a contended lock must be reported at the deadline, not hung ({elapsed}s elapsed)"
            assert not worktree_dir.exists(), "a lock-timeout abort must never touch the worktree"
        finally:
            holder.wait(timeout=15)
