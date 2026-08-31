"""Tests for cleanup-idle-open-pr-worktrees.sh.

The gh CLI is replaced in every test by a PATH shim that models the bulk
`gh pr list --state open` reply this script actually makes — one call, no
--head argument, an array of every open PR row for the repo — rather than
test_cleanup_merged_branches.py's per-branch keyed-lookup shim. Real git
operations run against temporary repos created per-test using the shared
scaffolding helpers in conftest.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from .conftest import (
    _commit,
    _make_feature_branch,
    _make_repo_with_remote,
    _make_worktree,
)

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "cleanup-idle-open-pr-worktrees.sh"

_EXPECTED_GH_PR_LIST_ARGS = [
    "pr", "list", "--state", "open", "--limit", "100",
    "--json", "headRefName,number,isDraft,updatedAt",
]


def _iso(hours_ago: float = 0, minutes_ago: float = 0) -> str:
    """A gh-shaped ISO8601 UTC timestamp, relative to the current instant.

    Generated clock-relative at test-execution time rather than as a
    hardcoded literal: this script's core logic is a delta against "now",
    so a fixed literal would drift stale as the suite ages.
    """
    when = datetime.now(UTC) - timedelta(hours=hours_ago, minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# fake_gh fixture — bulk `gh pr list --state open` shim
# ---------------------------------------------------------------------------

def _gh_shim_source(pr_data) -> str:
    """Return source for a gh shim script modeling the bulk open-PR query.

    pr_data is one of:
      list[dict]   -> the JSON array `gh pr list` returns; each row shaped
                       {"headRefName": str, "number": int, "isDraft": bool,
                        "updatedAt": iso8601 str}
      "error"      -> `gh pr list` itself exits non-zero
      "malformed"  -> `gh pr list` exits 0 but writes an unparseable body
      {"__auth__": "unauth"} -> `gh auth status` exits non-zero instead

    Validates the exact args the script passes rather than permissively
    returning canned data regardless of shape: a generous shim would hide
    both an argument-construction regression (wrong --limit, dropped
    --state open) and a classifier bug reading a field it never requested.
    """
    payload = json.dumps(pr_data)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys

        PR_DATA = json.loads({payload!r})
        EXPECTED_PR_LIST_ARGS = {_EXPECTED_GH_PR_LIST_ARGS!r}

        args = sys.argv[1:]

        if args[:2] == ["auth", "status"]:
            if isinstance(PR_DATA, dict) and PR_DATA.get("__auth__") == "unauth":
                sys.exit(1)
            sys.exit(0)

        if args[:2] == ["pr", "list"]:
            if args != EXPECTED_PR_LIST_ARGS:
                sys.stderr.write("gh shim: unexpected args: " + repr(args) + chr(10))
                sys.exit(1)
            if PR_DATA == "error":
                sys.exit(1)
            if PR_DATA == "malformed":
                sys.stdout.write('[{{"number":')
                sys.exit(0)
            print(json.dumps(PR_DATA))
            sys.exit(0)

        sys.stderr.write("gh shim: unrecognized subcommand: " + repr(args) + chr(10))
        sys.exit(1)
    """)


@pytest.fixture()
def fake_gh(tmp_path):
    """Yield a factory that installs a gh shim and returns the env dict."""
    shim_dir = tmp_path / "gh_shim"
    shim_dir.mkdir()

    def _make_env(pr_data) -> dict:
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source(pr_data))
        shim_py.chmod(0o755)
        new_path = str(shim_dir) + ":" + os.environ.get("PATH", "")
        return {**os.environ, "PATH": new_path}

    return _make_env


# ---------------------------------------------------------------------------
# fake_bsd_date fixture — forces the BSD/macOS `date` detection outcome
# ---------------------------------------------------------------------------

_BSD_DATE_SHIM_SOURCE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import sys, datetime

    args = sys.argv[1:]

    # Simulate BSD/macOS date's lack of --version support, forcing the
    # script's GNU/BSD detection to choose the BSD branch.
    if args[:1] == ["--version"]:
        sys.exit(1)

    # BSD form: date -j -u -f '%Y-%m-%dT%H:%M:%SZ' <timestamp> +%s
    if args[:3] == ["-j", "-u", "-f"]:
        fmt, timestamp, outfmt = args[3], args[4], args[5]
        if outfmt != "+%s":
            sys.exit(1)
        parsed = datetime.datetime.strptime(timestamp, fmt).replace(
            tzinfo=datetime.timezone.utc
        )
        print(int(parsed.timestamp()))
        sys.exit(0)

    # `date -u +%s` — current epoch (NOW_EPOCH); identical on GNU and BSD,
    # so this form is not part of the branch under test but must still work.
    if args[:2] == ["-u", "+%s"]:
        print(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
        sys.exit(0)

    sys.stderr.write("date shim: unexpected args: " + repr(args) + chr(10))
    sys.exit(1)
""")


@pytest.fixture()
def bsd_date_path(tmp_path):
    """Return a PATH-prepend directory containing a `date` shim that forces
    the BSD detection outcome and validates the BSD invocation shape."""
    shim_dir = tmp_path / "date_shim"
    shim_dir.mkdir()
    shim_py = shim_dir / "date"
    shim_py.write_text(_BSD_DATE_SHIM_SOURCE)
    shim_py.chmod(0o755)
    return shim_dir


_PINNED_NOW_SHIM_TEMPLATE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import subprocess, sys

    args = sys.argv[1:]

    # This script's NOW_EPOCH read (`date -u +%s`, no other arguments) is
    # pinned to a fixed value; every other invocation — including the GNU
    # updatedAt-parsing form (`date -u -d <ts> +%s`) — passes through to the
    # real `date` binary unmodified.
    if args == ["-u", "+%s"]:
        print({fixed_epoch})
        sys.exit(0)

    sys.exit(subprocess.call(["{real_date}", *args]))
""")


@pytest.fixture()
def pinned_now(tmp_path):
    """Return (shim_dir, fixed_epoch): a PATH-prepend directory whose `date`
    shim pins this script's own idea of "now" to a fixed epoch, captured at
    fixture time (clock-relative, not a hardcoded literal). A real, elapsed
    wall clock between generating an "exactly at the boundary" updatedAt
    fixture and the script's own `date -u +%s` call would otherwise make
    that boundary unobservable — subprocess startup latency alone pushes
    the actual delta past the threshold before the comparison ever runs.
    """
    real_date = subprocess.run(["which", "date"], capture_output=True, text=True, check=True).stdout.strip()
    fixed_epoch = int(datetime.now(UTC).timestamp())
    shim_dir = tmp_path / "pinned_now_shim"
    shim_dir.mkdir()
    shim_py = shim_dir / "date"
    shim_py.write_text(_PINNED_NOW_SHIM_TEMPLATE.format(fixed_epoch=fixed_epoch, real_date=real_date))
    shim_py.chmod(0o755)
    return shim_dir, fixed_epoch


def _run_script(
    repo: Path,
    env: dict,
    args: list[str] | None = None,
) -> subprocess.CompletedProcess:
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

class TestDraftSkippedRegardlessOfUpdatedAt:
    """A draft PR is skipped even when its updatedAt is far outside the
    idle window — a draft is explicitly WIP by GitHub's own definition."""

    def test_draft_pr_worktree_survives(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/draft")
        wt_path = tmp_path / "draft-tree"
        _make_worktree(local, "feat/draft", wt_path)

        env = fake_gh([
            {"headRefName": "feat/draft", "number": 1, "isDraft": True, "updatedAt": _iso(hours_ago=100)},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Skipped (draft): feat/draft" in result.stdout
        assert wt_path.exists()


class TestIdleHoursBoundary:
    """The idle-hours comparison is tested on both sides of the default
    4-hour threshold, with fixtures generated clock-relative to "now" —
    never a hardcoded literal, since this script's logic is a delta
    against wall-clock time."""

    def test_exactly_at_threshold_is_idle(self, tmp_path, fake_gh, pinned_now):
        """Exactly 4 hours ago: NOW_EPOCH - UPDATED_EPOCH == IDLE_SECONDS.
        The comparison is `elapsed < idle_seconds` (skip as still-active) —
        an exact match fails that test, so classification falls through to
        "idle candidate", the same side as one-second-over. Uses pinned_now
        rather than a real-clock-relative timestamp: with zero slack between
        the fixture and the boundary, a real wall clock would let ordinary
        subprocess startup latency push the actual delta past the threshold
        before the script ever compares it."""
        shim_dir, fixed_epoch = pinned_now
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/at-threshold")
        wt_path = tmp_path / "at-threshold-tree"
        _make_worktree(local, "feat/at-threshold", wt_path)

        updated_at = datetime.fromtimestamp(fixed_epoch - 4 * 3600, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        env = fake_gh([
            {"headRefName": "feat/at-threshold", "number": 1, "isDraft": False, "updatedAt": updated_at},
        ])
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert not wt_path.exists(), "exactly-at-threshold must be treated as idle, not still active"

    def test_one_second_under_threshold_is_still_active(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/one-under")
        wt_path = tmp_path / "one-under-tree"
        _make_worktree(local, "feat/one-under", wt_path)

        env = fake_gh([
            {"headRefName": "feat/one-under", "number": 1, "isDraft": False,
             "updatedAt": _iso(hours_ago=3, minutes_ago=59)},
        ])
        result = _run_script(local, env, )

        assert result.returncode == 0, result.stderr
        assert "Skipped (still active): feat/one-under" in result.stdout
        assert wt_path.exists()

    def test_one_second_over_threshold_is_idle(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/one-over")
        wt_path = tmp_path / "one-over-tree"
        _make_worktree(local, "feat/one-over", wt_path)

        updated_at = (datetime.now(UTC) - timedelta(hours=4, seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        env = fake_gh([
            {"headRefName": "feat/one-over", "number": 1, "isDraft": False, "updatedAt": updated_at},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Removed:" in result.stdout
        assert not wt_path.exists()

    def test_boundary_unaffected_by_non_utc_tz(self, tmp_path, fake_gh):
        """A non-UTC TZ in the subprocess environment must not shift the
        idle boundary — catches a forgotten -u/-j -u on either date branch."""
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/tz-boundary")
        wt_path = tmp_path / "tz-boundary-tree"
        _make_worktree(local, "feat/tz-boundary", wt_path)

        env = fake_gh([
            {"headRefName": "feat/tz-boundary", "number": 1, "isDraft": False,
             "updatedAt": _iso(hours_ago=3, minutes_ago=59)},
        ])
        env["TZ"] = "Pacific/Kiritimati"  # UTC+14 — far enough to expose a lost -u
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Skipped (still active): feat/tz-boundary" in result.stdout
        assert wt_path.exists()


class TestBsdDateBranchExercised:
    """Forces the "no GNU date support" detection outcome and asserts the
    BSD-form invocation (`date -j -u -f '%Y-%m-%dT%H:%M:%SZ' ...`) runs and
    parses a known ISO8601 timestamp correctly. Otherwise this branch ships
    as dead code from CI's perspective (this repo's dev/CI is Linux-only)."""

    def test_idle_branch_removed_via_bsd_date_form(self, tmp_path, fake_gh, bsd_date_path):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/bsd-date")
        wt_path = tmp_path / "bsd-date-tree"
        _make_worktree(local, "feat/bsd-date", wt_path)

        env = fake_gh([
            {"headRefName": "feat/bsd-date", "number": 1, "isDraft": False,
             "updatedAt": _iso(hours_ago=10)},
        ])
        env["PATH"] = f"{bsd_date_path}:{env['PATH']}"
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Removed:" in result.stdout
        assert not wt_path.exists()


class TestStaleOpenPrRemoved:
    """A stale (idle, non-draft) open-PR worktree is removed; the branch
    and PR are untouched, and the worktree is recreatable afterward — the
    actual rollback path the design's safety argument depends on."""

    def test_worktree_removed_branch_survives_and_is_recreatable(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/stale")
        wt_path = tmp_path / "stale-tree"
        _make_worktree(local, "feat/stale", wt_path)

        env = fake_gh([
            {"headRefName": "feat/stale", "number": 5, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Removed:" in result.stdout
        assert not wt_path.exists()

        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/stale"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch must survive worktree removal"

        remote_refs = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "feat/stale"],
            cwd=local, capture_output=True, text=True,
        ).stdout
        assert "feat/stale" in remote_refs, "the branch's PR-backing remote ref must survive"

        fresh_path = tmp_path / "stale-tree-recreated"
        recreate = subprocess.run(
            ["git", "worktree", "add", str(fresh_path), "feat/stale"],
            cwd=local, capture_output=True,
        )
        assert recreate.returncode == 0, "removed worktree must be recreatable from the surviving branch"
        assert fresh_path.exists()


class TestClosedUnmergedPrLeftUntouched:
    """A branch whose only PR is closed (never merged) produces no row in
    the --state open response; the worktree is left untouched. Documents
    the deliberate orphan gap as accepted, not accidental."""

    def test_no_matching_open_pr_row_worktree_untouched(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/closed-unmerged")
        wt_path = tmp_path / "closed-unmerged-tree"
        _make_worktree(local, "feat/closed-unmerged", wt_path)

        # The closed PR simply never appears in the --state open response.
        env = fake_gh([])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert wt_path.exists()
        assert "feat/closed-unmerged" not in result.stdout


class TestMultipleRowsSharingHeadRefName:
    """Two PR rows point at the same branch (e.g. against different base
    branches); the tie-break rule (lowest PR number wins) is applied
    deterministically, and classification uses only the winning row."""

    def test_lowest_pr_number_wins_and_governs_classification(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dup-head")
        wt_path = tmp_path / "dup-head-tree"
        _make_worktree(local, "feat/dup-head", wt_path)

        # The higher-numbered row is a draft; if the classifier ever picked
        # it instead of the lower-numbered (winning) row, this branch would
        # be skipped as draft rather than removed as idle.
        env = fake_gh([
            {"headRefName": "feat/dup-head", "number": 20, "isDraft": True, "updatedAt": _iso(hours_ago=10)},
            {"headRefName": "feat/dup-head", "number": 3, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Removed:" in result.stdout
        assert not wt_path.exists()


class TestSlashedBranchName:
    """A branch name containing slashes resolves correctly through this
    script's own worktree-path lookup call site — a second consumer of
    _worktree-lib.sh is exactly when a path-construction bug at the call
    site (as opposed to inside the lib) tends to reappear."""

    def test_slashed_branch_worktree_resolved_and_removed(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/scope/deep-name")
        wt_path = tmp_path / "deep-name-tree"
        _make_worktree(local, "feat/scope/deep-name", wt_path)

        env = fake_gh([
            {"headRefName": "feat/scope/deep-name", "number": 9, "isDraft": False,
             "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "feat/scope/deep-name" in result.stdout
        assert not wt_path.exists()


class TestWorktreeInUseSkipped:
    """A worktree a live process is working inside is skipped, not removed."""

    def test_worktree_with_live_process_is_skipped(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/in-use")
        wt_path = tmp_path / "in-use-tree"
        _make_worktree(local, "feat/in-use", wt_path)

        env = fake_gh([
            {"headRefName": "feat/in-use", "number": 7, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
        ])
        holder = subprocess.Popen(["sleep", "120"], cwd=str(wt_path))
        try:
            result = _run_script(local, env)
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0, result.stderr
        assert "Skipped (worktree in use" in result.stdout
        assert wt_path.exists()


class TestSelfWorktreeNeverRemoved:
    """The worktree the script is currently running from must never become
    a removal candidate, even when its own branch has an idle, non-draft,
    open PR — the WORKTREE_PATH == REPO_ROOT guard is the last safety check
    standing between the classification loop and the script removing the
    checkout it is executing from."""

    def test_worktree_running_the_script_is_never_a_removal_candidate(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/self")
        wt_path = tmp_path / "self-tree"
        _make_worktree(local, "feat/self", wt_path)

        env = fake_gh([
            {"headRefName": "feat/self", "number": 9, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(wt_path, env)

        assert result.returncode == 0, result.stderr
        assert "Removed:" not in result.stdout
        assert wt_path.exists(), "the worktree running the script must never remove itself"


class TestMultiRowBulkResponse:
    """A single bulk response mixing draft, recently-active, stale, and
    in-use branches: each lands in its correct bucket and the end-of-run
    summary counts match — the scenario a per-branch-only suite would never
    exercise, and exactly where an off-by-one over the JSON array or a
    wrong-key lookup would hide."""

    def test_each_branch_lands_in_its_correct_bucket(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)

        _make_feature_branch(local, "feat/multi-draft")
        draft_wt = tmp_path / "multi-draft-tree"
        _make_worktree(local, "feat/multi-draft", draft_wt)

        _make_feature_branch(local, "feat/multi-active")
        active_wt = tmp_path / "multi-active-tree"
        _make_worktree(local, "feat/multi-active", active_wt)

        _make_feature_branch(local, "feat/multi-stale")
        stale_wt = tmp_path / "multi-stale-tree"
        _make_worktree(local, "feat/multi-stale", stale_wt)

        _make_feature_branch(local, "feat/multi-in-use")
        in_use_wt = tmp_path / "multi-in-use-tree"
        _make_worktree(local, "feat/multi-in-use", in_use_wt)

        env = fake_gh([
            {"headRefName": "feat/multi-draft", "number": 1, "isDraft": True, "updatedAt": _iso(hours_ago=10)},
            {"headRefName": "feat/multi-active", "number": 2, "isDraft": False, "updatedAt": _iso(minutes_ago=5)},
            {"headRefName": "feat/multi-stale", "number": 3, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
            {"headRefName": "feat/multi-in-use", "number": 4, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
        ])
        holder = subprocess.Popen(["sleep", "120"], cwd=str(in_use_wt))
        try:
            result = _run_script(local, env)
        finally:
            holder.terminate()
            holder.wait()

        assert result.returncode == 0, result.stderr
        assert draft_wt.exists(), "draft must survive"
        assert active_wt.exists(), "recently-active must survive"
        assert not stale_wt.exists(), "stale must be removed"
        assert in_use_wt.exists(), "in-use must survive"

        assert "Skipped (draft): feat/multi-draft" in result.stdout
        assert "Skipped (still active): feat/multi-active" in result.stdout
        assert "feat/multi-stale: removed" in result.stdout
        assert "Skipped (worktree in use" in result.stdout

        summary_match = re.search(
            r"Summary: removed=(\d+) skipped-active=(\d+) skipped-draft=(\d+) "
            r"skipped-in-use=(\d+) skipped-no-pr=(\d+)",
            result.stdout,
        )
        assert summary_match is not None, result.stdout
        removed, skipped_active, skipped_draft, skipped_in_use, _skipped_no_pr = (
            int(g) for g in summary_match.groups()
        )
        assert removed == 1
        assert skipped_active == 1
        assert skipped_draft == 1
        assert skipped_in_use == 1


class TestOpenPrWithNoMatchingWorktree:
    """gh pr list returns a matching, idle, non-draft PR for a branch that
    was never given its own worktree; the script skips it rather than
    erroring on a missing worktree-path lookup."""

    def test_branch_without_worktree_is_skipped_not_errored(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-worktree")
        # Deliberately no _make_worktree call for this branch.

        env = fake_gh([
            {"headRefName": "feat/no-worktree", "number": 8, "isDraft": False,
             "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "feat/no-worktree" not in result.stdout
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/no-worktree"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0, "branch itself must be untouched"


class TestDirtyCandidateMidBatchDoesNotAbort:
    """A dirty (untracked-content) candidate mid-batch does not abort the
    run: at least two idle candidates, with the dirty one deliberately not
    last (branch names pin enumeration order via git for-each-ref's default
    alphabetical sort — renaming them would silently invert what this test
    proves, the same caveat test_cleanup_merged_branches.py's
    TestTierBPromptEOFDoesNotAbortPendingTierADeletes documents for its own
    ordering)."""

    def test_dirty_worktree_failure_does_not_block_later_candidate(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)

        _make_feature_branch(local, "aaa-dirty")
        dirty_wt = tmp_path / "aaa-dirty-tree"
        _make_worktree(local, "aaa-dirty", dirty_wt)
        (dirty_wt / "leftover.txt").write_text("in-progress work")

        _make_feature_branch(local, "zzz-clean")
        clean_wt = tmp_path / "zzz-clean-tree"
        _make_worktree(local, "zzz-clean", clean_wt)

        env = fake_gh([
            {"headRefName": "aaa-dirty", "number": 1, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
            {"headRefName": "zzz-clean", "number": 2, "isDraft": False, "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, "a mid-batch remove failure must not abort the run"
        assert "remove failed (manual step needed)" in result.stdout
        assert dirty_wt.exists(), "dirty worktree must survive its own failed removal"
        assert (dirty_wt / "leftover.txt").exists()
        assert not clean_wt.exists(), "the later candidate must still be processed"


class TestEndOfRunSummaryCounters:
    """Summary counters are asserted directly, not inferred from side
    effects alone."""

    def test_summary_counters_match_classification(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/summary-stale")
        stale_wt = tmp_path / "summary-stale-tree"
        _make_worktree(local, "feat/summary-stale", stale_wt)

        env = fake_gh([
            {"headRefName": "feat/summary-stale", "number": 1, "isDraft": False,
             "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Summary: removed=1 skipped-active=0 skipped-draft=0 skipped-in-use=0 skipped-no-pr=1" \
            in result.stdout


class TestDryRunTakesNoAction:
    """--dry-run reports candidates and reasons without acting."""

    def test_dry_run_no_destructive_action(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dry-stale")
        wt_path = tmp_path / "dry-stale-tree"
        _make_worktree(local, "feat/dry-stale", wt_path)

        env = fake_gh([
            {"headRefName": "feat/dry-stale", "number": 1, "isDraft": False,
             "updatedAt": _iso(hours_ago=10)},
        ])
        result = _run_script(local, env, args=["--dry-run"])

        assert result.returncode == 0, result.stderr
        assert "Would remove (idle open-PR worktree):" in result.stdout
        assert "feat/dry-stale" in result.stdout
        assert "Summary: would-remove=1" in result.stdout
        assert wt_path.exists(), "dry-run must never remove a worktree"
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/feat/dry-stale"],
            cwd=local, capture_output=True,
        )
        assert ref_check.returncode == 0


class TestUnparseableUpdatedAtFailsClosed:
    """A single PR row with an unparseable updatedAt is the fail-closed
    branch in classification (parse_iso8601_epoch failing for that one
    row): it must not abort the whole run under set -e, and must not fall
    through to being read as idle — the branch lands in
    SKIPPED_ACTIVE_BRANCHES and is reported as still-active rather than
    treated as a removal candidate. (An empty updatedAt is not used here:
    GNU date -d '' parses as "now" rather than failing, so it would
    exercise the idle-hours-elapsed branch instead of this fail-closed one.)"""

    def test_garbage_updated_at_worktree_survives_and_is_skipped(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/bad-updated-at")
        wt_path = tmp_path / "bad-updated-at-tree"
        _make_worktree(local, "feat/bad-updated-at", wt_path)

        env = fake_gh([
            {"headRefName": "feat/bad-updated-at", "number": 1, "isDraft": False,
             "updatedAt": "not-a-date"},
        ])
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "Skipped (still active): feat/bad-updated-at" in result.stdout
        assert wt_path.exists(), "an unparseable updatedAt must not be treated as idle"


class TestGhFailureModes:
    """No-upstream-remote, a non-zero-exit gh failure, and a malformed-JSON-
    on-zero-exit gh response are three separate failure surfaces (a
    git-config precondition, an API/auth precondition, and a parse-layer
    precondition respectively) that must all fail closed — remove nothing —
    but are distinguished in their error text."""

    def test_no_upstream_remote_is_a_usage_error(self, tmp_path, fake_gh):
        from .conftest import _init_repo

        repo = tmp_path / "no-origin-repo"
        _init_repo(repo)
        _commit(repo, "init")
        env = fake_gh([])
        result = _run_script(repo, env)
        assert result.returncode != 0
        assert "no 'origin' remote" in result.stderr

    def test_gh_pr_list_non_zero_exit_fails_closed(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/gh-error")
        wt_path = tmp_path / "gh-error-tree"
        _make_worktree(local, "feat/gh-error", wt_path)

        env = fake_gh("error")
        result = _run_script(local, env)

        assert result.returncode != 0
        assert "gh pr list" in result.stderr
        assert "failed" in result.stderr
        assert wt_path.exists(), "a gh failure must remove nothing"

    def test_malformed_json_on_zero_exit_fails_closed(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/gh-malformed")
        wt_path = tmp_path / "gh-malformed-tree"
        _make_worktree(local, "feat/gh-malformed", wt_path)

        env = fake_gh("malformed")
        result = _run_script(local, env)

        assert result.returncode != 0
        assert "could not be parsed as JSON" in result.stderr
        assert wt_path.exists(), (
            "malformed output must never be read as an empty PR list — that would "
            "wrongly treat every branch as a removal candidate"
        )


class TestIdleHoursOverride:
    """A non-default --idle-hours value changes the boundary."""

    def test_idle_hours_override_removes_branch_default_would_keep(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/override")
        wt_path = tmp_path / "override-tree"
        _make_worktree(local, "feat/override", wt_path)

        # 90 minutes ago: still active under the 4-hour default, idle under
        # a 1-hour override.
        env = fake_gh([
            {"headRefName": "feat/override", "number": 1, "isDraft": False,
             "updatedAt": _iso(minutes_ago=90)},
        ])

        default_result = _run_script(local, env)
        assert default_result.returncode == 0, default_result.stderr
        assert wt_path.exists(), "under the 4-hour default this branch is still active"

        override_result = _run_script(local, env, args=["--idle-hours=1"])
        assert override_result.returncode == 0, override_result.stderr
        assert not wt_path.exists(), "a 1-hour override must treat a 90-minute-old PR as idle"


class TestInvalidArgs:
    """Bad arguments exit 2 with usage on stderr."""

    @pytest.mark.parametrize("bad_args", [
        ["foo"],
        ["--bar"],
        ["--idle-hours=abc"],
        ["--idle-hours=-1"],
        ["--idle-hours="],
    ])
    def test_invalid_args_exit_nonzero(self, tmp_path, fake_gh, bad_args):
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh([])
        result = _run_script(local, env, args=bad_args)
        assert result.returncode != 0


class TestGhMissing:
    """gh not in PATH exits non-zero with install instructions."""

    def test_gh_missing_exits_nonzero(self, tmp_path):
        local, _ = _make_repo_with_remote(tmp_path)
        bin_dir = tmp_path / "min_bin"
        bin_dir.mkdir()
        for tool in ("git", "python3", "bash", "grep", "awk", "sed", "dirname"):
            tool_path = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
            if tool_path:
                (bin_dir / tool).symlink_to(tool_path)
        env = {**os.environ, "PATH": str(bin_dir)}
        result = _run_script(local, env)
        assert result.returncode != 0
        assert "gh" in result.stderr.lower() or "install" in result.stderr.lower()


class TestGhUnauthenticated:
    """gh present but unauthenticated exits non-zero."""

    def test_gh_unauth_exits_nonzero(self, tmp_path, fake_gh):
        local, _ = _make_repo_with_remote(tmp_path)
        env = fake_gh({"__auth__": "unauth"})
        result = _run_script(local, env)
        assert result.returncode != 0
        assert "auth" in result.stderr.lower() or "login" in result.stderr.lower()
