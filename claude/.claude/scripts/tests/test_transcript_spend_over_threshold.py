"""Tests for transcript_analysis/spend_over_threshold.py's cmd_spend_over_threshold, including its
nudge-log diagnostic footer."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from ._handoff_nudge_helpers import _spend_over_threshold_args
from .conftest import (
    _asst,
    _priced,
    _table_cols,
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


class TestSpendOverThreshold:
    def test_session_entirely_under_threshold_reports_zero_share(self, fake_projects, capsys):
        """Every main-thread turn's context_at_turn stays below the session's
        own fire threshold (150,000 for claude-sonnet-5): above-threshold
        dollars is 0, share is 0.0%."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=50_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=60_000, output=1_000, ts="2026-05-19T10:01:00.000Z"),
        ])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Sessions", row_contains="Total")
        assert int(cols["Sessions"]) == 1
        assert cols["Share"] == "0.0%"

    def test_session_entirely_over_threshold_reports_full_share(self, fake_projects, capsys):
        """Every main-thread turn's context_at_turn is at or above the
        session's own fire threshold: share is 100.0%."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=400_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=450_000, output=1_000, ts="2026-05-19T10:01:00.000Z"),
        ])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Sessions", row_contains="Total")
        assert cols["Share"] == "100.0%"

    def test_mixed_session_reports_partial_share(self, fake_projects, capsys):
        """One turn under threshold, one turn at/above it: the reported
        share is exactly the above-threshold turn's own dollar fraction of
        the session's total -- a hand-computed value, not just a nonzero
        check."""
        under = _priced("claude-sonnet-5", input=50_000, output=1_000, ts="2026-05-19T10:00:00.000Z")
        over = _priced("claude-sonnet-5", input=400_000, output=2_000, ts="2026-05-19T10:01:00.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [under, over])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out

        rates = _mod._model_rates("claude-sonnet-5")
        under_dollars = 50_000 / 1_000_000 * rates["input"] + 1_000 / 1_000_000 * rates["output"]
        over_dollars = 400_000 / 1_000_000 * rates["input"] + 2_000 / 1_000_000 * rates["output"]
        expected_share = 100.0 * over_dollars / (under_dollars + over_dollars)

        cols = _table_cols(out, header_contains="Sessions", row_contains="Total")
        assert cols["Share"] == f"{expected_share:.1f}%"

    def test_session_with_no_main_thread_usage_block_is_excluded(self, fake_projects, capsys):
        """A session with no main-thread turn carrying a usage block has no
        session_threshold to be above or below -- excluded from the report
        entirely, not shown with an undefined/blank share."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-5", branch="main", ts="2026-05-19T10:00:00.000Z"),  # usage={}
        ])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        assert "No sessions with a resolvable handoff-nudge threshold" in out

    def test_session_with_all_unpriced_turns_is_excluded_not_a_zero_division(self, fake_projects, capsys):
        """Every turn's model has no price-table entry (session_threshold is
        still resolvable -- pricing and context-window resolution are
        independent), so total_dollars is 0: the session is excluded from
        the report rather than raising ZeroDivisionError or reporting an
        undefined share."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-opus-4-7", input=400_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        assert "No sessions with a resolvable handoff-nudge threshold" in out

    def test_multiple_qualifying_sessions_aggregate_correctly_within_and_across_weeks(
        self, fake_projects, capsys
    ):
        """Two qualifying sessions in the same ISO week (2026-W21): the
        week row's Sessions/AboveUSD/TotalUSD reflect the sum of both, not
        just one -- an accumulation bug (data[week_str] reset instead of
        incremented, a wrong dict key, or the Total row summed from the
        wrong per-week field) would slip through every single-session test
        above."""
        under = _priced("claude-sonnet-5", input=50_000, output=1_000, ts="2026-05-19T10:00:00.000Z")
        over = _priced("claude-sonnet-5", input=400_000, output=2_000, ts="2026-05-21T10:00:00.000Z")
        _write_jsonl(fake_projects / "sess_a.jsonl", [under])
        _write_jsonl(fake_projects / "sess_b.jsonl", [over])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out

        rates = _mod._model_rates("claude-sonnet-5")
        under_dollars = 50_000 / 1_000_000 * rates["input"] + 1_000 / 1_000_000 * rates["output"]
        over_dollars = 400_000 / 1_000_000 * rates["input"] + 2_000 / 1_000_000 * rates["output"]
        expected_total = under_dollars + over_dollars
        expected_share = 100.0 * over_dollars / expected_total

        week_cols = _table_cols(out, header_contains="Sessions", row_contains="2026-W21")
        assert int(week_cols["Sessions"]) == 2
        assert float(week_cols["AboveUSD"].replace(",", "")) == pytest.approx(over_dollars, rel=1e-4)
        assert float(week_cols["TotalUSD"].replace(",", "")) == pytest.approx(expected_total, rel=1e-4)
        assert week_cols["Share"] == f"{expected_share:.1f}%"

        total_cols = _table_cols(out, header_contains="Sessions", row_contains="Total")
        assert int(total_cols["Sessions"]) == 2
        assert float(total_cols["AboveUSD"].replace(",", "")) == pytest.approx(over_dollars, rel=1e-4)
        assert float(total_cols["TotalUSD"].replace(",", "")) == pytest.approx(expected_total, rel=1e-4)

    def test_context_at_turn_exactly_equal_to_threshold_counts_as_above(self, fake_projects, capsys):
        """context_at_turn == session_threshold exactly -- the boundary the
        `>=` comparison in cmd_spend_over_threshold governs, and the same
        point the real hook fires at -- counts toward AboveUSD. An off-by-one
        (`>` written instead of `>=`) would silently misclassify this turn as
        under, since no other test in this class exercises the exact
        boundary (all use values far below or far above it)."""
        threshold = _mod.handoff_nudge._hook_effective_fire_threshold("claude-sonnet-5")
        exactly_at_threshold = _priced(
            "claude-sonnet-5", input=threshold, output=1_000, ts="2026-05-19T10:00:00.000Z"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [exactly_at_threshold])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Sessions", row_contains="Total")
        assert cols["Share"] == "100.0%"

    def test_since_filter_excludes_whole_sessions_before_cutoff(self, fake_projects, capsys):
        """--since scopes whole sessions (by first timestamp), matching
        _session_matches_rearm_scope's own convention for this shared
        per-turn machinery -- not individual records within one session."""
        _write_jsonl(fake_projects / "old.jsonl", [
            _priced("claude-sonnet-5", input=400_000, output=1_000, ts="2026-01-15T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "new.jsonl", [
            _priced("claude-sonnet-5", input=400_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args(since="2026-05-01"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Sessions", row_contains="Total")
        assert int(cols["Sessions"]) == 1

    def test_nudge_log_diagnostic_footer_swallows_unresolvable_config_dir(
        self, fake_projects, capsys, monkeypatch
    ):
        """An unresolvable config dir (e.g. $HOME unset) inside the trailing
        _print_nudge_log_diagnostic() footer must not crash an
        already-successful report -- the primary table has already printed."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=400_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])

        def _raise_value_error():
            raise ValueError("HOME is unset or empty, and CLAUDE_CONFIG_DIR is not set")

        monkeypatch.setattr(_mod.scope, "config_dir", _raise_value_error)
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Sessions", row_contains="Total")
        assert cols["Share"] == "100.0%"
        assert "Diagnostic" not in out


class TestSpendOverThresholdDiagnosticFooter:
    def test_footer_counts_schema_drift_lines_in_the_scope_config_dir_log(self, fake_projects, tmp_path, capsys):
        """The diagnostic footer counts the schema-drift lines in scope.config_dir()'s
        .handoff-nudge.log and names that path. fake_projects' scope.config_dir patch
        reaches the footer only while handoff_nudge.py reads config_dir by attribute.
        The test discriminates only because the autouse _isolate_transcript_corpus_lookups
        fixture pins CLAUDE_CONFIG_DIR away from fake_projects' tmp_path, so a by-name
        config_dir() reads a directory with no log."""
        assert Path(os.environ["CLAUDE_CONFIG_DIR"]) != tmp_path, (
            "autouse CLAUDE_CONFIG_DIR pin must differ from fake_projects' config dir"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=400_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "schema-drift session=drift-a event=Stop\n"
            "schema-drift session=drift-b event=Stop\n"
            "schema-drift session=drift-c event=Stop\n"
            "nudged session=drift-a est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        assert f"Diagnostic: 3 schema-drift line(s) in {tmp_path / '.handoff-nudge.log'}" in out

    def test_footer_prints_on_the_empty_data_early_return(self, fake_projects, tmp_path, capsys):
        """A run with no qualifying session takes the early-return path, which still prints
        the diagnostic footer -- the schema-drift count is what explains an empty report."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-opus-4-7", input=400_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        (tmp_path / ".handoff-nudge.log").write_text(
            "schema-drift session=drift-a event=Stop\n"
            "schema-drift session=drift-b event=Stop\n"
        )
        _mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())
        out = capsys.readouterr().out
        assert "No sessions with a resolvable handoff-nudge threshold" in out
        assert "Diagnostic: 2 schema-drift line(s)" in out
