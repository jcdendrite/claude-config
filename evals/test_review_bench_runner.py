"""Tests for evals/review_bench/runner.py. Offline throughout:
every per-run validity check is driven by synthetic subagent transcripts
under evals/fixtures/review-bench/, in the JSONL sidecar shape
test_measure_subagent_model_resolution.py's own helpers write. No test
launches `claude`.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest
import run_review_bench
import run_skill_evals
from review_bench import identifiers, runner
from review_bench.defects import ConfirmedDefect
from test_review_bench_cli import _STUBBED_ENVIRONMENT, _frozen_files
from test_review_bench_mining import _commit, _git, _init_repo, _write

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "review-bench"

AGENT_NAME = "bench-staff-backend-engineer"
MODEL_ID = "claude-sonnet-5"
INNER_PROMPT = "REVIEW_PROMPT_PLACEHOLDER"
DECLARED_TOOLS = frozenset({"Read", "Grep", "Glob"})
# The stream's terminal event on a run the CLI finished normally; a scenario
# with no stream.jsonl of its own gets it.
SUCCESS_STREAM_LINES = [
    json.dumps({"type": "result", "subtype": "success", "is_error": False, "terminal_reason": "completed"}).encode()
]


def _own_session_paths(scenario_dir: Path) -> tuple[Path, Path]:
    """This scenario's own (session_jsonl, session-id dir) pair, built by the
    same runner.own_session_paths_for execute_run uses -- never the whole
    session-store directory a sibling run's own transcript can also share."""
    return runner.own_session_paths_for(scenario_dir / "session-1.jsonl")


def _replace_tool_use(jsonl_path: Path, tool_use_id: str, *, name: str, input_: dict) -> None:
    """Rewrite one recorded tool_use block by tool_use_id, as parsed JSON --
    never a hand-typed string .replace() that must byte-match the fixture's
    own key order and spacing."""
    lines = []
    for raw in jsonl_path.read_text().splitlines():
        rec = json.loads(raw)
        if rec.get("type") == "assistant":
            for block in (rec.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("id") == tool_use_id:
                    block["name"] = name
                    block["input"] = input_
        lines.append(json.dumps(rec))
    jsonl_path.write_text("\n".join(lines) + "\n")


def _load_scenario(tmp_path: Path, name: str) -> Path:
    """Copy one committed fixture scenario to tmp_path (preserving its own
    symlink, for live-checkout-leak) and substitute the {FIXTURE_DIR}
    placeholder some scenarios embed in their subagent transcript for an
    absolute, checkout-independent path."""
    dest = tmp_path / name
    shutil.copytree(FIXTURES_DIR / name, dest, symlinks=True)
    for jsonl_path in dest.rglob("*.jsonl"):
        text = jsonl_path.read_text()
        if "{FIXTURE_DIR}" in text:
            jsonl_path.write_text(text.replace("{FIXTURE_DIR}", str(dest)))
    return dest


def _stream_lines(scenario_dir: Path) -> list[bytes]:
    stream_path = scenario_dir / "stream.jsonl"
    if not stream_path.exists():
        return list(SUCCESS_STREAM_LINES)
    return [line.encode() for line in stream_path.read_text().splitlines() if line.strip()]


def _rewrite_records(jsonl_path: Path, rewrite_block) -> None:
    """Apply rewrite_block(block) -> block | None to every assistant content
    block in the transcript (None drops the block), rewriting the file as
    parsed JSON."""
    lines = []
    for raw in jsonl_path.read_text().splitlines():
        rec = json.loads(raw)
        if rec.get("type") == "assistant":
            content = (rec.get("message") or {}).get("content") or []
            rec["message"]["content"] = [b for b in (rewrite_block(block) for block in content) if b is not None]
        lines.append(json.dumps(rec))
    jsonl_path.write_text("\n".join(lines) + "\n")


def _evaluate(
    scenario_dir: Path, *, own_dirs: tuple[Path, ...] | None = None,
    live_checkout_roots: tuple[Path, ...] = (), changed_relpaths: tuple[str, ...] = (),
    agent_declared_tools: frozenset[str] = DECLARED_TOOLS, timed_out: bool = False,
    fixture_dir: Path | None = None, fix_commit_relpaths: tuple[str, ...] = (),
) -> runner.RunValidity:
    session_jsonl = scenario_dir / "session-1.jsonl"
    # A sibling of scenario_dir, not nested inside it, so a relative "."
    # Grep/Glob path never accidentally resolves inside this stand-in.
    projects_root = scenario_dir.parent / "not-a-real-projects-root"
    return runner.evaluate_run_validity(
        dispatcher_session_jsonl=session_jsonl, stream_lines=_stream_lines(scenario_dir), timed_out=timed_out,
        expected_agent_name=AGENT_NAME, expected_inner_prompt=INNER_PROMPT, expected_model_id=MODEL_ID,
        agent_declared_tools=agent_declared_tools, fixture_dir=fixture_dir if fixture_dir is not None else scenario_dir,
        own_dirs=own_dirs if own_dirs is not None else (scenario_dir,),
        projects_root=projects_root, own_session_paths=_own_session_paths(scenario_dir),
        live_checkout_roots=live_checkout_roots, changed_relpaths=changed_relpaths,
        fix_commit_relpaths=fix_commit_relpaths,
    )


CHANGED_SKILL_RELPATH = "claude-skills/skills/example/SKILL.md"


# The conftest fixture replaces `default_live_checkout_roots` for every test.
_REAL_DEFAULT_LIVE_CHECKOUT_ROOTS = runner.default_live_checkout_roots


def _stow_shaped_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A git checkout laid out as this repository is (`claude/.claude/` and
    `claude-skills/skills/` under its top level) plus a stand-in config
    directory whose entries are symlinks into it, as `install.sh` stows them.
    The runner treats the checkout as its own repository and the stand-in as
    the active config directory. Returns (checkout, config_dir)."""
    checkout = _init_repo(tmp_path / "checkout")
    _write(checkout, "claude/.claude/CLAUDE.md", "instructions\n")
    _write(checkout, CHANGED_SKILL_RELPATH, "skill body\n")
    _commit(checkout, "layout")
    config_dir = tmp_path / "home" / ".claude"
    config_dir.mkdir(parents=True)
    (config_dir / "CLAUDE.md").symlink_to(checkout / "claude" / ".claude" / "CLAUDE.md")
    (config_dir / "skills").symlink_to(checkout / "claude-skills" / "skills")
    monkeypatch.setattr(runner, "REPO_ROOT", checkout)
    monkeypatch.setattr(runner, "config_dir", lambda: config_dir)
    monkeypatch.setattr(runner, "default_live_checkout_roots", _REAL_DEFAULT_LIVE_CHECKOUT_ROOTS)
    return checkout, config_dir


def _evaluate_read_of(tmp_path: Path, read_path: Path, *, roots: tuple[Path, ...]) -> runner.RunValidity:
    """The validity verdict for a reviewer transcript whose one Read is `read_path`."""
    scenario = _load_scenario(tmp_path, "live-checkout-leak")
    session_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
    _replace_tool_use(session_jsonl, "toolu_read_1", name="Read", input_={"file_path": str(read_path)})
    return _evaluate(scenario, live_checkout_roots=roots, changed_relpaths=(CHANGED_SKILL_RELPATH,))


class TestDefaultLiveCheckoutRoots:
    def test_roots_are_the_top_level_of_every_worktree_of_this_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        checkout, _config_dir = _stow_shaped_checkout(tmp_path, monkeypatch)
        sibling_worktree = tmp_path / "sibling-worktree"
        _git(checkout, "worktree", "add", "-q", str(sibling_worktree), "-b", "fix-branch")

        roots = runner.default_live_checkout_roots()

        assert roots == tuple(sorted({checkout.resolve(), sibling_worktree.resolve()}))

    def test_a_read_through_the_home_symlink_into_the_checkout_fails_as_a_live_checkout_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _checkout, config_dir = _stow_shaped_checkout(tmp_path, monkeypatch)

        result = _evaluate_read_of(
            tmp_path, config_dir / "skills" / "example" / "SKILL.md", roots=runner.default_live_checkout_roots(),
        )

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_a_read_in_a_sibling_worktree_of_a_changed_file_fails_as_a_live_checkout_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        checkout, _config_dir = _stow_shaped_checkout(tmp_path, monkeypatch)
        sibling_worktree = tmp_path / "sibling-worktree"
        _git(checkout, "worktree", "add", "-q", str(sibling_worktree), "-b", "fix-branch")

        result = _evaluate_read_of(
            tmp_path, sibling_worktree / CHANGED_SKILL_RELPATH, roots=runner.default_live_checkout_roots(),
        )

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_a_read_of_an_unrelated_file_in_the_checkout_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _checkout, config_dir = _stow_shaped_checkout(tmp_path, monkeypatch)

        result = _evaluate_read_of(tmp_path, config_dir / "CLAUDE.md", roots=runner.default_live_checkout_roots())

        assert result.ok is True

    def test_the_ambient_config_repo_contributes_its_worktrees_when_it_is_a_different_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _checkout, config_dir = _stow_shaped_checkout(tmp_path, monkeypatch)
        other_checkout = _init_repo(tmp_path / "other-checkout")
        _write(other_checkout, "CLAUDE.md", "other\n")
        _commit(other_checkout, "other layout")
        monkeypatch.setattr(runner, "config_dir", lambda: other_checkout)
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path / "checkout")

        roots = runner.default_live_checkout_roots()

        assert set(roots) == {(tmp_path / "checkout").resolve(), other_checkout.resolve()}

    def test_a_non_repository_ambient_checkout_raises_rather_than_narrowing_the_roots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stow_shaped_checkout(tmp_path, monkeypatch)
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()
        (not_a_repo / "CLAUDE.md").write_text("x\n")
        monkeypatch.setattr(runner, "config_dir", lambda: not_a_repo)

        with pytest.raises(runner.HarnessInvalidatedError, match="worktree list"):
            runner.default_live_checkout_roots()

    def test_a_worktree_path_holding_a_unicode_line_separator_stays_a_whole_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        checkout, _config_dir = _stow_shaped_checkout(tmp_path, monkeypatch)
        separator_worktree = tmp_path / "split\N{LINE SEPARATOR}here"
        _git(checkout, "worktree", "add", "-q", str(separator_worktree), "-b", "separator-branch")

        roots = runner.default_live_checkout_roots()

        assert separator_worktree.resolve() in roots

    def test_an_undecodable_worktree_listing_raises_rather_than_narrowing_the_roots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stow_shaped_checkout(tmp_path, monkeypatch)

        def undecodable_listing(*args, **kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        monkeypatch.setattr(runner.subprocess, "run", undecodable_listing)

        with pytest.raises(runner.HarnessInvalidatedError, match="worktree list"):
            runner.default_live_checkout_roots()

    def test_a_read_through_a_dotdot_path_into_a_live_root_fails_as_a_live_checkout_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        checkout, _config_dir = _stow_shaped_checkout(tmp_path, monkeypatch)
        detour_through_unrelated_dir = tmp_path / "unrelated" / ".." / "checkout" / CHANGED_SKILL_RELPATH
        (tmp_path / "unrelated").mkdir()

        result = _evaluate_read_of(tmp_path, detour_through_unrelated_dir, roots=runner.default_live_checkout_roots())

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_a_plugin_cache_read_of_a_changed_file_is_recorded_ok_with_an_out_of_session_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stow_shaped_checkout(tmp_path, monkeypatch)
        plugin_cache_copy = tmp_path / "home" / ".claude" / "plugins" / "cache" / CHANGED_SKILL_RELPATH
        plugin_cache_copy.parent.mkdir(parents=True)
        plugin_cache_copy.write_text("cached copy\n")

        result = _evaluate_read_of(tmp_path, plugin_cache_copy, roots=runner.default_live_checkout_roots())

        assert result.ok is True
        assert str(plugin_cache_copy) in result.out_of_session_paths


class TestLiveCheckoutLeakTargets:
    def test_resolves_each_relpath_under_each_root_once(self, tmp_path: Path) -> None:
        first_root, second_root = tmp_path / "first", tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()

        targets = runner.live_checkout_leak_targets((first_root, second_root), ("a.py", "dir/b.py"))

        assert targets == (
            first_root / "a.py", first_root / "dir" / "b.py", second_root / "a.py", second_root / "dir" / "b.py",
        )

    def test_a_target_matches_itself_and_a_directory_containing_it_only(self, tmp_path: Path) -> None:
        targets = runner.live_checkout_leak_targets((tmp_path,), ("dir/b.py",))

        assert runner.is_changed_file_leak(tmp_path / "dir" / "b.py", targets) is True
        assert runner.is_changed_file_leak(tmp_path / "dir", targets) is True
        assert runner.is_changed_file_leak(tmp_path / "dir" / "other.py", targets) is False


class TestCommandConstruction:
    def test_build_dispatch_command(self) -> None:
        cmd = runner.build_dispatch_command(
            "dispatch prompt text", model_id=MODEL_ID, session_id="sess-1", budget_cap_usd=11.5,
        )
        assert cmd == [
            "claude", "-p", "dispatch prompt text",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", MODEL_ID, "--session-id", "sess-1", "--max-budget-usd", "11.5",
        ]
        assert "--permission-mode" not in cmd

    def test_build_dispatcher_prompt_wraps_inner_prompt_between_markers(self) -> None:
        prompt = runner.build_dispatcher_prompt(AGENT_NAME, "the inner review prompt")
        assert prompt.startswith(runner.DISPATCH_PROMPT_TEMPLATE.format(agent=AGENT_NAME))
        assert "<<<\nthe inner review prompt\n>>>" in prompt

    def test_build_review_prompt_points_at_commit_subject_file_and_embeds_no_subject_text(self) -> None:
        prompt = runner.build_review_prompt()
        assert ".bench/commit-subject.txt" in prompt
        assert "{" not in prompt
        assert ".bench/change.diff" in prompt
        assert ".bench/change-function-context.diff" in prompt
        assert ".bench/changed-files.tsv" in prompt


class TestBlockPlanDeterminism:
    def test_same_inputs_produce_same_order(self) -> None:
        plan_a = runner.build_block_plan("defect-1", ("current-rule", "function-context"), 3, seed=42)
        plan_b = runner.build_block_plan("defect-1", ("current-rule", "function-context"), 3, seed=42)
        assert plan_a.ordered_runs == plan_b.ordered_runs

    def test_holds_k_per_arm(self) -> None:
        plan = runner.build_block_plan("defect-1", ("current-rule", "function-context"), 4, seed=1)
        assert sorted(plan.ordered_runs) == sorted(
            (arm, i) for arm in ("current-rule", "function-context") for i in range(4)
        )

    def test_different_defect_id_can_differ(self) -> None:
        plan_a = runner.build_block_plan("defect-1", ("current-rule", "function-context"), 5, seed=1)
        plan_b = runner.build_block_plan("defect-2", ("current-rule", "function-context"), 5, seed=1)
        assert plan_a.ordered_runs != plan_b.ordered_runs


class TestDispatchPromptVerbatimCheck:
    def test_matching_prompt_passes(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))
        assert result.ok is True

    def test_mismatched_prompt_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        session_jsonl = scenario / "session-1.jsonl"
        session_jsonl.write_text(session_jsonl.read_text().replace(INNER_PROMPT, "a different prompt entirely"))
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_PROMPT_MISMATCH
        assert result.prompt_verbatim is False

    def test_a_failure_confirmed_after_the_prompt_check_still_carries_prompt_verbatim_true(self, tmp_path: Path) -> None:
        """prompt_verbatim records whether the comparison actually ran and
        matched -- not merely "the failure wasn't prompt-mismatch". A
        later check's failure (here: undeclared tool) still confirms the
        prompt matched on the way there."""
        scenario = _load_scenario(tmp_path, "undeclared-tool")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_UNDECLARED_TOOL
        assert result.prompt_verbatim is True

    def test_a_failure_before_the_prompt_check_carries_prompt_verbatim_false(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "extra-dispatcher-tool-call")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_EXTRA_DISPATCHER_TOOL_CALL
        assert result.prompt_verbatim is False


class TestObservedModelCheck:
    def test_matching_model_passes(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))
        assert result.ok is True
        assert result.observed_model == MODEL_ID

    def test_mismatched_model_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "model-mismatch")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_MODEL_MISMATCH

    def test_applies_the_same_way_under_a_judge_model_id(self, tmp_path: Path) -> None:
        """The same check, under the judge's own frozen ID in place of the
        reviewer's -- the judge runs reuse this unchanged."""
        scenario = _load_scenario(tmp_path, "normal-success")  # observed model is claude-sonnet-5
        result = _evaluate(scenario, agent_declared_tools=DECLARED_TOOLS)
        session_jsonl = scenario / "session-1.jsonl"
        result_as_judge = runner.evaluate_run_validity(
            dispatcher_session_jsonl=session_jsonl, stream_lines=SUCCESS_STREAM_LINES, timed_out=False,
            expected_agent_name=AGENT_NAME, expected_inner_prompt=INNER_PROMPT,
            expected_model_id="claude-opus-5-5", agent_declared_tools=DECLARED_TOOLS,
            fixture_dir=scenario, own_dirs=(scenario,), projects_root=scenario / "nope",
            own_session_paths=_own_session_paths(scenario), live_checkout_roots=(), changed_relpaths=(),
        )
        assert result.ok is True
        assert result_as_judge.ok is False
        assert result_as_judge.failure_reason == runner.VALIDITY_FAIL_MODEL_MISMATCH


class TestDispatcherToolCallChecks:
    def test_extra_dispatcher_tool_call_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "extra-dispatcher-tool-call")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_EXTRA_DISPATCHER_TOOL_CALL

    def test_wrong_agent_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        result = _evaluate(scenario)
        wrong_result = runner.evaluate_run_validity(
            dispatcher_session_jsonl=scenario / "session-1.jsonl", stream_lines=SUCCESS_STREAM_LINES, timed_out=False,
            expected_agent_name="bench-staff-sdet", expected_inner_prompt=INNER_PROMPT,
            expected_model_id=MODEL_ID, agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario,
            own_dirs=(scenario,), projects_root=scenario / "nope",
            own_session_paths=_own_session_paths(scenario), live_checkout_roots=(), changed_relpaths=(),
        )
        assert result.ok is True
        assert wrong_result.ok is False
        assert wrong_result.failure_reason == runner.VALIDITY_FAIL_WRONG_AGENT


class TestUndeclaredToolCheck:
    def test_subagent_tool_outside_declared_list_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "undeclared-tool")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_UNDECLARED_TOOL


class TestLeakAndOutOfSessionChecks:
    def test_read_through_symlink_into_live_checkout_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        result = _evaluate(
            scenario, live_checkout_roots=(scenario,), changed_relpaths=("fake-live-checkout/changed_file.py",),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_read_of_the_live_copy_of_a_file_only_the_fix_commit_changed_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        result = _evaluate(
            scenario, live_checkout_roots=(scenario,), changed_relpaths=("introducing_only.py",),
            fix_commit_relpaths=("fake-live-checkout/changed_file.py",),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_fix_commit_files_never_count_as_whole_file_reads_of_changed_files(self, tmp_path: Path) -> None:
        """The adherence diagnostic keeps the introducing commit's set of changed files,
        so a fix-only file is not counted as a whole-file read of a changed file."""
        scenario = _load_scenario(tmp_path, "normal-success")
        result = _evaluate(scenario, changed_relpaths=(), fix_commit_relpaths=("changed_file.py",))
        assert result.ok is True
        assert result.stats.whole_file_reads_of_changed_files == 0

    def test_read_inside_a_live_checkout_root_but_not_a_changed_file_passes(self, tmp_path: Path) -> None:
        """The leak check is scoped to changed_relpaths specifically, not to
        "anything under a live-checkout root" -- this allow-side test guards
        against a regression that widens it to the latter."""
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        result = _evaluate(
            scenario, own_dirs=(scenario,), live_checkout_roots=(scenario,),
            changed_relpaths=("some/other/file.py",),
        )
        assert result.ok is True

    def test_grep_over_a_directory_containing_a_changed_file_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        session_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(session_jsonl, "toolu_read_1", name="Grep", input_={"path": str(scenario)})
        result = _evaluate(
            scenario, live_checkout_roots=(scenario,), changed_relpaths=("fake-live-checkout/changed_file.py",),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_glob_over_a_directory_containing_a_changed_file_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        session_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(session_jsonl, "toolu_read_1", name="Glob", input_={"path": str(scenario)})
        result = _evaluate(
            scenario, live_checkout_roots=(scenario,), changed_relpaths=("fake-live-checkout/changed_file.py",),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_read_under_a_stand_in_config_dir_projects_root_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "out-of-session-read")
        projects_root = scenario / "unrelated-sibling"
        result = runner.evaluate_run_validity(
            dispatcher_session_jsonl=scenario / "session-1.jsonl", stream_lines=SUCCESS_STREAM_LINES, timed_out=False,
            expected_agent_name=AGENT_NAME, expected_inner_prompt=INNER_PROMPT, expected_model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, own_dirs=(scenario,),
            projects_root=projects_root, own_session_paths=_own_session_paths(scenario),
            live_checkout_roots=(), changed_relpaths=(),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_CONFIG_DIR_LEAK

    def _evaluate_with_read_of(self, scenario: Path, read_path: Path) -> runner.RunValidity:
        session_jsonl, _ = _own_session_paths(scenario)
        agent_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(agent_jsonl, "toolu_read_1", name="Read", input_={"file_path": str(read_path)})
        return runner.evaluate_run_validity(
            dispatcher_session_jsonl=session_jsonl, stream_lines=SUCCESS_STREAM_LINES, timed_out=False,
            expected_agent_name=AGENT_NAME, expected_inner_prompt=INNER_PROMPT, expected_model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, own_dirs=(scenario,),
            projects_root=scenario, own_session_paths=_own_session_paths(scenario),
            live_checkout_roots=(), changed_relpaths=(),
        )

    def test_read_of_a_persisted_tool_result_in_the_runs_own_session_dir_passes(self, tmp_path: Path) -> None:
        """Genuine allow-side test for is_config_dir_leak's own-session
        carve-out: the Read path is under `<session-id>/tool-results/`, and
        projects_root is a real ancestor of it, not equal to it. Deleting
        the carve-out line flips this result."""
        scenario = _load_scenario(tmp_path, "out-of-session-read")

        result = self._evaluate_with_read_of(scenario, scenario / "session-1" / "tool-results" / "persisted.txt")

        assert result.ok is True

    def test_read_of_a_persisted_tool_result_in_a_sibling_session_dir_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "out-of-session-read")

        result = self._evaluate_with_read_of(scenario, scenario / "session-2" / "tool-results" / "persisted.txt")

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_CONFIG_DIR_LEAK

    def test_unchanged_live_file_is_recorded_not_failed(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "out-of-session-read")
        result = _evaluate(scenario, own_dirs=(scenario / "session-1",))
        assert result.ok is True
        assert str(scenario / "unrelated-sibling" / "unrelated.py") in result.out_of_session_paths


class TestConfigDirLeakContainingDirectoryBranch:
    """Direct unit tests for is_config_dir_leak's "directory containing
    projects_root" branch, mirroring the coverage the analogous
    is_changed_file_leak branch already has."""

    def test_directory_containing_projects_root_is_a_leak(self, tmp_path: Path) -> None:
        projects_root = tmp_path / "config" / "projects"
        projects_root.mkdir(parents=True)
        containing_dir = tmp_path / "config"
        assert runner.is_config_dir_leak(containing_dir, projects_root, own_session_paths=()) is True

    def test_directory_not_containing_projects_root_is_not_a_leak(self, tmp_path: Path) -> None:
        projects_root = tmp_path / "config" / "projects"
        projects_root.mkdir(parents=True)
        unrelated_dir = tmp_path / "unrelated"
        unrelated_dir.mkdir()
        assert runner.is_config_dir_leak(unrelated_dir, projects_root, own_session_paths=()) is False


class TestRelativePathResolution:
    """A relative Read/Grep/Glob path must resolve against the subagent's
    own runtime directory (fixture_dir), not this test process's own cwd --
    Path(raw_path).resolve() alone anchors to the wrong directory."""

    def test_relative_glob_that_stays_inside_the_fixture_directory_passes(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        session_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(session_jsonl, "toolu_read_1", name="Glob", input_={"path": "."})
        result = _evaluate(
            scenario, fixture_dir=scenario, own_dirs=(scenario,),
            live_checkout_roots=(runner.REPO_ROOT,), changed_relpaths=("some/other/file.py",),
        )
        assert result.ok is True

    def test_relative_grep_that_resolves_into_a_live_checkout_root_fails(self, tmp_path: Path) -> None:
        """Same relative "." path as the allow case above, but with
        live_checkout_roots naming the fixture directory itself. A relative
        Grep path resolves against fixture_dir, so here it is a leak;
        resolving against the process's own cwd would miss it."""
        scenario = _load_scenario(tmp_path, "normal-success")
        session_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(session_jsonl, "toolu_read_1", name="Grep", input_={"path": "."})
        result = _evaluate(
            scenario, fixture_dir=scenario, live_checkout_roots=(scenario,),
            changed_relpaths=("changed_file.py",),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK


def _write_subagent_transcript_with_tool_call(path: Path, *, name: str, input_: dict) -> None:
    record = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": name, "input": input_}]},
    }
    path.write_text(json.dumps(record) + "\n")


class TestExtractReadLikeCallTargets:
    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "expected_path"),
        [
            ("Read", {"file_path": "/repo/a.py"}, "/repo/a.py"),
            ("Grep", {"pattern": "needle", "path": "/repo/src"}, "/repo/src"),
            ("Glob", {"pattern": "/abs/dir/*.py"}, "/abs/dir"),
            ("Glob", {"pattern": "/*.py"}, "/"),
            ("Glob", {"pattern": "/abs/{a,b}/x.py"}, "/abs"),
            ("Glob", {"pattern": "/abs/dir/file[12].py"}, "/abs/dir"),
            ("Glob", {"pattern": "/abs/dir/file?.py"}, "/abs/dir"),
            ("Glob", {"pattern": "src/**/*.py"}, "src"),
            ("Glob", {"pattern": "/abs/dir/exact.py"}, "/abs/dir/exact.py"),
            ("Glob", {"path": "/repo", "pattern": "src/**/*.py"}, "/repo/src"),
            ("Glob", {"path": "/repo", "pattern": "*.py"}, "/repo"),
            ("Glob", {"path": "/repo", "pattern": "/abs/dir/*.py"}, "/abs/dir"),
        ],
    )
    def test_the_recorded_path_is_where_the_call_can_reach(
        self, tmp_path: Path, tool_name: str, tool_input: dict, expected_path: str,
    ) -> None:
        transcript = tmp_path / "agent.jsonl"
        _write_subagent_transcript_with_tool_call(transcript, name=tool_name, input_=tool_input)
        assert [call.path for call in runner.extract_read_like_calls(transcript)] == [expected_path]

    @pytest.mark.parametrize("tool_input", [{"pattern": "*.py"}, {}, {"pattern": ""}])
    def test_a_glob_anchored_to_no_directory_records_no_call(self, tmp_path: Path, tool_input: dict) -> None:
        transcript = tmp_path / "agent.jsonl"
        _write_subagent_transcript_with_tool_call(transcript, name="Glob", input_=tool_input)
        assert runner.extract_read_like_calls(transcript) == []


class TestAbsoluteGlobPatternChecks:
    def _scenario_with_glob(self, tmp_path: Path, glob_input: dict) -> Path:
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        agent_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(agent_jsonl, "toolu_read_1", name="Glob", input_=glob_input)
        return scenario

    def test_an_absolute_pattern_with_no_path_into_a_changed_files_directory_fails(self, tmp_path: Path) -> None:
        live_checkout = tmp_path / "live-checkout-leak"
        scenario = self._scenario_with_glob(tmp_path, {"pattern": f"{live_checkout}/fake-live-checkout/*.py"})
        result = _evaluate(
            scenario, live_checkout_roots=(scenario,), changed_relpaths=("fake-live-checkout/changed_file.py",),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_an_absolute_pattern_overrides_an_in_session_path_and_is_recorded_out_of_session(
        self, tmp_path: Path,
    ) -> None:
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        scenario = self._scenario_with_glob(tmp_path, {"path": ".", "pattern": f"{outside_dir}/*.py"})
        result = _evaluate(scenario, own_dirs=(scenario,))
        assert result.ok is True
        assert result.out_of_session_paths == (str(outside_dir),)

    def test_a_relative_pattern_under_the_fixture_directory_stays_in_session(self, tmp_path: Path) -> None:
        scenario = self._scenario_with_glob(tmp_path, {"pattern": "src/**/*.py"})
        result = _evaluate(scenario, own_dirs=(scenario,), changed_relpaths=("other/file.py",))
        assert result.ok is True
        assert result.out_of_session_paths == ()


class TestHomeAndEnvironmentVariablePathChecks:
    @pytest.fixture
    def home_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        monkeypatch.setenv("HOME", str(home_dir))
        return home_dir

    def _scenario_reading(self, tmp_path: Path, raw_path: str) -> Path:
        scenario = _load_scenario(tmp_path / "scenario-root", "normal-success")
        agent_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(agent_jsonl, "toolu_read_1", name="Read", input_={"file_path": raw_path})
        return scenario

    @pytest.mark.parametrize("raw_path", ["~/notes.py", "$HOME/notes.py"])
    def test_a_home_relative_read_of_a_changed_file_in_a_live_checkout_fails(
        self, tmp_path: Path, home_dir: Path, raw_path: str,
    ) -> None:
        scenario = self._scenario_reading(tmp_path, raw_path)
        result = _evaluate(scenario, live_checkout_roots=(home_dir,), changed_relpaths=("notes.py",))
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    @pytest.mark.parametrize("raw_path", ["~/notes.py", "$HOME/notes.py"])
    def test_a_home_relative_read_is_recorded_out_of_session_under_its_raw_text(
        self, tmp_path: Path, home_dir: Path, raw_path: str,
    ) -> None:
        scenario = self._scenario_reading(tmp_path, raw_path)
        result = _evaluate(scenario, own_dirs=(scenario,))
        assert result.ok is True
        assert result.out_of_session_paths == (raw_path,)

    def test_a_literal_tilde_directory_in_a_changed_path_is_not_expanded(self, tmp_path: Path, home_dir: Path) -> None:
        """A repo-relative changed path is never home-expanded, so a read of
        the fixture's own `~/f.py` counts as a whole-file read of it."""
        literal_tilde_file = tmp_path / "~" / "f.py"
        read_call = runner.ReadLikeCall(
            tool_use_id="toolu_1", tool_name="Read", path=str(literal_tilde_file), offset=None,
        )
        stats = runner.compute_read_stats(
            [read_call], {"toolu_1": "x = 1\n"}, fixture_dir=tmp_path, changed_relpaths=frozenset({"~/f.py"}),
        )
        assert stats.whole_file_reads_of_changed_files == 1

    def test_a_tilde_user_that_cannot_be_resolved_stays_a_literal_relative_path(self, tmp_path: Path) -> None:
        resolved = runner._resolve("~no-such-user-here/f.py", base_dir=tmp_path)
        assert resolved == (tmp_path / "~no-such-user-here" / "f.py").resolve()


class TestSessionStoreNotFoundThroughExecuteRun:
    def test_no_session_jsonl_found_records_session_store_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: None)
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 0.0)
        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=tmp_path, live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )
        record = runner.execute_run(
            ctx, arm="current-rule", run_index=0, session_id="missing-session",
            launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False),
        )
        assert record.status == runner.STATUS_MISSING
        assert record.missing_reason == runner.VALIDITY_FAIL_SESSION_STORE_NOT_FOUND


def _flush_run_context(fixture_dir: Path) -> runner.RunContext:
    return runner.RunContext(
        campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
        agent_declared_tools=DECLARED_TOOLS, fixture_dir=fixture_dir, live_checkout_roots=(),
        changed_relpaths=("changed_file.py",), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
        environment=runner.EnvironmentRecord("v1", "sha1"),
    )


class TestSessionFlushWaitThroughExecuteRun:
    """The dispatcher transcript and subagent sidecar can reach disk after
    the launch call returns; execute_run must wait, bounded, before
    classifying instead of recording the lag as a validity failure."""

    @pytest.fixture
    def config_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        config_root = tmp_path / "config"
        (config_root / "projects" / "some-hash").mkdir(parents=True)
        monkeypatch.setattr(runner, "config_dir", lambda: config_root)
        monkeypatch.setattr(runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.02)
        return config_root

    def test_a_sidecar_that_lands_after_launch_returns_still_yields_an_ok_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_root: Path,
    ) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 10.0)
        session_dir = config_root / "projects" / "some-hash"
        late_sidecar = threading.Timer(
            0.3, shutil.copytree, args=(scenario / "session-1" / "subagents", session_dir / "session-a" / "subagents"),
        )

        def launch(cmd, cwd, timeout_s):
            shutil.copyfile(scenario / "session-1.jsonl", session_dir / "session-a.jsonl")
            late_sidecar.start()
            return SUCCESS_STREAM_LINES, False

        try:
            record = runner.execute_run(
                _flush_run_context(scenario), arm="current-rule", run_index=0, session_id="session-a", launch=launch,
            )
        finally:
            late_sidecar.join()

        assert record.status == runner.STATUS_OK
        assert record.findings_text == "Finding: file:1 looks fine."

    def test_a_session_that_never_appears_is_waited_for_then_recorded_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_root: Path,
    ) -> None:
        wait_s = 0.3
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", wait_s)

        start = time.monotonic()
        record = runner.execute_run(
            _flush_run_context(tmp_path), arm="current-rule", run_index=0, session_id="never-written",
            launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False),
        )
        elapsed = time.monotonic() - start

        assert record.missing_reason == runner.VALIDITY_FAIL_SESSION_STORE_NOT_FOUND
        assert wait_s <= elapsed < 5

    def test_a_timed_out_run_is_not_waited_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_root: Path,
    ) -> None:
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 30.0)

        start = time.monotonic()
        record = runner.execute_run(
            _flush_run_context(tmp_path), arm="current-rule", run_index=0, session_id="never-written",
            launch=lambda cmd, cwd, timeout_s: ([], True),
        )

        assert time.monotonic() - start < 5
        assert record.status == runner.STATUS_MISSING
        assert record.missing_reason == runner.MISSING_REASON_TIMEOUT


class TestWaitForSessionFlushSettlesOnlyUpToTheTimeout:
    """The transcript and sidecar are returned once no file's size changed
    across two consecutive polls, or at SESSION_FLUSH_TIMEOUT_S, whichever comes
    first. The barrier holds only up to that timeout; a transcript still
    growing then is returned as it stands."""

    SESSION_ID = "session-a"

    @pytest.fixture
    def projects_root(self, tmp_path: Path) -> Path:
        projects_root = tmp_path / "projects"
        session_store = projects_root / "some-hash"
        (session_store / self.SESSION_ID / "subagents").mkdir(parents=True)
        (session_store / f"{self.SESSION_ID}.jsonl").write_text("dispatcher\n")
        sidecar_dir = session_store / self.SESSION_ID / "subagents"
        (sidecar_dir / "agent-1.meta.json").write_text("{}")
        (sidecar_dir / "agent-1.jsonl").write_text("subagent\n")
        return projects_root

    def _record_sleeps(self, monkeypatch: pytest.MonkeyPatch, on_sleep) -> list[float]:
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            on_sleep(len(sleeps))

        monkeypatch.setattr(runner.time, "sleep", fake_sleep)
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 60.0)
        return sleeps

    def test_returns_after_one_poll_interval_when_nothing_changes(
        self, projects_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sleeps = self._record_sleeps(monkeypatch, lambda sleep_count: None)
        session_jsonl = runner.wait_for_session_flush(projects_root, self.SESSION_ID, timed_out=False)
        assert session_jsonl == projects_root / "some-hash" / f"{self.SESSION_ID}.jsonl"
        assert len(sleeps) == 1

    def test_keeps_polling_while_the_transcript_is_still_growing(
        self, projects_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        transcript = projects_root / "some-hash" / f"{self.SESSION_ID}.jsonl"

        def grow_for_two_polls(sleep_count: int) -> None:
            if sleep_count <= 2:
                with transcript.open("a") as fh:
                    fh.write("more output\n")

        sleeps = self._record_sleeps(monkeypatch, grow_for_two_polls)
        session_jsonl = runner.wait_for_session_flush(projects_root, self.SESSION_ID, timed_out=False)
        assert session_jsonl == transcript
        assert len(sleeps) == 3
        assert transcript.read_text().count("more output") == 2

    def test_keeps_polling_while_a_sidecar_file_is_still_growing(
        self, projects_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sidecar_transcript = projects_root / "some-hash" / self.SESSION_ID / "subagents" / "agent-1.jsonl"

        def grow_once(sleep_count: int) -> None:
            if sleep_count == 1:
                with sidecar_transcript.open("a") as fh:
                    fh.write("late findings\n")

        sleeps = self._record_sleeps(monkeypatch, grow_once)
        runner.wait_for_session_flush(projects_root, self.SESSION_ID, timed_out=False)
        assert len(sleeps) == 2

    def test_a_transcript_that_never_stops_growing_is_still_returned_at_the_timeout(
        self, projects_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        transcript = projects_root / "some-hash" / f"{self.SESSION_ID}.jsonl"

        real_sleep = time.sleep

        def grow_then_sleep(seconds: float) -> None:
            with transcript.open("a") as fh:
                fh.write("more output\n")
            real_sleep(0.02)

        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 0.1)
        monkeypatch.setattr(runner.time, "sleep", grow_then_sleep)
        assert runner.wait_for_session_flush(projects_root, self.SESSION_ID, timed_out=False) == transcript

    def test_a_missing_sidecar_still_returns_the_transcript_at_the_timeout(
        self, projects_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for sidecar_file in (projects_root / "some-hash" / self.SESSION_ID / "subagents").iterdir():
            sidecar_file.unlink()
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 0.05)
        monkeypatch.setattr(runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.01)
        session_jsonl = runner.wait_for_session_flush(projects_root, self.SESSION_ID, timed_out=False)
        assert session_jsonl == projects_root / "some-hash" / f"{self.SESSION_ID}.jsonl"

    def test_a_timed_out_run_gets_a_single_check_and_no_sleep(
        self, projects_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sleeps = self._record_sleeps(monkeypatch, lambda sleep_count: None)
        session_jsonl = runner.wait_for_session_flush(projects_root, self.SESSION_ID, timed_out=True)
        assert session_jsonl is not None
        assert sleeps == []

    def test_a_session_that_never_appears_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 0.05)
        monkeypatch.setattr(runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.01)
        assert runner.wait_for_session_flush(tmp_path / "projects", "never-written", timed_out=False) is None


class TestExecuteRunOkRecord:
    """Every field of an ok reviewer record comes from the run's own
    transcript, stats, and context."""

    def test_the_record_carries_each_field_from_its_own_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        session_jsonl = scenario / "session-1.jsonl"
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: session_jsonl)
        monkeypatch.setattr(runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.01)
        stream = [json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.5}).encode()]
        ctx = dataclasses.replace(_flush_run_context(scenario), over_read_cap=True)

        record = runner.execute_run(
            ctx, arm="current-rule", run_index=3, session_id="session-a",
            launch=lambda cmd, cwd, timeout_s: (stream, False),
        )

        assert record.status == runner.STATUS_OK
        assert (record.campaign_id, record.defect_id, record.arm, record.run_index) == ("c1", "d1", "current-rule", 3)
        assert record.findings_text == "Finding: file:1 looks fine."
        assert record.observed_model == MODEL_ID
        assert record.observed_tools == ("Read",)
        assert record.read_calls == 1
        assert record.read_tokens_est == len("def f():\n    return 1\n") // 4
        assert record.partial_view_reads == 0
        assert record.paged_followups == 0
        assert record.whole_file_reads_of_changed_files == 1
        assert record.over_read_cap is True
        assert record.dispatch_prompt_verbatim is True
        assert (record.cli_version, record.ambient_config_commit) == ("v1", "sha1")
        assert record.total_cost_usd == pytest.approx(0.5)
        assert record.attempts == 1


class TestFixCommitPathsReachTheLeakCheck:
    """A dropped forward of `fix_commit_relpaths` between the spec, the run
    context, and the validity check would silently narrow the leak check to
    the introducing commit's files."""

    FIX_ONLY_RELPATHS = ("fake-live-checkout/changed_file.py",)

    @staticmethod
    def _leaking_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        monkeypatch.setattr(
            runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl",
        )
        monkeypatch.setattr(runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.01)
        return scenario

    def _ctx(self, scenario: Path, *, fix_commit_relpaths: tuple[str, ...]) -> runner.RunContext:
        return dataclasses.replace(
            _flush_run_context(scenario), live_checkout_roots=(scenario,), changed_relpaths=("introducing_only.py",),
            fix_commit_relpaths=fix_commit_relpaths,
        )

    def test_execute_run_fails_a_read_of_a_live_file_only_the_fix_commit_changed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = self._leaking_scenario(tmp_path, monkeypatch)

        record = runner.execute_run(
            self._ctx(scenario, fix_commit_relpaths=self.FIX_ONLY_RELPATHS), arm="current-rule", run_index=0,
            session_id="session-a", launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False),
        )

        assert record.status == runner.STATUS_MISSING
        assert record.missing_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    def test_execute_run_passes_the_same_read_when_the_fix_commit_did_not_change_that_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = self._leaking_scenario(tmp_path, monkeypatch)

        record = runner.execute_run(
            self._ctx(scenario, fix_commit_relpaths=()), arm="current-rule", run_index=0,
            session_id="session-a", launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False),
        )

        assert record.status == runner.STATUS_OK

    def test_run_defect_block_forwards_the_specs_fix_commit_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = self._leaking_scenario(tmp_path, monkeypatch)
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1"))
        spec = runner.DefectFixtureSpec(
            defect_id="d1", arm_fixture_dirs={"current-rule": scenario},
            arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
            live_checkout_roots=(scenario,), changed_relpaths=("introducing_only.py",), over_read_cap=False,
            fix_commit_relpaths=self.FIX_ONLY_RELPATHS,
        )

        result = runner.run_defect_block(
            spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1",
            launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False), workers=1,
        )

        assert [record.missing_reason for record in result.records] == [runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK]


class TestExtractFinalText:
    def test_the_last_assistant_text_block_wins_over_an_interim_one(self, tmp_path: Path) -> None:
        transcript = tmp_path / "agent.jsonl"
        records = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "interim: still reading"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "final findings"}]}},
        ]
        transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
        assert runner.extract_final_text(transcript) == "final findings"


class TestConfigDirLeakOwnSessionCarveOutAtExecuteRun:
    """Drives execute_run itself, so the own-session carve-out is pinned at the call
    site's construction of own_session_paths, not only at is_config_dir_leak."""

    @staticmethod
    def _execute_run_reading(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, read_target_of: Callable[[Path, Path], Path],
    ) -> runner.RunRecord:
        """Two sibling runs of one arm share a per-arm session-store directory.
        Run A's subagent Reads `read_target_of(session_a_dir, session_b_dir)`,
        where each argument is that run's own `<session-id>/` directory."""
        projects_root = tmp_path / "projects"
        shared_parent = projects_root / "some-hash"
        shared_parent.mkdir(parents=True)

        session_a_jsonl = shared_parent / "session-a.jsonl"
        shutil.copyfile(FIXTURES_DIR / "normal-success" / "session-1.jsonl", session_a_jsonl)
        # execute_run computes its own expected inner prompt via
        # runner.build_review_prompt(), never the raw INNER_PROMPT
        # placeholder the static fixture embeds.
        session_a_jsonl.write_text(session_a_jsonl.read_text().replace(INNER_PROMPT, runner.build_review_prompt()))
        session_a_dir = shared_parent / "session-a"
        shutil.copytree(FIXTURES_DIR / "normal-success" / "session-1" / "subagents", session_a_dir / "subagents")

        # Session B is a sibling run's own session directory: it shares
        # shared_parent with session A and is never looked up by execute_run.
        (shared_parent / "session-b.jsonl").write_text("")
        session_b_dir = shared_parent / "session-b"
        (session_b_dir / "subagents").mkdir(parents=True)
        (session_b_dir / "subagents" / "agent-1.jsonl").write_text("{}\n")

        agent_jsonl = session_a_dir / "subagents" / "agent-1.jsonl"
        _replace_tool_use(
            agent_jsonl, "toolu_read_1", name="Read",
            input_={"file_path": str(read_target_of(session_a_dir, session_b_dir))},
        )

        monkeypatch.setattr(
            runner, "find_session_jsonl_by_id",
            lambda root, session_id: session_a_jsonl if session_id == "session-a" else None,
        )
        monkeypatch.setattr(runner, "config_dir", lambda: projects_root.parent)

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=tmp_path / "fixture", live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )
        return runner.execute_run(
            ctx, arm="current-rule", run_index=0, session_id="session-a",
            launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False),
        )

    def test_read_into_a_sibling_runs_subagent_dir_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Read that resolves into a *different* sibling's subagent dir must
        be VALIDITY_FAIL_CONFIG_DIR_LEAK, not exempted -- is_config_dir_leak's
        own docstring names this as the regression it exists to prevent.
        Pins only the deny side of execute_run's own_session_paths; the
        allow side is the persisted-tool-result test below."""
        record = self._execute_run_reading(
            tmp_path, monkeypatch, read_target_of=lambda own_dir, sibling_dir: sibling_dir / "subagents" / "agent-1.jsonl",
        )

        assert record.status == runner.STATUS_MISSING
        assert record.missing_reason == runner.VALIDITY_FAIL_CONFIG_DIR_LEAK

    def test_read_of_a_persisted_tool_result_in_the_runs_own_session_dir_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reverting execute_run's own_session_paths to a narrower pair (such as
        the session transcript plus `<session-id>/subagents`) fails this run."""
        record = self._execute_run_reading(
            tmp_path, monkeypatch, read_target_of=lambda own_dir, sibling_dir: own_dir / "tool-results" / "persisted.txt",
        )

        assert record.status == runner.STATUS_OK
        assert record.missing_reason is None


class TestFinalResultChecks:
    def test_a_stream_with_no_result_event_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        result = runner.evaluate_run_validity(
            dispatcher_session_jsonl=scenario / "session-1.jsonl", stream_lines=[], timed_out=False,
            expected_agent_name=AGENT_NAME, expected_inner_prompt=INNER_PROMPT, expected_model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, own_dirs=(scenario,),
            projects_root=scenario.parent / "not-a-real-projects-root", own_session_paths=_own_session_paths(scenario),
            live_checkout_roots=(), changed_relpaths=(),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_NO_RESULT_EVENT

    def test_a_non_error_result_whose_subtype_is_not_success_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        (scenario / "stream.jsonl").write_text(
            json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": False}) + "\n"
        )
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_RESULT_ERROR
        assert result.failure_detail == "subtype 'error_max_turns', terminal_reason None"

    def test_a_result_flagged_is_error_fails_even_when_its_subtype_is_success(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        (scenario / "stream.jsonl").write_text(
            json.dumps({"type": "result", "subtype": "success", "is_error": True}) + "\n"
        )
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_RESULT_ERROR
        assert result.failure_detail == "subtype 'success', terminal_reason None"

    def test_error_result_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "result-error")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_RESULT_ERROR
        assert result.failure_detail == "subtype 'error_during_execution', terminal_reason None"

    def test_budget_stop_records_budget_missing_reason(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "budget-stop")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.MISSING_REASON_BUDGET
        assert result.failure_detail == "subtype None, terminal_reason 'budget_exhausted'"

    def test_timeout_before_anything_else_records_timeout(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        result = _evaluate(scenario, timed_out=True)
        assert result.ok is False
        assert result.failure_reason == runner.MISSING_REASON_TIMEOUT


class TestExtractFinalResultTypes:
    def test_a_non_string_terminal_reason_or_subtype_is_reported_as_none(self) -> None:
        line = json.dumps({"type": "result", "is_error": True, "terminal_reason": 7, "subtype": ["x"]}).encode()

        assert runner.extract_final_result([line]) == runner.FinalResult(True, None, None)


class TestZeroDispatcherToolCalls:
    def test_a_dispatcher_transcript_with_no_tool_call_gets_its_own_reason(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _rewrite_records(
            scenario / "session-1.jsonl", lambda block: None if block.get("type") == "tool_use" else block,
        )
        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_NO_DISPATCHER_TOOL_CALL
        assert result.prompt_verbatim is False


class TestUnreadableTranscript:
    """An unreadable transcript is not an empty one: it gets its own reason
    rather than no-dispatcher-tool-call or empty-findings."""

    def test_an_unreadable_dispatcher_transcript_gets_its_own_reason(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        session_jsonl = scenario / "session-1.jsonl"
        session_jsonl.unlink()
        session_jsonl.mkdir()  # opening a directory raises OSError, whatever the process's privileges

        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE
        assert result.failure_detail == "IsADirectoryError: Is a directory"
        assert result.prompt_verbatim is False

    def test_an_unreadable_subagent_transcript_gets_its_own_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")

        def raise_permission_error(subagent_jsonl: Path) -> str:
            raise PermissionError(13, "Permission denied", str(subagent_jsonl))

        monkeypatch.setattr(runner, "extract_final_text", raise_permission_error)

        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE
        assert result.failure_detail == "PermissionError: Permission denied"
        assert result.prompt_verbatim is True

    @pytest.mark.parametrize(
        ("tool_name", "tool_input"),
        [
            ("Read", {"file_path": "/repo/a\x00b.py"}),
            ("Grep", {"pattern": "needle", "path": "/repo/a\x00b"}),
            ("Glob", {"pattern": "/repo/a\x00b/*.py"}),
        ],
    )
    def test_a_read_like_call_with_an_embedded_nul_byte_gets_its_own_reason_without_the_path(
        self, tmp_path: Path, tool_name: str, tool_input: dict,
    ) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        subagent_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(subagent_jsonl, "toolu_read_1", name=tool_name, input_=tool_input)

        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE
        assert result.failure_detail == "a read-like call carried a path that cannot be resolved"
        assert result.prompt_verbatim is True

    @pytest.mark.parametrize("nul_call_comes_first", [True, False], ids=["nul-then-leak", "leak-then-nul"])
    def test_a_live_checkout_leak_read_wins_over_a_read_with_an_embedded_nul_byte(
        self, tmp_path: Path, nul_call_comes_first: bool,
    ) -> None:
        scenario = _load_scenario(tmp_path, "live-checkout-leak")
        subagent_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        nul_call = {"type": "tool_use", "id": "toolu_read_nul", "name": "Read", "input": {"file_path": "/repo/a\x00b.py"}}

        lines = []
        for raw in subagent_jsonl.read_text().splitlines():
            record = json.loads(raw)
            if record.get("type") == "assistant":
                content = record["message"]["content"]
                leak_index = next(i for i, block in enumerate(content) if block.get("id") == "toolu_read_1")
                content.insert(leak_index if nul_call_comes_first else leak_index + 1, nul_call)
            lines.append(json.dumps(record))
        subagent_jsonl.write_text("\n".join(lines) + "\n")

        result = _evaluate(
            scenario, live_checkout_roots=(scenario,), changed_relpaths=("fake-live-checkout/changed_file.py",),
        )

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK

    @pytest.mark.parametrize(
        "reader", [runner.read_dispatcher_transcript, runner.extract_read_like_calls,
                   runner.extract_tool_results, runner.extract_final_text],
    )
    def test_each_transcript_reader_raises_on_a_missing_file_instead_of_returning_empty(
        self, tmp_path: Path, reader,
    ) -> None:
        with pytest.raises(FileNotFoundError):
            reader(tmp_path / "absent.jsonl")


class TestEmptyFindings:
    @pytest.mark.parametrize("blank_text", ["", "  \n"])
    def test_a_run_whose_final_text_is_blank_is_not_ok(self, tmp_path: Path, blank_text: str) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _rewrite_records(
            scenario / "session-1" / "subagents" / "agent-1.jsonl",
            lambda block: {**block, "text": blank_text} if block.get("type") == "text" else block,
        )
        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_EMPTY_FINDINGS
        assert result.findings_text is None
        assert result.prompt_verbatim is True

    def test_a_run_with_no_text_block_at_all_is_not_ok(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _rewrite_records(
            scenario / "session-1" / "subagents" / "agent-1.jsonl",
            lambda block: None if block.get("type") == "text" else block,
        )
        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_EMPTY_FINDINGS


API_ERROR_TEXT = "API Error: the request failed before any review was written"


def _append_subagent_records(scenario: Path, *records: dict) -> None:
    subagent_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
    with open(subagent_jsonl, "a") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _assistant_text_record(text: str, *, model: str = MODEL_ID, **record_fields) -> dict:
    return {"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": text}]}, **record_fields}


class TestApiErrorFinalRecord:
    """A subagent whose last assistant record is one Claude Code synthesized
    for an API failure never counts as a completed review."""

    @pytest.mark.parametrize(
        "error_record",
        [
            _assistant_text_record(API_ERROR_TEXT, model=msmr.SYNTHETIC_MODEL_ID, isApiErrorMessage=True),
            _assistant_text_record(API_ERROR_TEXT, model=msmr.SYNTHETIC_MODEL_ID),
            _assistant_text_record(API_ERROR_TEXT, isApiErrorMessage=True),
        ],
        ids=["synthetic-and-flagged", "synthetic-model-only", "flagged-only"],
    )
    def test_a_final_api_error_record_after_real_turns_is_not_ok(self, tmp_path: Path, error_record: dict) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _append_subagent_records(scenario, error_record)

        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_API_ERROR_FINAL_RECORD
        assert result.findings_text is None
        assert result.prompt_verbatim is True
        assert API_ERROR_TEXT not in (result.failure_reason or "") + (result.failure_detail or "")

    def test_a_synthetic_record_mid_run_does_not_fail_a_real_final_text(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _append_subagent_records(
            scenario,
            _assistant_text_record(API_ERROR_TEXT, model=msmr.SYNTHETIC_MODEL_ID, isApiErrorMessage=True),
            _assistant_text_record("Finding: file:1 is fine after the retried turn."),
        )

        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))

        assert result.ok is True
        assert result.findings_text == "Finding: file:1 is fine after the retried turn."

    def test_a_synthetic_record_mid_run_followed_by_a_textless_real_record_yields_no_error_findings(
        self, tmp_path: Path,
    ) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _rewrite_records(
            scenario / "session-1" / "subagents" / "agent-1.jsonl",
            lambda block: None if block.get("type") == "text" else block,
        )
        _append_subagent_records(
            scenario,
            _assistant_text_record(API_ERROR_TEXT, model=msmr.SYNTHETIC_MODEL_ID, isApiErrorMessage=True),
            {"type": "assistant", "message": {"model": MODEL_ID, "content": [{"type": "thinking", "thinking": "hmm"}]}},
        )

        result = _evaluate(scenario, changed_relpaths=("changed_file.py",))

        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_EMPTY_FINDINGS
        assert result.findings_text is None
        assert API_ERROR_TEXT not in (result.failure_reason or "") + (result.failure_detail or "")

    def test_a_run_ending_on_an_api_error_is_retried_once_then_recorded_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        _append_subagent_records(
            scenario, _assistant_text_record(API_ERROR_TEXT, model=msmr.SYNTHETIC_MODEL_ID, isApiErrorMessage=True),
        )
        launch_count = 0

        def fake_launch(cmd, cwd, timeout_s):
            nonlocal launch_count
            launch_count += 1
            return SUCCESS_STREAM_LINES, False

        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")
        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )

        attempt = runner.run_one_with_retry(ctx, arm="current-rule", run_index=0, launch=fake_launch)

        assert launch_count == 2
        assert attempt.record.status == runner.STATUS_MISSING
        assert attempt.record.missing_reason == runner.VALIDITY_FAIL_API_ERROR_FINAL_RECORD
        assert attempt.record.findings_text is None
        assert attempt.record.attempts == 2


def _result_event_line(**fields) -> bytes:
    return json.dumps({"type": "result", "is_error": False, **fields}).encode()


class TestExtractResultUsage:
    def test_reads_cost_and_token_counts_from_the_result_event(self) -> None:
        line = _result_event_line(
            total_cost_usd=0.0464,
            usage={"input_tokens": 4, "output_tokens": 125, "cache_read_input_tokens": 38442,
                   "cache_creation_input_tokens": 8791},
        )

        usage = runner.extract_result_usage([b'{"type": "assistant"}', line])

        assert usage == runner.ResultUsage(
            total_cost_usd=0.0464, input_tokens=4, output_tokens=125, cache_read_input_tokens=38442,
            cache_creation_input_tokens=8791,
        )

    def test_the_last_result_event_wins(self) -> None:
        lines = [_result_event_line(total_cost_usd=1.0), _result_event_line(total_cost_usd=2.0)]
        assert runner.extract_result_usage(lines).total_cost_usd == 2.0

    def test_no_result_event_leaves_every_field_none(self) -> None:
        assert runner.extract_result_usage([b'{"type": "assistant"}', b"not json"]) == runner.ResultUsage.unavailable()

    def test_a_result_event_without_usage_fields_leaves_them_none(self) -> None:
        usage = runner.extract_result_usage([_result_event_line()])
        assert usage == runner.ResultUsage.unavailable()

    def test_non_numeric_and_boolean_values_are_ignored(self) -> None:
        line = _result_event_line(total_cost_usd="0.5", usage={"input_tokens": True, "output_tokens": 1.5})
        assert runner.extract_result_usage([line]) == runner.ResultUsage.unavailable()

    def test_an_integer_cost_is_returned_as_a_float(self) -> None:
        usage = runner.extract_result_usage([_result_event_line(total_cost_usd=1)])
        assert usage.total_cost_usd == 1.0
        assert isinstance(usage.total_cost_usd, float)


class TestSessionStoreLookupBySessionId:
    def test_finds_store_whatever_its_own_directory_name(self, tmp_path: Path) -> None:
        projects_root = tmp_path / "projects"
        odd_named_dir = projects_root / "some-opaque-hash-name"
        odd_named_dir.mkdir(parents=True)
        (odd_named_dir / "session-abc.jsonl").write_text("{}\n")
        found = runner.find_session_jsonl_by_id(projects_root, "session-abc")
        assert found == odd_named_dir / "session-abc.jsonl"

    def test_missing_session_jsonl_returns_none(self, tmp_path: Path) -> None:
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        assert runner.find_session_jsonl_by_id(projects_root, "does-not-exist") is None


class TestReadStatsCounting:
    def test_partial_view_and_paged_followup_and_whole_file_counts(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "partial-view-paged-followup")
        result = _evaluate(scenario, changed_relpaths=("changed_file.py", "other_changed_file.py"))
        assert result.ok is True
        assert result.stats.read_calls == 3
        assert result.stats.partial_view_reads == 1
        assert result.stats.paged_followups == 1
        # - changed_file.py's read was partial: excluded.
        # - Its paged follow-up carries an offset: excluded.
        # - other_changed_file.py's unpaged, non-partial read: the one
        #   whole-file read counted.
        assert result.stats.whole_file_reads_of_changed_files == 1

    def test_whole_file_read_counts_when_the_recorded_path_is_absolute(self, tmp_path: Path) -> None:
        """A real Read tool_use always records an absolute file_path, while
        changed_relpaths are bare repo-relative strings from `git diff
        --name-only`. Comparing the two as raw, unresolved strings always
        misses. Both sides must resolve against fixture_dir the same way a
        real run's ctx.fixture_dir and ctx.changed_relpaths combination
        would."""
        fixture_dir = tmp_path / "fixture"
        fixture_dir.mkdir()
        absolute_path = str(fixture_dir / "changed_file.py")
        calls = [runner.ReadLikeCall(tool_use_id="t1", tool_name="Read", path=absolute_path, offset=None)]
        stats = runner.compute_read_stats(
            calls, {"t1": "def f():\n    return 1\n"}, fixture_dir=fixture_dir,
            changed_relpaths=frozenset({"changed_file.py"}),
        )
        assert stats.whole_file_reads_of_changed_files == 1


def _patch_inner_prompt_to_match_build_review_prompt(scenario_dir: Path) -> None:
    """execute_run() computes its own expected inner prompt via
    runner.build_review_prompt(), never the raw INNER_PROMPT
    placeholder the static fixtures embed -- tests that exercise
    execute_run/run_one_with_retry (rather than calling
    evaluate_run_validity directly with an explicit expected_inner_prompt)
    must patch the fixture's embedded prompt to match what that call will
    actually expect."""
    session_jsonl = scenario_dir / "session-1.jsonl"
    real_prompt = runner.build_review_prompt()
    session_jsonl.write_text(session_jsonl.read_text().replace(INNER_PROMPT, real_prompt))


class TestRetryThenMissing:
    def test_failing_run_is_retried_once_then_recorded_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = _load_scenario(tmp_path, "model-mismatch")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        calls = {"n": 0}

        def fake_launch(cmd, cwd, timeout_s):
            calls["n"] += 1
            return SUCCESS_STREAM_LINES, False

        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )
        attempt = runner.run_one_with_retry(ctx, arm="current-rule", run_index=0, launch=fake_launch)

        assert calls["n"] == 2  # exactly one retry
        assert attempt.record.status == runner.STATUS_MISSING
        assert attempt.record.missing_reason == runner.VALIDITY_FAIL_MODEL_MISMATCH
        assert attempt.record.attempts == 2

    def test_a_retried_run_sums_cost_and_tokens_over_both_attempts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = _load_scenario(tmp_path, "model-mismatch")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        stream_by_attempt = [
            [
                _result_event_line(
                    subtype=runner.RESULT_SUBTYPE_SUCCESS, total_cost_usd=0.25,
                    usage={"input_tokens": 10, "output_tokens": 5},
                ),
            ],
            [
                _result_event_line(
                    subtype=runner.RESULT_SUBTYPE_SUCCESS, total_cost_usd=0.40,
                    usage={"input_tokens": 30, "output_tokens": 7},
                ),
            ],
        ]
        launch_calls = iter(stream_by_attempt)
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")
        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )

        attempt = runner.run_one_with_retry(
            ctx, arm="current-rule", run_index=0, launch=lambda cmd, cwd, timeout_s: (next(launch_calls), False),
        )

        assert attempt.record.attempts == 2
        assert attempt.record.missing_reason == runner.VALIDITY_FAIL_MODEL_MISMATCH
        assert attempt.record.total_cost_usd == pytest.approx(0.25 + 0.40)
        assert attempt.record.input_tokens == 10 + 30
        assert attempt.record.output_tokens == 5 + 7
        assert attempt.record.cache_read_input_tokens is None

    def test_a_missing_run_records_why_it_is_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _load_scenario(tmp_path, "result-error")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")
        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )

        record = runner.execute_run(
            ctx, arm="current-rule", run_index=0, session_id="s1",
            launch=lambda cmd, cwd, timeout_s: (_stream_lines(scenario), False),
        )

        assert record.missing_reason == runner.VALIDITY_FAIL_RESULT_ERROR
        assert record.missing_detail == "subtype 'error_during_execution', terminal_reason None"
        assert record.total_cost_usd is None

    def test_succeeding_on_first_try_never_retries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        calls = {"n": 0}

        def fake_launch(cmd, cwd, timeout_s):
            calls["n"] += 1
            return SUCCESS_STREAM_LINES, False

        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=("changed_file.py",), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )
        attempt = runner.run_one_with_retry(ctx, arm="current-rule", run_index=0, launch=fake_launch)

        assert calls["n"] == 1
        assert attempt.record.status == runner.STATUS_OK
        assert attempt.record.attempts == 1
        assert attempt.record.missing_detail is None


class TestReadEnvironmentRecord:
    """Every probe's failure must raise: an empty default would make the
    start-of-block and end-of-block readings match trivially."""

    VERSION_COMMAND = ("claude", "--version")
    COMMIT_COMMAND = ("git", "rev-parse", "HEAD")

    @staticmethod
    def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

    def _stub_probes(self, monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
        probes: dict[tuple[str, ...], object] = {
            self.VERSION_COMMAND: self._completed("2.1.0\n"),
            self.COMMIT_COMMAND: self._completed("abc123\n"),
        }
        probes.update({tuple(key.split("|")): value for key, value in overrides.items()})

        def fake_run(command, **_kwargs):
            outcome = probes[tuple(command)]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(runner.subprocess, "run", fake_run)

    def test_reads_the_cli_version_and_the_checkout_commit_and_probes_nothing_else(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._stub_probes(monkeypatch)
        record = runner.read_environment_record(checkout_root=tmp_path)
        assert record == runner.EnvironmentRecord("2.1.0", "abc123")

    @pytest.mark.parametrize("failing_probe", ["claude|--version", "git|rev-parse|HEAD"])
    def test_a_probe_exiting_nonzero_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_probe: str,
    ) -> None:
        failed_probe = self._completed("", returncode=128, stderr="fatal: not a git repository")
        self._stub_probes(monkeypatch, **{failing_probe: failed_probe})
        with pytest.raises(runner.HarnessInvalidatedError, match="exited 128"):
            runner.read_environment_record(checkout_root=tmp_path)

    @pytest.mark.parametrize("empty_probe", ["claude|--version", "git|rev-parse|HEAD"])
    def test_an_empty_version_or_commit_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, empty_probe: str,
    ) -> None:
        self._stub_probes(monkeypatch, **{empty_probe: self._completed("\n")})
        with pytest.raises(runner.HarnessInvalidatedError, match="empty reading"):
            runner.read_environment_record(checkout_root=tmp_path)

    def test_a_probe_timing_out_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_probes(monkeypatch, **{"git|rev-parse|HEAD": subprocess.TimeoutExpired(cmd="git", timeout=10)})
        with pytest.raises(runner.HarnessInvalidatedError, match="ambient_config_commit"):
            runner.read_environment_record(checkout_root=tmp_path)

    def test_a_missing_executable_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_probes(monkeypatch, **{"claude|--version": FileNotFoundError("claude")})
        with pytest.raises(runner.HarnessInvalidatedError, match="cli_version"):
            runner.read_environment_record(checkout_root=tmp_path)


FROZEN_ENVIRONMENT = {"cli_version": "v1", "ambient_config_commit": "sha1"}


class TestEnvironmentReference:
    def test_a_frozen_reference_accepts_only_its_own_cli_version_and_config_commit(self) -> None:
        reference = runner.EnvironmentReference(FROZEN_ENVIRONMENT)
        reference.require_match(runner.EnvironmentRecord("v1", "sha1"), where="d1's block start")
        for mismatched in (runner.EnvironmentRecord("v2", "sha1"), runner.EnvironmentRecord("v1", "sha2")):
            with pytest.raises(runner.EnvironmentMismatchError):
                reference.require_match(mismatched, where="d1's block start")

    def test_an_empty_reference_adopts_the_first_reading_and_holds_later_ones_to_it(self) -> None:
        reference = runner.EnvironmentReference()
        reference.require_match(runner.EnvironmentRecord("v1", "sha1"), where="d1's block start")
        reference.require_match(runner.EnvironmentRecord("v1", "sha1"), where="d1's block end")
        with pytest.raises(runner.EnvironmentMismatchError, match="first reading"):
            reference.require_match(runner.EnvironmentRecord("v1", "sha2"), where="d2's block start")

    def test_the_message_names_where_both_readings_and_the_recovery(self) -> None:
        reference = runner.EnvironmentReference(FROZEN_ENVIRONMENT)
        with pytest.raises(runner.EnvironmentMismatchError) as excinfo:
            reference.require_match(runner.EnvironmentRecord("v2", "sha2"), where="d1's block end")
        message = str(excinfo.value)
        assert "d1's block end" in message
        assert "'v1'->'v2'" in message
        assert "'sha1'->'sha2'" in message
        assert "same --campaign-id" in message
        assert "re-freeze" in message

    def test_a_reference_with_no_freeze_does_not_advise_a_re_freeze(self) -> None:
        reference = runner.EnvironmentReference()
        reference.require_match(runner.EnvironmentRecord("v1", "sha1"), where="d1's block start")
        with pytest.raises(runner.EnvironmentMismatchError) as excinfo:
            reference.require_match(runner.EnvironmentRecord("v1", "sha2"), where="d1's block end")
        assert "re-freeze" not in str(excinfo.value)


class TestEnvironmentMismatchHaltsBlock:
    """A block's start and end readings must both equal the reference
    environment. A mismatch halts with nothing rerun and no records returned."""

    def _spec_and_stubs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> runner.DefectFixtureSpec:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        monkeypatch.setattr(
            runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl",
        )
        return runner.DefectFixtureSpec(
            defect_id="d1", arm_fixture_dirs={"current-rule": scenario},
            arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
            live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
        )

    @staticmethod
    def _stub_readings(monkeypatch: pytest.MonkeyPatch, *readings: runner.EnvironmentRecord) -> None:
        remaining = iter(readings)
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: next(remaining))

    def test_matching_start_and_end_readings_return_the_block_stamped_with_the_start_reading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = self._spec_and_stubs(tmp_path, monkeypatch)
        self._stub_readings(
            monkeypatch, runner.EnvironmentRecord("v1", "sha1"), runner.EnvironmentRecord("v1", "sha1"),
        )

        result = runner.run_defect_block(
            spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1",
            launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False), workers=1,
            environment_reference=runner.EnvironmentReference(FROZEN_ENVIRONMENT),
        )

        assert len(result.records) == 1
        assert result.records[0].ambient_config_commit == "sha1"

    def test_a_start_reading_that_differs_from_the_frozen_environment_halts_before_any_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = self._spec_and_stubs(tmp_path, monkeypatch)
        self._stub_readings(monkeypatch, runner.EnvironmentRecord("v1", "sha2"))

        def fail_if_launched(cmd, cwd, timeout_s):
            raise AssertionError("no run may launch after a start-reading mismatch")

        with pytest.raises(runner.EnvironmentMismatchError, match="block start"):
            runner.run_defect_block(
                spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1", launch=fail_if_launched, workers=1,
                environment_reference=runner.EnvironmentReference(FROZEN_ENVIRONMENT),
            )

    @pytest.mark.parametrize(
        ("end_reading", "changed_field"),
        [
            (runner.EnvironmentRecord("v1", "sha2"), "ambient_config_commit"),
            (runner.EnvironmentRecord("v2", "sha1"), "cli_version"),
        ],
    )
    def test_an_end_reading_that_differs_halts_after_one_pass_and_returns_no_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, end_reading: runner.EnvironmentRecord,
        changed_field: str,
    ) -> None:
        spec = self._spec_and_stubs(tmp_path, monkeypatch)
        self._stub_readings(monkeypatch, runner.EnvironmentRecord("v1", "sha1"), end_reading)
        launches: list[int] = []

        def counting_launch(cmd, cwd, timeout_s):
            launches.append(1)
            return SUCCESS_STREAM_LINES, False

        with pytest.raises(runner.EnvironmentMismatchError, match="block end") as excinfo:
            runner.run_defect_block(
                spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1", launch=counting_launch, workers=1,
                environment_reference=runner.EnvironmentReference(FROZEN_ENVIRONMENT),
            )

        assert len(launches) == 1  # the block ran once and nothing reran
        assert changed_field in str(excinfo.value)
        assert "records the block wrote" not in str(excinfo.value)  # a reviewer block writes none before its end reading

    def test_with_no_frozen_environment_a_later_block_is_held_to_the_first_blocks_reading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = self._spec_and_stubs(tmp_path, monkeypatch)
        self._stub_readings(
            monkeypatch,
            runner.EnvironmentRecord("v1", "sha1"), runner.EnvironmentRecord("v1", "sha1"),  # block 1
            runner.EnvironmentRecord("v1", "sha2"),  # block 2 start
        )
        shared_reference = runner.EnvironmentReference()

        def launch(cmd, cwd, timeout_s):
            return SUCCESS_STREAM_LINES, False

        runner.run_defect_block(
            spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1", launch=launch, workers=1,
            environment_reference=shared_reference,
        )
        with pytest.raises(runner.EnvironmentMismatchError, match="first reading"):
            runner.run_defect_block(
                spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1", launch=launch, workers=1,
                environment_reference=shared_reference,
            )

    @pytest.mark.parametrize(
        ("environment_reference", "reference_origin"),
        [
            (runner.EnvironmentReference(FROZEN_ENVIRONMENT), "the frozen environment"),
            (None, "the campaign's first reading"),
        ],
        ids=["frozen-reference", "no-frozen-reference"],
    )
    def test_a_campaign_halts_before_dispatching_a_block_whose_start_reading_differs_from_the_reference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        environment_reference: runner.EnvironmentReference | None, reference_origin: str,
    ) -> None:
        """Drives the real run_defect_block through run_campaign, so a campaign that stopped
        forwarding its reference (each block then adopting its own first reading) fails here."""
        spec = self._spec_and_stubs(tmp_path, monkeypatch)
        self._stub_readings(
            monkeypatch,
            runner.EnvironmentRecord("v1", "sha1"), runner.EnvironmentRecord("v1", "sha1"),  # block 1 start, end
            runner.EnvironmentRecord("v1", "sha2"),  # block 2 start
        )
        run_store = runner.RunStore(tmp_path / "run-store")
        launches: list[int] = []

        def counting_launch(cmd, cwd, timeout_s):
            launches.append(1)
            return SUCCESS_STREAM_LINES, False

        with pytest.raises(runner.EnvironmentMismatchError, match="d2's block start") as excinfo:
            runner.run_campaign(
                ["d1", "d2"], build_spec=lambda defect_id: dataclasses.replace(spec, defect_id=defect_id),
                arms=("current-rule",), k=1, seed=1, campaign_id="c1", run_store=run_store,
                records_path=tmp_path / "records.jsonl", projects_root=tmp_path / "projects",
                launch=counting_launch, workers=1, environment_reference=environment_reference,
            )

        assert reference_origin in str(excinfo.value)
        assert len(launches) == 1  # block 1's one run; block 2 never dispatched
        assert run_store.completed_block_ids() == {"d1"}

    def test_a_halted_campaign_block_writes_no_records_and_is_not_marked_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = self._spec_and_stubs(tmp_path, monkeypatch)
        self._stub_readings(
            monkeypatch, runner.EnvironmentRecord("v1", "sha1"), runner.EnvironmentRecord("v1", "sha2"),
        )
        run_store = runner.RunStore(tmp_path / "run-store")
        records_path = tmp_path / "records.jsonl"
        built: list[str] = []

        def build_spec(defect_id: str) -> runner.DefectFixtureSpec:
            built.append(defect_id)
            return spec

        with pytest.raises(runner.EnvironmentMismatchError):
            runner.run_campaign(
                ["d1", "d2"], build_spec=build_spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1",
                run_store=run_store, records_path=records_path, projects_root=tmp_path / "projects",
                launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False), workers=1,
                environment_reference=runner.EnvironmentReference(FROZEN_ENVIRONMENT),
            )

        assert built == ["d1"]  # the campaign halts: d2's block never starts
        assert not records_path.exists()
        assert run_store.completed_block_ids() == set()
        run_store.acquire_lock()  # the halt released the lock
        run_store.release_lock()


class TestBlockInterrupt:
    def test_an_interrupt_tells_in_flight_launches_to_stop_before_the_pool_joins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Worker threads never receive KeyboardInterrupt, so the block must
        signal them itself or the pool's exit waits out every child's timeout."""
        spec = runner.DefectFixtureSpec(
            defect_id="d1", arm_fixture_dirs={"current-rule": tmp_path},
            arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
            live_checkout_roots=(), changed_relpaths=(), over_read_cap=False,
        )
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1"))
        monkeypatch.setattr(runner, "SESSION_FLUSH_TIMEOUT_S", 0.0)
        monkeypatch.setattr(runner.msmr, "_launches_aborted", threading.Event())
        launch_in_flight = threading.Event()
        abort_seen_while_in_flight = threading.Event()
        # Bounded so a regression fails the assertion below rather than hanging
        # the pool's join; far longer than a scheduler needs to run the
        # main thread's except clause.
        wait_bound_s = 10.0

        class InterruptedAfterLaunchStartsPool(concurrent.futures.ThreadPoolExecutor):
            """Raises the interrupt on the main thread once every item is
            submitted and a launch is in flight -- the state a Ctrl-C finds."""

            def map(self, fn, *iterables, **kwargs):
                submitted = super().map(fn, *iterables, **kwargs)
                assert launch_in_flight.wait(timeout=wait_bound_s)
                submitted.close()
                raise KeyboardInterrupt

        monkeypatch.setattr(runner, "ThreadPoolExecutor", InterruptedAfterLaunchStartsPool)

        def launch(cmd, cwd, timeout_s):
            # Waits on the flag the real launcher polls while its child runs.
            launch_in_flight.set()
            if runner.msmr._launches_aborted.wait(timeout=wait_bound_s):
                abort_seen_while_in_flight.set()
                raise runner.msmr.LaunchAbortedError("aborted")
            return SUCCESS_STREAM_LINES, False

        with pytest.raises(KeyboardInterrupt):
            runner.run_defect_block(
                spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1", launch=launch, workers=1,
            )

        assert abort_seen_while_in_flight.is_set()


class TestBlockCleanupOrdering:
    def test_cleanup_runs_only_after_every_run_in_the_block(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        fixture_dir = tmp_path / "shared-fixture"
        shutil.copytree(scenario, fixture_dir)

        launched: list[int] = []

        def fake_launch(cmd, cwd, timeout_s):
            launched.append(1)
            assert fixture_dir.exists(), "cleanup must not run before every run in the block has finished"
            return SUCCESS_STREAM_LINES, False

        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1"))
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: fixture_dir / "session-1.jsonl")

        spec = runner.DefectFixtureSpec(
            defect_id="d1", arm_fixture_dirs={"current-rule": fixture_dir, "function-context": fixture_dir},
            arm_agent_names={"current-rule": AGENT_NAME, "function-context": AGENT_NAME},
            agent_declared_tools=DECLARED_TOOLS, live_checkout_roots=(), changed_relpaths=("changed_file.py",),
            over_read_cap=False,
        )
        result = runner.run_defect_block(
            spec, arms=("current-rule", "function-context"), k=2, seed=1, campaign_id="c1",
            launch=fake_launch, workers=2,
        )
        assert len(launched) == 4  # 2 arms x k=2, before any cleanup call below
        assert fixture_dir.exists()  # cleanup_defect_block is a separate, later call
        runner.cleanup_defect_block(spec, result, projects_root=tmp_path / "projects")
        assert not fixture_dir.exists()


class TestWriteAheadRecordedBeforeLaunch:
    def test_run_one_with_retry_records_session_id_before_launching(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression guard: each run's session ID must be write-ahead
        recorded before that run launches (evals/README.md's "Interruption
        and cleanup" section) -- asserts the ordering directly, rather than
        only inferring it from a passing status."""
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario)
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")

        events: list[str] = []

        class RecordingRunStore:
            def record_directory(self, defect_id: str, directory: Path, session_id: str) -> None:
                events.append("record_directory")

        def fake_launch(cmd, cwd, timeout_s):
            events.append("launch")
            return SUCCESS_STREAM_LINES, False

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=("changed_file.py",), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1"),
        )
        runner.run_one_with_retry(ctx, arm="current-rule", run_index=0, launch=fake_launch, run_store=RecordingRunStore())

        assert events == ["record_directory", "launch"]


class TestRunCampaignResume:
    def test_sweeps_abandoned_entry_skips_completed_defect_reruns_pending_one_whole(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1"))
        build_spec_calls: list[str] = []

        def build_spec(defect_id: str) -> runner.DefectFixtureSpec:
            build_spec_calls.append(defect_id)
            scenario = _load_scenario(tmp_path, "normal-success")
            _patch_inner_prompt_to_match_build_review_prompt(scenario)
            monkeypatch.setattr(
                runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl",
            )
            return runner.DefectFixtureSpec(
                defect_id=defect_id, arm_fixture_dirs={"current-rule": scenario},
                arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
                live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
            )

        run_store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path)
        run_store.mark_block_complete("defect-done")
        # Simulates a hard interruption on a prior invocation, mid-run of
        # defect-pending -- its write-ahead entry names a directory that
        # must be swept before defect-pending's block reruns.
        abandoned_dir = tmp_path / "review-bench-abandoned"
        abandoned_dir.mkdir()
        run_store.record_directory("defect-pending", abandoned_dir, str(uuid.uuid4()))

        result = runner.run_campaign(
            ["defect-done", "defect-pending"], build_spec=build_spec, arms=("current-rule",), k=1, seed=1,
            campaign_id="c1", run_store=run_store, records_path=tmp_path / "records.jsonl",
            projects_root=tmp_path / "projects", launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False), workers=1,
        )

        assert not abandoned_dir.exists()  # swept before defect-pending's block reran
        assert build_spec_calls == ["defect-pending"]  # defect-done's block never reruns
        assert set(result.block_results) == {"defect-pending"}
        assert run_store.completed_block_ids() == {"defect-done", "defect-pending"}


class TestRunCampaignRecordDurabilityOrdering:
    def test_records_are_durable_before_the_block_is_marked_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression guard for a data-loss ordering bug: a block's
        RunRecords must be durably appended to records_path before (not
        after) run_store.mark_block_complete lands, since a crash between
        the two must leave the block rerunnable rather than permanently
        marked complete with its records never written. Asserts the records
        are already on disk when mark_block_complete runs, not just that the
        two calls happen in some order."""
        fake_record = runner.RunRecord(
            campaign_id="c1", defect_id="defect-1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read",), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=1.0, read_calls=1, read_tokens_est=10, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=0, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        monkeypatch.setattr(
            runner, "run_defect_block",
            lambda *a, **kw: runner.BlockResult(records=(fake_record,), representative_session_id_by_arm={}),
        )

        def build_spec(defect_id: str) -> runner.DefectFixtureSpec:
            return runner.DefectFixtureSpec(
                defect_id=defect_id, arm_fixture_dirs={},
                arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
                live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
            )

        run_store = runner.RunStore(tmp_path / "run-store")
        records_path = tmp_path / "records.jsonl"
        real_mark_block_complete = run_store.mark_block_complete
        events: list[str] = []

        def recording_mark_block_complete(defect_id: str) -> None:
            # If append_run_records ran first, this defect's records are
            # already durable by the time mark_block_complete is called --
            # the property that makes a crash in this gap safe to resume
            # rather than a silent, permanent loss of already-run results.
            assert any(record.defect_id == defect_id for record in runner.read_run_records(records_path))
            events.append("mark_block_complete")
            real_mark_block_complete(defect_id)

        monkeypatch.setattr(run_store, "mark_block_complete", recording_mark_block_complete)

        runner.run_campaign(
            ["defect-1"], build_spec=build_spec, arms=("current-rule",), k=1, seed=1,
            campaign_id="c1", run_store=run_store, records_path=records_path,
            projects_root=tmp_path / "projects", launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False), workers=1,
        )

        assert events == ["mark_block_complete"]  # ran, and only after the assertion above held
        assert run_store.completed_block_ids() == {"defect-1"}
        assert runner.read_run_records(records_path)

    def test_cleanup_runs_before_the_block_is_marked_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression guard: cleanup_defect_block must delete a completed
        block's fixture/session-store directories before (not after)
        run_store.mark_block_complete lands, since run_campaign's resume path
        (`if defect_id in completed: continue`) never revisits a block once
        it's marked complete -- a crash after mark_block_complete but before
        cleanup would leak that block's fixture/session-store directories
        permanently. arm_fixture_dirs points at a real, existing directory so
        cleanup_defect_block runs for real rather than as a no-op."""
        fixture_dir = tmp_path / "fixture-current-rule"
        fixture_dir.mkdir()

        monkeypatch.setattr(
            runner, "run_defect_block",
            lambda *a, **kw: runner.BlockResult(records=(), representative_session_id_by_arm={}),
        )

        def build_spec(defect_id: str) -> runner.DefectFixtureSpec:
            return runner.DefectFixtureSpec(
                defect_id=defect_id, arm_fixture_dirs={"current-rule": fixture_dir},
                arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
                live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
            )

        run_store = runner.RunStore(tmp_path / "run-store")
        real_mark_block_complete = run_store.mark_block_complete

        def recording_mark_block_complete(defect_id: str) -> None:
            # If cleanup_defect_block ran first, its fixture directory is
            # already gone by the time mark_block_complete is called -- the
            # property that keeps a crash in this gap from leaking it
            # permanently, since a completed block is never revisited.
            assert not fixture_dir.exists()
            real_mark_block_complete(defect_id)

        monkeypatch.setattr(run_store, "mark_block_complete", recording_mark_block_complete)

        runner.run_campaign(
            ["defect-1"], build_spec=build_spec, arms=("current-rule",), k=1, seed=1,
            campaign_id="c1", run_store=run_store, records_path=tmp_path / "records.jsonl",
            projects_root=tmp_path / "projects", launch=lambda cmd, cwd, timeout_s: (SUCCESS_STREAM_LINES, False), workers=1,
        )

        assert run_store.completed_block_ids() == {"defect-1"}
        assert not fixture_dir.exists()


def _build_two_commit_source_repo(repo_dir: Path, *, changed_file_content: str = "x = 2\n") -> ConfirmedDefect:
    """A throwaway real git repo with exactly base_commit then head_commit,
    for build_defect_fixture_spec (never a real evals/review_bench source)."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "changed_file.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo_dir, check=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True,
    ).stdout.strip()
    (repo_dir / "changed_file.py").write_text(changed_file_content)
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fix: bug"], cwd=repo_dir, check=True)
    head_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return ConfirmedDefect(
        id="defect-1", source="szz", lens="staff-backend-engineer", base_commit=base_commit,
        head_commit=head_commit, fix_commit=head_commit, fix_date="2024-01-01", description="test defect",
        path="app.py", file_is_markdown=False,
    )


class TestBuildDefectFixtureSpecRecordsDirectoriesImmediately:
    def test_records_each_arms_fixture_directory_before_returning(self, tmp_path: Path) -> None:
        """Regression guard: each directory a run store creates must be
        recorded as soon as it exists (evals/README.md's "Interruption and
        cleanup" section) -- build_defect_fixture_spec must record it before
        it returns, not only as a side effect of some later run against it
        (which never happens if a hard interruption strikes first)."""
        defect = _build_two_commit_source_repo(tmp_path / "source")
        arms_snapshot_root = tmp_path / "arms"
        agent_dir = arms_snapshot_root / "current-rule"
        agent_dir.mkdir(parents=True)
        (agent_dir / "bench-staff-backend-engineer.md").write_text("agent body\n")

        run_store = runner.RunStore(tmp_path / "run-store")
        spec = runner.build_defect_fixture_spec(
            defect, arm_names=("current-rule",), source_repo=tmp_path / "source", live_checkout_roots=(),
            arms_snapshot_root=arms_snapshot_root, run_store=run_store,
        )

        pending = run_store.pending_entries()
        assert len(pending) == 1
        assert pending[0].session_id == runner._NO_SESSION_ID_YET
        assert Path(pending[0].directory) == spec.arm_fixture_dirs["current-rule"]

    def test_the_spec_carries_the_fix_commits_files_beside_the_introducing_commits(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        defect = _build_two_commit_source_repo(source_repo)
        (source_repo / "fix_only.py").write_text("y = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=source_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fix: later"], cwd=source_repo, check=True)
        fix_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source_repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        arms_snapshot_root = tmp_path / "arms"
        (arms_snapshot_root / "current-rule").mkdir(parents=True)
        (arms_snapshot_root / "current-rule" / "bench-staff-backend-engineer.md").write_text("agent body\n")

        spec = runner.build_defect_fixture_spec(
            dataclasses.replace(defect, fix_commit=fix_sha), arm_names=("current-rule",), source_repo=source_repo,
            live_checkout_roots=(), arms_snapshot_root=arms_snapshot_root,
        )

        assert spec.changed_relpaths == ("changed_file.py",)
        assert spec.fix_commit_relpaths == ("fix_only.py",)
        shutil.rmtree(spec.arm_fixture_dirs["current-rule"], ignore_errors=True)

    def test_a_fixture_build_that_fails_still_leaves_its_directory_recorded(self, tmp_path: Path) -> None:
        """The directory is recorded before it is populated, so a build that
        dies partway (an unreachable commit here) leaves it for the sweep."""
        defect = _build_two_commit_source_repo(tmp_path / "source")
        unreachable_head = dataclasses.replace(defect, head_commit="0" * 40)
        run_store = runner.RunStore(tmp_path / "run-store")

        with pytest.raises(subprocess.CalledProcessError):
            runner.build_defect_fixture_spec(
                unreachable_head, arm_names=("current-rule",), source_repo=tmp_path / "source",
                live_checkout_roots=(), arms_snapshot_root=tmp_path / "arms", run_store=run_store,
            )

        pending = run_store.pending_entries()
        assert len(pending) == 1
        assert pending[0].session_id == runner._NO_SESSION_ID_YET
        shutil.rmtree(pending[0].directory, ignore_errors=True)

    def test_over_cap_changed_file_sets_over_read_cap_true(self, tmp_path: Path) -> None:
        """A changed file crossing fixture_repo._OVER_READ_CAP_TOKENS must
        flip DefectFixtureSpec.over_read_cap to True through the real
        any(stat.over_read_cap ...) wiring, not just via a directly
        constructed RunRecord -- mirrors fixture_repo.py's own
        test_over_read_cap_flag_follows_the_chars_divided_by_four_threshold
        threshold-boundary test, one layer up."""
        # 100_004 chars // 4 == 25_001, one token over fixture_repo._OVER_READ_CAP_TOKENS (25_000).
        defect = _build_two_commit_source_repo(tmp_path / "source", changed_file_content="a" * 100_004)
        arms_snapshot_root = tmp_path / "arms"
        agent_dir = arms_snapshot_root / "current-rule"
        agent_dir.mkdir(parents=True)
        (agent_dir / "bench-staff-backend-engineer.md").write_text("agent body\n")

        spec = runner.build_defect_fixture_spec(
            defect, arm_names=("current-rule",), source_repo=tmp_path / "source", live_checkout_roots=(),
            arms_snapshot_root=arms_snapshot_root,
        )

        assert spec.over_read_cap is True


class TestCleanupDefectBlockRemovesSessionStores:
    def test_removes_each_arms_session_store_found_by_its_representative_session_id(self, tmp_path: Path) -> None:
        projects_root = tmp_path / "projects"
        fixture_dir = tmp_path / "review-bench-fixture"
        fixture_dir.mkdir()
        session_store = projects_root / "some-hash"
        session_store.mkdir(parents=True)
        (session_store / "session-a.jsonl").write_text("{}\n")
        untouched_store = projects_root / "other-hash"
        untouched_store.mkdir()
        (untouched_store / "session-b.jsonl").write_text("{}\n")
        spec = runner.DefectFixtureSpec(
            defect_id="d1", arm_fixture_dirs={"current-rule": fixture_dir},
            arm_agent_names={"current-rule": "bench-staff-backend-engineer"}, agent_declared_tools=DECLARED_TOOLS,
            live_checkout_roots=(), changed_relpaths=(), over_read_cap=False,
        )
        block_result = runner.BlockResult(records=(), representative_session_id_by_arm={"current-rule": "session-a"})

        runner.cleanup_defect_block(spec, block_result, projects_root=projects_root)

        assert not session_store.exists()
        assert not fixture_dir.exists()
        assert untouched_store.exists()


def _outcome_record(
    defect_id: str, *, run_index: int, arm: str = "current-rule", status: str = runner.STATUS_OK,
    missing_reason: str | None = None, missing_detail: str | None = None, attempts: int = 1,
) -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=run_index, opaque_run_id=f"{defect_id}-{run_index}",
        status=status, missing_reason=missing_reason, observed_model=MODEL_ID, observed_tools=("Read",),
        out_of_session_paths=(), findings_text="No findings." if status == runner.STATUS_OK else None,
        wall_clock_s=1.0, read_calls=1, read_tokens_est=10, partial_view_reads=0, paged_followups=0,
        whole_file_reads_of_changed_files=0, over_read_cap=False, dispatch_prompt_verbatim=True,
        cli_version="2.1.0", ambient_config_commit="deadbeef", attempts=attempts, missing_detail=missing_detail,
    )


def _missing_outcome_record(defect_id: str, run_index: int, *, detail: str | None = None) -> runner.RunRecord:
    return _outcome_record(
        defect_id, run_index=run_index, status=runner.STATUS_MISSING,
        missing_reason=runner.VALIDITY_FAIL_RESULT_ERROR, missing_detail=detail, attempts=runner.ATTEMPTS_PER_RUN,
    )


class TestCountOutcomes:
    def test_counts_ok_missing_by_reason_and_retried_runs(self) -> None:
        records = [
            _outcome_record("d1", run_index=0),
            _outcome_record("d1", run_index=1, attempts=2),
            _missing_outcome_record("d1", 2),
            _outcome_record(
                "d1", run_index=3, status=runner.STATUS_MISSING, missing_reason=runner.MISSING_REASON_TIMEOUT,
                attempts=2,
            ),
        ]

        counts = runner.count_outcomes(records)

        assert counts.ok == 2
        assert counts.missing_by_reason == {runner.VALIDITY_FAIL_RESULT_ERROR: 1, runner.MISSING_REASON_TIMEOUT: 1}
        assert counts.missing == 2
        assert counts.retried == 3

    def test_formats_the_reason_counts_beside_the_ok_count(self) -> None:
        records = [_outcome_record("d1", run_index=0), _missing_outcome_record("d1", 1), _missing_outcome_record("d1", 2)]

        text = runner.format_outcome_counts(runner.count_outcomes(records))

        assert text == "1 ok, 2 missing (result-error x2), 2 retried"


class TestAllRunsMissing:
    def test_an_empty_record_list_is_not_all_missing(self) -> None:
        assert runner.all_runs_missing([]) is False

    def test_one_ok_run_among_missing_runs_is_not_all_missing(self) -> None:
        assert runner.all_runs_missing([_outcome_record("d1", run_index=0), _missing_outcome_record("d1", 1)]) is False

    def test_only_missing_runs_is_all_missing(self) -> None:
        assert runner.all_runs_missing([_missing_outcome_record("d1", 0), _missing_outcome_record("d1", 1)]) is True

    def test_a_missing_run_with_no_reason_still_counts_as_missing(self) -> None:
        reasonless_missing = _outcome_record("d1", run_index=0, status=runner.STATUS_MISSING, missing_reason=None)

        assert runner.all_runs_missing([reasonless_missing]) is True


class TestRunCampaignSystemicFailure:
    def _campaign(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocks: dict[str, tuple[runner.RunRecord, ...]],
        *, fault: str | None = None,
    ):
        monkeypatch.setattr(
            runner, "run_defect_block",
            lambda spec, **kw: runner.BlockResult(records=blocks[spec.defect_id], representative_session_id_by_arm={}),
        )
        built: list[str] = []

        def build_spec(defect_id: str) -> runner.DefectFixtureSpec:
            built.append(defect_id)
            return runner.DefectFixtureSpec(
                defect_id=defect_id, arm_fixture_dirs={},
                arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
                live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
            )

        run_store = runner.RunStore(tmp_path / "run-store")
        records_path = tmp_path / "records.jsonl"

        def run():
            return runner.run_campaign(
                list(blocks), build_spec=build_spec, arms=("current-rule",), k=2, seed=1, campaign_id="c1",
                run_store=run_store, records_path=records_path, projects_root=tmp_path / "projects", fault=fault,
                workers=1,
            )

        return run, run_store, records_path, built

    def test_a_block_whose_runs_are_all_missing_halts_the_campaign_and_stays_unmarked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        blocks = {
            "d1": (
                _missing_outcome_record("d1", 0, detail="subtype 'error_during_execution'"),
                _missing_outcome_record("d1", 1, detail="subtype 'error_during_execution'"),
            ),
            "d2": (_outcome_record("d2", run_index=0), _outcome_record("d2", run_index=1)),
        }
        run, run_store, records_path, built = self._campaign(tmp_path, monkeypatch, blocks)

        with pytest.raises(runner.SystemicFailureError) as excinfo:
            run()

        message = str(excinfo.value)
        assert "d1" in message and "result-error x2" in message and "error_during_execution" in message
        assert built == ["d1"]  # d2's block never started
        assert run_store.completed_block_ids() == set()  # a resume reruns d1
        assert len(runner.read_run_records(records_path)) == 2  # the missing runs' cost and reasons stay on record
        run_store.acquire_lock()  # the lock was released on the way out
        run_store.release_lock()

    def test_a_block_with_some_completed_runs_continues_and_reports_its_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        blocks = {
            "d1": (_outcome_record("d1", run_index=0), _missing_outcome_record("d1", 1)),
            "d2": (_outcome_record("d2", run_index=0), _outcome_record("d2", run_index=1)),
        }
        run, run_store, _records_path, built = self._campaign(tmp_path, monkeypatch, blocks)

        run()

        assert built == ["d1", "d2"]
        assert run_store.completed_block_ids() == {"d1", "d2"}
        stderr = capsys.readouterr().err
        assert "run: d1: 1 ok, 1 missing (result-error x1), 1 retried" in stderr
        assert "run: d2: 2 ok, 0 missing, 0 retried" in stderr

    def test_an_injected_fault_does_not_halt_on_the_missing_runs_it_causes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        blocks = {"d1": (_missing_outcome_record("d1", 0), _missing_outcome_record("d1", 1))}
        run, run_store, _records_path, _built = self._campaign(
            tmp_path, monkeypatch, blocks, fault=runner.FAULT_WRONG_AGENT,
        )

        run()

        assert run_store.completed_block_ids() == {"d1"}


class TestPreflightDefects:
    def _arm_snapshots(self, root: Path, *arms: str) -> None:
        for arm in arms:
            (root / arm).mkdir(parents=True, exist_ok=True)
            (root / arm / "bench-staff-backend-engineer.md").write_text("agent body\n")

    def test_passes_when_every_commit_resolves_and_every_snapshot_exists(self, tmp_path: Path) -> None:
        defect = _build_two_commit_source_repo(tmp_path / "source")
        self._arm_snapshots(tmp_path / "arms", "current-rule", "function-context")

        runner.preflight_defects(
            [defect], arm_names=("current-rule", "function-context"), source_repo=tmp_path / "source",
            arms_snapshot_root=tmp_path / "arms",
        )  # must not raise

    def test_lists_every_unresolvable_commit_and_every_missing_snapshot_in_one_error(self, tmp_path: Path) -> None:
        real = _build_two_commit_source_repo(tmp_path / "source")
        defect = ConfirmedDefect(
            id="defect-1", source="szz", lens="staff-backend-engineer", base_commit=real.base_commit,
            head_commit="a" * 40, fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
            path="app.py", file_is_markdown=False,
        )
        self._arm_snapshots(tmp_path / "arms", "current-rule")

        with pytest.raises(runner.HarnessInvalidatedError) as excinfo:
            runner.preflight_defects(
                [defect], arm_names=("current-rule", "function-context"), source_repo=tmp_path / "source",
                arms_snapshot_root=tmp_path / "arms",
            )

        message = str(excinfo.value)
        assert "a" * 40 in message and "b" * 40 in message
        assert real.base_commit not in message
        assert "first parent" not in message  # an unresolvable fix commit is reported once, not again as a missing parent
        assert str(tmp_path / "arms" / "function-context" / "bench-staff-backend-engineer.md") in message
        assert str(tmp_path / "arms" / "current-rule") not in message

    def _defect_whose_head_tree_holds(self, tmp_path: Path, config_relpath: str, config_text: str) -> ConfirmedDefect:
        source_repo = tmp_path / "source"
        real = _build_two_commit_source_repo(source_repo)
        _write(source_repo, config_relpath, config_text)
        head_commit = _commit(source_repo, "add project config")
        return dataclasses.replace(real, head_commit=head_commit, fix_commit=head_commit)

    @pytest.mark.parametrize(
        ("config_relpath", "config_text", "refusal"),
        [
            pytest.param(".claude/settings.json", '{"hooks": {}}', "hooks", id="unexpected-settings-key"),
            pytest.param(".mcp.json", "{}", "a session would load", id="mcp-file"),
            pytest.param(".claude/settings.json", "{not json", "unparseable", id="unparseable-settings"),
        ],
    )
    def test_a_head_tree_with_project_config_a_session_must_not_load_fails_before_any_fixture_is_built(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_relpath: str, config_text: str, refusal: str,
    ) -> None:
        defect = self._defect_whose_head_tree_holds(tmp_path, config_relpath, config_text)
        self._arm_snapshots(tmp_path / "arms", "current-rule")
        monkeypatch.setattr(runner.msmr, "_resolved_temp_project_dir", pytest.fail)  # a fixture build would call it

        with pytest.raises(runner.HarnessInvalidatedError) as excinfo:
            runner.preflight_defects(
                [defect], arm_names=("current-rule",), source_repo=tmp_path / "source",
                arms_snapshot_root=tmp_path / "arms",
            )

        assert f"{defect.id}: " in str(excinfo.value)
        assert refusal in str(excinfo.value)

    def test_a_head_tree_with_only_historical_settings_keys_passes(self, tmp_path: Path) -> None:
        defect = self._defect_whose_head_tree_holds(tmp_path, ".claude/settings.json", '{"permissions": {}}')
        self._arm_snapshots(tmp_path / "arms", "current-rule")

        runner.preflight_defects(
            [defect], arm_names=("current-rule",), source_repo=tmp_path / "source",
            arms_snapshot_root=tmp_path / "arms",
        )  # must not raise

    def test_an_unresolvable_head_commit_is_reported_once_and_not_as_a_config_problem(self, tmp_path: Path) -> None:
        real = _build_two_commit_source_repo(tmp_path / "source")
        defect = dataclasses.replace(real, head_commit="a" * 40, fix_commit="a" * 40)
        self._arm_snapshots(tmp_path / "arms", "current-rule")

        with pytest.raises(runner.HarnessInvalidatedError) as excinfo:
            runner.preflight_defects(
                [defect], arm_names=("current-rule",), source_repo=tmp_path / "source",
                arms_snapshot_root=tmp_path / "arms",
            )

        assert str(excinfo.value).count("a" * 40) == 1
        assert "project config" not in str(excinfo.value)

    def test_a_fix_commit_with_no_first_parent_is_reported_with_its_defect_id_before_any_fixture_is_built(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real = _build_two_commit_source_repo(tmp_path / "source")
        root_commit = real.base_commit  # a root commit: the fix diff has no first parent to compare against
        defect = dataclasses.replace(real, fix_commit=root_commit)
        self._arm_snapshots(tmp_path / "arms", "current-rule")
        monkeypatch.setattr(runner.msmr, "_resolved_temp_project_dir", pytest.fail)  # a fixture build would call it

        with pytest.raises(runner.HarnessInvalidatedError) as excinfo:
            runner.preflight_defects(
                [defect], arm_names=("current-rule",), source_repo=tmp_path / "source",
                arms_snapshot_root=tmp_path / "arms",
            )

        message = str(excinfo.value)
        assert f"{defect.id}: fix commit {root_commit} has no resolvable first parent" in message
        assert "does not resolve" not in message  # the root commit itself resolves

    def test_a_directory_that_is_not_a_repo_is_reported_rather_than_read_as_all_resolved(
        self, tmp_path: Path,
    ) -> None:
        defect = _build_two_commit_source_repo(tmp_path / "source")
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()

        with pytest.raises(runner.HarnessInvalidatedError, match="cat-file"):
            runner.preflight_defects([defect], arm_names=(), source_repo=not_a_repo, arms_snapshot_root=tmp_path)


class TestCampaignCostCeiling:
    def test_ceiling_is_every_run_at_its_cap_with_and_without_the_retry(self) -> None:
        ceiling = runner.campaign_cost_ceiling(3, 2, 10)

        assert ceiling.runs == 60
        assert ceiling.single_attempt_usd == pytest.approx(60 * runner.REVIEWER_BUDGET_CAP_USD)
        assert ceiling.all_retried_usd == pytest.approx(60 * runner.ATTEMPTS_PER_RUN * runner.REVIEWER_BUDGET_CAP_USD)

    def test_no_defects_costs_nothing(self) -> None:
        assert runner.campaign_cost_ceiling(0, 2, 10) == (0, 0.0, 0.0)


class TestRunStoreResume:
    def test_sweeps_only_recorded_directories_and_leaves_others_untouched(self, tmp_path: Path) -> None:
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path)
        recorded_dir = tmp_path / "review-bench-recorded"
        recorded_dir.mkdir()
        unrecorded_dir = tmp_path / "review-bench-unrecorded"
        unrecorded_dir.mkdir()
        session_id = str(uuid.uuid4())
        store.record_directory("defect-1", recorded_dir, session_id)

        projects_root = tmp_path / "projects"
        (projects_root / "hash1").mkdir(parents=True)
        (projects_root / "hash1" / f"{session_id}.jsonl").write_text("{}\n")

        swept = store.sweep_abandoned(projects_root)

        assert len(swept) == 1
        assert not recorded_dir.exists()
        assert unrecorded_dir.exists()  # never named in a write-ahead record -- left untouched
        assert not (projects_root / "hash1").exists()

    def test_sweeps_a_directory_only_entry_with_no_session_id_yet(self, tmp_path: Path) -> None:
        """build_defect_fixture_spec records a fixture directory's own
        write-ahead entry as soon as it exists, before any run has launched
        against it -- so its session_id is "" (runner._NO_SESSION_ID_YET),
        and the sweep must delete the directory without attempting a
        session-store lookup for an empty ID."""
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path)
        recorded_dir = tmp_path / "review-bench-unlaunched"
        recorded_dir.mkdir()
        store.record_directory("defect-1", recorded_dir, runner._NO_SESSION_ID_YET)

        swept = store.sweep_abandoned(tmp_path / "projects")

        assert len(swept) == 1
        assert not recorded_dir.exists()

    def test_a_pending_entry_whose_directory_and_session_store_are_gone_is_returned_and_nothing_else_is_deleted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A crash can leave a write-ahead entry whose targets were already
        removed, and the sweep must still report the defect for a rerun
        without raising or touching unrelated session files."""
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path)
        gone_dir = tmp_path / "review-bench-gone"
        store.record_directory("defect-1", gone_dir, runner._NO_SESSION_ID_YET)
        store.record_directory("defect-1", gone_dir, str(uuid.uuid4()))
        projects_root = tmp_path / "projects"
        unrelated_session_file = projects_root / "other-project" / ".jsonl"  # what an empty session id would glob
        unrelated_session_file.parent.mkdir(parents=True)
        unrelated_session_file.write_text("{}\n")

        swept = store.sweep_abandoned(projects_root)

        assert [entry.defect_id for entry in swept] == ["defect-1", "defect-1"]
        assert unrelated_session_file.exists()
        assert "could not remove" not in capsys.readouterr().err

    def test_completed_block_is_not_swept(self, tmp_path: Path) -> None:
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path)
        recorded_dir = tmp_path / "review-bench-done"
        recorded_dir.mkdir()
        store.record_directory("defect-1", recorded_dir, str(uuid.uuid4()))
        store.mark_block_complete("defect-1")

        swept = store.sweep_abandoned(tmp_path / "projects")

        assert swept == []
        assert recorded_dir.exists()

    def test_concurrent_record_directory_calls_never_corrupt_the_write_ahead_log(self, tmp_path: Path) -> None:
        """run_defect_block calls record_directory from every worker-pool
        thread in a block -- a torn or interleaved write here would corrupt
        the very log the resume sweep trusts (evals/README.md's
        "Interruption and cleanup" section)."""
        store = runner.RunStore(tmp_path / "run-store")
        directory = tmp_path / "shared-fixture"
        directory.mkdir()

        def record(i: int) -> None:
            store.record_directory("defect-1", directory, f"session-{i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(50)))

        lines = store.write_ahead_path.read_text().splitlines()
        assert len(lines) == 50
        parsed = [json.loads(line) for line in lines]  # raises if any line is torn/interleaved
        assert {p["session_id"] for p in parsed} == {f"session-{i}" for i in range(50)}


class TestSweepAbandonedTargetValidation:
    """The write-ahead log is a plain file: a corrupt or hand-edited entry
    must never steer a delete outside the bench's own fixtures."""

    def _store_with_entry(self, tmp_path: Path, directory: str, session_id: str = "") -> runner.RunStore:
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path / "fixtures")
        (tmp_path / "fixtures").mkdir(exist_ok=True)
        with open(store.write_ahead_path, "a") as fh:
            fh.write(json.dumps({"defect_id": "defect-1", "directory": directory, "session_id": session_id}) + "\n")
        return store

    @pytest.mark.parametrize(
        "directory_of",
        [
            pytest.param(lambda root: "", id="empty-path-normalizing-to-cwd"),
            pytest.param(lambda root: "review-bench-relative", id="relative-path"),
            pytest.param(lambda root: str(root.parent / "review-bench-outside-root"), id="outside-the-fixture-root"),
            pytest.param(lambda root: str(root / "not-a-fixture"), id="missing-fixture-prefix"),
            pytest.param(lambda root: str(root / "review-bench-a" / "review-bench-nested"), id="nested-below-a-fixture"),
            pytest.param(lambda root: str(root / "review-bench-a" / ".." / "review-bench-b"), id="dot-dot-segment"),
            pytest.param(lambda root: str(root), id="the-fixture-root-itself"),
        ],
    )
    def test_refuses_a_directory_that_is_not_a_direct_prefixed_child_of_the_fixture_root(
        self, tmp_path: Path, directory_of,
    ) -> None:
        store = self._store_with_entry(tmp_path, directory_of(tmp_path / "fixtures"))

        with pytest.raises(runner.HarnessInvalidatedError, match="refusing to delete"):
            store.sweep_abandoned(tmp_path / "projects")

    def test_refuses_a_symlink_named_like_a_fixture_and_leaves_its_target_untouched(self, tmp_path: Path) -> None:
        precious = tmp_path / "precious"
        precious.mkdir()
        (precious / "keep.txt").write_text("keep")
        (tmp_path / "fixtures").mkdir()
        link = tmp_path / "fixtures" / "review-bench-link"
        link.symlink_to(precious, target_is_directory=True)
        store = self._store_with_entry(tmp_path, str(link))

        with pytest.raises(runner.HarnessInvalidatedError, match="refusing to delete"):
            store.sweep_abandoned(tmp_path / "projects")

        assert (precious / "keep.txt").read_text() == "keep"

    @pytest.mark.parametrize("session_id", ["*", "../x", "SESSION-XYZ", "0" * 32, "{SESSION-XYZ}"])
    def test_refuses_a_session_id_that_is_not_a_canonical_uuid(self, tmp_path: Path, session_id: str) -> None:
        store = self._store_with_entry(tmp_path, str(tmp_path / "fixtures" / "review-bench-a"), session_id)
        projects_root = tmp_path / "projects"
        (projects_root / "real-project").mkdir(parents=True)
        (projects_root / "real-project" / "other.jsonl").write_text("{}\n")

        with pytest.raises(runner.HarnessInvalidatedError, match="invalid session ID"):
            store.sweep_abandoned(projects_root)

        assert (projects_root / "real-project" / "other.jsonl").exists()

    def test_refuses_a_session_store_reached_through_a_symlinked_project_directory(self, tmp_path: Path) -> None:
        session_id = str(uuid.uuid4())
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / f"{session_id}.jsonl").write_text("{}\n")
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        (projects_root / "linked").symlink_to(elsewhere, target_is_directory=True)
        store = self._store_with_entry(tmp_path, str(tmp_path / "fixtures" / "review-bench-a"), session_id)

        with pytest.raises(runner.HarnessInvalidatedError, match="refusing to delete session store"):
            store.sweep_abandoned(projects_root)

        assert (elsewhere / f"{session_id}.jsonl").exists()

    def test_deletes_nothing_when_any_pending_entry_is_refused(self, tmp_path: Path) -> None:
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path / "fixtures")
        valid_dir = tmp_path / "fixtures" / "review-bench-valid"
        valid_dir.mkdir(parents=True)
        store.record_directory("defect-1", valid_dir, runner._NO_SESSION_ID_YET)
        store.record_directory("defect-2", tmp_path / "elsewhere", runner._NO_SESSION_ID_YET)

        with pytest.raises(runner.HarnessInvalidatedError):
            store.sweep_abandoned(tmp_path / "projects")

        assert valid_dir.exists()

    def test_a_failed_delete_is_reported_and_the_sweep_still_returns_the_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path)
        stuck_dir = tmp_path / "review-bench-stuck"
        stuck_dir.mkdir()
        store.record_directory("defect-1", stuck_dir, runner._NO_SESSION_ID_YET)
        # A real permission failure is not reproducible as root, so the failing call is stubbed.
        monkeypatch.setattr(
            runner.shutil, "rmtree", lambda path, onexc: onexc(os.rmdir, str(path), PermissionError("denied")),
        )

        swept = store.sweep_abandoned(tmp_path / "projects")

        assert [entry.defect_id for entry in swept] == ["defect-1"]
        stderr = capsys.readouterr().err
        assert f"could not remove {stuck_dir}" in stderr
        assert "denied" in stderr


class TestIdentifierGrammars:
    @pytest.mark.parametrize("campaign_id", ["run-1a2b3c4d", "c1", "Baseline.v2_final", "a" * 64])
    def test_accepts_a_well_formed_campaign_id(self, campaign_id: str) -> None:
        assert identifiers.validate_campaign_id(campaign_id) == campaign_id

    @pytest.mark.parametrize(
        "campaign_id",
        ["", "../escape", "/abs/path", "a/b", "-leading-dash", ".hidden", "with space", "x" * 65, "new\nline", "a\x00b"],
    )
    def test_rejects_a_malformed_campaign_id(self, campaign_id: str) -> None:
        with pytest.raises(identifiers.InvalidIdentifierError):
            identifiers.validate_campaign_id(campaign_id)

    @pytest.mark.parametrize("base_ref", ["origin/main", "main", "release/1.2", "HEAD~3", "v1.0^"])
    def test_accepts_a_well_formed_base_ref(self, base_ref: str) -> None:
        assert identifiers.validate_base_ref(base_ref) == base_ref

    @pytest.mark.parametrize(
        "base_ref", ["", "--output=/tmp/x", "-n", "a..b", "main;rm", "a b", "x" * 129],
    )
    def test_rejects_a_malformed_base_ref(self, base_ref: str) -> None:
        with pytest.raises(identifiers.InvalidIdentifierError):
            identifiers.validate_base_ref(base_ref)

    def test_accepts_a_canonical_uuid_and_the_empty_no_session_marker(self) -> None:
        session_id = str(uuid.uuid4())
        assert identifiers.validate_session_id(session_id) == session_id
        assert identifiers.validate_session_id("") == ""


class TestPendingEntriesTornTailTolerance:
    def test_pending_entries_tolerates_a_torn_final_line(self, tmp_path: Path) -> None:
        """The read-side tolerance for a torn final line applies to
        pending_entries directly, independent of whether sweep_abandoned's
        on-disk repair has run yet -- mirrors read_run_records' own
        truncated-final-line tolerance."""
        store = runner.RunStore(tmp_path / "run-store")
        store.record_directory("defect-1", tmp_path / "review-bench-a", runner._NO_SESSION_ID_YET)
        store.record_directory("defect-2", tmp_path / "review-bench-b", runner._NO_SESSION_ID_YET)
        with open(store.write_ahead_path, "a") as fh:
            fh.write('{"defect_id": "defect-3", "directory": "')  # torn mid-write, no closing brace, no newline

        pending = store.pending_entries()

        assert {entry.defect_id for entry in pending} == {"defect-1", "defect-2"}

    def test_sweep_abandoned_repairs_the_write_ahead_tail_before_the_next_append(self, tmp_path: Path) -> None:
        """sweep_abandoned must repair the write-ahead log's torn tail on
        disk, not merely tolerate it in memory -- record_directory's next
        append glues its new bytes onto whatever the file's last line
        already is, so an unrepaired torn tail would corrupt that append
        into a permanently malformed mid-file line that pending_entries
        would then raise on instead of parsing cleanly."""
        store = runner.RunStore(tmp_path / "run-store", fixture_root=tmp_path)
        store.record_directory("defect-1", tmp_path / "review-bench-a", runner._NO_SESSION_ID_YET)
        store.record_directory("defect-2", tmp_path / "review-bench-b", runner._NO_SESSION_ID_YET)
        with open(store.write_ahead_path, "a") as fh:
            fh.write('{"defect_id": "defect-3", "directory": "')  # torn mid-write, no closing brace, no newline

        store.sweep_abandoned(tmp_path / "projects")
        store.record_directory("defect-4", tmp_path / "review-bench-d", runner._NO_SESSION_ID_YET)

        pending = store.pending_entries()
        assert {entry.defect_id for entry in pending} == {"defect-1", "defect-2", "defect-4"}

    def test_pending_entries_raises_on_a_malformed_line_that_is_not_the_last_one(self, tmp_path: Path) -> None:
        """The truncated-final-line tolerance is positional, not blanket:
        a JSON-syntax failure earlier in the file is a genuine corruption,
        mirroring read_run_records' own non-final-line behavior."""
        store = runner.RunStore(tmp_path / "run-store")
        with open(store.write_ahead_path, "w") as fh:
            fh.write('{"defect_id": "defect-1", "directory": "d1"\n')  # truncated JSON, not the last line
            fh.write(json.dumps({"defect_id": "defect-2", "directory": "d2", "session_id": ""}) + "\n")

        with pytest.raises(runner.HarnessInvalidatedError, match="malformed line 1"):
            store.pending_entries()

    @pytest.mark.parametrize(
        "missing_field, position",
        [
            ("defect_id", "mid_file"),
            ("defect_id", "final_line"),
            ("directory", "mid_file"),
            ("directory", "final_line"),
        ],
    )
    def test_pending_entries_raises_at_the_malformed_lines_own_line_number(
        self, tmp_path: Path, missing_field: str, position: str
    ) -> None:
        """WriteAheadEntry(**data) raises TypeError when either required field
        is missing. pending_entries must reach that raise whether the
        malformed line sits mid-file or is the log's own final line. A
        sole-content fixture proves only line 1, not every position."""
        store = runner.RunStore(tmp_path / "run-store")
        well_formed = {"defect_id": "defect-well-formed", "directory": "d", "session_id": ""}
        malformed = {k: v for k, v in well_formed.items() if k != missing_field}
        lines = [well_formed, malformed, well_formed] if position == "mid_file" else [well_formed, well_formed, malformed]
        malformed_line_number = lines.index(malformed) + 1
        with open(store.write_ahead_path, "w") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")

        with pytest.raises(runner.HarnessInvalidatedError, match=rf"malformed line {malformed_line_number} in "):
            store.pending_entries()

    def test_pending_entries_raises_on_a_malformed_line_for_an_already_completed_defect_id(
        self, tmp_path: Path
    ) -> None:
        """A malformed line whose defect_id is already completed must still
        raise. pending_entries validates every line's shape before filtering
        out already-completed blocks, so an already-complete defect_id can't
        mask a shape defect."""
        store = runner.RunStore(tmp_path / "run-store")
        store.mark_block_complete("defect-1")
        lines = [
            {"defect_id": "defect-1", "directory": "d1", "session_id": ""},
            {"defect_id": "defect-1"},  # missing directory, but defect-1 is already completed
            {"defect_id": "defect-2", "directory": "d2", "session_id": ""},
        ]
        with open(store.write_ahead_path, "w") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")

        with pytest.raises(runner.HarnessInvalidatedError, match="malformed line 2"):
            store.pending_entries()


class TestRunStoreLock:
    def test_a_second_acquirer_is_refused_and_told_the_holders_pid(self, tmp_path: Path) -> None:
        holder = runner.RunStore(tmp_path / "run-store")
        holder.acquire_lock()
        contender = runner.RunStore(tmp_path / "run-store")
        with pytest.raises(runner.RunStoreLocked, match=str(os.getpid())):
            contender.acquire_lock()

    def test_the_lock_is_available_again_after_release(self, tmp_path: Path) -> None:
        holder = runner.RunStore(tmp_path / "run-store")
        holder.acquire_lock()
        holder.release_lock()
        runner.RunStore(tmp_path / "run-store").acquire_lock()  # does not raise

    def test_a_refused_acquirer_releasing_does_not_release_the_holders_lock(self, tmp_path: Path) -> None:
        holder = runner.RunStore(tmp_path / "run-store")
        holder.acquire_lock()
        contender = runner.RunStore(tmp_path / "run-store")
        with pytest.raises(runner.RunStoreLocked):
            contender.acquire_lock()
        contender.release_lock()
        with pytest.raises(runner.RunStoreLocked):
            runner.RunStore(tmp_path / "run-store").acquire_lock()

    def test_the_lock_dies_with_a_holder_killed_without_releasing_it(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "run-store"
        holder_script = (
            "import sys, time; sys.path.insert(0, sys.argv[1]); "
            "from pathlib import Path; from review_bench import runner; "
            "runner.RunStore(Path(sys.argv[2])).acquire_lock(); print('held', flush=True); time.sleep(60)"
        )
        evals_dir = Path(__file__).resolve().parent
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_script, str(evals_dir), str(store_dir)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            with pytest.raises(runner.RunStoreLocked, match=str(holder.pid)):
                runner.RunStore(store_dir).acquire_lock()
        finally:
            holder.send_signal(signal.SIGKILL)
            holder.wait()
            holder.stdout.close()
        runner.RunStore(store_dir).acquire_lock()  # the kernel dropped the dead holder's lock

    def test_concurrent_acquire_attempts_under_contention_yield_exactly_one_winner(self, tmp_path: Path) -> None:
        stores = [runner.RunStore(tmp_path / "run-store") for _ in range(8)]
        results: list[str] = []

        def try_acquire(store: runner.RunStore) -> None:
            try:
                store.acquire_lock()
                results.append("acquired")
            except runner.RunStoreLocked:
                results.append("locked")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(try_acquire, stores))

        assert sorted(results) == ["acquired"] + ["locked"] * 7

    def test_run_campaign_refuses_a_held_store_before_any_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        holder = runner.RunStore(tmp_path / "run-store")
        holder.acquire_lock()
        contender = runner.RunStore(tmp_path / "run-store")
        sweeps: list[Path] = []
        monkeypatch.setattr(contender, "sweep_abandoned", lambda projects_root: sweeps.append(projects_root))

        with pytest.raises(runner.RunStoreLocked):
            runner.run_campaign(
                ["defect-1"], build_spec=lambda defect_id: None, arms=("current-rule",), k=1, seed=0,
                campaign_id="c1", run_store=contender, records_path=tmp_path / "records.jsonl",
                projects_root=tmp_path / "projects",
            )

        assert sweeps == []

    def test_main_reports_a_held_store_as_a_clean_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: _STUBBED_ENVIRONMENT)
        files = _frozen_files(tmp_path, monkeypatch, k=runner.DEFAULT_K)
        holder = runner.RunStore(tmp_path / "run-store")
        holder.acquire_lock()

        exit_code = run_review_bench.main([
            "run", "--defects-path", str(files["defects"]), "--arms-root", str(files["arms"]),
            "--conditions-path", str(files["conditions"]), "--run-store-dir", str(tmp_path / "run-store"),
            "--records-dir", str(tmp_path / "records"), "--campaign-id", "c1",
        ])

        assert exit_code == 1
        stderr = capsys.readouterr().err
        assert "held by pid" in stderr
        assert "Traceback" not in stderr


class TestRunRejectsSmokeOnlyFaultInjection:
    def test_run_subcommand_has_no_inject_fault_flag(self) -> None:
        parser = run_review_bench.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "--inject-fault", "wrong-agent"])

    def test_smoke_subcommand_accepts_it(self) -> None:
        parser = run_review_bench.build_parser()
        args = parser.parse_args(["smoke", "--defect-id", "d1", "--inject-fault", "wrong-agent"])
        assert args.inject_fault == "wrong-agent"

    def test_run_subcommand_parses_without_it(self) -> None:
        parser = run_review_bench.build_parser()
        args = parser.parse_args(["run"])
        assert args.subcommand == "run"


class TestRunOrSmokeWithNoDefects:
    def test_cmd_run_returns_nonzero_when_no_defects_are_selected(self, tmp_path: Path) -> None:
        empty_defects_path = tmp_path / "defects.json"  # never written -- load_confirmed_defects treats absent as []
        parser = run_review_bench.build_parser()
        args = parser.parse_args(["run", "--defects-path", str(empty_defects_path)])
        assert run_review_bench.cmd_run(args) == 1


class TestRunOrSmokeDefaultFillReachesRunCampaign:
    def test_cli_default_k_and_workers_reach_run_campaign_when_defects_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-empty defect set is the one path that consumes _run_or_smoke's
        None-default fill-in of args.k and args.workers."""
        defect = ConfirmedDefect(
            id="defect-1", source="szz", lens="staff-backend-engineer", base_commit="a" * 40,
            head_commit="b" * 40, fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
            path="app.py", file_is_markdown=False,
        )
        monkeypatch.setattr(run_review_bench, "_load_defects_for_run", lambda args: [defect])

        captured_kwargs: dict = {}

        def fake_run_campaign(defect_ids, **kwargs):
            captured_kwargs.update(kwargs)
            return runner.CampaignResult(campaign_id=kwargs["campaign_id"], block_results={})

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)

        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: _STUBBED_ENVIRONMENT)
        files = _frozen_files(tmp_path, monkeypatch, k=runner.DEFAULT_K)

        parser = run_review_bench.build_parser()
        args = parser.parse_args([
            "run", "--defects-path", str(files["defects"]), "--arms-root", str(files["arms"]),
            "--conditions-path", str(files["conditions"]), "--run-store-dir", str(tmp_path / "run-store"),
            "--records-dir", str(tmp_path / "records"),
        ])
        assert args.k is None and args.workers is None  # the CLI default this fill-in must resolve

        assert run_review_bench.cmd_run(args) == 0
        assert captured_kwargs["k"] == runner.DEFAULT_K
        assert captured_kwargs["workers"] == run_skill_evals.DEFAULT_WORKERS


class TestRunRecordJsonlRoundTrip:
    def test_append_then_read_back(self, tmp_path: Path) -> None:
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=("/tmp/x.py",), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        # Distinct run_index from the first call -- the same run_index would
        # collide under read_run_records' identity dedup and defeat this
        # test's two-call persistence intent.
        second_record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=1, opaque_run_id="def456",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=("/tmp/x.py",), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_records(path, (record,))
        runner.append_run_records(path, (second_record,))
        loaded = runner.read_run_records(path)
        assert len(loaded) == 2
        assert record in loaded and second_record in loaded

    def test_cost_attempts_and_missing_detail_survive_a_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_MISSING, missing_reason=runner.VALIDITY_FAIL_RESULT_ERROR, observed_model=None,
            observed_tools=(), out_of_session_paths=(), findings_text=None, wall_clock_s=1.0, read_calls=0,
            read_tokens_est=0, partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=0,
            over_read_cap=False, dispatch_prompt_verbatim=True, cli_version="2.1.0", ambient_config_commit="deadbeef",
            total_cost_usd=0.75, input_tokens=1, output_tokens=2, cache_read_input_tokens=3,
            cache_creation_input_tokens=4, attempts=2, missing_detail="subtype 'error_during_execution'",
        )

        runner.append_run_records(path, (record,))

        assert runner.read_run_records(path) == [record]

    def test_read_run_records_dedups_by_identity_keeping_the_later_record(self, tmp_path: Path) -> None:
        """A later record for the same (campaign_id, defect_id, arm,
        run_index) identity replaces an earlier one instead of both
        existing -- the shape a block rerun after a crash between
        append_run_records and mark_block_complete produces (run_campaign's
        own comment on that ordering), so it doesn't double-weight that
        defect downstream (e.g. analysis.pooled_precision)."""
        path = tmp_path / "records.jsonl"
        first_attempt = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="First attempt.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        rerun_attempt = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="def456",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="Rerun attempt.",
            wall_clock_s=13.0, read_calls=4, read_tokens_est=120, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_records(path, (first_attempt,))
        runner.append_run_records(path, (rerun_attempt,))

        loaded = runner.read_run_records(path)

        assert loaded == [rerun_attempt]

    def test_read_run_records_keeps_distinct_run_indices_of_the_same_defect_and_arm(
        self, tmp_path: Path,
    ) -> None:
        """Dedup keys on the full (campaign_id, defect_id, arm, run_index)
        tuple, not a coarser prefix -- two different run_index values for
        the same defect/arm must not collapse into one."""
        path = tmp_path / "records.jsonl"
        record_0 = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="Run 0.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        record_1 = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=1, opaque_run_id="def456",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="Run 1.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_records(path, (record_0, record_1))

        loaded = runner.read_run_records(path)

        assert {r.opaque_run_id for r in loaded} == {"abc123", "def456"}

    def test_append_run_records_writes_a_whole_batch_in_one_call(self, tmp_path: Path) -> None:
        path = tmp_path / "records.jsonl"

        def _record(run_index: int) -> runner.RunRecord:
            return runner.RunRecord(
                campaign_id="c1", defect_id="d1", arm="current-rule", run_index=run_index,
                opaque_run_id=f"abc{run_index}", status=runner.STATUS_OK, missing_reason=None,
                observed_model=MODEL_ID, observed_tools=("Read", "Grep"), out_of_session_paths=(),
                findings_text="No findings.", wall_clock_s=12.5, read_calls=3, read_tokens_est=100,
                partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=1,
                over_read_cap=False, dispatch_prompt_verbatim=True, cli_version="2.1.0",
                ambient_config_commit="deadbeef",
            )

        # Three distinct run_index values -- not the same record three
        # times, which read_run_records' identity dedup would collapse to
        # one and defeat this test's "one call writes the whole batch" intent.
        runner.append_run_records(path, (_record(0), _record(1), _record(2)))
        assert len(runner.read_run_records(path)) == 3

    def test_append_run_records_is_a_no_op_on_an_empty_sequence(self, tmp_path: Path) -> None:
        path = tmp_path / "records.jsonl"
        runner.append_run_records(path, ())
        assert not path.exists()

    def test_read_run_records_returns_empty_list_for_a_nonexistent_path(self, tmp_path: Path) -> None:
        path = tmp_path / "never-written.jsonl"
        assert runner.read_run_records(path) == []

    def test_read_run_records_returns_empty_list_for_an_all_blank_lines_file(self, tmp_path: Path) -> None:
        path = tmp_path / "records.jsonl"
        path.write_text("\n\n   \n")
        assert runner.read_run_records(path) == []

    def test_read_run_records_tolerates_one_truncated_trailing_line_and_keeps_the_rest(
        self, tmp_path: Path,
    ) -> None:
        """Regression guard: a truncated final JSONL line -- e.g. a partial
        write from a hard kill mid-append -- must not discard every other
        already-recorded run in the same file, and must not raise."""
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_records(path, (record,))
        with open(path, "a") as fh:
            fh.write('{"defect_id": "d1", "arm": "current-rule"\n')  # truncated JSON, no closing brace

        loaded = runner.read_run_records(path)

        assert loaded == [record]

    def test_append_run_records_discards_a_torn_tail_that_fails_to_parse(
        self, tmp_path: Path,
    ) -> None:
        """A process killed before writing a record's closing brace leaves
        a trailing line with no newline that also fails to parse as JSON.
        The next append_run_records call must discard that torn tail
        rather than glue the new record onto it. Gluing would leave
        permanently invalid JSON that's no longer the file's last line, so
        it would no longer be eligible for read_run_records'
        truncated-final-line tolerance."""
        path = tmp_path / "records.jsonl"
        path.write_text('{"defect_id": "d1", "arm": "current-rule"')  # torn mid-write, no closing brace, no newline
        new_record = runner.RunRecord(
            campaign_id="c1", defect_id="d2", arm="current-rule", run_index=0, opaque_run_id="def456",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )

        runner.append_run_records(path, (new_record,))
        loaded = runner.read_run_records(path)

        assert loaded == [new_record]

    def test_append_run_records_restores_a_complete_record_missing_only_its_trailing_newline(
        self, tmp_path: Path,
    ) -> None:
        """A process killed between writing a record's closing brace and
        its trailing newline leaves a complete record that still parses
        as JSON. That record can be a real, already-observed run a caller
        is about to reuse -- e.g. cmd_judge's recall-record dedup. The
        next append_run_records call must keep it and restore only the
        missing newline, instead of discarding it as if it were
        corruption."""
        path = tmp_path / "records.jsonl"
        preexisting_record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        # No trailing newline -- the kill landed after the closing brace but
        # before the write of "\n" was flushed.
        path.write_text(json.dumps(preexisting_record.to_dict()))
        new_record = runner.RunRecord(
            campaign_id="c1", defect_id="d2", arm="current-rule", run_index=0, opaque_run_id="def456",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )

        runner.append_run_records(path, (new_record,))
        loaded = runner.read_run_records(path)

        assert preexisting_record in loaded and new_record in loaded
        assert len(loaded) == 2

    def test_append_run_records_discards_a_torn_tail_without_touching_earlier_records(
        self, tmp_path: Path,
    ) -> None:
        """The torn-tail-discard branch must operate on the tail only, not
        the whole buffer: with prior complete, newline-terminated records
        already in the file, a regression that truncated from byte 0
        instead of from the last newline would wipe them, and a
        sole-content fixture (the file being only the torn line) can't
        catch that since data.rfind(b"\\n") returns -1 either way."""
        path = tmp_path / "records.jsonl"
        earlier_records = [
            runner.RunRecord(
                campaign_id="c1", defect_id="d1", arm="current-rule", run_index=run_index,
                opaque_run_id=f"earlier{run_index}", status=runner.STATUS_OK, missing_reason=None,
                observed_model=MODEL_ID, observed_tools=("Read", "Grep"), out_of_session_paths=(),
                findings_text="No findings.", wall_clock_s=12.5, read_calls=3, read_tokens_est=100,
                partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=1,
                over_read_cap=False, dispatch_prompt_verbatim=True, cli_version="2.1.0",
                ambient_config_commit="deadbeef",
            )
            for run_index in (0, 1)
        ]
        with open(path, "w") as fh:
            for record in earlier_records:
                fh.write(json.dumps(record.to_dict()) + "\n")
            fh.write('{"defect_id": "d1", "arm": "current-rule"')  # torn mid-write, no closing brace, no newline
        new_record = runner.RunRecord(
            campaign_id="c1", defect_id="d2", arm="current-rule", run_index=0, opaque_run_id="def456",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )

        runner.append_run_records(path, (new_record,))
        loaded = runner.read_run_records(path)

        assert all(record in loaded for record in (*earlier_records, new_record))
        assert len(loaded) == 3

    def test_append_run_records_restores_a_torn_tail_without_touching_earlier_records(
        self, tmp_path: Path,
    ) -> None:
        """The parse check that selects the restore branch must run against
        the isolated tail, not the whole buffer: with prior complete,
        newline-terminated records already in the file, a regression that
        checked `json.loads(data)` instead of `json.loads(tail)` would see
        the earlier records as leading garbage, misclassify this fixture as
        unparseable, and wipe the earlier records via the discard branch
        instead of restoring the tail's missing newline."""
        path = tmp_path / "records.jsonl"
        earlier_records = [
            runner.RunRecord(
                campaign_id="c1", defect_id="d1", arm="current-rule", run_index=run_index,
                opaque_run_id=f"earlier{run_index}", status=runner.STATUS_OK, missing_reason=None,
                observed_model=MODEL_ID, observed_tools=("Read", "Grep"), out_of_session_paths=(),
                findings_text="No findings.", wall_clock_s=12.5, read_calls=3, read_tokens_est=100,
                partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=1,
                over_read_cap=False, dispatch_prompt_verbatim=True, cli_version="2.1.0",
                ambient_config_commit="deadbeef",
            )
            for run_index in (0, 1)
        ]
        preexisting_record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=2, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        with open(path, "w") as fh:
            for record in earlier_records:
                fh.write(json.dumps(record.to_dict()) + "\n")
            # No trailing newline -- the kill landed after the closing brace
            # but before the write of "\n" was flushed.
            fh.write(json.dumps(preexisting_record.to_dict()))
        new_record = runner.RunRecord(
            campaign_id="c1", defect_id="d2", arm="current-rule", run_index=0, opaque_run_id="def456",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )

        runner.append_run_records(path, (new_record,))
        loaded = runner.read_run_records(path)

        assert all(record in loaded for record in (*earlier_records, preexisting_record, new_record))
        assert len(loaded) == 4

    def test_read_run_records_raises_on_a_truncated_line_that_is_not_the_last_one(
        self, tmp_path: Path,
    ) -> None:
        """The truncated-final-line tolerance is positional, not blanket: a
        JSON-syntax failure earlier in the file is a genuine corruption.
        append_run_records repairs a torn trailing line at write time
        (restoring the missing newline or truncating it away) before it can
        get glued to a later append, so a non-final truncated line reaching
        read_run_records is never that recovered shape -- it still fails
        closed."""
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        with open(path, "w") as fh:
            fh.write('{"defect_id": "d1", "arm": "current-rule"\n')  # truncated JSON, not the last line
        runner.append_run_records(path, (record,))

        with pytest.raises(runner.HarnessInvalidatedError, match="malformed line 1"):
            runner.read_run_records(path)

    def test_read_run_records_raises_on_a_line_whose_top_level_json_value_is_a_bare_scalar(
        self, tmp_path: Path,
    ) -> None:
        """A line can parse to valid JSON that isn't an object at all -- e.g. a bare
        string. RunRecord.from_dict's `dict(data)` call raises ValueError in that
        case, not TypeError or json.JSONDecodeError. That's a schema failure, not a
        truncated-write one, so it raises even as the file's last line."""
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_records(path, (record,))
        with open(path, "a") as fh:
            fh.write(json.dumps("corrupt") + "\n")  # valid JSON, but not object-shaped

        with pytest.raises(runner.HarnessInvalidatedError, match="malformed line 2"):
            runner.read_run_records(path)

    def test_read_run_records_raises_on_a_valid_json_line_missing_a_required_field(
        self, tmp_path: Path,
    ) -> None:
        """A line can be syntactically valid JSON and still fail to parse
        into a RunRecord -- e.g. a required field dropped by a hand-edited
        fixture or a future schema-drift line. RunRecord.from_dict raises
        TypeError in that case, not json.JSONDecodeError. That's a schema
        failure, not a truncated-write one, so it raises even as the file's
        last line."""
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_records(path, (record,))
        with open(path, "a") as fh:
            fh.write(json.dumps({"defect_id": "d2", "arm": "current-rule"}) + "\n")  # missing campaign_id, etc.

        with pytest.raises(runner.HarnessInvalidatedError, match="malformed line 2"):
            runner.read_run_records(path)

    def test_from_dict_defaults_missing_over_read_cap_to_false(self) -> None:
        """A record with no over_read_cap key must default it to False rather
        than raise a TypeError."""
        data = {
            "campaign_id": "c1", "defect_id": "d1", "arm": "current-rule", "run_index": 0,
            "opaque_run_id": "abc123", "status": runner.STATUS_OK, "missing_reason": None,
            "observed_model": MODEL_ID, "observed_tools": ("Read", "Grep"), "out_of_session_paths": (),
            "findings_text": "No findings.", "wall_clock_s": 12.5, "read_calls": 3, "read_tokens_est": 100,
            "partial_view_reads": 0, "paged_followups": 0, "whole_file_reads_of_changed_files": 1,
            "dispatch_prompt_verbatim": True, "cli_version": "2.1.0", "ambient_config_commit": "deadbeef",
        }
        assert "over_read_cap" not in data

        record = runner.RunRecord.from_dict(data)

        assert record.over_read_cap is False

    def test_read_run_records_accepts_a_record_written_before_cost_attempts_and_missing_detail_existed(
        self, tmp_path: Path,
    ) -> None:
        legacy = {
            "campaign_id": "c1", "defect_id": "d1", "arm": "current-rule", "run_index": 0,
            "opaque_run_id": "abc123", "status": runner.STATUS_OK, "missing_reason": None,
            "observed_model": MODEL_ID, "observed_tools": ["Read"], "out_of_session_paths": [],
            "findings_text": "No findings.", "wall_clock_s": 12.5, "read_calls": 3, "read_tokens_est": 100,
            "partial_view_reads": 0, "paged_followups": 0, "whole_file_reads_of_changed_files": 1,
            "over_read_cap": False, "dispatch_prompt_verbatim": True, "cli_version": "2.1.0",
            "ambient_config_commit": "deadbeef",
        }
        path = tmp_path / "records.jsonl"
        path.write_text(json.dumps(legacy) + "\n")

        (record,) = runner.read_run_records(path)

        assert record.total_cost_usd is None
        assert record.input_tokens is None
        assert record.attempts == 1
        assert record.missing_detail is None


class TestApplyFaultInjection:
    def test_none_fault_is_a_no_op(self) -> None:
        assert runner.apply_fault_injection("base prompt", fault=None) == "base prompt"

    def test_unknown_fault_raises(self) -> None:
        with pytest.raises(ValueError):
            runner.apply_fault_injection("base prompt", fault="not-a-real-fault")

    # Verifies prompt mutation only. How evaluate_run_validity classifies these
    # faults' effects is covered offline by TestDispatcherToolCallChecks.
    def test_known_faults_mutate_the_prompt(self) -> None:
        for fault in runner.KNOWN_SMOKE_FAULTS:
            mutated = runner.apply_fault_injection("base prompt", fault=fault)
            assert mutated != "base prompt"
            assert mutated.startswith("base prompt")
