"""Tests for transcript_analysis/cache_rebuild.py (cmd_cache_rebuild): pure-1h idle-band reads, rate-multiplier
footing, the unpriced and cache-miss-reason disclosures, and the default-path regression."""
import importlib.util
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ._cache_rebuild_helpers import (
    _cache_rebuild_args,
    _extract_cache_rebuild_row,
    _extract_ttl_verdict_root_row,
    _extract_ttl_verdict_summary,
    _extract_ttl_verdict_tier_split_line,
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


def _ttl_verdict_ts(seconds_after_start: float) -> str:
    """ISO timestamp `seconds_after_start` after the fixed 10:00:00 start the
    hand-derived --ttl-verdict fixtures below share."""
    start = datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
    return (start + timedelta(seconds=seconds_after_start)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _pure_1h_write_plus_read_records(
    gap_seconds: float, *, second_write: int = 100_000, read_tokens: int = 200_000,
) -> list[dict]:
    """A 1h-tier root: a session-start 1,000,000-token ephemeral_1h write,
    then one call `gap_seconds` later that both writes `second_write`
    ephemeral_1h tokens (no ephemeral_5m) and reads `read_tokens`."""
    return [
        _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts=_ttl_verdict_ts(0), request_id="p1"),
        _priced(
            "claude-sonnet-5", ephemeral_1h=second_write, cache_read=read_tokens,
            ts=_ttl_verdict_ts(gap_seconds), request_id="p2",
        ),
    ]


class TestCacheRebuildTtlVerdictPure1hWriteInIdleBand:
    """A call inside the idle band whose own write is purely ephemeral_1h
    still read its prefix warm from the live 1h tier, so its reads count
    toward Z and its (write_5m - read) expiry term counts toward the
    1h-to-5m net. The band flags feeding those accumulators are the gap test
    alone, unlike the cause table's own "idle 5m-1h" row.

    Fixture arithmetic, claude-sonnet-5 per MTok (base $2.00 x the vendor's
    1.25 / 2 / 0.1 multipliers): write_5m $2.50, write_1h $4.00, read $0.20.
    That is a $1.50 saving per 1h-tier write token and a $2.30 expiry cost
    per read token.

    Base corpus (_pure_1h_write_plus_read_records): call1 writes 1,000,000
    ephemeral_1h at session start; call2, after gap G, writes 100,000
    ephemeral_1h and reads 200,000. W1h = 1,100,000; margin volume =
    1.1 x $4.00 = $4.40.
    - Without the expiry term: net = 1.1 x $1.50 = $1.65.
    - With it (G in [300, 3600)): net = $1.65 - 0.2 x $2.30 = $1.19, a 27%
      margin, still clearing; Z = 200,000 < W1h so the token tiebreaker
      agrees the root favors 5m."""

    def test_in_band_read_beside_a_pure_1h_write_counts_toward_z(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", _pure_1h_write_plus_read_records(360))
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Tier"] == "1h"
        assert root_row["W5m/W1h"] == "1,100,000"
        assert root_row["X/Z"] == "200,000"

    def test_in_band_read_beside_a_pure_1h_write_restores_the_expiry_cost_in_net(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", _pure_1h_write_plus_read_records(360))
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Net$"] == "$1.19"
        assert root_row["Favors"] == "5m"
        assert root_row["Clears"] == "True"

    def test_gap_of_exactly_300s_counts_toward_z(self, fake_projects, capsys):
        """The band's lower bound is inclusive, so the same shape at exactly
        300s counts its reads."""
        _write_jsonl(fake_projects / "sess.jsonl", _pure_1h_write_plus_read_records(300))
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["X/Z"] == "200,000"
        assert root_row["Net$"] == "$1.19"

    def test_gap_in_the_60s_to_300s_band_fails_the_sensitivity_margin_only(self, fake_projects, capsys):
        """At G = 200s the call is outside the primary band (Z stays 0, primary
        net stays $1.65) but inside the 60s sensitivity band, so its expiry
        term lands in the sensitivity net only. Net$ prints only the primary
        net, so Clears is the observable.

        Read = 660,000 (0.6 x W1h): sensitivity net = $1.65 - 0.66 x $2.30 =
        $0.132, which cents-rounds to $0.13, a 3% margin on the $4.40 volume.
        It falls under the 10% margin whenever read / W1h exceeds
        (1.5 - 0.4) / 2.3 ~ 0.478, so 0.6 sits well clear of the edge
        cent rounding could flip."""
        _write_jsonl(
            fake_projects / "sess.jsonl", _pure_1h_write_plus_read_records(200, read_tokens=660_000)
        )
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["X/Z"] == "0"
        assert root_row["Net$"] == "$1.65"
        assert root_row["Clears"] == "False"

    @pytest.mark.parametrize("gap_seconds", [3600, 3601], ids=["at-3600s-exclusive-upper-bound", "above-3600s"])
    def test_read_at_or_above_the_upper_bound_contributes_nothing_to_z(self, fake_projects, capsys, gap_seconds):
        _write_jsonl(fake_projects / "sess.jsonl", _pure_1h_write_plus_read_records(gap_seconds))
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["X/Z"] == "0"
        assert root_row["Net$"] == "$1.65"

    def test_first_call_pure_1h_write_with_reads_contributes_nothing_to_z(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced(
                "claude-sonnet-5", ephemeral_1h=1_000_000, cache_read=200_000,
                ts=_ttl_verdict_ts(0), request_id="f1",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["X/Z"] == "0"

    @pytest.mark.parametrize("ttl_verdict", [False, True], ids=["without-ttl-verdict", "with-ttl-verdict"])
    def test_cause_table_keeps_the_pure_1h_write_unexplained_with_or_without_the_flag(
        self, fake_projects, capsys, ttl_verdict
    ):
        """The widened band applies to --ttl-verdict's own accumulators, never
        to the cause table: the same invariant
        TestCacheRebuildCacheTierGapMismatch.test_pure_1h_tier_write_in_5m_1h_gap_reclassifies_unexplained
        pins without the flag."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts=_ttl_verdict_ts(0), request_id="c1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=200_000, cache_read=100_000,
                ts=_ttl_verdict_ts(360), request_id="c2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=ttl_verdict), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 1

    def test_negative_gap_record_completes_the_report_and_contributes_nothing_to_z(self, fake_projects, capsys):
        """A clock-skewed record (earlier timestamp than its predecessor) has
        no gap at all, so the band predicate must treat it as out of band
        rather than compare against None. Run under a non-None --since, the
        production-shaped path. The record carries ephemeral_1h > 0 so it
        enters the W1h branch: under a None gap, in_w1h_branch would
        otherwise be False and "contributes nothing to Z" vacuous."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts=_ttl_verdict_ts(600), request_id="n1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=100_000, cache_read=200_000,
                ts=_ttl_verdict_ts(0), request_id="n2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(
            _cache_rebuild_args(ttl_verdict=True, since="36500d"), roots=[fake_projects.parent]
        )
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["W5m/W1h"] == "1,100,000"
        assert root_row["X/Z"] == "0"

    def test_unparseable_timestamp_record_completes_the_report_and_contributes_nothing_to_z(
        self, fake_projects, capsys
    ):
        """An unparseable timestamp also yields a None gap. Only a run with
        --since unset reaches the --ttl-verdict block with such a record,
        since the block sits behind the in-scope check."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts=_ttl_verdict_ts(0), request_id="u1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=100_000, cache_read=200_000,
                ts="not-a-timestamp", request_id="u2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["W5m/W1h"] == "1,100,000"
        assert root_row["X/Z"] == "0"

    def test_pure_1h_in_band_write_with_a_model_changed_miss_reason_prints_no_cross_tab(
        self, fake_projects, capsys
    ):
        """The cache-miss-reason cross-tab stays keyed on the cause table's
        own "idle 5m-1h" population -- calls whose write the gap is claimed
        to have forced. A warm in-band read has no cache miss to explain, so
        the widened accumulator flag must not feed it."""
        records = _pure_1h_write_plus_read_records(360)
        records[1]["message"]["diagnostics"] = {
            "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 100_000},
        }
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert "cache-miss-reason cross-tab" not in out
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 0


class TestCacheRebuildTtlVerdictTierSplitInBandPure1hRead:
    """The mixed root's tier-split cross-check reads the 1h-to-5m switch
    delta, so a pure-1h write's in-band read moves its 1h slice.

    Fixture arithmetic, claude-sonnet-5 per MTok: write_5m $2.50, write_1h
    $4.00, read $0.20. call1 (session start) writes 100,000 ephemeral_5m;
    call2, 360s later, writes 200,000 ephemeral_1h and reads 200,000.
    - 5m slice: call1 is a session start, so no rescue term; switch cost =
      0.1 x $1.50 = $0.15, net = -$0.15, so the slice favors 5m.
    - 1h slice: tier saving = 0.2 x $1.50 = $0.30. Without the read's expiry
      term net = +$0.30 (favors 5m, "agree"). With it: $0.30 - 0.2 x $2.30 =
      -$0.16 (favors 1h, "disagree").
    The 1h slice changes sign only when read / W1h_slice exceeds
    (4.00 - 2.50) / (2.50 - 0.20) ~ 0.652; here it is 1.0."""

    def test_in_band_read_beside_a_pure_1h_write_flips_the_1h_slice_and_the_agreement_label(
        self, fake_projects, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100_000, ts=_ttl_verdict_ts(0), request_id="t1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=200_000, cache_read=200_000,
                ts=_ttl_verdict_ts(360), request_id="t2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split == {
            "favors_5m_slice": "5m",
            "favors_1h_slice": "1h",
            "agreement": "disagree",
        }


# Per-call net and margin volume for the rate-footing fixtures, claude-sonnet-5
# per MTok: write_5m $2.50, write_1h $4.00, read $0.20. A fast-mode call is
# 2x, a US-inference-geo call 1.1x, and a call with both 2.2x.
_RATE_FOOTING_VARIANTS = {
    "fast": {"speed": "fast", "inference_geo": None},
    "us": {"speed": None, "inference_geo": "us"},
    "fast+us": {"speed": "fast", "inference_geo": "us"},
}
# Plain-run and per-variant Net$ for _rate_footing_records, hand-derived in
# TestCacheRebuildTtlVerdictRateMultiplierFooting's docstring.
_RATE_FOOTING_NET = {
    ("5m", None): "$47.70", ("5m", "fast"): "$95.40", ("5m", "us"): "$52.47", ("5m", "fast+us"): "$104.94",
    ("1h", None): "$76.44", ("1h", "fast"): "$152.88", ("1h", "us"): "$84.08", ("1h", "fast+us"): "$168.17",
}


def _rate_footing_records(tier: str, variant: str | None) -> list[dict]:
    """Two-call corpus for one tier, every record carrying the named rate
    variant's speed/inference_geo (None for the plain run)."""
    rate_fields = _RATE_FOOTING_VARIANTS[variant] if variant else {}
    if tier == "5m":
        return [
            _priced(
                "claude-sonnet-5", ephemeral_5m=108_500_000, ts=_ttl_verdict_ts(0),
                request_id="rf-1", **rate_fields,
            ),
            _priced(
                "claude-sonnet-5", ephemeral_5m=91_500_000, ts=_ttl_verdict_ts(360),
                request_id="rf-2", **rate_fields,
            ),
        ]
    return [
        _priced(
            "claude-sonnet-5", ephemeral_1h=200_000_000, ts=_ttl_verdict_ts(0),
            request_id="rf-1", **rate_fields,
        ),
        _priced(
            "claude-sonnet-5", cache_read=97_200_000, ts=_ttl_verdict_ts(360),
            request_id="rf-2", **rate_fields,
        ),
    ]


def _run_ttl_verdict_on_origin(fake_projects, capsys, origin: str, records: list[dict]) -> tuple[dict, dict]:
    """Write `records` as the named origin's only traffic, run --ttl-verdict,
    and return (root row, bucket summary). Re-running on the same
    fake_projects overwrites the previous corpus."""
    if origin == "subagent":
        for rec in records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", records)
    else:
        _write_jsonl(fake_projects / "sess.jsonl", records)
    _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
    out = capsys.readouterr().out
    return (
        _extract_ttl_verdict_root_row(out, origin, "account-1"),
        _extract_ttl_verdict_summary(out, origin),
    )


class TestCacheRebuildTtlVerdictRateMultiplierFooting:
    """The margin denominator carries the same fast-mode (2x) and
    US-inference-geo (1.1x) multipliers the per-call net does, so a uniform
    rate multiplier scales Net$ and leaves every ratio-derived cell
    unchanged.

    A pre-fix denominator omitting the multiplier is m x the correct ratio
    too large, so Clears differs between a plain and a multiplied run only
    when the plain ratio lies in [0.10 / m, 0.10). The fixtures place the
    plain ratio inside the tightest window, m = 1.1 -> [0.0909, 0.10), with
    ~0.0045 of headroom either side, so it also sits inside m = 2 ->
    [0.05, 0.10) and m = 2.2 -> [0.0455, 0.10). Token counts are ~100x a
    minimal fixture so cent rounding of Net$ moves the ratio by ~1e-5.

    Rates per MTok (claude-sonnet-5): write_5m $2.50, write_1h $4.00, read
    $0.20.

    5m-tier corpus: call1 (session start, not idle) writes 108,500,000
    ephemeral_5m; call2 (360s later, idle) writes 91,500,000 -> W5m = 200M,
    X = 91.5M. Net = 91.5 x (4.00 - 0.20) - 200 x (4.00 - 2.50) = 347.70 -
    300 = $47.70; volume = 200 x $2.50 = $500; ratio 0.0954.

    1h-tier corpus: call1 writes 200,000,000 ephemeral_1h at session start;
    call2 (360s later, read-only so this class does not depend on how a
    pure-1h write is banded) reads 97,200,000 -> W1h = 200M, Z = 97.2M <
    W1h. Net = 200 x $1.50 - 97.2 x $2.30 = 300 - 223.56 = $76.44; volume =
    200 x $4.00 = $800; ratio 0.09555.

    Multiplying every record by m scales Net$ by m (expected values in
    _RATE_FOOTING_NET) and leaves W5m/W1h, X/Z, Favors, Clears and Share
    as-is."""

    @pytest.mark.parametrize("origin", ["main", "subagent"])
    @pytest.mark.parametrize("tier", ["5m", "1h"])
    @pytest.mark.parametrize("variant", ["fast", "us", "fast+us"])
    def test_uniform_rate_multiplier_scales_net_and_leaves_every_ratio_cell_unchanged(
        self, fake_projects, capsys, origin, tier, variant
    ):
        plain_row, plain_summary = _run_ttl_verdict_on_origin(
            fake_projects, capsys, origin, _rate_footing_records(tier, None)
        )
        multiplied_row, multiplied_summary = _run_ttl_verdict_on_origin(
            fake_projects, capsys, origin, _rate_footing_records(tier, variant)
        )

        # Absolute assertions on the plain run: a price-table change or a
        # mis-bucketed fixture must fail loudly rather than pass vacuously.
        assert plain_row["Tier"] == tier
        assert plain_row["W5m/W1h"] == "200,000,000"
        assert plain_row["Net$"] == _RATE_FOOTING_NET[(tier, None)]
        assert plain_row["Clears"] == "False"

        assert multiplied_row["Net$"] == _RATE_FOOTING_NET[(tier, variant)]
        for cell in ("Tier", "W5m/W1h", "X/Z", "Favors", "Clears", "Share"):
            assert multiplied_row[cell] == plain_row[cell], cell
        assert multiplied_summary == plain_summary

    @pytest.mark.parametrize("origin", ["main", "subagent"])
    def test_mixed_rate_5m_tier_root_denominator_carries_every_records_multiplier(
        self, fake_projects, capsys, origin
    ):
        """One 5m-tier root interleaving default, fast, US-geo and fast+US
        records. Per-record weight (tokens in millions, m the multiplier,
        idle = 360s after the prior call):
        - call1: 650, m 1, session start (not idle)
        - call2: 40, m 2 (fast), idle
        - call3: 350, m 1.1 (US geo), idle
        - call4: 40, m 2.2 (both), idle

        Net = -1.5 x 650 + 2.3 x (2 x 40 + 1.1 x 350 + 2.2 x 40) = -975 +
        184 + 885.5 + 202.4 = $296.90.
        Volume = 2.5 x (650 + 80 + 385 + 88) = $3,007.50; ratio 0.09872,
        under the 10% margin.

        Dropping any one record's multiplier from the volume raises the
        ratio to at least 0.10: fast 0.1021, US 0.1017, fast+US 0.1028. The
        window is [0.10 x (1 - s_min), 0.10) with s_min = 0.0291 (the US
        record's excess share of the volume), i.e. [0.0971, 0.10)."""
        records = [
            _priced("claude-sonnet-5", ephemeral_5m=650_000_000, ts=_ttl_verdict_ts(0), request_id="m1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=40_000_000, speed="fast",
                ts=_ttl_verdict_ts(360), request_id="m2",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_5m=350_000_000, inference_geo="us",
                ts=_ttl_verdict_ts(720), request_id="m3",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_5m=40_000_000, speed="fast", inference_geo="us",
                ts=_ttl_verdict_ts(1080), request_id="m4",
            ),
        ]
        root_row, _summary = _run_ttl_verdict_on_origin(fake_projects, capsys, origin, records)

        assert root_row["Tier"] == "5m"
        assert root_row["W5m/W1h"] == "1,080,000,000"
        assert root_row["Net$"] == "$296.90"
        assert root_row["Clears"] == "False"

    @pytest.mark.parametrize("origin", ["main", "subagent"])
    def test_mixed_rate_1h_tier_root_denominator_carries_every_records_multiplier(
        self, fake_projects, capsys, origin
    ):
        """One 1h-tier root interleaving default, fast, US-geo and fast+US
        1h writes (tokens in millions, m the multiplier; writes 30s apart, so
        never in either idle band), then two read-only idle calls:
        - writes: 100 (m 1, session start), 80 (m 2), 550 (m 1.1), 40 (m 2.2)
        - reads, 360s after the prior call: 300 (m 1), 80 (m 2)

        Net = 1.5 x (100 + 160 + 605 + 88) - 2.3 x (300 + 160) = 1,429.50 -
        1,058.00 = $371.50. Volume = 4 x 953 = $3,812; ratio 0.09746, under
        the 10% margin. Z = 380M < W1h = 770M, so the token tiebreaker
        agrees with the dollar sign and only the margin can fail.

        Dropping any one write's multiplier from the volume raises the ratio
        to at least 0.10: fast 0.1064, US 0.1034, fast+US 0.1026. The window
        is [0.10 x (1 - s_min), 0.10) with s_min = 0.0504 (the fast+US
        write's excess share of the volume), i.e. [0.0950, 0.10)."""
        records = [
            _priced("claude-sonnet-5", ephemeral_1h=100_000_000, ts=_ttl_verdict_ts(0), request_id="h1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=80_000_000, speed="fast",
                ts=_ttl_verdict_ts(30), request_id="h2",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_1h=550_000_000, inference_geo="us",
                ts=_ttl_verdict_ts(60), request_id="h3",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_1h=40_000_000, speed="fast", inference_geo="us",
                ts=_ttl_verdict_ts(90), request_id="h4",
            ),
            _priced("claude-sonnet-5", cache_read=300_000_000, ts=_ttl_verdict_ts(450), request_id="h5"),
            _priced(
                "claude-sonnet-5", cache_read=80_000_000, speed="fast",
                ts=_ttl_verdict_ts(810), request_id="h6",
            ),
        ]
        root_row, _summary = _run_ttl_verdict_on_origin(fake_projects, capsys, origin, records)

        assert root_row["Tier"] == "1h"
        assert root_row["W5m/W1h"] == "770,000,000"
        assert root_row["X/Z"] == "380,000,000"
        assert root_row["Net$"] == "$371.50"
        assert root_row["Clears"] == "False"


class TestCacheRebuildTtlVerdictUnpricedDisclosure:
    """--ttl-verdict's own unpriced-model disclosure (pooled across both
    directions and every root) -- mirrors the sibling
    unpriced_switch_delta_turns disclosure's own coverage
    (TestCacheRebuildSubagentDispersion, around line 9593)."""

    def test_unpriced_model_call_prints_the_disclosure_line_once_not_twice(self, fake_projects, capsys):
        """A single unpriced-model call is priced once per record, before
        either the W5m or W1h branch runs, rather than once per boundary
        iteration -- the disclosure count must reflect the one underlying
        call, not the two boundaries (primary and sensitivity) it would
        otherwise be priced at."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-unknown-model", ephemeral_5m=250_000, ts="2026-08-01T10:00:00.000Z", request_id="u1"),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        match = re.search(
            r"\((\d[\d,]*) calls / (\d[\d,]*) tokens excluded from every Net\$ figure", out,
        )
        assert match is not None, "unpriced ttl-verdict disclosure line not found in output"
        assert match.group(1) == "1"
        assert match.group(2) == "250,000"

    def test_unpriced_mixed_tier_write_prints_the_disclosure_line_once_not_twice(self, fake_projects, capsys):
        """A single unpriced-model call carrying both ephemeral_1h and
        ephemeral_5m tokens (a mixed-tier write) enters both the W5m and
        the W1h branch, but must be counted once, not twice, since both
        branches price the same underlying call."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced(
                "claude-unknown-model", ephemeral_1h=100_000, ephemeral_5m=150_000,
                ts="2026-08-01T10:00:00.000Z", request_id="u2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        match = re.search(
            r"\((\d[\d,]*) calls / (\d[\d,]*) tokens excluded from every Net\$ figure", out,
        )
        assert match is not None, "unpriced ttl-verdict disclosure line not found in output"
        assert match.group(1) == "1"
        assert match.group(2) == "250,000"


class TestCacheRebuildTtlVerdictCacheMissReasonValidator:
    """Cross-tab the gap-derived idle-5m-1h cause against
    pricing._cache_miss_reason's own model_changed signal -- disclosed
    only, never overriding the gap-derived accumulation."""

    def test_agreeing_cache_miss_reason_prints_no_discrepancy_line(self, fake_projects, capsys):
        records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000,
                ts="2026-08-01T10:06:00.000Z", request_id="r2",
            ),
        ]
        records[1]["message"]["diagnostics"] = {
            "cache_miss_reason": {"type": "excessive_gap", "cache_missed_input_tokens": 200_000},
        }
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert "cache-miss-reason cross-tab" not in out
        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["X/Z"] == "200,000"

    def test_disagreeing_cache_miss_reason_prints_discrepancy_but_never_overrides_the_accumulator(
        self, fake_projects, capsys
    ):
        records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000,
                ts="2026-08-01T10:06:00.000Z", request_id="r2",
            ),
        ]
        records[1]["message"]["diagnostics"] = {
            "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 200_000},
        }
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        match = re.search(r"cache-miss-reason cross-tab: (\d+) of (\d+)", out)
        assert match is not None, "cache-miss-reason cross-tab discrepancy line not found in output"
        assert match.group(1) == "1"
        assert match.group(2) == "1"

        # The gap-derived classification still governs the cause breakdown
        # and the per-root accumulator, never pricing._cache_miss_reason's
        # own signal.
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 1
        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["X/Z"] == "200,000"


class TestCacheRebuildTtlVerdictDefaultPathRegression:
    def test_output_without_the_flag_is_unchanged_and_omits_the_new_section(self, fake_projects, capsys):
        """Same fixture and assertions as
        TestCacheRebuildSwitchDeltaArithmetic.test_default_rate_model_break_even_arithmetic
        -- a spot-check of the subagent row's W5m/X/Net$ cells, plus
        confirmation the new TTL-verdict section never appears, when
        --ttl-verdict is omitted."""
        records = [
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
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
        assert "## TTL-verdict" not in out
