"""Tests for evals/review_bench/runner.py. Offline throughout:
every per-run validity check is driven by synthetic subagent transcripts
under evals/fixtures/review-bench/, in the JSONL sidecar shape
test_measure_subagent_model_resolution.py's own helpers write. No test
launches `claude`.
"""
from __future__ import annotations

import concurrent.futures
import json
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
import run_review_bench
import run_skill_evals
from review_bench import runner
from review_bench.defects import ConfirmedDefect

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "review-bench"

AGENT_NAME = "bench-staff-backend-engineer"
MODEL_ID = "claude-sonnet-5"
INNER_PROMPT = "REVIEW_PROMPT_PLACEHOLDER"
DECLARED_TOOLS = frozenset({"Read", "Grep", "Glob"})


def _own_session_paths(scenario_dir: Path) -> tuple[Path, Path]:
    """This scenario's own (session_jsonl, subagent_dir) pair, matching
    exactly what runner.execute_run computes from a real session_jsonl via
    msmr.subagent_dir_for_session -- never the whole session-store
    directory a sibling run's own transcript can also share."""
    session_jsonl = scenario_dir / "session-1.jsonl"
    return session_jsonl, runner.msmr.subagent_dir_for_session(session_jsonl)


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
        return []
    return [line.encode() for line in stream_path.read_text().splitlines() if line.strip()]


def _evaluate(
    scenario_dir: Path, *, own_dirs: tuple[Path, ...] | None = None,
    live_checkout_roots: tuple[Path, ...] = (), changed_relpaths: tuple[str, ...] = (),
    agent_declared_tools: frozenset[str] = DECLARED_TOOLS, timed_out: bool = False,
    fixture_dir: Path | None = None,
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
    )


class TestDefaultLiveCheckoutRoots:
    def test_dedupes_when_ambient_config_resolves_into_this_same_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(runner, "ambient_config_checkout_root", lambda: runner.REPO_ROOT)
        assert runner.default_live_checkout_roots() == (runner.REPO_ROOT.resolve(),)

    def test_keeps_both_when_ambient_config_resolves_elsewhere(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        other_checkout = tmp_path / "other-checkout"
        other_checkout.mkdir()
        monkeypatch.setattr(runner, "ambient_config_checkout_root", lambda: other_checkout)
        roots = runner.default_live_checkout_roots()
        assert runner.REPO_ROOT.resolve() in roots
        assert other_checkout.resolve() in roots
        assert len(roots) == 2


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

    def test_build_review_prompt_fills_subject(self) -> None:
        prompt = runner.build_review_prompt("fix: a bug")
        assert "Its commit subject is: fix: a bug." in prompt
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
            dispatcher_session_jsonl=session_jsonl, stream_lines=[], timed_out=False,
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
            dispatcher_session_jsonl=scenario / "session-1.jsonl", stream_lines=[], timed_out=False,
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
            dispatcher_session_jsonl=scenario / "session-1.jsonl", stream_lines=[], timed_out=False,
            expected_agent_name=AGENT_NAME, expected_inner_prompt=INNER_PROMPT, expected_model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, own_dirs=(scenario,),
            projects_root=projects_root, own_session_paths=_own_session_paths(scenario),
            live_checkout_roots=(), changed_relpaths=(),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_CONFIG_DIR_LEAK

    def test_read_of_a_persisted_tool_result_in_the_runs_own_subagent_dir_passes(self, tmp_path: Path) -> None:
        """Genuine allow-side test for is_config_dir_leak's own-session
        carve-out: the Read path is nested inside the run's own subagent
        sidecar dir, and projects_root is a real ancestor of it, not equal
        to it. Deleting the carve-out line flips this result."""
        scenario = _load_scenario(tmp_path, "out-of-session-read")
        session_jsonl, own_subagent_dir = _own_session_paths(scenario)
        agent_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(
            agent_jsonl, "toolu_read_1", name="Read",
            input_={"file_path": str(own_subagent_dir / "a-sibling-runs-persisted-result.jsonl")},
        )
        result = runner.evaluate_run_validity(
            dispatcher_session_jsonl=session_jsonl, stream_lines=[], timed_out=False,
            expected_agent_name=AGENT_NAME, expected_inner_prompt=INNER_PROMPT, expected_model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, own_dirs=(scenario,),
            projects_root=scenario, own_session_paths=(session_jsonl, own_subagent_dir),
            live_checkout_roots=(), changed_relpaths=(),
        )
        assert result.ok is True

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
        """Same relative "." Grep path as the allow case above, but with
        live_checkout_roots naming the fixture directory itself: a campaign
        invoked from a directory that is also a live-checkout root turns
        every relative Grep into a false leak unless resolution anchors to
        fixture_dir rather than the process's own cwd."""
        scenario = _load_scenario(tmp_path, "normal-success")
        session_jsonl = scenario / "session-1" / "subagents" / "agent-1.jsonl"
        _replace_tool_use(session_jsonl, "toolu_read_1", name="Grep", input_={"path": "."})
        result = _evaluate(
            scenario, fixture_dir=scenario, live_checkout_roots=(scenario,),
            changed_relpaths=("changed_file.py",),
        )
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_LIVE_CHECKOUT_LEAK


class TestSessionStoreNotFoundThroughExecuteRun:
    def test_no_session_jsonl_found_records_session_store_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: None)
        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", subject="fix: bug", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=tmp_path, live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1", False),
        )
        record = runner.execute_run(
            ctx, arm="current-rule", run_index=0, session_id="missing-session",
            launch=lambda cmd, cwd, timeout_s: ([], False),
        )
        assert record.status == runner.STATUS_MISSING
        assert record.missing_reason == runner.VALIDITY_FAIL_SESSION_STORE_NOT_FOUND


class TestConfigDirLeakNotExemptedForSiblingSession:
    def test_read_into_a_sibling_runs_subagent_dir_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two sibling runs of one arm share a per-arm session-store parent
        directory. own_session_paths must name only this run's own
        (session_jsonl, subagent_dir). A Read that resolves
        into a *different* sibling's subagent_dir must still be
        VALIDITY_FAIL_CONFIG_DIR_LEAK, not exempted -- is_config_dir_leak's
        own docstring names this as the regression it exists to prevent.
        This drives the scenario through execute_run itself, not
        evaluate_run_validity directly, so it also pins execute_run's own
        construction of own_session_paths at its call site."""
        projects_root = tmp_path / "projects"
        shared_parent = projects_root / "some-hash"
        shared_parent.mkdir(parents=True)

        session_a_jsonl = shared_parent / "session-a.jsonl"
        shutil.copyfile(FIXTURES_DIR / "normal-success" / "session-1.jsonl", session_a_jsonl)
        # execute_run computes its own expected inner prompt via
        # runner.build_review_prompt(ctx.subject), never the raw
        # INNER_PROMPT placeholder the static fixture embeds.
        session_a_jsonl.write_text(session_a_jsonl.read_text().replace(INNER_PROMPT, runner.build_review_prompt("fix: bug")))
        session_a_subagent_dir = shared_parent / "session-a" / "subagents"
        shutil.copytree(FIXTURES_DIR / "normal-success" / "session-1" / "subagents", session_a_subagent_dir)

        # Session B: a sibling run's own (session_jsonl, subagent_dir) pair,
        # sharing shared_parent with session A but never looked up by this
        # test -- only its subagent_dir is the Read target below.
        (shared_parent / "session-b.jsonl").write_text("")
        session_b_subagent_dir = shared_parent / "session-b" / "subagents"
        session_b_subagent_dir.mkdir(parents=True)
        sibling_leaked_file = session_b_subagent_dir / "agent-1.jsonl"
        sibling_leaked_file.write_text("{}\n")

        agent_jsonl = session_a_subagent_dir / "agent-1.jsonl"
        _replace_tool_use(agent_jsonl, "toolu_read_1", name="Read", input_={"file_path": str(sibling_leaked_file)})

        monkeypatch.setattr(
            runner, "find_session_jsonl_by_id",
            lambda root, session_id: session_a_jsonl if session_id == "session-a" else None,
        )
        monkeypatch.setattr(runner, "config_dir", lambda: projects_root.parent)

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", subject="fix: bug", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=tmp_path / "fixture", live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1", False),
        )
        record = runner.execute_run(
            ctx, arm="current-rule", run_index=0, session_id="session-a",
            launch=lambda cmd, cwd, timeout_s: ([], False),
        )
        assert record.status == runner.STATUS_MISSING
        assert record.missing_reason == runner.VALIDITY_FAIL_CONFIG_DIR_LEAK


class TestFinalResultChecks:
    def test_error_result_fails(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "result-error")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.VALIDITY_FAIL_RESULT_ERROR

    def test_budget_stop_records_budget_missing_reason(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "budget-stop")
        result = _evaluate(scenario)
        assert result.ok is False
        assert result.failure_reason == runner.MISSING_REASON_BUDGET

    def test_timeout_before_anything_else_records_timeout(self, tmp_path: Path) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        result = _evaluate(scenario, timed_out=True)
        assert result.ok is False
        assert result.failure_reason == runner.MISSING_REASON_TIMEOUT


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
        # changed_file.py's own read was partial (excluded); its paged
        # follow-up carries an offset (excluded); only other_changed_file.py's
        # unpaged, non-partial read counts as a whole-file read.
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


def _patch_inner_prompt_to_match_build_review_prompt(scenario_dir: Path, subject: str) -> None:
    """execute_run() computes its own expected inner prompt via
    runner.build_review_prompt(ctx.subject), never the raw INNER_PROMPT
    placeholder the static fixtures embed -- tests that exercise
    execute_run/run_one_with_retry (rather than calling
    evaluate_run_validity directly with an explicit expected_inner_prompt)
    must patch the fixture's embedded prompt to match what that call will
    actually expect."""
    session_jsonl = scenario_dir / "session-1.jsonl"
    real_prompt = runner.build_review_prompt(subject)
    session_jsonl.write_text(session_jsonl.read_text().replace(INNER_PROMPT, real_prompt))


class TestRetryThenMissing:
    """Drives only the model-mismatch failure mode through run_one_with_retry,
    since it never branches on missing_reason (only record.status). Every
    other VALIDITY_FAIL_*/MISSING_REASON_* constant is already exercised
    directly through evaluate_run_validity elsewhere in this file."""

    def test_failing_run_is_retried_once_then_recorded_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = _load_scenario(tmp_path, "model-mismatch")
        _patch_inner_prompt_to_match_build_review_prompt(scenario, "fix: bug")
        calls = {"n": 0}

        def fake_launch(cmd, cwd, timeout_s):
            calls["n"] += 1
            return [], False

        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1", False))

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", subject="fix: bug", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=(), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1", False),
        )
        attempt = runner.run_one_with_retry(ctx, arm="current-rule", run_index=0, launch=fake_launch)

        assert calls["n"] == 2  # exactly one retry
        assert attempt.record.status == runner.STATUS_MISSING
        assert attempt.record.missing_reason == runner.VALIDITY_FAIL_MODEL_MISMATCH

    def test_succeeding_on_first_try_never_retries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario, "fix: bug")
        calls = {"n": 0}

        def fake_launch(cmd, cwd, timeout_s):
            calls["n"] += 1
            return [], False

        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1", False))

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", subject="fix: bug", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=("changed_file.py",), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1", False),
        )
        attempt = runner.run_one_with_retry(ctx, arm="current-rule", run_index=0, launch=fake_launch)

        assert calls["n"] == 1
        assert attempt.record.status == runner.STATUS_OK


class TestEnvironmentDriftReruns:
    def test_block_reruns_whole_and_replaces_first_attempt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario, "fix: bug")
        spec = runner.DefectFixtureSpec(
            defect_id="d1", subject="fix: bug", arm_fixture_dirs={"current-rule": scenario},
            arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
            live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
        )
        env_readings = iter([
            runner.EnvironmentRecord("v1", "sha1", False),  # block 1 start
            runner.EnvironmentRecord("v1", "sha2", False),  # block 1 end (drift -> rerun)
            runner.EnvironmentRecord("v1", "sha2", False),  # block 2 (rerun) start
            runner.EnvironmentRecord("v1", "sha2", False),  # block 2 (rerun) end (stable)
        ])
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: next(env_readings))
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")

        result = runner.run_defect_block(
            spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1",
            launch=lambda cmd, cwd, timeout_s: ([], False), workers=1,
        )
        assert len(result.records) == 1
        assert result.records[0].ambient_config_commit == "sha2"

    def test_block_raises_after_max_retries_against_a_persistently_flapping_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario, "fix: bug")
        spec = runner.DefectFixtureSpec(
            defect_id="d1", subject="fix: bug", arm_fixture_dirs={"current-rule": scenario},
            arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
            live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
        )
        # Every reading differs from the last -- an environment that never
        # stabilizes, unlike the drift-then-stabilize sequence above.
        shas = (f"sha{i}" for i in range(1, 100))
        monkeypatch.setattr(
            runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", next(shas), False),
        )
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")

        with pytest.raises(runner.EnvironmentDriftExceededError) as excinfo:
            runner.run_defect_block(
                spec, arms=("current-rule",), k=1, seed=1, campaign_id="c1",
                launch=lambda cmd, cwd, timeout_s: ([], False), workers=1,
            )

        # The two retried iterations' drift prints to stderr. The final,
        # fatal iteration's drift -- the one that actually triggered the
        # failure -- is folded into the exception's own message instead.
        stderr = capsys.readouterr().err
        assert stderr.count("block drifted during its run") == runner.MAX_ENVIRONMENT_DRIFT_RETRIES
        assert stderr.count("ambient_config_commit") == runner.MAX_ENVIRONMENT_DRIFT_RETRIES
        assert "ambient_config_commit" in str(excinfo.value)
        assert "cli_version" in str(excinfo.value)
        assert "dirty" in str(excinfo.value)


class TestBlockCleanupOrdering:
    def test_cleanup_runs_only_after_every_run_in_the_block(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _load_scenario(tmp_path, "normal-success")
        _patch_inner_prompt_to_match_build_review_prompt(scenario, "fix: bug")
        fixture_dir = tmp_path / "shared-fixture"
        shutil.copytree(scenario, fixture_dir)

        launched: list[int] = []

        def fake_launch(cmd, cwd, timeout_s):
            launched.append(1)
            assert fixture_dir.exists(), "cleanup must not run before every run in the block has finished"
            return [], False

        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1", False))
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: fixture_dir / "session-1.jsonl")

        spec = runner.DefectFixtureSpec(
            defect_id="d1", subject="fix: bug", arm_fixture_dirs={"current-rule": fixture_dir, "function-context": fixture_dir},
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
        _patch_inner_prompt_to_match_build_review_prompt(scenario, "fix: bug")
        monkeypatch.setattr(runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl")
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1", False))

        events: list[str] = []

        class RecordingRunStore:
            def record_directory(self, defect_id: str, directory: Path, session_id: str) -> None:
                events.append("record_directory")

        def fake_launch(cmd, cwd, timeout_s):
            events.append("launch")
            return [], False

        ctx = runner.RunContext(
            campaign_id="c1", defect_id="d1", subject="fix: bug", agent_name=AGENT_NAME, model_id=MODEL_ID,
            agent_declared_tools=DECLARED_TOOLS, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=("changed_file.py",), over_read_cap=False, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1", False),
        )
        runner.run_one_with_retry(ctx, arm="current-rule", run_index=0, launch=fake_launch, run_store=RecordingRunStore())

        assert events == ["record_directory", "launch"]


class TestRunCampaignResume:
    def test_sweeps_abandoned_entry_skips_completed_defect_reruns_pending_one_whole(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: runner.EnvironmentRecord("v1", "sha1", False))
        build_spec_calls: list[str] = []

        def build_spec(defect_id: str) -> runner.DefectFixtureSpec:
            build_spec_calls.append(defect_id)
            scenario = _load_scenario(tmp_path, "normal-success")
            _patch_inner_prompt_to_match_build_review_prompt(scenario, "fix: bug")
            monkeypatch.setattr(
                runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl",
            )
            return runner.DefectFixtureSpec(
                defect_id=defect_id, subject="fix: bug", arm_fixture_dirs={"current-rule": scenario},
                arm_agent_names={"current-rule": AGENT_NAME}, agent_declared_tools=DECLARED_TOOLS,
                live_checkout_roots=(), changed_relpaths=("changed_file.py",), over_read_cap=False,
            )

        run_store = runner.RunStore(tmp_path / "run-store")
        run_store.mark_block_complete("defect-done")
        # Simulates a hard interruption on a prior invocation, mid-run of
        # defect-pending -- its write-ahead entry names a directory that
        # must be swept before defect-pending's block reruns.
        abandoned_dir = tmp_path / "review-bench-abandoned"
        abandoned_dir.mkdir()
        run_store.record_directory("defect-pending", abandoned_dir, "abandoned-session-id")

        result = runner.run_campaign(
            ["defect-done", "defect-pending"], build_spec=build_spec, arms=("current-rule",), k=1, seed=1,
            campaign_id="c1", run_store=run_store, records_path=tmp_path / "records.jsonl",
            projects_root=tmp_path / "projects", launch=lambda cmd, cwd, timeout_s: ([], False), workers=1,
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
        marked complete with its records never written. The invariant lives
        entirely in run_campaign's own loop body, so run_defect_block is
        stubbed to return a canned BlockResult directly (the same
        substitution technique used above for read_environment_record) --
        nothing about how a block's result is produced bears on this
        ordering. Asserts the records are already on disk at the moment
        mark_block_complete runs, not just that the two calls happen in some
        order."""
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
                defect_id=defect_id, subject="fix: bug", arm_fixture_dirs={},
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
            projects_root=tmp_path / "projects", launch=lambda cmd, cwd, timeout_s: ([], False), workers=1,
        )

        assert events == ["mark_block_complete"]  # ran, and only after the assertion above held
        assert run_store.completed_block_ids() == {"defect-1"}
        assert runner.read_run_records(records_path)


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


class TestRunStoreResume:
    def test_sweeps_only_recorded_directories_and_leaves_others_untouched(self, tmp_path: Path) -> None:
        store = runner.RunStore(tmp_path / "run-store")
        recorded_dir = tmp_path / "review-bench-recorded"
        recorded_dir.mkdir()
        unrecorded_dir = tmp_path / "review-bench-unrecorded"
        unrecorded_dir.mkdir()
        store.record_directory("defect-1", recorded_dir, "session-xyz")

        projects_root = tmp_path / "projects"
        (projects_root / "hash1").mkdir(parents=True)
        (projects_root / "hash1" / "session-xyz.jsonl").write_text("{}\n")

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
        store = runner.RunStore(tmp_path / "run-store")
        recorded_dir = tmp_path / "review-bench-unlaunched"
        recorded_dir.mkdir()
        store.record_directory("defect-1", recorded_dir, runner._NO_SESSION_ID_YET)

        swept = store.sweep_abandoned(tmp_path / "projects")

        assert len(swept) == 1
        assert not recorded_dir.exists()

    def test_completed_block_is_not_swept(self, tmp_path: Path) -> None:
        store = runner.RunStore(tmp_path / "run-store")
        recorded_dir = tmp_path / "review-bench-done"
        recorded_dir.mkdir()
        store.record_directory("defect-1", recorded_dir, "session-xyz")
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


class TestRunStoreLock:
    def test_refuses_to_start_under_a_live_pid(self, tmp_path: Path) -> None:
        store = runner.RunStore(tmp_path / "run-store")
        store.acquire_lock(pid=12345, is_alive=lambda pid: True)
        with pytest.raises(runner.RunStoreLocked):
            store.acquire_lock(pid=99999, is_alive=lambda pid: True)

    def test_starts_over_a_lock_naming_a_dead_pid(self, tmp_path: Path) -> None:
        store = runner.RunStore(tmp_path / "run-store")
        store.acquire_lock(pid=12345, is_alive=lambda pid: False)
        store.acquire_lock(pid=99999, is_alive=lambda pid: False)  # does not raise
        assert store.lock_path.read_text().strip() == "99999"

    def test_concurrent_acquire_attempts_under_contention_yield_exactly_one_winner(self, tmp_path: Path) -> None:
        """A plain exists-check followed by a write is not atomic: two
        near-simultaneous callers could each observe no live lock and each
        write. os.O_EXCL makes the create+check atomic, so exactly one of
        many real concurrent threads acquires it -- every other one must
        observe a genuinely existing lock, never a false "no lock"."""
        store = runner.RunStore(tmp_path / "run-store")
        results: list[tuple[str, int]] = []

        def try_acquire(pid: int) -> None:
            try:
                store.acquire_lock(pid=pid, is_alive=lambda _pid: True)
                results.append(("acquired", pid))
            except runner.RunStoreLocked:
                results.append(("locked", pid))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(try_acquire, range(1000, 1008)))

        acquired = [r for r in results if r[0] == "acquired"]
        assert len(acquired) == 1
        assert len(results) == 8

    def test_pid_is_alive_reflects_real_process_state(self) -> None:
        proc = subprocess.Popen(["sleep", "5"])
        try:
            assert runner.pid_is_alive(proc.pid) is True
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait()
        # 20x50ms is a generous multiple of typical OS process-reap latency,
        # bounding worst-case test runtime at 1s while tolerating a loaded CI
        # runner. Empirical, not vendor-documented -- no OS guarantees a reap
        # deadline.
        for _ in range(20):
            if not runner.pid_is_alive(proc.pid):
                break
            time.sleep(0.05)
        assert runner.pid_is_alive(proc.pid) is False


class TestRunRejectsSmokeOnlyFaultInjection:
    def test_run_subcommand_has_no_inject_fault_flag(self) -> None:
        parser = run_review_bench.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "--inject-fault", "wrong-agent"])

    def test_smoke_subcommand_accepts_it(self) -> None:
        parser = run_review_bench.build_parser()
        args = parser.parse_args(["smoke", "--inject-fault", "wrong-agent"])
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
        """The no-defects test above returns before args.k/args.workers are
        read again -- this drives the one non-empty-defect-set path that
        actually consumes _run_or_smoke's None-default fill-in."""
        defect = ConfirmedDefect(
            id="defect-1", source="szz", lens="staff-backend-engineer", base_commit="a" * 40,
            head_commit="b" * 40, fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
        )
        monkeypatch.setattr(run_review_bench, "_load_defects_for_run", lambda args: [defect])

        captured_kwargs: dict = {}

        def fake_run_campaign(defect_ids, **kwargs):
            captured_kwargs.update(kwargs)
            return runner.CampaignResult(campaign_id=kwargs["campaign_id"], block_results={})

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)

        parser = run_review_bench.build_parser()
        args = parser.parse_args(
            ["run", "--run-store-dir", str(tmp_path / "run-store"), "--records-dir", str(tmp_path / "records")],
        )
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
        runner.append_run_record(path, record)
        runner.append_run_record(path, record)
        loaded = runner.read_run_records(path)
        assert len(loaded) == 2
        assert loaded[0] == record

    def test_append_run_records_writes_a_whole_batch_in_one_call(self, tmp_path: Path) -> None:
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_records(path, (record, record, record))
        assert len(runner.read_run_records(path)) == 3

    def test_append_run_records_is_a_no_op_on_an_empty_sequence(self, tmp_path: Path) -> None:
        path = tmp_path / "records.jsonl"
        runner.append_run_records(path, ())
        assert not path.exists()

    def test_read_run_records_skips_one_malformed_trailing_line_and_keeps_the_rest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Regression guard: a single truncated/malformed JSONL line -- e.g.
        a partial write from a hard kill mid-append -- must not discard every
        other already-recorded run in the same file."""
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_record(path, record)
        with open(path, "a") as fh:
            fh.write('{"defect_id": "d1", "arm": "current-rule"\n')  # truncated JSON, no closing brace

        loaded = runner.read_run_records(path)

        assert loaded == [record]
        assert "skipping malformed line 2" in capsys.readouterr().err

    def test_read_run_records_skips_a_valid_json_line_missing_a_required_field(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A line can be syntactically valid JSON and still fail to parse
        into a RunRecord -- e.g. a required field dropped by a hand-edited
        fixture or a future schema-drift line. RunRecord.from_dict raises
        TypeError in that case, not json.JSONDecodeError, so it must be
        skipped-and-reported the same way rather than crashing the read."""
        path = tmp_path / "records.jsonl"
        record = runner.RunRecord(
            campaign_id="c1", defect_id="d1", arm="current-rule", run_index=0, opaque_run_id="abc123",
            status=runner.STATUS_OK, missing_reason=None, observed_model=MODEL_ID,
            observed_tools=("Read", "Grep"), out_of_session_paths=(), findings_text="No findings.",
            wall_clock_s=12.5, read_calls=3, read_tokens_est=100, partial_view_reads=0, paged_followups=0,
            whole_file_reads_of_changed_files=1, over_read_cap=False, dispatch_prompt_verbatim=True,
            cli_version="2.1.0", ambient_config_commit="deadbeef",
        )
        runner.append_run_record(path, record)
        with open(path, "a") as fh:
            fh.write(json.dumps({"defect_id": "d2", "arm": "current-rule"}) + "\n")  # missing campaign_id, etc.

        loaded = runner.read_run_records(path)

        assert loaded == [record]
        assert "skipping malformed line 2" in capsys.readouterr().err

    def test_from_dict_defaults_missing_over_read_cap_to_false(self) -> None:
        """A reviewer.jsonl/judge.jsonl line written before over_read_cap
        existed lacks the key entirely -- from_dict must default it rather
        than raise a raw TypeError."""
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


class TestApplyFaultInjection:
    def test_none_fault_is_a_no_op(self) -> None:
        assert runner.apply_fault_injection("base prompt", fault=None) == "base prompt"

    def test_unknown_fault_raises(self) -> None:
        with pytest.raises(ValueError):
            runner.apply_fault_injection("base prompt", fault="not-a-real-fault")

    # Verifies prompt mutation only. Downstream evaluate_run_validity
    # classification (VALIDITY_FAIL_WRONG_AGENT / VALIDITY_FAIL_EXTRA_DISPATCHER_TOOL_CALL)
    # is out of scope. That check needs a live claude session, which this
    # LOCAL-ONLY, never-CI harness doesn't run offline.
    def test_known_faults_mutate_the_prompt(self) -> None:
        for fault in runner.KNOWN_SMOKE_FAULTS:
            mutated = runner.apply_fault_injection("base prompt", fault=fault)
            assert mutated != "base prompt"
            assert mutated.startswith("base prompt")
