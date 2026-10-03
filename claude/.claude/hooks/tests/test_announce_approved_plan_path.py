"""Tests for announce-approved-plan-path.sh."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    bash_input,
    extract_skill_command,
    git_toplevel,
    install_marker_script,
    run_hook,
    symlink_hooks_lib_chain,
)

from .conftest import _seed_session

ANNOUNCE_HOOK = HOOKS_DIR / "announce-approved-plan-path.sh"
ENFORCE_MARKER_SCRIPT_SHAPE_HOOK = HOOKS_DIR / "enforce-marker-script-shape.sh"
_SETTINGS_PATH = HOOKS_DIR.parent / "settings.base.json"
_PLAN_REVIEW_SKILL = SKILLS_DIR / "plan-review" / "SKILL.md"

# Paired literal: PLAN_REVIEW_COVERED_PATH_PREFIX in marker.sh.
COVERED_PATH_PREFIX = "plan-review marker covers: "

PLAN_PATH = "/work/repo/.claude/plans/p.md"
OTHER_PLAN_PATH = "/work/repo/.claude/plans/q.md"
APPROVED_MESSAGE = f"plan-review marker recorded for: {PLAN_PATH}"
WITHHELD_MESSAGE = (
    "plan-review marker recorded; a path is not shown (unexpected characters or path shape)."
)
DRIFT_MESSAGE = (
    "announce-approved-plan-path.sh: this Bash result carried no tool_response.stdout "
    "string, so no plan path can be shown."
)


def _record_completion_command() -> str:
    """The exact command /plan-review's SKILL.md tells the model to run, so a
    SKILL.md edit that stops matching the hook's trigger fails here."""
    command = extract_skill_command(_PLAN_REVIEW_SKILL, "record-completion")
    assert command.startswith("~/.claude/scripts/marker.sh write plan-review"), command
    return command


def _covered_stdout(*paths: str) -> str:
    return "".join(f"{COVERED_PATH_PREFIX}{path}\n" for path in paths)


def _payload(command: str, tool_response, tool_name: str = "Bash") -> dict:
    return {
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "tool_response": tool_response,
    }


def _run_hook_raw(
    payload: dict | str,
    home: Path,
    hook: Path = ANNOUNCE_HOOK,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Runs the hook as an executed file (not `bash <path>`), so the exec bit
    is part of what passes."""
    env = {**os.environ, "HOME": str(home)}
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [str(hook)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def _message(result: subprocess.CompletedProcess) -> str | None:
    """The emitted systemMessage, or None when the hook stayed silent.
    Asserts the envelope carries no other field and the message is printable
    ASCII only."""
    if result.stdout == "":
        return None
    emitted = json.loads(result.stdout)
    assert set(emitted) == {"systemMessage"}
    assert re.fullmatch(r"[ -~]*", emitted["systemMessage"])
    return emitted["systemMessage"]


def _observed_tool_response(stdout: str) -> dict:
    """The tool_response keys the harness has been observed to send, with stdout
    unterminated as observed."""
    return {
        "stdout": stdout.removesuffix("\n"),
        "stderr": "",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
    }


def _announce(home: Path, command: str, stdout: str, tool_name: str = "Bash") -> str | None:
    payload = _payload(command, _observed_tool_response(stdout), tool_name)
    return _message(_run_hook_raw(payload, home))


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _run_real_marker_write(
    repo: Path, home: Path, plan_file_names: tuple[str, ...] = ("p.md",)
) -> str:
    """Runs /plan-review's recorded command against the real marker.sh, with one
    untracked plan per name in `plan_file_names`, and returns its stdout, the
    text the harness would hand to the hook."""
    _seed_session(home, "announce-plan-path-session")
    install_marker_script(home)
    plans_dir = repo / ".claude" / "plans"
    plans_dir.mkdir(parents=True)
    for plan_file_name in plan_file_names:
        (plans_dir / plan_file_name).write_text("# plan\n")
    result = subprocess.run(
        ["bash", "-c", _record_completion_command()],
        cwd=repo,
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestRegistration:
    def test_settings_registers_hook_under_post_tool_use_with_exact_bash_matcher(self):
        settings = json.loads(_SETTINGS_PATH.read_text())

        def matchers_registering(event_name: str) -> list[str | None]:
            return [
                group.get("matcher")
                for group in settings["hooks"].get(event_name, [])
                for entry in group.get("hooks", [])
                if entry.get("command", "").endswith(ANNOUNCE_HOOK.name)
            ]

        assert matchers_registering("PostToolUse") == ["Bash"]
        assert matchers_registering("PreToolUse") == []

    def test_settings_entry_has_no_if_condition(self):
        settings = json.loads(_SETTINGS_PATH.read_text())
        matching_entries = [
            entry
            for group in settings["hooks"].get("PostToolUse", [])
            for entry in group.get("hooks", [])
            if entry.get("command", "").endswith(ANNOUNCE_HOOK.name)
        ]

        assert len(matching_entries) == 1
        assert "if" not in matching_entries[0]


# Each builder turns the extracted /plan-review command into a shape the
# real enforce-marker-script-shape.sh gate allows.
GATE_ALLOWED_SHAPE_PARAMS = [
    pytest.param(lambda c: c, id="extracted_tilde_form"),
    pytest.param(
        lambda c: c.replace("~", "/home/example", 1), id="absolute_path_prefix"
    ),
    pytest.param(lambda c: "   " + c, id="leading_spaces"),
    pytest.param(lambda c: "\t" + c, id="leading_tab"),
    pytest.param(lambda c: c + " 2>/dev/null", id="trailing_stderr_discard"),
    pytest.param(
        lambda c: c.replace("write", "deactivate") + " && " + c,
        id="deactivate_then_write_chain",
    ),
    pytest.param(
        lambda c: c.replace("write", "deactivate") + " && " + c + " 2>/dev/null",
        id="deactivate_then_write_chain_with_stderr_discard",
    ),
    pytest.param(lambda c: c + "&&git commit -m x", id="no_space_before_chain_operator"),
    pytest.param(lambda c: c + " && git commit -m x", id="write_then_git_commit"),
]


class TestTrigger:
    @pytest.mark.parametrize("command_builder", GATE_ALLOWED_SHAPE_PARAMS)
    def test_announces_for_every_gate_allowed_shape_containing_write_plan_review(
        self, isolated_home, command_builder
    ):
        command = command_builder(_record_completion_command())

        assert _announce(isolated_home, command, _covered_stdout(PLAN_PATH)) == APPROVED_MESSAGE

    @pytest.mark.parametrize("command_builder", GATE_ALLOWED_SHAPE_PARAMS)
    def test_every_announced_shape_is_one_the_real_shape_gate_allows(
        self, isolated_home, command_builder
    ):
        command = command_builder(_record_completion_command())

        decision = run_hook(
            ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(command), home=isolated_home
        )

        assert decision == "allow"

    @pytest.mark.parametrize("tool_name", ["Write", "Read"])
    def test_stays_silent_for_a_non_bash_tool_carrying_a_triggering_command(
        self, isolated_home, tool_name
    ):
        message = _announce(
            isolated_home,
            _record_completion_command(),
            _covered_stdout(PLAN_PATH),
            tool_name=tool_name,
        )

        assert message is None

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param('grep -rn "marker.sh write plan-review" docs/', id="grep_mentioning_the_op"),
            pytest.param(
                "~/.claude/scripts/marker.sh write code-review", id="write_of_another_skill"
            ),
            pytest.param(
                "~/.claude/scripts/marker.sh deactivate plan-review", id="deactivate_alone"
            ),
            pytest.param(
                "~/.claude/scripts/marker.sh write plan-review-x",
                id="longer_token_after_plan_review",
            ),
            pytest.param(
                "echo start && ~/.claude/scripts/marker.sh write plan-review",
                id="marker_path_not_first_token",
            ),
            pytest.param(
                "$HOME/.claude/scripts/marker.sh write plan-review", id="home_variable_form"
            ),
            pytest.param(
                "echo hi\n~/.claude/scripts/marker.sh write plan-review",
                id="newline_before_marker_path",
            ),
            pytest.param(
                "~/.claude/scripts/marker.sh write plan-review\necho hi",
                id="newline_after_marker_op",
            ),
        ],
    )
    def test_stays_silent_for_a_non_triggering_command_even_when_stdout_carries_a_covered_path(
        self, isolated_home, command
    ):
        assert _announce(isolated_home, command, _covered_stdout(PLAN_PATH)) is None


# The hook's bash 3.2 empty-array constraint is verified only on a developer
# macOS run, not on Linux CI.
class TestRelay:
    def test_ignores_lines_without_the_exact_prefix_at_line_start(self, isolated_home):
        stdout = (
            "[main abc1234] commit the plan\n"
            f"{_covered_stdout(PLAN_PATH)}"
            " 1 file changed, 1 insertion(+)\n"
        )

        message = _announce(isolated_home, _record_completion_command(), stdout)

        assert message == APPROVED_MESSAGE

    def test_stays_silent_when_the_prefix_appears_only_mid_line(self, isolated_home):
        stdout = f"note: {COVERED_PATH_PREFIX}{PLAN_PATH}\n"

        assert _announce(isolated_home, _record_completion_command(), stdout) is None

    def test_joins_two_prefixed_lines_with_comma_and_space(self, isolated_home):
        message = _announce(
            isolated_home,
            _record_completion_command(),
            _covered_stdout(PLAN_PATH, OTHER_PLAN_PATH),
        )

        assert message == f"plan-review marker recorded for: {PLAN_PATH}, {OTHER_PLAN_PATH}"


class TestWithheldPath:
    def test_announces_a_path_built_from_every_allowlisted_punctuation_class(self, isolated_home):
        allowlisted_path = "/work/re+po@1/.claude/plans/p-1.v2_x.md"

        message = _announce(
            isolated_home, _record_completion_command(), _covered_stdout(allowlisted_path)
        )

        assert message == f"plan-review marker recorded for: {allowlisted_path}"

    def test_announces_a_plan_path_inside_a_nested_worktree_verbatim(self, isolated_home):
        worktree_plan_path = "/work/repo/.claude/worktrees/x/.claude/plans/x.md"

        message = _announce(
            isolated_home, _record_completion_command(), _covered_stdout(worktree_plan_path)
        )

        assert message == f"plan-review marker recorded for: {worktree_plan_path}"

    def test_withholds_every_path_when_one_fails_the_allowlist(self, isolated_home):
        stdout = _covered_stdout("/work/my repo/.claude/plans/p.md", PLAN_PATH)

        message = _announce(isolated_home, _record_completion_command(), stdout)

        assert message == WITHHELD_MESSAGE
        assert "/work" not in message

    @pytest.mark.parametrize(
        "unshapely_path",
        [
            pytest.param("/work/repo/docs/p.md", id="outside_claude_plans_segment"),
            pytest.param("/work/repo/.claude/plans/p.txt", id="not_md_suffix"),
            pytest.param("relative/.claude/plans/p.md", id="not_absolute"),
            pytest.param("/work/repo/.claude/plans/sub/p.md", id="nested_below_plans_dir"),
            pytest.param(
                "/work/repo/.claude/plans/.claude/plans/p.md", id="plans_segment_repeated"
            ),
            pytest.param("/work/repo/.claude/plans/../p.md", id="dotdot_after_plans_dir"),
            pytest.param("/work/../.claude/plans/p.md", id="dotdot_before_plans_dir"),
        ],
    )
    def test_withholds_a_path_lacking_the_plan_file_shape(self, isolated_home, unshapely_path):
        message = _announce(
            isolated_home, _record_completion_command(), _covered_stdout(unshapely_path)
        )

        assert message == WITHHELD_MESSAGE

    @pytest.mark.parametrize(
        "hostile_path",
        [
            pytest.param("/work/repo\x1b[31m/.claude/plans/p.md", id="escape_byte"),
            pytest.param("/work/repo/.claude/plans/p.md\r", id="carriage_return"),
            pytest.param("/work/repo\u202e/.claude/plans/p.md", id="bidi_override_u202e"),
            pytest.param(
                '/work/repo","systemMessage":"x/.claude/plans/p.md', id="json_string_breakout"
            ),
            pytest.param("/work/repo\\/.claude/plans/p.md", id="backslash"),
            pytest.param("/work/repo/$(id)/.claude/plans/p.md", id="command_substitution"),
            pytest.param("/work/repo/`id`/.claude/plans/p.md", id="backtick"),
            pytest.param("/work/repo\t/.claude/plans/p.md", id="tab"),
        ],
    )
    def test_emits_only_the_static_withheld_message_and_no_path_bytes_for_a_hostile_path(
        self, isolated_home, hostile_path
    ):
        payload = _payload(
            _record_completion_command(),
            {"stdout": _covered_stdout(hostile_path), "stderr": "", "exit_code": 0},
        )

        result = _run_hook_raw(payload, isolated_home)

        assert _message(result) == WITHHELD_MESSAGE
        assert re.fullmatch(r"[\n -~]*", result.stdout), "raw hook output must be printable ASCII"
        assert "repo" not in result.stdout and "/work" not in result.stdout

    def test_withholds_a_prefixed_line_with_an_empty_path(self, isolated_home):
        message = _announce(isolated_home, _record_completion_command(), _covered_stdout(""))

        assert message == WITHHELD_MESSAGE

    def test_withholds_the_real_marker_output_for_a_repo_under_a_directory_with_a_space(
        self, isolated_home, tmp_path
    ):
        repo = tmp_path / "dir with space" / "repo"
        _init_repo(repo)
        marker_stdout = _run_real_marker_write(repo, isolated_home)
        assert marker_stdout.startswith(COVERED_PATH_PREFIX)

        message = _announce(isolated_home, _record_completion_command(), marker_stdout)

        assert message == WITHHELD_MESSAGE


class TestPayloadDriftAndSilence:
    @pytest.mark.parametrize(
        "tool_response",
        [
            pytest.param({"stderr": "", "exit_code": 0}, id="object_without_stdout"),
            pytest.param(None, id="null_tool_response"),
            pytest.param("plain text result", id="bare_string_tool_response"),
            pytest.param(["stdout"], id="array_tool_response"),
            pytest.param({"stdout": {"text": "x"}}, id="object_valued_stdout"),
            pytest.param({"stdout": None}, id="null_stdout"),
        ],
    )
    def test_emits_the_drift_line_when_stdout_is_not_a_string(self, isolated_home, tool_response):
        payload = _payload(_record_completion_command(), tool_response)

        message = _message(_run_hook_raw(payload, isolated_home))

        assert message == DRIFT_MESSAGE

    def test_stays_silent_when_stdout_is_an_empty_string(self, isolated_home):
        payload = _payload(_record_completion_command(), {"stdout": ""})

        assert _message(_run_hook_raw(payload, isolated_home)) is None

    def test_stays_silent_when_the_marker_write_failed(self, isolated_home):
        # A prefixed line in stderr pins that the hook reads stdout only.
        failed_write = {
            "stdout": "",
            "stderr": _covered_stdout(PLAN_PATH),
            "exit_code": 1,
        }
        payload = _payload(_record_completion_command(), failed_write)

        assert _message(_run_hook_raw(payload, isolated_home)) is None


class TestPairingWithMarkerScript:
    def test_announces_the_absolute_plan_path_marker_sh_really_prints(
        self, isolated_home, git_repo
    ):
        """Pins the paired prefix literal: marker.sh's real output, not a
        hand-built line, must be what the hook relays."""
        marker_stdout = _run_real_marker_write(git_repo, isolated_home)

        message = _announce(isolated_home, _record_completion_command(), marker_stdout)

        assert message == (
            f"plan-review marker recorded for: {git_toplevel(git_repo)}/.claude/plans/p.md"
        )

    def test_announces_both_paths_when_the_marker_really_prints_two_md_plans(
        self, isolated_home, git_repo
    ):
        marker_stdout = _run_real_marker_write(git_repo, isolated_home, ("a.md", "b.md"))

        message = _announce(isolated_home, _record_completion_command(), marker_stdout)

        plans_dir = f"{git_toplevel(git_repo)}/.claude/plans"
        assert message == f"plan-review marker recorded for: {plans_dir}/a.md, {plans_dir}/b.md"

    def test_withholds_every_path_when_the_marker_really_prints_a_txt_plan_beside_an_md_plan(
        self, isolated_home, git_repo
    ):
        """Pins the Known gap: the marker enumerates `.txt` plans, the hook shows
        only `.md` paths, and any withheld path suppresses the whole list."""
        marker_stdout = _run_real_marker_write(git_repo, isolated_home, ("a.md", "b.txt"))
        assert marker_stdout.count(COVERED_PATH_PREFIX) == 2

        message = _announce(isolated_home, _record_completion_command(), marker_stdout)

        assert message == WITHHELD_MESSAGE


class TestFailOpen:
    def test_exits_zero_silently_on_malformed_json_carrying_both_prefilter_tokens(
        self, isolated_home
    ):
        result = _run_hook_raw("marker.sh plan-review {not json", isolated_home)

        assert result.stdout == ""

    def test_exits_zero_silently_when_lib_sh_is_not_adjacent(self, isolated_home, tmp_path):
        """dirname($0) resolves to HOOKS_DIR only when the hook runs from its
        real location, so a copy with no adjacent _lib.sh exercises the
        could-not-source exit-0 path.

        The positive control proves the same payload announces once the lib
        chain is linked."""
        payload = _payload(_record_completion_command(), {"stdout": _covered_stdout(PLAN_PATH)})

        bare_dir = tmp_path / "bare-hooks"
        bare_dir.mkdir()
        bare_hook = bare_dir / ANNOUNCE_HOOK.name
        shutil.copy2(ANNOUNCE_HOOK, bare_hook)
        bare_result = _run_hook_raw(payload, isolated_home, hook=bare_hook)

        linked_dir = tmp_path / "linked-hooks"
        linked_dir.mkdir()
        linked_hook = linked_dir / ANNOUNCE_HOOK.name
        shutil.copy2(ANNOUNCE_HOOK, linked_hook)
        symlink_hooks_lib_chain(linked_dir)
        linked_result = _run_hook_raw(payload, isolated_home, hook=linked_hook)

        assert bare_result.stdout == ""
        assert _message(linked_result) == APPROVED_MESSAGE


class TestPrefilterPosition:
    def _stub_jq_dir(self, tmp_path: Path) -> tuple[Path, Path]:
        """A PATH dir whose `jq` records its invocation and fails, so a spawn
        is observable and the hook falls through to its silent exit."""
        stub_bin = tmp_path / "stub-bin"
        stub_bin.mkdir()
        spawned_marker = stub_bin / "jq-invoked"
        fake_jq = stub_bin / "jq"
        fake_jq.write_text(f"#!/bin/bash\ntouch {shlex.quote(str(spawned_marker))}\nexit 1\n")
        fake_jq.chmod(0o755)
        return stub_bin, spawned_marker

    def test_an_ordinary_bash_call_never_spawns_jq(self, isolated_home, tmp_path):
        stub_bin, spawned_marker = self._stub_jq_dir(tmp_path)
        payload = _payload("ls -la", {"stdout": "total 0\n", "stderr": "", "exit_code": 0})

        result = _run_hook_raw(
            payload, isolated_home, extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}"}
        )

        assert result.stdout == ""
        assert not spawned_marker.exists(), "jq must not run before the raw-stdin prefilter passes"

    def test_a_write_of_another_skill_never_spawns_jq(self, isolated_home, tmp_path):
        stub_bin, spawned_marker = self._stub_jq_dir(tmp_path)
        payload = _payload(
            "~/.claude/scripts/marker.sh write code-review",
            {"stdout": "", "stderr": "", "exit_code": 0},
        )

        result = _run_hook_raw(
            payload, isolated_home, extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}"}
        )

        assert result.stdout == ""
        assert not spawned_marker.exists(), "jq must not run before the raw-stdin prefilter passes"

    def test_a_prefilter_passing_payload_does_spawn_jq(self, isolated_home, tmp_path):
        """Positive control for the test above: the stub is on PATH and does
        get spawned once the payload clears the prefilter."""
        stub_bin, spawned_marker = self._stub_jq_dir(tmp_path)
        payload = _payload(
            _record_completion_command(), {"stdout": _covered_stdout(PLAN_PATH)}
        )

        result = _run_hook_raw(
            payload, isolated_home, extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}"}
        )

        assert result.stdout == ""
        assert spawned_marker.exists()
