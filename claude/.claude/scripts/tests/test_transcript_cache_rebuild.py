"""Tests for transcript_analysis/cache_rebuild.py (cmd_cache_rebuild): core report mechanics."""
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ._cache_rebuild_helpers import (
    _cache_rebuild_args,
    _extract_cache_rebuild_row,
    _extract_cache_rebuild_summary,
)
from .conftest import (
    _priced,
    _table_cols,
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


class TestCacheRebuildExcessPricing:
    """Direct unit coverage for _cache_rebuild_excess_dollars -- new pricing
    logic (the counterfactual warm-read leg) not exercised by any of
    _price_turn's own existing tests."""

    def test_fast_mode_multiplier_applies_to_both_write_and_warm_read_dollars(self):
        """Mirrors _price_turn's own fast-mode multiplier on the
        counterfactual warm-read leg too, not just the actual write leg --
        otherwise a fast-mode call's excess would overstate the gap between
        what was paid and what a warm hit would have cost."""
        usage = _priced("claude-sonnet-5", ephemeral_5m=1_000_000, speed="fast")["message"]["usage"]
        excess, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_excess_dollars("claude-sonnet-5", usage)
        # write: 1,000,000/1e6 * 2.00*1.25*2(fast) = 5.00; warm read:
        # 1,000,000/1e6 * 2.00*0.10*2(fast) = 0.40; excess = 4.60.
        assert excess == pytest.approx(4.60)
        assert unpriced_tokens == 0

    def test_unpriced_model_returns_none_excess_not_a_silent_zero(self):
        """A model absent from _MODEL_BASE_INPUT_RATES must not silently
        price its excess as $0 -- callers distinguish 'unpriced' from
        'priced at zero' via the None sentinel, matching _price_turn's own
        unpriced-model contract."""
        usage = _priced("claude-unknown-model", ephemeral_5m=1_000_000, input=10, output=5)["message"]["usage"]
        excess, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_excess_dollars("claude-unknown-model", usage)
        assert excess is None
        assert unpriced_tokens > 0

    def test_fable_5_1_warm_read_leg_uses_reduced_cache_read_multiplier(self):
        """This function re-derives rates["cache_read"] via its own
        _model_rates(model) call, independent of _price_turn -- cache-
        rebuild's entire thesis is a cache-read-vs-cache-write delta, the
        exact axis Fable 5.1's reduced cache-read multiplier deviates on, so
        this is the site most exposed to the Fable pricing change. Like
        test_transcript_cost.py::TestFablePricing, this validates rate
        arithmetic only -- it never confirms "claude-fable-5-1" is the exact
        string Claude Code writes to message.model."""
        usage = _priced("claude-fable-5-1", ephemeral_5m=1_000_000)["message"]["usage"]
        excess, unpriced_tokens = _mod.cache_rebuild_rules._cache_rebuild_excess_dollars("claude-fable-5-1", usage)
        # write: 1,000,000/1e6 * 10.00*1.25 = 12.50; warm read uses the
        # 0.025x reduced multiplier, not the standard 0.1x: 1,000,000/1e6 *
        # 10.00*0.025 = 0.25; excess = 12.25.
        assert excess == pytest.approx(12.25)
        assert unpriced_tokens == 0


class TestCacheRebuildClassification:
    """Direct coverage for _cache_rebuild_report's per-call cause
    classification and priced excess, against a single hand-built
    transcript exercising every case Verification item 1 in
    .claude/plans/context-cost-root-cause.md names."""

    def test_dedup_synthetic_exclusion_cause_classification_and_priced_excess(
        self, fake_projects, capsys
    ):
        """One transcript exercising every classification case:
        - a multi-record requestId run (dedups to one small, non-tail call)
        - a requestId-less <synthetic> entry (excluded from both pricing and
          the i/prev_ts/prev_model bookkeeping)
        - the transcript's own first call (session start, never an idle
          bucket)
        - a flat-field cache_creation fallback (unexplained at a sub-5-minute
          gap; its pricing-as-5m-only is covered separately by
          test_flat_cache_creation_fallback_priced_as_5m_only)
        - a record with no timestamp at all (timestamp-anomaly bucket)
        - a negative-gap clock-skew pair (timestamp-anomaly bucket)
        - a genuinely garbled, non-empty timestamp string (timestamp-anomaly
          bucket -- none of these three anomalies is silently folded into
          idle or unexplained)
        - a 6-minute gap (idle 5m-1h, priced excess hand-computed below)
        - a model switch at a sub-5-minute gap (model switch, not
          unexplained)
        """
        run_ts = "2026-08-01T10:01:00.000Z"
        records = [
            # i=0: first call ever -- session start, regardless of tail size.
            _priced("claude-sonnet-5", ephemeral_5m=150_000, ts="2026-08-01T10:00:00.000Z", request_id="req-first"),
            # Three raw records sharing one requestId: one JSONL record per
            # content block, dedup must collapse this run to one logical
            # call. Small (non-tail) so it doesn't add a 4th tail call.
            _priced("claude-sonnet-5", ephemeral_5m=500, ts=run_ts, request_id="req-multi", output=3),
            _priced("claude-sonnet-5", ephemeral_5m=500, ts=run_ts, request_id="req-multi", output=3),
            _priced("claude-sonnet-5", ephemeral_5m=500, ts=run_ts, request_id="req-multi", output=50),
            # requestId-less synthetic entry, large enough that its wrongful
            # inclusion would be obvious in the cause-breakdown totals below.
            _priced("<synthetic>", ephemeral_5m=500_000, ts="2026-08-01T10:02:00.000Z"),
            # gap from the merged run (10:01:00) is 120s: not idle, same
            # model -- unexplained. Flat-field fallback (no nested
            # cache_creation block) -- see test_flat_cache_creation_fallback_priced_as_5m_only
            # for the pricing-as-5m-only coverage this fixture shape doesn't
            # itself price.
            _priced(
                "claude-sonnet-5", flat_cache_creation=180_000,
                ts="2026-08-01T10:03:00.000Z", request_id="req-flat",
            ),
            # No timestamp at all -- distinct from the clock-skew case below
            # (both endpoints present but out of order): here the call's own
            # endpoint is missing, so no gap can be computed either way.
            _priced("claude-sonnet-5", ephemeral_5m=210_000, ts="", request_id="req-absentts"),
            # Clock skew: 30s BEFORE req-flat's own timestamp (the absent-ts
            # record above never updates the tracked "previous timestamp") --
            # negative gap, must classify as the explicit anomaly bucket too.
            _priced(
                "claude-sonnet-5", ephemeral_5m=250_000,
                ts="2026-08-01T10:02:30.000Z", request_id="req-negts",
            ),
            # 6-minute gap from the clock-skew record's own (still valid)
            # timestamp -- idle 5m-1h. Excess hand-computed below.
            _priced(
                "claude-sonnet-5", ephemeral_5m=300_000,
                ts="2026-08-01T10:08:30.000Z", request_id="req-idle",
            ),
            # 60s gap (not idle) but a different, still-priced model --
            # model switch, not unexplained.
            _priced(
                "claude-opus-5", ephemeral_5m=220_000,
                ts="2026-08-01T10:09:30.000Z", request_id="req-switch",
            ),
            # Genuinely garbled, non-empty timestamp string -- distinct from
            # the absent-timestamp case above, must land in the same
            # explicit anomaly bucket rather than raising or silently
            # excluding the call from the scan entirely.
            _priced(
                "claude-sonnet-5", ephemeral_5m=150_000,
                ts="not-a-date", request_id="req-malformed-ts",
            ),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        # Dedup: 11 raw records collapse to 8 logical calls (the 3-record
        # run counts once); the synthetic entry is excluded entirely (not 9).
        summary = _extract_cache_rebuild_summary(out)
        assert summary["scanned"] == "8"
        assert summary["tail"] == "7"

        # Cause-breakdown rows are (count, share%) -- only the count is
        # asserted here, per-cause share formatting is not this test's concern.
        assert _extract_cache_rebuild_row(out, "session start")[0] == 1
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 1
        assert _extract_cache_rebuild_row(out, "idle >1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "model switch")[0] == 1
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 1
        # The absent-timestamp, negative-gap, and malformed-timestamp
        # records all land here.
        assert _extract_cache_rebuild_row(out, "excluded (timestamp anomaly)")[0] == 3

        # 300,000 5m-tier cache-write tokens at claude-sonnet-5's $2.00/MTok
        # base: write $0.75 (1.25x), warm-read-equivalent $0.06 (0.1x),
        # excess $0.69. Single-transcript fixture: no other session was ever
        # active, so this lands in "everything idle", not "another session".
        assert _extract_cache_rebuild_row(out, "Everything idle (a break)") == (1, "0.69")
        assert _extract_cache_rebuild_row(out, "Another session active") == (0, "0.00")


class TestCacheRebuildGroupBoundary:
    """Verification item 1 (.claude/plans/context-cost-root-cause.md):
    is_first_call/gap_seconds/model_changed must reset at every
    _read_session_file_partitioned group boundary, not just once at the top
    of the whole flattened (main thread + subagent files) session."""

    def test_subagent_groups_own_first_call_never_inherits_main_threads_gap(
        self, fake_projects, capsys
    ):
        """A subagent group's own first call is a large (>=threshold)
        cache-write landing only 6 minutes after the main thread's own
        (small) first call -- in flattened file-concatenation order this
        would fall inside the main thread's own idle-5m-1h gap window.
        Classified per group instead, the subagent's own first call has no
        predecessor within its own group and must classify session start,
        never an idle-gap/model-switch/unexplained cause carried over from
        the main thread's prior call."""
        session_id = "sess-boundary"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
        ])
        subagent_rec = _priced(
            "claude-sonnet-5", ephemeral_5m=200_000,
            ts="2026-08-01T10:06:00.000Z", request_id="sub-1",
        )
        subagent_rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_cache_rebuild_summary(out)
        assert summary["scanned"] == "2"
        assert summary["tail"] == "1"

        assert _extract_cache_rebuild_row(out, "session start")[0] == 1
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "model switch")[0] == 0
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 0


class TestCacheRebuildOriginSplit:
    """Verification items 1-2 (.claude/plans/subagent-idle-gap-cache-rebuild-split.md):
    origin classification must be per-record via isSidechain, not by which
    source file (group) a record came from, and the sequential gap-chain
    used for cause classification must be keyed per (group, origin), not
    per group alone."""

    def test_main_and_subagent_idle_gap_rebuilds_each_land_in_their_own_origin_row(
        self, fake_projects, capsys
    ):
        """One main-thread and one subagent idle-5m-1h rebuild in the same
        fixture session: each origin's rebuild lands in its own row of the
        new origin-split block, and the corpus-wide cause-breakdown and
        concurrency-split totals this instrument already printed are
        unchanged by adding the split."""
        session_id = "sess-origin-split"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=100_000,
                ts="2026-08-01T10:06:00.000Z", request_id="main-2",
            ),
        ])
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000,
                ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        for rec in subagent_records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 2
        assert _extract_cache_rebuild_row(out, "Everything idle (a break)") == (2, "0.69")

        assert _extract_cache_rebuild_row(out, "main") == (1, "0.23")
        assert _extract_cache_rebuild_row(out, "subagent") == (1, "0.46")

    def test_inline_sidechain_record_in_main_file_counts_as_subagent_origin_and_own_gap_chain(
        self, fake_projects, capsys
    ):
        """A record carrying isSidechain: true, written into the MAIN
        transcript file (not a subagents/*.jsonl file), must count as
        subagent origin -- a group_index > 0 classifier would misfile this
        record's tokens under "main" instead. It must also be classified
        against its OWN origin's prior call, not whichever record precedes
        it in file order: a chain keyed per group alone would compare this
        record to the immediately preceding main-thread record instead of
        treating it as its own chain's first-ever call."""
        main_first = _priced(
            "claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:00:00.000Z", request_id="main-1",
        )
        # 350s after main-1. Correctly classified: the SUBAGENT chain's own
        # first-ever call (session start), regardless of the elapsed time
        # from main-1 -- a chain shared with the main thread would instead
        # see this as the group's 2nd record and classify idle 5m-1h.
        sidechain_first = _priced(
            "claude-sonnet-5", ephemeral_5m=150_000, ts="2026-08-01T10:05:50.000Z", request_id="sub-inline-1",
        )
        sidechain_first["isSidechain"] = True
        # Main thread's own 2nd call: 355s after main-1, but only 5s after
        # the inline sidechain record above. Correctly classified against
        # main-1 (idle 5m-1h) -- a chain shared across origins would instead
        # compare it to the sidechain record 5s earlier and classify
        # unexplained.
        main_second = _priced(
            "claude-sonnet-5", ephemeral_5m=150_000, ts="2026-08-01T10:05:55.000Z", request_id="main-2",
        )
        _write_jsonl(fake_projects / "sess.jsonl", [main_first, sidechain_first, main_second])

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        # Two session starts (main's own, and the sidechain's own first
        # call), one idle-5m-1h (main's 2nd call), zero unexplained -- a
        # group-level (not per-origin) chain would instead produce one
        # session start, one idle-5m-1h (the sidechain record, wrongly),
        # and one unexplained (main's 2nd call, wrongly). The idle-5m-1h
        # COUNT is coincidentally 1 either way, which is why the
        # session-start/unexplained counts, and the per-origin X split
        # below, are this test's real assertions.
        assert _extract_cache_rebuild_row(out, "session start")[0] == 2
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 1
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 0

        subagent_row = _table_cols(out, header_contains="Ratio", row_contains="subagent")
        assert subagent_row["W5m"] == "150,000"
        assert subagent_row["X"] == "0"
        main_row = _table_cols(out, header_contains="Ratio", row_contains="main")
        assert main_row["W5m"] == "250,000"
        assert main_row["X"] == "150,000"


class TestCacheRebuildCacheTierGapMismatch:
    """Verification item 4: a call's cache-write tier, not just its elapsed
    gap, gates the idle-5m-1h cause -- a purely ephemeral_1h-tier write
    inside a <1h gap cannot have been forced by that gap's TTL expiry, since
    the 1h-tier cache would still be warm."""

    def test_pure_1h_tier_write_in_5m_1h_gap_reclassifies_unexplained(self, fake_projects, capsys):
        """A 6-minute gap whose tail write is entirely ephemeral_1h-tier
        (no ephemeral_5m tokens at all) falls to unexplained, not idle
        5m-1h -- the 1h-TTL cache can't have expired inside 6 minutes."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=200_000,
                ts="2026-08-01T10:06:00.000Z", request_id="r2-pure-1h",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 1

    def test_mixed_tier_write_in_5m_1h_gap_still_classifies_idle(self, fake_projects, capsys):
        """The same 6-minute gap, but the tail write carries SOME
        ephemeral_5m tokens alongside its ephemeral_1h tokens -- the
        5m-tier portion could genuinely have been forced by the gap, so
        this stays idle 5m-1h, not unexplained."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=50_000, ephemeral_5m=150_000,
                ts="2026-08-01T10:06:00.000Z", request_id="r2-mixed",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 1
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 0


class TestCacheRebuildThresholdBoundary:
    def test_exactly_threshold_tokens_counts_as_tail_one_below_does_not(self, fake_projects, capsys):
        """A call writing exactly the default 100,000-token threshold counts
        as a large rebuild; one writing 99,999 does not -- the >= boundary
        the corpus's own tail-call figures depend on."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=99_999, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced("claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:00:01.000Z", request_id="r2"),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        summary = _extract_cache_rebuild_summary(capsys.readouterr().out)
        assert summary["scanned"] == "2"
        assert summary["tail"] == "1"


class TestCacheRebuildConcurrencySplit:
    def test_own_subagent_activity_never_self_matches_and_cross_root_activity_flags_concurrent(
        self, tmp_path, capsys
    ):
        """Three-root fixture (Verification item 3): root A's own session
        has two idle-gap tail calls, and between them a subagent record
        whose own timestamp falls INSIDE the first gap despite being
        processed after both main-thread calls (subagent files are appended
        after the main thread in _read_session_file's file-concatenation
        order, not re-sorted by timestamp) -- proving a transcript's own
        later record never counts as "another session" even when it lands
        chronologically inside its own gap. Root B's own call falls inside
        the SECOND gap, correctly flagging that gap concurrent. Root C is
        valid but holds no transcripts at all -- the realistic state of a
        rarely-used account, exercised here for no crash and no false
        signal on either other root's classification."""
        proj_slug = "-home-user-repo"
        root_a = _write_cost_root(tmp_path, "acct-a", proj_slug, "sess-a", [
            _priced("claude-sonnet-5", ephemeral_5m=500, ts="2026-08-01T10:00:00.000Z", request_id="a-1"),
            # gap from a-1 is 600s -- idle 5m-1h. Window (10:00:00, 10:10:00).
            _priced("claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:10:00.000Z", request_id="a-2"),
            # gap from a-2 is 3,900s -- idle >1h. Window (10:10:00, 11:15:00).
            _priced("claude-sonnet-5", ephemeral_1h=150_000, ts="2026-08-01T11:15:00.000Z", request_id="a-3"),
        ])
        subagent_rec = _priced(
            "claude-sonnet-5", ephemeral_5m=500, ts="2026-08-01T10:05:00.000Z", request_id="a-sub",
        )
        subagent_rec["isSidechain"] = True
        _write_subagent_jsonl(root_a / proj_slug, "sess-a", "agent-1", [subagent_rec])

        # Falls inside the SECOND gap's window -- that gap must flag concurrent.
        root_b = _write_cost_root(tmp_path, "acct-b", proj_slug, "sess-b", [
            _priced("claude-sonnet-5", ephemeral_5m=500, ts="2026-08-01T10:35:00.000Z", request_id="b-1"),
        ])

        root_c = tmp_path / "acct-c"
        root_c.mkdir()  # valid directory, no project dirs or transcripts at all

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[root_a, root_b, root_c])
        out = capsys.readouterr().out

        assert "[unverified]" in out

        # 200,000 5m-tier tokens: write $0.50, warm read $0.04, excess $0.46.
        assert _extract_cache_rebuild_row(out, "Everything idle (a break)") == (1, "0.46")
        # 150,000 1h-tier tokens: write $0.60, warm read $0.03, excess $0.57.
        assert _extract_cache_rebuild_row(out, "Another session active") == (1, "0.57")

        # account-1 = acct-a (both idle-gap rebuilds live here, including
        # the one whose own gap contained its own subagent's activity);
        # account-2 = acct-b (its own call is other-session activity, never
        # itself a rebuild); account-3 = acct-c (valid but empty).
        assert _extract_cache_rebuild_row(out, "account-1") == (2, "1.03")
        assert _extract_cache_rebuild_row(out, "account-2") == (0, "0.00")
        assert _extract_cache_rebuild_row(out, "account-3") == (0, "0.00")


class TestCacheRebuildMultiRootRegression:
    def test_hand_computed_rebuild_count_and_dollar_total_match_the_union_across_two_roots(
        self, tmp_path, capsys
    ):
        """Verification item 4: a generated corpus of known composition
        (150 calls per root, 300 total), split across two synthetic roots.
        Every 5th call after the first is a large (150,000-token, 5m-tier)
        idle-gap rebuild, reached by a 400-second gap (inside the 5m-1h
        idle bucket); every other call is a small, closely-spaced (10s)
        filler that never crosses the threshold. Regresses against the
        UNION total across both roots, not one root's own count --
        aggregation across roots is exactly what this subcommand adds, and
        a running total or requestId dedup scoped per-root instead of
        post-union would hide here."""
        calls_per_root = 150
        idle_gap_indices = range(5, calls_per_root, 5)  # 5, 10, ..., 145 -> 29 per root
        base_ts = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)

        def _build_records(root_label: str) -> list[dict]:
            records = []
            ts = base_ts
            for i in range(calls_per_root):
                if i > 0:
                    ts += timedelta(seconds=400 if i % 5 == 0 else 10)
                is_rebuild = i > 0 and i % 5 == 0
                records.append(_priced(
                    "claude-sonnet-5",
                    ephemeral_5m=150_000 if is_rebuild else 100,
                    ts=ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    request_id=f"{root_label}-{i}",
                ))
            return records

        root_x = _write_cost_root(tmp_path, "acct-x", "-home-user-repo", "sess-x", _build_records("x"))
        root_y = _write_cost_root(tmp_path, "acct-y", "-home-user-repo", "sess-y", _build_records("y"))

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[root_x, root_y])
        out = capsys.readouterr().out

        expected_rebuilds = len(idle_gap_indices) * 2
        # 150,000 5m-tier tokens at $2.00/MTok base: write $0.375, warm read
        # $0.03, excess $0.345 per rebuild.
        expected_excess = expected_rebuilds * 0.345

        summary = _extract_cache_rebuild_summary(out)
        assert summary["tail"] == str(expected_rebuilds)

        idle_rebuilds, _idle_excess = _extract_cache_rebuild_row(out, "Everything idle (a break)")
        concurrent_rebuilds, _concurrent_excess = _extract_cache_rebuild_row(out, "Another session active")
        assert idle_rebuilds + concurrent_rebuilds == expected_rebuilds

        total_rebuilds, total_excess = _extract_cache_rebuild_row(out, "Total idle-gap rebuilds")
        assert total_rebuilds == expected_rebuilds
        assert total_excess == f"{expected_excess:,.2f}"


class TestCacheRebuildNoRedactMultiRootRefusal:
    def test_no_redact_refused_by_cache_rebuild_report_itself_even_when_called_directly(self, tmp_path):
        """Defense-in-depth, mirroring _cost_report's own version of this
        test: _cache_rebuild_report must refuse the multi-root + --no-redact
        combination itself rather than trusting that _resolve_cost_roots
        already validated it -- every test in this module calls
        _cache_rebuild_report directly, bypassing that CLI-level boundary."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", ephemeral_5m=100_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", ephemeral_5m=100_000)])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(no_redact=True), roots=[root_a, root_b])
        assert exc_info.value.code == 2

    def test_no_redact_refused_with_ttl_verdict_and_multi_root(self, tmp_path, capsys):
        """--ttl-verdict's own accumulation family is keyed on
        (origin, root_ordinal), carrying the identical multi-root +
        --no-redact leak risk as the pooled figures the sibling test above
        covers. This needs its own test rather than an edit to the sibling
        test, since ttl_verdict defaults to False on _cache_rebuild_args and
        every other test in this class leaves it there."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", ephemeral_5m=100_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", ephemeral_5m=100_000)])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cache_rebuild._cache_rebuild_report(
                _cache_rebuild_args(no_redact=True, ttl_verdict=True), roots=[root_a, root_b],
            )
        assert exc_info.value.code == 2
        out = capsys.readouterr().out
        assert "acct-a" not in out
        assert "acct-b" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out


class TestCacheRebuildArgparseWiring:
    def test_parses_since_threshold_and_extra_config_dirs(self):
        """cache-rebuild's real argparse wiring, not just the hand-rolled
        _cache_rebuild_args() test shim -- --since defaults to
        _CACHE_REBUILD_DEFAULT_SINCE ('30d'), unlike the shim's own
        since=None default, so a caller relying on the shim alone would
        never catch the two drifting apart."""
        parser = _mod.build_parser()
        args = parser.parse_args(["cache-rebuild"])
        assert args.since == _mod._CACHE_REBUILD_DEFAULT_SINCE
        assert args.threshold == _mod._CACHE_REBUILD_DEFAULT_THRESHOLD
        assert args.extra_config_dirs is None

        args = parser.parse_args([
            "cache-rebuild", "--since", "7d", "--threshold", "50000",
            "--config-dir", "/tmp/acct-b",
        ])
        assert args.since == "7d"
        assert args.threshold == 50_000
        assert args.extra_config_dirs == ["/tmp/acct-b"]


