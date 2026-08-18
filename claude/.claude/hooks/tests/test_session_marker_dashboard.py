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

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    git_toplevel,
    plant_traversal_canary,
)

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


def _ledger_path(isolated_home: Path, repo: Path, session_id: str) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return (
        isolated_home / ".claude" / "review-narrative-ledger" / f"{repo_hash}.{session_id}.jsonl"
    )


def _additional_context(result: subprocess.CompletedProcess) -> str:
    """Parse the hookSpecificOutput.additionalContext from the hook's JSON output."""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


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
    """The review-narrative ledger extension: reads this session's ledger
    file (keyed the same way review-ledger.sh keys writes) and folds a
    compact summary into the existing additionalContext output."""

    def _write_ledger(self, isolated_home: Path, repo: Path, sid: str, *records: dict) -> None:
        ledger = _ledger_path(isolated_home, repo, sid)
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
        ledger = _ledger_path(isolated_home, git_repo, sid)
        ledger.parent.mkdir(parents=True)
        ledger.touch()
        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)
        assert result.returncode == 0
        assert result.stdout == "", (
            "an empty ledger, with no active markers either, must produce no output"
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

    def test_kill_switch_suppresses_only_ledger_portion(self, isolated_home, git_repo):
        """The kill switch gates the new ledger-summary behavior only —
        the existing marker-status reporting stays always-on."""
        sid = "sess-ledger-killswitch"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        self._write_ledger(
            isolated_home,
            git_repo,
            sid,
            {"finding": "f1", "disposition": "ADDRESS", "rationale": "r", "source": "n/a"},
        )
        (isolated_home / ".claude" / ".review-narrative-ledger-disabled").touch()

        result = _run_dashboard({"session_id": sid, "cwd": str(git_repo)}, isolated_home, cwd=git_repo)

        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "ready-for-review-active: present" in ctx, (
            "marker-status reporting must stay unaffected by the ledger kill switch"
        )
        assert "findings recorded" not in ctx, (
            "the kill switch must suppress the ledger summary"
        )

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
