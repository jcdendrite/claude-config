"""Tests for activate-handoff-bypass.sh."""
from __future__ import annotations

from pathlib import Path

import pytest
from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    bash_input,
    install_marker_script,
    plant_traversal_canary,
    run_hook,
    run_hook_until_marker_exists,
    skill_input,
)

from .conftest import _seed_session

ACTIVATE_HOOK = HOOKS_DIR / "activate-handoff-bypass.sh"


def _handoff_active_marker_path(home: Path, session_id: str) -> Path:
    """The `/handoff` active-bypass marker path -- same layout
    nudge-handoff-near-context-cap.sh reads and `marker.sh activate handoff`
    writes."""
    return home / ".claude" / ".handoff-active.d" / session_id


def _drift_marker_path(home: Path, session_id: str) -> Path:
    return home / ".claude" / ".activate-handoff-bypass-drift.d" / session_id


def _activation_failed_marker_path(home: Path, session_id: str) -> Path:
    return home / ".claude" / ".activate-handoff-bypass-drift.d" / f"{session_id}-activation-failed"


def _drift_log_path(home: Path) -> Path:
    return home / ".claude" / ".activate-handoff-bypass.log"


class TestActivateHandoffBypass:
    @pytest.mark.timing
    def test_handoff_skill_load_activates_the_marker(self, isolated_home):
        sid = "session-activate-handoff"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        payload = skill_input("handoff", session_id=sid)
        assert run_hook(ACTIVATE_HOOK, payload) == "allow"
        run_hook_until_marker_exists(ACTIVATE_HOOK, payload, _handoff_active_marker_path(isolated_home, sid))

    def test_different_skill_name_is_noop(self, isolated_home):
        sid = "session-other-skill"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        run_hook(ACTIVATE_HOOK, skill_input("branch-management", session_id=sid))
        assert not _handoff_active_marker_path(isolated_home, sid).exists()

    def test_wrong_tool_name_is_noop(self, isolated_home):
        sid = "session-wrong-tool"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        run_hook(ACTIVATE_HOOK, bash_input("echo hi", session_id=sid))
        assert not _handoff_active_marker_path(isolated_home, sid).exists()

    def test_agent_type_set_is_noop(self, isolated_home):
        """A subagent-issued Skill(handoff) call must not disarm the parent
        session's block -- marker.sh resolves the session via ancestor walk
        to the Claude main process, not from this payload's own fields."""
        sid = "session-subagent"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        run_hook(ACTIVATE_HOOK, skill_input("handoff", session_id=sid, agent_type="Explore"))
        assert not _handoff_active_marker_path(isolated_home, sid).exists()

    def test_malformed_tool_input_is_noop(self, isolated_home):
        sid = "session-malformed"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        payload = {"tool_name": "Skill", "tool_input": "not-an-object", "session_id": sid}
        assert run_hook(ACTIVATE_HOOK, payload) == "allow"
        assert not _handoff_active_marker_path(isolated_home, sid).exists()

    @pytest.mark.timing
    def test_directory_qualified_skill_name_activates(self, isolated_home):
        """A repo whose stow source sits under a `.claude` ancestor
        directory renders directory-qualified (e.g.
        `.claude/worktrees/<branch>/claude:skill`), per
        activate-handoff-bypass.sh's own label-normalization comment."""
        sid = "session-dir-qualified"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        name = ".claude/worktrees/some-branch/claude:handoff"
        payload = skill_input(name, session_id=sid)
        run_hook_until_marker_exists(ACTIVATE_HOOK, payload, _handoff_active_marker_path(isolated_home, sid))

    @pytest.mark.timing
    def test_plugin_qualified_skill_name_activates(self, isolated_home):
        sid = "session-plugin-qualified"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        payload = skill_input("some-plugin:handoff", session_id=sid)
        run_hook_until_marker_exists(ACTIVATE_HOOK, payload, _handoff_active_marker_path(isolated_home, sid))

    def test_missing_skill_field_logs_drift_and_noops(self, isolated_home):
        """A well-formed Skill tool_input with no `skill` key at all --
        distinguishes 'the field was renamed upstream' from 'this call is
        for a different skill', which would otherwise both exit 0 silently."""
        sid = "session-drift"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        payload = {"tool_name": "Skill", "tool_input": {}, "session_id": sid}
        assert run_hook(ACTIVATE_HOOK, payload) == "allow"
        assert not _handoff_active_marker_path(isolated_home, sid).exists()
        log = _drift_log_path(isolated_home)
        assert log.exists()
        log_text = log.read_text()
        assert "schema-drift" in log_text
        assert f"session={sid}" in log_text
        assert _drift_marker_path(isolated_home, sid).exists()

    def test_missing_skill_field_only_logs_once_per_session(self, isolated_home):
        """Mirrors nudge-handoff-near-context-cap.sh's own DRIFT_MARKER dedup."""
        sid = "session-drift-dedup"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        payload = {"tool_name": "Skill", "tool_input": {}, "session_id": sid}
        run_hook(ACTIVATE_HOOK, payload)
        run_hook(ACTIVATE_HOOK, payload)
        drift_lines = [
            line
            for line in _drift_log_path(isolated_home).read_text().splitlines()
            if "schema-drift" in line
        ]
        assert len(drift_lines) == 1

    def test_marker_script_failure_still_allows(self, isolated_home):
        """A non-executable marker.sh (simulating a bad PATH/exec failure)
        must not turn into a propagated failure -- the hook stays fail-open
        on every path, matching nudge-handoff-near-context-cap.sh's own
        `_lib_capped_for` discipline. A failed activation must still surface
        as an activation-failed log line, under a tag distinct from
        schema-drift, so the two cases aren't conflated in the log."""
        sid = "session-marker-fails"
        broken = isolated_home / ".claude" / "scripts" / "marker.sh"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("#!/bin/bash\nexit 1\n")
        _seed_session(isolated_home, sid)
        assert run_hook(ACTIVATE_HOOK, skill_input("handoff", session_id=sid)) == "allow"
        assert not _handoff_active_marker_path(isolated_home, sid).exists()
        log_text = _drift_log_path(isolated_home).read_text()
        assert "activation-failed" in log_text
        assert f"session={sid}" in log_text
        assert _activation_failed_marker_path(isolated_home, sid).exists()

    def test_marker_script_failure_only_logs_once_per_session(self, isolated_home):
        """Mirrors the schema-drift dedup: a repeated activation failure for
        the same session must not grow the log unbounded."""
        sid = "session-marker-fails-dedup"
        broken = isolated_home / ".claude" / "scripts" / "marker.sh"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("#!/bin/bash\nexit 1\n")
        _seed_session(isolated_home, sid)
        run_hook(ACTIVATE_HOOK, skill_input("handoff", session_id=sid))
        run_hook(ACTIVATE_HOOK, skill_input("handoff", session_id=sid))
        failure_lines = [
            line
            for line in _drift_log_path(isolated_home).read_text().splitlines()
            if "activation-failed" in line
        ]
        assert len(failure_lines) == 1

    def test_traversal_session_id_does_not_touch_drift_marker_dir(self, isolated_home):
        """A session_id of '../canary' must not reach the drift-log write."""
        canary = plant_traversal_canary(isolated_home)
        mtime_before = canary.stat().st_mtime_ns

        payload = {"tool_name": "Skill", "tool_input": {}, "session_id": TRAVERSAL_SESSION_ID}
        assert run_hook(ACTIVATE_HOOK, payload) == "allow"
        assert canary.stat().st_mtime_ns == mtime_before, (
            "a traversal session_id must not touch a file outside "
            ".activate-handoff-bypass-drift.d/"
        )
        assert canary.read_text() == CANARY_CONTENT

    def test_hook_always_exits_allow(self, isolated_home):
        sid = "session-always-allow"
        install_marker_script(isolated_home)
        _seed_session(isolated_home, sid)
        assert run_hook(ACTIVATE_HOOK, skill_input("handoff", session_id=sid)) == "allow"
