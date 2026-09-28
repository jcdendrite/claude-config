"""Tests for nudge-answer-provenance.sh.

PostToolUse AskUserQuestion hook that reports via `additionalContext` a
static reminder separating the engineer's selected label/typed text from
the model's own option descriptions. A menu answer carries only the label,
so the model can restate its own option prose as the engineer's decision.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from helpers import HOOKS_DIR, REPO_ROOT, _build_subprocess_env, build_path_without, run_hook_context

HOOK = HOOKS_DIR / "nudge-answer-provenance.sh"
CLAUDE_MD = REPO_ROOT / "claude" / ".claude" / "CLAUDE.md"
SETTINGS_PATH = REPO_ROOT / "claude" / ".claude" / "settings.json"

DRIFT_PHRASE = "Attribute to the engineer only what they said"
DRIFT_BULLET_LEAD = re.compile(r"^- \*\*" + re.escape(DRIFT_PHRASE), re.MULTILINE)

# tool_response's shape here is illustrative, not verified against a real
# AskUserQuestion harness capture -- the hook only reads tool_name, so the
# shape doesn't affect correctness today (see test_fires_with_tool_response_absent).
ASK_USER_QUESTION_INPUT = {
    "tool_name": "AskUserQuestion",
    "tool_response": {
        "questions": [
            {
                "question": "Which approach?",
                "answers": [{"label": "Option A", "text": "Option A"}],
            }
        ]
    },
}


def _run_hook(stdin_text: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = _build_subprocess_env(None, extra_env)
    return subprocess.run(
        [str(HOOK)], input=stdin_text, capture_output=True, text=True, env=env, check=False
    )


def _assert_fires(payload: dict) -> None:
    """Asserts a PostToolUse advisory with non-empty additionalContext."""
    result = _run_hook(json.dumps(payload))
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert parsed["hookSpecificOutput"]["additionalContext"]


def _assert_silent(stdin_text: str) -> None:
    result = _run_hook(stdin_text)
    assert result.returncode == 0
    assert not result.stdout.strip()


def _section_body(markdown: str, heading: str) -> str:
    """Returns the text between `## <heading>` and the next `## ` heading (or end of file)."""
    match = re.search(
        r"^## " + re.escape(heading) + r"\n(.*?)(?=^## |\Z)", markdown, re.MULTILINE | re.DOTALL
    )
    assert match, f"no `## {heading}` section"
    return match.group(1)


class TestFiresOnAskUserQuestion:
    def test_fires_on_ask_user_question_payload(self):
        _assert_fires(ASK_USER_QUESTION_INPUT)

    def test_fires_with_tool_response_absent(self):
        _assert_fires({"tool_name": "AskUserQuestion"})

    def test_fires_on_full_posttooluse_envelope_with_string_tool_response(self):
        _assert_fires(
            {
                "session_id": "00000000-0000-0000-0000-000000000000",
                "transcript_path": "/tmp/example-session/transcript.jsonl",
                "cwd": "/tmp/example-project",
                "hook_event_name": "PostToolUse",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which approach?",
                            "options": [{"label": "Option A", "description": "Do A."}],
                        }
                    ]
                },
                "tool_response": 'User has answered your questions: "Which approach?"="Option A".',
                "tool_use_id": "toolu_00000000000000000000",
            }
        )


class TestSilentOnOtherTools:
    def test_silent_on_bash(self):
        _assert_silent(json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}))

    @pytest.mark.parametrize(
        "near_miss_tool_name",
        ["mcp__x__AskUserQuestion", "askuserquestion", "AskUserQuestion "],
    )
    def test_silent_on_near_miss_tool_name(self, near_miss_tool_name):
        _assert_silent(json.dumps({"tool_name": near_miss_tool_name}))


class TestFailsOpen:
    def test_empty_stdin_stays_silent(self):
        result = _run_hook("")
        assert result.returncode == 0
        assert not result.stdout.strip()

    def test_malformed_json_stays_silent(self):
        result = _run_hook("not json")
        assert result.returncode == 0
        assert not result.stdout.strip()

    def test_jq_absent_stays_silent(self, tmp_path):
        farm_dir = tmp_path / "farm"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)
        result = _run_hook(json.dumps(ASK_USER_QUESTION_INPUT), extra_env={"PATH": path_without_jq})
        assert result.returncode == 0
        assert not result.stdout.strip()

    @pytest.mark.parametrize(
        "wrong_shape_stdin",
        ["[]", "123", "null", "{}", '{"tool_name": null}', "  \n"],
        ids=["array", "number", "null", "empty-object", "null-tool-name", "whitespace-only"],
    )
    def test_valid_json_of_wrong_shape_stays_silent(self, wrong_shape_stdin):
        _assert_silent(wrong_shape_stdin)

    def test_missing_lib_sh_stays_silent(self, tmp_path):
        """Missing `_lib.sh` next to the hook fails open (exit 0, no output)."""
        tmp_hook = tmp_path / HOOK.name
        shutil.copy2(HOOK, tmp_hook)
        tmp_hook.chmod(0o755)
        result = subprocess.run(
            [str(tmp_hook)],
            input=json.dumps(ASK_USER_QUESTION_INPUT),
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=False,
        )
        assert result.returncode == 0
        assert not result.stdout.strip()


class TestDriftGuard:
    def test_drift_phrase_appears_in_runtime_output_and_as_claude_md_bullet_lead(self):
        """The quoted CLAUDE.md phrase must appear in the parsed runtime output, not just the .sh source."""
        ctx = run_hook_context(HOOK, ASK_USER_QUESTION_INPUT)
        assert DRIFT_PHRASE in ctx
        working_style_section = _section_body(CLAUDE_MD.read_text(), "Working Style")
        assert DRIFT_BULLET_LEAD.search(working_style_section)


class TestWiring:
    def test_registered_under_askuserquestion_matcher_in_settings_json(self):
        settings = json.loads(SETTINGS_PATH.read_text())
        post_tool_use = settings["hooks"]["PostToolUse"]
        matching_groups = [g for g in post_tool_use if g.get("matcher") == "AskUserQuestion"]
        assert matching_groups, "no PostToolUse group with matcher \"AskUserQuestion\""
        commands = [h.get("command", "") for g in matching_groups for h in g["hooks"]]
        assert "~/.claude/hooks/nudge-answer-provenance.sh" in commands

    def test_exactly_one_posttooluse_hook_entry_references_the_script(self):
        settings = json.loads(SETTINGS_PATH.read_text())
        referencing_entries = [
            h
            for g in settings["hooks"]["PostToolUse"]
            for h in g["hooks"]
            if HOOK.name in h.get("command", "")
        ]
        assert len(referencing_entries) == 1
