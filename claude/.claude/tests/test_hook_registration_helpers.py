"""Unit tests for helpers.py's settings.json registration lookups and the
`run_hook_raw` home guard, run against a tmp settings.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import (
    matcher_admits_tool,
    registered_hook_event_name,
    registered_hook_matchers,
    run_hook_raw,
)

HOOK = Path("/hooks/bar.sh")


def _settings_with(tmp_path: Path, hooks: dict[str, list[dict]]) -> Path:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"hooks": hooks}))
    return settings_path


def _group(command: str, matcher: str | None = None) -> dict:
    group: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


class TestRegisteredHookEventName:
    def test_resolves_the_single_registered_event(self, tmp_path):
        settings_path = _settings_with(tmp_path, {"PostToolUse": [_group("$HOME/.claude/hooks/bar.sh")]})
        assert registered_hook_event_name(HOOK, settings_path) == "PostToolUse"

    def test_hook_whose_name_merely_ends_with_the_target_does_not_match(self, tmp_path):
        """`foo-bar.sh` ends with `bar.sh` but is a different hook."""
        settings_path = _settings_with(tmp_path, {"PostToolUse": [_group("$HOME/.claude/hooks/foo-bar.sh")]})
        with pytest.raises(AssertionError, match="not found"):
            registered_hook_event_name(HOOK, settings_path)

    def test_two_event_registration_raises(self, tmp_path):
        settings_path = _settings_with(
            tmp_path,
            {
                "PostToolUse": [_group("$HOME/.claude/hooks/bar.sh")],
                "PreToolUse": [_group("$HOME/.claude/hooks/bar.sh")],
            },
        )
        with pytest.raises(AssertionError, match="multiple events"):
            registered_hook_event_name(HOOK, settings_path)

    def test_unregistered_hook_raises(self, tmp_path):
        settings_path = _settings_with(tmp_path, {"PostToolUse": [_group("$HOME/.claude/hooks/other.sh")]})
        with pytest.raises(AssertionError, match="not found"):
            registered_hook_event_name(HOOK, settings_path)


class TestRegisteredHookMatchers:
    def test_returns_the_matcher_of_each_registering_group(self, tmp_path):
        settings_path = _settings_with(
            tmp_path,
            {"PostToolUse": [_group("$HOME/.claude/hooks/bar.sh", "Write"), _group("$HOME/.claude/hooks/bar.sh")]},
        )
        assert registered_hook_matchers(HOOK, settings_path) == ["Write", ""]


class TestMatcherAdmitsTool:
    @pytest.mark.parametrize(
        ("matcher", "expected"),
        [
            ("", True),
            ("*", True),
            ("Write", True),
            ("Edit", False),
            ("Edit|Write", True),
        ],
    )
    def test_matcher_against_write(self, matcher, expected):
        assert matcher_admits_tool(matcher, "Write") is expected

    @pytest.mark.parametrize(
        ("matcher", "tool_name"),
        [
            ("Write", "WriteAll"),
            ("Write", "NotWrite"),
            ("Edit|Write", "MultiEdit"),
        ],
    )
    def test_matcher_must_match_the_whole_tool_name(self, matcher, tool_name):
        assert matcher_admits_tool(matcher, tool_name) is False


class TestRunHookRawHomeGuard:
    def test_none_home_raises_type_error(self):
        with pytest.raises(TypeError, match="home"):
            run_hook_raw(HOOK, {}, home=None)
