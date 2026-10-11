"""Tests for transcript_analysis/pr_cost_export.py's per-account gating, ordinals, and
provenance: opt-in/opt-out, empty-ledger accounts, legacy (no-host) headers, and the
provenance line's corpus digest and override flag."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ._pr_cost_helpers import (
    _PRE_HOST_HEADER_LINE,
    _PRE_MODEL_HEADER_LINE,
    _enable_pr_cost,
    _fake_pr_cost_subprocess_run,
    _legacy_row_line,
    _parse_pr_cost_export_provenance_line,
    _parse_pr_cost_export_row,
    _pr_cost_export_args,
    _pre_model_row_line,
    _sample_model_breakdown,
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
class TestPrCostExportOrdinalsAndOrder:
    def test_row_order_is_identical_regardless_of_which_account_is_active(
        self, tmp_path, monkeypatch,
    ):
        """Accounts are visited in _redaction_ordinals order (sorted by
        resolved path), not _resolve_cost_roots' own active-profile-first
        order. This test runs against cmd_pr_cost_export itself rather than
        mirroring the existing ordinal-stability test, because that would
        only re-test an unchanged helper and would still pass against a bug
        that reverted this ordering."""
        acct_a = tmp_path / "acct-a"
        (acct_a / "projects").mkdir(parents=True)
        (acct_a / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_a / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1)])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        (acct_b / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=2)])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        roots_file = tmp_path / "roots"
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
        roots_file.write_text(f"{acct_b}\n")
        out_a_active = tmp_path / "out-a-active.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_a_active)))

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_b))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_b / "projects")
        roots_file.write_text(f"{acct_a}\n")
        out_b_active = tmp_path / "out-b-active.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_b_active)))

        def account_cells(path):
            return [_parse_pr_cost_export_row(line)["account"] for line in path.read_text().splitlines()[2:]]

        assert account_cells(out_a_active) == account_cells(out_b_active)
        assert account_cells(out_a_active) == ["account-1", "account-2"]


class TestPrCostExportOptIn:
    def test_missing_sentinel_and_empty_ledger_accounts_are_skipped_without_renumbering(
        self, tmp_path, monkeypatch, capsys,
    ):
        """declared=4, opted_in=3, skipped_not_opted_in=1, and
        len(formatted_rows)=2 are all pairwise distinct, so a swap between
        any two of them -- e.g. opted_in/declared in the stdout summary
        f-string, or opted_in/skipped_not_opted_in at the
        _pr_cost_export_provenance_line call site -- changes the output
        instead of passing undetected."""
        acct_a = tmp_path / "acct-a"
        (acct_a / "projects").mkdir(parents=True)
        (acct_a / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_a / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1)])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        (acct_b / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=2)])
        acct_c = tmp_path / "acct-c"
        (acct_c / "projects").mkdir(parents=True)
        (acct_c / ".pr-cost-enabled").touch()
        # acct_c: opted in, but a header-only (never captured) ledger --
        # must count toward opted_in without contributing a row.
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_c / "pr-cost-ledger.tsv", [])
        acct_d = tmp_path / "acct-d"
        (acct_d / "projects").mkdir(parents=True)
        # acct_d: no sentinel, but a ledger present -- must still contribute
        # zero rows and must not renumber any other account's own ordinal.
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_d / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=4)])
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n{acct_c}\n{acct_d}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        text = out_path.read_text()
        rows = text.splitlines()[2:]
        assert len(rows) == 2
        # not renumbered despite acct_c's zero-row ledger and acct_d's skip
        assert [_parse_pr_cost_export_row(row)["account"] for row in rows] == ["account-1", "account-2"]
        # Same parse-into-dict pattern as
        # TestPrCostExportProvenanceLine.test_provenance_line_present_above_header_and_parses_as_key_value_tokens,
        # not a substring check -- "declared=4" would also match "declared=40".
        parsed = _parse_pr_cost_export_provenance_line(text.splitlines()[0])
        assert parsed["declared"] == "4"
        assert parsed["opted_in"] == "3"
        assert parsed["skipped_not_opted_in"] == "1"
        out = capsys.readouterr().out
        assert str(out_path) in out
        assert "wrote 2 row(s) from 3 of 4 declared account(s)" in out
        assert list(tmp_path.glob(".pr-cost-export-*.tmp")) == []

    def test_fully_skipped_run_with_no_opted_in_account_exits_0_with_header_only_file(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """The realistic first-run experience for a stow consumer who tries
        pr-cost-export before opting in anywhere: the sole declared account
        has no .pr-cost-enabled sentinel at all, not merely an empty
        ledger (TestPrCostExportEmptyLedger covers that separate case).
        Must still exit 0 and write a valid header-only file, not crash or
        divide by a zero opted-in count somewhere upstream."""
        # No .pr-cost-enabled sentinel created.
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        text = out_path.read_text()
        lines = text.splitlines()
        assert lines[1] == _mod.pr_cost_export._PR_COST_EXPORT_HEADER_LINE
        assert lines[2:] == []
        # Same parse-into-dict pattern as
        # TestPrCostExportProvenanceLine.test_provenance_line_present_above_header_and_parses_as_key_value_tokens,
        # not a substring check -- "declared=1" would also match "declared=10".
        parsed = _parse_pr_cost_export_provenance_line(lines[0])
        assert parsed["declared"] == "1"
        assert parsed["opted_in"] == "0"
        assert parsed["skipped_not_opted_in"] == "1"

    def test_symlinked_sentinel_opts_both_accounts_into_export_together(self, tmp_path, monkeypatch):
        """Mirrors pr-cost --all-accounts' own
        test_symlinked_sentinel_opts_both_accounts_in_together: the sentinel
        check is a plain Path.exists(), which follows symlinks -- an
        account whose .pr-cost-enabled is a symlink to another account's
        real sentinel is included in the export too, with no separate
        consent of its own. Export is the higher-stakes consumer of this
        same gate, since inclusion here means the row leaves the machine."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()
        os.symlink(acct_a / ".pr-cost-enabled", acct_b / ".pr-cost-enabled")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_a / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1)])
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=2)])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        text = out_path.read_text()
        lines = text.splitlines()
        rows = lines[2:]
        assert len(rows) == 2
        parsed = _parse_pr_cost_export_provenance_line(lines[0])
        assert parsed["opted_in"] == "2"

    def test_dangling_symlinked_sentinel_is_skipped_not_included(self, tmp_path, fake_projects, monkeypatch):
        """Inverse of the live-symlink case above: Path.exists() follows a
        symlink to a nonexistent target and returns False, so a dangling
        .pr-cost-enabled is treated the same as no sentinel at all."""
        os.symlink(tmp_path / "nonexistent-target", tmp_path / ".pr-cost-enabled")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        parsed = _parse_pr_cost_export_provenance_line(out_path.read_text().splitlines()[0])
        assert parsed["opted_in"] == "0"
        assert parsed["skipped_not_opted_in"] == "1"

    def test_toml_true_with_no_sentinel_file_is_included(self, tmp_path, fake_projects, monkeypatch):
        """An account whose consent lives only in claude-config.toml (the
        normal install.sh-prompted path today) has no .pr-cost-enabled file
        at all -- must still be included, since --record's own opt-in gate
        already treats this account as fully opted in via the identical
        _config.config_enabled call."""
        (tmp_path / "claude-config.toml").write_text("pr_cost_recording = true\n")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(tmp_path / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1)])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        text = out_path.read_text()
        assert len(text.splitlines()[2:]) == 1
        parsed = _parse_pr_cost_export_provenance_line(text.splitlines()[0])
        assert parsed["opted_in"] == "1"
        assert parsed["skipped_not_opted_in"] == "0"

    def test_toml_false_overrides_a_present_legacy_sentinel_and_is_excluded(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """Regression test: an explicit pr_cost_recording = false in
        claude-config.toml must exclude the account even with a leftover
        .pr-cost-enabled sentinel still present on disk. That sentinel is
        typically still present because migrate-legacy-config.sh's delete
        offer defaults to No. A bare sentinel_path.exists() check wrongly
        included this account despite the explicit revocation."""
        (tmp_path / "claude-config.toml").write_text("pr_cost_recording = false\n")
        (tmp_path / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(tmp_path / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1)])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        text = out_path.read_text()
        assert text.splitlines()[2:] == []
        parsed = _parse_pr_cost_export_provenance_line(text.splitlines()[0])
        assert parsed["opted_in"] == "0"
        assert parsed["skipped_not_opted_in"] == "1"

    def test_config_dir_unresolvable_exits_1_with_its_own_diagnostic(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        """Tests _config.config_enabled("pr_cost_recording", ...) returning
        None, distinct from a resolved account simply not being opted in.
        This is not reachable through account_config_dir itself, since
        root.parent is always a concrete Path (per this call site's own
        comment). The test therefore forces the condition directly through
        _config.config_enabled. Mirrors
        TestPrCostRecordingConfigDirUnresolvable's identical coverage of
        --record's own sibling branch."""
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        real_config_enabled = _mod._config.config_enabled

        def _fake_config_enabled(key, config_dir_override=None):
            if key == "pr_cost_recording":
                return None
            return real_config_enabled(key, config_dir_override=config_dir_override)

        monkeypatch.setattr(_mod._config, "config_enabled", _fake_config_enabled)
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        assert "account-1's config directory could not be resolved" in capsys.readouterr().err


class TestPrCostExportConfigSchemaErrors:
    """Tests _pr_cost_export_rows's own copy of --record's config-schema
    error handling around _config.config_enabled. Forces each branch
    directly, mirroring TestPrCostRecordingKeyError's forcing technique.
    Covers only this export-path copy of the branches -- --record's own
    identical-shaped branches are a separate, pre-existing coverage gap."""

    def test_config_schema_empty_error_exits_1_naming_the_account(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        def _raise_schema_empty(key, config_dir_override=None):
            raise _mod._config.ConfigSchemaEmptyError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_schema_empty)
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        assert "account-1: config-keys.psv empty or malformed" in capsys.readouterr().err

    def test_config_schema_row_truncated_error_exits_1_naming_the_account(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        def _raise_schema_truncated(key, config_dir_override=None):
            raise _mod._config.ConfigSchemaRowTruncatedError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_schema_truncated)
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-1: pr_cost_recording's config-keys.psv row is" in err
        assert "truncated" in err

    def test_reports_unknown_key_when_key_error_and_schema_populated(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        def _raise_key_error(key, config_dir_override=None):
            raise KeyError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_key_error)
        monkeypatch.setattr(_mod._config, "schema", lambda: {"worktree_required": object()})
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "account-1: unknown config key" in err
        assert "pr_cost_recording" in err

    def test_reports_unreadable_schema_when_key_error_and_schema_empty(
        self, tmp_path, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        def _raise_key_error(key, config_dir_override=None):
            raise KeyError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_key_error)
        monkeypatch.setattr(_mod._config, "schema", lambda: {})
        out_path = tmp_path / "export.tsv"

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        assert exc_info.value.code == 1
        assert not out_path.exists()
        assert "account-1: could not read config-keys.psv" in capsys.readouterr().err


class TestPrCostExportEmptyLedger:
    def test_header_only_ledger_contributes_zero_rows_and_is_excluded_from_corpus_identities(
        self, tmp_path, monkeypatch,
    ):
        """A ledger that parses to zero data rows (header-only: opted in,
        but no PR ever captured -- distinct from the no-sentinel skip
        TestPrCostExportOptIn covers above) must still count toward
        opted_in. It must contribute no row and no corpus_identities entry
        of its own."""
        acct_a = tmp_path / "acct-a"
        (acct_a / "projects").mkdir(parents=True)
        (acct_a / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(acct_a / "pr-cost-ledger.tsv", [])  # header only, zero rows
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        (acct_b / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(captured_at="2026-01-01T00:00:00Z", machine="ci1")],
        )
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        roots_file = tmp_path / "roots"

        def digest_of(path):
            return _parse_pr_cost_export_provenance_line(path.read_text().splitlines()[0])["corpus"]

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        out_both = tmp_path / "out-both.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_both)))

        text = out_both.read_text()
        provenance = text.splitlines()[0]
        assert "declared=2" in provenance
        assert "opted_in=2" in provenance
        rows = text.splitlines()[2:]
        assert len(rows) == 1
        assert _parse_pr_cost_export_row(rows[0])["account"] == "account-2"  # acct_a's empty ledger contributes no row

        # acct_a's empty ledger must not be counted in corpus_identities: an
        # export scoped to acct_b alone produces the identical corpus digest.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_b))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_b / "projects")
        roots_file.write_text("")
        out_solo = tmp_path / "out-solo.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_solo)))
        assert digest_of(out_both) == digest_of(out_solo)

    def test_opted_in_account_with_no_ledger_file_contributes_zero_rows_and_is_excluded_from_corpus_identities(
        self, tmp_path, monkeypatch,
    ):
        """Distinct from the header-only case above: here the sentinel is
        present but pr-cost-ledger.tsv was never created (no --record has
        ever run for this account). Must still:
        - count toward opted_in
        - contribute no row
        - contribute no corpus_identities entry
        """
        acct_a = tmp_path / "acct-a"
        (acct_a / "projects").mkdir(parents=True)
        (acct_a / ".pr-cost-enabled").touch()  # opted in, but no ledger file at all
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        (acct_b / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(captured_at="2026-01-01T00:00:00Z", machine="ci1")],
        )
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        roots_file = tmp_path / "roots"

        def digest_of(path):
            return _parse_pr_cost_export_provenance_line(path.read_text().splitlines()[0])["corpus"]

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        out_both = tmp_path / "out-both.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_both)))

        text = out_both.read_text()
        provenance = text.splitlines()[0]
        assert "declared=2" in provenance
        assert "opted_in=2" in provenance
        rows = text.splitlines()[2:]
        assert len(rows) == 1
        assert _parse_pr_cost_export_row(rows[0])["account"] == "account-2"  # acct_a's missing ledger contributes no row

        # acct_a's missing ledger must not be counted in corpus_identities: an
        # export scoped to acct_b alone produces the identical corpus digest.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_b))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_b / "projects")
        roots_file.write_text("")
        out_solo = tmp_path / "out-solo.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_solo)))
        assert digest_of(out_both) == digest_of(out_solo)


class TestPrCostExportLegacyHeader:
    def test_legacy_header_host_backfill_tokenizes_identically_to_a_recorded_github_com(self):
        """A ledger's legacy-header rows all predate GHE support, so a
        backfilled host="github.com" is a validated historical fact, not a
        guess. It must therefore tokenize identically to a genuinely
        recorded github.com. One ledger file carries exactly one header
        format, so this can't be shown by comparing two accounts (their
        tokens are account-namespaced and never equal). Called directly
        instead, twice with the same ordinal and host_map."""
        legacy_text = _mod.pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE + "\n" + _legacy_row_line() + "\n"
        legacy_row = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(legacy_text)[0]
        current_row = _sample_pr_cost_row(host="github.com")

        host_map: dict = {}
        legacy_token = _mod.pr_cost_export._redact_pr_cost_row_for_export(legacy_row, 1, 0, host_map, {}, {}, {}, {})["host"]
        current_token = _mod.pr_cost_export._redact_pr_cost_row_for_export(current_row, 1, 0, host_map, {}, {}, {}, {})["host"]
        assert legacy_token == current_token

    def test_legacy_header_account_is_counted_in_the_provenance_line(self, tmp_path, fake_projects, monkeypatch):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        ledger_path.write_text(_mod.pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE + "\n" + _legacy_row_line() + "\n")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        lines = out_path.read_text().splitlines()
        assert "legacy_header_accounts=1" in lines[0]
        assert len(lines[2:]) == 1
        assert _parse_pr_cost_export_row(lines[2])["host"] == "account-1/host-1"


class TestPrCostExportMixedSchemaAccounts:
    def test_pre_host_pre_model_and_current_accounts_export_together_and_only_the_pre_host_one_counts_as_legacy(
        self, tmp_path, monkeypatch,
    ):
        """account-1 holds a pre-host ledger, account-2 a pre-model one, account-3 a current one with a
        populated model_breakdown. legacy_header_accounts counts the pre-host ledger only."""
        account_dirs = []
        for name in ("acct-a", "acct-b", "acct-c"):
            account_dir = tmp_path / name
            (account_dir / "projects").mkdir(parents=True)
            (account_dir / ".pr-cost-enabled").touch()
            account_dirs.append(account_dir)
        acct_a, acct_b, acct_c = account_dirs
        (acct_a / "pr-cost-ledger.tsv").write_text(_PRE_HOST_HEADER_LINE + "\n" + _legacy_row_line(pr_number=1) + "\n")
        (acct_b / "pr-cost-ledger.tsv").write_text(
            _PRE_MODEL_HEADER_LINE + "\n" + _pre_model_row_line(pr_number=2) + "\n"
        )
        source_breakdown = _sample_model_breakdown("claude-test-literal")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_c / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=3, model_breakdown=source_breakdown)],
        )
        source_cell = (acct_c / "pr-cost-ledger.tsv").read_text().splitlines()[1].split("\t")[-1]
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n{acct_c}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        lines = out_path.read_text().splitlines()
        assert _parse_pr_cost_export_provenance_line(lines[0])["legacy_header_accounts"] == "1"
        exported_rows = [_parse_pr_cost_export_row(line) for line in lines[2:]]
        assert [row["account"] for row in exported_rows] == ["account-1", "account-2", "account-3"]
        assert [row["model_breakdown"] for row in exported_rows] == ["", "", source_cell]


class TestPrCostExportProvenanceLine:
    def test_provenance_line_present_above_header_and_parses_as_key_value_tokens(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row()])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        lines = out_path.read_text().splitlines()
        provenance = lines[0]
        assert provenance.startswith("# pr-cost-export ")
        assert "DO-NOT-PUBLISH" in provenance
        # First three space-separated fields are "#", the subcommand name,
        # and the DO-NOT-PUBLISH marker -- none of them key=value shaped.
        parsed = _parse_pr_cost_export_provenance_line(provenance)
        assert set(parsed) == {
            "exported_at", "declared", "opted_in", "skipped_not_opted_in", "legacy_header_accounts",
            "legacy_machine_value_rows", "corpus", "corpus_override",
        }
        assert lines[1] == _mod.pr_cost_export._PR_COST_EXPORT_HEADER_LINE

    def test_legacy_machine_value_row_is_counted_but_hex_identity_row_is_not(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """legacy_machine_value_rows counts only rows whose machine cell
        doesn't match _MACHINE_IDENTITY_RE -- a pre-migration operator-chosen
        label like "acme1" is counted, a tool-generated 8-hex-char identity
        is not. Two distinct legacy rows must sum to =2, not merely flag
        presence at =1 -- a buggy boolean-flag regression would still pass
        a single-legacy-row assertion."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(pr_number=1, machine="acme1"),
            _sample_pr_cost_row(pr_number=2, machine="1a2b3c4d"),
            _sample_pr_cost_row(pr_number=3, machine="laptop2"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        provenance = out_path.read_text().splitlines()[0]
        assert "legacy_machine_value_rows=2" in provenance

    def test_legacy_machine_value_rows_counts_post_collapse_not_per_raw_capture(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """Two raw captures sharing one (host, repo, pr_number, machine) key
        -- an in-place correction under the same legacy machine value --
        collapse to a single current row, so legacy_machine_value_rows must
        count =1, not =2. A count taken over raw_rows before collapse would
        double-count this correction."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(pr_number=1, machine="legacy1", captured_at="2026-01-01T00:00:00Z"),
            _sample_pr_cost_row(pr_number=1, machine="legacy1", captured_at="2026-01-02T00:00:00Z"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        provenance = out_path.read_text().splitlines()[0]
        assert "legacy_machine_value_rows=1" in provenance

    def test_same_pr_recaptured_under_new_machine_identity_exports_both_rows(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """machine is part of _collapse_pr_cost_rows_to_current's grouping
        key, so a pre-migration legacy-labeled capture and a later
        --record --force --pr N recapture of the identical (host, repo,
        pr_number) under a new hex machine identity do not collapse into
        one row. Both survive as independent rows, and only the
        legacy-shaped one is flagged."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(pr_number=42, machine="acme1", captured_at="2026-01-01T00:00:00Z"),
            _sample_pr_cost_row(pr_number=42, machine="1a2b3c4d", captured_at="2026-02-01T00:00:00Z"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        lines = out_path.read_text().splitlines()
        assert "legacy_machine_value_rows=1" in lines[0]
        rows = [_parse_pr_cost_export_row(line) for line in lines[2:]]
        assert len(rows) == 2
        assert {row["machine"] for row in rows} == {"account-1/machine-1", "account-1/machine-2"}
        assert all(row["correction_count"] == "0" for row in rows)

    def test_same_raw_machine_value_tokenizes_differently_across_accounts(
        self, tmp_path, monkeypatch,
    ):
        """machine_map is a fresh dict scoped to one _pr_cost_export_rows
        call, keyed by (ordinal, raw value), so two accounts whose ledgers
        each record the identical raw machine value must not collapse into
        one shared token. Each keeps its own account-K ordinal prefix.
        Mirrors TestPrCostExportLegacyHeader's identical-host-tokenizes-
        identically proof, but for the opposite claim (same raw value,
        different accounts, different tokens)."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()
        (acct_b / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_a / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=1, machine="same1")],
        )
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(pr_number=2, machine="same1")],
        )
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        rows = [_parse_pr_cost_export_row(line) for line in out_path.read_text().splitlines()[2:]]
        assert len(rows) == 2
        machine_tokens = {row["account"]: row["machine"] for row in rows}
        assert machine_tokens == {"account-1": "account-1/machine-1", "account-2": "account-2/machine-1"}

    def test_corpus_override_true_from_claude_config_dir_alone_with_no_roots_file_override(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """A contributor isolating a smoke test by setting only
        CLAUDE_CONFIG_DIR (no real ~/.claude/transcript-config-dirs to
        isolate TRANSCRIPT_CONFIG_DIRS_FILE from) produces a fully synthetic
        export, just like the seam-file override every other test in this
        class relies on. corpus_override must still flag it -- otherwise
        this shape is indistinguishable from a real production export."""
        monkeypatch.delenv("TRANSCRIPT_CONFIG_DIRS_FILE", raising=False)
        # Keeps declared_transcript_roots' fallback off this machine's real file.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _enable_pr_cost(tmp_path)
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        provenance = out_path.read_text().splitlines()[0]
        assert "corpus_override=1" in provenance

    def test_corpus_override_false_from_claude_config_dir_alone_with_real_roots_file_present(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """~/.claude/transcript-config-dirs resolves against $HOME, never
        CLAUDE_CONFIG_DIR. The real multi-account scenario this check must
        not regress: a real non-personal-account run legitimately sets only
        CLAUDE_CONFIG_DIR while that real roots file still exists and
        declares the account roster. This case must stay
        corpus_override=0, because a real declared-roots file makes it a
        legitimate multi-account run, not a synthetic-corpus one."""
        monkeypatch.delenv("TRANSCRIPT_CONFIG_DIRS_FILE", raising=False)
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "transcript-config-dirs").write_text("")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _enable_pr_cost(tmp_path)
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        provenance = out_path.read_text().splitlines()[0]
        assert "corpus_override=0" in provenance

    def test_corpus_digest_matches_same_account_set_and_differs_across_different_sets(
        self, tmp_path, monkeypatch,
    ):
        acct_a = tmp_path / "acct-a"
        (acct_a / "projects").mkdir(parents=True)
        (acct_a / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_a / "pr-cost-ledger.tsv", [_sample_pr_cost_row(captured_at="2026-01-01T00:00:00Z", machine="ci1")],
        )
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        (acct_b / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_b / "pr-cost-ledger.tsv", [_sample_pr_cost_row(captured_at="2026-02-01T00:00:00Z", machine="ci2")],
        )
        acct_c = tmp_path / "acct-c"
        (acct_c / "projects").mkdir(parents=True)
        (acct_c / ".pr-cost-enabled").touch()
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            acct_c / "pr-cost-ledger.tsv", [_sample_pr_cost_row(captured_at="2026-03-01T00:00:00Z", machine="ci3")],
        )
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
        roots_file = tmp_path / "roots"
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        def digest_of(path):
            return _parse_pr_cost_export_provenance_line(path.read_text().splitlines()[0])["corpus"]

        roots_file.write_text(f"{acct_b}\n")
        out1 = tmp_path / "out1.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out1)))
        out2 = tmp_path / "out2.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out2)))
        assert digest_of(out1) == digest_of(out2)

        roots_file.write_text(f"{acct_c}\n")
        out3 = tmp_path / "out3.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out3)))
        assert digest_of(out3) != digest_of(out1)

    def test_corpus_digest_is_unchanged_when_a_second_row_is_appended_to_the_ledger(
        self, tmp_path, fake_projects, monkeypatch,
    ):
        """corpus= is built from each account's raw_rows[0] alone (the
        ledger's first line). Append-only writes never move or rewrite that
        line, so a later --force correction or a second captured PR
        appended to the same ledger must leave the digest unchanged. The
        same-account-set test above only proves stability when row count
        itself never changes. This proves the first-row-never-moves
        invariant docs/pr-cost.md asserts."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        first_row = _sample_pr_cost_row(pr_number=1, captured_at="2026-01-01T00:00:00Z", machine="ci1")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [first_row])
        out1 = tmp_path / "out1.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out1)))

        second_row = _sample_pr_cost_row(pr_number=2, captured_at="2026-02-01T00:00:00Z", machine="ci2")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [first_row, second_row])
        out2 = tmp_path / "out2.tsv"
        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out2)))

        def digest_of(path):
            return _parse_pr_cost_export_provenance_line(path.read_text().splitlines()[0])["corpus"]

        assert digest_of(out1) == digest_of(out2)
