"""Tests for plugins/lovable-cloud/hooks/validate-migration-filename.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from helpers import bash_input, edit_input, run_hook, run_hook_reason, write_input

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PLUGIN_ROOT = WORKTREE_ROOT / "plugins" / "lovable-cloud"
VALIDATE_HOOK = PLUGIN_ROOT / "hooks" / "validate-migration-filename.sh"


def _plugin_env(home: Path) -> dict:
    """Build the subprocess env with HOME and CLAUDE_PLUGIN_ROOT set."""
    return {
        **os.environ,
        "HOME": str(home),
        "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
    }


def _place_token(home: Path, basename: str) -> Path:
    """Write a token file for the given migration basename."""
    token_dir = home / ".claude" / "lovable-cloud" / "migration-tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    token_path = token_dir / basename
    token_path.touch()
    return token_path


def _hook_decision(tool_input: dict, home: Path) -> str:
    return run_hook(
        VALIDATE_HOOK,
        tool_input,
        home=home,
        extra_env={"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)},
    )


def _hook_reason(tool_input: dict, home: Path) -> str | None:
    return run_hook_reason(
        VALIDATE_HOOK,
        tool_input,
        home=home,
        extra_env={"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)},
    )


class TestTokenPresent:
    def test_token_present_for_valid_migration_path_allows(self, tmp_path):
        basename = "20260612120000_add-users.sql"
        _place_token(tmp_path, basename)
        decision = _hook_decision(
            write_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert decision == "allow"


class TestNoToken:
    def test_no_token_non_uuid_migration_denies(self, tmp_path):
        decision = _hook_decision(
            write_input("supabase/migrations/20260612120000_add-users.sql"), tmp_path
        )
        assert decision == "deny"


class TestUUIDExemption:
    def test_uuid_named_migration_allows(self, tmp_path):
        # Lovable-emitted files use lowercase UUID as the post-prefix segment.
        uuid_basename = "20260612120000_fa10d453-8db5-429b-b9f6-30af3fc13e66.sql"
        decision = _hook_decision(
            write_input(f"supabase/migrations/{uuid_basename}"), tmp_path
        )
        assert decision == "allow"

    def test_uppercase_uuid_denies(self, tmp_path):
        # UUID regex is lowercase-only; uppercase is not a Lovable emit.
        basename = "20260612120000_FA10D453-8DB5-429B-B9F6-30AF3FC13E66.sql"
        decision = _hook_decision(
            write_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert decision == "deny"

    def test_uuid_with_extra_suffix_denies(self, tmp_path):
        # A UUID-like segment with trailing content is not a clean UUID.
        basename = "20260612120000_fa10d453-8db5-429b-b9f6-30af3fc13e66-extra.sql"
        decision = _hook_decision(
            write_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert decision == "deny"

    def test_wrong_segment_length_uuid_denies(self, tmp_path):
        # 11 hex chars in the last group instead of 12 — the anchored regex
        # rejects it. This pins that grep -E is strict about segment length.
        basename = "20260612120000_fa10d453-8db5-429b-b9f6-30af3fc13e6.sql"
        decision = _hook_decision(
            write_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert decision == "deny"

    def test_mixed_case_uuid_denies(self, tmp_path):
        # UUID regex is [0-9a-f] without -i; one uppercase char must deny.
        # This pins that grep's -E flag is case-sensitive (no -i was added).
        basename = "20260612120000_Fa10d453-8db5-429b-b9f6-30af3fc13e66.sql"
        decision = _hook_decision(
            write_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert decision == "deny"


class TestNonMigrationPaths:
    def test_non_migration_write_allows(self, tmp_path):
        decision = _hook_decision(
            write_input("src/components/Foo.tsx"), tmp_path
        )
        assert decision == "allow"

    def test_edit_tool_same_migration_path_allows(self, tmp_path):
        # Defense-in-depth: Edit is not Write; hook should pass it through.
        decision = _hook_decision(
            edit_input("supabase/migrations/20260612120000_add-users.sql"), tmp_path
        )
        assert decision == "allow"

    def test_bash_tool_allows(self, tmp_path):
        decision = _hook_decision(
            bash_input("echo hello"), tmp_path
        )
        assert decision == "allow"


class TestMalformedInput:
    def test_malformed_json_stdin_denies(self, tmp_path):
        result = subprocess.run(
            [str(VALIDATE_HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            env=_plugin_env(tmp_path),
        )
        assert result.returncode == 0
        import json
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_missing_lib_sh_denies(self, tmp_path):
        # CLAUDE_PLUGIN_ROOT points to an empty dir — _lib.sh cannot be sourced.
        # The fail-closed guarantee: the hook must still emit deny and exit 0,
        # not crash silently. This pins the sourcing-failure path independently
        # of the JSON-parse-failure path.
        empty_plugin_root = tmp_path / "empty-plugin"
        empty_plugin_root.mkdir()
        result = subprocess.run(
            [str(VALIDATE_HOOK)],
            input='{"tool_name":"Write","tool_input":{"file_path":"supabase/migrations/20260612120000_add-users.sql"}}',
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(empty_plugin_root)},
        )
        assert result.returncode == 0, f"Hook must exit 0 even on sourcing failure: {result.stderr}"
        import json
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestTimestampPrefixBoundary:
    def test_13_digit_prefix_allows(self, tmp_path):
        # 13-digit prefix doesn't match the 14-digit pattern — not a migration.
        decision = _hook_decision(
            write_input("supabase/migrations/2026061212000_add-users.sql"), tmp_path
        )
        assert decision == "allow"

    def test_15_digit_prefix_allows(self, tmp_path):
        # 15-digit prefix doesn't match either.
        decision = _hook_decision(
            write_input("supabase/migrations/202606121200001_add-users.sql"), tmp_path
        )
        assert decision == "allow"

    def test_14_digits_without_underscore_allows(self, tmp_path):
        # 14 digits but no underscore separator — not a valid migration filename.
        decision = _hook_decision(
            write_input("supabase/migrations/20260612120000addusers.sql"), tmp_path
        )
        assert decision == "allow"

    def test_missing_14_digit_prefix_allows(self, tmp_path):
        decision = _hook_decision(
            write_input("supabase/migrations/add-users.sql"), tmp_path
        )
        assert decision == "allow"


class TestDenyMessage:
    def test_deny_message_contains_resolved_generator_path(self, tmp_path):
        # The deny message must contain a real filesystem path ending in
        # scripts/new-migration, not the literal ${CLAUDE_PLUGIN_ROOT} string.
        reason = _hook_reason(
            write_input("supabase/migrations/20260612120000_add-users.sql"), tmp_path
        )
        assert reason is not None
        assert "scripts/new-migration" in reason, (
            f"Deny reason should reference scripts/new-migration: {reason!r}"
        )
        assert "${CLAUDE_PLUGIN_ROOT}" not in reason, (
            f"Deny reason must not contain literal variable, got: {reason!r}"
        )

    def test_deny_message_contains_blocked_filename(self, tmp_path):
        basename = "20260612120000_add-users.sql"
        reason = _hook_reason(
            write_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert reason is not None
        assert basename in reason, (
            f"Deny reason should contain the blocked filename {basename!r}: {reason!r}"
        )


class TestPathTraversalPolicy:
    def test_traversal_path_with_migration_suffix_token_allows(self, tmp_path):
        # A path with traversal components that still ends in the migration suffix
        # matches the grep pattern (which searches anywhere in the string, not
        # from the start). With a valid token the hook allows — the gate is
        # keyed on the basename, not the traversal context. This behavior is
        # deliberate: the Write tool controls the actual destination; the hook
        # gates on the migration naming convention, not filesystem safety.
        basename = "20260612120000_add-users.sql"
        _place_token(tmp_path, basename)
        decision = _hook_decision(
            write_input(f"../../supabase/migrations/{basename}"), tmp_path
        )
        assert decision == "allow"

    def test_locally_plausible_filename_without_token_denies(self, tmp_path):
        # A valid-looking 14-digit filename a developer might type by hand
        # (identical in structure to a UTC generator output) is denied without
        # a token. UTC enforcement comes from token presence — the hook does not
        # parse or compare timestamps; it only checks that the generator ran.
        basename = "20260612093000_add-missing-index.sql"  # plausible local-time entry
        decision = _hook_decision(
            write_input(f"supabase/migrations/{basename}"), tmp_path
        )
        assert decision == "deny", (
            "A hand-typed valid-looking filename must be denied without a generator token"
        )
