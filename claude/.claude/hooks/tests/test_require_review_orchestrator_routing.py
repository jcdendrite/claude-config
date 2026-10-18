"""Tests for require-review-orchestrator-routing.sh.

The gate denies a main-session Skill(code-review) call while the
review_orchestrator_routing key is on, unless the call's args begin with the
word `unrouted`. test_hook_alignment.py covers the fail-closed payload cases
every gate shares, so this file covers the gate's own allow rules, its key
reads and its deny text.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, REPO_ROOT, SKILLS_DIR

HOOK = HOOKS_DIR / "require-review-orchestrator-routing.sh"
SUBAGENT_DELEGATION_SKILL = SKILLS_DIR / "subagent-delegation" / "SKILL.md"
_SETTINGS_PATH = REPO_ROOT / "claude" / ".claude" / "settings.json"
_SCHEMA_PSV = HOOKS_DIR / "config-keys.psv"

KEY_NAME = "review_orchestrator_routing"
# The documented enable line, which the install notes tell an engineer to write.
ENABLE_LINE = f"{KEY_NAME} = true\n"
SUBAGENT_ID = "agent-0123456789abcdef"
_OMIT = object()

# The directory-qualified form of the name, as sent when the stow source sits under a `.claude` ancestor.
DIRECTORY_QUALIFIED_CODE_REVIEW = ".claude/worktrees/some-branch/claude:code-review"


def _payload(
    *,
    tool_name: str = "Skill",
    skill=_OMIT,
    args=_OMIT,
    agent_id=_OMIT,
    agent_type=_OMIT,
) -> dict:
    """A PreToolUse payload for a Skill call. Defaults give the baseline-deny
    shape (skill `code-review`, no args, no `agent_id`); pass `_OMIT` to leave
    a field out and `None` to send JSON null. Field names and types follow the
    payload shape listed under "Observed before the change shipped" in
    docs/design-decisions/review-orchestrator-routing-gate.md."""
    tool_input: dict = {}
    tool_input["skill"] = "code-review" if skill is _OMIT else skill
    if args is not _OMIT:
        tool_input["args"] = args
    payload: dict = {"tool_name": tool_name, "tool_input": tool_input}
    if agent_id is not _OMIT:
        payload["agent_id"] = agent_id
    if agent_type is not _OMIT:
        payload["agent_type"] = agent_type
    return payload


def _payload_without_skill() -> dict:
    payload = _payload()
    del payload["tool_input"]["skill"]
    return payload


def _run_gate(
    tmp_path: Path,
    payload: dict,
    *,
    key_line: str | None = ENABLE_LINE,
    hook: Path = HOOK,
    env_overrides: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the gate with an isolated HOME and an explicit CLAUDE_CONFIG_DIR.
    `key_line` is the full text written to the state file, or None for no
    state file."""
    home = tmp_path / "home"
    config_dir = home / ".claude"
    config_dir.mkdir(parents=True, exist_ok=True)
    if key_line is not None:
        (config_dir / "claude-config.toml").write_text(key_line)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )


def _assert_allows(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stdout == "", result.stdout


def _deny_reason(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    return output["permissionDecisionReason"]


def _assert_denies(result: subprocess.CompletedProcess) -> None:
    _deny_reason(result)


# --- jq filter recording ------------------------------------------------------

SKILL_FIELD_FILTER = "tool_input.skill"
ARGS_FIELD_FILTER = "tool_input.args"


def _recording_jq_standin(tmp_path: Path, fail_on_filter: str | None = None) -> tuple[dict, Path]:
    """Return (env overrides, call log) for a `jq` stand-in that execs the
    real jq by absolute path and appends each call's argv to the call log. It
    exits 1 instead of running jq when an argument contains `fail_on_filter`
    (None fails none)."""
    real_jq = shutil.which("jq")
    if real_jq is None:
        pytest.skip("jq not found in PATH")
    shim_dir = tmp_path / "jq-shim"
    shim_dir.mkdir()
    call_log = tmp_path / "jq-calls"
    shim = shim_dir / "jq"
    shim.write_text(
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$*" >> "{call_log}"\n'
        + (
            f'for arg in "$@"; do case "$arg" in *{shlex.quote(fail_on_filter)}*) exit 1 ;; esac; done\n'
            if fail_on_filter
            else ""
        )
        + f'exec "{real_jq}" "$@"\n'
    )
    shim.chmod(0o755)
    env = {"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
    return env, call_log


def _field_read_filters_run(call_log: Path) -> set[str]:
    """Which of the gate's two field-read filters appear in the recorded calls."""
    recorded = call_log.read_text() if call_log.exists() else ""
    return {name for name in (SKILL_FIELD_FILTER, ARGS_FIELD_FILTER) if name in recorded}


# --- deny ---------------------------------------------------------------------


class TestBaselineDeny:
    @pytest.fixture(scope="class")
    def baseline_deny_reason(self, tmp_path_factory):
        return _deny_reason(_run_gate(tmp_path_factory.mktemp("baseline-deny"), _payload()))

    def test_deny_reason_starts_with_the_gate_label(self, baseline_deny_reason):
        assert baseline_deny_reason.startswith("Blocked by review-orchestrator routing gate: ")

    @pytest.mark.parametrize(
        ("substring", "why"),
        [
            pytest.param(
                'args "unrouted"',
                "names the retry spelling for a round with no diff path",
                id="bare_retry_spelling",
            ),
            pytest.param(
                'args "unrouted <path>"',
                "names the retry spelling for a round with a diff path",
                id="path_retry_spelling",
            ),
            pytest.param(
                "without the quotation marks",
                "warns off the quoted spelling the gate denies",
                id="quotation_marks_warning",
            ),
            pytest.param(
                "review-orchestrator agent",
                "names the agent the round routes to, in the body past the label",
                id="routing_target",
            ),
            pytest.param(
                "subagent-delegation",
                "points at the skill that owns the protocol",
                id="protocol_skill",
            ),
            pytest.param(
                "/ready-for-review",
                "names the cumulative-pass inline case",
                id="inline_case_ready_for_review",
            ),
            pytest.param(
                "/review-pr",
                "names the one-off review inline case",
                id="inline_case_review_pr",
            ),
            pytest.param(
                "HALT fallback",
                "names the orchestrator-halt inline case",
                id="inline_case_halt_fallback",
            ),
            pytest.param(
                "inline round the user asks for",
                "names the user-requested inline case",
                id="inline_case_user_request",
            ),
            pytest.param(
                "report this denial to your dispatcher",
                "tells a subagent not to resolve the denial itself",
                id="subagent_instruction",
            ),
        ],
    )
    def test_deny_reason_carries(self, baseline_deny_reason, substring, why):
        assert substring in baseline_deny_reason, why

    def test_deny_reason_cites_a_live_protocol_heading(self, baseline_deny_reason):
        skill_lines = SUBAGENT_DELEGATION_SKILL.read_text(encoding="utf-8").splitlines()
        assert f"### {_cited_heading(baseline_deny_reason)}" in skill_lines

    def test_protocol_section_carries_the_unrouted_args_the_deny_text_names(self, baseline_deny_reason):
        section = _section_from_heading(
            SUBAGENT_DELEGATION_SKILL.read_text(encoding="utf-8"),
            f"### {_cited_heading(baseline_deny_reason)}",
        )

        assert 'args "unrouted"' in " ".join(section.split())

    @pytest.mark.parametrize(
        "skill",
        [
            pytest.param("plugin-x:code-review", id="plugin_qualified"),
            pytest.param(DIRECTORY_QUALIFIED_CODE_REVIEW, id="directory_qualified"),
            pytest.param("/code-review", id="leading_slash"),
        ],
    )
    def test_qualified_code_review_skill_name_denies(self, tmp_path, skill):
        _assert_denies(_run_gate(tmp_path, _payload(skill=skill)))

    @pytest.mark.parametrize(
        "args",
        [
            pytest.param("Review the staged diff; diff file is /abs/path/review.diff", id="prose_args"),
            pytest.param('"unrouted"', id="quoted_spelling"),
            pytest.param(["unrouted"], id="non_string_args"),
        ],
    )
    def test_args_not_beginning_with_the_unrouted_word_deny(self, tmp_path, args):
        _assert_denies(_run_gate(tmp_path, _payload(args=args)))

    @pytest.mark.parametrize(
        "agent_type",
        [
            pytest.param("some-other-agent", id="non_roster_name"),
            pytest.param("review-orchestrator", id="review_orchestrator_name"),
        ],
    )
    def test_agent_type_without_agent_id_reads_as_main_session_and_denies(self, tmp_path, agent_type):
        _assert_denies(_run_gate(tmp_path, _payload(agent_type=agent_type)))

    def test_empty_agent_id_reads_as_main_session_and_denies(self, tmp_path):
        _assert_denies(_run_gate(tmp_path, _payload(agent_id="")))


def _cited_heading(reason: str) -> str:
    """The heading text inside the reason's `§ "..."` citation."""
    citation = re.search(r'§ "([^"]+)"', reason)
    assert citation, reason
    return citation.group(1)


def _section_from_heading(skill_text: str, heading_line: str) -> str:
    """The text from `heading_line` to the next line starting with `## `, or
    to the end of the file."""
    lines = skill_text.splitlines()
    start = lines.index(heading_line)
    section_lines = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        section_lines.append(line)
    return "\n".join(section_lines)


# --- allow --------------------------------------------------------------------


class TestAllow:
    @pytest.mark.parametrize(
        "args",
        [
            pytest.param("unrouted", id="word_alone"),
            pytest.param("unrouted /abs/x.diff", id="word_then_path"),
        ],
    )
    def test_args_beginning_with_the_unrouted_word_allow(self, tmp_path, args):
        _assert_allows(_run_gate(tmp_path, _payload(args=args)))

    @pytest.mark.parametrize(
        "agent_type",
        [
            pytest.param("review-orchestrator", id="review_orchestrator"),
            pytest.param(_OMIT, id="agent_type_absent"),
            pytest.param("code-writer", id="different_roster_name"),
        ],
    )
    def test_subagent_call_with_agent_id_allows(self, tmp_path, agent_type):
        _assert_allows(_run_gate(tmp_path, _payload(agent_id=SUBAGENT_ID, agent_type=agent_type)))

    @pytest.mark.parametrize(
        "skill",
        [
            pytest.param("code-review-proj", id="suffix_extended"),
            pytest.param("pre-code-review", id="prefix_extended"),
            # The match is case-sensitive: the gate compares the name the
            # payload carries, and whether the harness resolves skill names
            # case-insensitively is unverified.
            pytest.param("Code-Review", id="different_case"),
            pytest.param("plugin:", id="empty_name_after_plugin_prefix"),
            pytest.param(None, id="null_skill"),
        ],
    )
    def test_skill_other_than_code_review_allows(self, tmp_path, skill):
        _assert_allows(_run_gate(tmp_path, _payload(skill=skill)))

    def test_missing_skill_field_allows(self, tmp_path):
        _assert_allows(_run_gate(tmp_path, _payload_without_skill()))

    def test_non_skill_tool_carrying_a_code_review_skill_field_allows(self, tmp_path):
        _assert_allows(_run_gate(tmp_path, _payload(tool_name="Bash")))

    def test_key_false_allows(self, tmp_path):
        _assert_allows(_run_gate(tmp_path, _payload(), key_line=f"{KEY_NAME} = false\n"))

    def test_no_state_file_allows_because_the_schema_default_is_off(self, tmp_path):
        _assert_allows(_run_gate(tmp_path, _payload(), key_line=None))


# --- failed key reads ---------------------------------------------------------


def _isolated_hooks_dir(tmp_path: Path, *, schema_text: str | None) -> Path:
    """Symlink the gate plus its _lib.sh/_config.sh dependencies into a
    directory whose schema is `schema_text`, or absent when None. Returns the
    isolated gate's path."""
    isolated = tmp_path / "isolated-hooks"
    isolated.mkdir()
    (isolated / HOOK.name).symlink_to(HOOK)
    (isolated / "_lib.sh").symlink_to(HOOKS_DIR / "_lib.sh")
    (isolated / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
    if schema_text is not None:
        (isolated / "config-keys.psv").write_text(schema_text)
    return isolated / HOOK.name


class TestFailedKeyReadAllows:
    """Each case is a one-factor change from the baseline-deny payload with the
    key line seeded true, so only the failed key read lets the call through."""

    def test_unresolvable_config_dir_exit_2_allows(self, tmp_path):
        # _run_gate seeds the true row at $HOME/.claude, which a relative
        # CLAUDE_CONFIG_DIR makes unreachable.
        result = _run_gate(tmp_path, _payload(), env_overrides={"CLAUDE_CONFIG_DIR": "relative/config-dir"})

        _assert_allows(result)

    def test_unreadable_schema_exit_3_allows(self, tmp_path):
        isolated_hook = _isolated_hooks_dir(tmp_path, schema_text=None)

        _assert_allows(_run_gate(tmp_path, _payload(), hook=isolated_hook))

    def test_schema_missing_the_row_exit_4_allows(self, tmp_path):
        schema_lines = _SCHEMA_PSV.read_text().splitlines(keepends=True)
        schema_without_row = "".join(line for line in schema_lines if not line.startswith(f"{KEY_NAME}|"))
        assert schema_without_row != "".join(schema_lines)
        isolated_hook = _isolated_hooks_dir(tmp_path, schema_text=schema_without_row)

        _assert_allows(_run_gate(tmp_path, _payload(), hook=isolated_hook))


class TestFailedFieldReadAllows:
    """A field read that fails after the parse succeeded lets the call through.
    Each case fails the read by its filter text, so no case depends on the
    order of the gate's jq calls."""

    def test_unfailing_stand_in_denies_after_both_field_reads_run(self, tmp_path):
        env, call_log = _recording_jq_standin(tmp_path)

        _assert_denies(_run_gate(tmp_path, _payload(), env_overrides=env))

        assert _field_read_filters_run(call_log) == {SKILL_FIELD_FILTER, ARGS_FIELD_FILTER}

    def test_failed_skill_read_allows_without_reading_args(self, tmp_path):
        env, call_log = _recording_jq_standin(tmp_path, fail_on_filter=SKILL_FIELD_FILTER)

        _assert_allows(_run_gate(tmp_path, _payload(), env_overrides=env))

        assert _field_read_filters_run(call_log) == {SKILL_FIELD_FILTER}

    def test_failed_args_read_allows(self, tmp_path):
        env, call_log = _recording_jq_standin(tmp_path, fail_on_filter=ARGS_FIELD_FILTER)

        _assert_allows(_run_gate(tmp_path, _payload(), env_overrides=env))

        assert _field_read_filters_run(call_log) == {SKILL_FIELD_FILTER, ARGS_FIELD_FILTER}


class TestKeyOffCost:
    """Pins that the paths which skip the field reads run neither field-read
    filter. Each is a one-factor change from the key-on baseline."""

    def test_key_false_runs_no_field_read(self, tmp_path):
        env, call_log = _recording_jq_standin(tmp_path)

        _assert_allows(_run_gate(tmp_path, _payload(), key_line=f"{KEY_NAME} = false\n", env_overrides=env))

        assert _field_read_filters_run(call_log) == set()

    def test_subagent_call_runs_no_field_read(self, tmp_path):
        env, call_log = _recording_jq_standin(tmp_path)

        _assert_allows(_run_gate(tmp_path, _payload(agent_id=SUBAGENT_ID), env_overrides=env))

        assert _field_read_filters_run(call_log) == set()

    def test_non_skill_tool_runs_no_field_read(self, tmp_path):
        env, call_log = _recording_jq_standin(tmp_path)

        _assert_allows(_run_gate(tmp_path, _payload(tool_name="Bash"), env_overrides=env))

        assert _field_read_filters_run(call_log) == set()


# --- registration -------------------------------------------------------------


def _pretooluse_matcher_groups_for(hook: Path) -> list[str]:
    """The `matcher` string of every PreToolUse group containing an entry
    wired to `hook`, using the exact-last-shell-word match
    test_hook_alignment.py uses."""
    expected_invocation = f"~/.claude/hooks/{hook.name}"
    config = json.loads(_SETTINGS_PATH.read_text())
    matchers: list[str] = []
    for group in config.get("hooks", {}).get("PreToolUse", []):
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []):
            if not isinstance(entry, dict):
                continue
            tokens = shlex.split(entry.get("command", ""))
            if tokens and tokens[-1] == expected_invocation:
                matchers.append(group.get("matcher", ""))
    return matchers


def test_gate_is_wired_on_the_skill_matcher_only() -> None:
    """A settings.json entry on a wider matcher would run the gate on tools it
    never filters for; a missing entry leaves the key with no effect."""
    assert _pretooluse_matcher_groups_for(HOOK) == ["Skill"]
