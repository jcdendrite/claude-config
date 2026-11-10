"""Tests for the read-cost command's argparse wiring, scope refusal, printed scope header and scan-root resolution."""
import argparse

import pytest
from transcript_analysis import read_cost, scope

from ._read_cost_helpers import (
    _PROJECT_SLUG,
    _read_cost_args,
    _turn,
)
from .conftest import (
    _write_jsonl,
)


class TestReadCostArgparseWiring:
    """Round-trips the real registrar; a hand-built Namespace misses `dest=` typos, missing `set_defaults`, and flag drift."""

    def test_read_cost_wires_to_cmd_read_cost_with_this_repo(self):
        parser = argparse.ArgumentParser()
        read_cost._add_read_cost_subparser(parser.add_subparsers())
        parsed = parser.parse_args(["read-cost", "--this-repo"])
        assert parsed.func is read_cost.cmd_read_cost
        assert parsed.this_repo is True


class TestScopeRefusal:
    def test_refused_when_more_than_one_root_is_in_scope(self, fake_projects, tmp_path, capsys):
        other_root = tmp_path / "other-account" / "projects"
        other_root.mkdir(parents=True)
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_report(_read_cost_args(), [fake_projects.parent, other_root])
        assert raised.value.code == 2
        captured = capsys.readouterr()
        assert "more than one root" in captured.err
        assert captured.out == ""

    def test_refused_without_this_repo(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_report(_read_cost_args(this_repo=False), [fake_projects.parent])
        assert raised.value.code == 2
        assert "requires --this-repo" in capsys.readouterr().err

    def test_refused_with_a_projects_glob(self, fake_projects, capsys):
        # The parser's mutually exclusive group never yields this Namespace.
        # The `projects ... not in (None, "*")` test in `_refuse_wider_scope` is defense-in-depth for direct callers.
        # The explicit `this_repo=True` keeps the other refusal leg out of play, so only the projects leg can refuse.
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_report(_read_cost_args(projects="-home-*", this_repo=True), [fake_projects.parent])
        assert raised.value.code == 2
        assert "refuses any --projects scope" in capsys.readouterr().err

    def test_cmd_scans_the_active_account_alone_even_when_other_roots_are_declared(
        self, fake_projects, stub_repo_tracked_agent_types, tmp_path, monkeypatch, capsys,
    ):
        stub_repo_tracked_agent_types()
        declared = tmp_path / "declared-account"
        (declared / "projects" / _PROJECT_SLUG).mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_jsonl(declared / "projects" / _PROJECT_SLUG / "other.jsonl", [_turn("m9")])

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "sessions=1 " in out

    def test_header_states_the_active_account_the_working_directorys_repository_and_the_unconsulted_roots_file(
        self, fake_projects, stub_repo_tracked_agent_types, capsys,
    ):
        stub_repo_tracked_agent_types()
        read_cost.cmd_read_cost(_read_cost_args())

        assert capsys.readouterr().out.splitlines()[0] == (
            "READ COST SOURCES (this repo (1 project dirs); reads one account (the active account),"
            " the working directory's repository only; ~/.claude/transcript-config-dirs not consulted)"
        )

    def test_roots_are_the_active_config_dirs_projects_directory(self, fake_projects):
        assert read_cost._read_cost_roots() == [fake_projects.parent]

    def test_unresolvable_config_dir_exits_2(self, monkeypatch, capsys):
        def unresolvable_config_dir():
            raise ValueError("CLAUDE_CONFIG_DIR is not absolute")

        monkeypatch.setattr(scope, "config_dir", unresolvable_config_dir)
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_roots()
        assert raised.value.code == 2
        assert capsys.readouterr().err == "read-cost: CLAUDE_CONFIG_DIR is not absolute\n"
