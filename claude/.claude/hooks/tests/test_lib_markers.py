"""Unit tests for _lib.sh's _lib_marker_value_present, _lib_active_bypass_marker_live
and _lib_active_bypass_marker_live_and_touch.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    assert_cap_engaged,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from ._lib_sh_helpers import _LIB_SH

# --- _lib_marker_value_present -------------------------------------------
#
# Completion markers are content-addressed: the stored value is the whole
# authorization, and the filename prefix only namespaces concurrent writers
# apart. These tests pin that read contract, plus the two shell-level
# properties the helper depends on — whole-line matching, and a zero-match
# glob reporting "not found" rather than erroring on an unexpanded pattern.


def _marker_value_present(
    markers_dir: Path, expected: str, *prefixes: str
) -> subprocess.CompletedProcess:
    """Run _lib_marker_value_present; returncode 0 means found."""
    argv = [
        "bash", "-c", f'. {_LIB_SH}; _lib_marker_value_present "$@"', "bash",
        str(markers_dir), expected, *prefixes,
    ]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _write_marker(
    markers_dir: Path, name: str, value: str, *, trailing_newline: bool = True
) -> None:
    markers_dir.mkdir(parents=True, exist_ok=True)
    (markers_dir / name).write_text(value + ("\n" if trailing_newline else ""))


def test_marker_value_present_matches_stored_value(tmp_path: Path) -> None:
    """A marker holding the expected value under a matching prefix is found."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode == 0


def test_marker_value_present_matches_across_session_keys(tmp_path: Path) -> None:
    """The session suffix is not part of the read predicate.

    This is the defect the helper exists to close: a marker written under one
    session id must authorize a read from any other session, because the stored
    hash — not the filename — proves which state was reviewed.
    """
    _write_marker(tmp_path, "repohash.session-that-has-since-ended", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode == 0


def test_marker_value_present_rejects_different_value(tmp_path: Path) -> None:
    """A marker under a matching prefix holding a different value is not a match."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "cafebabe", "repohash.").returncode != 0


def test_marker_value_present_tolerates_missing_trailing_newline(tmp_path: Path) -> None:
    """A stored value with no trailing newline still matches.

    grep treats a final unterminated line as a line, so the read side does not
    depend on the writer's newline discipline.
    """
    _write_marker(tmp_path, "repohash.session-a", "deadbeef", trailing_newline=False)
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode == 0


def test_marker_value_present_scans_multiple_prefixes(tmp_path: Path) -> None:
    """A match under any supplied prefix counts — the sibling-worktree fallback tier."""
    _write_marker(tmp_path, "siblinghash.session-a", "deadbeef")
    result = _marker_value_present(tmp_path, "deadbeef", "currenthash.", "siblinghash.")
    assert result.returncode == 0


def test_marker_value_present_ignores_non_matching_prefix(tmp_path: Path) -> None:
    """The right value stored under the wrong repo-hash prefix must not release.

    This is the cross-repo boundary: dropping the repo key entirely would let a
    review performed against a different codebase authorize this one.
    """
    _write_marker(tmp_path, "otherrepohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode != 0


def test_marker_value_present_requires_whole_line_match(tmp_path: Path) -> None:
    """A stored value that merely CONTAINS the expected value is not a match.

    Pins the `-x` (whole-line) flag: a regression to bare `-F` would let a
    longer digest sharing this one as a substring release the gate.
    """
    _write_marker(tmp_path, "repohash.session-a", "deadbeefextra")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode != 0


def test_marker_value_present_rejects_stored_value_shorter_than_expected(tmp_path: Path) -> None:
    """The inverse substring direction: stored value is a proper prefix of expected."""
    _write_marker(tmp_path, "repohash.session-a", "dead")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode != 0


def test_marker_value_present_zero_match_glob_reports_not_found(tmp_path: Path) -> None:
    """No marker for this prefix → clean not-found, not a grep error.

    Without `nullglob`, bash expands a zero-match `prefix*` to the literal
    unexpanded pattern string, which grep then fails to open. "No marker exists
    yet" is the most common call, so that failure mode would misreport the
    common case as an error. The empty-stderr assertion is what catches it.
    """
    result = _marker_value_present(tmp_path, "deadbeef", "nosuchrepohash.")
    assert result.returncode != 0
    assert result.stderr == "", repr(result.stderr)


def test_marker_value_present_absent_directory_reports_not_found(tmp_path: Path) -> None:
    """A markers directory that does not exist yet is not-found, not an error."""
    result = _marker_value_present(tmp_path / "never-created", "deadbeef", "repohash.")
    assert result.returncode != 0
    assert result.stderr == "", repr(result.stderr)


def test_marker_value_present_requires_a_prefix(tmp_path: Path) -> None:
    """With no prefixes supplied, the helper reports not-found rather than scanning all."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef").returncode != 0


def test_marker_value_present_empty_expected_value_reports_not_found(tmp_path: Path) -> None:
    """An empty expected value never matches.

    A caller reaches this helper with an uncomputed hash only by skipping its
    own fail-closed check; matching an empty marker file there would convert a
    hashing failure into a gate release.
    """
    _write_marker(tmp_path, "repohash.session-a", "")
    assert _marker_value_present(tmp_path, "", "repohash.").returncode != 0


def test_marker_value_present_ignores_subdirectories(tmp_path: Path) -> None:
    """A stray subdirectory under the markers dir does not produce grep noise."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    (tmp_path / "repohash.stray-subdir").mkdir()
    result = _marker_value_present(tmp_path, "deadbeef", "repohash.")
    assert result.returncode == 0
    assert result.stderr == "", repr(result.stderr)


def test_marker_value_present_restores_nullglob_state() -> None:
    """The helper must not leak `nullglob` into its caller's shell.

    Hooks source _lib.sh and then run their own globs; silently flipping a
    shell option under them would change unrelated behavior far from this
    call site.
    """
    harness = (
        f". {_LIB_SH}; "
        "_lib_marker_value_present /nonexistent-markers-dir val prefix. ; "
        "shopt -q nullglob && echo LEAKED || echo RESTORED"
    )
    result = subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, check=False
    )
    assert result.stdout.strip() == "RESTORED", repr(result.stdout)


# --- _lib_active_bypass_marker_live ---------------------------------------
#
# The four bypass-shaped gates (require-{memory-skill,plan-review,
# ready-for-review,respond-pr}.sh), plus nudge-handoff-near-context-cap.sh's
# hard-block suppression, share this helper, so the liveness,
# orphan-eviction, and traversal properties are pinned once here rather than
# five times over. Each caller's own test file still asserts the disposition
# it produces when the helper returns false, which is what differs by caller.

_MARKER_DIR_NAME = ".test-skill-active.d"


def _active_bypass_marker_live(home: Path, session_id: str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_active_bypass_marker_live "$1" "$2"',
            "bash",
            _MARKER_DIR_NAME,
            session_id,
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        check=False,
    )
    return result.returncode == 0


def _write_active_bypass_marker(home: Path, session_id: str, content: str) -> Path:
    marker_dir = home / ".claude" / _MARKER_DIR_NAME
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / session_id
    marker.write_text(content)
    return marker


def test_active_bypass_marker_live_true_for_live_pid(tmp_path) -> None:
    """The whole point of the marker: a running skill's PID means the gate
    should let that session's own calls through."""
    _write_active_bypass_marker(tmp_path, "sess-live", str(os.getpid()))
    assert _active_bypass_marker_live(tmp_path, "sess-live")


def test_active_bypass_marker_live_false_when_no_marker(tmp_path) -> None:
    assert not _active_bypass_marker_live(tmp_path, "sess-absent")


@pytest.mark.parametrize(
    "content",
    [
        "2147483647",  # above the default pid_max — no such process
        "",
        "not-a-pid",
        "-1",
    ],
    ids=["dead-pid", "empty", "non-numeric", "negative"],
)
def test_active_bypass_marker_live_evicts_orphan(tmp_path, content: str) -> None:
    """A marker whose PID is dead or unreadable is an orphan from a session
    that died before its cleanup step. It must not release the gate, and it
    must be removed so it cannot accumulate."""
    marker = _write_active_bypass_marker(tmp_path, "sess-orphan", content)
    assert not _active_bypass_marker_live(tmp_path, "sess-orphan")
    assert not marker.exists(), "an orphaned marker must be evicted, not left in place"


def test_active_bypass_marker_live_keeps_live_marker(tmp_path) -> None:
    """Eviction is for orphans only — a live marker must survive being read,
    or the first gate hit would revoke the running skill's own bypass."""
    marker = _write_active_bypass_marker(tmp_path, "sess-keep", str(os.getpid()))
    assert _active_bypass_marker_live(tmp_path, "sess-keep")
    assert marker.exists()


def test_active_bypass_marker_live_false_for_traversal_id_without_touching_target(
    tmp_path,
) -> None:
    """The guard is inside the helper, so a path-escaping id must be rejected
    before the marker path is built — even when a live-PID file sits exactly
    where the traversal would resolve. Planting the PID there is what
    discriminates: without the guard the helper would read it, find it live,
    and return true."""
    (tmp_path / ".claude" / _MARKER_DIR_NAME).mkdir(parents=True)
    canary = tmp_path / ".claude" / "canary"
    canary.write_text(str(os.getpid()))

    assert not _active_bypass_marker_live(tmp_path, "../canary")
    assert canary.exists(), "a traversal id must not let the eviction rm escape the marker dir"


# The plan-review skill also writes a `.planmode-path` sibling into the same
# active-marker directory as the PID file this helper reads (see marker.sh's
# `write plan-review` and require-plan-review.sh's ExitPlanMode branch). The
# two are read by entirely separate code -- this helper vs. a plain `cat` of
# the sibling -- so the two-way non-interference property below is what that
# design depends on: neither read may perturb the other.


def _sibling_path(home: Path, session_id: str) -> Path:
    return home / ".claude" / _MARKER_DIR_NAME / f"{session_id}.planmode-path"


def test_active_bypass_marker_live_true_with_sibling_present_does_not_alter_it(
    tmp_path,
) -> None:
    """A live PID marker still liveness-checks correctly with a sibling file
    present alongside it, and the sibling's content survives the read
    untouched -- this helper only ever reads/evicts the exact session-id
    file it was called with."""
    _write_active_bypass_marker(tmp_path, "sess-with-sibling", str(os.getpid()))
    sibling = _sibling_path(tmp_path, "sess-with-sibling")
    sibling.write_text("/home/user/.claude/plans/scratch-plan.md")

    assert _active_bypass_marker_live(tmp_path, "sess-with-sibling")
    assert sibling.read_text() == "/home/user/.claude/plans/scratch-plan.md", (
        "a live-PID read must not alter the sibling file's content"
    )


def test_active_bypass_marker_live_evicts_dead_pid_without_touching_sibling(
    tmp_path,
) -> None:
    """The reverse direction: evicting a dead-PID marker removes only the
    PID file, never the sibling sharing its directory -- the sibling's own
    content/hash-read logic (marker.sh's job, not this helper's) is
    unaffected by the PID file's liveness state."""
    _write_active_bypass_marker(tmp_path, "sess-dead-with-sibling", "99999999")
    sibling = _sibling_path(tmp_path, "sess-dead-with-sibling")
    sibling.write_text("/home/user/.claude/plans/scratch-plan.md")

    assert not _active_bypass_marker_live(tmp_path, "sess-dead-with-sibling")
    assert sibling.exists(), "evicting the dead-PID marker must not remove the sibling"
    assert sibling.read_text() == "/home/user/.claude/plans/scratch-plan.md"


# Empty session_id is deliberately NOT tested here. `$HOME/.claude/<dir>/` with
# an empty id names the marker directory itself, which `[ -f ]` rejects whether
# or not the guard ran — no canary placement can separate the two states, so any
# such test would pass with the guard deleted and pin nothing. Empty-id
# rejection is pinned where it is discriminating: the parametrized rejection
# table for _lib_valid_session_id_component in test_lib_session_identity.py, which includes "".


# --- _lib_active_bypass_marker_live_and_touch -------------------------------
#
# The idle-window expiry (60 minutes) is touch-on-use, not a hard age cap: the
# four gating hooks (require-{memory-skill,plan-review,ready-for-review,
# respond-pr}.sh) call this wrapper, which refreshes a live marker's mtime so
# the window keeps sliding for as long as the owning skill keeps gating. The
# base predicate above must never do that refresh itself, or a status-only
# read (marker.sh status, nudge-handoff-near-context-cap.sh's enumeration)
# would keep every marker artificially fresh just by observing it.


def _active_bypass_marker_live_and_touch(home: Path, session_id: str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_active_bypass_marker_live_and_touch "$1" "$2"',
            "bash",
            _MARKER_DIR_NAME,
            session_id,
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        check=False,
    )
    return result.returncode == 0


def test_active_bypass_marker_live_and_touch_advances_mtime_on_live_check(tmp_path) -> None:
    """A gating call that finds the marker live must refresh its mtime -- this
    is what makes the idle window slide forward instead of expiring a
    long-running skill mid-review."""
    marker = _write_active_bypass_marker(tmp_path, "sess-touch-live", str(os.getpid()))
    old_time = time.time() - 300  # well within the 60-minute idle window
    os.utime(marker, (old_time, old_time))

    assert _active_bypass_marker_live_and_touch(tmp_path, "sess-touch-live")
    assert marker.stat().st_mtime > old_time + 1, (
        "a live check through the wrapper must refresh the marker's mtime"
    )


def test_active_bypass_marker_live_and_touch_does_not_touch_dead_pid_eviction(tmp_path) -> None:
    """A dead-PID marker's eviction propagates through the wrapper's own
    short-circuit, not just the underlying predicate called directly --
    the predicate evicts before the wrapper's touch line is ever reached."""
    marker = _write_active_bypass_marker(tmp_path, "sess-touch-dead", "99999999")
    old_time = time.time() - 300
    os.utime(marker, (old_time, old_time))

    assert not _active_bypass_marker_live_and_touch(tmp_path, "sess-touch-dead")
    assert not marker.exists(), "a dead-PID marker must be evicted, not refreshed and kept"


def test_active_bypass_marker_live_and_touch_evicts_idle_expired_live_pid(tmp_path) -> None:
    """The wrapper must reach the same idle-expiry verdict as the base
    predicate for a live PID whose mtime has aged past the 60-minute window --
    this is the exact combination the idle window exists to handle when
    reached through a gate hook's own call path, not just inferable by
    composing the wrapper's fresh-marker test with the predicate's own
    idle-expiry test."""
    marker = _write_active_bypass_marker(tmp_path, "sess-touch-idle", str(os.getpid()))
    stale_time = time.time() - 3700  # just past the 60-minute idle window
    os.utime(marker, (stale_time, stale_time))

    assert not _active_bypass_marker_live_and_touch(tmp_path, "sess-touch-idle")
    assert not marker.exists(), (
        "an idle-expired marker reached through the wrapper must be evicted"
    )


def test_active_bypass_marker_live_and_touch_false_when_no_marker(tmp_path) -> None:
    """Mirrors test_active_bypass_marker_live_false_when_no_marker for the
    wrapper's hottest real-world call shape: the overwhelming majority of
    gate-hook invocations find no active-bypass marker at all."""
    assert not _active_bypass_marker_live_and_touch(tmp_path, "sess-touch-absent")


def test_active_bypass_marker_live_and_touch_toctou_stub_touch_deletes_marker_first(
    tmp_path,
) -> None:
    """Regression test for the -c (no-create) contract under the exact race it
    guards against: a concurrent eviction (clear-stale, deactivate, another
    gate hit) removing the marker between the wrapper's liveness check and its
    own touch call. Shadows touch on PATH with a stub that deletes the marker
    immediately before exec'ing the real touch, forcing the race
    deterministically -- the same PATH-stub technique
    test_hung_jq_denied_within_timeout and
    test_timeout_absent_fallback_valid_payload_returns_ok use in test_lib.py."""
    marker = _write_active_bypass_marker(tmp_path, "sess-toctou", str(os.getpid()))

    real_touch = shutil.which("touch")
    if not real_touch:
        pytest.skip("touch not found in PATH")

    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub_touch = stub_dir / "touch"
    stub_touch.write_text(f'#!/bin/bash\nrm -f "{marker}"\nexec {real_touch} "$@"\n')
    stub_touch.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_active_bypass_marker_live_and_touch "$1" "$2"',
            "bash",
            _MARKER_DIR_NAME,
            "sess-toctou",
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": f"{stub_dir}:{os.environ['PATH']}"},
        check=False,
    )
    assert result.returncode == 0, (
        "the wrapper must still grant the verdict already committed before the race"
    )
    assert not marker.exists(), "touch -c must not resurrect a marker evicted mid-race"


@pytest.mark.timing
def test_active_bypass_marker_live_find_hang_capped_withholds_bypass(tmp_path) -> None:
    """Regression test for the `_lib_capped` wrap around this predicate's
    `find -mmin -60` freshness check. A `find` that hangs past the 5s cap
    must not hang the hook process, and the unresolved freshness read must
    not grant the bypass -- the same fail-safe posture a dead PID or an
    aged-out marker gets. Same PATH-stub technique as
    test_active_bypass_marker_live_and_touch_toctou_stub_touch_deletes_marker_first
    above and test_hung_jq_denied_within_timeout in test_lib.py. The
    stub sleeps past the cap, then -- only if not killed -- execs the real
    `find`, which would emit the marker's own path and grant the bypass; a
    working cap kills the stub before that exec runs, so this fails on a
    missing/broken `_lib_capped` wrap instead of passing regardless of
    whether the cap engaged."""
    marker = _write_active_bypass_marker(tmp_path, "sess-find-hang", str(os.getpid()))

    real_find = shutil.which("find")
    if not real_find:
        pytest.skip("find not found in PATH")
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

    stub_dir = tmp_path / "stub-bin-find"
    stub_dir.mkdir()
    write_scaled_timeout_shim(stub_dir)
    stub_find = stub_dir / "find"
    stub_find.write_text(f'#!/bin/bash\nsleep {scaled_shim_sleep(10)}\nexec {real_find} "$@"\n')
    stub_find.chmod(0o755)

    with assert_cap_engaged(stub_dir, production_cap=5):
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'. {_LIB_SH}; _lib_active_bypass_marker_live "$1" "$2"',
                "bash",
                _MARKER_DIR_NAME,
                "sess-find-hang",
            ],
            capture_output=True,
            text=True,
            env={"HOME": str(tmp_path), "PATH": f"{stub_dir}:{os.environ['PATH']}"},
            check=False,
        )
    assert result.returncode != 0, (
        "a capped-out find must withhold the bypass, not grant it on an "
        "unresolved freshness check"
    )
    assert not marker.exists(), "a capped-out freshness read must evict the marker"


@pytest.mark.timing
def test_active_bypass_marker_live_cat_hang_capped_withholds_bypass(tmp_path) -> None:
    """Regression test for the `_lib_capped` wrap around this predicate's
    `cat` read of the marker's stored PID. A `cat` that hangs past the 5s
    cap must not hang the hook process, and the unresolved PID read must
    not grant the bypass. Same PATH-stub technique as the find-hang test
    above: the stub sleeps past the cap, then -- only if not killed -- execs
    the real `cat`, which would emit the marker's stored PID and grant the
    bypass; a working cap kills the stub before that exec runs, so this
    fails on a missing/broken `_lib_capped` wrap instead of passing
    regardless of whether the cap engaged.

    cat and tr share the same 5s cap on this pipe; command="cat" isolates
    cat's kill from tr's."""
    marker = _write_active_bypass_marker(tmp_path, "sess-cat-hang", str(os.getpid()))

    real_cat = shutil.which("cat")
    if not real_cat:
        pytest.skip("cat not found in PATH")
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

    stub_dir = tmp_path / "stub-bin-cat"
    stub_dir.mkdir()
    write_scaled_timeout_shim(stub_dir)
    stub_cat = stub_dir / "cat"
    stub_cat.write_text(f'#!/bin/bash\nsleep {scaled_shim_sleep(10)}\nexec {real_cat} "$@"\n')
    stub_cat.chmod(0o755)

    with assert_cap_engaged(stub_dir, production_cap=5, killed_calls=1, command="cat"):
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'. {_LIB_SH}; _lib_active_bypass_marker_live "$1" "$2"',
                "bash",
                _MARKER_DIR_NAME,
                "sess-cat-hang",
            ],
            capture_output=True,
            text=True,
            env={"HOME": str(tmp_path), "PATH": f"{stub_dir}:{os.environ['PATH']}"},
            check=False,
        )
    assert result.returncode != 0, (
        "a capped-out cat must withhold the bypass, not grant it on an "
        "unresolved PID read"
    )
    assert not marker.exists(), "a capped-out PID read must evict the marker"


def test_active_bypass_marker_live_never_advances_mtime(tmp_path) -> None:
    """Pins the gating/status-read split itself, not just the touch wrapper's
    own behavior: the base predicate must stay side-effect-free on mtime
    regardless of outcome, so status-only callers can safely share it."""
    marker = _write_active_bypass_marker(tmp_path, "sess-mtime-stable", str(os.getpid()))
    old_time = time.time() - 300
    os.utime(marker, (old_time, old_time))
    before_mtime = marker.stat().st_mtime

    assert _active_bypass_marker_live(tmp_path, "sess-mtime-stable")
    assert marker.stat().st_mtime == before_mtime, "the base predicate must never refresh mtime"


def test_marker_ages_out_despite_repeated_status_only_reads(tmp_path) -> None:
    """Cross-hook-interference case: repeated status-only reads (the shape
    marker.sh status and nudge-handoff-near-context-cap.sh's enumeration both
    use) must neither refresh the marker's mtime nor prevent it from aging out
    once the window has genuinely elapsed. Holds the marker in-window across
    several reads first, then ages it past the boundary with no further gating
    call, so the test actually exercises "idle detection survives repeated
    reads while still in-window" rather than only "stays evicted once gone"."""
    marker = _write_active_bypass_marker(tmp_path, "sess-idle-ages-out", str(os.getpid()))
    fresh_time = time.time() - 300  # well within the 60-minute idle window
    os.utime(marker, (fresh_time, fresh_time))
    before_mtime = marker.stat().st_mtime

    for _ in range(3):
        assert _active_bypass_marker_live(tmp_path, "sess-idle-ages-out")
        assert marker.stat().st_mtime == before_mtime, (
            "repeated status-only reads must not refresh the marker's mtime"
        )

    stale_time = time.time() - 3700  # just past the 60-minute idle window
    os.utime(marker, (stale_time, stale_time))

    assert not _active_bypass_marker_live(tmp_path, "sess-idle-ages-out")
    assert not marker.exists(), (
        "an idle-expired marker must be evicted even though prior status-only "
        "reads left it untouched"
    )


def test_active_bypass_marker_live_at_3599_seconds_still_live(tmp_path) -> None:
    """Brackets the 60-minute cutoff from the live side, 1 second inside the
    window, rather than approaching it from a comfortable distance."""
    marker = _write_active_bypass_marker(tmp_path, "sess-boundary-live", str(os.getpid()))
    boundary_time = time.time() - 3599
    os.utime(marker, (boundary_time, boundary_time))

    assert _active_bypass_marker_live(tmp_path, "sess-boundary-live")
    assert marker.exists()


def test_active_bypass_marker_live_at_3601_seconds_already_expired(tmp_path) -> None:
    """Brackets the 60-minute cutoff from the expired side, 1 second past the
    window."""
    marker = _write_active_bypass_marker(tmp_path, "sess-boundary-expired", str(os.getpid()))
    boundary_time = time.time() - 3601
    os.utime(marker, (boundary_time, boundary_time))

    assert not _active_bypass_marker_live(tmp_path, "sess-boundary-expired")
    assert not marker.exists()
