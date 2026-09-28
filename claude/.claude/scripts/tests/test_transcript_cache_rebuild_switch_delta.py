"""Tests for transcript_analysis/cache_rebuild.py (cmd_cache_rebuild): 5m-to-1h switch-delta pricing, display rounding,
and per-dispatch dispersion."""
import importlib.util
import math
import re
import sys
from pathlib import Path

import pytest

from ._cache_rebuild_helpers import (
    _cache_rebuild_args,
    _extract_cache_rebuild_dispersion,
    _extract_cache_rebuild_row,
    _extract_cache_rebuild_summary,
)
from .conftest import (
    _priced,
    _table_cols,
    _write_jsonl,
    _write_subagent_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


class TestCacheRebuildSwitchDeltaPricing:
    """Direct unit coverage for _cache_rebuild_switch_delta_dollars, mirroring
    TestCacheRebuildExcessPricing's three cases -- the new helper's own
    fast-mode/US-geo/unpriced-model parity is exercised nowhere else, since
    the pooled-report fixtures below use only claude-sonnet-5 with no
    speed/inference_geo variation."""

    def test_fast_mode_multiplier_applies_to_both_switch_cost_and_rescue_legs(self):
        """Mirrors _price_turn's own fast-mode multiplier: a wholly
        idle-5m-1h-cause call's switch delta is the negation of that same
        call's own _cache_rebuild_excess_dollars value (write $5.00, warm
        read $0.40, excess $4.60 -- see TestCacheRebuildExcessPricing's own
        fast-mode case)."""
        usage = _priced("claude-sonnet-5", ephemeral_5m=1_000_000, speed="fast")["message"]["usage"]
        delta, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_switch_delta_dollars(
            "claude-sonnet-5", usage, is_idle_5m_1h_cause=True
        )
        assert delta == pytest.approx(-4.60)
        assert unpriced_tokens == 0

    def test_unpriced_model_returns_none_delta_not_a_silent_zero(self):
        """A model absent from _MODEL_BASE_INPUT_RATES must not silently
        price its delta as $0 -- callers distinguish 'unpriced' from 'priced
        at zero' via the None sentinel, matching _price_turn's own
        unpriced-model contract."""
        usage = _priced("claude-unknown-model", ephemeral_5m=1_000_000, input=10, output=5)["message"]["usage"]
        delta, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_switch_delta_dollars(
            "claude-unknown-model", usage, is_idle_5m_1h_cause=True
        )
        assert delta is None
        assert unpriced_tokens > 0

    def test_fable_5_1_uses_reduced_cache_read_multiplier_not_hardcoded_1_9(self):
        """A hardcoded (2 - 0.1) = 1.9 coefficient would price this call's
        rescue leg at 1,000,000/1e6 * 10.00*1.9 = 19.00, giving a delta of
        -11.50; the correct, per-model-resolved coefficient uses Fable
        5.1's own reduced 0.025x cache-read multiplier ((2 - 0.025) = 1.975,
        rescue $19.75), giving -12.25 -- identical to that same call's own
        _cache_rebuild_excess_dollars value."""
        usage = _priced("claude-fable-5-1", ephemeral_5m=1_000_000)["message"]["usage"]
        delta, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_switch_delta_dollars(
            "claude-fable-5-1", usage, is_idle_5m_1h_cause=True
        )
        assert delta == pytest.approx(-12.25)
        assert unpriced_tokens == 0


class TestCacheRebuildNegateSwitchDeltaForDisplay:
    """Direct unit coverage for _negate_switch_delta_for_display's own
    sign-bit guard -- exercised elsewhere only indirectly, through a full
    report run and a regex-parsed "Net$" cell
    (test_exact_break_even_boundary_nets_zero_and_the_strict_inequality_holds)."""

    def test_a_tiny_positive_residual_negates_to_positive_zero_not_negative_zero(self):
        """A +1e-16 floating-point residual at an exact break-even point
        must not print as the misleading "-0.00" once negated: round(0.0 -
        1e-16, 2) underflows to -0.0 without the function's own + 0.0
        guard."""
        result = _mod.cache_rebuild_rules._negate_switch_delta_for_display(1e-16)
        assert result == 0.0
        assert math.copysign(1.0, result) == 1.0

    def test_negates_and_rounds_a_genuine_nonzero_delta(self):
        """-4.567 mirrors the sign _cache_rebuild_switch_delta_dollars
        actually accumulates (see TestCacheRebuildSwitchDeltaPricing's own
        negative deltas); negation makes it a positive savings figure."""
        assert _mod.cache_rebuild_rules._negate_switch_delta_for_display(-4.567) == pytest.approx(4.57)


class TestCacheRebuildSwitchDeltaThresholdIndependence:
    """Verification item 3: W5m and X must be threshold-independent -- both
    accumulate over every in-scope call, not just tail (>= threshold) ones."""

    def test_sub_threshold_calls_count_toward_w5m_and_x_but_stay_invisible_in_tail_cause_table(
        self, fake_projects, capsys
    ):
        """Three sub-threshold subagent calls: none crosses the 100,000-token
        tail threshold, so none appears in the cause-breakdown table or the
        'Calls writing >= ... tokens' tail count -- but their own 5m-tier
        write tokens still accumulate into W5m, and the one classified idle
        5m-1h still accumulates into X. The likelier implementer mistake
        this pins: gating X's own accumulation on the same in_tail check
        that gates the cause table, rather than accumulating it
        unconditionally."""
        records = [
            _priced("claude-sonnet-5", ephemeral_5m=10_000, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            # 400s gap -- idle 5m-1h, but sub-threshold (30,000 < 100,000).
            _priced(
                "claude-sonnet-5", ephemeral_5m=30_000,
                ts="2026-08-01T10:06:40.000Z", request_id="sub-2",
            ),
            # 10s gap -- not idle, also sub-threshold.
            _priced(
                "claude-sonnet-5", ephemeral_5m=20_000,
                ts="2026-08-01T10:06:50.000Z", request_id="sub-3",
            ),
        ]
        for rec in records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_cache_rebuild_summary(out)
        assert summary["tail"] == "0"
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "session start")[0] == 0

        subagent_row = _table_cols(out, header_contains="Ratio", row_contains="subagent")
        assert subagent_row["W5m"] == "60,000"
        assert subagent_row["X"] == "30,000"


class TestCacheRebuildSwitchDeltaNumeratorBoundaries:
    """Verification item 4: X excludes idle >1h (a 1-hour cache is also
    cold past 3600s) and pure-1h-tier writes (can't have been forced by a
    <1h gap) -- extends TestCacheRebuildCacheTierGapMismatch's fixture shape
    to a subagent-origin record."""

    def test_idle_over_1h_and_pure_1h_tier_subagent_writes_stay_out_of_x(self, fake_projects, capsys):
        records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            # 3,900s gap -- idle >1h. Excluded from X despite carrying
            # 5m-tier write tokens (it still counts toward W5m).
            _priced(
                "claude-sonnet-5", ephemeral_5m=150_000,
                ts="2026-08-01T11:05:00.000Z", request_id="sub-2",
            ),
            # 6-minute gap, but a PURE ephemeral_1h-tier write (no
            # ephemeral_5m at all) -- the 1h-TTL cache can't have expired
            # inside 6 minutes, so this reclassifies unexplained and
            # contributes nothing to either W5m or X (no eph_5m tokens).
            _priced(
                "claude-sonnet-5", ephemeral_1h=200_000,
                ts="2026-08-01T11:11:00.000Z", request_id="sub-3",
            ),
        ]
        for rec in records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        subagent_row = _table_cols(out, header_contains="Ratio", row_contains="subagent")
        assert subagent_row["W5m"] == "150,100"
        assert subagent_row["X"] == "0"


class TestCacheRebuildSwitchDeltaArithmetic:
    """Verification items 5-6: the printed net dollar figure for the
    subagent origin row is savings-positive and uses the correct, per-model
    coefficients, and the break-even boundary is exact, not a
    rounded-decimal approximation."""

    def test_default_rate_model_break_even_arithmetic(self, fake_projects, capsys):
        """X = 500,000, W5m = 1,000,000 on claude-sonnet-5 ($2.00/MTok
        base): net = (1.9 x 500,000 - 0.75 x 1,000,000) / 1e6 * $2.00 =
        $0.40, savings-positive per the report's negation step -- a fixture
        that instead asserted the helper's own pre-negation per-call sum
        would expect -$0.40."""
        records = [
            # Session start: contributes 500,000 to W5m only (not X).
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            # 6-minute gap -- idle 5m-1h: contributes 500,000 to both W5m
            # and X.
            _priced(
                "claude-sonnet-5", ephemeral_5m=500_000,
                ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        for rec in records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        subagent_row = _table_cols(out, header_contains="Ratio", row_contains="subagent")
        assert subagent_row["W5m"] == "1,000,000"
        assert subagent_row["X"] == "500,000"
        assert subagent_row["Net$"] == "0.40"

    def test_exact_break_even_boundary_nets_zero_and_the_strict_inequality_holds(
        self, fake_projects, capsys
    ):
        """X = 150,000, W5m = 380,000 is the exact rational break-even
        (150,000/380,000 = 15/38 = 0.75/1.9), not the rounded 0.3947 display
        figure -- a fixture built at the rounded decimal would sit
        measurably off the true boundary and couldn't discriminate '>'
        from '>='. Net must print exactly $0, not the "-0.00" a raw,
        un-rounded floating-point cancellation could otherwise leave."""
        records = [
            _priced("claude-sonnet-5", ephemeral_5m=230_000, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=150_000,
                ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        for rec in records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        subagent_row = _table_cols(out, header_contains="Ratio", row_contains="subagent")
        assert subagent_row["W5m"] == "380,000"
        assert subagent_row["X"] == "150,000"
        assert subagent_row["Net$"] == "0.00"

        # The fixture's single subagent group sits exactly at the break-even
        # ratio -- it must not count as "clearing" under the strict '>' this
        # test's own docstring pins, which a '>=' off-by-one would flip.
        dispersion = _extract_cache_rebuild_dispersion(out)
        assert dispersion["clearing"] == 0


class TestCacheRebuildSwitchDeltaZeroState:
    def test_no_sidechain_records_prints_zero_valued_subagent_row(self, fake_projects, capsys):
        """A corpus with no sidechain records at all still renders a
        zero-valued subagent row in every new block, matching the
        per-account zero-seeding convention, rather than vanishing -- and
        the dispersion block's own share-of-zero division doesn't raise."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "subagent") == (0, "0.00")

        subagent_row = _table_cols(out, header_contains="Ratio", row_contains="subagent")
        assert subagent_row["W5m"] == "0"
        assert subagent_row["X"] == "0"
        assert subagent_row["Net$"] == "0.00"

        dispersion = _extract_cache_rebuild_dispersion(out)
        assert dispersion["eligible"] == 0
        assert dispersion["clearing"] == 0
        assert dispersion["share"] == "0.0%"
        assert dispersion["net"] == "0.00"


class TestCacheRebuildSubagentDispersion:
    """Verification item 8: per-dispatch dispersion is computed against each
    subagent group's OWN X and W5m, not the pooled corpus-wide denominator --
    the mechanism Phase 0c's Outcome 2 (a selective lever not excluded)
    would read off."""

    def test_per_group_ratios_dispersion_excludes_zero_w5m_group_and_counts_sub_threshold_calls(
        self, fake_projects, capsys
    ):
        session_id = "sess-dispersion"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])

        # This dispatch's own ratio (150,000/200,000 = 0.75) clears the
        # default-rate break-even (0.3947) with margin.
        clearing_records = [
            _priced("claude-sonnet-5", ephemeral_5m=50_000, ts="2026-08-01T10:00:00.000Z", request_id="c-1"),
            # Sub-threshold (10,000 < 100,000 default threshold) but idle
            # 5m-1h -- must still count toward THIS group's own X/W5m
            # (per-group threshold independence, mirroring the pooled fix
            # above).
            _priced(
                "claude-sonnet-5", ephemeral_5m=10_000,
                ts="2026-08-01T10:06:00.000Z", request_id="c-2",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_5m=140_000,
                ts="2026-08-01T10:12:00.000Z", request_id="c-3",
            ),
        ]
        for rec in clearing_records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-clearing", clearing_records)

        # This dispatch's own ratio (10,000/200,000 = 0.05) does not clear.
        non_clearing_records = [
            _priced("claude-sonnet-5", ephemeral_5m=190_000, ts="2026-08-01T10:00:00.000Z", request_id="n-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=10_000,
                ts="2026-08-01T10:06:00.000Z", request_id="n-2",
            ),
        ]
        for rec in non_clearing_records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-non-clearing", non_clearing_records)

        # No 5-minute-tier writes at all -- undefined ratio, excluded from
        # both the clearing count and the W5m-share denominator, and must
        # not raise a division error.
        zero_w5m_record = _priced(
            "claude-sonnet-5", ephemeral_1h=100_000, ts="2026-08-01T10:00:00.000Z", request_id="z-1",
        )
        zero_w5m_record["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-zero-w5m", [zero_w5m_record])

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        dispersion = _extract_cache_rebuild_dispersion(out)
        assert dispersion["eligible"] == 2
        assert dispersion["clearing"] == 1
        assert dispersion["share"] == "50.0%"
        assert dispersion["net"] == "0.27"

    def test_unpriced_model_call_inside_a_priced_group_is_excluded_from_that_groups_own_w5m_and_x(
        self, fake_projects, capsys
    ):
        """A group's own W5m/X reflect only its priced calls -- per-group
        accumulation happens exclusively inside the priced branch of the
        report's accumulation block, by design (an unpriced call has no
        delta_dollars to fold into group_delta_dollars, and a group's
        "clears/doesn't clear" verdict must be computed over the same
        population as its own ratio). agent-1's own priced calls (40,000
        session-start + 100,000 idle-5m-1h) clear the default-rate
        break-even at ratio 100,000/140,000 = 0.714. A second,
        unambiguously non-clearing group (agent-2, own ratio 10,000/200,000
        = 0.05) sits alongside it so the dispersion block has two eligible
        groups -- with only one eligible group, "share" reduces to
        w5m/w5m = 100% regardless of what that group's own w5m actually is,
        so a regression that folds the unpriced call's tokens into
        agent-1's own group_w5m_tokens too (e.g. moving that accumulator
        line outside the price-gated branch) would leave "share" unchanged
        at 100% and go undetected; with agent-2 present, the same
        regression inflates agent-1's numerator and denominator by
        different amounts (agent-1 alone is the numerator, both groups sum
        to the denominator), so "share" moves from 41.2% to 85.1% and the
        assertion below catches it. The unpriced call's tokens still count
        toward the POOLED subagent W5m (price-independent there, by
        design), which is exactly what the dispersion block's own
        coverage-disclosure line reports as uncovered, and its excluded
        turn/token count is exactly what the switch-delta table's own
        unpriced-model disclosure line reports."""
        session_id = "sess-unpriced-in-group"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])

        records = [
            _priced("claude-sonnet-5", ephemeral_5m=40_000, ts="2026-08-01T10:00:00.000Z", request_id="u-1"),
            # 6-minute gap -- idle 5m-1h, priced.
            _priced(
                "claude-sonnet-5", ephemeral_5m=100_000,
                ts="2026-08-01T10:06:00.000Z", request_id="u-2",
            ),
            # 6-minute gap -- also idle 5m-1h, but an unpriced model: no
            # price-table entry, so this call's tokens can never reach
            # group_delta_dollars/group_w5m_tokens/group_x_tokens.
            _priced(
                "claude-unknown-model", ephemeral_5m=1_000_000,
                ts="2026-08-01T10:12:00.000Z", request_id="u-3",
            ),
        ]
        for rec in records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", records)

        # A second, unambiguously non-clearing group -- gives the dispersion
        # block two eligible groups so "share" is a genuine weighted value
        # (see docstring above for why a single-group fixture can't
        # discriminate the regression this test guards against).
        other_records = [
            _priced("claude-sonnet-5", ephemeral_5m=190_000, ts="2026-08-01T10:00:00.000Z", request_id="o-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=10_000,
                ts="2026-08-01T10:06:00.000Z", request_id="o-2",
            ),
        ]
        for rec in other_records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-2", other_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        dispersion = _extract_cache_rebuild_dispersion(out)
        assert dispersion["eligible"] == 2
        assert dispersion["clearing"] == 1
        assert dispersion["share"] == "41.2%"
        assert dispersion["net"] == "0.17"
        assert dispersion["uncovered_w5m"] == 1_000_000
        assert dispersion["pooled_subagent_w5m"] == 1_340_000

        unpriced_switch_delta = re.search(
            r"\((\d[\d,]*) 5m-tier write calls / (\d[\d,]*) tokens"
            r" excluded from the switch-delta figures above", out,
        )
        assert unpriced_switch_delta is not None, "unpriced switch-delta disclosure line not found in output"
        assert unpriced_switch_delta.group(1) == "1"
        assert unpriced_switch_delta.group(2) == "1,000,000"


