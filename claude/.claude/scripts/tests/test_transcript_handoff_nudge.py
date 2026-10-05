"""Tests for transcript_analysis/handoff_nudge.py: fire threshold, ramp curve, per-session turn
extraction and scope, and nudge-log parsing, including a contract test against the real hook."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

from ._handoff_nudge_helpers import _ramp_curve_from_records
from .conftest import (
    _asst,
    _priced,
    _priced_sidechain_asst,
    _user_msg,
    _write_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


class TestHookEffectiveFireThreshold:
    def test_200k_window_model_fires_at_40pct_not_the_abs_cap(self):
        """A 200k-context-window model's real fire point is 80,000 (40% of
        its own window) -- well under the 1M-window arm's 150,000 cap, so
        using the cap uniformly for every session would understate how early
        such sessions actually get nudged today."""
        assert _mod.handoff_nudge._hook_effective_fire_threshold("claude-sonnet-4-5") == 80_000

    def test_1m_window_model_fires_at_the_abs_cap_not_40pct(self):
        """A 1M-context-window model's 40% figure (400,000) exceeds
        _HANDOFF_NUDGE_ABS_CAP, so the cap governs instead."""
        assert _mod.handoff_nudge._hook_effective_fire_threshold("claude-sonnet-5") == 150_000


class TestRampCurveFromCorpus:
    def test_turn_index_bucket_edges_match_bands_including_the_gap(self):
        """PR #605's own table never labeled turn index 10-19 (its bands jump
        from "5-10" to "20-40"); the cascading less-than lookup this reuses
        from _EDIT_OLD_STRING_SIZE_BUCKETS' own convention folds that range
        into "20-40" rather than leaving it unbucketed."""
        cases = {
            0: "0-5", 4: "0-5",
            5: "5-10", 9: "5-10",
            10: "20-40", 19: "20-40", 39: "20-40",
            40: "40-80", 79: "40-80",
            80: "80-150", 149: "80-150",
            150: "150-300", 299: "150-300",
            300: "300+", 1000: "300+",
        }
        for turn_index, expected_label in cases.items():
            assert _mod.handoff_nudge._ramp_curve_turn_index_bucket(turn_index) == expected_label, turn_index

    def test_sane_rate_and_mean_context_on_synthetic_corpus_with_known_growth(self):
        """A two-turn session with known input/output/context, both turns
        landing in the '0-5' bucket, produces a hand-computed $/1k-output
        rate and output-token-weighted mean context -- not a bounds check."""
        recs = [
            _priced("claude-sonnet-5", input=100_000, output=1000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=200_000, output=3000, ts="2026-05-19T10:01:00.000Z"),
        ]
        curve, total_output_tokens = _ramp_curve_from_records(recs)
        rates = _mod._model_rates("claude-sonnet-5")
        turn1_dollars = 100_000 / 1_000_000 * rates["input"] + 1000 / 1_000_000 * rates["output"]
        turn2_dollars = 200_000 / 1_000_000 * rates["input"] + 3000 / 1_000_000 * rates["output"]
        expected_rate = (turn1_dollars + turn2_dollars) / ((1000 + 3000) / 1000)
        expected_mean_context = (100_000 * 1000 + 200_000 * 3000) / (1000 + 3000)
        assert curve["0-5"]["rate"] == pytest.approx(expected_rate)
        assert curve["0-5"]["mean_context"] == pytest.approx(expected_mean_context)
        assert total_output_tokens == 1000 + 3000

    def test_bucket_with_zero_turns_falls_back_to_corpus_wide_rate_not_nan(self):
        """A corpus with data only in the '0-5' bucket still returns a
        defined, non-NaN rate/mean_context for a bucket with zero turns
        (e.g. '300+'), equal to the corpus-wide rate/context since '0-5' is
        the only contributing bucket -- not a division-by-zero or NaN
        propagating into _simulate_rearm_spacing."""
        recs = [_priced("claude-sonnet-5", input=100_000, output=1000)]
        curve, _total_output_tokens = _ramp_curve_from_records(recs)
        assert curve["300+"]["rate"] == pytest.approx(curve["0-5"]["rate"])
        assert curve["300+"]["mean_context"] == pytest.approx(curve["0-5"]["mean_context"])

    def test_whole_corpus_unpriced_reports_zero_total_output_tokens(self):
        """A corpus whose only turns are on an unpriced model can't compute
        a real ramp curve at all -- total_output_tokens is 0, letting a
        caller (_rearm_backtest_report) tell "genuinely cheap ramp" apart
        from "curve couldn't be computed", which every bucket's own
        rate/mean_context (both silently 0.0 here) can't distinguish."""
        recs = [_priced("claude-opus-4-7", input=100_000, output=1000)]  # unpriced model
        curve, total_output_tokens = _ramp_curve_from_records(recs)
        assert total_output_tokens == 0
        assert curve["0-5"]["rate"] == 0.0


class TestParseNudgeLogEntries:
    def test_all_three_line_shapes_are_parsed(self, tmp_path):
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=abc123 est=400000 model=claude-opus-5 window=1000000 event=Stop\n"
            "schema-drift session=def456 event=UserPromptSubmit\n"
            "handoff session=abc123\n"
        )
        assert _mod.handoff_nudge._parse_nudge_log_entries(log_path) == [
            {"kind": "nudged", "session": "abc123", "est": 400000, "model": "claude-opus-5",
             "window": 1000000, "event": "Stop"},
            {"kind": "schema-drift", "session": "def456", "event": "UserPromptSubmit"},
            {"kind": "handoff", "session": "abc123"},
        ]

    def test_malformed_lines_are_skipped_without_raising(self, tmp_path):
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "not a recognized line at all\n"
            "nudged session=abc est=not-an-int model=x window=1000000 event=Stop\n"
            "nudged session=abc est=400000 model=x window=1000000\n"  # missing event=
            "nudged session=abc bare-token-no-equals est=400000 model=x window=1000000 event=Stop\n"
            "nudged session=abc est=400000 model=x window=1000000 event=Stop\n"  # valid
        )
        assert _mod.handoff_nudge._parse_nudge_log_entries(log_path) == [
            {"kind": "nudged", "session": "abc", "est": 400000, "model": "x", "window": 1000000, "event": "Stop"},
        ]

    def test_missing_log_file_returns_empty_list(self, tmp_path):
        assert _mod.handoff_nudge._parse_nudge_log_entries(tmp_path / "does-not-exist.log") == []

    def test_action_block_field_is_captured_when_present(self, tmp_path):
        """A hard-block fire's nudged line carries action=block -- captured
        as an absent-key-by-default field, not an always-present None, so
        an ordinary advisory line (no action= token) produces a dict with no
        "action" key at all, matching test_all_three_line_shapes_are_parsed's
        exact-equality assertion above."""
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=abc est=400000 model=x window=1000000 event=PostToolBatch action=block\n"
        )
        assert _mod.handoff_nudge._parse_nudge_log_entries(log_path) == [
            {"kind": "nudged", "session": "abc", "est": 400000, "model": "x",
             "window": 1000000, "event": "PostToolBatch", "action": "block"},
        ]

    def test_ignored_and_skills_fields_are_captured_when_present(self, tmp_path):
        """A telemetry-era nudged line carries ignored=/skills= -- captured
        as typed fields (ignored as int, skills as the raw comma-joined
        string), the same optional-key style action= already uses."""
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=abc est=400000 model=x window=1000000 event=PostToolBatch "
            "ignored=3 skills=handoff,memory-skill action=block\n"
        )
        assert _mod.handoff_nudge._parse_nudge_log_entries(log_path) == [
            {"kind": "nudged", "session": "abc", "est": 400000, "model": "x",
             "window": 1000000, "event": "PostToolBatch", "action": "block",
             "ignored": 3, "skills": "handoff,memory-skill"},
        ]

    def test_pre_telemetry_line_parses_without_ignored_or_skills_keys(self, tmp_path):
        """A `nudged` line written before the ignored=/skills= telemetry
        addition carries neither field -- the returned dict has no
        "ignored" or "skills" key at all, distinguishable from a live
        session with nothing active (skills=-, ignored=0) rather than
        conflated with it."""
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=abc est=400000 model=x window=1000000 event=Stop\n"
        )
        entries = _mod.handoff_nudge._parse_nudge_log_entries(log_path)
        assert "ignored" not in entries[0]
        assert "skills" not in entries[0]

    def test_malformed_ignored_field_drops_only_that_key(self, tmp_path):
        """A non-integer ignored= value doesn't discard the whole entry --
        only the "ignored" key is left unset, matching how a pre-telemetry
        line (missing the key entirely) is already handled. skills= is
        unaffected, confirming the malformed field is isolated from its
        sibling."""
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=abc est=400000 model=x window=1000000 event=Stop "
            "ignored=not-an-int skills=-\n"
        )
        entries = _mod.handoff_nudge._parse_nudge_log_entries(log_path)
        assert len(entries) == 1
        assert "ignored" not in entries[0]
        assert entries[0]["skills"] == "-"


class TestParseNudgeLogEntriesRealHookLineContract:
    """Fires the real nudge-handoff-near-context-cap.sh hook and feeds its
    emitted `nudged` line straight into _parse_nudge_log_entries, rather than
    a hand-written fixture line on each side -- a field-ordering or delimiter
    drift between the hook's printf format and this parser could otherwise
    pass both suites while breaking the real pipeline."""

    _NUDGE_HOOK = HOOKS_DIR / "nudge-handoff-near-context-cap.sh"

    @staticmethod
    def _usage_record(total: int, *, model: str = "claude-sonnet-5") -> dict:
        """An assistant record whose four usage fields sum to `total`,
        matching nudge-handoff-near-context-cap.sh's own ESTIMATE
        computation (cache_read + cache_creation + input + output tokens)."""
        rec = _asst(model)
        rec["message"]["usage"] = {
            "cache_read_input_tokens": total,
            "cache_creation_input_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        return rec

    def _fire(self, tmp_path: Path, transcript: Path, env: dict) -> subprocess.CompletedProcess:
        payload = {
            "session_id": "contract-session",
            "transcript_path": str(transcript),
            "hook_event_name": "PostToolBatch",
        }
        return subprocess.run(
            [str(self._NUDGE_HOOK)], input=json.dumps(payload),
            capture_output=True, text=True, env=env, check=False,
        )

    def test_real_hard_block_line_parses_with_ignored_and_skills_correctly_typed(self, tmp_path):
        # claude-sonnet-5's 1M window caps its threshold at
        # HANDOFF_NUDGE_ABS_CAP's shipped default (150000); block_at is one
        # rearm-spacing hop past that, so the second fire is both a qualifying
        # rearm and past the block point.
        threshold = 150_000
        block_at = threshold + 80_000
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        for var in ("HANDOFF_NUDGE_ABS_CAP", "HANDOFF_NUDGE_REARM_SPACING", "HANDOFF_NUDGE_BLOCK_AFTER"):
            env.pop(var, None)
        env["HANDOFF_NUDGE_BLOCK_AT"] = str(block_at)

        transcript = tmp_path / "t.jsonl"
        _write_jsonl(transcript, [self._usage_record(threshold)])
        first = self._fire(tmp_path, transcript, env)
        assert first.returncode == 0  # first-ever crossing: always advisory

        with transcript.open("a") as f:
            f.write(json.dumps(self._usage_record(block_at)) + "\n")
        second = self._fire(tmp_path, transcript, env)
        assert second.returncode == 2, f"estimate reaches HANDOFF_NUDGE_BLOCK_AT={block_at}"

        log_path = tmp_path / ".claude" / ".handoff-nudge.log"
        nudged_lines = [line for line in log_path.read_text().splitlines() if line.startswith("nudged")]
        # Pre-parse sanity tripwire on the raw log line; the parser-based
        # assertions below are what actually validate the contract.
        assert nudged_lines[-1].endswith("action=block")

        entries = _mod.handoff_nudge._parse_nudge_log_entries(log_path)
        block_entry = entries[-1]
        assert block_entry["action"] == "block"
        assert block_entry["ignored"] == 1
        assert isinstance(block_entry["ignored"], int)
        assert block_entry["skills"] == "-"


class TestOperatorResponseLagFromLog:
    def test_exact_match_join_measures_lag_past_the_fire_point(self):
        session_traces = {"abc": [100, 200, 405_000, 410_000]}
        log_entries = [
            {"kind": "nudged", "session": "abc", "est": 405_000, "model": "x", "window": 1_000_000, "event": "Stop"},
        ]
        lags, excluded = _mod.handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
        assert lags == [410_000 - 405_000]
        assert excluded == 0

    def test_no_match_is_excluded_and_counted_not_silently_dropped(self):
        session_traces = {"abc": [100, 200]}
        log_entries = [
            {"kind": "nudged", "session": "does-not-exist", "est": 100, "model": "x", "window": 1, "event": "Stop"},
        ]
        lags, excluded = _mod.handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
        assert lags == []
        assert excluded == 1

    def test_first_value_at_or_above_est_is_picked_over_an_earlier_below_est_value(self):
        """A nudged line carries no timestamp, only est= -- the join skips
        300 (below est=400, however close) and picks 500 (index 2), the
        trace's first value that actually reaches est. This fixture's peak-
        from-fire-point-onward happens to land on the same lag either way a
        fire index is chosen here (the suffix's max value dominates
        regardless of start point), so it does not by itself distinguish
        first-crossing from a nearest-value join -- see
        test_post_compaction_dip_does_not_mis_join_to_a_later_closer_looking_turn
        for the fixture that actually pins that distinction, since a
        same-lag result requires the higher peak to be reachable from every
        candidate start point, which a monotonically non-decreasing trace
        (like this one) always satisfies."""
        session_traces = {"s": [100, 300, 500]}
        log_entries = [{"kind": "nudged", "session": "s", "est": 400, "model": "x", "window": 1, "event": "Stop"}]
        lags, excluded = _mod.handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
        # First value >= est is 500 (index 2); peak at or after it is 500,
        # so lag = 500 - 400 = 100.
        assert lags == [100]
        assert excluded == 0

    def test_no_trace_value_reaches_est_is_excluded_not_crashing(self):
        """A trace that never reaches the logged est (e.g. a truncated or
        mismatched transcript) can't identify a fire turn -- excluded and
        counted, not a false join to whichever value happens to be closest."""
        session_traces = {"s": [100, 200, 300]}
        log_entries = [{"kind": "nudged", "session": "s", "est": 400, "model": "x", "window": 1, "event": "Stop"}]
        lags, excluded = _mod.handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
        assert lags == []
        assert excluded == 1

    def test_hard_block_entry_is_excluded_and_counted_not_measured_as_lag(self):
        """A hard-block fire's overshoot is forced by the block itself, not
        the voluntary operator-response lag this function measures -- an
        action=block entry is excluded from the lag population and counted,
        even though its session_id joins and its trace does reach est."""
        session_traces = {"abc": [100, 200, 405_000, 410_000]}
        log_entries = [
            {"kind": "nudged", "session": "abc", "est": 405_000, "model": "x",
             "window": 1_000_000, "event": "PostToolBatch", "action": "block"},
        ]
        lags, excluded = _mod.handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
        assert lags == []
        assert excluded == 1

    def test_post_compaction_dip_does_not_mis_join_to_a_later_closer_looking_turn(self):
        """A mid-session isCompactSummary drop can produce a later turn
        whose abs-token value is numerically closer to est than the true,
        earlier first-crossing turn -- the join must still land on the first
        turn that actually reaches est, not the nearest-looking one after
        the dip."""
        session_traces = {"abc": [100, 450_000, 900_000, 60_000, 200_000, 449_000]}
        log_entries = [
            {"kind": "nudged", "session": "abc", "est": 400_000, "model": "x", "window": 1, "event": "Stop"},
        ]
        lags, excluded = _mod.handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
        # True first crossing is index 1 (450_000 >= est); the peak at or
        # after it is 900_000, so lag = 900_000 - 400_000 = 500_000. A
        # nearest-est join would instead pick index 5 (449_000, closer to
        # est than 450_000 is) and understate the lag to 49_000.
        assert lags == [500_000]
        assert excluded == 0


class TestSessionMatchesRearmScope:
    """--since and --branches scope whole sessions here (unlike `cost`'s
    per-record --branches filter) -- see _session_matches_rearm_scope's own
    docstring for why."""

    def test_session_excluded_when_first_timestamp_is_before_since_cutoff(self):
        since_ts = _mod._parse_ts("2026-05-10T00:00:00.000Z")
        records = [_asst("claude-sonnet-5", ts="2026-05-01T00:00:00.000Z")]
        assert _mod.handoff_nudge._session_matches_rearm_scope(records, since_ts, None) is False

    def test_session_included_when_first_timestamp_is_exactly_at_the_since_boundary(self):
        since_ts = _mod._parse_ts("2026-05-10T00:00:00.000Z")
        records = [_asst("claude-sonnet-5", ts="2026-05-10T00:00:00.000Z")]
        assert _mod.handoff_nudge._session_matches_rearm_scope(records, since_ts, None) is True

    def test_session_excluded_when_no_main_thread_turn_matches_branches(self):
        records = [_asst("claude-sonnet-5", branch="other")]
        assert _mod.handoff_nudge._session_matches_rearm_scope(records, None, {"main"}) is False

    def test_session_excluded_when_only_a_sidechain_turn_matches_the_branch_filter(self):
        """A sidechain turn sharing the target branch name must not count --
        --branches scopes to main-thread turns only, matching the
        `not bool(r.get("isSidechain"))` guard."""
        records = [_asst("claude-sonnet-5", branch="main", sidechain=True)]
        assert _mod.handoff_nudge._session_matches_rearm_scope(records, None, {"main"}) is False

    def test_session_included_when_branch_changes_mid_session_and_only_some_turns_match(self):
        records = [
            _asst("claude-sonnet-5", branch="other"),
            _asst("claude-sonnet-5", branch="main"),
        ]
        assert _mod.handoff_nudge._session_matches_rearm_scope(records, None, {"main"}) is True


class TestExtractRearmSessionTurnsModelAndPosition:
    """main_thread_models / main_thread_record_positions are new parallel
    lists alongside main_thread_turns, not a widening of its own 3-tuple
    shape -- _ramp_curve_from_corpus, _simulate_rearm_spacing, and
    _rearm_backtest_report all positionally unpack that tuple as
    Sequence[tuple[int, int, float]]."""

    def test_models_and_positions_are_parallel_to_main_thread_turns(self):
        records = [
            _priced("claude-opus-5", input=100, output=50, ts="2026-05-19T10:00:00.000Z"),
            _user_msg("go", ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-sonnet-5", input=200, output=75, ts="2026-05-19T10:00:02.000Z"),
        ]
        data = _mod.handoff_nudge._extract_rearm_session_turns(records)
        assert data["main_thread_models"] == ["claude-opus-5", "claude-sonnet-5"]
        assert len(data["main_thread_record_positions"]) == len(data["main_thread_turns"])
        for turn_index, record_index in enumerate(data["main_thread_record_positions"]):
            rec = data["deduped"][record_index]
            assert rec["message"]["model"] == data["main_thread_models"][turn_index]

    def test_record_positions_skip_sidechain_and_no_usage_records(self):
        """A no-usage synthetic record and a sidechain turn both advance
        "deduped"'s own index but must not appear in
        main_thread_record_positions -- that list only indexes usage-carrying
        main-thread turns, mirroring _hook_observable_boundaries' own
        desync guard."""
        records = [
            _asst("claude-opus-5", ts="2026-05-19T10:00:00.000Z"),  # no usage block
            _priced_sidechain_asst("claude-opus-5", output_tokens=10, ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", input=100, output=50, ts="2026-05-19T10:00:02.000Z"),
        ]
        data = _mod.handoff_nudge._extract_rearm_session_turns(records)
        assert data["main_thread_record_positions"] == [2]
