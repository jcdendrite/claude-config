"""Tests for transcript_analysis/pr_cost_ledger.py (the pr-cost ledger's on-disk format,
canonical parser and formatter, append-only upsert, crash-safe write, and the --record lock)."""
import csv
import errno
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from transcript_analysis import (
    pr_cost,
    pr_cost_ledger,
    pricing,
)

from ._pr_cost_helpers import (
    _APPEND_TAB_HINT,
    _CANONICAL_SAMPLE_REVIEW_ROUNDS_CELL,
    _FROZEN_HEADER_CASES,
    _FROZEN_PR_COST_HEADER_LINES,
    _LEDGER_DIR_MARKER,
    _PRE_MODEL_HEADER_LINE,
    _PRIOR_ROW_MARKER,
    _UPGRADE_NOTICE_FRAGMENT,
    _UPGRADE_NOTICE_STEM,
    _enable_pr_cost,
    _fake_pr_cost_subprocess_run,
    _first_round,
    _legacy_row_line,
    _make_mkstemp_create_0644,
    _merged_pr,
    _mutated_review_rounds_cell,
    _pr_cost_args,
    _pre_model_row_line,
    _sample_model_breakdown,
    _sample_pr_cost_row,
    _sample_review_round,
    _sample_review_rounds,
    _upgrade_notice_line,
)
from .conftest import _priced, _two_declared_roots, _write_jsonl


class TestAppendPrCostLedgerRow:
    def test_duplicate_key_without_force_raises(self):
        existing_row = _sample_pr_cost_row()
        with pytest.raises(ValueError, match="already exists"):
            pr_cost_ledger._append_pr_cost_ledger_row(
                [existing_row], _sample_pr_cost_row(), already=existing_row, force=False,
            )

    def test_duplicate_key_with_force_appends_superseding_row_byte_identical_prior_rows(self):
        existing_row = _sample_pr_cost_row(captured_at="2026-01-01T00:00:00Z")
        existing_rows = [existing_row]
        new_row = _sample_pr_cost_row(captured_at="2026-01-02T00:00:00Z", supersedes="2026-01-01T00:00:00Z")

        result = pr_cost_ledger._append_pr_cost_ledger_row(existing_rows, new_row, already=existing_row, force=True)

        assert result == [existing_row, new_row]
        assert (
            pr_cost_ledger._format_pr_cost_ledger_row(result[0])
            == pr_cost_ledger._format_pr_cost_ledger_row(existing_row)
        )

    def test_duplicate_key_without_force_error_omits_raw_repo_value(self):
        existing_row = _sample_pr_cost_row(repo="acme-corp/internal-project")
        with pytest.raises(ValueError) as exc_info:
            pr_cost_ledger._append_pr_cost_ledger_row(
                [existing_row],
                _sample_pr_cost_row(repo="acme-corp/internal-project"),
                already=existing_row,
                force=False,
            )
        assert "acme-corp" not in str(exc_info.value)


class TestPrCostLedgerConcurrentWrite:
    """Two sequential --record-shaped lock/write/unlock cycles, plus a genuine real-thread race
    against the same ledger file exercising the lock's actual mutual-exclusion guarantee."""

    def test_two_sequential_record_writes_both_persist(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        lock_path = ledger_path.with_name(ledger_path.name + ".lock")

        row1 = _sample_pr_cost_row(pr_number=1, captured_at="2026-01-01T00:00:00Z")
        with open(lock_path, "w") as lock_f:
            pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
            try:
                pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [row1])
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

        row2 = _sample_pr_cost_row(pr_number=2, captured_at="2026-01-01T00:00:00Z")
        with open(lock_path, "w") as lock_f:
            pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
            try:
                current_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
                pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*current_rows, row2])
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

        final_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert [r["pr_number"] for r in final_rows] == [1, 2]

    def test_racing_threads_each_persist_their_own_row_via_the_ledger_lock(self, tmp_path):
        """Real threading.Thread contention against _acquire_pr_cost_ledger_lock, not a
        simulated sequential stand-in -- mirrors
        TestMachineIdentity.test_racing_threads_on_first_use_converge_on_one_identity's
        real-thread race shape. Each thread's own acquire/read/modify-write/release cycle must
        leave every thread's row present exactly once, with none lost to an unserialized
        read-modify-write against the shared file. Best-effort: a pass shows this run didn't
        lose data, not that the lock is race-free, since OS thread scheduling isn't controlled
        here."""
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        lock_path = ledger_path.with_name(ledger_path.name + ".lock")
        thread_count = 8

        def _record(pr_number: int) -> None:
            with open(lock_path, "w") as lock_f:
                pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
                try:
                    try:
                        current_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
                    except FileNotFoundError:
                        current_rows = []
                    row = _sample_pr_cost_row(pr_number=pr_number, captured_at="2026-01-01T00:00:00Z")
                    pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*current_rows, row])
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert sorted(r["pr_number"] for r in final_rows) == list(range(thread_count))


class TestPrCostLedgerRowFormatRoundTrip:
    def test_format_then_parse_round_trip_is_lossless(self):
        row = _sample_pr_cost_row()
        line = pr_cost_ledger._format_pr_cost_ledger_row(row)
        parsed = pr_cost_ledger._parse_pr_cost_ledger_row_cells(line.split("\t"), line_no=2)
        assert parsed == row


class TestParsePrCostLedgerFileTextMalformed:
    def _valid_line(self) -> str:
        return pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row())

    def test_wrong_column_count_raises(self):
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "owner/repo\t1\tci1\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="expected .* columns"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_non_numeric_pr_number_raises(self):
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(pr_number="not-a-number"))
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="non-numeric pr_number"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_unknown_join_confidence_raises(self):
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(join_confidence="extreme"))
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="unknown join_confidence"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_unknown_status_raises(self):
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(status="degraded_mystery"))
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="unknown status"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_malformed_repo_raises_without_leaking_raw_value(self):
        """A non-lowercase repo value fails the malformed-repo check, and --
        mirroring _append_pr_cost_ledger_row's duplicate-key error -- the
        raised message omits the raw value, since the ledger's repo column
        is never scrubbed at rest."""
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(repo="Acme-Corp/Internal-Project"))
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="malformed repo value") as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert "Acme-Corp" not in str(exc_info.value)

    def test_malformed_host_raises_without_leaking_raw_value(self):
        """Same guard as the repo check above, mirrored for the host column --
        a non-lowercase host fails the malformed-host check without the raw
        value reaching the raised message, since host is never scrubbed at
        rest either."""
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(host="Acme-Corp.GHE.com"))
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="malformed host value") as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert "Acme-Corp" not in str(exc_info.value)

    def test_malformed_merged_at_raises(self):
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(merged_at="not-a-timestamp"))
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="malformed merged_at"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_malformed_captured_at_raises(self):
        """Guards _latest_pr_cost_row's lexicographic string max() on
        captured_at, which would silently misresolve given a malformed value."""
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(captured_at="2026/01/01"))
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="malformed captured_at"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def _line_with_malformed_cell(self, column: str, malformed_value: str) -> str:
        """A valid formatted row line with `column`'s own cell replaced by
        malformed_value. This bypasses _format_pr_cost_ledger_row's own
        bool/float rendering, which would otherwise reject an arbitrary
        string before parsing is ever reached for those columns."""
        cells = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
        cells[pr_cost_ledger._PR_COST_LEDGER_COLUMNS.index(column)] = malformed_value
        return "\t".join(cells)

    @pytest.mark.parametrize(
        "column,malformed_value",
        [
            ("machine", "AcmeCorp-Bldg9"),
            ("pr_number", "ACME-NONNUMERIC-PRVALUE"),
            ("merged_at", "ACME-BAD-TIMESTAMP"),
            ("rate_stamp", "ACME-BAD-RATE-STAMP"),
            ("join_confidence", "ACME-BAD-CONFIDENCE"),
            ("status", "ACME-BAD-STATUS"),
            ("cache_read_usd", "ACME-BAD-FLOAT"),  # representative _PR_COST_FLOAT_COLUMNS
            ("turn_count", "ACME-BAD-INT"),  # representative _PR_COST_INT_COLUMNS
            ("tests_changed", "ACME-BAD-BOOL"),  # representative _PR_COST_BOOL_COLUMNS
        ],
    )
    def test_malformed_value_omitted_from_message_but_column_and_line_named(
        self, column, malformed_value,
    ):
        """The value-omission discipline the host/repo checks apply above
        is a uniform contract of _parse_pr_cost_ledger_row_cells, not
        special-cased to any column: every validation branch omits the raw
        cell and names only the column and line number."""
        text = (
            pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n"
            + self._line_with_malformed_cell(column, malformed_value) + "\n"
        )
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        message = str(exc_info.value)
        assert malformed_value not in message
        assert column in message
        assert "line 2" in message

    def test_merge_conflict_marker_raises(self):
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + self._valid_line() + "\n<<<<<<< HEAD\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="merge-conflict marker"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_unrecognized_first_line_is_named_without_echo_or_the_newer_version_hint(self):
        text = "not-the-header\n" + self._valid_line() + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert str(exc_info.value) == "missing or mismatched pr-cost ledger header row (unrecognized first line)"

    def test_empty_file_is_named_without_the_newer_version_hint(self):
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text("")
        assert str(exc_info.value) == "missing or mismatched pr-cost ledger header row (the file is empty)"

    def test_current_header_with_a_trailing_space_gets_no_newer_version_hint(self):
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + " \n" + self._valid_line() + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert "update this checkout" not in str(exc_info.value)

    @pytest.mark.parametrize("trailing", [
        pytest.param("", id="nothing-after-the-tab"),
        pytest.param(" ", id="space-after-the-tab"),
        pytest.param("\t", id="second-tab"),
    ])
    def test_current_header_followed_by_a_tab_and_no_text_gets_no_newer_version_hint(self, trailing):
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\t" + trailing + "\n" + self._valid_line() + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert str(exc_info.value) == "missing or mismatched pr-cost ledger header row (unrecognized first line)"

    def test_mismatched_header_raises(self):
        # Dropping a middle column (not the last) keeps this header distinct from every recognized frozen header.
        columns = pr_cost_ledger._PR_COST_LEDGER_COLUMNS
        bad_header = "\t".join([*columns[:5], *columns[6:]])
        text = bad_header + "\n" + self._valid_line() + "\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="missing or mismatched"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)


class TestPrCostLedgerLegacyHostColumnMigration:
    """The pre-host-column header (_PR_COST_LEDGER_LEGACY_HEADER_LINE) is
    a recognized older header -- every row recorded under it predates
    GHE host-awareness, so implicitly belongs to github.com."""

    def test_legacy_header_row_parses_with_host_defaulted_to_github_com(self):
        text = pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE + "\n" + _legacy_row_line() + "\n"

        rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

        assert len(rows) == 1
        assert rows[0]["host"] == "github.com"

    @pytest.mark.parametrize("case", _FROZEN_HEADER_CASES, ids=lambda case: case.case_id)
    def test_record_against_older_header_file_rewrites_every_prior_row_under_the_current_header(
        self, fake_projects, tmp_path, monkeypatch, case,
    ):
        """A subsequent --record write against an older-header file rewrites the whole ledger under the
        current header -- no separate migration script needed. Each prior row keeps its original cells in
        its original order, gaining only the cells its header lacked (host, then the empty model_breakdown,
        zero invalid_usage_records and empty review_rounds defaults)."""
        header_line, make_row_line = case.header_line, case.make_row_line
        expect_host_prefix, expected_tail = bool(case.host_prefix), case.upgraded_tail
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        seed_overrides = [
            {"pr_number": 7, "machine": "legacy01", "captured_at": "2026-01-01T00:00:00Z", "additions": 71},
            # Same key as the row above: a supersede chain.
            {
                "pr_number": 7, "machine": "legacy01", "captured_at": "2026-01-02T00:00:00Z",
                "supersedes": "2026-01-01T00:00:00Z", "additions": 72,
            },
            {
                "pr_number": 8, "machine": "legacy01", "status": "degraded_network", "supersedes": "",
                "tests_changed": False, "plan_file_added": False, "risk_surface_flag": False,
                "cache_read_usd": 0.0, "cache_write_5m_usd": 0.0, "cache_write_1h_usd": 0.0,
                "output_usd": 0.0, "input_usd": 0.0, "opus_dollars": 0.0, "opus_dollar_share_pct": 0.0,
            },
        ]
        if not expect_host_prefix:
            seed_overrides.append({"pr_number": 9, "machine": "ab12cd34", "host": "ghe.example.com"})
        seeded_lines = [make_row_line(**overrides) for overrides in seed_overrides]
        ledger_path.write_text(header_line + "\n" + "\n".join(seeded_lines) + "\n")

        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(repo="owner/repo", merged_prs=merged_prs))

        args = _pr_cost_args(record=True)
        pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        written_lines = ledger_path.read_text().splitlines()
        assert written_lines[0] == pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE
        host_prefix = "github.com\t" if expect_host_prefix else ""
        assert written_lines[1:1 + len(seeded_lines)] == [host_prefix + line + expected_tail for line in seeded_lines]
        assert len(written_lines) == 1 + len(seeded_lines) + 1
        new_row = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())[-1]
        assert new_row["pr_number"] == 1
        assert new_row["model_breakdown"], "the check below returns early on a None cell"
        pr_cost._check_model_breakdown_cell(new_row["model_breakdown"], new_row)  # must not raise


class TestPrCostLedgerFrozenHeaders:
    def test_production_older_headers_equal_the_hand_written_frozen_lines(self):
        pre_host_line, pre_model_line, pre_invalid_usage_line, pre_review_rounds_line = _FROZEN_PR_COST_HEADER_LINES
        assert pre_host_line == pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE, (
            "the pre-host header is frozen; never re-derive from the live tuple"
        )
        assert "\t".join(pr_cost_ledger._PR_COST_LEDGER_PRE_MODEL_COLUMNS) == pre_model_line, (
            "the pre-model header is frozen; never re-derive from the live tuple"
        )
        assert "\t".join(pr_cost_ledger._PR_COST_LEDGER_PRE_INVALID_USAGE_COLUMNS) == pre_invalid_usage_line, (
            "the pre-invalid_usage_records header is frozen; never re-derive from the live tuple"
        )
        assert "\t".join(pr_cost_ledger._PR_COST_LEDGER_PRE_REVIEW_ROUNDS_COLUMNS) == pre_review_rounds_line, (
            "the pre-review_rounds header is frozen; never re-derive from the live tuple"
        )

    def test_current_header_is_the_newest_frozen_header_plus_review_rounds(self):
        assert _FROZEN_PR_COST_HEADER_LINES[-1] + "\treview_rounds" == pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE, (
            "the newest frozen header is frozen; never re-derive from the live tuple. The next column addition"
            " appends the outgoing header to _FROZEN_PR_COST_HEADER_LINES"
        )

    def test_every_recognized_header_is_in_the_header_table(self):
        table = pr_cost_ledger._PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE
        assert set(table) == {*_FROZEN_PR_COST_HEADER_LINES, pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE}

    @pytest.mark.parametrize("header_line", [*_FROZEN_PR_COST_HEADER_LINES])
    def test_every_current_column_missing_from_a_recognized_header_has_a_default(self, header_line):
        header_columns = set(header_line.split("\t"))
        missing = [c for c in pr_cost_ledger._PR_COST_LEDGER_COLUMNS if c not in header_columns]
        assert missing, "a frozen header must lack at least one current column"
        for column in missing:
            assert column in pr_cost_ledger._PR_COST_LEDGER_COLUMN_DEFAULTS, column

    def test_every_column_of_every_recognized_header_exists_in_the_current_column_tuple(self):
        """A column dropped from the current tuple would be silently discarded when an older-header row is rewritten."""
        for header_line, columns in pr_cost_ledger._PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE.items():
            retired = [c for c in columns if c not in pr_cost_ledger._PR_COST_LEDGER_COLUMNS]
            assert retired == [], header_line

    @pytest.mark.parametrize("case", _FROZEN_HEADER_CASES, ids=lambda case: case.case_id)
    def test_literal_row_parses_to_every_column_by_name_with_defaults_filled(self, case):
        parsed_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(case.header_line + "\n" + case.literal_row_line + "\n")

        assert parsed_rows == [case.literal_row_parsed]
        assert parsed_rows[0]["review_rounds"] is None

    @pytest.mark.parametrize("case", _FROZEN_HEADER_CASES, ids=lambda case: case.case_id)
    def test_literal_row_survives_the_real_upgrading_write_as_its_original_cells_plus_the_defaulted_ones(
        self, tmp_path, case,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text(case.header_line + "\n" + case.literal_row_line + "\n")
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

        rewritten_lines = ledger_path.read_text().splitlines()
        expected_line = case.host_prefix + case.literal_row_line + case.upgraded_tail
        assert rewritten_lines == [pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE, expected_line]
        assert pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text()) == [case.literal_row_parsed]

    @pytest.mark.parametrize(
        "header_line,make_row_line,short_row_hint",
        [
            *[
                pytest.param(case.header_line, case.make_row_line, case.short_row_hint, id=case.case_id)
                for case in _FROZEN_HEADER_CASES
            ],
            pytest.param(
                pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE,
                lambda: pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()),
                _APPEND_TAB_HINT,
                id="current",
            ),
        ],
    )
    def test_row_width_is_checked_against_its_own_files_header_with_the_hint_the_last_column_implies(
        self, header_line, make_row_line, short_row_hint,
    ):
        """The hint follows the header's last column: a JSON column gets append-a-tab, a column with a
        non-empty default gets restore-the-value, and a column with no default entry gets none."""
        width = len(header_line.split("\t"))
        valid_cells = make_row_line().split("\t")
        assert len(valid_cells) == width, "fixture row must match its header's width"
        wider_line = "\t".join([*valid_cells, "extra"])
        shorter_line = "\t".join(valid_cells[:-1])

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as wider_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(header_line + "\n" + wider_line + "\n")
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as shorter_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(header_line + "\n" + shorter_line + "\n")

        assert str(wider_info.value) == f"line 2: expected {width} columns, got {width + 1}"
        assert str(shorter_info.value) == f"line 2: expected {width} columns, got {width - 1}{short_row_hint}"

    def test_header_equal_to_current_plus_an_extra_column_names_the_newer_version_remedy(self):
        header_line = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\tnewer_column"
        text = header_line + "\n" + "x\n"
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="missing or mismatched") as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert "update this checkout" in str(exc_info.value)
        assert "docs/pr-cost.md" in str(exc_info.value)


_CELL_MARKER = "cellmarker"  # passes the cell key rule, so it can stand in a key position
_CANONICAL_SAMPLE_BREAKDOWN_CELL = (
    '{"claude-sonnet-5":{"standard":{"cache_read":{"tokens":1000,"usd_micros":1500000},'
    '"cache_write_1h":{"tokens":100,"usd_micros":100000},"cache_write_5m":{"tokens":200,"usd_micros":250000},'
    '"input":{"tokens":300,"usd_micros":500000},"output":{"tokens":500,"usd_micros":2000000}}}}'
)


def _single_group_cell(model="claude-sonnet-5", variant="standard", token_class="input", leaf=None) -> str:
    return json.dumps({model: {variant: {token_class: leaf if leaf is not None else {"tokens": 1, "usd_micros": 1}}}})


def _current_ledger_text_with_cell(cell: str, column: str = "model_breakdown") -> str:
    cells = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
    cells[pr_cost_ledger._PR_COST_LEDGER_COLUMNS.index(column)] = cell
    return pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "\t".join(cells) + "\n"


# Hostile primitives shared by the model_breakdown and review_rounds codec suites: a bad integer and a bad key.
_PRIMITIVE_INTEGER_VALUES = {
    "float": 1.5, "bool": True, "negative": -1, "nan": float("nan"), "infinity": float("inf"), "above-int64": 2**63,
}
_PRIMITIVE_KEYS = {
    "non-ascii": _CELL_MARKER + "é", "leading-dot": f".{_CELL_MARKER}", "leading-hyphen": f"-{_CELL_MARKER}",
    "leading-underscore": f"_{_CELL_MARKER}", "slash": f"{_CELL_MARKER}/x", "colon": f"{_CELL_MARKER}:x",
    "space": f"{_CELL_MARKER} x", "trailing-newline": f"{_CELL_MARKER}\n",
}
_LEAF_JSON = '{"tokens":1,"usd_micros":1}'
_CODEC_REJECT_CASES = [
    pytest.param(f"{_CELL_MARKER} not json", id="non-json"),
    pytest.param("   ", id="whitespace-only"),
    pytest.param(
        f'{{"{_CELL_MARKER}":{{"standard":{{"input":{_LEAF_JSON}}}}},"{_CELL_MARKER}":{{"standard":{{"input":{_LEAF_JSON}}}}}}}',
        id="duplicate-model-key",
    ),
    pytest.param(
        f'{{"claude-sonnet-5":{{"{_CELL_MARKER}":{{"input":{_LEAF_JSON}}},"{_CELL_MARKER}":{{"input":{_LEAF_JSON}}}}}}}',
        id="duplicate-variant-key",
    ),
    pytest.param(
        f'{{"claude-sonnet-5":{{"standard":{{"{_CELL_MARKER}":{_LEAF_JSON},"{_CELL_MARKER}":{_LEAF_JSON}}}}}}}',
        id="duplicate-class-key",
    ),
    pytest.param(
        '{"claude-sonnet-5":{"standard":{"input":{"tokens":1,"tokens":2,"usd_micros":1}}}}', id="duplicate-leaf-key",
    ),
    pytest.param("null", id="top-level-null"),
    pytest.param(json.dumps([_CELL_MARKER]), id="top-level-list"),
    pytest.param("12345", id="top-level-number"),
    pytest.param(json.dumps(_CELL_MARKER), id="top-level-string"),
    pytest.param(json.dumps({_CELL_MARKER: [_CELL_MARKER]}), id="model-value-not-an-object"),
    pytest.param(json.dumps({"claude-sonnet-5": {_CELL_MARKER: [_CELL_MARKER]}}), id="variant-value-not-an-object"),
    pytest.param(json.dumps({"claude-sonnet-5": {"standard": {"input": _CELL_MARKER}}}), id="class-value-not-an-object"),
    pytest.param(json.dumps({_CELL_MARKER: {}}), id="empty-model-object"),
    pytest.param(json.dumps({"claude-sonnet-5": {_CELL_MARKER: {}}}), id="empty-variant-object"),
    pytest.param(_single_group_cell(model=""), id="empty-model-key"),
    pytest.param(_single_group_cell(variant=""), id="empty-variant-key"),
    pytest.param(_single_group_cell(token_class=""), id="empty-class-key"),
    pytest.param(_single_group_cell(model=_CELL_MARKER.upper()), id="uppercase-model-key"),
    pytest.param(_single_group_cell(variant=_CELL_MARKER.upper()), id="uppercase-variant-key"),
    pytest.param(_single_group_cell(token_class=_CELL_MARKER.upper()), id="uppercase-class-key"),
    pytest.param(_single_group_cell(model="a" * 65), id="65-character-key"),
    *[
        pytest.param(_single_group_cell(**{position: bad_key}), id=f"{label}-{position.removeprefix('token_')}-key")
        for position in ("model", "variant", "token_class")
        for label, bad_key in _PRIMITIVE_KEYS.items()
    ],
    pytest.param(_single_group_cell(leaf={"tokens": 1}), id="leaf-missing-usd-micros"),
    pytest.param(_single_group_cell(leaf={"usd_micros": 1}), id="leaf-missing-tokens"),
    pytest.param(_single_group_cell(leaf={"tokens": 1, "usd_micros": 1, _CELL_MARKER: 2}), id="leaf-extra-key"),
    *[
        pytest.param(_single_group_cell(leaf={leaf_key: bad_value, other_key: 1}), id=f"{leaf_key}-{label}")
        for leaf_key, other_key in (("tokens", "usd_micros"), ("usd_micros", "tokens"))
        for label, bad_value in _PRIMITIVE_INTEGER_VALUES.items()
    ],
]


class TestDecodeModelBreakdownCell:
    @pytest.mark.parametrize("cell", _CODEC_REJECT_CASES)
    def test_malformed_cell_raises_naming_line_and_column_without_echoing_cell_text(self, cell):
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(_current_ledger_text_with_cell(cell))

        message = str(exc_info.value)
        assert "line 2" in message
        assert "model_breakdown" in message
        assert _CELL_MARKER not in message.lower()
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_message_format_is_line_then_fixed_rule(self):
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._decode_model_breakdown_cell("not json", 5)
        assert str(exc_info.value) == "line 5: malformed model_breakdown (not valid JSON)"

    def test_recursion_error_from_json_decoding_maps_to_a_parse_error(self, monkeypatch):
        def json_loads_overflowing_the_recursion_limit(*args, **kwargs):
            raise RecursionError("maximum recursion depth exceeded")

        # json.loads is process-wide, so the patch spans only the call under test.
        with monkeypatch.context() as patch_window:
            patch_window.setattr(pr_cost_ledger.json, "loads", json_loads_overflowing_the_recursion_limit)
            with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match=r"\(nesting too deep\)") as exc_info:
                pr_cost_ledger._decode_model_breakdown_cell("{}", 2)

        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_over_long_integer_literal_maps_value_error_to_a_parse_error(self):
        """CPython's default 4300-digit int string-conversion limit makes json.loads raise ValueError for a
        5000-digit literal. The limit is pinned here so a contributor's PYTHONINTMAXSTRDIGITS cannot change
        the outcome."""
        previous_limit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            cell = '{"claude-sonnet-5":{"standard":{"input":{"tokens":' + "1" * 5000 + ',"usd_micros":1}}}}'
            with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="malformed model_breakdown") as exc_info:
                pr_cost_ledger._decode_model_breakdown_cell(cell, 2)
        finally:
            sys.set_int_max_str_digits(previous_limit)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_leaf_integers_at_the_int64_maximum_are_accepted(self):
        cell = _single_group_cell(leaf={"tokens": 2**63 - 1, "usd_micros": 2**63 - 1})
        assert pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_empty_cell_decodes_to_not_recorded_and_empty_object_to_recorded_nothing(self):
        assert pr_cost_ledger._decode_model_breakdown_cell("", 2) is None
        assert pr_cost_ledger._decode_model_breakdown_cell("{}", 2) == {}

    def test_canonical_populated_cell_decodes_to_its_value(self):
        decoded = pr_cost_ledger._decode_model_breakdown_cell(_CANONICAL_SAMPLE_BREAKDOWN_CELL, 2)
        assert decoded == _sample_model_breakdown()

    def test_whitespace_and_unsorted_variant_decodes_to_the_same_value_and_reformats_canonically(self):
        spaced_cell = json.dumps(_sample_model_breakdown(), separators=(" , ", " : "))  # insertion order, not sorted
        unsorted_cell = '{ "claude-sonnet-5" : { "standard" : { "output": {"usd_micros": 2000000, "tokens": 500},' \
            ' "input": {"usd_micros": 500000, "tokens": 300}, "cache_write_5m": {"usd_micros": 250000, "tokens": 200},' \
            ' "cache_write_1h": {"usd_micros": 100000, "tokens": 100}, "cache_read": {"usd_micros": 1500000,' \
            ' "tokens": 1000} } } }'
        for cell in (spaced_cell, unsorted_cell):
            text = _current_ledger_text_with_cell(cell)

            row = pr_cost_ledger._parse_pr_cost_ledger_file_text(text)[0]

            assert row["model_breakdown"] == _sample_model_breakdown()
            rendered_cell = pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[_ledger_cell_index("model_breakdown")]
            assert rendered_cell == _CANONICAL_SAMPLE_BREAKDOWN_CELL

    def test_unknown_but_well_formed_variant_and_class_labels_are_accepted(self):
        cell = _single_group_cell(variant="ultra_fast", token_class="cache_write_24h")
        assert pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_group_missing_a_class_is_accepted(self):
        cell = _single_group_cell(token_class="output")
        assert pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_retired_synthetic_model_key_is_accepted(self):
        cell = _single_group_cell(model="claude-test-retired")
        assert pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_64_character_key_is_accepted(self):
        cell = _single_group_cell(model="a" * 64)
        assert pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    @pytest.mark.parametrize("model_id", sorted(pricing._MODEL_BASE_INPUT_RATES))
    def test_every_priced_model_id_satisfies_the_key_rule(self, model_id):
        cell = _single_group_cell(model=model_id)
        assert pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    @pytest.mark.parametrize("model_id", sorted(pricing._MODEL_BASE_INPUT_RATES))
    def test_every_priced_model_id_matches_the_vendor_model_id_grammar(self, model_id):
        """Table keys persist verbatim in ledger rows and exports, so only vendor-published IDs may be keys.
        The family list below is the review checkpoint: a new family's first key fails here until a reviewer adds it."""
        assert re.fullmatch(r"claude-(opus|sonnet|haiku|fable)(-[0-9]+)+", model_id)

    def test_pricing_variant_labels_equal_the_hand_written_literal(self):
        assert pricing._PRICING_VARIANTS == ("standard", "fast", "us_geo", "fast_us_geo"), (
            "labels persist in ledger rows: rename or remove none, and add a new one to this literal"
        )

    def test_token_class_labels_equal_the_hand_written_literal(self):
        assert pricing._TOKEN_CLASSES == ("cache_read", "cache_write_5m", "cache_write_1h", "output", "input"), (
            "labels persist in ledger rows: rename or remove none, and add a new one to this literal"
        )

    def test_hand_written_golden_ledger_with_an_unknown_label_and_a_retired_key_parses(self):
        """The header line is written inline, not built from production constants, so a rename of any
        column or reshaping of the parser fails this independent of the helpers."""
        header_line = (
            "host\trepo\tpr_number\tmachine\thead_branch\tmerged_at\trate_stamp\tcaptured_at\tjoin_confidence"
            "\tsupersedes\tstatus\tcache_read_usd\tcache_write_5m_usd\tcache_write_1h_usd\toutput_usd\tinput_usd"
            "\tcache_read_tokens\tcache_write_5m_tokens\tcache_write_1h_tokens\toutput_tokens\tinput_tokens"
            "\tunpriced_turns\tunpriced_tokens\tturn_count\tsession_count\topus_dollars\topus_dollar_share_pct"
            "\tsum_context_at_turn\tmean_context_at_turn\tadditions\tdeletions\tchanged_files\tcommit_count"
            "\treview_comment_count\tdistinct_top_level_dirs\tdistinct_file_extensions\ttests_changed"
            "\tplan_file_added\trisk_surface_flag\tmodel_breakdown"
        )
        golden_cell = (
            '{"claude-test-retired":{"ultra_fast":{"cache_write_24h":{"tokens":3,"usd_micros":4},'
            '"input":{"tokens":5,"usd_micros":6}}},"claude-sonnet-5":{"standard":{"input":{"tokens":7,"usd_micros":8}}}}'
        )
        row_line = "\t".join([
            "github.com", "owner/repo", "42", "ci1", "account-1/branch-1", "2026-01-01T00:00:00Z", "2026-08-02",
            "2026-01-02T00:00:00Z", "high", "", "ok",
            "1.500000", "0.250000", "0.100000", "2.000000", "0.500000",
            "1000", "200", "100", "500", "300", "0", "0", "5", "2", "0.000000", "0.000000", "1500", "300.000000",
            "42", "10", "3", "4", "1", "2", "3", "true", "true", "false",
            golden_cell,
        ])

        row = pr_cost_ledger._parse_pr_cost_ledger_file_text(header_line + "\n" + row_line + "\n")[0]

        assert row["model_breakdown"]["claude-test-retired"]["ultra_fast"]["cache_write_24h"] == {"tokens": 3, "usd_micros": 4}
        assert row["model_breakdown"]["claude-sonnet-5"]["standard"]["input"] == {"tokens": 7, "usd_micros": 8}


class TestFormatModelBreakdownCell:
    def test_populated_cell_renders_to_its_canonical_bytes(self):
        """Pins one cell's canonical encoding at cell level, not as a whole row; every other test compares
        cells via json.loads."""
        row = _sample_pr_cost_row(model_breakdown=_sample_model_breakdown())
        rendered_cell = pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[_ledger_cell_index("model_breakdown")]
        assert rendered_cell == _CANONICAL_SAMPLE_BREAKDOWN_CELL

    def test_not_recorded_cell_renders_empty(self):
        rendered_line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row())
        assert rendered_line.endswith("\tfalse\t\t0\t")

    def test_empty_object_cell_renders_as_two_characters_and_round_trips_distinct_from_not_recorded(self):
        row = _sample_pr_cost_row(model_breakdown={})

        line = pr_cost_ledger._format_pr_cost_ledger_row(row)
        parsed_row = pr_cost_ledger._parse_pr_cost_ledger_row_cells(line.split("\t"), 2)

        assert line.split("\t")[_ledger_cell_index("model_breakdown")] == "{}"
        assert parsed_row["model_breakdown"] == {}
        assert parsed_row["model_breakdown"] is not None
        assert parsed_row == row

    def test_empty_object_and_not_recorded_cells_survive_a_file_write_as_different_cells(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        rows = [_sample_pr_cost_row(pr_number=1, model_breakdown={}), _sample_pr_cost_row(pr_number=2)]

        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows)

        data_lines = ledger_path.read_text().splitlines()[1:]
        model_breakdown_index = _ledger_cell_index("model_breakdown")
        assert [line.split("\t")[model_breakdown_index] for line in data_lines] == ["{}", ""]
        parsed_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert [r["model_breakdown"] for r in parsed_rows] == [{}, None]

    def test_two_argument_row_parser_round_trips_a_populated_row(self):
        row = _sample_pr_cost_row(model_breakdown=_sample_model_breakdown())
        line = pr_cost_ledger._format_pr_cost_ledger_row(row)
        assert pr_cost_ledger._parse_pr_cost_ledger_row_cells(line.split("\t"), 2) == row

    def test_pre_model_line_formats_as_its_original_cells_plus_the_two_defaulted_ones(self):
        overrides = {"pr_number": 5, "additions": 99}
        assert _pre_model_row_line(**overrides) + "\t\t0\t" == pr_cost_ledger._format_pr_cost_ledger_row(
            _sample_pr_cost_row(**overrides)
        )

    @pytest.mark.parametrize("model_breakdown", [_sample_model_breakdown(), None], ids=["populated", "empty-last-cell"])
    def test_csv_reader_and_split_yield_the_same_cells(self, model_breakdown):
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(model_breakdown=model_breakdown))
        assert next(csv.reader([line], delimiter="\t")) == line.split("\t")


class TestWritePrCostLedgerFileMode:
    def test_fresh_file_created_with_0600_even_when_mkstemp_creates_0644(self, tmp_path, monkeypatch):
        _make_mkstemp_create_0644(monkeypatch)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [])
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o600

    def test_existing_file_mode_preserved_across_write(self, tmp_path):
        """A user-loosened mode on an existing ledger file is preserved, not
        silently reset back to 0600 on a later write."""
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [])
        os.chmod(ledger_path, 0o644)
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o644


class TestWritePrCostLedgerFileVerificationFailure:
    def test_readback_mismatch_raises_without_publishing(self, tmp_path, monkeypatch):
        """Forces the write/read-back byte-equality check to fail: the
        function must raise rather than ever call os.replace, so the
        destination path is never created."""
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        real_read_text = Path.read_text

        def corrupting_read_text(self, *a, **kw):
            return real_read_text(self, *a, **kw) + "CORRUPTED"

        monkeypatch.setattr(Path, "read_text", corrupting_read_text)
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [])
        assert str(exc_info.value) == "refusing to write the ledger (ledger unchanged): write verification mismatch"
        assert not ledger_path.exists()
        assert list(tmp_path.glob(".pr-cost-ledger-*.tmp")) == []

    @pytest.mark.parametrize(
        "separator",
        [pytest.param("\t", id="tab"), pytest.param("\n", id="newline"), pytest.param("\r", id="carriage-return")],
    )
    def test_row_cell_containing_a_tab_or_line_break_is_refused_with_the_write_refusal_prefix_and_no_temp_file(
        self, tmp_path, separator,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=1)])
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
                _sample_pr_cost_row(pr_number=1),
                _sample_pr_cost_row(pr_number=2, head_branch=f"separator{separator}in-branch"),
            ])

        assert str(exc_info.value) == (
            "refusing to write the ledger (ledger unchanged): "
            "column 'head_branch' contains a tab or newline -- refusing to write a corrupt row"
        )
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_replace_failure_propagates_leaving_the_ledger_unchanged_and_no_temp_file(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=1)])
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        def replace_failing(source, destination):
            raise OSError(errno.EIO, "replace failed")

        monkeypatch.setattr(os, "replace", replace_failing)

        with pytest.raises(OSError, match="replace failed"):
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
                _sample_pr_cost_row(pr_number=1), _sample_pr_cost_row(pr_number=2),
            ])

        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)


class TestWritePrCostLedgerFileDurability:
    def test_temp_file_is_fsynced_after_its_bytes_are_flushed_and_before_the_replace(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        events: list[tuple[str, int]] = []
        real_fsync, real_replace = os.fsync, os.replace

        def recording_fsync(fd):
            events.append(("fsync", os.fstat(fd).st_size))
            real_fsync(fd)

        def recording_replace(source, destination):
            events.append(("replace", 0))
            real_replace(source, destination)

        monkeypatch.setattr(os, "fsync", recording_fsync)
        monkeypatch.setattr(os, "replace", recording_replace)

        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])

        assert events == [("fsync", ledger_path.stat().st_size), ("replace", 0)]

    def test_fsync_failure_propagates_leaving_the_ledger_unchanged_and_no_temp_file(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=1)])
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        def fsync_failing(fd):
            raise OSError(errno.EIO, "fsync failed")

        monkeypatch.setattr(os, "fsync", fsync_failing)

        with pytest.raises(OSError, match="fsync failed"):
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
                _sample_pr_cost_row(pr_number=1), _sample_pr_cost_row(pr_number=2),
            ])

        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)


_REFUSAL_SUFFIX = (
    " -- a claude-config defect, or the ledger was edited during this run;"
    " rerun, and if it repeats on a current claude-config, report it"
)


def _change_refusal(data_row_ordinal: int, column: str) -> str:
    return (
        "refusing to write the ledger (ledger unchanged): the rewrite would change prior data row"
        f" {data_row_ordinal}, column {column}{_REFUSAL_SUFFIX}"
    )


def _drop_refusal(first_lost_ordinal: int) -> str:
    return (
        "refusing to write the ledger (ledger unchanged): the rewrite would drop prior data rows from"
        f" {first_lost_ordinal} on{_REFUSAL_SUFFIX}"
    )


def _ledger_cell_index(column: str) -> int:
    return pr_cost_ledger._PR_COST_LEDGER_COLUMNS.index(column)


def _assert_ledger_untouched(ledger_path: Path, before_bytes: bytes, before_mtime_ns: int) -> None:
    assert ledger_path.read_bytes() == before_bytes
    assert ledger_path.stat().st_mtime_ns == before_mtime_ns
    assert list(ledger_path.parent.glob(".pr-cost-ledger-*.tmp")) == []


class TestWritePrCostLedgerFileValuePostcondition:
    def _seed_two_rows(self, ledger_path: Path) -> list[dict]:
        rows = [
            _sample_pr_cost_row(pr_number=1, model_breakdown=_sample_model_breakdown()),
            _sample_pr_cost_row(pr_number=2, additions=77),
        ]
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows)
        return pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

    def test_encoder_that_drops_a_value_is_refused_naming_row_and_column_with_ledger_unchanged(
        self, tmp_path, monkeypatch,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        monkeypatch.setattr(pr_cost_ledger, "_encode_json_cell", lambda value: "{}")

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*prior_rows, _sample_pr_cost_row(pr_number=3)])

        assert str(exc_info.value) == _change_refusal(1, "model_breakdown")
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_call_that_drops_a_prior_row_is_refused_with_ledger_unchanged(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows[:1])

        assert str(exc_info.value) == _drop_refusal(2)
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_call_that_reorders_prior_rows_is_refused_with_ledger_unchanged(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [prior_rows[1], prior_rows[0]])

        assert str(exc_info.value) == _change_refusal(1, "pr_number")
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_hand_edited_cells_that_re_render_at_ledger_precision_pass_and_re_render(self, tmp_path):
        """A hand-edited 1.5, a reformatted JSON cell, a seven-decimal 0.1234567, and -1e-7 are all values
        the parser accepts, and the write re-renders them at the ledger's six decimals / canonical encoding
        instead of refusing."""
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        cells = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
        cells[_ledger_cell_index("cache_read_usd")] = "1.5"
        cells[_ledger_cell_index("opus_dollars")] = "0.1234567"
        cells[_ledger_cell_index("mean_context_at_turn")] = "-1e-7"
        cells[_ledger_cell_index("model_breakdown")] = json.dumps(_sample_model_breakdown(), separators=(" , ", " : "))
        ledger_path.write_text(
            pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "\t".join(cells) + "\n"
        )
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

        rewritten_cells = ledger_path.read_text().splitlines()[1].split("\t")
        assert rewritten_cells[_ledger_cell_index("cache_read_usd")] == "1.500000"
        assert rewritten_cells[_ledger_cell_index("opus_dollars")] == "0.123457"
        assert rewritten_cells[_ledger_cell_index("mean_context_at_turn")] == "-0.000000"
        assert rewritten_cells[_ledger_cell_index("model_breakdown")] == _CANONICAL_SAMPLE_BREAKDOWN_CELL

    def test_six_decimal_comparison_passes_edge_float_cells_through_the_writer(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        edited_float_cells = {
            "cache_read_usd": "-1e-7", "cache_write_5m_usd": "0.0078125", "cache_write_1h_usd": "2.5e-06",
            "output_usd": "5e-07", "input_usd": "1.5e-06", "opus_dollars": "1e300",
        }
        cells = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
        for column, edited_cell in edited_float_cells.items():
            cells[_ledger_cell_index(column)] = edited_cell
        ledger_path.write_text(
            pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "\t".join(cells) + "\n"
        )
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

        rewritten_cells = ledger_path.read_text().splitlines()[1].split("\t")
        for column, edited_cell in edited_float_cells.items():
            assert rewritten_cells[_ledger_cell_index(column)] == f"{float(edited_cell):.6f}", column

    def test_malformed_existing_file_is_refused_with_the_changed_during_this_run_wording(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text("not-the-header\nsome\tbad\trow\n")
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])

        assert str(exc_info.value).startswith(
            "refusing to write the ledger (ledger unchanged): the ledger on disk changed during this run"
            " and no longer parses: missing or mismatched pr-cost ledger header row"
        )
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_non_not_found_oserror_from_the_prior_file_read_propagates_unchanged(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        real_read_text = Path.read_text

        def read_text_denied_for_the_ledger_only(self, *args, **kwargs):
            if self == ledger_path:
                raise PermissionError(errno.EACCES, "denied")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text_denied_for_the_ledger_only)

        with pytest.raises(PermissionError):
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=9)])

        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_structurally_invalid_cell_is_refused_with_staged_rewrite_wording_and_ledger_unchanged(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        invalid_cell = {_CELL_MARKER.upper(): {"standard": {"input": {"tokens": 1, "usd_micros": 1}}}}

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(
                ledger_path, [*prior_rows, _sample_pr_cost_row(pr_number=3, model_breakdown=invalid_cell)],
            )

        message = str(exc_info.value)
        assert message.startswith(
            "refusing to write the ledger (ledger unchanged): the staged rewrite failed validation: line 4:"
        )
        assert "model_breakdown" in message
        assert _CELL_MARKER not in message.lower()
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_structurally_invalid_cell_with_no_existing_ledger_leaves_no_file(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        invalid_cell = {_CELL_MARKER.upper(): {"standard": {"input": {"tokens": 1, "usd_micros": 1}}}}

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="staged rewrite failed validation"):
            pr_cost_ledger._write_pr_cost_ledger_file(
                ledger_path, [_sample_pr_cost_row(model_breakdown=invalid_cell)],
            )

        assert not ledger_path.exists()
        assert list(tmp_path.glob(".pr-cost-ledger-*.tmp")) == []


_OLDER_HEADER_PARAMS = [
    pytest.param(case.header_line, case.make_row_line, id=case.case_id) for case in _FROZEN_HEADER_CASES
]


class TestWritePrCostLedgerFilePostconditionOnTheUpgradingWrite:
    """The first rewrite of an older-header file is the sole copy of every prior row once transcripts age out,
    so the value postcondition has to refuse there too."""

    @staticmethod
    def _seed_two_rows_under_an_older_header(ledger_path: Path, header_line: str, make_row_line) -> list[dict]:
        seeded_lines = [make_row_line(pr_number=1, additions=71), make_row_line(pr_number=2, additions=77)]
        ledger_path.write_text(header_line + "\n" + "\n".join(seeded_lines) + "\n")
        return pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            *_OLDER_HEADER_PARAMS,
        ],
    )
    @pytest.mark.parametrize("defect", ["formatter-changes-a-value", "call-drops-a-row", "call-reorders-rows"])
    def test_rewrite_that_changes_drops_or_reorders_a_prior_row_is_refused_with_the_file_unchanged(
        self, tmp_path, monkeypatch, header_line, make_row_line, defect,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows_under_an_older_header(ledger_path, header_line, make_row_line)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        rows_to_write, expected_refusal = {
            "formatter-changes-a-value": (prior_rows, _change_refusal(1, "additions")),
            "call-drops-a-row": (prior_rows[:1], _drop_refusal(2)),
            "call-reorders-rows": ([prior_rows[1], prior_rows[0]], _change_refusal(1, "pr_number")),
        }[defect]
        if defect == "formatter-changes-a-value":
            real_format = pr_cost_ledger._format_pr_cost_ledger_row

            def format_adding_one_to_additions(row, **kwargs):
                return real_format({**row, "additions": row["additions"] + 1}, **kwargs)

            monkeypatch.setattr(pr_cost_ledger, "_format_pr_cost_ledger_row", format_adding_one_to_additions)

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows_to_write)

        assert str(exc_info.value) == expected_refusal
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)


def _current_header_row_line(**overrides) -> str:
    return pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(**overrides))


class TestWritePrCostLedgerFilePostconditionCoversEveryPriorRow:
    """The postcondition compares all prior rows, not only the first ones: a defect confined to the third of
    three prior rows must be refused, naming ordinal 3."""

    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            pytest.param(pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE, _current_header_row_line, id="current"),
            *_OLDER_HEADER_PARAMS,
        ],
    )
    @pytest.mark.parametrize("defect", ["formatter-changes-the-third-row", "call-changes-the-third-row"])
    def test_defect_confined_to_the_third_prior_row_is_refused_naming_ordinal_3_with_the_file_unchanged(
        self, tmp_path, monkeypatch, header_line, make_row_line, defect,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        seeded_lines = [make_row_line(pr_number=number, additions=70 + number) for number in (1, 2, 3)]
        ledger_path.write_text(header_line + "\n" + "\n".join(seeded_lines) + "\n")
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        rows_to_write = prior_rows
        if defect == "formatter-changes-the-third-row":
            real_format = pr_cost_ledger._format_pr_cost_ledger_row

            def format_adding_one_to_additions_of_pr_3(row, **kwargs):
                if row["pr_number"] == 3:
                    row = {**row, "additions": row["additions"] + 1}
                return real_format(row, **kwargs)

            monkeypatch.setattr(pr_cost_ledger, "_format_pr_cost_ledger_row", format_adding_one_to_additions_of_pr_3)
        else:
            rows_to_write = [*prior_rows[:2], {**prior_rows[2], "additions": prior_rows[2]["additions"] + 1}]

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows_to_write)

        assert str(exc_info.value) == _change_refusal(3, "additions")
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)


def _changed_value_of_the_same_type(value):
    """A value of the same type as value that the postcondition must see as different: bool flipped (checked before
    int, since bool is an int), int plus one, float plus 0.001 (above the ledger's six-decimal precision), string
    suffixed, a dict with one more key."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.001
    if isinstance(value, str):
        return value + "-changed"
    if isinstance(value, dict):
        return {**value, "claude-opus-5": {}}
    raise AssertionError(f"no changed value defined for {type(value).__name__}")


class TestPriorRowsPreservedComparison:
    """_refuse_if_prior_rows_not_preserved compares floats at the ledger's six decimals and every other column exactly."""

    @pytest.mark.parametrize("column", pr_cost_ledger._PR_COST_LEDGER_COLUMNS)
    def test_a_change_in_any_single_column_is_refused_naming_that_column(self, column):
        prior_row = _sample_pr_cost_row(model_breakdown=_sample_model_breakdown(), review_rounds=_sample_review_rounds())
        changed_row = {**prior_row, column: _changed_value_of_the_same_type(prior_row[column])}

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._refuse_if_prior_rows_not_preserved([prior_row], [changed_row])

        assert str(exc_info.value) == _change_refusal(1, column)

    @staticmethod
    def _refuse_if_rows_differ_in(column: str, prior_value, staged_value) -> None:
        pr_cost_ledger._refuse_if_prior_rows_not_preserved(
            [_sample_pr_cost_row(**{column: prior_value})], [_sample_pr_cost_row(**{column: staged_value})],
        )

    @pytest.mark.parametrize(
        "column,prior_value,staged_value",
        [
            pytest.param("cache_read_usd", 1.5, 1.500001, id="float-one-unit-in-the-sixth-decimal-up"),
            pytest.param("opus_dollars", 0.123457, 0.123458, id="float-one-unit-in-the-sixth-decimal-up-from-a-rounded-value"),
            pytest.param("mean_context_at_turn", -0.000001, 0.0, id="float-one-unit-in-the-sixth-decimal-negative"),
            pytest.param("head_branch", "account-1/branch-1", "account-1/branch-2", id="string"),
            pytest.param("status", "ok", "degraded_network", id="enum-string"),
            pytest.param("tests_changed", True, False, id="bool"),
            pytest.param("additions", 42, 43, id="int"),
            pytest.param("model_breakdown", None, {}, id="not-recorded-became-empty-object"),
            pytest.param("model_breakdown", {}, None, id="empty-object-became-not-recorded"),
        ],
    )
    def test_value_difference_is_refused_naming_the_column(self, column, prior_value, staged_value):
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            self._refuse_if_rows_differ_in(column, prior_value, staged_value)

        assert str(exc_info.value) == _change_refusal(1, column)

    @pytest.mark.parametrize(
        "column,prior_value,staged_value",
        [
            pytest.param("cache_read_usd", 1.5, 1.5000001, id="float-below-the-sixth-decimal"),
            pytest.param("opus_dollars", 0.1234567, 0.123457, id="float-seven-decimals-against-its-six-decimal-rendering"),
            pytest.param("mean_context_at_turn", -1e-7, 0.0, id="float-negative-below-the-sixth-decimal"),
            pytest.param("model_breakdown", _sample_model_breakdown(), _sample_model_breakdown(), id="equal-cells"),
        ],
    )
    def test_difference_that_vanishes_at_the_ledgers_precision_passes(self, column, prior_value, staged_value):
        self._refuse_if_rows_differ_in(column, prior_value, staged_value)

    @pytest.mark.parametrize(
        "changed_staged_index,expected_ordinal",
        [
            pytest.param(0, 1, id="mismatch-at-an-earlier-ordinal-than-the-truncation"),
            pytest.param(1, 2, id="mismatch-at-the-last-row-before-the-truncation-boundary"),
            pytest.param(None, 3, id="clean-truncation-names-the-first-lost-ordinal"),
        ],
    )
    def test_staged_rows_shorter_than_prior_name_the_first_ordinal_that_differs_or_is_lost(
        self, changed_staged_index, expected_ordinal,
    ):
        prior_rows = [_sample_pr_cost_row(pr_number=number) for number in (1, 2, 3)]
        staged_rows = [dict(row) for row in prior_rows[:2]]
        if changed_staged_index is not None:
            staged_rows[changed_staged_index]["head_branch"] = "account-1/changed-branch"

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._refuse_if_prior_rows_not_preserved(prior_rows, staged_rows)

        assert str(exc_info.value) == _drop_refusal(expected_ordinal)

    def test_writer_whose_formatter_drops_a_decimal_is_refused_with_the_ledger_unchanged(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(cache_read_usd=0.123457)])
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        real_format = pr_cost_ledger._format_pr_cost_ledger_row

        def format_with_five_decimal_cache_read(row, **kwargs):
            cells = real_format(row, **kwargs).split("\t")
            cells[_ledger_cell_index("cache_read_usd")] = f"{row['cache_read_usd']:.5f}"
            return "\t".join(cells)

        monkeypatch.setattr(pr_cost_ledger, "_format_pr_cost_ledger_row", format_with_five_decimal_cache_read)

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*prior_rows, _sample_pr_cost_row(pr_number=3)])

        assert str(exc_info.value) == _change_refusal(1, "cache_read_usd")
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)


class TestWritePrCostLedgerFileReturnsWhetherItUpgraded:
    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            *_OLDER_HEADER_PARAMS,
        ],
    )
    def test_returns_true_when_it_replaces_a_file_under_an_older_header(self, tmp_path, header_line, make_row_line):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text(header_line + "\n" + make_row_line() + "\n")
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        assert pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows) is True
        assert ledger_path.read_text().splitlines()[0] == pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE

    def test_returns_false_for_a_current_header_write_and_for_creation(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"

        assert pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()]) is False
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows) is False

    @pytest.mark.parametrize("mode", [0o600, 0o644])
    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            *_OLDER_HEADER_PARAMS,
        ],
    )
    def test_upgrade_preserves_the_files_mode(self, tmp_path, header_line, make_row_line, mode):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text(header_line + "\n" + make_row_line() + "\n")
        os.chmod(ledger_path, mode)
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

        assert stat.S_IMODE(ledger_path.stat().st_mode) == mode


class TestRecordUpgradeNotice:
    def test_two_branches_into_a_pre_model_ledger_print_the_notice_exactly_once(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        _enable_pr_cost(tmp_path)
        ledger_dir = tmp_path / _LEDGER_DIR_MARKER
        ledger_dir.mkdir()
        ledger_path = ledger_dir / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        ledger_path.write_text(
            _PRE_MODEL_HEADER_LINE + "\n" + _pre_model_row_line(head_branch=_PRIOR_ROW_MARKER) + "\n"
        )
        _write_jsonl(fake_projects / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000, branch="feature-a")])
        _write_jsonl(fake_projects / "sess-b.jsonl", [_priced("claude-sonnet-5", input=1_000, branch="feature-b")])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(
            merged_prs=[_merged_pr(1, "feature-a"), _merged_pr(2, "feature-b")],
        ))

        pr_cost._pr_cost_report(_pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        captured = capsys.readouterr()
        assert captured.err.count(_UPGRADE_NOTICE_FRAGMENT) == 1
        assert _upgrade_notice_line("the ledger") in captured.err
        assert len(pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())) == 3
        for marker in (_PRIOR_ROW_MARKER, _LEDGER_DIR_MARKER):
            assert marker not in captured.err
            assert marker not in captured.out

    def test_all_accounts_over_two_upgraded_ledgers_prints_the_notice_once_per_ledger_naming_each_account(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for ordinal_root, branch in zip(roots, ("feature-a", "feature-b"), strict=True):
            account_dir = ordinal_root.parent
            _enable_pr_cost(account_dir)
            project_dir = ordinal_root / "-home-user-testrepo"
            project_dir.mkdir(parents=True)
            _write_jsonl(project_dir / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, branch=branch)])
            (account_dir / "pr-cost-ledger.tsv").write_text(
                _PRE_MODEL_HEADER_LINE + "\n" + _pre_model_row_line(head_branch=_PRIOR_ROW_MARKER) + "\n"
            )
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(
            merged_prs=[_merged_pr(1, "feature-a"), _merged_pr(2, "feature-b")],
        ))

        pr_cost._pr_cost_report(
            _pr_cost_args(record=True, all_accounts=True), datetime(2026, 8, 10, tzinfo=UTC), roots,
        )

        captured = capsys.readouterr()
        assert captured.err.count(_upgrade_notice_line("account-1's ledger")) == 1
        assert captured.err.count(_upgrade_notice_line("account-2's ledger")) == 1
        assert captured.err.count(_UPGRADE_NOTICE_STEM) == 2
        assert _PRIOR_ROW_MARKER not in captured.err + captured.out

    def test_a_current_header_ledger_prints_no_notice(self, fake_projects, tmp_path, monkeypatch, capsys):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=50)])
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, branch="feature-a")])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=[_merged_pr(1, "feature-a")]))

        pr_cost._pr_cost_report(_pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert _UPGRADE_NOTICE_STEM not in capsys.readouterr().err

    def test_a_refused_write_prints_the_refusal_and_no_notice(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        _enable_pr_cost(tmp_path)
        ledger_dir = tmp_path / _LEDGER_DIR_MARKER
        ledger_dir.mkdir()
        ledger_path = ledger_dir / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        ledger_path.write_text(
            _PRE_MODEL_HEADER_LINE + "\n" + _pre_model_row_line(head_branch=_PRIOR_ROW_MARKER) + "\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, branch="feature-a")])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=[_merged_pr(1, "feature-a")]))

        def refuse_every_write(ledger_path_arg, rows):
            raise pr_cost_ledger._PrCostLedgerParseError("refusing to write the ledger (ledger unchanged): test double")

        monkeypatch.setattr(pr_cost_ledger, "_write_pr_cost_ledger_file", refuse_every_write)

        with pytest.raises(SystemExit) as exc_info:
            pr_cost._pr_cost_report(
                _pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent],
            )

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "refusing to write the ledger" in captured.err
        assert _UPGRADE_NOTICE_STEM not in captured.err
        for marker in (_PRIOR_ROW_MARKER, _LEDGER_DIR_MARKER):
            assert marker not in captured.err + captured.out


class TestLatestPrCostRowTieBreakLastWins:
    """The tie-break fix, exercised directly at _latest_pr_cost_row's own
    call site -- independent of pr-cost-export's suite, since the live
    --record/read path should not have its only coverage inside a new
    subcommand's tests."""

    def test_exact_captured_at_tie_resolves_to_the_last_appended_row(self):
        row_a = _sample_pr_cost_row(additions=1, captured_at="2026-01-01T00:00:00Z")
        row_b = _sample_pr_cost_row(additions=2, captured_at="2026-01-01T00:00:00Z")
        result = pr_cost_ledger._latest_pr_cost_row([row_a, row_b], "github.com", "owner/repo", 42, "ci1")
        assert result["additions"] == 2

    def test_distinct_captured_at_values_pick_the_chronologically_latest_row(self):
        """The ordinary (non-tie) case at this same call site: three
        candidates with distinct captured_at values, appended out of
        chronological order, must still resolve to the latest by
        captured_at rather than by append order."""
        row_a = _sample_pr_cost_row(additions=1, captured_at="2026-01-02T00:00:00Z")
        row_b = _sample_pr_cost_row(additions=2, captured_at="2026-01-03T00:00:00Z")
        row_c = _sample_pr_cost_row(additions=3, captured_at="2026-01-01T00:00:00Z")
        result = pr_cost_ledger._latest_pr_cost_row([row_a, row_b, row_c], "github.com", "owner/repo", 42, "ci1")
        assert result["additions"] == 2


class TestFormatPrCostLedgerRowColumnsParameterRegression:
    def test_default_columns_rendering_is_unchanged(self):
        """Golden literal captured from _format_pr_cost_ledger_row(_sample_pr_cost_row())
        with the default `columns`; pins that the default-columns rendering
        stays unaffected by the `columns` keyword-only parameter
        pr-cost-export also uses.
        Not derived from _PR_COST_LEDGER_HEADER_LINE plus a per-column type
        loop, which would re-implement the formatter's own branching inside
        the test."""
        golden = (
            "github.com\towner/repo\t42\tci1\taccount-1/branch-1\t2026-01-01T00:00:00Z\t2026-08-02"
            "\t2026-01-02T00:00:00Z\thigh\t\tok\t1.500000\t0.250000\t0.100000\t2.000000\t0.500000"
            "\t1000\t200\t100\t500\t300\t0\t0\t5\t2\t0.000000\t0.000000\t1500\t300.000000"
            "\t42\t10\t3\t4\t1\t2\t3\ttrue\ttrue\tfalse"
            "\t\t0\t"
        )
        assert pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()) == golden


class TestReviewRoundsColumnRegistration:
    def test_review_rounds_is_the_last_column_with_an_empty_default(self):
        assert pr_cost_ledger._PR_COST_LEDGER_COLUMNS[-1] == "review_rounds"
        assert pr_cost_ledger._PR_COST_LEDGER_COLUMN_DEFAULTS["review_rounds"] == ""

    def test_field_tuples_are_the_hand_written_literals(self):
        assert pr_cost_ledger._REVIEW_ROUND_COUNT_FIELDS == (
            "main_usd_micros", "agent_usd_micros", "agents", "dangling", "unpriced_turns", "unpriced_tokens",
            "invalid_usage_records",
        )
        assert pr_cost_ledger._REVIEW_ROUND_TOKEN_FIELDS == ("main_tokens", "agent_tokens")
        assert pr_cost_ledger._REVIEW_ROUND_FLAG_FIELDS == ("after_merge",)


class TestDecodeReviewRoundsCellAccepts:
    def test_canonical_sample_decodes_to_its_value_and_reformats_to_the_same_bytes(self):
        row = pr_cost_ledger._parse_pr_cost_ledger_file_text(
            _current_ledger_text_with_cell(_CANONICAL_SAMPLE_REVIEW_ROUNDS_CELL, "review_rounds")
        )[0]

        assert row["review_rounds"] == _sample_review_rounds()
        rendered_cell = pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[_ledger_cell_index("review_rounds")]
        assert rendered_cell == _CANONICAL_SAMPLE_REVIEW_ROUNDS_CELL

    def test_empty_cell_decodes_to_not_captured_and_all_empty_lists_to_captured_with_zero_rounds(self):
        zero_rounds = {"rule": 1, "rounds": {"code-review": [], "plan-review": [], "ready-for-review": []}}

        assert pr_cost_ledger._decode_review_rounds_cell("", 2) is None
        assert pr_cost_ledger._decode_review_rounds_cell(json.dumps(zero_rounds), 2) == zero_rounds

    def test_rule_and_no_skills_decodes(self):
        assert pr_cost_ledger._decode_review_rounds_cell('{"rule":1,"rounds":{}}', 2) == {"rule": 1, "rounds": {}}

    def test_empty_token_objects_decode(self):
        def empty_tokens(value):
            _first_round(value)["main_tokens"] = {}
            _first_round(value)["agent_tokens"] = {}

        cell = _mutated_review_rounds_cell(empty_tokens)
        assert pr_cost_ledger._decode_review_rounds_cell(cell, 2) == json.loads(cell)

    def test_vocabulary_this_checkout_has_never_seen_decodes_because_the_decoder_checks_structure_only(self):
        value = _sample_review_rounds()
        value["rule"] = 2**63 - 1
        value["rounds"]["future-review"] = [_sample_review_round()]
        _first_round(value)["main_tokens"] = {"claude-future-9": {"warp": {"input": 1, "cache_write_24h": 2}}}
        cell = json.dumps(value)

        assert pr_cost_ledger._decode_review_rounds_cell(cell, 2) == value

    def test_unknown_integer_round_key_survives_a_rewrite_and_the_postcondition(self, tmp_path):
        value = _sample_review_rounds()
        _first_round(value)["future-count"] = 7
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(review_rounds=value)])
        prior_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*prior_rows, _sample_pr_cost_row(pr_number=43)])

        rewritten_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert _first_round(rewritten_rows[0]["review_rounds"])["future-count"] == 7
        assert rewritten_rows[0] == prior_rows[0]

    def test_whitespace_and_indented_cell_decodes_to_the_same_value_and_reformats_canonically(self):
        indented_cell = json.dumps(_sample_review_rounds(), indent=2)
        assert "\n" in indented_cell
        # A raw newline would split the line; collapse it to spaces, which JSON also accepts between tokens.
        spaced_cell = indented_cell.replace("\n", " ")

        row = pr_cost_ledger._parse_pr_cost_ledger_file_text(_current_ledger_text_with_cell(spaced_cell, "review_rounds"))[0]

        assert row["review_rounds"] == _sample_review_rounds()
        rendered_cell = pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[_ledger_cell_index("review_rounds")]
        assert rendered_cell == _CANONICAL_SAMPLE_REVIEW_ROUNDS_CELL

    def test_raw_cell_exactly_at_the_read_bound_decodes(self, monkeypatch):
        at_bound_cell = json.dumps(_sample_review_rounds()) + " " * 100  # json.loads accepts trailing whitespace
        monkeypatch.setattr(pr_cost_ledger, "_REVIEW_ROUNDS_CELL_READ_MAX_CHARS", len(at_bound_cell))

        assert pr_cost_ledger._decode_review_rounds_cell(at_bound_cell, 2) == _sample_review_rounds()

    @pytest.mark.parametrize("key", ["a-b_c.d", "0abc", "a" * 64])
    def test_hyphen_underscore_and_dot_after_the_first_character_and_a_64_character_key_are_accepted(self, key):
        def renamed_model(value):
            tokens = _first_round(value)["main_tokens"]
            tokens[key] = tokens.pop("claude-sonnet-5")

        cell = _mutated_review_rounds_cell(renamed_model)
        assert pr_cost_ledger._decode_review_rounds_cell(cell, 2) == json.loads(cell)

    def test_integers_at_the_int64_maximum_are_accepted(self):
        def at_maximum(value):
            round_ = _first_round(value)
            round_["agents"] = 2**63 - 1
            round_["main_tokens"]["claude-sonnet-5"]["standard"]["input"] = 2**63 - 1

        cell = _mutated_review_rounds_cell(at_maximum)
        assert pr_cost_ledger._decode_review_rounds_cell(cell, 2) == json.loads(cell)


# Rule text per defect class, hand-written: a rename of any rule in production fails the table below.
_RULE_INVALID_JSON = "not valid JSON"
_RULE_DUPLICATE_KEY = "duplicate key"
_RULE_DEEP_NESTING = "nesting too deep"
_RULE_OVERSIZED = "cell too long"
_RULE_TOP_LEVEL = "top level must be an object with exactly rule and rounds"
_RULE_RULE_VALUE = "rule must be an integer from 0 to 2**63 - 1"
_RULE_KEY_SHAPE = "key outside the allowed key shape"
_RULE_ROUNDS_SHAPE = "rounds must be an object of lists"
_RULE_ROUND_SHAPE = "round must be an object"
_RULE_MISSING_FIELD = "round missing a required field"
_RULE_INTEGER_RANGE = "integer outside 0 to 2**63 - 1"
_RULE_AFTER_MERGE = "after_merge must be 0 or 1"
_RULE_TOKEN_OBJECT = "token object must nest non-empty objects"

_INTEGER_SENTINEL = "@@integer@@"  # swapped for a raw digit run after serialization


def _set_rule(value, new):
    value["rule"] = new


def _set_count_field(value, new):
    _first_round(value)["agents"] = new


def _set_token_leaf(value, new):
    _first_round(value)["main_tokens"]["claude-sonnet-5"]["standard"]["input"] = new


def _set_unknown_round_key(value, new):
    _first_round(value)["future-count"] = new


def _set_after_merge(value, new):
    _first_round(value)["after_merge"] = new


# position -> (setter, the rule a bad integer there raises)
_INTEGER_POSITIONS = {
    "rule": (_set_rule, _RULE_RULE_VALUE),
    "count-field": (_set_count_field, _RULE_INTEGER_RANGE),
    "token-leaf": (_set_token_leaf, _RULE_INTEGER_RANGE),
    "unknown-round-key": (_set_unknown_round_key, _RULE_INTEGER_RANGE),
    "after-merge": (_set_after_merge, _RULE_AFTER_MERGE),
}


def _rename_skill(value, new_key):
    value["rounds"][new_key] = value["rounds"].pop("plan-review")


def _add_unknown_round_key(value, new_key):
    _first_round(value)[new_key] = 1


def _rename_model(value, new_key):
    tokens = _first_round(value)["main_tokens"]
    tokens[new_key] = tokens.pop("claude-sonnet-5")


def _rename_variant(value, new_key):
    models = _first_round(value)["main_tokens"]["claude-sonnet-5"]
    models[new_key] = models.pop("standard")


def _rename_class(value, new_key):
    classes = _first_round(value)["main_tokens"]["claude-sonnet-5"]["standard"]
    classes[new_key] = classes.pop("input")


_KEY_POSITIONS = {
    "skill": _rename_skill,
    "unknown-round-key": _add_unknown_round_key,
    "model": _rename_model,
    "variant": _rename_variant,
    "class": _rename_class,
}


def _integer_position_cases() -> list:
    cases = []
    for position, (setter, rule) in _INTEGER_POSITIONS.items():
        bad_values = {"float": 1.5, "above-int64": 2**63}
        if position in ("rule", "after-merge"):
            # These two positions carry their own bool and lower-bound checks, apart from the shared leaf predicate.
            bad_values.update({"bool": True, "negative": -1})
        if position == "after-merge":
            bad_values["two"] = 2
        for label, bad_value in bad_values.items():
            cases.append(pytest.param(
                _mutated_review_rounds_cell(lambda value, s=setter, b=bad_value: s(value, b)), rule,
                id=f"{position}-{label}",
            ))
        raw_literal_cell = _mutated_review_rounds_cell(lambda value, s=setter: s(value, _INTEGER_SENTINEL))
        cases.append(pytest.param(
            raw_literal_cell.replace(f'"{_INTEGER_SENTINEL}"', "1" * 5000), _RULE_INVALID_JSON,
            id=f"{position}-5000-digit-literal",
        ))
    return cases


def _key_position_cases() -> list:
    return [
        pytest.param(
            _mutated_review_rounds_cell(lambda value, r=renamer, k=bad_key: r(value, k)), _RULE_KEY_SHAPE,
            id=f"{position}-{label}",
        )
        for position, renamer in _KEY_POSITIONS.items()
        for label, bad_key in (("empty", ""), ("uppercase", _CELL_MARKER.upper()), ("65-characters", "a" * 65))
    ]


def _primitive_cases() -> list:
    return [
        *[
            pytest.param(
                _mutated_review_rounds_cell(lambda value, b=bad_value: _set_count_field(value, b)), _RULE_INTEGER_RANGE,
                id=f"count-field-{label}",
            )
            for label, bad_value in _PRIMITIVE_INTEGER_VALUES.items()
        ],
        *[
            pytest.param(
                _mutated_review_rounds_cell(lambda value, k=bad_key: _rename_model(value, k)), _RULE_KEY_SHAPE,
                id=f"model-key-{label}",
            )
            for label, bad_key in _PRIMITIVE_KEYS.items()
        ],
    ]


def _deleted_field_cell(field: str) -> str:
    return _mutated_review_rounds_cell(lambda value: _first_round(value).pop(field))


def _structure_cases() -> list:
    sample_cell = json.dumps(_sample_review_rounds())
    return [
        pytest.param(_mutated_review_rounds_cell(lambda v: v.pop("rounds")), _RULE_TOP_LEVEL, id="missing-rounds"),
        pytest.param(_mutated_review_rounds_cell(lambda v: v.pop("rule")), _RULE_TOP_LEVEL, id="missing-rule"),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: v.update({_CELL_MARKER: 1})), _RULE_TOP_LEVEL, id="extra-top-level-key",
        ),
        pytest.param("null", _RULE_TOP_LEVEL, id="null"),
        pytest.param(json.dumps([_CELL_MARKER]), _RULE_TOP_LEVEL, id="top-level-list"),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: v.update({"rounds": [_CELL_MARKER]})), _RULE_ROUNDS_SHAPE,
            id="rounds-not-an-object",
        ),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: v["rounds"].update({"code-review": {_CELL_MARKER: 1}})),
            _RULE_ROUNDS_SHAPE, id="skill-value-not-a-list",
        ),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: v["rounds"].update({"code-review": [_CELL_MARKER]})),
            _RULE_ROUND_SHAPE, id="round-not-an-object",
        ),
        pytest.param(_deleted_field_cell("agents"), _RULE_MISSING_FIELD, id="missing-count-field"),
        pytest.param(_deleted_field_cell("agent_tokens"), _RULE_MISSING_FIELD, id="missing-token-field"),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: _first_round(v)["main_tokens"].update({_CELL_MARKER: {}})),
            _RULE_TOKEN_OBJECT, id="empty-model-object",
        ),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: _first_round(v)["main_tokens"]["claude-sonnet-5"].update({"fast": {}})),
            _RULE_TOKEN_OBJECT, id="empty-variant-object",
        ),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: _first_round(v).update({"main_tokens": [_CELL_MARKER]})),
            _RULE_TOKEN_OBJECT, id="token-object-is-a-list",
        ),
        pytest.param(
            _mutated_review_rounds_cell(lambda v: _first_round(v)["main_tokens"].update({"claude-sonnet-5": 5})),
            _RULE_TOKEN_OBJECT, id="model-value-is-a-number",
        ),
        pytest.param(
            _mutated_review_rounds_cell(
                lambda v: _first_round(v)["main_tokens"]["claude-sonnet-5"].update({"standard": _CELL_MARKER})
            ),
            _RULE_TOKEN_OBJECT, id="variant-value-is-a-string",
        ),
        pytest.param('{"rule":1,"rule":2,"rounds":{}}', _RULE_DUPLICATE_KEY, id="duplicate-top-level-key"),
        pytest.param(
            sample_cell.replace('"agents": 2', '"agents": 2, "agents": 3'), _RULE_DUPLICATE_KEY,
            id="duplicate-round-key",
        ),
        pytest.param(f"{_CELL_MARKER} not json", _RULE_INVALID_JSON, id="invalid-json"),
        pytest.param("   ", _RULE_INVALID_JSON, id="whitespace-only"),
    ]


_REVIEW_ROUNDS_REFUSAL_CASES = [
    *_integer_position_cases(), *_key_position_cases(), *_primitive_cases(), *_structure_cases(),
]
_OVERSIZED_CELL_BOUND = 4096  # patched in as the read bound for the oversized case


@pytest.fixture
def pinned_int_max_str_digits():
    """CPython's default 4300-digit int string-conversion limit makes json.loads raise ValueError for a
    5000-digit literal. The limit is pinned so a contributor's PYTHONINTMAXSTRDIGITS cannot change the outcome."""
    previous_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    yield
    sys.set_int_max_str_digits(previous_limit)


class TestDecodeReviewRoundsCellRefuses:
    @pytest.mark.parametrize("cell,expected_rule", _REVIEW_ROUNDS_REFUSAL_CASES)
    def test_malformed_cell_raises_the_rule_for_its_defect_naming_line_and_column_without_cell_text(
        self, pinned_int_max_str_digits, cell, expected_rule,
    ):
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(_current_ledger_text_with_cell(cell, "review_rounds"))

        assert str(exc_info.value) == f"line 2: malformed review_rounds ({expected_rule})"
        assert _CELL_MARKER not in str(exc_info.value).lower()
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_raw_cell_one_character_over_the_read_bound_is_refused(self, monkeypatch):
        monkeypatch.setattr(pr_cost_ledger, "_REVIEW_ROUNDS_CELL_READ_MAX_CHARS", _OVERSIZED_CELL_BOUND)
        over_bound_cell = json.dumps(_sample_review_rounds()).ljust(_OVERSIZED_CELL_BOUND + 1)
        at_bound_cell = over_bound_cell[:-1]
        assert pr_cost_ledger._decode_review_rounds_cell(at_bound_cell, 2) == _sample_review_rounds()

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._decode_review_rounds_cell(over_bound_cell, 2)

        assert str(exc_info.value) == f"line 2: malformed review_rounds ({_RULE_OVERSIZED})"
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_oversized_cell_is_refused_before_it_is_parsed(self, monkeypatch):
        monkeypatch.setattr(pr_cost_ledger, "_REVIEW_ROUNDS_CELL_READ_MAX_CHARS", 8)
        monkeypatch.setattr(pr_cost_ledger.json, "loads", lambda *a, **k: pytest.fail("parsed an oversized cell"))

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="cell too long"):
            pr_cost_ledger._decode_review_rounds_cell("x" * 9, 2)

    def test_recursion_error_from_the_json_parser_maps_to_the_nesting_rule_with_no_chained_exception(self, monkeypatch):
        """Injected, because whether json.loads raises RecursionError on deep input depends on the interpreter."""
        def recursing_loads(*args, **kwargs):
            raise RecursionError

        monkeypatch.setattr(pr_cost_ledger.json, "loads", recursing_loads)

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._decode_review_rounds_cell("{}", 2)

        assert str(exc_info.value) == f"line 2: malformed review_rounds ({_RULE_DEEP_NESTING})"
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_message_format_is_line_then_fixed_rule(self):
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._decode_review_rounds_cell("not json", 5)
        assert str(exc_info.value) == "line 5: malformed review_rounds (not valid JSON)"

    def test_refusal_table_covers_every_decode_rule_except_the_two_with_their_own_tests(self):
        """Oversized and deep nesting cannot be reached from a cell alone: the first needs a patched bound, the second
        an injected RecursionError. The tests above pin each of those two rules' text."""
        table_rules = {case.values[1] for case in _REVIEW_ROUNDS_REFUSAL_CASES}
        rules_with_their_own_tests = {_RULE_OVERSIZED, _RULE_DEEP_NESTING}

        assert table_rules == set(pr_cost_ledger._REVIEW_ROUNDS_DECODE_RULES) - rules_with_their_own_tests
        assert len(pr_cost_ledger._REVIEW_ROUNDS_DECODE_RULES) == len(set(pr_cost_ledger._REVIEW_ROUNDS_DECODE_RULES))

    def test_a_blank_review_rounds_cell_with_its_trailing_tab_stripped_is_refused_with_the_append_a_tab_hint(self):
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row())
        assert line.endswith("\t")
        text = pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line[:-1] + "\n"

        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

        assert str(exc_info.value).endswith(_APPEND_TAB_HINT)


class TestReviewRoundsCellBounds:
    def test_read_bound_equals_its_hand_written_literal(self):
        assert pr_cost_ledger._REVIEW_ROUNDS_CELL_READ_MAX_CHARS == 8_388_608, (
            "persisted contract: a checkout refuses any cell longer than the value it shipped with; never lower it"
        )

    def test_write_bound_does_not_exceed_the_read_bound(self):
        assert pr_cost_ledger._REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS <= pr_cost_ledger._REVIEW_ROUNDS_CELL_READ_MAX_CHARS

    def test_a_cell_longer_than_the_write_bound_still_decodes_and_the_write_check_refuses_it_with_size(self):
        rounds_over_the_write_bound = (
            pr_cost_ledger._REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS // len(pr_cost_ledger._encode_json_cell(_sample_review_round()))
            + 2
        )
        value = _sample_review_rounds()
        value["rounds"]["code-review"] = [_sample_review_round() for _ in range(rounds_over_the_write_bound)]
        cell = pr_cost_ledger._encode_json_cell(value)
        assert pr_cost_ledger._REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS < len(cell) <= pr_cost_ledger._REVIEW_ROUNDS_CELL_READ_MAX_CHARS

        assert pr_cost_ledger._decode_review_rounds_cell(cell, 2) == value
        with pytest.raises(pr_cost._ReviewRoundsCheckError) as exc_info:
            pr_cost._check_review_rounds_cell(value)
        assert exc_info.value.rule == "size"


class TestFormatReviewRoundsCell:
    def test_populated_cell_renders_to_its_canonical_bytes(self):
        row = _sample_pr_cost_row(review_rounds=_sample_review_rounds())
        rendered_cell = pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[_ledger_cell_index("review_rounds")]
        assert rendered_cell == _CANONICAL_SAMPLE_REVIEW_ROUNDS_CELL

    def test_not_captured_cell_renders_empty_as_the_lines_last_character(self):
        assert pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).endswith("\t0\t")

    def test_all_empty_lists_round_trip_distinct_from_not_captured(self):
        zero_rounds = {"rule": 1, "rounds": {"code-review": [], "plan-review": [], "ready-for-review": []}}
        row = _sample_pr_cost_row(review_rounds=zero_rounds)

        parsed_row = pr_cost_ledger._parse_pr_cost_ledger_row_cells(
            pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t"), 2,
        )

        assert parsed_row["review_rounds"] == zero_rounds
        assert parsed_row == row

    def test_populated_row_round_trips(self):
        row = _sample_pr_cost_row(model_breakdown=_sample_model_breakdown(), review_rounds=_sample_review_rounds())
        line = pr_cost_ledger._format_pr_cost_ledger_row(row)
        assert pr_cost_ledger._parse_pr_cost_ledger_row_cells(line.split("\t"), 2) == row

    @pytest.mark.parametrize("review_rounds", [_sample_review_rounds(), None], ids=["populated", "empty-last-cell"])
    def test_csv_reader_and_split_yield_the_same_cells(self, review_rounds):
        line = pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(review_rounds=review_rounds))
        assert next(csv.reader([line], delimiter="\t")) == line.split("\t")

    def test_each_json_column_names_itself_in_its_decode_error(self):
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="malformed model_breakdown"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(_current_ledger_text_with_cell("not json", "model_breakdown"))
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError, match="malformed review_rounds"):
            pr_cost_ledger._parse_pr_cost_ledger_file_text(_current_ledger_text_with_cell("not json", "review_rounds"))
