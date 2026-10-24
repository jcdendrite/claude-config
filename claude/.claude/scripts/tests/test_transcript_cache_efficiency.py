"""Tests for transcript_analysis/cache_efficiency.py: the prefix total, the read-collapse
classifier, the per-group scan's chain keying and compaction reset, and cmd_cache_efficiency end to
end, including the --no-redact refusal and the per-account breakdown.
"""
import pytest
from transcript_analysis import cache_efficiency, scope

from ._cache_efficiency_helpers import _cache_efficiency_args
from .conftest import (
    _compact_boundary_rec,
    _priced,
    _table_cols,
    _user_msg,
    _write_cost_root,
    _write_jsonl,
)


class TestCachePrefixTotal:
    """Direct unit tests for _cache_prefix_total's usage-dict traversal --
    the read-collapse classifier's prior-turn denominator."""

    def test_excludes_input_tokens(self):
        """input_tokens must not contribute to the prefix total -- only
        cache_read_input_tokens and both cache_creation tiers count, per the
        function's own documented differentiator from _context_at_turn. A
        prior turn carrying only input_tokens (no cache fields at all) must
        report a prefix total of zero, not the input_tokens value."""
        usage = _priced("claude-sonnet-5", input=50_000, cache_read=0)["message"]["usage"]
        assert cache_efficiency._cache_prefix_total(usage) == 0

    def test_sums_read_and_both_cache_write_tiers(self):
        usage = _priced("claude-sonnet-5", cache_read=1000, ephemeral_1h=200, ephemeral_5m=300)["message"]["usage"]
        assert cache_efficiency._cache_prefix_total(usage) == 1500


class TestCacheEfficiencyClassifier:
    """Direct unit tests for the read-collapse classifier
    (docs/case-studies/cold-cache-attribution.md), scored at
    T=_COLD_READ_COLLAPSE_MARGIN=0.50 -- the margin the case study measured
    as the maximum Youden's J across every threshold tested there."""

    def test_margin_boundary_exactly_at_t_is_not_cold(self):
        """collapse == T (not strictly greater than T) stays warm: prior
        prefix 100, this turn's read 50 -> exactly 0.50 collapse."""
        assert cache_efficiency._is_cold_read_collapse(100, 50) is False

    def test_margin_boundary_just_past_t_is_cold(self):
        """One token further past the same prior prefix flips the
        classification: prior prefix 100, read 49 -> 0.51 collapse."""
        assert cache_efficiency._is_cold_read_collapse(100, 49) is True

    def test_zero_prior_prefix_is_never_cold(self):
        """No prefix to collapse from -- covers both a prior turn whose own
        prefix total was genuinely zero and (via _scan_cache_efficiency_group
        below) a session's first turn, which has no prior turn at all."""
        assert cache_efficiency._is_cold_read_collapse(0, 0) is False


class TestCacheEfficiencyGroupScan:
    """Direct unit tests for _scan_cache_efficiency_group's classification
    over one source-file group -- the layer TestCacheEfficiencyClassifier's
    pure-function tests can't reach, since the first-turn carve-out depends
    on the per-session prior-turn chain, not the margin comparison alone."""

    def test_first_turn_of_session_with_zero_read_is_not_cold(self):
        """A session's first assistant turn has no predecessor, so a
        first-turn read==0 (e.g. a genuinely fresh context) must not be
        misread as a cold re-write -- the first-turn carve-out."""
        records = [_priced("claude-sonnet-5", cache_read=0, ephemeral_5m=100)]
        stats = cache_efficiency._new_cache_efficiency_stats()
        cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert stats["main"]["turns"] == 1
        assert stats["main"]["cold_events"] == 0

    def test_non_first_turn_with_zero_read_after_nonzero_prior_is_cold(self):
        """The non-first-turn counterpart to the carve-out above: a turn
        following a real prior prefix, whose own read collapses to zero,
        must classify cold."""
        records = [
            _priced("claude-sonnet-5", cache_read=1000, request_id="r1"),
            _priced("claude-sonnet-5", cache_read=0, ephemeral_5m=500, request_id="r2"),
        ]
        stats = cache_efficiency._new_cache_efficiency_stats()
        cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert stats["main"]["cold_events"] == 1
        assert stats["main"]["cold_tokens"] == 500

    def test_compact_boundary_resets_the_chain(self):
        """A compact_boundary record resets the prior-turn chain -- the turn
        immediately after compaction is treated as a first turn (not cold),
        since the pre-compaction prefix it would otherwise be compared
        against no longer exists."""
        records = [
            _priced("claude-sonnet-5", cache_read=100_000, request_id="r1"),
            _compact_boundary_rec(),
            _priced("claude-sonnet-5", cache_read=0, ephemeral_5m=100, request_id="r2"),
        ]
        stats = cache_efficiency._new_cache_efficiency_stats()
        cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert stats["main"]["cold_events"] == 0

    def test_input_heavy_prior_turn_not_treated_as_cache_prefix(self):
        """A prior turn with a large input_tokens value but zero cache
        fields contributes nothing to the prior-prefix chain (per
        _cache_prefix_total's documented exclusion) -- the next turn's own
        cache_read must not be misclassified as a collapse against that
        input-heavy, uncached prefix. A buggy _cache_prefix_total that
        folded input_tokens back in would see a 50,000-token prior prefix
        and flag turn 2 as cold; this expects no such collapse."""
        records = [
            _priced("claude-sonnet-5", input=50_000, cache_read=0, request_id="r1"),
            _priced("claude-sonnet-5", cache_read=0, ephemeral_5m=100, request_id="r2"),
        ]
        stats = cache_efficiency._new_cache_efficiency_stats()
        cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert stats["main"]["cold_events"] == 0

    def test_return_value_counts_sidechain_assistant_records_read(self):
        """The int return value counts isSidechain assistant records seen in
        this group, independent of stats['sidechain']['turns'] -- feeds the
        drift canary via _cache_efficiency_report's total_sidechain_turns
        accumulation."""
        side_rec = _priced("claude-sonnet-5", cache_read=100)
        side_rec["isSidechain"] = True
        records = [_priced("claude-sonnet-5", cache_read=50), side_rec]
        stats = cache_efficiency._new_cache_efficiency_stats()
        sidechain_turns_read = cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert sidechain_turns_read == 1

    def test_second_session_first_turn_does_not_inherit_first_sessions_chain(self):
        """The carve-out is keyed by sessionId, not by turn position within
        the group: a second session's first turn must not inherit a first
        session's already-built prior-prefix chain, even though both turns
        appear in the same source-file group."""
        session_a_turn = _priced("claude-sonnet-5", cache_read=10_000, request_id="r1")
        session_a_turn["sessionId"] = "session-a"
        session_b_first_turn = _priced("claude-sonnet-5", cache_read=0, ephemeral_5m=100, request_id="r2")
        session_b_first_turn["sessionId"] = "session-b"
        records = [session_a_turn, session_b_first_turn]
        stats = cache_efficiency._new_cache_efficiency_stats()
        cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert stats["main"]["cold_events"] == 0

    def test_sidechain_turn_buckets_separately_from_main(self):
        """isSidechain routes a turn into its own thread bucket, mirroring
        cost's and subagents' own main/sidechain split."""
        side_rec = _priced("claude-sonnet-5", cache_read=200)
        side_rec["isSidechain"] = True
        records = [_priced("claude-sonnet-5", cache_read=100), side_rec]
        stats = cache_efficiency._new_cache_efficiency_stats()
        cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert stats["main"]["turns"] == 1
        assert stats["main"]["read_tokens"] == 100
        assert stats["sidechain"]["turns"] == 1
        assert stats["sidechain"]["read_tokens"] == 200

    def test_sidechain_first_turn_does_not_inherit_mains_prior_prefix(self):
        """The prior-prefix chain is keyed by (sessionId, thread), not
        sessionId alone: a sidechain record sharing the main record's
        sessionId (the real-world shape -- a subagent file's records carry
        the parent session's sessionId) must not inherit the main thread's
        prior prefix. Values are chosen so cross-thread contamination would
        flip the verdict: main's prefix (10,000) vastly exceeds the
        sidechain's own read (0), so a shared chain would misclassify the
        sidechain's first turn as a cold collapse."""
        main_rec = _priced("claude-sonnet-5", cache_read=10_000)
        main_rec["sessionId"] = "session-a"
        side_rec = _priced("claude-sonnet-5", cache_read=0, ephemeral_5m=100)
        side_rec["isSidechain"] = True
        side_rec["sessionId"] = "session-a"
        records = [main_rec, side_rec]
        stats = cache_efficiency._new_cache_efficiency_stats()
        cache_efficiency._scan_cache_efficiency_group(records, stats)
        assert stats["sidechain"]["cold_events"] == 0


class TestCacheEfficiency:
    def test_turn_and_token_totals_and_cold_share_hand_computed(self, fake_projects, capsys):
        """End-to-end report totals against a small hand-computed fixture:
        turn 1 (no predecessor) is never cold; turn 2 is a warm append
        (read stays at turn 1's full prefix); turn 3's read collapses to
        zero against turn 2's 1,200-token prefix -- cold, contributing its
        own 500 write-5m tokens as cold tokens."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", cache_read=1000, request_id="r1"),
            _priced("claude-sonnet-5", cache_read=1000, ephemeral_5m=200, request_id="r2"),
            _priced("claude-sonnet-5", cache_read=0, ephemeral_5m=500, request_id="r3"),
        ])
        cache_efficiency._cache_efficiency_report(_cache_efficiency_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Turns"] == "3"
        assert cols["Read"] == "2,000"
        assert cols["Write5m"] == "700"
        assert cols["ColdTok"] == "500"
        assert cols["ColdEvts"] == "1"

    def test_multi_record_turn_split_across_jsonl_lines_not_misread_cold(self, fake_projects, capsys):
        """Claude Code writes one JSONL record per assistant content block
        (thinking/text/tool_use), all sharing one requestId and identical
        cache_read/cache_creation usage (_dedup_turns_by_request_id's own
        documented invariant). Without deduping each source-file group
        before classification, the second record of that same turn would
        read as a second turn whose read 'collapsed' against the first
        record's own (identical) usage -- a false cold event on a turn that
        never actually ended. Guards _cache_efficiency_report's per-group
        _dedup_turns_by_request_id call."""
        rec1 = _priced("claude-sonnet-5", cache_read=100, ephemeral_5m=10_000, request_id="req-1")
        rec2 = _priced("claude-sonnet-5", cache_read=100, ephemeral_5m=10_000, request_id="req-1")
        _write_jsonl(fake_projects / "sess.jsonl", [rec1, rec2])
        cache_efficiency._cache_efficiency_report(_cache_efficiency_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Turns"] == "1"
        assert cols["ColdEvts"] == "0"

    def test_no_assistant_turns_prints_zeroes_without_division_error(self, fake_projects, capsys):
        """A session with no assistant turns at all (e.g. only a user
        message) prints a clean zero-state row rather than raising
        ZeroDivisionError -- mirrors edit-format's own zero-state coverage."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        cache_efficiency._cache_efficiency_report(_cache_efficiency_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Turns"] == "0"
        assert cols["Cold/Wr"] == "0.0%"

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """--no-redact is refused when --config-dir puts more than one root
        in scope, mirroring cost's/edit-format's/read-scope's own refusal."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            cache_efficiency.cmd_cache_efficiency(_cache_efficiency_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root_content_unchanged(self, fake_projects, capsys):
        """--no-redact with no --config-dir (single root) is unaffected —
        this report's content never varies with redact, since it carries no
        project name or session ID, but the banner still prints for CLI
        parity with cost/edit-format/read-scope."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", cache_read=100),
        ])
        cache_efficiency._cache_efficiency_report(_cache_efficiency_args(no_redact=True))
        out = capsys.readouterr().out
        assert scope._DO_NOT_PUBLISH_BANNER in out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Turns"] == "1"

    def test_no_redact_refused_by_report_itself_even_when_called_directly(self, tmp_path, capsys):
        """Defense-in-depth: _cache_efficiency_report must refuse the
        multi-root + --no-redact combination itself rather than trusting
        that _resolve_cost_roots already validated it, mirroring
        test_no_redact_refused_by_edit_format_report_itself_even_when_called_directly.
        Refusal happens before any output is printed."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", [
            _priced("claude-sonnet-5", cache_read=100),
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [
            _priced("claude-sonnet-5", cache_read=200),
        ])
        with pytest.raises(SystemExit) as exc_info:
            cache_efficiency._cache_efficiency_report(_cache_efficiency_args(no_redact=True), roots=[root_a, root_b])
        assert exc_info.value.code == 2
        assert capsys.readouterr().out == ""

    def test_per_account_breakdown_uses_account_n_labels_not_raw_paths(self, tmp_path, capsys):
        """Per-account figures are emitted through account-N labels — never
        the raw config-dir path or account-identifying directory name — the
        same discipline edit-format's own per-account breakdown carries."""
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-repo-a", "sess-a", [
            _priced("claude-sonnet-5", cache_read=100),
        ])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b", [
            _priced("claude-sonnet-5", cache_read=200),
            _priced("claude-sonnet-5", cache_read=300),
        ])
        cache_efficiency._cache_efficiency_report(_cache_efficiency_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "acct-alice-clientwork" not in out
        assert "acct-bob-clientwork" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out
        account_1 = _table_cols(out, header_contains="Thread", row_contains="main", occurrence=2)
        account_2 = _table_cols(out, header_contains="Thread", row_contains="main", occurrence=3)
        assert account_1["Turns"] == "1"
        assert account_2["Turns"] == "2"

    def test_per_account_breakdown_attributes_cold_events_to_the_correct_account(self, tmp_path, capsys):
        """A cold event originating in one account's session must land in
        that account's own per_account[ordinal] bucket, not leak into the
        other account's bucket or only the aggregate. A bug in the
        redact_ordinals/root_position lookup could pass a turn-counts-only
        check while still misattributing cold figures across accounts."""
        root_a_turn = _priced("claude-sonnet-5", cache_read=100)
        root_a_turn["sessionId"] = "sess-a"
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", [root_a_turn])

        root_b_turn_1 = _priced("claude-sonnet-5", cache_read=1000, request_id="r1")
        root_b_turn_1["sessionId"] = "sess-b"
        root_b_turn_2 = _priced("claude-sonnet-5", cache_read=0, ephemeral_5m=500, request_id="r2")
        root_b_turn_2["sessionId"] = "sess-b"
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [root_b_turn_1, root_b_turn_2])
        cache_efficiency._cache_efficiency_report(_cache_efficiency_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        account_1 = _table_cols(out, header_contains="Thread", row_contains="main", occurrence=2)
        account_2 = _table_cols(out, header_contains="Thread", row_contains="main", occurrence=3)
        assert account_1["ColdEvts"] == "0"
        assert account_1["ColdTok"] == "0"
        assert account_2["ColdEvts"] == "1"
        assert account_2["ColdTok"] == "500"
