"""Direct unit tests for marker-clear-stale.py's `pid_alive` and
`read_no_follow`, isolating them from the sweep loop that calls them.
`marker.sh clear-stale`'s aggregate stdout is covered separately by
claude/.claude/hooks/tests/test_marker_script.py's TestMarkerScriptClearStale.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from helpers import SCRIPTS_DIR

SCRIPT_PATH = SCRIPTS_DIR / "marker-clear-stale.py"

_spec = importlib.util.spec_from_file_location("marker_clear_stale", SCRIPT_PATH)
_clear_stale = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_clear_stale)


class TestReadNoFollow:
    def test_returns_file_content(self, tmp_path):
        target = tmp_path / "marker"
        target.write_bytes(b"12345\n")
        assert _clear_stale.read_no_follow(str(target)) == b"12345\n"

    def test_returns_none_for_a_missing_file(self, tmp_path):
        assert _clear_stale.read_no_follow(str(tmp_path / "absent")) is None

    def test_returns_none_for_a_symlink_rather_than_following_it(self, tmp_path):
        real = tmp_path / "real"
        real.write_bytes(b"secret\n")
        link = tmp_path / "link"
        link.symlink_to(real)
        assert _clear_stale.read_no_follow(str(link)) is None


class TestPidAlive:
    def test_true_for_this_process_own_pid(self):
        assert _clear_stale.pid_alive(str(os.getpid())) is True

    def test_false_for_a_dead_pid(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        assert _clear_stale.pid_alive(str(proc.pid)) is False

    def test_false_for_empty_text(self):
        assert _clear_stale.pid_alive("") is False

    def test_false_for_none(self):
        assert _clear_stale.pid_alive(None) is False

    def test_false_for_non_numeric_text(self):
        assert _clear_stale.pid_alive("not-a-pid") is False

    def test_false_for_a_negative_number(self):
        assert _clear_stale.pid_alive("-1") is False


def _write_review_pr_provenance(active_dir: Path, session_id: str, pid: str) -> None:
    """Field order matches _lib_review_pr_completion_marker_fields's own
    convention: PR identity, headRefOid, PID, mode -- PID is field index 2
    (0-based), which sweep()'s REVIEW_PR_SUFFIXES branch reads."""
    (active_dir / f"{session_id}.provenance").write_text(
        "\n".join(["foo/bar#42", "abc123", pid, "checkout"]) + "\n"
    )


class TestSweepReviewPrSuffixBranch:
    """sweep()'s REVIEW_PR_SUFFIXES branch derives the owning PID from a
    sibling .provenance file rather than the entry's own content -- a
    separate rule from the generic single-file PID+mtime branch every other
    sweep() test here exercises. Calls sweep() directly (already imported
    via importlib above), not the CLI, since these three cases are about the
    branch's own eviction logic, not its stdout formatting."""

    def test_review_pr_entry_with_a_live_sibling_pid_is_kept(self, tmp_path):
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        (active_dir / "alive-session.body").write_text("findings\n")
        _write_review_pr_provenance(active_dir, "alive-session", str(os.getpid()))

        # Two entries are swept, not one: the sibling .provenance file is
        # itself a REVIEW_PR_SUFFIXES entry (it ends in ".provenance"), whose
        # own sibling lookup resolves to itself -- self-referential, and
        # correctly so, since a provenance file's own liveness is judged by
        # the same PID field.
        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (0, 2)
        assert (active_dir / "alive-session.body").exists()
        assert (active_dir / "alive-session.provenance").exists()

    def test_review_pr_entry_with_a_dead_sibling_pid_is_evicted(self, tmp_path):
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "dead-session.body").write_text("findings\n")
        _write_review_pr_provenance(active_dir, "dead-session", str(proc.pid))

        # Both the .body entry and its self-referential .provenance entry
        # (see the live-sibling test above) are evicted.
        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (2, 0)
        assert not (active_dir / "dead-session.body").exists()
        assert not (active_dir / "dead-session.provenance").exists()

    def test_review_pr_entry_with_no_sibling_provenance_is_evicted(self, tmp_path):
        """No provenance file at all (never written, or already reaped) must
        read as "owner dead" rather than being kept indefinitely."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        (active_dir / "orphan-session.body").write_text("findings\n")

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (1, 0)
        assert not (active_dir / "orphan-session.body").exists()


class TestReviewPrSuffixScopingIsDirNameGated:
    """REVIEW_PR_SUFFIXES matching is gated on dir_name == ".review-pr-active.d"
    (sweep()'s own scoping check) -- a same-suffixed entry inside any other
    *-active.d directory must fall through to the generic single-file
    PID+mtime branch instead, never the review-pr sibling-provenance branch."""

    def test_review_pr_suffixed_entry_outside_review_pr_active_dir_uses_generic_branch(
        self, tmp_path
    ):
        active_dir = tmp_path / ".other-active.d"
        active_dir.mkdir()
        entry = active_dir / "session.body"
        # The generic branch reads the entry's own content as its PID -- a
        # live PID here proves the generic branch ran, since the review-pr
        # branch would instead look for a sibling "session.provenance" file
        # (absent here) and evict unconditionally regardless of content.
        entry.write_text(f"{os.getpid()}\n")

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (0, 1)
        assert entry.exists()


def _run_cli(config_dir: Path, dry_run: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(config_dir), dry_run],
        capture_output=True,
        text=True,
    )


class TestUsageErrors:
    def test_wrong_argc_exits_two(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "only-one-arg"], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()


class TestCliSweepsDeadPidMarker:
    def test_evicts_an_entry_with_a_dead_pid(self, tmp_path):
        active_dir = tmp_path / ".foo-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        entry = active_dir / "session-1"
        entry.write_text(f"{proc.pid}\n")

        result = _run_cli(tmp_path, "0")
        assert result.returncode == 0
        assert not entry.exists()
        assert "evicted 1 orphan(s), kept 0 active" in result.stdout

    def test_ignores_a_dotfile_entry_rather_than_reading_or_evicting_it(self, tmp_path):
        """A bare shell glob (e.g. "$active_dir"/*) never expands to a
        dotfile -- a stray .DS_Store must stay untouched and uncounted, not
        misread as a dead-PID marker and evicted."""
        active_dir = tmp_path / ".foo-active.d"
        active_dir.mkdir()
        dotfile = active_dir / ".DS_Store"
        dotfile.write_text("not a marker\n")

        result = _run_cli(tmp_path, "0")
        assert result.returncode == 0
        assert dotfile.exists()
        assert "evicted 0 orphan(s), kept 0 active" in result.stdout
