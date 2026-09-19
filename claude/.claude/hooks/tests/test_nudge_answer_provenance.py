"""Tests for nudge-answer-provenance.sh.

PostToolUse AskUserQuestion hook that reports via `additionalContext` a
static reminder separating the engineer's selected label/typed text from
the model's own option descriptions -- see
`.claude/plans/attribution-provenance-guard.md` for the incident this
backstops.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from helpers import HOOKS_DIR, REPO_ROOT, _build_subprocess_env, build_path_without, run_hook_context

HOOK = HOOKS_DIR / "nudge-answer-provenance.sh"
CLAUDE_MD = REPO_ROOT / "claude" / ".claude" / "CLAUDE.md"
SETTINGS_PATH = REPO_ROOT / "claude" / ".claude" / "settings.json"

DRIFT_PHRASE = "Attribute to the engineer only what they said"

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
    """Shared assertion for every "fires" case: hookEventName pins the
    payload as a PostToolUse advisory (not, say, a PreToolUse decision
    shape), and additionalContext is what actually reaches the model."""
    result = _run_hook(json.dumps(payload))
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert parsed["hookSpecificOutput"]["additionalContext"]
    assert run_hook_context(HOOK, payload)


class TestFiresOnAskUserQuestion:
    def test_fires_on_ask_user_question_payload(self):
        _assert_fires(ASK_USER_QUESTION_INPUT)

    def test_fires_with_tool_response_absent(self):
        _assert_fires({"tool_name": "AskUserQuestion"})


class TestSilentOnOtherTools:
    def test_silent_on_bash(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}
        assert run_hook_context(HOOK, payload) is None


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

    def test_missing_lib_sh_stays_silent(self, tmp_path):
        """Hook copied to a directory with no _lib.sh alongside it, so
        `${0%/*}/_lib.sh` fails to source -- proves the informational
        hook-class's documented fail-open contract (exit 0, no output)
        rather than a crash, mirroring test_hook_alignment.py's gate-hook
        missing-lib-sh test but for this hook's silent-allow shape instead
        of a deny."""
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
    def test_drift_phrase_appears_in_runtime_output_and_claude_md(self):
        """The hook's additionalContext restates CLAUDE.md's Working Style
        bullet's bold lead for salience at the moment the answer arrives --
        pinned against the parsed runtime output, not the .sh source text,
        so a future edit that breaks the quoted match still shows up here."""
        ctx = run_hook_context(HOOK, ASK_USER_QUESTION_INPUT)
        assert DRIFT_PHRASE in ctx
        assert DRIFT_PHRASE in CLAUDE_MD.read_text()


class TestWiring:
    def test_registered_under_askuserquestion_matcher_in_settings_json(self):
        settings = json.loads(SETTINGS_PATH.read_text())
        post_tool_use = settings["hooks"]["PostToolUse"]
        matching_groups = [g for g in post_tool_use if g.get("matcher") == "AskUserQuestion"]
        assert matching_groups, "no PostToolUse group with matcher \"AskUserQuestion\""
        commands = [h["command"] for g in matching_groups for h in g["hooks"]]
        assert "~/.claude/hooks/nudge-answer-provenance.sh" in commands

    def test_hook_file_exists(self):
        assert HOOK.is_file()
