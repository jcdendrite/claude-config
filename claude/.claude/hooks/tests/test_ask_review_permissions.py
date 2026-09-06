"""Tests for ask-review-permissions.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    run_hook,
    run_hook_reason,
    write_input,
)

REVIEW_PERMS_HOOK = HOOKS_DIR / "ask-review-permissions.sh"


class TestAskReviewPermissions:
    @pytest.mark.parametrize(
        "tool_input",
        [
            edit_input("/some/project/.claude/settings.json"),
            edit_input("/some/project/.claude/settings.local.json"),
            write_input("/some/project/.claude/settings.json"),
        ],
        ids=["edit-settings", "edit-settings-local", "write-settings"],
    )
    def test_settings_edits_ask(self, tool_input):
        assert run_hook(REVIEW_PERMS_HOOK, tool_input) == "ask"

    @pytest.mark.parametrize(
        "path",
        [
            "/some/project/package.json",
            "/some/project/.claude/CLAUDE.md",
            "/some/project/.claude/skills/foo.md",
        ],
    )
    def test_non_settings_paths_allowed(self, path):
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(path)) == "allow"

    def test_bash_tool_allowed(self):
        assert run_hook(REVIEW_PERMS_HOOK, bash_input("cat /some/project/.claude/settings.json")) == "allow"

    def test_ask_reason_names_deny_and_default_mode(self):
        """Pins the ask-reason wording — it must name both permissions.deny
        and permissions.defaultMode, not just permissions.allow."""
        reason = run_hook_reason(
            REVIEW_PERMS_HOOK, edit_input("/some/project/.claude/settings.json")
        )
        assert reason is not None
        assert "permissions.deny" in reason
        assert "permissions.defaultMode" in reason

    def test_unreadable_lib_sh_fails_open_with_stderr_diagnostic(self, tmp_path):
        """dirname($0) resolves to HOOKS_DIR only when the hook runs from
        its real location; running a copy with no adjacent _lib.sh exercises
        the "could not source _lib.sh" exit-0 path directly. Fail-open is
        correct here (a broken _lib.sh must not block Edit/Write), but the
        silent-allow on a security-relevant gate must leave a stderr trail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / REVIEW_PERMS_HOOK.name
            shutil.copy2(REVIEW_PERMS_HOOK, tmp_hook)
            tmp_hook.chmod(0o755)
            result = subprocess.run(
                ["bash", str(tmp_hook)],
                input=json.dumps(edit_input("/some/project/.claude/settings.json")),
                cwd=str(tmp_path),
                env={**os.environ},
                capture_output=True,
                text=True,
                check=False,
            )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert "[ask-review-permissions]" in result.stderr
        assert "could not source _lib.sh" in result.stderr
