"""Tests for transcript_analysis/context_distribution.py's cmd_context_distribution: threshold
crossings and dollar shares in the percentage and absolute-token tables, main-thread-only peaks,
requestId dedup, the --no-redact refusal and banner, the empty scope, and the pricing.py and
render.py helpers it reads.
"""
import pytest
from transcript_analysis import context_distribution, pricing, render, scope

from .conftest import (
    _asst,
    _context_distribution_args,
    _priced,
    _user_msg,
    _write_jsonl,
    _write_subagent_jsonl,
)

_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER = "## Peak absolute-token crossing thresholds"


def _extract_context_distribution_row(out: str, pct: int) -> dict[str, str]:
    """Read one context-distribution percentage-table threshold row
    ('Threshold Sessions SessShare $ DollarShare') by its leading 'NN%'
    token, rather than _table_cols' row_contains, since every row's leading
    token is a candidate substring of another row's trailing percentage
    columns. Scoped to the output before the absolute-token table's own
    header, so an absolute threshold's leading token (a plain digit, never a
    percentage) can't be misread as a percentage row by coincidence."""
    section = out.split(_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER, 1)[0]
    target = f"{pct}%"
    for line in section.splitlines():
        tokens = line.split()
        if tokens and tokens[0] == target:
            return {
                "sessions": tokens[1],
                "sess_share": tokens[2],
                "dollars": tokens[3],
                "dollar_share": tokens[4],
            }
    raise AssertionError(f"threshold row for {pct}% not found in output:\n{out}")


def _extract_context_distribution_abs_row(out: str, threshold: int) -> dict[str, str]:
    """Read one context-distribution absolute-token-table threshold row by
    its leading comma-formatted token (e.g. '80,000'). Scoped to the output
    at/after the absolute table's own header, the section-boundary
    counterpart of _extract_context_distribution_row above."""
    if _CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER not in out:
        raise AssertionError(f"absolute-token table header not found in output:\n{out}")
    section = out.split(_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER, 1)[1]
    target = f"{threshold:,}"
    for line in section.splitlines():
        tokens = line.split()
        if tokens and tokens[0] == target:
            return {
                "sessions": tokens[1],
                "sess_share": tokens[2],
                "dollars": tokens[3],
                "dollar_share": tokens[4],
            }
    raise AssertionError(f"absolute threshold row for {threshold:,} not found in output:\n{out}")


class TestContextDistribution:
    def test_threshold_crossing_and_dollar_share_hand_computed(self, fake_projects, capsys):
        """Three sessions with distinct peak context-at-turn percentages of
        claude-sonnet-5's 1M default window (65%, 45%, 10%) cross a different
        subset of the 30/40/50/60% thresholds — crossing-count and
        dollar-share are verified against hand-computed expected values at
        every threshold, not just one."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=650_000),  # peak 65%, $1.30
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=450_000),  # peak 45%, $0.90
        ])
        _write_jsonl(fake_projects / "sess-c.jsonl", [
            _priced("claude-sonnet-5", input=100_000),  # peak 10%, $0.20
        ])
        context_distribution._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out

        assert "Sessions in scope: 3" in out
        assert "Total priced dollars: 2.40" in out

        # 30%/40%: sessions A and B cross (65% and 45% both >= threshold);
        # dollars = 1.30 + 0.90 = 2.20; share = 2.20 / 2.40 = 91.7%.
        for pct in (30, 40):
            row = _extract_context_distribution_row(out, pct)
            assert row["sessions"] == "2"
            assert row["dollars"] == "2.20"
            assert row["dollar_share"] == "91.7%"

        # 50%/60%: only session A crosses (65% >= threshold, 45% does not);
        # dollars = 1.30; share = 1.30 / 2.40 = 54.2%.
        for pct in (50, 60):
            row = _extract_context_distribution_row(out, pct)
            assert row["sessions"] == "1"
            assert row["dollars"] == "1.30"
            assert row["dollar_share"] == "54.2%"

    def test_peak_exactly_at_threshold_counts_as_crossing(self, fake_projects, capsys):
        """A session whose peak lands exactly on a candidate threshold (not
        strictly above it) still counts as crossing — the comparison is
        >=, mirroring the nudge hook's own 'fires at exactly threshold'
        test for its single threshold. Regression guard for a future
        float-rounding change silently flipping >= to >."""
        _write_jsonl(fake_projects / "sess-exact.jsonl", [
            _priced("claude-sonnet-5", input=400_000),  # peak exactly 40% of 1M
        ])
        context_distribution._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        row_40 = _extract_context_distribution_row(out, 40)
        assert row_40["sessions"] == "1"

    def test_sidechain_turns_excluded_from_peak_context_tracking(self, fake_projects, capsys):
        """A session's peak is computed from main-thread turns only — a deep
        sidechain (subagent) turn must not count toward the session's own
        handoff-relevant peak, since subagent context isn't shared with the
        parent and doesn't accumulate toward a /handoff decision."""
        session_id = "sess-side"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=100_000),  # main: peak 10%
        ])
        subagent_rec = _priced("claude-sonnet-5", input=900_000)  # sidechain: would-be peak 90%
        subagent_rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])

        context_distribution._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        # Session's own peak stays 10% (main-thread only) — crosses no threshold.
        row_30 = _extract_context_distribution_row(out, 30)
        assert row_30["sessions"] == "0"
        # But dollars still sum main + sidechain: $0.20 (main, 100k input) +
        # $1.80 (sidechain, 900k input) = $2.00 — a bug that coupled the
        # sidechain exclusion to the dollar total too (instead of only the
        # peak) would silently drop the sidechain's $1.80 here.
        assert "Total priced dollars: 2.00" in out

    def test_multi_record_request_id_group_priced_exactly_once(self, fake_projects, capsys):
        """Three assistant records sharing one requestId (one JSONL record per
        content block, as Claude Code writes for a single API call) carry a
        byte-identical usage dict — context-distribution prices the group's
        usage once, not once per record."""
        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        for rec in recs:
            rec["message"]["usage"] = dict(usage)
        _write_jsonl(fake_projects / "sess.jsonl", recs)
        context_distribution._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        # $2.00 (claude-sonnet-5's $2/MTok input rate on 1M input tokens) once,
        # not $6.00 for pricing the group's usage three times over.
        assert "Total priced dollars: 2.00" in out

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """--no-redact is refused when --config-dir puts more than one root in
        scope, mirroring cost's own refusal exactly."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            context_distribution.cmd_context_distribution(
                _context_distribution_args(no_redact=True, extra_config_dirs=[str(acct_b)])
            )
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root(self, fake_projects, capsys):
        """--no-redact with no --config-dir (single root) is unaffected — it
        prints the DO NOT PUBLISH banner but does not exit, the allow-path
        counterpart to the multi-root refusal above."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=100_000)])
        context_distribution._context_distribution_report(_context_distribution_args(no_redact=True))
        out = capsys.readouterr().out
        assert scope._DO_NOT_PUBLISH_BANNER in out
        assert "Sessions in scope: 1" in out

    def test_no_sessions_in_scope_prints_zero_without_division_error(self, fake_projects, capsys):
        """An empty scope (no priced or main-thread turns anywhere) prints a
        zero-sessions summary and 0.0% shares rather than raising
        ZeroDivisionError — in both the percentage table and its absolute-token
        sibling, since both share the same _pct_of-guarded arithmetic."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        context_distribution._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        assert "Sessions in scope: 0" in out
        row_30 = _extract_context_distribution_row(out, 30)
        assert row_30["sessions"] == "0"
        assert row_30["dollar_share"] == "0.0%"
        row_abs = _extract_context_distribution_abs_row(out, 80_000)
        assert row_abs["sessions"] == "0"
        assert row_abs["dollar_share"] == "0.0%"

    def test_unpriced_model_still_contributes_to_absolute_bucket(self, fake_projects, capsys):
        """A model ID absent from _MODEL_BASE_INPUT_RATES prices at $0, but its
        turns still feed the absolute-token peak — that path computes from
        context_at_turn + output_tokens directly, and its value doesn't depend
        on _context_window_for_model's result, unlike the percentage path."""
        _write_jsonl(fake_projects / "sess-unpriced.jsonl", [
            _priced("claude-opus-4-7", input=250_000),  # unpriced model; crosses the 250,000 bucket
        ])
        context_distribution._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        row = _extract_context_distribution_abs_row(out, 250_000)
        assert row["sessions"] == "1"
        assert row["dollars"] == "0.00"

    def test_mixed_window_session_argmax_differs_between_pct_and_abs_peak(self, fake_projects, capsys):
        """A session with one turn on a 200k-window model (high percentage,
        lower absolute tokens) and one turn on a 1M-window model (lower
        percentage, higher absolute tokens) — the percentage table's peak and
        the absolute table's peak come from different turns, pinning that
        peak_abs_tokens is its own tracked maximum, not peak_pct * window."""
        _write_jsonl(fake_projects / "sess-mixed.jsonl", [
            _priced("claude-sonnet-4-5", input=190_000),  # 200k model: 95% of window, abs 190,000
            _priced("claude-sonnet-5", input=300_000),  # 1M model: 30% of window, abs 300,000
        ])
        context_distribution._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        row_60 = _extract_context_distribution_row(out, 60)
        assert row_60["sessions"] == "1"  # peak_pct 95%, from the 200k turn
        row_250k = _extract_context_distribution_abs_row(out, 250_000)
        assert row_250k["sessions"] == "1"  # peak_abs_tokens 300,000, from the 1M turn
        row_400k = _extract_context_distribution_abs_row(out, 400_000)
        assert row_400k["sessions"] == "0"  # peak_abs_tokens is 300,000, not higher

    def test_absolute_peak_accumulator_flat_and_nested_cache_creation_agree(self):
        """One turn's cache_creation expressed via the hook's flat
        cache_creation_input_tokens field and an equal-total turn expressed via
        the nested ephemeral_1h/ephemeral_5m split feed the new absolute-peak
        accumulator (_session_peak_context) to the identical total — pinning
        the _cache_write_split equivalence the accumulator leans on, rather
        than only citing it."""
        flat_usage = _priced(
            "claude-sonnet-5", input=100_000, cache_read=50_000, output=5_000, flat_cache_creation=30_000
        )["message"]["usage"]
        nested_usage = _priced(
            "claude-sonnet-5", input=100_000, cache_read=50_000, output=5_000,
            ephemeral_1h=10_000, ephemeral_5m=20_000,
        )["message"]["usage"]

        _dollars_flat, context_flat, _unpriced_flat = pricing._price_turn("claude-sonnet-5", flat_usage)
        _dollars_nested, context_nested, _unpriced_nested = pricing._price_turn("claude-sonnet-5", nested_usage)

        _peak_pct_flat, peak_abs_flat = pricing._session_peak_context([(context_flat, 5_000, 1_000_000)])
        _peak_pct_nested, peak_abs_nested = pricing._session_peak_context([(context_nested, 5_000, 1_000_000)])

        expected = 100_000 + 50_000 + 30_000 + 5_000
        assert peak_abs_flat == peak_abs_nested == expected

    def test_session_peak_context_tracks_independent_maxima(self):
        """_session_peak_context's two maxima are independent — the turn with
        the highest percentage of its own window need not be the turn with the
        highest absolute token count."""
        turns = [
            (190_000, 0, 200_000),  # pct 95%, abs 190,000
            (300_000, 0, 1_000_000),  # pct 30%, abs 300,000
        ]
        peak_pct, peak_abs_tokens = pricing._session_peak_context(turns)
        assert peak_pct == pytest.approx(0.95)
        assert peak_abs_tokens == 300_000

    def test_session_peak_context_abs_includes_output_tokens(self):
        """peak_abs_tokens is context_at_turn + output_tokens — the hook's own
        four-field ESTIMATE sum — not context_at_turn alone; peak_pct is
        unaffected by output_tokens, matching context_at_turn's own definition."""
        turns = [(100_000, 50_000, 1_000_000)]
        peak_pct, peak_abs_tokens = pricing._session_peak_context(turns)
        assert peak_pct == pytest.approx(0.1)
        assert peak_abs_tokens == 150_000

    def test_session_peak_context_no_main_thread_turns_returns_zero(self):
        """No main-thread turns (e.g. a session with only sidechain activity)
        yields (0.0, 0) rather than raising."""
        assert pricing._session_peak_context([]) == (0.0, 0)

    def test_context_distribution_rows_session_share_hand_computed(self):
        """_context_distribution_rows' session-share arithmetic, exercised
        directly on a synthetic multi-session fixture with a known expected
        fraction — a handoff-nudge cap chosen off this report's session-share
        column is only as trustworthy as this arithmetic."""
        peaks = [0.9, 0.5, 0.3, 0.1, 0.05]
        dollars = [1.0, 1.0, 1.0, 1.0, 1.0]
        rows = render._context_distribution_rows([0.4], peaks, dollars)
        # 2 of 5 sessions (0.9, 0.5) cross 0.4 -> 40.0% session-share.
        assert rows[0]["sessions"] == 2
        assert rows[0]["session_share"] == "40.0%"
        assert rows[0]["dollars"] == pytest.approx(2.0)
        assert rows[0]["dollar_share"] == "40.0%"
