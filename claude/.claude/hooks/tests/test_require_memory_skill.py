"""Tests for require-memory-skill.sh."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    multiedit_input,
    run_hook,
    run_hook_reason,
    write_input,
)

HOOK_PATH = HOOKS_DIR / "require-memory-skill.sh"


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


def write_transcript(tmp_path: Path, messages: list[dict]) -> str:
    """Write a JSONL transcript file and return its path as a string.

    Each dict in `messages` is written as one JSON line. Real Claude Code
    transcript user records include at least `type` and `uuid` fields.
    """
    transcript = tmp_path / "transcript.jsonl"
    with transcript.open("w") as fh:
        for msg in messages:
            fh.write(json.dumps(msg) + "\n")
    return str(transcript)


def _memory_input(base_input: dict, session_id: str, transcript_path: str) -> dict:
    """Merge session_id and transcript_path into an existing tool-input dict."""
    return {**base_input, "session_id": session_id, "transcript_path": transcript_path}


class TestRequireMemorySkill:
    def test_memory_md_edit_blocked(self, isolated_home, memory_tree, tmp_path):
        """Edit on MEMORY.md is denied (no marker for this turn)."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-1"}]
        )
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-edit", transcript)
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_memory_md_write_blocked(self, isolated_home, memory_tree, tmp_path):
        """Write to MEMORY.md is denied."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-2"}]
        )
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(write_input(memory_md), "sess-write", transcript)
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_memory_md_multiedit_blocked(self, isolated_home, memory_tree, tmp_path):
        """MultiEdit on MEMORY.md is denied."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-3"}]
        )
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(multiedit_input(memory_md), "sess-multiedit", transcript)
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_new_topic_file_write_blocked(self, isolated_home, memory_tree, tmp_path):
        """Write to a non-existent path under memory/ is denied (new topic file)."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-4"}]
        )
        new_topic = str(memory_tree / "new_topic.md")
        # Confirm the file doesn't exist — the hook's class-(b) check requires it.
        assert not Path(new_topic).exists()
        payload = _memory_input(write_input(new_topic), "sess-new-topic", transcript)
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_existing_topic_file_edit_allowed(self, isolated_home, memory_tree, tmp_path):
        """Edit on an existing topic file passes through (only new files are gated)."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-5"}]
        )
        existing = str(memory_tree / "user_role.md")
        payload = _memory_input(edit_input(existing), "sess-existing-edit", transcript)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_existing_topic_file_write_allowed(self, isolated_home, memory_tree, tmp_path):
        """Write overwriting an existing topic file passes through."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-6"}]
        )
        existing = str(memory_tree / "user_role.md")
        payload = _memory_input(write_input(existing), "sess-existing-write", transcript)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_non_memory_path_allowed(self, isolated_home, tmp_path):
        """Edit on a path outside the memory directory passes through."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-7"}]
        )
        readme = str(isolated_home / "some-project" / "README.md")
        payload = _memory_input(edit_input(readme), "sess-non-memory", transcript)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_bash_tool_allowed(self, isolated_home, tmp_path):
        """Bash input passes through — self-filter by tool name."""
        payload = bash_input("echo hello", session_id="sess-bash")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_per_turn_debounce_same_uuid(self, isolated_home, memory_tree, tmp_path):
        """First deny writes the marker; second call in the same turn is allowed."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-dedup"}]
        )
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-dedup", transcript)

        # First call → deny and write marker.
        assert run_hook(HOOK_PATH, payload) == "deny"

        # Second call with the same transcript (same latest uuid) → allow.
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_new_turn_re_blocks(self, isolated_home, memory_tree, tmp_path):
        """After a new user message, the next call is denied again."""
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text(
            json.dumps({"type": "user", "uuid": "uuid-turn1"}) + "\n"
        )
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(
            edit_input(memory_md), "sess-newturn", str(transcript_path)
        )

        # First turn → deny.
        assert run_hook(HOOK_PATH, payload) == "deny"

        # Append a new user message (new turn).
        with transcript_path.open("a") as fh:
            fh.write(json.dumps({"type": "user", "uuid": "uuid-turn2"}) + "\n")

        # The payload references the same transcript path — hook re-reads it.
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_missing_session_id_allows(self, isolated_home, memory_tree, tmp_path):
        """Edit on MEMORY.md with no session_id in input passes through (fail open)."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-nosess"}]
        )
        memory_md = str(memory_tree / "MEMORY.md")
        # Build payload without session_id.
        payload = {**edit_input(memory_md), "transcript_path": transcript}
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_missing_transcript_path_allows(self, isolated_home, memory_tree, tmp_path):
        """Edit on MEMORY.md with no transcript_path in input passes through."""
        memory_md = str(memory_tree / "MEMORY.md")
        # Build payload without transcript_path.
        payload = {**edit_input(memory_md), "session_id": "sess-notranscript"}
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_transcript_no_user_messages_allows(self, isolated_home, memory_tree, tmp_path):
        """Transcript with no type=='user' entries passes through (fail open)."""
        transcript = write_transcript(
            tmp_path,
            [{"type": "assistant", "uuid": "uuid-asst"}],
        )
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-nousers", transcript)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_malformed_json_stdin(self, isolated_home, tmp_path):
        """Malformed JSON stdin must not crash the hook and must exit 0."""
        result = subprocess.run(
            [str(HOOK_PATH)],
            input="not-valid-json{{{",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_deny_message_mentions_skill_name(self, isolated_home, memory_tree, tmp_path):
        """Deny reason must reference ai-instruction-and-memory-files so the agent
        knows which skill to invoke."""
        transcript = write_transcript(
            tmp_path, [{"type": "user", "uuid": "uuid-reason-check"}]
        )
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-reason", transcript)
        reason = run_hook_reason(HOOK_PATH, payload)
        assert reason is not None
        assert "ai-instruction-and-memory-files" in reason
