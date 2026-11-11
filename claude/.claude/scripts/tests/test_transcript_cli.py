import argparse
import os
import subprocess
import sys

import pytest
from transcript_analysis import cli, review_cost_by_size, scope

from .conftest import _asst, _write_jsonl


class TestConfigDirFlag:
    """--config-dir reassigns scope.PROJECTS_DIR after argument parsing,
    distinct from the CLAUDE_CONFIG_DIR read when PROJECTS_DIR first resolves
    in a fresh module copy, which
    test_projects_dir_honors_claude_config_dir in test_transcript_scope.py
    covers. Every test here that reaches main()'s reassignment registers
    scope.PROJECTS_DIR with monkeypatch before calling main() (even when just
    re-setting it to its current value; the refusal tests exit first and need
    no registration) so main()'s direct
    `scope.PROJECTS_DIR = ...` reassignment — which bypasses monkeypatch — is
    still restored to its pre-test value at teardown, protecting the rest of
    this shared, once-imported module from leaking state across tests."""

    def test_flag_set_enumerates_sessions_from_the_fixture_dir(self, monkeypatch, tmp_path, capsys):
        """The reassignment's core invariant: a subcommand actually finds
        sessions under PATH/projects, not the module's default. The no-flag
        and error-path tests below can both pass while this one is broken.

        Asserts on captured stdout, not just the PROJECTS_DIR attribute --
        the attribute alone would stay green even if `buckets`' own
        session-iteration path captured PROJECTS_DIR into a local before
        main()'s post-parse reassignment, silently reporting zero sessions
        (the exact regression class this feature exists to close)."""
        monkeypatch.setattr(scope, "PROJECTS_DIR", scope.PROJECTS_DIR)
        config_dir = tmp_path / "other-account"
        proj = config_dir / "projects" / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "s.jsonl", [_asst("claude-sonnet-4-6", branch="feat-config-dir")])

        monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "--config-dir", str(config_dir), "buckets"])
        cli.main()

        assert config_dir / "projects" == scope.PROJECTS_DIR
        out = capsys.readouterr().out
        assert "feat-config-dir" in out, (
            f"the seeded session's branch never surfaced in `buckets` output: {out!r}"
        )

    def test_no_flag_leaves_projects_dir_unchanged_after_parsing(self, monkeypatch, tmp_path):
        """Without --config-dir, main()'s post-parse reassignment must not
        run at all. test_projects_dir_honors_claude_config_dir in
        test_transcript_scope.py only covers the CLAUDE_CONFIG_DIR read when
        PROJECTS_DIR first resolves in a fresh module copy; the regression risk
        this guards is in the reassignment main() performs after parsing,
        e.g. an unguarded `Path(None)`."""
        fixture_projects_dir = tmp_path / "projects"
        fixture_projects_dir.mkdir()
        monkeypatch.setattr(scope, "PROJECTS_DIR", fixture_projects_dir)

        monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "buckets"])
        cli.main()

        assert fixture_projects_dir == scope.PROJECTS_DIR

    def test_this_repo_loud_error_on_zero_matches(self, monkeypatch, tmp_path, capsys):
        """--config-dir + --this-repo, resolved against a config dir with no
        matching project directories, errors loudly instead of silently
        reporting an empty scope -- closes the original reported symptom
        (declaring no sessions exist for a container that has them)."""
        monkeypatch.setattr(scope, "PROJECTS_DIR", scope.PROJECTS_DIR)
        config_dir = tmp_path / "other-account"
        (config_dir / "projects").mkdir(parents=True)  # exists, but empty

        monkeypatch.setattr(os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(config_dir), "buckets", "--this-repo"],
        )
        with pytest.raises(SystemExit):
            cli.main()

        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert "buckets" in err

    @pytest.mark.parametrize("subcommand", scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR)
    def test_top_level_config_dir_refused_for_subcommands_with_their_own(
        self, monkeypatch, tmp_path, capsys, subcommand
    ):
        """Every subcommand in _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR
        refuses the top-level --config-dir outright. Most resolve their own
        scan roots via their own --config-dir (_resolve_cost_roots ->
        config_dir() + declared_transcript_roots()), never reading the
        module-global PROJECTS_DIR this top-level flag reassigns. Letting the
        top-level flag through silently would reassign an unused global while
        the actual scan root stays whatever config_dir() resolves to -- an
        operator typing --config-dir /other-account cost would see no error
        and would silently scan their own default account instead. main()
        refuses the combination outright, matching every other subcommand's
        actually-effective top-level --config-dir. The refusal is
        unconditional on subcommand alone, checked before args.this_repo is
        ever read, so a bare subcommand invocation (no --this-repo) is the
        correct, strictly-scoped regression pin -- a --this-repo variant
        would hit the identical check with no new branch coverage.
        Parametrized directly off the tuple (not a hand-maintained list) so a
        future entry is covered automatically."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        active_config_dir = tmp_path / "active-account"
        (active_config_dir / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active_config_dir))

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(other_account), subcommand],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

        assert exc_info.value.code == 2
        assert other_account / "projects" != scope.PROJECTS_DIR  # refusal happens before reassignment
        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert subcommand in err

    def test_message_recommends_the_subcommands_own_flag_when_it_has_one(self, monkeypatch, tmp_path, capsys):
        """Regression coverage for the other branch of main()'s hasattr
        check: a subcommand that DOES register its own --config-dir (e.g.
        "cost") must still get the "use that instead" recommendation --
        TestConfigDirRouting.test_top_level_config_dir_message_omits_flag_recommendation
        only pins the branch that omits it."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            sys, "argv", ["transcript-analysis.py", "--config-dir", str(other_account), "cost"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "use that instead: transcript-analysis.py cost --config-dir PATH" in err


_REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG = (
    "cost-counts", "period-cost", "review-cost-by-size", "read-cost",
)


def _subcommands_refusing_top_level_config_dir_without_their_own_flag() -> list[str]:
    subparsers_action = next(
        action for action in cli.build_parser()._actions if isinstance(action, argparse._SubParsersAction)
    )
    return [
        name for name in scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR
        if "--config-dir" not in subparsers_action.choices[name]._option_string_actions
    ]


class TestConfigDirRouting:
    def test_context_composition_registered_in_subcommands_with_own_config_dir(self):
        assert "context-composition" in scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR

    def test_refusing_subcommands_without_their_own_config_dir_flag_are_exactly_the_expected_set(self):
        assert set(_subcommands_refusing_top_level_config_dir_without_their_own_flag()) == set(
            _REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG
        )

    @pytest.mark.parametrize("subcommand", _REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG)
    def test_top_level_config_dir_message_omits_flag_recommendation(self, monkeypatch, tmp_path, capsys, subcommand):
        """The members of scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR
        listed in _REFUSING_SUBCOMMANDS_WITHOUT_THEIR_OWN_CONFIG_DIR_FLAG register no
        --config-dir flag of their own -- the shared refusal message must not
        recommend a flag that doesn't exist on their own parsers."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            sys, "argv", ["transcript-analysis.py", "--config-dir", str(other_account), subcommand],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert subcommand in err
        assert "use that instead" not in err
        assert "since this subcommand resolves its scan roots itself" in err

    def test_top_level_config_dir_refused_end_to_end(self, monkeypatch, tmp_path, capsys):
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        active_config_dir = tmp_path / "active-account"
        (active_config_dir / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active_config_dir))

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(other_account), "context-composition"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

        assert exc_info.value.code == 2
        assert other_account / "projects" != scope.PROJECTS_DIR
        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert "context-composition" in err


def test_review_cost_by_size_without_this_repo_is_refused_through_main(monkeypatch, capsys):
    """main() reaches cmd_review_cost_by_size with the parsed Namespace, which refuses before any gh or git call."""

    def fail_on_any_subprocess(cmd, *args, **kwargs):
        pytest.fail(f"refusal must precede every subprocess call, got {cmd!r}")

    monkeypatch.setattr(subprocess, "run", fail_on_any_subprocess)
    monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "review-cost-by-size"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "review-cost-by-size: requires --this-repo" in err


def test_read_cost_without_this_repo_is_refused_through_main(monkeypatch, capsys):
    """main() reaches cmd_read_cost, which refuses a scope without --this-repo before any scan."""
    monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "read-cost"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "read-cost: requires --this-repo" in err


def test_read_cost_with_a_projects_glob_is_refused_through_main(monkeypatch, capsys):
    """argparse accepts --projects alone, so the refusal comes from cmd_read_cost, not the parser.

    The glob uses the --projects=GLOB form because argparse reads a separate "-home-*" token as an option.
    """
    monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "read-cost", "--projects=-home-*"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "read-cost: requires --this-repo and refuses any --projects scope" in err


def test_read_cost_this_repo_runs_to_a_report_through_main(
    fake_projects, stub_repo_tracked_agent_types, monkeypatch, capsys,
):
    """Only read-cost test reaching _resolve_project_scope from parsed --this-repo; any other pre-seeds args._this_repo_slugs."""
    monkeypatch.setattr(scope, "_repo_scoped_project_slugs", lambda *args, **kwargs: [fake_projects.name])
    stub_repo_tracked_agent_types()
    _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6")])
    monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "read-cost", "--this-repo"])

    cli.main()

    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("READ COST SOURCES (this repo (1 project dirs);")
    assert "sessions=1 " in out


class TestBuildParser:
    """build_parser() as a testable seam — the argparse layer without executing
    a subcommand against the real ~/.claude/projects."""

    def test_this_repo_and_projects_mutually_exclusive_on_generic_subcommand(self):
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["buckets", "--this-repo", "--projects", "x"])

    @staticmethod
    def _subcommand_flags(parser):
        """Subcommand name -> its registered option strings, from the real registrar."""
        subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return {name: set(sub._option_string_actions) for name, sub in subparsers.choices.items()}

    def test_subcommands_with_projects_but_no_this_repo_are_the_known_exemptions(self):
        """A subcommand silently losing --this-repo would drop out of the exclusivity walk
        below, so the set that has --projects without it is pinned explicitly."""
        flags = self._subcommand_flags(cli.build_parser())
        assert {name for name, opts in flags.items() if "--projects" in opts and "--this-repo" not in opts} == {"user-input"}

    def test_every_subcommand_with_both_scope_flags_refuses_them_together(self, capsys):
        """Walks the real registrar, so a subcommand registered later is covered automatically.
        Asserts argparse's own refusal text: a SystemExit from a missing required flag would
        otherwise pass for the wrong reason. Collects every offender rather than stopping at the first."""
        parser = cli.build_parser()
        both_flags = [name for name, opts in self._subcommand_flags(parser).items() if {"--projects", "--this-repo"} <= opts]
        # The walk below skips any subcommand that loses either flag, so each name here guards its
        # own registrar: cost via `cli.py`'s `_add_project_scope_args` call, skill-invocation via
        # its hand-rolled group, skill-pair and subagents via their modules' registrars.
        assert {"cost", "skill-invocation", "skill-pair", "subagents"} <= set(both_flags)
        not_refused = []
        for name in both_flags:
            try:
                parser.parse_args([name, "--this-repo", "--projects", "x"])
            except SystemExit:
                refused = "not allowed with argument" in capsys.readouterr().err
            else:
                refused = False
            if not refused:
                not_refused.append(name)
        assert not not_refused, f"accepted both flags or exited for another reason: {not_refused}"

    def test_this_repo_alone_parses_without_error(self):
        parser = cli.build_parser()
        parsed = parser.parse_args(["buckets", "--this-repo"])
        assert parsed.projects == "*"
        assert parsed.this_repo is True

    def test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts(self):
        parser = cli.build_parser()
        with_flag = parser.parse_args(["review-cost-by-size", "--this-repo", "--all-accounts"])
        without_flag = parser.parse_args(["review-cost-by-size", "--this-repo"])
        assert with_flag.func is review_cost_by_size.cmd_review_cost_by_size
        assert with_flag.all_accounts is True
        assert without_flag.func is review_cost_by_size.cmd_review_cost_by_size
        assert without_flag.all_accounts is False

    def test_review_trace_parses_omit_plan_review_spawns(self):
        """The flag is registered, and the default timeline keeps every row without it."""
        parser = cli.build_parser()
        assert parser.parse_args(["review-trace", "--omit-plan-review-spawns"]).omit_plan_review_spawns is True
        assert parser.parse_args(["review-trace"]).omit_plan_review_spawns is False


class TestProjectsFlagHasNoArgparseType:
    @pytest.mark.parametrize(
        "cli_args",
        [["buckets", "--projects", "a/b"], ["skill-invocation", "--projects", "a/b"]],
        ids=["buckets", "skill-invocation"],
    )
    def test_cli_accepts_a_multi_segment_projects_value_at_parse_time(self, cli_args):
        """No subcommand's --projects carries an argparse type=, so
        parsing alone never rejects a nested-directory value -- rejection, when
        it applies, happens later, only under multi-root scope."""
        parser = cli.build_parser()
        args = parser.parse_args(cli_args)
        assert args.projects == "a/b"

    def test_every_projects_registration_across_every_subcommand_has_no_argparse_type(self):
        """Walks every subparser build_parser() registers: --projects's
        one-level restriction must stay runtime-validated, since whether it
        applies depends on how many roots this invocation resolves, not on
        the flag's syntax alone."""
        parser = cli.build_parser()
        subparsers_action = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        projects_registrations = [
            (subcommand, action)
            for subcommand, subparser in subparsers_action.choices.items()
            for action in subparser._actions
            if action.dest == "projects"
        ]
        registered_subcommands = {subcommand for subcommand, _ in projects_registrations}
        assert {"buckets", "skill-invocation"} <= registered_subcommands
        typed = [
            subcommand
            for subcommand, action in projects_registrations
            if action.type is not None
        ]
        assert typed == []
