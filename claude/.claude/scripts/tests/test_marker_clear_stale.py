"""Direct unit tests for marker-clear-stale.py's `pid_alive` and
`read_no_follow`, isolating them from the sweep loop that calls them.
`marker.sh clear-stale`'s aggregate stdout is covered separately by
claude/.claude/hooks/tests/test_marker_script.py's TestMarkerScriptClearStale.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR, write_review_pr_provenance

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

    def test_true_for_eperm_a_live_process_owned_by_another_user(self, monkeypatch):
        """os.kill raising PermissionError means the process exists and is
        merely inaccessible -- not the same as ProcessLookupError's genuinely
        dead PID, so it must not be misread as an eviction candidate."""

        def fake_kill(pid, sig):
            raise PermissionError("synthetic EPERM")

        monkeypatch.setattr(_clear_stale.os, "kill", fake_kill)
        assert _clear_stale.pid_alive("1") is True

    def test_false_for_non_numeric_text(self):
        assert _clear_stale.pid_alive("not-a-pid") is False

    def test_false_for_a_negative_number(self):
        assert _clear_stale.pid_alive("-1") is False

    def test_false_for_zero_rather_than_signalling_the_callers_process_group(self):
        assert _clear_stale.pid_alive("0") is False

    def test_false_for_a_pid_too_large_for_the_c_pid_type(self):
        assert _clear_stale.pid_alive("9" * 40) is False

    def test_false_for_digit_text_past_pythons_int_conversion_limit(self):
        assert _clear_stale.pid_alive("9" * 5000) is False


def _write_session_file(sessions_dir: Path, pid: int, session_id: str, start_time: str | None = None) -> None:
    """Write sessions/<pid> in capture-session-id.sh's two-line format. A None
    start_time records the process's real `ps -o lstart=` value, so the entry
    reads as a live, unreused PID."""
    if start_time is None:
        start_time = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.rstrip("\n")
    sessions_dir.mkdir(exist_ok=True)
    (sessions_dir / str(pid)).write_text(f"{session_id}\n{start_time}\n")


class TestSweepReviewPrSuffixBranch:
    """sweep()'s REVIEW_PR_SUFFIXES branch derives the owning PID from a
    sibling .provenance file rather than the entry's own content -- a
    separate rule from the generic single-file PID+mtime branch every other
    sweep() test here exercises. Calls sweep() directly (already imported
    via importlib above), not the CLI, since these cases are about the
    branch's own eviction logic, not its stdout formatting."""

    def test_review_pr_entry_with_a_live_sibling_pid_is_kept(self, tmp_path):
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        (active_dir / "alive-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", os.getpid(),
            mode="checkout", session_id="alive-session", config_dir=tmp_path,
        )

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
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="dead-session", config_dir=tmp_path,
        )

        # Both the .body entry and its self-referential .provenance entry
        # (see the live-sibling test above) are evicted.
        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (2, 0)
        assert not (active_dir / "dead-session.body").exists()
        assert not (active_dir / "dead-session.provenance").exists()

    def test_review_pr_entry_with_a_dead_provenance_pid_but_a_live_session_entry_is_kept(self, tmp_path):
        """A resumed session runs under a new PID, so its provenance still
        names the old, dead one; capture-session-id.sh's sessions/<pid> file
        for the live PID is what shows the session is still running."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "resumed-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="resumed-session", config_dir=tmp_path,
        )
        _write_session_file(tmp_path / "sessions", os.getpid(), "resumed-session")

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (0, 2)
        assert (active_dir / "resumed-session.body").exists()
        assert (active_dir / "resumed-session.provenance").exists()

    def test_review_pr_entry_whose_live_session_entry_has_a_mismatched_start_time_is_evicted(self, tmp_path):
        """A sessions/<pid> file naming a live PID whose recorded start time
        differs from the process's current one is a reused PID, the same rule
        _lib_resolve_claude_pid applies, so it does not count as a live session."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "reused-pid-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="reused-pid-session", config_dir=tmp_path,
        )
        _write_session_file(
            tmp_path / "sessions", os.getpid(), "reused-pid-session", start_time="Thu Jan  1 00:00:00 1970"
        )

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (2, 0)
        assert not (active_dir / "reused-pid-session.body").exists()
        assert not (active_dir / "reused-pid-session.provenance").exists()

    @pytest.mark.parametrize(
        "ps_failure",
        [OSError("synthetic: ps cannot run"), subprocess.TimeoutExpired(["ps"], 5)],
        ids=["ps_cannot_run", "ps_times_out"],
    )
    def test_review_pr_entry_is_kept_when_ps_cannot_verify_the_live_sessions_start_time(
        self, tmp_path, monkeypatch, ps_failure
    ):
        """An unverifiable start time reads as a match, so a live review's
        artifacts are never evicted on a ps failure."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "resumed-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="resumed-session", config_dir=tmp_path,
        )
        _write_session_file(tmp_path / "sessions", os.getpid(), "resumed-session")

        def failing_ps(*args, **kwargs):
            raise ps_failure

        monkeypatch.setattr(_clear_stale.subprocess, "run", failing_ps)
        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (0, 2)
        assert (active_dir / "resumed-session.body").exists()
        assert (active_dir / "resumed-session.provenance").exists()

    def test_review_pr_entry_is_evicted_when_ps_cannot_run_and_the_session_entry_names_a_dead_pid(
        self, tmp_path, monkeypatch
    ):
        """With ps unable to verify any start time, only the liveness check on
        the sessions/<pid> file's own PID separates a dead session's leftover
        entry from a live one."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "ended-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="ended-session", config_dir=tmp_path,
        )
        _write_session_file(
            tmp_path / "sessions", proc.pid, "ended-session", start_time="Thu Jan  1 00:00:00 1970"
        )

        def failing_ps(*args, **kwargs):
            raise OSError("synthetic: ps cannot run")

        monkeypatch.setattr(_clear_stale.subprocess, "run", failing_ps)
        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (2, 0)
        assert not (active_dir / "ended-session.body").exists()
        assert not (active_dir / "ended-session.provenance").exists()

    def test_review_pr_entry_whose_owning_session_differs_from_a_live_session_entry_is_evicted(self, tmp_path):
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "ended-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="ended-session", config_dir=tmp_path,
        )
        _write_session_file(tmp_path / "sessions", os.getpid(), "some-other-live-session")

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (2, 0)
        assert not (active_dir / "ended-session.body").exists()
        assert not (active_dir / "ended-session.provenance").exists()

    def test_dry_run_reports_a_live_session_entry_as_the_keep_reason(self, tmp_path):
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "resumed-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="resumed-session", config_dir=tmp_path,
        )
        _write_session_file(tmp_path / "sessions", os.getpid(), "resumed-session")

        evicted, kept, lines = _clear_stale.sweep(str(tmp_path), dry_run=True)
        assert (evicted, kept) == (0, 2)
        assert "  keep: .review-pr-active.d/resumed-session.body (owning session resumed-session alive)" in lines
        assert (active_dir / "resumed-session.body").exists()

    def test_review_pr_entry_with_a_dead_provenance_pid_and_only_a_dead_session_entry_is_evicted(self, tmp_path):
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (active_dir / "ended-session.body").write_text("findings\n")
        write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", proc.pid,
            mode="checkout", session_id="ended-session", config_dir=tmp_path,
        )
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / str(proc.pid)).write_text("ended-session\nstart-time\n")

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (2, 0)
        assert not (active_dir / "ended-session.body").exists()
        assert not (active_dir / "ended-session.provenance").exists()

    def test_review_pr_entry_with_provenance_without_a_schema_header_is_kept(self, tmp_path):
        """A provenance file with no `schema=1` header has no determinable
        liveness, so its artifacts are kept. Mirrors _lib.sh's
        _lib_review_pr_provenance_field, which fails closed the same way."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        (active_dir / "headerless-session.body").write_text("findings\n")
        # No header line, no "pid=" key.
        (active_dir / "headerless-session.provenance").write_text(
            "\n".join(["foo/bar#42", "abc123", str(os.getpid()), "checkout"]) + "\n"
        )

        # Both the .body entry and its self-referential .provenance entry
        # (see the live-sibling test above) are kept.
        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (0, 2)
        assert (active_dir / "headerless-session.body").exists()
        assert (active_dir / "headerless-session.provenance").exists()

    @pytest.mark.parametrize("strip_pid_line", [True, False], ids=["no_pid_line", "empty_pid_value"])
    def test_review_pr_entry_with_schema_1_provenance_lacking_a_pid_is_evicted(
        self, tmp_path, strip_pid_line
    ):
        """A well-formed schema=1 provenance file that carries no usable
        owner PID (no `pid=` line, or `pid=` with an empty value) is
        recognized, so the owner is known to be undeterminable rather than
        the format being unrecognized: the .body and the .provenance file
        itself are both evicted. The helper renders its empty-string pid as
        a bare `pid=` line; `no_pid_line` then removes that line."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        (active_dir / "no-pid-session.body").write_text("findings\n")
        provenance = write_review_pr_provenance(
            tmp_path, "foo/bar#42", "abc123", "",
            mode="checkout", session_id="no-pid-session", config_dir=tmp_path,
        )
        if strip_pid_line:
            provenance.write_text(
                "".join(
                    line for line in provenance.read_text().splitlines(keepends=True)
                    if not line.startswith("pid=")
                )
            )

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (2, 0)
        assert not (active_dir / "no-pid-session.body").exists()
        assert not provenance.exists()

    def test_review_pr_entry_with_an_empty_provenance_file_is_kept(self, tmp_path):
        """An empty provenance file must be kept, not evicted: the writer
        (_lib_write_no_follow) opens with O_TRUNC before it writes, so a
        concurrent sweep can observe an empty file for a live review.
        Keeping it is what makes that window safe. No age bound applies, so
        a writer that crashed mid-write leaves an empty file that pins its
        sibling artifacts until they are removed by hand; the file is aged
        to the epoch here to pin that."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        (active_dir / "truncated-session.body").write_text("findings\n")
        empty_provenance = active_dir / "truncated-session.provenance"
        empty_provenance.write_text("")
        os.utime(empty_provenance, (0, 0))

        evicted, kept, _lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept) == (0, 2)
        assert (active_dir / "truncated-session.body").exists()
        assert (active_dir / "truncated-session.provenance").exists()

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


class TestReviewPrActiveDirEntryWithAnUnknownSuffixIsLeftAlone:
    def test_entry_with_no_review_pr_suffix_is_neither_read_as_a_pid_nor_evicted(self, tmp_path):
        """Without the skip, the generic branch would read this entry's content
        as a PID, evict it, and echo that content in the eviction line."""
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        entry = active_dir / "session.unknown-artifact"
        entry.write_text("content that is not a PID\n")

        evicted, kept, lines = _clear_stale.sweep(str(tmp_path), dry_run=False)
        assert (evicted, kept, lines) == (0, 0, [])
        assert entry.exists()


class TestFailedRemovalIsNotCountedAsAnEviction:
    """os.remove is replaced rather than a directory made read-only, so the
    failure is injected the same way whether or not the suite runs as root."""

    @staticmethod
    def _fail_removal_of(monkeypatch, failing_path: Path) -> None:
        real_remove = os.remove

        def remove_that_fails_for_one_path(path, *args, **kwargs):
            if Path(path) == failing_path:
                raise PermissionError(1, "Operation not permitted", str(path))
            real_remove(path, *args, **kwargs)

        monkeypatch.setattr(_clear_stale.os, "remove", remove_that_fails_for_one_path)

    def test_generic_branch_entry_whose_removal_fails_gets_a_failed_line_and_no_eviction(
        self, tmp_path, monkeypatch
    ):
        active_dir = tmp_path / ".foo-active.d"
        active_dir.mkdir()
        proc = subprocess.Popen(["true"])
        proc.wait()
        stuck_entry = active_dir / "stuck-session"
        stuck_entry.write_text(f"{proc.pid}\n")
        removable_entry = active_dir / "removable-session"
        removable_entry.write_text(f"{proc.pid}\n")

        with monkeypatch.context() as patch:
            self._fail_removal_of(patch, stuck_entry)
            evicted, kept, lines = _clear_stale.sweep(str(tmp_path), dry_run=False)

        assert (evicted, kept) == (1, 0)
        assert stuck_entry.exists()
        assert not removable_entry.exists()
        assert [line for line in lines if line.startswith("  evict: ")] == [
            f"  evict: .foo-active.d/removable-session (PID {proc.pid} dead)"
        ]
        assert [line for line in lines if line.startswith("  failed: ")] == [
            f"  failed: .foo-active.d/stuck-session (PID {proc.pid} dead; removal failed: Operation not permitted)"
        ]

    def test_review_pr_branch_entry_whose_removal_fails_gets_a_failed_line_and_no_eviction(
        self, tmp_path, monkeypatch
    ):
        active_dir = tmp_path / ".review-pr-active.d"
        active_dir.mkdir()
        stuck_entry = active_dir / "orphan-session.body"
        stuck_entry.write_text("findings\n")

        with monkeypatch.context() as patch:
            self._fail_removal_of(patch, stuck_entry)
            evicted, kept, lines = _clear_stale.sweep(str(tmp_path), dry_run=False)

        assert (evicted, kept) == (0, 0)
        assert stuck_entry.exists()
        assert lines == [
            "  failed: .review-pr-active.d/orphan-session.body "
            "(owning PID empty dead; removal failed: Operation not permitted)"
        ]


class TestReviewPrArtifactSuffixEnumerationsAgree:
    """Three enumerations of review-pr's session artifact suffixes live apart:
    review-pr-finish.sh's removal list, marker-clear-stale.py's
    REVIEW_PR_SUFFIXES, and every other script's calls to
    _lib_review_pr_artifact_path. A suffix missing from one is never removed or
    never swept."""

    _ARTIFACT_PATH_NAME = "_lib_review_pr_artifact_path"
    _ARTIFACT_PATH_CALL = re.compile(
        _ARTIFACT_PATH_NAME + r'\s+"?\$\{?CONFIG_DIR\}?"?\s+"[^"]+"\s+"?([A-Za-z0-9_.]+)"?'
    )

    @classmethod
    def _suffixes_named_in(cls, script_paths: list[Path]) -> set[str]:
        """Suffixes of every non-comment call site. Fails when a call site is
        in a shape the pattern cannot parse, so it never drops out silently."""
        suffixes: set[str] = set()
        for script_path in script_paths:
            code = "\n".join(
                line for line in script_path.read_text().splitlines() if not line.lstrip().startswith("#")
            )
            call_sites = code.count(cls._ARTIFACT_PATH_NAME) - code.count(f"{cls._ARTIFACT_PATH_NAME}()")
            parsed_suffixes = cls._ARTIFACT_PATH_CALL.findall(code)
            assert len(parsed_suffixes) == call_sites, (
                f"{script_path.name} has {call_sites} {cls._ARTIFACT_PATH_NAME} call sites "
                f"but {len(parsed_suffixes)} parsed; widen _ARTIFACT_PATH_CALL"
            )
            suffixes.update(parsed_suffixes)
        return suffixes

    def test_finish_removal_list_clear_stale_suffixes_and_writers_name_the_same_suffixes(self):
        finish_script = SCRIPTS_DIR / "review-pr-finish.sh"
        other_scripts = [path for path in sorted(SCRIPTS_DIR.glob("*.sh")) if path != finish_script]

        finish_suffixes = self._suffixes_named_in([finish_script])
        clear_stale_suffixes = {suffix.removeprefix(".") for suffix in _clear_stale.REVIEW_PR_SUFFIXES}
        script_suffixes = self._suffixes_named_in(other_scripts)

        assert finish_suffixes == clear_stale_suffixes == script_suffixes


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

    def test_config_dir_name_with_glob_metacharacters_is_matched_literally(self, tmp_path):
        """A config dir whose own path holds `[ab]` must still be swept: the
        path is not a glob pattern, only the `.*-active.d` suffix is."""
        config_dir = tmp_path / "config[ab]"
        active_dir = config_dir / ".foo-active.d"
        active_dir.mkdir(parents=True)
        proc = subprocess.Popen(["true"])
        proc.wait()
        entry = active_dir / "session-1"
        entry.write_text(f"{proc.pid}\n")

        result = _run_cli(config_dir, "0")
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
