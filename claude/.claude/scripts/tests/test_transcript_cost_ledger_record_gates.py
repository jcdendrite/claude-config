"""Tests for transcript_analysis/cost_ledger.py's --record refusal gates:
TestCostLedgerPublishSafety and TestCostLedgerSentinelGate -- every path
where --record must refuse and write nothing, plus each gate's boundary
success cases. See test_transcript_cost_ledger.py for the rest of
cost_ledger.py's tests.
"""
import importlib.util
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from .conftest import _cost_ledger_args, _priced, _write_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


class TestCostLedgerPublishSafety:
    def test_record_output_and_file_carry_no_project_or_session_identifiers(
        self, tmp_path, monkeypatch, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """A distinctive project/session marker present in the scanned
        corpus must not reach the ledger file or stdout — cost-ledger's row
        is aggregate-only by construction (no path/session/project field in
        its schema), mirroring #601's cited-path join test shape."""
        projects = tmp_path / "projects"
        proj = projects / "SENTINEL-PROJECT-marker"
        proj.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(proj / "SENTINEL-SESSION-marker.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        out = capsys.readouterr().out
        assert "SENTINEL-PROJECT-marker" not in out
        assert "SENTINEL-SESSION-marker" not in out
        file_text = cost_ledger_file.read_text()
        assert "SENTINEL-PROJECT-marker" not in file_text
        assert "SENTINEL-SESSION-marker" not in file_text

    def test_note_containing_pipe_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note containing '|' is refused outright, since it would
        corrupt the table's row format on write — exercised here with a
        note shaped like it might carry a private project name, the
        highest-risk column per docs/cost-ledger.md. The recorder does not
        re-implement deny-private-project-refs.sh's own blocklist scan;
        that hook covers the actual publish boundary, `git commit`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True, note="acme-corp | internal rollout"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_note_containing_ansi_escape_byte_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note carrying an ANSI/OSC terminal escape sequence is
        refused outright -- cost-ledger's read mode interpolates note
        unescaped into terminal output, and this is the default (no
        sentinel/opt-in) subcommand."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True, note="\x1b]0;PWNED\x07\x1b[2J\x1b[H"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_note_containing_markdown_image_syntax_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note carrying markdown image syntax is refused outright --
        docs/cost-ledger.md is rendered by GitHub, so an image reference
        would beacon an external server on every view."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True, note="![](https://example.com/t.png)"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_note_containing_markdown_link_syntax_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note carrying a plain markdown link (no leading '!') is
        refused outright -- docs/cost-ledger.md is rendered by GitHub, so a
        link would beacon an external server on every view."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True, note="[click here](https://example.com/t)"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before


class TestCostLedgerSentinelGate:
    def test_record_refuses_without_sentinel(self, fake_projects, cost_ledger_file, tmp_path, monkeypatch):
        cfg_dir = tmp_path / "isolated-claude-config-no-sentinel"
        cfg_dir.mkdir()
        monkeypatch.setattr(_mod.cost_ledger, "config_dir", lambda: cfg_dir)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_record_exits_when_config_dir_unresolvable_distinct_from_missing_sentinel(
        self, fake_projects, cost_ledger_file, monkeypatch, capsys,
    ):
        """_config.config_enabled("cost_ledger_recording") returning None
        (config dir unresolvable) is distinguished from a resolved dir that
        simply lacks the sentinel file (test_record_refuses_without_sentinel
        above) -- forces the condition via _config's own config_dir binding,
        the one _config.config_enabled actually reads (see cost_ledger_enabled
        fixture's docstring for why patching _mod's binding has no effect
        on it)."""
        def _raise_value_error():
            raise ValueError("HOME is unset or empty, and CLAUDE_CONFIG_DIR is not set")

        monkeypatch.setattr(_mod._config, "config_dir", _raise_value_error)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code == 1
        assert "could not resolve the Claude Code config directory" in capsys.readouterr().err
        assert cost_ledger_file.read_text() == before

    def test_record_reports_unreadable_schema_when_key_error_and_schema_empty(
        self, fake_projects, cost_ledger_file, monkeypatch, capsys,
    ):
        """_config.config_enabled raising KeyError with an empty schema()
        means config-keys.psv itself was unreadable -- the message must
        name that cause, not an unknown-key bug."""
        def _raise_key_error(key, config_dir_override=None):
            raise KeyError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_key_error)
        monkeypatch.setattr(_mod._config, "schema", lambda: {})
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code == 1
        assert "could not read config-keys.psv" in capsys.readouterr().err

    def test_record_reports_unknown_key_when_key_error_and_schema_populated(
        self, fake_projects, cost_ledger_file, monkeypatch, capsys,
    ):
        """The same KeyError with a non-empty schema() means config-keys.psv
        parsed fine -- a real unknown-key bug at the call site, not the
        stow-relink/git-pull infrastructure cause. The message must name the
        actual key and must not misattribute it to config-keys.psv being
        unreadable."""
        def _raise_key_error(key, config_dir_override=None):
            raise KeyError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_key_error)
        monkeypatch.setattr(_mod._config, "schema", lambda: {"worktree_required": object()})
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "unknown config key" in err
        assert "cost_ledger_recording" in err
        assert "could not read config-keys.psv" not in err

    def test_record_refuses_when_identity_file_content_is_hand_written_well_formed_label(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """A hand-written value well-formed under the wider
        _MACHINE_LABEL_RE (e.g. "tstm1") is not well-formed under
        _MACHINE_IDENTITY_RE (exactly 8 lowercase hex chars) -- the direct
        demonstration that the narrowed class rejects a human-shaped name
        rather than silently adopting it."""
        (cost_ledger_enabled / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_text("tstm1")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before
        err = capsys.readouterr().err
        assert "well-formed" in err

    def test_record_refuses_when_identity_file_content_is_overlong(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        (cost_ledger_enabled / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_text("Too-Long-Label")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0

    def test_record_accepts_identity_file_content_with_trailing_newline(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled,
    ):
        """Python's `$` (without re.MULTILINE) matches immediately before a
        trailing '\\n' as well as end-of-string, so a naive ^...$ pattern
        would let "7e57c0de\\n" slip past _MACHINE_IDENTITY_RE. The strip
        in _read_machine_identity_or_refuse runs first, so a trailing
        newline in a hand-edited or tool-written identity file resolves to
        the stripped, well-formed value rather than being rejected."""
        (cost_ledger_enabled / _mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_text("7e57c0de\n")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert rows[0]["machine"] == "7e57c0de"

    def test_record_refuses_when_multi_root_and_ledger_path_git_tracked(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """--record refuses when a second account is in scope via the
        declared-roots file AND the resolved ledger path sits inside a git
        working tree, appending no row -- refusing this call shape is what
        keeps a union commit from landing in a path git could commit/push."""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert "more than one root is in scope" in capsys.readouterr().err
        assert cost_ledger_file.read_text() == before

    def test_record_succeeds_when_multi_root_and_ledger_path_not_git_tracked(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """The ledger's default path is not git-tracked, so a second
        declared account does not block --record -- only a git-tracked
        destination does (see the git-tracked case above)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cost_ledger._cost_ledger_report(
            _cost_ledger_args(record=True), date(2026, 6, 3)
        )
        capsys.readouterr()
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1

    def test_record_succeeds_when_multi_root_and_ledger_path_in_bare_repo(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """git rev-parse --is-inside-work-tree exits 0 with stdout "false"
        for a bare repository (tracked by git, but not a work tree) -- pins
        that this is treated the same as "not git-tracked", not misrouted
        into the fail-closed branch."""
        subprocess.run(["git", "init", "-q", "--bare"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cost_ledger._cost_ledger_report(
            _cost_ledger_args(record=True), date(2026, 6, 3)
        )
        capsys.readouterr()
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1

    def test_record_refuses_when_git_tracked_check_times_out(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A timed-out git-tracked check fails closed (refuses) rather than
        treating a hung check as "not tracked"."""
        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=10)
        monkeypatch.setattr(_mod.subprocess, "run", _raise_timeout)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_git_tracked_check_binary_missing(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A missing git binary (FileNotFoundError, e.g. git absent from
        PATH) fails closed (refuses) rather than raising past both except
        clauses uncaught."""
        def _raise_not_found(*args, **kwargs):
            raise FileNotFoundError("git")
        monkeypatch.setattr(_mod.subprocess, "run", _raise_not_found)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_git_tracked_check_stderr_has_invalid_utf8(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """Non-UTF-8 bytes on the git-tracked check's stderr (e.g. a
        non-ASCII ancestor path in a permission-denied message) decode via
        errors="replace" rather than raising UnicodeDecodeError uncaught,
        which would otherwise crash --record instead of failing closed. A
        fake `git` on PATH emits invalid UTF-8 so this doesn't depend on the
        host's locale or filesystem permission semantics."""
        fake_bin = tmp_path / "fake-git-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text("#!/bin/sh\nprintf '\\377\\376 permission denied\\n' >&2\nexit 128\n")
        fake_git.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_git_tracked_check_exits_nonzero_unexpectedly(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A non-zero git exit whose stderr doesn't match the expected "not
        a git repository" text fails closed -- the branch most likely to
        silently flip if that stderr text ever changes."""
        def _fake_permission_denied(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: permission denied\n")
        monkeypatch.setattr(_mod.subprocess, "run", _fake_permission_denied)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_multi_root_and_ledger_path_in_linked_worktree(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A linked worktree's directory contains a `.git` file (a worktree
        pointer), not a `.git` directory -- this repo's own convention
        (.claude/worktrees/<branch>/) makes that the dominant real-world
        layout, and no other case here exercises it."""
        main_repo = tmp_path / "main-repo"
        subprocess.run(["git", "init", "-q", str(main_repo)], check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=main_repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=main_repo, check=True)
        (main_repo / "README.md").write_text("x\n")
        subprocess.run(["git", "add", "README.md"], cwd=main_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=main_repo, check=True)
        worktree_dir = tmp_path / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(worktree_dir), "-b", "wt-branch"],
            cwd=main_repo, check=True,
        )
        ledger_path = worktree_dir / "cost-ledger.md"
        ledger_path.write_text(
            "# Cost-trend ledger\n\n"
            + _mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n"
            + _mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
        )
        monkeypatch.setattr(_mod.cost_ledger, "_cost_ledger_path", lambda: ledger_path)

        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = ledger_path.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert ledger_path.read_text() == before

    def test_record_refuses_when_multi_root_and_ledger_path_git_tracked_even_with_force(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """Regression: --force skips the duplicate-(week, machine)-row check
        in _upsert_cost_ledger_row, not the multi-root git-tracked refusal
        above it -- pins that ordering against a future change that
        special-cases --force to bypass this guard too."""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True, force=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_succeeds_when_single_root_and_ledger_path_git_tracked(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path
    ):
        """The git-tracked check only runs when more than one root is in
        scope -- a single declared account still succeeds against a
        git-tracked ledger path, pinning that boundary against a future
        edit to the `len(roots) > 1 and ...` guard."""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(
            _cost_ledger_args(record=True), date(2026, 6, 3)
        )
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1

    def test_record_not_redirected_by_inherited_git_dir_env(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """An operator's shell exporting GIT_DIR/GIT_WORK_TREE for an
        unrelated repo must not redirect the git-tracked check to that
        repo's tracked status -- the check has to see the ledger path's own
        (untracked) ancestor, not whatever the caller's env points at.
        GIT_WORK_TREE is set to the ledger's own ancestor (not a sibling
        directory) specifically so an unstripped env would answer "true"
        (wrongly tracked) while the stripped env correctly answers "false" --
        a sibling GIT_WORK_TREE answers "false" either way and wouldn't
        discriminate the two behaviors."""
        unrelated_repo = tmp_path / "unrelated-repo"
        unrelated_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=unrelated_repo, check=True)
        monkeypatch.setenv("GIT_DIR", str(unrelated_repo / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cost_ledger._cost_ledger_report(
            _cost_ledger_args(record=True), date(2026, 6, 3)
        )
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
