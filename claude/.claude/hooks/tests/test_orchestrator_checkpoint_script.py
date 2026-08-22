"""Tests for claude/.claude/scripts/orchestrator-checkpoint.sh."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR, git_toplevel

ORCHESTRATOR_CHECKPOINT_SCRIPT = SCRIPTS_DIR / "orchestrator-checkpoint.sh"

RUN_ID = "code-review-my-branch-1700000000-abcd1234"


def _run(
    args: list[str], cwd, home, extra_env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(ORCHESTRATOR_CHECKPOINT_SCRIPT)] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _popen(args: list[str], cwd, home) -> subprocess.Popen:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.Popen(
        ["bash", str(ORCHESTRATOR_CHECKPOINT_SCRIPT)] + args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _checkpoint_path(home: Path, repo: Path, run_id: str = RUN_ID) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return home / ".claude" / "orchestrator-checkpoints" / f"{repo_hash}.{run_id}.jsonl"


def _append_args(
    run_id: str = RUN_ID,
    step: str = "skill-invoked",
    status: str = "done",
    marker_hash: str | None = None,
) -> list[str]:
    args = ["append", run_id, "--step", step, "--status", status]
    if marker_hash is not None:
        args += ["--marker-hash", marker_hash]
    return args


class TestOrchestratorCheckpointAppendReadRoundTrip:
    def test_append_then_read_returns_the_entry(self, isolated_home, git_repo):
        result = _run(
            _append_args(marker_hash="deadbeef"), cwd=git_repo, home=isolated_home
        )
        assert result.returncode == 0, result.stderr

        read_result = _run(["read", RUN_ID], cwd=git_repo, home=isolated_home)
        assert read_result.returncode == 0, read_result.stderr
        record = json.loads(read_result.stdout.splitlines()[0])
        assert record == {
            "step": "skill-invoked",
            "status": "done",
            "marker_hash": "deadbeef",
        }

    def test_append_defaults_marker_hash_to_n_a(self, isolated_home, git_repo):
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        checkpoint = _checkpoint_path(isolated_home, git_repo)
        record = json.loads(checkpoint.read_text().splitlines()[0])
        assert record["marker_hash"] == "n/a"

    def test_missing_step_rejected(self, isolated_home, git_repo):
        args = ["append", RUN_ID, "--status", "done"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _checkpoint_path(isolated_home, git_repo).exists()

    def test_missing_status_rejected(self, isolated_home, git_repo):
        args = ["append", RUN_ID, "--step", "skill-invoked"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _checkpoint_path(isolated_home, git_repo).exists()

    def test_unknown_subcommand_rejected(self, isolated_home, git_repo):
        result = _run(["forge", RUN_ID], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2


class TestOrchestratorCheckpointFieldCapsRejectOverLength:
    """Pins the _CHECKPOINT_*_MAX_CHARS guards: an over-length field is
    rejected rather than silently truncated, which would corrupt the
    resumability this checkpoint exists to preserve."""

    def test_over_length_step_rejected(self, isolated_home, git_repo):
        args = ["append", RUN_ID, "--step", "x" * 201, "--status", "done"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _checkpoint_path(isolated_home, git_repo).exists()

    def test_over_length_status_rejected(self, isolated_home, git_repo):
        args = ["append", RUN_ID, "--step", "skill-invoked", "--status", "x" * 101]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _checkpoint_path(isolated_home, git_repo).exists()

    def test_over_length_marker_hash_rejected(self, isolated_home, git_repo):
        args = _append_args(marker_hash="x" * 129)
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _checkpoint_path(isolated_home, git_repo).exists()


class TestOrchestratorCheckpointNoCheckpointYet:
    def test_read_reports_absence_for_a_brand_new_run_id(self, isolated_home, git_repo):
        result = _run(["read", "brand-new-run-id-0000"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert "no checkpoint" in result.stdout.lower()

    def test_read_writes_nothing_for_a_brand_new_run_id(self, isolated_home, git_repo):
        _run(["read", "brand-new-run-id-0000"], cwd=git_repo, home=isolated_home)
        checkpoint_dir = isolated_home / ".claude" / "orchestrator-checkpoints"
        stray = list(checkpoint_dir.iterdir()) if checkpoint_dir.exists() else []
        assert stray == [], f"read must never write a checkpoint file: {stray}"


class TestOrchestratorCheckpointRunIdTraversalGuard:
    """_validate_run_id reuses _lib_valid_session_id_component to reject a
    traversal-shaped orchestrator_run_id before it is concatenated into
    CHECKPOINT_FILE -- end-to-end regression coverage for that guard, since
    test_lib.py's convention test only proves the guard is called, not that
    it actually blocks a traversal payload here."""

    def test_traversal_run_id_rejected_on_append(self, isolated_home, git_repo):
        result = _run(_append_args(run_id="../../etc/passwd"), cwd=git_repo, home=isolated_home)
        assert result.returncode != 0
        checkpoint_dir = isolated_home / ".claude" / "orchestrator-checkpoints"
        stray = list(checkpoint_dir.rglob("*")) if checkpoint_dir.exists() else []
        assert stray == [], f"a traversal-shaped run id must not write outside CHECKPOINT_DIR: {stray}"
        assert not (isolated_home / "etc" / "passwd").exists()

    def test_traversal_run_id_rejected_on_read(self, isolated_home, git_repo):
        result = _run(["read", "../../etc/passwd"], cwd=git_repo, home=isolated_home)
        assert result.returncode != 0
        checkpoint_dir = isolated_home / ".claude" / "orchestrator-checkpoints"
        stray = list(checkpoint_dir.rglob("*")) if checkpoint_dir.exists() else []
        assert stray == [], f"a traversal-shaped run id must not write outside CHECKPOINT_DIR: {stray}"


class TestOrchestratorCheckpointDuplicateStepRetry:
    def test_identical_retry_for_the_same_step_is_deduped(self, isolated_home, git_repo):
        """A resumed orchestrator re-emitting the identical step/status/
        marker-hash triple (e.g. re-appending 'started' after a crash before
        it could confirm the append landed) is a no-op, not a duplicate."""
        _run(_append_args(status="started"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(status="started"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _checkpoint_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 1, f"identical repeat append must be a no-op, got: {lines}"

    def test_progression_from_started_to_done_appends_a_new_line(self, isolated_home, git_repo):
        """The same step moving from 'started' to 'done' is genuine
        progression, not noise to dedup away."""
        _run(_append_args(status="started"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(status="done"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _checkpoint_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 2, (
            f"a changed status for the same step must append a new line rather "
            f"than dedup, got: {lines}"
        )
        statuses = {json.loads(line)["status"] for line in lines}
        assert statuses == {"started", "done"}


class TestOrchestratorCheckpointCorruptLine:
    def test_read_survives_a_truncated_jsonl_line_from_a_kill_mid_write(
        self, isolated_home, git_repo
    ):
        """A kill mid-write can leave a truncated final line. read is a plain
        passthrough (like review-ledger.sh's show), so it must still exit 0
        and surface every line -- including the truncated one -- for the
        orchestrator's own resume logic to reason about, rather than crashing
        or silently dropping content."""
        checkpoint = _checkpoint_path(isolated_home, git_repo)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(
            '{"step": "skill-invoked", "status": "done", "marker_hash": "n/a"}\n'
            '{"step": "reviewer:ciso-reviewer", "status": "star'  # truncated mid-write
        )

        result = _run(["read", RUN_ID], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["step"] == "skill-invoked"
        with pytest.raises(json.JSONDecodeError):
            json.loads(lines[1])


class TestOrchestratorCheckpointStaleSweep:
    def _make_stale(self, path: Path, content: str = '{"step":"stale"}\n') -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        thirty_one_days_ago = time.time() - 31 * 24 * 60 * 60
        os.utime(path, (thirty_one_days_ago, thirty_one_days_ago))

    def test_append_sweeps_a_stale_jsonl_from_an_abandoned_run(self, isolated_home, git_repo):
        """The sweep runs directory-wide, not scoped to the invoking run's own
        file -- an abandoned run's checkpoint would otherwise never get swept."""
        checkpoint_dir = isolated_home / ".claude" / "orchestrator-checkpoints"
        repo_hash = hashlib.sha256(git_toplevel(git_repo).encode()).hexdigest()
        abandoned = checkpoint_dir / f"{repo_hash}.abandoned-run-1600000000-dead0000.jsonl"
        self._make_stale(abandoned)

        _run(_append_args(run_id="fresh-run-1700000000-cafe0000"), cwd=git_repo, home=isolated_home)

        assert not abandoned.exists(), (
            "append's directory-wide sweep must remove a stale checkpoint "
            "from an abandoned run"
        )

    def test_append_sweeps_stale_lock_files(self, isolated_home, git_repo):
        checkpoint_dir = isolated_home / ".claude" / "orchestrator-checkpoints"
        repo_hash = hashlib.sha256(git_toplevel(git_repo).encode()).hexdigest()
        stale_lock = checkpoint_dir / f"{repo_hash}.abandoned-run-1600000000-dead0000.jsonl.lock"
        self._make_stale(stale_lock, content="")

        _run(_append_args(run_id="fresh-run-1700000000-cafe0000"), cwd=git_repo, home=isolated_home)

        assert not stale_lock.exists()

    def test_append_does_not_sweep_a_fresh_checkpoint(self, isolated_home, git_repo):
        checkpoint_dir = isolated_home / ".claude" / "orchestrator-checkpoints"
        repo_hash = hashlib.sha256(git_toplevel(git_repo).encode()).hexdigest()
        fresh = checkpoint_dir / f"{repo_hash}.other-run-1700000000-beef0000.jsonl"
        fresh.parent.mkdir(parents=True, exist_ok=True)
        fresh.write_text('{"step":"fresh"}\n')

        _run(_append_args(run_id="another-run-1700000001-f00d0000"), cwd=git_repo, home=isolated_home)

        assert fresh.exists(), "append's sweep must not remove a checkpoint younger than 30 days"


class TestOrchestratorCheckpointLocking:
    def test_lock_released_after_successful_append(self, isolated_home, git_repo):
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        lock_file = _checkpoint_path(isolated_home, git_repo).with_suffix(".jsonl.lock")
        assert not lock_file.exists(), "the lock file must be removed after a successful append"

    def test_concurrent_appends_with_identical_content_produce_no_corruption(
        self, isolated_home, git_repo
    ):
        """Two racing appends of the identical step/status/marker-hash triple
        may produce a duplicate line (a low-consequence outcome) but must
        never corrupt the file -- every resulting line must parse."""
        procs = [_popen(_append_args(), cwd=git_repo, home=isolated_home) for _ in range(2)]
        for proc in procs:
            proc.communicate(timeout=10)

        checkpoint = _checkpoint_path(isolated_home, git_repo)
        lines = checkpoint.read_text().splitlines()
        assert len(lines) in (1, 2), f"expected 1 (deduped) or 2 (raced) lines, got: {lines}"
        for line in lines:
            record = json.loads(line)  # raises if a raced write corrupted the line
            assert record["step"] == "skill-invoked"

    def test_concurrent_appends_with_distinct_steps_both_land(self, isolated_home, git_repo):
        """A racing append that loses the lock must still fall through to an
        unlocked append rather than silently dropping the write."""
        procs = [
            _popen(_append_args(step="reviewer:staff-backend-engineer"), cwd=git_repo, home=isolated_home),
            _popen(_append_args(step="reviewer:ciso-reviewer"), cwd=git_repo, home=isolated_home),
        ]
        for proc in procs:
            proc.communicate(timeout=10)

        checkpoint = _checkpoint_path(isolated_home, git_repo)
        steps = {json.loads(line)["step"] for line in checkpoint.read_text().splitlines()}
        assert steps == {"reviewer:staff-backend-engineer", "reviewer:ciso-reviewer"}


class TestOrchestratorCheckpointRepoHashScoping:
    def test_checkpoint_in_one_worktree_is_not_visible_from_a_sibling_worktree(
        self, isolated_home, git_repo
    ):
        """Two worktrees of the same repo resolve to different absolute
        toplevel paths, hence different repo-hashes -- a checkpoint written
        from one must not be readable from the other, even under the
        identical orchestrator_run_id."""
        _run(_append_args(), cwd=git_repo, home=isolated_home)

        worktree = git_repo.parent / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "orchestrator-checkpoint-test-branch", str(worktree)],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        read_result = _run(["read", RUN_ID], cwd=worktree, home=isolated_home)

        assert read_result.returncode == 0, read_result.stderr
        assert "no checkpoint" in read_result.stdout.lower(), (
            "a checkpoint written from the main tree must not be visible "
            "when read from a linked worktree of the same repo"
        )
