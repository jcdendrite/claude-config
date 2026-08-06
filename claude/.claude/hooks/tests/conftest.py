"""Shared pytest fixtures auto-discovered across the hook test suite.

Only fixtures used by two or more test files live here. Class-local
fixtures stay with their class in the per-hook test file. `_seed_session`
is a plain helper function rather than a fixture (matching
scripts/tests/conftest.py's convention) since callers need to pass a
per-test session id, not a fixed injected value; import it directly with
`from conftest import _seed_session`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR


def _seed_session(home: Path, session_id: str, pid: int | None = None) -> None:
    """Write $HOME/.claude/sessions/<pid> in the two-line format
    capture-session-id.sh writes: the session id, then that pid's
    `TZ=UTC LC_ALL=C ps -o lstart=` start time. The start time is captured
    the same way the writer's `$(...)` does — trailing newline stripped,
    other trailing whitespace preserved — so a seeded entry round-trips
    through marker.sh's _walk_session comparison exactly like a real
    capture-session-id.sh write would.

    pid defaults to this test process's own pid: marker.sh resolves its
    session id by walking process ancestors, and when it runs as a
    subprocess of pytest, that walk reaches the pytest process itself.
    """
    target_pid = os.getpid() if pid is None else pid
    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(target_pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    (sessions_dir / str(target_pid)).write_text(f"{session_id}\n{start_time}\n")


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Sandbox $HOME so the hooks' marker files don't collide with real state."""
    home = tmp_path / "home"
    (home / ".claude" / "code-review-markers").mkdir(parents=True)
    hooks_dir = home / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "_lib.sh").symlink_to(HOOKS_DIR / "_lib.sh")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


@pytest.fixture
def git_repo(tmp_path):
    """Fresh git repo with one committed file and one staged change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\nsecond\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    return repo


@pytest.fixture
def opted_in_repo(tmp_path):
    """Git repo with .claude/worktree-required committed (opted into
    worktree enforcement)."""
    repo = tmp_path / "opted-in"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-required").write_text("# sentinel\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def stray_marker_repo(tmp_path):
    """Git repo with .claude/worktree-required present but NOT committed or
    staged — the GH-427 scenario. Enforcement still activates (existence-based
    check, unchanged), but the deny message should carry the stray-marker
    hint since a tracked-marker repo would not."""
    repo = tmp_path / "stray-marker"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-required").write_text("# stray, untracked\n")
    return repo


@pytest.fixture
def staged_marker_repo(tmp_path):
    """Git repo with .claude/worktree-required staged (git add) but not yet
    committed. _lib_stray_marker_hint uses `git ls-files --error-unmatch`,
    which succeeds for staged-not-committed files, not just committed ones —
    this fixture exercises that middle state so the hint's actual gate
    (index-tracked, not HEAD-committed) is what tests pin down."""
    repo = tmp_path / "staged-marker"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-required").write_text("# staged, not committed\n")
    subprocess.run(["git", "add", ".claude/worktree-required"], cwd=repo, check=True)
    return repo


@pytest.fixture
def non_opted_repo(tmp_path):
    """Git repo without the sentinel — enforcement should be a no-op."""
    repo = tmp_path / "non-opted"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def user_marker_home(isolated_home):
    """Sandboxed $HOME with ~/.claude/worktree-required present.
    Builds on isolated_home so $HOME is set to a temp dir; this fixture
    adds the machine-level marker file. The assertion verifies the write
    succeeded so a typo in the path cannot yield a silently-inert marker.
    """
    marker = isolated_home / ".claude" / "worktree-required"
    marker.write_text("# machine-level sentinel\n")
    assert marker.exists(), f"user_marker_home: marker not written at {marker}"
    return isolated_home


@pytest.fixture
def repo_with_optout(tmp_path):
    """Git repo with .claude/worktree-optout present (but no .claude/worktree-required).
    Used to verify opt-out is an inert modulator, not a trigger."""
    repo = tmp_path / "optout-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-optout").write_text("# opt-out\n")
    return repo


@pytest.fixture
def opted_in_with_worktree(opted_in_repo, tmp_path):
    """Opted-in repo with a linked worktree at a path that does NOT contain
    '/worktrees/' — verifies the hook's worktree check reads git-dir rather
    than pattern-matching the working-tree path."""
    wt_path = tmp_path / "feature-tree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt_path)],
        cwd=opted_in_repo,
        check=True,
    )
    return opted_in_repo, wt_path
