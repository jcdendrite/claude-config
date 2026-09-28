"""Tests for transcript_analysis/ledger_common.py (the shared machine-identity generation and
git-tracked-destination check)."""
import importlib.util
import stat
import subprocess
import sys
import threading
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ._pr_cost_helpers import _fake_pr_cost_subprocess_run, _pr_cost_args, _sample_pr_cost_row
from .conftest import _cost_ledger_args, _cost_ledger_row, _priced, _two_declared_roots, _write_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# Machine identity (--record's generated `machine` value, replacing operator-
# supplied --machine-label) -- shared mechanism between cost-ledger and
# pr-cost. See docs/pr-cost.md's "Machine identity" section for the full
# contract.
# ---------------------------------------------------------------------------
class TestMachineIdentity:
    def test_generate_on_first_use_cost_ledger_record(
        self, fake_projects, cost_ledger_file, tmp_path, monkeypatch,
    ):
        cfg_dir = tmp_path / "isolated-claude-config"
        cfg_dir.mkdir()
        (cfg_dir / ".cost-ledger-enabled").touch()
        monkeypatch.setattr(_mod.ledger_common, "config_dir", lambda: cfg_dir)
        assert not (cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))

        content = (cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).read_text()
        assert _mod.ledger_common._MACHINE_IDENTITY_RE.match(content)
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert rows[0]["machine"] == content

    def test_generate_on_first_use_pr_cost_record(self, fake_projects, tmp_path, monkeypatch):
        (tmp_path / ".pr-cost-enabled").touch()
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        assert not (tmp_path / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        content = (tmp_path / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).read_text()
        assert _mod.ledger_common._MACHINE_IDENTITY_RE.match(content)
        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert rows[0]["machine"] == content

    def test_reuse_leaves_identity_file_byte_identical_cost_ledger(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled,
    ):
        identity_path = cost_ledger_enabled / _mod.ledger_common._MACHINE_IDENTITY_FILENAME
        before = identity_path.read_bytes()
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))

        after = identity_path.read_bytes()
        assert before == after
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert rows[0]["machine"] == before.decode()

    def test_reuse_across_two_record_invocations_pr_cost(self, fake_projects, tmp_path, monkeypatch):
        (tmp_path / ".pr-cost-enabled").touch()
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        args = _pr_cost_args(record=True, pr=1)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        identity_path = tmp_path / _mod.ledger_common._MACHINE_IDENTITY_FILENAME
        first = identity_path.read_bytes()

        # Second call hits the "already captured" refusal, but identity
        # resolution runs before that check -- exercises the read-existing
        # branch, not the generate branch, for free.
        with pytest.raises(SystemExit):
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert identity_path.read_bytes() == first

    def test_two_config_dirs_get_two_distinct_generated_identities(self, tmp_path, monkeypatch):
        """Deterministic monkeypatched token_hex sequence, so this can
        neither pass by luck nor fail at 2**-32."""
        values = iter([b"\xaa\xbb\xcc\xdd", b"\x11\x22\x33\x44"])
        monkeypatch.setattr(_mod.ledger_common.secrets, "token_hex", lambda n: next(values).hex())
        cfg_a = tmp_path / "cfg-a"
        cfg_a.mkdir()
        cfg_b = tmp_path / "cfg-b"
        cfg_b.mkdir()

        identity_a = _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=cfg_a)
        identity_b = _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=cfg_b)

        assert identity_a == "aabbccdd"
        assert identity_b == "11223344"

    def test_resolve_machine_identity_routes_through_ledger_common_config_dir_not_the_shims(
        self, tmp_path, monkeypatch,
    ):
        """_machine_identity_path resolves config_dir() through ledger_common's own by-name
        binding, not the shim's -- every other test in this class either passes
        config_dir_override explicitly or patches both bindings to the same directory, so
        neither would catch a call routed through the wrong one. Patching the two bindings to
        distinct directories and asserting the identity file lands only in ledger_common's own
        closes that gap."""
        ledger_common_dir = tmp_path / "ledger-common-config"
        ledger_common_dir.mkdir()
        shim_dir = tmp_path / "shim-config"
        shim_dir.mkdir()
        monkeypatch.setattr(_mod.ledger_common, "config_dir", lambda: ledger_common_dir)
        monkeypatch.setattr(_mod, "config_dir", lambda: shim_dir)

        _mod.ledger_common._resolve_machine_identity("cost-ledger")

        assert (ledger_common_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()
        assert not (shim_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()

    def test_shared_across_subcommands(self, fake_projects, cost_ledger_file, tmp_path, monkeypatch):
        """One identity, resolved through the same config dir, is shared
        by both subcommands rather than each minting its own."""
        (tmp_path / ".cost-ledger-enabled").touch()
        (tmp_path / ".pr-cost-enabled").touch()
        # _cost_ledger_report's own sentinel check reads config_dir()
        # directly via _config.py's own binding, not through fake_projects'
        # mod.cost_ledger.config_dir patch above. pr-cost's own gate check instead
        # derives its config_dir_override from `roots`, independent of
        # mod.cost_ledger.config_dir. The two only agree here because fake_projects'
        # tmp_path/"projects" root's parent is this same tmp_path.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z", branch="feature-a"),
        ])

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        _preamble, cost_ledger_rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        cost_ledger_identity = cost_ledger_rows[0]["machine"]

        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))
        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        pr_cost_rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((tmp_path / "pr-cost-ledger.tsv").read_text())

        assert pr_cost_rows[0]["machine"] == cost_ledger_identity
        assert _mod.ledger_common._MACHINE_IDENTITY_RE.match(cost_ledger_identity)

    def test_link_collision_adopts_racers_value_and_cleans_up_temp_file(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        winner_identity = "deadbeef"

        def racing_link(src, dst):
            # Simulates a racing process's own link() winning first: its
            # value is already at the target path by the time this one's
            # link() call raises EEXIST.
            Path(dst).write_text(winner_identity)
            raise FileExistsError(17, "File exists")

        monkeypatch.setattr(_mod.os, "link", racing_link)

        identity = _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=cfg_dir)

        assert identity == winner_identity
        assert list(cfg_dir.glob(f"{_mod.ledger_common._MACHINE_IDENTITY_FILENAME}.*")) == []

    def test_racing_threads_on_first_use_converge_on_one_identity(self, tmp_path):
        """Real threads, not a mocked collision -- exercises the actual
        mkstemp+os.link generate-on-first-use race rather than a
        single-thread simulation of its outcome, matching
        TestCostLedgerConcurrency's real-threading.Thread race shape."""
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        identities: list[str | None] = [None] * 8

        def _run(i: int) -> None:
            identities[i] = _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=cfg_dir)

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(len(identities))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(identities)) == 1
        assert _mod.ledger_common._MACHINE_IDENTITY_RE.match(identities[0])
        assert list(cfg_dir.glob(f"{_mod.ledger_common._MACHINE_IDENTITY_FILENAME}.*")) == []

    def test_malformed_identity_file_exits_1_without_leaking_the_config_dir_path(self, tmp_path, capsys):
        distinctive_dirname = "distinctive-marker-config-dir"
        cfg_dir = tmp_path / distinctive_dirname
        cfg_dir.mkdir()
        (cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_text("not-hex!!")

        with pytest.raises(SystemExit) as exc_info:
            _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=cfg_dir)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert distinctive_dirname not in err
        assert str(cfg_dir) not in err
        assert "mint a new one" in err

    def test_empty_identity_file_exits_1(self, tmp_path, capsys):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_text("")

        with pytest.raises(SystemExit) as exc_info:
            _mod.ledger_common._resolve_machine_identity("pr-cost", config_dir_override=cfg_dir)

        assert exc_info.value.code == 1
        assert "mint a new one" in capsys.readouterr().err

    def test_non_utf8_identity_file_exits_1(self, tmp_path, capsys):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_bytes(b"\xff\xfe\x00\x01\x02\x03\x04\x05")

        with pytest.raises(SystemExit) as exc_info:
            _mod.ledger_common._resolve_machine_identity("pr-cost", config_dir_override=cfg_dir)

        assert exc_info.value.code == 1
        assert "mint a new one" in capsys.readouterr().err

    def test_dangling_symlink_at_identity_path_exits_1_without_leaking_the_path(self, tmp_path, capsys):
        """Path.exists() returns False for a dangling symlink, so this
        reaches the generate branch, mints a token, and hits os.link's
        FileExistsError against the existing dirent -- the link-collision
        retry path, not a direct "does the file exist" short-circuit."""
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).symlink_to(tmp_path / "nowhere" / "machine-id")

        with pytest.raises(SystemExit) as exc_info:
            _mod.ledger_common._resolve_machine_identity("pr-cost", config_dir_override=cfg_dir)

        assert exc_info.value.code == 1
        assert str(cfg_dir) not in capsys.readouterr().err
        assert list(cfg_dir.glob(f"{_mod.ledger_common._MACHINE_IDENTITY_FILENAME}.*")) == []

    def test_generated_identity_file_mode_is_0600(self, tmp_path):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()

        _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=cfg_dir)

        identity_path = cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME
        assert stat.S_IMODE(identity_path.stat().st_mode) == 0o600

    def test_consent_gate_skipped_account_gets_no_machine_id_file(self, tmp_path, monkeypatch):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()  # acct_b deliberately left without a sentinel
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        assert (acct_a / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()
        assert not (acct_b / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()

    def test_all_accounts_malformed_identity_refusal_names_the_account_not_a_fixed_path(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_b = roots[1].parent
        # acct_a (roots[0].parent) deliberately left without a sentinel, so
        # this run reaches acct_b (which carries the malformed identity
        # file) without also needing acct_a's own corpus data.
        (acct_b / ".pr-cost-enabled").touch()
        (acct_b / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_text("not-hex!!")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=[]))

        args = _pr_cost_args(record=True, all_accounts=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "account-2" in err
        assert "~/.claude/machine-id" not in err
        assert str(acct_b) not in err

    def test_cost_ledger_machine_label_flag_removed_from_parser(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["cost-ledger", "--machine-label", "x"])
        assert exc_info.value.code == 2

    def test_cross_machine_notice_names_row_count_and_stops_after_first_matching_row_cost_ledger(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys,
    ):
        seeded_legacy_rows = [
            _cost_ledger_row(week="2026-W20", machine="legacy1"),
            _cost_ledger_row(week="2026-W21", machine="legacy1"),
        ]
        cost_ledger_file.write_text(
            cost_ledger_file.read_text()
            + "".join(_mod.cost_ledger._format_cost_ledger_row(r) + "\n" for r in seeded_legacy_rows)
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))

        err = capsys.readouterr().err
        assert f"{len(seeded_legacy_rows)} existing row" in err
        assert "safe to sum" in err
        assert "docs/pr-cost.md" in err
        assert "synced" in err and "dotfile" in err

        # A second --record, now under this machine's own identity, must not
        # repeat the notice.
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-08T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 10))
        assert "safe to sum" not in capsys.readouterr().err

    def test_cross_machine_notice_names_row_count_and_stops_after_first_matching_row_pr_cost(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        (tmp_path / ".pr-cost-enabled").touch()
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        seeded_legacy_rows = [_sample_pr_cost_row(pr_number=1, machine="legacy1")]
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, seeded_legacy_rows)
        merged_prs = [{
            "number": 2, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])

        _mod.pr_cost._pr_cost_report(
            _pr_cost_args(record=True, pr=2), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent],
        )

        err = capsys.readouterr().err
        assert f"{len(seeded_legacy_rows)} existing row" in err
        assert "safe to sum" in err

        merged_prs.append({
            "number": 3, "headRefName": "feature-b", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        })
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-b"),
        ])
        _mod.pr_cost._pr_cost_report(
            _pr_cost_args(record=True, pr=3), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent],
        )
        assert "safe to sum" not in capsys.readouterr().err

    def test_no_cross_machine_notice_on_first_ever_record_against_empty_ledger(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys,
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert "safe to sum" not in capsys.readouterr().err

    def test_identity_path_independent_of_overridden_ledger_path_cost_ledger(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        cfg_dir = tmp_path / "isolated-claude-config"
        cfg_dir.mkdir()
        (cfg_dir / ".cost-ledger-enabled").touch()
        monkeypatch.setattr(_mod.ledger_common, "config_dir", lambda: cfg_dir)
        overridden_ledger_dir = tmp_path / "elsewhere"
        overridden_ledger_dir.mkdir()
        monkeypatch.setenv("COST_LEDGER_PATH", str(overridden_ledger_dir / "cost-ledger.md"))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))

        assert (cfg_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()
        assert not (overridden_ledger_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()

    def test_identity_path_independent_of_overridden_ledger_path_pr_cost(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        (tmp_path / ".pr-cost-enabled").touch()
        overridden_ledger_dir = tmp_path / "elsewhere"
        overridden_ledger_dir.mkdir()
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(overridden_ledger_dir / "pr-cost-ledger.tsv"))
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert (tmp_path / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()
        assert not (overridden_ledger_dir / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).exists()

    def test_parser_level_elicitation_surface_closure(self, capsys):
        parser = _mod.build_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["cost-ledger", "--help"])
        cost_ledger_help = capsys.readouterr().out
        assert "--machine-label" not in cost_ledger_help

        with pytest.raises(SystemExit):
            parser.parse_args(["pr-cost", "--help"])
        pr_cost_help = capsys.readouterr().out
        assert "read mode" in pr_cost_help
        assert "Refused" in pr_cost_help and "--record" in pr_cost_help

    def test_cross_account_symlink_escape_hatch_adopts_targets_identity(self, tmp_path):
        real_account = tmp_path / "real-account"
        real_account.mkdir()
        real_identity = _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=real_account)

        other_account = tmp_path / "other-account"
        other_account.mkdir()
        (other_account / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).symlink_to(
            real_account / _mod.ledger_common._MACHINE_IDENTITY_FILENAME
        )

        identity = _mod.ledger_common._resolve_machine_identity("pr-cost", config_dir_override=other_account)

        assert identity == real_identity

    def test_mkstemp_non_collision_oserror_refuses_without_a_path_leak(self, tmp_path, monkeypatch, capsys):
        cfg_dir = tmp_path / "distinctive-cfg-dir-mkstemp"
        cfg_dir.mkdir()

        def raising_mkstemp(*a, **kw):
            # 3-arg OSError sets .filename, matching a real mkstemp failure
            # (e.g. ENOSPC), which embeds the temp-file path -- exercises
            # the actual leak path rather than a filename-less synthetic.
            raise OSError(28, "No space left on device", str(cfg_dir / "mkstemp-tmp-file"))

        monkeypatch.setattr(_mod.tempfile, "mkstemp", raising_mkstemp)

        with pytest.raises(SystemExit) as exc_info:
            _mod.ledger_common._resolve_machine_identity("cost-ledger", config_dir_override=cfg_dir)

        assert exc_info.value.code == 1
        assert str(cfg_dir) not in capsys.readouterr().err
        assert list(cfg_dir.glob(f"{_mod.ledger_common._MACHINE_IDENTITY_FILENAME}.*")) == []

    def test_link_non_collision_oserror_refuses_and_cleans_up_the_temp_file(self, tmp_path, monkeypatch, capsys):
        cfg_dir = tmp_path / "distinctive-cfg-dir-link"
        cfg_dir.mkdir()

        def raising_link(src, dst):
            # 3-arg OSError sets .filename to src, matching a real link()
            # failure -- src is the temp file mkstemp already created
            # inside cfg_dir, so this exercises the actual leak path
            # rather than a filename-less synthetic.
            raise OSError(28, "No space left on device", src)

        monkeypatch.setattr(_mod.os, "link", raising_link)

        with pytest.raises(SystemExit) as exc_info:
            _mod.ledger_common._resolve_machine_identity("pr-cost", config_dir_override=cfg_dir)

        assert exc_info.value.code == 1
        assert str(cfg_dir) not in capsys.readouterr().err
        # Load-bearing here, unlike the mkstemp-failure test above:
        # mkstemp already created a temp file before link() raised, so the
        # finally block's cleanup is what removes it.
        assert list(cfg_dir.glob(f"{_mod.ledger_common._MACHINE_IDENTITY_FILENAME}.*")) == []


class TestLedgerPathIsGitTracked:
    """Direct unit-level calls to _ledger_path_is_git_tracked with a monkeypatched
    subprocess.run, covering its fail-closed branches and two structural cases (ancestor
    walk-up, bare-repo) -- unlike test_transcript_pr_cost.py's git_tracked=True command-level
    deny tests, these exercise the function itself rather than routing through --record."""

    def test_timeout_fails_closed(self, tmp_path, monkeypatch):
        def raising_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=10)

        monkeypatch.setattr(subprocess, "run", raising_run)

        assert _mod.ledger_common._ledger_path_is_git_tracked(tmp_path / "ledger.tsv") is True

    def test_oserror_fails_closed(self, tmp_path, monkeypatch):
        def raising_run(*a, **kw):
            raise OSError("git binary not found")

        monkeypatch.setattr(subprocess, "run", raising_run)

        assert _mod.ledger_common._ledger_path_is_git_tracked(tmp_path / "ledger.tsv") is True

    def test_unexpected_nonzero_exit_without_not_a_git_repository_text_fails_closed(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: unknown option\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _mod.ledger_common._ledger_path_is_git_tracked(tmp_path / "ledger.tsv") is True

    def test_ancestor_walk_up_probes_the_first_existing_directory(self, tmp_path, monkeypatch):
        """ledger_path's own parent, and its grandparent, don't exist -- the walk-up loop must
        land on tmp_path itself (the first existing ancestor) as the -C argument, not on either
        non-existent intermediate directory."""
        ledger_path = tmp_path / "not-yet-created-a" / "not-yet-created-b" / "ledger.tsv"
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = _mod.ledger_common._ledger_path_is_git_tracked(ledger_path)

        assert result is True
        assert calls == [["git", "-C", str(tmp_path), "rev-parse", "--is-inside-work-tree"]]

    def test_bare_repository_is_not_treated_as_git_tracked(self, tmp_path, monkeypatch):
        """returncode == 0 with stdout "false" is git's own bare-repository signal, per this
        function's own docstring: tracked by git but not a work tree, so this is not an
        ambiguous result and returns False rather than failing closed."""
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="false\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _mod.ledger_common._ledger_path_is_git_tracked(tmp_path / "ledger.tsv") is False

    def test_not_a_git_repository_stderr_permits(self, tmp_path, monkeypatch):
        """git's clean "not a git repository" signal is the common case at every real call
        site -- a ledger path legitimately outside any git working tree -- so this is the one
        non-zero-exit branch that permits (False) rather than failing closed."""
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, returncode=128, stdout="",
                stderr="fatal: not a git repository (or any of the parent directories): .git\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _mod.ledger_common._ledger_path_is_git_tracked(tmp_path / "ledger.tsv") is False
