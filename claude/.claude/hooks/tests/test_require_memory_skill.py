"""Tests for require-memory-skill.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    assert_gate_handles_traversal_session_id,
    bash_input,
    edit_input,
    extract_skill_command,
    multiedit_input,
    run_hook,
    run_hook_reason,
    run_skill_command,
    write_input,
)

HOOK_PATH = HOOKS_DIR / "require-memory-skill.sh"
AI_MEMORY_SKILL = SKILLS_DIR / "ai-instruction-and-memory-files" / "SKILL.md"


@pytest.fixture
def memory_tree(isolated_home):
    """Populate a realistic auto-memory directory under the isolated $HOME.

    Creates:
      ~/.claude/projects/abc123/memory/MEMORY.md  (existing index)
      ~/.claude/projects/abc123/memory/user_role.md  (existing topic file)

    Returns the path to the memory directory.
    """
    mem_dir = isolated_home / ".claude" / "projects" / "abc123" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").touch()
    (mem_dir / "user_role.md").write_text("# User role\n")
    return mem_dir


def _memory_input(base_input: dict, session_id: str) -> dict:
    """Merge session_id into an existing tool-input dict."""
    return {**base_input, "session_id": session_id}


def _write_active_marker(isolated_home: Path, session_id: str) -> Path:
    """Create an active-bypass marker with the current process PID (alive)."""
    marker_dir = isolated_home / ".claude" / ".memory-skill-active.d"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / session_id
    marker.write_text(str(os.getpid()))
    return marker


class TestRequireMemorySkill:
    def test_memory_md_edit_blocked(self, isolated_home, memory_tree):
        """Edit on MEMORY.md is denied (no active marker for this session)."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-edit")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_memory_md_write_blocked(self, isolated_home, memory_tree):
        """Write to MEMORY.md is denied."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(write_input(memory_md), "sess-write")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_memory_md_multiedit_blocked(self, isolated_home, memory_tree):
        """MultiEdit on MEMORY.md is denied."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(multiedit_input(memory_md), "sess-multiedit")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_new_topic_file_write_blocked(self, isolated_home, memory_tree):
        """Write to a non-existent path under memory/ is denied (new topic file)."""
        new_topic = str(memory_tree / "new_topic.md")
        assert not Path(new_topic).exists()
        payload = _memory_input(write_input(new_topic), "sess-new-topic")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_existing_topic_file_edit_allowed(self, isolated_home, memory_tree):
        """Edit on an existing topic file passes through (only new files are gated)."""
        existing = str(memory_tree / "user_role.md")
        payload = _memory_input(edit_input(existing), "sess-existing-edit")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_existing_topic_file_write_allowed(self, isolated_home, memory_tree):
        """Write overwriting an existing topic file passes through."""
        existing = str(memory_tree / "user_role.md")
        payload = _memory_input(write_input(existing), "sess-existing-write")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_non_memory_path_allowed(self, isolated_home):
        """Edit on a path outside the memory directory passes through."""
        readme = str(isolated_home / "some-project" / "README.md")
        payload = _memory_input(edit_input(readme), "sess-non-memory")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_empty_json_object_denied(self):
        """'{}' → DENY (empty TOOL_NAME; path c: no .tool_name in payload)."""
        payload = {}
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_bash_tool_allowed(self, isolated_home):
        """Bash input passes through — self-filter by tool name."""
        payload = bash_input("echo hello", session_id="sess-bash")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_missing_session_id_allows(self, isolated_home, memory_tree):
        """Edit on MEMORY.md with no session_id in input passes through (fail open)."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = edit_input(memory_md)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_malformed_json_stdin(self, isolated_home):
        """Malformed JSON stdin must fail closed (deny), not silently allow."""
        import json
        result = subprocess.run(
            [str(HOOK_PATH)],
            input="not-valid-json{{{",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "Hook must emit a deny message on malformed JSON, not silent exit"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_deny_message_mentions_skill_name(self, isolated_home, memory_tree):
        """Deny reason must reference ai-instruction-and-memory-files."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-reason")
        reason = run_hook_reason(HOOK_PATH, payload)
        assert reason is not None
        assert "ai-instruction-and-memory-files" in reason

    # -- Active-marker bypass tests ------------------------------------------

    def test_active_marker_present_allows(self, isolated_home, memory_tree):
        """Fresh active-bypass marker allows Edit on MEMORY.md through."""
        sid = "sess-active-allow"
        _write_active_marker(isolated_home, sid)
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), sid)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_dead_pid_active_marker_evicts_and_denies(self, isolated_home, memory_tree):
        """Orphaned marker with a dead PID is evicted and the gate denies."""
        sid = "sess-dead-pid"
        marker_dir = isolated_home / ".claude" / ".memory-skill-active.d"
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = marker_dir / sid
        marker.write_text("99999999")  # PID outside Linux/macOS max range → always dead
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), sid)
        assert run_hook(HOOK_PATH, payload) == "deny"
        assert not marker.exists(), "hook must evict the orphan marker on dead PID"

    def test_active_marker_other_session_does_not_bypass(self, isolated_home, memory_tree):
        """Active marker keyed to a different session_id does not bypass this session."""
        _write_active_marker(isolated_home, "sess-other")
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-current")
        assert run_hook(HOOK_PATH, payload) == "deny"

    # -- Skill ↔ hook alignment -----------------------------------------------
    # Execute the SKILL.md activate / deactivate recipes verbatim. If the skill
    # body drifts from the marker layout require-memory-skill.sh expects, these fail.

    def test_skill_activate_command_creates_bypass_marker(
        self, isolated_home, memory_tree, git_repo
    ):
        """Run the SKILL.md activate-gate recipe and verify the resulting marker
        authorizes a previously-gated Edit on MEMORY.md."""
        sid = "test-session-memory-skill-activate"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        memory_md = str(memory_tree / "MEMORY.md")
        assert (
            run_hook(HOOK_PATH, _memory_input(edit_input(memory_md), sid)) == "deny"
        ), "precondition: MEMORY.md edit must be gated before activate runs"

        activate_command = extract_skill_command(AI_MEMORY_SKILL, "activate-gate")
        run_skill_command(activate_command, cwd=git_repo, isolated_home=isolated_home)

        marker = isolated_home / ".claude" / ".memory-skill-active.d" / sid
        assert marker.exists(), (
            "SKILL.md activate-gate recipe ran but no marker landed at the "
            "path the hook checks — the skill and hook disagree on layout."
        )
        assert run_hook(HOOK_PATH, _memory_input(edit_input(memory_md), sid)) == "allow"

    def test_skill_deactivate_command_removes_bypass_marker(
        self, isolated_home, memory_tree, git_repo
    ):
        """Run activate then deactivate from SKILL.md; verify deactivate removes
        the marker and the hook re-gates subsequent writes."""
        sid = "test-session-memory-skill-deactivate"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        activate_command = extract_skill_command(AI_MEMORY_SKILL, "activate-gate")
        run_skill_command(activate_command, cwd=git_repo, isolated_home=isolated_home)
        marker = isolated_home / ".claude" / ".memory-skill-active.d" / sid
        assert marker.exists(), "activate-gate setup did not create the marker"

        deactivate_command = extract_skill_command(AI_MEMORY_SKILL, "deactivate-gate")
        run_skill_command(deactivate_command, cwd=git_repo, isolated_home=isolated_home)

        assert not marker.exists(), (
            "SKILL.md deactivate-gate recipe ran but the marker is still "
            "present — the skill and hook disagree on the marker path."
        )
        memory_md = str(memory_tree / "MEMORY.md")
        assert run_hook(HOOK_PATH, _memory_input(edit_input(memory_md), sid)) == "deny"

    # -- Hostile session_id ---------------------------------------------------

    def test_traversal_session_id_denies_and_does_not_touch_marker_dir(
        self, isolated_home, memory_tree
    ):
        """A session_id of '../canary' must not read or write through the
        traversal: ACTIVE_MARKER concatenates it into
        .memory-skill-active.d/../canary, which resolves to a file one level
        up ($HOME/.claude/canary). The invalid id must skip the active-marker
        bypass entirely and fall through to the gate's normal deny — not be
        treated as an authorization to allow."""
        memory_md = str(memory_tree / "MEMORY.md")
        assert_gate_handles_traversal_session_id(
            HOOK_PATH,
            lambda sid: _memory_input(edit_input(memory_md), sid),
            isolated_home,
            expected_decision="deny",
        )
