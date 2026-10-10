"""Tests for transcript_analysis/pr_cost_ledger.py (the pr-cost ledger's on-disk format,
canonical parser and formatter, append-only upsert, crash-safe write, and the --record lock)."""
import csv
import errno
import fcntl
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ._pr_cost_helpers import (
    _FROZEN_PR_COST_HEADER_LINES,
    _PRE_HOST_HEADER_LINE,
    _PRE_HOST_ROW_LINE,
    _PRE_HOST_ROW_PARSED,
    _PRE_MODEL_HEADER_LINE,
    _PRE_MODEL_ROW_LINE,
    _PRE_MODEL_ROW_PARSED,
    _enable_pr_cost,
    _fake_pr_cost_subprocess_run,
    _legacy_row_line,
    _make_mkstemp_create_0644,
    _pr_cost_args,
    _pre_model_row_line,
    _sample_model_breakdown,
    _sample_pr_cost_row,
)
from .conftest import _priced, _two_declared_roots, _write_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


class TestAppendPrCostLedgerRow:
    def test_duplicate_key_without_force_raises(self):
        existing_row = _sample_pr_cost_row()
        with pytest.raises(ValueError, match="already exists"):
            _mod.pr_cost_ledger._append_pr_cost_ledger_row(
                [existing_row], _sample_pr_cost_row(), already=existing_row, force=False,
            )

    def test_duplicate_key_with_force_appends_superseding_row_byte_identical_prior_rows(self):
        existing_row = _sample_pr_cost_row(captured_at="2026-01-01T00:00:00Z")
        existing_rows = [existing_row]
        new_row = _sample_pr_cost_row(captured_at="2026-01-02T00:00:00Z", supersedes="2026-01-01T00:00:00Z")

        result = _mod.pr_cost_ledger._append_pr_cost_ledger_row(existing_rows, new_row, already=existing_row, force=True)

        assert result == [existing_row, new_row]
        assert (
            _mod.pr_cost_ledger._format_pr_cost_ledger_row(result[0])
            == _mod.pr_cost_ledger._format_pr_cost_ledger_row(existing_row)
        )

    def test_duplicate_key_without_force_error_omits_raw_repo_value(self):
        existing_row = _sample_pr_cost_row(repo="acme-corp/internal-project")
        with pytest.raises(ValueError) as exc_info:
            _mod.pr_cost_ledger._append_pr_cost_ledger_row(
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
            _mod.pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
            try:
                _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [row1])
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

        row2 = _sample_pr_cost_row(pr_number=2, captured_at="2026-01-01T00:00:00Z")
        with open(lock_path, "w") as lock_f:
            _mod.pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
            try:
                current_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
                _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*current_rows, row2])
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

        final_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
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
                _mod.pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
                try:
                    try:
                        current_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
                    except FileNotFoundError:
                        current_rows = []
                    row = _sample_pr_cost_row(pr_number=pr_number, captured_at="2026-01-01T00:00:00Z")
                    _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*current_rows, row])
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert sorted(r["pr_number"] for r in final_rows) == list(range(thread_count))


class TestPrCostLedgerRowFormatRoundTrip:
    def test_format_then_parse_round_trip_is_lossless(self):
        row = _sample_pr_cost_row()
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(row)
        parsed = _mod.pr_cost_ledger._parse_pr_cost_ledger_row_cells(line.split("\t"), line_no=2)
        assert parsed == row


class TestParsePrCostLedgerFileTextMalformed:
    def _valid_line(self) -> str:
        return _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row())

    def test_wrong_column_count_raises(self):
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "owner/repo\t1\tci1\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="expected .* columns"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_non_numeric_pr_number_raises(self):
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(pr_number="not-a-number"))
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="non-numeric pr_number"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_unknown_join_confidence_raises(self):
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(join_confidence="extreme"))
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="unknown join_confidence"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_unknown_status_raises(self):
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(status="degraded_mystery"))
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="unknown status"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_malformed_repo_raises_without_leaking_raw_value(self):
        """A non-lowercase repo value fails the malformed-repo check, and --
        mirroring _append_pr_cost_ledger_row's duplicate-key error -- the
        raised message omits the raw value, since the ledger's repo column
        is never scrubbed at rest."""
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(repo="Acme-Corp/Internal-Project"))
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="malformed repo value") as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert "Acme-Corp" not in str(exc_info.value)

    def test_malformed_host_raises_without_leaking_raw_value(self):
        """Same guard as the repo check above, mirrored for the host column --
        a non-lowercase host fails the malformed-host check without the raw
        value reaching the raised message, since host is never scrubbed at
        rest either."""
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(host="Acme-Corp.GHE.com"))
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="malformed host value") as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert "Acme-Corp" not in str(exc_info.value)

    def test_malformed_merged_at_raises(self):
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(merged_at="not-a-timestamp"))
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="malformed merged_at"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_malformed_captured_at_raises(self):
        """Guards _latest_pr_cost_row's lexicographic string max() on
        captured_at, which would silently misresolve given a malformed value."""
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(captured_at="2026/01/01"))
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="malformed captured_at"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def _line_with_malformed_cell(self, column: str, malformed_value: str) -> str:
        """A valid formatted row line with `column`'s own cell replaced by
        malformed_value. This bypasses _format_pr_cost_ledger_row's own
        bool/float rendering, which would otherwise reject an arbitrary
        string before parsing is ever reached for those columns."""
        cells = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
        cells[_mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS.index(column)] = malformed_value
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
            _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n"
            + self._line_with_malformed_cell(column, malformed_value) + "\n"
        )
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        message = str(exc_info.value)
        assert malformed_value not in message
        assert column in message
        assert "line 2" in message

    def test_merge_conflict_marker_raises(self):
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + self._valid_line() + "\n<<<<<<< HEAD\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="merge-conflict marker"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_unrecognized_first_line_is_named_without_echo_or_the_newer_version_hint(self):
        text = "not-the-header\n" + self._valid_line() + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert str(exc_info.value) == "missing or mismatched pr-cost ledger header row (unrecognized first line)"

    def test_empty_file_is_named_without_the_newer_version_hint(self):
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text("")
        assert str(exc_info.value) == "missing or mismatched pr-cost ledger header row (the file is empty)"

    def test_current_header_with_a_trailing_space_gets_no_newer_version_hint(self):
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + " \n" + self._valid_line() + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
        assert "update this checkout" not in str(exc_info.value)

    def test_mismatched_header_raises(self):
        # Dropping a middle column (not the last) keeps this header distinct from every recognized frozen header.
        columns = _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS
        bad_header = "\t".join([*columns[:5], *columns[6:]])
        text = bad_header + "\n" + self._valid_line() + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="missing or mismatched"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)


class TestPrCostLedgerLegacyHostColumnMigration:
    """The pre-host-column header (_PR_COST_LEDGER_LEGACY_HEADER_LINE) is
    a recognized older header -- every row recorded under it predates
    GHE host-awareness, so implicitly belongs to github.com."""

    def test_legacy_header_row_parses_with_host_defaulted_to_github_com(self):
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE + "\n" + _legacy_row_line() + "\n"

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

        assert len(rows) == 1
        assert rows[0]["host"] == "github.com"

    @pytest.mark.parametrize(
        "header_line,make_row_line,expect_host_prefix",
        [
            pytest.param(_PRE_HOST_HEADER_LINE, _legacy_row_line, True, id="pre-host"),
            pytest.param(_PRE_MODEL_HEADER_LINE, _pre_model_row_line, False, id="pre-model"),
        ],
    )
    def test_record_against_older_header_file_rewrites_every_prior_row_under_the_current_header(
        self, fake_projects, tmp_path, monkeypatch, header_line, make_row_line, expect_host_prefix,
    ):
        """A subsequent --record write against an older-header file rewrites the whole ledger under the
        current header -- no separate migration script needed. Each prior row keeps its original cells in
        its original order, gaining only the cells its header lacked (host, then an empty model_breakdown)."""
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
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        written_lines = ledger_path.read_text().splitlines()
        assert written_lines[0] == _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE
        host_prefix = "github.com\t" if expect_host_prefix else ""
        assert written_lines[1:1 + len(seeded_lines)] == [host_prefix + line + "\t" for line in seeded_lines]
        assert len(written_lines) == 1 + len(seeded_lines) + 1
        new_row = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())[-1]
        assert new_row["pr_number"] == 1
        assert new_row["model_breakdown"], "the check below returns early on a None cell"
        _mod.pr_cost._check_model_breakdown_cell(new_row["model_breakdown"], new_row)  # must not raise


class TestPrCostLedgerFrozenHeaders:
    def test_production_older_headers_equal_the_hand_written_frozen_lines(self):
        pre_host_line, pre_model_line = _FROZEN_PR_COST_HEADER_LINES
        assert pre_host_line == _mod.pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE, (
            "the pre-host header is frozen; never re-derive from the live tuple"
        )
        assert "\t".join(_mod.pr_cost_ledger._PR_COST_LEDGER_PRE_MODEL_COLUMNS) == pre_model_line, (
            "the pre-model header is frozen; never re-derive from the live tuple"
        )

    def test_current_header_is_the_newest_frozen_header_plus_model_breakdown(self):
        assert _FROZEN_PR_COST_HEADER_LINES[-1] + "\tmodel_breakdown" == _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE, (
            "the newest frozen header is frozen; never re-derive from the live tuple. The next column addition"
            " appends the outgoing header to _FROZEN_PR_COST_HEADER_LINES"
        )

    def test_every_recognized_header_is_in_the_header_table(self):
        table = _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE
        assert set(table) == {*_FROZEN_PR_COST_HEADER_LINES, _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE}

    @pytest.mark.parametrize("header_line", [*_FROZEN_PR_COST_HEADER_LINES])
    def test_every_current_column_missing_from_a_recognized_header_has_a_default(self, header_line):
        header_columns = set(header_line.split("\t"))
        missing = [c for c in _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS if c not in header_columns]
        assert missing, "a frozen header must lack at least one current column"
        for column in missing:
            assert column in _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMN_DEFAULTS, column

    def test_every_column_of_every_recognized_header_exists_in_the_current_column_tuple(self):
        """A column dropped from the current tuple would be silently discarded when an older-header row is rewritten."""
        for header_line, columns in _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE.items():
            retired = [c for c in columns if c not in _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS]
            assert retired == [], header_line

    @pytest.mark.parametrize(
        "header_line,row_line,expected_row",
        [
            pytest.param(_PRE_HOST_HEADER_LINE, _PRE_HOST_ROW_LINE, _PRE_HOST_ROW_PARSED, id="pre-host"),
            pytest.param(_PRE_MODEL_HEADER_LINE, _PRE_MODEL_ROW_LINE, _PRE_MODEL_ROW_PARSED, id="pre-model"),
        ],
    )
    def test_literal_row_parses_to_every_column_by_name_with_defaults_filled(self, header_line, row_line, expected_row):
        parsed_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(header_line + "\n" + row_line + "\n")

        assert parsed_rows == [expected_row]

    @pytest.mark.parametrize(
        "header_line,row_line,expected_row,expected_line",
        [
            pytest.param(
                _PRE_HOST_HEADER_LINE, _PRE_HOST_ROW_LINE, _PRE_HOST_ROW_PARSED, "github.com\t" + _PRE_HOST_ROW_LINE + "\t",
                id="pre-host",
            ),
            pytest.param(
                _PRE_MODEL_HEADER_LINE, _PRE_MODEL_ROW_LINE, _PRE_MODEL_ROW_PARSED, _PRE_MODEL_ROW_LINE + "\t",
                id="pre-model",
            ),
        ],
    )
    def test_literal_row_survives_the_real_upgrading_write_as_its_original_cells_plus_the_defaulted_ones(
        self, tmp_path, header_line, row_line, expected_row, expected_line,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text(header_line + "\n" + row_line + "\n")
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

        rewritten_lines = ledger_path.read_text().splitlines()
        assert rewritten_lines == [_mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE, expected_line]
        assert _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text()) == [expected_row]

    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            pytest.param(_PRE_HOST_HEADER_LINE, _legacy_row_line, id="pre-host"),
            pytest.param(_PRE_MODEL_HEADER_LINE, _pre_model_row_line, id="pre-model"),
            pytest.param(
                _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE,
                lambda: _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()),
                id="current",
            ),
        ],
    )
    def test_row_width_is_checked_against_its_own_files_header(self, header_line, make_row_line):
        width = len(header_line.split("\t"))
        valid_cells = make_row_line().split("\t")
        assert len(valid_cells) == width, "fixture row must match its header's width"
        wider_line = "\t".join([*valid_cells, "extra"])
        shorter_line = "\t".join(valid_cells[:-1])

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as wider_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(header_line + "\n" + wider_line + "\n")
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as shorter_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(header_line + "\n" + shorter_line + "\n")

        assert str(wider_info.value) == f"line 2: expected {width} columns, got {width + 1}"
        trim_suffix = " (if an editor trimmed trailing whitespace, append a tab to this line)"
        if header_line == _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE:
            assert str(shorter_info.value) == f"line 2: expected {width} columns, got {width - 1}{trim_suffix}"
        else:
            assert str(shorter_info.value) == f"line 2: expected {width} columns, got {width - 1}"

    def test_header_equal_to_current_plus_an_extra_column_names_the_newer_version_remedy(self):
        header_line = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\tnewer_column"
        text = header_line + "\n" + "x\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="missing or mismatched") as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)
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


def _current_ledger_text_with_cell(cell: str) -> str:
    cells = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
    cells[_mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS.index("model_breakdown")] = cell
    return _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "\t".join(cells) + "\n"


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
    pytest.param(_single_group_cell(model=_CELL_MARKER + "\u00e9"), id="non-ascii-model-key-escaped"),
    pytest.param(_single_group_cell(model="a" * 65), id="65-character-key"),
    *[
        pytest.param(_single_group_cell(**{position: bad_key}), id=f"{label}-{position.removeprefix('token_')}-key")
        for position in ("model", "variant", "token_class")
        for label, bad_key in (
            ("leading-dot", f".{_CELL_MARKER}"),
            ("leading-hyphen", f"-{_CELL_MARKER}"),
            ("leading-underscore", f"_{_CELL_MARKER}"),
            ("slash", f"{_CELL_MARKER}/x"),
            ("colon", f"{_CELL_MARKER}:x"),
            ("space", f"{_CELL_MARKER} x"),
        )
    ],
    pytest.param(_single_group_cell(leaf={"tokens": 1}), id="leaf-missing-usd-micros"),
    pytest.param(_single_group_cell(leaf={"usd_micros": 1}), id="leaf-missing-tokens"),
    pytest.param(_single_group_cell(leaf={"tokens": 1, "usd_micros": 1, _CELL_MARKER: 2}), id="leaf-extra-key"),
    *[
        pytest.param(_single_group_cell(leaf={leaf_key: bad_value, other_key: 1}), id=f"{leaf_key}-{label}")
        for leaf_key, other_key in (("tokens", "usd_micros"), ("usd_micros", "tokens"))
        for label, bad_value in (
            ("float", 1.5), ("bool", True), ("negative", -1), ("nan", float("nan")), ("infinity", float("inf")),
            ("above-int64", 2**63),
        )
    ],
]


class TestDecodeModelBreakdownCell:
    @pytest.mark.parametrize("cell", _CODEC_REJECT_CASES)
    def test_malformed_cell_raises_naming_line_and_column_without_echoing_cell_text(self, cell):
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(_current_ledger_text_with_cell(cell))

        message = str(exc_info.value)
        assert "line 2" in message
        assert "model_breakdown" in message
        assert _CELL_MARKER not in message.lower()
        for linked_exception in (exc_info.value.__cause__, exc_info.value.__context__):
            assert linked_exception is None or _CELL_MARKER not in str(linked_exception).lower()

    def test_message_format_is_line_then_fixed_rule(self):
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._decode_model_breakdown_cell("not json", 5)
        assert str(exc_info.value) == "line 5: malformed model_breakdown (not valid JSON)"

    def test_deeply_nested_input_maps_recursion_error_to_a_parse_error(self):
        deeply_nested_cell = '{"a":' * 100000

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="malformed model_breakdown") as exc_info:
            _mod.pr_cost_ledger._decode_model_breakdown_cell(deeply_nested_cell, 2)

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
            with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="malformed model_breakdown") as exc_info:
                _mod.pr_cost_ledger._decode_model_breakdown_cell(cell, 2)
        finally:
            sys.set_int_max_str_digits(previous_limit)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_leaf_integers_at_the_int64_maximum_are_accepted(self):
        cell = _single_group_cell(leaf={"tokens": 2**63 - 1, "usd_micros": 2**63 - 1})
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_empty_cell_decodes_to_not_recorded_and_empty_object_to_recorded_nothing(self):
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell("", 2) is None
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell("{}", 2) == {}

    def test_canonical_populated_cell_decodes_to_its_value(self):
        decoded = _mod.pr_cost_ledger._decode_model_breakdown_cell(_CANONICAL_SAMPLE_BREAKDOWN_CELL, 2)
        assert decoded == _sample_model_breakdown()

    def test_whitespace_and_unsorted_variant_decodes_to_the_same_value_and_reformats_canonically(self):
        spaced_cell = json.dumps(_sample_model_breakdown(), separators=(" , ", " : "))  # insertion order, not sorted
        unsorted_cell = '{ "claude-sonnet-5" : { "standard" : { "output": {"usd_micros": 2000000, "tokens": 500},' \
            ' "input": {"usd_micros": 500000, "tokens": 300}, "cache_write_5m": {"usd_micros": 250000, "tokens": 200},' \
            ' "cache_write_1h": {"usd_micros": 100000, "tokens": 100}, "cache_read": {"usd_micros": 1500000,' \
            ' "tokens": 1000} } } }'
        for cell in (spaced_cell, unsorted_cell):
            text = _current_ledger_text_with_cell(cell)

            row = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)[0]

            assert row["model_breakdown"] == _sample_model_breakdown()
            rendered_cell = _mod.pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[-1]
            assert rendered_cell == _CANONICAL_SAMPLE_BREAKDOWN_CELL

    def test_unknown_but_well_formed_variant_and_class_labels_are_accepted(self):
        cell = _single_group_cell(variant="ultra_fast", token_class="cache_write_24h")
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_group_missing_a_class_is_accepted(self):
        cell = _single_group_cell(token_class="output")
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_retired_synthetic_model_key_is_accepted(self):
        cell = _single_group_cell(model="claude-test-retired")
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_64_character_key_is_accepted(self):
        cell = _single_group_cell(model="a" * 64)
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    @pytest.mark.parametrize("model_id", sorted(_mod.pricing._MODEL_BASE_INPUT_RATES))
    def test_every_priced_model_id_satisfies_the_key_rule(self, model_id):
        cell = _single_group_cell(model=model_id)
        assert _mod.pr_cost_ledger._decode_model_breakdown_cell(cell, 2) == json.loads(cell)

    def test_pricing_variant_labels_equal_the_hand_written_literal(self):
        assert _mod.pricing._PRICING_VARIANTS == ("standard", "fast", "us_geo", "fast_us_geo"), (
            "labels persist in ledger rows: rename or remove none, and add a new one to this literal"
        )

    def test_token_class_labels_equal_the_hand_written_literal(self):
        assert _mod.pricing._TOKEN_CLASSES == ("cache_read", "cache_write_5m", "cache_write_1h", "output", "input"), (
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

        row = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(header_line + "\n" + row_line + "\n")[0]

        assert row["model_breakdown"]["claude-test-retired"]["ultra_fast"]["cache_write_24h"] == {"tokens": 3, "usd_micros": 4}
        assert row["model_breakdown"]["claude-sonnet-5"]["standard"]["input"] == {"tokens": 7, "usd_micros": 8}


class TestFormatModelBreakdownCell:
    def test_populated_cell_renders_to_its_canonical_bytes(self):
        """Pins one cell's canonical encoding at cell level, not as a whole row; every other test compares
        cells via json.loads."""
        row = _sample_pr_cost_row(model_breakdown=_sample_model_breakdown())
        rendered_cell = _mod.pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[-1]
        assert rendered_cell == _CANONICAL_SAMPLE_BREAKDOWN_CELL

    def test_not_recorded_cell_renders_empty(self):
        rendered_line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row())
        assert rendered_line.endswith("\tfalse\t")

    def test_empty_object_cell_renders_as_two_characters_and_round_trips_distinct_from_not_recorded(self):
        row = _sample_pr_cost_row(model_breakdown={})

        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(row)
        parsed_row = _mod.pr_cost_ledger._parse_pr_cost_ledger_row_cells(line.split("\t"), 2)

        assert line.split("\t")[-1] == "{}"
        assert parsed_row["model_breakdown"] == {}
        assert parsed_row["model_breakdown"] is not None
        assert parsed_row == row

    def test_empty_object_and_not_recorded_cells_survive_a_file_write_as_different_cells(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        rows = [_sample_pr_cost_row(pr_number=1, model_breakdown={}), _sample_pr_cost_row(pr_number=2)]

        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows)

        data_lines = ledger_path.read_text().splitlines()[1:]
        assert [line.split("\t")[-1] for line in data_lines] == ["{}", ""]
        parsed_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert [r["model_breakdown"] for r in parsed_rows] == [{}, None]

    def test_two_argument_row_parser_round_trips_a_populated_row(self):
        row = _sample_pr_cost_row(model_breakdown=_sample_model_breakdown())
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(row)
        assert _mod.pr_cost_ledger._parse_pr_cost_ledger_row_cells(line.split("\t"), 2) == row

    def test_pre_model_line_formats_as_its_original_cells_plus_one_empty_cell(self):
        overrides = {"pr_number": 5, "additions": 99}
        assert _pre_model_row_line(**overrides) + "\t" == _mod.pr_cost_ledger._format_pr_cost_ledger_row(
            _sample_pr_cost_row(**overrides)
        )

    @pytest.mark.parametrize("model_breakdown", [_sample_model_breakdown(), None], ids=["populated", "empty-last-cell"])
    def test_csv_reader_and_split_yield_the_same_cells(self, model_breakdown):
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(model_breakdown=model_breakdown))
        assert next(csv.reader([line], delimiter="\t")) == line.split("\t")


class TestWritePrCostLedgerFileMode:
    def test_fresh_file_created_with_0600_even_when_mkstemp_creates_0644(self, tmp_path, monkeypatch):
        _make_mkstemp_create_0644(monkeypatch)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [])
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o600

    def test_existing_file_mode_preserved_across_write(self, tmp_path):
        """A user-loosened mode on an existing ledger file is preserved, not
        silently reset back to 0600 on a later write."""
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [])
        os.chmod(ledger_path, 0o644)
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])
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
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="write verification mismatch"):
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [])
        assert not ledger_path.exists()
        assert list(tmp_path.glob(".pr-cost-ledger-*.tmp")) == []

    def test_replace_failure_propagates_leaving_the_ledger_unchanged_and_no_temp_file(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=1)])
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        def replace_failing(source, destination):
            raise OSError(errno.EIO, "replace failed")

        monkeypatch.setattr(os, "replace", replace_failing)

        with pytest.raises(OSError, match="replace failed"):
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
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

        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])

        assert events == [("fsync", ledger_path.stat().st_size), ("replace", 0)]

    def test_fsync_failure_propagates_leaving_the_ledger_unchanged_and_no_temp_file(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=1)])
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        def fsync_failing(fd):
            raise OSError(errno.EIO, "fsync failed")

        monkeypatch.setattr(os, "fsync", fsync_failing)

        with pytest.raises(OSError, match="fsync failed"):
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
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
    return _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS.index(column)


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
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows)
        return _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

    def test_encoder_that_drops_a_value_is_refused_naming_row_and_column_with_ledger_unchanged(
        self, tmp_path, monkeypatch,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        monkeypatch.setattr(_mod.pr_cost_ledger, "_encode_model_breakdown_cell", lambda value: "{}")

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*prior_rows, _sample_pr_cost_row(pr_number=3)])

        assert str(exc_info.value) == _change_refusal(1, "model_breakdown")
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_call_that_drops_a_prior_row_is_refused_with_ledger_unchanged(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows[:1])

        assert str(exc_info.value) == _drop_refusal(2)
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_call_that_reorders_prior_rows_is_refused_with_ledger_unchanged(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [prior_rows[1], prior_rows[0]])

        assert str(exc_info.value) == _change_refusal(1, "pr_number")
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_hand_edited_cells_that_re_render_at_ledger_precision_pass_and_re_render(self, tmp_path):
        """A hand-edited 1.5, a reformatted JSON cell, a seven-decimal 0.1234567, and -1e-7 are all values
        the parser accepts, and the write re-renders them at the ledger's six decimals / canonical encoding
        instead of refusing."""
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        cells = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
        cells[_ledger_cell_index("cache_read_usd")] = "1.5"
        cells[_ledger_cell_index("opus_dollars")] = "0.1234567"
        cells[_ledger_cell_index("mean_context_at_turn")] = "-1e-7"
        cells[_ledger_cell_index("model_breakdown")] = json.dumps(_sample_model_breakdown(), separators=(" , ", " : "))
        ledger_path.write_text(
            _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "\t".join(cells) + "\n"
        )
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

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
        cells = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()).split("\t")
        for column, edited_cell in edited_float_cells.items():
            cells[_ledger_cell_index(column)] = edited_cell
        ledger_path.write_text(
            _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + "\t".join(cells) + "\n"
        )
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

        rewritten_cells = ledger_path.read_text().splitlines()[1].split("\t")
        for column, edited_cell in edited_float_cells.items():
            assert rewritten_cells[_ledger_cell_index(column)] == f"{float(edited_cell):.6f}", column

    def test_malformed_existing_file_is_refused_with_the_changed_during_this_run_wording(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text("not-the-header\nsome\tbad\trow\n")
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])

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
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=9)])

        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)

    def test_structurally_invalid_cell_is_refused_with_staged_rewrite_wording_and_ledger_unchanged(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        prior_rows = self._seed_two_rows(ledger_path)
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        invalid_cell = {_CELL_MARKER.upper(): {"standard": {"input": {"tokens": 1, "usd_micros": 1}}}}

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(
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

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="staged rewrite failed validation"):
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(
                ledger_path, [_sample_pr_cost_row(model_breakdown=invalid_cell)],
            )

        assert not ledger_path.exists()
        assert list(tmp_path.glob(".pr-cost-ledger-*.tmp")) == []


class TestWritePrCostLedgerFilePostconditionOnTheUpgradingWrite:
    """The first rewrite of an older-header file is the sole copy of every prior row once transcripts age out,
    so the value postcondition has to refuse there too."""

    @staticmethod
    def _seed_two_rows_under_an_older_header(ledger_path: Path, header_line: str, make_row_line) -> list[dict]:
        seeded_lines = [make_row_line(pr_number=1, additions=71), make_row_line(pr_number=2, additions=77)]
        ledger_path.write_text(header_line + "\n" + "\n".join(seeded_lines) + "\n")
        return _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            pytest.param(_PRE_HOST_HEADER_LINE, _legacy_row_line, id="pre-host"),
            pytest.param(_PRE_MODEL_HEADER_LINE, _pre_model_row_line, id="pre-model"),
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
            real_format = _mod.pr_cost_ledger._format_pr_cost_ledger_row

            def format_adding_one_to_additions(row, **kwargs):
                return real_format({**row, "additions": row["additions"] + 1}, **kwargs)

            monkeypatch.setattr(_mod.pr_cost_ledger, "_format_pr_cost_ledger_row", format_adding_one_to_additions)

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows_to_write)

        assert str(exc_info.value) == expected_refusal
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)


def _current_header_row_line(**overrides) -> str:
    return _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(**overrides))


class TestWritePrCostLedgerFilePostconditionCoversEveryPriorRow:
    """The postcondition compares all prior rows, not only the first ones: a defect confined to the third of
    three prior rows must be refused, naming ordinal 3."""

    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            pytest.param(_mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE, _current_header_row_line, id="current"),
            pytest.param(_PRE_HOST_HEADER_LINE, _legacy_row_line, id="pre-host"),
            pytest.param(_PRE_MODEL_HEADER_LINE, _pre_model_row_line, id="pre-model"),
        ],
    )
    @pytest.mark.parametrize("defect", ["formatter-changes-the-third-row", "call-changes-the-third-row"])
    def test_defect_confined_to_the_third_prior_row_is_refused_naming_ordinal_3_with_the_file_unchanged(
        self, tmp_path, monkeypatch, header_line, make_row_line, defect,
    ):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        seeded_lines = [make_row_line(pr_number=number, additions=70 + number) for number in (1, 2, 3)]
        ledger_path.write_text(header_line + "\n" + "\n".join(seeded_lines) + "\n")
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        rows_to_write = prior_rows
        if defect == "formatter-changes-the-third-row":
            real_format = _mod.pr_cost_ledger._format_pr_cost_ledger_row

            def format_adding_one_to_additions_of_pr_3(row, **kwargs):
                if row["pr_number"] == 3:
                    row = {**row, "additions": row["additions"] + 1}
                return real_format(row, **kwargs)

            monkeypatch.setattr(_mod.pr_cost_ledger, "_format_pr_cost_ledger_row", format_adding_one_to_additions_of_pr_3)
        else:
            rows_to_write = [*prior_rows[:2], {**prior_rows[2], "additions": prior_rows[2]["additions"] + 1}]

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows_to_write)

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

    @pytest.mark.parametrize("column", _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS)
    def test_a_change_in_any_single_column_is_refused_naming_that_column(self, column):
        prior_row = _sample_pr_cost_row(model_breakdown=_sample_model_breakdown())
        changed_row = {**prior_row, column: _changed_value_of_the_same_type(prior_row[column])}

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._refuse_if_prior_rows_not_preserved([prior_row], [changed_row])

        assert str(exc_info.value) == _change_refusal(1, column)

    @staticmethod
    def _refuse_if_rows_differ_in(column: str, prior_value, staged_value) -> None:
        _mod.pr_cost_ledger._refuse_if_prior_rows_not_preserved(
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
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
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

    def test_writer_whose_formatter_drops_a_decimal_is_refused_with_the_ledger_unchanged(self, tmp_path, monkeypatch):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(cache_read_usd=0.123457)])
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
        real_format = _mod.pr_cost_ledger._format_pr_cost_ledger_row

        def format_with_five_decimal_cache_read(row, **kwargs):
            cells = real_format(row, **kwargs).split("\t")
            cells[_ledger_cell_index("cache_read_usd")] = f"{row['cache_read_usd']:.5f}"
            return "\t".join(cells)

        monkeypatch.setattr(_mod.pr_cost_ledger, "_format_pr_cost_ledger_row", format_with_five_decimal_cache_read)

        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError) as exc_info:
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [*prior_rows, _sample_pr_cost_row(pr_number=3)])

        assert str(exc_info.value) == _change_refusal(1, "cache_read_usd")
        _assert_ledger_untouched(ledger_path, before_bytes, before_mtime_ns)


class TestWritePrCostLedgerFileReturnsWhetherItUpgraded:
    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            pytest.param(_PRE_HOST_HEADER_LINE, _legacy_row_line, id="pre-host"),
            pytest.param(_PRE_MODEL_HEADER_LINE, _pre_model_row_line, id="pre-model"),
        ],
    )
    def test_returns_true_when_it_replaces_a_file_under_an_older_header(self, tmp_path, header_line, make_row_line):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text(header_line + "\n" + make_row_line() + "\n")
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        assert _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows) is True
        assert ledger_path.read_text().splitlines()[0] == _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE

    def test_returns_false_for_a_current_header_write_and_for_creation(self, tmp_path):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"

        assert _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()]) is False
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows) is False

    @pytest.mark.parametrize("mode", [0o600, 0o644])
    @pytest.mark.parametrize(
        "header_line,make_row_line",
        [
            pytest.param(_PRE_HOST_HEADER_LINE, _legacy_row_line, id="pre-host"),
            pytest.param(_PRE_MODEL_HEADER_LINE, _pre_model_row_line, id="pre-model"),
        ],
    )
    def test_upgrade_preserves_the_files_mode(self, tmp_path, header_line, make_row_line, mode):
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        ledger_path.write_text(header_line + "\n" + make_row_line() + "\n")
        os.chmod(ledger_path, mode)
        prior_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())

        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, prior_rows)

        assert stat.S_IMODE(ledger_path.stat().st_mode) == mode


_UPGRADE_NOTICE_FRAGMENT = (
    "to the current header -- older claude-config checkouts refuse this file until they are updated,"
    " and there is no supported downgrade; see docs/pr-cost.md in the claude-config repo"
)
_PRIOR_ROW_MARKER = "zzpriorrowmarkerzz"
_LEDGER_DIR_MARKER = "zzledgerdirmarkerzz"


def _merged_pr(number: int, branch: str) -> dict:
    return {
        "number": number, "headRefName": branch, "additions": 1, "deletions": 1,
        "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
    }


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

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        captured = capsys.readouterr()
        assert captured.err.count(_UPGRADE_NOTICE_FRAGMENT) == 1
        assert "upgraded the ledger " in captured.err
        assert len(_mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())) == 3
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

        _mod.pr_cost._pr_cost_report(
            _pr_cost_args(record=True, all_accounts=True), datetime(2026, 8, 10, tzinfo=UTC), roots,
        )

        captured = capsys.readouterr()
        assert captured.err.count(f"upgraded account-1's ledger {_UPGRADE_NOTICE_FRAGMENT}") == 1
        assert captured.err.count(f"upgraded account-2's ledger {_UPGRADE_NOTICE_FRAGMENT}") == 1
        assert captured.err.count("upgraded ") == 2
        assert _PRIOR_ROW_MARKER not in captured.err + captured.out

    def test_a_current_header_ledger_prints_no_notice(self, fake_projects, tmp_path, monkeypatch, capsys):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=50)])
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, branch="feature-a")])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=[_merged_pr(1, "feature-a")]))

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert "upgraded" not in capsys.readouterr().err

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
            raise _mod.pr_cost_ledger._PrCostLedgerParseError("refusing to write the ledger (ledger unchanged): test double")

        monkeypatch.setattr(_mod.pr_cost_ledger, "_write_pr_cost_ledger_file", refuse_every_write)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(
                _pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent],
            )

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "refusing to write the ledger" in captured.err
        assert "upgraded" not in captured.err
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
        result = _mod.pr_cost_ledger._latest_pr_cost_row([row_a, row_b], "github.com", "owner/repo", 42, "ci1")
        assert result["additions"] == 2

    def test_distinct_captured_at_values_pick_the_chronologically_latest_row(self):
        """The ordinary (non-tie) case at this same call site: three
        candidates with distinct captured_at values, appended out of
        chronological order, must still resolve to the latest by
        captured_at rather than by append order."""
        row_a = _sample_pr_cost_row(additions=1, captured_at="2026-01-02T00:00:00Z")
        row_b = _sample_pr_cost_row(additions=2, captured_at="2026-01-03T00:00:00Z")
        row_c = _sample_pr_cost_row(additions=3, captured_at="2026-01-01T00:00:00Z")
        result = _mod.pr_cost_ledger._latest_pr_cost_row([row_a, row_b, row_c], "github.com", "owner/repo", 42, "ci1")
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
            "\t"
        )
        assert _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()) == golden
