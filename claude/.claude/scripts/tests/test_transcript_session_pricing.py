"""Tests for transcript_analysis/session_pricing.py: per-session, per-UTC-day pricing,
its error and stderr contracts, and the pins that hold its stability promise for
importers outside this repository."""
from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import os
import pickle
import subprocess
import sys
import time
import traceback
import typing
from collections.abc import Mapping
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path

import pytest
from helpers import REPO_ROOT, SCRIPTS_DIR
from transcript_analysis import corpus, pricing, session_pricing

from .conftest import _agent_use, _asst, _priced, _user_msg, _write_jsonl, _write_subagent_jsonl

_PRICED_MODEL = "claude-sentinel-priced-1"
_PRICED_MODEL_BASE_RATE = 2.0
_UNPRICED_MODEL = "claude-sentinel-model-9"
_FAR_REVERIFY_BY = date(2099, 12, 31)

_PROJECT_SLUG = "sentinel-project-slug"
_FIRST_SESSION_STEM = "sentinel-session-0001"

_START = date(2026, 5, 1)
_END = date(2026, 5, 31)
_AS_OF = date(2026, 6, 1)

# One million input tokens at the synthetic model's $2.00/MTok base rate.
_ONE_MILLION_INPUT_TURN_MICRO_DOLLARS = 2_000_000

_ARCHITECTURE_DOC = REPO_ROOT / "docs" / "transcript-analysis-architecture.md"
_SESSION_PRICING_HEADING = "### `session_pricing.py`"

_PROMISE_HINT = (
    "this breaks session_pricing's stability promise; if deliberate, record the break in the "
    "session_pricing.py section of docs/transcript-analysis-architecture.md and update this pin"
)


@pytest.fixture(autouse=True)
def _fresh_non_contiguous_notice_set(monkeypatch):
    """The suite-wide autouse fixture resets the two drift flags but not this set,
    and a NOTICE already logged by an earlier test would silence the one a test here asserts on."""
    monkeypatch.setattr(pricing, "_non_contiguous_merge_notices_logged", set())


@pytest.fixture(autouse=True)
def _synthetic_priced_model(monkeypatch):
    """A priced model whose rate and expiry live in this file, so oracle values do not move
    when the real rate table is refreshed."""
    monkeypatch.setitem(pricing._MODEL_BASE_INPUT_RATES, _PRICED_MODEL, _PRICED_MODEL_BASE_RATE)
    monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, _PRICED_MODEL, _FAR_REVERIFY_BY)


@pytest.fixture(params=["UTC0", "<-08>8", "<+14>-14"], ids=["utc", "utc-minus-8", "utc-plus-14"])
def host_time_zone(request):
    """Run the test with the process time zone set to a fixed POSIX offset, then restore it."""
    expected_utc_offset_seconds = {"UTC0": 0, "<-08>8": -8 * 3600, "<+14>-14": 14 * 3600}[request.param]
    original = os.environ.get("TZ")
    os.environ["TZ"] = request.param
    time.tzset()
    try:
        # A platform that ignores the POSIX TZ string would make the host-independence tests vacuous.
        assert time.localtime(0).tm_gmtoff == expected_utc_offset_seconds
        yield request.param
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def _write_session(root: Path, index: int, records: list[dict]) -> Path:
    project = root / _PROJECT_SLUG
    project.mkdir(exist_ok=True)
    path = project / f"sentinel-session-{index + 1:04d}.jsonl"
    _write_jsonl(path, records)
    return path


def _price(
    session_paths,
    *,
    start: date = _START,
    end: date = _END,
    as_of: date = _AS_OF,
    include_unpriced_model_ids: bool = True,
) -> session_pricing.SessionPricingResult:
    return session_pricing.price_sessions_by_utc_day(
        session_paths, start=start, end=end, as_of=as_of, include_unpriced_model_ids=include_unpriced_model_ids,
    )


def _turn(ts: str, **usage_tokens: int) -> dict:
    """A priced turn on the synthetic model; one million input tokens unless overridden."""
    usage_tokens = usage_tokens or {"input": 1_000_000}
    return _priced(_PRICED_MODEL, ts=ts, **usage_tokens)


def _add_priced_model(monkeypatch, model: str, reverify_by: date) -> None:
    monkeypatch.setitem(pricing._MODEL_BASE_INPUT_RATES, model, _PRICED_MODEL_BASE_RATE)
    monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, model, reverify_by)


def _with_usage(record: dict, usage) -> dict:
    record["message"]["usage"] = usage
    return record


_RECORD_FIELD_SENTINELS = {
    "cwd": "SENTINEL_CWD_VALUE",
    "sessionId": "SENTINEL_SESSION_ID_VALUE",
    "uuid": "SENTINEL_UUID_VALUE",
    "gitBranch": "SENTINEL_GIT_BRANCH_VALUE",
}


def _with_field_sentinels(record):
    """The record with sentinel values in the identifying fields real transcripts carry. A non-object line is returned as is."""
    return {**record, **_RECORD_FIELD_SENTINELS} if isinstance(record, dict) else record


def _usage_warning_count(stderr: str) -> int:
    return sum(1 for line in stderr.splitlines() if line.startswith("WARNING: requestId "))


def _subagent_warning_count(stderr: str) -> int:
    return sum(1 for line in stderr.splitlines() if line.startswith("WARNING: subagent spawns detected"))


def _contiguous_drifting_records(request_id: str, ts: str) -> list[dict]:
    """Two contiguous records of one requestId whose input_tokens disagree."""
    return [
        _priced(_PRICED_MODEL, input=10, request_id=request_id, ts=ts),
        _priced(_PRICED_MODEL, input=11, request_id=request_id, ts=ts),
    ]


def _spawn_turn(ts: str = "2026-05-19T10:00:00.000Z") -> dict:
    return _priced(_PRICED_MODEL, input=1, ts=ts, content=[_agent_use("toolu-spawn", "staff-backend-engineer")])


def _sidechain_turn(ts: str = "2026-05-19T10:00:00.000Z") -> dict:
    record = _priced(_PRICED_MODEL, input=1, ts=ts)
    record["isSidechain"] = True
    return record


class TestWindowAndDays:
    pytestmark = pytest.mark.usefixtures("host_time_zone")

    def test_window_counts_both_edge_days_and_excludes_the_milliseconds_outside(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [
                _turn("2026-05-10T00:00:00.000Z"),
                _turn("2026-05-12T23:59:59.999Z"),
                _turn("2026-05-13T00:00:00.000Z"),
                _turn("2026-05-09T23:59:59.999Z"),
            ],
        )

        result = _price([session], start=date(2026, 5, 10), end=date(2026, 5, 12))

        assert result.sessions[0].micro_dollars_by_day == {
            date(2026, 5, 10): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS,
            date(2026, 5, 12): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS,
        }

    def test_single_day_window_counts_only_that_day(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [_turn("2026-05-09T23:59:59.999Z"), _turn("2026-05-10T12:00:00.000Z"), _turn("2026-05-11T00:00:00.000Z")],
        )

        result = _price([session], start=date(2026, 5, 10), end=date(2026, 5, 10))

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 10): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}

    def test_turns_a_millisecond_apart_across_midnight_land_on_two_days(self, tmp_path):
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T23:59:59.999Z"), _turn("2026-05-20T00:00:00.000Z")])

        result = _price([session])

        assert list(result.sessions[0].micro_dollars_by_day) == [date(2026, 5, 19), date(2026, 5, 20)]

    def test_offset_timestamps_are_converted_to_the_utc_day(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [_turn("2026-05-19T20:00:00.000-08:00"), _turn("2026-05-20T09:00:00.000+14:00", input=3_000_000)],
        )

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {
            date(2026, 5, 20): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS,
            date(2026, 5, 19): 3 * _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS,
        }

    def test_offsetless_timestamp_is_undated_not_read_in_host_time(self, tmp_path):
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000")])

        result = _price([session])

        assert result.sessions[0].undated_turn_count == 1
        assert result.sessions[0].micro_dollars_by_day == {}
        assert result.total_micro_dollars == 0

    def test_contiguous_run_is_priced_on_its_first_records_day_with_its_last_records_usage(self, tmp_path):
        first = _priced(_PRICED_MODEL, output=10, request_id="req-run", ts="2026-05-19T23:59:59.999Z")
        last = _priced(_PRICED_MODEL, output=1_000_000, request_id="req-run", ts="2026-05-20T00:00:01.000Z")
        session = _write_session(tmp_path, 0, [first, last])

        result = _price([session])

        # Output is 5x the $2.00/MTok base rate, so one million output tokens cost $10.
        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 10_000_000}

    def test_non_contiguous_run_is_priced_on_its_first_records_day(self, tmp_path):
        first = _priced(_PRICED_MODEL, output=100_000, request_id="req-run", ts="2026-05-19T23:59:59.999Z")
        last = _priced(_PRICED_MODEL, output=100_000, request_id="req-run", ts="2026-05-20T00:00:01.000Z")
        session = _write_session(tmp_path, 0, [first, _user_msg("interleaved"), last])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 1_000_000}

    def test_session_with_only_out_of_window_turns_appears_with_empty_maps_in_input_order(self, tmp_path):
        in_window = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        out_of_window = _write_session(tmp_path, 1, [_turn("2026-07-19T10:00:00.000Z")])

        result = _price([out_of_window, in_window])

        assert len(result.sessions) == 2
        assert result.sessions[0] == session_pricing.SessionSpend(
            main_file_readable=True, micro_dollars_by_day={}, unpriced_tokens_by_day={}, undated_turn_count=0
        )
        assert result.sessions[1].micro_dollars_by_day == {date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}

    def test_day_map_keys_ascend_whatever_the_file_order(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [
                _turn("2026-05-21T10:00:00.000Z"),
                _turn("2026-05-19T10:00:00.000Z"),
                _priced(_UNPRICED_MODEL, input=5, ts="2026-05-22T10:00:00.000Z"),
                _priced(_UNPRICED_MODEL, input=5, ts="2026-05-18T10:00:00.000Z"),
            ],
        )

        result = _price([session])

        assert list(result.sessions[0].micro_dollars_by_day) == [date(2026, 5, 19), date(2026, 5, 21)]
        assert list(result.sessions[0].unpriced_tokens_by_day) == [date(2026, 5, 18), date(2026, 5, 22)]

    def test_iso_datetime_parse_distinguishes_aware_naive_and_garbage(self):
        zulu = corpus._parse_iso_datetime("2026-05-19T10:00:00.000Z")
        offset = corpus._parse_iso_datetime("2026-05-19T10:00:00.000+05:30")
        naive = corpus._parse_iso_datetime("2026-05-19T10:00:00.000")

        assert zulu is not None and zulu.utcoffset() is not None
        assert offset is not None and offset.utcoffset() is not None
        assert naive is not None and naive.utcoffset() is None
        assert corpus._parse_iso_datetime("") is None
        assert corpus._parse_iso_datetime(None) is None
        assert corpus._parse_iso_datetime("garbage") is None


class TestMicroDollars:
    def test_half_up_rounding_helper_rounds_a_tie_up_and_a_near_tie_down(self):
        assert session_pricing._micro_dollars_half_up(Fraction(5, 2_000_000)) == 3
        assert session_pricing._micro_dollars_half_up(Fraction(2_499_999, 10**12)) == 2
        assert session_pricing._micro_dollars_half_up(Fraction(3, 10**6)) == 3

    def test_exact_dollars_reads_the_decimal_repr_not_the_binary_float(self):
        assert session_pricing._exact_dollars(0.1) == Fraction(1, 10)
        assert session_pricing._exact_dollars(3e-07) == Fraction(3, 10_000_000)

    def test_class_amounts_that_float_summing_would_leave_below_a_tie_round_up(self, tmp_path, monkeypatch):
        assert 3e-07 + 4.2e-06 < 4.5e-06
        monkeypatch.setattr(pricing, "_price_turn", lambda model, usage: ({"input": 3e-07, "output": 4.2e-06}, 0, 0))
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 5}

    def test_amount_just_under_a_tie_rounds_down(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pricing, "_price_turn", lambda model, usage: ({"input": 2.499999e-06}, 0, 0))
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 2}

    def test_run_total_sums_each_session_days_rounded_value(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pricing, "_price_turn", lambda model, usage: ({"input": 5e-07}, 0, 0))
        sessions = [_write_session(tmp_path, index, [_turn("2026-05-19T10:00:00.000Z")]) for index in range(2)]

        result = _price(sessions)

        assert [spend.micro_dollars_by_day for spend in result.sessions] == [{date(2026, 5, 19): 1}] * 2
        assert result.total_micro_dollars == 2

    def test_same_day_turns_are_summed_exactly_before_rounding_not_rounded_per_turn(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pricing, "_price_turn", lambda model, usage: ({"input": 4e-07}, 0, 0))
        session = _write_session(
            tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z"), _turn("2026-05-19T11:00:00.000Z")]
        )

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 1}
        assert result.total_micro_dollars == 1

    def test_day_that_rounds_to_zero_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pricing, "_price_turn", lambda model, usage: ({"input": 4e-07}, 0, 0))
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {}
        assert result.total_micro_dollars == 0

    @pytest.mark.parametrize(
        ("speed", "inference_geo", "expected_micro_dollars"),
        [(None, None, 6_200_000), ("fast", None, 12_400_000), (None, "us", 6_820_000), ("fast", "us", 13_640_000)],
        ids=["standard", "fast", "us-geo", "fast-us-geo"],
    )
    def test_hand_derived_turn_cost_on_the_synthetic_model(self, tmp_path, speed, inference_geo, expected_micro_dollars):
        # $2 input + $1 output + $0.20 cache read + $2 1h write + $1 5m write at a $2.00/MTok base rate.
        turn = _priced(
            _PRICED_MODEL,
            input=1_000_000,
            output=100_000,
            cache_read=1_000_000,
            ephemeral_1h=500_000,
            ephemeral_5m=400_000,
            speed=speed,
            inference_geo=inference_geo,
            ts="2026-05-19T10:00:00.000Z",
        )
        session = _write_session(tmp_path, 0, [turn])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): expected_micro_dollars}

    def test_end_to_end_half_micro_tie_rounds_up_where_float_rounding_would_round_down(self, tmp_path):
        turn = _priced(_PRICED_MODEL, ephemeral_5m=5, ts="2026-05-19T10:00:00.000Z")
        dollars_by_class, _, _ = pricing._price_turn(_PRICED_MODEL, turn["message"]["usage"])
        assert repr(dollars_by_class["cache_write_5m"]) == "1.25e-05"
        assert round(dollars_by_class["cache_write_5m"] * 1_000_000) == 12
        session = _write_session(tmp_path, 0, [turn])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 13}

    def test_totals_equal_the_sums_over_two_sessions_and_two_days(self, tmp_path):
        first = _write_session(
            tmp_path,
            0,
            [
                _turn("2026-05-19T10:00:00.000Z"),
                _turn("2026-05-20T10:00:00.000Z", input=2_000_000),
                _priced(_UNPRICED_MODEL, input=100, output=50, ts="2026-05-19T10:00:00.000Z"),
            ],
        )
        second = _write_session(
            tmp_path,
            1,
            [
                _turn("2026-05-19T11:00:00.000Z", input=3_000_000),
                _priced(_UNPRICED_MODEL, input=7, ts="2026-05-20T11:00:00.000Z"),
            ],
        )

        result = _price([first, second])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 2_000_000, date(2026, 5, 20): 4_000_000}
        assert result.sessions[1].micro_dollars_by_day == {date(2026, 5, 19): 6_000_000}
        assert result.total_micro_dollars == 12_000_000
        assert result.total_unpriced_tokens == 157


class TestUnpriced:
    def test_unpriced_tokens_land_on_their_turns_day(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [
                _priced(_UNPRICED_MODEL, input=100, output=50, ts="2026-05-19T10:00:00.000Z"),
                _priced(_UNPRICED_MODEL, cache_read=7, ts="2026-05-20T10:00:00.000Z"),
                _turn("2026-05-19T10:00:00.000Z"),
            ],
        )

        result = _price([session])

        assert result.sessions[0].unpriced_tokens_by_day == {date(2026, 5, 19): 150, date(2026, 5, 20): 7}
        assert result.total_unpriced_tokens == 157

    def test_absent_null_integer_and_list_models_collapse_to_one_empty_model_id(self, tmp_path):
        absent = _priced(_UNPRICED_MODEL, input=1)
        del absent["message"]["model"]
        null = _priced(_UNPRICED_MODEL, input=1)
        null["message"]["model"] = None
        integer = _priced(_UNPRICED_MODEL, input=1)
        integer["message"]["model"] = 7
        listed = _priced(_UNPRICED_MODEL, input=1)
        listed["message"]["model"] = ["claude-sentinel-model-9"]
        session = _write_session(tmp_path, 0, [absent, null, integer, listed])

        result = _price([session], include_unpriced_model_ids=True)

        assert result.unpriced_models == ("",)
        assert result.unpriced_model_count == 1
        assert result.total_unpriced_tokens == 4

    def test_synthetic_model_at_zero_tokens_is_excluded_but_another_unpriced_model_at_zero_is_included(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [
                _priced(pricing._SYNTHETIC_MODEL_ID, ts="2026-05-19T10:00:00.000Z"),
                _priced(_UNPRICED_MODEL, ts="2026-05-19T10:00:00.000Z"),
            ],
        )

        result = _price([session], include_unpriced_model_ids=True)

        assert result.unpriced_models == (_UNPRICED_MODEL,)
        assert result.unpriced_model_count == 1
        assert result.total_unpriced_tokens == 0
        assert result.sessions[0].unpriced_tokens_by_day == {}

    def test_synthetic_model_with_tokens_is_listed(self, tmp_path):
        session = _write_session(tmp_path, 0, [_priced(pricing._SYNTHETIC_MODEL_ID, input=3, ts="2026-05-19T10:00:00.000Z")])

        result = _price([session], include_unpriced_model_ids=True)

        assert result.unpriced_models == (pricing._SYNTHETIC_MODEL_ID,)
        assert result.unpriced_model_count == 1

    def test_models_are_sorted_and_one_seen_only_outside_the_window_is_excluded(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [
                _priced("claude-sentinel-model-9", input=1, ts="2026-05-19T10:00:00.000Z"),
                _priced("claude-sentinel-model-1", input=1, ts="2026-05-19T10:00:00.000Z"),
                _priced("claude-sentinel-model-5", input=1, ts="2026-07-19T10:00:00.000Z"),
            ],
        )

        result = _price([session], include_unpriced_model_ids=True)

        assert result.unpriced_models == ("claude-sentinel-model-1", "claude-sentinel-model-9")
        assert result.unpriced_model_count == 2

    @pytest.mark.parametrize("first_session_first", [True, False], ids=["a-then-b", "b-then-a"])
    def test_unpriced_models_union_sessions_and_list_synthetic_when_any_session_has_tokens_on_it(
        self, tmp_path, first_session_first
    ):
        session_a = _write_session(
            tmp_path,
            0,
            [
                _priced(pricing._SYNTHETIC_MODEL_ID, ts="2026-05-19T10:00:00.000Z"),
                _priced("claude-sentinel-model-1", input=1, ts="2026-05-19T10:00:00.000Z"),
            ],
        )
        session_b = _write_session(
            tmp_path,
            1,
            [
                _priced(pricing._SYNTHETIC_MODEL_ID, input=3, ts="2026-05-19T10:00:00.000Z"),
                _priced("claude-sentinel-model-2", input=1, ts="2026-05-19T10:00:00.000Z"),
            ],
        )

        sessions = [session_a, session_b] if first_session_first else [session_b, session_a]
        result = _price(sessions, include_unpriced_model_ids=True)

        assert result.unpriced_models == (pricing._SYNTHETIC_MODEL_ID, "claude-sentinel-model-1", "claude-sentinel-model-2")
        assert result.unpriced_model_count == 3

    def test_unpriced_models_is_populated_by_default_and_the_count_matches(
        self, tmp_path
    ):
        assert inspect.signature(
            session_pricing.price_sessions_by_utc_day
        ).parameters["include_unpriced_model_ids"].default is True

        session = _write_session(
            tmp_path,
            0,
            [
                _priced("claude-sentinel-model-1", input=1, ts="2026-05-19T10:00:00.000Z"),
                _priced("claude-sentinel-model-2", input=1, ts="2026-05-19T10:00:00.000Z"),
            ],
        )

        result = _price([session])

        assert result.unpriced_models == ("claude-sentinel-model-1", "claude-sentinel-model-2")
        assert result.unpriced_model_count == 2


class TestReadability:
    def test_missing_path_and_directory_path_are_unreadable_not_errors(self, tmp_path):
        directory = tmp_path / _PROJECT_SLUG / "a-directory.jsonl"
        directory.mkdir(parents=True)
        missing = tmp_path / _PROJECT_SLUG / "missing.jsonl"

        result = _price([missing, directory])

        assert [spend.main_file_readable for spend in result.sessions] == [False, False]
        assert result.total_micro_dollars == 0

    def test_missing_main_file_with_a_priced_subagent_file_is_unreadable_with_zero_spend(self, tmp_path):
        project = tmp_path / _PROJECT_SLUG
        _write_subagent_jsonl(project, _FIRST_SESSION_STEM, "agent-1", [_sidechain_turn()])

        result = _price([project / f"{_FIRST_SESSION_STEM}.jsonl"])

        assert result.sessions[0].main_file_readable is False
        assert result.sessions[0].micro_dollars_by_day == {}
        assert result.total_micro_dollars == 0

    def test_empty_main_file_is_readable(self, tmp_path):
        session = _write_session(tmp_path, 0, [])
        session.write_text("")

        result = _price([session])

        assert result.sessions[0].main_file_readable is True
        assert result.sessions[0].micro_dollars_by_day == {}

    def test_empty_main_file_still_counts_a_priced_subagent_file(self, tmp_path):
        session = _write_session(tmp_path, 0, [])
        session.write_text("")
        sidechain = _sidechain_turn()
        _write_subagent_jsonl(session.parent, session.stem, "agent-1", [sidechain])

        result = _price([session])

        assert result.sessions[0].main_file_readable is True
        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): 2}

    def test_main_file_of_only_undecodable_and_invalid_json_lines_is_readable_with_empty_maps(self, tmp_path):
        session = _write_session(tmp_path, 0, [])
        session.write_bytes(b"\xff\xfe not utf-8\n{not json\n")

        result = _price([session])

        assert result.sessions[0].main_file_readable is True
        assert result.sessions[0].micro_dollars_by_day == {}
        assert result.sessions[0].unpriced_tokens_by_day == {}

    def test_invalid_json_line_beside_a_priced_turn_is_skipped(self, tmp_path):
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        with session.open("a") as handle:
            handle.write("{not json\n")

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}

    def test_unreadable_subagent_path_is_skipped_and_a_later_readable_subagent_file_still_counts(self, tmp_path):
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        unreadable_subagent = session.parent / session.stem / corpus.SUBAGENT_SUBDIR / "agent-x.jsonl"
        unreadable_subagent.mkdir(parents=True)
        # "agent-y" sorts after "agent-x", so a scan that stopped at the first unreadable entry would miss it.
        _write_subagent_jsonl(session.parent, session.stem, "agent-y", [_sidechain_turn()])

        result = _price([session])

        assert result.sessions[0].main_file_readable is True
        assert result.sessions[0].micro_dollars_by_day == {
            date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS + 2
        }

    def test_request_id_in_the_main_file_and_a_subagent_file_is_priced_once(self, tmp_path):
        main_turn = _priced(_PRICED_MODEL, input=1_000_000, request_id="req-shared-across-files")
        subagent_turn = copy.deepcopy(main_turn)
        subagent_turn["isSidechain"] = True
        session = _write_session(tmp_path, 0, [main_turn])
        _write_subagent_jsonl(session.parent, session.stem, "agent-1", [subagent_turn])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}

    def test_contiguous_run_of_one_request_id_inside_a_subagent_file_is_priced_once(self, tmp_path):
        session = _write_session(tmp_path, 0, [])
        subagent_run = [
            _priced(_PRICED_MODEL, input=1_000_000, request_id="req-subagent-run", content=[{"type": "text", "text": text}])
            for text in ("first block", "second block")
        ]
        for record in subagent_run:
            record["isSidechain"] = True
        _write_subagent_jsonl(session.parent, session.stem, "agent-1", subagent_run)

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}


class TestUndatedTurns:
    def test_priced_turn_without_a_timestamp_counts_once_and_enters_no_day(self, tmp_path):
        no_timestamp = _turn("2026-05-19T10:00:00.000Z")
        del no_timestamp["timestamp"]
        session = _write_session(tmp_path, 0, [no_timestamp])

        result = _price([session])

        assert result.sessions[0].undated_turn_count == 1
        assert result.sessions[0].micro_dollars_by_day == {}

    def test_priced_turn_with_an_unparseable_timestamp_counts_once(self, tmp_path):
        session = _write_session(tmp_path, 0, [_turn("not-a-timestamp")])

        result = _price([session])

        assert result.sessions[0].undated_turn_count == 1

    def test_zero_token_undated_turn_adds_nothing_and_unpriced_undated_tokens_add_one(self, tmp_path):
        zero_priced = _priced(_PRICED_MODEL, ts="not-a-timestamp")
        zero_unpriced = _priced(_UNPRICED_MODEL, ts="not-a-timestamp")
        unpriced_with_tokens = _priced(_UNPRICED_MODEL, input=5, ts="not-a-timestamp")
        session = _write_session(tmp_path, 0, [zero_priced, zero_unpriced, unpriced_with_tokens])

        result = _price([session])

        assert result.sessions[0].undated_turn_count == 1
        assert result.sessions[0].unpriced_tokens_by_day == {}

    def test_undated_turn_models_stay_out_of_unpriced_models_and_the_priced_set(self, tmp_path):
        session = _write_session(
            tmp_path, 0, [_priced(_UNPRICED_MODEL, input=5, ts="not-a-timestamp"), _turn("not-a-timestamp")]
        )

        result = _price([session], as_of=date(2100, 1, 1))

        assert result.unpriced_models == ()
        assert result.pricing_reverify_by is None
        assert result.stale_models == ()
        assert result.sessions[0].undated_turn_count == 2

    @pytest.mark.parametrize(
        "timestamp",
        ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"],
        ids=["before-year-1-in-utc", "after-year-9999-in-utc"],
    )
    def test_timestamp_outside_the_representable_utc_range_is_undated_when_it_has_spend(self, tmp_path, timestamp):
        session = _write_session(
            tmp_path, 0, [_turn(timestamp), _priced(_UNPRICED_MODEL, input=5, ts=timestamp)]
        )

        result = _price([session], as_of=date(2100, 1, 1))

        assert result.sessions[0].undated_turn_count == 2
        assert result.sessions[0].micro_dollars_by_day == {}
        assert result.sessions[0].unpriced_tokens_by_day == {}
        assert result.unpriced_models == ()
        assert result.stale_models == ()
        assert result.pricing_reverify_by is None

    @pytest.mark.parametrize(
        "timestamp",
        ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"],
        ids=["before-year-1-in-utc", "after-year-9999-in-utc"],
    )
    def test_zero_token_turn_at_the_utc_range_edge_neither_raises_nor_counts(self, tmp_path, timestamp):
        session = _write_session(
            tmp_path, 0, [_priced(_PRICED_MODEL, ts=timestamp), _priced(_UNPRICED_MODEL, ts=timestamp)]
        )

        result = _price([session])

        assert result.sessions[0].undated_turn_count == 0

    @pytest.mark.parametrize("timestamp", [0, False, []], ids=["zero", "false", "empty-list"])
    def test_falsy_non_string_timestamp_on_a_priced_turn_is_undated_not_an_error(self, tmp_path, timestamp):
        falsy_timestamp = _turn("2026-05-19T10:00:00.000Z")
        falsy_timestamp["timestamp"] = timestamp  # _asst writes a timestamp only when it is truthy.
        session = _write_session(tmp_path, 0, [falsy_timestamp])

        result = _price([session])

        assert result.sessions[0].undated_turn_count == 1
        assert result.sessions[0].micro_dollars_by_day == {}

    @pytest.mark.parametrize("timestamp", [5, ["x"]], ids=["int", "list"])
    def test_truthy_non_string_timestamp_on_a_user_record_does_not_fail_the_session(self, tmp_path, timestamp):
        user_record = _user_msg("hello")
        user_record["timestamp"] = timestamp  # _user_msg's ts parameter is typed str.
        session = _write_session(tmp_path, 0, [user_record, _turn("2026-05-19T10:00:00.000Z")])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}
        assert result.sessions[0].undated_turn_count == 0

    @pytest.mark.parametrize("timestamp", [5, ["x"]], ids=["int", "list"])
    @pytest.mark.parametrize("usage", [{}, "absent"], ids=["empty-dict", "absent"])
    def test_truthy_non_string_timestamp_on_an_assistant_record_with_falsy_usage_does_not_fail_the_session(
        self, tmp_path, timestamp, usage
    ):
        no_usage_record = _asst(_PRICED_MODEL, ts="2026-05-19T10:00:00.000Z")
        if usage == "absent":
            del no_usage_record["message"]["usage"]
        else:
            no_usage_record["message"]["usage"] = usage
        no_usage_record["timestamp"] = timestamp
        session = _write_session(tmp_path, 0, [no_usage_record, _turn("2026-05-19T10:00:00.000Z")])

        result = _price([session])

        assert result.sessions[0].micro_dollars_by_day == {date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}
        assert result.sessions[0].undated_turn_count == 0

    @pytest.mark.parametrize("message", [None, "absent"], ids=["null", "absent"])
    def test_assistant_record_with_a_null_or_absent_message_is_skipped(self, tmp_path, message):
        record = {"type": "assistant", "timestamp": "2026-05-19T10:00:00.000Z"}
        if message != "absent":
            record["message"] = message
        session = _write_session(tmp_path, 0, [record])

        result = _price([session])

        assert result.sessions[0] == session_pricing.SessionSpend(
            main_file_readable=True, micro_dollars_by_day={}, unpriced_tokens_by_day={}, undated_turn_count=0
        )

    @pytest.mark.parametrize("record_type", ["user", "system"])
    @pytest.mark.parametrize("message_shape", ["priced-usage", "string"])
    def test_non_assistant_record_is_skipped_whatever_its_message_carries(self, tmp_path, record_type, message_shape):
        record = _turn("2026-05-19T10:00:00.000Z")
        record["type"] = record_type
        if message_shape == "string":
            record["message"] = "not an object"  # Reading .get on it would raise were the record not skipped.
        session = _write_session(tmp_path, 0, [record])

        result = _price([session])

        assert result.sessions[0] == session_pricing.SessionSpend(
            main_file_readable=True, micro_dollars_by_day={}, unpriced_tokens_by_day={}, undated_turn_count=0
        )
        assert result.total_micro_dollars == 0

    @pytest.mark.parametrize("usage", [{}, None, "absent"], ids=["empty-dict", "null", "absent"])
    def test_falsy_usage_is_skipped_without_validation_and_adds_nothing(self, tmp_path, usage):
        record = _asst(_PRICED_MODEL, ts="not-a-timestamp")
        if usage == "absent":
            del record["message"]["usage"]
        else:
            record["message"]["usage"] = usage
        session = _write_session(tmp_path, 0, [record])

        result = _price([session])

        assert result.sessions[0] == session_pricing.SessionSpend(
            main_file_readable=True, micro_dollars_by_day={}, unpriced_tokens_by_day={}, undated_turn_count=0
        )


class TestStaleness:
    def test_reverify_by_is_the_earliest_date_among_models_priced_in_the_window(self, tmp_path, monkeypatch):
        early, late, outside_only = date(2026, 12, 4), date(2027, 1, 1), date(2026, 6, 1)
        monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, _PRICED_MODEL, early)
        _add_priced_model(monkeypatch, "claude-sentinel-priced-2", late)
        _add_priced_model(monkeypatch, "claude-sentinel-priced-3", outside_only)
        session = _write_session(
            tmp_path,
            0,
            [
                _turn("2026-05-19T10:00:00.000Z"),
                _priced("claude-sentinel-priced-2", input=1, ts="2026-05-19T10:00:00.000Z"),
                _priced("claude-sentinel-priced-3", input=1, ts="2026-07-19T10:00:00.000Z"),
            ],
        )

        result = _price([session])

        assert result.pricing_reverify_by == early

    @pytest.mark.parametrize("first_session_first", [True, False], ids=["a-then-b", "b-then-a"])
    def test_reverify_by_is_the_minimum_and_stale_models_the_sorted_union_across_sessions(
        self, tmp_path, monkeypatch, first_session_first
    ):
        earlier, later = date(2026, 12, 4), date(2026, 12, 20)
        monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, _PRICED_MODEL, earlier)
        _add_priced_model(monkeypatch, "claude-sentinel-priced-2", later)
        session_a = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        session_b = _write_session(
            tmp_path, 1, [_priced("claude-sentinel-priced-2", input=1, ts="2026-05-19T10:00:00.000Z")]
        )
        ordered = [session_a, session_b] if first_session_first else [session_b, session_a]

        between = _price(ordered, as_of=date(2026, 12, 10))
        after_both = _price(ordered, as_of=date(2026, 12, 31))

        assert between.pricing_reverify_by == earlier
        assert between.stale_models == (_PRICED_MODEL,)
        assert after_both.pricing_reverify_by == earlier
        assert after_both.stale_models == (_PRICED_MODEL, "claude-sentinel-priced-2")

    def test_every_rated_model_has_a_reverify_by_date(self):
        missing = set(pricing._MODEL_BASE_INPUT_RATES) - set(pricing._MODEL_RATE_EXPIRES)

        assert not missing, missing

    def test_zero_token_priced_in_window_turn_counts_as_priced(self, tmp_path, monkeypatch):
        reverify_by = date(2026, 12, 4)
        monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, _PRICED_MODEL, reverify_by)
        session = _write_session(tmp_path, 0, [_priced(_PRICED_MODEL, ts="2026-05-19T10:00:00.000Z")])

        result = _price([session], as_of=date(2026, 12, 5))

        assert result.pricing_reverify_by == reverify_by
        assert result.stale_models == (_PRICED_MODEL,)

    def test_stale_models_flips_the_day_after_the_reverify_by_date(self, tmp_path, monkeypatch):
        reverify_by = date(2026, 12, 4)
        monkeypatch.setitem(pricing._MODEL_RATE_EXPIRES, _PRICED_MODEL, reverify_by)
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        day_before = _price([session], as_of=date(2026, 12, 3))
        on_the_day = _price([session], as_of=reverify_by)
        day_after = _price([session], as_of=date(2026, 12, 5))

        assert (day_before.stale_models, on_the_day.stale_models, day_after.stale_models) == ((), (), (_PRICED_MODEL,))

    def test_no_priced_in_window_turn_gives_no_reverify_date_and_no_stale_models(self, tmp_path):
        session = _write_session(tmp_path, 0, [_turn("2026-07-19T10:00:00.000Z")])

        result = _price([session], as_of=date(2100, 1, 1))

        assert result.pricing_reverify_by is None
        assert result.stale_models == ()

    def test_pricing_fetch_date_is_the_rate_tables_fetch_date(self, tmp_path):
        result = _price([_write_session(tmp_path, 0, [])])

        assert result.pricing_fetch_date == pricing._PRICING_FETCH_DATE


class TestRequestIdCollisions:
    def test_request_id_shared_by_two_sessions_counts_once_and_both_keep_their_spend(self, tmp_path):
        sessions = [
            _write_session(
                tmp_path,
                index,
                [_priced(_PRICED_MODEL, input=1_000_000, request_id="req-shared", ts="2026-05-19T10:00:00.000Z")],
            )
            for index in range(2)
        ]

        result = _price(sessions)

        assert result.request_id_collision_count == 1
        assert [spend.micro_dollars_by_day for spend in result.sessions] == [
            {date(2026, 5, 19): _ONE_MILLION_INPUT_TURN_MICRO_DOLLARS}
        ] * 2

    def test_request_id_shared_by_three_sessions_still_counts_once(self, tmp_path):
        sessions = [
            _write_session(tmp_path, index, [_priced(_PRICED_MODEL, input=1, request_id="req-shared")]) for index in range(3)
        ]

        assert _price(sessions).request_id_collision_count == 1

    def test_collision_on_turns_outside_the_window_is_still_counted(self, tmp_path):
        sessions = [
            _write_session(
                tmp_path,
                index,
                [_priced(_PRICED_MODEL, input=1, request_id="req-shared", ts="2026-07-19T10:00:00.000Z")],
            )
            for index in range(2)
        ]

        assert _price(sessions).request_id_collision_count == 1

    def test_collision_counts_assistant_records_with_empty_usage(self, tmp_path):
        sessions = [
            _write_session(tmp_path, index, [_with_usage(_priced(_PRICED_MODEL, input=1, request_id="req-shared"), {})])
            for index in range(2)
        ]

        result = _price(sessions)

        assert result.request_id_collision_count == 1
        assert result.total_micro_dollars == 0

    def test_request_id_repeated_inside_one_session_is_not_a_collision(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [
                _priced(_PRICED_MODEL, input=1, request_id="req-repeat"),
                _user_msg("interleaved"),
                _priced(_PRICED_MODEL, input=2, request_id="req-repeat"),
            ],
        )

        assert _price([session]).request_id_collision_count == 0

    def test_request_id_on_user_records_does_not_collide(self, tmp_path):
        sessions = [
            _write_session(tmp_path, index, [{**_user_msg("hello"), "requestId": "req-user"}]) for index in range(2)
        ]

        assert _price(sessions).request_id_collision_count == 0

    def test_null_and_empty_request_ids_do_not_collide(self, tmp_path):
        def record_with_request_id(request_id) -> dict:
            record = _priced(_PRICED_MODEL, input=1)
            record["requestId"] = request_id
            return record

        sessions = [
            _write_session(tmp_path, 0, [record_with_request_id(None), record_with_request_id("")]),
            _write_session(tmp_path, 1, [record_with_request_id(None), record_with_request_id("")]),
        ]

        assert _price(sessions).request_id_collision_count == 0

    def test_same_path_passed_twice_collides_on_every_distinct_request_id(self, tmp_path):
        session = _write_session(
            tmp_path,
            0,
            [
                _priced(_PRICED_MODEL, input=1, request_id="req-a"),
                _priced(_PRICED_MODEL, input=1, request_id="req-b"),
                _priced(_PRICED_MODEL, input=1, request_id="req-b"),
            ],
        )

        assert _price([session, session]).request_id_collision_count == 2


class TestCanaries:
    def test_drifting_session_reports_usage_drift_and_prints_one_warning(self, tmp_path, capsys):
        session = _write_session(tmp_path, 0, _contiguous_drifting_records("req-drift", "2026-05-19T10:00:00.000Z"))

        result = _price([session])

        assert result.usage_drift_detected is True
        assert _usage_warning_count(capsys.readouterr().err) == 1

    def test_later_calls_report_their_own_drift_though_the_warning_prints_once_per_process(self, tmp_path, capsys):
        drifting_first = _write_session(tmp_path, 0, _contiguous_drifting_records("req-drift-a", "2026-05-19T10:00:00.000Z"))
        drifting_second = _write_session(tmp_path, 1, _contiguous_drifting_records("req-drift-b", "2026-05-19T10:00:00.000Z"))
        clean = _write_session(tmp_path, 2, [_turn("2026-05-19T10:00:00.000Z")])

        first_call = _price([drifting_first])
        warnings_after_first = _usage_warning_count(capsys.readouterr().err)
        second_call = _price([drifting_second])
        warnings_after_second = _usage_warning_count(capsys.readouterr().err)
        third_call = _price([clean])

        assert (first_call.usage_drift_detected, second_call.usage_drift_detected, third_call.usage_drift_detected) == (
            True,
            True,
            False,
        )
        assert (warnings_after_first, warnings_after_second) == (1, 0)

    def test_drifting_run_entirely_outside_the_window_still_reports_usage_drift(self, tmp_path):
        session = _write_session(tmp_path, 0, _contiguous_drifting_records("req-drift", "2026-07-19T10:00:00.000Z"))

        assert _price([session]).usage_drift_detected is True

    def test_drifting_session_followed_by_a_clean_one_reports_usage_drift(self, tmp_path):
        drifting = _write_session(tmp_path, 0, _contiguous_drifting_records("req-drift", "2026-05-19T10:00:00.000Z"))
        clean = _write_session(tmp_path, 1, [_turn("2026-05-19T10:00:00.000Z")])

        assert _price([drifting, clean]).usage_drift_detected is True

    def test_spawn_without_any_sidechain_turn_reports_subagent_drift_and_prints_one_warning(self, tmp_path, capsys):
        session = _write_session(tmp_path, 0, [_spawn_turn()])

        result = _price([session])

        assert result.subagent_format_drift_detected is True
        assert _subagent_warning_count(capsys.readouterr().err) == 1

    @pytest.mark.parametrize("spawn_session_first", [True, False], ids=["spawn-first", "spawn-last"])
    def test_spawn_in_one_session_and_sidechain_turns_in_another_is_not_drift(self, tmp_path, capsys, spawn_session_first):
        spawning = _write_session(tmp_path, 0, [_spawn_turn()])
        sidechain = _write_session(tmp_path, 1, [_sidechain_turn()])
        ordered = [spawning, sidechain] if spawn_session_first else [sidechain, spawning]

        result = _price(ordered)

        assert result.subagent_format_drift_detected is False
        assert _subagent_warning_count(capsys.readouterr().err) == 0

    def test_spawn_in_the_main_file_answered_by_a_sidechain_turn_in_a_subagent_file_is_not_drift(self, tmp_path, capsys):
        session = _write_session(tmp_path, 0, [_spawn_turn()])
        _write_subagent_jsonl(session.parent, session.stem, "agent-1", [_sidechain_turn()])

        result = _price([session])

        assert result.subagent_format_drift_detected is False
        assert _subagent_warning_count(capsys.readouterr().err) == 0

    @pytest.mark.parametrize("spawn_session_first", [True, False], ids=["spawn-first", "spawn-last"])
    def test_spawn_only_session_beside_a_quiet_session_is_drift_in_either_order(self, tmp_path, capsys, spawn_session_first):
        spawning = _write_session(tmp_path, 0, [_spawn_turn()])
        quiet = _write_session(tmp_path, 1, [_turn("2026-05-19T10:00:00.000Z")])
        ordered = [spawning, quiet] if spawn_session_first else [quiet, spawning]

        result = _price(ordered)

        assert result.subagent_format_drift_detected is True
        assert _subagent_warning_count(capsys.readouterr().err) == 1

    def test_sidechain_turn_outside_the_window_still_answers_a_spawn_inside_it(self, tmp_path, capsys):
        session = _write_session(tmp_path, 0, [_spawn_turn(), _sidechain_turn(ts="2026-07-19T10:00:00.000Z")])

        result = _price([session])

        assert result.subagent_format_drift_detected is False
        assert _subagent_warning_count(capsys.readouterr().err) == 0

    def test_spawn_outside_the_window_without_a_sidechain_turn_is_still_drift(self, tmp_path, capsys):
        session = _write_session(tmp_path, 0, [_spawn_turn(ts="2026-07-19T10:00:00.000Z")])

        result = _price([session])

        assert result.subagent_format_drift_detected is True
        assert _subagent_warning_count(capsys.readouterr().err) == 1

    def test_healthy_call_after_a_subagent_drift_call_is_clean_while_the_process_flag_stays_set(self, tmp_path):
        spawning = _write_session(tmp_path, 0, [_spawn_turn()])
        healthy = _write_session(tmp_path, 1, [_turn("2026-05-19T10:00:00.000Z")])

        drift_call = _price([spawning])
        healthy_call = _price([healthy])

        assert drift_call.subagent_format_drift_detected is True
        assert healthy_call.subagent_format_drift_detected is False
        assert pricing._subagent_format_drift_detected is True

    def test_same_inputs_give_equal_results_whatever_state_the_process_flags_are_in(self, tmp_path, monkeypatch):
        drifting_records = _contiguous_drifting_records("req-drift", "2026-05-19T10:00:00.000Z")
        session = _write_session(tmp_path, 0, [*drifting_records, _spawn_turn()])

        with_clean_flags = _price([session])
        monkeypatch.setattr(pricing, "_usage_drift_warned", True)
        monkeypatch.setattr(pricing, "_subagent_format_drift_detected", True)
        with_set_flags = _price([session])

        assert with_clean_flags == with_set_flags

    @pytest.mark.parametrize(
        ("usage_drift", "subagent_drift", "expected"),
        [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
    )
    def test_format_drift_detected_is_true_when_either_flag_is_true(self, tmp_path, usage_drift, subagent_drift, expected):
        result = dataclasses.replace(
            _price([_write_session(tmp_path, 0, [])]),
            usage_drift_detected=usage_drift,
            subagent_format_drift_detected=subagent_drift,
        )

        assert result.format_drift_detected is expected


_NESTED_CACHE_CREATION = {"ephemeral_1h_input_tokens": 1, "ephemeral_5m_input_tokens": 2}

# (usage, text the message must name). No case's message may carry the sentinel value.
_MALFORMED_USAGE_CASES = [
    pytest.param([1, 2], "list", id="non-dict-usage"),
    pytest.param({"input_tokens": None}, "input_tokens", id="null-field"),
    pytest.param({"output_tokens": "123"}, "output_tokens", id="numeric-string"),
    pytest.param({"cache_read_input_tokens": 1.48151623}, "cache_read_input_tokens", id="float"),
    pytest.param({"input_tokens": True}, "input_tokens", id="bool"),
    pytest.param({"output_tokens": -48151623}, "output_tokens", id="negative"),
    pytest.param({"cache_creation": [1]}, "cache_creation", id="non-dict-cache-creation"),
    pytest.param(
        {"cache_creation": {"ephemeral_1h_input_tokens": None}},
        "cache_creation.ephemeral_1h_input_tokens",
        id="null-nested-field",
    ),
    pytest.param(
        {"cache_creation": {"ephemeral_5m_input_tokens": -1}},
        "cache_creation.ephemeral_5m_input_tokens",
        id="negative-nested-field",
    ),
    pytest.param(
        {"cache_creation_input_tokens": "SENTINEL_TOKEN_VALUE", "cache_creation": _NESTED_CACHE_CREATION},
        "cache_creation_input_tokens",
        id="bad-flat-field-beside-valid-nested-block",
    ),
]


class TestValidator:
    @pytest.mark.parametrize(
        "usage",
        [
            {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4},
            {"input_tokens": 0, "cache_creation": None},
            {"cache_creation": _NESTED_CACHE_CREATION},
            {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_creation_input_tokens": 3,
                "cache_creation": _NESTED_CACHE_CREATION,
                "service_tier": "standard",
                "server_tool_use": {"web_search_requests": 0},
                "speed": "fast",
                "inference_geo": "us",
            },
        ],
        ids=["flat-fields", "null-cache-creation", "nested-only", "realistic-with-non-token-keys"],
    )
    def test_accepts_well_formed_usage(self, usage):
        session_pricing._validate_usage_token_fields(usage)

    @pytest.mark.parametrize(("usage", "named_text"), _MALFORMED_USAGE_CASES)
    def test_rejects_malformed_usage_naming_the_key_and_never_the_value(self, usage, named_text):
        with pytest.raises(ValueError) as raised:
            session_pricing._validate_usage_token_fields(usage)

        assert named_text in str(raised.value)
        assert "48151623" not in str(raised.value)
        assert "SENTINEL_TOKEN_VALUE" not in str(raised.value)


class _BytesPathLike:
    def __fspath__(self) -> bytes:
        return b"SENTINEL_BYTES_PATH"


class TestErrors:
    """Cause types and text are asserted to prove which stage failed, because `except Exception` also wraps coding bugs.
    They are not part of the stability promise.
    """

    @pytest.mark.parametrize(("usage", "named_text"), _MALFORMED_USAGE_CASES)
    def test_malformed_usage_in_the_second_session_raises_with_its_index_and_a_value_error_cause(
        self, tmp_path, usage, named_text
    ):
        valid = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        malformed = _write_session(tmp_path, 1, [_with_usage(_turn("2026-05-19T10:00:00.000Z"), usage)])

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price([valid, malformed])

        assert raised.value.session_index == 1
        assert isinstance(raised.value.__cause__, ValueError)
        assert named_text in str(raised.value.__cause__)

    def test_exception_from_the_read_layer_on_the_second_session_raises_with_its_index(self, tmp_path, monkeypatch):
        first = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        second = _write_session(tmp_path, 1, [_turn("2026-05-19T10:00:00.000Z")])
        real_read = corpus._read_session_file_partitioned

        def read_failing_on_the_second_path(path, *args, **kwargs):
            if path == second:
                raise RuntimeError("simulated read failure")
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(corpus, "_read_session_file_partitioned", read_failing_on_the_second_path)

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price([first, second])

        assert raised.value.session_index == 1
        assert isinstance(raised.value.__cause__, RuntimeError)

    def test_line_that_json_loads_aborts_on_beside_a_priced_turn_raises_with_its_index(self, tmp_path):
        valid = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        deeply_nested = _write_session(tmp_path, 1, [_turn("2026-05-19T10:00:00.000Z")])
        with deeply_nested.open("a") as handle:
            handle.write("[" * 200_000 + "\n")

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price([valid, deeply_nested])

        assert raised.value.session_index == 1

    @pytest.mark.parametrize(
        "token_count",
        [
            pytest.param(10**400, id="overflows-in-the-division"),
            pytest.param(10**314, id="overflows-only-in-the-product"),
        ],
    )
    def test_token_count_that_overflows_float_pricing_raises_with_its_index(self, tmp_path, token_count):
        valid = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])
        overflowing = _write_session(tmp_path, 1, [_turn("2026-05-19T10:00:00.000Z", input=token_count)])

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price([valid, overflowing])

        assert raised.value.session_index == 1

    def test_malformed_usage_on_a_turn_outside_the_window_still_raises(self, tmp_path):
        session = _write_session(tmp_path, 0, [_with_usage(_turn("2026-07-19T10:00:00.000Z"), {"input_tokens": None})])

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price([session])

        assert isinstance(raised.value.__cause__, ValueError)

    @pytest.mark.parametrize(
        ("bad_record", "cause_type"),
        [
            pytest.param("a bare string", TypeError, id="json-string-line"),
            pytest.param([1, 2], TypeError, id="json-list-line"),
            pytest.param(_with_usage(_turn(["not", "a", "string"]), {"input_tokens": 1}), AttributeError, id="list-timestamp"),
            pytest.param(_with_usage(_turn(12345), {"input_tokens": 1}), AttributeError, id="integer-timestamp"),
            pytest.param({"type": "assistant", "message": "not an object"}, AttributeError, id="non-dict-message"),
        ],
    )
    def test_record_shapes_outside_the_format_raise_with_the_matching_cause_type(self, tmp_path, bad_record, cause_type):
        session = _write_session(tmp_path, 0, [bad_record])

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price([session])

        assert raised.value.session_index == 0
        assert isinstance(raised.value.__cause__, cause_type)

    @pytest.mark.parametrize(
        ("bad_record", "cause_type"),
        [
            pytest.param("SENTINEL_DISCLOSURE_VALUE", TypeError, id="sentinel-json-line"),
            pytest.param(["SENTINEL_DISCLOSURE_VALUE"], TypeError, id="sentinel-in-json-list"),
            pytest.param(_turn(["SENTINEL_DISCLOSURE_VALUE"]), AttributeError, id="sentinel-in-timestamp-list"),
            pytest.param(
                _with_usage(_turn("2026-05-19T10:00:00.000Z"), {"input_tokens": "SENTINEL_DISCLOSURE_VALUE"}),
                ValueError,
                id="sentinel-token-field",
            ),
            pytest.param(
                {"type": "assistant", "message": "SENTINEL_DISCLOSURE_VALUE"}, AttributeError, id="sentinel-message"
            ),
        ],
    )
    def test_neither_the_message_nor_the_traceback_discloses_content_or_paths(self, tmp_path, bad_record, cause_type):
        valid = _write_session(tmp_path, 0, [_with_field_sentinels(_turn("2026-05-19T10:00:00.000Z"))])
        malformed = _write_session(tmp_path, 1, [_with_field_sentinels(bad_record)])
        session_paths = [valid, malformed]

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price(session_paths)

        rendered = "".join(traceback.format_exception(raised.value))
        assert isinstance(raised.value.__cause__, cause_type)
        assert "SENTINEL_DISCLOSURE_VALUE" not in rendered
        assert str(tmp_path) not in rendered
        for disclosed in _RECORD_FIELD_SENTINELS.values():
            assert disclosed not in rendered
        assert str(raised.value) == "session_paths[1] could not be priced"

    @pytest.mark.parametrize(
        ("overrides", "error_type"),
        [
            pytest.param({"start": date(2026, 6, 1), "end": date(2026, 5, 1)}, ValueError, id="start-after-end"),
            pytest.param({"start": datetime(2026, 5, 1)}, TypeError, id="datetime-start"),
            pytest.param({"end": datetime(2026, 5, 31)}, TypeError, id="datetime-end"),
            pytest.param({"as_of": datetime(2026, 6, 1)}, TypeError, id="datetime-as-of"),
            pytest.param({"as_of": "2026-06-01"}, TypeError, id="string-as-of"),
        ],
    )
    def test_bad_date_arguments_raise_before_any_session_is_read(self, tmp_path, monkeypatch, overrides, error_type):
        reads = []
        monkeypatch.setattr(corpus, "_read_session_file_partitioned", lambda *args, **kwargs: reads.append(args) or [])
        missing = tmp_path / _PROJECT_SLUG / "missing.jsonl"
        malformed = _write_session(tmp_path, 1, ["a bare string"])
        arguments = {"start": _START, "end": _END, "as_of": _AS_OF} | overrides

        with pytest.raises(error_type):
            session_pricing.price_sessions_by_utc_day([missing, malformed], **arguments)

        assert reads == []

    @pytest.mark.parametrize(
        "session_paths_factory",
        [
            pytest.param(lambda tmp_path: str(tmp_path / _PROJECT_SLUG / f"{_FIRST_SESSION_STEM}.jsonl"), id="str"),
            pytest.param(lambda tmp_path: str(tmp_path / _PROJECT_SLUG / f"{_FIRST_SESSION_STEM}.jsonl").encode(), id="bytes"),
            pytest.param(lambda tmp_path: tmp_path / _PROJECT_SLUG / f"{_FIRST_SESSION_STEM}.jsonl", id="path"),
        ],
    )
    def test_session_paths_given_as_one_path_instead_of_a_sequence_raises_a_type_error_naming_no_value(
        self, tmp_path, session_paths_factory
    ):
        with pytest.raises(TypeError) as raised:
            _price(session_paths_factory(tmp_path))

        assert _FIRST_SESSION_STEM not in str(raised.value)
        assert _PROJECT_SLUG not in str(raised.value)
        assert str(tmp_path) not in str(raised.value)

    def test_non_path_element_raises_a_type_error_naming_its_index_and_type(self, tmp_path, monkeypatch):
        reads = []
        monkeypatch.setattr(corpus, "_read_session_file_partitioned", lambda *args, **kwargs: reads.append(args) or [])
        malformed = _write_session(tmp_path, 0, ["a bare string"])

        with pytest.raises(TypeError) as raised:
            _price([malformed, 7])

        assert "session_paths[1]" in str(raised.value)
        assert "int" in str(raised.value)
        assert reads == []

    @pytest.mark.parametrize(
        ("element", "type_name"),
        [
            pytest.param(b"SENTINEL_BYTES_PATH", "bytes", id="bytes"),
            pytest.param(_BytesPathLike(), "_BytesPathLike", id="pathlike-bytes"),
        ],
    )
    def test_element_whose_fspath_is_bytes_raises_a_type_error_naming_its_index_and_type(
        self, tmp_path, monkeypatch, element, type_name
    ):
        reads = []
        monkeypatch.setattr(corpus, "_read_session_file_partitioned", lambda *args, **kwargs: reads.append(args) or [])
        valid = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        with pytest.raises(TypeError) as raised:
            _price([valid, element])

        assert "session_paths[1]" in str(raised.value)
        assert type_name in str(raised.value)
        assert "SENTINEL_BYTES_PATH" not in str(raised.value)
        assert str(tmp_path) not in str(raised.value)
        assert reads == []

    def test_element_with_a_nul_byte_raises_a_value_error_naming_only_its_index(self, tmp_path, monkeypatch):
        reads = []
        monkeypatch.setattr(corpus, "_read_session_file_partitioned", lambda *args, **kwargs: reads.append(args) or [])
        valid = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        with pytest.raises(ValueError) as raised:
            _price([valid, "SENTINEL_NUL_PATH\x00tail"])

        assert "session_paths[1]" in str(raised.value)
        assert "SENTINEL_NUL_PATH" not in str(raised.value)
        assert str(tmp_path) not in str(raised.value)
        assert reads == []

    def test_element_unencodable_as_an_os_path_raises_a_value_error_naming_only_its_index(self, tmp_path, monkeypatch):
        reads = []
        monkeypatch.setattr(corpus, "_read_session_file_partitioned", lambda *args, **kwargs: reads.append(args) or [])
        valid = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        with pytest.raises(ValueError) as raised:
            _price([valid, "SENTINEL_SURROGATE_PATH\ud800tail"])

        assert "session_paths[1]" in str(raised.value)
        assert "SENTINEL_SURROGATE_PATH" not in str(raised.value)
        assert str(tmp_path) not in str(raised.value)
        assert reads == []

    def test_element_with_a_surrogate_escaped_byte_is_accepted_as_an_os_path(self, tmp_path):
        undecodable_name = "x\udcff.jsonl"
        try:
            os.fsencode(undecodable_name)
        except UnicodeEncodeError:
            pytest.skip(
                f"filesystem encoding {sys.getfilesystemencoding()}/{sys.getfilesystemencodeerrors()} "
                "does not round-trip a surrogate-escaped byte"
            )

        result = _price([tmp_path / undecodable_name])

        assert result.sessions[0].main_file_readable is False

    def test_bad_as_of_value_is_never_echoed_in_its_error_message(self):
        with pytest.raises(TypeError) as raised:
            _price([], as_of=_FIRST_SESSION_STEM)

        assert _FIRST_SESSION_STEM not in str(raised.value)

    def test_first_failing_session_aborts_and_no_later_session_is_read(self, tmp_path, monkeypatch):
        reads = []
        real_read = corpus._read_session_file_partitioned

        def counting_read(*args, **kwargs):
            reads.append(args)
            return real_read(*args, **kwargs)

        monkeypatch.setattr(corpus, "_read_session_file_partitioned", counting_read)
        sessions = [_write_session(tmp_path, index, ["a bare string"]) for index in range(2)]

        with pytest.raises(session_pricing.SessionPricingError) as raised:
            _price(sessions)

        assert raised.value.session_index == 0
        assert len(reads) == 1

    def test_keyboard_interrupt_from_pricing_propagates_unwrapped(self, tmp_path, monkeypatch):
        def interrupted(model, usage):
            raise KeyboardInterrupt

        monkeypatch.setattr(pricing, "_price_turn", interrupted)
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        with pytest.raises(KeyboardInterrupt):
            _price([session])


class TestEmptyInput:
    def test_empty_session_paths_return_empty_zero_totals_and_no_reverify_date(self):
        result = _price([])

        assert result.sessions == ()
        assert result.total_micro_dollars == 0
        assert result.total_unpriced_tokens == 0
        assert result.unpriced_models == ()
        assert result.stale_models == ()
        assert result.pricing_reverify_by is None
        assert result.request_id_collision_count == 0
        assert result.format_drift_detected is False


def _reference_parse_ts(ts_str):
    """Falsy input gives None, Z is read as UTC, an offsetless value is read as host-local.

    A ValueError or TypeError while parsing gives None, and a truthy non-string raises AttributeError.
    """
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _outcome(function, argument):
    try:
        return ("returned", function(argument))
    except Exception as exc:
        return ("raised", type(exc))


class TestParseTsParity:
    pytestmark = pytest.mark.usefixtures("host_time_zone")

    @pytest.mark.parametrize(
        "ts_str",
        [
            None,
            "",
            "garbage",
            "2026-05-19T10:00:00.000Z",
            "2026-05-19T10:00:00.000+05:30",
            "2026-05-19T10:00:00.000",
            "0001-01-01T00:00:00",
            "9999-12-31T23:59:59",
            5,
        ],
        ids=["none", "empty", "garbage", "zulu", "offset", "offsetless", "naive-year-1", "naive-year-9999", "int"],
    )
    def test_parse_ts_returns_or_raises_what_the_reference_does(self, ts_str):
        assert _outcome(corpus._parse_ts, ts_str) == _outcome(_reference_parse_ts, ts_str)


class TestOutputContract:
    # The four stderr lines pricing.py may print, as documented prefixes.
    _STDERR_PREFIXES = (
        "WARNING: requestId ",
        "NOTICE: non-contiguous requestId run merged -- ",
        "NOTICE: non-contiguous requestId run rejected -- ",
        "WARNING: subagent spawns detected in main thread",
    )
    # Whitespace-normalized sentences the doc's "What the output can disclose" list must keep about unpriced_models entries.
    _UNPRICED_MODELS_DISCLOSURE_CLAUSES = (
        "untrusted transcript text of any length",
        "account-identifying pre-announcement name",
        "nowhere safe to put that",
        "escape entries before rendering",
    )

    def _session_triggering_every_stderr_line(self, tmp_path: Path) -> Path:
        records = [
            *[
                _priced(_UNPRICED_MODEL, input=tokens, request_id="req-drift", ts="2026-05-19T10:00:00.000Z")
                for tokens in (10, 11)
            ],
            _priced(_UNPRICED_MODEL, output=5, request_id="req-matching", ts="2026-05-19T10:00:00.000Z"),
            _user_msg("interleaved"),
            _priced(_UNPRICED_MODEL, output=5, request_id="req-matching", ts="2026-05-19T10:00:00.000Z"),
            _priced(_UNPRICED_MODEL, output=5, request_id="req-rejected", ts="2026-05-19T10:00:00.000Z"),
            _user_msg("interleaved"),
            _priced(_UNPRICED_MODEL, output=6, request_id="req-rejected", ts="2026-05-19T10:00:00.000Z"),
            _priced(_UNPRICED_MODEL, input=1, ts="2026-05-19T10:00:00.000Z", content=[_agent_use("toolu-spawn", "x")]),
        ]
        return _write_session(tmp_path, 0, [_with_field_sentinels(record) for record in records])

    def test_one_call_prints_exactly_the_four_documented_lines_and_discloses_no_path_or_content(self, tmp_path, capsys):
        session = self._session_triggering_every_stderr_line(tmp_path)
        assert session.name == f"{_FIRST_SESSION_STEM}.jsonl"
        assert session.parent.name == _PROJECT_SLUG

        _price([session])

        captured = capsys.readouterr()
        stderr_lines = captured.err.splitlines()
        assert captured.out == ""
        assert len(stderr_lines) == len(self._STDERR_PREFIXES)
        for prefix in self._STDERR_PREFIXES:
            assert sum(line.startswith(prefix) for line in stderr_lines) == 1, prefix
        for disclosed in (_PROJECT_SLUG, _FIRST_SESSION_STEM, _UNPRICED_MODEL, str(tmp_path), *_RECORD_FIELD_SENTINELS.values()):
            assert disclosed not in captured.err

    @pytest.mark.parametrize(
        ("prefix", "records_factory"),
        [
            pytest.param(
                "WARNING: requestId ",
                lambda request_id: _contiguous_drifting_records(request_id, "2026-05-19T10:00:00.000Z"),
                id="usage-drift",
            ),
            pytest.param(
                "NOTICE: non-contiguous requestId run merged -- ",
                lambda request_id: [
                    _priced(_PRICED_MODEL, output=5, request_id=request_id),
                    _user_msg("interleaved"),
                    _priced(_PRICED_MODEL, output=5, request_id=request_id),
                ],
                id="non-contiguous-merged",
            ),
            pytest.param(
                "NOTICE: non-contiguous requestId run rejected -- ",
                lambda request_id: [
                    _priced(_PRICED_MODEL, output=5, request_id=request_id),
                    _user_msg("interleaved"),
                    _priced(_PRICED_MODEL, output=6, request_id=request_id),
                ],
                id="non-contiguous-rejected",
            ),
        ],
    )
    def test_hostile_request_id_is_escaped_so_it_forges_no_line_and_emits_no_control_character(
        self, tmp_path, capsys, prefix, records_factory
    ):
        hostile_request_id = "req-x\n\x1b[31mWARNING: forged"
        session = _write_session(tmp_path, 0, records_factory(hostile_request_id))

        _price([session])

        stderr = capsys.readouterr().err
        assert len(stderr.splitlines()) == 1
        assert stderr.startswith(prefix)
        assert stderr.count(prefix) == 1
        assert "\x1b" not in stderr
        assert stderr.count("\n") == 1

    def test_every_stderr_prefix_is_documented_in_the_architecture_doc_section(self):
        section = _architecture_doc_section()

        for prefix in self._STDERR_PREFIXES:
            assert prefix in section, f"{prefix!r} is not in the doc's session_pricing.py section"

    def test_unpriced_models_disclosure_clauses_are_documented_in_the_architecture_doc_section(self):
        section = " ".join(_architecture_doc_section().split())

        for clause in self._UNPRICED_MODELS_DISCLOSURE_CLAUSES:
            assert clause in section, f"{clause!r} is not in the doc's session_pricing.py section"

    def test_breaking_change_note_states_the_live_default(self):
        """Pins the doc's "Breaking change" paragraph against the live signature default, so a future flip
        of include_unpriced_model_ids cannot leave this paragraph stating the opposite default."""
        section = " ".join(_architecture_doc_section().split())
        live_default = inspect.signature(
            session_pricing.price_sessions_by_utc_day
        ).parameters["include_unpriced_model_ids"].default

        assert f"default `{live_default}`" in section or f"default {live_default}" in section, (
            f"the doc's Breaking change paragraph does not state the live default {live_default!r}"
        )

    def test_unpriced_models_disclosure_is_a_behavioral_control_not_just_documentation(self, tmp_path):
        """The doc's disclosure clauses describe a real opt-out gate, not prose a caller could ignore.
        unpriced_models is populated unless the caller opts out, and opting out leaks no transcript-derived
        text."""
        session = _write_session(
            tmp_path, 0, [_priced("claude-sentinel-model-1", input=1, ts="2026-05-19T10:00:00.000Z")]
        )

        default_call = _price([session])
        opted_out_call = _price([session], include_unpriced_model_ids=False)

        assert default_call.unpriced_models == ("claude-sentinel-model-1",)
        assert default_call.unpriced_model_count == 1
        assert opted_out_call.unpriced_models == ()
        assert opted_out_call.unpriced_model_count == 1

    def test_quiet_call_prints_nothing(self, tmp_path, capsys):
        session = _write_session(tmp_path, 0, [_turn("2026-05-19T10:00:00.000Z")])

        _price([session])

        captured = capsys.readouterr()
        assert (captured.out, captured.err) == ("", "")


def _architecture_doc_section() -> str:
    lines = _ARCHITECTURE_DOC.read_text().splitlines()
    start = lines.index(_SESSION_PRICING_HEADING)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    return "\n".join(lines[start:end])


_PROMISED_RESULT_FIELD_TYPES = {
    "sessions": tuple[session_pricing.SessionSpend, ...],
    "total_micro_dollars": int,
    "total_unpriced_tokens": int,
    "unpriced_models": tuple[str, ...],
    "unpriced_model_count": int,
    "stale_models": tuple[str, ...],
    "pricing_fetch_date": date,
    "pricing_reverify_by": date | None,
    "usage_drift_detected": bool,
    "subagent_format_drift_detected": bool,
    "request_id_collision_count": int,
}
_PROMISED_SPEND_FIELD_TYPES = {
    "main_file_readable": bool,
    "micro_dollars_by_day": Mapping[date, int],
    "unpriced_tokens_by_day": Mapping[date, int],
    "undated_turn_count": int,
}
_PROMISED_PUBLIC_NAMES = {"SessionPricingError", "SessionPricingResult", "SessionSpend", "price_sessions_by_utc_day"}


def _module_tree() -> ast.Module:
    return ast.parse(Path(session_pricing.__file__).read_text())


def _top_level_assignment_target_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets = [node.target]
        for target in targets:
            names.update(sub.id for sub in ast.walk(target) if isinstance(sub, ast.Name))
    return names


def _transcript_analysis_imports(tree: ast.Module) -> set[str]:
    """Names this module takes from the transcript_analysis package, as the submodule they reach."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "transcript_analysis":
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("transcript_analysis."):
            imported.add(node.module.removeprefix("transcript_analysis."))
        elif isinstance(node, ast.Import):
            imported.update(
                alias.name.removeprefix("transcript_analysis.")
                for alias in node.names
                if alias.name == "transcript_analysis" or alias.name.startswith("transcript_analysis.")
            )
    return imported


class TestPublicSurfacePin:
    def test_all_names_the_promised_public_names(self):
        assert set(session_pricing.__all__) >= _PROMISED_PUBLIC_NAMES, _PROMISE_HINT

    def test_module_defines_no_public_name_outside_all(self):
        defined_public = {
            name
            for name, value in vars(session_pricing).items()
            if not name.startswith("_") and getattr(value, "__module__", None) == session_pricing.__name__
        }

        assert defined_public == set(session_pricing.__all__), _PROMISE_HINT

    def test_every_top_level_assignment_target_is_underscore_prefixed(self):
        public_targets = {name for name in _top_level_assignment_target_names(_module_tree()) if not name.startswith("_")}

        assert public_targets == set(), _PROMISE_HINT

    def test_function_signature_keeps_its_parameters(self):
        parameters = list(inspect.signature(session_pricing.price_sessions_by_utc_day).parameters.values())

        assert parameters[0].name == "session_paths", _PROMISE_HINT
        assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, _PROMISE_HINT
        assert parameters[0].default is inspect.Parameter.empty, _PROMISE_HINT
        by_name = {parameter.name: parameter for parameter in parameters[1:]}
        for required in ("start", "end", "as_of"):
            assert required in by_name, _PROMISE_HINT
            assert by_name[required].kind is inspect.Parameter.KEYWORD_ONLY, _PROMISE_HINT
            assert by_name[required].default is inspect.Parameter.empty, _PROMISE_HINT
        for additional in parameters[1:]:
            if additional.name not in ("start", "end", "as_of"):
                assert additional.kind is inspect.Parameter.KEYWORD_ONLY, _PROMISE_HINT
                assert additional.default is not inspect.Parameter.empty, _PROMISE_HINT

    @pytest.mark.parametrize(
        ("result_type", "promised_field_types"),
        [
            (session_pricing.SessionSpend, _PROMISED_SPEND_FIELD_TYPES),
            (session_pricing.SessionPricingResult, _PROMISED_RESULT_FIELD_TYPES),
        ],
        ids=["SessionSpend", "SessionPricingResult"],
    )
    def test_promised_fields_keep_their_types_and_every_field_is_keyword_only_and_documented(
        self, result_type, promised_field_types
    ):
        hints = typing.get_type_hints(result_type)
        fields = {field.name: field for field in dataclasses.fields(result_type)}

        for name, expected_type in promised_field_types.items():
            assert name in fields, f"{result_type.__name__}.{name} is missing: {_PROMISE_HINT}"
            assert hints[name] == expected_type, f"{result_type.__name__}.{name} changed type: {_PROMISE_HINT}"
        documented_lines = [line.strip() for line in (result_type.__doc__ or "").splitlines()]
        for name, field in fields.items():
            assert field.kw_only, f"{result_type.__name__}.{name} is not keyword-only: {_PROMISE_HINT}"
            assert any(line.startswith(f"{name}:") for line in documented_lines), (
                f"{result_type.__name__}.{name} is undocumented"
            )

    def test_format_drift_detected_is_a_property_on_the_result(self):
        assert isinstance(session_pricing.SessionPricingResult.__dict__["format_drift_detected"], property), _PROMISE_HINT

    def test_result_fields_refuse_reassignment(self):
        result = _price([])

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.total_micro_dollars = 1

    def test_session_spend_fields_refuse_reassignment(self, tmp_path):
        spend = _price([tmp_path / "missing.jsonl"]).sessions[0]

        with pytest.raises(dataclasses.FrozenInstanceError):
            spend.undated_turn_count = 1

    def test_error_type_and_its_index_survive_copy_and_pickle_with_an_index_only_message(self):
        assert issubclass(session_pricing.SessionPricingError, Exception), _PROMISE_HINT
        error = session_pricing.SessionPricingError(3)

        assert error.session_index == 3
        assert str(error) == "session_paths[3] could not be priced"
        for clone in (pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)):
            assert isinstance(clone, session_pricing.SessionPricingError)
            assert clone.session_index == 3
            assert str(clone) == "session_paths[3] could not be priced"

    def test_module_imports_only_corpus_and_pricing_from_the_package(self):
        assert _transcript_analysis_imports(_module_tree()) <= {"corpus", "pricing"}, _PROMISE_HINT


class TestImportPath:
    _FRESH_INTERPRETER_SCRIPT = """
import json
import os
import sys
from datetime import date

sys.path.append(sys.argv[1])
from transcript_analysis import session_pricing

result = session_pricing.price_sessions_by_utc_day(
    [sys.argv[2]], start=date(2026, 5, 1), end=date(2026, 5, 31), as_of=date(2026, 6, 1)
)
# Known boundary: only modules this call imports are seen; a function-level import on an unexercised branch escapes.
# Every module loaded from the scripts directory, so a top-level sibling such as _config_dir counts too.
scripts_dir = os.path.realpath(sys.argv[1]) + os.sep
loaded = sorted(
    name
    for name, module in sys.modules.items()
    if getattr(module, "__file__", None) and os.path.realpath(module.__file__).startswith(scripts_dir)
)
print(json.dumps({"loaded": loaded, "priced": result.total_micro_dollars > 0}))
"""

    def test_documented_import_path_works_in_a_fresh_interpreter_loading_only_the_promised_modules(self, tmp_path):
        # The fresh interpreter sees the live rate table, not this file's synthetic model, so the turn uses a live model.
        live_model = next(
            model
            for model in pricing._MODEL_BASE_INPUT_RATES
            if model not in (_PRICED_MODEL, pricing._SYNTHETIC_MODEL_ID)
        )
        session = _write_session(tmp_path, 0, [_priced(live_model, input=1_000)])

        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", self._FRESH_INTERPRETER_SCRIPT, str(SCRIPTS_DIR), str(session)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)
        assert report["loaded"] == [
            "transcript_analysis",
            "transcript_analysis.corpus",
            "transcript_analysis.pricing",
            "transcript_analysis.session_pricing",
        ]
        assert report["priced"] is True

    def test_changed_modules_parse_at_the_python_3_11_grammar(self):
        """A partial guard: CI runs only 3.12, so this catches newer syntax but not newer library calls."""
        package_dir = Path(session_pricing.__file__).parent
        for module_name in ("session_pricing.py", "pricing.py", "corpus.py"):
            source = (package_dir / module_name).read_text()
            ast.parse(source, feature_version=(3, 11))
