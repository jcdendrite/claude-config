"""Tests for plugins/lovable-cloud/hooks/consume-migration-token.sh.

These tests assert on filesystem state (token present/absent), not on hook
output — the consume hook never blocks and emits nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from helpers import posttooluse_input

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PLUGIN_ROOT = WORKTREE_ROOT / "plugins" / "lovable-cloud"
CONSUME_HOOK = PLUGIN_ROOT / "hooks" / "consume-migration-token.sh"


def _token_dir(home: Path) -> Path:
    return home / ".claude" / "lovable-cloud" / "migration-tokens"


def _place_token(home: Path, basename: str) -> Path:
    """Write a token file for the given migration basename."""
    token_dir = _token_dir(home)
    token_dir.mkdir(parents=True, exist_ok=True)
    token_path = token_dir / basename
    token_path.touch()
    return token_path


def _run_consume(payload: dict, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(CONSUME_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)},
        check=False,
    )


class TestTokenConsumption:
    # PostToolUse fires only after a tool call succeeds (harness contract).
    # No "failed Write" or "denied Write" case is tested here — the harness
    # guarantees those events are never delivered to PostToolUse hooks, so
    # a token written before a rejected Write is never consumed and will
    # block the next attempt (by design: re-generate to get a fresh token).

    def test_valid_migration_write_removes_token(self, tmp_path):
        basename = "20260612120000_add-users.sql"
        token = _place_token(tmp_path, basename)
        assert token.exists()
        result = _run_consume(
            posttooluse_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert result.returncode == 0, result.stderr
        assert not token.exists(), "Token should be removed after successful Write"

    def test_absent_token_is_noop(self, tmp_path):
        # No token exists — hook should exit 0 silently.
        basename = "20260612120000_add-users.sql"
        result = _run_consume(
            posttooluse_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert result.returncode == 0, result.stderr

    def test_non_migration_path_leaves_unrelated_token(self, tmp_path):
        # An unrelated token for a real migration should survive a non-migration Write.
        basename = "20260612120000_add-users.sql"
        token = _place_token(tmp_path, basename)
        result = _run_consume(
            posttooluse_input("src/components/Foo.tsx"), tmp_path
        )
        assert result.returncode == 0, result.stderr
        assert token.exists(), "Unrelated token must not be touched by a non-migration Write"

    def test_edit_tool_name_is_noop(self, tmp_path):
        # Defense-in-depth: Edit events must not consume tokens.
        basename = "20260612120000_add-users.sql"
        token = _place_token(tmp_path, basename)
        edit_payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": f"supabase/migrations/{basename}", "old_string": "a", "new_string": "b"},
        }
        result = _run_consume(edit_payload, tmp_path)
        assert result.returncode == 0, result.stderr
        assert token.exists(), "Edit tool must not consume migration tokens"

    def test_bash_tool_name_is_noop(self, tmp_path):
        basename = "20260612120000_add-users.sql"
        token = _place_token(tmp_path, basename)
        bash_payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
        }
        result = _run_consume(bash_payload, tmp_path)
        assert result.returncode == 0, result.stderr
        assert token.exists(), "Bash tool must not consume migration tokens"

    def test_malformed_json_exits_zero_no_crash(self, tmp_path):
        # Fail-open: malformed JSON must not cause a non-zero exit.
        result = subprocess.run(
            [str(CONSUME_HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)},
            check=False,
        )
        assert result.returncode == 0, f"Consume hook must exit 0 on malformed input: {result.stderr}"
