"""Tests for transcript_analysis/cache_rebuild.py (cmd_cache_rebuild): --ttl-verdict wiring, each verdict rule's unit
tests, the reducer, and each rule's full-pipeline boundary test."""
import importlib.util
import sys
from pathlib import Path

import pytest

from ._cache_rebuild_helpers import (
    _cache_rebuild_args,
    _extract_ttl_verdict_root_row,
    _extract_ttl_verdict_summary,
    _extract_ttl_verdict_tier_split_line,
    _ttl_verdict_1h_tier_non_wash_disagreement_records,
    _ttl_verdict_5m_tier_adopt_records,
    _ttl_verdict_dominant_1h_mixed_root_records,
    _ttl_verdict_near_tie_mixed_root_records,
)
from .conftest import (
    _priced,
    _write_cost_root,
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


class TestCacheRebuildTtlVerdictArgparseWiring:
    def test_ttl_verdict_flag_defaults_false_and_parses_true(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["cache-rebuild"])
        assert args.ttl_verdict is False

        args = parser.parse_args(["cache-rebuild", "--ttl-verdict"])
        assert args.ttl_verdict is True


class TestClassifyCacheRebuildCauseIdle5mBoundaryOverride:
    """Direct edge-value coverage for _classify_cache_rebuild_cause's
    idle_5m_boundary_seconds override, at its own exact boundary. The
    classifier forwards the override to _cache_rebuild_in_idle_5m_1h_band,
    whose own class (TestCacheRebuildIdle5m1hBandPredicate) covers the band
    edges. TestCacheRebuildTtlVerdictSensitivityBoundary covers the
    --ttl-verdict block's wiring of the sensitivity boundary."""

    def test_gap_at_the_overridden_boundary_classifies_idle(self):
        assert _mod.cache_rebuild_rules._classify_cache_rebuild_cause(
            is_first_call=False, gap_seconds=60, model_changed=False, pure_1h_tier_write=False,
            idle_5m_boundary_seconds=60,
        ) == _mod.cache_rebuild_rules._CAUSE_IDLE_5M_1H

    def test_gap_one_second_under_the_overridden_boundary_does_not_classify_idle(self):
        assert _mod.cache_rebuild_rules._classify_cache_rebuild_cause(
            is_first_call=False, gap_seconds=59, model_changed=False, pure_1h_tier_write=False,
            idle_5m_boundary_seconds=60,
        ) == _mod.cache_rebuild_rules._CAUSE_UNEXPLAINED


class TestCacheRebuildIdle5m1hBandPredicate:
    """Direct edge-value coverage for _cache_rebuild_in_idle_5m_1h_band: the
    gap test alone, [idle_5m_boundary_seconds, 3600) for a non-first call
    with a parseable gap."""

    def test_gap_at_the_default_lower_bound_is_in_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, 300) is True

    def test_gap_just_under_the_default_lower_bound_is_out_of_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, 299.999) is False

    def test_gap_just_under_the_upper_bound_is_in_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, 3599.99) is True

    def test_gap_at_the_upper_bound_is_out_of_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, 3600) is False

    def test_first_call_with_an_in_band_gap_is_out_of_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(True, 600) is False

    def test_unparseable_gap_is_out_of_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, None) is False

    def test_overridden_lower_bound_is_inclusive(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, 60, idle_5m_boundary_seconds=60) is True

    def test_gap_one_second_under_the_overridden_lower_bound_is_out_of_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, 59, idle_5m_boundary_seconds=60) is False

    def test_overridden_lower_bound_with_gap_at_the_hardcoded_upper_bound_is_out_of_band(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_in_idle_5m_1h_band(False, 3600, idle_5m_boundary_seconds=60) is False


class TestCacheRebuild1hTo5mDeltaPricing:
    """Direct unit coverage for _cache_rebuild_1h_to_5m_delta_dollars,
    mirroring TestCacheRebuildSwitchDeltaPricing's three cases for the
    sibling 5m-to-1h formula -- fast-mode/US-geo multiplier parity, the
    unpriced-model sentinel, and the reduced-cache-read-rate coefficient
    are exercised nowhere else."""

    def test_fast_mode_multiplier_applies_to_the_expiry_leg(self):
        """A wholly idle-5m-1h-cause call (eph_1h=0, all read tokens) nets
        1,000,000/1e6*(2.5-0.2) = $2.30 at the default rate; fast mode
        doubles every dollar class, so this call's own delta doubles too."""
        usage = _priced("claude-sonnet-5", cache_read=1_000_000, speed="fast")["message"]["usage"]
        delta, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-sonnet-5", usage, is_idle_5m_1h_cause=True
        )
        assert delta == pytest.approx(4.60)
        assert unpriced_tokens == 0

    def test_unpriced_model_returns_none_delta_not_a_silent_zero(self):
        """Mirrors the sibling's own unpriced-model contract: None, not a
        silently priced $0, so callers can distinguish the two."""
        usage = _priced("claude-unknown-model", ephemeral_1h=1_000_000, input=10, output=5)["message"]["usage"]
        delta, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-unknown-model", usage, is_idle_5m_1h_cause=False
        )
        assert delta is None
        assert unpriced_tokens > 0

    def test_fable_5_1_uses_reduced_cache_read_multiplier_not_hardcoded_1_15(self):
        """A hardcoded (1.25 - 0.1) = 1.15 coefficient would price this
        call's expiry leg at 1,000,000/1e6 * (12.5 - 1.0) = $11.50. The
        correct, per-model-resolved coefficient uses Fable 5.1's own
        reduced 0.025x cache-read multiplier (read rate $0.25, expiry
        coefficient 12.5-0.25=12.25), giving $12.25 instead."""
        usage = _priced("claude-fable-5-1", cache_read=1_000_000)["message"]["usage"]
        delta, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-fable-5-1", usage, is_idle_5m_1h_cause=True
        )
        assert delta == pytest.approx(12.25)
        assert unpriced_tokens == 0


class TestCacheRebuild1hTo5mDeltaArithmetic:
    """Direct unit coverage for _cache_rebuild_1h_to_5m_delta_dollars' own
    per-call arithmetic at, above, and below its algebraic break-even
    (Z/W1h = 0.75/(1.25-r) ~= 0.6522 at the default rate) -- the mirror of
    TestCacheRebuildSwitchDeltaArithmetic's own coverage for the sibling
    5m-to-1h formula. W1h=1,150,000 (one session-start call, contributing
    only the always-on base term) pairs with a second, idle-5m-1h-cause
    call's own read tokens (Z) at each of the three points."""

    def test_below_break_even_z_favors_dropping_to_5m(self):
        """Z=500,000: 500,000/1,150,000 = 0.435 is well under 0.6522, so
        dropping to 5m nets a savings (a negative, switch-cost-positive
        sum)."""
        base_usage = _priced("claude-sonnet-5", ephemeral_1h=1_150_000)["message"]["usage"]
        idle_usage = _priced("claude-sonnet-5", cache_read=500_000)["message"]["usage"]
        base_delta, _unpriced = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-sonnet-5", base_usage, is_idle_5m_1h_cause=False
        )
        idle_delta, _unpriced = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-sonnet-5", idle_usage, is_idle_5m_1h_cause=True
        )
        assert base_delta + idle_delta == pytest.approx(-0.575)

    def test_exact_rational_break_even_nets_exactly_zero(self):
        """Z=750,000 is the exact rational break-even (750,000/1,150,000 =
        15/23 = 0.75/1.15), not the rounded 0.6522 display figure."""
        base_usage = _priced("claude-sonnet-5", ephemeral_1h=1_150_000)["message"]["usage"]
        idle_usage = _priced("claude-sonnet-5", cache_read=750_000)["message"]["usage"]
        base_delta, _unpriced = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-sonnet-5", base_usage, is_idle_5m_1h_cause=False
        )
        idle_delta, _unpriced = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-sonnet-5", idle_usage, is_idle_5m_1h_cause=True
        )
        assert base_delta + idle_delta == pytest.approx(0.0, abs=1e-9)

    def test_above_break_even_z_disfavors_dropping_to_5m(self):
        """Z=1,000,000: 1,000,000/1,150,000 = 0.870 clears 0.6522, so
        dropping to 5m nets a cost (a positive, switch-cost-positive
        sum)."""
        base_usage = _priced("claude-sonnet-5", ephemeral_1h=1_150_000)["message"]["usage"]
        idle_usage = _priced("claude-sonnet-5", cache_read=1_000_000)["message"]["usage"]
        base_delta, _unpriced = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-sonnet-5", base_usage, is_idle_5m_1h_cause=False
        )
        idle_delta, _unpriced = _mod.cache_rebuild_rules._cache_rebuild_1h_to_5m_delta_dollars(
            "claude-sonnet-5", idle_usage, is_idle_5m_1h_cause=True
        )
        assert base_delta + idle_delta == pytest.approx(0.575)


class TestCacheRebuildMarginClears:
    """Direct unit coverage for _cache_rebuild_margin_clears -- shared by
    both directions' own per-root check, at, above, and below the plan's
    10% margin threshold (_CACHE_REBUILD_TTL_MARGIN_FRACTION)."""

    def test_exactly_at_the_margin_threshold_clears(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_margin_clears(10.0, 100.0) is True

    def test_above_the_margin_threshold_clears(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_margin_clears(50.0, 100.0) is True

    def test_below_the_margin_threshold_does_not_clear(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_margin_clears(5.0, 100.0) is False

    def test_non_positive_volume_never_clears(self):
        """A root with zero (or, degenerately, negative) dollar-equivalent
        volume never clears, rather than dividing by zero or by a negative
        number."""
        assert _mod.cache_rebuild_rules._cache_rebuild_margin_clears(0.0, 0.0) is False
        assert _mod.cache_rebuild_rules._cache_rebuild_margin_clears(5.0, -1.0) is False


class TestCacheRebuildTokenTiebreakerFavors5m:
    """Direct unit coverage for _cache_rebuild_token_tiebreaker_favors_5m --
    the raw-token, zero-price, zero-tolerance sign check, exercised
    independently of any verdict-decision branch test."""

    def test_z_below_w1h_favors_dropping_to_5m(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_token_tiebreaker_favors_5m(5, 10) is True

    def test_z_above_w1h_disfavors_dropping_to_5m(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_token_tiebreaker_favors_5m(15, 10) is False

    def test_z_equal_to_w1h_is_a_disagreement_not_a_favorable_tie(self):
        """Z == W1h counts as a disagreement, never a wash --
        including the degenerate 0 == 0 case (a root with no data in
        either direction never reaches this function in the report's
        own per-root reduction, but the function itself must not
        special-case zero)."""
        assert _mod.cache_rebuild_rules._cache_rebuild_token_tiebreaker_favors_5m(10, 10) is None
        assert _mod.cache_rebuild_rules._cache_rebuild_token_tiebreaker_favors_5m(0, 0) is None


class TestCacheRebuildDominantTierShare:
    """Direct unit coverage for _cache_rebuild_dominant_tier_share, the
    ratio --ttl-verdict's per-root eligibility test compares against
    _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN. Exercised independently of
    the full-pipeline boundary tests below."""

    def test_share_exactly_at_the_threshold(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_dominant_tier_share(900_000, 100_000) == pytest.approx(0.9)

    def test_share_just_above_the_threshold(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_dominant_tier_share(901_000, 99_000) == pytest.approx(0.901)

    def test_share_just_below_the_threshold(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_dominant_tier_share(899_000, 101_000) == pytest.approx(0.899)

    def test_share_is_symmetric_in_which_tier_is_dominant(self):
        """max() makes the dominant tier's own identity irrelevant to the
        ratio: swapping which argument is larger yields the same share."""
        assert _mod.cache_rebuild_rules._cache_rebuild_dominant_tier_share(
            100_000, 900_000
        ) == _mod.cache_rebuild_rules._cache_rebuild_dominant_tier_share(900_000, 100_000)

    def test_no_data_root_raises_instead_of_returning_an_undefined_ratio(self):
        """Pins that the no-data case raises `ZeroDivisionError` rather than
        returning a sentinel. The docstring's `w5m == w1h == 0` exclusion is
        a caller obligation, not a guard inside the function."""
        with pytest.raises(ZeroDivisionError):
            _mod.cache_rebuild_rules._cache_rebuild_dominant_tier_share(0, 0)


class TestCacheRebuildRootIsDominant:
    """Direct unit coverage for _cache_rebuild_root_is_dominant, the
    boolean the print loop's own eligibility branch decides on. Exercised
    independently of the full-pipeline boundary test below."""

    def test_share_exactly_at_the_threshold_counts_as_dominant(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_root_is_dominant(0.900) is True

    def test_share_just_above_the_threshold_counts_as_dominant(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_root_is_dominant(0.901) is True

    def test_share_just_below_the_threshold_does_not_count_as_dominant(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_root_is_dominant(0.899) is False


class TestCacheRebuildRootVerdictInput:
    """Direct unit coverage for _cache_rebuild_root_verdict_input -- the
    per-root reduction the report's own per-bucket loop calls once per
    5m-tier root and once per 1h-tier root. For a 5m-tier root,
    apply_tiebreaker=False and the tiebreaker never runs, since W1h is
    always 0 for a 5m-tier root by construction, making a raw-token
    comparison against it degenerate. For a 1h-tier root, apply_tiebreaker=
    True and the tiebreaker gates clears."""

    def test_5m_tier_root_clears_despite_disagreeing_tiebreaker_value(self):
        """apply_tiebreaker is False for a 5m-tier root, so clears is
        decided by the margin alone even when tiebreaker_favors_5m is
        passed a value that would disagree with the dollar accounting's
        own sign."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=5.0, net_sensitivity=5.0, volume=10.0,
            positive_favors="1h", negative_favors="5m",
            apply_tiebreaker=False, tiebreaker_favors_5m=True,
        )
        assert root_input == {"favors": "1h", "clears": True}

    def test_5m_tier_root_clears_despite_tiebreaker_wash(self):
        """A 5m-tier root's most common would-be tiebreaker outcome is
        Z == W1h == 0, a wash -- but apply_tiebreaker is False for it, so
        the wash never gates clears the way it does for a 1h-tier root."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=5.0, net_sensitivity=5.0, volume=10.0,
            positive_favors="1h", negative_favors="5m",
            apply_tiebreaker=False,
            tiebreaker_favors_5m=_mod.cache_rebuild_rules._cache_rebuild_token_tiebreaker_favors_5m(0, 0),
        )
        assert root_input == {"favors": "1h", "clears": True}

    def test_5m_tier_root_favoring_5m_when_net_primary_is_non_positive(self):
        """A 5m-tier root whose own net$ is non-positive (no savings from
        adopting 1h) favors staying at 5m, never clearing -- apply_tiebreaker
        is always False for a 5m-tier root, so the margin failure alone is
        what fails it here."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=-1.0, net_sensitivity=-1.0, volume=10.0,
            positive_favors="1h", negative_favors="5m",
            apply_tiebreaker=False,
        )
        assert root_input == {"favors": "5m", "clears": False}

    def test_1h_tier_root_declines_when_sensitivity_boundary_fails_margin(self):
        """Clears at the primary boundary's own margin but not at the
        sensitivity boundary's -- the two-point AND-gate fails the
        whole root even with a tiebreaker that agrees."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=5.0, net_sensitivity=-1.0, volume=10.0,
            positive_favors="5m", negative_favors="1h",
            apply_tiebreaker=True, tiebreaker_favors_5m=True,
        )
        assert root_input == {"favors": "5m", "clears": False}

    def test_1h_tier_root_declines_on_tiebreaker_disagreement_despite_clearing_margin(self):
        """A dollar margin that clears comfortably at both boundaries
        still declines when the raw-token tiebreaker disagrees with the
        dollar accounting's own sign."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=5.0, net_sensitivity=5.0, volume=10.0,
            positive_favors="5m", negative_favors="1h",
            apply_tiebreaker=True, tiebreaker_favors_5m=False,
        )
        assert root_input == {"favors": "5m", "clears": False}

    def test_1h_tier_root_declines_on_tiebreaker_wash(self):
        """A 1h-tier root's own raw-token tiebreaker wash (Z == W1h) counts
        as a disagreement, never a favorable tie, even when the dollar
        margin clears comfortably -- apply_tiebreaker is True here, unlike
        a 5m-tier root, so the wash still gates clears."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=5.0, net_sensitivity=5.0, volume=10.0,
            positive_favors="5m", negative_favors="1h",
            apply_tiebreaker=True,
            tiebreaker_favors_5m=_mod.cache_rebuild_rules._cache_rebuild_token_tiebreaker_favors_5m(10, 10),
        )
        assert root_input == {"favors": "5m", "clears": False}

    def test_1h_tier_root_adopts_when_margin_clears_and_tiebreaker_agrees(self):
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=5.0, net_sensitivity=5.0, volume=10.0,
            positive_favors="5m", negative_favors="1h",
            apply_tiebreaker=True, tiebreaker_favors_5m=True,
        )
        assert root_input == {"favors": "5m", "clears": True}

    def test_5m_tier_root_favors_5m_at_the_net_primary_zero_sign_boundary(self):
        """favors resolves via net_primary > 0, not net_primary >= 0, so an
        exact 0.0 net_primary -- a real dollar-delta accumulation can land
        exactly on zero -- takes the negative_favors branch for a 5m-tier
        root's own orientation."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=0.0, net_sensitivity=0.0, volume=10.0,
            positive_favors="1h", negative_favors="5m",
            apply_tiebreaker=False,
        )
        assert root_input == {"favors": "5m", "clears": False}

    def test_1h_tier_root_favors_1h_at_the_net_primary_zero_sign_boundary(self):
        """The same net_primary == 0.0 sign boundary, at a 1h-tier root's
        own positive_favors/negative_favors orientation."""
        root_input = _mod.cache_rebuild_rules._cache_rebuild_root_verdict_input(
            net_primary=0.0, net_sensitivity=0.0, volume=10.0,
            positive_favors="5m", negative_favors="1h",
            apply_tiebreaker=True, tiebreaker_favors_5m=False,
        )
        assert root_input == {"favors": "1h", "clears": False}


class TestCacheRebuildTierSplitAgreement:
    """Direct unit coverage for _cache_rebuild_tier_split_agreement -- the
    two-slice cross-check's sign-resolution and agreement comparison the
    report's own per-bucket loop calls once per mixed root. Exercised
    independently of the full-pipeline tests below."""

    def test_positive_net_5m_slice_resolves_to_favors_1h(self):
        favors_5m_slice, _, _ = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(1.0, 0.0)
        assert favors_5m_slice == "1h"

    def test_negative_net_5m_slice_resolves_to_favors_5m(self):
        favors_5m_slice, _, _ = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(-1.0, 0.0)
        assert favors_5m_slice == "5m"

    def test_exact_zero_net_5m_slice_resolves_to_favors_5m(self):
        """Resolves via net_5m_slice > 0, not >= 0, so an exact wash takes
        the favors-5m branch. This is the same sign convention
        `_cache_rebuild_root_verdict_input` applies."""
        favors_5m_slice, _, _ = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(0.0, 0.0)
        assert favors_5m_slice == "5m"

    def test_positive_net_1h_slice_resolves_to_favors_5m(self):
        _, favors_1h_slice, _ = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(0.0, 1.0)
        assert favors_1h_slice == "5m"

    def test_negative_net_1h_slice_resolves_to_favors_1h(self):
        _, favors_1h_slice, _ = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(0.0, -1.0)
        assert favors_1h_slice == "1h"

    def test_exact_zero_net_1h_slice_resolves_to_favors_1h(self):
        """Resolves via net_1h_slice > 0, not >= 0, so an exact wash takes
        the favors-1h branch."""
        _, favors_1h_slice, _ = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(0.0, 0.0)
        assert favors_1h_slice == "1h"

    def test_both_slices_favoring_5m_agree(self):
        *_, agreement = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(-1.0, 1.0)
        assert agreement == "agree"

    def test_both_slices_favoring_1h_agree(self):
        *_, agreement = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(1.0, -1.0)
        assert agreement == "agree"

    def test_slices_favoring_opposite_tiers_disagree(self):
        *_, agreement = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(1.0, 1.0)
        assert agreement == "disagree"

    def test_exact_zero_wash_on_both_slices_disagrees(self):
        """The two slices' independent wash conventions resolve to opposite
        tiers: favors-5m for the 5m slice, favors-1h for the 1h slice. A
        double wash therefore reports disagree rather than a vacuous
        agree."""
        *_, agreement = _mod.cache_rebuild_rules._cache_rebuild_tier_split_agreement(0.0, 0.0)
        assert agreement == "disagree"


class TestCacheRebuildTtlVerdictDecision:
    """Direct unit coverage for _cache_rebuild_ttl_verdict's own four-way
    reduction, with hand-fed per-root {"favors", "clears"} inputs -- one
    test per branch."""

    def test_zero_consistent_roots_is_no_verdict(self):
        assert _mod.cache_rebuild_rules._cache_rebuild_ttl_verdict([]) == _mod.cache_rebuild_rules._TTL_VERDICT_NO_VERDICT

    def test_single_direction_all_clearing_is_adopt(self):
        root_inputs = [
            {"favors": "1h", "clears": True},
            {"favors": "1h", "clears": True},
        ]
        assert _mod.cache_rebuild_rules._cache_rebuild_ttl_verdict(root_inputs) == _mod.cache_rebuild_rules._TTL_VERDICT_ADOPT

    def test_single_direction_one_not_clearing_is_decline(self):
        """A margin/boundary miss on one otherwise-agreeing root declines
        the whole bucket, even though every root points the same way."""
        root_inputs = [
            {"favors": "1h", "clears": True},
            {"favors": "1h", "clears": False},
        ]
        assert _mod.cache_rebuild_rules._cache_rebuild_ttl_verdict(root_inputs) == _mod.cache_rebuild_rules._TTL_VERDICT_DECLINE

    def test_dollar_vs_token_tiebreaker_disagreement_declines(self):
        """A root whose dollar accounting and raw-token tiebreaker
        disagree never clears, regardless of its own dollar margin --
        modeled here by resolving "clears" through the tiebreaker exactly
        as the report's own per-root reduction does, then feeding the
        result into the pure verdict function."""
        dollar_favors_5m = True
        tiebreaker_favors_5m = _mod.cache_rebuild_rules._cache_rebuild_token_tiebreaker_favors_5m(900_000, 100_000)
        assert tiebreaker_favors_5m is False  # Z > W1h disfavors 5m
        tiebreaker_agrees = tiebreaker_favors_5m is not None and tiebreaker_favors_5m == dollar_favors_5m
        root_inputs = [{"favors": "5m", "clears": tiebreaker_agrees}]
        assert _mod.cache_rebuild_rules._cache_rebuild_ttl_verdict(root_inputs) == _mod.cache_rebuild_rules._TTL_VERDICT_DECLINE

    def test_differing_favored_directions_is_roots_disagree(self):
        root_inputs = [
            {"favors": "1h", "clears": True},
            {"favors": "5m", "clears": True},
        ]
        assert (
            _mod.cache_rebuild_rules._cache_rebuild_ttl_verdict(root_inputs)
            == _mod.cache_rebuild_rules._TTL_VERDICT_ROOTS_DISAGREE
        )


class TestCacheRebuildTtlVerdictDominantTierShareThreshold:
    """Boundary coverage for _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN
    (0.900) -- the comparison is >=, so a root sitting exactly at the
    threshold counts, never just above it."""

    def _mixed_root_records(self, *, w1h: int, w5m: int) -> list[dict]:
        """A 1h-dominant mixed root: a 1h write, then a 5m write 10s later --
        inside the idle band's lower bound, so it never classifies as an
        idle-gap rebuild. Share is decided purely by w1h/w5m."""
        return [
            _priced("claude-sonnet-5", ephemeral_1h=w1h, ts="2026-08-01T10:00:00.000Z", request_id="w1h"),
            _priced("claude-sonnet-5", ephemeral_5m=w5m, ts="2026-08-01T10:00:10.000Z", request_id="w5m"),
        ]

    def test_share_exactly_at_threshold_counts_as_consistent(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", self._mixed_root_records(w1h=900_000, w5m=100_000))
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_1h"] == "1"
        assert summary["excluded"] == "0"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Share"] == "0.900"
        assert root_row["Clears"] == "True"
        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split == {"favors_5m_slice": "5m", "favors_1h_slice": "5m", "agreement": "agree"}

    def test_share_exactly_at_threshold_counts_as_consistent_for_subagent_origin(self, fake_projects, capsys):
        subagent_records = self._mixed_root_records(w1h=900_000, w5m=100_000)
        for rec in subagent_records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", subagent_records)
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "subagent")
        assert summary["consistent_1h"] == "1"
        assert summary["excluded"] == "0"

        root_row = _extract_ttl_verdict_root_row(out, "subagent", "account-1")
        assert root_row["Share"] == "0.900"
        assert root_row["Clears"] == "True"
        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split == {"favors_5m_slice": "5m", "favors_1h_slice": "5m", "agreement": "agree"}


class TestCacheRebuildTtlVerdictTierSplitCrossCheck:
    """Full-pipeline coverage for the two-slice cross-check's own
    'tier-split' note lines -- informative only, so every test here also
    confirms the line never moves a count or the verdict."""

    def test_near_tie_excluded_mixed_root_prints_its_own_disagreeing_tier_split_line(
        self, fake_projects, capsys
    ):
        """A mixed root at share 0.870 sits below the dominance threshold,
        so it is excluded(near-tie). Its two tiers' own accumulators
        disagree:

        - 5m-tier slice: idle rebuild ratio (X/W5m = 0.5, above the
          ~0.3947 break-even) favors switching to 1h.
        - 1h-tier slice: the minority 1h write carries no idle-read
          evidence of its own, favors staying at -- i.e. dropping to --
          5m.

        The tier-split line must name exactly this disagreement.
        _extract_ttl_verdict_root_row must still find exactly one row for
        the root, and the summary counts must reflect only the near-tie
        exclusion, unmoved by the cross-check."""
        _write_jsonl(fake_projects / "sess.jsonl", _ttl_verdict_near_tie_mixed_root_records())
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "0"
        assert summary["consistent_1h"] == "0"
        assert summary["excluded"] == "1"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Share"] == "0.870"
        assert root_row["Clears"] == "excluded(near-tie)"
        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split == {"favors_5m_slice": "1h", "favors_1h_slice": "5m", "agreement": "disagree"}

    def test_dominance_resolved_mixed_root_prints_its_own_disagreeing_tier_split_line(
        self, fake_projects, capsys
    ):
        """Same disagreeing per-slice shape as the near-tie test above:

        - 5m-tier slice favors switching to 1h.
        - 1h-tier slice favors staying at 5m.

        Here the minority 1h write is smaller (90,000, share 0.917),
        clearing the dominance threshold, so the root counts toward the
        verdict this time. The tier-split line must still print and
        still name the same disagreement, unaffected by which side of the
        threshold the root landed on."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="a1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=500_000,
                ts="2026-08-01T10:06:00.000Z", request_id="a2",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_1h=90_000,
                ts="2026-08-01T10:13:00.000Z", request_id="a3",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "1"
        assert summary["excluded"] == "0"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Share"] == "0.917"
        assert root_row["Clears"] == "True"
        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split == {"favors_5m_slice": "1h", "favors_1h_slice": "5m", "agreement": "disagree"}

    def test_two_simultaneously_mixed_roots_each_print_their_own_directions_not_the_others(
        self, tmp_path, capsys
    ):
        """Two mixed roots in the same bucket disagree in different,
        distinguishable directions:

        - account-1: idle-heavy 5m block plus a minority 1h write
          disagrees one way.
        - account-2: 1h-dominant block plus a minority 5m write agrees
          the other way.

        Each root's own tier-split line must name only its own data.
        With only these two roots, the test demonstrates that their
        tier-split lines do not cross-contaminate each other.
        `_extract_ttl_verdict_root_row` cannot catch that gap on its own,
        since it skips these lines by design."""
        root_a_records = _ttl_verdict_near_tie_mixed_root_records()
        root_b_records = _ttl_verdict_dominant_1h_mixed_root_records()
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", root_a_records)
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", root_b_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[root_a, root_b])
        out = capsys.readouterr().out

        tier_split_1 = _extract_ttl_verdict_tier_split_line(out, "account-1")
        tier_split_2 = _extract_ttl_verdict_tier_split_line(out, "account-2")
        assert tier_split_1 == {"favors_5m_slice": "1h", "favors_1h_slice": "5m", "agreement": "disagree"}
        assert tier_split_2 == {"favors_5m_slice": "5m", "favors_1h_slice": "5m", "agreement": "agree"}

    def test_root_row_extraction_stays_singular_across_mixed_no_data_and_pure_roots_in_one_corpus(
        self, tmp_path, capsys
    ):
        """One corpus carries all three per-root shapes at once:
        - account-1: dominance-resolved mixed root (share 0.917)
        - account-2: no cache-tier data
        - account-3: pure 5m-tier root, from _ttl_verdict_5m_tier_adopt_records

        _extract_ttl_verdict_root_row must still return exactly one row per
        root despite the tier-split line sitting between rows in the same
        table."""
        mixed_root = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-mixed", "sess-mixed", [
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="a1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=500_000,
                ts="2026-08-01T10:06:00.000Z", request_id="a2",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_1h=90_000,
                ts="2026-08-01T10:13:00.000Z", request_id="a3",
            ),
        ])
        no_data_root = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-no-data", "sess-no-data", [])
        pure_root = _write_cost_root(
            tmp_path, "acct-c", "-home-user-repo-pure", "sess-pure", _ttl_verdict_5m_tier_adopt_records(),
        )

        _mod.cache_rebuild._cache_rebuild_report(
            _cache_rebuild_args(ttl_verdict=True), roots=[mixed_root, no_data_root, pure_root],
        )
        out = capsys.readouterr().out

        mixed_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        no_data_row = _extract_ttl_verdict_root_row(out, "main", "account-2")
        pure_row = _extract_ttl_verdict_root_row(out, "main", "account-3")

        assert mixed_row["Share"] == "0.917"
        assert mixed_row["Clears"] == "True"
        assert no_data_row["Clears"] == "excluded(no-data)"
        assert pure_row["Share"] == "1.000"
        assert pure_row["Clears"] == "True"

        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split == {"favors_5m_slice": "1h", "favors_1h_slice": "5m", "agreement": "disagree"}
        assert _extract_ttl_verdict_tier_split_line(out, "account-2") is None
        assert _extract_ttl_verdict_tier_split_line(out, "account-3") is None

    def test_minority_slice_net_delta_of_exactly_zero_resolves_like_the_dominant_tiers_own_wash(
        self, fake_projects, capsys
    ):
        """A minority 5m-tier write small enough (100 tokens) that its own
        switch-delta rounds to exactly $0.00 pins the slice-favors wash
        boundary. _cache_rebuild_root_verdict_input's own net > 0 test
        resolves a net of exactly 0 to negative_favors, never the positive
        direction. The two-slice cross-check's own hand-rolled sign test
        must resolve the same way at that boundary."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="a1"),
            # 10s later, well inside the idle band's own lower bound, so
            # unexplained rather than idle.
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:10.000Z", request_id="a2"),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split is not None
        assert tier_split["favors_5m_slice"] == "5m"

    def test_minority_1h_slice_net_delta_of_exactly_zero_resolves_like_the_dominant_tiers_own_wash(
        self, fake_projects, capsys
    ):
        """Mirror of the test above for a 5m-dominant root: a minority
        1h-tier write small enough (100 tokens) that its own switch-delta
        rounds to exactly $0.00 pins the same wash boundary on the
        tier == 5m branch's own net_1h_slice > 0 test."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="a1"),
            # 10s later, well inside the idle band's own lower bound, so
            # unexplained rather than idle.
            _priced("claude-sonnet-5", ephemeral_1h=100, ts="2026-08-01T10:00:10.000Z", request_id="a2"),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split is not None
        assert tier_split["favors_1h_slice"] == "1h"

    def test_both_origins_mixed_with_different_directions_each_print_only_their_own_line(
        self, fake_projects, capsys
    ):
        """The same root ordinal (account-1) is mixed in both the main and
        subagent buckets at once, disagreeing in a different direction per
        origin:

        - main is the near-tie mixed root shape (share 0.870, excluded,
          disagree).
        - subagent is the dominance-resolved mixed root shape (share
          0.971, clears, agree).

        Every other test in this class matches its tier-split line as an
        unscoped substring of the whole report; here that would pass even
        if a line leaked into the wrong origin's own '### {origin}'
        section, so each assertion below is scoped to that section."""
        main_records = _ttl_verdict_near_tie_mixed_root_records()
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="d1"),
            _priced("claude-sonnet-5", cache_read=400_000, ts="2026-08-01T10:06:00.000Z", request_id="d2"),
            _priced("claude-sonnet-5", ephemeral_5m=30_000, ts="2026-08-01T10:07:40.000Z", request_id="d3"),
        ]
        for rec in subagent_records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", main_records)
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        main_section = out[out.index("### main"):out.index("### subagent")]
        subagent_section = out[out.index("### subagent"):]
        main_tier_split = _extract_ttl_verdict_tier_split_line(main_section, "account-1")
        subagent_tier_split = _extract_ttl_verdict_tier_split_line(subagent_section, "account-1")
        assert main_tier_split == {"favors_5m_slice": "1h", "favors_1h_slice": "5m", "agreement": "disagree"}
        assert subagent_tier_split == {"favors_5m_slice": "5m", "favors_1h_slice": "5m", "agreement": "agree"}

        main_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        subagent_row = _extract_ttl_verdict_root_row(out, "subagent", "account-1")
        assert main_row["Clears"] == "excluded(near-tie)"
        assert subagent_row["Clears"] == "True"


class TestCacheRebuildTtlVerdictTiebreakerBoundarySelection:
    """Full-pipeline coverage that the per-root tiebreaker reduction reads
    z_by_origin_root at the primary, 300s boundary, never the 60s
    sensitivity boundary. Each fixture below adds a read call idle only at
    the 60s sensitivity boundary (gap in [60s, 300s)) alongside one idle at
    both boundaries, so the primary and sensitivity Z totals genuinely
    diverge. That divergence is observable only through a 1h-tier root's
    own printed Z figure in its X/Z column, since apply_tiebreaker is False
    for a 5m-tier root."""

    def test_5m_tier_root_adopts_regardless_of_which_boundary_z_is_read_at(self, fake_projects, capsys):
        """apply_tiebreaker is False for a 5m-tier root, so a Z/W1h wash at
        either boundary never gates clears."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="w5m-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=500_000,
                ts="2026-08-01T10:06:00.000Z", request_id="w5m-2",
            ),
            _priced(
                "claude-sonnet-5", cache_read=300_000,
                ts="2026-08-01T10:07:30.000Z", request_id="w5m-3",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["verdict"] == "adopt"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["W5m/W1h"] == "1,000,000"
        assert root_row["X/Z"] == "500,000"
        assert root_row["Favors"] == "1h"
        assert root_row["Clears"] == "True"

    def test_1h_tier_root_declines_on_a_non_wash_disagreement(self, fake_projects, capsys):
        """W1h=1,000,000 (call1, session start). call2 reads 800,000
        tokens at a 400s gap, idle at both boundaries. call3 reads a
        further 400,000 at a 100s gap after call2, idle only at the 60s
        sensitivity boundary.

        The primary Z is 800,000: nonzero, unequal to W1h, and 0.8 below
        the 1.0 tiebreaker threshold favors dropping to 5m, disagreeing
        with the dollar accounting's own "favors 1h" sign.

        0.8 also exceeds the ~0.652 dollar break-even, so net$ is negative
        here too. This ratio band structurally always fails the margin
        alongside the tiebreaker, since the break-even sits below the
        tiebreaker's own threshold.

        Gating Z's own increment on the sensitivity boundary instead of
        the primary one would total 1,200,000, a different printed X/Z
        figure than the 800,000 asserted below."""
        _write_jsonl(fake_projects / "sess.jsonl", _ttl_verdict_1h_tier_non_wash_disagreement_records())
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["verdict"] == "decline"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["W5m/W1h"] == "1,000,000"
        assert root_row["X/Z"] == "800,000"
        assert root_row["Favors"] == "1h"
        assert root_row["Clears"] == "False"


class TestCacheRebuildTtlVerdictSensitivityBoundary:
    def test_clears_margin_at_300s_but_fails_at_60s_never_adopts(self, fake_projects, capsys):
        """A 1h-tier root whose margin clears using the primary
        (300s-boundary) accumulation but fails once a gap in [60s, 300s)
        is also reclassified idle at the 60s sensitivity boundary -- the
        two-point check is an explicit AND-gate, so this must
        decline, never adopt, even though the primary boundary alone
        clears comfortably.

        call1 (session start) writes 1,150,000 ephemeral_1h tokens (W1h),
        contributing only the always-on base term (-$1.725) at both
        boundaries. call2, a 100s gap, reads 900,000 tokens:
        - at the 300s boundary, this call is unexplained (gap < 300) and
          never touches Z or the expiry term, leaving net$ = +$1.725
          (margin 1.725/4.6 = 37.5%, clears).
        - at the 60s boundary, the same call is idle 5m-1h, adding a
          +$2.07 expiry term that flips the total to -$0.345 (a negative
          margin, failing)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_1h=1_150_000, ts="2026-08-01T10:00:00.000Z", request_id="b1"),
            _priced(
                "claude-sonnet-5", cache_read=900_000,
                ts="2026-08-01T10:01:40.000Z", request_id="b2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_1h"] == "1"
        assert summary["verdict"] != "adopt"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Clears"] == "False"


