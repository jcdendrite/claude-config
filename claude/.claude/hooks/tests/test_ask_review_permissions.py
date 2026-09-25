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
    multiedit_input,
    read_input,
    run_hook,
    run_hook_reason,
    write_input,
)

REVIEW_PERMS_HOOK = HOOKS_DIR / "ask-review-permissions.sh"


class TestAskReviewPermissions:
    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        """The hook also checks the resolved config-dir root (gap (c)), so every
        test needs an ambient CLAUDE_CONFIG_DIR that can't coincide with a test
        fixture path like /some/project/... A test that needs its own
        CLAUDE_CONFIG_DIR passes extra_env, which overrides this default."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "unused-ambient-config-dir"))

    @pytest.mark.parametrize(
        "tool_input",
        [
            edit_input("/some/project/.claude/settings.json"),
            edit_input("/some/project/.claude/settings.local.json"),
            write_input("/some/project/.claude/settings.json"),
            multiedit_input("/some/project/.claude/settings.json"),
        ],
        ids=["edit-settings", "edit-settings-local", "write-settings", "multiedit-settings"],
    )
    def test_settings_edits_ask(self, tool_input):
        assert run_hook(REVIEW_PERMS_HOOK, tool_input) == "ask"

    @pytest.mark.parametrize(
        "path",
        [
            "/some/project/package.json",
            "/some/project/.claude/CLAUDE.md",
            "/some/project/.claude/skills/foo.md",
            "/some/project/.claude/mysettings.json",
            "/some/project/.claude/settings/x.json",
        ],
    )
    @pytest.mark.parametrize(
        "build_input",
        [edit_input, write_input, multiedit_input],
        ids=["edit", "write", "multiedit"],
    )
    def test_non_settings_paths_allowed(self, build_input, path):
        assert run_hook(REVIEW_PERMS_HOOK, build_input(path)) == "allow"

    def test_bash_tool_allowed(self):
        assert run_hook(REVIEW_PERMS_HOOK, bash_input("cat /some/project/.claude/settings.json")) == "allow"

    def test_read_tool_on_settings_path_allowed(self):
        """A Bash tool_input has no file_path, so test_bash_tool_allowed passes
        regardless of whether the tool-name filter works: the path check falls
        through on the missing field either way. A Read on an actual settings
        path isolates the tool-name filter, since the path check alone would
        otherwise match."""
        assert run_hook(REVIEW_PERMS_HOOK, read_input("/some/project/.claude/settings.json")) == "allow"

    def test_settings_file_at_config_dir_root_with_no_claude_segment_asks(self, tmp_path):
        """gap (c): a config dir with no `.claude/` segment (e.g. a
        non-personal CLAUDE_CONFIG_DIR account root under
        ~/.local/state/claude-accounts/<account>/) still holds its own
        settings.json directly at its root."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )

    def test_settings_file_nested_under_config_dir_root_not_matched(self, tmp_path):
        """A settings file nested below the config-dir root, rather than
        directly at it, is outside gap (c)'s fixed shape and stays allowed —
        same [^/]* boundary the `.claude/settings*.json` match already uses."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / "nested" / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "file_path",
        [
            "/some/project/.claude//settings.json",
            "/some/project/.claude/./settings.json",
            "/some/project/.claude/sub/../settings.json",
            "/some/project/.CLAUDE/SETTINGS.json",
        ],
        ids=["double-slash", "dot-segment", "dotdot-segment", "case-variant"],
    )
    def test_aliased_or_case_varied_settings_path_asks(self, file_path):
        """gap (h): the hook's regex used to run on the raw, case-sensitive
        `file_path`. The alias decorations sit between `.claude` and
        `settings` specifically, so the raw string doesn't already contain the
        literal `.claude/settings` substring — a decoration elsewhere in the
        path (e.g. before `.claude`) would pass even without normalization,
        since the substring survives intact."""
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(file_path)) == "ask"

    def test_settings_path_through_symlinked_claude_ancestor_asks(self, tmp_path):
        """Regression control: _lib_realpath_m follows symlinks even under
        `-m`, so a dotfiles layout where `.claude` is itself a symlink to a
        differently-named real directory would resolve away the literal
        `.claude/settings...json` substring the normalized match relies on.
        The raw-path check runs alongside the normalized one, not in its
        place, specifically so this case still asks."""
        real_target = tmp_path / "dotfiles" / "claude"
        real_target.mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude").symlink_to(real_target, target_is_directory=True)
        file_path = project_dir / ".claude" / "settings.json"
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(str(file_path))) == "ask"

    def test_aliased_path_at_config_dir_root_asks(self, tmp_path):
        """Combines gap (h) and gap (c): an aliased path reaching a settings
        file at a config-dir root (no `.claude/` segment) needs the
        config-dir-root arm to also compare against the normalized path, not
        just the raw one."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / "sub" / ".." / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )

    @pytest.mark.parametrize(
        "filename",
        ["SETTINGS.json", "settings.local.json"],
        ids=["case-variant", "settings-local"],
    )
    def test_config_dir_root_settings_variants_ask(self, tmp_path, filename):
        """The config-dir-root arm (gap (c)) covers the same filename variants
        as the `.claude/settings*.json` arm: a case variant and
        settings.local.json, not just the bare lowercase settings.json
        already covered by test_settings_file_at_config_dir_root_with_no_claude_segment_asks."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / filename
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )

    def test_config_dir_dot_is_escaped_not_treated_as_wildcard(self, tmp_path):
        """Pins that the config-dir path's `.` escaping in the built grep -E
        pattern is real: an unescaped `.` matches any character, so a
        same-length sibling directory differing only at the dot's position
        would wrongly ask too if the escape were broken."""
        config_dir = tmp_path / "work.2024"
        exact_match_path = config_dir / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(exact_match_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )
        near_miss_path = tmp_path / "workX2024" / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(near_miss_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    def test_config_dir_resolution_failure_falls_through_to_allow(self, tmp_path):
        """Pins the hook's own `CONFIG_DIR=... || CONFIG_DIR=""` fallback when
        `_lib_config_dir` itself fails (CLAUDE_CONFIG_DIR empty and HOME
        empty, per `_lib_config_dir`'s own documented contract)."""
        file_path = tmp_path / "claude-accounts" / "work" / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": "", "HOME": ""},
            )
            == "allow"
        )

    def test_ask_reason_names_allow_deny_default_mode_and_review_skill(self):
        """Pins the ask-reason wording — it must name permissions.allow,
        permissions.deny, permissions.defaultMode and /review-permissions."""
        reason = run_hook_reason(
            REVIEW_PERMS_HOOK, edit_input("/some/project/.claude/settings.json")
        )
        assert reason is not None
        assert "permissions.deny" in reason
        assert "permissions.defaultMode" in reason
        assert "permissions.allow" in reason
        assert "/review-permissions" in reason

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
