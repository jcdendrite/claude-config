"""Tests for require-diff-size-prompt.sh, one case per branch. The count's input matrix is in test_lib_diff_size.py.

Every repository is built against a bare `origin` whose default branch is `trunk`. Every subprocess gets one explicit
CLAUDE_CONFIG_DIR under `tmp_path`, so each latch-path assertion derives from it and nothing reads or writes a real one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    _run_git,
    absolute_git_dir,
    bash_input,
    build_path_without,
    git_toplevel,
    installed_locale_where,
    run_hook,
    run_hook_reason,
)

from ._lib_sh_helpers import (
    DIFF_SIZE_ACK_COMMAND,
    DIFF_SIZE_GATE_HOOK,
    HOSTILE_BASE_NAME_PARAMS,
    _ack_env,
    _assert_hook_allows,
    _clone_with_staged_lines,
    _clone_with_trunk_files,
    _config_dir,
    _default_branch_arm_base,
    _forge_state_marker,
    _latch_path,
    _lines,
    _linked_worktree_with_staged_lines,
    _run_diff_size_ack,
    _run_lib_call,
    _stage_file,
    isolated_git_config,  # noqa: F401 -- an autouse fixture that applies to every test here
    test_isolated_git_config_fixture_reaches_every_git_child,  # noqa: F401 -- pytest collects it here, once, by this import
)

_REPO_ROOT = HOOKS_DIR.parents[2]
_SETTINGS_PATH = _REPO_ROOT / "claude" / ".claude" / "settings.json"
_COMMIT_COMMAND = "git commit -m x"
_THRESHOLD_LINES = 400
_OVER_THRESHOLD_LINES = 437
_SIZE_REASON_FRAGMENTS = (
    str(_OVER_THRESHOLD_LINES),
    "origin/trunk",
    f"{_THRESHOLD_LINES}-line threshold",
    "AskUserQuestion",
    "`Split now`",
    "`Proceed anyway`",
    DIFF_SIZE_ACK_COMMAND,
)
_MARKER_CHAIN_COMMAND = "~/.claude/scripts/marker.sh write code-review && git commit -m x"


def _hook_env(tmp_path: Path, env_overrides: dict[str, str] | None = None) -> dict[str, str]:
    return {"CLAUDE_CONFIG_DIR": str(_config_dir(tmp_path)), **(env_overrides or {})}


def _deny_reason(
    tmp_path: Path,
    payload: dict,
    process_cwd: Path,
    env_overrides: dict[str, str] | None = None,
    hook: Path = DIFF_SIZE_GATE_HOOK,
) -> str | None:
    return run_hook_reason(hook, payload, cwd=process_cwd, extra_env=_hook_env(tmp_path, env_overrides))


def _decision(
    tmp_path: Path,
    payload: dict,
    process_cwd: Path,
    env_overrides: dict[str, str] | None = None,
    hook: Path = DIFF_SIZE_GATE_HOOK,
) -> str:
    return run_hook(hook, payload, cwd=process_cwd, extra_env=_hook_env(tmp_path, env_overrides))


def _assert_allows(
    tmp_path: Path,
    payload: dict,
    process_cwd: Path,
    env_overrides: dict[str, str] | None = None,
    hook: Path = DIFF_SIZE_GATE_HOOK,
) -> None:
    _assert_hook_allows(hook, payload, process_cwd, _hook_env(tmp_path, env_overrides))


def _commit_payload(repo: Path, command: str = _COMMIT_COMMAND) -> dict:
    return bash_input(command, cwd=git_toplevel(repo))


def _roster_agent_payload(agent_type: str | None, agent_id: str | None, cwd: Path | None, command: str) -> dict:
    payload = bash_input(command, agent_type=agent_type, cwd=str(cwd) if cwd is not None else None)
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def _assert_denies_commit(tmp_path: Path, clone: Path, command: str = _COMMIT_COMMAND) -> str:
    reason = _deny_reason(tmp_path, _commit_payload(clone, command), clone)
    assert reason is not None, "premise: the untoggled fixture must deny"
    return reason


class TestDenyMessage:
    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(_COMMIT_COMMAND, id="plain commit"),
            pytest.param(f"FOO=1 {_COMMIT_COMMAND}", id="environment-prefixed commit"),
        ],
    )
    def test_denies_a_commit_over_the_threshold_and_names_the_question_and_the_ack(
        self, tmp_path: Path, command: str
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)

        reason = _assert_denies_commit(tmp_path, clone, command)

        for expected_fragment in _SIZE_REASON_FRAGMENTS:
            assert expected_fragment in reason, reason
        assert str(_latch_path(clone, tmp_path)) not in reason
        assert not _config_dir(tmp_path).joinpath(".diff-size-prompt-latch.d").exists()

    def test_the_deny_text_tells_a_subagent_to_report_the_denial_to_its_dispatcher(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)

        reason = _assert_denies_commit(tmp_path, clone)

        assert "A subagent reports this denial to its dispatcher" in reason

    @pytest.mark.parametrize(
        "agent_type",
        [
            pytest.param("code-writer", id="roster agent"),
            pytest.param("general-purpose", id="non-roster agent"),
        ],
    )
    def test_denies_a_subagent_commit_over_the_threshold_with_the_size_reason_not_the_roster_bars(
        self, tmp_path: Path, agent_type: str
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        payload = _roster_agent_payload(agent_type, "agent-1", git_toplevel(clone), _COMMIT_COMMAND)

        reason = _deny_reason(tmp_path, payload, clone)

        assert reason is not None, "premise: a subagent commit over the threshold denies"
        for expected_fragment in _SIZE_REASON_FRAGMENTS:
            assert expected_fragment in reason, reason
        assert "cannot release the diff-size gate" not in reason


class TestThresholdBoundary:
    def test_399_lines_is_allowed(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES - 1)

        _assert_allows(tmp_path, _commit_payload(clone), clone)

    def test_400_lines_is_denied(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)

        reason = _assert_denies_commit(tmp_path, clone)

        assert f"holds {_THRESHOLD_LINES} changed lines" in reason


class TestAllowsCallsOtherThanAnOverThresholdCommit:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"tool_name": "Read", "tool_input": {"file_path": "work.txt"}}, id="non-Bash call"),
            pytest.param(
                {"tool_name": "Edit", "tool_input": {"command": _COMMIT_COMMAND}},
                id="non-Bash call whose input carries a commit command",
            ),
            pytest.param(
                {
                    "tool_name": "Edit",
                    "tool_input": {"command": DIFF_SIZE_ACK_COMMAND},
                    "agent_type": "code-writer",
                    "agent_id": "agent-1",
                },
                id="non-Bash call from a roster agent whose input carries the ack command",
            ),
            pytest.param(bash_input("ls -la"), id="Bash call with no git"),
            pytest.param(bash_input("git status"), id="git call that does not conclude a commit"),
        ],
    )
    def test_allows_on_a_fixture_that_denies_a_commit(self, tmp_path: Path, payload: dict) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        _assert_denies_commit(tmp_path, clone)

        _assert_allows(tmp_path, {**payload, "cwd": git_toplevel(clone)}, clone)


def _toggle_detached_head(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    # _lib_diff_size_gate_state status 1.
    _run_git(clone, "checkout", "-q", "--detach")
    return _COMMIT_COMMAND, {}


def _toggle_latch_file_present(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    # _lib_diff_size_gate_state status 3.
    latch_file = _latch_path(clone, tmp_path)
    latch_file.parent.mkdir(parents=True)
    latch_file.write_text("recorded\n")
    return _COMMIT_COMMAND, {}


def _toggle_paused_merge_on_a_continue_form(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    _forge_state_marker(absolute_git_dir(clone), "merge", _run_git(clone, "rev-parse", "HEAD").strip())
    return "git merge --continue", {}


def _toggle_path_without(binary: str):
    def toggle(clone: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
        # _lib_command_concludes_commit status 2.
        farm_dir = tmp_path / f"path-without-{binary}"
        farm_dir.mkdir()
        return _COMMIT_COMMAND, {"PATH": build_path_without(binary, farm_dir)}

    return toggle


class TestAllowOnStateFailureOrAnswer:
    @pytest.mark.parametrize(
        ("untoggled_command", "toggle"),
        [
            pytest.param(_COMMIT_COMMAND, _toggle_detached_head, id="detached HEAD"),
            pytest.param(_COMMIT_COMMAND, _toggle_latch_file_present, id="latch file present"),
            pytest.param(
                "git merge --continue", _toggle_paused_merge_on_a_continue_form, id="--continue form during a paused merge"
            ),
            pytest.param(_COMMIT_COMMAND, _toggle_path_without("sed"), id="PATH without sed"),
            pytest.param(_COMMIT_COMMAND, _toggle_path_without("tr"), id="PATH without tr"),
        ],
    )
    def test_allows_after_one_toggle_of_a_fixture_that_denies(
        self, tmp_path: Path, untoggled_command: str, toggle
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        _assert_denies_commit(tmp_path, clone, untoggled_command)

        command, env_overrides = toggle(clone, tmp_path)

        _assert_allows(tmp_path, _commit_payload(clone, command), clone, env_overrides)


class TestWhereTheCommitIsMeasured:
    def test_a_payload_cwd_in_the_clone_is_measured_from_a_process_cwd_outside_any_repository(
        self, tmp_path: Path
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        outside = tmp_path / "outside-any-repository"
        outside.mkdir()

        reason = _deny_reason(tmp_path, _commit_payload(clone), outside)

        assert reason is not None
        assert str(_OVER_THRESHOLD_LINES) in reason

    def test_a_payload_with_no_cwd_is_measured_from_a_process_cwd_inside_the_clone(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)

        reason = _deny_reason(tmp_path, bash_input(_COMMIT_COMMAND), clone)

        assert reason is not None
        assert str(_OVER_THRESHOLD_LINES) in reason


class TestHostileBaseName:
    """Only the default-branch arm can deliver an unchecked base name to the gate."""

    @pytest.mark.parametrize("hostile_name", HOSTILE_BASE_NAME_PARAMS)
    def test_a_name_that_fails_the_shape_check_prints_as_a_placeholder(self, tmp_path: Path, hostile_name: str) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        _default_branch_arm_base(clone, hostile_name)

        reason = _assert_denies_commit(tmp_path, clone)

        assert str(_OVER_THRESHOLD_LINES) in reason
        assert f"{_THRESHOLD_LINES}-line threshold" in reason
        assert "the base branch" in reason
        assert hostile_name not in reason
        assert "origin/the base branch" not in reason

    def test_a_clean_name_on_the_same_arm_prints_as_origin_slash_name(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        _default_branch_arm_base(clone, "clean-base")

        reason = _assert_denies_commit(tmp_path, clone)

        assert "origin/clean-base" in reason
        assert "the base branch" not in reason


class TestRosterBarOnTheBareAckCommand:
    """The bar keys on `agent_type`; `agent_id` is only the non-empty subagent discriminator."""

    @pytest.mark.parametrize("agent_id", [None, "agent-1"], ids=["no agent_id", "with agent_id"])
    @pytest.mark.parametrize(
        "agent_type",
        [
            pytest.param("ciso-reviewer", id="review-only agent"),
            pytest.param("plan-architect", id="plan-writer agent"),
            pytest.param("code-writer", id="code-writer"),
        ],
    )
    def test_denies_a_roster_agent_from_a_directory_outside_any_repository(
        self, tmp_path: Path, agent_type: str, agent_id: str | None
    ) -> None:
        outside = tmp_path / "outside-any-repository"
        outside.mkdir()

        reason = _deny_reason(
            tmp_path, _roster_agent_payload(agent_type, agent_id, outside, DIFF_SIZE_ACK_COMMAND), outside
        )

        assert reason is not None
        assert "report this to your dispatcher" in reason.lower()
        assert agent_type in reason

    def test_denies_every_name_in_the_roster(self, tmp_path: Path) -> None:
        roster_run = _run_lib_call("_lib_no_gate_release_agents", env=dict(os.environ))
        roster = roster_run.stdout.split()
        assert roster, roster_run.stderr
        outside = tmp_path / "outside-any-repository"
        outside.mkdir()

        allowed_names = [
            agent_type
            for agent_type in roster
            if _decision(tmp_path, _roster_agent_payload(agent_type, None, outside, DIFF_SIZE_ACK_COMMAND), outside)
            != "deny"
        ]

        assert allowed_names == []

    @pytest.mark.parametrize(
        ("agent_type", "agent_id"),
        [
            pytest.param("general-purpose", "agent-1", id="non-roster subagent"),
            pytest.param(None, "code-writer", id="a roster name in agent_id, not agent_type"),
            pytest.param(None, None, id="main session"),
        ],
    )
    def test_allows_a_caller_outside_the_roster(self, tmp_path: Path, agent_type: str | None, agent_id: str | None) -> None:
        outside = tmp_path / "outside-any-repository"
        outside.mkdir()

        _assert_allows(tmp_path, _roster_agent_payload(agent_type, agent_id, outside, DIFF_SIZE_ACK_COMMAND), outside)

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("grep -n diff-size-ack.sh docs/hooks.md", id="grep of the name"),
            pytest.param("ls claude/.claude/scripts/diff-size-ack.sh", id="ls of the script"),
            pytest.param("cat claude/.claude/scripts/diff-size-ack.sh", id="cat of the script"),
            pytest.param(f"{DIFF_SIZE_ACK_COMMAND} --help", id="the path with an argument"),
            pytest.param(f"{chr(0xA0)}{DIFF_SIZE_ACK_COMMAND}", id="the path with a leading no-break space"),
        ],
    )
    def test_bars_only_the_one_spelling_for_a_code_writer(self, tmp_path: Path, command: str) -> None:
        outside = tmp_path / "outside-any-repository"
        outside.mkdir()

        _assert_allows(tmp_path, _roster_agent_payload("code-writer", "agent-1", outside, command), outside)

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(f" {DIFF_SIZE_ACK_COMMAND}", id="the path with leading whitespace"),
            pytest.param(f"{DIFF_SIZE_ACK_COMMAND} ", id="the path with trailing whitespace"),
            pytest.param(f"{DIFF_SIZE_ACK_COMMAND}\n", id="the path with a trailing newline"),
            pytest.param(f"\t{DIFF_SIZE_ACK_COMMAND}", id="the path with a leading tab"),
        ],
    )
    def test_bars_the_path_with_ascii_whitespace_around_it_with_the_bar_text(
        self, tmp_path: Path, command: str
    ) -> None:
        outside = tmp_path / "outside-any-repository"
        outside.mkdir()

        reason = _deny_reason(tmp_path, _roster_agent_payload("code-writer", "agent-1", outside, command), outside)

        assert reason is not None
        assert "report this to your dispatcher" in reason.lower()
        assert "code-writer" in reason

    def test_the_trim_stays_ascii_only_under_a_locale_that_counts_ideographic_space_as_space(
        self, tmp_path: Path
    ) -> None:
        locale_name = installed_locale_where(
            '[[ "$1" == [[:space:]] ]]',
            chr(0x3000),
            absent_message="no installed locale makes [[:space:]] match U+3000",
        )
        outside = tmp_path / "outside-any-repository"
        outside.mkdir()
        payload = _roster_agent_payload("code-writer", "agent-1", outside, f"{chr(0x3000)}{DIFF_SIZE_ACK_COMMAND}")

        _assert_allows(tmp_path, payload, outside, {"LC_ALL": locale_name})


def _bash_hooks_applying_to(command: str) -> list[Path]:
    """Hooks of settings.json's first PreToolUse `Bash` matcher group that run for `command`.
    An `if` filter is evaluated only in the form `Bash(<prefix> *)`; any other shape fails the test."""
    settings = json.loads(_SETTINGS_PATH.read_text())
    bash_group = next(group for group in settings["hooks"]["PreToolUse"] if group["matcher"] == "Bash")
    applying: list[Path] = []
    for entry in bash_group["hooks"]:
        condition = entry.get("if")
        if condition is not None:
            assert condition.startswith("Bash(") and condition.endswith(" *)"), f"unsupported if filter: {condition!r}"
            if not command.startswith(condition[len("Bash("):-len("*)")]):
                continue
        hook = HOOKS_DIR / entry["command"].rsplit("/", 1)[1]
        if hook not in applying:
            applying.append(hook)
    return applying


class TestEveryBashHookInSettings:
    def test_the_enumeration_holds_the_hooks_these_cases_rely_on(self) -> None:
        enumerated_names = {hook.name for hook in _bash_hooks_applying_to(_COMMIT_COMMAND)}

        assert {
            "require-diff-size-prompt.sh",
            "require-code-review.sh",
            "require-worktree-for-git-writes.sh",
            "enforce-marker-script-shape.sh",
            "deny-invisible-commit-content.sh",
        } <= enumerated_names

    def test_every_hook_allows_the_bare_ack_command_from_the_main_session(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        payload = bash_input(DIFF_SIZE_ACK_COMMAND, cwd=git_toplevel(clone))

        decisions = {
            hook.name: run_hook(
                hook, payload, cwd=clone, home=tmp_path / "home", extra_env=_hook_env(tmp_path)
            )
            for hook in _bash_hooks_applying_to(DIFF_SIZE_ACK_COMMAND)
        }

        assert {name: decision for name, decision in decisions.items() if decision != "allow"} == {}
        _assert_allows(tmp_path, payload, clone)

    def test_the_gates_accept_the_retried_marker_chain_after_the_ack(self, tmp_path: Path) -> None:
        """Proves the gates accept the retried chain once the ack ran. It does not run the chain, which needs marker.sh
        installed under the test's home."""
        _clone, linked = _linked_worktree_with_staged_lines(tmp_path, _OVER_THRESHOLD_LINES)
        payload = bash_input(_MARKER_CHAIN_COMMAND, cwd=git_toplevel(linked))
        hooks = _bash_hooks_applying_to(_MARKER_CHAIN_COMMAND)

        def decisions() -> dict[str, str]:
            return {
                hook.name: run_hook(hook, payload, cwd=linked, home=tmp_path / "home", extra_env=_hook_env(tmp_path))
                for hook in hooks
            }

        before_the_ack = decisions()
        ack = _run_diff_size_ack(linked, _ack_env(tmp_path))
        after_the_ack = decisions()

        assert before_the_ack.pop("require-diff-size-prompt.sh") == "deny"
        assert {name: decision for name, decision in before_the_ack.items() if decision != "allow"} == {}
        assert ack.returncode == 0, ack.stderr
        assert after_the_ack.pop("require-diff-size-prompt.sh") == "allow"
        _assert_allows(tmp_path, payload, linked)
        assert {name: decision for name, decision in after_the_ack.items() if decision != "allow"} == {}


class TestChainedAddIsClosedByTheInvisibleContentGate:
    def test_an_unchained_commit_with_the_same_untracked_work_is_allowed_by_both_gates(self, tmp_path: Path) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "small.txt", _lines("small", 5))
        (clone / "work.txt").write_text(_lines("work", _OVER_THRESHOLD_LINES))
        unchained_payload = _commit_payload(clone)

        invisible_content_gate = _decision(
            tmp_path, unchained_payload, clone, hook=HOOKS_DIR / "deny-invisible-commit-content.sh"
        )

        _assert_allows(tmp_path, unchained_payload, clone)
        assert invisible_content_gate == "allow"

    def test_a_chained_add_is_denied_by_deny_invisible_commit_content_while_this_gate_reads_the_smaller_index(
        self, tmp_path: Path
    ) -> None:
        _bare, clone = _clone_with_trunk_files(tmp_path)
        _stage_file(clone, "small.txt", _lines("small", 5))
        (clone / "work.txt").write_text(_lines("work", _OVER_THRESHOLD_LINES))
        chained_command = f"git add work.txt && {_COMMIT_COMMAND}"
        chained_payload = _commit_payload(clone, chained_command)

        invisible_content_gate = _decision(
            tmp_path, chained_payload, clone, hook=HOOKS_DIR / "deny-invisible-commit-content.sh"
        )

        _assert_allows(tmp_path, chained_payload, clone)  # this gate reads the smaller index
        assert invisible_content_gate == "deny"
