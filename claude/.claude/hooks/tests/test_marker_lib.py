"""Tests for _lib.sh shared helper library."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

LIB_SH = HOOKS_DIR / "_lib.sh"


def _run_lib_fn(fn_call: str) -> str:
    """Source _lib.sh in a bash subprocess and evaluate fn_call."""
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; {fn_call}'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _advance_offset(
    transcript: Path, offset: int, current_size: int, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Shell out to the real _lib_advance_offset_past_complete_lines."""
    return subprocess.run(
        [
            "bash", "-c",
            f'. "{LIB_SH}"; _lib_advance_offset_past_complete_lines "$1" "$2" "$3"',
            "_advance_offset", str(transcript), str(offset), str(current_size),
        ],
        capture_output=True,
        text=True,
        env=env if env is not None else os.environ,
    )


def _active_plan_hash(repo: Path, env_overrides: dict | None = None) -> str:
    """Shell out to the real _lib_active_plan_hash against `repo`."""
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_active_plan_hash "$1"', "_active_plan_hash", str(repo)],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(env_overrides or {})},
    )
    return result.stdout.strip()


def _find_case_insensitive_collation_locale() -> str | None:
    """Return an installed locale whose collation interleaves upper- and
    lowercase (so `sort` orders `B.md`/`a.md` differently than the C locale
    does), or None if the machine only has C/POSIX available."""
    try:
        installed = subprocess.run(
            ["locale", "-a"], capture_output=True, text=True, check=True
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None

    for candidate in installed:
        if candidate.lower().replace("-", "") in ("c", "posix", "c.utf8"):
            continue
        ordering = subprocess.run(
            ["bash", "-c", 'printf "B\\na\\n" | sort'],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": candidate},
        )
        if ordering.returncode == 0 and ordering.stdout == "a\nB\n":
            return candidate
    return None


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


class TestMarkerLibRepoHash:
    def test_known_path_matches_python_sha256(self):
        path = "/some/known/path"
        expected = hashlib.sha256(path.encode()).hexdigest()
        actual = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        assert actual == expected, (
            f"_marker_lib_repo_hash produced {actual!r}, expected {expected!r}"
        )

    def test_trailing_newline_produces_different_hash(self):
        # If printf '%s' ever starts emitting a trailing newline, the hash would
        # match sha256("/tmp/foo\n") instead of sha256("/tmp/foo"). Assert the
        # two are distinct so this test fails if the recipe regresses.
        path = "/tmp/foo"
        hash_without_newline = hashlib.sha256(path.encode()).hexdigest()
        hash_with_newline = hashlib.sha256((path + "\n").encode()).hexdigest()
        actual = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        assert actual == hash_without_newline, (
            f"_marker_lib_repo_hash produced {actual!r}, expected no-newline hash {hash_without_newline!r}"
        )
        assert actual != hash_with_newline, (
            "hash matched the newline-suffixed input — printf '%s' may be adding a trailing newline"
        )

    def test_matches_inline_recipe(self):
        # Both the library function and the original inline recipe must produce
        # the same hash for the same path. This detects recipe drift between
        # the library and any remaining inline usage.
        path = "/tmp/test-repo"
        from_lib = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        from_inline = subprocess.run(
            ["bash", "-c", f"printf '%s' '{path}' | sha256sum | awk '{{print $1}}'"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert from_lib == from_inline, (
            f"Library hash {from_lib!r} != inline recipe {from_inline!r}"
        )


class TestLibActivePlanHash:
    """Tests for _lib_active_plan_hash (GH #466). Relational assertions
    only -- never a golden sha256 literal -- since the exact digest recipe
    is free to evolve as long as the write side and read side agree."""

    def test_empty_when_no_plans_dir(self, tmp_path):
        repo = tmp_path / "no-plans"
        _init_repo(repo)
        assert _active_plan_hash(repo) == ""

    def test_empty_when_plans_dir_empty(self, tmp_path):
        repo = tmp_path / "empty-plans"
        _init_repo(repo)
        (repo / ".claude" / "plans").mkdir(parents=True)
        assert _active_plan_hash(repo) == ""

    def test_empty_when_all_plans_committed_clean(self, tmp_path):
        repo = tmp_path / "clean-plans"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "p.md").write_text("# plan\n")
        subprocess.run(["git", "add", ".claude/plans/p.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "plan"], cwd=repo, check=True)
        assert _active_plan_hash(repo) == ""

    def test_nonempty_when_plan_active(self, tmp_path):
        repo = tmp_path / "active-plan"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "p.md").write_text("# plan\n")
        assert _active_plan_hash(repo) != ""

    def test_stable_under_reordered_active_set(self, tmp_path):
        """The same two active plans, recreated in reverse filesystem order
        within the same repo, must still produce the same hash -- LC_ALL=C
        sort normalizes enumeration order regardless of on-disk creation
        order. Holds the repo path (and so every hashed path) constant so
        creation order is the only variable -- comparing across two
        different tmp_path repos would also vary the absolute path prefix
        embedded in the hash, confounding the assertion."""
        repo = tmp_path / "order-repo"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "aaa.md").write_text("first\n")
        (plans_dir / "zzz.md").write_text("second\n")
        forward_order_hash = _active_plan_hash(repo)

        for existing in plans_dir.iterdir():
            existing.unlink()
        (plans_dir / "zzz.md").write_text("second\n")
        (plans_dir / "aaa.md").write_text("first\n")
        reverse_order_hash = _active_plan_hash(repo)

        assert forward_order_hash == reverse_order_hash

    def test_content_edit_changes_hash(self, tmp_path):
        repo = tmp_path / "edit-plan"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan = plans_dir / "p.md"
        plan.write_text("# plan v1\n")
        before = _active_plan_hash(repo)
        plan.write_text("# plan v2\n")
        after = _active_plan_hash(repo)
        assert before != after

    def test_active_set_change_changes_hash(self, tmp_path):
        repo = tmp_path / "add-plan"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "a.md").write_text("plan a\n")
        before = _active_plan_hash(repo)
        (plans_dir / "b.md").write_text("plan b\n")
        after = _active_plan_hash(repo)
        assert before != after

    def test_spaces_in_filename_round_trips(self, tmp_path):
        """A plan filename containing spaces must produce a non-empty hash,
        and re-running against the unchanged file must reproduce it
        identically -- guards against unquoted word-splitting in the file
        enumeration loop."""
        repo = tmp_path / "spacey-plan"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "my plan draft.md").write_text("# spacey plan\n")
        first = _active_plan_hash(repo)
        second = _active_plan_hash(repo)
        assert first != ""
        assert first == second

    def test_deleted_tracked_plan_disarms_rather_than_failing(self, tmp_path):
        """A committed plan deleted from the worktree shows up as modified
        vs HEAD but has no bytes left to hash. Counting it as active would
        make the hash unconditionally unobtainable -- denying every write
        forever instead of disarming, and with no file left for the user to
        repair."""
        repo = tmp_path / "deleted-plan"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan = plans_dir / "p.md"
        plan.write_text("# plan\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "plan"], cwd=repo, check=True)
        assert _active_plan_hash(repo) == "", "committed clean plan should not arm the gate"

        plan.unlink()
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'. "{LIB_SH}"; _lib_active_plan_hash "$1"',
                "_active_plan_hash",
                str(repo),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"deleting a plan must not be a hash failure, got exit {result.returncode} "
            f"stdout={result.stdout!r}"
        )
        assert result.stdout.strip() == "", "a deleted plan leaves nothing active to gate"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_plan_exits_nonzero_and_names_the_file(self, tmp_path):
        """An active-but-unhashable plan must be distinguishable from "no
        active plan". Both used to return empty with status 0, so every
        caller read the failure as "gate disarmed" and allowed. The exit
        status carries the distinction; stdout carries the offending path so
        the caller's deny message can point the user at it."""
        repo = tmp_path / "unreadable-plan"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan = plans_dir / "p.md"
        plan.write_text("# plan\n")
        plan.chmod(0o000)
        try:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'. "{LIB_SH}"; _lib_active_plan_hash "$1"',
                    "_active_plan_hash",
                    str(repo),
                ],
                capture_output=True,
                text=True,
            )
        finally:
            plan.chmod(0o644)

        assert result.returncode == 1, (
            f"expected exit 1 for an unhashable active plan, got {result.returncode}"
        )
        assert result.stdout.strip() == str(plan), (
            f"stdout must name the offending plan file, got {result.stdout!r}"
        )

    def test_hash_is_invariant_under_ambient_locale(self, tmp_path):
        """`LC_ALL=C sort` in the enumeration is load-bearing, not cosmetic:
        marker.sh runs in the user's Bash-tool locale and the hook runs in
        the harness hook environment, so a bare `sort` honoring $LC_COLLATE
        would order a >=2-plan set differently on each side and wedge the
        gate into a permanent false-deny. The filenames must be a pair the
        two collations genuinely disagree on -- C sorts uppercase before
        lowercase, most UTF-8 collations interleave them -- since an
        all-lowercase pair sorts identically everywhere and would make this
        test vacuous."""
        alt_locale = _find_case_insensitive_collation_locale()
        if alt_locale is None:
            pytest.skip("no non-C collation locale installed to contrast against")

        repo = tmp_path / "locale-repo"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "B.md").write_text("upper\n")
        (plans_dir / "a.md").write_text("lower\n")

        assert _active_plan_hash(repo, env_overrides={"LC_ALL": "C"}) == _active_plan_hash(
            repo, env_overrides={"LC_ALL": alt_locale}
        )

    def test_non_ascii_filename_round_trips(self, tmp_path):
        """A plan filename containing non-ASCII characters must produce a
        non-empty hash, and re-running against the unchanged file must
        reproduce it identically -- guards against a byte-width assumption
        in the file enumeration or hashing path."""
        repo = tmp_path / "unicode-plan"
        _init_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "plan-été-日本.md").write_text("# unicode plan\n")
        first = _active_plan_hash(repo)
        second = _active_plan_hash(repo)
        assert first != ""
        assert first == second


class TestLibAdvanceOffsetPastCompleteLines:
    """Unit tests for _lib_advance_offset_past_complete_lines -- shared by
    nudge-handoff-near-context-cap.sh and nudge-long-turn-subagent.sh for
    mid-write transcript safety when resuming an incremental scan from a
    stored byte offset. The two hooks' own tests cover that each wires this
    helper in; these tests cover the helper's own branch selection and
    boundary behavior."""

    def test_offset_already_equal_to_current_size_returns_offset_unchanged(self, tmp_path):
        """Zero new bytes since the last scan -- the boundary short-circuit
        ahead of both the fast and slow path."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, size, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(size)

    def test_fast_path_advances_to_current_size_when_file_ends_in_newline(self, tmp_path):
        """The file's last byte is a newline, so everything up to
        current_size is complete lines -- the one-`tail -c 1`-read fast
        path, the common case for a Claude Code transcript."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, 0, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(size)

    def test_slow_path_stops_before_incomplete_trailing_line(self, tmp_path):
        """The file currently ends mid-line (caught mid-write) -- the slow
        path must stop at the last complete line's newline, not at
        current_size."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\npartial-no-newline")
        complete_prefix_size = len("line one\nline two\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, 0, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(complete_prefix_size)

    def test_slow_path_from_nonzero_offset_counts_only_the_new_slice(self, tmp_path):
        """Resuming from a nonzero offset: only newlines in the unread slice
        (offset+1..current_size) count toward the returned boundary, not
        newlines before the offset."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\nline three\npartial")
        offset = len("line one\n")
        complete_prefix_size = len("line one\nline two\nline three\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, offset, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(complete_prefix_size)

    @pytest.mark.timing
    def test_slow_path_tail_timeout_returns_offset_unchanged(self, tmp_path):
        """The slow path's `tail -c +N` calls are capped via
        _lib_capped_for(2) -- a stalled tail must not hang the scan, and per
        the function's own documented limitation must degrade to OFFSET
        unchanged rather than partial progress."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\npartial-no-newline")
        real_tail = shutil.which("tail")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "tail"
        stub.write_text(
            '#!/bin/bash\n'
            'if [ "$1" = "-c" ] && [[ "$2" == +* ]]; then\n'
            '  sleep 10\n'
            'fi\n'
            f'exec {real_tail} "$@"\n'
        )
        stub.chmod(0o755)

        offset = 0
        size = transcript.stat().st_size
        start = time.monotonic()
        result = _advance_offset(
            transcript, offset, size,
            env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
        )
        elapsed = time.monotonic() - start

        assert result.returncode == 0
        assert result.stdout.strip() == str(offset), "a timed-out slow-path scan must return OFFSET unchanged"
        # No upper bound: 5/8 trials clocked 4.0-4.8s with no extra system
        # load, an empirically observed baseline rather than a guessed
        # margin -- the invariant that matters is not hanging for the ~10s
        # stub sleep, which the lower bound alone already rules out.
        assert elapsed >= 1.5, (
            f"expected the 2s _lib_capped_for timeout to fire (stub sleeps 10s "
            f"if it does not), took {elapsed:.1f}s for this single call"
        )
