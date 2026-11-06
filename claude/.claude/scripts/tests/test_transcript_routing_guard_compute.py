"""Tests for transcript_analysis/routing_guard.py's compute mode, on hand-written capture files and synthetic fixtures only."""
import errno
import importlib.util
import json
import math
import random
import re
import shlex
import stat
import subprocess
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from itertools import product
from pathlib import Path
from typing import NamedTuple

import _config
import _config_dir
import pytest
from helpers import REPO_ROOT
from transcript_analysis import corpus, pricing, review_rounds, routing_guard, scope

from ._pr_cost_helpers import _enable_pr_cost
from ._review_round_helpers import _SONNET
from ._routing_guard_helpers import (
    _BANNER_PROVENANCE_SENTENCE,
    _COMPUTE_PROVENANCE_LINE,
    _NOISY_CONFIG_MALFORMED_TOKEN,
    _NOISY_CONFIG_UNKNOWN_KEY,
    _VALID_STATES,
    _assert_git_failure_diagnostic_precedes_note_refusal,
    _capture_file_text,
    _config_enabled_stub,
    _digest_name,
    _environment_unchanged,
    _install_git_not_found,
    _install_path_spies,
    _install_subprocess_recorder,
    _invoke_routing_guard,
    _log_row,
    _note_line,
    _note_row,
    _opt_in_faults,
    _Outcome,
    _routing_guard_section_lines,
    _write_log,
    _write_noisy_config_state_file,
)
from .conftest import _agent_use, _commit, _init_repo, _priced, _skill_block, _user_msg, _write_jsonl, _write_subagent_dispatch

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)

# _SONNET's cache-read rate is $0.20 per million tokens.
_OPUS = "claude-opus-5"  # cache-read rate $0.50 per million tokens
_UNPRICED_MODEL = "claude-unlisted-model"
_UNLISTED_MODEL_SENTINEL = "claude-unlisted-sentinel-model"
# A model in the rate table that no fixture here samples, so only a scan of the whole table could find it expired.
_LISTED_MODEL_NOT_IN_THE_SAMPLE = next(
    model for model in pricing._MODEL_BASE_INPUT_RATES if model not in {_SONNET, _OPUS}
)
_CAPTURES_DIR = "routing-guard-captures"
_LOCATOR = re.compile(r"note line \d+")
# Module-level so test_select_tests.py's TestCrossDomainReadCompleteness sees this read.
_TRANSCRIPT_ANALYSIS_DOC = REPO_ROOT / "docs" / "transcript-analysis.md"
_SAMPLE_OUTPUT_HEADING = "### Sample output"
# `routing_guard.py` names the file, not an attribute of the module.
_CITED_MODULE_NAME = re.compile(r"routing_guard\.(?!py\b)([A-Za-z_]\w*)")
# The --out lines that echo the operator's own path, quoted by repr. The fixed --out messages are not exempt.
_DIGIT_SCAN_EXEMPT_PREFIXES = (
    "routing-guard: --out '", 'routing-guard: --out "', "routing-guard: git-tracked check",
)
_PRODUCTION_BOOTSTRAP_REPLICATES = routing_guard._BOOTSTRAP_REPLICATES
_TEST_BOOTSTRAP_REPLICATES = 300
# Dates far enough from any real clock that a rate's expiry never flips with the day the suite runs.
_FAR_PAST_RATE_EXPIRY = date(2000, 1, 1)
_FAR_FUTURE_RATE_EXPIRY = date(2999, 1, 1)


@pytest.fixture(autouse=True)
def _rates_never_expire(monkeypatch):
    """Keep the stale-rate line off every output, so no test depends on the clock."""
    for model in pricing._MODEL_BASE_INPUT_RATES:
        monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, model, _FAR_FUTURE_RATE_EXPIRY)


def _loop(
    *, input_tokens: int = 0, growth: int | None = None, later: dict[str, int] | None = None, degraded: bool = False,
    model: str = _SONNET, variant: str = "standard",
) -> dict:
    return {
        "overhead_tokens": {model: {variant: {"input": input_tokens}}} if input_tokens else {},
        "growth_tokens": growth, "later_turns": later or {}, "degraded": degraded,
    }


class _Lab:
    """A temp directory holding accounts, their hand-written capture files and the note."""

    def __init__(self, root: Path):
        self.root = root
        self.note = root / "notes" / "note.tsv"
        self.note.parent.mkdir(parents=True)
        self.note_lines: list[str] = []

    def account(self, name: str = "acct-a", *, key_on: bool = True, captures_dir: bool = True) -> Path:
        config_dir = self.root / name
        (config_dir / "projects").mkdir(parents=True, exist_ok=True)
        if key_on:
            _enable_pr_cost(config_dir)
        if captures_dir:
            (config_dir / _CAPTURES_DIR).mkdir(exist_ok=True)
        return config_dir

    def row(
        self, account: Path, branch: str, arm: str = "on", *, loops: list[dict] | None = None,
        pilot_states: tuple[str, str] = ("off", "off"), status: str = "merged",
    ) -> int:
        """Add a note row and, when `loops` is given, its capture file. Returns the row's note line.

        `arm` and `pilot_states` are the states the capture file stores. The note holds only the branch and its status.
        """
        self.note_lines.append(_note_line(account, branch, status))
        if loops is not None:
            self.write_capture(account, branch, loops, routing=arm, round2=pilot_states[0], architect=pilot_states[1])
        return len(self.note_lines)

    @staticmethod
    def write_capture(
        account: Path, branch: str, loops: list[dict], *, revision: int = 1, routing: str = "off", round2: str = "off",
        architect: str = "off",
    ) -> Path:
        path = account / _CAPTURES_DIR / _digest_name(branch, revision)
        path.write_text(_capture_file_text(
            loops, routing=routing, round_consult_round2_pilot=round2, architect_fix_plan_pilot=architect,
        ))
        return path

    def write_note(self, *, mode: int = 0o600, order: Sequence[int] | None = None) -> None:
        lines = self.note_lines if order is None else [self.note_lines[i] for i in order]
        self.note.write_text("\n".join(lines) + "\n")
        self.note.chmod(mode)


@pytest.fixture
def lab(tmp_path, monkeypatch) -> _Lab:
    """A lab whose guard minimum is one key-on branch, so a test needs only the rows it is about.

    The bootstrap runs a few hundred replicates. A test that asserts the real count or a position in it adds
    `production_bootstrap_replicates`.
    """
    monkeypatch.setattr(routing_guard, "_MIN_ON_BRANCHES", 1)
    monkeypatch.setattr(routing_guard, "_BOOTSTRAP_REPLICATES", _TEST_BOOTSTRAP_REPLICATES)
    return _Lab(tmp_path)


@pytest.fixture
def lab_at_the_real_minimum(tmp_path, monkeypatch) -> _Lab:
    monkeypatch.setattr(routing_guard, "_BOOTSTRAP_REPLICATES", _TEST_BOOTSTRAP_REPLICATES)
    return _Lab(tmp_path)


@pytest.fixture
def production_bootstrap_replicates(monkeypatch) -> None:
    """Put back the replicate count the module ships with, read before any fixture changed it."""
    monkeypatch.setattr(routing_guard, "_BOOTSTRAP_REPLICATES", _PRODUCTION_BOOTSTRAP_REPLICATES)


def _namespace(lab: _Lab, **overrides) -> object:
    fields = {"config_dir": None, "note": str(lab.note), "capture": False, "out": None}
    fields.update(overrides)
    return type("Args", (), fields)()


def _compute(capsys, lab: _Lab, **overrides) -> _Outcome:
    return _invoke_routing_guard(capsys, _namespace(lab, **overrides))


def _minimal_pair(lab: _Lab, *, on_growth: int = 100, off_growth: int = 300) -> tuple[Path, int, int]:
    """One account with a key-on and a key-off branch. Returns the account and the two note lines."""
    account = lab.account()
    on_line = lab.row(account, "branch-on", "on", loops=[_loop(input_tokens=2_000_000, growth=on_growth)])
    off_line = lab.row(
        account, "branch-off", "off", loops=[_loop(input_tokens=1_000_000, growth=off_growth, later={_SONNET: 10})],
    )
    lab.write_note()
    return account, on_line, off_line


def _not_run_line(reason: str, line_no: int | None = None) -> str:
    locator = f" (note line {line_no})" if line_no is not None else ""
    return f"routing-guard: NOT RUN: {reason}{locator}"


def _digits_outside_locators(outcome: _Outcome) -> list[str]:
    """Every output line holding a digit once `note line <n>` locators and the exempt --out lines are set aside."""
    lines = (outcome.out + outcome.err).splitlines()
    scanned = [line for line in lines if not line.startswith(_DIGIT_SCAN_EXEMPT_PREFIXES)]
    return [line for line in scanned if any(ch.isdigit() for ch in _LOCATOR.sub("", line))]


# --- readiness ---------------------------------------------------------------


class TestReadiness:
    def test_a_key_that_is_off_is_a_not_run_reason_naming_the_first_note_line_and_none_of_its_files_is_opened(
        self, lab, capsys, monkeypatch,
    ):
        account = lab.account(key_on=False)
        lab.row(account, "branch-on", "on", loops=[_loop(growth=1)])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2)])
        lab.write_note()
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_ACCOUNT_NOT_READY, 1)]
        assert not any(path.endswith(".r1.json") for path in spies.opened_paths())

    def test_the_same_account_with_its_key_on_has_its_files_opened(self, lab, capsys, monkeypatch):
        _minimal_pair(lab)
        spies = _install_path_spies(monkeypatch)

        _compute(capsys, lab)

        assert any(path.endswith(_digest_name("branch-on")) for path in spies.opened_paths())
        assert any(path.endswith(_digest_name("branch-off")) for path in spies.opened_paths())

    def test_a_missing_captures_directory_is_a_not_run_reason_naming_the_first_note_line_and_no_capture_file_is_opened(
        self, lab, capsys, monkeypatch,
    ):
        account = lab.account(captures_dir=False)
        lab.row(account, "branch-on", "on")
        lab.row(account, "branch-off", "off")
        lab.write_note()
        misplaced_file = account / _digest_name("branch-on")
        misplaced_file.write_text(json.dumps({"loops": [_loop(growth=1)]}))
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_ACCOUNT_NOT_READY, 1)]
        assert not any(path.endswith(".r1.json") for path in spies.opened_paths())

    @pytest.mark.parametrize(
        ("case_id", "reader_result"), [(case_id, reader_result) for case_id, reader_result, *_ in _opt_in_faults()],
    )
    def test_each_outcome_that_is_not_opted_in_is_not_run_and_never_exit_1(
        self, lab, capsys, monkeypatch, case_id, reader_result,
    ):
        _minimal_pair(lab)
        monkeypatch.setattr(_config, "config_enabled", _config_enabled_stub(reader_result))
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert _not_run_line(routing_guard._REASON_ACCOUNT_NOT_READY, 1) in outcome.out.splitlines()
        assert not any(path.endswith(".r1.json") for path in spies.opened_paths())

    def test_an_opted_in_account_proceeds_past_the_readiness_check(self, lab, capsys):
        _minimal_pair(lab)

        outcome = _compute(capsys, lab)

        assert routing_guard._REASON_ACCOUNT_NOT_READY not in outcome.out
        assert outcome.code in (0, 1)

    def test_an_unreadable_schema_is_not_run_with_no_path_and_no_digit_outside_a_locator(
        self, lab, capsys, monkeypatch, tmp_path,
    ):
        _minimal_pair(lab)
        monkeypatch.setattr(_config, "_SCHEMA_FILE", tmp_path / "no-such-schema.psv")

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert "no-such-schema" not in outcome.out + outcome.err
        assert _digits_outside_locators(outcome) == []

    def test_a_malformed_and_an_unknown_config_line_leave_neither_the_path_nor_the_line_in_any_stream(
        self, lab, capsys, tmp_path,
    ):
        account, _on_line, _off_line = _minimal_pair(lab)
        second_account = lab.account("acct-b")
        for config_dir in (account, second_account):
            _write_noisy_config_state_file(config_dir)
        lab.row(second_account, "branch-on-b", "on", loops=[_loop(growth=1)])
        lab.row(second_account, "branch-off-b", "off", loops=[_loop(growth=2)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        combined = outcome.out + outcome.err
        assert outcome.code in (0, 1)
        assert _NOISY_CONFIG_MALFORMED_TOKEN not in combined
        assert _NOISY_CONFIG_UNKNOWN_KEY not in combined
        assert str(account) not in combined
        assert str(second_account) not in combined
        assert str(tmp_path) not in combined
        assert routing_guard._CONFIG_READER_NOTICE in outcome.err


# --- the sample --------------------------------------------------------------

_ACCOUNT_A = Path("/synthetic/acct-a")
_ACCOUNT_B = Path("/synthetic/acct-b")
_DEFINED = [_loop(growth=1)]
_UNDEFINED = [_loop(growth=None)]


def _record(arm: str = "on", pilot_states: tuple[str, str] = ("off", "off"), loops: list[dict] | None = None):
    """A loaded capture file: the loops and the three stored states."""
    return routing_guard._CaptureRecord(
        _DEFINED if loops is None else loops, routing_guard._KeyStates(arm, pilot_states[0], pilot_states[1]),
    )


def _select(entries: list[tuple], ready=(_ACCOUNT_A, _ACCOUNT_B), loaded: dict | None = None):
    """Run the sample selection. Each entry is (note row, the row's loaded file), the file a `_record`."""
    rows = [row for row, _file in entries]
    fetched = loaded if loaded is not None else {row.line_no: file for row, file in entries}
    return routing_guard._select_sample(rows, ready, fetched)


def _entry(
    line_no: int, account: Path, branch: str, arm: str = "on", *, pilot_states=("off", "off"), loops=None, status="merged",
):
    return _note_row(line_no, account, branch, status), _record(arm, pilot_states, loops)


class TestSelectSample:
    @pytest.fixture(autouse=True)
    def _minimum_of_one(self, monkeypatch):
        monkeypatch.setattr(routing_guard, "_MIN_ON_BRANCHES", 1)

    def test_a_balanced_account_gives_no_reason_and_both_arms_read_from_the_files(self):
        entries = [_entry(1, _ACCOUNT_A, "on-1"), _entry(2, _ACCOUNT_A, "off-1", "off")]

        selection = _select(entries)

        assert selection.reasons == []
        assert [row.branch for row in selection.on_rows] == ["on-1"]
        assert [row.branch for row in selection.off_rows] == ["off-1"]

    def test_the_arm_comes_from_the_file_and_not_from_the_row_order(self):
        entries = [_entry(1, _ACCOUNT_A, "first", "off"), _entry(2, _ACCOUNT_A, "second", "on")]

        selection = _select(entries)

        assert [row.branch for row in selection.on_rows] == ["second"]
        assert [row.branch for row in selection.off_rows] == ["first"]

    def test_fewer_on_files_than_the_minimum_is_a_reason(self, monkeypatch):
        monkeypatch.setattr(routing_guard, "_MIN_ON_BRANCHES", 2)
        entries = [_entry(1, _ACCOUNT_A, "on-1"), _entry(2, _ACCOUNT_A, "off-1", "off")]

        assert _select(entries).reasons == [routing_guard._NotRunReason(routing_guard._REASON_TOO_FEW_ON, None)]

    def test_an_account_that_is_not_ready_is_one_reason_naming_its_first_merged_row_and_excluded_rows_do_not_count(self):
        entries = [
            _entry(1, _ACCOUNT_A, "work-in-progress", status="excluded"),
            _entry(2, _ACCOUNT_A, "on-1"),
            _entry(3, _ACCOUNT_A, "off-1", "off"),
        ]

        selection = _select(entries, ready=(), loaded={})

        assert selection.reasons == [routing_guard._NotRunReason(routing_guard._REASON_ACCOUNT_NOT_READY, 2)]
        assert selection.on_rows == [] and selection.off_rows == []

    def test_a_missing_and_an_unusable_file_are_separate_reasons_each_naming_its_row(self):
        entries = [_entry(1, _ACCOUNT_A, "on-1"), _entry(2, _ACCOUNT_A, "off-1", "off")]
        loaded = {1: routing_guard._StandingFile.ABSENT, 2: routing_guard._StandingFile.UNREADABLE}

        selection = _select(entries, loaded=loaded)

        assert selection.reasons == [
            routing_guard._NotRunReason(routing_guard._REASON_FILE_MISSING, 1),
            routing_guard._NotRunReason(routing_guard._REASON_FILE_UNUSABLE, 2),
        ]

    @pytest.mark.parametrize("unusable", [routing_guard._StandingFile.INVALID, routing_guard._StandingFile.LOOPS_ONLY])
    def test_an_invalid_file_and_a_loops_only_file_are_both_unusable(self, unusable):
        entries = [_entry(1, _ACCOUNT_A, "on-1"), _entry(2, _ACCOUNT_A, "off-1", "off")]

        selection = _select(entries, loaded={1: _record(), 2: unusable})

        assert selection.reasons == [routing_guard._NotRunReason(routing_guard._REASON_FILE_UNUSABLE, 2)]

    def test_count_reasons_are_withheld_while_any_file_failed_to_load(self, monkeypatch):
        monkeypatch.setattr(routing_guard, "_MIN_ON_BRANCHES", 5)
        entries = [_entry(1, _ACCOUNT_A, "on-1"), _entry(2, _ACCOUNT_A, "on-2"), _entry(3, _ACCOUNT_A, "off-1", "off")]

        selection = _select(entries, loaded={1: _record(), 2: _record(), 3: routing_guard._StandingFile.UNREADABLE})

        assert selection.reasons == [routing_guard._NotRunReason(routing_guard._REASON_FILE_UNUSABLE, 3)]

    def test_an_account_whose_on_files_mix_pilot_states_has_no_reference_state(self):
        entries = [
            _entry(1, _ACCOUNT_A, "on-1", pilot_states=("on", "off")),
            _entry(2, _ACCOUNT_A, "on-2", pilot_states=("off", "off")),
            _entry(3, _ACCOUNT_A, "off-1", "off"),
            _entry(4, _ACCOUNT_A, "off-2", "off", pilot_states=("on", "on")),
        ]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_MIXED_PILOTS, 1)]

    def test_an_off_file_with_a_different_pilot_state_than_the_on_files_is_a_reason_naming_that_row(self):
        entries = [
            _entry(1, _ACCOUNT_A, "on-1", pilot_states=("on", "off")),
            _entry(2, _ACCOUNT_A, "off-1", "off", pilot_states=("on", "off")),
            _entry(3, _ACCOUNT_A, "on-2", pilot_states=("on", "off")),
            _entry(4, _ACCOUNT_A, "off-2", "off", pilot_states=("off", "off")),
        ]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_OFF_PILOT_DIFFERS, 4)]

    def test_on_files_differing_only_in_the_architect_pilot_have_no_reference_state(self):
        entries = [
            _entry(1, _ACCOUNT_A, "on-1", pilot_states=("on", "on")),
            _entry(2, _ACCOUNT_A, "on-2", pilot_states=("on", "off")),
            _entry(3, _ACCOUNT_A, "off-1", "off", pilot_states=("on", "on")),
            _entry(4, _ACCOUNT_A, "off-2", "off", pilot_states=("on", "on")),
        ]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_MIXED_PILOTS, 1)]

    def test_an_off_file_differing_only_in_the_architect_pilot_is_a_reason_naming_that_row(self):
        entries = [
            _entry(1, _ACCOUNT_A, "on-1", pilot_states=("on", "on")),
            _entry(2, _ACCOUNT_A, "off-1", "off", pilot_states=("on", "on")),
            _entry(3, _ACCOUNT_A, "on-2", pilot_states=("on", "on")),
            _entry(4, _ACCOUNT_A, "off-2", "off", pilot_states=("on", "off")),
        ]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_OFF_PILOT_DIFFERS, 4)]

    def test_an_account_with_unequal_arm_counts_is_a_reason_naming_its_first_row(self):
        entries = [_entry(1, _ACCOUNT_A, "on-1"), _entry(2, _ACCOUNT_A, "on-2")]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_ARM_SIZE_MISMATCH, 1)]

    def test_an_arm_with_no_loop_whose_growth_is_defined_is_a_reason(self):
        entries = [_entry(1, _ACCOUNT_A, "on-1", loops=_UNDEFINED), _entry(2, _ACCOUNT_A, "off-1", "off")]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_NO_GROWTH_DEFINED, None)]

    def test_the_growth_reason_is_withheld_while_the_arm_sizes_differ(self):
        entries = [_entry(1, _ACCOUNT_A, "on-1", loops=_UNDEFINED), _entry(2, _ACCOUNT_A, "on-2", loops=_UNDEFINED)]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_ARM_SIZE_MISMATCH, 1)]

    def test_the_growth_reason_is_withheld_while_there_are_too_few_on_files(self, monkeypatch):
        monkeypatch.setattr(routing_guard, "_MIN_ON_BRANCHES", 2)
        entries = [
            _entry(1, _ACCOUNT_A, "on-1", loops=_UNDEFINED),
            _entry(2, _ACCOUNT_A, "off-1", "off", loops=_UNDEFINED),
        ]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_TOO_FEW_ON, None)]

    def test_the_growth_reason_surfaces_once_the_count_reasons_clear(self):
        entries = [
            _entry(1, _ACCOUNT_A, "on-1", loops=_UNDEFINED),
            _entry(2, _ACCOUNT_A, "off-1", "off", loops=_UNDEFINED),
        ]

        reasons = _select(entries).reasons

        assert reasons == [routing_guard._NotRunReason(routing_guard._REASON_NO_GROWTH_DEFINED, None)]

    def test_off_files_of_an_account_with_no_on_file_and_every_excluded_row_are_ignored(self):
        entries = [
            _entry(1, _ACCOUNT_A, "on-1"),
            _entry(2, _ACCOUNT_A, "off-1", "off"),
            _entry(3, _ACCOUNT_B, "stray-off", "off"),
            _entry(4, _ACCOUNT_A, "in-progress", status="excluded"),
        ]

        selection = _select(entries)

        assert selection.reasons == []
        assert [row.branch for row in selection.on_rows + selection.off_rows] == ["on-1", "off-1"]

    def test_two_reasons_that_hold_at_once_come_back_in_the_declared_order(self, monkeypatch):
        monkeypatch.setattr(routing_guard, "_MIN_ON_BRANCHES", 3)
        entries = [_entry(1, _ACCOUNT_A, "on-1"), _entry(2, _ACCOUNT_A, "on-2")]

        reasons = [found.reason for found in _select(entries).reasons]

        assert reasons == [routing_guard._REASON_TOO_FEW_ON, routing_guard._REASON_ARM_SIZE_MISMATCH]
        assert reasons == sorted(reasons, key=routing_guard._NOT_RUN_REASONS.index)

    def test_every_reason_is_a_distinct_digit_free_string(self):
        assert len(set(routing_guard._NOT_RUN_REASONS)) == len(routing_guard._NOT_RUN_REASONS)
        assert not any(ch.isdigit() for reason in routing_guard._NOT_RUN_REASONS for ch in reason)


class TestSampleReasonsEndToEnd:
    def test_an_unbalanced_account_is_not_run_naming_its_first_row(self, lab, capsys):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(growth=1)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_ARM_SIZE_MISMATCH, 1)]

    def test_nine_hand_written_on_rows_are_not_run_at_the_real_minimum_and_ten_are_not(self, lab_at_the_real_minimum, capsys):
        lab = lab_at_the_real_minimum
        account = lab.account()
        for index in range(9):
            lab.row(account, f"on-{index}", "on", loops=[_loop(input_tokens=1_000_000, growth=10 * (index + 1))])
            lab.row(account, f"off-{index}", "off", loops=[_loop(input_tokens=1_000_000, growth=20 * (index + 1))])
        lab.write_note()

        nine = _compute(capsys, lab)

        lab.row(account, "on-9", "on", loops=[_loop(input_tokens=1_000_000, growth=100)])
        lab.row(account, "off-9", "off", loops=[_loop(input_tokens=1_000_000, growth=200)])
        lab.write_note()
        ten = _compute(capsys, lab)

        assert routing_guard._MIN_ON_BRANCHES == 10
        assert nine.code == 3
        assert _not_run_line(routing_guard._REASON_TOO_FEW_ON) in nine.out.splitlines()
        assert ten.code in (0, 1)
        assert routing_guard._REASON_TOO_FEW_ON not in ten.out

    def test_a_pilot_mismatch_is_not_run_naming_the_off_row(self, lab, capsys):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(growth=1)], pilot_states=("on", "on"))
        off_line = lab.row(account, "branch-off", "off", loops=[_loop(growth=2)], pilot_states=("off", "on"))
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_OFF_PILOT_DIFFERS, off_line)]

    def test_an_account_with_no_on_file_and_excluded_rows_do_not_affect_the_run_or_have_a_file_opened(
        self, lab, capsys, monkeypatch,
    ):
        account, _on_line, _off_line = _minimal_pair(lab)
        stray = lab.account("acct-stray")
        lab.row(stray, "stray-off", "off", loops=[_loop(growth=3)])
        lab.row(stray, "in-progress", status="excluded", loops=[_loop(growth=5)])
        lab.row(account, "excluded-beside-the-pair", status="excluded", loops=[_loop(growth=6)])
        lab.write_note()
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab)

        opened_names = {Path(path).name for path in spies.opened_paths()}
        assert outcome.code in (0, 1)
        assert "NOT RUN" not in outcome.out
        assert {_digest_name("branch-on"), _digest_name("branch-off")} <= opened_names
        for ignored_branch in ("in-progress", "excluded-beside-the-pair"):
            assert _digest_name(ignored_branch) not in opened_names

    def test_an_account_with_only_key_off_files_and_its_key_off_is_not_run(self, lab, capsys, monkeypatch):
        account, _on_line, _off_line = _minimal_pair(lab)
        key_off_account = lab.account("acct-key-off", key_on=False)
        stray_line = lab.row(key_off_account, "stray-off", "off", loops=[_loop(growth=4)])
        lab.write_note()
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_ACCOUNT_NOT_READY, stray_line)]
        assert _digest_name("stray-off") not in {Path(path).name for path in spies.opened_paths()}

    def test_one_invalid_on_file_among_ten_gives_exactly_the_per_row_reason_and_no_count_reason(
        self, lab_at_the_real_minimum, capsys,
    ):
        lab = lab_at_the_real_minimum
        account = lab.account()
        # Nine loaded key-on files plus the invalid one: counting only the loaded files would add the key-on minimum
        # and the arm-size reasons.
        for index in range(9):
            lab.row(account, f"on-{index}", "on", loops=[_loop(input_tokens=1_000_000, growth=10 + index)])
        for index in range(10):
            lab.row(account, f"off-{index}", "off", loops=[_loop(input_tokens=1_000_000, growth=20 + index)])
        invalid_line = lab.row(account, "on-invalid", "on")
        (account / _CAPTURES_DIR / _digest_name("on-invalid")).write_bytes(b"not json")
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_FILE_UNUSABLE, invalid_line)]

    def test_a_log_in_the_account_directory_that_contradicts_its_files_changes_nothing_because_compute_reads_no_log(
        self, lab, capsys, monkeypatch,
    ):
        account, _on_line, _off_line = _minimal_pair(lab)
        baseline = _compute(capsys, lab)
        _write_log(account, _log_row("any-session", routing="false"), _log_row("another-session", routing="true"))
        spies = _install_path_spies(monkeypatch)

        with_log = _compute(capsys, lab)

        assert (with_log.code, with_log.out) == (baseline.code, baseline.out)
        assert not any(path.endswith(".session-config-keys-log.jsonl") for path in spies.opened_paths())


class TestFlags:
    def test_a_top_level_config_dir_is_refused_with_exit_2_and_its_own_message(self, lab, capsys):
        _minimal_pair(lab)

        outcome = _compute(capsys, lab, config_dir=str(lab.root / "acct-a"))

        assert outcome.code == 2
        assert outcome.err.strip() == routing_guard._COMPUTE_CONFIG_DIR_MESSAGE
        assert outcome.out == ""

    def test_out_with_capture_is_refused_with_exit_2_through_the_shim_parser(self, lab, capsys):
        _minimal_pair(lab)
        args = _mod.build_parser().parse_args(
            ["--config-dir", str(lab.root / "acct-a"), "routing-guard", "--capture", "--note", str(lab.note),
             "--out", str(lab.root / "figures.txt")],
        )

        with pytest.raises(SystemExit) as exit_info:
            args.func(args)

        assert exit_info.value.code == 2
        assert capsys.readouterr().err.strip() == routing_guard._OUT_WITH_CAPTURE_MESSAGE
        assert not (lab.root / "figures.txt").exists()

    def test_the_shim_parser_runs_compute_on_hand_written_files(self, lab, capsys):
        _minimal_pair(lab)
        args = _mod.build_parser().parse_args(["routing-guard", "--note", str(lab.note)])

        with pytest.raises(SystemExit) as exit_info:
            args.func(args)

        assert exit_info.value.code in (0, 1)
        assert args.out is None and args.capture is False
        assert capsys.readouterr().out.splitlines()[-1] in (routing_guard._STATUS_PASSED, routing_guard._STATUS_FAILED)

    def test_a_note_readable_by_others_prints_the_permission_notice_and_an_owner_only_note_does_not(self, lab, capsys):
        _minimal_pair(lab)
        lab.write_note(mode=0o644)
        permissive = _compute(capsys, lab)
        lab.write_note(mode=0o600)
        owner_only = _compute(capsys, lab)

        assert routing_guard._PERMISSIVE_NOTE_NOTICE in permissive.err
        assert routing_guard._PERMISSIVE_NOTE_NOTICE not in owner_only.err


# --- capture files -----------------------------------------------------------


def _sentinel_cells() -> list[tuple[str, dict]]:
    """Capture file contents the validator refuses, each carrying a sentinel in the rejected cell."""
    base = _loop(input_tokens=5, growth=1)
    cases = [
        ("unknown-variant", {"loops": [{**base, "overhead_tokens": {"sentinel-model": {"sentinel-variant": {"input": 1}}}}]}),
        ("unknown-class", {"loops": [{**base, "overhead_tokens": {_SONNET: {"standard": {"sentinel-class": 1}}}}]}),
        ("model-not-an-object", {"loops": [{**base, "overhead_tokens": {"sentinel-model": 1}}]}),
        ("extra-loop-key", {"loops": [{**base, "sentinel-key": 1}]}),
        ("later-turns-cell", {"loops": [{**base, "later_turns": {"sentinel-model": "sentinel-count"}}]}),
        ("extra-root-key", {"loops": [base], "sentinel-key": 1}),
    ]
    with_states = [(case_id, {**_VALID_STATES, **content}) for case_id, content in cases]
    state_cells = [
        (f"{key}-sentinel-state", {"loops": [base], **{**_VALID_STATES, key: "sentinel-state"}}) for key in _VALID_STATES
    ]
    return [*with_states, *state_cells]


class TestCaptureFiles:
    @staticmethod
    def _two_row_lab(lab: _Lab) -> tuple[Path, int, int]:
        account = lab.account()
        on_line = lab.row(account, "branch-on", "on", loops=[_loop(growth=1)])
        off_line = lab.row(account, "branch-off", "off", loops=[_loop(growth=2)])
        return account, on_line, off_line

    def test_a_missing_file_is_a_reason_naming_that_row(self, lab, capsys):
        account, _on_line, off_line = self._two_row_lab(lab)
        (account / _CAPTURES_DIR / _digest_name("branch-off")).unlink()
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_FILE_MISSING, off_line)]

    @pytest.mark.parametrize("state", ["directory", "live-symlink", "dangling-symlink", "invalid-bytes"])
    def test_a_file_that_cannot_be_read_or_fails_the_validator_is_a_reason_naming_that_row(
        self, lab, capsys, tmp_path, state,
    ):
        account, on_line, _off_line = self._two_row_lab(lab)
        path = account / _CAPTURES_DIR / _digest_name("branch-on")
        path.unlink()
        if state == "directory":
            path.mkdir()
        elif state == "live-symlink":
            real_file = tmp_path / "real-capture.json"
            real_file.write_text(json.dumps({"loops": [_loop(growth=1)]}))
            path.symlink_to(real_file)
        elif state == "dangling-symlink":
            path.symlink_to(tmp_path / "no-such-target.json")
        else:
            path.write_bytes(b"not json")
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_FILE_UNUSABLE, on_line)]

    def test_two_bad_files_of_different_shapes_give_two_reasons_each_naming_its_own_row(self, lab, capsys):
        account, on_line, off_line = self._two_row_lab(lab)
        (account / _CAPTURES_DIR / _digest_name("branch-on")).write_bytes(b"[]")
        (account / _CAPTURES_DIR / _digest_name("branch-off")).unlink()
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.out.splitlines() == [
            _not_run_line(routing_guard._REASON_FILE_MISSING, off_line),
            _not_run_line(routing_guard._REASON_FILE_UNUSABLE, on_line),
        ]

    @pytest.mark.parametrize(("case_id", "content"), _sentinel_cells(), ids=[case for case, _ in _sentinel_cells()])
    def test_a_sentinel_in_a_rejected_cell_never_reaches_a_stream(self, lab, capsys, case_id, content):
        account, on_line, _off_line = self._two_row_lab(lab)
        (account / _CAPTURES_DIR / _digest_name("branch-on")).write_text(json.dumps(content))
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_FILE_UNUSABLE, on_line)]
        assert "sentinel" not in outcome.out + outcome.err

    def test_a_file_at_another_revision_a_file_for_an_unlisted_branch_and_a_temp_file_are_never_opened(
        self, lab, capsys, monkeypatch,
    ):
        account, _on_line, _off_line = self._two_row_lab(lab)
        lab.write_note()
        other_revision = lab.write_capture(account, "branch-on", [_loop(growth=1)], revision=2)
        unlisted = lab.write_capture(account, "unlisted-branch", [_loop(growth=1)])
        temp_file = account / _CAPTURES_DIR / ".routing-guard-planted.tmp"
        temp_file.write_text("not a capture")
        spies = _install_path_spies(monkeypatch)

        _compute(capsys, lab)

        opened_names = {Path(path).name for path in spies.opened_paths()}
        assert other_revision.name not in opened_names
        assert unlisted.name not in opened_names
        assert temp_file.name not in opened_names
        assert _digest_name("branch-on") in opened_names

    def test_a_branch_with_a_unicode_and_slash_name_published_by_capture_is_found_by_compute(self, lab, capsys):
        branch = "feat/ünï-cøde"
        account = lab.account()
        round_facts = routing_guard._RoundFacts(
            session_key="session-a", session_id="session-a", open_idx=0, open_ts=1.0, has_marker_write=True,
            context_at_open=10, context_at_window_end=40, later_turns={},
            overhead_tokens={_SONNET: {"standard": {"input": 1_000_000}}},
            degraded=False, denied_open=False, spawn_open=False,
        )
        scan = routing_guard._ScanFacts()
        scan.branches[branch] = routing_guard._BranchFacts(
            has_timestamped_record=True, first_record_ts=1.0,
        )
        scan.rounds_by_branch[branch] = [round_facts]
        on_row = routing_guard._NoteRow(1, account.resolve(), branch, "merged")
        key_states = routing_guard._BranchKeyStates(
            {key: "on" if key == routing_guard._ROUTING_KEY else "off" for key in routing_guard._STATE_KEYS}, (),
        )
        # The published file goes through capture's own row publisher, then compute finds it by the branch's digest.
        report = routing_guard._capture_account_rows(
            [on_row], {1: routing_guard._StandingFile.ABSENT}, {1: key_states}, set(), scan, account,
        )
        capsys.readouterr()
        lab.row(account, branch)
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert report.kinds[routing_guard._RowKind.CAPTURED] == 1
        assert (account / _CAPTURES_DIR / _digest_name(branch)).is_file()
        assert outcome.code in (0, 1)
        assert "NOT RUN" not in outcome.out


# --- pricing -----------------------------------------------------------------


class TestPricing:
    @pytest.mark.parametrize("variant", pricing._PRICING_VARIANTS)
    def test_the_usage_rebuild_returns_each_pricing_variant_label(self, variant):
        usage = routing_guard._usage_from_variant_tokens(variant, {"input": 1})

        assert pricing._pricing_variant(usage) == variant

    def test_the_usage_rebuild_keeps_a_nonzero_one_hour_write_in_its_own_class(self):
        usage = routing_guard._usage_from_variant_tokens(
            "standard", {"cache_write_1h": 7, "cache_write_5m": 3, "input": 1, "output": 2, "cache_read": 4},
        )

        assert pricing._cache_write_split(usage) == (7, 3)
        assert pricing._token_counts(usage) == {
            "input": 1, "output": 2, "cache_read": 4, "cache_write_1h": 7, "cache_write_5m": 3,
        }

    def test_a_loops_overhead_equals_the_round_dollars_review_round_cost_prints(self, fake_projects):
        session_id = "sess-1"
        records = [
            _priced(
                _SONNET, input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _priced(_SONNET, input=50_000, output=2_000, branch="feat", ts="2026-08-01T10:00:10.000Z", speed="fast"),
            _priced(_SONNET, input=60_000, branch="feat", ts="2026-08-01T10:00:20.000Z", inference_geo="us"),
            _priced(
                _OPUS, input=70_000, cache_read=900_000, branch="feat", ts="2026-08-01T10:00:30.000Z",
                speed="fast", inference_geo="us",
            ),
            _priced(
                _SONNET, input=10_000, ephemeral_1h=40_000, ephemeral_5m=5_000, branch="feat",
                ts="2026-08-01T10:00:40.000Z",
            ),
            _priced(_UNPRICED_MODEL, input=80_000, branch="feat", ts="2026-08-01T10:00:50.000Z"),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ]
        _write_jsonl(fake_projects / f"{session_id}.jsonl", records)
        nested_spawn_rec = _priced(
            _SONNET, input=100_000, branch="feat", ts="2026-08-01T10:00:10.000Z", content=[_agent_use("n1", "staff-sdet")],
        )
        nested_spawn_rec["isSidechain"] = True
        _write_subagent_dispatch(fake_projects, session_id, "agent-1", "a1", [nested_spawn_rec])
        nested_rec = _priced(_SONNET, input=500_000, cache_read=20_000, branch="feat", ts="2026-08-01T10:00:20.000Z")
        nested_rec["isSidechain"] = True
        _write_subagent_dispatch(fake_projects, session_id, "agent-2", "n1", [nested_rec], spawn_depth=2)
        expected_dollars = sum(
            round_["main_dollars"] + round_["agent_dollars"]
            for round_ in review_rounds.compute_review_round_costs(corpus.iter_sessions(fake_projects.parent, "*"))["rounds"]
        )
        windows = review_rounds.detect_round_windows(records)
        entries = review_rounds._price_session_rounds(
            fake_projects / f"{session_id}.jsonl", records, windows,
            review_rounds._session_record_branches(records, windows), None, defaultdict(float), collect_tokens=True,
        )
        loop = {
            "overhead_tokens": entries[0]["tokens"],
            "growth_tokens": None, "later_turns": {}, "degraded": False,
        }

        scalars = routing_guard._branch_scalars([loop], "digest", price_later_turns=False)

        assert expected_dollars > 0
        assert scalars.overhead_dollars == pytest.approx(expected_dollars)
        assert scalars.degraded_loops == 1
        assert _UNPRICED_MODEL in loop["overhead_tokens"]

    @pytest.mark.parametrize("model", ["<synthetic>", "", _UNPRICED_MODEL])
    def test_a_model_the_table_does_not_price_gives_a_degraded_notice_and_no_crash(self, lab, capsys, model):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(input_tokens=5, growth=1, model=model)])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code in (0, 1)
        assert routing_guard._DEGRADED_NOTICES["on"] in outcome.err.splitlines()
        assert routing_guard._DEGRADED_NOTICES["off"] not in outcome.err.splitlines()

    @pytest.mark.parametrize(
        "key_on_loop",
        [
            pytest.param(
                {
                    "overhead_tokens": {
                        _SONNET: {"standard": {"input": 1_000_000}}, _UNPRICED_MODEL: {"standard": {"input": 5}},
                    },
                    "growth_tokens": 100, "later_turns": {}, "degraded": False,
                },
                id="a-skipped-model",
            ),
            pytest.param(_loop(input_tokens=1_000_000, growth=100, degraded=True), id="a-lost-dispatch"),
        ],
    )
    def test_a_degraded_key_on_arm_on_the_passed_side_still_prints_passed_as_the_only_stdout_line_beside_its_notice(
        self, lab, capsys, key_on_loop,
    ):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[key_on_loop])
        lab.row(account, "branch-off", "off", loops=[_loop(input_tokens=2_000_000, growth=300, later={_SONNET: 10})])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 0
        assert outcome.out.splitlines() == [routing_guard._STATUS_PASSED]
        assert routing_guard._DEGRADED_NOTICES["on"] in outcome.err.splitlines()

    def test_an_unlisted_model_name_in_a_capture_file_reaches_no_output_stream_and_the_run_completes_degraded(
        self, lab, capsys,
    ):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(input_tokens=5, growth=1, model=_UNLISTED_MODEL_SENTINEL)])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2, later={_UNLISTED_MODEL_SENTINEL: 4})])
        lab.write_note()
        out_path = lab.root / "figures.txt"

        outcome = _compute(capsys, lab, out=str(out_path))

        assert outcome.code in (0, 1)
        assert routing_guard._DEGRADED_NOTICES["on"] in outcome.err.splitlines()
        assert routing_guard._DEGRADED_NOTICES["off"] in outcome.err.splitlines()
        for stream_text in (outcome.out, outcome.err, out_path.read_text()):
            assert _UNLISTED_MODEL_SENTINEL not in stream_text

    def test_a_model_unpriced_when_captured_is_priced_once_the_table_lists_it(self, lab, capsys, monkeypatch):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(input_tokens=1_000_000, growth=1, model="claude-future-1")])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2)])
        lab.write_note()
        unlisted = _compute(capsys, lab)
        monkeypatch.setitem(pricing._MODEL_BASE_INPUT_RATES, "claude-future-1", 3.00)
        monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, "claude-future-1", _FAR_FUTURE_RATE_EXPIRY)

        listed = _compute(capsys, lab)

        assert routing_guard._DEGRADED_NOTICES["on"] in unlisted.err.splitlines()
        assert routing_guard._DEGRADED_NOTICES["on"] not in listed.err.splitlines()

    def test_an_unpriced_model_in_a_key_on_rows_later_turns_is_never_priced_and_gives_no_notice(self, lab, capsys):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(growth=1, later={_UNPRICED_MODEL: 4})])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.err.count("degraded") == 0

    def test_an_unpriced_model_in_a_key_off_rows_later_turns_is_degraded(self, lab, capsys):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(growth=1)])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2, later={_UNPRICED_MODEL: 4})])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert routing_guard._DEGRADED_NOTICES["off"] in outcome.err.splitlines()

    @pytest.mark.parametrize(
        ("expired_model", "line_prints", "key_on_overhead_model"),
        [
            pytest.param(_SONNET, True, _SONNET, id="expired-for-a-model-priced-through-overhead"),
            pytest.param(_OPUS, True, _SONNET, id="expired-for-a-model-priced-only-through-a-key-off-rows-later-turns"),
            pytest.param(
                _LISTED_MODEL_NOT_IN_THE_SAMPLE, True, _LISTED_MODEL_NOT_IN_THE_SAMPLE,
                id="expired-for-a-model-priced-only-through-a-key-on-rows-overhead",
            ),
            pytest.param(
                _LISTED_MODEL_NOT_IN_THE_SAMPLE, False, _SONNET, id="expired-for-a-listed-model-the-sample-never-priced",
            ),
            pytest.param(None, False, _SONNET, id="expired-for-no-model"),
        ],
    )
    def test_the_stale_pricing_line_prints_only_for_an_expired_model_compute_priced(
        self, lab, capsys, monkeypatch, expired_model, line_prints, key_on_overhead_model,
    ):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(input_tokens=1_000_000, growth=100, model=key_on_overhead_model)])
        lab.row(account, "branch-off", "off", loops=[_loop(input_tokens=1_000_000, growth=300, later={_OPUS: 10})])
        lab.write_note()
        if expired_model is not None:
            monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, expired_model, _FAR_PAST_RATE_EXPIRY)

        outcome = _compute(capsys, lab)

        assert (routing_guard._STALE_PRICING_LINE in outcome.err.splitlines()) == line_prints
        assert not any(ch.isdigit() for ch in routing_guard._STALE_PRICING_LINE)


# --- the point net -----------------------------------------------------------


def _hand_computed_fixture(lab: _Lab) -> None:
    """Two accounts with arms of different sizes. Overhead is priced at $2.00 per million sonnet input tokens.

    Key-on: five loops costing $20.00 in all, with growth 100k, 200k, 300k and 200k on four of them.
    Key-off: three loops costing $6.00 each, with growth 500k, 700k and 600k.
    Later turns: sonnet at $0.20 and opus at $0.50 per million tokens.
    """
    first = lab.account("acct-a")
    second = lab.account("acct-b")
    lab.row(first, "on-1", "on", loops=[_loop(input_tokens=1_000_000, growth=100_000), _loop(input_tokens=1_000_000)])
    lab.row(first, "on-2", "on", loops=[
        _loop(input_tokens=3_000_000, growth=200_000), _loop(input_tokens=3_000_000, growth=300_000),
    ])
    lab.row(first, "off-1", "off", loops=[_loop(input_tokens=3_000_000, growth=500_000, later={_SONNET: 10, _OPUS: 5})])
    lab.row(first, "off-2", "off", loops=[_loop(input_tokens=3_000_000, growth=700_000, later={_SONNET: 20})])
    lab.row(second, "on-3", "on", loops=[_loop(input_tokens=2_000_000, growth=200_000)])
    lab.row(second, "off-3", "off", loops=[_loop(input_tokens=3_000_000, growth=600_000, later={_SONNET: 10, _OPUS: 5})])
    lab.write_note()


class TestPointNet:
    def test_the_net_equals_the_hand_computed_value_with_four_distinct_terms(self, lab, capsys):
        _hand_computed_fixture(lab)
        out_path = lab.root / "figures.txt"

        outcome = _compute(capsys, lab, out=str(out_path))

        figure_lines = out_path.read_text().splitlines()
        # On: $20.00 / 5 loops = $4.00 per loop. Off: $18.00 / 3 loops = $6.00 per loop. Gap: 600k - 200k = 400k tokens.
        # Carry: 400k * (10*0.2 + 5*0.5 + 20*0.2 + 10*0.2 + 5*0.5)e-6 / 3 = 400k * 13e-6 / 3 = $1.7333 per loop.
        assert outcome.code in (0, 1)
        assert "overhead difference per loop: -$2.00" in figure_lines
        assert "carry per loop: $1.73" in figure_lines
        assert "growth gap, key-off minus key-on, tokens per loop: 400,000.0" in figure_lines
        assert "net per loop: -$3.73" in figure_lines

    def test_the_arm_net_over_scalars_matches_the_hand_computed_terms_unrounded(self):
        on = [
            routing_guard._BranchScalars("d1", 2, 100_000, 1, 8.0, 0.0, 0, frozenset()),
            routing_guard._BranchScalars("d2", 2, 500_000, 2, 12.0, 0.0, 0, frozenset()),
        ]
        off = [
            routing_guard._BranchScalars("d3", 1, 500_000, 1, 6.0, 4.5e-6, 0, frozenset()),
            routing_guard._BranchScalars("d4", 1, 700_000, 1, 6.0, 4.0e-6, 0, frozenset()),
        ]

        figures = routing_guard._arm_net(on, off)

        # On: $20 / 4 loops = $5. Off: $12 / 2 = $6. Gap: 600k - 200k = 400k. Carry: 400k * 8.5e-6 / 2 = $1.70.
        assert figures.overhead_difference == pytest.approx(-1.0)
        assert figures.gap == pytest.approx(400_000)
        assert figures.carry == pytest.approx(1.70)
        assert figures.net == pytest.approx(-2.70)

    def test_a_key_on_arm_that_grew_more_than_the_key_off_arm_gives_a_negative_gap_and_a_negative_carry_that_raises_the_net(self):
        on = [routing_guard._BranchScalars("d1", 1, 700_000, 1, 5.0, 0.0, 0, frozenset())]
        off = [routing_guard._BranchScalars("d2", 2, 600_000, 2, 8.0, 4.0e-6, 0, frozenset())]

        figures = routing_guard._arm_net(on, off)

        # On: $5 / 1 loop = $5. Off: $8 / 2 = $4. Gap: key-off mean 300k less key-on mean 700k = -400k.
        # Carry: -400k * 4e-6 / 2 loops = -$0.80, so the net is 1 - (-0.80).
        assert figures.overhead_difference == pytest.approx(1.0)
        assert figures.gap == pytest.approx(-400_000)
        assert figures.carry == pytest.approx(-0.80)
        assert figures.net == pytest.approx(1.80)

    def test_a_key_off_branch_with_only_null_growth_loops_still_counts_in_the_per_loop_overhead_and_carry_denominators(self):
        on = [routing_guard._BranchScalars("d1", 2, 200_000, 2, 10.0, 0.0, 0, frozenset())]
        off = [
            routing_guard._BranchScalars("d2", 1, 600_000, 1, 6.0, 4.0e-6, 0, frozenset()),
            routing_guard._BranchScalars("d3", 2, 0, 0, 14.0, 6.0e-6, 0, frozenset()),
        ]

        figures = routing_guard._arm_net(on, off)

        # On: $10 / 2 loops = $5. Off: $20 / 3 loops = $6.67, because the null-growth branch's 2 loops count.
        # Gap: key-off mean 600k over its 1 growth-defined loop, less key-on mean 100k (200k / 2 loops) = 500k tokens.
        # Carry: 500k * 10e-6 / 3 key-off loops = $1.67.
        assert figures.overhead_difference == pytest.approx(5.0 - 20.0 / 3)
        assert figures.gap == pytest.approx(500_000)
        assert figures.carry == pytest.approx(500_000 * 10.0e-6 / 3)
        assert figures.net == pytest.approx(5.0 - 20.0 / 3 - 500_000 * 10.0e-6 / 3)

    def test_the_out_file_figures_of_an_arm_holding_a_null_growth_key_off_loop_equal_the_hand_computed_values(
        self, lab, capsys,
    ):
        account = lab.account()
        lab.row(account, "on-1", "on", loops=[_loop(input_tokens=1_000_000, growth=100_000)])
        lab.row(account, "on-2", "on", loops=[_loop(input_tokens=1_000_000, growth=300_000)])
        lab.row(account, "off-1", "off", loops=[_loop(input_tokens=2_000_000, growth=600_000, later={_SONNET: 10})])
        lab.row(account, "off-2", "off", loops=[_loop(input_tokens=4_000_000, growth=None, later={_SONNET: 10})])
        lab.write_note()
        out_path = lab.root / "figures.txt"

        outcome = _compute(capsys, lab, out=str(out_path))

        figure_lines = out_path.read_text().splitlines()
        # On: $4.00 / 2 loops = $2.00. Off: $12.00 / 2 loops = $6.00, the null-growth loop included.
        # Gap: key-off mean 600k over its 1 growth-defined loop, less key-on mean 200k ((100k + 300k) / 2 loops) = 400k tokens.
        # Carry: 400k * (10 + 10) * 0.2e-6 / 2 loops = $0.80.
        assert outcome.code in (0, 1)
        assert "overhead difference per loop: -$4.00" in figure_lines
        assert "carry per loop: $0.80" in figure_lines
        assert "growth gap, key-off minus key-on, tokens per loop: 400,000.0" in figure_lines
        assert "net per loop: -$4.80" in figure_lines
        assert "key-off loops: 2" in figure_lines
        assert "key-off growth-defined loops: 1" in figure_lines

    def test_the_net_is_undefined_when_an_arm_has_no_growth_defined_loop(self):
        defined = routing_guard._BranchScalars("d1", 1, 5, 1, 1.0, 0.0, 0, frozenset())
        undefined = routing_guard._BranchScalars("d2", 1, 0, 0, 1.0, 0.0, 0, frozenset())

        assert routing_guard._arm_net([defined], [undefined]) is None
        assert routing_guard._arm_net([undefined], [defined]) is None

    def test_a_branch_with_a_null_growth_loop_counts_its_overhead_and_loop_but_not_its_growth(self):
        loops = [_loop(input_tokens=1_000_000, growth=None), _loop(growth=40)]

        scalars = routing_guard._branch_scalars(loops, "d", price_later_turns=False)

        assert scalars.loop_count == 2
        assert scalars.growth_defined_loops == 1
        assert scalars.growth_sum == 40
        assert scalars.overhead_dollars == pytest.approx(2.0)

    def test_the_carry_rate_is_per_token_so_a_missing_per_million_divisor_would_show(self):
        scalars = routing_guard._branch_scalars(
            [_loop(growth=1, later={_SONNET: 1_000_000})], "d", price_later_turns=True,
        )

        assert scalars.carry_rate_sum == pytest.approx(0.2)


# --- the bootstrap -----------------------------------------------------------


def _bs(digest: str, *, loops: int = 1, growth_sum: int = 0, growth_loops: int = 1, overhead: float = 0.0, rate: float = 0.0):
    return routing_guard._BranchScalars(digest, loops, growth_sum, growth_loops, overhead, rate, 0, frozenset())


class _ScriptedRng:
    """Returns prearranged index draws and fails on any call past the script."""

    def __init__(self, draws: list[list[int]]):
        self._draws = iter(draws)
        self.calls: list[tuple[list, int]] = []

    def choices(self, population, k):
        self.calls.append((list(population), k))
        indices = next(self._draws)
        assert len(indices) == k
        return [population[i] for i in indices]


class _SpyRng(random.Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.calls: list[tuple[list, int]] = []

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        self.calls.append((list(population), k))
        return super().choices(population, weights, cum_weights=cum_weights, k=k)


def _six_branch_arms() -> tuple[list, list]:
    on = [_bs(f"on-{i}", loops=1 + i % 3, growth_sum=1000 * (i + 1), growth_loops=1 + i % 2, overhead=2.0 + i) for i in range(6)]
    off = [
        _bs(f"off-{i}", loops=1 + i % 2, growth_sum=2500 * (i + 2), growth_loops=1, overhead=5.0 + 2 * i, rate=1e-6 * (i + 1))
        for i in range(6)
    ]
    return on, off


class TestBootstrap:
    def test_the_bounds_of_two_branches_per_arm_are_the_extremes_of_nine_pairwise_distinct_nets(self):
        on = [_bs("a", loops=1, growth_sum=100, overhead=1.0), _bs("b", loops=2, growth_sum=700, growth_loops=2, overhead=6.0)]
        off = [
            _bs("c", loops=1, growth_sum=400, overhead=5.0, rate=2e-6),
            _bs("d", loops=3, growth_sum=1500, growth_loops=3, overhead=21.0, rate=9e-6),
        ]
        multisets = [[0, 0], [0, 1], [1, 1]]
        nets = [
            routing_guard._arm_net([on[i] for i in on_draw], [off[j] for j in off_draw]).net
            for on_draw, off_draw in product(multisets, multisets)
        ]

        result = routing_guard._bootstrap_net(on, off, replicates=10_000, rng=random.Random(0))

        assert len({round(net, 12) for net in nets}) == 9
        assert result.lower == min(nets)
        assert result.upper == max(nets)
        assert result.undefined_total == 0

    def test_each_replicate_draws_each_arm_sorted_by_digest_with_k_its_size(self):
        on = [_bs("z"), _bs("a"), _bs("m")]
        off = [_bs("y"), _bs("b")]
        rng = _SpyRng(0)

        routing_guard._bootstrap_net(on, off, replicates=3, rng=rng)

        draws_per_replicate = [([b.digest for b in population], k) for population, k in rng.calls]
        assert len(draws_per_replicate) == 6
        assert sorted(draws_per_replicate) == sorted([(["a", "m", "z"], 3), (["b", "y"], 2)] * 3)

    def test_the_result_does_not_depend_on_the_global_random_state_and_depends_on_the_injected_seed(self):
        on, off = _six_branch_arms()
        global_state_before = random.getstate()
        try:
            random.seed(11)
            first = routing_guard._bootstrap_net(on, off, replicates=300, rng=random.Random(0))
            random.seed(99)
            second = routing_guard._bootstrap_net(on, off, replicates=300, rng=random.Random(0))
        finally:
            random.setstate(global_state_before)
        other_seed = routing_guard._bootstrap_net(on, off, replicates=300, rng=random.Random(1))

        assert (first.lower, first.upper) == (second.lower, second.upper)
        assert (first.lower, first.upper) != (other_seed.lower, other_seed.upper)

    # A scripted stream is consumed in call order, so these draws follow production's order of the key-on then key-off draw.
    def test_a_scripted_stream_ranks_undefined_replicates_last_and_reads_the_lower_bound_at_index_10_of_the_360_defined(self):
        on = [_bs("01", overhead=1.0, growth_sum=10), _bs("02", overhead=3.0, growth_loops=0)]
        off = [_bs("03", overhead=10.0, growth_sum=10), _bs("04", overhead=4.0, growth_sum=10)]
        groups = [
            ([0, 0], [0, 0], 10), ([0, 0], [0, 1], 100), ([0, 0], [1, 1], 100),
            ([0, 1], [0, 0], 50), ([0, 1], [1, 1], 100), ([1, 1], [0, 0], 40),
        ]
        draws = [draw for on_draw, off_draw, count in groups for draw in [on_draw, off_draw] * count]
        defined_nets = sorted(
            routing_guard._arm_net([on[i] for i in on_draw], [off[j] for j in off_draw]).net
            for on_draw, off_draw, count in groups[:-1] for _ in range(count)
        )
        rng = _ScriptedRng(draws)

        result = routing_guard._bootstrap_net(on, off, replicates=400, rng=rng)

        assert len(defined_nets) == 360
        assert defined_nets[9] != defined_nets[10], "a dropped-replicate implementation would read index 9"
        assert result.lower == defined_nets[10]
        assert math.isinf(result.upper), "index 389 falls among the 40 undefined replicates"
        assert (result.undefined_total, result.undefined_on, result.undefined_off) == (40, 40, 0)
        assert len(rng.calls) == 800

    def test_a_replicate_whose_key_on_arm_grew_more_than_the_key_off_arm_keeps_its_negative_gap_and_carry(self):
        on = [_bs("01", growth_sum=700_000, overhead=5.0), _bs("02", growth_sum=100_000, overhead=3.0)]
        off = [_bs("03", growth_sum=300_000, overhead=4.0, rate=5e-6), _bs("04", growth_sum=900_000, overhead=8.0, rate=1e-6)]
        # Two replicates put the lower bound at the smaller net and the upper bound at the larger.
        # Replicate 1 (branch 01 twice, branch 03 twice): overhead difference 5 - 4 = 1, gap 300k - 700k = -400k,
        # carry -400k * 10e-6 / 2 loops = -2, net 1 - (-2) = 3.
        # Replicate 2 (branch 02 twice, branch 04 twice): overhead difference 3 - 8 = -5, gap 900k - 100k = 800k,
        # carry 800k * 2e-6 / 2 loops = 0.8, net -5 - 0.8 = -5.8.
        rng = _ScriptedRng([[0, 0], [0, 0], [1, 1], [1, 1]])

        result = routing_guard._bootstrap_net(on, off, replicates=2, rng=rng)

        assert result.upper == pytest.approx(3.0)
        assert result.lower == pytest.approx(-5.8)

    def test_an_unbounded_upper_bound_is_written_as_unbounded_never_inf(self):
        defined = _bs("01", growth_sum=10)
        figures = routing_guard._arm_net([defined], [defined])
        bootstrap = routing_guard._BootstrapResult(-1.0, math.inf, 400, 40, 0, 40)
        totals = routing_guard._arm_totals([defined])

        text = routing_guard._out_file_text(figures, bootstrap, totals, totals)

        interval_line = next(line for line in text.splitlines() if "interval" in line)
        assert interval_line == "95% interval: -$1.00 to unbounded"
        assert "inf" not in interval_line

    def test_a_thin_growth_arm_at_ten_branches_runs_to_a_status_with_about_a_tenth_of_replicates_undefined(self):
        on = [
            _bs(f"on-{i:02d}", growth_sum=100 * i if i < 2 else 0, growth_loops=1 if i < 2 else 0, overhead=2.0 + i)
            for i in range(10)
        ]
        off = [_bs(f"off-{i:02d}", growth_sum=900 + 50 * i, overhead=6.0 + (i % 3), rate=1e-6) for i in range(10)]
        rng = _SpyRng(0)

        result = routing_guard._bootstrap_net(on, off, replicates=10_000, rng=rng)

        # The seeded replay matches the production stream only in production's draw order, key-on then key-off.
        reference_rng = random.Random(0)
        reference_nets = []
        for _ in range(10_000):
            figures = routing_guard._arm_net(
                reference_rng.choices(on, k=10), reference_rng.choices(off, k=10),
            )
            reference_nets.append(None if figures is None else figures.net)
        defined = sorted(net for net in reference_nets if net is not None)
        undefined_count = reference_nets.count(None)

        def lower_with_fill(fill: float) -> float:
            filled = sorted([*defined, *([fill] * undefined_count)])
            return review_rounds._resample_percentile(filled, level=0.95)[0]

        assert len(rng.calls) == 20_000
        assert result.undefined_total == undefined_count > 250
        assert defined[250] != defined[round(0.025 * (9_999 - undefined_count))]
        assert result.lower == lower_with_fill(max(defined))
        assert result.lower == lower_with_fill(1e300)
        assert result.lower >= lower_with_fill(-1e300)
        assert result.undefined_on == undefined_count and result.undefined_off == 0

    def test_one_replicate_that_leaves_an_arm_without_growth_gives_an_infinite_lower_bound(self):
        on = [_bs("01", growth_loops=0)]
        off = [_bs("02", growth_sum=5)]

        result = routing_guard._bootstrap_net(on, off, replicates=1, rng=_ScriptedRng([[0], [0]]))

        assert math.isinf(result.lower) and result.undefined_total == 1


class _PipelineSpy(NamedTuple):
    levels: list[float]
    replicate_counts: list[int]
    sorted_nets: list[list[float]]
    bounds: list[tuple[float, float]]
    results: list[routing_guard._BootstrapResult]


def _six_branch_lab(lab: _Lab, account_names: Sequence[str] = ("acct-a",)) -> list[int]:
    """Six key-on and six key-off hand-written branches differing in loops, overhead and growth, spread over the accounts.

    Returns each row's note line in creation order."""
    accounts = [lab.account(name) for name in account_names]
    lines = []
    for index in range(6):
        account = accounts[index % len(accounts)]
        lines.append(lab.row(account, f"on-{index}", "on", loops=[
            _loop(input_tokens=1_000_000 * (index + 1), growth=1_000 * (index + 1)),
            *([_loop(input_tokens=500_000, growth=None)] if index % 2 else []),
        ]))
    for index in range(6):
        account = accounts[index % len(accounts)]
        lines.append(lab.row(account, f"off-{index}", "off", loops=[
            _loop(input_tokens=1_500_000 + 700_000 * index, growth=4_000 + 900 * index, later={_SONNET: 3 + index, _OPUS: index}),
        ]))
    lab.write_note()
    return lines


@pytest.fixture
def pipeline_spy(monkeypatch) -> _PipelineSpy:
    spy = _PipelineSpy([], [], [], [], [])
    real_percentile = review_rounds._resample_percentile
    real_bootstrap = routing_guard._bootstrap_net

    def percentile_spy(sorted_values, level=None):
        spy.levels.append(level)
        spy.sorted_nets.append(list(sorted_values))
        bounds = real_percentile(sorted_values, level=level)
        spy.bounds.append(bounds)
        return bounds

    def bootstrap_spy(on, off, *, replicates, rng):
        spy.replicate_counts.append(replicates)
        result = real_bootstrap(on, off, replicates=replicates, rng=rng)
        spy.results.append(result)
        return result

    monkeypatch.setattr(review_rounds, "_resample_percentile", percentile_spy)
    monkeypatch.setattr(routing_guard, "_bootstrap_net", bootstrap_spy)
    return spy


class TestBootstrapThroughCompute:
    def test_the_default_path_uses_the_registered_level_count_and_seed(
        self, lab, capsys, pipeline_spy, production_bootstrap_replicates,
    ):
        _six_branch_lab(lab)

        _compute(capsys, lab)

        assert pipeline_spy.levels == [0.95]
        assert pipeline_spy.replicate_counts == [10_000]
        assert len(pipeline_spy.sorted_nets[0]) == 10_000
        loaded_on = [
            routing_guard._branch_scalars(
                [
                    _loop(input_tokens=1_000_000 * (i + 1), growth=1_000 * (i + 1)),
                    *([_loop(input_tokens=500_000)] if i % 2 else []),
                ],
                routing_guard._branch_digest(f"on-{i}"), price_later_turns=False,
            )
            for i in range(6)
        ]
        loaded_off = [
            routing_guard._branch_scalars(
                [_loop(input_tokens=1_500_000 + 700_000 * i, growth=4_000 + 900 * i, later={_SONNET: 3 + i, _OPUS: i})],
                routing_guard._branch_digest(f"off-{i}"), price_later_turns=True,
            )
            for i in range(6)
        ]
        direct = routing_guard._bootstrap_net(loaded_on, loaded_off, replicates=10_000, rng=random.Random(0))
        assert pipeline_spy.bounds[0] == (direct.lower, direct.upper)

    def test_the_ci_level_constant_is_read_when_the_call_runs(
        self, lab, capsys, monkeypatch, pipeline_spy, production_bootstrap_replicates,
    ):
        _six_branch_lab(lab)
        _compute(capsys, lab)
        monkeypatch.setattr(routing_guard, "_GUARD_CI_LEVEL", 0.90)
        _compute(capsys, lab)

        default_nets, narrowed_nets = pipeline_spy.sorted_nets
        assert pipeline_spy.bounds[0] == (default_nets[250], default_nets[9_749])
        assert pipeline_spy.bounds[1] == (narrowed_nets[500], narrowed_nets[9_499])
        assert default_nets[250] != default_nets[500]
        assert default_nets[9_749] != default_nets[9_499]

    def test_an_all_null_growth_arm_is_not_run_before_any_replicate(self, lab, capsys, pipeline_spy):
        account = lab.account()
        lab.row(account, "branch-on", "on", loops=[_loop(growth=None)])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=5)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_NO_GROWTH_DEFINED)]
        assert pipeline_spy.replicate_counts == []

    @pytest.mark.parametrize(("lower", "expected_code"), [(0.0, 0), (1e-9, 1), (-1e-9, 0)])
    def test_a_lower_bound_of_exactly_zero_passes_and_only_a_positive_one_fails(
        self, lab, capsys, monkeypatch, lower, expected_code,
    ):
        _minimal_pair(lab)
        bootstrap = routing_guard._BootstrapResult(lower, 1.0, 10_000, 0, 0, 0)
        monkeypatch.setattr(routing_guard, "_bootstrap_net", lambda on, off, *, replicates, rng: bootstrap)

        outcome = _compute(capsys, lab)

        assert outcome.code == expected_code
        assert outcome.out.splitlines() == [routing_guard._STATUS_FAILED if expected_code else routing_guard._STATUS_PASSED]

    @pytest.mark.parametrize("lower", [math.inf, math.nan, -math.inf])
    def test_a_lower_bound_that_is_not_finite_is_not_run_never_passed(self, lab, capsys, monkeypatch, lower):
        _minimal_pair(lab)
        bootstrap = routing_guard._BootstrapResult(lower, 1.0, 10_000, 0, 0, 0)
        monkeypatch.setattr(routing_guard, "_bootstrap_net", lambda on, off, *, replicates, rng: bootstrap)

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_NON_FINITE)]

    def test_a_point_net_that_is_not_finite_is_not_run(self, lab, capsys, monkeypatch):
        _minimal_pair(lab)
        monkeypatch.setattr(
            routing_guard, "_arm_net", lambda on, off: routing_guard._NetFigures(math.inf, 0.0, 0.0, 0.0),
        )

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert outcome.out.splitlines() == [_not_run_line(routing_guard._REASON_NON_FINITE)]

    def test_a_thin_growth_sample_runs_to_a_status_and_notes_the_undefined_replicates(
        self, lab, capsys, pipeline_spy, production_bootstrap_replicates,
    ):
        account = lab.account()
        for index in range(10):
            lab.row(account, f"on-{index}", "on", loops=[
                _loop(input_tokens=1_000_000 + index, growth=100 * index if index < 2 else None),
            ])
            lab.row(account, f"off-{index}", "off", loops=[_loop(input_tokens=2_000_000 + index, growth=900 + index)])
        lab.write_note()
        out_path = lab.root / "figures.txt"

        outcome = _compute(capsys, lab, out=str(out_path))

        figure_lines = out_path.read_text().splitlines()
        assert outcome.code in (0, 1)
        assert routing_guard._UNDEFINED_REPLICATE_NOTICES["on"] in outcome.err.splitlines()
        assert routing_guard._UNDEFINED_REPLICATE_NOTICES["off"] not in outcome.err.splitlines()
        assert "key-on growth-defined branches: 2" in figure_lines
        assert "key-on growth-defined loops: 2" in figure_lines
        assert "key-off growth-defined branches: 10" in figure_lines
        undefined_replicates = pipeline_spy.results[0].undefined_total
        assert undefined_replicates > 250
        assert f"undefined replicates: {undefined_replicates} of 10000" in figure_lines


# --- output ------------------------------------------------------------------


def _passed_fixture(lab: _Lab) -> Path:
    """Two branches per arm, differing within each arm in loops, overhead, growth and carry. Key-on costs far less."""
    account = lab.account()
    lab.row(account, "on-1", "on", loops=[_loop(input_tokens=1_000_000, growth=100_000)])
    lab.row(account, "on-2", "on", loops=[
        _loop(input_tokens=1_000_000, growth=300_000), _loop(input_tokens=2_000_000, growth=500_000),
    ])
    lab.row(account, "off-1", "off", loops=[_loop(input_tokens=6_000_000, growth=400_000, later={_SONNET: 10})])
    lab.row(account, "off-2", "off", loops=[
        _loop(input_tokens=7_000_000, growth=600_000, later={_SONNET: 5, _OPUS: 2}),
        _loop(input_tokens=5_000_000, growth=800_000),
    ])
    lab.write_note()
    return lab.root / "figures.txt"


class TestOutput:
    def test_the_out_file_is_owner_only_opens_with_the_banner_and_carries_every_figure(self, lab, capsys):
        out_path = _passed_fixture(lab)

        outcome = _compute(capsys, lab, out=str(out_path))

        text = out_path.read_text()
        assert outcome.code == 0
        assert stat.S_IMODE(out_path.stat().st_mode) == 0o600
        assert text.startswith(routing_guard._DO_NOT_PUBLISH_BANNER + "\n")
        assert [line.split(":")[0] for line in text.splitlines()[len(routing_guard._DO_NOT_PUBLISH_BANNER.splitlines()):]] == [
            "net per loop", "95% interval", "overhead difference per loop", "carry per loop",
            "growth gap, key-off minus key-on, tokens per loop",
            "key-on branches", "key-on loops", "key-on growth-defined branches", "key-on growth-defined loops",
            "key-off branches", "key-off loops", "key-off growth-defined branches", "key-off growth-defined loops",
            "undefined replicates",
        ]
        assert routing_guard._OUT_PUBLISHED_NOTICE in outcome.err.splitlines()

    def test_the_out_file_banner_states_that_the_key_state_rests_on_rows_any_process_running_as_the_account_can_write(
        self, lab, capsys,
    ):
        out_path = _passed_fixture(lab)

        _compute(capsys, lab, out=str(out_path))

        assert _BANNER_PROVENANCE_SENTENCE in out_path.read_text().splitlines()

    @pytest.mark.parametrize("scenario", ["passed", "failed"])
    def test_a_status_run_prints_the_unauthenticated_rows_statement_on_stderr_and_keeps_stdout_the_bare_status(
        self, lab, capsys, scenario,
    ):
        account = lab.account()
        on_input_tokens = 9_000_000 if scenario == "failed" else 1_000_000
        lab.row(account, "on-1", "on", loops=[_loop(input_tokens=on_input_tokens, growth=100)])
        lab.row(account, "off-1", "off", loops=[_loop(input_tokens=1_000_000, growth=100)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        expected_status = routing_guard._STATUS_FAILED if scenario == "failed" else routing_guard._STATUS_PASSED
        assert outcome.out.splitlines() == [expected_status]
        assert _COMPUTE_PROVENANCE_LINE in outcome.err.splitlines()

    def test_a_not_run_prints_no_unauthenticated_rows_statement(self, lab, capsys):
        account = lab.account()
        lab.row(account, "on-1", "on", loops=[_loop(growth=1)])
        lab.write_note()

        outcome = _compute(capsys, lab)

        assert outcome.code == 3
        assert _COMPUTE_PROVENANCE_LINE not in outcome.err

    def test_the_out_file_is_ascii_so_its_bytes_do_not_depend_on_the_locale(self, lab, capsys):
        out_path = _passed_fixture(lab)

        _compute(capsys, lab, out=str(out_path))

        assert out_path.read_bytes().isascii()

    def test_the_out_file_of_the_enumerable_passed_fixture_equals_a_literal(
        self, lab, capsys, production_bootstrap_replicates,
    ):
        out_path = _passed_fixture(lab)

        _compute(capsys, lab, out=str(out_path))

        # On: $8.00 over 3 loops. Off: $36.00 over 3 loops, so the overhead difference is 8/3 - 12 = -$9.33.
        # Gap: 600k - 300k = 300k tokens. Carry: 300k * (2 + 2)e-6 / 3 = $0.40. Net: -$9.73.
        # The interval is the smallest and largest of the nine resample nets, hand-enumerated as -$10.67 and -$9.00.
        banner_lines = routing_guard._DO_NOT_PUBLISH_BANNER.splitlines()
        assert out_path.read_text().splitlines()[len(banner_lines):] == [
            "net per loop: -$9.73",
            "95% interval: -$10.67 to -$9.00",
            "overhead difference per loop: -$9.33",
            "carry per loop: $0.40",
            "growth gap, key-off minus key-on, tokens per loop: 300,000.0",
            "key-on branches: 2",
            "key-on loops: 3",
            "key-on growth-defined branches: 2",
            "key-on growth-defined loops: 3",
            "key-off branches: 2",
            "key-off loops: 3",
            "key-off growth-defined branches: 2",
            "key-off growth-defined loops: 3",
            "undefined replicates: 0 of 10000",
        ]

    def test_stdout_carries_only_the_status_and_stderr_carries_no_figure(self, lab, capsys):
        out_path = _passed_fixture(lab)

        outcome = _compute(capsys, lab, out=str(out_path))

        assert outcome.out.splitlines() == [routing_guard._STATUS_PASSED]
        assert "$" not in outcome.out + outcome.err

    @pytest.mark.parametrize(
        ("scenario", "expected_code", "stream", "expected_line"),
        [
            ("passed", 0, "out", routing_guard._STATUS_PASSED),
            ("failed", 1, "out", routing_guard._STATUS_FAILED),
            ("not-run", 3, "out", _not_run_line(routing_guard._REASON_ARM_SIZE_MISMATCH, 1)),
            ("refused-config-dir", 2, "err", routing_guard._COMPUTE_CONFIG_DIR_MESSAGE),
            ("degraded", 0, "err", routing_guard._DEGRADED_NOTICES["on"]),
            ("undefined", 0, "err", routing_guard._UNDEFINED_REPLICATE_NOTICES["on"]),
        ],
    )
    def test_no_digit_reaches_either_stream_outside_a_note_line_locator(
        self, lab, capsys, scenario, expected_code, stream, expected_line,
    ):
        if scenario == "failed":
            account = lab.account()
            lab.row(account, "on-1", "on", loops=[_loop(input_tokens=9_000_000, growth=100)])
            lab.row(account, "off-1", "off", loops=[_loop(input_tokens=1_000_000, growth=100)])
            lab.write_note()
        elif scenario == "not-run":
            account = lab.account()
            lab.row(account, "on-1", "on", loops=[_loop(growth=1)])
            lab.write_note()
        elif scenario == "degraded":
            account = lab.account()
            lab.row(account, "on-1", "on", loops=[_loop(input_tokens=3, growth=1, model=_UNPRICED_MODEL)])
            lab.row(account, "off-1", "off", loops=[_loop(growth=2, later={_UNPRICED_MODEL: 2})])
            lab.write_note()
        elif scenario == "undefined":
            account = lab.account()
            for index in range(4):
                lab.row(account, f"on-{index}", "on", loops=[_loop(growth=1 if index == 0 else None)])
                lab.row(account, f"off-{index}", "off", loops=[_loop(growth=2)])
            lab.write_note()
        else:
            _passed_fixture(lab)
        overrides = {"config_dir": str(lab.root / "acct-a")} if scenario == "refused-config-dir" else {}

        outcome = _compute(capsys, lab, **overrides)

        assert outcome.code == expected_code, outcome.err
        assert expected_line in getattr(outcome, stream).splitlines()
        assert _digits_outside_locators(outcome) == []

    def test_one_branch_set_captured_as_one_account_and_as_three_gives_identical_stdout_and_out_files(
        self, tmp_path, capsys, monkeypatch,
    ):
        monkeypatch.setattr(routing_guard, "_MIN_ON_BRANCHES", 1)
        results = []
        for names in (("acct-a",), ("acct-a", "acct-b", "acct-c")):
            lab = _Lab(tmp_path / f"{len(names)}-accounts")
            _six_branch_lab(lab, names)
            out_path = lab.root / "figures.txt"
            outcome = _compute(capsys, lab, out=str(out_path))
            results.append((outcome.out, out_path.read_text()))

        assert results[0] == results[1]

    def test_permuting_the_note_rows_gives_identical_output(self, lab, capsys):
        lines = _six_branch_lab(lab)
        outputs = []
        for order in (list(range(len(lines))), list(reversed(range(len(lines)))), [3, 0, 7, 1, 10, 5, 2, 11, 4, 8, 6, 9]):
            lab.write_note(order=order)
            out_path = lab.root / f"figures-{len(outputs)}.txt"
            outcome = _compute(capsys, lab, out=str(out_path))
            outputs.append((outcome.out, out_path.read_text()))

        assert outputs[0] == outputs[1] == outputs[2]


class TestOutPath:
    def test_an_empty_path_is_refused_with_exit_2_before_any_capture_file_is_opened(self, lab, capsys, monkeypatch):
        _minimal_pair(lab)
        args = _mod.build_parser().parse_args(["routing-guard", "--note", str(lab.note), "--out", ""])
        spies = _install_path_spies(monkeypatch)

        with pytest.raises(SystemExit) as exit_info:
            args.func(args)

        captured = capsys.readouterr()
        assert exit_info.value.code == 2
        assert captured.err.strip() == routing_guard._EMPTY_OUT_MESSAGE
        assert captured.out == ""
        assert not any(char.isdigit() for char in captured.err)
        assert not any(path.endswith(".r1.json") for path in spies.opened_paths())

    def test_an_existing_path_is_refused_with_exit_2_before_any_capture_file_is_opened(self, lab, capsys, monkeypatch):
        _minimal_pair(lab)
        out_path = lab.root / "figures.txt"
        out_path.write_text("standing")
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab, out=str(out_path))

        assert outcome.code == 2
        assert "routing-guard: --out" in outcome.err and "pr-cost-export" not in outcome.err
        assert out_path.read_text() == "standing"
        assert not any(path.endswith(".r1.json") for path in spies.opened_paths())

    def test_a_path_in_a_git_work_tree_is_refused_with_exit_2_before_any_capture_file_is_opened(self, lab, capsys, monkeypatch):
        _minimal_pair(lab)
        repo = lab.root / "repo"
        _init_repo(repo)
        _commit(repo, "init")
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab, out=str(repo / "figures.txt"))

        assert outcome.code == 2
        assert "git working tree" in outcome.err and "pr-cost-export" not in outcome.err
        assert not (repo / "figures.txt").exists()
        assert not any(path.endswith(".r1.json") for path in spies.opened_paths())

    def test_a_live_leaf_symlink_is_refused_with_exit_2_before_any_capture_file_is_opened(self, lab, capsys, monkeypatch):
        _minimal_pair(lab)
        target = lab.root / "target.txt"
        target.write_text("standing")
        link = lab.root / "link.txt"
        link.symlink_to(target)
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab, out=str(link))

        assert outcome.code == 2
        assert target.read_text() == "standing"
        assert not any(path.endswith(".r1.json") for path in spies.opened_paths())

    def test_a_dangling_leaf_symlink_is_refused_at_publish_after_compute_with_no_status_line(self, lab, capsys, monkeypatch):
        _minimal_pair(lab)
        target = lab.root / "never-created.txt"
        link = lab.root / "dangling.txt"
        link.symlink_to(target)
        spies = _install_path_spies(monkeypatch)

        outcome = _compute(capsys, lab, out=str(link))

        assert outcome.code == 2
        assert "routing-guard: --out" in outcome.err and "pr-cost-export" not in outcome.err
        assert not target.exists()
        assert outcome.out == ""
        assert any(path.endswith(".r1.json") for path in spies.opened_paths())

    def test_a_not_run_run_with_out_writes_no_file(self, lab, capsys):
        account = lab.account(key_on=False)
        lab.row(account, "branch-on", "on", loops=[_loop(growth=1)])
        lab.row(account, "branch-off", "off", loops=[_loop(growth=2)])
        lab.write_note()
        out_path = lab.root / "figures.txt"

        outcome = _compute(capsys, lab, out=str(out_path))

        assert outcome.code == 3
        assert not out_path.exists()


class TestUnexpectedFailures:
    def test_an_oserror_the_handler_does_not_expect_exits_4_printing_the_strerror_and_not_the_filename(
        self, lab, capsys, monkeypatch,
    ):
        _minimal_pair(lab)

        def fail(*_args, **_kwargs):
            raise OSError(errno.EACCES, "sentinel-strerror", "/sentinel/path")

        monkeypatch.setattr(routing_guard, "_select_sample", fail)

        outcome = _compute(capsys, lab)

        assert outcome.code == 4
        assert "sentinel-strerror" in outcome.err
        assert "/sentinel/path" not in outcome.err + outcome.out

    def test_an_exception_that_is_not_an_oserror_exits_4_with_the_fixed_message_and_no_traceback(
        self, lab, capsys, monkeypatch,
    ):
        _minimal_pair(lab)

        def fail(*_args, **_kwargs):
            raise RuntimeError("sentinel-message")

        monkeypatch.setattr(routing_guard, "_select_sample", fail)

        outcome = _compute(capsys, lab)

        assert outcome.code == 4
        assert outcome.err.strip() == f"{routing_guard._UNEXPECTED_ERROR_MESSAGE} (RuntimeError in fail)"
        assert "sentinel-message" not in outcome.err + outcome.out
        assert "Traceback" not in outcome.err


class TestGitCheckFailure:
    def test_a_failed_git_call_prints_the_diagnostic_under_the_routing_guard_label_before_the_note_refusal(
        self, lab, capsys, monkeypatch, tmp_path,
    ):
        _minimal_pair(lab)
        _install_git_not_found(monkeypatch)

        outcome = _compute(capsys, lab)

        _assert_git_failure_diagnostic_precedes_note_refusal(outcome, _DIGIT_SCAN_EXEMPT_PREFIXES, tmp_path)


class TestUnlistedDenyRemedyCommand:
    """The command capture's unlisted-branch refusal names parses through the CLI's own parser."""

    _SLUG_PLACEHOLDER = "<main-project-slug>"
    _A_MAIN_CHECKOUT = "/placeholder/checkout"

    @staticmethod
    def _remedy_words() -> list[str]:
        unlisted_deny = routing_guard._DenyFact("unlisted-branch", 1.0, "sess-1")
        reconciliation = routing_guard._reconcile_denies(
            [unlisted_deny], [routing_guard._KeyState("on", None)], [], Path("/placeholder/account"), set(), {}, {},
        )
        (refusal,) = reconciliation.refusals
        return [word.rstrip(",;") for word in refusal.split()]

    @pytest.mark.parametrize("scope_form", ["this-repo", "projects-glob"])
    def test_the_named_review_trace_command_parses_in_each_scope_form(self, tmp_path, scope_form):
        words = self._remedy_words()
        assert {"--config-dir", "review-trace", "--deny-only"} <= set(words)
        if scope_form == "this-repo":
            assert "--this-repo" in words
            scope_args = ["--this-repo"]
        else:
            (projects_word,) = (word for word in words if word.startswith("--projects"))
            assert self._SLUG_PLACEHOLDER in projects_word
            main_project_slug = scope._path_to_project_slug(self._A_MAIN_CHECKOUT)
            scope_args = shlex.split(projects_word.replace(self._SLUG_PLACEHOLDER, main_project_slug))

        args = _mod.build_parser().parse_args(
            ["--config-dir", str(tmp_path), "review-trace", "--deny-only", *scope_args],
        )

        assert args.deny_only is True
        if scope_form == "this-repo":
            assert args.this_repo is True
        else:
            removed_worktree_slug = scope._path_to_project_slug(f"{self._A_MAIN_CHECKOUT}/.claude/worktrees/removed-branch")
            projects_root = tmp_path / "projects"
            removed_worktree_project = projects_root / removed_worktree_slug
            removed_worktree_project.mkdir(parents=True)
            session_file = removed_worktree_project / "sess-removed.jsonl"
            _write_jsonl(session_file, [_user_msg("hello", branch="removed-branch")])
            other_project = projects_root / scope._path_to_project_slug("/placeholder/other-checkout")
            other_project.mkdir()
            _write_jsonl(other_project / "sess-other.jsonl", [_user_msg("hello", branch="other")])

            assert [path for path, _records in corpus.iter_sessions(projects_root, args.projects)] == [session_file]


# --- criterion 3 in compute mode ---------------------------------------------


class TestCriterionThreeInComputeMode:
    @staticmethod
    def _is_allowed(path: str, lab: _Lab, accounts: Sequence[Path]) -> bool:
        if path in (str(lab.note), str(_config._SCHEMA_FILE)):
            return True
        resolved = Path(path)
        for account in accounts:
            if resolved == account / "claude-config.toml" or resolved.name in (".pr-cost-enabled",):
                return True
            if resolved.parent == account / _CAPTURES_DIR and resolved.name.endswith(".r1.json"):
                return True
        return False

    def test_compute_reads_no_transcripts_lists_no_captures_directory_and_ignores_an_unlisted_decoy(
        self, lab, capsys, monkeypatch, tmp_path,
    ):
        account, _on_line, _off_line = _minimal_pair(lab)
        lab.row(account, "excluded-branch", status="excluded", loops=[_loop(growth=9)])
        lab.write_note()
        decoy = lab.account("decoy-account")
        (decoy / "projects" / "decoy-project").mkdir()
        (decoy / "projects" / "decoy-project" / "session.jsonl").write_text("{}")
        (decoy / _CAPTURES_DIR / _digest_name("branch-on")).write_text("not a capture")
        roots_file = tmp_path / "declared-roots"
        roots_file.write_text(f"{decoy}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        assert decoy in _config_dir.declared_transcript_roots()
        spies = _install_path_spies(monkeypatch)
        recorder = _install_subprocess_recorder(monkeypatch)
        # Positive control: the `<=` check below also holds for a recorder that records nothing.
        subprocess.Popen([sys.executable, "-c", "pass"]).wait()
        assert recorder.executables() == {sys.executable}
        recorder.argvs.clear()

        with _environment_unchanged():
            outcome = _compute(capsys, lab)

        recorded = spies.all_paths()
        assert outcome.code in (0, 1)
        assert [path for path in recorded if not self._is_allowed(path, lab, [account])] == []
        projects_dirs = [account / "projects", decoy / "projects"]
        assert not any(
            Path(path) == projects_dir or projects_dir in Path(path).parents
            for path in recorded for projects_dir in projects_dirs
        )
        assert not any(str(decoy) in path for path in recorded)
        assert _digest_name("excluded-branch") not in {Path(path).name for path in spies.opened_paths()}
        assert not any(path.endswith(".session-config-keys-log.jsonl") for path in recorded)
        assert not any(path.endswith(_CAPTURES_DIR) for path in spies.listed_paths())
        assert str(lab.note) in spies.opened_paths()
        assert any(path.endswith(_digest_name("branch-on")) for path in spies.opened_paths())
        assert recorder.executables() <= {"git"}


def test_the_digit_helper_ignores_only_note_line_locators():
    outcome = _Outcome(3, "routing-guard: NOT RUN: a reason (note line 12)\nrouting-guard: 5 unexpected\n", "")

    assert _digits_outside_locators(outcome) == ["routing-guard: 5 unexpected"]


def test_the_digit_helper_exempts_a_repr_quoted_out_echo_line_and_flags_a_fixed_out_line_with_a_digit():
    double_quoted_repr = repr("/placeholder/operator's-figures-1.txt")
    single_quoted_repr = repr("/placeholder/figures-1.txt")
    assert double_quoted_repr[0] != single_quoted_repr[0]
    echo_lines = [
        f"routing-guard: --out {path_repr} already exists -- refusing to overwrite"
        for path_repr in (double_quoted_repr, single_quoted_repr)
    ]
    fixed_line = "routing-guard: --out is empty 2"

    flagged = _digits_outside_locators(_Outcome(2, "", "\n".join([*echo_lines, fixed_line]) + "\n"))

    assert flagged == [fixed_line]


@pytest.mark.parametrize(
    "fixed_out_message", [routing_guard._OUT_WITH_CAPTURE_MESSAGE, routing_guard._EMPTY_OUT_MESSAGE],
)
def test_each_fixed_out_message_is_digit_free(fixed_out_message):
    assert not any(char.isdigit() for char in fixed_out_message)


def test_the_captures_directory_name_is_the_one_the_fixtures_write_into():
    assert routing_guard._CAPTURES_DIR_NAME == _CAPTURES_DIR


def _sample_output_fences(section_lines: list[str]) -> list[str]:
    """The text of each code fence under the section's `### Sample output` subheading, and no other fence."""
    fences: list[str] = []
    in_sample_output = False
    in_fence = False
    fence_lines: list[str] = []
    for line in section_lines:
        if line.startswith("```"):
            if in_fence and in_sample_output:
                fences.append("\n".join(fence_lines))
            in_fence = not in_fence
            fence_lines = []
        elif in_fence:
            fence_lines.append(line)
        elif line.startswith("#"):
            in_sample_output = line == _SAMPLE_OUTPUT_HEADING
    return fences


_SYNTHETIC_DOC = """\
## earlier
```
# not a heading
```
## routing-guard
prose
```bash
# a comment line inside a fence never ends the section
echo 1
```
### Sample output
```text
sample <n>
```
### Later subsection
```text
not a sample 2
```
## next section
```text
outside 3
```
"""


class TestRoutingGuardDocSection:
    def test_the_section_scanner_is_fence_aware_and_ends_at_the_next_level_two_heading(self):
        section = _routing_guard_section_lines(_SYNTHETIC_DOC)

        assert "echo 1" in section
        assert "not a sample 2" in section
        assert "outside 3" not in section
        assert "# not a heading" not in section

    def test_the_sample_scanner_returns_only_the_fences_under_the_sample_subheading(self):
        fences = _sample_output_fences(_routing_guard_section_lines(_SYNTHETIC_DOC))

        assert fences == ["sample <n>"]

    def test_the_sample_output_fences_hold_no_digit(self):
        fences = _sample_output_fences(_routing_guard_section_lines(_TRANSCRIPT_ANALYSIS_DOC.read_text()))

        assert fences, "the routing-guard section has no fence under its Sample output subheading"
        assert [fence for fence in fences if re.search(r"\d", fence)] == []

    def test_the_out_sample_fence_opens_with_the_banner_placeholder_and_the_doc_names_the_constant_it_stands_for(self):
        placeholder = "<DO NOT PUBLISH banner>"
        section = _routing_guard_section_lines(_TRANSCRIPT_ANALYSIS_DOC.read_text())
        out_fences = [fence for fence in _sample_output_fences(section) if "<figure label>" in fence]

        assert len(out_fences) == 1
        assert out_fences[0].splitlines()[0] == placeholder
        naming_lines = [
            line for line in section if f"`{placeholder}`" in line and "`routing_guard._DO_NOT_PUBLISH_BANNER`" in line
        ]
        assert naming_lines, "no line of the section says which constant the banner placeholder stands for"
        assert hasattr(routing_guard, "_DO_NOT_PUBLISH_BANNER")

    def test_the_heading_the_messages_cite_is_a_subheading_of_the_section_and_each_message_carries_the_pointer(self):
        account = Path("/placeholder/account")
        unlisted_branch_refusal, = routing_guard._reconcile_denies(
            [routing_guard._DenyFact("unlisted-branch", 1.0, "sess-1")], [routing_guard._KeyState("on", None)], [], account,
            set(), {}, {},
        ).refusals
        pointer_opening = 'routing-guard "'
        cited_from = unlisted_branch_refusal[unlisted_branch_refusal.index(pointer_opening) + len(pointer_opening):]
        cited_heading = cited_from.split('"')[0]
        section = _routing_guard_section_lines(_TRANSCRIPT_ANALYSIS_DOC.read_text())

        assert f"### {cited_heading}" in section
        for message in (unlisted_branch_refusal, routing_guard._DROPPED_DENY_NOTICE):
            assert f'routing-guard "{cited_heading}"' in message

    def test_each_routing_guard_name_the_section_cites_exists_on_the_module(self):
        section_text = "\n".join(_routing_guard_section_lines(_TRANSCRIPT_ANALYSIS_DOC.read_text()))

        cited_names = set(_CITED_MODULE_NAME.findall(section_text))

        assert {"_NOT_RUN_REASONS", "_DO_NOT_PUBLISH_BANNER"} <= cited_names
        assert [name for name in cited_names if not hasattr(routing_guard, name)] == []
