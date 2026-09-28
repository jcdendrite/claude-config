"""Tests for transcript_analysis/pr_cost_export.py (cmd_pr_cost_export) -- redacted
cross-account export. Every test here monkeypatches subprocess.run (reusing
_fake_pr_cost_subprocess_run), since _ledger_path_is_git_tracked's own git rev-parse call runs
unconditionally on every invocation, not only the git-tree refusal test. Per-account gating,
ordinals, and provenance live in test_transcript_pr_cost_export_accounts.py."""
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ._pr_cost_helpers import (
    _enable_pr_cost,
    _fake_pr_cost_subprocess_run,
    _parse_pr_cost_export_row,
    _pr_cost_export_args,
    _sample_pr_cost_row,
)
from .conftest import _two_declared_roots

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)
class TestPrCostExportRedaction:
    def test_host_repo_and_branch_are_tokenized_not_present_raw(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        distinctive_host = "acme-corp.ghe.example"
        distinctive_repo = "acme-corp/super-secret-internal-project"
        distinctive_branch = "feature/acme-super-secret-launch"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(host=distinctive_host, repo=distinctive_repo, head_branch=distinctive_branch),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        captured = capsys.readouterr()
        written = out_path.read_text()
        for distinctive_value in (distinctive_host, distinctive_repo, distinctive_branch):
            assert distinctive_value not in written
            assert distinctive_value not in captured.out
            assert distinctive_value not in captured.err
        assert "account-1/host-1" in written
        assert "account-1/repo-1" in written
        assert "account-1/branch-1" in written
        assert stat.S_IMODE(out_path.stat().st_mode) == 0o600

    def test_pr_number_is_tokenized_not_present_raw(self, tmp_path, fake_projects, monkeypatch, capsys):
        """pr_number=42 is the fixture default reused everywhere else in this
        suite and can't distinguish redacted from coincidentally rare -- a
        distinctive PR number is required."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        distinctive_pr = 918273
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=distinctive_pr)])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        captured = capsys.readouterr()
        written = out_path.read_text()
        assert str(distinctive_pr) not in written
        assert str(distinctive_pr) not in captured.out
        assert str(distinctive_pr) not in captured.err
        assert "account-1/pr-1" in written

    @pytest.mark.parametrize(
        "row_kwargs,export_column",
        [
            ({"host": "github.com"}, "host"),
            ({"repo": "owner/repo"}, "repo"),
            ({"pr_number": 42}, "pr_number"),
            ({"head_branch": "account-1/branch-1"}, "head_branch_label"),
        ],
        ids=["host", "repo", "pr_number", "head_branch"],
    )
    def test_identical_raw_value_across_two_accounts_re_tokenizes_to_distinct_tokens(
        self, tmp_path, monkeypatch, row_kwargs, export_column,
    ):
        """Each of the four tokenized columns is namespaced per account
        through its own map keyed by (ordinal, raw value) -- two accounts
        sharing the identical raw value must not collapse to the same
        export token. head_branch is re-tokenized on export even though it
        already holds an opaque placeholder from the write path. Its
        identical stored value ("account-1/branch-1", _sample_pr_cost_row's
        own default) must still re-tokenize to two distinct labels rather
        than being skipped because the input already looked redacted."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        for account_config_dir in (acct_a, acct_b):
            (account_config_dir / ".pr-cost-enabled").touch()
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(
                account_config_dir / "pr-cost-ledger.tsv",
                [_sample_pr_cost_row(**row_kwargs)],
            )
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        rows = out_path.read_text().splitlines()[2:]
        tokens = [_parse_pr_cost_export_row(r)[export_column] for r in rows]
        assert len(tokens) == 2
        assert tokens[0] != tokens[1]


class TestPrCostExportSchema:
    def test_header_and_metric_cells_match_ledger_formatting(self, tmp_path, fake_projects, monkeypatch):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        source_row = _sample_pr_cost_row()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [source_row])
        call_log: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(call_log=call_log))
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        # pr-cost-export makes no gh call at all, unlike --record — it only
        # reads already-captured ledger rows. The sole expected call is
        # therefore the git-tracked check on --out (_ledger_path_is_git_tracked).
        assert call_log == [["git", "-C", str(tmp_path.resolve()), "rev-parse", "--is-inside-work-tree"]]

        lines = out_path.read_text().splitlines()
        assert lines[1] == _mod.pr_cost_export._PR_COST_EXPORT_HEADER_LINE
        header_cols = lines[1].split("\t")
        assert header_cols[0] == "account"
        assert "head_branch_label" in header_cols
        assert "correction_count" in header_cols
        assert "head_branch" not in header_cols
        assert "supersedes" not in header_cols

        exported_cells = _parse_pr_cost_export_row(lines[2])
        source_cells = dict(zip(
            _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS,
            _mod.pr_cost_ledger._format_pr_cost_ledger_row(source_row).split("\t"),
            strict=True,
        ))
        # Tokenized (host/repo/pr_number/head_branch/machine), truncated
        # (merged_at/captured_at), and replaced (supersedes) columns are
        # excluded -- they're supposed to differ. Every other column is
        # byte-identical to _format_pr_cost_ledger_row's own rendering of
        # the source row.
        transformed_columns = {
            "host", "repo", "pr_number", "head_branch", "machine", "merged_at", "captured_at", "supersedes",
        }
        for col in _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS:
            if col in transformed_columns:
                continue
            assert exported_cells[col] == source_cells[col], col


class TestRedactPrCostRowForExportColumnShape:
    def test_returned_dict_keys_exactly_match_export_columns(self):
        """_redact_pr_cost_row_for_export must return a dict scoped exactly
        to _PR_COST_EXPORT_COLUMNS. The sole caller pins columns= to that
        same tuple today, but a future caller that iterates .items() instead
        must not silently inherit the ledger's own stale head_branch/
        supersedes keys."""
        result = _mod.pr_cost_export._redact_pr_cost_row_for_export(_sample_pr_cost_row(), 1, 0, {}, {}, {}, {}, {})
        assert set(result) == set(_mod.pr_cost_export._PR_COST_EXPORT_COLUMNS)

    def test_every_ledger_column_is_triaged_for_export_redaction(self):
        """Guards against a new _PR_COST_LEDGER_COLUMNS member reaching the
        export unredacted: every column must already be triaged below as
        tokenized, date-truncated, or an explicitly-approved passthrough."""
        tokenized = {"host", "repo", "pr_number", "head_branch", "machine"}
        date_truncated = {"merged_at", "captured_at"}
        renamed_to_correction_count = {"supersedes"}
        approved_passthrough = {
            "rate_stamp", "join_confidence", "status",
            "cache_read_usd", "cache_write_5m_usd", "cache_write_1h_usd", "output_usd", "input_usd",
            "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "output_tokens", "input_tokens",
            "unpriced_turns", "unpriced_tokens", "turn_count", "session_count",
            "opus_dollars", "opus_dollar_share_pct", "sum_context_at_turn", "mean_context_at_turn",
            "additions", "deletions", "changed_files", "commit_count", "review_comment_count",
            "distinct_top_level_dirs", "distinct_file_extensions",
            "tests_changed", "plan_file_added", "risk_surface_flag",
        }
        triaged = tokenized | date_truncated | renamed_to_correction_count | approved_passthrough
        assert triaged == set(_mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS), (
            "a new ledger column would silently reach the cross-account export unredacted "
            "unless triaged above"
        )


class TestCollapsePrCostRowsToCurrent:
    """_collapse_pr_cost_rows_to_current, exercised directly."""

    def test_later_captured_at_wins_and_superseded_row_is_absent(self):
        older = _sample_pr_cost_row(additions=1, captured_at="2026-01-01T00:00:00Z")
        newer = _sample_pr_cost_row(additions=2, captured_at="2026-01-02T00:00:00Z", supersedes="2026-01-01T00:00:00Z")
        collapsed = _mod.pr_cost_export._collapse_pr_cost_rows_to_current([older, newer])
        assert len(collapsed) == 1
        row, correction_count = collapsed[0]
        assert row["additions"] == 2
        assert correction_count == 1

    def test_collapse_compares_full_precision_captured_at_before_any_truncation(self):
        """Guards the collapse-before-truncation ordering requirement:
        collapse must run before merged_at/captured_at are truncated to a
        date. Two same-key rows share a calendar day but differ by seconds;
        the row appended FIRST is the chronologically later one by
        full-precision captured_at. If collapse instead compared truncated
        dates (both equal) and fell back to append order, it would wrongly
        pick the second (older) row.
        """
        newer_appended_first = _sample_pr_cost_row(additions=10, captured_at="2026-01-01T09:00:10Z")
        older_appended_second = _sample_pr_cost_row(additions=20, captured_at="2026-01-01T09:00:05Z")
        collapsed = _mod.pr_cost_export._collapse_pr_cost_rows_to_current([newer_appended_first, older_appended_second])
        assert len(collapsed) == 1
        row, _correction_count = collapsed[0]
        assert row["additions"] == 10

    def test_correction_count_reflects_prior_capture_count(self):
        rows = [
            _sample_pr_cost_row(captured_at="2026-01-01T00:00:00Z"),
            _sample_pr_cost_row(captured_at="2026-01-02T00:00:00Z"),
            _sample_pr_cost_row(captured_at="2026-01-03T00:00:00Z"),
        ]
        collapsed = _mod.pr_cost_export._collapse_pr_cost_rows_to_current(rows)
        assert len(collapsed) == 1
        _row, correction_count = collapsed[0]
        assert correction_count == 2

    def test_uncorrected_row_has_zero_correction_count(self):
        collapsed = _mod.pr_cost_export._collapse_pr_cost_rows_to_current([_sample_pr_cost_row()])
        _row, correction_count = collapsed[0]
        assert correction_count == 0

    def test_two_rows_differing_only_by_machine_both_survive(self):
        """machine is part of the collapse key, so two machines' rows for
        the same PR are two distinct keys, not a correction pair."""
        rows = [_sample_pr_cost_row(machine="ci1"), _sample_pr_cost_row(machine="ci2")]
        collapsed = _mod.pr_cost_export._collapse_pr_cost_rows_to_current(rows)
        assert len(collapsed) == 2
        assert {row["machine"] for row, _cc in collapsed} == {"ci1", "ci2"}

    def test_newer_degraded_row_wins_over_an_older_ok_row(self):
        """docs/pr-cost.md's documented invariant: collapse never prefers an
        older ok row over a newer, still-current correction, even when that
        correction is itself degraded -- inverting an operator's own
        explicit correction would be worse."""
        older_ok = _sample_pr_cost_row(status="ok", captured_at="2026-01-01T00:00:00Z")
        newer_degraded = _sample_pr_cost_row(status="degraded_network", captured_at="2026-01-02T00:00:00Z")
        collapsed = _mod.pr_cost_export._collapse_pr_cost_rows_to_current([older_ok, newer_degraded])
        assert len(collapsed) == 1
        row, _correction_count = collapsed[0]
        assert row["status"] == "degraded_network"


class TestPrCostExportCollapseAndTieBreakIntegration:
    def test_three_rows_two_sharing_an_identical_captured_at_last_appended_wins(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """Collapse and the tie-break interact: row-A strictly older, then
        row-B and row-C sharing one identical later captured_at, appended in
        that order. A fix that only special-cases exactly two elements
        would pass the plain tie-break test but fail here."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        row_a = _sample_pr_cost_row(additions=1, captured_at="2026-01-01T00:00:00Z")
        row_b = _sample_pr_cost_row(additions=2, captured_at="2026-01-02T00:00:00Z")
        row_c = _sample_pr_cost_row(additions=3, captured_at="2026-01-02T00:00:00Z")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [row_a, row_b, row_c])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        rows = out_path.read_text().splitlines()[2:]
        assert len(rows) == 1
        exported = _parse_pr_cost_export_row(rows[0])
        assert exported["additions"] == "3"
        assert exported["correction_count"] == "2"


class TestPrCostExportTimestamps:
    def test_merged_at_and_captured_at_are_date_only_and_rate_stamp_is_unchanged(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(merged_at="2026-03-04T05:06:07Z", captured_at="2026-03-08T09:10:11Z", rate_stamp="2026-08-02"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        exported = _parse_pr_cost_export_row(out_path.read_text().splitlines()[2])
        assert exported["merged_at"] == "2026-03-04"
        assert exported["captured_at"] == "2026-03-08"
        assert exported["rate_stamp"] == "2026-08-02"


class TestPrCostExportRefusals:
    def test_missing_out_exits_2(self, fake_projects, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=None))
        assert exc_info.value.code == 2

    def test_existing_out_refuses_fast_before_any_ledger_read_with_bytes_intact(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        # No .pr-cost-enabled sentinel on purpose: if the code reached
        # _pr_cost_export_rows, its own skip stderr line would appear below,
        # proving this refusal did NOT happen fast (before any ledger read).
        out_path = tmp_path / "export.tsv"
        out_path.write_text("preexisting content\n")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 2
        assert out_path.read_text() == "preexisting content\n"
        err = capsys.readouterr().err
        assert str(out_path) in err
        assert "is not opted in" not in err
        assert "pass a new path" in err

    def test_out_inside_a_git_working_tree_refuses(self, tmp_path, fake_projects, monkeypatch, capsys):
        out_path = tmp_path / "export.tsv"
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(git_tracked=True))

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 2
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "git working tree" in err
        assert str(out_path) in err  # the operator's own literal --out is echoed by design

    def test_pr_cost_ledger_path_env_var_with_two_roots_refuses(self, tmp_path, monkeypatch, capsys):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(tmp_path / "shared-ledger.tsv"))
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 2
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert str(roots[0]) not in err
        assert str(roots[1]) not in err

    def test_relative_pr_cost_ledger_path_exits_1_naming_account_with_no_raw_value(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        """_pr_cost_export_rows's own account-level `except ValueError` around
        _pr_cost_ledger_path -- that raising function has no dedicated unit
        test of its own anywhere, so this test exercises this catch-and-
        report branch's exit code and account attribution directly."""
        _enable_pr_cost(tmp_path)
        monkeypatch.setenv("PR_COST_LEDGER_PATH", "relative/pr-cost-ledger.tsv")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-1" in err
        assert "relative/pr-cost-ledger.tsv" not in err

    def test_malformed_ledger_exits_1_naming_account_with_no_path_or_raw_value_and_no_partial_file(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        ledger_path.write_text("not-the-header\nsome\tbad\trow\n")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-1" in err
        assert str(ledger_path) not in err
        assert str(tmp_path) not in err

    def test_malformed_ledger_on_second_account_names_that_account_with_no_partial_file(
        self, tmp_path, monkeypatch, capsys,
    ):
        """account-1's ledger is valid and already collected in-memory when
        account-2's own ledger fails to parse. The failure must still name
        account-2, not account-1. --out must not exist, proving
        account-1's already-collected rows are discarded rather than
        partially written."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_a / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1)])
        (acct_b / ".pr-cost-enabled").touch()
        (acct_b / "pr-cost-ledger.tsv").write_text("not-the-header\nsome\tbad\trow\n")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-2" in err
        assert "account-1" not in err

    @pytest.mark.parametrize(
        "column,malformed_value",
        [
            ("machine", "AcmeCorp-Bldg9"),
            ("pr_number", "ACME-NONNUMERIC-PRVALUE"),
            ("merged_at", "ACME-BAD-TIMESTAMP"),
        ],
    )
    def test_malformed_ledger_stderr_omits_raw_value_for_machine_pr_number_and_timestamp(
        self, tmp_path, fake_projects, monkeypatch, capsys, column, malformed_value,
    ):
        """Covers the `machine`/`pr_number`/`merged_at` branches. For
        `pr_number` and `merged_at`, the wrapped stdlib call raises a
        `ValueError` that embeds the raw value.
        `_parse_pr_cost_ledger_row_cells` discards that value before it
        reaches stderr. `machine` instead fails a direct regex match with
        no upstream exception at all. Either way, `pr-cost-export`'s stderr
        line still doesn't leak a peer account's raw cell for any of the
        three."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        line = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(**{column: malformed_value}))
        ledger_path.write_text(_mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + line + "\n")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-1" in err
        assert malformed_value not in err

    def test_export_column_formatting_error_exits_1_naming_account_with_no_partial_file(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        """_format_pr_cost_ledger_row's tab/newline guard has a second call
        site -- _pr_cost_export_rows' own per-row formatting under
        _PR_COST_EXPORT_COLUMNS -- distinct from the ledger-round-trip call
        the malformed-ledger tests above exercise. This test exercises
        _pr_cost_export_rows' own exception handling at this call site, not
        the guard's tab/newline detection (covered separately by the
        malformed-ledger tests above). A fake value is required here because
        no real _PR_COST_EXPORT_COLUMNS value can contain a tab."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        real_format = _mod.pr_cost_ledger._format_pr_cost_ledger_row

        def fake_format(row, *, columns=_mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS):
            if columns == _mod.pr_cost_export._PR_COST_EXPORT_COLUMNS:
                raise _mod.pr_cost_ledger._PrCostLedgerParseError(
                    "line 2: column 'host' contains a tab or newline -- refusing to write a corrupt row"
                )
            return real_format(row, columns=columns)

        monkeypatch.setattr(_mod.pr_cost_ledger, "_format_pr_cost_ledger_row", fake_format)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-1:" in err

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_ledger_read_oserror_exits_1_naming_account_with_no_path_disclosure(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        """A permission-denied ledger file raises OSError from read_text(),
        distinct from the _PrCostLedgerParseError paths covered above. This
        must:
        - exit 1
        - name the account
        - disclose no path either
        """
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])
        os.chmod(ledger_path, 0o000)
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        try:
            with pytest.raises(SystemExit) as exc_info:
                _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))
        finally:
            os.chmod(ledger_path, 0o600)

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-1" in err
        assert str(ledger_path) not in err
        assert str(tmp_path) not in err


class TestPrCostExportSymlinks:
    def test_live_symlink_at_out_refuses_at_the_early_check(self, tmp_path, fake_projects, monkeypatch, capsys):
        real_target = tmp_path / "real.tsv"
        real_target.write_text("preexisting\n")
        live_link = tmp_path / "live-link.tsv"
        live_link.symlink_to(real_target)
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(live_link)))

        assert exc_info.value.code == 2
        assert real_target.read_text() == "preexisting\n"
        err = capsys.readouterr().err
        assert str(live_link) in err
        assert "real.tsv" not in err  # the resolved target's own path never leaks
        assert "is not opted in" not in err  # caught before _pr_cost_export_rows ever ran

    def test_dangling_symlink_at_out_skips_the_early_check_and_refuses_at_publish(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        # No .pr-cost-enabled sentinel on purpose: its own skip stderr line
        # proves execution reached _pr_cost_export_rows, i.e. that the early
        # lexists check did NOT fire for this dangling symlink -- only the
        # terminal os.link publish step is left to catch it.
        dangling_link = tmp_path / "dangling-link.tsv"
        dangling_link.symlink_to(tmp_path / "does-not-exist.tsv")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(dangling_link)))

        assert exc_info.value.code == 2
        assert not (tmp_path / "does-not-exist.tsv").exists()
        err = capsys.readouterr().err
        assert "is not opted in" in err

    def test_out_whose_parent_is_a_symlink_into_a_git_working_tree_refuses(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        real_dir = tmp_path / "real-target-dir"
        real_dir.mkdir()
        symlinked_parent = tmp_path / "symlinked-parent"
        symlinked_parent.symlink_to(real_dir)
        out_path = symlinked_parent / "export.tsv"
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(git_tracked=True))

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 2
        assert not out_path.exists()
        assert "git working tree" in capsys.readouterr().err


class TestPrCostExportPublishBackstop:
    def test_publish_link_refuses_even_when_the_early_lexists_check_is_bypassed(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """Without this, a later refactor that drops the atomic os.link
        publish in favour of the early check alone would pass every other
        test here."""
        _enable_pr_cost(tmp_path)
        out_path = tmp_path / "export.tsv"
        out_path.write_text("preexisting\n")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        monkeypatch.setattr(os.path, "lexists", lambda p: False)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 2
        assert out_path.read_text() == "preexisting\n"
        assert list(tmp_path.glob(".pr-cost-export-*.tmp")) == []

    def test_publish_generic_oserror_exits_2_and_never_creates_out_or_leaves_a_temp_file(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        """os.link raising a generic OSError (e.g. EXDEV, a permission
        failure) at publish time is distinct from the FileExistsError branch
        covered above. It must still:
        - clean up the temp file
        - never leave --out created
        """
        _enable_pr_cost(tmp_path)
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        def fake_link(src, dst):
            raise PermissionError("denied")

        monkeypatch.setattr(os, "link", fake_link)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 2
        assert not out_path.exists()
        assert list(tmp_path.glob(".pr-cost-export-*.tmp")) == []
        err = capsys.readouterr().err
        assert f"--out {str(out_path)!r} could not be published" in err


class TestPrCostExportWriteOSError:
    def test_write_oserror_exits_2_and_never_creates_out_or_leaves_a_temp_file(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        """The write-time OSError (f.write raising into the same-directory
        temp file, before --out itself is ever touched) is distinct from the
        publish-time OSError TestPrCostExportPublishBackstop above covers.
        It must:
        - clean up the temp file rather than leave a truncated one behind
        - never create --out at all
        """
        _enable_pr_cost(tmp_path)
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        class _WriteFailsFile:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def write(self, text):
                raise OSError("disk full")

        def fake_fdopen(fd, mode):
            os.close(fd)  # avoid leaking the real fd mkstemp already created
            return _WriteFailsFile()

        monkeypatch.setattr(os, "fdopen", fake_fdopen)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 2
        assert not out_path.exists()
        assert list(tmp_path.glob(".pr-cost-export-*.tmp")) == []
        err = capsys.readouterr().err
        assert f"--out {str(out_path)!r} could not be written" in err


class TestPrCostExportArgparseWiring:
    def test_registers_pr_cost_export_subcommand_with_expected_defaults(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["pr-cost-export"])
        assert args.out is None
        assert args.extra_config_dirs is None
        assert args.func == _mod.pr_cost_export.cmd_pr_cost_export

    def test_config_dir_wires_to_extra_config_dirs(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["pr-cost-export", "--config-dir", "X"])
        assert args.extra_config_dirs == ["X"]

    def test_config_dir_extra_root_is_scanned_and_its_row_appears(
        self, tmp_path, fake_projects, fake_config_dir_factory, monkeypatch,
    ):
        """Only args.extra_config_dirs is None is asserted elsewhere -- this
        pins the populated case, so cmd_pr_cost_export not forwarding
        args.extra_config_dirs to _resolve_cost_roots would fail here instead
        of passing every existing test silently."""
        _enable_pr_cost(tmp_path)
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(tmp_path / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1)])
        acct_b = fake_config_dir_factory("acct-b")
        (acct_b / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=2)])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path), extra_config_dirs=[str(acct_b)]))

        text = out_path.read_text()
        assert "declared=2" in text.splitlines()[0]
        rows = text.splitlines()[2:]
        assert len(rows) == 2
