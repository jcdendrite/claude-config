"""Tests for transcript_analysis/rearm_backtest.py's nudge-to-handoff conversion classifier and per-
root log-size disclosure line."""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


class TestNudgeConversionFromLog:
    """Pure unit tests against _nudge_conversion_from_log, mirroring
    TestOperatorResponseLagFromLog's own convention for its sibling
    function -- plain session_traces/log_entries_by_root dicts, no
    filesystem or report-rendering plumbing. Pins the pre-registered
    classification from .claude/plans/handoff-nudge-deep-tail-lever.md."""

    def test_nudged_then_handoff_is_voluntary(self):
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["voluntary"] == 1
        assert result["forced"] == 0
        assert result["blocked_no_handoff"] == 0
        assert result["no_compliance"] == 0
        assert result["dropped"] == 0

    def test_block_before_handoff_is_forced(self):
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "nudged", "session": "s", "est": 200, "model": "x", "window": 1,
             "event": "PostToolBatch", "action": "block"},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["forced"] == 1
        assert result["voluntary"] == 0

    def test_block_after_handoff_is_still_voluntary(self):
        """The classification's 'precedes' language is load-bearing: a block
        that fires only after a handoff already ran -- the session kept
        working and later hit the block -- must not be misclassified as
        forced just because a block line exists somewhere in the log."""
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "handoff", "session": "s"},
            {"kind": "nudged", "session": "s", "est": 200, "model": "x", "window": 1,
             "event": "PostToolBatch", "action": "block"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["voluntary"] == 1
        assert result["forced"] == 0

    def test_second_handoff_line_does_not_flip_classification_to_forced(self):
        """Classification is decided against the FIRST handoff line reached,
        per this function's own documented contract -- a block sandwiched
        between two handoff lines must not flip an otherwise-voluntary
        session to forced."""
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "handoff", "session": "s"},
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1,
             "event": "PostToolBatch", "action": "block"},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["voluntary"] == 1
        assert result["forced"] == 0

    def test_block_with_no_handoff_is_its_own_bucket_not_folded_into_forced(self):
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1,
             "event": "PostToolBatch", "action": "block"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["blocked_no_handoff"] == 1
        assert result["forced"] == 0
        assert result["voluntary"] == 0

    def test_nudged_only_is_no_compliance_observed(self):
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["no_compliance"] == 1

    def test_handoff_session_without_in_scope_trace_is_dropped(self):
        """Mirrors test_excluded_operator_lag_count_is_reported: a session
        with no surviving in-scope transcript is excluded and counted, not
        silently discarded -- even though it has both a nudged and a
        handoff line."""
        session_traces: dict = {}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["dropped"] == 1
        assert result["voluntary"] == 0

    def test_nudged_only_session_without_in_scope_trace_is_dropped_not_no_compliance(self):
        """The mirror orphan shape: a nudged-only session with no in-scope
        trace must land in dropped, not inflate no_compliance."""
        session_traces: dict = {}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["dropped"] == 1
        assert result["no_compliance"] == 0

    def test_entry_with_missing_session_field_is_silently_unaccounted_for(self):
        """A missing/empty `session` skips the `if session:` guard entirely,
        landing in no bucket (not even `dropped`)."""
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        total_classified = (
            result["voluntary"] + result["forced"] + result["blocked_no_handoff"]
            + result["no_compliance"] + result["dropped"]
        )
        assert total_classified == 1  # only the "s" session is accounted for anywhere
        assert result["voluntary"] == 1  # "s" itself still classifies normally

    def test_missing_ignored_field_is_counted_not_defaulted_to_zero(self):
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["voluntary"] == 1
        assert result["no_ignored_field"] == 1
        assert result["ignored_values"] == []

    def test_ignored_value_of_zero_is_counted_not_treated_as_missing(self):
        """The lookup checks key presence, not truthiness. ignored=0 (complied
        on the first nudge) must land in ignored_values, not no_ignored_field
        -- a truthiness-style regression would misclassify it as missing."""
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1,
             "event": "Stop", "ignored": 0},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["ignored_values"] == [0]
        assert result["no_ignored_field"] == 0

    def test_ignored_value_is_read_from_the_line_immediately_preceding_handoff(self):
        """Not the first, min, max, or a sum/average -- a plausible wrong
        selection would not surface by hand-checking the report."""
        session_traces = {"s": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1,
             "event": "Stop", "ignored": 2},
            {"kind": "nudged", "session": "s", "est": 200, "model": "x", "window": 1,
             "event": "Stop", "ignored": 9},
            {"kind": "nudged", "session": "s", "est": 300, "model": "x", "window": 1,
             "event": "Stop", "ignored": 4},
            {"kind": "handoff", "session": "s"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["ignored_values"] == [4]
        assert result["no_ignored_field"] == 0

    def test_non_overlapping_session_ids_yield_zero_join_validity(self):
        """The hook resolves its session id from the hook-event payload.
        handoff-record-conversion.sh resolves its own by PID walk. A
        systematic mismatch between the two would read as universal
        non-compliance. This fixture's two well-formed, never-coincident
        ids exercise that shape directly, distinct from a malformed or
        missing-field input. pidwalk-B's orphan handoff line is neither
        classified nor counted as dropped -- it never appeared on a
        nudged line, so it was never a candidate for any bucket."""
        session_traces = {"hookid-A": [100]}
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "hookid-A", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "handoff", "session": "pidwalk-B"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)
        assert result["join_validity"] == 0
        assert result["no_compliance"] == 1
        assert result["dropped"] == 0

    def test_bucket_exhaustiveness_and_derived_rate_arithmetic_match_hand_computed_counts(self):
        """Every fired, in-scope session lands in exactly one of the four
        buckets or dropped, and the derived conversion/block-reach rates are
        simple sums over those bucket counts.
        test_conversion_bucket_and_rate_arithmetic_matches_hand_computed_counts
        is the equivalent check against the printed report, additionally
        verifying the percentage strings. Bucket sizes are pairwise
        distinct (5/1/3/7), so a bucket-swap regression in the rate formula
        changes the asserted total instead of passing coincidentally."""
        session_traces = {
            "voluntary-1": [100], "voluntary-2": [100], "voluntary-3": [100],
            "voluntary-4": [100], "voluntary-5": [100],
            "forced-1": [100],
            "blocked-1": [100], "blocked-2": [100], "blocked-3": [100],
            "nocompliance-1": [100], "nocompliance-2": [100], "nocompliance-3": [100],
            "nocompliance-4": [100], "nocompliance-5": [100], "nocompliance-6": [100],
            "nocompliance-7": [100],
        }
        log_entries_by_root = {"root": [
            {"kind": "nudged", "session": "voluntary-1", "est": 100, "model": "x", "window": 1,
             "event": "Stop", "ignored": 3},
            {"kind": "handoff", "session": "voluntary-1"},
            *[
                entry
                for i in range(2, 6)
                for entry in (
                    {"kind": "nudged", "session": f"voluntary-{i}", "est": 100, "model": "x", "window": 1,
                     "event": "Stop"},
                    {"kind": "handoff", "session": f"voluntary-{i}"},
                )
            ],
            {"kind": "nudged", "session": "forced-1", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "nudged", "session": "forced-1", "est": 200, "model": "x", "window": 1,
             "event": "PostToolBatch", "action": "block"},
            {"kind": "handoff", "session": "forced-1"},
            *[
                {"kind": "nudged", "session": f"blocked-{i}", "est": 100, "model": "x", "window": 1,
                 "event": "PostToolBatch", "action": "block"}
                for i in range(1, 4)
            ],
            *[
                {"kind": "nudged", "session": f"nocompliance-{i}", "est": 100, "model": "x", "window": 1,
                 "event": "Stop"}
                for i in range(1, 8)
            ],
            {"kind": "nudged", "session": "dropped-1", "est": 100, "model": "x", "window": 1, "event": "Stop"},
        ]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, log_entries_by_root)

        total = (
            result["voluntary"] + result["forced"] + result["blocked_no_handoff"]
            + result["no_compliance"] + result["dropped"]
        )
        assert total == 17  # 16 fired, in-scope sessions + dropped-1
        assert result["voluntary"] == 5
        assert result["forced"] == 1
        assert result["blocked_no_handoff"] == 3
        assert result["no_compliance"] == 7
        assert result["dropped"] == 1

        conversion = result["voluntary"] + result["forced"]
        block_reach = result["forced"] + result["blocked_no_handoff"]
        assert conversion == 6
        assert block_reach == 4

    def test_session_id_repeated_across_two_roots_lands_in_root_scan_order(self):
        """Pins the function's own documented limitation (the comment above
        its per_session grouping loop): a session id colliding across two
        roots (stale symlink, merged log, PID reuse) is not detected.
        Entries from both roots merge in root-scan order -- the dict
        iteration order of log_entries_by_root -- rather than true
        chronological order, since neither line type carries a timestamp.
        Swapping which root is scanned first changes whether the block line
        lands before or after the handoff line, flipping the bucket, turning
        the comment's claim into a regression-guarded fact."""
        session_traces = {"s": [100]}
        entries_root_a = [
            {"kind": "nudged", "session": "s", "est": 100, "model": "x", "window": 1, "event": "Stop"},
            {"kind": "handoff", "session": "s"},
        ]
        entries_root_b = [
            {"kind": "nudged", "session": "s", "est": 200, "model": "x", "window": 1,
             "event": "PostToolBatch", "action": "block"},
        ]

        handoff_scanned_first = _mod.rearm_backtest._nudge_conversion_from_log(
            session_traces, {"root-a": entries_root_a, "root-b": entries_root_b}
        )
        assert handoff_scanned_first["voluntary"] == 1
        assert handoff_scanned_first["forced"] == 0

        block_scanned_first = _mod.rearm_backtest._nudge_conversion_from_log(
            session_traces, {"root-b": entries_root_b, "root-a": entries_root_a}
        )
        assert block_scanned_first["forced"] == 1
        assert block_scanned_first["voluntary"] == 0


class TestRearmBacktestLogSizeLines:
    """Pure unit tests against _rearm_backtest_log_size_lines: plain
    (Path, size-or-None) tuples in, no filesystem or report-rendering
    plumbing."""

    def test_single_root_prints_account_n_label_and_byte_count(self):
        root = Path("/fake/config-dir/projects")
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root, 12_345)], multi_root=False, redact=True,
            redact_ordinals={root.resolve(): 1},
        )
        assert lines == ["  account-1 nudge log: 12,345 bytes"]

    def test_single_root_no_redact_prints_raw_path(self):
        root = Path("/fake/config-dir/projects")
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root, 100)], multi_root=False, redact=False,
            redact_ordinals={root.resolve(): 1},
        )
        assert lines == [f"  {root.parent / '.handoff-nudge.log'} nudge log: 100 bytes"]

    def test_single_root_over_cap_flags_truncated(self):
        root = Path("/fake/config-dir/projects")
        oversized = _mod.handoff_nudge._NUDGE_LOG_MAX_READ + 1
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root, oversized)], multi_root=False, redact=True,
            redact_ordinals={root.resolve(): 1},
        )
        assert lines == [
            f"  account-1 nudge log: {oversized:,} bytes [truncated -- oldest lines dropped]"
        ]

    def test_single_root_at_exactly_the_cap_is_not_flagged_truncated(self):
        root = Path("/fake/config-dir/projects")
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root, _mod.handoff_nudge._NUDGE_LOG_MAX_READ)], multi_root=False, redact=True,
            redact_ordinals={root.resolve(): 1},
        )
        assert lines == [f"  account-1 nudge log: {_mod.handoff_nudge._NUDGE_LOG_MAX_READ:,} bytes"]

    def test_single_root_unreadable_prints_no_byte_count(self):
        root = Path("/fake/config-dir/projects")
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root, None)], multi_root=False, redact=True,
            redact_ordinals={root.resolve(): 1},
        )
        assert lines == ["  account-1 nudge log: unreadable"]

    def test_multi_root_pools_bytes_with_no_per_root_breakdown(self):
        root_a, root_b = Path("/fake/a/projects"), Path("/fake/b/projects")
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root_a, 100), (root_b, 200)], multi_root=True, redact=True,
            redact_ordinals={root_a.resolve(): 1, root_b.resolve(): 2},
        )
        assert lines == ["  nudge logs across every resolved root: 300 bytes"]
        assert "account-" not in lines[0]

    def test_multi_root_at_exactly_the_cap_is_not_flagged_truncated(self):
        root_a, root_b = Path("/fake/a/projects"), Path("/fake/b/projects")
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root_a, _mod.handoff_nudge._NUDGE_LOG_MAX_READ), (root_b, 10)], multi_root=True, redact=True,
            redact_ordinals={root_a.resolve(): 1, root_b.resolve(): 2},
        )
        assert lines == [f"  nudge logs across every resolved root: {_mod.handoff_nudge._NUDGE_LOG_MAX_READ + 10:,} bytes"]

    def test_multi_root_flags_truncated_without_a_count_or_naming_the_root(self):
        root_a, root_b = Path("/fake/a/projects"), Path("/fake/b/projects")
        oversized = _mod.handoff_nudge._NUDGE_LOG_MAX_READ + 1
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root_a, oversized), (root_b, 10)], multi_root=True, redact=True,
            redact_ordinals={root_a.resolve(): 1, root_b.resolve(): 2},
        )
        assert lines == [
            f"  nudge logs across every resolved root: {oversized + 10:,} bytes"
            " (some roots truncated -- oldest lines dropped)"
        ]

    def test_multi_root_flags_unreadable_without_a_count_and_excludes_it_from_the_total(self):
        root_a, root_b = Path("/fake/a/projects"), Path("/fake/b/projects")
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root_a, None), (root_b, 500)], multi_root=True, redact=True,
            redact_ordinals={root_a.resolve(): 1, root_b.resolve(): 2},
        )
        assert lines == ["  nudge logs across every resolved root: 500 bytes (some roots unreadable)"]

    def test_multi_root_combines_truncated_and_unreadable_in_expected_order(self):
        root_a, root_b = Path("/fake/a/projects"), Path("/fake/b/projects")
        oversized = _mod.handoff_nudge._NUDGE_LOG_MAX_READ + 1
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(root_a, oversized), (root_b, None)], multi_root=True, redact=True,
            redact_ordinals={root_a.resolve(): 1, root_b.resolve(): 2},
        )
        assert lines == [
            f"  nudge logs across every resolved root: {oversized:,} bytes"
            " (some roots truncated -- oldest lines dropped) (some roots unreadable)"
        ]

    def test_multi_root_disclosure_line_never_contains_a_digit_that_varies_with_root_count(self):
        """Redaction regression guard: a per-condition digit (a count of
        truncated or unreadable roots) discloses a root/account-cardinality
        lower bound, which docs/private-project-redaction.md's
        Account-cardinality bar prohibits at any pooling breadth. Sweeps
        the healthy-root count (0 extra vs. 3 extra, alongside one fixed
        truncated root and one fixed unreadable root) and asserts the note
        text itself (after the pooled byte total, which does legitimately
        vary) contains no digit and is identical across both sweep sizes."""
        oversized = _mod.handoff_nudge._NUDGE_LOG_MAX_READ + 1

        def note_for(root_count: int) -> str:
            roots = [Path(f"/fake/{n}/projects") for n in range(root_count)]
            per_root_sizes = [(roots[0], oversized), (roots[1], None)] + [
                (r, 10) for r in roots[2:]
            ]
            lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
                per_root_sizes, multi_root=True, redact=True,
                redact_ordinals={r.resolve(): i + 1 for i, r in enumerate(roots)},
            )
            assert len(lines) == 1
            return lines[0].split("bytes", 1)[1]

        assert not any(char.isdigit() for char in note_for(2))
        assert note_for(2) == note_for(5)

    def test_multi_root_at_three_roots_still_prints_exactly_one_line(self):
        roots = [Path(f"/fake/{n}/projects") for n in "abc"]
        lines = _mod.rearm_backtest._rearm_backtest_log_size_lines(
            [(roots[0], 10), (roots[1], 20), (roots[2], 30)], multi_root=True, redact=True,
            redact_ordinals={r.resolve(): i + 1 for i, r in enumerate(roots)},
        )
        assert len(lines) == 1
        assert "account-" not in lines[0]
        assert lines[0] == "  nudge logs across every resolved root: 60 bytes"
