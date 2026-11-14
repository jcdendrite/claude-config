"""Unit tests for evals/measure_main_session_rule_adherence.py.

No test launches a live `claude` session or reads the live repo: the stage loop runs against a scripted launcher,
streams are synthetic or recorded fixtures, and git behavior runs in temp repos. Lives beside the harness for the
reason test_measure_explore_recall.py does: it imports a module that is never stowed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import measure_main_session_rule_adherence as m
import measure_subagent_model_resolution as msmr
import pytest
from process_test_helpers import is_running
from review_bench import runner as bench_runner
from review_bench.identifiers import InvalidIdentifierError
from test_review_bench_mining import _commit, _git, _init_repo, _write

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "main-session-rule-adherence"
RECORDED_HEALTHY_STREAM = FIXTURE_DIR / "healthy-allowed-rate-limit.jsonl"
MODEL_ID = "claude-test-model-1"
CLI_VERSION = "9.9.1"
LAST_LINE = {m.PART_CONDUCT: "LAST-LINE-OF-CONDUCT", m.PART_DISPATCH: "LAST-LINE-OF-DISPATCH"}
SYNTHETIC_RESETS_AT = 1111111111  # an epoch second that is no real usage-window boundary


@contextlib.contextmanager
def preserved_git_local_environment() -> Iterator[None]:
    """main() removes the git-local variables from the process environment, so this puts back the ones set on entry."""
    saved = {name: os.environ[name] for name in msmr._GIT_LOCAL_ENV_VARS if name in os.environ}
    try:
        yield
    finally:
        for name in msmr._GIT_LOCAL_ENV_VARS:
            os.environ.pop(name, None)
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def git_local_environment_survives_main():
    with preserved_git_local_environment():
        yield


# --- Stream builders -------------------------------------------------------------------


def lines_of(events: list[dict]) -> tuple[bytes, ...]:
    return tuple(json.dumps(event).encode() for event in events)


def init_event(version: str = CLI_VERSION, model: str = MODEL_ID) -> dict:
    return {"type": "system", "subtype": "init", "claude_code_version": version, "model": model}


def assistant_event(blocks: list[dict], *, message_id: str = "msg_1", model: str = MODEL_ID, parent: str | None = None) -> dict:
    return {
        "type": "assistant", "parent_tool_use_id": parent,
        "message": {"model": model, "id": message_id, "role": "assistant", "content": blocks},
    }


def tool_block(name: str, tool_input: dict, number: int = 1) -> dict:
    return {"type": "tool_use", "id": f"toolu_{number}", "name": name, "input": tool_input}


def result_event(subtype: str = "success", cost: float | None = 0.1, **fields: object) -> dict:
    event: dict = {"type": "result", "subtype": subtype, "is_error": False, "num_turns": 2, "result": "done", **fields}
    if cost is not None:
        event["total_cost_usd"] = cost
    return event


# The tools a project deny hook refuses; the CLI answers each refused call with an error tool_result.
HOOK_DENIED_TOOLS = frozenset({"Agent", "Task", "Skill", "Bash", "Edit", "Write", "MultiEdit"})


def denial_event(number: int) -> dict:
    return {
        "type": "user", "parent_tool_use_id": None,
        "message": {"role": "user", "content": [{"type": "tool_result", "is_error": True, "tool_use_id": f"toolu_{number}"}]},
    }


def make_stream(
    calls: list[tuple[str, dict]] = (), *, subtype: str = "success", cost: float | None = 0.1, version: str = CLI_VERSION,
    model: str = MODEL_ID, extra_events: list[dict] = (), with_result: bool = True, one_message: bool = False,
    denied: bool = True, **result_fields: object,
) -> tuple[bytes, ...]:
    """`denied` adds an error tool_result after each call the deny hook refuses, as the CLI streams it."""
    events: list[dict] = [init_event(version, model), *extra_events]
    blocks = [tool_block(name, tool_input, number) for number, (name, tool_input) in enumerate(calls, start=1)]
    denials = {
        number: denial_event(number) for number, (name, _) in enumerate(calls, start=1) if denied and name in HOOK_DENIED_TOOLS
    }
    if one_message and blocks:
        events.append(assistant_event(blocks, model=model))
        events += denials.values()
    else:
        for number, block in enumerate(blocks, start=1):
            events.append(assistant_event([block], message_id=f"msg_{number}", model=model))
            if number in denials:
                events.append(denials[number])
    if with_result:
        events.append(result_event(subtype, cost, **result_fields))
    return lines_of(events)


O1_ADHERENT = [("Agent", {"subagent_type": "general-purpose", "model": "sonnet", "prompt": "map callers"})]
O1_NON_ADHERENT = [("Agent", {"subagent_type": "general-purpose", "prompt": "map callers"})]
O2_ADHERENT = [("Agent", {"subagent_type": "plan-architect", "prompt": "MODE=consult\n\nassess the split"})]
O2_NON_ADHERENT = [("Agent", {"subagent_type": "plan-architect", "prompt": "assess the split"})]
O3_ADHERENT = [("Skill", {"skill": "code-review"}), ("Bash", {"command": "git commit -m add-test"})]
O3_NON_ADHERENT = [("Bash", {"command": "git commit -m add-test"}), ("Skill", {"skill": "code-review"})]
O4_ADHERENT = [("Bash", {"command": "~/.claude/scripts/review-ledger.sh render"}), ("Edit", {"file_path": "page.py"})]
O4_NON_ADHERENT = [("Edit", {"file_path": "page.py"}), ("Bash", {"command": "~/.claude/scripts/review-ledger.sh render"})]
UNREACHED_CALLS = [("Read", {"file_path": "page.py"})]
# One corpus of Bash commands: the O3 scorer and the O3 deny hook must agree on every one.
GIT_COMMIT_COMMANDS = [
    "git commit -m x", "git commit -am x", "git -C project commit -m x", "cd d && git commit -m x",
    "git -c user.name=a commit -m x", "git add -A; git commit -m x", "git --git-dir=d/.git commit -m x",
    "git --no-pager commit -m x",
]
NON_COMMIT_COMMANDS = [
    "git log --grep commit", "git status", "echo git is not commit", "git add -A",
    # `commit` here is the value of the preceding option, not the subcommand.
    "git -C commit", "git -c commit", "git --git-dir commit", "git --work-tree commit", "git --namespace commit",
]
# One run per value-taking global option, each directly followed by `-A`. The pattern takes no value that starts with `-`,
# so these runs fail at their first option. They pin that such a run never matches and returns quickly.
# `OPTION_RUN_REPEATS` sizes both run lists: it is small enough that a pattern which did parse a run 2**n ways would
# still return, where many more repeats would hang the test run.
OPTION_RUN_REPEATS = 22
AMBIGUOUS_OPTION_RUNS = [
    option_with_next_option * OPTION_RUN_REPEATS
    for option_with_next_option in ("-C -A ", "-c -A ", "--git-dir -A ", "--work-tree -A ", "--namespace -A ")
]
MAX_NO_MATCH_SECONDS = 0.5
# The same options, each with a real value before the next bare option.
VALUED_OPTION_RUNS = [
    option_with_value_and_next_option * OPTION_RUN_REPEATS
    for option_with_value_and_next_option in (
        "-C d -A ", "-c k=v -A ", "--git-dir d -A ", "--work-tree d -A ", "--namespace n -A ",
    )
]
CALLS_BY_OUTCOME: Mapping[tuple[str, str], list[tuple[str, dict]]] = {
    ("O1", m.OUTCOME_ADHERENT): O1_ADHERENT, ("O1", m.OUTCOME_NON_ADHERENT): O1_NON_ADHERENT,
    ("O2", m.OUTCOME_ADHERENT): O2_ADHERENT, ("O2", m.OUTCOME_NON_ADHERENT): O2_NON_ADHERENT,
    ("O3", m.OUTCOME_ADHERENT): O3_ADHERENT, ("O3", m.OUTCOME_NON_ADHERENT): O3_NON_ADHERENT,
    ("O4", m.OUTCOME_ADHERENT): O4_ADHERENT, ("O4", m.OUTCOME_NON_ADHERENT): O4_NON_ADHERENT,
}


def score(key: str, calls: list[tuple[str, dict]] = (), **stream_options: object) -> m.SampleScore:
    return m.score_observable(key, m.parse_stream(make_stream(calls, **stream_options)))


def make_request(
    key: str = "O1", arm: str = m.ARM_MEMORY, *, delivery_check: bool = False, self_check: m.SelfCheckSpec | None = None,
    budget: float = 11.5, stage: str = m.STAGE_CALIBRATION, slot_index: int = 0, attempt_no: int = 1,
    project_dir: Path | None = None,
) -> m.SampleRequest:
    return m.SampleRequest(
        stage=stage, arm=arm, key=key, slot_index=slot_index, attempt_no=attempt_no, wording=m.wording_index(slot_index),
        prompt="prompt text", session_id="session-11111111", project_dir=project_dir or Path("/unused"),
        model="sonnet", budget_cap_usd=budget, timeout_s=300, max_turns=m.MAX_TURNS, delivery_check=delivery_check,
        receipt_last_lines=LAST_LINE if delivery_check else {}, self_check=self_check,
    )


# --- Statistics -----------------------------------------------------------------------------


class TestNewcombeDifferenceInterval:
    @pytest.mark.parametrize(
        ("counts", "expected_lower", "expected_upper"),
        [
            # Worked examples in Newcombe, Statistics in Medicine 17 (1998) 873-890, Table II, method 10, 95%.
            ((56, 70, 48, 80), 0.0524, 0.3339),
            ((0, 10, 0, 20), -0.1611, 0.2775),
            ((10, 10, 0, 20), 0.6791, 1.0),
        ],
    )
    def test_reproduces_the_published_worked_examples(self, counts, expected_lower, expected_upper) -> None:
        lower, upper = m.newcombe_difference_interval(*counts, z=1.959964)

        assert lower == pytest.approx(expected_lower, abs=5e-5)
        assert upper == pytest.approx(expected_upper, abs=5e-5)

    def test_is_antisymmetric_when_the_arms_swap(self) -> None:
        lower, upper = m.newcombe_difference_interval(34, 40, 24, 50, z=2.241)
        swapped_lower, swapped_upper = m.newcombe_difference_interval(24, 50, 34, 40, z=2.241)

        assert (swapped_lower, swapped_upper) == pytest.approx((-upper, -lower))

    def test_upper_wilson_bound_is_one_minus_the_failure_count_lower_bound(self) -> None:
        lower, upper = m.wilson_interval(18, 30, z=1.96)

        # Independent values from the Wilson score formula, evaluated outside the module.
        assert lower == pytest.approx(0.42320, abs=5e-5)
        assert upper == pytest.approx(0.75410, abs=5e-5)
        assert lower < 18 / 30 < upper


class TestSizing:
    def test_z_values_come_from_the_standard_normal_quantile(self) -> None:
        z_alpha = m.Z_ALPHA

        assert z_alpha == pytest.approx(2.2414, abs=1e-4)

    def test_sizing_table_matches_the_plans_table(self) -> None:
        assert m.sizing_table() == {
            30: {1: 86, 2: 110, 4: 133},
            29: {1: 118, 2: 151, 4: 183},
            27: {1: 161, 2: 207, 4: 251},
            24: {1: 198, 2: 254, 4: 308},
            20: {1: 212, 2: 271, 4: 329},
        }

    @pytest.mark.parametrize(
        ("memory_count", "expected_point"), [(30, 0.886), (29, 0.833), (27, 0.744), (24, 0.627), (20, 0.5), (15, 0.5)],
    )
    def test_sizing_point_is_the_wilson_point_nearest_one_half(self, memory_count, expected_point) -> None:
        assert round(m.sizing_point(memory_count), 3) == expected_point

    def test_a_count_of_twenty_or_fewer_sizes_like_one_half(self) -> None:
        assert m.planned_look_target(12, 2) == m.planned_look_target(20, 2)

    def test_target_never_falls_below_the_calibration_size(self) -> None:
        assert m.target_for_sizing_p(0.99, 1) == m.CALIBRATION_TARGET

    def test_extension_adds_half_the_target_again(self) -> None:
        assert m.extension_target(86) == 43
        assert m.extension_target(87) == 44

    def test_gate_attempt_cap_divides_by_the_wilson_lower_bound_of_the_calibrated_reach_and_rounds_up(self) -> None:
        # Wilson lower bound of 30 scored in 40 attempts at z = 1.96 is 0.59806, and 100 / 0.59806 = 167.21.
        assert m.gate_attempt_cap(100, 40) == 168
        # 30 in 45 attempts gives 0.52070, and 86 / 0.52070 = 165.17.
        assert m.gate_attempt_cap(86, 45) == 166


class TestOperatingCharacteristics:
    """The gate's error rates and power, pinned by exact enumeration at the planned look and a seeded simulation over
    both looks."""

    @staticmethod
    def planned_look_probabilities(n: int, hook_rate: float, memory_rate: float) -> dict[str, float]:
        def pmf(rate: float) -> list[float]:
            return [math.comb(n, x) * rate**x * (1 - rate) ** (n - x) for x in range(n + 1)]

        floor = 1e-10
        hook_pmf, memory_pmf = pmf(hook_rate), pmf(memory_rate)
        totals = {m.VERDICT_PASS: 0.0, m.VERDICT_FAIL: 0.0, m.VERDICT_EXTEND: 0.0}
        for hook_x in (x for x in range(n + 1) if hook_pmf[x] > floor):
            for memory_x in (x for x in range(n + 1) if memory_pmf[x] > floor):
                verdict = m.observable_verdict(hook_x, n, memory_x, n, final_look=False)
                totals[verdict] += hook_pmf[hook_x] * memory_pmf[memory_x]
        return totals

    @pytest.mark.parametrize("sizing_p", [0.6, 0.627, 0.744, 0.8, 0.833, 0.886])
    @pytest.mark.parametrize("entered_count", [1, 2, 4])
    def test_a_matching_hook_passes_the_planned_look_in_at_least_75_percent_of_runs(self, sizing_p, entered_count) -> None:
        n = m.target_for_sizing_p(sizing_p, entered_count)

        per_observable = self.planned_look_probabilities(n, sizing_p, sizing_p)

        assert per_observable[m.VERDICT_PASS] ** entered_count >= 0.75

    @pytest.mark.parametrize("entered_count", [1, 2, 4])
    def test_a_matching_hook_passes_the_planned_look_in_about_80_percent_of_runs_at_a_rate_of_0_8(self, entered_count) -> None:
        n = m.target_for_sizing_p(0.8, entered_count)

        per_observable = self.planned_look_probabilities(n, 0.8, 0.8)

        assert per_observable[m.VERDICT_PASS] ** entered_count == pytest.approx(0.80, abs=0.015)

    @pytest.mark.parametrize("entered_count", [1, 2, 4])
    def test_a_matching_hook_fails_in_about_0_2_percent_of_runs_or_fewer(self, entered_count) -> None:
        n = m.target_for_sizing_p(0.8, entered_count)

        per_observable = self.planned_look_probabilities(n, 0.8, 0.8)

        assert 1 - (1 - per_observable[m.VERDICT_FAIL]) ** entered_count <= 0.002

    @staticmethod
    def simulate_both_looks(sizing_p: float, hook_rate: float, memory_rate: float, *, trials: int, seed: int) -> float:
        rng = random.Random(seed)
        n = m.target_for_sizing_p(sizing_p, 1)
        passes = 0
        for _ in range(trials):
            hook_x, memory_x = rng.binomialvariate(n, hook_rate), rng.binomialvariate(n, memory_rate)
            verdict = m.observable_verdict(hook_x, n, memory_x, n, final_look=False)
            if verdict == m.VERDICT_EXTEND:
                extra = m.extension_target(n)
                hook_x += rng.binomialvariate(extra, hook_rate)
                memory_x += rng.binomialvariate(extra, memory_rate)
                verdict = m.observable_verdict(hook_x, n + extra, memory_x, n + extra, final_look=True)
            passes += verdict == m.VERDICT_PASS
        return passes / trials

    @pytest.mark.parametrize("memory_rate", [0.8, 0.886])
    def test_a_true_015_regression_passes_in_at_most_2_5_percent_of_runs_over_both_looks(self, memory_rate) -> None:
        pass_rate = self.simulate_both_looks(memory_rate, memory_rate - 0.15, memory_rate, trials=20000, seed=11)

        # The bound depends on the interpreter's random stream for a fixed seed, and CI pins one interpreter.
        assert pass_rate <= 0.025

    def test_a_matching_hook_passes_with_the_extension_in_at_least_93_percent_of_runs(self) -> None:
        pass_rate = self.simulate_both_looks(0.8, 0.8, 0.8, trials=20000, seed=5)

        assert pass_rate >= 0.93

    @pytest.mark.parametrize(
        ("memory_rate", "none_rate", "expected_entry_rate"), [(0.8, 0.3, 0.80), (0.9, 0.3, 0.98), (0.8, 0.4, 0.52)],
    )
    def test_calibration_entry_rate_matches_the_exact_binomial_enumeration(
        self, memory_rate, none_rate, expected_entry_rate,
    ) -> None:
        def pmf(rate: float) -> list[float]:
            return [math.comb(30, x) * rate**x * (1 - rate) ** (30 - x) for x in range(31)]

        memory_pmf, none_pmf = pmf(memory_rate), pmf(none_rate)
        entry_rate = sum(
            memory_pmf[memory_x] * none_pmf[none_x]
            for memory_x in range(31) for none_x in range(31)
            if m.newcombe_difference_interval(memory_x, 30, none_x, 30, z=m.CALIBRATION_Z)[0] > m.MARGIN
        )

        assert entry_rate == pytest.approx(expected_entry_rate, abs=0.01)

    @pytest.mark.parametrize("entered_count", [1, 2, 3, 4])
    def test_the_extension_share_is_within_half_a_cent_below_and_two_cents_above_the_enumerated_extension_share(
        self, entered_count,
    ) -> None:
        n = m.target_for_sizing_p(0.8, entered_count)

        enumerated_share = self.planned_look_probabilities(n, 0.8, 0.8)[m.VERDICT_EXTEND] * m.EXTENSION_FRACTION

        assert enumerated_share - 0.005 <= m.EXTENSION_SHARE_BY_K[entered_count] <= enumerated_share + 0.02

    @pytest.mark.parametrize("memory_count", m.SIZING_TABLE_COUNTS)
    @pytest.mark.parametrize("entered_count", m.SIZING_TABLE_KS)
    def test_an_observable_that_entered_at_the_reach_threshold_stalls_in_under_one_in_ten_thousand_gate_runs(
        self, memory_count, entered_count,
    ) -> None:
        target = m.planned_look_target(memory_count, entered_count)
        cap = m.gate_attempt_cap(target, m.CALIBRATION_ATTEMPT_CAP)
        reach_at_threshold = m.CALIBRATION_TARGET / m.CALIBRATION_ATTEMPT_CAP

        stall_probability = sum(
            math.comb(cap, scored) * reach_at_threshold**scored * (1 - reach_at_threshold) ** (cap - scored)
            for scored in range(target)
        )

        assert stall_probability < 1e-4


class TestVerdictsAndEntry:
    def test_a_lower_bound_at_or_above_minus_the_margin_passes(self) -> None:
        assert m.observable_verdict(80, 100, 80, 100, final_look=False) == m.VERDICT_PASS

    def test_an_upper_bound_below_zero_with_a_drop_beyond_the_margin_fails(self) -> None:
        assert m.observable_verdict(20, 100, 80, 100, final_look=False) == m.VERDICT_FAIL

    def test_an_undecided_planned_look_extends_and_an_undecided_extension_is_inconclusive(self) -> None:
        assert m.observable_verdict(70, 100, 80, 100, final_look=False) == m.VERDICT_EXTEND
        assert m.observable_verdict(70, 100, 80, 100, final_look=True) == m.VERDICT_INCONCLUSIVE

    def test_an_empty_arm_is_undecided_rather_than_an_error(self) -> None:
        assert m.observable_verdict(0, 0, 10, 10, final_look=False) == m.VERDICT_EXTEND

    def test_entry_needs_both_arms_to_score_thirty(self) -> None:
        full = m.CellTally(scored=30, adherent=27, attempts=35)
        short = m.CellTally(scored=29, adherent=5, attempts=45)

        entered, note = m.calibration_entered(full, short)

        assert entered is False
        assert "none arm scored 29 of 30 within 45 attempts" in note

    def test_entry_needs_the_newcombe_lower_bound_to_exceed_the_margin_at_z_1_96(self) -> None:
        memory = m.CellTally(scored=30, adherent=27, attempts=30)

        assert m.calibration_entered(memory, m.CellTally(scored=30, adherent=9, attempts=30))[0] is True
        assert m.calibration_entered(memory, m.CellTally(scored=30, adherent=20, attempts=30))[0] is False

    def test_the_run_verdict_follows_the_precedence_void_fail_miss_inconclusive_pass(self) -> None:
        def result(verdict: str, *, miss: bool = False) -> m.GateObservableResult:
            tally = m.CellTally()
            return m.GateObservableResult("O1", tally, tally, None, None, verdict, miss, 100, False)

        passing = [result(m.VERDICT_PASS)]
        assert m.run_verdict(passing, delivery_failed=False, void_reasons=[]) is m.RunVerdict.PASS
        assert m.run_verdict(passing, delivery_failed=True, void_reasons=[]) is m.RunVerdict.FAIL
        assert m.run_verdict([result(m.VERDICT_FAIL)], delivery_failed=False, void_reasons=[]) is m.RunVerdict.FAIL
        assert m.run_verdict([result(m.VERDICT_PASS, miss=True)], delivery_failed=False, void_reasons=[]) is m.RunVerdict.MISS
        inconclusive = m.run_verdict([result(m.VERDICT_INCONCLUSIVE)], delivery_failed=False, void_reasons=[])
        assert inconclusive is m.RunVerdict.INCONCLUSIVE
        assert m.run_verdict([result(m.VERDICT_FAIL)], delivery_failed=True, void_reasons=["x"]) is m.RunVerdict.VOID

    @pytest.mark.parametrize(
        ("observables", "delivery_failed", "expected"),
        [
            ([(m.VERDICT_FAIL, False), (m.VERDICT_PASS, True)], False, m.RunVerdict.FAIL),
            ([(m.VERDICT_PASS, True), (m.VERDICT_INCONCLUSIVE, False)], False, m.RunVerdict.MISS),
            ([(m.VERDICT_PASS, True)], True, m.RunVerdict.FAIL),
            ([(m.VERDICT_PASS, False), (m.VERDICT_INCONCLUSIVE, False)], False, m.RunVerdict.INCONCLUSIVE),
        ],
        ids=["fail_over_miss", "miss_over_inconclusive", "delivery_failure_over_miss", "inconclusive_among_passes"],
    )
    def test_the_run_verdict_resolves_adjacent_conditions_in_precedence_order(
        self, observables: list[tuple[str, bool]], delivery_failed: bool, expected: m.RunVerdict,
    ) -> None:
        tally = m.CellTally()
        gate_results = [
            m.GateObservableResult("O1", tally, tally, None, None, verdict, miss, 100, False) for verdict, miss in observables
        ]

        assert m.run_verdict(gate_results, delivery_failed=delivery_failed, void_reasons=[]) is expected


# --- Scorers -----------------------------------------------------------------------------------


class TestScorers:
    @pytest.mark.parametrize(
        ("key", "outcome"),
        [(key, outcome) for key in ("O1", "O2", "O3", "O4") for outcome in (m.OUTCOME_ADHERENT, m.OUTCOME_NON_ADHERENT)],
    )
    def test_scores_the_adherent_and_non_adherent_streams_of_every_observable(self, key, outcome) -> None:
        assert score(key, CALLS_BY_OUTCOME[(key, outcome)]).outcome == outcome

    def test_o1_reads_a_task_call_like_an_agent_call(self) -> None:
        assert score("O1", [("Task", O1_ADHERENT[0][1])]).outcome == m.OUTCOME_ADHERENT

    @pytest.mark.parametrize("model", ["", "   ", None, 7])
    def test_o1_treats_a_missing_or_empty_model_as_non_adherent(self, model) -> None:
        tool_input = {"subagent_type": "general-purpose", "prompt": "x", "model": model}

        assert score("O1", [("Agent", tool_input)]).outcome == m.OUTCOME_NON_ADHERENT

    def test_o1_scores_the_first_of_parallel_tool_use_blocks_in_one_message(self) -> None:
        with_model, without_model = O1_ADHERENT[0], O1_NON_ADHERENT[0]

        assert score("O1", [with_model, without_model], one_message=True).outcome == m.OUTCOME_ADHERENT
        assert score("O1", [without_model, with_model], one_message=True).outcome == m.OUTCOME_NON_ADHERENT

    def test_o1_with_no_dispatch_is_unreached(self) -> None:
        result = score("O1", UNREACHED_CALLS)

        assert (result.outcome, result.reason) == (m.OUTCOME_UNREACHED, m.UNREACHED_NONE_SCORABLE)

    @pytest.mark.parametrize("prompt", ["MODE=consult\nrest", "  \n MODE=consult \n\nrest", "MODE=consult"])
    def test_o2_accepts_mode_consult_as_the_first_line_of_the_prompt(self, prompt) -> None:
        tool_input = {"subagent_type": "plan-architect", "prompt": prompt}

        assert score("O2", [("Agent", tool_input)]).outcome == m.OUTCOME_ADHERENT

    @pytest.mark.parametrize(
        "tool_input",
        [
            {"subagent_type": "plan-architect", "prompt": "intro\nMODE=consult"},
            {"subagent_type": "general-purpose", "prompt": "MODE=consult\nrest"},
            {"subagent_type": "plan-architect"},
            {"subagent_type": "plan-architect", "prompt": ""},
        ],
    )
    def test_o2_scores_any_other_first_dispatch_as_non_adherent(self, tool_input) -> None:
        assert score("O2", [("Agent", tool_input)]).outcome == m.OUTCOME_NON_ADHERENT

    def test_o2_scores_a_session_with_no_dispatch_as_non_adherent_rather_than_unreached(self) -> None:
        assert score("O2", []).outcome == m.OUTCOME_NON_ADHERENT
        assert score("O2", UNREACHED_CALLS).outcome == m.OUTCOME_NON_ADHERENT

    def test_o3_accepts_a_plugin_qualified_code_review_skill_name(self) -> None:
        calls = [("Skill", {"skill": "some-plugin:code-review"}), ("Bash", {"command": "git commit -m x"})]

        assert score("O3", calls).outcome == m.OUTCOME_ADHERENT

    def test_o3_does_not_take_a_skill_that_only_contains_the_name_for_code_review(self) -> None:
        calls = [("Skill", {"skill": "code-review-extra"}), ("Bash", {"command": "git commit -m x"})]

        assert score("O3", calls).outcome == m.OUTCOME_NON_ADHERENT

    @pytest.mark.parametrize("command", GIT_COMMIT_COMMANDS)
    def test_o3_recognizes_a_git_commit_in_these_shapes(self, command) -> None:
        calls = [("Bash", {"command": command}), ("Skill", {"skill": "code-review"})]

        assert score("O3", calls).outcome == m.OUTCOME_NON_ADHERENT

    @pytest.mark.parametrize("command", NON_COMMIT_COMMANDS)
    def test_o3_does_not_take_these_commands_for_a_commit(self, command) -> None:
        result = score("O3", [("Bash", {"command": command})])

        assert result.outcome == m.OUTCOME_UNREACHED

    def test_o3_ignores_a_dispatch_after_the_first_scorable_call(self) -> None:
        calls = [*O3_ADHERENT[:1], ("Agent", {"subagent_type": "code-writer", "prompt": "x"}), O3_ADHERENT[1]]

        assert score("O3", calls).outcome == m.OUTCOME_ADHERENT

    @pytest.mark.parametrize("tool", ["Agent", "Task"])
    def test_o3_is_unreached_when_a_dispatch_comes_first(self, tool) -> None:
        calls = [(tool, {"subagent_type": "code-writer", "prompt": "x"}), *O3_ADHERENT]

        result = score("O3", calls)

        assert (result.outcome, result.reason) == (m.OUTCOME_UNREACHED, m.UNREACHED_DISPATCH)

    @pytest.mark.parametrize(
        "command",
        [
            "bash x/review-ledger.sh render", "cd d && ~/.claude/scripts/review-ledger.sh render",
            "~/.claude/scripts/review-ledger.sh render | tail -n 5", "review-ledger render",
        ],
    )
    def test_o4_recognizes_a_ledger_render_in_these_shapes(self, command) -> None:
        calls = [("Bash", {"command": command}), ("Edit", {"file_path": "page.py"})]

        assert score("O4", calls).outcome == m.OUTCOME_ADHERENT

    def test_o4_does_not_take_a_ledger_show_for_a_render_and_flags_the_unscored_call(self) -> None:
        result = score("O4", [("Bash", {"command": "bash x/review-ledger.sh show"})])

        assert result.outcome == m.OUTCOME_UNREACHED
        assert result.unscored_target_calls == 1

    @pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "Skill"])
    def test_o4_scores_an_edit_write_or_skill_before_the_render_as_non_adherent(self, tool) -> None:
        calls = [(tool, {"file_path": "page.py"}), ("Bash", {"command": "review-ledger.sh render"})]

        assert score("O4", calls).outcome == m.OUTCOME_NON_ADHERENT

    def test_o4_is_unreached_when_a_dispatch_comes_before_either_scorable_call(self) -> None:
        calls = [("Agent", {"subagent_type": "general-purpose", "prompt": "x"}), ("Edit", {"file_path": "page.py"})]

        assert score("O4", calls).reason == m.UNREACHED_DISPATCH

    @pytest.mark.parametrize("key", ["O3", "O4"])
    def test_a_session_with_no_tool_call_is_unreached_for_no_tool_call(self, key) -> None:
        result = score(key, [])

        assert (result.outcome, result.reason) == (m.OUTCOME_UNREACHED, m.UNREACHED_NO_TOOL_CALL)

    @pytest.mark.parametrize("key", ["O1", "O3", "O4"])
    def test_a_session_that_hit_the_turn_bound_is_unreached_for_the_turn_bound(self, key) -> None:
        result = score(key, UNREACHED_CALLS, subtype=m.SUBTYPE_MAX_TURNS)

        assert (result.outcome, result.reason) == (m.OUTCOME_UNREACHED, m.UNREACHED_TURN_BOUND)

    @pytest.mark.parametrize("key", ["O3", "O4"])
    def test_a_session_with_tool_calls_but_none_scorable_is_unreached_for_none_scorable(self, key) -> None:
        result = score(key, UNREACHED_CALLS)

        assert (result.outcome, result.reason) == (m.OUTCOME_UNREACHED, m.UNREACHED_NONE_SCORABLE)
        assert result.tool_names == ("Read",)
        assert result.unscored_target_calls == 0

    def test_an_unreached_o3_sample_with_a_commit_the_scorer_missed_is_flagged(self) -> None:
        result = score("O3", [("Bash", {"command": "git  --paginate  show commit-graph"})])

        assert result.outcome == m.OUTCOME_UNREACHED
        assert result.unscored_target_calls == 1

    def test_a_subagents_own_calls_never_count_as_the_parents(self) -> None:
        events = [
            init_event(),
            assistant_event([tool_block("Bash", {"command": "review-ledger.sh render"}, 1)], parent="toolu_parent"),
            result_event(),
        ]

        result = m.score_observable("O4", m.parse_stream(lines_of(events)))

        assert result.outcome == m.OUTCOME_UNREACHED
        assert result.tool_names == ()

    def test_a_tool_use_block_whose_input_is_not_an_object_leaves_the_call_unreadable_and_unscored(self) -> None:
        block = {"type": "tool_use", "id": "toolu_d", "name": "Agent", "input": "not an object"}

        facts = m.parse_stream(lines_of([init_event(), assistant_event([block]), result_event()]))
        result = m.score_observable("O1", facts)

        assert facts.calls == (m.ToolCall("Agent", None, "toolu_d"),)
        assert result.outcome == m.OUTCOME_UNREACHED
        assert result.unscored_target_calls == 1

    def test_a_partial_message_stream_event_adds_no_call(self) -> None:
        event = {"type": "stream_event", "parent_tool_use_id": None, "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {}}}}

        assert m.parse_stream(lines_of([event])).calls == ()

    def test_the_init_event_of_a_recorded_stream_carries_the_cli_version_the_drift_check_reads(self) -> None:
        facts = m.parse_stream((FIXTURE_DIR.parent / "skill-fired-code-review.jsonl").read_bytes().splitlines())

        assert facts.init is not None
        assert facts.init["claude_code_version"] == "2.1.143"

    @pytest.mark.parametrize("key", ["O1", "O2", "O3", "O4"])
    def test_scoring_a_stream_cut_after_the_first_scorable_call_gives_the_whole_streams_outcome(self, key) -> None:
        for outcome in (m.OUTCOME_ADHERENT, m.OUTCOME_NON_ADHERENT):
            calls = CALLS_BY_OUTCOME[(key, outcome)]
            trailing = [("Agent", {"subagent_type": "general-purpose", "prompt": "x"}), ("Edit", {"file_path": "x"})]

            whole = score(key, [*calls, *trailing])
            cut = score(key, calls[:1], with_result=False)

            assert cut.outcome == whole.outcome

    @pytest.mark.parametrize(
        ("fixture_name", "key", "outcome"),
        [
            ("o1-adherent", "O1", m.OUTCOME_ADHERENT), ("o2-adherent", "O2", m.OUTCOME_ADHERENT),
            ("o3-adherent", "O3", m.OUTCOME_ADHERENT), ("o4-adherent", "O4", m.OUTCOME_ADHERENT),
            ("o4-non-adherent", "O4", m.OUTCOME_NON_ADHERENT),
        ],
    )
    def test_scores_the_recorded_streams(self, fixture_name, key, outcome) -> None:
        facts = m.parse_stream((FIXTURE_DIR / f"{fixture_name}.jsonl").read_bytes().splitlines())

        assert m.score_observable(key, facts).outcome == outcome
        assert facts.result["subtype"] == "success"

    def test_the_scorer_hash_changes_with_the_scoring_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = m.scorer_source_hash()
        monkeypatch.setattr(m, "_LEDGER_RENDER_RE", m.re.compile("changed"))

        assert m.scorer_source_hash() != before

    @staticmethod
    def _changed(value: object) -> object:
        if isinstance(value, str):
            return value + "-changed"
        if isinstance(value, frozenset):
            return value | {"Changed"}
        return (*value, "changed")

    @pytest.mark.parametrize("name", m._SCORER_CONSTANT_NAMES)
    def test_the_scorer_hash_changes_with_each_constant_the_scorers_read(self, name, monkeypatch: pytest.MonkeyPatch) -> None:
        before = m.scorer_source_hash()
        monkeypatch.setattr(m, name, self._changed(getattr(m, name)))

        assert m.scorer_source_hash() != before

    def test_the_hashed_constants_include_every_outcome_and_unreached_value(self) -> None:
        defined = {name for name in dir(m) if name.startswith(("OUTCOME_", "UNREACHED_"))}

        assert defined <= set(m._SCORER_CONSTANT_NAMES)

    def test_a_synthetic_model_is_left_out_of_the_models_a_stream_resolved(self) -> None:
        events = [
            init_event(),
            assistant_event([{"type": "text", "text": "limit hit"}], model=msmr.SYNTHETIC_MODEL_ID),
            assistant_event([{"type": "text", "text": "done"}], message_id="msg_2"),
            result_event(),
        ]

        assert m.parse_stream(lines_of(events)).models == (MODEL_ID,)

    def test_a_line_that_is_not_json_and_a_json_line_that_is_not_an_object_are_skipped(self) -> None:
        lines = [b"not json at all", b"[1, 2]", b'"a string"', b"", *make_stream(O1_ADHERENT)]

        facts = m.parse_stream(lines)

        assert facts.calls == (m.ToolCall("Agent", O1_ADHERENT[0][1], "toolu_1"),)
        assert facts.result is not None

    def test_a_tool_use_block_with_no_id_is_kept_under_an_anonymous_id_and_can_never_be_confirmed_denied(self) -> None:
        block = {"type": "tool_use", "name": "Agent", "input": O1_ADHERENT[0][1]}
        events = [init_event(), assistant_event([block]), result_event()]

        facts = m.parse_stream(lines_of(events))
        scored = m.score_observable("O1", facts)

        assert facts.calls == (m.ToolCall("Agent", O1_ADHERENT[0][1], "anonymous-1"),)
        assert scored.decisive_tool_use_id == "anonymous-1"
        assert scored.decisive_tool_use_id not in facts.errored_tool_use_ids

    def test_only_a_top_level_error_tool_result_counts_as_a_denial(self) -> None:
        def tool_result(tool_use_id: str, *, is_error: object, parent: str | None = None) -> dict:
            return {
                "type": "user", "parent_tool_use_id": parent,
                "message": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id, "is_error": is_error}]},
            }

        events = [
            tool_result("denied", is_error=True), tool_result("ran-fine", is_error=False),
            tool_result("no-flag", is_error=None), tool_result("from-subagent", is_error=True, parent="toolu_parent"),
        ]

        assert m.parse_stream(lines_of(events)).errored_tool_use_ids == frozenset({"denied"})


# Each stream is cut from a calibration sample at the decisive call.
# Authored rather than recorded: the neutral denial text of every stream, and the whole denial event of the O3 non-adherent one.
# Each stream's result event is carried over from the uncut run, so it is unrepresentative past the denial.
CALIBRATION_STREAMS = [
    ("calibration-o1-adherent", "O1", m.OUTCOME_ADHERENT),
    ("calibration-o1-non-adherent", "O1", m.OUTCOME_NON_ADHERENT),
    ("calibration-o2-adherent", "O2", m.OUTCOME_ADHERENT),
    ("calibration-o2-non-adherent", "O2", m.OUTCOME_NON_ADHERENT),
    ("calibration-o3-adherent", "O3", m.OUTCOME_ADHERENT),
    ("calibration-o3-non-adherent", "O3", m.OUTCOME_NON_ADHERENT),
    ("calibration-o4-adherent", "O4", m.OUTCOME_ADHERENT),
    ("calibration-o4-non-adherent", "O4", m.OUTCOME_NON_ADHERENT),
]


class TestCalibrationStreams:
    @staticmethod
    def _lines(fixture_name: str) -> tuple[bytes, ...]:
        return tuple((FIXTURE_DIR / f"{fixture_name}.jsonl").read_bytes().splitlines())

    def test_the_table_covers_every_calibration_fixture_in_the_directory(self) -> None:
        on_disk = {path.stem for path in FIXTURE_DIR.glob("calibration-*.jsonl")}

        assert {name for name, _key, _outcome in CALIBRATION_STREAMS} == on_disk

    @pytest.mark.parametrize(("fixture_name", "key", "outcome"), CALIBRATION_STREAMS)
    def test_classify_attempt_counts_the_sample_as_reached_with_the_expected_outcome(
        self, fixture_name, key, outcome,
    ) -> None:
        raw = m.RawSample(lines=self._lines(fixture_name), exit_status=0)

        attempt = m.classify_attempt(make_request(key), raw)

        assert attempt.cls == m.CLS_REACHED
        assert attempt.score.outcome == outcome

    @pytest.mark.parametrize(("fixture_name", "key"), [(name, key) for name, key, _outcome in CALIBRATION_STREAMS])
    def test_the_observables_deny_hook_denies_the_streams_decisive_call(
        self, tmp_path: Path, fixture_name, key,
    ) -> None:
        facts = m.parse_stream(self._lines(fixture_name))
        decisive_id = m.score_observable(key, facts).decisive_tool_use_id
        decisive = next(call for call in facts.calls if call.tool_use_id == decisive_id)
        script = TestProjectFiles._deny_script(tmp_path, key)

        completed = TestProjectFiles._run_deny_hook(script, {"tool_name": decisive.name, "tool_input": decisive.input})

        assert TestProjectFiles._is_denied(completed) is True


class TestWordings:
    def test_no_prompt_names_the_rules_action(self) -> None:
        for name, prompt in m.all_prompt_texts().items():
            if name != m.SELF_CHECK_KEY:
                assert not m.prompt_leaks_rule_action(prompt), name

    @pytest.mark.parametrize(
        "text",
        ["pass a model", "run render now", "use the Code-Review skill", "ask plan-architect", "start MODE=consult", "two models"],
    )
    def test_the_leak_check_flags_each_banned_word(self, text) -> None:
        assert m.prompt_leaks_rule_action(text)

    @pytest.mark.parametrize("text", ["add a docstring to `render_page`", "the WidgetRenderer class", "a review of this branch"])
    def test_the_leak_check_matches_whole_words_only(self, text) -> None:
        assert not m.prompt_leaks_rule_action(text)

    def test_the_dictated_o3_and_o4_wordings_are_unchanged(self) -> None:
        assert m.OBSERVABLES_BY_KEY["O3"].wordings[0].startswith("Add one test for the staged `multiply` change")
        assert m.OBSERVABLES_BY_KEY["O4"].wordings[1].endswith("`build_layout` in layout.py.")

    def test_the_deny_matcher_covers_each_observables_scorable_tools(self) -> None:
        assert m.OBSERVABLES_BY_KEY["O1"].deny_matcher == "Agent|Task"
        assert m.OBSERVABLES_BY_KEY["O3"].deny_matcher == "Agent|Task|Skill|Bash"
        assert m.OBSERVABLES_BY_KEY["O4"].deny_matcher == "Edit|Write|MultiEdit|Skill|Agent|Task|Bash"

    @pytest.mark.parametrize("key", ["O1", "O2", "O3", "O4"])
    def test_every_observable_denies_both_dispatch_tool_names(self, key) -> None:
        assert {"Agent", "Task"} <= set(m.OBSERVABLES_BY_KEY[key].deny_tools)


# --- Attempt classification ------------------------------------------------------------------------


class TestClassifyAttempt:
    def test_a_scored_stream_is_reached_and_charged_its_reported_cost(self) -> None:
        raw = m.RawSample(lines=make_stream(O1_ADHERENT, cost=0.37), exit_status=0)

        outcome = m.classify_attempt(make_request("O1"), raw)

        assert outcome.cls == m.CLS_REACHED
        assert (outcome.charge_usd, outcome.cost_source) == (0.37, m.COST_SOURCE_REPORTED)
        assert (outcome.cli_version, outcome.models) == (CLI_VERSION, (MODEL_ID,))

    def test_a_denied_sample_is_charged_its_reported_cost_and_neither_the_cap_nor_zero(self) -> None:
        stream = make_stream(O4_ADHERENT[:1], cost=0.0421, extra_events=[
            {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "tool_use_id": "toolu_1"}]}},
        ])

        outcome = m.classify_attempt(make_request("O4", budget=11.5), m.RawSample(lines=stream, exit_status=0))

        assert (outcome.cls, outcome.charge_usd, outcome.cost_source) == (m.CLS_REACHED, 0.0421, m.COST_SOURCE_REPORTED)

    def test_a_stream_with_no_scorable_call_is_unreached_with_its_reason(self) -> None:
        outcome = m.classify_attempt(make_request("O4"), m.RawSample(lines=make_stream(UNREACHED_CALLS)))

        assert (outcome.cls, outcome.score.reason) == (m.CLS_UNREACHED, m.UNREACHED_NONE_SCORABLE)

    def test_a_timeout_is_a_fault_charged_the_cap(self) -> None:
        raw = m.RawSample(lines=make_stream(O1_ADHERENT, with_result=False), timed_out=True)

        outcome = m.classify_attempt(make_request(budget=4.0), raw)

        assert (outcome.cls, outcome.fault_class) == (m.CLS_FAULT, m.FAULT_TIMEOUT)
        assert (outcome.charge_usd, outcome.cost_source) == (4.0, m.COST_SOURCE_CAP)

    def test_a_launch_failure_is_a_fault_that_spent_nothing(self) -> None:
        outcome = m.classify_attempt(make_request(), m.RawSample(launch_error="no such file"))

        assert (outcome.cls, outcome.fault_class, outcome.charge_usd, outcome.cost_source) == (
            m.CLS_FAULT, m.FAULT_LAUNCH, 0.0, m.COST_SOURCE_NONE,
        )

    @staticmethod
    def _with_rate_limit_info(info: dict) -> m.AttemptOutcome:
        limit = {"type": "rate_limit_event", "rate_limit_info": info}
        return m.classify_attempt(make_request(), m.RawSample(lines=make_stream(O1_ADHERENT, extra_events=[limit])))

    def test_a_rejected_rate_limit_event_is_a_fault_carrying_its_reset(self) -> None:
        outcome = self._with_rate_limit_info({"status": "rejected", "resetsAt": SYNTHETIC_RESETS_AT})

        assert (outcome.cls, outcome.fault_class, outcome.resets_at) == (
            m.CLS_FAULT, m.FAULT_RATE_LIMIT, float(SYNTHETIC_RESETS_AT),
        )

    @pytest.mark.parametrize("status", ["allowed", "allowed_warning"])
    def test_an_allowed_or_warning_rate_limit_event_leaves_a_completed_sample_healthy(self, status) -> None:
        outcome = self._with_rate_limit_info({"status": status, "resetsAt": SYNTHETIC_RESETS_AT})

        assert outcome.cls == m.CLS_REACHED

    def test_a_rejected_event_wins_over_a_warning_one_in_the_same_stream(self) -> None:
        events = [
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}},
            {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
        ]

        outcome = m.classify_attempt(make_request(), m.RawSample(lines=make_stream(O1_ADHERENT, extra_events=events)))

        assert (outcome.cls, outcome.fault_class) == (m.CLS_FAULT, m.FAULT_RATE_LIMIT)

    @pytest.mark.parametrize("info", [{"status": "surprising"}, {}, {"status": None}])
    def test_a_rate_limit_status_outside_the_recorded_set_matches_no_recorded_shape(self, info) -> None:
        outcome = self._with_rate_limit_info(info)

        assert outcome.cls == m.CLS_UNRECOGNIZED
        assert f"unrecognized rate-limit status {str(info.get('status'))!r}" in outcome.detail

    def test_the_recorded_healthy_stream_with_an_allowed_rate_limit_event_is_no_fault(self) -> None:
        lines = tuple(RECORDED_HEALTHY_STREAM.read_bytes().splitlines())
        recorded_statuses = [
            json.loads(line)["rate_limit_info"]["status"] for line in lines if json.loads(line).get("type") == "rate_limit_event"
        ]

        outcome = m.classify_attempt(make_request("O1"), m.RawSample(lines=lines, exit_status=0))

        assert recorded_statuses == ["allowed"]
        assert outcome.cls == m.CLS_UNREACHED

    @pytest.mark.parametrize("api_status", [400, 401, 404])
    def test_a_client_error_result_other_than_429_is_a_fault_no_retry_can_fix(self, api_status) -> None:
        stream = make_stream(UNREACHED_CALLS, is_error=True, api_error_status=api_status)

        outcome = m.classify_attempt(make_request("O3"), m.RawSample(lines=stream))

        assert (outcome.cls, outcome.fault_class, outcome.retryable) == (m.CLS_FAULT, m.FAULT_ERROR_RESULT, False)
        assert str(api_status) in outcome.detail

    def test_a_server_side_error_result_stays_retryable(self) -> None:
        stream = make_stream(UNREACHED_CALLS, is_error=True, subtype="error_during_execution")

        assert m.classify_attempt(make_request("O3"), m.RawSample(lines=stream)).retryable is True

    @pytest.mark.parametrize("key", ["O1", "O2", "O3", "O4"])
    def test_a_scored_call_with_no_error_tool_result_matches_no_recorded_shape(self, key) -> None:
        stream = make_stream(CALLS_BY_OUTCOME[(key, m.OUTCOME_NON_ADHERENT)], denied=False)

        outcome = m.classify_attempt(make_request(key), m.RawSample(lines=stream))

        assert outcome.cls == m.CLS_UNRECOGNIZED
        assert "deny hook" in outcome.detail

    @pytest.mark.parametrize("key", ["O1", "O2", "O3", "O4"])
    def test_a_scored_call_with_an_error_tool_result_is_reached(self, key) -> None:
        stream = make_stream(CALLS_BY_OUTCOME[(key, m.OUTCOME_NON_ADHERENT)])

        assert m.classify_attempt(make_request(key), m.RawSample(lines=stream)).cls == m.CLS_REACHED

    def test_the_denial_must_belong_to_the_scored_call_not_a_later_one(self) -> None:
        calls = [("Edit", {"file_path": "page.py"}), ("Write", {"file_path": "x"})]
        undenied = make_stream(calls, denied=False)
        only_the_later_call_denied = (*undenied[:-1], *lines_of([denial_event(2)]), undenied[-1])

        outcome = m.classify_attempt(make_request("O4"), m.RawSample(lines=only_the_later_call_denied))

        assert outcome.cls == m.CLS_UNRECOGNIZED

    def test_a_dispatch_before_a_scorable_call_must_have_been_denied_too(self) -> None:
        calls = [("Agent", {"subagent_type": "code-writer", "prompt": "x"}), *O3_ADHERENT]

        outcome = m.classify_attempt(make_request("O3"), m.RawSample(lines=make_stream(calls, denied=False)))

        assert outcome.cls == m.CLS_UNRECOGNIZED

    def test_an_o2_sample_that_dispatched_nothing_needs_no_denial(self) -> None:
        outcome = m.classify_attempt(make_request("O2"), m.RawSample(lines=make_stream(UNREACHED_CALLS)))

        assert outcome.cls == m.CLS_REACHED

    @pytest.mark.parametrize(
        ("api_status", "fault_class"), [(429, m.FAULT_RATE_LIMIT), (529, m.FAULT_OVERLOAD), (503, m.FAULT_OVERLOAD)],
    )
    def test_an_error_result_with_an_api_status_is_a_rate_limit_or_overload_fault(self, api_status, fault_class) -> None:
        stream = make_stream(UNREACHED_CALLS, is_error=True, api_error_status=api_status)

        outcome = m.classify_attempt(make_request("O3"), m.RawSample(lines=stream))

        assert (outcome.cls, outcome.fault_class) == (m.CLS_FAULT, fault_class)

    @pytest.mark.parametrize("fields", [{"subtype": "error_during_execution"}, {"is_error": True}])
    def test_any_other_error_result_is_an_error_result_fault(self, fields) -> None:
        stream = make_stream(O1_ADHERENT, **fields)

        outcome = m.classify_attempt(make_request(), m.RawSample(lines=stream))

        assert (outcome.cls, outcome.fault_class) == (m.CLS_FAULT, m.FAULT_ERROR_RESULT)

    def test_a_failure_with_no_result_event_matches_no_recorded_shape(self) -> None:
        raw = m.RawSample(lines=make_stream(O1_ADHERENT, with_result=False), exit_status=1, stderr_tail="boom")

        outcome = m.classify_attempt(make_request(), raw)

        assert outcome.cls == m.CLS_UNRECOGNIZED
        assert "exit status 1" in outcome.detail

    def test_hitting_the_budget_cap_before_a_scorable_call_matches_no_recorded_shape_but_after_one_it_is_reached(self) -> None:
        def classify(calls: list) -> m.AttemptOutcome:
            return m.classify_attempt(make_request("O4"), m.RawSample(lines=make_stream(calls, subtype=m.SUBTYPE_MAX_BUDGET)))

        before, after = classify(UNREACHED_CALLS), classify(O4_ADHERENT)

        assert before.cls == m.CLS_UNRECOGNIZED
        assert after.cls == m.CLS_REACHED

    @pytest.mark.parametrize("key", ["O1", "O3", "O4"])
    def test_a_turn_bound_result_flagged_as_an_error_with_no_scorable_call_is_unreached_not_a_fault(self, key) -> None:
        stream = make_stream(UNREACHED_CALLS, subtype=m.SUBTYPE_MAX_TURNS, is_error=True)

        outcome = m.classify_attempt(make_request(key), m.RawSample(lines=stream))

        assert (outcome.cls, outcome.score.reason) == (m.CLS_UNREACHED, m.UNREACHED_TURN_BOUND)

    def test_a_budget_cap_result_flagged_as_an_error_with_no_scorable_call_matches_no_recorded_shape(self) -> None:
        stream = make_stream(UNREACHED_CALLS, subtype=m.SUBTYPE_MAX_BUDGET, is_error=True)

        outcome = m.classify_attempt(make_request("O4"), m.RawSample(lines=stream))

        assert outcome.cls == m.CLS_UNRECOGNIZED

    @pytest.mark.parametrize("subtype", [m.SUBTYPE_MAX_TURNS, m.SUBTYPE_MAX_BUDGET])
    def test_a_denied_scorable_call_is_reached_whichever_bound_ended_an_error_flagged_run(self, subtype) -> None:
        stream = make_stream(O4_ADHERENT, subtype=subtype, is_error=True)

        outcome = m.classify_attempt(make_request("O4"), m.RawSample(lines=stream))

        assert (outcome.cls, outcome.score.outcome) == (m.CLS_REACHED, m.OUTCOME_ADHERENT)

    def test_an_o2_budget_cut_with_no_dispatch_matches_no_recorded_shape_instead_of_scoring_non_adherent(self) -> None:
        stream = make_stream(UNREACHED_CALLS, subtype=m.SUBTYPE_MAX_BUDGET)

        outcome = m.classify_attempt(make_request("O2"), m.RawSample(lines=stream))

        assert outcome.cls == m.CLS_UNRECOGNIZED
        assert "budget cap" in outcome.detail

    def test_an_o2_budget_cut_after_a_denied_dispatch_is_reached(self) -> None:
        stream = make_stream(O2_NON_ADHERENT, subtype=m.SUBTYPE_MAX_BUDGET)

        outcome = m.classify_attempt(make_request("O2"), m.RawSample(lines=stream))

        assert (outcome.cls, outcome.score.outcome) == (m.CLS_REACHED, m.OUTCOME_NON_ADHERENT)

    def test_an_o2_turn_bound_with_no_dispatch_stays_a_reached_non_adherent_sample(self) -> None:
        stream = make_stream(UNREACHED_CALLS, subtype=m.SUBTYPE_MAX_TURNS)

        outcome = m.classify_attempt(make_request("O2"), m.RawSample(lines=stream))

        assert (outcome.cls, outcome.score.outcome) == (m.CLS_REACHED, m.OUTCOME_NON_ADHERENT)

    def test_a_read_error_after_the_child_ran_is_a_retryable_fault_charged_the_reported_cost(self) -> None:
        raw = m.RawSample(lines=make_stream(O1_ADHERENT, cost=0.07), exit_status=0, read_error="OSError: transcript vanished")

        outcome = m.classify_attempt(make_request(budget=4.0), raw)

        assert (outcome.cls, outcome.fault_class, outcome.retryable) == (m.CLS_FAULT, m.FAULT_READ, True)
        assert (outcome.charge_usd, outcome.cost_source) == (0.07, m.COST_SOURCE_REPORTED)
        assert outcome.detail == "OSError: transcript vanished"

    def test_a_read_error_with_no_stream_read_is_charged_the_cap_never_zero(self) -> None:
        raw = m.RawSample(exit_status=0, read_error="OSError: stderr unreadable")

        outcome = m.classify_attempt(make_request(budget=4.0), raw)

        assert (outcome.fault_class, outcome.charge_usd, outcome.cost_source) == (m.FAULT_READ, 4.0, m.COST_SOURCE_CAP)

    def test_a_read_error_is_not_mistaken_for_missing_markers_in_the_hook_arm(self) -> None:
        raw = m.RawSample(lines=make_stream(O1_ADHERENT), exit_status=0, markers=None, read_error="OSError: marker unreadable")

        outcome = m.classify_attempt(make_request("O1", m.ARM_HOOK, delivery_check=True), raw)

        assert (outcome.cls, outcome.fault_class, outcome.delivery) == (m.CLS_FAULT, m.FAULT_READ, None)

    def test_the_blocked_prompt_recorded_from_claude_p_is_recognized_by_its_outcome_kind_not_its_exit_status(self) -> None:
        lines = tuple((FIXTURE_DIR / "blocked-prompt.jsonl").read_bytes().splitlines())
        # Observed when blocked-prompt.jsonl was recorded: `claude -p` exited 0 with empty stderr. The fixture drops the
        # init event, so the recording's CLI version is not on file.
        exit_status, stderr_tail = 0, ""

        raw = m.RawSample(lines=lines, exit_status=exit_status, stderr_tail=stderr_tail)

        outcome = m.classify_attempt(make_request("O1"), raw)

        assert m.parse_stream(lines).blocked_prompt is True
        assert outcome.cls == m.CLS_BLOCKED

    def test_a_blocked_prompt_in_the_hook_arm_is_a_delivery_failure_and_never_a_non_adherent_sample(self) -> None:
        lines = tuple((FIXTURE_DIR / "blocked-prompt.jsonl").read_bytes().splitlines())

        outcome = m.classify_attempt(make_request("O1", m.ARM_HOOK, delivery_check=True), m.RawSample(lines=lines, exit_status=0))

        assert (outcome.cls, outcome.delivery) == (m.CLS_DELIVERY_FAILURE, m.DELIVERY_BLOCKED)

    def test_a_stream_that_never_blocked_is_not_recognized_as_blocked(self) -> None:
        assert m.parse_stream(make_stream(O1_ADHERENT)).blocked_prompt is False


class TestDeliveryCheck:
    @staticmethod
    def hook_event(*texts: str) -> list[dict]:
        return [
            {"type": "system", "subtype": "hook_response", "hook_event": "SessionStart", "output": text, "stdout": text}
            for text in texts
        ]

    @classmethod
    def facts(cls, *texts: str, started: bool = True) -> m.StreamFacts:
        events = [*([init_event()] if started else []), *cls.hook_event(*texts), result_event()]
        return m.parse_stream(lines_of(events))

    both_present = {part: m.MarkerState(present=True) for part in m.CONTENT_PARTS}

    def test_delivery_is_ok_when_both_markers_exist_and_the_stream_holds_each_last_line(self) -> None:
        status, _ = m.evaluate_delivery(self.facts(*LAST_LINE.values()), self.both_present, LAST_LINE)

        assert status == m.DELIVERY_OK

    def test_the_last_line_is_found_inside_the_emitted_json_text(self) -> None:
        emitted = json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": f"body\n{LAST_LINE['conduct']}"}})
        other = json.dumps({"hookSpecificOutput": {"additionalContext": f'quote " and \\ \n{LAST_LINE["dispatch"]}'}})

        status, _ = m.evaluate_delivery(self.facts(emitted, other), self.both_present, LAST_LINE)

        assert status == m.DELIVERY_OK

    def test_a_missing_marker_without_a_sidecar_is_missing(self) -> None:
        markers = {**self.both_present, m.PART_DISPATCH: m.MarkerState(present=False)}

        status, detail = m.evaluate_delivery(self.facts(*LAST_LINE.values()), markers, LAST_LINE)

        assert (status, detail) == (m.DELIVERY_MISSING, m.PART_DISPATCH)

    def test_a_missing_marker_with_a_sidecar_names_the_emitters_cause(self) -> None:
        markers = {**self.both_present, m.PART_CONDUCT: m.MarkerState(present=False, failure="content file over 10,000 bytes")}

        status, detail = m.evaluate_delivery(self.facts(*LAST_LINE.values()), markers, LAST_LINE)

        assert status == m.DELIVERY_SIDECAR
        assert "content file over 10,000 bytes" in detail

    def test_a_sidecar_cause_on_one_part_wins_over_a_bare_missing_marker_on_the_other(self) -> None:
        markers = {m.PART_CONDUCT: m.MarkerState(present=False), m.PART_DISPATCH: m.MarkerState(present=False, failure="cause")}

        status, detail = m.evaluate_delivery(self.facts(*LAST_LINE.values()), markers, LAST_LINE)

        assert (status, detail) == (m.DELIVERY_SIDECAR, "dispatch: cause")

    def test_no_markers_at_all_name_both_parts_as_missing(self) -> None:
        status, detail = m.evaluate_delivery(self.facts(*LAST_LINE.values()), None, LAST_LINE)

        assert (status, detail) == (m.DELIVERY_MISSING, "conduct, dispatch")

    def test_markers_without_the_text_in_the_stream_are_a_receipt_failure(self) -> None:
        status, detail = m.evaluate_delivery(self.facts(LAST_LINE["conduct"]), self.both_present, LAST_LINE)

        assert (status, detail) == (m.DELIVERY_RECEIPT_MISSING, m.PART_DISPATCH)

    def test_a_stream_that_never_started_is_not_checked(self) -> None:
        status, _ = m.evaluate_delivery(self.facts(started=False), None, LAST_LINE)

        assert status == m.DELIVERY_NOT_STARTED

    def test_a_hook_sample_whose_stream_has_no_init_event_is_a_delivery_failure_not_a_pass(self) -> None:
        stream = make_stream(O1_ADHERENT, extra_events=self.hook_event(*LAST_LINE.values()))
        without_init = tuple(line for line in stream if json.loads(line).get("subtype") != "init")
        markers = {part: m.MarkerState(present=True) for part in m.CONTENT_PARTS}

        outcome = m.classify_attempt(
            make_request("O1", m.ARM_HOOK, delivery_check=True), m.RawSample(lines=without_init, markers=markers),
        )

        assert (outcome.cls, outcome.delivery) == (m.CLS_DELIVERY_FAILURE, m.DELIVERY_NOT_STARTED)

    def test_a_hook_sample_with_both_markers_but_without_the_text_in_its_stream_is_a_receipt_failure(self) -> None:
        lines = make_stream(O1_ADHERENT, extra_events=self.hook_event(LAST_LINE[m.PART_CONDUCT]))

        outcome = m.classify_attempt(
            make_request("O1", m.ARM_HOOK, delivery_check=True),
            m.RawSample(lines=lines, exit_status=0, markers=self.both_present),
        )

        assert (outcome.cls, outcome.delivery, outcome.detail) == (
            m.CLS_DELIVERY_FAILURE, m.DELIVERY_RECEIPT_MISSING, m.PART_DISPATCH,
        )

    def test_a_first_miss_with_no_sidecar_is_retried_once_and_a_second_miss_counts(self) -> None:
        markers = {part: m.MarkerState(present=False) for part in m.CONTENT_PARTS}
        lines = make_stream(O1_ADHERENT, extra_events=self.hook_event(*LAST_LINE.values()))
        request = make_request("O1", m.ARM_HOOK, delivery_check=True)

        first = m.classify_attempt(request, m.RawSample(lines=lines, markers=markers))
        second = m.classify_attempt(request, m.RawSample(lines=lines, markers=markers), delivery_retried=True)

        assert first.cls == m.CLS_DELIVERY_RETRY
        assert (second.cls, second.delivery) == (m.CLS_DELIVERY_FAILURE, m.DELIVERY_MISSING)

    def test_a_sidecar_cause_is_a_delivery_failure_without_a_retry(self) -> None:
        markers = {part: m.MarkerState(present=False, failure="cause") for part in m.CONTENT_PARTS}
        lines = make_stream(O1_ADHERENT, extra_events=self.hook_event(*LAST_LINE.values()))

        request = make_request("O1", m.ARM_HOOK, delivery_check=True)

        outcome = m.classify_attempt(request, m.RawSample(lines=lines, markers=markers))

        assert (outcome.cls, outcome.delivery) == (m.CLS_DELIVERY_FAILURE, m.DELIVERY_SIDECAR)


class TestSelfCheck:
    spec = m.SelfCheckSpec(
        {m.ARM_MEMORY: "NONCE-MEMORY", m.PART_CONDUCT: "NONCE-CONDUCT", m.PART_DISPATCH: "NONCE-DISPATCH"},
        "Never stage, commit, or push.",
    )

    def check(self, arm: str, transcript: str | None, *, answer: str = "") -> dict:
        raw = m.RawSample(session_jsonl_text=transcript)
        facts = m.parse_stream(make_stream([], extra_events=[], **{"result": answer}))
        return m.evaluate_self_check(arm, self.spec, raw, facts)

    def test_memory_meets_its_condition_when_the_transcript_holds_its_nonce(self) -> None:
        assert self.check(m.ARM_MEMORY, 'x "NONCE-MEMORY" y')["meets"] is True
        assert self.check(m.ARM_MEMORY, "nothing")["meets"] is False
        assert self.check(m.ARM_MEMORY, None)["meets"] is False

    def test_hook_needs_both_files_nonces(self) -> None:
        assert self.check(m.ARM_HOOK, "NONCE-CONDUCT NONCE-DISPATCH")["meets"] is True
        assert self.check(m.ARM_HOOK, "NONCE-CONDUCT")["meets"] is False

    def test_none_meets_its_condition_when_no_nonce_and_no_agent_briefing_bullet_appears(self) -> None:
        assert self.check(m.ARM_NONE, "unrelated transcript")["meets"] is True
        assert self.check(m.ARM_NONE, "NONCE-CONDUCT")["meets"] is False

    @pytest.mark.parametrize("arm", [m.ARM_MEMORY, m.ARM_NONE, m.ARM_HOOK])
    @pytest.mark.parametrize("transcript", [None, ""])
    def test_no_arm_meets_its_condition_when_no_transcript_text_was_read(self, arm, transcript) -> None:
        check = self.check(arm, transcript)

        assert check["meets"] is False
        assert check["transcript_read"] is False

    def test_a_transcript_that_was_read_is_recorded_as_read(self) -> None:
        assert self.check(m.ARM_NONE, "unrelated transcript")["transcript_read"] is True

    def test_a_leaked_agent_briefing_bullet_in_none_is_recorded_as_a_leak(self) -> None:
        check = self.check(m.ARM_NONE, 'text "Never stage, commit, or push." more')

        assert check["leaked_agent_briefing_bullet"] is True
        assert check["meets"] is False

    def test_a_bullet_with_json_escapes_is_found_in_its_escaped_form(self) -> None:
        spec = m.SelfCheckSpec({}, 'Quote "this" and `that`')
        check = m.evaluate_self_check(
            m.ARM_NONE, spec, m.RawSample(session_jsonl_text=json.dumps({"text": 'Quote "this" and `that`'})), m.parse_stream(()),
        )

        assert check["leaked_agent_briefing_bullet"] is True

    @pytest.mark.parametrize("ensure_ascii", [True, False], ids=["non-ascii-escaped", "non-ascii-raw"])
    def test_a_bullet_with_a_quote_and_an_em_dash_is_found_whichever_way_the_transcript_escapes_the_dash(
        self, ensure_ascii: bool,
    ) -> None:
        bullet = 'Never dispatch an agent — of any type — whose instructions are "do no work".'
        transcript_line = json.dumps({"text": bullet}, ensure_ascii=ensure_ascii)
        spec = m.SelfCheckSpec({}, bullet)

        check = m.evaluate_self_check(m.ARM_NONE, spec, m.RawSample(session_jsonl_text=transcript_line), m.parse_stream(()))

        assert bullet not in transcript_line
        assert check["leaked_agent_briefing_bullet"] is True

    def test_a_named_user_file_path_is_recorded_without_deciding_the_check(self) -> None:
        check = self.check(m.ARM_NONE, "unrelated", answer="I was shown ~/.claude/CLAUDE.md and ./CLAUDE.md")

        assert check["named_claude_md_path"] is True
        assert check["meets"] is True

    def test_a_project_claude_md_path_alone_is_not_the_user_file(self) -> None:
        assert self.check(m.ARM_NONE, "x", answer="Only ./CLAUDE.md")["named_claude_md_path"] is False


# --- Arms, project files, preflight -----------------------------------------------------------------

BASE_CLAUDE_MD = (
    "# Agent Core\n\ncore rule\n\n# Main session\n\n## Safety\n\n- safety rule\n\n## Agent Briefing\n\n"
    "- Never dispatch a no-op agent.\n- second bullet\n\n## Shipping\n\n- ship rule\n"
)
CANDIDATE_CLAUDE_MD = "# Agent Core\n\ncore rule\n"


def _settings_with(registrations: tuple[m.Registration, ...]) -> dict:
    hooks: dict[str, list] = {}
    for registration in registrations:
        handler: dict = {"type": "command", "command": registration.command, "timeout": 10}
        if registration.on_failure:
            handler["onFailure"] = registration.on_failure
        group: dict = {"hooks": [handler]}
        if registration.matcher:
            group["matcher"] = registration.matcher
        hooks.setdefault(registration.event, []).append(group)
    return {"model": "sonnet", "hooks": hooks}


@dataclass
class TempRepo:
    path: Path
    base_sha: str
    candidate_sha: str


def _build_repo(
    tmp_path: Path, *, registrations: tuple[m.Registration, ...] = m.EXPECTED_REGISTRATIONS, emitter: bool = True,
    empty_content_part: str | None = None, omit_content_part: str | None = None, candidate_settings_text: str | None = None,
    omit_hook_file: str | None = None,
) -> TempRepo:
    repo = _init_repo(tmp_path / "repo")
    _write(repo, m.GLOBAL_CLAUDE_MD_RELPATH, BASE_CLAUDE_MD)
    _write(repo, m.SETTINGS_RELPATH, json.dumps({"model": "sonnet"}))
    base_sha = _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)
    _write(repo, m.GLOBAL_CLAUDE_MD_RELPATH, CANDIDATE_CLAUDE_MD)
    _write(
        repo, m.SETTINGS_RELPATH,
        json.dumps(_settings_with(registrations)) if candidate_settings_text is None else candidate_settings_text,
    )
    hooks = f"{m.HOOKS_RELPATH}/"
    for name in (m.PROMPT_GATE_FILE, m.DISPATCH_GATE_FILE, m.LIB_FILE, *([m.EMITTER_FILE] if emitter else [])):
        if name == omit_hook_file:
            continue
        _write(repo, hooks + name, f"#!/bin/bash\n# {name}\n")
    for part in m.CONTENT_PARTS:
        if part == omit_content_part:
            continue
        content = "\n\n" if part == empty_content_part else f"# Main session\n\n{part} body\n{LAST_LINE[part]}\n"
        _write(repo, f"{hooks}{m.CONTENT_DIR_NAME}/{part}.md", content)
    _write(repo, f"{hooks}tests/test_hook.py", "def test_x(): pass\n")
    candidate_sha = _commit(repo, "candidate")
    _git(repo, "checkout", "-q", base_sha)
    return TempRepo(repo, base_sha, candidate_sha)


def _prepare_args(repo: TempRepo, phase: str, *, base_ref: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        phase=phase, seed=3, base_ref=base_ref if base_ref is not None else repo.base_sha, candidate_ref=repo.candidate_sha,
    )


class TestArms:
    def test_cutting_the_main_session_keeps_only_the_text_above_the_marker_line(self) -> None:
        assert m.cut_main_session(BASE_CLAUDE_MD) == CANDIDATE_CLAUDE_MD

    @pytest.mark.parametrize("text", [CANDIDATE_CLAUDE_MD, BASE_CLAUDE_MD + "\n# Main session\n\nagain\n", ""])
    def test_the_cut_refuses_unless_the_marker_line_occurs_exactly_once(self, text) -> None:
        with pytest.raises(m.ArmConstructionError, match="exactly one"):
            m.cut_main_session(text)

    def test_a_marker_inside_a_longer_heading_is_not_the_marker(self) -> None:
        with pytest.raises(m.ArmConstructionError):
            m.cut_main_session("# Agent Core\n\n# Main session rules\n")

    def test_the_first_agent_briefing_bullet_is_read_from_the_base_text(self) -> None:
        assert m.first_agent_briefing_bullet(BASE_CLAUDE_MD) == "Never dispatch a no-op agent."

    def test_a_base_text_without_the_heading_refuses(self) -> None:
        with pytest.raises(m.ArmConstructionError):
            m.first_agent_briefing_bullet(CANDIDATE_CLAUDE_MD)

    def test_git_show_and_the_default_base_ref_read_a_temp_repo(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)

        assert m.git_show(repo.path, repo.base_sha, m.GLOBAL_CLAUDE_MD_RELPATH) == BASE_CLAUDE_MD
        assert m.default_base_ref(repo.path) == repo.base_sha

    def test_git_show_of_a_missing_path_raises_an_arm_error(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)

        with pytest.raises(m.ArmConstructionError):
            m.git_show(repo.path, repo.base_sha, "no/such/file")

    @pytest.mark.parametrize("bad_ref", ["--output=/tmp/x", "-1", "a b", "x..y", "HEAD:claude/.claude/CLAUDE.md", "main@{1}"])
    def test_a_ref_that_could_be_taken_for_a_git_option_range_or_path_selector_is_refused(self, tmp_path: Path, bad_ref) -> None:
        repo = _build_repo(tmp_path)
        args = argparse.Namespace(phase=m.PHASE_GATE, seed=1, base_ref=bad_ref, candidate_ref=repo.candidate_sha)

        with pytest.raises(InvalidIdentifierError):
            m.prepare_run(args, tmp_path / "scratch", repo_root=repo.path)

    @pytest.mark.parametrize("bad_ref", ["--upload-pack=x", "HEAD:claude/.claude/settings.json", "main@{1}"])
    def test_a_candidate_ref_that_could_be_taken_for_a_git_option_or_path_selector_is_refused(
        self, tmp_path: Path, bad_ref,
    ) -> None:
        repo = _build_repo(tmp_path)
        args = argparse.Namespace(phase=m.PHASE_GATE, seed=1, base_ref=repo.base_sha, candidate_ref=bad_ref)

        with pytest.raises(InvalidIdentifierError):
            m.prepare_run(args, tmp_path / "scratch", repo_root=repo.path)

    def test_freezing_a_ref_that_holds_no_hooks_directory_refuses(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)

        with pytest.raises(m.ArmConstructionError, match="git archive"):
            m.freeze_hooks_dir(repo.path, repo.base_sha, tmp_path / "frozen")

    def test_a_failed_tar_extraction_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _build_repo(tmp_path)
        real_run = subprocess.run
        tar_argvs: list[list[str]] = []

        def failing_tar(cmd, *args, **kwargs):
            if cmd[0] == "tar":
                tar_argvs.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"tar boom")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(m.subprocess, "run", failing_tar)

        with pytest.raises(m.ArmConstructionError, match="tar extraction failed: tar boom"):
            m.freeze_hooks_dir(repo.path, repo.candidate_sha, tmp_path / "frozen")

        assert len(tar_argvs) == 1
        tar_argv = tar_argvs[0]
        assert tar_argv[tar_argv.index("-f") + 1] == "-"

    def test_an_archive_that_times_out_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _build_repo(tmp_path)

        def hang(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=m.GIT_TIMEOUT_S)

        monkeypatch.setattr(m.subprocess, "run", hang)

        with pytest.raises(m.ArmConstructionError, match="freezing"):
            m.freeze_hooks_dir(repo.path, repo.candidate_sha, tmp_path / "frozen")

    def test_a_git_call_that_times_out_raises_an_arm_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def hang(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=m.GIT_TIMEOUT_S)

        monkeypatch.setattr(m.subprocess, "run", hang)

        with pytest.raises(m.ArmConstructionError, match="failed"):
            m.run_git(tmp_path, "status")

    def test_a_nonce_is_appended_after_the_texts_last_line(self) -> None:
        assert m.with_nonce("text\n\n", "N1") == f"text\n\n{m.NONCE_LINE_PREFIX}N1\n"

    def test_prepare_run_builds_the_memory_and_none_arms_for_the_calibrate_phase(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)

        prepared = m.prepare_run(_prepare_args(repo, m.PHASE_CALIBRATE), tmp_path / "scratch", repo_root=repo.path)

        assert set(prepared.arms) == {m.ARM_MEMORY, m.ARM_NONE}
        assert prepared.arms[m.ARM_MEMORY].claude_md == BASE_CLAUDE_MD
        assert prepared.arms[m.ARM_NONE].claude_md == CANDIDATE_CLAUDE_MD
        assert m.NONCE_LINE_PREFIX in prepared.self_check_arms[m.ARM_MEMORY].claude_md
        assert prepared.self_check_arms[m.ARM_NONE].claude_md == CANDIDATE_CLAUDE_MD
        assert prepared.receipt_last_lines == {}

    def test_prepare_run_records_the_hash_of_every_arm_input_prompt_and_the_scorer(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)

        inputs = m.prepare_run(_prepare_args(repo, m.PHASE_CALIBRATE), tmp_path / "scratch", repo_root=repo.path).inputs

        assert inputs["base_claude_md"] == m.sha256_text(BASE_CLAUDE_MD)
        assert inputs["none_claude_md"] == m.sha256_text(CANDIDATE_CLAUDE_MD)
        assert inputs["scorer"] == m.scorer_source_hash()
        assert {f"prompt.{name}" for name in m.all_prompt_texts()} <= set(inputs)

    def test_the_input_hashes_cover_the_deny_hook_command_flags_turn_bound_and_seed_fixtures(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)

        inputs = m.prepare_run(_prepare_args(repo, m.PHASE_CALIBRATE), tmp_path / "scratch", repo_root=repo.path).inputs

        expected = {"deny_hook_body", "build_command", "max_turns", "fixture.dispatch-project", "fixture.temp-project"}
        expected |= {"fixture.temp-project.staged.patch"}
        expected |= {f"{kind}.{observable.key}" for kind in ("deny_hook", "observable") for observable in m.OBSERVABLES}
        assert expected <= set(inputs)
        assert inputs["deny_hook.O3"] == m.sha256_text(m.deny_hook_source(m.OBSERVABLES_BY_KEY["O3"]))

    @staticmethod
    def _hashes() -> dict[str, str]:
        return m.collect_input_hashes(
            base_claude_md="base", memory_claude_md="base", none_claude_md="none", candidate_claude_md=None,
            frozen_hooks_dir=None, handlers=[],
        )

    def test_the_input_hashes_change_with_the_deny_hook_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = self._hashes()
        monkeypatch.setattr(m, "DENY_HOOK_BODY", m.DENY_HOOK_BODY + "# changed\n")

        after = self._hashes()

        changed = {name for name in before if before[name] != after[name]}
        assert changed == {"deny_hook_body", *(f"deny_hook.{observable.key}" for observable in m.OBSERVABLES)}

    @pytest.mark.parametrize(
        "change", [{"permission_mode": "plan"}, {"max_turns": 3}, {"deny_tools": ("Agent",)}],
    )
    def test_the_input_hashes_change_with_an_observables_settings(self, change, monkeypatch: pytest.MonkeyPatch) -> None:
        before = self._hashes()
        changed_o3 = replace(m.OBSERVABLES_BY_KEY["O3"], **change)
        monkeypatch.setattr(m, "OBSERVABLES", tuple(changed_o3 if o.key == "O3" else o for o in m.OBSERVABLES))

        after = self._hashes()

        assert before["observable.O3"] != after["observable.O3"]
        assert all(before[name] == after[name] for name in before if name not in ("observable.O3", "deny_hook.O3"))

    def test_the_input_hashes_change_with_the_turn_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = self._hashes()
        monkeypatch.setattr(m, "MAX_TURNS", m.MAX_TURNS + 1)

        assert self._hashes()["max_turns"] != before["max_turns"]

    def test_the_input_hashes_change_with_the_launch_command_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = self._hashes()

        def build_command(request, *, claude_path, observable):
            return [claude_path]

        monkeypatch.setattr(m, "build_command", build_command)

        assert self._hashes()["build_command"] != before["build_command"]

    @pytest.mark.parametrize(
        ("fixture_attribute", "input_name", "touched_file"),
        [
            ("DISPATCH_PROJECT_FIXTURE", "fixture.dispatch-project", "page.py"),
            ("TEMP_PROJECT_FIXTURE", "fixture.temp-project", "calculator.py"),
        ],
    )
    def test_the_input_hashes_change_with_a_file_in_a_seeded_project_fixture(
        self, fixture_attribute, input_name, touched_file, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        before = self._hashes()
        copy = tmp_path / "fixture-copy"
        shutil.copytree(getattr(m, fixture_attribute), copy)
        (copy / touched_file).write_text("changed\n")
        monkeypatch.setattr(m, fixture_attribute, copy)

        after = self._hashes()

        assert before[input_name] != after[input_name]

    def test_the_input_hashes_change_with_the_staged_patch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        before = self._hashes()
        patch = tmp_path / "staged.patch"
        patch.write_text("changed\n")
        monkeypatch.setattr(m, "TEMP_PROJECT_STAGED_PATCH", patch)

        assert self._hashes()["fixture.temp-project.staged.patch"] != before["fixture.temp-project.staged.patch"]

    def test_a_base_ref_whose_claude_md_has_no_main_session_section_fails_loudly_instead_of_building_two_identical_arms(
        self, tmp_path: Path,
    ) -> None:
        repo = _build_repo(tmp_path)

        with pytest.raises(m.ArmConstructionError, match="exactly one"):
            args = _prepare_args(repo, m.PHASE_CALIBRATE, base_ref=repo.candidate_sha)
            m.prepare_run(args, tmp_path / "scratch", repo_root=repo.path)

    def test_the_gate_phase_builds_the_hook_arm_from_the_candidates_frozen_hooks(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)
        scratch = tmp_path / "scratch"

        prepared = m.prepare_run(_prepare_args(repo, m.PHASE_GATE), scratch, repo_root=repo.path)

        hook = prepared.arms[m.ARM_HOOK]
        frozen = scratch / "frozen" / m.HOOKS_RELPATH
        assert hook.claude_md == CANDIDATE_CLAUDE_MD
        assert len(hook.handlers) == len(m.EXPECTED_REGISTRATIONS) == 6
        assert all(m.HOOKS_COMMAND_PREFIX not in entry.handler["command"] for entry in hook.handlers)
        assert all(str(frozen) in entry.handler["command"] for entry in hook.handlers)
        assert not (frozen / "tests").exists()
        assert prepared.receipt_last_lines == LAST_LINE
        expected_inputs = {"content.conduct", "content.dispatch", f"hook.{m.EMITTER_FILE}", f"hook.{m.LIB_FILE}", "handlers"}
        assert expected_inputs <= set(prepared.inputs)

    def test_the_gate_phases_self_check_hook_arm_uses_a_copy_whose_content_files_end_in_a_nonce(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)
        scratch = tmp_path / "scratch"

        prepared = m.prepare_run(_prepare_args(repo, m.PHASE_GATE), scratch, repo_root=repo.path)

        nonced = scratch / "frozen-nonced" / m.HOOKS_RELPATH
        conduct_nonce = prepared.self_check_spec[m.ARM_HOOK].nonces[m.PART_CONDUCT]
        assert (nonced / m.CONTENT_DIR_NAME / "conduct.md").read_text().endswith(f"{m.NONCE_LINE_PREFIX}{conduct_nonce}\n")
        assert all(str(nonced) in entry.handler["command"] for entry in prepared.self_check_arms[m.ARM_HOOK].handlers)

    def test_the_gate_phase_refuses_when_the_candidate_lacks_a_registration(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path, registrations=m.EXPECTED_REGISTRATIONS[:-1])

        with pytest.raises(m.PreflightError, match="require-main-session-rules.sh"):
            m.prepare_run(_prepare_args(repo, m.PHASE_GATE), tmp_path / "scratch", repo_root=repo.path)

    @pytest.mark.parametrize("settings_text", ["{not json", "", "[]", "null", '"text"'])
    def test_the_gate_phase_refuses_a_candidate_settings_file_that_is_not_a_json_object(
        self, tmp_path: Path, settings_text: str,
    ) -> None:
        repo = _build_repo(tmp_path, candidate_settings_text=settings_text)

        with pytest.raises(m.PreflightError, match=m.SETTINGS_RELPATH):
            m.prepare_run(_prepare_args(repo, m.PHASE_GATE), tmp_path / "scratch", repo_root=repo.path)

    def test_the_gate_phase_refuses_when_the_candidate_has_no_emitter_file(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path, emitter=False)

        with pytest.raises(m.PreflightError, match=m.EMITTER_FILE):
            m.prepare_run(_prepare_args(repo, m.PHASE_GATE), tmp_path / "scratch", repo_root=repo.path)

    @pytest.mark.parametrize("hook_file", [m.PROMPT_GATE_FILE, m.DISPATCH_GATE_FILE, m.LIB_FILE])
    def test_the_gate_phase_refuses_when_a_candidate_hook_file_the_gate_hashes_is_absent(
        self, tmp_path: Path, hook_file: str,
    ) -> None:
        """A refusal exits 2 through main(), where a bare FileNotFoundError from the input hashing would exit 1, the
        code of a failed gate."""
        repo = _build_repo(tmp_path, omit_hook_file=hook_file)

        with pytest.raises(m.PreflightError, match=hook_file):
            m.prepare_run(_prepare_args(repo, m.PHASE_GATE), tmp_path / "scratch", repo_root=repo.path)

    @pytest.mark.parametrize("part", m.CONTENT_PARTS)
    def test_the_gate_phase_refuses_when_a_candidate_content_file_has_no_text(self, tmp_path: Path, part: str) -> None:
        """An empty receipt line would be contained in any SessionStart text and pass the delivery check."""
        repo = _build_repo(tmp_path, empty_content_part=part)

        with pytest.raises(m.PreflightError, match=f"{part}.md"):
            m.prepare_run(_prepare_args(repo, m.PHASE_GATE), tmp_path / "scratch", repo_root=repo.path)

    @pytest.mark.parametrize("part", m.CONTENT_PARTS)
    def test_the_gate_phase_refuses_when_a_candidate_content_file_is_absent(self, tmp_path: Path, part: str) -> None:
        """An absent file is a refusal, not an unhandled FileNotFoundError out of the hooks directory freeze."""
        repo = _build_repo(tmp_path, omit_content_part=part)

        with pytest.raises(m.PreflightError, match=f"{part}.md"):
            m.prepare_run(_prepare_args(repo, m.PHASE_GATE), tmp_path / "scratch", repo_root=repo.path)

    def test_the_calibrate_phase_needs_neither_the_handlers_nor_the_emitter(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path, registrations=(), emitter=False)

        prepared = m.prepare_run(_prepare_args(repo, m.PHASE_CALIBRATE), tmp_path / "scratch", repo_root=repo.path)

        assert m.ARM_HOOK not in prepared.arms


class TestHandlerExtraction:
    def test_finds_all_six_registrations_in_a_complete_settings_file(self) -> None:
        handlers, missing = m.extract_main_session_handlers(_settings_with(m.EXPECTED_REGISTRATIONS))

        assert missing == []
        assert [(entry.event, entry.matcher) for entry in handlers] == [
            (r.event, r.matcher) for r in m.EXPECTED_REGISTRATIONS
        ]

    def test_the_registrations_name_the_emitter_per_part_and_the_two_gates(self) -> None:
        by_event = {}
        for registration in m.EXPECTED_REGISTRATIONS:
            by_event.setdefault(registration.event, []).append(registration)

        assert [r.command for r in by_event["SessionStart"]] == [
            "MAIN_SESSION_RULES_PART=conduct ~/.claude/hooks/emit-main-session-rules.sh",
            "MAIN_SESSION_RULES_PART=dispatch ~/.claude/hooks/emit-main-session-rules.sh",
        ]
        assert {r.matcher for r in by_event["SubagentStart"]} == {"review-orchestrator"}
        assert by_event["UserPromptSubmit"][0].command == "~/.claude/hooks/refuse-prompt-without-main-session-rules.sh"
        assert (by_event["PreToolUse"][0].matcher, by_event["PreToolUse"][0].on_failure) == ("Agent|Task", "block")

    @pytest.mark.parametrize("index", range(6))
    def test_reports_each_registration_that_is_missing(self, index) -> None:
        kept = tuple(r for position, r in enumerate(m.EXPECTED_REGISTRATIONS) if position != index)

        _, missing = m.extract_main_session_handlers(_settings_with(kept))

        assert missing == [m.EXPECTED_REGISTRATIONS[index]]

    def test_a_dispatch_gate_without_on_failure_block_does_not_count(self) -> None:
        settings = _settings_with(m.EXPECTED_REGISTRATIONS)
        del settings["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"]

        _, missing = m.extract_main_session_handlers(settings)

        assert [r.command for r in missing] == [f"{m.HOOKS_COMMAND_PREFIX}{m.DISPATCH_GATE_FILE}"]

    def test_a_registration_under_the_wrong_matcher_does_not_count(self) -> None:
        settings = _settings_with(m.EXPECTED_REGISTRATIONS)
        settings["hooks"]["SubagentStart"][0]["matcher"] = "code-writer"

        _, missing = m.extract_main_session_handlers(settings)

        assert len(missing) == 1

    def test_settings_with_no_hooks_object_report_everything_missing(self) -> None:
        handlers, missing = m.extract_main_session_handlers({})

        assert handlers == []
        assert len(missing) == 6

    def test_rewriting_replaces_the_hooks_prefix_with_the_frozen_directory(self, tmp_path: Path) -> None:
        handlers, _ = m.extract_main_session_handlers(_settings_with(m.EXPECTED_REGISTRATIONS))

        rewritten = m.rewrite_handlers_to_frozen_hooks(handlers, tmp_path / "frozen")

        assert rewritten[0].handler["command"] == (
            f"MAIN_SESSION_RULES_PART=conduct {tmp_path}/frozen/{m.EMITTER_FILE}"
        )
        assert handlers[0].handler["command"].startswith("MAIN_SESSION_RULES_PART=conduct ~/.claude/hooks/")

    def test_rewriting_quotes_a_frozen_directory_that_holds_a_space_so_the_shell_reads_one_path(self, tmp_path: Path) -> None:
        handlers, _ = m.extract_main_session_handlers(_settings_with(m.EXPECTED_REGISTRATIONS))
        frozen = tmp_path / "frozen dir; echo injected"

        command = m.rewrite_handlers_to_frozen_hooks(handlers, frozen)[0].handler["command"]

        assert shlex.split(command) == [
            "MAIN_SESSION_RULES_PART=conduct", f"{frozen}/{m.EMITTER_FILE}",
        ]


class TestProjectFiles:
    @staticmethod
    def _user_claude_md(tmp_path: Path) -> Path:
        real = tmp_path / "real" / "CLAUDE.md"
        real.parent.mkdir()
        real.write_text("user rules")
        link = tmp_path / "cfg" / "CLAUDE.md"
        link.parent.mkdir()
        link.symlink_to(real)
        return link

    def test_the_user_claude_md_is_excluded_by_link_path_and_real_path(self, tmp_path: Path) -> None:
        link = self._user_claude_md(tmp_path)

        settings = m.project_settings(m.ArmSpec("memory", "x"), deny_script=None, observable=None, user_claude_md=link)

        assert settings["claudeMdExcludes"] == [str(link), str((tmp_path / "real" / "CLAUDE.md").resolve())]
        assert "hooks" not in settings

    def test_a_plain_user_claude_md_is_listed_once(self, tmp_path: Path) -> None:
        plain = tmp_path.resolve() / "CLAUDE.md"
        plain.write_text("x")

        settings = m.project_settings(m.ArmSpec("memory", "x"), deny_script=None, observable=None, user_claude_md=plain)

        assert settings["claudeMdExcludes"] == [str(plain)]

    def test_the_hook_arms_handlers_and_the_deny_hook_share_the_project_settings(self, tmp_path: Path) -> None:
        handlers, _ = m.extract_main_session_handlers(_settings_with(m.EXPECTED_REGISTRATIONS))
        arm = m.ArmSpec(m.ARM_HOOK, "x", tuple(handlers))

        settings = m.project_settings(
            arm, deny_script=tmp_path / "deny.py", observable=m.OBSERVABLES_BY_KEY["O1"], user_claude_md=tmp_path / "CLAUDE.md",
        )

        hooks = settings["hooks"]
        assert set(hooks) == {"SessionStart", "SubagentStart", "UserPromptSubmit", "PreToolUse"}
        assert len(hooks["SessionStart"]) == 2
        assert "matcher" not in hooks["SessionStart"][0]
        assert hooks["SubagentStart"][0]["matcher"] == "review-orchestrator"
        assert [group.get("matcher") for group in hooks["PreToolUse"]] == ["Agent|Task", "Agent|Task"]
        assert str(tmp_path / "deny.py") in hooks["PreToolUse"][1]["hooks"][0]["command"]
        assert hooks["PreToolUse"][0]["hooks"][0]["onFailure"] == "block"
        assert hooks["PreToolUse"][1]["hooks"][0]["onFailure"] == "block"

    def test_the_memory_arm_registers_only_the_deny_hook(self, tmp_path: Path) -> None:
        settings = m.project_settings(
            m.ArmSpec("memory", "x"), deny_script=tmp_path / "deny.py", observable=m.OBSERVABLES_BY_KEY["O4"],
            user_claude_md=tmp_path / "CLAUDE.md",
        )

        assert list(settings["hooks"]) == ["PreToolUse"]
        assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Edit|Write|MultiEdit|Skill|Agent|Task|Bash"

    def test_an_o3_project_is_the_staged_multiply_fixture_with_the_arms_text_and_a_worktree_optout(self, tmp_path: Path) -> None:
        project = tmp_path / "project"

        m.write_project_files(
            project, m.ArmSpec("memory", "arm text"), observable=m.OBSERVABLES_BY_KEY["O3"],
            user_claude_md=tmp_path / "CLAUDE.md", session_id="abcdef12-sample",
        )

        assert "def multiply" in (project / "calculator.py").read_text()
        assert (project / "CLAUDE.md").read_text() == "arm text"
        assert (project / ".claude" / "worktree-optout").read_text() == ""
        assert json.loads((project / ".claude" / "settings.json").read_text())["claudeMdExcludes"]
        assert (project / ".claude" / "deny-scorable-tools.py").is_file()

    def test_an_o4_project_sits_on_a_feature_branch_named_uniquely_per_sample(self, tmp_path: Path) -> None:
        branches = []
        for number, session_id in enumerate(("aaaaaaaa-sample", "bbbbbbbb-sample")):
            project = tmp_path / f"project-{number}"
            m.write_project_files(
                project, m.ArmSpec("memory", "x"), observable=m.OBSERVABLES_BY_KEY["O4"],
                user_claude_md=tmp_path / "CLAUDE.md", session_id=session_id,
            )
            branches.append(_git(project, "branch", "--show-current").strip())

        assert branches == ["feature/main-session-aaaaaaaa", "feature/main-session-bbbbbbbb"]

    def test_an_o1_project_is_the_dispatch_fixture_without_a_git_repository(self, tmp_path: Path) -> None:
        project = tmp_path / "project"

        m.write_project_files(
            project, m.ArmSpec("memory", "x"), observable=m.OBSERVABLES_BY_KEY["O1"],
            user_claude_md=tmp_path / "CLAUDE.md", session_id="aaaaaaaa-sample",
        )

        assert (project / "page.py").is_file()
        assert not (project / ".git").exists()

    @staticmethod
    def _deny_script(tmp_path: Path, key: str) -> Path:
        script = tmp_path / f"deny-{key}.py"
        script.write_text(m.deny_hook_source(m.OBSERVABLES_BY_KEY[key]), encoding="utf-8")
        return script

    @staticmethod
    def _run_deny_hook(script: Path, payload: dict | str) -> subprocess.CompletedProcess:
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, "-I", str(script)], input=stdin, capture_output=True, text=True)

    @staticmethod
    def _is_denied(completed: subprocess.CompletedProcess) -> bool:
        assert completed.returncode == 0
        if not completed.stdout:
            return False
        return json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.parametrize(
        ("key", "tool", "tool_input", "denied"),
        [
            ("O1", "Agent", {}, True), ("O1", "Task", {}, True), ("O1", "Bash", {"command": "ls"}, False),
            ("O2", "Agent", {}, True), ("O2", "Read", {}, False),
            ("O3", "Skill", {"skill": "code-review"}, True), ("O3", "Skill", {"skill": "p:code-review"}, True),
            ("O3", "Skill", {"skill": "plan-review"}, False), ("O3", "Bash", {"command": "git commit -m x"}, True),
            ("O3", "Bash", {"command": "ls"}, False), ("O3", "Edit", {}, False), ("O3", "Agent", {}, True),
            ("O3", "Task", {}, True),
            ("O4", "Edit", {}, True), ("O4", "Write", {}, True), ("O4", "MultiEdit", {}, True),
            ("O4", "Skill", {"skill": "x"}, True), ("O4", "Agent", {}, True), ("O4", "Task", {}, True),
            ("O4", "Bash", {"command": "review-ledger.sh render"}, True), ("O4", "Bash", {"command": "ls"}, False),
            ("O4", "Bash", {"command": "bash x/review-ledger.sh show"}, False),
            ("O4", "Read", {}, False),
        ],
    )
    def test_the_deny_hook_denies_only_the_observables_scorable_calls(
        self, tmp_path: Path, key, tool, tool_input, denied,
    ) -> None:
        script = self._deny_script(tmp_path, key)

        completed = self._run_deny_hook(script, {"tool_name": tool, "tool_input": tool_input})

        assert self._is_denied(completed) is denied

    @pytest.mark.parametrize(
        "payload",
        ["not json", "", "[]", '"a string"', '{"tool_name": "Bash", "tool_input": "not an object"}'],
        ids=["not-json", "empty", "array", "string", "tool-input-not-an-object"],
    )
    def test_the_deny_hook_fails_closed_on_a_payload_it_cannot_evaluate(self, tmp_path: Path, payload) -> None:
        completed = self._run_deny_hook(self._deny_script(tmp_path, "O3"), payload)

        assert completed.returncode == 2
        assert completed.stdout == ""
        assert "could not evaluate" in completed.stderr

    def test_the_deny_hook_fails_closed_when_a_rule_pattern_is_not_a_valid_regular_expression(self, tmp_path: Path) -> None:
        script = tmp_path / "broken-rule.py"
        script.write_text('RULES = {"tools": [], "skill_suffixes": [], "bash_patterns": ["("]}\n' + m.DENY_HOOK_BODY)

        completed = self._run_deny_hook(script, {"tool_name": "Bash", "tool_input": {"command": "ls"}})

        assert completed.returncode == 2

    @pytest.fixture(scope="class")
    def o3_deny_script(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return self._deny_script(tmp_path_factory.mktemp("o3-deny"), "O3")

    @pytest.mark.parametrize(
        ("command", "is_commit"),
        [(command, True) for command in GIT_COMMIT_COMMANDS] + [(command, False) for command in NON_COMMIT_COMMANDS],
    )
    def test_the_scorer_and_the_generated_deny_hook_agree_on_what_a_git_commit_call_is(
        self, o3_deny_script: Path, command, is_commit,
    ) -> None:
        call = m.ToolCall("Bash", {"command": command}, "toolu_1")

        completed = self._run_deny_hook(o3_deny_script, {"tool_name": "Bash", "tool_input": {"command": command}})

        assert m.is_git_commit_call(call) is is_commit
        assert self._is_denied(completed) is is_commit

    @pytest.fixture(scope="class")
    def o4_deny_script(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return self._deny_script(tmp_path_factory.mktemp("o4-deny"), "O4")

    @pytest.mark.parametrize(
        ("command", "is_render"),
        [
            ("bash x/review-ledger.sh render", True), ("cd d && ~/.claude/scripts/review-ledger.sh render", True),
            ("~/.claude/scripts/review-ledger.sh render | tail -n 5", True), ("review-ledger render", True),
            ("bash x/review-ledger.sh show", False), ("~/.claude/scripts/review-ledger.sh show | tail -n 15", False),
            ("review-ledger show", False),
        ],
    )
    def test_the_scorer_and_the_generated_deny_hook_agree_on_what_a_ledger_render_call_is(
        self, o4_deny_script: Path, command, is_render,
    ) -> None:
        call = m.ToolCall("Bash", {"command": command}, "toolu_1")

        completed = self._run_deny_hook(o4_deny_script, {"tool_name": "Bash", "tool_input": {"command": command}})

        assert m.is_ledger_render_call(call) is is_render
        assert self._is_denied(completed) is is_render

    def test_the_command_the_project_settings_register_runs_and_denies(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        m.write_project_files(
            project, m.ArmSpec("memory", "x"), observable=m.OBSERVABLES_BY_KEY["O3"],
            user_claude_md=tmp_path / "CLAUDE.md", session_id="aaaaaaaa-sample",
        )
        settings = json.loads((project / ".claude" / "settings.json").read_text())
        (group,) = settings["hooks"]["PreToolUse"]
        (handler,) = group["hooks"]

        denied = subprocess.run(
            handler["command"], shell=True, input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git -C d commit"}}),
            capture_output=True, text=True, cwd=tmp_path,
        )
        allowed = subprocess.run(
            handler["command"], shell=True, input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
            capture_output=True, text=True, cwd=tmp_path,
        )

        assert group["matcher"] == "Agent|Task|Skill|Bash"
        assert self._is_denied(denied) is True
        assert self._is_denied(allowed) is False

    def test_the_command_carries_the_turn_and_budget_bounds_and_the_session_id(self) -> None:
        request = make_request("O1", budget=3.25)

        cmd = m.build_command(request, claude_path="/real/claude", observable=m.OBSERVABLES_BY_KEY["O1"])

        assert cmd[:3] == ["/real/claude", "-p", "prompt text"]
        assert cmd[cmd.index("--max-turns") + 1] == str(m.MAX_TURNS)
        assert cmd[cmd.index("--max-budget-usd") + 1] == "3.25"
        assert cmd[cmd.index("--session-id") + 1] == request.session_id
        assert "--permission-mode" not in cmd

    def test_the_command_asks_for_the_stream_the_parser_reads(self) -> None:
        request = make_request("O1")

        cmd = m.build_command(request, claude_path="c", observable=m.OBSERVABLES_BY_KEY["O1"])

        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in cmd
        assert "--include-hook-events" in cmd
        assert cmd[cmd.index("--model") + 1] == request.model

    def test_the_self_check_runs_with_every_tool_disabled_and_an_observable_does_not(self) -> None:
        self_check = m.build_command(make_request("O1"), claude_path="c", observable=None)
        observed = m.build_command(make_request("O1"), claude_path="c", observable=m.OBSERVABLES_BY_KEY["O1"])

        assert self_check[-2:] == ["--tools", ""], "--tools takes a variable number of values, so nothing may follow it"
        assert "--strict-mcp-config" in self_check
        assert "--mcp-config" not in self_check
        assert "--tools" not in observed
        assert "--strict-mcp-config" not in observed

    def test_only_o3_runs_under_accept_edits(self) -> None:
        o3 = m.build_command(make_request("O3"), claude_path="c", observable=m.OBSERVABLES_BY_KEY["O3"])
        o4 = m.build_command(make_request("O4"), claude_path="c", observable=m.OBSERVABLES_BY_KEY["O4"])

        assert o3[o3.index("--permission-mode") + 1] == "acceptEdits"
        assert "--permission-mode" not in o4


class TestLiveLauncher:
    def test_launches_in_the_seeded_project_and_returns_the_outcome_and_the_cleared_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_dir = tmp_path / "cfg"
        marker_dir = config_dir / m.MARKER_DIR_NAME
        marker_dir.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        request = make_request("O1", m.ARM_HOOK, delivery_check=True, project_dir=tmp_path / "project")
        (marker_dir / f"session.{request.session_id}.{m.PART_CONDUCT}").write_text("")
        (marker_dir / f"session.{request.session_id}.{m.PART_DISPATCH}{m.FAILURE_SIDECAR_SUFFIX}").write_text("cause text\n")
        captured: dict = {}

        def fake_run(cmd, cwd, timeout_s, *, outcome=None):
            settings_file = Path(cwd) / ".claude" / "settings.json"
            captured.update(cmd=cmd, cwd=cwd, timeout_s=timeout_s, settings_present=settings_file.is_file())
            outcome.exit_status, outcome.stderr_tail = 0, "tail"
            return [b"line-1", b"line-2"], False

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run)
        launcher = m.LiveLauncher(
            claude_path="/real/claude", arms={m.ARM_HOOK: m.ArmSpec(m.ARM_HOOK, "x")}, self_check_arms={},
            projects_root=tmp_path / "projects",
        )

        raw = launcher(request)

        assert captured["cwd"] == request.project_dir
        assert captured["settings_present"] is True
        assert captured["cmd"][0] == "/real/claude"
        assert (raw.lines, raw.timed_out, raw.exit_status, raw.stderr_tail) == ((b"line-1", b"line-2"), False, 0, "tail")
        assert raw.markers == {m.PART_CONDUCT: m.MarkerState(True), m.PART_DISPATCH: m.MarkerState(False, "cause text")}
        assert list(marker_dir.iterdir()) == []

    def test_reads_the_session_transcript_for_a_self_check_sample(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setattr(msmr, "SIDECAR_POLL_INTERVAL_S", 0.01)
        spec = m.SelfCheckSpec({m.ARM_MEMORY: "NONCE"}, "bullet")
        request = make_request(m.SELF_CHECK_KEY, self_check=spec, project_dir=tmp_path / "project")
        projects_root = tmp_path / "projects"
        transcript = projects_root / "some-hash" / f"{request.session_id}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{"text": "NONCE"}\n')
        monkeypatch.setattr(msmr, "_run_claude_to_completion", lambda cmd, cwd, timeout_s, *, outcome=None: ([], False))
        arm = m.ArmSpec(m.ARM_MEMORY, "x")
        launcher = m.LiveLauncher(
            claude_path="c", arms={}, self_check_arms={m.ARM_MEMORY: arm}, projects_root=projects_root,
        )

        raw = launcher(request)

        assert raw.session_jsonl_text == '{"text": "NONCE"}\n'

    @pytest.mark.timing
    def test_a_transcript_that_never_appears_is_read_as_none_after_the_poll_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(msmr, "SIDECAR_POLL_TIMEOUT_S", 0.05)
        monkeypatch.setattr(msmr, "SIDECAR_POLL_INTERVAL_S", 0.01)
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        launcher = m.LiveLauncher(claude_path="c", arms={}, self_check_arms={}, projects_root=projects_root)

        started = time.monotonic()
        transcript = launcher._read_flushed_transcript("session-that-never-flushed")

        assert transcript is None
        assert time.monotonic() - started < 5

    def _launcher_whose_child_ran(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exit_status: int | None, raises: Exception | None = None,
    ) -> m.LiveLauncher:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))

        def fake_run(cmd, cwd, timeout_s, *, outcome=None):
            outcome.exit_status = exit_status
            if raises is not None:
                raise raises
            return [b"line-1"], False

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run)
        return m.LiveLauncher(
            claude_path="c", arms={m.ARM_HOOK: m.ArmSpec(m.ARM_HOOK, "x")}, self_check_arms={},
            projects_root=tmp_path / "projects",
        )

    def test_a_marker_read_failure_after_the_child_ran_keeps_the_stream_and_reports_a_read_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launcher = self._launcher_whose_child_ran(tmp_path, monkeypatch, exit_status=0)

        def unreadable(session_id: str) -> None:
            raise PermissionError("marker directory unreadable")

        monkeypatch.setattr(launcher, "_read_and_clear_markers", unreadable)
        request = make_request("O1", m.ARM_HOOK, delivery_check=True, project_dir=tmp_path / "project")

        raw = launcher(request)

        assert raw.lines == (b"line-1",)
        assert raw.markers is None
        assert raw.read_error == "PermissionError: marker directory unreadable"
        assert raw.launch_error is None

    def test_a_transcript_read_failure_after_the_child_ran_reports_a_read_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launcher = self._launcher_whose_child_ran(tmp_path, monkeypatch, exit_status=0)
        self_check_arm = m.ArmSpec(m.ARM_MEMORY, "x")
        launcher.self_check_arms = {m.ARM_MEMORY: self_check_arm}

        def unreadable(session_id: str) -> None:
            raise FileNotFoundError("transcript vanished")

        monkeypatch.setattr(launcher, "_read_flushed_transcript", unreadable)
        spec = m.SelfCheckSpec({m.ARM_MEMORY: "NONCE"}, "bullet")
        request = make_request(m.SELF_CHECK_KEY, self_check=spec, project_dir=tmp_path / "project")

        raw = launcher(request)

        assert raw.session_jsonl_text is None
        assert raw.read_error == "FileNotFoundError: transcript vanished"

    def test_an_oserror_after_the_childs_exit_status_was_recorded_is_a_read_error_not_a_launch_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launcher = self._launcher_whose_child_ran(tmp_path, monkeypatch, exit_status=1, raises=OSError("stderr unreadable"))
        request = make_request("O1", m.ARM_HOOK, project_dir=tmp_path / "project")

        raw = launcher(request)

        assert (raw.exit_status, raw.lines, raw.read_error) == (1, (), "OSError: stderr unreadable")

    def test_an_oserror_before_the_child_has_an_exit_status_propagates_as_a_launch_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launcher = self._launcher_whose_child_ran(tmp_path, monkeypatch, exit_status=None, raises=FileNotFoundError("no claude"))
        request = make_request("O1", m.ARM_HOOK, project_dir=tmp_path / "project")

        with pytest.raises(FileNotFoundError, match="no claude"):
            launcher(request)


class TestPreflight:
    def test_an_ambient_settings_file_registering_the_emitter_aborts_naming_the_confound(self, tmp_path: Path) -> None:
        settings = _settings_with(m.EXPECTED_REGISTRATIONS)
        (tmp_path / "settings.json").write_text(json.dumps(settings))

        with pytest.raises(m.PreflightError, match=m.EMITTER_FILE):
            m.abort_if_emitter_registered(tmp_path)

    def test_the_local_settings_file_is_checked_too(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").write_text(json.dumps(_settings_with(m.EXPECTED_REGISTRATIONS)))

        with pytest.raises(m.PreflightError):
            m.abort_if_emitter_registered(tmp_path)

    def test_unrelated_hooks_and_absent_files_pass(self, tmp_path: Path) -> None:
        m.abort_if_emitter_registered(tmp_path)
        (tmp_path / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"command": "other.sh"}]}]}}))

        m.abort_if_emitter_registered(tmp_path)

    def test_an_unparsable_settings_file_refuses_rather_than_assuming_the_emitter_is_absent(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text("{not json")

        with pytest.raises(m.PreflightError, match="not valid JSON"):
            m.abort_if_emitter_registered(tmp_path)

    def test_the_claude_binary_is_resolved_to_its_real_path_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        real = tmp_path / "real-claude"
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        link_dir = tmp_path / "bin"
        link_dir.mkdir()
        (link_dir / "claude").symlink_to(real)
        monkeypatch.setenv("PATH", str(link_dir))

        assert m.resolve_claude_binary() == str(real.resolve())

    def test_a_missing_claude_binary_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))

        with pytest.raises(m.PreflightError, match="claude"):
            m.resolve_claude_binary()

    def test_the_default_model_is_the_one_the_checkouts_settings_configure(self, tmp_path: Path) -> None:
        _write(tmp_path, m.SETTINGS_RELPATH, json.dumps({"model": "configured-model"}))

        assert m.default_model(repo_root=tmp_path) == "configured-model"

    @pytest.mark.parametrize("content", [None, "{not json", "[]"], ids=["missing-file", "not-json", "not-an-object"])
    def test_a_settings_file_that_is_not_an_object_refuses_as_a_preflight_failure(self, tmp_path: Path, content) -> None:
        if content is not None:
            _write(tmp_path, m.SETTINGS_RELPATH, content)

        with pytest.raises(m.PreflightError, match="pass --model"):
            m.default_model(repo_root=tmp_path)

    @pytest.mark.parametrize("settings", [{}, {"model": ""}, {"model": 3}])
    def test_settings_with_no_usable_model_refuse_instead_of_guessing(self, tmp_path: Path, settings) -> None:
        _write(tmp_path, m.SETTINGS_RELPATH, json.dumps(settings))

        with pytest.raises(m.PreflightError, match="pass --model"):
            m.default_model(repo_root=tmp_path)


# --- Run log, resume, output paths ------------------------------------------------------------------------------


class TestRunLog:
    def test_a_fresh_run_refuses_an_existing_log(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        m.RunLog(path, resume=False).close()

        with pytest.raises(m.OutputPathError, match="already exists"):
            m.RunLog(path, resume=False)

    def test_the_log_is_owner_only(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        log = m.RunLog(path, resume=False)
        log.append({"kind": "header"})
        log.close()

        assert path.stat().st_mode & 0o777 == 0o600

    def test_resume_tolerates_a_truncated_final_line_and_keeps_appending_cleanly(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        path.write_text(json.dumps({"kind": "header", "phase": "calibrate"}) + "\n" + '{"kind": "start", "slo')

        log = m.RunLog(path, resume=True)
        log.append({"kind": "end"})
        log.close()

        assert log.records == [{"kind": "header", "phase": "calibrate"}]
        assert [json.loads(line)["kind"] for line in path.read_text().splitlines()] == ["header", "end"]

    def test_resuming_a_missing_log_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(m.ResumeRefused, match="no such run log"):
            m.RunLog(tmp_path / "absent.jsonl", resume=True)

    def test_the_run_lock_is_held_for_the_runs_life_and_refuses_a_second_holder(self, tmp_path: Path) -> None:
        first = bench_runner.RunStore(tmp_path / "store")
        first.acquire_lock()
        try:
            with pytest.raises(bench_runner.RunStoreLocked):
                bench_runner.RunStore(tmp_path / "store").acquire_lock()
        finally:
            first.release_lock()

    def test_an_append_writes_the_whole_line_even_when_the_kernel_takes_it_in_pieces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real_write = os.write
        log = m.RunLog(tmp_path / "run.jsonl", resume=False)

        with monkeypatch.context() as patch:
            patch.setattr(m.os, "write", lambda fd, data: real_write(fd, bytes(data)[:3]))
            log.append({"kind": "header", "phase": "calibrate"})
        log.close()

        assert json.loads((tmp_path / "run.jsonl").read_text()) == {"kind": "header", "phase": "calibrate"}

    def test_an_append_that_makes_no_progress_raises_instead_of_dropping_the_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log = m.RunLog(tmp_path / "run.jsonl", resume=False)

        with monkeypatch.context() as patch, pytest.raises(OSError, match="no progress"):
            patch.setattr(m.os, "write", lambda fd, data: 0)
            log.append({"kind": "header"})
        log.close()

    @pytest.mark.parametrize("field_name", ["phase", "seed", "model", "cli_version", "ambient_config_commit", "account_label"])
    def test_resume_refuses_when_a_scalar_header_field_differs(self, field_name) -> None:
        logged = {
            "phase": "calibrate", "seed": 1, "model": "sonnet", "cli_version": "1", "ambient_config_commit": "a",
            "account_label": "account-a", "inputs": {},
        }
        current = {**logged, field_name: "changed"}

        assert m.resume_mismatches(logged, current) == [field_name]

    @pytest.mark.parametrize("input_name", ["base_claude_md", "scorer", "prompt.O1.0", "content.conduct"])
    def test_resume_refuses_when_an_arm_input_prompt_or_scorer_hash_differs(self, input_name) -> None:
        logged = {"phase": "p", "inputs": {"base_claude_md": "1", "scorer": "2", "prompt.O1.0": "3", "content.conduct": "4"}}
        current = {"phase": "p", "inputs": {**logged["inputs"], input_name: "different"}}

        assert m.resume_mismatches(logged, current) == [f"inputs.{input_name}"]

    def test_resume_accepts_identical_headers(self) -> None:
        header = {
            "phase": "gate", "seed": 2, "model": "m", "cli_version": "1", "ambient_config_commit": "c",
            "inputs": {"a": "1"},
        }

        assert m.resume_mismatches(header, dict(header)) == []


class TestOutputPaths:
    def test_a_path_inside_a_worktree_of_the_repository_is_refused_naming_its_flag(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "README.md", "x")
        _commit(repo, "initial")

        with pytest.raises(m.OutputPathError, match="--json"):
            m.validate_output_path("--json", repo / "out" / "result.json", start_dir=repo)

    @pytest.mark.parametrize("flag", ["--json", "--keep-artifacts", "--run-log"])
    def test_each_output_flag_is_refused_inside_a_linked_worktree(self, tmp_path: Path, flag) -> None:
        repo = _init_repo(tmp_path / "main")
        _write(repo, "README.md", "x")
        _commit(repo, "initial")
        linked = tmp_path / "linked"
        _git(repo, "worktree", "add", "-q", str(linked), "-b", "side")

        with pytest.raises(m.OutputPathError, match=flag):
            m.validate_output_path(flag, linked / "inside", start_dir=repo)

    def test_a_path_outside_every_worktree_passes(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "README.md", "x")
        _commit(repo, "initial")

        m.validate_output_path("--run-log", tmp_path / "elsewhere" / "run.jsonl", start_dir=repo)


# --- Stage loop under a scripted launcher -------------------------------------------------------------------------

SELF_CHECK_SPECS = {
    m.ARM_MEMORY: m.SelfCheckSpec({m.ARM_MEMORY: "NONCE-MEMORY"}, "BULLET-TEXT"),
    m.ARM_NONE: m.SelfCheckSpec(
        {m.ARM_MEMORY: "NONCE-MEMORY", m.PART_CONDUCT: "NONCE-CONDUCT", m.PART_DISPATCH: "NONCE-DISPATCH"}, "BULLET-TEXT",
    ),
    m.ARM_HOOK: m.SelfCheckSpec({m.PART_CONDUCT: "NONCE-CONDUCT", m.PART_DISPATCH: "NONCE-DISPATCH"}, "BULLET-TEXT"),
}
SELF_CHECK_TRANSCRIPTS = {
    m.ARM_MEMORY: "memory block NONCE-MEMORY", m.ARM_NONE: "no main session text", m.ARM_HOOK: "NONCE-CONDUCT NONCE-DISPATCH",
}
SEPARATING = {
    (arm, key): rate for key in ("O1", "O2", "O3", "O4")
    for arm, rate in ((m.ARM_MEMORY, 0.9), (m.ARM_NONE, 0.2), (m.ARM_HOOK, 0.9))
}


def separating_except(*flat_keys: str) -> dict[tuple[str, str], float]:
    """Adherence rates under which only the observables not named in `flat_keys` separate `memory` from `none`."""
    rates = dict(SEPARATING)
    for key in flat_keys:
        rates[(m.ARM_NONE, key)] = rates[(m.ARM_MEMORY, key)]
    return rates


class ScriptedLauncher:
    """A sample launcher whose outcome depends only on the slot, so any completion order scores alike. Adherence and reach
    follow a fixed pattern over slot indices, giving exact rates over every hundred slots."""

    def __init__(
        self, adherence: Mapping[tuple[str, str], float] | None = None, *,
        reach: Callable[[m.SampleRequest], float] | None = None,
        cost: float = 0.1, fault: Callable[[m.SampleRequest, int], m.RawSample | None] | None = None,
        on_launch: Callable[[m.SampleRequest, int], None] | None = None,
        markers: Callable[[m.SampleRequest], dict | None] | None = None,
        version: str | Callable[[m.SampleRequest, int], str] = CLI_VERSION, unreached_calls: Mapping[str, list] | None = None,
        transcripts: Mapping[str, str] | None = None, jitter_s: float = 0.0,
    ):
        self.adherence = adherence if adherence is not None else SEPARATING
        self.reach = reach or (lambda request: 1.0)
        self.cost = cost
        self.fault = fault
        self.on_launch = on_launch
        self.markers = markers
        self.version = version
        self.unreached_calls = unreached_calls or {}
        self.transcripts = transcripts or SELF_CHECK_TRANSCRIPTS
        self.jitter_s = jitter_s
        self.requests: list[m.SampleRequest] = []
        self._lock = threading.Lock()

    def __call__(self, request: m.SampleRequest) -> m.RawSample:
        with self._lock:
            self.requests.append(request)
            count = len(self.requests)
        if self.on_launch is not None:
            self.on_launch(request, count)
        if self.jitter_s:
            time.sleep(random.Random(f"{request.stage}{request.arm}{request.key}{request.slot_index}").random() * self.jitter_s)
        injected = self.fault(request, count) if self.fault is not None else None
        if injected is not None:
            return injected
        version = self.version(request, count) if callable(self.version) else self.version
        if request.key == m.SELF_CHECK_KEY:
            lines = make_stream([], cost=self.cost, version=version)
            return m.RawSample(lines=lines, exit_status=0, session_jsonl_text=self.transcripts[request.arm])
        return self._observable(request, version)

    def _observable(self, request: m.SampleRequest, version: str) -> m.RawSample:
        reached = (request.slot_index * 53 + 11) % 100 < round(self.reach(request) * 100)
        adherent = (request.slot_index * 37) % 100 < round(self.adherence.get((request.arm, request.key), 0.5) * 100)
        if request.key != "O2" and not reached:
            calls = self.unreached_calls.get(request.key, UNREACHED_CALLS)
        else:
            calls = CALLS_BY_OUTCOME[(request.key, m.OUTCOME_ADHERENT if adherent else m.OUTCOME_NON_ADHERENT)]
        extra = TestDeliveryCheck.hook_event(*LAST_LINE.values()) if request.arm == m.ARM_HOOK else []
        markers = None
        if request.delivery_check:
            markers = self.markers(request) if self.markers is not None else None
            markers = markers if markers is not None else {part: m.MarkerState(True) for part in m.CONTENT_PARTS}
        lines = make_stream(calls, cost=self.cost, version=version, extra_events=extra)
        return m.RawSample(lines=lines, exit_status=0, markers=markers)


def timeout_sample() -> m.RawSample:
    return m.RawSample(lines=make_stream([], with_result=False), timed_out=True)


def rate_limit_sample(resets_at: float | None = None) -> m.RawSample:
    info: dict = {"status": "rejected"}
    if resets_at is not None:
        info["resetsAt"] = resets_at
    return m.RawSample(lines=make_stream([], extra_events=[{"type": "rate_limit_event", "rate_limit_info": info}]), exit_status=0)


# A real listing holds at least the lister's own process, so an empty one is refused by the resume guard.
PROCESS_LISTING_WITHOUT_CHILDREN = "/bin/zsh\nps -axww -o command=\n"


@dataclass
class Built:
    harness: m.Harness
    log: m.RunLog
    store: bench_runner.RunStore
    notices: list[str]
    sleeps: list[float]

    def release(self) -> None:
        self.log.close()
        self.store.release_lock()


class RunKit:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.temp_root = tmp_path / "tmp"
        self.built: list[Built] = []

    def build(
        self, launcher: Callable[[m.SampleRequest], m.RawSample], *, phase: str = m.PHASE_CALIBRATE, workers: int = 3,
        stop: float = 300.0, budget: float = 11.5, seed: int = 7, resume: bool = False, log_name: str = "run.jsonl",
        environment_reader: Callable[[], bench_runner.EnvironmentRecord] | None = None,
        header_inputs: Mapping[str, str] | None = None,
        keep_artifacts_dir: Path | None = None,
        process_lister: Callable[[], str] | None = lambda: PROCESS_LISTING_WITHOUT_CHILDREN,
    ) -> Built:
        """A `process_lister` of None builds the Harness with its production default."""
        log_path = self.tmp_path / log_name
        log = m.RunLog(log_path, resume=resume)
        store = bench_runner.RunStore(self.tmp_path / f"{log_name}.d", fixture_root=self.temp_root.resolve())
        store.acquire_lock()
        config = m.RunConfig(
            phase=phase, seed=seed, model="sonnet", workers=workers, timeout_s=300, budget_cap_usd=budget,
            cumulative_stop_usd=stop,
            projects_root=self.tmp_path / "projects", log_path=log_path, keep_artifacts_dir=keep_artifacts_dir,
            receipt_last_lines=LAST_LINE if phase == m.PHASE_GATE else {}, self_check_spec=SELF_CHECK_SPECS,
        )
        notices: list[str] = []
        sleeps: list[float] = []
        lister_option = {} if process_lister is None else {"process_lister": process_lister}
        harness = m.Harness(
            config, launcher, log, store,
            environment_reader=environment_reader or (lambda: bench_runner.EnvironmentRecord("cli 1", "commit-1")),
            sleep=sleeps.append, notify=notices.append, header_inputs=header_inputs or {"base_claude_md": "hash-1"},
            **lister_option,
        )
        built = Built(harness, log, store, notices, sleeps)
        self.built.append(built)
        return built

    def leftover_projects(self) -> list[Path]:
        return sorted(self.temp_root.iterdir())

    def close(self) -> None:
        for built in self.built:
            built.release()


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "account-label-xyz"))
    run_kit = RunKit(tmp_path)
    yield run_kit
    run_kit.close()
    msmr._launches_aborted.clear()


def stage_of(launcher: ScriptedLauncher, stage: str) -> list[m.SampleRequest]:
    return [request for request in launcher.requests if request.stage == stage]


def log_entries(built: Built) -> list[dict]:
    return [json.loads(line) for line in built.log.path.read_text().splitlines()]


def finish_lines(built: Built) -> list[dict]:
    return [entry for entry in log_entries(built) if entry["kind"] == "finish"]


class TestCalibrationStages:
    def test_the_calibrate_phase_runs_all_three_stages_and_enters_the_separating_observables(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        built = kit.build(launcher)

        result = built.harness.run()

        assert result.verdict == m.VERDICT_CALIBRATED
        assert result.halt is None
        assert built.harness.entered_keys() == ["O1", "O2", "O3", "O4"]
        assert len(stage_of(launcher, m.STAGE_SELF_CHECK)) == 2 * m.SELF_CHECK_SAMPLES
        assert len(stage_of(launcher, m.STAGE_SMOKE)) == 2 * 4 * m.SMOKE_SAMPLES_PER_CELL
        for entry in built.harness.calibration.values():
            assert entry["memory"].scored == entry["none"].scored == m.CALIBRATION_TARGET
            assert entry["memory"].attempts <= m.CALIBRATION_ATTEMPT_CAP
        assert {request.arm for request in launcher.requests} == {m.ARM_MEMORY, m.ARM_NONE}
        assert kit.leftover_projects() == []

    def test_the_stages_run_in_order_and_every_sample_gets_a_start_line_before_its_finish_line(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        built = kit.build(launcher, workers=1)

        built.harness.run()

        stages = [request.stage for request in launcher.requests]
        assert [stage for stage in dict.fromkeys(stages)] == [m.STAGE_SELF_CHECK, m.STAGE_SMOKE, m.STAGE_CALIBRATION]
        kinds = [json.loads(line)["kind"] for line in built.log.path.read_text().splitlines()]
        body = kinds[1:-1]
        assert (kinds[0], kinds[-1]) == ("header", "end")
        assert body == ["start", "finish"] * (len(body) // 2)

    def test_the_log_header_records_the_inputs_and_the_account_label_only_there(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(), header_inputs={"scorer": "abc"})

        built.harness.run()

        header = json.loads(built.log.path.read_text().splitlines()[0])
        assert header["inputs"] == {"scorer": "abc"}
        assert header["account_label"] == "account-label-xyz"
        assert header["phase"] == m.PHASE_CALIBRATE

    def test_an_observable_whose_arms_do_not_differ_does_not_enter_while_each_file_keeps_one(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(separating_except("O2", "O4"))
        built = kit.build(launcher)

        result = built.harness.run()

        assert result.verdict == m.VERDICT_CALIBRATED
        assert built.harness.entered_keys() == ["O1", "O3"]
        assert "does not exceed" in built.harness.calibration["O2"]["note"]

    def test_a_content_file_with_no_entered_observable_voids_the_run_and_still_reports_the_table(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(separating_except("O3", "O4")))

        result = built.harness.run()

        assert result.verdict == m.RunVerdict.VOID.value
        assert isinstance(result.halt, m.VoidRun)
        assert "conduct.md" in result.void_reasons[0]
        assert set(result.report["calibration"]) == {"O1", "O2", "O3", "O4"}
        assert json.loads(built.log.path.read_text().splitlines()[-1])["verdict"] == "void"

    def test_an_observable_scoring_under_thirty_stops_at_the_attempt_cap_and_does_not_enter(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(reach=lambda request: 0.3 if request.key == "O4" else 1.0)
        built = kit.build(launcher)

        built.harness.run()

        o4 = built.harness.calibration["O4"]
        assert o4["entered"] is False
        assert o4["memory"].attempts == o4["none"].attempts == m.CALIBRATION_ATTEMPT_CAP
        assert o4["memory"].scored < m.CALIBRATION_TARGET
        assert o4["memory"].stopped_short is True
        assert f"scored {o4['memory'].scored} of 30 within 45 attempts" in o4["note"]

    def test_unreached_samples_count_toward_the_attempt_cap_but_not_toward_the_scored_target(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(reach=lambda request: 0.8 if request.key == "O3" else 1.0)
        built = kit.build(launcher)

        built.harness.run()

        memory = built.harness.calibration["O3"]["memory"]
        assert memory.scored == m.CALIBRATION_TARGET
        assert memory.attempts > m.CALIBRATION_TARGET
        assert memory.classes[m.CLS_UNREACHED] == memory.attempts - memory.scored
        assert memory.unreached_reasons[m.UNREACHED_NONE_SCORABLE] == memory.classes[m.CLS_UNREACHED]

    def test_the_seeded_shuffle_is_the_same_across_runs_and_interleaves_the_arms(self, kit: RunKit) -> None:
        first, second = ScriptedLauncher(), ScriptedLauncher()

        kit.build(first, workers=1, log_name="a.jsonl").harness.run()
        kit.build(second, workers=1, log_name="b.jsonl").harness.run()

        def order(launcher: ScriptedLauncher) -> list[tuple[str, str, str, int]]:
            return [(r.stage, r.arm, r.key, r.slot_index) for r in launcher.requests]

        assert order(first) == order(second)
        calibration_arms = [r.arm for r in stage_of(first, m.STAGE_CALIBRATION)[:12]]
        assert set(calibration_arms) == {m.ARM_MEMORY, m.ARM_NONE}

    def test_a_different_seed_gives_a_different_order(self, kit: RunKit) -> None:
        first, second = ScriptedLauncher(), ScriptedLauncher()

        kit.build(first, workers=1, seed=1, log_name="a.jsonl").harness.run()
        kit.build(second, workers=1, seed=2, log_name="b.jsonl").harness.run()

        def cells(launcher: ScriptedLauncher) -> list[tuple[str, str]]:
            return [(r.arm, r.key) for r in stage_of(launcher, m.STAGE_CALIBRATION)]

        assert cells(first) != cells(second)

    def test_every_arm_holds_both_wordings_of_every_observable_in_equal_counts(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        kit.build(launcher).harness.run()

        for observable in m.OBSERVABLES:
            assert observable.wordings[0] != observable.wordings[1]
            calibration = stage_of(launcher, m.STAGE_CALIBRATION)
            counts = {
                arm: Counter(r.wording for r in calibration if r.arm == arm and r.key == observable.key)
                for arm in (m.ARM_MEMORY, m.ARM_NONE)
            }
            assert counts[m.ARM_MEMORY] == counts[m.ARM_NONE] == Counter({0: 15, 1: 15})

    def test_each_samples_wording_follows_its_slot_index_in_blocks(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        kit.build(launcher).harness.run()

        for request in stage_of(launcher, m.STAGE_CALIBRATION):
            observable = m.OBSERVABLES_BY_KEY[request.key]
            assert request.wording == m.wording_index(request.slot_index)
            assert request.prompt == observable.wordings[request.wording]

    def test_the_self_check_stage_sends_the_self_check_prompt_with_no_deny_hook_observable(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        kit.build(launcher).harness.run()

        assert {request.prompt for request in stage_of(launcher, m.STAGE_SELF_CHECK)} == {m.SELF_CHECK_PROMPT}
        assert {request.key for request in stage_of(launcher, m.STAGE_SELF_CHECK)} == {m.SELF_CHECK_KEY}


class TestSelfCheckStage:
    def test_a_failing_arm_voids_the_run_before_any_calibration_sample(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(transcripts={**SELF_CHECK_TRANSCRIPTS, m.ARM_MEMORY: "the nonce never arrived"})
        built = kit.build(launcher)

        result = built.harness.run()

        assert result.verdict == "void"
        assert "memory" in result.void_reasons[0]
        assert stage_of(launcher, m.STAGE_SMOKE) == []
        assert result.report["self_check"][m.ARM_MEMORY]["passed"] is False

    def test_two_of_three_samples_meeting_the_condition_pass_the_arm(self, kit: RunKit) -> None:
        class TwoOfThree(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                raw = super().__call__(request)
                if request.key == m.SELF_CHECK_KEY and request.arm == m.ARM_MEMORY and request.slot_index == 0:
                    return m.RawSample(lines=raw.lines, exit_status=0, session_jsonl_text="no nonce here")
                return raw

        built = kit.build(TwoOfThree())

        result = built.harness.run()

        summary = result.report["self_check"][m.ARM_MEMORY]
        assert (summary["samples"], summary["meets"], summary["passed"]) == (3, 2, True)
        assert result.verdict == m.VERDICT_CALIBRATED

    def test_one_of_three_samples_meeting_the_condition_fails_the_arm_and_voids_the_run(self, kit: RunKit) -> None:
        class OneOfThree(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                raw = super().__call__(request)
                if request.key == m.SELF_CHECK_KEY and request.arm == m.ARM_MEMORY and request.slot_index != 0:
                    return m.RawSample(lines=raw.lines, exit_status=0, session_jsonl_text="no nonce here")
                return raw

        launcher = OneOfThree()
        result = kit.build(launcher).harness.run()

        summary = result.report["self_check"][m.ARM_MEMORY]
        assert (summary["samples"], summary["meets"], summary["passed"]) == (3, 1, False)
        assert result.verdict == "void"
        assert stage_of(launcher, m.STAGE_SMOKE) == []

    def test_a_none_sample_whose_transcript_was_never_read_does_not_meet_and_voids_the_run(self, kit: RunKit) -> None:
        class NoneTranscriptUnreadable(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                raw = super().__call__(request)
                if request.key == m.SELF_CHECK_KEY and request.arm == m.ARM_NONE:
                    return m.RawSample(lines=raw.lines, exit_status=0, session_jsonl_text=None)
                return raw

        result = kit.build(NoneTranscriptUnreadable()).harness.run()

        assert result.verdict == "void"
        assert result.report["self_check"][m.ARM_NONE]["meets"] == 0
        assert "none" in result.void_reasons[0]

    def test_a_none_sample_that_quotes_the_agent_briefing_bullet_leaks_the_user_file_and_voids_the_run(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(transcripts={**SELF_CHECK_TRANSCRIPTS, m.ARM_NONE: "the user file said BULLET-TEXT"})
        built = kit.build(launcher)

        result = built.harness.run()

        assert result.verdict == "void"
        assert "leaked" in result.void_reasons[0]
        assert result.report["self_check"][m.ARM_NONE]["leak"] is True


class TestCostAccounting:
    def test_self_check_and_smoke_run_under_the_budget_cap_and_calibration_under_ten_times_smoke_max(self, kit: RunKit) -> None:
        class Priced(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                self.cost = 0.5 if (request.stage == m.STAGE_SMOKE and request.slot_index == 1) else 0.1
                return super().__call__(request)

        launcher = Priced()
        kit.build(launcher, budget=4.0, workers=1).harness.run()

        self_check_caps = {request.budget_cap_usd for request in stage_of(launcher, m.STAGE_SELF_CHECK)}
        smoke_caps = {request.budget_cap_usd for request in stage_of(launcher, m.STAGE_SMOKE)}
        calibration_caps = {request.budget_cap_usd for request in stage_of(launcher, m.STAGE_CALIBRATION)}
        assert self_check_caps == {4.0}
        assert smoke_caps == {4.0}
        assert calibration_caps == {5.0}

    def test_a_smoke_that_reports_a_zero_charge_voids_the_run_before_any_calibration_sample(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(cost=0.0)
        built = kit.build(launcher, budget=4.0, workers=1)

        result = built.harness.run()

        assert result.verdict == "void"
        assert any("calibration cost projection undefined" in reason for reason in result.void_reasons)
        assert stage_of(launcher, m.STAGE_CALIBRATION) == []
        assert not any(text.startswith("calibration projection") for text in built.notices)
        assert any("smoke reported no positive charge" in notice for notice in built.notices)

    def test_a_smoke_with_no_reported_charge_leaves_the_later_stages_at_the_budget_cap(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(cost=None)
        built = kit.build(launcher, budget=4.0, workers=1, stop=5000.0)

        built.harness.run()

        calibration_caps = {request.budget_cap_usd for request in stage_of(launcher, m.STAGE_CALIBRATION)}
        assert calibration_caps == {4.0}
        assert any("smoke reported no positive charge" in notice for notice in built.notices)

    def test_every_attempts_charge_counts_toward_the_spend_including_retried_faults(self, kit: RunKit) -> None:
        faulted: set[tuple] = set()

        def timeout_once_per_calibration_slot(request: m.SampleRequest, count: int) -> m.RawSample | None:
            key = (request.stage, request.arm, request.key, request.slot_index)
            if request.stage == m.STAGE_CALIBRATION and request.arm == m.ARM_NONE and request.key == "O1" and key not in faulted:
                faulted.add(key)
                return timeout_sample()
            return None

        built = kit.build(ScriptedLauncher(fault=timeout_once_per_calibration_slot), budget=2.0)

        built.harness.run()

        finishes = finish_lines(built)
        faults = [entry for entry in finishes if entry["class"] == m.CLS_FAULT]
        calibration_cap = round(0.1 * msmr.BUDGET_CAP_MULTIPLIER, 2)
        assert faults
        assert all((entry["charge_usd"], entry["cost_source"]) == (calibration_cap, m.COST_SOURCE_CAP) for entry in faults)
        assert built.harness.spend_usd == pytest.approx(sum(entry["charge_usd"] for entry in finishes))
        assert built.harness.cost_source_counts[m.COST_SOURCE_CAP] == len(faults)

    def test_a_denied_sample_is_never_charged_the_cap_or_zero(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(cost=0.037))

        built.harness.run()

        assert {entry["charge_usd"] for entry in finish_lines(built)} == {0.037}
        assert {entry["cost_source"] for entry in finish_lines(built)} == {m.COST_SOURCE_REPORTED}

    def test_the_cumulative_stop_voids_the_run_and_flushes_the_log(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        built = kit.build(launcher, stop=1.0, workers=1)

        result = built.harness.run()

        assert result.verdict == "void"
        assert isinstance(result.halt, m.StopReached)
        assert built.harness.spend_usd >= 1.0
        assert len(launcher.requests) <= 12
        assert json.loads(built.log.path.read_text().splitlines()[-1])["kind"] == "end"

    def test_with_n_workers_the_run_overshoots_the_stop_by_at_most_n_in_flight_samples(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        built = kit.build(launcher, stop=1.0, workers=4)

        result = built.harness.run()

        assert isinstance(result.halt, m.StopReached)
        assert 1.0 <= built.harness.spend_usd <= 1.0 + 4 * 0.1 + 1e-9

    def test_the_projection_and_ceiling_print_after_the_smoke_and_before_the_first_scored_sample(self, kit: RunKit) -> None:
        notices: list[tuple[str, int]] = []
        launcher = ScriptedLauncher()
        built = kit.build(launcher)
        built.harness._notify = lambda text: notices.append((text, len(launcher.requests)))

        built.harness.run()

        text, launches_when_printed = notices[0]
        assert "calibration projection" in text
        assert "cumulative stop 300.00" in text
        assert launches_when_printed == 2 * m.SELF_CHECK_SAMPLES + 2 * 4 * m.SMOKE_SAMPLES_PER_CELL
        assert not any(request.stage == m.STAGE_CALIBRATION for request in launcher.requests[:launches_when_printed])

    def test_a_calibration_projected_above_the_remaining_stop_is_refused_before_any_scored_sample(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()
        built = kit.build(launcher, stop=5.0)

        result = built.harness.run()

        assert result.verdict == "refused"
        assert isinstance(result.halt, m.StageRefused)
        assert stage_of(launcher, m.STAGE_CALIBRATION) == []

    def test_a_refused_stage_names_the_log_to_resume_with_a_higher_stop(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(), stop=5.0)

        result = built.harness.run()

        assert isinstance(result.halt, m.StageRefused)
        assert result.halt.status_line().endswith(f"raise --cumulative-stop-usd; resume with --resume {built.log.path}")

    def test_a_reached_stop_names_the_log_to_resume_with_a_higher_stop(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(), stop=1.0, workers=1)

        result = built.harness.run()

        assert isinstance(result.halt, m.StopReached)
        assert result.halt.status_line().endswith(
            f"raise --cumulative-stop-usd to continue; resume with --resume {built.log.path}"
        )

    def test_the_calibration_projection_prices_each_cell_at_its_smoke_mean_from_target_to_cap(self) -> None:
        cells = (m.CellPlan(m.ARM_MEMORY, "O1", 30, 45), m.CellPlan(m.ARM_NONE, "O1", 30, 45))

        projection = m.calibration_projection(cells, {"memory/O1": 0.1, "none/O1": 0.2})

        assert projection["low_usd"] == pytest.approx(30 * 0.1 + 30 * 0.2)
        assert projection["high_usd"] == pytest.approx(45 * 0.1 + 45 * 0.2)
        assert (projection["samples_low"], projection["samples_high"]) == (60, 90)
        assert projection["imputed_cells"] == 0

    def test_the_gate_projection_prices_both_arms_at_the_memory_mean_with_the_extension_share_and_a_row_range(self) -> None:
        tally = m.CellTally(scored=30, adherent=27, attempts=40, mean_charge_usd=0.2)
        reach = 30 / 40

        projection = m.gate_projection([("O1", tally, 100)], 1)

        base = 2 * (100 / reach) * 0.2
        assert projection["expected_usd"] == pytest.approx(base * 1.10)
        assert projection["extension_inclusive_usd"] == pytest.approx(base * 1.5)
        assert projection["scored_samples"] == 200
        assert projection["row_range_low_usd"] < projection["expected_usd"] < projection["row_range_high_usd"]

    @pytest.mark.parametrize(("entered_count", "share"), [(1, 0.10), (2, 0.05), (3, 0.05), (4, 0.03)])
    def test_the_extension_share_follows_the_number_of_entered_observables(self, entered_count, share) -> None:
        tally = m.CellTally(scored=30, adherent=27, attempts=30, mean_charge_usd=1.0)
        entered = [(f"O{n}", tally, 50) for n in range(1, entered_count + 1)]

        projection = m.gate_projection(entered, entered_count)

        assert projection["expected_usd"] == pytest.approx(2 * 50 * entered_count * (1 + share) / (30 / 30))

    def test_the_gate_projection_refuses_to_price_a_count_of_entered_observables_with_no_extension_share(self) -> None:
        tally = m.CellTally(scored=30, adherent=27, attempts=30, mean_charge_usd=1.0)

        with pytest.raises(ValueError, match="no extension share for 5 entered observables"):
            m.gate_projection([("O1", tally, 50)], 5)

    @pytest.mark.parametrize("mean_charge_usd", [None, 0.0], ids=["no_mean", "zero_mean"])
    def test_the_gate_projection_refuses_to_price_an_entered_observable_with_no_positive_mean_charge(
        self, mean_charge_usd: float | None,
    ) -> None:
        tally = m.CellTally(scored=30, adherent=27, attempts=30, mean_charge_usd=mean_charge_usd)

        with pytest.raises(m.UndefinedProjectionPrice, match="no positive mean charge"):
            m.gate_projection([("O1", tally, 50)], 1)

    @pytest.mark.parametrize("mean_charge_usd", [None, 0.0], ids=["no_mean", "zero_mean"])
    def test_the_calibration_projection_refuses_to_price_a_smoke_with_no_positive_mean_charge(
        self, mean_charge_usd: float | None,
    ) -> None:
        cells = (m.CellPlan(m.ARM_MEMORY, "O1", 30, 45),)

        with pytest.raises(m.UndefinedProjectionPrice, match="no positive mean charge"):
            m.calibration_projection(cells, {"memory/O1": mean_charge_usd})

    @pytest.mark.parametrize("unpriced_mean", [None, 0.0], ids=["no_mean", "zero_mean"])
    def test_the_calibration_projection_prices_a_cell_with_no_positive_smoke_mean_at_the_average_of_the_others(
        self, unpriced_mean: float | None,
    ) -> None:
        cells = (
            m.CellPlan(m.ARM_MEMORY, "O1", 30, 45),
            m.CellPlan(m.ARM_NONE, "O1", 30, 45),
            m.CellPlan(m.ARM_MEMORY, "O2", 30, 45),
        )

        projection = m.calibration_projection(
            cells, {"memory/O1": unpriced_mean, "none/O1": 0.1, "memory/O2": 0.3},
        )

        average_of_measured = (0.1 + 0.3) / 2
        assert projection["low_usd"] == pytest.approx(30 * (average_of_measured + 0.1 + 0.3))
        assert projection["high_usd"] == pytest.approx(45 * (average_of_measured + 0.1 + 0.3))
        assert projection["imputed_cells"] == 1

    def test_the_calibration_notice_states_how_many_cells_were_priced_by_substitution(self) -> None:
        cells = (m.CellPlan(m.ARM_MEMORY, "O1", 30, 45), m.CellPlan(m.ARM_NONE, "O1", 30, 45))
        projection = m.calibration_projection(cells, {"memory/O1": 0.0, "none/O1": 0.2})

        notice = m.format_calibration_notice(projection, 300.0, 250.0)

        assert "cells priced at the average of the others, not measured: 1" in notice

    def test_the_calibration_notice_claims_no_substitution_when_every_cell_was_measured(self) -> None:
        cells = (m.CellPlan(m.ARM_MEMORY, "O1", 30, 45), m.CellPlan(m.ARM_NONE, "O1", 30, 45))
        projection = m.calibration_projection(cells, {"memory/O1": 0.1, "none/O1": 0.2})

        notice = m.format_calibration_notice(projection, 300.0, 250.0)

        assert "average of the others" not in notice

    def test_a_value_error_from_the_sizing_math_is_not_taken_for_an_undefined_price(
        self, kit: RunKit, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def broken_extension_target(target: int) -> int:
            raise ValueError("sizing arithmetic broke")

        monkeypatch.setattr(m, "extension_target", broken_extension_target)
        built = kit.build(ScriptedLauncher(gate_rates()), phase=m.PHASE_GATE)

        with pytest.raises(ValueError, match="sizing arithmetic broke") as raised:
            built.harness.run()

        assert not isinstance(raised.value, m.UndefinedProjectionPrice)
        assert built.harness.void_reasons == []

    def test_an_undefined_gate_projection_voids_the_run_before_the_planned_look(
        self, kit: RunKit, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(m, "EXTENSION_SHARE_BY_K", {})
        launcher = ScriptedLauncher(gate_rates())

        result = kit.build(launcher, phase=m.PHASE_GATE).harness.run()

        assert result.verdict == "void"
        assert any("gate cost projection undefined" in reason for reason in result.void_reasons)
        assert not stage_of(launcher, m.STAGE_PLANNED_LOOK)

    @staticmethod
    def _attempt_cap_oracle(target: int, calibration_attempts: int) -> int:
        """Attempts that reach `target` at the Wilson lower bound (z = 1.96) of 30 scored in `calibration_attempts`."""
        z, reach = 1.96, 30 / calibration_attempts
        lower = (reach + z * z / (2 * calibration_attempts) - z * math.sqrt(
            reach * (1 - reach) / calibration_attempts + z * z / (4 * calibration_attempts**2)
        )) / (1 + z * z / calibration_attempts)
        return math.ceil(target / lower)

    def test_the_gate_projection_ceiling_prices_the_planned_look_and_extension_at_each_cells_attempt_cap(self) -> None:
        tally = m.CellTally(scored=30, adherent=27, attempts=40, mean_charge_usd=0.2)

        projection = m.gate_projection([("O1", tally, 100)], 1)

        planned_cap, extension_cap = self._attempt_cap_oracle(100, 40), self._attempt_cap_oracle(50, 40)
        assert (planned_cap, extension_cap) == (168, 84)
        assert projection["ceiling_attempts"] == 2 * (planned_cap + extension_cap)
        assert projection["ceiling_usd"] == pytest.approx(2 * (planned_cap + extension_cap) * 0.2)

    def test_the_gate_projection_ceiling_sums_every_entered_observable_at_its_own_cap_and_mean(self) -> None:
        first = m.CellTally(scored=30, adherent=27, attempts=40, mean_charge_usd=0.2)
        second = m.CellTally(scored=30, adherent=24, attempts=45, mean_charge_usd=0.5)

        projection = m.gate_projection([("O1", first, 100), ("O3", second, 86)], 2)

        capped = [
            (2 * self._attempt_cap_oracle(target, attempts), mean)
            for attempts, mean, planned in ((40, 0.2, 100), (45, 0.5, 86))
            for target in (planned, math.ceil(planned * 0.5))
        ]
        assert projection["ceiling_attempts"] == sum(attempts for attempts, _ in capped)
        assert projection["ceiling_usd"] == pytest.approx(sum(attempts * mean for attempts, mean in capped))

    def test_a_run_whose_remaining_stop_falls_between_the_extension_inclusive_projection_and_the_ceiling_is_not_refused(
        self, kit: RunKit,
    ) -> None:
        reference = kit.build(ScriptedLauncher(gate_rates()), phase=m.PHASE_GATE, log_name="reference.jsonl")
        reference.harness.run()
        gate = reference.harness.projection["gate"]
        spent_before_the_planned_look = sum(
            entry["charge_usd"] for entry in finish_lines(reference)
            if entry["slot"].split("/")[0] in {m.STAGE_SELF_CHECK, m.STAGE_SMOKE, m.STAGE_CALIBRATION}
        )
        stop = spent_before_the_planned_look + (gate["extension_inclusive_usd"] + gate["ceiling_usd"]) / 2
        launcher = ScriptedLauncher(gate_rates())

        result = kit.build(launcher, phase=m.PHASE_GATE, log_name="between.jsonl", stop=stop).harness.run()

        assert gate["extension_inclusive_usd"] < gate["ceiling_usd"]
        assert not isinstance(result.halt, m.StageRefused)
        assert stage_of(launcher, m.STAGE_PLANNED_LOOK)

    def test_the_gate_notice_prints_the_attempt_cap_ceiling_beside_the_expected_figure(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(gate_rates())
        built = kit.build(launcher, phase=m.PHASE_GATE)

        built.harness.run()

        projection = built.harness.projection["gate"]
        [notice] = [text for text in built.notices if text.startswith("gate projection")]
        assert f"over {projection['ceiling_attempts']} attempts" in notice
        assert f"attempt-cap ceiling with extension {projection['ceiling_usd']:.2f}" in notice


class TestFaultsAndPauses:
    def test_a_fault_is_retried_with_exponential_backoff_and_counts_toward_no_attempt_or_reach_rate(self, kit: RunKit) -> None:
        def rate_limit_first_calibration_launch_twice(request: m.SampleRequest, count: int) -> m.RawSample | None:
            first_slot = request.stage == m.STAGE_CALIBRATION and request.arm == m.ARM_MEMORY and request.key == "O2"
            return rate_limit_sample() if first_slot and request.slot_index == 0 and request.attempt_no <= 2 else None

        built = kit.build(ScriptedLauncher(fault=rate_limit_first_calibration_launch_twice), workers=1)

        built.harness.run()

        assert built.sleeps == [m.fault_retry_delay_s(0), m.fault_retry_delay_s(1)] == [0.5, 1.0]
        memory = built.harness.calibration["O2"]["memory"]
        assert memory.scored == m.CALIBRATION_TARGET
        assert memory.attempts == m.CALIBRATION_TARGET
        assert m.CLS_FAULT not in memory.classes

    def test_backoff_is_capped_at_the_ceiling(self) -> None:
        assert [m.fault_retry_delay_s(n) for n in range(6)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]

    def test_a_fault_that_outlasts_the_retry_bound_pauses_the_run_with_a_resumable_status(self, kit: RunKit) -> None:
        def always_time_out(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return timeout_sample() if request.stage == m.STAGE_SMOKE else None

        built = kit.build(ScriptedLauncher(fault=always_time_out), workers=1)

        result = built.harness.run()

        assert result.verdict == "paused"
        assert isinstance(result.halt, m.RunPaused)
        assert result.halt.fault_class == m.FAULT_TIMEOUT
        assert f"--resume {built.log.path}" in result.halt.status_line()
        assert json.loads(built.log.path.read_text().splitlines()[-1])["verdict"] == "paused"
        assert len(finish_lines(built)) >= m.FAULT_MAX_RETRIES + 1

    def test_a_rate_limit_whose_reset_lies_beyond_the_backoff_ceiling_pauses_at_once(self, kit: RunKit) -> None:
        now = 1_000_000.0
        reset = now + 3600

        def limited(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return rate_limit_sample(reset) if request.stage == m.STAGE_SMOKE else None

        built = kit.build(ScriptedLauncher(fault=limited), workers=1)
        built.harness._clock = lambda: now

        result = built.harness.run()

        assert isinstance(result.halt, m.RunPaused)
        assert "resets at" in result.halt.status_line()
        assert built.sleeps == []

    def test_a_rate_limit_resetting_within_the_backoff_ceiling_is_retried(self, kit: RunKit) -> None:
        now = 1_000_000.0

        def limited_once(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if request.stage == m.STAGE_SMOKE and request.attempt_no == 1 and request.slot_index == 0:
                return rate_limit_sample(now + 3)
            return None

        built = kit.build(ScriptedLauncher(fault=limited_once), workers=1)
        built.harness._clock = lambda: now

        result = built.harness.run()

        assert result.verdict == m.VERDICT_CALIBRATED
        assert built.sleeps

    def test_five_consecutive_faults_pause_the_run(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher())
        outcome = m.AttemptOutcome(m.CLS_FAULT, 0.0, m.COST_SOURCE_NONE, fault_class=m.FAULT_OVERLOAD)

        paused = [built.harness._after_fault(outcome, 0) for _ in range(m.CONSECUTIVE_FAULT_PAUSE)]

        assert paused == [False, False, False, False, True]
        assert isinstance(built.harness._halt, m.RunPaused)
        assert "5 consecutive faults across slots" in built.harness._halt.status_line()

    def test_a_slot_out_of_retries_pauses_the_run_naming_the_retry_limit_not_the_consecutive_count(
        self, kit: RunKit,
    ) -> None:
        built = kit.build(ScriptedLauncher())
        outcome = m.AttemptOutcome(m.CLS_FAULT, 0.0, m.COST_SOURCE_NONE, fault_class=m.FAULT_OVERLOAD)

        paused = built.harness._after_fault(outcome, m.FAULT_MAX_RETRIES)

        assert paused is True
        halt_message = str(built.harness._halt)
        per_slot_wording = (
            f"{m.FAULT_MAX_RETRIES + 1} faults on one slot since this process started, "
            f"past its {m.FAULT_MAX_RETRIES} retries"
        )
        assert per_slot_wording in halt_message
        assert "consecutive" not in halt_message

    def test_a_success_between_faults_resets_the_consecutive_count_so_sporadic_faults_never_pause_the_run(
        self, kit: RunKit,
    ) -> None:
        def first_smoke_attempt_times_out(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return timeout_sample() if request.stage == m.STAGE_SMOKE and request.attempt_no == 1 else None

        built = kit.build(ScriptedLauncher(fault=first_smoke_attempt_times_out), workers=1)

        result = built.harness.run()

        smoke_faults = [entry for entry in finish_lines(built) if entry["class"] == m.CLS_FAULT]
        assert len(smoke_faults) > m.CONSECUTIVE_FAULT_PAUSE
        assert result.verdict == m.VERDICT_CALIBRATED

    def test_a_failure_matching_no_recorded_shape_pauses_the_run_rather_than_being_classified(self, kit: RunKit) -> None:
        def crash(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if request.stage == m.STAGE_SMOKE:
                return m.RawSample(lines=make_stream([], with_result=False), exit_status=139)
            return None

        built = kit.build(ScriptedLauncher(fault=crash), workers=1)

        result = built.harness.run()

        assert result.verdict == "paused"
        assert result.halt.fault_class == m.CLS_UNRECOGNIZED
        assert "exit status 139" in result.halt.status_line()

    def test_a_blocked_prompt_in_an_arm_without_the_hook_pauses_the_run(self, kit: RunKit) -> None:
        blocked = m.RawSample(lines=tuple((FIXTURE_DIR / "blocked-prompt.jsonl").read_bytes().splitlines()), exit_status=0)

        def block_smoke(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return blocked if request.stage == m.STAGE_SMOKE else None

        result = kit.build(ScriptedLauncher(fault=block_smoke), workers=1).harness.run()

        assert result.verdict == "paused"
        assert result.halt.fault_class == m.CLS_BLOCKED

    def test_a_launch_failure_is_retried_as_a_fault(self, kit: RunKit) -> None:
        def fail_once(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return m.RawSample(launch_error="exec failed") if count == 1 else None

        built = kit.build(ScriptedLauncher(fault=fail_once), workers=1)

        result = built.harness.run()

        assert result.verdict == m.VERDICT_CALIBRATED
        assert finish_lines(built)[0]["fault_class"] == m.FAULT_LAUNCH

    def test_an_oserror_from_the_launcher_is_a_launch_fault(self, kit: RunKit) -> None:
        class Flaky(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                if not self.requests:
                    self.requests.append(request)
                    raise OSError("no such file")
                return super().__call__(request)

        built = kit.build(Flaky(), workers=1)

        result = built.harness.run()

        assert result.verdict == m.VERDICT_CALIBRATED
        assert finish_lines(built)[0]["fault_class"] == m.FAULT_LAUNCH


class TestDrift:
    def test_a_second_cli_version_seen_in_a_stream_halts_the_run_as_void_with_the_log_flushed(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(version=lambda request, count: CLI_VERSION if count <= 10 else "9.9.2")
        built = kit.build(launcher, workers=1)

        result = built.harness.run()

        assert result.verdict == "void"
        assert isinstance(result.halt, m.DriftHalt)
        assert "cli_version" in str(result.halt)
        assert len(launcher.requests) <= 12
        assert json.loads(built.log.path.read_text().splitlines()[-1])["verdict"] == "void"

    def test_a_second_resolved_model_halts_the_run(self, kit: RunKit) -> None:
        class SwitchesModel(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                raw = super().__call__(request)
                if len(self.requests) > 12:
                    return m.RawSample(
                        lines=tuple(line.replace(MODEL_ID.encode(), b"claude-other") for line in raw.lines), exit_status=0,
                        session_jsonl_text=raw.session_jsonl_text,
                    )
                return raw

        result = kit.build(SwitchesModel(), workers=1).harness.run()

        assert isinstance(result.halt, m.DriftHalt)
        assert "model" in str(result.halt)

    def test_a_second_environment_reading_at_a_stage_boundary_halts_before_the_next_sample(self, kit: RunKit) -> None:
        readings = iter([("cli 1", "commit-1"), ("cli 1", "commit-1"), ("cli 1", "commit-2")])
        launcher = ScriptedLauncher()
        built = kit.build(
            launcher, environment_reader=lambda: bench_runner.EnvironmentRecord(*next(readings)),
        )

        result = built.harness.run()

        assert isinstance(result.halt, m.DriftHalt)
        assert "ambient_config_commit" in str(result.halt)
        assert {request.stage for request in launcher.requests} == {m.STAGE_SELF_CHECK}


    @pytest.mark.parametrize(
        ("phase", "readings_before_the_verdict", "last_stage"),
        [(m.PHASE_CALIBRATE, 4, m.STAGE_CALIBRATION), (m.PHASE_GATE, 5, m.STAGE_PLANNED_LOOK)],
    )
    def test_a_config_commit_that_changes_during_the_last_stage_voids_the_run_instead_of_giving_a_verdict(
        self, kit: RunKit, phase, readings_before_the_verdict, last_stage,
    ) -> None:
        """The reads so far are the run's start and one per stage start; the next one is the verdict's own."""
        readings: list[str] = []

        def commit_changes_after_the_last_stage_began() -> bench_runner.EnvironmentRecord:
            readings.append("read")
            commit = "commit-2" if len(readings) > readings_before_the_verdict else "commit-1"
            return bench_runner.EnvironmentRecord("cli 1", commit)

        launcher = ScriptedLauncher(gate_rates())
        built = kit.build(launcher, phase=phase, environment_reader=commit_changes_after_the_last_stage_began)

        result = built.harness.run()

        assert result.verdict == m.RunVerdict.VOID.value
        assert isinstance(result.halt, m.DriftHalt)
        assert "ambient_config_commit" in str(result.halt)
        assert stage_of(launcher, last_stage)
        assert len(readings) == readings_before_the_verdict + 1


class TestDeliveryInTheGatePhase:
    def test_a_delivery_failure_during_the_smoke_halts_the_run_before_any_scored_sample(self, kit: RunKit) -> None:
        def cause_in_smoke(request: m.SampleRequest) -> dict | None:
            if request.stage == m.STAGE_SMOKE:
                return {part: m.MarkerState(False, f"{part} cause") for part in m.CONTENT_PARTS}
            return None

        launcher = ScriptedLauncher(markers=cause_in_smoke)
        built = kit.build(launcher, phase=m.PHASE_GATE)

        result = built.harness.run()

        assert result.verdict == "fail"
        assert isinstance(result.halt, m.DeliveryHalt)
        assert "cause" in str(result.halt)
        assert not any(request.stage in (m.STAGE_CALIBRATION, m.STAGE_PLANNED_LOOK) for request in launcher.requests)

    def test_a_blocked_hook_prompt_is_a_delivery_failure_never_a_non_adherent_sample(self, kit: RunKit) -> None:
        blocked = m.RawSample(lines=tuple((FIXTURE_DIR / "blocked-prompt.jsonl").read_bytes().splitlines()), exit_status=0)

        def block_hook_smoke(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return blocked if request.stage == m.STAGE_SMOKE and request.arm == m.ARM_HOOK else None

        built = kit.build(ScriptedLauncher(fault=block_hook_smoke), phase=m.PHASE_GATE)

        result = built.harness.run()

        assert result.verdict == "fail"
        assert all(entry["adherent"] is None for entry in finish_lines(built) if entry["class"] == m.CLS_DELIVERY_FAILURE)
        assert result.report["delivery_failures"]

    def test_a_hook_stream_without_the_emitted_text_halts_the_gate_run_despite_both_markers(self, kit: RunKit) -> None:
        def stream_without_the_text(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if request.arm != m.ARM_HOOK or request.key == m.SELF_CHECK_KEY:
                return None
            lines = make_stream(CALLS_BY_OUTCOME[(request.key, m.OUTCOME_ADHERENT)])
            return m.RawSample(lines=lines, exit_status=0, markers={part: m.MarkerState(True) for part in m.CONTENT_PARTS})

        launcher = ScriptedLauncher(fault=stream_without_the_text)
        built = kit.build(launcher, phase=m.PHASE_GATE)

        result = built.harness.run()

        assert result.verdict == "fail"
        assert isinstance(result.halt, m.DeliveryHalt)
        assert m.DELIVERY_RECEIPT_MISSING in str(result.halt)
        assert any(m.DELIVERY_RECEIPT_MISSING in failure for failure in result.report["delivery_failures"])
        assert not any(request.stage in (m.STAGE_CALIBRATION, m.STAGE_PLANNED_LOOK) for request in launcher.requests)

    def test_only_hook_samples_outside_the_self_check_are_delivery_checked(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(gate_rates())
        kit.build(launcher, phase=m.PHASE_GATE).harness.run()

        checked = {(r.arm, r.key == m.SELF_CHECK_KEY) for r in launcher.requests if r.delivery_check}
        assert checked == {(m.ARM_HOOK, False)}
        assert {r.arm for r in launcher.requests} == {m.ARM_MEMORY, m.ARM_NONE, m.ARM_HOOK}


def gate_rates(**overrides: float) -> dict[tuple[str, str], float]:
    """Only O1 and O3 separate `memory` from `none` (1.0 against 0.2), so two observables enter, each file keeps one, and
    a matching hook is sized at 110 scored samples per arm. Overrides are named `<arm>_<key>`, such as `hook_o3`."""
    rates = {(arm, key): 0.5 for arm in (m.ARM_MEMORY, m.ARM_NONE, m.ARM_HOOK) for key in ("O1", "O2", "O3", "O4")}
    for key in ("O1", "O3"):
        rates[(m.ARM_MEMORY, key)] = rates[(m.ARM_HOOK, key)] = 1.0
        rates[(m.ARM_NONE, key)] = 0.2
    for name, rate in overrides.items():
        arm, key = name.split("_")
        rates[(arm, key.upper())] = rate
    return rates


def hook_reach_halved(request: m.SampleRequest) -> float:
    return 0.5 if (request.arm == m.ARM_HOOK and request.stage == m.STAGE_PLANNED_LOOK) else 1.0


class TestGateComparison:
    def test_a_matching_hook_passes_with_the_scored_targets_sized_from_the_calibration(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(gate_rates())
        built = kit.build(launcher, phase=m.PHASE_GATE)

        result = built.harness.run()

        sizing = built.harness.sizing
        assert result.verdict == m.RunVerdict.PASS.value
        assert sizing["k"] == 2
        for key in ("O1", "O3"):
            memory_count = built.harness.calibration[key]["memory"].adherent
            assert sizing["targets"][key] == m.planned_look_target(memory_count, 2)
            calibration_attempts = built.harness.calibration[key]["memory"].attempts
            assert sizing["caps"][key] == m.gate_attempt_cap(sizing["targets"][key], calibration_attempts)
            gate = built.harness.gate_results[key]
            assert gate.hook.scored == gate.memory.scored == sizing["targets"][key]
            assert gate.verdict == m.VERDICT_PASS
        assert not stage_of(launcher, m.STAGE_EXTENSION)
        assert {r.key for r in stage_of(launcher, m.STAGE_PLANNED_LOOK)} == {"O1", "O3"}
        assert {r.arm for r in stage_of(launcher, m.STAGE_PLANNED_LOOK)} == {m.ARM_HOOK, m.ARM_MEMORY}

    def test_the_gate_phase_prints_its_projection_with_the_ceiling_before_the_planned_look(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(gate_rates()), phase=m.PHASE_GATE)

        built.harness.run()

        assert any("gate projection" in notice and "cumulative stop" in notice for notice in built.notices)

    def test_the_extension_runs_for_the_undecided_observable_only_and_may_end_inconclusive(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(gate_rates(hook_o3=0.9))
        built = kit.build(launcher, phase=m.PHASE_GATE)

        result = built.harness.run()

        extension = stage_of(launcher, m.STAGE_EXTENSION)
        assert {r.key for r in extension} == {"O3"}
        assert built.harness.gate_results["O1"].verdict == m.VERDICT_PASS
        assert built.harness.gate_results["O3"].verdict == m.VERDICT_INCONCLUSIVE
        assert built.harness.gate_results["O3"].final_look is True
        planned_target = built.harness.sizing["targets"]["O3"]
        assert built.harness.gate_results["O3"].hook.scored == planned_target + m.extension_target(planned_target)
        assert result.verdict == m.RunVerdict.INCONCLUSIVE.value

    def test_an_observable_whose_interval_lies_below_zero_with_a_drop_beyond_the_margin_fails_the_run(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(gate_rates(hook_o3=0.85))
        built = kit.build(launcher, phase=m.PHASE_GATE)

        result = built.harness.run()

        assert built.harness.gate_results["O3"].verdict == m.VERDICT_FAIL
        assert result.verdict == m.RunVerdict.FAIL.value
        assert not stage_of(launcher, m.STAGE_EXTENSION)

    def test_a_hook_arm_that_stops_short_of_its_target_while_memory_reaches_it_is_a_miss(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(gate_rates(), reach=hook_reach_halved), phase=m.PHASE_GATE)

        result = built.harness.run()

        gate = built.harness.gate_results["O1"]
        assert gate.miss is True
        assert gate.hook.stopped_short is True
        assert gate.memory.scored >= gate.target
        assert result.verdict == m.RunVerdict.MISS.value

    def test_a_hook_arm_that_reaches_its_target_but_with_a_much_lower_reach_than_memory_is_a_miss(self, kit: RunKit) -> None:
        def hook_reach_trails_memory(request: m.SampleRequest) -> float:
            if request.stage == m.STAGE_CALIBRATION:
                return 0.8
            return 0.8 if (request.arm == m.ARM_HOOK and request.stage == m.STAGE_PLANNED_LOOK) else 1.0

        built = kit.build(ScriptedLauncher(gate_rates(), reach=hook_reach_trails_memory), phase=m.PHASE_GATE)

        result = built.harness.run()

        gate = built.harness.gate_results["O1"]
        assert gate.hook.scored >= gate.target
        assert gate.hook.reach < gate.memory.reach - m.REACH_GAP_TOLERANCE
        assert gate.miss is True
        assert result.verdict == m.RunVerdict.MISS.value

    def test_a_hook_arm_that_reaches_its_target_with_a_reach_within_the_margin_of_memory_is_not_a_miss(self, kit: RunKit) -> None:
        def hook_reach_trails_memory_within_the_margin(request: m.SampleRequest) -> float:
            if request.stage == m.STAGE_CALIBRATION:
                return 0.8
            return 0.9 if (request.arm == m.ARM_HOOK and request.stage == m.STAGE_PLANNED_LOOK) else 1.0

        launcher = ScriptedLauncher(gate_rates(), reach=hook_reach_trails_memory_within_the_margin)
        built = kit.build(launcher, phase=m.PHASE_GATE)

        result = built.harness.run()

        gate = built.harness.gate_results["O1"]
        assert gate.hook.scored >= gate.target  # the first disjunct of the miss rule is false
        assert gate.hook.reach < gate.memory.reach  # a bare "hook reach below memory reach" test would flag it
        assert gate.hook.reach >= gate.memory.reach - m.REACH_GAP_TOLERANCE
        assert gate.miss is False
        assert result.verdict == m.RunVerdict.PASS.value

    def test_a_memory_arm_that_stops_short_of_its_target_voids_the_run(self, kit: RunKit) -> None:
        def memory_reach_collapses(request: m.SampleRequest) -> float:
            return 0.2 if (request.arm == m.ARM_MEMORY and request.stage == m.STAGE_PLANNED_LOOK) else 1.0

        built = kit.build(ScriptedLauncher(gate_rates(), reach=memory_reach_collapses), phase=m.PHASE_GATE)

        result = built.harness.run()

        assert result.verdict == m.RunVerdict.VOID.value
        assert any("memory arm stopped short" in reason for reason in result.void_reasons)

    def test_a_missing_marker_without_a_sidecar_is_retried_once_before_it_counts(self, kit: RunKit) -> None:
        def missing_on_first_attempt(request: m.SampleRequest) -> dict | None:
            first_slot = request.stage == m.STAGE_PLANNED_LOOK and request.key == "O1" and request.slot_index == 0
            if first_slot and request.attempt_no == 1:
                return {part: m.MarkerState(False) for part in m.CONTENT_PARTS}
            return None

        built = kit.build(ScriptedLauncher(gate_rates(), markers=missing_on_first_attempt), phase=m.PHASE_GATE)

        result = built.harness.run()

        assert result.verdict == m.RunVerdict.PASS.value
        assert result.report["delivery_retries"] == 1
        assert result.report["delivery_failures"] == []

    def test_a_marker_missing_on_both_attempts_is_a_delivery_failure_that_fails_the_run(self, kit: RunKit) -> None:
        def always_missing(request: m.SampleRequest) -> dict | None:
            if request.stage == m.STAGE_PLANNED_LOOK and request.key == "O1" and request.slot_index == 0:
                return {part: m.MarkerState(False) for part in m.CONTENT_PARTS}
            return None

        built = kit.build(ScriptedLauncher(gate_rates(), markers=always_missing), phase=m.PHASE_GATE)

        result = built.harness.run()

        assert result.verdict == m.RunVerdict.FAIL.value
        assert result.report["delivery_retries"] == 1
        assert any("planned-look/hook/O1/0" in failure for failure in result.report["delivery_failures"])

    def test_a_resume_keeps_the_delivery_retry_spent_so_a_marker_missing_again_is_a_delivery_failure(self, kit: RunKit) -> None:
        def is_target_slot(request: m.SampleRequest) -> bool:
            return request.stage == m.STAGE_PLANNED_LOOK and request.key == "O1" and request.slot_index == 0

        def missing_markers(request: m.SampleRequest) -> dict | None:
            return {part: m.MarkerState(False) for part in m.CONTENT_PARTS} if is_target_slot(request) else None

        def crash_on_second_attempt(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if is_target_slot(request) and request.attempt_no == 2:
                return m.RawSample(lines=make_stream([], with_result=False), exit_status=139)
            return None

        first = kit.build(
            ScriptedLauncher(gate_rates(), markers=missing_markers, fault=crash_on_second_attempt),
            phase=m.PHASE_GATE, workers=1, log_name="retry-spent.jsonl",
        )
        first_result = first.harness.run()
        first.release()
        resumed_launcher = ScriptedLauncher(gate_rates(), markers=missing_markers)

        resumed = kit.build(resumed_launcher, phase=m.PHASE_GATE, workers=1, log_name="retry-spent.jsonl", resume=True)
        result = resumed.harness.run()

        assert first_result.halt.fault_class == m.CLS_UNRECOGNIZED
        assert [r.attempt_no for r in resumed_launcher.requests if is_target_slot(r)] == [3]
        assert result.verdict == m.RunVerdict.FAIL.value
        assert result.report["delivery_retries"] == 1
        assert any("planned-look/hook/O1/0" in failure for failure in result.report["delivery_failures"])
        failure = next(entry for entry in finish_lines(resumed) if entry["class"] == m.CLS_DELIVERY_FAILURE)
        assert (failure["delivery"], failure["attempt"]) == (m.DELIVERY_MISSING, 3)

    def test_the_planned_look_is_refused_when_its_projected_high_exceeds_what_remains_of_the_stop(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(gate_rates())
        built = kit.build(launcher, phase=m.PHASE_GATE, stop=60.0)

        result = built.harness.run()

        assert result.verdict == "refused"
        assert stage_of(launcher, m.STAGE_CALIBRATION)
        assert not stage_of(launcher, m.STAGE_PLANNED_LOOK)
        assert isinstance(result.halt, m.StageRefused)
        assert result.halt.status_line().startswith("planned look refused")
        assert result.halt.status_line().endswith(f"raise --cumulative-stop-usd; resume with --resume {built.log.path}")

    def test_a_refused_planned_look_runs_when_its_log_is_resumed_with_a_stop_whose_remainder_clears_its_projected_high(
        self, kit: RunKit,
    ) -> None:
        # Both costs are independent of the stop, so a probe run measures the bounds the real stops are derived from.
        probe = kit.build(ScriptedLauncher(gate_rates()), phase=m.PHASE_GATE, log_name="probe.jsonl", stop=60.0)
        probe_result = probe.harness.run()
        assert isinstance(probe_result.halt, m.StageRefused)
        projected_high = probe.harness.projection["gate"]["extension_inclusive_usd"]
        spend_before_planned_look = probe.harness.spend_usd
        assert spend_before_planned_look > 0
        stop_above_projection_but_not_above_projection_plus_spend = projected_high + spend_before_planned_look / 2
        stop_above_projection_plus_spend = projected_high + spend_before_planned_look * 2
        first = kit.build(
            ScriptedLauncher(gate_rates()), phase=m.PHASE_GATE, log_name="refused.jsonl",
            stop=stop_above_projection_but_not_above_projection_plus_spend,
        )
        first_result = first.harness.run()
        first.release()
        resumed_launcher = ScriptedLauncher(gate_rates())

        resumed = kit.build(
            resumed_launcher, phase=m.PHASE_GATE, log_name="refused.jsonl", resume=True,
            stop=stop_above_projection_plus_spend,
        )
        resumed_result = resumed.harness.run()

        assert isinstance(first_result.halt, m.StageRefused)
        assert first_result.halt.status_line().startswith("planned look refused")
        assert not stage_of(resumed_launcher, m.STAGE_CALIBRATION)
        assert stage_of(resumed_launcher, m.STAGE_PLANNED_LOOK)
        assert resumed_result.halt is None
        assert resumed_result.verdict == m.RunVerdict.PASS.value


def blocked_prompt_sample(*, cost: float) -> m.RawSample:
    """The recorded blocked-prompt stream, with its result event reporting `cost`."""
    lines = [json.loads(line) for line in (FIXTURE_DIR / "blocked-prompt.jsonl").read_bytes().splitlines()]
    assert lines[-1]["type"] == "result"
    lines[-1]["total_cost_usd"] = cost
    return m.RawSample(lines=lines_of(lines), exit_status=0)


def start_line(stage: str, arm: str, key: str, index: int, attempt: int, *, cap_usd: float) -> dict:
    """The `start` line a harness writes before it launches an attempt."""
    return {
        "kind": "start", "slot": m.slot_key(stage, arm, key, index), "attempt": attempt, "stage": stage,
        "session_id": f"session-{stage}-{arm}-{key}-{index}-{attempt}", "project": "/unused", "arm": arm,
        "observable": key, "index": index, "wording": m.wording_index(index), "cap_usd": cap_usd,
    }


class TestResume:
    @staticmethod
    def _summaries(harness: m.Harness) -> dict:
        return {
            "calibration": {
                key: {arm: entry[arm].to_dict(include_dollars=False) for arm in ("memory", "none")}
                for key, entry in harness.calibration.items()
            },
            "gate": {
                key: {
                    "hook": result.hook.to_dict(include_dollars=False),
                    "memory": result.memory.to_dict(include_dollars=False), "verdict": result.verdict,
                }
                for key, result in harness.gate_results.items()
            },
        }

    @staticmethod
    def _finish_entries_so_far(log_path: Path) -> list[dict]:
        """The finish lines already in a log another thread is appending to; a half-written last line is skipped."""
        entries = []
        for line in log_path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry["kind"] == "finish":
                entries.append(entry)
        return entries

    def _interrupt_then_resume(
        self, kit: RunKit, *, phase: str, interrupt_at: Callable[[m.SampleRequest, int], bool], workers: int,
        stop: float = 300.0, before_launch: Callable[[m.SampleRequest, int], None] | None = None,
    ) -> tuple[Built, Built, ScriptedLauncher, ScriptedLauncher]:
        interrupted_launcher = ScriptedLauncher(gate_rates(), jitter_s=0.003)

        def interrupt(request: m.SampleRequest, count: int) -> None:
            if before_launch is not None:
                before_launch(request, count)
            if interrupt_at(request, count):
                msmr.abort_launches()
                raise msmr.LaunchAbortedError("test interrupt")

        interrupted_launcher.on_launch = interrupt
        first = kit.build(interrupted_launcher, phase=phase, workers=workers, log_name="resumable.jsonl", stop=stop)
        with pytest.raises(msmr.LaunchAbortedError):
            first.harness.run()
        msmr._launches_aborted.clear()
        first.release()
        resumed_launcher = ScriptedLauncher(gate_rates(), jitter_s=0.003)
        second = kit.build(resumed_launcher, phase=phase, workers=workers, log_name="resumable.jsonl", resume=True, stop=stop)
        second.harness.run()
        return first, second, interrupted_launcher, resumed_launcher

    def test_an_interrupt_at_a_stage_boundary_resumes_without_rerunning_finished_stages(self, kit: RunKit) -> None:
        baseline = kit.build(ScriptedLauncher(gate_rates()), log_name="baseline.jsonl")
        baseline.harness.run()

        first, second, _, resumed_launcher = self._interrupt_then_resume(
            kit, phase=m.PHASE_CALIBRATE, interrupt_at=lambda request, count: request.stage == m.STAGE_CALIBRATION, workers=3,
        )

        assert {request.stage for request in resumed_launcher.requests} == {m.STAGE_CALIBRATION}
        assert self._summaries(second.harness) == self._summaries(baseline.harness)

    def test_an_interrupt_mid_stage_with_out_of_order_completions_keeps_the_scored_set(self, kit: RunKit) -> None:
        baseline_launcher = ScriptedLauncher(gate_rates(), jitter_s=0.003)
        baseline = kit.build(baseline_launcher, phase=m.PHASE_GATE, workers=4, log_name="baseline.jsonl")
        baseline.harness.run()

        held_cell: list[tuple[str, str]] = []
        held_lock = threading.Lock()

        def hold_a_cells_first_slot_until_a_later_slot_of_that_cell_finishes(request: m.SampleRequest, count: int) -> None:
            if request.stage != m.STAGE_PLANNED_LOOK or request.slot_index != 0:
                return
            with held_lock:
                if held_cell:
                    return
                held_cell.append((request.arm, request.key))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                finished_later = [
                    entry for entry in self._finish_entries_so_far(kit.tmp_path / "resumable.jsonl")
                    if (entry["stage"], entry["arm"], entry["observable"]) == (request.stage, request.arm, request.key)
                    and entry["index"] > 0
                ]
                if finished_later:
                    return
                time.sleep(0.01)

        first, second, interrupted, resumed = self._interrupt_then_resume(
            kit, phase=m.PHASE_GATE, workers=4,
            interrupt_at=lambda request, count: request.stage == m.STAGE_PLANNED_LOOK and request.slot_index >= 40,
            before_launch=hold_a_cells_first_slot_until_a_later_slot_of_that_cell_finishes,
        )

        assert self._summaries(second.harness) == self._summaries(baseline.harness)
        entries = log_entries(second)
        interrupted_segment = entries[: next(position for position, entry in enumerate(entries) if entry["kind"] == "end")]
        planned_finishes = [
            entry for entry in interrupted_segment if entry["kind"] == "finish" and entry["stage"] == m.STAGE_PLANNED_LOOK
        ]
        assert any(
            earlier["index"] > later["index"] and (earlier["arm"], earlier["observable"]) == (later["arm"], later["observable"])
            for position, later in enumerate(planned_finishes) for earlier in planned_finishes[:position]
        )
        finished_slots = [(entry["slot"], entry["attempt"]) for entry in finish_lines(second)]
        assert finished_slots
        assert len(finished_slots) == len(set(finished_slots))
        assert len(resumed.requests) < len(baseline_launcher.requests)
        planned = second.harness.gate_results["O1"]
        assert planned.hook.scored == second.harness.sizing["targets"]["O1"]

    def test_a_resume_inside_a_stage_is_not_refused_on_the_projection_of_the_whole_stage(self, kit: RunKit) -> None:
        def interrupt_after_sixty_calibration_launches(request: m.SampleRequest, count: int) -> bool:
            return request.stage == m.STAGE_CALIBRATION and count > 66

        first, second, _, _ = self._interrupt_then_resume(
            kit, phase=m.PHASE_CALIBRATE, interrupt_at=interrupt_after_sixty_calibration_launches, workers=1, stop=40.0,
        )

        remaining_when_resumed = 40.0 - sum(entry["charge_usd"] for entry in finish_lines(first))
        assert remaining_when_resumed < second.harness.projection["calibration"]["high_usd"]
        assert second.harness.entered_keys() == ["O1", "O3"]

    def test_resume_continues_the_cumulative_spend_from_the_logged_total(self, kit: RunKit) -> None:
        first, second, _, _ = self._interrupt_then_resume(
            kit, phase=m.PHASE_CALIBRATE, interrupt_at=lambda request, count: request.stage == m.STAGE_CALIBRATION, workers=2,
        )

        logged_total = sum(entry["charge_usd"] for entry in finish_lines(second))
        assert second.harness.spend_usd == pytest.approx(logged_total)
        assert second.harness.spend_usd > first.harness.spend_usd

    def test_resume_removes_the_temp_project_of_a_started_slot_with_no_finish_line(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(gate_rates()), log_name="crashed.jsonl")
        built.harness.run()
        built.release()
        abandoned = kit.temp_root / (m.PROJECT_DIR_PREFIX + "abandoned")
        abandoned.mkdir()
        (abandoned / "marker").write_text("x")
        store = bench_runner.RunStore(kit.tmp_path / "crashed.jsonl.d", fixture_root=kit.temp_root.resolve())
        store.record_directory("calibration/memory/O1/99#1", abandoned, "")
        with open(kit.tmp_path / "crashed.jsonl", "a") as log_file:
            log_file.write(json.dumps(start_line("calibration", m.ARM_MEMORY, "O1", 99, 1, cap_usd=1.0)) + "\n")

        resumed = kit.build(ScriptedLauncher(gate_rates()), log_name="crashed.jsonl", resume=True)
        resumed.harness.run()

        assert not abandoned.exists()
        assert any("abandoned" in notice for notice in resumed.notices)

    @staticmethod
    def _attempt_record(cls: str) -> m.AttemptRecord:
        return m.AttemptRecord(
            stage=m.STAGE_SMOKE, arm=m.ARM_MEMORY, key="O1", index=0, attempt_no=1, wording=0, cls=cls, charge_usd=0.1,
            cost_source=m.COST_SOURCE_REPORTED, session_id="session-1",
        )

    @pytest.mark.parametrize(
        ("cls", "closes_slot"),
        [
            (m.CLS_REACHED, True), (m.CLS_UNREACHED, True), (m.CLS_DELIVERY_FAILURE, True),
            (m.CLS_FAULT, False), (m.CLS_DELIVERY_RETRY, False), (m.CLS_UNRECOGNIZED, False), (m.CLS_BLOCKED, False),
        ],
    )
    def test_only_an_attempt_that_neither_retried_nor_paused_the_run_closes_its_slot_on_resume(
        self, cls, closes_slot,
    ) -> None:
        assert m.resume_closes_slot(self._attempt_record(cls)) is closes_slot

    @pytest.mark.parametrize(
        ("paused_class", "paused_sample"),
        [
            (m.CLS_UNRECOGNIZED, m.RawSample(lines=make_stream([], with_result=False), exit_status=139)),
            (m.CLS_BLOCKED, blocked_prompt_sample(cost=0.25)),
        ],
    )
    def test_a_resume_reruns_the_slot_that_paused_the_run_and_still_counts_the_paused_attempts_charge(
        self, kit: RunKit, paused_class, paused_sample,
    ) -> None:
        def pause_in_smoke(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return paused_sample if request.stage == m.STAGE_SMOKE else None

        first = kit.build(ScriptedLauncher(fault=pause_in_smoke), workers=1, log_name="paused.jsonl")
        assert first.harness.run().halt.fault_class == paused_class
        first.release()
        paused_finish = next(entry for entry in finish_lines(first) if entry["class"] == paused_class)
        resumed_launcher = ScriptedLauncher()

        resumed = kit.build(resumed_launcher, workers=1, log_name="paused.jsonl", resume=True)
        result = resumed.harness.run()

        rerun = [r for r in resumed_launcher.requests if m.slot_key(r.stage, r.arm, r.key, r.slot_index) == paused_finish["slot"]]
        assert [r.attempt_no for r in rerun] == [2]
        assert result.verdict == m.VERDICT_CALIBRATED
        assert resumed.harness._finished[paused_finish["slot"]].attempt_no == 2
        assert resumed.harness.spend_usd == pytest.approx(sum(entry["charge_usd"] for entry in finish_lines(resumed)))
        assert paused_finish["charge_usd"] > 0

    def test_a_stage_whose_only_attempt_paused_the_run_is_a_resumed_stage_not_refused_on_its_whole_projection(
        self, kit: RunKit,
    ) -> None:
        def crash_first_calibration_sample(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if request.stage == m.STAGE_CALIBRATION:
                return m.RawSample(lines=make_stream([], with_result=False), exit_status=139)
            return None

        first = kit.build(ScriptedLauncher(fault=crash_first_calibration_sample), workers=1, log_name="paused.jsonl", stop=40.0)
        assert first.harness.run().halt.fault_class == m.CLS_UNRECOGNIZED
        first.release()
        resumed_launcher = ScriptedLauncher()

        resumed = kit.build(resumed_launcher, workers=1, log_name="paused.jsonl", resume=True, stop=37.0)
        result = resumed.harness.run()

        assert 37.0 - first.harness.spend_usd < resumed.harness.projection["calibration"]["high_usd"]
        assert not isinstance(result.halt, m.StageRefused)
        assert stage_of(resumed_launcher, m.STAGE_CALIBRATION)

    def test_every_start_line_records_the_stage_cap_the_arm_and_the_wording_a_resume_needs(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(), workers=1, budget=4.0)

        built.harness.run()

        starts = [entry for entry in log_entries(built) if entry["kind"] == "start"]
        smoke_start = next(entry for entry in starts if entry["stage"] == m.STAGE_SMOKE)
        calibration_start = next(entry for entry in starts if entry["stage"] == m.STAGE_CALIBRATION)
        assert smoke_start["cap_usd"] == 4.0
        assert calibration_start["cap_usd"] == round(0.1 * msmr.BUDGET_CAP_MULTIPLIER, 2)
        assert {"arm", "observable", "index", "wording"} <= set(calibration_start)

    def test_a_resume_charges_the_cap_for_an_attempt_that_has_a_start_line_and_no_finish_line_and_reruns_its_slot(
        self, kit: RunKit,
    ) -> None:
        first, second, interrupted, resumed = self._interrupt_then_resume(
            kit, phase=m.PHASE_CALIBRATE, interrupt_at=lambda request, count: request.stage == m.STAGE_CALIBRATION, workers=1,
        )

        killed = [entry for entry in finish_lines(second) if entry["fault_class"] == m.FAULT_KILLED]
        interrupted_request = interrupted.requests[-1]
        slot = m.slot_key(
            interrupted_request.stage, interrupted_request.arm, interrupted_request.key, interrupted_request.slot_index,
        )
        assert [(entry["slot"], entry["attempt"], entry["class"]) for entry in killed] == [(slot, 1, m.CLS_FAULT)]
        assert (killed[0]["charge_usd"], killed[0]["cost_source"]) == (interrupted_request.budget_cap_usd, m.COST_SOURCE_CAP)
        assert [r.attempt_no for r in resumed.requests if m.slot_key(r.stage, r.arm, r.key, r.slot_index) == slot] == [2]
        assert second.harness.spend_usd == pytest.approx(sum(entry["charge_usd"] for entry in finish_lines(second)))

    def test_resuming_again_does_not_charge_a_killed_attempt_twice(self, kit: RunKit) -> None:
        _, second, _, _ = self._interrupt_then_resume(
            kit, phase=m.PHASE_CALIBRATE, interrupt_at=lambda request, count: request.stage == m.STAGE_CALIBRATION, workers=1,
        )
        second.release()
        spend_after_first_resume = second.harness.spend_usd

        third = kit.build(ScriptedLauncher(), workers=1, log_name="resumable.jsonl", resume=True)
        third.harness.run()

        killed = [entry for entry in finish_lines(third) if entry["fault_class"] == m.FAULT_KILLED]
        assert len(killed) == 1
        assert third.harness.spend_usd == pytest.approx(spend_after_first_resume)

    def test_a_killed_attempts_project_directory_is_still_swept_on_resume(self, kit: RunKit) -> None:
        first = kit.build(ScriptedLauncher(), log_name="crashed.jsonl", stop=0.5)
        first.harness.run()
        first.release()
        abandoned = kit.temp_root / (m.PROJECT_DIR_PREFIX + "abandoned")
        abandoned.mkdir()
        store = bench_runner.RunStore(kit.tmp_path / "crashed.jsonl.d", fixture_root=kit.temp_root.resolve())
        store.record_directory("calibration/memory/O1/99#1", abandoned, "")
        with open(kit.tmp_path / "crashed.jsonl", "a") as log_file:
            log_file.write(json.dumps(start_line("calibration", m.ARM_MEMORY, "O1", 99, 1, cap_usd=1.0)) + "\n")

        resumed = kit.build(ScriptedLauncher(), log_name="crashed.jsonl", resume=True, stop=300.0)
        resumed.harness.run()

        assert not abandoned.exists()
        assert any(entry["fault_class"] == m.FAULT_KILLED for entry in finish_lines(resumed))

    def _log_with_an_unfinished_attempt(self, kit: RunKit) -> str:
        """Ends a run early, appends a `start` line with no `finish` line, and returns that attempt's session id."""
        first = kit.build(ScriptedLauncher(), log_name="crashed.jsonl", stop=0.5)
        first.harness.run()
        first.release()
        unfinished = start_line("calibration", m.ARM_MEMORY, "O1", 99, 1, cap_usd=1.0)
        with open(kit.tmp_path / "crashed.jsonl", "a") as log_file:
            log_file.write(json.dumps(unfinished) + "\n")
        return unfinished["session_id"]

    def test_a_resume_refuses_while_the_child_of_an_unfinished_attempt_is_still_running(self, kit: RunKit) -> None:
        session_id = self._log_with_an_unfinished_attempt(kit)
        running_childs_directory = kit.temp_root / (m.PROJECT_DIR_PREFIX + "running-child")
        running_childs_directory.mkdir()
        store = bench_runner.RunStore(kit.tmp_path / "crashed.jsonl.d", fixture_root=kit.temp_root.resolve())
        store.record_directory("calibration/memory/O1/99#1", running_childs_directory, "")
        log_before = (kit.tmp_path / "crashed.jsonl").read_text()
        launcher = ScriptedLauncher()
        resumed = kit.build(
            launcher, log_name="crashed.jsonl", resume=True, stop=300.0,
            process_lister=lambda: f"/bin/zsh\nclaude -p --session-id {session_id} --max-turns 20\n",
        )

        with pytest.raises(m.ResumeRefused, match=session_id):
            resumed.harness.run()

        assert launcher.requests == []
        assert (kit.tmp_path / "crashed.jsonl").read_text() == log_before
        assert running_childs_directory.is_dir()

    def test_a_resume_refuses_while_a_real_detached_child_carries_an_unfinished_attempts_session_id_with_the_default_lister(
        self, kit: RunKit,
    ) -> None:
        session_id = self._log_with_an_unfinished_attempt(kit)
        log_before = (kit.tmp_path / "crashed.jsonl").read_text()
        launcher = ScriptedLauncher()
        resumed = kit.build(launcher, log_name="crashed.jsonl", resume=True, stop=300.0, process_lister=None)
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", session_id], start_new_session=True,
        )
        try:
            with pytest.raises(m.ResumeRefused, match=session_id):
                resumed.harness.run()
        finally:
            child.kill()
            child.wait()

        assert launcher.requests == []
        assert (kit.tmp_path / "crashed.jsonl").read_text() == log_before

    def test_a_resume_refuses_while_a_real_child_carries_the_session_id_after_a_real_samples_length_of_argv(
        self, kit: RunKit,
    ) -> None:
        """`build_command` puts --session-id after the prompt and the model flags, hundreds of characters in."""
        session_id = self._log_with_an_unfinished_attempt(kit)
        resumed = kit.build(ScriptedLauncher(), log_name="crashed.jsonl", resume=True, stop=300.0, process_lister=None)
        padding = "x" * 500
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", padding, session_id], start_new_session=True,
        )
        try:
            listing = m.list_process_commands()
            with pytest.raises(m.ResumeRefused, match=session_id):
                resumed.harness.run()
        finally:
            child.kill()
            child.wait()

        assert session_id in listing

    def test_a_resume_proceeds_when_no_running_process_carries_an_unfinished_attempts_session_id(
        self, kit: RunKit,
    ) -> None:
        self._log_with_an_unfinished_attempt(kit)
        resumed = kit.build(
            ScriptedLauncher(), log_name="crashed.jsonl", resume=True, stop=300.0,
            process_lister=lambda: "/bin/zsh\nclaude -p --session-id some-other-session\n",
        )

        resumed.harness.run()

        assert any(entry["fault_class"] == m.FAULT_KILLED for entry in finish_lines(resumed))

    @pytest.mark.parametrize(
        "failure",
        [
            FileNotFoundError("ps"), subprocess.CalledProcessError(1, "ps"), subprocess.TimeoutExpired("ps", 10),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        ],
        ids=["ps_missing", "ps_exits_nonzero", "ps_times_out", "listing_not_decodable"],
    )
    def test_a_resume_refuses_when_the_process_list_cannot_be_read(self, kit: RunKit, failure: Exception) -> None:
        self._log_with_an_unfinished_attempt(kit)
        log_before = (kit.tmp_path / "crashed.jsonl").read_text()

        def unreadable_process_list() -> str:
            raise failure

        resumed = kit.build(
            ScriptedLauncher(), log_name="crashed.jsonl", resume=True, stop=300.0, process_lister=unreadable_process_list,
        )

        with pytest.raises(m.ResumeRefused, match="cannot list processes"):
            resumed.harness.run()

        assert (kit.tmp_path / "crashed.jsonl").read_text() == log_before

    @pytest.mark.parametrize("listing", ["", "\n", "  \n\n"], ids=["empty", "newline", "blank_lines"])
    def test_a_resume_refuses_when_the_process_list_is_empty_because_that_cannot_rule_out_a_running_child(
        self, kit: RunKit, listing: str,
    ) -> None:
        self._log_with_an_unfinished_attempt(kit)
        resumed = kit.build(
            ScriptedLauncher(), log_name="crashed.jsonl", resume=True, stop=300.0, process_lister=lambda: listing,
        )

        with pytest.raises(m.ResumeRefused, match="listing was empty"):
            resumed.harness.run()

    def test_a_resume_of_a_log_with_no_unfinished_attempt_does_not_list_processes(self, kit: RunKit) -> None:
        first = kit.build(ScriptedLauncher(), log_name="clean.jsonl", stop=0.5)
        first.harness.run()
        first.release()

        def unexpected_process_list() -> str:
            raise AssertionError("listed processes with nothing unfinished")

        resumed = kit.build(
            ScriptedLauncher(), log_name="clean.jsonl", resume=True, stop=300.0, process_lister=unexpected_process_list,
        )

        resumed.harness.run()

    def test_a_resume_that_replays_a_complete_run_launches_nothing(self, kit: RunKit) -> None:
        first = kit.build(ScriptedLauncher(gate_rates()), log_name="done.jsonl")
        first.harness.run()
        first.release()
        replay_launcher = ScriptedLauncher(gate_rates())

        replayed = kit.build(replay_launcher, log_name="done.jsonl", resume=True)
        result = replayed.harness.run()

        assert replay_launcher.requests == []
        assert result.verdict == m.VERDICT_CALIBRATED

    @pytest.mark.parametrize(
        ("changed_option", "changed_header"), [("seed", "seed"), ("phase", "phase"), ("header_inputs", "inputs.base_claude_md")],
    )
    def test_a_resume_refuses_when_the_seed_phase_or_an_input_hash_differs(
        self, kit: RunKit, changed_option, changed_header,
    ) -> None:
        first = kit.build(ScriptedLauncher(gate_rates()), log_name="log.jsonl", stop=0.5)
        first.harness.run()
        first.release()
        options: dict = {"seed": 7, "phase": m.PHASE_CALIBRATE, "header_inputs": {"base_claude_md": "hash-1"}}
        options[changed_option] = {"seed": 8, "phase": m.PHASE_GATE, "header_inputs": {"base_claude_md": "other"}}[changed_option]
        second = kit.build(ScriptedLauncher(gate_rates()), log_name="log.jsonl", resume=True, **options)

        with pytest.raises(m.ResumeRefused, match=changed_header):
            second.harness.run()

    def test_a_resume_refuses_when_the_cli_version_differs(self, kit: RunKit) -> None:
        first = kit.build(ScriptedLauncher(gate_rates()), log_name="log.jsonl", stop=0.5)
        first.harness.run()
        first.release()
        second = kit.build(
            ScriptedLauncher(gate_rates()), log_name="log.jsonl", resume=True,
            environment_reader=lambda: bench_runner.EnvironmentRecord("cli 2", "commit-1"),
        )

        with pytest.raises(m.ResumeRefused, match="cli_version"):
            second.harness.run()


class TestGitCommitPatternBacktracking:
    @pytest.mark.timing
    @pytest.mark.parametrize("option_run", AMBIGUOUS_OPTION_RUNS)
    def test_a_long_option_run_with_no_commit_fails_to_match_quickly(self, option_run: str) -> None:
        started = time.monotonic()

        matched = re.search(m.GIT_COMMIT_PATTERN, f"\tgit {option_run}status")

        assert matched is None
        assert time.monotonic() - started < MAX_NO_MATCH_SECONDS

    @pytest.mark.timing
    @pytest.mark.parametrize("option_run", VALUED_OPTION_RUNS)
    def test_a_long_valued_option_run_with_no_commit_fails_to_match_quickly(self, option_run: str) -> None:
        started = time.monotonic()

        matched = re.search(m.GIT_COMMIT_PATTERN, f"\tgit {option_run}status")

        assert matched is None
        assert time.monotonic() - started < MAX_NO_MATCH_SECONDS

    @pytest.mark.parametrize("option_run", VALUED_OPTION_RUNS)
    def test_a_long_option_run_ending_in_commit_still_matches(self, option_run: str) -> None:
        assert re.search(m.GIT_COMMIT_PATTERN, f"git {option_run}commit -m x")


class TestListProcessCommands:
    @pytest.mark.parametrize("token_suffix", [b"", b"\xff"], ids=["ascii_argv", "argv_with_undecodable_byte"])
    def test_lists_the_command_line_of_a_detached_child_until_it_is_reaped(self, token_suffix: bytes) -> None:
        token = f"listing-contract-{uuid.uuid4()}".encode()
        child = subprocess.Popen(
            [os.fsencode(sys.executable), b"-c", b"import time; time.sleep(60)", token + token_suffix], start_new_session=True,
        )
        try:
            listing_while_running = m.list_process_commands()
        finally:
            child.kill()
            child.wait()
        listing_after_reaping = m.list_process_commands()

        assert token.decode() in listing_while_running
        assert token.decode() not in listing_after_reaping

    def test_a_listing_with_bytes_that_are_not_utf8_still_decodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        listing = b"/bin/zsh\nclaude -p --session-id abc\xff\n"
        monkeypatch.setattr(
            m.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=listing, stderr=b""),
        )

        assert "claude -p --session-id abc" in m.list_process_commands()


class TestInterruptibleSleep:
    def test_waits_out_the_whole_delay_while_no_abort_was_requested(self) -> None:
        delay_s = 0.05
        msmr._launches_aborted.clear()
        start = time.monotonic()

        m.interruptible_sleep(delay_s)

        assert time.monotonic() - start >= delay_s

    @pytest.mark.timing
    def test_raises_at_once_instead_of_waiting_out_the_delay_once_an_abort_was_requested(self) -> None:
        msmr.abort_launches()
        start = time.monotonic()

        try:
            with pytest.raises(msmr.LaunchAbortedError):
                m.interruptible_sleep(60)
        finally:
            msmr._launches_aborted.clear()

        assert time.monotonic() - start < 1


class TestInterrupts:
    @pytest.mark.timing
    def test_a_sigterm_mid_stage_ends_every_child_and_removes_every_project_and_store(self, kit: RunKit, tmp_path: Path) -> None:
        pid_dir = tmp_path / "pids"
        pid_dir.mkdir()
        grandchild_pid_dir = tmp_path / "grandchild-pids"
        grandchild_pid_dir.mkdir()
        projects_root = tmp_path / "projects"

        def sleeping_child(request: m.SampleRequest) -> m.RawSample:
            store_dir = projects_root / str(request.project_dir).replace("/", "-")
            store_dir.mkdir(parents=True)
            (store_dir / f"{request.session_id}.jsonl").write_text("{}\n")
            pid_file = pid_dir / request.session_id
            grandchild_pid_file = grandchild_pid_dir / request.session_id
            # The shell's pid file is written last, so its presence means the grandchild's pid file exists too.
            lines, timed_out = msmr._run_claude_to_completion(
                ["sh", "-c", 'sleep 60 & echo $! > "$1"; echo $$ > "$0"; wait', str(pid_file), str(grandchild_pid_file)],
                request.project_dir, 120,
            )
            return m.RawSample(lines=tuple(lines), timed_out=timed_out)

        built = kit.build(sleeping_child, workers=2)
        m.install_termination_handlers()
        # Without a handler the real SIGTERM below would end the whole pytest process instead of failing this test.
        assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL

        cancelled = threading.Event()
        alive_before_sigterm: dict[int, bool] = {}

        def read_pids(directory: Path) -> list[int]:
            return [int(path.read_text()) for path in directory.glob("*")]

        def terminate_once_two_children_run() -> None:
            deadline = time.monotonic() + 10
            while len(list(pid_dir.glob("*"))) < 2 and time.monotonic() < deadline:
                if cancelled.wait(0.02):
                    return
            if cancelled.wait(0.2):
                return
            try:
                for pid in read_pids(pid_dir) + read_pids(grandchild_pid_dir):
                    alive_before_sigterm[pid] = is_running(pid)
            finally:
                os.kill(os.getpid(), signal.SIGTERM)

        terminator = threading.Thread(target=terminate_once_two_children_run, daemon=True)
        terminator.start()

        try:
            with pytest.raises(KeyboardInterrupt):
                built.harness.run()
        finally:
            cancelled.set()
            terminator.join(timeout=5)

        pids = read_pids(pid_dir) + read_pids(grandchild_pid_dir)
        assert len(pids) == 4
        assert alive_before_sigterm == dict.fromkeys(pids, True)
        deadline = time.monotonic() + 5
        while any(is_running(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not any(is_running(pid) for pid in pids)
        assert kit.leftover_projects() == []
        assert list(projects_root.glob("*")) == []
        assert msmr._launches_aborted.is_set()

    def test_termination_handlers_route_sighup_and_sigterm_to_keyboard_interrupt_and_keep_an_ignored_sighup(self) -> None:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        m.install_termination_handlers()

        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGTERM)

    def test_termination_handlers_replace_a_default_sighup_handler(self) -> None:
        signal.signal(signal.SIGHUP, signal.SIG_DFL)

        m.install_termination_handlers()

        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGHUP)


class TestKeepArtifacts:
    def test_rescoring_the_kept_streams_applies_the_current_scorer_to_each_observable_stream(self, tmp_path: Path) -> None:
        (tmp_path / "smoke-memory-O1-0-1.jsonl").write_bytes(b"\n".join(make_stream(O1_ADHERENT)))
        (tmp_path / "planned-look-hook-O4-3-1.jsonl").write_bytes(b"\n".join(make_stream(O4_NON_ADHERENT)))
        (tmp_path / "self-check-memory-self-check-0-1.jsonl").write_bytes(b"\n".join(make_stream([])))
        (tmp_path / "notes.txt").write_text("ignored")

        scores = m.rescore_kept_streams(tmp_path)

        assert {name: score.outcome for name, score in scores.items()} == {
            "smoke-memory-O1-0-1.jsonl": m.OUTCOME_ADHERENT, "planned-look-hook-O4-3-1.jsonl": m.OUTCOME_NON_ADHERENT,
        }

    def test_each_samples_raw_stream_lines_are_kept_owner_only(self, kit: RunKit, tmp_path: Path) -> None:
        keep = tmp_path / "kept"
        keep.mkdir()
        built = kit.build(ScriptedLauncher(), keep_artifacts_dir=keep, stop=1.0, workers=1)

        built.harness.run()

        kept = sorted(keep.glob("*.jsonl"))
        assert kept
        assert kept[0].name.startswith(m.STAGE_SELF_CHECK)
        assert kept[0].stat().st_mode & 0o777 == 0o600
        assert json.loads(kept[0].read_text().splitlines()[0])["subtype"] == "init"


    def test_a_kept_stream_never_overwrites_an_existing_file_of_the_same_name(self, kit: RunKit, tmp_path: Path) -> None:
        keep = tmp_path / "kept"
        keep.mkdir()
        earlier = keep / f"{m.STAGE_SELF_CHECK}-{m.ARM_MEMORY}-{m.SELF_CHECK_KEY}-0-1.jsonl"
        earlier.write_text("earlier run\n")
        built = kit.build(ScriptedLauncher(), keep_artifacts_dir=keep, workers=1)
        request = make_request(m.SELF_CHECK_KEY, m.ARM_MEMORY, stage=m.STAGE_SELF_CHECK)

        with pytest.raises(FileExistsError):
            built.harness._keep_artifacts(request, m.RawSample(lines=(b"{}",)))

        assert earlier.read_text() == "earlier run\n"


class TestKeptStreamCollisionDuringAResume:
    """A resume may reuse a --keep-artifacts directory, so a file left by another run can sit at a kept path."""

    @staticmethod
    def plant_at_the_first_launch(
        keep: Path, collided: list[tuple[str, int, Path]],
    ) -> Callable[[m.SampleRequest, int], None]:
        """Plants a file at the first launched sample's kept path and records which sample it collides with."""

        def plant(request: m.SampleRequest, count: int) -> None:
            if count == 1:
                name = f"{request.stage}-{request.arm}-{request.key}-{request.slot_index}-{request.attempt_no}.jsonl"
                (keep / name).write_text("earlier run\n")
                slot = m.slot_key(request.stage, request.arm, request.key, request.slot_index)
                collided.append((slot, request.attempt_no, keep / name))

        return plant

    def stopped_run_log(self, kit: RunKit, keep: Path) -> None:
        first = kit.build(ScriptedLauncher(cost=0.25), stop=0.5, workers=1, log_name="run.jsonl", keep_artifacts_dir=keep)
        first.harness.run()
        first.release()

    def test_a_file_already_at_a_kept_stream_path_pauses_the_run_after_logging_and_charging_the_sample(
        self, kit: RunKit, tmp_path: Path,
    ) -> None:
        keep = tmp_path / "kept"
        keep.mkdir()
        self.stopped_run_log(kit, keep)
        collided: list[tuple[str, int, Path]] = []
        launcher = ScriptedLauncher(cost=0.25, on_launch=self.plant_at_the_first_launch(keep, collided))
        resumed = kit.build(launcher, stop=300.0, workers=3, log_name="run.jsonl", resume=True, keep_artifacts_dir=keep)

        result = resumed.harness.run()

        ((slot, attempt, planted),) = collided
        entries = log_entries(resumed)
        finishes = finish_lines(resumed)
        assert isinstance(result.halt, m.RunPaused)
        assert result.halt.fault_class == m.PAUSE_KEPT_STREAM
        status = result.halt.status_line()
        assert planted.name in status
        assert "--keep-artifacts" in status
        assert f"--resume {resumed.log.path}" in status
        assert planted.read_text() == "earlier run\n"
        (collided_finish,) = [entry for entry in finishes if (entry["slot"], entry["attempt"]) == (slot, attempt)]
        assert collided_finish["charge_usd"] == pytest.approx(0.25)
        assert resumed.harness.spend_usd == pytest.approx(sum(entry["charge_usd"] for entry in finishes))
        assert all(entry["fault_class"] != m.FAULT_KILLED for entry in finishes)
        assert {(entry["slot"], entry["attempt"]) for entry in entries if entry["kind"] == "start"} == {
            (entry["slot"], entry["attempt"]) for entry in finishes
        }, "every start line has a finish line"
        assert entries[-1]["kind"] == "end"
        assert entries[-1]["verdict"] == m.RunPaused.label

    def test_a_later_resume_does_not_charge_the_collided_sample_a_second_time(self, kit: RunKit, tmp_path: Path) -> None:
        keep, second_keep = tmp_path / "kept", tmp_path / "second-kept"
        keep.mkdir()
        second_keep.mkdir()
        self.stopped_run_log(kit, keep)
        collided: list[tuple[str, int, Path]] = []
        paused = kit.build(
            ScriptedLauncher(cost=0.25, on_launch=self.plant_at_the_first_launch(keep, collided)),
            stop=300.0, workers=1, log_name="run.jsonl", resume=True, keep_artifacts_dir=keep,
        )
        paused.harness.run()
        paused.release()
        ((slot, attempt, _),) = collided
        charge_of_collided_sample = sum(
            entry["charge_usd"] for entry in finish_lines(paused) if (entry["slot"], entry["attempt"]) == (slot, attempt)
        )

        later_launcher = ScriptedLauncher(cost=0.25)
        later = kit.build(
            later_launcher, stop=300.0, workers=1, log_name="run.jsonl", resume=True, keep_artifacts_dir=second_keep,
        )
        later_result = later.harness.run()

        finishes = finish_lines(later)
        attempts = [(entry["slot"], entry["attempt"]) for entry in finishes]
        assert later_result.halt is None
        assert later_launcher.requests, "the later run launched samples, so the assertions below are not vacuous"
        assert charge_of_collided_sample == pytest.approx(0.25)
        assert len(attempts) == len(set(attempts))
        assert all(entry["fault_class"] != m.FAULT_KILLED for entry in finishes)
        assert later.harness.spend_usd == pytest.approx(sum(entry["charge_usd"] for entry in finishes))


# --- Report, JSON, CLI ---------------------------------------------------------------------------------------------------


class TestReport:
    def test_the_report_text_carries_rates_and_counts_but_no_dollar_figure_and_no_account_label(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(gate_rates()), phase=m.PHASE_GATE)

        result = built.harness.run()
        text = m.render_report(result.report, result.verdict)

        assert "$" not in text
        assert "account-label-xyz" not in text
        assert "mean_charge" not in text
        assert "account_label" not in json.dumps(result.report)
        assert "spend_usd" not in json.dumps(result.report)
        assert "project-level `CLAUDE.md`" in text
        assert "Run verdict: pass" in text

    def test_a_delivery_failure_prints_its_status_class_only_and_keeps_the_free_text_cause_out_of_the_report(
        self, kit: RunKit,
    ) -> None:
        home_root = "/" + "Users" + "/"
        cause = f"cannot write {home_root}someone/.local/state/claude-accounts/account-label-xyz/.main-session-rules.d/x"

        def sidecar_cause(request: m.SampleRequest) -> dict | None:
            if request.stage == m.STAGE_SMOKE:
                return {part: m.MarkerState(False, cause) for part in m.CONTENT_PARTS}
            return None

        built = kit.build(ScriptedLauncher(markers=sidecar_cause), phase=m.PHASE_GATE)

        result = built.harness.run()
        text = m.render_report(result.report, result.verdict)
        full = m.build_report_data(built.harness, include_dollars=True)

        assert "smoke/hook/" in text
        assert ": sidecar" in text
        assert home_root not in text
        assert "account-label-xyz" not in text
        assert "cannot write" not in text
        assert any(cause in line for line in full["delivery_failure_causes"])
        assert any(cause in entry["detail"] for entry in finish_lines(built) if entry["class"] == m.CLS_DELIVERY_FAILURE)

    def test_the_report_counts_faults_by_class_for_each_arm_and_observable_without_a_dollar_figure(self, kit: RunKit) -> None:
        def one_timeout_and_one_overload(request: m.SampleRequest, count: int) -> m.RawSample | None:
            in_cell = (request.stage, request.arm, request.key, request.slot_index, request.attempt_no)
            if in_cell == (m.STAGE_CALIBRATION, m.ARM_NONE, "O1", 0, 1):
                return timeout_sample()
            if in_cell == (m.STAGE_CALIBRATION, m.ARM_NONE, "O1", 1, 1):
                return m.RawSample(lines=make_stream([], is_error=True, api_error_status=529), exit_status=1)
            if in_cell == (m.STAGE_CALIBRATION, m.ARM_MEMORY, "O3", 0, 1):
                return timeout_sample()
            return None

        built = kit.build(ScriptedLauncher(fault=one_timeout_and_one_overload), workers=1)

        result = built.harness.run()
        text = m.render_report(result.report, result.verdict)

        assert result.report["fault_counts"] == {"memory/O3": {"timeout": 1}, "none/O1": {"overload": 1, "timeout": 1}}
        assert "none/O1: overload 1, timeout 1" in text
        assert "memory/O3: timeout 1" in text
        assert "$" not in text

    def test_a_run_with_no_fault_prints_no_fault_line(self, kit: RunKit) -> None:
        result = kit.build(ScriptedLauncher()).harness.run()

        assert result.report["fault_counts"] == {}
        assert "Faults by class" not in m.render_report(result.report, result.verdict)

    def test_the_self_check_answers_are_model_output_so_they_stay_out_of_the_report_and_reach_only_the_json_data(
        self, kit: RunKit,
    ) -> None:
        answer = "I was shown " + "/" + "home" + "/someone/.claude/CLAUDE.md"

        class Answering(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                raw = super().__call__(request)
                if request.key != m.SELF_CHECK_KEY:
                    return raw
                return m.RawSample(lines=make_stream([], result=answer), exit_status=0, session_jsonl_text=raw.session_jsonl_text)

        built = kit.build(Answering())

        result = built.harness.run()
        text = m.render_report(result.report, result.verdict)
        full = m.build_report_data(built.harness, include_dollars=True)

        assert answer not in text
        assert answer not in json.dumps(result.report)
        assert "answers" not in result.report["self_check"][m.ARM_MEMORY]
        assert full["self_check"][m.ARM_MEMORY]["answers"] == [answer] * m.SELF_CHECK_SAMPLES
        assert result.report["self_check"][m.ARM_MEMORY]["named_claude_md_path"] == m.SELF_CHECK_SAMPLES

    def test_the_json_data_adds_spend_projection_attempts_and_the_account_label(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(gate_rates()), phase=m.PHASE_GATE)
        built.harness.run()

        data = m.build_report_data(built.harness, include_dollars=True)

        assert data["account_label"] == "account-label-xyz"
        assert data["spend_usd"] == pytest.approx(built.harness.spend_usd)
        assert {"calibration", "gate"} <= set(data["projection"])
        assert len(data["attempts"]) == len(built.harness.attempt_records)
        assert "mean_charge_usd" in data["calibration"]["O1"]["memory"]

    def test_the_report_carries_coverage_scope_hashes_versions_and_cost_source_counts(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(), header_inputs={"scorer": "0123456789abcdef"})
        result = built.harness.run()

        text = m.render_report(result.report, result.verdict)

        assert "Coverage, measured: Code Review (O3)" in text
        assert "Scope of a pass:" in text
        assert "scorer=0123456789ab" in text
        assert f"CLI versions seen: {CLI_VERSION}" in text
        assert f"models seen: {MODEL_ID}" in text
        assert "Attempts per cost source: reported" in text
        assert "nominal dollars" in text

    def test_the_report_counts_attempt_classes_and_flags_unreached_samples_with_unscored_target_calls(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher(
            reach=lambda request: 0.8 if request.key == "O3" else 1.0,
            unreached_calls={"O3": [("Bash", {"command": "echo commit-graph"})]},
        )
        built = kit.build(launcher)

        result = built.harness.run()
        text = m.render_report(result.report, result.verdict)

        assert "unreached: none-scorable" in text
        assert "WARNING: unreached samples in memory, none hold target-tool calls the scorer did not score" in text

    def test_a_void_run_reports_its_reason_and_the_table_it_has(self, kit: RunKit) -> None:
        built = kit.build(ScriptedLauncher(separating_except("O3", "O4")))
        result = built.harness.run()

        text = m.render_report(result.report, result.verdict)

        assert "Run verdict: void" in text
        assert "Void: no entered observable for content file(s): conduct.md" in text
        assert "O3: entered no" in text


class TestCli:
    def test_list_prints_every_observable_with_its_wordings(self, capsys: pytest.CaptureFixture) -> None:
        assert m.main(["--list"]) == 0

        out = capsys.readouterr().out
        for observable in m.OBSERVABLES:
            assert observable.key in out
            assert observable.wordings[0] in out

    def test_dry_run_prints_the_gate_schedule_and_the_sizing_table_and_launches_nothing(
        self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(msmr, "_run_claude_to_completion", lambda *a, **k: pytest.fail("a dry run launched a child"))

        assert m.main(["--phase", "gate", "--dry-run"]) == 0

        out = capsys.readouterr().out
        assert "arms memory, none, hook" in out
        assert "memory 30 of 30: k=1 -> 86, k=2 -> 110, k=4 -> 133" in out
        assert "30 scored samples per observable within 45 attempts" in out

    def test_dry_run_for_calibrate_names_only_the_two_arms_that_need_no_new_hook(self, capsys: pytest.CaptureFixture) -> None:
        assert m.main(["--phase", "calibrate", "--dry-run"]) == 0

        out = capsys.readouterr().out
        assert "arms memory, none" in out
        assert "hook" not in out.split("self-check")[0]
        assert "planned look" not in out

    def test_a_run_without_a_phase_is_a_usage_error(self, capsys: pytest.CaptureFixture) -> None:
        assert m.main([]) == 2
        assert "--phase is required" in capsys.readouterr().err

    @pytest.mark.parametrize("extra", [[], ["--run-log", "a.jsonl", "--resume", "b.jsonl"]])
    def test_a_live_run_needs_exactly_one_of_run_log_and_resume(self, capsys: pytest.CaptureFixture, extra) -> None:
        assert m.main(["--phase", "calibrate", *extra]) == 2
        assert "exactly one of --run-log" in capsys.readouterr().err

    @pytest.mark.parametrize("flag", ["--json", "--keep-artifacts", "--run-log"])
    def test_an_output_path_inside_a_worktree_is_refused_before_anything_launches(
        self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag,
    ) -> None:
        monkeypatch.setattr(msmr, "_run_claude_to_completion", lambda *a, **k: pytest.fail("launched"))
        monkeypatch.setattr(m.mer, "_repo_roots_for_containment_check", lambda start_dir=None: (tmp_path / "worktree",))
        args = ["--phase", "calibrate", "--run-log", str(tmp_path / "worktree" / "run.jsonl")]
        if flag != "--run-log":
            args += [flag, str(tmp_path / "worktree" / "out")]

        assert m.main(args) == 2
        assert flag in capsys.readouterr().err

    def test_an_ambient_emitter_registration_aborts_naming_the_confound(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        config = tmp_path / "cfg"
        config.mkdir()
        (config / "settings.json").write_text(json.dumps(_settings_with(m.EXPECTED_REGISTRATIONS)))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        monkeypatch.setattr(msmr, "_run_claude_to_completion", lambda *a, **k: pytest.fail("launched"))
        monkeypatch.setattr(m.mer, "_repo_roots_for_containment_check", lambda start_dir=None: ())

        assert m.main(["--phase", "calibrate", "--run-log", str(tmp_path / "run.jsonl")]) == 2

        err = capsys.readouterr().err
        assert m.EMITTER_FILE in err
        assert "one-time" in err

    def test_the_parser_offers_every_flag_the_plan_lists(self) -> None:
        parser = m.build_arg_parser()
        flags = {option for action in parser._actions for option in action.option_strings}

        assert {
            "--phase", "--list", "--seed", "--model", "--base-ref", "--candidate-ref", "--dry-run", "--workers", "--timeout-s",
            "--budget-cap-usd", "--cumulative-stop-usd", "--json", "--keep-artifacts", "--run-log", "--resume",
        } <= flags

    def test_the_flag_defaults_come_from_the_helpers_the_plan_names(self) -> None:
        args = m.build_arg_parser().parse_args(["--phase", "gate"])

        assert args.workers == m.run_skill_evals.DEFAULT_WORKERS
        assert args.timeout_s == m.mer.DEFAULT_TIMEOUT_S
        assert args.budget_cap_usd == msmr.PER_RUN_BUDGET_CAP_USD
        assert args.cumulative_stop_usd == m.mer.DEFAULT_CUMULATIVE_STOP_USD
        assert args.candidate_ref == "HEAD"
        assert args.base_ref is None

    def test_the_help_shows_the_default_of_each_spend_and_pace_flag(self) -> None:
        actions = {action.dest: action for action in m.build_arg_parser()._actions}

        for dest, default in (
            ("workers", m.run_skill_evals.DEFAULT_WORKERS), ("timeout_s", m.mer.DEFAULT_TIMEOUT_S),
            ("budget_cap_usd", msmr.PER_RUN_BUDGET_CAP_USD), ("cumulative_stop_usd", m.mer.DEFAULT_CUMULATIVE_STOP_USD),
        ):
            assert f"(default {default})" in actions[dest].help % {"default": actions[dest].default}


# --- Delivery, resume and stop behavior under the scripted launcher ----------------------------------------------------


class TestDeliveryFailureStopsTheStage:
    @staticmethod
    def _missing_markers() -> dict:
        return {part: m.MarkerState(False) for part in m.CONTENT_PARTS}

    def test_a_delivery_failure_in_the_planned_look_launches_no_further_hook_attempt(self, kit: RunKit) -> None:
        def always_missing(request: m.SampleRequest) -> dict | None:
            first_hook_slot = request.stage == m.STAGE_PLANNED_LOOK and request.key == "O1" and request.slot_index == 0
            return self._missing_markers() if first_hook_slot else None

        launcher = ScriptedLauncher(gate_rates(), markers=always_missing)
        built = kit.build(launcher, phase=m.PHASE_GATE, workers=1)

        result = built.harness.run()

        failing = [
            position for position, request in enumerate(launcher.requests)
            if (request.stage, request.arm, request.key, request.slot_index) == (m.STAGE_PLANNED_LOOK, m.ARM_HOOK, "O1", 0)
        ]
        assert isinstance(result.halt, m.DeliveryHalt)
        assert result.verdict == m.RunVerdict.FAIL.value
        assert len(failing) == 2
        assert not any(request.arm == m.ARM_HOOK for request in launcher.requests[failing[-1] + 1:])

    def test_a_delivery_failure_in_the_extension_launches_no_further_hook_attempt(self, kit: RunKit) -> None:
        def missing_in_the_extension(request: m.SampleRequest) -> dict | None:
            return self._missing_markers() if request.stage == m.STAGE_EXTENSION else None

        launcher = ScriptedLauncher(gate_rates(hook_o3=0.9), markers=missing_in_the_extension)
        built = kit.build(launcher, phase=m.PHASE_GATE, workers=1)

        result = built.harness.run()

        hook_requests = [request for request in launcher.requests if request.arm == m.ARM_HOOK]
        assert isinstance(result.halt, m.DeliveryHalt)
        assert hook_requests[-1].stage == m.STAGE_EXTENSION
        # The first miss is retried once and the second miss fails the sample, so the stage ends on that attempt.
        assert [request.attempt_no for request in hook_requests[-2:]] == [1, 2]

    def test_a_resume_of_a_run_that_recorded_a_delivery_failure_halts_again_without_launching(self, kit: RunKit) -> None:
        def cause_in_smoke(request: m.SampleRequest) -> dict | None:
            return {part: m.MarkerState(False, "cause") for part in m.CONTENT_PARTS} if request.stage == m.STAGE_SMOKE else None

        first = kit.build(ScriptedLauncher(markers=cause_in_smoke), phase=m.PHASE_GATE, workers=1, log_name="failed.jsonl")
        first.harness.run()
        first.release()
        launcher = ScriptedLauncher()

        result = kit.build(launcher, phase=m.PHASE_GATE, workers=1, log_name="failed.jsonl", resume=True).harness.run()

        assert isinstance(result.halt, m.DeliveryHalt)
        assert launcher.requests == []


class TestSpendStopOnResume:
    def test_a_resume_of_a_run_already_at_the_stop_launches_nothing(self, kit: RunKit) -> None:
        first = kit.build(ScriptedLauncher(cost=0.25), stop=1.0, workers=1, log_name="stopped.jsonl")
        assert isinstance(first.harness.run().halt, m.StopReached)
        first.release()
        launcher = ScriptedLauncher(cost=0.25)

        resumed = kit.build(launcher, stop=1.0, workers=1, log_name="stopped.jsonl", resume=True)
        result = resumed.harness.run()

        assert launcher.requests == []
        assert isinstance(result.halt, m.StopReached)
        assert result.verdict == m.RunVerdict.VOID.value

    def test_a_resume_with_a_raised_stop_continues(self, kit: RunKit) -> None:
        first = kit.build(ScriptedLauncher(cost=0.25), stop=1.0, workers=1, log_name="stopped.jsonl")
        first.harness.run()
        first.release()
        launcher = ScriptedLauncher(cost=0.25)

        resumed = kit.build(launcher, stop=2.0, workers=1, log_name="stopped.jsonl", resume=True)
        resumed.harness.run()

        assert len(launcher.requests) == 4  # the stop lands again at 2.0 after four more samples at 0.25


class TestAttemptIdentityAcrossAResume:
    def test_a_resumed_slot_continues_its_attempt_numbering_so_no_write_ahead_id_or_kept_stream_repeats(
        self, kit: RunKit, tmp_path: Path,
    ) -> None:
        keep = tmp_path / "kept"
        keep.mkdir()

        def always_time_out(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return timeout_sample() if request.stage == m.STAGE_SMOKE else None

        first = kit.build(
            ScriptedLauncher(fault=always_time_out), workers=1, log_name="paused.jsonl", keep_artifacts_dir=keep,
        )
        assert isinstance(first.harness.run().halt, m.RunPaused)
        first.release()
        paused_attempts = {entry["attempt"] for entry in finish_lines(first)}

        resumed = kit.build(ScriptedLauncher(), workers=1, log_name="paused.jsonl", resume=True, keep_artifacts_dir=keep)
        resumed.harness.run()

        finishes = finish_lines(resumed)
        attempts = [(entry["slot"], entry["attempt"]) for entry in finishes]
        write_ahead_ids = [json.loads(line)["defect_id"] for line in resumed.store.write_ahead_path.read_text().splitlines()]
        assert paused_attempts == {1, 2, 3}
        assert len(attempts) == len(set(attempts))
        assert len(write_ahead_ids) == len(set(write_ahead_ids))
        assert len(list(keep.glob("*.jsonl"))) == len(finishes)


class TestErrorsPreparingASample:
    @pytest.mark.parametrize(
        "error",
        [subprocess.CalledProcessError(128, ["seed", "command"], stderr=b"failed"), subprocess.TimeoutExpired("seed", 30)],
        ids=["seed-command-failed", "seed-command-timed-out"],
    )
    def test_is_a_zero_charge_launch_fault_and_never_tears_down_the_sibling_samples(self, kit: RunKit, error) -> None:
        class FailsOnce(ScriptedLauncher):
            failed = False

            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                with self._lock:
                    fail_now, self.failed = not self.failed, True
                if fail_now:
                    raise error
                return super().__call__(request)

        built = kit.build(FailsOnce(), workers=3)

        result = built.harness.run()

        launch_faults = [entry for entry in finish_lines(built) if entry["fault_class"] == m.FAULT_LAUNCH]
        assert result.verdict == m.VERDICT_CALIBRATED
        assert len(launch_faults) == 1
        assert (launch_faults[0]["charge_usd"], launch_faults[0]["cost_source"]) == (0.0, m.COST_SOURCE_NONE)
        assert type(error).__name__ in launch_faults[0]["detail"]
        assert not msmr._launches_aborted.is_set()


class TestFaultClassesAtTheHarnessLevel:
    def test_a_rate_limit_fault_carrying_a_synthetic_assistant_event_is_retried_and_does_not_halt_on_drift(
        self, kit: RunKit,
    ) -> None:
        def synthetic_rate_limit(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if count != 1:
                return None
            events = [
                init_event(),
                assistant_event([{"type": "text", "text": "limit"}], model=msmr.SYNTHETIC_MODEL_ID),
                {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
                result_event(),
            ]
            return m.RawSample(lines=lines_of(events), exit_status=0)

        built = kit.build(ScriptedLauncher(fault=synthetic_rate_limit), workers=1)

        result = built.harness.run()

        assert result.verdict == m.VERDICT_CALIBRATED
        assert built.sleeps
        assert finish_lines(built)[0]["fault_class"] == m.FAULT_RATE_LIMIT
        assert finish_lines(built)[0]["model"] == []
        assert built.harness.drift.first_seen["model"] == MODEL_ID

    def test_a_read_error_after_the_child_ran_is_retried_and_charged_the_stream_it_already_read(self, kit: RunKit) -> None:
        def unreadable_transcript_once(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if count != 1:
                return None
            return m.RawSample(lines=make_stream([], cost=0.3), exit_status=0, read_error="OSError: transcript vanished")

        built = kit.build(ScriptedLauncher(fault=unreadable_transcript_once), workers=1)

        result = built.harness.run()

        first_finish = finish_lines(built)[0]
        assert result.verdict == m.VERDICT_CALIBRATED
        assert (first_finish["fault_class"], first_finish["charge_usd"], first_finish["cost_source"]) == (
            m.FAULT_READ, 0.3, m.COST_SOURCE_REPORTED,
        )
        assert built.sleeps

    def test_a_client_error_result_pauses_the_run_at_once_without_a_retry(self, kit: RunKit) -> None:
        def unauthorized(request: m.SampleRequest, count: int) -> m.RawSample | None:
            if request.stage != m.STAGE_SMOKE:
                return None
            return m.RawSample(lines=make_stream([], is_error=True, api_error_status=401), exit_status=1)

        built = kit.build(ScriptedLauncher(fault=unauthorized), workers=1)

        result = built.harness.run()

        assert isinstance(result.halt, m.RunPaused)
        assert result.halt.fault_class == m.FAULT_ERROR_RESULT
        assert "401" in result.halt.status_line()
        assert built.sleeps == []
        assert len([entry for entry in finish_lines(built) if entry["class"] == m.CLS_FAULT]) == 1


class TestRefusedResume:
    def test_leaves_the_log_it_was_refused_against_untouched(self, kit: RunKit) -> None:
        first = kit.build(ScriptedLauncher(gate_rates()), log_name="log.jsonl", stop=0.5)
        first.harness.run()
        first.release()
        before = first.log.path.read_bytes()
        second = kit.build(ScriptedLauncher(gate_rates()), log_name="log.jsonl", resume=True, seed=8)

        with pytest.raises(m.ResumeRefused, match="seed"):
            second.harness.run()

        assert first.log.path.read_bytes() == before

    def test_refuses_a_log_written_under_a_different_account(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        first = kit.build(ScriptedLauncher(gate_rates()), log_name="log.jsonl", stop=0.5)
        first.harness.run()
        first.release()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "another-account"))
        second = kit.build(ScriptedLauncher(gate_rates()), log_name="log.jsonl", resume=True)

        with pytest.raises(m.ResumeRefused, match="account_label"):
            second.harness.run()


class TestSessionStoreCleanup:
    def test_a_project_directory_with_an_underscore_and_a_dot_maps_to_both_store_spellings(self) -> None:
        project_dir = Path("/private/var/folders/xx/abc_def/T/review-bench.main-session-x")

        assert m.own_session_store_dir_names(project_dir) == {
            "-private-var-folders-xx-abc_def-T-review-bench.main-session-x",
            "-private-var-folders-xx-abc-def-T-review-bench-main-session-x",
        }

    @staticmethod
    def _launcher_storing_session(projects_root: Path, directory_name_for) -> ScriptedLauncher:
        class StoringSession(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                store_dir = projects_root / directory_name_for(request)
                store_dir.mkdir(parents=True, exist_ok=True)
                (store_dir / f"{request.session_id}.jsonl").write_text("{}\n")
                (store_dir / "another-session.jsonl").write_text("{}\n")
                return super().__call__(request)

        return StoringSession()

    def test_removes_the_store_the_samples_own_project_directory_maps_to(self, kit: RunKit) -> None:
        projects_root = kit.tmp_path / "projects"
        launcher = self._launcher_storing_session(projects_root, lambda request: str(request.project_dir).replace("/", "-"))

        kit.build(launcher, stop=0.05, workers=1).harness.run()

        assert len(launcher.requests) == 1
        assert list(projects_root.glob("*")) == []

    def test_removes_the_store_named_with_every_non_alphanumeric_character_of_the_project_directory_replaced(
        self, kit: RunKit,
    ) -> None:
        projects_root = kit.tmp_path / "projects"
        launcher = self._launcher_storing_session(
            projects_root, lambda request: re.sub(r"[^A-Za-z0-9]", "-", str(request.project_dir)),
        )

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        [request] = launcher.requests
        assert str(request.project_dir).replace("/", "-") != re.sub(r"[^A-Za-z0-9]", "-", str(request.project_dir))
        assert list(projects_root.glob("*")) == []
        assert not any("left" in notice for notice in built.notices)

    def test_leaves_a_store_found_under_any_other_name_in_place(self, kit: RunKit) -> None:
        projects_root = kit.tmp_path / "projects"
        launcher = self._launcher_storing_session(projects_root, lambda request: "shared-with-other-sessions")

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        assert (projects_root / "shared-with-other-sessions" / "another-session.jsonl").is_file()
        assert any("left" in notice and "shared-with-other-sessions" in notice for notice in built.notices)

    @staticmethod
    def _not_found_notices(built: Built) -> list[str]:
        return [notice for notice in built.notices if "not found at cleanup" in notice]

    def test_reports_how_many_samples_left_no_store_to_find(self, kit: RunKit) -> None:
        launcher = ScriptedLauncher()

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        assert len(launcher.requests) == 1
        [notice] = self._not_found_notices(built)
        assert notice.startswith("1 sample session store(s)")
        assert str(kit.tmp_path / "projects") in notice
        assert repr(m.PROJECT_DIR_PREFIX) in notice
        assert built.notices.count(notice) == 1

    def test_reports_nothing_when_the_stores_were_found_and_removed(self, kit: RunKit) -> None:
        projects_root = kit.tmp_path / "projects"
        launcher = self._launcher_storing_session(projects_root, lambda request: str(request.project_dir).replace("/", "-"))

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        assert self._not_found_notices(built) == []

    @staticmethod
    def _undeletable_notices(built: Built) -> list[str]:
        return [notice for notice in built.notices if "could not be deleted" in notice]

    def test_reports_a_project_directory_that_survives_its_delete(
        self, kit: RunKit, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real_rmtree = shutil.rmtree

        def rmtree_that_leaves_sample_projects(path: Path, *args: object, **kwargs: object) -> None:
            if Path(path).parent != kit.temp_root.resolve():
                real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(m.shutil, "rmtree", rmtree_that_leaves_sample_projects)
        launcher = ScriptedLauncher()

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        assert len(launcher.requests) == 1
        [notice] = self._undeletable_notices(built)
        assert notice.startswith("1 sample project or session store")
        assert repr(m.PROJECT_DIR_PREFIX) in notice
        assert str(kit.temp_root) in notice
        assert str(kit.tmp_path / "projects") in notice
        assert built.notices.count(notice) == 1

    def test_counts_a_directory_whose_existence_check_raises_as_remaining(
        self, kit: RunKit, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real_exists = Path.exists
        sample_project_parent = kit.temp_root.resolve()

        def exists_raising_for_sample_projects(path: Path) -> bool:
            if path.parent == sample_project_parent:
                raise PermissionError("parent directory is not searchable")
            return real_exists(path)

        monkeypatch.setattr(Path, "exists", exists_raising_for_sample_projects)
        launcher = ScriptedLauncher()

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        assert len(launcher.requests) == 1
        assert len(self._undeletable_notices(built)) == 1

    def test_reports_a_session_store_that_survives_its_delete(self, kit: RunKit, monkeypatch: pytest.MonkeyPatch) -> None:
        projects_root = kit.tmp_path / "projects"
        real_rmtree = shutil.rmtree

        def rmtree_that_leaves_session_stores(path: Path, *args: object, **kwargs: object) -> None:
            if Path(path).parent != projects_root:
                real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(m.shutil, "rmtree", rmtree_that_leaves_session_stores)
        launcher = self._launcher_storing_session(projects_root, lambda request: str(request.project_dir).replace("/", "-"))

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        assert len(self._undeletable_notices(built)) == 1
        assert self._not_found_notices(built) == []

    def test_reports_nothing_when_every_directory_was_deleted(self, kit: RunKit) -> None:
        projects_root = kit.tmp_path / "projects"
        launcher = self._launcher_storing_session(projects_root, lambda request: str(request.project_dir).replace("/", "-"))

        built = kit.build(launcher, stop=0.05, workers=1)
        built.harness.run()

        assert self._undeletable_notices(built) == []

    def test_does_not_count_samples_whose_child_never_produced_a_transcript(self, kit: RunKit) -> None:
        def launch_fails_then_times_out(request: m.SampleRequest, count: int) -> m.RawSample | None:
            return m.RawSample(launch_error="OSError: no binary") if count == 1 else timeout_sample()

        built = kit.build(ScriptedLauncher(fault=launch_fails_then_times_out), workers=1)
        built.harness.run()

        assert self._not_found_notices(built) == []


class TestLauncherSidecarRead:
    def test_caps_the_failure_text_it_reads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / "cfg"
        marker_dir = config_dir / m.MARKER_DIR_NAME
        marker_dir.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        session_id = "session-11111111"
        sidecar = marker_dir / f"session.{session_id}.{m.PART_CONDUCT}{m.FAILURE_SIDECAR_SUFFIX}"
        sidecar.write_text("x" * (m.SIDECAR_TEXT_CAP_BYTES * 5))

        states = m.LiveLauncher._read_and_clear_markers(session_id)

        assert len(states[m.PART_CONDUCT].failure) == m.SIDECAR_TEXT_CAP_BYTES
        assert not sidecar.exists()


# --- The live-run wiring ---------------------------------------------------------------------------------------


def wire_live_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, launcher: ScriptedLauncher, *,
    receipt_last_lines: dict[str, str] | None = None,
) -> None:
    """Replaces what `_live_run` reaches outside this process: the binary, git, the launcher and the environment record.

    A gate-phase run passes the candidate's `receipt_last_lines`, which `prepare_run` otherwise derives from git.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg-label-q"))
    monkeypatch.setattr(m.mer, "_repo_roots_for_containment_check", lambda start_dir=None: ())
    monkeypatch.setattr(m, "resolve_claude_binary", lambda: "claude")
    prepared = m.PreparedRun(
        arms={}, self_check_arms={}, self_check_spec=SELF_CHECK_SPECS,
        receipt_last_lines=receipt_last_lines or {}, inputs={"base_claude_md": "hash-1"},
    )
    monkeypatch.setattr(m, "prepare_run", lambda args, scratch, **kwargs: prepared)
    monkeypatch.setattr(m, "LiveLauncher", lambda **kwargs: launcher)
    monkeypatch.setattr(m.bench_runner, "read_environment_record", lambda: bench_runner.EnvironmentRecord("cli 1", "commit-1"))


class TestLiveRun:
    def test_a_calibrate_run_prints_only_the_report_on_stdout_and_the_engineer_notices_on_stderr(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        json_out = tmp_path / "out" / "result.json"
        json_out.parent.mkdir()
        log_path = tmp_path / "live.jsonl"

        code = m.main(["--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet", "--json", str(json_out)])

        out, err = capsys.readouterr()
        assert code == 0
        assert "Run verdict: calibrated" in out
        for engineer_only in ("$", "projection", "cumulative stop", "remaining", "cfg-label-q", "HALTED"):
            assert engineer_only not in out
        assert "calibration projection" in err
        assert "cumulative stop" in err
        data = json.loads(json_out.read_text())
        assert (data["account_label"], data["verdict"]) == ("cfg-label-q", m.VERDICT_CALIBRATED)
        assert json_out.stat().st_mode & 0o777 == 0o600
        assert list(kit.temp_root.glob("main-session-adherence-*")) == []
        store = bench_runner.RunStore(Path(f"{log_path}.d"))
        store.acquire_lock()  # the run released its lock
        store.release_lock()

    def test_a_run_whose_log_is_locked_by_another_holder_is_refused_before_anything_is_created_or_launched(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        launcher = ScriptedLauncher()
        wire_live_run(monkeypatch, tmp_path, launcher)
        log_path = tmp_path / "live.jsonl"
        other_holder = bench_runner.RunStore(Path(f"{log_path}.d"))
        other_holder.acquire_lock()

        try:
            code = m.main(["--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet"])
        finally:
            other_holder.release_lock()

        assert code == 2
        assert "run store lock" in capsys.readouterr().err
        assert launcher.requests == []
        assert not log_path.exists()
        assert list(kit.temp_root.glob("main-session-adherence-*")) == []

    def test_the_run_lock_is_held_while_samples_launch(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "live.jsonl"
        second_holder_refusals: list[bool] = []

        def try_to_take_the_lock_during_a_sample(request: m.SampleRequest, count: int) -> None:
            second_holder = bench_runner.RunStore(Path(f"{log_path}.d"))
            try:
                second_holder.acquire_lock()
            except bench_runner.RunStoreLocked:
                second_holder_refusals.append(True)
            else:
                second_holder.release_lock()
                second_holder_refusals.append(False)

        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher(on_launch=try_to_take_the_lock_during_a_sample))

        code = m.main(["--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet"])

        assert code == 0
        assert second_holder_refusals
        assert all(second_holder_refusals)

    def test_an_interrupt_ends_the_run_with_130_a_resume_hint_a_released_lock_and_no_scratch_directory(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        def interrupt_on_the_first_launch(request: m.SampleRequest, count: int) -> None:
            raise KeyboardInterrupt

        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher(on_launch=interrupt_on_the_first_launch))
        log_path = tmp_path / "live.jsonl"

        code = m.main(["--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet"])

        assert code == 130
        assert f"resume with --resume {log_path}" in capsys.readouterr().err
        successor = bench_runner.RunStore(Path(f"{log_path}.d"))
        successor.acquire_lock()
        successor.release_lock()
        assert list(kit.temp_root.glob("main-session-adherence-*")) == []

    def test_the_self_check_answers_reach_the_json_file_and_never_stdout(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        answer = "SELF-CHECK-ANSWER-TEXT"

        class Answering(ScriptedLauncher):
            def __call__(self, request: m.SampleRequest) -> m.RawSample:
                raw = super().__call__(request)
                if request.key != m.SELF_CHECK_KEY:
                    return raw
                return m.RawSample(lines=make_stream([], result=answer), exit_status=0, session_jsonl_text=raw.session_jsonl_text)

        wire_live_run(monkeypatch, tmp_path, Answering())
        json_out = tmp_path / "result.json"

        code = m.main([
            "--phase", "calibrate", "--run-log", str(tmp_path / "live.jsonl"), "--model", "sonnet", "--json", str(json_out),
        ])

        out, err = capsys.readouterr()
        assert code == 0
        assert answer not in out
        assert answer not in err
        assert json.loads(json_out.read_text())["self_check"][m.ARM_MEMORY]["answers"] == [answer] * m.SELF_CHECK_SAMPLES

    def test_a_halted_run_reports_the_halt_on_stderr_only(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())

        code = m.main([
            "--phase", "calibrate", "--run-log", str(tmp_path / "live.jsonl"), "--model", "sonnet",
            "--cumulative-stop-usd", "1.0",
        ])

        out, err = capsys.readouterr()
        assert code == 1
        assert "HALTED" in err
        assert "HALTED" not in out
        assert "nominal dollars was reached" not in out

    def test_a_passing_gate_exits_zero_with_the_pass_verdict_and_no_halt(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher(gate_rates()), receipt_last_lines=LAST_LINE)

        code = m.main(["--phase", "gate", "--run-log", str(tmp_path / "live.jsonl"), "--model", "sonnet"])

        out, err = capsys.readouterr()
        assert code == 0
        assert f"Run verdict: {m.RunVerdict.PASS.value}" in out
        assert "HALTED" not in err

    @pytest.mark.parametrize(
        ("adherence", "reach", "verdict"),
        [
            pytest.param(gate_rates(hook_o3=0.85), None, m.RunVerdict.FAIL, id="fail"),
            pytest.param(gate_rates(), hook_reach_halved, m.RunVerdict.MISS, id="miss"),
            pytest.param(gate_rates(hook_o3=0.9), None, m.RunVerdict.INCONCLUSIVE, id="inconclusive"),
        ],
    )
    def test_a_gate_without_a_pass_verdict_exits_one_from_the_verdict_and_not_from_a_halt(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
        adherence: dict, reach: Callable[[m.SampleRequest], float] | None, verdict: m.RunVerdict,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher(adherence, reach=reach), receipt_last_lines=LAST_LINE)

        code = m.main(["--phase", "gate", "--run-log", str(tmp_path / "live.jsonl"), "--model", "sonnet"])

        out, err = capsys.readouterr()
        assert code == 1
        assert f"Run verdict: {verdict.value}" in out
        assert "HALTED" not in err

    def test_a_resume_of_a_missing_log_is_refused_before_any_directory_exists(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        absent = tmp_path / "absent.jsonl"

        code = m.main(["--phase", "calibrate", "--resume", str(absent), "--model", "sonnet"])

        assert code == 2
        assert "no such run log" in capsys.readouterr().err
        assert not Path(f"{absent}.d").exists()

    def test_a_model_that_cannot_be_resolved_is_refused_before_the_run_log_exists(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())

        def no_model(**kwargs: object) -> str:
            raise m.PreflightError("no model configured; pass --model")

        monkeypatch.setattr(m, "default_model", no_model)
        log_path = tmp_path / "live.jsonl"

        code = m.main(["--phase", "calibrate", "--run-log", str(log_path)])

        assert code == 2
        assert "pass --model" in capsys.readouterr().err
        assert not log_path.exists()
        assert not Path(f"{log_path}.d").exists()

    def test_a_json_path_in_a_missing_directory_is_refused_before_the_run_log_exists(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        log_path = tmp_path / "live.jsonl"

        missing_directory_json = tmp_path / "missing" / "r.json"

        code = m.main([
            "--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet", "--json", str(missing_directory_json),
        ])

        assert code == 2
        assert "--json" in capsys.readouterr().err
        assert not log_path.exists()
        assert not Path(f"{log_path}.d").exists()

    def test_a_fresh_run_refuses_an_existing_json_path_before_any_run_file_exists(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        earlier_results = tmp_path / "result.json"
        earlier_results.write_text("earlier run")
        log_path = tmp_path / "live.jsonl"

        code = m.main(["--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet", "--json", str(earlier_results)])

        assert code == 2
        assert "--json" in capsys.readouterr().err
        assert earlier_results.read_text() == "earlier run"
        assert not log_path.exists()
        assert not Path(f"{log_path}.d").exists()

    def test_a_fresh_run_refuses_a_keep_artifacts_directory_that_already_holds_files(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        kept = tmp_path / "kept"
        kept.mkdir()
        (kept / "smoke-memory-O1-0-1.jsonl").write_text("earlier run")
        log_path = tmp_path / "live.jsonl"

        code = m.main(["--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet", "--keep-artifacts", str(kept)])

        assert code == 2
        assert "--keep-artifacts" in capsys.readouterr().err
        assert (kept / "smoke-memory-O1-0-1.jsonl").read_text() == "earlier run"
        assert not log_path.exists()

    def test_a_fresh_run_refuses_a_regular_file_at_the_keep_artifacts_path_without_a_traceback(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        a_file_not_a_directory = tmp_path / "kept"
        a_file_not_a_directory.write_text("not a directory")
        log_path = tmp_path / "live.jsonl"

        code = m.main([
            "--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet",
            "--keep-artifacts", str(a_file_not_a_directory),
        ])

        assert code == 2
        assert "--keep-artifacts" in capsys.readouterr().err
        assert a_file_not_a_directory.read_text() == "not a directory"
        assert not log_path.exists()
        assert not Path(f"{log_path}.d").exists()

    def test_a_resume_refuses_a_regular_file_at_the_keep_artifacts_path_without_a_traceback(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        log_path = tmp_path / "live.jsonl"
        log_path.write_text("")  # a resume needs an existing run log; the refusal comes before it is read
        a_file_not_a_directory = tmp_path / "kept"
        a_file_not_a_directory.write_text("not a directory")

        code = m.main([
            "--phase", "calibrate", "--resume", str(log_path), "--model", "sonnet",
            "--keep-artifacts", str(a_file_not_a_directory),
        ])

        assert code == 2
        assert "--keep-artifacts" in capsys.readouterr().err
        assert a_file_not_a_directory.read_text() == "not a directory"
        assert log_path.read_text() == ""
        assert not Path(f"{log_path}.d").exists()

    def test_a_resume_refuses_a_dangling_symlink_at_the_keep_artifacts_path_without_a_traceback(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        log_path = tmp_path / "live.jsonl"
        log_path.write_text("")  # a resume needs an existing run log; the refusal comes before it is read
        target_that_does_not_exist = tmp_path / "kept-target"
        dangling = tmp_path / "kept"
        dangling.symlink_to(target_that_does_not_exist)

        code = m.main([
            "--phase", "calibrate", "--resume", str(log_path), "--model", "sonnet", "--keep-artifacts", str(dangling),
        ])

        assert code == 2
        assert "--keep-artifacts" in capsys.readouterr().err
        assert not target_that_does_not_exist.exists()
        assert log_path.read_text() == ""
        assert not Path(f"{log_path}.d").exists()

    def test_a_fresh_run_refuses_a_dangling_symlink_at_the_json_path(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        target_that_does_not_exist = tmp_path / "result-target.json"  # its directory exists, so only the symlink check can refuse
        dangling = tmp_path / "result.json"
        dangling.symlink_to(target_that_does_not_exist)
        log_path = tmp_path / "live.jsonl"

        code = m.main(["--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet", "--json", str(dangling)])

        assert code == 2
        assert "--json" in capsys.readouterr().err
        assert not target_that_does_not_exist.exists()
        assert not log_path.exists()

    def test_a_fresh_run_refuses_a_dangling_symlink_at_the_keep_artifacts_path_without_a_traceback(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        target_that_does_not_exist = tmp_path / "kept-target"
        dangling = tmp_path / "kept"
        dangling.symlink_to(target_that_does_not_exist)
        log_path = tmp_path / "live.jsonl"

        code = m.main([
            "--phase", "calibrate", "--run-log", str(log_path), "--model", "sonnet", "--keep-artifacts", str(dangling),
        ])

        assert code == 2
        assert "--keep-artifacts" in capsys.readouterr().err
        assert not target_that_does_not_exist.exists()
        assert not log_path.exists()
        assert not Path(f"{log_path}.d").exists()

    def test_a_fresh_run_accepts_an_empty_keep_artifacts_directory_and_a_new_json_path(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        kept = tmp_path / "kept"
        kept.mkdir()

        code = m.main([
            "--phase", "calibrate", "--run-log", str(tmp_path / "live.jsonl"), "--model", "sonnet",
            "--keep-artifacts", str(kept), "--json", str(tmp_path / "result.json"),
        ])

        assert code == 0
        assert list(kept.glob("*.jsonl"))
        assert (tmp_path / "result.json").is_file()

    def test_a_resume_may_reuse_the_json_path_and_the_keep_artifacts_directory_of_the_run_it_continues(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())
        kept, results, log_path = tmp_path / "kept", tmp_path / "result.json", tmp_path / "live.jsonl"
        common = ["--phase", "calibrate", "--model", "sonnet", "--keep-artifacts", str(kept), "--json", str(results)]
        assert m.main([*common, "--run-log", str(log_path), "--cumulative-stop-usd", "1.0"]) == 1
        kept_after_the_pause = sorted(path.name for path in kept.glob("*.jsonl"))
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())

        code = m.main([*common, "--resume", str(log_path)])

        assert code == 0
        assert set(kept_after_the_pause) < {path.name for path in kept.glob("*.jsonl")}

    def test_a_failed_seed_git_command_names_the_command_and_carries_only_the_stderr_tail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def failing_run(*args, **kwargs):
            raise subprocess.CalledProcessError(
                128, args[0], stderr="x" * m.STDERR_DETAIL_TAIL_CHARS + "fatal: the tail\n",
            )

        monkeypatch.setattr(subprocess, "run", failing_run)

        with pytest.raises(subprocess.SubprocessError) as raised:
            m._git_in(tmp_path, "checkout", "-q")

        message = str(raised.value)
        assert message.startswith("git checkout exited 128: ")
        assert message.endswith("fatal: the tail'")
        assert "x" * (m.STDERR_DETAIL_TAIL_CHARS - len("fatal: the tail")) in message
        assert "x" * (m.STDERR_DETAIL_TAIL_CHARS - len("fatal: the tail") + 1) not in message

    def test_main_removes_the_git_local_variables_so_o3_seeding_leaves_a_repository_named_by_git_dir_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        other_repo = _init_repo(tmp_path / "other-repo")
        _write(other_repo, "tracked.txt", "tracked\n")
        head_before = _commit(other_repo, "base")
        o3 = m.OBSERVABLES_BY_KEY["O3"]
        monkeypatch.setenv("GIT_DIR", str(other_repo / ".git"))
        m.seed_project(o3, tmp_path / "control-project", session_id="abcdef12-control")
        assert _git(other_repo, "rev-parse", "HEAD").strip() != head_before, "without the removal the seeding commits there"
        head_before = _git(other_repo, "rev-parse", "HEAD").strip()
        monkeypatch.setenv("GIT_DIR", str(other_repo / ".git"))

        assert m.main(["--list"]) == 0
        m.seed_project(o3, tmp_path / "project", session_id="abcdef12-sample")

        assert "GIT_DIR" not in os.environ
        assert _git(other_repo, "rev-parse", "HEAD").strip() == head_before
        assert (tmp_path / "project" / ".git").is_dir()

    def test_the_git_local_environment_helper_restores_the_variables_main_removes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GIT_INDEX_FILE", "index-set-by-a-hook")  # undone at teardown even if this test fails

        with preserved_git_local_environment():
            assert m.main(["--list"]) == 0
            assert "GIT_INDEX_FILE" not in os.environ

        assert os.environ["GIT_INDEX_FILE"] == "index-set-by-a-hook"

    def test_a_failure_building_the_run_store_leaves_no_scratch_directory(
        self, kit: RunKit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wire_live_run(monkeypatch, tmp_path, ScriptedLauncher())

        def disk_full(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(m.bench_runner, "RunStore", disk_full)

        with pytest.raises(OSError, match="disk full"):
            m.main(["--phase", "calibrate", "--run-log", str(tmp_path / "live.jsonl"), "--model", "sonnet"])

        assert list(kit.temp_root.glob("main-session-adherence-*")) == []

    def test_a_resume_log_inside_a_worktree_is_refused_before_anything_launches(
        self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(msmr, "_run_claude_to_completion", lambda *a, **k: pytest.fail("launched"))
        monkeypatch.setattr(m.mer, "_repo_roots_for_containment_check", lambda start_dir=None: (tmp_path / "worktree",))

        code = m.main(["--phase", "calibrate", "--resume", str(tmp_path / "worktree" / "run.jsonl")])

        assert code == 2
        assert "--resume" in capsys.readouterr().err
        assert not (tmp_path / "worktree").exists()

    def test_a_resume_log_outside_every_worktree_passes_the_path_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(m.mer, "_repo_roots_for_containment_check", lambda start_dir=None: (tmp_path / "worktree",))
        args = m.build_arg_parser().parse_args(["--phase", "calibrate", "--resume", str(tmp_path / "elsewhere" / "run.jsonl")])

        m._validated_paths(args)


class TestSpendAndWorkerFlags:
    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--cumulative-stop-usd", "nan"), ("--cumulative-stop-usd", "inf"), ("--cumulative-stop-usd", "-1"),
            ("--cumulative-stop-usd", "0"), ("--cumulative-stop-usd", "lots"), ("--budget-cap-usd", "nan"),
            ("--budget-cap-usd", "-inf"), ("--budget-cap-usd", "-0.5"), ("--budget-cap-usd", "0"),
            ("--workers", "0"), ("--workers", "-2"), ("--workers", str(m.MAX_WORKERS + 1)), ("--workers", "many"),
            ("--timeout-s", "0"), ("--timeout-s", "-30"), ("--timeout-s", "soon"), ("--timeout-s", "1.5"),
        ],
    )
    def test_are_rejected_when_the_arguments_are_parsed(self, capsys: pytest.CaptureFixture, flag, value) -> None:
        with pytest.raises(SystemExit) as exit_info:
            m.build_arg_parser().parse_args(["--phase", "gate", flag, value])

        assert exit_info.value.code == 2
        assert flag in capsys.readouterr().err

    def test_accept_a_whole_number_of_seconds_above_zero_as_the_timeout(self) -> None:
        assert m.build_arg_parser().parse_args(["--phase", "gate", "--timeout-s", "1"]).timeout_s == 1

    @pytest.mark.parametrize("workers", [1, m.MAX_WORKERS])
    def test_accept_a_worker_count_from_one_to_the_cap(self, workers) -> None:
        args = m.build_arg_parser().parse_args(["--phase", "gate", "--workers", str(workers), "--cumulative-stop-usd", "12.5"])

        assert (args.workers, args.cumulative_stop_usd) == (workers, 12.5)
