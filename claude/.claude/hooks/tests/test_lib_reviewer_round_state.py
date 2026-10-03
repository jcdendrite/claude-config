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

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bare_remote_with_default_branch,
    build_conflicted_rebase,
    build_conflicted_revert,
    push_conflicting_edit_to_origin,
    staged_diff_hash_at_base,
)

from .conftest import _DEFAULT_BRANCH_CANDIDATES, _review_ledger_path

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


def _state_value(repo: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **extra_env} if extra_env else None
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_reviewer_round_state_value "$1"', "_", str(repo)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _isolated_hooks_dir_missing_config_keys_psv(tmp_path: Path) -> Path:
    """Symlinks _lib.sh and its _config.sh sibling into an isolated hooks
    dir with no config-keys.psv of its own -- config-keys.psv is resolved
    relative to _config.sh's own directory (_CONFIG_SCHEMA_FILE in
    _config.sh), not CLAUDE_CONFIG_DIR, so reproducing a missing schema
    means isolating the hooks dir itself rather than the config dir.
    Mirrors test_config_lib.py's TestExitCodeContract.test_unreadable_schema_returns_exit_3
    isolation technique, extended to _lib.sh since
    _lib_reviewer_round_state_cap lives there, not in _config.sh."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_lib.sh").symlink_to(LIB_SH)
    (isolated_hooks_dir / "_config.sh").symlink_to(LIB_SH.parent / "_config.sh")
    return isolated_hooks_dir


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

    def test_unknown_diff_state_returns_1_with_no_stdout(self, tmp_path):
        """A failed or capped `git diff --cached` -- the exact call
        _lib_staged_diff_hash hashes, caught via its own `${PIPESTATUS[0]}`
        check on that call rather than a separate probe -- must not be
        stored as though it were a real round state: a later genuinely empty
        diff at the same HEAD would otherwise `grep -qFx` match a poisoned
        line. Forced with a stub `git` that fails only the hashed call's
        exact argument shape, so the real HEAD resolution and the
        no-in-progress-state gitdir check still succeed."""
        real_git = shutil.which("git")
        repo = tmp_path / "unknown-diff-state"
        _init_repo(repo)
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "git"
        stub.write_text(
            '#!/bin/bash\n'
            'if [ "$3" = "diff" ] && [ "$4" = "--cached" ]; then\n'
            '  exit 128\n'
            'fi\n'
            f'exec {real_git} "$@"\n'
        )
        stub.chmod(0o755)
        result = _state_value(repo, extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"})
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
        """Legacy fallback arm: no claude-config.toml row, so _config_value
        falls back to the raw legacy-file presence probe."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        (config_dir / ".round-consult-round2-pilot").touch()
        result = _state_cap(str(config_dir))
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_cap_is_one_with_toml_key_true_and_no_legacy_file(self, tmp_path):
        """TOML arm: round_consult_round2_pilot = true resolves the cap
        without any legacy sentinel file present."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        (config_dir / "claude-config.toml").write_text(
            "round_consult_round2_pilot = true\n"
        )
        result = _state_cap(str(config_dir))
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_default_cap_when_toml_key_false_overrides_legacy_file(self, tmp_path):
        """TOML-wins-over-legacy precedence: an explicit false in
        claude-config.toml overrides a stale legacy sentinel file."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        (config_dir / ".round-consult-round2-pilot").touch()
        (config_dir / "claude-config.toml").write_text(
            "round_consult_round2_pilot = false\n"
        )
        result = _state_cap(str(config_dir))
        assert result.returncode == 0
        assert result.stdout.strip() == "2"

    def test_default_cap_when_config_keys_psv_missing(self, tmp_path):
        """Pins the always-valid-integer contract when config-keys.psv
        itself is missing (interrupted stow-relink/git-pull), not merely an
        unreadable CLAUDE_CONFIG_DIR -- _config_enabled's exit 3 propagates
        through the `if` as false, so the cap resolves the safe default."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        isolated_hooks_dir = _isolated_hooks_dir_missing_config_keys_psv(tmp_path)
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        result = subprocess.run(
            ["bash", "-c", f'. "{isolated_hooks_dir / "_lib.sh"}"; _lib_reviewer_round_state_cap'],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "2"

    def test_default_cap_on_unresolvable_config_dir(self):
        """A relative CLAUDE_CONFIG_DIR fails _lib_config_dir's own
        resolution -- the cap must still print the default rather than
        leaving stdout empty."""
        result = _state_cap("relative/config/dir")
        assert result.returncode == 0
        assert result.stdout.strip() == "2"


def _make_git_rejecting_write_tree(bin_dir: Path) -> Path:
    """Simulates git < 2.38: `merge-tree --write-tree` is rejected outright.
    Every other subcommand proxies to the real git. Local copy of
    test_lib.py's shim of the same name (DAMP test code, per CLAUDE.md's
    named exception) -- this file's fallback assertion needs its own stub,
    not a shared import, per the plan's "per call site" mandate."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "--write-tree" ]; then\n'
        '    echo "error: unknown option \x60--write-tree\x60" >&2\n'
        '    exit 129\n'
        '  fi\n'
        'done\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _make_git_rejecting_merge_base_flag(bin_dir: Path) -> Path:
    """Simulates git 2.38-2.39: --write-tree is accepted but --merge-base=
    is rejected. Local copy of test_lib.py's shim of the same name."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    --merge-base=*)\n'
        '      echo "error: unknown option \x60--merge-base\x60" >&2\n'
        '      exit 129\n'
        '      ;;\n'
        '  esac\n'
        'done\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _merge_tree_base(repo: Path) -> str:
    """Local copy of test_require_code_review.py's helper of the same name
    (DAMP test code): independently computes the reference tree
    _lib_gate_diff_base's merge row computes, via the literal MERGE_HEAD OID
    (not the ref name -- git embeds a merge-tree argument's own textual form
    into the conflict marker label, which would compute a byte-different
    tree from production's `merge-tree --write-tree HEAD "$state_oid"`)."""
    merge_head_oid = (repo / ".git" / "MERGE_HEAD").read_text().strip()
    out = subprocess.run(
        ["git", "merge-tree", "--write-tree", "HEAD", merge_head_oid],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return out.strip().splitlines()[0]


def _build_conflicted_merge_via_origin(tmp_path: Path) -> Path:
    """Local copy of test_require_code_review.py's fixture of the same
    name: a conflicted merge whose MERGE_HEAD is trusted via the
    origin/<default> anchor, resolved and staged."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    (clone / "f").write_text("ours-edit\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits f"], cwd=clone, check=True)
    push_conflicting_edit_to_origin(tmp_path, bare, "f", "origin-edit\n")
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
    result = subprocess.run(
        ["git", "merge", "-q", "origin/main"], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (clone / ".git" / "MERGE_HEAD").exists()
    (clone / "f").write_text("resolved\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    return clone


def _round_state_empty_base_diff_hash(base: str) -> str:
    """Independent oracle for _lib_reviewer_round_state_value's empty-base-
    relative-diff binding: sha256("round-state-empty-base:<base>"), computed
    directly in Python rather than by calling the shell function under
    test."""
    return hashlib.sha256(f"round-state-empty-base:{base}".encode()).hexdigest()


def _build_forged_anchor_clean_merge(
    tmp_path: Path, name: str = "repo", payload_content: str = "payload\n"
) -> Path:
    """Local copy of test_require_code_review.py's fixture of the same name
    (DAMP test code): the forged-anchor + clean-merge attack -- builds M via
    `git commit-tree` (no `git commit` subprocess), forges
    refs/remotes/origin/main to point at M directly (plain plumbing -- no
    push, no fetch, no attacker infrastructure), then runs an ordinary `git
    merge --no-ff --no-commit` against that forged ref. The merge
    auto-stages M's payload file with no conflict, so the base-relative diff
    is empty even though the payload is genuinely novel content nobody
    reviewed. `payload_content` distinguishes independently-forged repos'
    trees (and therefore their resolved bases) from one another."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    head_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    (repo / "payload.txt").write_text(payload_content)
    subprocess.run(["git", "add", "payload.txt"], cwd=repo, check=True)
    tree_oid = subprocess.run(
        ["git", "write-tree"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    m_oid = subprocess.run(
        ["git", "commit-tree", tree_oid, "-p", head_oid, "-m", "forged"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Return the working tree to a clean HEAD state -- the merge below must
    # introduce payload.txt itself, not find it already sitting untracked.
    subprocess.run(["git", "reset", "--hard", "-q", "HEAD"], cwd=repo, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", m_oid], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", "-q", "refs/remotes/origin/main"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / ".git" / "MERGE_HEAD").exists()
    return repo


class TestLibReviewerRoundStateValueForgedAnchorEmptyBaseDiff:
    """Adversarial coverage for _lib_reviewer_round_state_value's own
    empty-diff branch, mirroring
    TestRequireCodeReviewForgedAnchorEmptyBaseDiff in
    test_require_code_review.py -- the same sha256("") collapse surface,
    reached through the round-state gate's own call site rather than the
    code-review marker's."""

    def test_two_different_forged_bases_produce_different_diff_hashes(self, tmp_path):
        """The sha256("") reuse concern: two independently-forged bases each
        landing on an empty base-relative diff must not collapse to the same
        diff-hash half -- that collapse is exactly what would let a
        round-state entry recorded against one forged base be replayed
        against a different, independently-forged one."""
        repo1 = _build_forged_anchor_clean_merge(tmp_path, name="repo1", payload_content="payload-1\n")
        repo2 = _build_forged_anchor_clean_merge(tmp_path, name="repo2", payload_content="payload-2\n")
        base1 = _merge_tree_base(repo1)
        base2 = _merge_tree_base(repo2)
        assert base1 != base2, "fixture must produce two distinct forged bases"

        result1 = _state_value(repo1)
        result2 = _state_value(repo2)
        assert result1.returncode == 0
        assert result2.returncode == 0
        _, diff_hash1 = result1.stdout.split(" ", 1)
        _, diff_hash2 = result2.stdout.split(" ", 1)
        assert diff_hash1 != diff_hash2
        assert diff_hash1 != hashlib.sha256(b"").hexdigest()
        assert diff_hash2 != hashlib.sha256(b"").hexdigest()

    def test_value_matches_independent_oracle_for_empty_base_relative_diff(self, tmp_path):
        repo = _build_forged_anchor_clean_merge(tmp_path)
        base = _merge_tree_base(repo)
        result = _state_value(repo)
        assert result.returncode == 0
        head_sha, diff_hash = result.stdout.split(" ", 1)
        expected_head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert head_sha == expected_head_sha
        assert diff_hash == _round_state_empty_base_diff_hash(base)


class TestLibReviewerRoundStateValueMergeAwareBase:
    """_lib_reviewer_round_state_value's diff-hash half routes through
    _lib_gate_diff_base/_lib_staged_diff_hash, so a mid-merge round state
    covers the resolution only. Pinned against an independent oracle rather
    than this file's usual relational-only convention (both sides calling
    the same function), because a relational-only check cannot catch a bug
    in a shared primitive both sides would inherit identically.

    These tests exercise the shared function directly rather than through
    both call sites (log-reviewer-round.sh, require-architect-consult.sh),
    because both hook scripts invoke it identically today with no
    per-caller divergence to catch -- if either script gains caller-specific
    pre/post-processing around the call, add a direct test at that point."""

    def test_mid_merge_value_matches_independent_oracle_on_both_halves(self, tmp_path):
        repo = _build_conflicted_merge_via_origin(tmp_path)
        result = _state_value(repo)
        assert result.returncode == 0
        head_sha, diff_hash = result.stdout.split(" ", 1)

        expected_head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert head_sha == expected_head_sha

        base = _merge_tree_base(repo)
        assert diff_hash == staged_diff_hash_at_base(repo, base)

    def test_mid_merge_key_stays_armed_head_attached(self, tmp_path):
        """HEAD stays attached through a merge, so the round-3 consult gate
        stays armed -- asserted directly rather than assumed."""
        repo = _build_conflicted_merge_via_origin(tmp_path)
        result = _state_key(repo)
        assert result.returncode == 0
        assert result.stdout != ""

    def test_mid_merge_value_recorded_still_matches_while_merge_in_progress(self, tmp_path):
        """A value recorded mid-merge still matches on a later read while
        that same merge is in progress -- no intervening state change moves
        it out from under a read that follows a write in the same window."""
        repo = _build_conflicted_merge_via_origin(tmp_path)
        recorded = _state_value(repo)
        read_again = _state_value(repo)
        assert recorded.returncode == 0
        assert read_again.returncode == 0
        assert recorded.stdout == read_again.stdout

    def test_mid_rebase_key_disarmed_empty_stdout(self, tmp_path):
        """Pinned as asserted behavior: mid-rebase, detached HEAD means
        _lib_reviewer_round_state_key returns non-zero with empty stdout,
        and the base substitution in the value's diff-hash half does not
        reach this branch-half gap."""
        repo = tmp_path / "rebase-key-disarmed"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        build_conflicted_rebase(repo)
        result = _state_key(repo)
        assert result.returncode != 0
        assert result.stdout == ""

    def test_fallback_write_tree_rejected_behaves_like_today(self, tmp_path):
        """`--write-tree` outright rejection (git < 2.38) must fall back to
        exactly today's plain HEAD-relative diff-hash recipe rather than
        hanging or erroring the round-state value."""
        repo = _build_conflicted_merge_via_origin(tmp_path)
        bin_dir = tmp_path / "bin-fallback-write-tree"
        _make_git_rejecting_write_tree(bin_dir)
        extra_env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        result = _state_value(repo, extra_env=extra_env)
        assert result.returncode == 0, result.stderr
        head_sha, diff_hash = result.stdout.split(" ", 1)
        expected_head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert head_sha == expected_head_sha
        expected_diff = subprocess.run(
            ["git", "diff", "--cached"], cwd=repo, capture_output=True, check=True
        ).stdout
        assert diff_hash == hashlib.sha256(expected_diff).hexdigest()

    def test_fallback_merge_base_flag_rejected_behaves_like_today(self, tmp_path):
        """The `--merge-base=` rejection band (git 2.38-2.39) only affects
        the three states whose merge-tree call passes that flag -- rebase,
        cherry-pick, revert, not merge -- so this needs its own fixture: a
        conflicted revert, trusted via the HEAD anchor by construction."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        build_conflicted_revert(repo)
        bin_dir = tmp_path / "bin-fallback-merge-base"
        _make_git_rejecting_merge_base_flag(bin_dir)
        extra_env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        result = _state_value(repo, extra_env=extra_env)
        assert result.returncode == 0, result.stderr
        head_sha, diff_hash = result.stdout.split(" ", 1)
        expected_head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert head_sha == expected_head_sha
        expected_diff = subprocess.run(
            ["git", "diff", "--cached"], cwd=repo, capture_output=True, check=True
        ).stdout
        assert diff_hash == hashlib.sha256(expected_diff).hexdigest()


def _ledger_path_resolution(config_dir: Path, repo: Path | str, session_id: str) -> subprocess.CompletedProcess:
    """Shell out to the real _lib_review_ledger_path."""
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_review_ledger_path "$1" "$2" "$3"', "_", str(config_dir), str(repo), session_id],
        capture_output=True,
        text=True,
        check=False,
    )


def _set_origin_head(repo: Path, default_branch: str) -> None:
    """Point refs/remotes/origin/HEAD at origin/<default_branch> without a
    real remote, the same local-refs-only setup test_lib.py's default-branch
    tests use."""
    subprocess.run(["git", "update-ref", f"refs/remotes/origin/{default_branch}", "HEAD"], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{default_branch}"],
        cwd=repo, check=True,
    )


class TestLibReviewLedgerPath:
    """The scope matrix for _lib_review_ledger_path, tested once here. The
    expected path comes from conftest's independent oracle, and each case
    also pins its expected scope word."""

    SESSION_ID = "lib-test-session"

    def _resolve(self, tmp_path: Path, repo: Path) -> tuple[str, str]:
        result = _ledger_path_resolution(tmp_path / "config dir", repo, self.SESSION_ID)
        assert result.returncode == 0, result.stderr
        scope, _, path = result.stdout.partition(" ")
        return scope, path

    def _expected_path(self, tmp_path: Path, repo: Path) -> str:
        # The oracle builds <home>/.claude/review-narrative-ledger/...; the
        # function takes the config dir directly, so map the two.
        oracle_path = _review_ledger_path(tmp_path, repo, self.SESSION_ID)
        return str(tmp_path / "config dir" / "review-narrative-ledger" / oracle_path.name)

    def test_feature_branch_with_origin_head_set_is_branch_scope(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")
        _set_origin_head(repo, "main")
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "branch"
        assert path == self._expected_path(tmp_path, repo)
        assert Path(path).name == f"{_state_key(repo).stdout}.jsonl"

    def test_default_branch_named_by_origin_head_is_session_scope(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="trunk")
        _set_origin_head(repo, "trunk")

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "session"
        assert path == self._expected_path(tmp_path, repo)
        assert Path(path).name.endswith(f".{self.SESSION_ID}.jsonl")

    def test_detached_head_is_session_scope(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "session"
        assert path == self._expected_path(tmp_path, repo)

    @pytest.mark.parametrize("default_named_branch", ["main", "master", "develop"])
    def test_no_origin_on_a_candidate_named_branch_is_session_scope(self, tmp_path, default_named_branch):
        repo = tmp_path / "repo"
        _init_repo(repo, branch=default_named_branch)

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "session"
        assert path == self._expected_path(tmp_path, repo)

    def test_no_origin_on_a_feature_branch_is_branch_scope(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="feature")

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "branch"
        assert path == self._expected_path(tmp_path, repo)

    def test_candidate_named_branch_is_branch_scope_when_origin_head_names_another_default(self, tmp_path):
        """The documented residual: only origin/HEAD's own answer decides
        once it resolves, so a local branch named `main` is branch-keyed
        when the remote's default is `trunk`."""
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")
        _set_origin_head(repo, "trunk")

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "branch"
        assert path == self._expected_path(tmp_path, repo)

    def test_config_dir_containing_a_space_still_splits_into_scope_and_path(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="feature")

        scope, path = self._resolve(tmp_path, repo)

        assert " " in path
        assert path.startswith(str(tmp_path / "config dir"))
        assert scope == "branch"

    def test_empty_repo_root_returns_1_with_no_stdout(self, tmp_path):
        result = _ledger_path_resolution(tmp_path / "config dir", "", self.SESSION_ID)

        assert result.returncode == 1
        assert result.stdout == ""

    def test_default_branch_names_are_exactly_the_candidates_default_branch_probing_resolves(self, tmp_path):
        """The resolver's fallback name list is a copy of the one
        _lib_default_branch_or_guess probes. Probing a universe of names
        with only origin/<name> present checks both directions: every name
        in the list resolves, and no other name in the universe does."""
        names = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; printf "%s\\n" "${{_LIB_REVIEW_LEDGER_DEFAULT_BRANCH_NAMES[@]}}"'],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        decoy_names = ["trunk", "dev", "development", "release", "production", "stable", "next", "default"]
        assert names, "the resolver's default-branch name list is empty"
        assert tuple(names) == _DEFAULT_BRANCH_CANDIDATES, "conftest's oracle list differs from the resolver's"
        assert not set(names) & set(decoy_names)
        for index, name in enumerate(names + decoy_names):
            repo = tmp_path / f"probe-{index}"
            _init_repo(repo, branch=name)
            subprocess.run(["git", "update-ref", f"refs/remotes/origin/{name}", "HEAD"], cwd=repo, check=True)
            resolved = subprocess.run(
                ["bash", "-c", f'. "{LIB_SH}"; _lib_default_branch_or_guess "$1"', "_", str(repo)],
                capture_output=True, text=True, check=False,
            )
            if name in names:
                assert resolved.stdout == name, f"_lib_default_branch_or_guess does not resolve {name!r}"
            else:
                assert resolved.returncode == 1, (
                    f"_lib_default_branch_or_guess resolves {name!r}, absent from the resolver's list"
                )

    def test_slash_and_dash_branch_names_get_distinct_flat_files(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")
        paths = {}
        for branch in ("a/b", "a-b"):
            subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True)
            scope, paths[branch] = self._resolve(tmp_path, repo)
            assert scope == "branch"

        assert paths["a/b"] != paths["a-b"]
        ledger_dir = tmp_path / "config dir" / "review-narrative-ledger"
        assert all(Path(path).parent == ledger_dir for path in paths.values())

    def test_linked_worktree_is_keyed_by_its_own_toplevel_and_branch(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")
        linked_worktree = tmp_path / "linked"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "feature", str(linked_worktree)], cwd=repo, check=True)

        scope, path = self._resolve(tmp_path, linked_worktree)
        _main_scope, main_checkout_path = self._resolve(tmp_path, repo)

        assert scope == "branch"
        assert path == self._expected_path(tmp_path, linked_worktree)
        assert Path(path).name == f"{_state_key(linked_worktree).stdout}.jsonl"
        assert Path(path).name.split(".")[0] != Path(main_checkout_path).name.split(".")[0]

    def test_unborn_head_on_a_feature_branch_is_branch_scope(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "feature"], cwd=repo, check=True)

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "branch"
        assert path == self._expected_path(tmp_path, repo)

    def test_unborn_head_on_main_is_session_scope(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)

        scope, path = self._resolve(tmp_path, repo)

        assert scope == "session"
        assert path == self._expected_path(tmp_path, repo)

    @pytest.mark.parametrize(
        ("branch", "expected_scope"), [("main", "session"), ("trunk", "branch")],
    )
    def test_dangling_origin_head_falls_through_to_the_candidate_names(self, tmp_path, branch, expected_scope):
        """origin/HEAD names a branch whose remote-tracking ref is absent, so
        it does not resolve and only the candidate-name fallback decides."""
        repo = tmp_path / "repo"
        _init_repo(repo, branch=branch)
        subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"],
            cwd=repo, check=True,
        )

        scope, path = self._resolve(tmp_path, repo)

        assert scope == expected_scope
        assert path == self._expected_path(tmp_path, repo)

    def test_git_failure_reading_head_returns_1_instead_of_downgrading_to_session_scope(self, tmp_path):
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        result = _ledger_path_resolution(tmp_path / "config dir", not_a_repo, self.SESSION_ID)

        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("bad_session_id", ["../escape", "a/b", "", "has space"])
    def test_invalid_session_id_returns_1_in_session_scope(self, tmp_path, bad_session_id):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")

        result = _ledger_path_resolution(tmp_path / "config dir", repo, bad_session_id)

        assert result.returncode == 1
        assert result.stdout == ""

    def test_session_id_is_not_consulted_in_branch_scope(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="feature")

        result = _ledger_path_resolution(tmp_path / "config dir", repo, "")

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("branch ")


class TestLibReviewLedgerSessionPath:
    def _session_path(self, config_dir: Path, repo: Path, session_id: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "bash", "-c", f'. "{LIB_SH}"; _lib_review_ledger_session_path "$1" "$2" "$3"',
                "_", str(config_dir), str(repo), session_id,
            ],
            capture_output=True, text=True, check=False,
        )

    def test_prints_the_repo_hash_and_session_id_keyed_file(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")

        result = self._session_path(tmp_path / "config dir", repo, "session-1")

        assert result.returncode == 0, result.stderr
        expected_name = _review_ledger_path(tmp_path, repo, "session-1").name
        assert result.stdout == str(tmp_path / "config dir" / "review-narrative-ledger" / expected_name)

    @pytest.mark.parametrize("bad_session_id", ["../escape", "a/b", "", "has space", "dot.ted"])
    def test_rejects_a_session_id_that_is_not_a_path_component(self, tmp_path, bad_session_id):
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")

        result = self._session_path(tmp_path / "config dir", repo, bad_session_id)

        assert result.returncode == 1
        assert result.stdout == ""

    def test_rejects_an_empty_repo_root(self, tmp_path):
        result = self._session_path(tmp_path / "config dir", "", "session-1")

        assert result.returncode == 1
        assert result.stdout == ""
