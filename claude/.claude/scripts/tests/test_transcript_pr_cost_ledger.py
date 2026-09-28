"""Tests for transcript_analysis/pr_cost_ledger.py (the pr-cost ledger's on-disk format,
canonical parser and formatter, append-only upsert, crash-safe write, and the --record lock)."""
import fcntl
import importlib.util
import os
import stat
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ._pr_cost_helpers import (
    _enable_pr_cost,
    _fake_pr_cost_subprocess_run,
    _legacy_row_line,
    _pr_cost_args,
    _sample_pr_cost_row,
)
from .conftest import _priced, _write_jsonl

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

    def test_missing_header_raises(self):
        text = "not-the-header\n" + self._valid_line() + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="missing or mismatched"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

    def test_mismatched_header_raises(self):
        bad_header = "\t".join(_mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS[:-1])  # drop the last column
        text = bad_header + "\n" + self._valid_line() + "\n"
        with pytest.raises(_mod.pr_cost_ledger._PrCostLedgerParseError, match="missing or mismatched"):
            _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)


class TestPrCostLedgerLegacyHostColumnMigration:
    """The pre-host-column header (_PR_COST_LEDGER_LEGACY_HEADER_LINE) is
    the one documented backward-compat exception to the parser's otherwise
    exact header/column-count match -- every row recorded under it predates
    GHE host-awareness, so implicitly belongs to github.com."""

    def test_legacy_header_row_parses_with_host_defaulted_to_github_com(self):
        text = _mod.pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE + "\n" + _legacy_row_line() + "\n"

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(text)

        assert len(rows) == 1
        assert rows[0]["host"] == "github.com"

    def test_record_against_legacy_file_upgrades_it_to_current_schema(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """A subsequent --record write against a legacy-format file rewrites
        the whole ledger under the current header -- no separate migration
        script needed, since the writer always renders the current schema
        from the in-memory rows the parser already normalized."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        legacy_line = _legacy_row_line(pr_number=7, machine="ci1")
        ledger_path.write_text(_mod.pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE + "\n" + legacy_line + "\n")

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

        written_text = ledger_path.read_text()
        assert written_text.splitlines()[0] == _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE
        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(written_text)
        assert len(rows) == 2  # the upgraded legacy row (pr_number=7) plus the freshly recorded one (pr_number=1)
        assert all(r["host"] == "github.com" for r in rows)


class TestWritePrCostLedgerFileMode:
    def test_fresh_file_created_with_0600(self, tmp_path):
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
        )
        assert _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row()) == golden
