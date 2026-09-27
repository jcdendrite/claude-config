"""Tests for transcript_analysis/cache_rebuild.py (cmd_cache_rebuild): --ttl-verdict per-root accumulation and
dominance reduction."""
import importlib.util
import sys
from pathlib import Path

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


class TestCacheRebuildTtlVerdictPerRootAccumulation:
    """Full-report coverage of --ttl-verdict's own per-(origin, root_ordinal)
    accumulation and reduction, on synthetic corpora built via fake_projects/
    fake_config_dir_factory."""

    def test_main_bucket_5m_tier_root_reaches_adopt(self, fake_projects, capsys):
        """A clean 5m-tier main-thread root (W5m=1,000,000, X=500,000,
        comfortably clearing margin) reaches 'adopt' for the main bucket.
        A third, idle-gap warm read (300,000 tokens, no write at all)
        accumulates Z=300,000 for this root, but apply_tiebreaker is False
        for a 5m-tier root, so this read's presence or absence never gates
        its own clears (see
        test_main_bucket_5m_tier_root_reaches_adopt_despite_z_w1h_wash
        below for the same root reaching 'adopt' with no read at all)."""
        _write_jsonl(fake_projects / "sess.jsonl", _ttl_verdict_5m_tier_adopt_records())
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "1"
        assert summary["consistent_1h"] == "0"
        assert summary["verdict"] == "adopt"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["W5m/W1h"] == "1,000,000"
        assert root_row["X/Z"] == "500,000"
        assert root_row["Favors"] == "1h"
        assert root_row["Clears"] == "True"

    def test_main_bucket_5m_tier_root_reaches_adopt_despite_z_w1h_wash(self, fake_projects, capsys):
        """Regression test guarding the tiebreaker-scope fix: apply_tiebreaker
        is False for a 5m-tier root, so its own Z==W1h==0 wash (no idle-gap
        read at all) never gates clears, and this root adopts despite the
        wash -- same clean 5m-tier root as
        test_main_bucket_5m_tier_root_reaches_adopt above (W5m=1,000,000,
        X=500,000), but with no third call at all."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="w1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=500_000,
                ts="2026-08-01T10:06:00.000Z", request_id="w2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "1"
        assert summary["verdict"] == "adopt"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Favors"] == "1h"
        assert root_row["Clears"] == "True"

    def test_subagent_bucket_1h_tier_root_reaches_adopt(self, fake_projects, capsys):
        """A clean 1h-tier subagent-origin root (W1h nonzero from a
        single session-start write, Z=0) reaches 'adopt' for the subagent
        bucket, favoring a drop to 5m."""
        records = [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="s1"),
        ]
        for rec in records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "subagent")
        assert summary["consistent_1h"] == "1"
        assert summary["verdict"] == "adopt"

        root_row = _extract_ttl_verdict_root_row(out, "subagent", "account-1")
        assert root_row["W5m/W1h"] == "1,000,000"
        assert root_row["X/Z"] == "0"
        assert root_row["Favors"] == "5m"
        assert root_row["Clears"] == "True"

    def test_dominance_gate_evaluates_each_origin_independently_for_the_same_root(
        self, fake_projects, capsys
    ):
        """The main-origin and subagent-origin buckets share the same root
        ordinal (account-1) but accumulate independently, so one origin's
        traffic can clear the dominance threshold while the other's stays
        near-tie for that same root ordinal.

        - Main gets a clean 5m-tier root (share 1.000, clears).
        - Subagent gets the near-tie mixed root shape (share 0.870, excluded(near-tie))."""
        _write_jsonl(fake_projects / "sess.jsonl", _ttl_verdict_5m_tier_adopt_records())
        subagent_records = _ttl_verdict_near_tie_mixed_root_records(request_id_prefix="sa")
        for rec in subagent_records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        main_summary = _extract_ttl_verdict_summary(out, "main")
        assert main_summary["consistent_5m"] == "1"
        assert main_summary["excluded"] == "0"

        subagent_summary = _extract_ttl_verdict_summary(out, "subagent")
        assert subagent_summary["consistent_5m"] == "0"
        assert subagent_summary["excluded"] == "1"

        main_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert main_row["Share"] == "1.000"
        assert main_row["Clears"] == "True"

        subagent_row = _extract_ttl_verdict_root_row(out, "subagent", "account-1")
        assert subagent_row["Share"] == "0.870"
        assert subagent_row["Clears"] == "excluded(near-tie)"

    def test_per_root_w5m_cells_sum_to_the_pooled_per_origin_w5m_row(self, tmp_path, capsys):
        """Reconciliation guard (plan Verification section): the new
        per-(origin, root_ordinal) W5m accumulator must never drift from
        the existing pooled w5m_by_origin figure it duplicates at finer
        grain -- reuses the dispersion coverage-disclosure extractor's own
        "sum the parts, compare to the pooled row" pattern
        (TestCacheRebuildSubagentDispersion, around line 9549)."""
        session_id = "sess-reconcile-root"
        proj_slug = "-home-user-repo"
        root_x = _write_cost_root(tmp_path, "acct-x", proj_slug, session_id, [])
        root_y = _write_cost_root(tmp_path, "acct-y", proj_slug, session_id, [])

        records_x = [
            _priced("claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:00:00.000Z", request_id="x-1"),
        ]
        for rec in records_x:
            rec["isSidechain"] = True
        _write_subagent_jsonl(root_x / proj_slug, session_id, "agent-x", records_x)

        records_y = [
            _priced("claude-sonnet-5", ephemeral_5m=250_000, ts="2026-08-01T10:00:00.000Z", request_id="y-1"),
        ]
        for rec in records_y:
            rec["isSidechain"] = True
        _write_subagent_jsonl(root_y / proj_slug, session_id, "agent-y", records_y)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[root_x, root_y])
        out = capsys.readouterr().out

        pooled_row = _table_cols(out, header_contains="Ratio", row_contains="subagent")
        pooled_w5m = int(pooled_row["W5m"].replace(",", ""))

        root_1 = _extract_ttl_verdict_root_row(out, "subagent", "account-1")
        root_2 = _extract_ttl_verdict_root_row(out, "subagent", "account-2")
        per_root_sum = int(root_1["W5m/W1h"].replace(",", "")) + int(root_2["W5m/W1h"].replace(",", ""))

        assert per_root_sum == pooled_w5m == 350_000
        # Redacted by default: the raw account names/paths never leak into
        # the per-root table, matching this file's own convention for every
        # other redacted per-account table.
        assert "acct-x" not in out
        assert "acct-y" not in out
        assert str(root_x) not in out
        assert str(root_y) not in out

    def test_two_roots_of_different_tiers_in_same_bucket_combine_into_bucket_verdict(
        self, tmp_path, capsys
    ):
        """Two roots landing in the same bucket but on different tiers must
        both feed that bucket's verdict through the real accumulation loop,
        not just the pure verdict function directly. account-1 (5m-tier)
        reuses _ttl_verdict_5m_tier_adopt_records; account-2 (1h-tier)
        reuses _ttl_verdict_1h_tier_non_wash_disagreement_records -- closing
        the multi-tier wiring gap those single-root tests can't cover on
        their own."""
        root_a = _write_cost_root(
            tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _ttl_verdict_5m_tier_adopt_records(),
        )
        root_b = _write_cost_root(
            tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
            _ttl_verdict_1h_tier_non_wash_disagreement_records(),
        )
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[root_a, root_b])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "1"
        assert summary["consistent_1h"] == "1"
        assert summary["verdict"] == "decline"

        root_1 = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_1["W5m/W1h"] == "1,000,000"
        assert root_1["X/Z"] == "500,000"
        assert root_1["Favors"] == "1h"
        assert root_1["Clears"] == "True"

        root_2 = _extract_ttl_verdict_root_row(out, "main", "account-2")
        assert root_2["W5m/W1h"] == "1,000,000"
        assert root_2["X/Z"] == "800,000"
        assert root_2["Favors"] == "1h"
        assert root_2["Clears"] == "False"

    def test_hand_computed_w1h_and_z_totals_match_known_fixture(self, fake_projects, capsys):
        """A small, hand-computed 1h-tier fixture.

        call1 (session start) writes 200,000 ephemeral_1h tokens (W1h
        only -- session start is never idle-gap-caused).
        call2, a 6-minute-gap warm read of 150,000 tokens with no write
        at all, is idle 5m-1h under a live 1h tier (W1h unchanged,
        Z += 150,000).
        call3, a further 6-minute-gap PURE ephemeral_1h-tier write of
        100,000 tokens, reclassifies unexplained (the 1h cache can't have
        expired inside 6 minutes), so it adds to W1h. It adds 0 to Z only
        because its own cache_read is 0 -- a pure-1h write that also read
        would add those read tokens to Z.

        Expected totals -- W1h=300,000, Z=150,000 -- are known in
        advance, not derived from the code under test."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_1h=200_000, ts="2026-08-01T10:00:00.000Z", request_id="w1"),
            _priced(
                "claude-sonnet-5", cache_read=150_000,
                ts="2026-08-01T10:06:00.000Z", request_id="w2",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_1h=100_000,
                ts="2026-08-01T10:12:00.000Z", request_id="w3",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["W5m/W1h"] == "300,000"
        assert root_row["X/Z"] == "150,000"

    def test_root_with_both_w5m_and_w1h_nonzero_contributes_no_verdict_for_that_bucket(
        self, fake_projects, capsys
    ):
        """A root paying both tiers simultaneously in this window has a
        share of 0.500, below _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN,
        so it is excluded(near-tie). The break-even algebra assumes a
        single live tier per root per window, so a root with no data gets
        the same treatment. The row still prints with its own exclusion
        reason. The four summary fields below are unaffected by the row
        now always printing -- showing the row never moves the gate."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="mix-1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=500_000,
                ts="2026-08-01T10:06:00.000Z", request_id="mix-2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "0"
        assert summary["consistent_1h"] == "0"
        assert summary["excluded"] == "1"
        assert summary["verdict"] == "no verdict"

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Clears"] == "excluded(near-tie)"

    def test_tie_between_w5m_and_w1h_resolves_deterministically_to_5m_tier_for_display(
        self, fake_projects, capsys
    ):
        """A root whose W5m and W1h accumulate to exactly the same nonzero
        total is still excluded(near-tie) -- the tie only decides which
        tier's own accumulators the display row names, via the per-root
        loop's `if root_w5m >= root_w1h` comparison resolving equality to
        the 5m branch. Pins today's tie resolution (`>=`, defaults to the
        5m branch) as display-only -- it has no verdict consequence, since
        the row is excluded either way."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=400_000, ts="2026-08-01T10:00:00.000Z", request_id="tie-1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=400_000,
                ts="2026-08-01T10:06:00.000Z", request_id="tie-2",
            ),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_row["Tier"] == "5m"
        assert root_row["Favors"] == "5m"
        assert root_row["Share"] == "0.500"
        assert root_row["Clears"] == "excluded(near-tie)"
        tier_split = _extract_ttl_verdict_tier_split_line(out, "account-1")
        assert tier_split == {"favors_5m_slice": "5m", "favors_1h_slice": "5m", "agreement": "agree"}

    def test_zero_consistent_roots_reaches_no_verdict_not_adopt(self, fake_projects, capsys):
        """A corpus with cache activity but no cache-write/read tokens
        crossing either direction's own accumulation gate (plain
        input/output tokens only) leaves neither direction with data --
        'no verdict', never a vacuous 'adopt'. The root's own row still
        prints, labelled excluded(no-data), in both buckets."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100, output=50, ts="2026-08-01T10:00:00.000Z", request_id="z1"),
        ])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        for origin in ("main", "subagent"):
            summary = _extract_ttl_verdict_summary(out, origin)
            assert summary["consistent_5m"] == "0"
            assert summary["consistent_1h"] == "0"
            assert summary["verdict"] == "no verdict"

            root_row = _extract_ttl_verdict_root_row(out, origin, "account-1")
            assert root_row["Clears"] == "excluded(no-data)"

    def test_no_redact_single_root_shows_real_path_not_account_ordinal(self, fake_projects, capsys):
        """root_label's own redact branch prints "account-N"; the
        --no-redact branch must print scan_roots[0]'s own real parent path
        instead -- no prior test pinned this for the per-root table."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="nr1"),
        ])
        _mod.cache_rebuild._cache_rebuild_report(
            _cache_rebuild_args(no_redact=True, ttl_verdict=True), roots=[fake_projects.parent],
        )
        out = capsys.readouterr().out

        root_row = _extract_ttl_verdict_root_row(out, "main", str(fake_projects.parent.parent))
        assert root_row["W5m/W1h"] == "500,000"
        assert "account-1" not in out

    def test_empty_corpus_reaches_no_verdict_without_crashing(self, fake_projects, capsys):
        """--ttl-verdict against zero in-scope calls prints clean
        no-verdict output for both buckets rather than crashing (e.g. a
        division by zero in the margin check, already guarded by
        _cache_rebuild_margin_clears' own non-positive-volume branch). The
        zero-data root still gets its own excluded(no-data) row rather than
        vanishing from the table."""
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        for origin in ("main", "subagent"):
            summary = _extract_ttl_verdict_summary(out, origin)
            assert summary["verdict"] == "no verdict"

            root_row = _extract_ttl_verdict_root_row(out, origin, "account-1")
            assert root_row["Clears"] == "excluded(no-data)"
            assert root_row["Net$"] == "n/a"
            assert root_row["Share"] == "n/a"

    def test_no_data_root_alongside_a_real_root_leaves_the_real_roots_verdict_untouched(
        self, tmp_path, capsys
    ):
        """Two roots in the same bucket:
        - account-1 has real 5m-tier data and reaches 'adopt' on its own.
        - account-2 has no cache-tier data at all.

        The no-data root's presence must not change account-1's own
        verdict, and its own Net$/Share render as n/a rather than
        $0.00/0.000."""
        root_a = _write_cost_root(
            tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _ttl_verdict_5m_tier_adopt_records(),
        )
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [])
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[root_a, root_b])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "1"
        assert summary["excluded"] == "1"
        assert summary["verdict"] == "adopt"

        root_1 = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_1["W5m/W1h"] == "1,000,000"
        assert root_1["Clears"] == "True"

        root_2 = _extract_ttl_verdict_root_row(out, "main", "account-2")
        assert root_2["Clears"] == "excluded(no-data)"
        assert root_2["Net$"] == "n/a"
        assert root_2["Share"] == "n/a"

    def test_no_data_root_and_a_near_tie_root_in_the_same_bucket_both_count_toward_excluded(
        self, tmp_path, capsys
    ):
        """Two roots in the same bucket, excluded for different reasons:

        - account-1 has no cache-tier data at all.
        - account-2 is the mixed root shape at share 0.870 (below the
          dominance threshold).

        The no-data and near-tie branches each independently increment
        excluded_roots; this pins that the bucket's printed total sums
        both reasons rather than only the last branch reached."""
        no_data_root = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-no-data", "sess-no-data", [])
        near_tie_root = _write_cost_root(
            tmp_path, "acct-b", "-home-user-repo-near-tie", "sess-near-tie",
            _ttl_verdict_near_tie_mixed_root_records(),
        )
        _mod.cache_rebuild._cache_rebuild_report(
            _cache_rebuild_args(ttl_verdict=True), roots=[no_data_root, near_tie_root],
        )
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["excluded"] == "2"

        root_1 = _extract_ttl_verdict_root_row(out, "main", "account-1")
        assert root_1["Clears"] == "excluded(no-data)"
        root_2 = _extract_ttl_verdict_root_row(out, "main", "account-2")
        assert root_2["Share"] == "0.870"
        assert root_2["Clears"] == "excluded(near-tie)"

    def test_mixed_root_row_shows_the_dominant_tiers_own_accumulators_not_a_combined_figure(
        self, tmp_path, capsys
    ):
        """A mixed root's displayed row must match a control root carrying
        only the dominant tier's data -- isolating whether the minority
        write leaks into the row."""
        mixed_records = _ttl_verdict_dominant_1h_mixed_root_records()
        pure_1h_records = [
            # Leading no-cache-tokens call mirrors the mixed fixture's own
            # session-start/idle-gap timing, so the 1h write and read
            # classify identically in both.
            _priced("claude-sonnet-5", input=10, output=5, ts="2026-08-01T10:00:00.000Z", request_id="pure-0"),
            _priced("claude-sonnet-5", ephemeral_1h=800_000, ts="2026-08-01T10:06:00.000Z", request_id="pure-1"),
            _priced("claude-sonnet-5", cache_read=150_000, ts="2026-08-01T10:12:00.000Z", request_id="pure-2"),
        ]
        mixed_root = _write_cost_root(tmp_path, "acct-mixed", "-home-user-repo-mixed", "sess-mixed", mixed_records)
        pure_root = _write_cost_root(tmp_path, "acct-pure", "-home-user-repo-pure", "sess-pure", pure_1h_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[mixed_root])
        mixed_out = capsys.readouterr().out
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[pure_root])
        pure_out = capsys.readouterr().out

        mixed_row = _extract_ttl_verdict_root_row(mixed_out, "main", "account-1")
        pure_row = _extract_ttl_verdict_root_row(pure_out, "main", "account-1")
        assert mixed_row["W5m/W1h"] == pure_row["W5m/W1h"] == "800,000"
        assert mixed_row["X/Z"] == pure_row["X/Z"] == "150,000"
        assert mixed_row["Net$"] == pure_row["Net$"]
        assert mixed_row["Favors"] == pure_row["Favors"]
        assert mixed_row["Clears"] == "excluded(near-tie)"
        assert mixed_row["Share"] == "0.800"
        assert pure_row["Clears"] == "True"
        assert pure_row["Share"] == "1.000"


class TestCacheRebuildTtlVerdictDominanceReduction:
    """Full-pipeline coverage that a dominance-resolved mixed root's own
    dominant-tier accumulators, and only those, reach
    _cache_rebuild_root_verdict_input and the bucket's consistent-root
    counts -- the reduction mechanics .claude/plans/cache-ttl-verdict-gate-fix.md's
    Approach section describes."""

    def test_minority_5m_write_outside_the_idle_band_never_changes_the_1h_rows_own_verdict_inputs(
        self, tmp_path, capsys
    ):
        """A pure-1h corpus (W1h=1,000,000, one idle-gap read Z=400,000)
        is compared against the same corpus plus one small 5m write
        placed 100 seconds after the read. That write sits under the idle
        band's own 300-second lower bound, so it classifies as
        unexplained, not idle, and never reaches the displayed 1h-tier
        row's own accumulators. The 1h row's own W5m/W1h, X/Z, Net$,
        Favors, and Clears must be byte-identical across both runs; only
        Share (1.000 vs 0.971) and the presence of an informative
        tier-split line differ."""
        pure_records = [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="p1"),
            _priced("claude-sonnet-5", cache_read=400_000, ts="2026-08-01T10:06:00.000Z", request_id="p2"),
        ]
        mixed_records = [
            *pure_records,
            _priced("claude-sonnet-5", ephemeral_5m=30_000, ts="2026-08-01T10:07:40.000Z", request_id="p3"),
        ]
        pure_root = _write_cost_root(tmp_path, "acct-pure", "-home-user-repo-pure", "sess-pure", pure_records)
        mixed_root = _write_cost_root(tmp_path, "acct-mixed", "-home-user-repo-mixed", "sess-mixed", mixed_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[pure_root])
        pure_out = capsys.readouterr().out
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[mixed_root])
        mixed_out = capsys.readouterr().out

        pure_row = _extract_ttl_verdict_root_row(pure_out, "main", "account-1")
        mixed_row = _extract_ttl_verdict_root_row(mixed_out, "main", "account-1")
        assert pure_row["W5m/W1h"] == mixed_row["W5m/W1h"] == "1,000,000"
        assert pure_row["X/Z"] == mixed_row["X/Z"] == "400,000"
        assert pure_row["Net$"] == mixed_row["Net$"]
        assert pure_row["Favors"] == mixed_row["Favors"]
        assert pure_row["Clears"] == mixed_row["Clears"] == "True"
        assert pure_row["Share"] == "1.000"
        assert mixed_row["Share"] == "0.971"
        assert _extract_ttl_verdict_tier_split_line(pure_out, "account-1") is None
        assert _extract_ttl_verdict_tier_split_line(mixed_out, "account-1") is not None

    def test_minority_5m_write_followed_by_an_idle_gap_read_inflates_z_strictly_against_dropping_to_5m(
        self, tmp_path, capsys
    ):
        """Proves the 1h-branch's idle-read disjunct, not the write
        disjunct, drives Z inflation. Distinct from the isolation test
        above, which only covers a minority write."""
        control_records = [
            # Control: read never enters the 1h branch.
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="c1"),
            # 4,000s after c1, past the 3,600s idle->1h boundary, so Z stays 0.
            _priced("claude-sonnet-5", cache_read=1_500_000, ts="2026-08-01T11:06:40.000Z", request_id="c2"),
        ]
        inflated_records = [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="m1"),
            # 10s after m1, well inside the idle band's own lower bound.
            _priced("claude-sonnet-5", ephemeral_5m=50_000, ts="2026-08-01T10:00:10.000Z", request_id="m2"),
            # 3,200s after m2, inside the idle 5m-1h band.
            # The read-token disjunct fires, so Z absorbs the full read even though no 1h write expired.
            _priced("claude-sonnet-5", cache_read=1_500_000, ts="2026-08-01T10:53:30.000Z", request_id="m3"),
        ]
        control_root = _write_cost_root(
            tmp_path, "acct-control", "-home-user-repo-control", "sess-control", control_records,
        )
        inflated_root = _write_cost_root(
            tmp_path, "acct-inflated", "-home-user-repo-inflated", "sess-inflated", inflated_records,
        )

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[control_root])
        control_out = capsys.readouterr().out
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[inflated_root])
        inflated_out = capsys.readouterr().out

        control_row = _extract_ttl_verdict_root_row(control_out, "main", "account-1")
        inflated_row = _extract_ttl_verdict_root_row(inflated_out, "main", "account-1")
        assert control_row["X/Z"] == "0"
        assert inflated_row["X/Z"] == "1,500,000"
        assert control_row["Favors"] == "5m"
        # Read size (1,500,000) exceeds W1h (1,000,000), flipping the raw-token
        # tiebreaker from favoring a drop to 5m to favoring 1h.
        assert inflated_row["Favors"] == "1h"
        assert control_row["Clears"] == "True"
        assert inflated_row["Clears"] == "False"

        # Control's Net$: the always-on base term alone, with no idle-read cost.
        assert control_row["Net$"] == "$1.50"
        # Inflated's Net$ adds the phantom read's expiry-cost term.
        # That term is strictly switch-cost-positive, so it can only move Net$ down, never up.
        assert inflated_row["Net$"] == "-$1.95"

    def test_dominance_resolved_mixed_root_counts_once_never_twice(self, tmp_path, capsys):
        """A dominance-resolved mixed root (share 0.971) increments
        consistent-1h by exactly one. It must never increment consistent_5m,
        and must never increment consistent_1h more than once."""
        records = [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="u1"),
            _priced("claude-sonnet-5", cache_read=400_000, ts="2026-08-01T10:06:00.000Z", request_id="u2"),
            _priced("claude-sonnet-5", ephemeral_5m=30_000, ts="2026-08-01T10:07:40.000Z", request_id="u3"),
        ]
        root = _write_cost_root(tmp_path, "acct", "-home-user-repo", "sess", records)
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[root])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "0"
        assert summary["consistent_1h"] == "1"
        assert summary["excluded"] == "0"

    def test_dominance_resolved_mixed_root_counts_once_never_twice_for_subagent_origin(
        self, fake_projects, capsys
    ):
        """Same dominance-resolved mixed root shape (share 0.971) as
        test_dominance_resolved_mixed_root_counts_once_never_twice above,
        on the subagent bucket instead of main."""
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="u1"),
            _priced("claude-sonnet-5", cache_read=400_000, ts="2026-08-01T10:06:00.000Z", request_id="u2"),
            _priced("claude-sonnet-5", ephemeral_5m=30_000, ts="2026-08-01T10:07:40.000Z", request_id="u3"),
        ]
        for rec in subagent_records:
            rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-1", subagent_records)
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "subagent")
        assert summary["consistent_5m"] == "0"
        assert summary["consistent_1h"] == "1"
        assert summary["excluded"] == "0"

    def test_dominance_resolved_mixed_roots_own_favors_agrees_with_the_tier_splits_same_tier_slice(
        self, tmp_path, capsys
    ):
        """A dominance-resolved mixed root's displayed Favors and its own
        tier-split line's same-tier slice favors are computed from the
        identical net_primary/net_5m_slice or net_1h_slice expression
        (transcript-analysis.py's own "Keep both call sites in sync"
        comment) -- this pins that the two stay equal, for both a
        5m-dominant and a 1h-dominant root (share 0.971 each)."""
        dominant_5m_root = _write_cost_root(tmp_path, "acct-dom5m", "-home-user-repo-dom5m", "sess-dom5m", [
            _priced("claude-sonnet-5", ephemeral_5m=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="e1"),
            _priced("claude-sonnet-5", ephemeral_1h=30_000, ts="2026-08-01T10:00:10.000Z", request_id="e2"),
        ])
        dominant_1h_root = _write_cost_root(tmp_path, "acct-dom1h", "-home-user-repo-dom1h", "sess-dom1h", [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="f1"),
            _priced("claude-sonnet-5", cache_read=400_000, ts="2026-08-01T10:06:00.000Z", request_id="f2"),
            _priced("claude-sonnet-5", ephemeral_5m=30_000, ts="2026-08-01T10:07:40.000Z", request_id="f3"),
        ])

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[dominant_5m_root])
        dominant_5m_out = capsys.readouterr().out
        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(ttl_verdict=True), roots=[dominant_1h_root])
        dominant_1h_out = capsys.readouterr().out

        dominant_5m_row = _extract_ttl_verdict_root_row(dominant_5m_out, "main", "account-1")
        assert dominant_5m_row["Tier"] == "5m"
        assert dominant_5m_row["Share"] == "0.971"
        dominant_5m_tier_split = _extract_ttl_verdict_tier_split_line(dominant_5m_out, "account-1")
        assert dominant_5m_tier_split is not None
        assert dominant_5m_row["Favors"] == dominant_5m_tier_split["favors_5m_slice"]

        dominant_1h_row = _extract_ttl_verdict_root_row(dominant_1h_out, "main", "account-1")
        assert dominant_1h_row["Tier"] == "1h"
        assert dominant_1h_row["Share"] == "0.971"
        dominant_1h_tier_split = _extract_ttl_verdict_tier_split_line(dominant_1h_out, "account-1")
        assert dominant_1h_tier_split is not None
        assert dominant_1h_row["Favors"] == dominant_1h_tier_split["favors_1h_slice"]

    def test_dominance_resolved_root_and_a_pure_root_favoring_opposite_directions_disagree(
        self, tmp_path, capsys
    ):
        """Two roots in one bucket:
        - pure 5m-tier root (favors 1h)
        - dominance-resolved mixed 1h-tier root (share 0.971, favors 5m)

        The bucket verdict must read 'roots disagree', proving the
        dominance-resolved root entered the reduction rather than being
        silently dropped."""
        pure_5m_root = _write_cost_root(
            tmp_path, "acct-pure5m", "-home-user-repo-pure5m", "sess-pure5m",
            _ttl_verdict_5m_tier_adopt_records(),
        )
        dominant_1h_records = [
            _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="d1"),
            _priced("claude-sonnet-5", cache_read=400_000, ts="2026-08-01T10:06:00.000Z", request_id="d2"),
            _priced("claude-sonnet-5", ephemeral_5m=30_000, ts="2026-08-01T10:07:40.000Z", request_id="d3"),
        ]
        dominant_1h_root = _write_cost_root(
            tmp_path, "acct-dom1h", "-home-user-repo-dom1h", "sess-dom1h", dominant_1h_records,
        )
        _mod.cache_rebuild._cache_rebuild_report(
            _cache_rebuild_args(ttl_verdict=True), roots=[pure_5m_root, dominant_1h_root],
        )
        out = capsys.readouterr().out

        summary = _extract_ttl_verdict_summary(out, "main")
        assert summary["consistent_5m"] == "1"
        assert summary["consistent_1h"] == "1"
        assert summary["verdict"] == "roots disagree"

        # Ordinal assignment is path-sort-order-derived, not root-list-order,
        # so the two rows are told apart by their own Share instead of a
        # fixed account-N mapping.
        rows = [
            _extract_ttl_verdict_root_row(out, "main", "account-1"),
            _extract_ttl_verdict_root_row(out, "main", "account-2"),
        ]
        pure_row = next(row for row in rows if row["Share"] == "1.000")
        dominant_row = next(row for row in rows if row["Share"] == "0.971")
        assert pure_row["Favors"] == "1h"
        assert dominant_row["Favors"] == "5m"


