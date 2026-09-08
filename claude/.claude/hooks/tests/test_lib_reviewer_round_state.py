"""Tests for _lib.sh's _lib_reviewer_round_state_key and
_lib_reviewer_round_state_value (round3-review-consult-trigger plan).
Relational assertions only -- never a golden sha256 literal -- mirroring
test_marker_lib.py's TestLibActivePlanHash precedent for
_lib_active_plan_hash: the exact digest recipe is free to evolve as long as
the read side (require-architect-consult.sh) and write side
(log-reviewer-round.sh) agree, which these tests pin by calling the
functions directly rather than through either hook.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from helpers import HOOKS_DIR

LIB_SH = HOOKS_DIR / "_lib.sh"


def _init_repo(repo: Path, branch: str = "main") -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("first\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _state_key(repo: Path) -> subprocess.CompletedProcess:
    """Shell out to the real _lib_reviewer_round_state_key -- a fresh bash
    subprocess each call, so a "repeat calls agree" assertion exercises two
    genuinely independent invocations, not a cached result."""
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_reviewer_round_state_key "$1"', "_", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def _state_value(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_reviewer_round_state_value "$1"', "_", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def _state_cap(config_dir: str | None) -> subprocess.CompletedProcess:
    """Shell out to the real _lib_reviewer_round_state_cap with
    CLAUDE_CONFIG_DIR set to config_dir (or unset when None) -- isolates
    from any ambient CLAUDE_CONFIG_DIR the real environment carries, per
    helpers._build_subprocess_env's documented caveat."""
    env = dict(os.environ)
    if config_dir is None:
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_reviewer_round_state_cap'],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestLibReviewerRoundStateKey:
    def test_deterministic_across_repeat_calls(self, tmp_path):
        repo = tmp_path / "repeat-key"
        _init_repo(repo)
        first = _state_key(repo)
        second = _state_key(repo)
        assert first.returncode == 0
        assert second.returncode == 0
        assert first.stdout == second.stdout
        assert first.stdout != ""

    def test_differs_for_different_branch(self, tmp_path):
        repo = tmp_path / "diff-branch"
        _init_repo(repo, branch="main")
        main_key = _state_key(repo)
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
        feature_key = _state_key(repo)
        assert main_key.returncode == 0
        assert feature_key.returncode == 0
        assert main_key.stdout != feature_key.stdout

    def test_differs_for_different_repo_same_branch_name(self, tmp_path):
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _init_repo(repo_a, branch="main")
        _init_repo(repo_b, branch="main")
        key_a = _state_key(repo_a)
        key_b = _state_key(repo_b)
        assert key_a.returncode == 0
        assert key_b.returncode == 0
        assert key_a.stdout != key_b.stdout

    def test_stable_across_staged_changes(self, tmp_path):
        """The key is branch-scoped, not diff-scoped -- staging a change
        must not move it (that is _lib_reviewer_round_state_value's job)."""
        repo = tmp_path / "stable-key"
        _init_repo(repo)
        before = _state_key(repo)
        (repo / "f.txt").write_text("first\nsecond\n")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
        after = _state_key(repo)
        assert before.returncode == 0
        assert after.returncode == 0
        assert before.stdout == after.stdout

    def test_empty_on_detached_head(self, tmp_path):
        repo = tmp_path / "detached-key"
        _init_repo(repo)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", sha], cwd=repo, check=True)
        result = _state_key(repo)
        assert result.returncode != 0
        assert result.stdout == ""

    def test_empty_on_empty_repo_root_argument(self):
        result = _state_key("")
        assert result.returncode != 0
        assert result.stdout == ""


class TestLibReviewerRoundStateValue:
    def test_deterministic_across_repeat_calls(self, tmp_path):
        repo = tmp_path / "repeat-value"
        _init_repo(repo)
        first = _state_value(repo)
        second = _state_value(repo)
        assert first.returncode == 0
        assert second.returncode == 0
        assert first.stdout == second.stdout
        assert first.stdout != ""

    def test_differs_after_commit(self, tmp_path):
        """A committed change moves the head-sha half of the pair, even
        though the staged diff resets to empty."""
        repo = tmp_path / "value-after-commit"
        _init_repo(repo)
        before = _state_value(repo)
        (repo / "f.txt").write_text("first\nsecond\n")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "wip"], cwd=repo, check=True)
        after = _state_value(repo)
        assert before.returncode == 0
        assert after.returncode == 0
        assert before.stdout != after.stdout

    def test_differs_for_different_staged_diff_same_head(self, tmp_path):
        repo = tmp_path / "value-different-diff"
        _init_repo(repo)
        (repo / "f.txt").write_text("first\nsecond\n")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
        value_a = _state_value(repo)
        (repo / "f.txt").write_text("first\nthird\n")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
        value_b = _state_value(repo)
        assert value_a.returncode == 0
        assert value_b.returncode == 0
        assert value_a.stdout != value_b.stdout

    def test_empty_when_no_commits_yet(self, tmp_path):
        repo = tmp_path / "no-commits"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        result = _state_value(repo)
        assert result.returncode != 0
        assert result.stdout == ""

    def test_empty_on_empty_repo_root_argument(self):
        result = _state_value("")
        assert result.returncode != 0
        assert result.stdout == ""


class TestLibReviewerRoundStateKeyValueIndependence:
    """Cross-agreement between the key and value recipes, independent of
    either hook: the key (branch-scoped) and value (head+diff-scoped) must
    vary on different axes, or the round-state file's whole "one line per
    reviewed state, capped at 2" design would conflate a new commit on the
    SAME branch with a genuinely different branch."""

    def test_staging_a_change_moves_value_but_not_key(self, tmp_path):
        repo = tmp_path / "independence"
        _init_repo(repo)
        key_before = _state_key(repo)
        value_before = _state_value(repo)
        (repo / "f.txt").write_text("first\nchanged\n")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
        key_after = _state_key(repo)
        value_after = _state_value(repo)
        assert key_before.stdout == key_after.stdout
        assert value_before.stdout != value_after.stdout


class TestLibReviewerRoundStateCap:
    """Contract for _lib_reviewer_round_state_cap, consumed by both hooks
    via `$(...)` into an integer comparison. Must always print a valid
    integer to stdout and never fail silently."""

    def test_default_cap_without_pilot_sentinel(self, tmp_path):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        result = _state_cap(str(config_dir))
        assert result.returncode == 0
        assert result.stdout.strip() == "2"

    def test_cap_is_one_with_pilot_sentinel_present(self, tmp_path):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        (config_dir / ".round-consult-round2-pilot").touch()
        result = _state_cap(str(config_dir))
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_default_cap_on_unresolvable_config_dir(self):
        """A relative CLAUDE_CONFIG_DIR fails _lib_config_dir's own
        resolution -- the cap must still print the default rather than
        leaving stdout empty."""
        result = _state_cap("relative/config/dir")
        assert result.returncode == 0
        assert result.stdout.strip() == "2"
