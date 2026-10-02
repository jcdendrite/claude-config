"""Tests for session-marker-dashboard.sh.

The hook is a SessionStart hook (matcher: startup|clear|compact|resume) that
emits hookSpecificOutput.additionalContext — the harness injects this JSON
payload into the agent's conversation context on session start, /clear,
/compact, and a genuine session resume. It surfaces existing active-marker
state, and a review-narrative ledger summary, so Claude can see which
review-skill gates are currently bypassed and what the ledger recorded when
resuming after compaction.

Output is emitted only when at least one active marker is present or
stale, or the ledger has content to summarize — an all-absent state
(normal fresh session) produces no output.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    plant_traversal_canary,
)

from .conftest import _review_ledger_path

SESSION_MARKER_DASHBOARD_HOOK = HOOKS_DIR / "session-marker-dashboard.sh"


def _run_dashboard(
    payload: dict,
    isolated_home: Path,
    extra_env: dict | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(isolated_home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(SESSION_MARKER_DASHBOARD_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _additional_context(result: subprocess.CompletedProcess) -> str:
    """Parse the hookSpecificOutput.additionalContext from the hook's JSON output."""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def _system_message(result: subprocess.CompletedProcess) -> str:
    """Parse the top-level systemMessage (the engineer-visible channel) from the hook's JSON output."""
    return json.loads(result.stdout)["systemMessage"]


class TestSessionMarkerDashboard:
    def test_all_absent_produces_no_output(self, isolated_home):
        """When no active markers exist, the hook exits silently."""
        result = _run_dashboard({"session_id": "sess-absent"}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_fresh_plan_review_marker_emits_json(self, isolated_home):
        """A fresh plan-review-active marker triggers JSON dashboard output."""
        sid = "sess-pr-active"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active" in ctx
        assert "present" in ctx

    def test_stale_plan_review_marker_shows_stale(self, isolated_home):
        """A >60-min-old active marker is labelled 'stale' in the context."""
        sid = "sess-pr-stale"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "stale" in ctx
        assert "plan-review-active" in ctx

    def test_respond_pr_marker_triggers_output(self, isolated_home):
        """A respond-pr-active marker triggers dashboard output."""
        sid = "sess-rpr-active"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "respond-pr-active" in ctx
        assert "present" in ctx

    def test_ready_for_review_marker_triggers_output(self, isolated_home):
        """A ready-for-review-active marker triggers dashboard output."""
        sid = "sess-rfr-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "ready-for-review-active" in ctx
        assert "present" in ctx

    def test_other_sessions_marker_does_not_trigger(self, isolated_home):
        """Session A's active marker must not appear in session B's dashboard."""
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        result = _run_dashboard({"session_id": "session-B"}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_session_id_produces_no_output(self, isolated_home):
        """Missing session_id in the payload → hook exits silently."""
        result = _run_dashboard({}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_all_three_markers_all_shown(self, isolated_home):
        """When all three skills have active markers, all three appear in context."""
        sid = "sess-all-active"
        for skill in ("plan-review", "ready-for-review", "respond-pr"):
            marker_dir = isolated_home / ".claude" / f".{skill}-active.d"
            marker_dir.mkdir(parents=True)
            (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active: present" in ctx
        assert "ready-for-review-active: present" in ctx
        assert "respond-pr-active: present" in ctx

    def test_output_is_valid_json_with_correct_shape(self, isolated_home):
        """When markers are active, output must be parseable JSON with hookEventName + additionalContext."""
        sid = "sess-json-shape"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        hook_output = parsed["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "SessionStart"
        assert isinstance(hook_output["additionalContext"], str)

    def test_exit_0_always(self, isolated_home):
        """Hook must always exit 0 to avoid blocking session startup."""
        result = _run_dashboard({"session_id": "sess-exit-check"}, isolated_home)
        assert result.returncode == 0

    def test_malformed_json_does_not_block_and_emits_no_output(self, isolated_home):
        """hook-class: informational — malformed stdin must not block
        session startup. _lib_jq on unparseable input yields an empty
        SESSION_ID, which the hook's own empty-SESSION_ID guard rejects."""
        env = {**os.environ, "HOME": str(isolated_home)}
        result = subprocess.run(
            [str(SESSION_MARKER_DASHBOARD_HOOK)],
            input="not valid json {{",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    # -- CLAUDE_CONFIG_DIR ------------------------------------------------

    def test_fresh_plan_review_marker_under_config_dir_emits_json(self, isolated_home, tmp_path):
        """CLAUDE_CONFIG_DIR set: markers are read from the resolved config
        dir, not $HOME/.claude."""
        sid = "sess-pr-active-config-dir"
        config_dir = tmp_path / "profile"
        marker_dir = config_dir / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard(
            {"session_id": sid}, isolated_home, extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)}
        )
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active" in ctx
        assert "present" in ctx

    def test_legacy_home_marker_ignored_when_config_dir_set(self, isolated_home, tmp_path):
        """Config-dir resolution is a swap, not a union: a marker at the
        legacy $HOME/.claude location produces no output once
        CLAUDE_CONFIG_DIR points elsewhere."""
        sid = "sess-legacy-ignored"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        result = _run_dashboard(
            {"session_id": sid}, isolated_home, extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)}
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_relative_config_dir_produces_no_output(self, isolated_home):
        """CLAUDE_CONFIG_DIR set to a relative value cannot be resolved, so
        the dashboard exits with no output rather than falling back to the
        legacy $HOME/.claude marker directory."""
        sid = "sess-relative-config-dir"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard(
            {"session_id": sid}, isolated_home, extra_env={"CLAUDE_CONFIG_DIR": "relative/path"}
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_traversal_session_id_produces_no_output(self, isolated_home):
        """A traversal session_id must not make this read-only hook report
        marker status for a file outside the three *-active.d/ directories.

        All three directories (.plan-review-active.d, .ready-for-review-active.d,
        .respond-pr-active.d) sit one level under ~/.claude, so a session_id of
        '../canary' resolves every marker_status() call to the same file —
        planting a canary there would make all three read as "present",
        which is the discriminating signal: with the guard, the hook exits
        before any marker_status() call and produces the documented
        all-absent disposition (no output); without it, the canary would be
        misreported as three present markers and the hook would emit a
        dashboard payload.

        At least one *-active.d directory must already exist for this to be
        meaningful: `[ -f ]` on a path that walks '..' through a nonexistent
        directory component fails closed (ENOENT) regardless of the guard,
        which would make the traversal inert by accident."""
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        canary = plant_traversal_canary(isolated_home)

        result = _run_dashboard({"session_id": TRAVERSAL_SESSION_ID}, isolated_home)

        assert result.returncode == 0
        assert result.stdout == ""
        assert canary.read_text() == CANARY_CONTENT


class TestSessionMarkerDashboardLedgerSummary:
    """The review-narrative ledger extension: reads the ledger file
    review-ledger.sh appends to for the payload's cwd (the branch's file, or
    the session's own on a detached HEAD or the default branch) and folds a
    compact summary into the existing additionalContext output."""

    def _write_ledger(self, isolated_home: Path, repo: Path, sid: str, *records: dict) -> None:
        ledger = _review_ledger_path(isolated_home, repo, sid)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("".join(json.dumps(r) + "\n" for r in records))

    def test_ledger_present_and_non_empty_appends_summary(self, isolated_home, git_repo):
        """A non-empty ledger for this session folds a summary into the
        existing marker-status output (regression guard: marker text stays
        present alongside it)."""
        sid = "sess-ledger-present"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
            {"finding": "f2", "disposition": "DEFER", "rationale": "r", "source": "n/a"},
        )
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "2 findings recorded this session" in ctx
        assert "1 addressed" in ctx
        assert "1 deferred" in ctx
        assert "plan-review-active" in ctx, "existing marker-status output must be unchanged"

    def test_ledger_present_but_empty_adds_no_summary(self, isolated_home, git_repo):
        """An empty (zero-byte) ledger file for this session must not be
        treated as content to summarize."""
        sid = "sess-ledger-empty"
        ledger = _review_ledger_path(isolated_home, git_repo, sid)
        ledger.parent.mkdir(parents=True)
        ledger.touch()
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        assert result.stdout == "", (
            "an empty ledger, with no active markers either, must produce no output"
        )

    def test_ledger_with_only_clean_rows_adds_no_summary(self, isolated_home, git_repo):
        """A non-empty ledger holding only CLEAN dispositions (no ADDRESS/
        DEFER rows) must not be treated as content to summarize — the common
        all-clean-review case must match the absent/empty-ledger no-output
        behavior, not surface a "0 findings" line."""
        sid = "sess-ledger-clean-only"
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "n/a", "disposition": "CLEAN", "rationale": "r", "source": "n/a"},
        )
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        assert result.stdout == "", (
            "a CLEAN-only ledger, with no active markers either, must produce no output"
        )

    def test_ledger_with_clean_rows_interleaved_excludes_them_from_count(
        self, isolated_home, git_repo
    ):
        """CLEAN rows interleaved with ADDRESS/DEFER rows in the same ledger
        must be excluded from both the count and the total, not just when
        CLEAN is the only disposition present."""
        sid = "sess-ledger-clean-interleaved"
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "n/a", "disposition": "CLEAN", "rationale": "r", "source": "n/a"},
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
            {"finding": "n/a", "disposition": "CLEAN", "rationale": "r", "source": "n/a"},
            {"finding": "f2", "disposition": "DEFER", "rationale": "r", "source": "n/a"},
        )
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert ctx == (
            "2 findings recorded this session: 1 addressed, 1 deferred, 0 settled"
            " — run `review-ledger.sh show` for detail"
        ), "exact match guards against a digit-collision false total (e.g. '42 findings...')"

    def test_active_marker_with_clean_only_ledger_shows_marker_but_no_summary(
        self, isolated_home, git_repo
    ):
        """An active bypass marker alongside a CLEAN-only ledger exercises
        the elif-MARKER_BLOCK-only branch: the marker still reports, but the
        CLEAN-only ledger must not surface a findings summary."""
        sid = "sess-marker-with-clean-ledger"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "n/a", "disposition": "CLEAN", "rationale": "r", "source": "n/a"},
        )
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active" in ctx
        assert "findings recorded" not in ctx

    def test_ledger_with_unrecognized_disposition_adds_no_summary(self, isolated_home, git_repo):
        """A disposition value outside the ADDRESS|DEFER|CLEAN enum must be
        silently excluded from the count, the same as a CLEAN-only ledger,
        rather than leaking a spurious "0 findings" line."""
        sid = "sess-ledger-unrecognized-disposition"
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "n/a", "disposition": "WEIRD", "rationale": "r", "source": "n/a"},
        )
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        assert result.stdout == "", (
            "an unrecognized disposition, with no active markers either, must produce no output"
        )

    def test_ledger_absent_leaves_existing_marker_behavior_unchanged(self, isolated_home, git_repo):
        """Regression guard: with no ledger file at all, output is
        identical to the pre-ledger marker-only dashboard."""
        sid = "sess-no-ledger"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "respond-pr-active: present" in ctx
        assert "findings recorded" not in ctx

    def test_retired_opt_out_sentinel_keeps_summary_and_shows_engineer_notice(self, isolated_home, git_repo):
        """The sentinel file leaves the summary in place. The engineer sees a
        systemMessage stating the file is ignored, that quotes reach PR
        bodies, and that no replacement opt-out exists; none of that text
        enters the model's additionalContext."""
        sid = "sess-ledger-retired-sentinel"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
        )
        sentinel = isolated_home / ".claude" / ".review-narrative-ledger-disabled"
        sentinel.touch()

        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)

        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "ready-for-review-active: present" in ctx
        assert "1 findings recorded this session" in ctx, "the sentinel must not suppress the summary"
        assert "no longer honored" not in ctx
        notice = _system_message(result)
        assert str(sentinel) in notice
        assert "no longer honored" in notice
        assert "PR bodies" in notice
        assert "no replacement opt-out" in notice

    def test_retired_opt_out_sentinel_notice_alone_triggers_output(self, isolated_home, git_repo):
        """With no markers and no ledger rows, the sentinel notice is the only
        output, so the all-absent early exit must account for it. It is
        emitted on systemMessage alone, with no empty additionalContext."""
        sentinel = isolated_home / ".claude" / ".review-narrative-ledger-disabled"
        sentinel.touch()

        result = _run_dashboard(
            {"session_id": "sess-sentinel-only", "cwd": str(git_repo)}, isolated_home, cwd=git_repo
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert str(sentinel) in payload["systemMessage"]
        assert "hookSpecificOutput" not in payload

    def test_no_notice_when_retired_sentinel_is_absent(self, isolated_home, git_repo):
        """The notice is tied to the sentinel file; a ledger summary alone
        must not mention it."""
        sid = "sess-no-sentinel"
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
        )

        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)

        assert "no longer honored" not in _additional_context(result)
        assert "systemMessage" not in json.loads(result.stdout)

    def test_fresh_session_sees_other_sessions_branch_rows(self, isolated_home, git_repo):
        """A session with no rows of its own, on a feature branch another
        session reviewed, sees that branch's rows worded "on this branch"
        with the date span of the file."""
        subprocess.run(["git", "checkout", "-q", "-b", "feature/branch-rows"], cwd=git_repo, check=True)
        self._write_ledger(
            isolated_home,
            git_repo,
            "sess-earlier-author",
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a",
             "session_id": "sess-earlier-author", "event_time": "2026-09-01T10:00:00Z"},
            {"finding": "f2", "disposition": "DEFER", "rationale": "r", "source": "n/a",
             "session_id": "sess-earlier-author", "event_time": "2026-09-02T10:00:00Z"},
            {"finding": "f3", "disposition": "SETTLED", "rationale": "r", "source": "n/a",
             "session_id": "sess-other-author", "event_time": "2026-09-04T10:00:00Z"},
        )

        result = _run_dashboard(
            {"session_id": "sess-fresh-on-branch", "cwd": str(git_repo)}, isolated_home, cwd=git_repo
        )

        assert result.returncode == 0
        assert _additional_context(result) == (
            "3 findings recorded on this branch (2026-09-01 to 2026-09-04):"
            " 1 addressed, 1 deferred, 1 settled — run `review-ledger.sh show` for detail"
        )

    def test_settled_only_ledger_triggers_summary(self, isolated_home, git_repo):
        """A ledger holding only SETTLED rows is content to summarize; the
        gate on addressed plus deferred alone would print nothing."""
        subprocess.run(["git", "checkout", "-q", "-b", "feature/settled-only"], cwd=git_repo, check=True)
        self._write_ledger(
            isolated_home,
            git_repo,
            "sess-settled-author",
            {"finding": "f1", "disposition": "SETTLED", "rationale": "r", "source": "n/a",
             "session_id": "sess-settled-author", "event_time": "2026-09-03T10:00:00Z"},
        )

        result = _run_dashboard(
            {"session_id": "sess-settled-reader", "cwd": str(git_repo)}, isolated_home, cwd=git_repo
        )

        assert _additional_context(result) == (
            "1 findings recorded on this branch (2026-09-03):"
            " 0 addressed, 0 deferred, 1 settled — run `review-ledger.sh show` for detail"
        )

    def test_a_carry_row_counts_under_its_own_disposition(self, isolated_home, git_repo):
        """A carry row has no tally of its own: it is counted by the
        disposition it restates, like any other row."""
        subprocess.run(["git", "checkout", "-q", "-b", "feature/carry-row"], cwd=git_repo, check=True)
        self._write_ledger(
            isolated_home,
            git_repo,
            "sess-carry-author",
            {"finding": "f1", "disposition": "SETTLED", "decided_by": "engineer", "rationale": "r",
             "source": "a.py:1-2", "id": "a" * 12, "carry_forward": True,
             "session_id": "sess-carry-author", "event_time": "2026-09-03T10:00:00Z"},
            {"finding": "f2", "disposition": "SETTLED", "decided_by": "carry", "rationale": "same defect",
             "source": "a.py:1-2", "ref": "a" * 12, "id": "b" * 12,
             "session_id": "sess-carry-author", "event_time": "2026-09-04T10:00:00Z"},
        )

        result = _run_dashboard(
            {"session_id": "sess-carry-reader", "cwd": str(git_repo)}, isolated_home, cwd=git_repo
        )

        assert _additional_context(result) == (
            "2 findings recorded on this branch (2026-09-03 to 2026-09-04):"
            " 0 addressed, 0 deferred, 2 settled — run `review-ledger.sh show` for detail"
        )

    def test_detached_head_reads_session_file_with_session_wording(self, isolated_home, git_repo):
        """On a detached HEAD the ledger is the session's own file, so the
        summary is worded "this session" and ignores the branch's file."""
        subprocess.run(["git", "checkout", "-q", "-b", "feature/detached"], cwd=git_repo, check=True)
        branch_file = _review_ledger_path(isolated_home, git_repo, "sess-detached")
        branch_file.parent.mkdir(parents=True, exist_ok=True)
        branch_file.write_text(
            json.dumps({"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"}) + "\n"
        )
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=git_repo, check=True)
        self._write_ledger(
            isolated_home,
            git_repo,
            "sess-detached",
            {"finding": "f2", "disposition": "DEFER", "rationale": "r", "source": "n/a"},
            {"finding": "f3", "disposition": "DEFER", "rationale": "r", "source": "n/a"},
        )

        result = _run_dashboard(
            {"session_id": "sess-detached", "cwd": str(git_repo)}, isolated_home, cwd=git_repo
        )

        assert _additional_context(result) == (
            "2 findings recorded this session: 0 addressed, 2 deferred, 0 settled"
            " — run `review-ledger.sh show` for detail"
        )

    def _summary_for_rows(self, isolated_home: Path, git_repo: Path, sid: str, *records: dict) -> str:
        self._write_ledger(isolated_home, git_repo, sid, *records)
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        return _additional_context(result)

    @staticmethod
    def _address_row(**extra) -> dict:
        return {"finding": "f", "disposition": "ADDRESS", "rationale": "r", "source": "n/a", **extra}

    def test_date_span_ignores_rows_without_event_time(self, isolated_home, git_repo):
        """A file spanning the writer transition holds rows with and without
        event_time; only the dated rows bound the span, every row counts."""
        ctx = self._summary_for_rows(
            isolated_home,
            git_repo,
            "sess-span-mixed",
            self._address_row(event_time="2026-09-01T10:00:00Z"),
            self._address_row(),
            self._address_row(event_time="2026-09-03T10:00:00Z"),
        )

        assert ctx == (
            "3 findings recorded this session (2026-09-01 to 2026-09-03):"
            " 3 addressed, 0 deferred, 0 settled — run `review-ledger.sh show` for detail"
        )

    def test_date_span_ignores_non_string_event_time(self, isolated_home, git_repo):
        """A number, object, or null event_time cannot bound the span and must
        not break the summary."""
        ctx = self._summary_for_rows(
            isolated_home,
            git_repo,
            "sess-span-non-string",
            self._address_row(event_time=20260101),
            self._address_row(event_time={"at": "2026-01-01"}),
            self._address_row(event_time=None),
            self._address_row(event_time="2026-09-02T10:00:00Z"),
        )

        assert ctx == (
            "4 findings recorded this session (2026-09-02):"
            " 4 addressed, 0 deferred, 0 settled — run `review-ledger.sh show` for detail"
        )

    def test_date_span_uses_latest_date_even_when_not_in_last_row(self, isolated_home, git_repo):
        """The span is the min and max date over all rows, not first and last row."""
        ctx = self._summary_for_rows(
            isolated_home,
            git_repo,
            "sess-span-unordered",
            self._address_row(event_time="2026-09-03T10:00:00Z"),
            self._address_row(event_time="2026-09-05T10:00:00Z"),
            self._address_row(event_time="2026-09-01T10:00:00Z"),
        )

        assert ctx == (
            "3 findings recorded this session (2026-09-01 to 2026-09-05):"
            " 3 addressed, 0 deferred, 0 settled — run `review-ledger.sh show` for detail"
        )

    def test_corrupt_ledger_lines_are_skipped_not_fatal(self, isolated_home, git_repo):
        """A garbage line, a non-object line, and a torn unterminated last line
        must not hide the summary of the intact rows."""
        sid = "sess-ledger-corrupt"
        ledger = _review_ledger_path(isolated_home, git_repo, sid)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        intact_address = json.dumps(self._address_row(event_time="2026-09-01T10:00:00Z"))
        intact_defer = json.dumps({**self._address_row(event_time="2026-09-02T10:00:00Z"), "disposition": "DEFER"})
        ledger.write_text(
            f"{intact_address}\nnot json at all\n[1,2]\n{intact_defer}\n" + '{"finding":"torn","disposition":"ADDR'
        )

        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)

        assert result.returncode == 0
        assert _additional_context(result) == (
            "2 findings recorded this session (2026-09-01 to 2026-09-02):"
            " 1 addressed, 1 deferred, 0 settled — run `review-ledger.sh show` for detail"
        )

    def test_unresolvable_ledger_location_prints_no_summary(self, isolated_home, git_repo, tmp_path):
        """When git cannot read HEAD the resolver exits 1 (location unknown).
        The summary must print nothing, not fall back to a session file that
        happens to hold rows."""
        sid = "sess-unknown-location"
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
        )
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        # A git shim that fails `symbolic-ref` with a status above 1, the
        # "git could not read HEAD" signal, and delegates everything else.
        shim_dir = tmp_path / "failing-symbolic-ref-bin"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        shim.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '  if [ "$arg" = symbolic-ref ]; then exit 128; fi\n'
            "done\n"
            f'exec {shutil.which("git")} "$@"\n'
        )
        shim.chmod(0o755)

        control = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert "1 findings recorded this session" in _additional_context(control), (
            "without the shim the same fixture must summarize, so the shim is what suppresses it"
        )

        result = _run_dashboard(
            {"session_id": sid, "cwd": str(git_repo)},
            isolated_home,
            extra_env={"PATH": f"{shim_dir}:{os.environ['PATH']}"},
            cwd=git_repo,
        )

        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active" in ctx, "marker-status reporting must be unaffected"
        assert "findings recorded" not in ctx

    def test_ledger_summary_alone_triggers_output_with_no_active_markers(
        self, isolated_home, git_repo
    ):
        """A ledger with content, but no active bypass markers at all, must
        still produce output — the all-absent early exit must account for
        the ledger, not just the three marker statuses."""
        sid = "sess-ledger-only"
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
        )
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "1 findings recorded this session" in ctx

    def test_ledger_summary_resolves_against_payload_cwd_not_process_cwd(
        self, isolated_home, git_repo, tmp_path
    ):
        """Worktree-drift regression: process cwd (an unrelated repo with no
        ledger) differs from the payload's declared `.cwd` (git_repo, which
        has this session's ledger). A hook that read process cwd instead of
        the payload would compute the wrong repo-hash and find nothing."""
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=other_repo, check=True)

        sid = "sess-payload-cwd-drift"
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
        )
        result = _run_dashboard(
            {"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=other_repo
        )
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "1 findings recorded this session" in ctx

    def test_ledger_summary_absent_when_payload_cwd_is_not_a_git_repo(
        self, isolated_home, tmp_path
    ):
        """A payload `.cwd` outside any git repo must not block the
        always-on marker-status half of this hook — only the ledger-summary
        portion is skipped."""
        sid = "sess-payload-cwd-not-a-repo"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()

        result = _run_dashboard({"session_id": sid, "cwd": str(non_repo)}, isolated_home)

        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active" in ctx
        assert "findings recorded" not in ctx
