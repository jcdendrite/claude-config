"""Integration test: generator → PreToolUse allow → PostToolUse consume → deny.

This test drives the full one-shot lifecycle using the generator's actual
emitted token (never a hand-placed fixture), proving that the path the
generator writes is byte-identical to the path the hooks read.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from helpers import posttooluse_input, run_hook, write_input

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PLUGIN_ROOT = WORKTREE_ROOT / "plugins" / "lovable-cloud"
VALIDATE_HOOK = PLUGIN_ROOT / "hooks" / "validate-migration-filename.sh"
CONSUME_HOOK = PLUGIN_ROOT / "hooks" / "consume-migration-token.sh"
GENERATOR = PLUGIN_ROOT / "scripts" / "new-migration"


def _plugin_extra_env() -> dict:
    return {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}


def test_one_shot_lifecycle(tmp_path):
    # Step 1: run the generator to obtain an authorized filename and token.
    result = subprocess.run(
        [str(GENERATOR), "integration-test-slug"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
        check=True,
    )
    emitted_filename = result.stdout.strip()
    assert emitted_filename, "Generator must emit a filename to stdout"

    # Step 2: verify token was written at exactly the emitted basename
    # (path-agreement contract test between generator and hooks).
    token_path = (
        tmp_path / ".claude" / "lovable-cloud" / "migration-tokens" / emitted_filename
    )
    assert token_path.exists(), (
        f"Generator-written token not found at {token_path}"
    )

    # Step 3: PreToolUse validate — generated filename → allow.
    migration_path = f"supabase/migrations/{emitted_filename}"
    decision = run_hook(
        VALIDATE_HOOK,
        write_input(migration_path),
        home=tmp_path,
        extra_env=_plugin_extra_env(),
    )
    assert decision == "allow", (
        "PreToolUse should allow a Write with a valid generator token"
    )

    # Step 4: PostToolUse consume — token removed after successful Write.
    subprocess.run(
        [str(CONSUME_HOOK)],
        input=json.dumps(posttooluse_input(migration_path)),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
        },
        check=True,
    )
    assert not token_path.exists(), (
        "Token should be consumed (deleted) after PostToolUse Write"
    )

    # Step 5: second PreToolUse for the same name → deny (proves one-shot).
    decision = run_hook(
        VALIDATE_HOOK,
        write_input(migration_path),
        home=tmp_path,
        extra_env=_plugin_extra_env(),
    )
    assert decision == "deny", (
        "PreToolUse should deny a second Write for the same filename after token consumed"
    )
