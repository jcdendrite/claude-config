"""Tests for transcript_analysis/pricing.py's per-session helpers: _dedup_session,
the per-call return values of the two drift canaries, and _rate_expired -- plus
the cost.py wiring that feeds the subagent canary from them."""
from datetime import date

import pytest
from transcript_analysis import cost, pricing

from .conftest import (
    _agent_use,
    _asst,
    _cost_args,
    _priced,
    _priced_sidechain_asst,
    _user_msg,
    _write_jsonl,
    _write_subagent_jsonl,
)

_MODEL = "claude-sonnet-5"


@pytest.fixture(autouse=True)
def _fresh_non_contiguous_notice_set(monkeypatch):
    """The suite-wide autouse fixture resets the two drift flags but not this
    set, and a NOTICE already logged by an earlier test would silence the
    one a test here asserts on."""
    monkeypatch.setattr(pricing, "_non_contiguous_merge_notices_logged", set())


def _usage(input_tokens: int, output_tokens: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def _block(text: str) -> dict:
    return {"type": "text", "text": text}


def _assistant_record(
    request_id: str | None,
    *,
    uuid: str,
    ts: str,
    text: str,
    usage: dict,
    sidechain: bool = False,
) -> dict:
    rec = _asst(_MODEL, sidechain=sidechain, ts=ts, content=[_block(text)], request_id=request_id)
    rec["uuid"] = uuid
    rec["message"]["usage"] = usage
    return rec


def _merged_record(
    request_id: str, *, uuid: str, ts: str, texts: list[str], usage: dict, sidechain: bool = False
) -> dict:
    """The hand-written shape of one merged requestId run: the first record's
    identity and timestamp, every record's content, the last record's usage."""
    return {
        "type": "assistant",
        "gitBranch": "main",
        "isSidechain": sidechain,
        "timestamp": ts,
        "requestId": request_id,
        "uuid": uuid,
        "message": {"model": _MODEL, "content": [_block(text) for text in texts], "usage": usage},
    }


class TestDedupSessionTurns:
    def test_merges_matching_runs_and_leaves_unmatched_records_alone(self):
        """A contiguous run and a usage-matching non-contiguous run each collapse to one
        turn; a non-contiguous pair whose usage differs, a requestId-less assistant record
        and a user record stay as they are."""
        user_a = _user_msg("first prompt")
        contiguous_first = _assistant_record(
            "req-contiguous", uuid="u-c1", ts="2026-05-19T10:00:00.000Z", text="c1", usage=_usage(100, 3)
        )
        contiguous_last = _assistant_record(
            "req-contiguous", uuid="u-c2", ts="2026-05-19T10:00:01.000Z", text="c2", usage=_usage(100, 50)
        )
        matching_first = _assistant_record(
            "req-split-match", uuid="u-m1", ts="2026-05-19T10:01:00.000Z", text="m1", usage=_usage(10, 5)
        )
        user_b = _user_msg("tool result between")
        matching_last = _assistant_record(
            "req-split-match", uuid="u-m2", ts="2026-05-19T10:01:02.000Z", text="m2", usage=_usage(10, 5)
        )
        unmatched_first = _assistant_record(
            "req-split-differs", uuid="u-d1", ts="2026-05-19T10:02:00.000Z", text="d1", usage=_usage(10, 5)
        )
        user_c = _user_msg("another tool result")
        unmatched_last = _assistant_record(
            "req-split-differs", uuid="u-d2", ts="2026-05-19T10:02:02.000Z", text="d2", usage=_usage(10, 9)
        )
        without_request_id = _assistant_record(
            None, uuid="u-n1", ts="2026-05-19T10:03:00.000Z", text="n1", usage=_usage(1, 1)
        )
        records = [
            user_a, contiguous_first, contiguous_last, matching_first, user_b, matching_last,
            unmatched_first, user_c, unmatched_last, without_request_id,
        ]

        deduped = pricing._dedup_session(records)

        assert deduped.turns == [
            user_a,
            _merged_record(
                "req-contiguous", uuid="u-c1", ts="2026-05-19T10:00:00.000Z",
                texts=["c1", "c2"], usage=_usage(100, 50),
            ),
            _merged_record(
                "req-split-match", uuid="u-m1", ts="2026-05-19T10:01:00.000Z",
                texts=["m1", "m2"], usage=_usage(10, 5),
            ),
            user_b,
            unmatched_first,
            user_c,
            unmatched_last,
            without_request_id,
        ]


class TestDedupSessionCounts:
    def test_spawn_count_counts_main_thread_agent_and_task_but_not_sidechain_spawns(self):
        records = [
            _asst(_MODEL, content=[_agent_use("t1", "staff-backend-engineer")]),
            _asst(_MODEL, content=[_agent_use("t2", "staff-backend-engineer", tool_name="Task")]),
            _asst(_MODEL, sidechain=True, content=[_agent_use("t3", "staff-backend-engineer")]),
        ]

        assert pricing._dedup_session(records).subagent_spawns == 2

    def test_empty_records_yield_zero_counts_and_no_drift(self):
        deduped = pricing._dedup_session([])

        assert deduped.turns == []
        assert deduped.subagent_spawns == 0
        assert deduped.sidechain_assistant_turns == 0
        assert deduped.usage_drift is False

    def test_sidechain_count_treats_a_truthy_non_bool_flag_as_sidechain(self):
        """The flag is read by truthiness, so a hand-edited or foreign `1` still counts."""
        truthy_non_bool_sidechain = {**_asst(_MODEL), "isSidechain": 1}

        assert pricing._dedup_session([truthy_non_bool_sidechain]).sidechain_assistant_turns == 1

    def test_sidechain_count_includes_usage_less_turns_and_counts_a_merged_run_once(self):
        """Counted over deduplicated turns before any usage filter; a sidechain
        record that is not an assistant record is not a turn."""
        usage_less_sidechain = _asst(_MODEL, sidechain=True)
        merged_sidechain_first = _assistant_record(
            "req-sidechain", uuid="u-s1", ts="2026-05-19T10:00:00.000Z", text="s1",
            usage=_usage(100, 3), sidechain=True,
        )
        merged_sidechain_last = _assistant_record(
            "req-sidechain", uuid="u-s2", ts="2026-05-19T10:00:01.000Z", text="s2",
            usage=_usage(100, 50), sidechain=True,
        )
        sidechain_user = {**_user_msg("sidechain prompt"), "isSidechain": True}
        main_thread_assistant = _asst(_MODEL, sidechain=False)
        records = [
            usage_less_sidechain, merged_sidechain_first, merged_sidechain_last,
            sidechain_user, main_thread_assistant,
        ]

        assert pricing._dedup_session(records).sidechain_assistant_turns == 2


# Spelled out rather than imported from pricing so dropping a key from the
# canary's invariant set fails here instead of silently shrinking coverage.
_INVARIANT_USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _drifting_run() -> list[dict]:
    """A contiguous requestId run whose input_tokens disagree across its records."""
    return [
        _assistant_record(
            "req-drift", uuid="u-1", ts="2026-05-19T10:00:00.000Z", text="a", usage=_usage(100, 3)
        ),
        _assistant_record(
            "req-drift", uuid="u-2", ts="2026-05-19T10:00:01.000Z", text="b", usage=_usage(999, 50)
        ),
    ]


def _clean_ascending_run() -> list[dict]:
    """A contiguous requestId run whose output_tokens ascend, the documented norm."""
    return [
        _assistant_record(
            "req-clean", uuid="u-1", ts="2026-05-19T10:00:00.000Z", text="a", usage=_usage(100, 3)
        ),
        _assistant_record(
            "req-clean", uuid="u-2", ts="2026-05-19T10:00:01.000Z", text="b", usage=_usage(100, 50)
        ),
    ]


class TestUsageDriftReporting:
    def test_dedup_session_reports_drift_after_the_process_warning_already_fired(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(pricing, "_usage_drift_warned", True)

        deduped = pricing._dedup_session(_drifting_run())

        assert deduped.usage_drift is True
        assert capsys.readouterr().err == ""

    def test_dedup_session_reports_no_drift_for_ascending_output_tokens(self):
        assert pricing._dedup_session(_clean_ascending_run()).usage_drift is False

    def test_dedup_session_reports_no_drift_for_a_rejected_non_contiguous_run(self):
        """A non-contiguous run whose invariant usage differs is rejected unmerged, and
        the drift canary reads only merged runs, so it stays clear."""
        rejected_first = _assistant_record(
            "req-split-rejected", uuid="u-r1", ts="2026-05-19T10:00:00.000Z", text="r1", usage=_usage(100, 3)
        )
        user_between = _user_msg("tool result between")
        rejected_last = _assistant_record(
            "req-split-rejected", uuid="u-r2", ts="2026-05-19T10:00:02.000Z", text="r2", usage=_usage(999, 50)
        )

        deduped = pricing._dedup_session([rejected_first, user_between, rejected_last])

        assert deduped.usage_drift is False
        assert deduped.turns == [rejected_first, user_between, rejected_last]

    @pytest.mark.parametrize("drifting_run_first", [True, False], ids=["drifting-first", "drifting-last"])
    def test_dedup_session_reports_drift_whichever_side_of_a_clean_run_the_drifting_run_sits(
        self, drifting_run_first
    ):
        """Drift is accumulated across runs, so a later clean run cannot clear it."""
        runs = [_drifting_run(), _clean_ascending_run()]
        records = [rec for run in (runs if drifting_run_first else runs[::-1]) for rec in run]

        assert pricing._dedup_session(records).usage_drift is True

    @pytest.mark.parametrize("drifting_key", _INVARIANT_USAGE_KEYS)
    def test_dedup_session_reports_drift_on_each_invariant_usage_key(self, drifting_key):
        first = _assistant_record(
            "req-key-drift", uuid="u-1", ts="2026-05-19T10:00:00.000Z", text="a", usage=_usage(100, 3)
        )
        second = _assistant_record(
            "req-key-drift", uuid="u-2", ts="2026-05-19T10:00:01.000Z", text="b",
            usage={**_usage(100, 50), drifting_key: 999},
        )

        assert pricing._dedup_session([first, second]).usage_drift is True

    @pytest.mark.parametrize("drifting_key", _INVARIANT_USAGE_KEYS)
    def test_dedup_session_reports_drift_that_appears_only_on_the_last_record_of_a_run(
        self, drifting_key
    ):
        """Drift on a record after the second is detected, not only drift on run[1]."""
        first = _assistant_record(
            "req-last-drift", uuid="u-1", ts="2026-05-19T10:00:00.000Z", text="a", usage=_usage(100, 3)
        )
        middle = _assistant_record(
            "req-last-drift", uuid="u-2", ts="2026-05-19T10:00:01.000Z", text="b", usage=_usage(100, 20)
        )
        last = _assistant_record(
            "req-last-drift", uuid="u-3", ts="2026-05-19T10:00:02.000Z", text="c",
            usage={**_usage(100, 50), drifting_key: 999},
        )

        assert pricing._dedup_session([first, middle, last]).usage_drift is True

    def test_warn_returns_true_on_every_drifting_call_and_prints_only_the_first(self, capsys):
        first_return = pricing._warn_if_run_usage_drift(_drifting_run())
        first_err = capsys.readouterr().err
        second_return = pricing._warn_if_run_usage_drift(_drifting_run())
        second_err = capsys.readouterr().err

        assert first_return is True
        assert first_err.count("WARNING: requestId") == 1
        assert second_return is True
        assert second_err == ""

    def test_warn_returns_false_for_a_clean_run(self, capsys):
        assert pricing._warn_if_run_usage_drift(_clean_ascending_run()) is False
        assert capsys.readouterr().err == ""

    def test_dedup_prints_the_non_contiguous_notice_before_the_usage_warning(self, capsys):
        """The usage WARNING is emitted at merge time, after the grouping loop has already
        logged every non-contiguous decision -- even when the drifting run comes first."""
        rejected_first = _assistant_record(
            "req-split-differs", uuid="u-d1", ts="2026-05-19T10:02:00.000Z", text="d1", usage=_usage(10, 5)
        )
        rejected_last = _assistant_record(
            "req-split-differs", uuid="u-d2", ts="2026-05-19T10:02:02.000Z", text="d2", usage=_usage(10, 9)
        )
        records = [*_drifting_run(), rejected_first, _user_msg("tool result between"), rejected_last]

        pricing._dedup_session(records)

        err = capsys.readouterr().err
        notice_prefix = "NOTICE: non-contiguous requestId run rejected"
        warning_prefix = "WARNING: requestId"
        assert notice_prefix in err
        assert warning_prefix in err
        assert err.index(notice_prefix) < err.index(warning_prefix)


class TestSubagentFormatDriftReporting:
    def test_returns_true_and_sets_the_flag_for_spawns_with_no_sidechain_turns(self, capsys):
        assert pricing._warn_if_subagent_format_drift(1, 0) is True
        assert pricing._subagent_format_drift_detected is True
        assert "WARNING: subagent spawns detected" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("total_spawns", "total_sidechain_turns"),
        [(0, 0), (2, 1)],
        ids=["no-spawns", "spawns-with-sidechain-turns"],
    )
    def test_returns_false_and_leaves_the_flag_clear_when_the_signature_does_not_match(
        self, total_spawns, total_sidechain_turns, capsys
    ):
        result = pricing._warn_if_subagent_format_drift(total_spawns, total_sidechain_turns)

        assert result is False
        assert pricing._subagent_format_drift_detected is False
        assert capsys.readouterr().err == ""


class TestRateExpired:
    def test_is_false_through_the_expiry_date_and_true_the_day_after(self, monkeypatch):
        expiry = date(2026, 12, 4)
        monkeypatch.setattr(pricing, "_MODEL_RATE_EXPIRES", {"model-x": expiry})

        assert pricing._rate_expired("model-x", date(2026, 12, 3)) is False
        assert pricing._rate_expired("model-x", expiry) is False
        assert pricing._rate_expired("model-x", date(2026, 12, 5)) is True


_SUBAGENT_WARNING = "WARNING: subagent spawns detected"
_BANNER = "PRICING INTEGRITY"
_SESSION_ORDERS = pytest.mark.parametrize(
    ("name_of_session_a", "name_of_session_b"),
    [("sess-1", "sess-2"), ("sess-2", "sess-1")],
    ids=["a-read-first", "a-read-last"],
)


def _spawn_session_records() -> list[dict]:
    return [_asst("claude-opus-4-7", content=[_agent_use("a1", "staff-backend-engineer")])]


class TestCostAccumulatesSubagentCanaryInputsAcrossSessions:
    """cost feeds the subagent canary the totals over every session it reads, so the
    verdict cannot depend on the order sessions are read in."""

    @_SESSION_ORDERS
    def test_spawn_in_one_session_and_sidechain_turn_in_another_is_not_drift(
        self, fake_projects, capsys, name_of_session_a, name_of_session_b
    ):
        _write_jsonl(fake_projects / f"{name_of_session_a}.jsonl", _spawn_session_records())
        _write_jsonl(fake_projects / f"{name_of_session_b}.jsonl", [_user_msg("prompt")])
        _write_subagent_jsonl(
            fake_projects, name_of_session_b, "agent-1", [_priced_sidechain_asst(_MODEL, input_tokens=1_000)]
        )

        cost._cost_report(_cost_args(), date(2026, 8, 2))

        captured = capsys.readouterr()
        assert _SUBAGENT_WARNING not in captured.err
        assert _BANNER not in captured.out

    def test_spawns_in_both_sessions_and_no_sidechain_turns_warns_once(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess-1.jsonl", _spawn_session_records())
        _write_jsonl(fake_projects / "sess-2.jsonl", _spawn_session_records())

        cost._cost_report(_cost_args(), date(2026, 8, 2))

        captured = capsys.readouterr()
        assert captured.err.count(_SUBAGENT_WARNING) == 1
        assert _BANNER in captured.out

    @_SESSION_ORDERS
    def test_spawn_in_one_session_and_nothing_in_the_other_warns_once(
        self, fake_projects, capsys, name_of_session_a, name_of_session_b
    ):
        _write_jsonl(fake_projects / f"{name_of_session_a}.jsonl", _spawn_session_records())
        _write_jsonl(fake_projects / f"{name_of_session_b}.jsonl", [_priced(_MODEL, input=1_000)])

        cost._cost_report(_cost_args(), date(2026, 8, 2))

        captured = capsys.readouterr()
        assert captured.err.count(_SUBAGENT_WARNING) == 1
        assert _BANNER in captured.out
