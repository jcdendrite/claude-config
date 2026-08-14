"""Tests for ask-review-permissions.sh."""
from __future__ import annotations

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
