"""Tests for transcript-analysis.py."""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import SKILLS_DIR
from transcript_analysis import (
    audit_routing,
    buckets,
    cache_efficiency,
    cache_rebuild,
    commit_gate,
    context_composition,
    context_distribution,
    corpus,
    cost,
    cost_ledger,
    duration,
    edit_format,
    fail_seq,
    instrument_authoring,
    judgment_pair,
    plan_boundary,
    pr_link,
    read_scope,
    rearm_backtest,
    review_cost_by_size,
    review_trace,
    reviewer_yield,
    scope,
    sessions,
    skill_invocation,
    skill_pair,
    spend_over_threshold,
    struggle,
    subagent_mix,
    subagents,
    user_input,
)

from ._audit_routing_helpers import _audit_routing_samples_args, _audit_routing_shape_args
from ._cache_efficiency_helpers import _cache_efficiency_args
from ._cache_rebuild_helpers import _cache_rebuild_args
from ._commit_gate_helpers import _gate_args
from ._handoff_nudge_helpers import _rearm_backtest_args, _spend_over_threshold_args
from ._session_signal_helpers import _SONNET, _branch_args, _ui_user, _user_input_args
from ._skill_helpers import (
    _TS_FIXED,
    _judgment_pair_args,
    _review_asst,
    _skill_inv_args,
    _skill_pair_args,
    _user_reply,
)
from ._subagent_helpers import _subagents_args
from .conftest import (
    _agent_use,
    _asst,
    _attr_ts,
    _audit_routing_args,
    _context_distribution_args,
    _cost_args,
    _cost_ledger_args,
    _cost_trend_args,
    _extract_grand_total,
    _opus,
    _priced,
    _read_use,
    _review_trace_args,
    _reviewer_yield_args,
    _skill_use,
    _skill_use_rec,
    _table_cols,
    _two_declared_roots,
    _user_msg,
    _write_jsonl,
    _write_subagent_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


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
        _mod.main()

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
        _mod.main()

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
            _mod.main()

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
            _mod.main()

        assert exc_info.value.code == 2
        assert other_account / "projects" != scope.PROJECTS_DIR  # refusal happens before reassignment
        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert subcommand in err

    def test_message_recommends_the_subcommands_own_flag_when_it_has_one(self, monkeypatch, tmp_path, capsys):
        """Regression coverage for the other branch of main()'s hasattr
        check: a subcommand that DOES register its own --config-dir (e.g.
        "cost") must still get the "use that instead" recommendation --
        test_transcript_cost_counts.py's
        test_top_level_config_dir_message_omits_flag_recommendation only pins
        the branch that omits it."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            sys, "argv", ["transcript-analysis.py", "--config-dir", str(other_account), "cost"],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "use that instead: transcript-analysis.py cost --config-dir PATH" in err


class TestConfigDirRouting:
    def test_context_composition_registered_in_subcommands_with_own_config_dir(self):
        assert "context-composition" in scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR

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
            _mod.main()

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
        _mod.main()

    assert exc_info.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "review-cost-by-size: requires --this-repo" in err


# ---------------------------------------------------------------------------
# _table_cols self-tests
# ---------------------------------------------------------------------------


class TestTableColsHelper:
    """Unit tests for the _table_cols helper — verifies the safety properties
    the refactor depends on."""

    def test_basic_column_lookup(self):
        out = "Branch Lead Main\nfeat   1    0  \n"
        cols = _table_cols(out, header_contains="Lead", row_contains="feat")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"

    def test_drop_leading_labels(self):
        out = "Branch Thread Opus Sonnet\nmain   main   1    2\n       sidechain 0  1\n"
        # sidechain row has Branch suppressed — one fewer leading token
        cols = _table_cols(out, header_contains="Thread", row_contains="sidechain",
                           drop_leading_labels=1)
        assert cols["Thread"] == "sidechain"
        assert cols["Opus"] == "0"
        assert cols["Sonnet"] == "1"

    def test_row_not_unique_raises(self):
        out = "Branch Lead\nfeat   1\nfeat   2\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_row_too_few_tokens_raises(self):
        out = "Branch Lead Main\nfeat   1\n"
        with pytest.raises(AssertionError, match="fewer cells"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_max_labels_excludes_trailing_multiword_header(self):
        # "Top subagent types" is 3 header tokens but 1 data token (—); max_labels=4
        # limits to Branch/Sess/Spawns/CR so the assertion len(values)>=len(labels) passes.
        out = "Branch Sess Spawns CR Top subagent types\nfeat   1    0      2 —\n"
        cols = _table_cols(out, header_contains="Spawns", row_contains="feat", max_labels=4)
        assert cols["Spawns"] == "0"
        assert cols["CR"] == "2"

    def test_row_startswith_excludes_indented_lines(self):
        # "code-review" appears at column 0 in the data row and indented in the summary.
        out = "skill  top-level user-slash\ncode-review  1  0\n  code-review (1 top)\n"
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review",
                           row_startswith=True)
        assert cols["top-level"] == "1"
        assert cols["user-slash"] == "0"

    def test_header_not_unique_raises(self):
        # Two lines both contain "Lead" → header ambiguity must raise, not silently pick one.
        out = "Bin Lead Main\n2026-W20 1 0\nLead row duplicate 0 1\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="2026-W20")

    def test_occurrence_scoping_excludes_content_after_the_selected_block(self):
        """occurrence=N must stop its row/header search at its own table's
        boundary (next blank line or next header-containing line), not
        search to the end of `out` — every existing occurrence= call site
        (reviewer-yield's Table 2, always the last table printed, with a
        composite row_contains already globally unique) would stay green
        even if that boundary were dropped, since there is nothing after
        Table 2 for an unscoped search to leak into. This fixture puts a
        second occurrence directly after the first's true boundary, with a
        row that would also satisfy `row_contains` if the boundary weren't
        enforced — occurrence=1 must select only the first table's row."""
        out = "\n".join([
            "Header      Count",
            "------------------",
            "alpha           1",
            "",
            "Header      Count",
            "------------------",
            "alpha           2",
        ])
        cols = _table_cols(out, header_contains="Header", row_contains="alpha", occurrence=1)
        assert cols["Count"] == "1"


# ---------------------------------------------------------------------------
# cache-efficiency
# ---------------------------------------------------------------------------


class TestCacheEfficiencyArgparseWiring:
    """Round-trips the real argparser, mirroring
    TestPlanBoundaryArgparseWiring -- every other new test in this section
    builds args via the hand-rolled _cache_efficiency_args() factory, which
    cannot catch a dest= typo or missing set_defaults in the real parser."""

    def test_registers_cache_efficiency_subcommand_with_expected_defaults(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["cache-efficiency"])
        assert args.extra_config_dirs is None
        assert args.no_redact is False
        assert args.func == cache_efficiency.cmd_cache_efficiency

    def test_config_dir_and_no_redact_wire_to_expected_attributes(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["cache-efficiency", "--config-dir", "X", "--no-redact"])
        assert args.func is cache_efficiency.cmd_cache_efficiency
        assert args.extra_config_dirs == ["X"]
        assert args.no_redact is True


class TestCacheRebuildCrossInstrumentReconciliation:
    """Verification item 10: cache-rebuild's new sidechain W5m accumulator
    must agree exactly with cache-efficiency's own sidechain Write5m column
    on an identical fixture -- both dedup by requestId per group and
    classify origin off the identical isSidechain expression, so a
    disagreement means the new accumulator has a bug, not that the corpus
    is odd."""

    def test_sidechain_w5m_matches_cache_efficiency_sidechain_write5m_on_identical_fixture(
        self, fake_projects, capsys
    ):
        session_id = "sess-reconcile"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
        ])
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_5m=75_000, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=125_000,
                ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        for rec in subagent_records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        rebuild_out = capsys.readouterr().out
        rebuild_row = _table_cols(rebuild_out, header_contains="Ratio", row_contains="subagent")

        cache_efficiency._cache_efficiency_report(_cache_efficiency_args(), roots=[fake_projects.parent])
        efficiency_out = capsys.readouterr().out
        efficiency_row = _table_cols(efficiency_out, header_contains="Write5m", row_contains="sidechain")

        assert rebuild_row["W5m"] == efficiency_row["Write5m"]


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    """build_parser() as a testable seam — the argparse layer without executing
    a subcommand against the real ~/.claude/projects."""

    def test_this_repo_and_projects_mutually_exclusive_on_generic_subcommand(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["buckets", "--this-repo", "--projects", "x"])

    def test_this_repo_and_projects_mutually_exclusive_on_skill_invocation(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["skill-invocation", "--this-repo", "--projects", "x"])

    def test_bare_skill_invocation_leaves_projects_unset(self):
        """None, not "*", is what makes skill-invocation default to this repo's worktrees."""
        parsed = _mod.build_parser().parse_args(["skill-invocation"])
        assert parsed.projects is None

    @staticmethod
    def _subcommand_flags(parser):
        """Subcommand name -> its registered option strings, from the real registrar."""
        subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return {name: set(sub._option_string_actions) for name, sub in subparsers.choices.items()}

    def test_subcommands_with_projects_but_no_this_repo_are_the_known_exemptions(self):
        """A subcommand silently losing --this-repo would drop out of the exclusivity walk
        below, so the set that has --projects without it is pinned explicitly."""
        flags = self._subcommand_flags(_mod.build_parser())
        assert {name for name, opts in flags.items() if "--projects" in opts and "--this-repo" not in opts} == {"user-input"}

    def test_every_subcommand_with_both_scope_flags_refuses_them_together(self, capsys):
        """Walks the real registrar, so a subcommand registered later is covered automatically.
        Asserts argparse's own refusal text: a SystemExit from a missing required flag would
        otherwise pass for the wrong reason. Collects every offender rather than stopping at the first."""
        parser = _mod.build_parser()
        both_flags = [name for name, opts in self._subcommand_flags(parser).items() if {"--projects", "--this-repo"} <= opts]
        # One subcommand per registration path: the shim's helper call, the hand-rolled group
        # in skill_invocation.py, and a module-registered subparser.
        assert {"subagents", "skill-invocation", "skill-pair"} <= set(both_flags)
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
        parser = _mod.build_parser()
        parsed = parser.parse_args(["buckets", "--this-repo"])
        assert parsed.projects == "*"
        assert parsed.this_repo is True

    def test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts(self):
        parser = _mod.build_parser()
        with_flag = parser.parse_args(["review-cost-by-size", "--this-repo", "--all-accounts"])
        without_flag = parser.parse_args(["review-cost-by-size", "--this-repo"])
        assert with_flag.func is review_cost_by_size.cmd_review_cost_by_size
        assert with_flag.all_accounts is True
        assert without_flag.func is review_cost_by_size.cmd_review_cost_by_size
        assert without_flag.all_accounts is False

    def test_pr_link_without_repo_flag_defaults_to_none(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["pr-link", "--branches", "feat-y"])
        assert parsed.repo is None

    def test_pr_link_with_repo_flag_parses_value(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["pr-link", "--branches", "feat-y", "--repo", "owner/repo"])
        assert parsed.repo == "owner/repo"


# ---------------------------------------------------------------------------
# format-drift canary
# ---------------------------------------------------------------------------


class TestFormatDriftCanary:
    def test_drift_warning_emitted_when_spawns_but_no_sidechain_turns(
        self, fake_projects, capsys
    ):
        """Spawn tool_use with no subagent files → WARNING on stderr."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        subagents.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "isSidechain" in captured.err
        # The warning must not bleed into stdout table rows.
        assert "WARNING" not in captured.out

    def test_no_warning_when_no_spawns(self, fake_projects, capsys):
        """No Agent/Task tool_uses → no drift warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main"),
        ])
        subagents.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_old_inlined_format(self, fake_projects, capsys):
        """Agent spawn + isSidechain records inlined in the top-level file → no warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        subagents.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_drift_warning_also_fires_in_skill_pair(self, fake_projects, capsys):
        """cmd_skill_pair also emits the drift warning when spawns have no sidechain turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        assert "WARNING" in capsys.readouterr().err

    def test_subagent_records_without_issidechain_field_treated_as_main_thread(
        self, fake_projects, capsys
    ):
        """Subagent records missing the isSidechain field are counted as main-thread.

        This documents the failure mode if the transcript format drifts to drop
        isSidechain — subagent turns silently appear in the main row, not sidechain.
        """
        session_id = "sess-no-sidechain-field"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "check-runner"),
            ]),
        ])
        # Subagent file record WITHOUT isSidechain field.
        subdir = fake_projects / session_id / corpus.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        rec = _asst("claude-sonnet-4-6", branch="main", sidechain=True)
        del rec["isSidechain"]  # simulate format drift
        (subdir / "agent-drifted.jsonl").write_text(json.dumps(rec) + "\n")
        subagents.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        sidechain_lines = [ln for ln in captured.out.splitlines() if "sidechain" in ln]
        # Without isSidechain=True, the subagent record is not a sidechain turn.
        assert not sidechain_lines, (
            "subagent records without isSidechain field must not appear in sidechain row"
        )
        # The drift canary fires: spawns=1 (main-thread Agent), sidechain_turns=0
        # (subagent record has no isSidechain field → not counted as sidechain).
        assert "WARNING" in captured.err

    def test_drift_warning_also_fires_in_cache_efficiency(self, fake_projects, capsys):
        """cmd_cache_efficiency also emits the drift warning when spawns
        have no sidechain turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        cache_efficiency.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" in capsys.readouterr().err

    def test_no_warning_in_cache_efficiency_on_healthy_corpus(self, fake_projects, capsys):
        """The negative counterpart for cmd_cache_efficiency: no spawns, no
        warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", cache_read=100),
        ])
        cache_efficiency.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_in_cache_efficiency_with_spawn_and_real_sidechain_turn(self, fake_projects, capsys):
        """The true no-drift case: a spawn paired with an actual sidechain
        assistant turn in the subagents/ file. The spawn-only case above
        (sidechain=0) and the no-spawn case (sidechain=0) both leave
        total_sidechain_turns at 0 regardless of whether
        _scan_cache_efficiency_group's return value is summed correctly --
        only this spawn=1/sidechain=1 case proves the accumulation actually
        works, since a dropped or off-by-one return value would still print
        no warning in the other two cases but would wrongly warn here."""
        session_id = "sess-side"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        side_rec = _priced("claude-sonnet-5", cache_read=100)
        side_rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "a1", [side_rec])
        cache_efficiency.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_in_cache_efficiency_with_spawn_and_unpriced_sidechain_turn(self, fake_projects, capsys):
        """The cache-efficiency counterpart to
        TestCostFormatDriftCanary.test_no_warning_in_cost_with_spawn_and_unpriced_sidechain_turn
        (test_transcript_cost.py): an unpriced sidechain assistant turn must
        still count toward
        _scan_cache_efficiency_group's returned sidechain_turns_read, since
        that count happens before the group scan's own `if not usage:
        continue` guard."""
        session_id = "sess-unpriced-side-ce"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "a1", [
            _asst("claude-opus-4-7", branch="main", sidechain=True),
        ])
        cache_efficiency.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Multi-account scope (transcript-corpus-multi-account-scope plan) —
# cross-subcommand resolved-scope-header and roots-threading coverage.
# ---------------------------------------------------------------------------


def _fake_gh_pr_list_run(cmd, *a, **k):
    """A no-op `gh` double for subcommands (pr-link) whose branch-iteration
    loop shells out regardless of session content."""
    if cmd[:2] == ["gh", "pr"]:
        return subprocess.CompletedProcess(cmd, 0, "[]", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


# (cli_name, header_name, cmd_func, zero-arg args factory) for the
# subcommands whose resolved-scope header prints unconditionally, even over
# an empty scope. review-trace and skill-invocation print unconditionally
# too, but carry zero-match message text and branches the other rows don't, so
# they get their own tests below rather than a row here.
_UNCONDITIONAL_HEADER_CASES: list[tuple[str, str, object, object]] = [
    ("buckets", "BUCKETS", buckets.cmd_buckets,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("fail-seq", "FAIL SEQ", fail_seq.cmd_fail_seq,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": "main"})()),
    ("struggle", "STRUGGLE", struggle.cmd_struggle,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("duration", "DURATION", duration.cmd_duration,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("subagents", "SUBAGENTS", subagents.cmd_subagents, _subagents_args),
    ("subagent-mix", "SUBAGENT MIX", subagent_mix.cmd_subagent_mix,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()),
    ("reviewer-yield", "REVIEWER YIELD", reviewer_yield.cmd_reviewer_yield, _reviewer_yield_args),
    ("skill-pair", "SKILL PAIR", skill_pair.cmd_skill_pair, _skill_pair_args),
    ("pr-link", "PR LINK", pr_link.cmd_pr_link,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "branches": "main",
         "repo": "owner/repo", "author": None,
     })()),
    ("commit-gate", "COMMIT GATE", commit_gate.cmd_commit_gate, lambda: _gate_args("code-review")),
    ("audit-routing", "AUDIT ROUTING", audit_routing.cmd_audit_routing, _audit_routing_args),
    ("cost", "COST", cost.cmd_cost, _cost_args),
    ("context-distribution", "CONTEXT DISTRIBUTION", context_distribution.cmd_context_distribution, _context_distribution_args),
    ("context-composition", "CONTEXT COMPOSITION", context_composition.cmd_context_composition,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "since": None, "no_redact": False, "extra_config_dirs": None,
     })()),
    ("cost-trend", "COST TREND", cost.cmd_cost_trend, _cost_trend_args),
    ("cache-rebuild", "CACHE REBUILD", cache_rebuild.cmd_cache_rebuild, _cache_rebuild_args),
    ("spend-over-threshold", "SPEND OVER THRESHOLD", spend_over_threshold.cmd_spend_over_threshold, _spend_over_threshold_args),
    ("rearm-backtest", "REARM BACKTEST", rearm_backtest.cmd_rearm_backtest, _rearm_backtest_args),
    ("audit-routing-shape", "AUDIT ROUTING SHAPE", audit_routing.cmd_audit_routing_shape, _audit_routing_shape_args),
    ("audit-routing-samples", "AUDIT ROUTING SAMPLES", audit_routing.cmd_audit_routing_samples, _audit_routing_samples_args),
    ("judgment-pair", "JUDGMENT PAIR", judgment_pair.cmd_judgment_pair, _judgment_pair_args),
    ("edit-format", "EDIT FORMAT", edit_format.cmd_edit_format,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "no_redact": False, "extra_config_dirs": None})()),
    ("read-scope", "READ SCOPE", read_scope.cmd_read_scope,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "no_redact": False, "extra_config_dirs": None})()),
    ("instrument-authoring", "INSTRUMENT AUTHORING", instrument_authoring.cmd_instrument_authoring,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "extra_config_dirs": None})()),
    ("cost-ledger", "COST LEDGER", cost_ledger.cmd_cost_ledger, _cost_ledger_args),
    ("plan-boundary", "PLAN BOUNDARY", plan_boundary.cmd_plan_boundary,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "since": None, "no_redact": False, "extra_config_dirs": None,
     })()),
    ("sessions", "SESSIONS", sessions.cmd_sessions,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "paths": True, "include_subagents": False,
     })()),
]


class TestAllSubcommandsSingleRootHeader:
    """Across every funnel subcommand, the resolved-scope header states the
    root count unconditionally — even at one root with nothing declared, the
    exact state that produced the original corpus undercount — and no
    per-root stderr progress line prints at one root (that line is gated on
    len(roots) > 1)."""

    _HEADER_SUFFIX = "1 root (no ~/.claude/transcript-config-dirs declared))"

    @pytest.mark.parametrize("subcommand,header_name,cmd_func,args_factory", _UNCONDITIONAL_HEADER_CASES)
    def test_header_states_one_root_and_no_progress_line_prints(
        self, fake_projects, cost_ledger_file, monkeypatch, capsys, subcommand, header_name, cmd_func, args_factory
    ):
        monkeypatch.setattr(subprocess, "run", _fake_gh_pr_list_run)
        cmd_func(args_factory())
        out, err = capsys.readouterr()
        combined = out + err
        assert f"{header_name} SOURCES (" in combined, f"{subcommand}: no resolved-scope header printed"
        assert self._HEADER_SUFFIX in combined, f"{subcommand}: header missing the unconditional root-count suffix"
        assert "scanning root" not in combined, f"{subcommand}: a single-root run must not print a per-root progress line"

    def test_review_trace_header_states_one_root_once_a_session_matches(self, fake_projects, capsys):
        """review-trace prints its header before the scan regardless of match
        count; seed one qualifying session so the matched path is covered here
        (the zero-match path has its own test)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        review_trace.cmd_review_trace(_review_trace_args())
        out, err = capsys.readouterr()
        combined = out + err
        assert "REVIEW TRACE SOURCES (" in combined
        assert self._HEADER_SUFFIX in combined
        assert "scanning root" not in combined

    def test_skill_invocation_header_states_one_root_once_a_skill_matches(self, fake_projects, capsys):
        """skill-invocation prints its header before the zero-match return;
        this covers the matched path (the zero-match path has its own test)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        skill_invocation.cmd_skill_invocation(_skill_inv_args())
        out, err = capsys.readouterr()
        combined = out + err
        assert "SKILL INVOCATION SOURCES (" in combined
        assert self._HEADER_SUFFIX in combined
        assert "scanning root" not in combined

    def test_review_trace_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """A zero-match run is the case the header exists for: at one root no
        per-root progress line prints either, so without this the run is
        byte-for-byte silent and a mis-scoped scan reads as a genuine empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        review_trace.cmd_review_trace(_review_trace_args(branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "REVIEW TRACE SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No sessions matched in scope." in out
        assert "### " not in out, "no session block should be emitted on a zero-match run"
        assert "scanning root" not in out + err

    def test_review_trace_deny_summary_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """--deny-summary's third state — scope matched no sessions at all, as
        distinct from matching sessions that carried no denial."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        review_trace.cmd_review_trace(_review_trace_args(deny_summary=True, branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "REVIEW TRACE SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No sessions matched in scope." in out
        assert "No denials found in scope." not in out, (
            "zero sessions matched is a different state from matched-but-no-denials"
        )
        assert "scanning root" not in out + err

    def test_skill_invocation_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """The not-found message alone cannot distinguish a wrongly-scoped scan
        from a correctly-scoped empty one; the header is what separates them."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        skill_invocation.cmd_skill_invocation(_skill_inv_args(branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "SKILL INVOCATION SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No skill invocations found." in out
        assert "scanning root" not in out + err

    def test_skill_invocation_zero_match_header_names_the_subagent_thread_scope(
        self, fake_projects, capsys
    ):
        """The thread-scope clause is part of what a zero-match run must disclose —
        'searched main+subagents and found nothing' is a different claim from
        'searched the main thread and found nothing'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        skill_invocation.cmd_skill_invocation(
            _skill_inv_args(branches="no-such-branch", include_subagents=True)
        )
        out = capsys.readouterr().out
        assert "main+subagents" in out
        assert "No skill invocations found." in out

    _DISTINCTIVE_GLOB = "-home-user-somerepo-worktrees-somebranch"

    def test_review_trace_zero_match_header_echoes_an_explicit_projects_glob_verbatim(
        self, fake_projects, capsys
    ):
        """review-trace's scope label IS the glob, so an operator's own --projects
        value reaches stdout even at zero matches — the reason the section's
        not-publish-safe warning has to cover empty runs. Pinned so a later
        'redact the empty case to be safe' change breaks a test, not a promise."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        review_trace.cmd_review_trace(
            _review_trace_args(projects=self._DISTINCTIVE_GLOB, branches="no-such-branch")
        )
        out = capsys.readouterr().out
        assert "REVIEW TRACE SOURCES (" in out
        assert self._DISTINCTIVE_GLOB in out

    def test_skill_invocation_zero_match_header_does_not_echo_the_projects_glob(
        self, fake_projects, capsys
    ):
        """skill-invocation labels the escape hatch ('explicit --projects
        (not repo-scoped)') instead of interpolating the glob, so its output stays
        free of the operator's path even under --projects. That divergence from
        review-trace is a minimization property, not an oversight."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        skill_invocation.cmd_skill_invocation(
            _skill_inv_args(projects=self._DISTINCTIVE_GLOB, branches="no-such-branch")
        )
        out = capsys.readouterr().out
        assert "explicit --projects (not repo-scoped)" in out
        assert self._DISTINCTIVE_GLOB not in out

    def test_review_trace_deny_only_does_not_mask_which_zero_match_state_was_reached(
        self, fake_projects, capsys
    ):
        """`any_session_matched` is set before the --deny-only skip, so a run whose
        sessions matched but carried no denial must still say 'No denials found',
        not 'No sessions matched'. Moving that assignment below the skip would
        silently misreport scope coverage as empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        review_trace.cmd_review_trace(_review_trace_args(deny_summary=True, deny_only=True))
        out = capsys.readouterr().out
        assert "No denials found in scope." in out
        assert "No sessions matched in scope." not in out


class TestRootsThreadingSpy:
    """Every _resolve_project_scope call site threads the SAME `roots` list
    to both scope resolution and the header call —
    format-independent, catches a subcommand whose roots list isn't threaded
    identically to both call sites, regardless of what it prints. Wraps (not
    replaces) the real functions, so each subcommand's normal behavior and
    assertions from the header test above still apply; only the call
    arguments are additionally recorded."""

    @pytest.mark.parametrize("subcommand,header_name,cmd_func,args_factory", _UNCONDITIONAL_HEADER_CASES)
    def test_resolve_project_scope_and_header_receive_identical_multi_root_list(
        self, tmp_path, cost_ledger_file, monkeypatch, subcommand, header_name, cmd_func, args_factory
    ):
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        scope_calls: list[list] = []
        header_calls: list[list] = []
        real_resolve = scope._resolve_project_scope
        real_print = scope.print_resolved_scope

        def spy_resolve(*a, **k):
            scope_calls.append(list(k["roots"]) if k.get("roots") is not None else None)
            return real_resolve(*a, **k)

        def spy_print(*a, **k):
            roots = a[2] if len(a) > 2 else k.get("roots")
            header_calls.append(list(roots))
            return real_print(*a, **k)

        monkeypatch.setattr(scope, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(scope, "print_resolved_scope", spy_print)
        monkeypatch.setattr(subprocess, "run", _fake_gh_pr_list_run)

        cmd_func(args_factory())

        assert scope_calls, f"{subcommand}: _resolve_project_scope was never called"
        assert header_calls, f"{subcommand}: print_resolved_scope was never called"
        assert scope_calls[-1] == expected_roots, (
            f"{subcommand}: scope resolution did not receive the multi-root list — {scope_calls[-1]!r}"
        )
        assert header_calls[-1] == expected_roots, (
            f"{subcommand}: header did not receive the multi-root list — {header_calls[-1]!r}"
        )

    def test_review_trace_scope_and_header_receive_identical_multi_root_list(self, tmp_path, monkeypatch):
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        proj = expected_roots[0] / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        scope_calls: list[list] = []
        header_calls: list[list] = []
        real_resolve = scope._resolve_project_scope
        real_print = scope.print_resolved_scope

        def spy_resolve(*a, **k):
            scope_calls.append(list(k["roots"]) if k.get("roots") is not None else None)
            return real_resolve(*a, **k)

        def spy_print(*a, **k):
            roots = a[2] if len(a) > 2 else k.get("roots")
            header_calls.append(list(roots))
            return real_print(*a, **k)

        monkeypatch.setattr(scope, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(scope, "print_resolved_scope", spy_print)

        review_trace.cmd_review_trace(_review_trace_args())

        assert scope_calls[-1] == expected_roots
        assert header_calls[-1] == expected_roots

    def test_skill_invocation_glob_scope_receives_the_multi_root_list_as_is(self, tmp_path, monkeypatch):
        """cmd_skill_invocation never calls _resolve_project_scope — its two
        call sites (:2146, :2148 in the plan's line numbering) route through
        _iter_glob_scoped_sessions/_iter_scoped_sessions directly. Above one
        root, the --projects branch takes the _iter_glob_scoped_sessions path
        with the full roots list (not iter_sessions' single-Path shape)."""
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        calls: list[list] = []
        real_glob_scoped = scope._iter_glob_scoped_sessions

        def spy_glob_scoped(roots, *a, **k):
            calls.append(list(roots))
            return real_glob_scoped(roots, *a, **k)

        monkeypatch.setattr(scope, "_iter_glob_scoped_sessions", spy_glob_scoped)

        skill_invocation.cmd_skill_invocation(_skill_inv_args())  # projects="*" -- the explicit-glob branch

        assert calls, "_iter_glob_scoped_sessions was never called"
        assert calls[-1] == expected_roots

    def test_skill_invocation_repo_scoped_call_receives_the_multi_root_list(self, tmp_path, monkeypatch):
        """The --this-repo-equivalent default branch (--projects unset) threads
        roots=roots into _iter_scoped_sessions rather than defaulting."""
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        calls: list[list | None] = []
        real_scoped = scope._iter_scoped_sessions

        def spy_scoped(slugs, include_subagents, roots=None):
            calls.append(list(roots) if roots is not None else None)
            return real_scoped(slugs, include_subagents, roots=roots)

        monkeypatch.setattr(scope, "_iter_scoped_sessions", spy_scoped)
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/main")

        def fake_git_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_git_run)

        skill_invocation.cmd_skill_invocation(_skill_inv_args(projects=None))  # unset -- the repo-scoped default branch

        assert calls, "_iter_scoped_sessions was never called"
        assert calls[-1] == expected_roots

    def test_single_root_skill_invocation_glob_call_passes_a_bare_path_not_a_list(self, fake_projects, monkeypatch):
        """At one root, cmd_skill_invocation's --projects branch keeps calling
        iter_sessions(roots[0], ...) directly (a single Path, not a list) —
        iter_sessions' documented flat-sort-over-full-paths ordering guarantee
        is unaffected by this plan at single root."""
        calls: list[Path] = []
        real_iter_sessions = corpus.iter_sessions

        def spy_iter_sessions(projects_dir, *a, **k):
            calls.append(projects_dir)
            return real_iter_sessions(projects_dir, *a, **k)

        monkeypatch.setattr(corpus, "iter_sessions", spy_iter_sessions)

        skill_invocation.cmd_skill_invocation(_skill_inv_args())  # projects="*", single root (fake_projects' PROJECTS_DIR)

        assert calls == [scope.PROJECTS_DIR]


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
        parser = _mod.build_parser()
        args = parser.parse_args(cli_args)
        assert args.projects == "a/b"

    def test_every_projects_registration_across_every_subcommand_has_no_argparse_type(self):
        """Walks every subparser build_parser() registers: --projects's
        one-level restriction must stay runtime-validated, since whether it
        applies depends on how many roots this invocation resolves, not on
        the flag's syntax alone."""
        parser = _mod.build_parser()
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


class TestMultiRootFormatOutliers:
    """End-to-end multi-root output tests for the three format-outlier
    subcommands (judgment-pair writes --out,
    audit-routing-samples is a JSON stream with the header on stderr, cost
    redacts by default) — each reached through the declared-roots file (the
    default union scope.resolve_scan_roots builds from declared_transcript_roots()),
    not cost's own --config-dir extra (already covered by
    TestCostMultiRootRedaction)."""

    def test_cost_redacted_totals_sum_across_declared_roots(self, tmp_path, monkeypatch, capsys):
        default_config = tmp_path / "default-account"
        default_proj = default_config / "projects" / "-home-user-repo-a"
        default_proj.mkdir(parents=True)
        _write_jsonl(default_proj / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(scope, "config_dir", lambda: default_config)

        declared_config = tmp_path / "declared-account"
        declared_proj = declared_config / "projects" / "-home-user-repo-b"
        declared_proj.mkdir(parents=True)
        _write_jsonl(declared_proj / "sess-b.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared_config}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        cost.cmd_cost(_cost_args())
        out = capsys.readouterr().out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out
        assert _extract_grand_total(out) == pytest.approx(4.0)

    def test_audit_routing_samples_stdout_still_parses_as_json_across_declared_roots(
        self, tmp_path, monkeypatch, capsys
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [_opus([_read_use("r1", "/a.py")], out=100)])

        audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        out, err = capsys.readouterr()
        assert "2 roots" in err  # header routed to stderr, matching audit-routing-samples' convention
        # The per-root progress line is gated on len(roots) > 1, so above one
        # root (unlike TestAllSubcommandsSingleRootHeader's assertions) it must print.
        assert "scanning root 1/2..." in err
        assert "scanning root 2/2..." in err
        records = json.loads(out)
        assert {rec["session_id"] for rec in records} == {"sess-0", "sess-1"}

    def test_judgment_pair_out_file_still_one_header_line_across_declared_roots(
        self, tmp_path, monkeypatch
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [
                _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
                _review_asst("Logic error in retry loop.", "2026-05-20T10:01:00.000Z"),
                _user_reply("Will fix the retry logic."),
            ])
        out_file = roots[0].parent / "out.txt"

        judgment_pair.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
        content = out_file.read_text()
        lines = content.splitlines()
        assert lines[0] == "JUDGMENT PAIR SOURCES (*; 2 roots)"
        header_lines = [ln for ln in lines if ln.startswith("JUDGMENT PAIR SOURCES")]
        assert len(header_lines) == 1
        assert content.count("Logic error in retry loop.") == 2  # one block per root's session


class TestSkillFilesQuoteRootCountDescLiteral:
    def test_absent_state_literal_appears_in_both_skill_files(self, tmp_path, monkeypatch):
        """Contract test, a tripwire not a full guarantee: derives
        _root_count_desc()'s absent-state literal and asserts it appears in
        both transcript-analysis/SKILL.md and transcript-narrative/SKILL.md
        source text. This only catches the rendered literal drifting out of
        sync with what the skills quote verbatim -- each skill's own
        verbatim positive pin on its own scope-confirmation sentence (see
        each skill's dedicated tests) is the real contract."""
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        literal = scope._root_count_desc([tmp_path / "acct-a" / "projects"])
        for skill_name in ("transcript-analysis", "transcript-narrative"):
            skill_text = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
            assert literal in skill_text, f"{skill_name}/SKILL.md does not quote _root_count_desc()'s literal"


class TestSkillFilesReportObservedScopeNotUnionGuarantee:
    """Mechanism 6: transcript-narrative/SKILL.md and transcript-analysis/SKILL.md
    must instruct the reader to record the *observed* scope rather than assert
    a multi-account union/unconditional-header guarantee that --summary makes
    false. Each positive pin is the real contract (source-scanning a SKILL.md
    is acceptable here -- it has no executable form to run, unlike the .py
    files this module tests elsewhere). Each negative-grep pin is a tripwire
    only: it catches the exact old sentence being re-added verbatim, not a
    reworded regression that reintroduces the same false claim in different
    words."""

    def test_transcript_narrative_names_the_sessions_sources_line_to_record(self):
        skill_text = (SKILLS_DIR / "transcript-narrative" / "SKILL.md").read_text()
        assert (
            "`sessions --paths` prints its resolved-scope header (`SESSIONS SOURCES (...)`) to stderr"
            " — record that line."
        ) in skill_text

    def test_transcript_narrative_no_longer_asserts_every_declared_account_union(self):
        skill_text = (SKILLS_DIR / "transcript-narrative" / "SKILL.md").read_text()
        assert "every declared account, not just the active one — and read only those files" not in skill_text
        assert "resolved against every declared account (`~/.claude/transcript-config-dirs`)" not in skill_text

    def test_transcript_analysis_names_the_summary_scope_line_as_the_carrier(self):
        skill_text = (SKILLS_DIR / "transcript-analysis" / "SKILL.md").read_text()
        assert "`cost --summary` prints no resolved-scope header" in skill_text
        assert "on its own `Scope:` line" in skill_text

    def test_transcript_analysis_no_longer_claims_the_header_is_unconditional_for_every_subcommand(self):
        skill_text = (SKILLS_DIR / "transcript-analysis" / "SKILL.md").read_text()
        assert (
            "not just the active profile. The resolved-scope header states the root count unconditionally,"
            " even at one root with nothing declared — see \"Scope confirmation\" above."
        ) not in skill_text


# ---------------------------------------------------------------------------
# per-record --branches consumers: pre-branch and subagent attribution
# ---------------------------------------------------------------------------


class TestPerRecordConsumersCreditPrefixToTheBranchEntered:
    """A session that opens on the default branch and then enters a feature branch:
    each shim subcommand credits the leading default-branch records to that branch
    (docs/transcript-analysis.md, "Pre-branch default-branch attribution")."""

    def test_buckets_date_range_starts_at_prefix_and_main_row_counts_drop(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts="2026-06-01T10:00:00.000Z"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:00:00.000Z"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:05:00.000Z"),
            _asst(_SONNET, branch="main", ts="2026-06-03T10:00:00.000Z"),
        ])
        # Never returns to main, so its main row membership moves entirely to the branch.
        _write_jsonl(fake_projects / "sess-no-return.jsonl", [
            _asst(_SONNET, branch="main", ts="2026-06-01T11:00:00.000Z"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T11:00:00.000Z"),
        ])
        buckets.cmd_buckets(_branch_args())
        out = capsys.readouterr().out
        feature_cols = _table_cols(out, header_contains="Branch", row_contains="feature-x", max_labels=8)
        main_cols = _table_cols(out, header_contains="Branch", row_contains="main", max_labels=8)
        assert feature_cols["Total"] == "5"  # each session's prefix turn plus its turns on the branch
        assert feature_cols["Sess"] == "2"
        assert main_cols["Total"] == "1"  # only the turn after the boundary stays
        assert main_cols["Sess"] == "1"
        assert "2026-06-01..2026-06-02" in out
        assert "2026-06-03..2026-06-03" in out

    def test_buckets_branch_filter_keeps_the_prefix_turn(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        buckets.cmd_buckets(_branch_args(branches="feature-x"))
        out = capsys.readouterr().out
        assert _table_cols(out, header_contains="Branch", row_contains="feature-x", max_labels=8)["Total"] == "2"

    def test_duration_span_includes_the_prefix(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(0)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(10)),
        ])
        duration.cmd_duration(_branch_args(branches="feature-x"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Span(min)", row_contains="feature-x")
        assert cols["Span(min)"] == "10"  # 12:00 to 12:10, not 12:05 to 12:10
        assert cols["Active(min)"] == "10"

    def test_struggle_counts_a_prefix_correction_under_the_branch_entered(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=_attr_ts(1)),
            _user_msg([{"type": "text", "text": "this is the wrong approach"}], branch="main", ts=_attr_ts(2)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        struggle.cmd_struggle(_branch_args(branches="feature-x"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feature-x")
        assert cols["Opus"] == "1"

    def test_user_input_unfiltered_branchless_prompt_keeps_an_empty_label(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("a prompt recorded with no branch", branch="", ts=_attr_ts(1)),
            _asst(_SONNET, branch="", ts=_attr_ts(2)),
        ])
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "a prompt recorded with no branch" in out
        assert "None" not in out
        assert re.search(r"^### \S+ ·  · \d{4}-\d{2}-\d{2}$", out, re.MULTILINE)

    def test_user_input_branch_filter_counts_the_prefix_first_prompt_as_the_initial_prompt(
        self, fake_projects, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prefix planning prompt", branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="main", ts=_attr_ts(2)),
            _ui_user("prompt after entering the branch", branch="feature-x", ts=_attr_ts(5)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(6)),
        ])
        user_input.cmd_user_input(_user_input_args(branches="feature-x"))
        out = capsys.readouterr().out
        assert re.search(r"INITIAL · \w+\]\*\*\n~~~text\nprefix planning prompt\n~~~", out)
        assert re.search(r"FOLLOWUP · \w+\]\*\*\n~~~text\nprompt after entering the branch\n~~~", out)

    def test_judgment_pair_prints_the_attributed_branch_of_a_prefix_invocation(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", _attr_ts(1), branch="main"),
            _review_asst("Review text recorded before the branch existed.", _attr_ts(2), branch="main"),
            _user_reply("Acknowledged.", branch="main"),
            _review_asst("Work on the branch.", _attr_ts(5), branch="feature-x"),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(branches="feature-x"))
        out = capsys.readouterr().out
        assert "Skill: code-review  branch=feature-x" in out

    def test_user_input_since_window_leaves_a_later_default_branch_prompt_on_the_default_branch(
        self, fake_projects, capsys
    ):
        """main -> X -> main -> Y with --since between X and the second main. The session
        context is built before the window filter; one built from in-window prompts would
        see the second main as the leading run and credit its prompt to Y."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prefix prompt", branch="main", ts="2026-06-01T10:00:00.000Z"),
            _ui_user("prompt on branch x", branch="feature-x", ts="2026-06-02T10:00:00.000Z"),
            _ui_user("later prompt on the default branch", branch="main", ts="2026-06-04T10:00:00.000Z"),
            _ui_user("prompt on branch y", branch="feature-y", ts="2026-06-06T10:00:00.000Z"),
        ])

        user_input.cmd_user_input(_user_input_args(branches="main", since="2026-06-03"))
        main_out = capsys.readouterr().out
        assert "later prompt on the default branch" in main_out
        user_input.cmd_user_input(_user_input_args(branches="feature-y", since="2026-06-03"))
        feature_y_out = capsys.readouterr().out
        assert "prompt on branch y" in feature_y_out
        assert "later prompt on the default branch" not in feature_y_out

    def test_judgment_pair_since_window_leaves_a_later_default_branch_invocation_on_the_default_branch(
        self, fake_projects, capsys
    ):
        """main -> X -> main -> Y with --since between X and the second main: the second
        main's invocation stays on main, as a context built from in-window records would
        not keep it."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-06-01T10:00:00.000Z", branch="main", tool_id="s1"),
            _review_asst("Review recorded before the branch existed.", "2026-06-01T10:01:00.000Z", branch="main"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:00:00.000Z"),
            _skill_use_rec("code-review", "2026-06-04T10:00:00.000Z", branch="main", tool_id="s2"),
            _review_asst("Review recorded on the default branch later.", "2026-06-04T10:01:00.000Z", branch="main"),
            _asst(_SONNET, branch="feature-y", ts="2026-06-06T10:00:00.000Z"),
        ])

        judgment_pair.cmd_judgment_pair(_judgment_pair_args(branches="main", since="2026-06-03"))
        main_out = capsys.readouterr().out
        assert "Skill: code-review  branch=main" in main_out
        assert "Review recorded on the default branch later." in main_out
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(branches="feature-y", since="2026-06-03"))
        assert "Review recorded on the default branch later." not in capsys.readouterr().out

    def test_pr_link_counts_the_prefix_turn_toward_the_branch_model_split(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])

        def fake_run(cmd, *_, **__):
            class Completed:
                stdout = json.dumps([{"number": 77}]) if "list" in cmd else ""
                returncode = 0

            return Completed()

        monkeypatch.setattr(subprocess, "run", fake_run)
        pr_link.cmd_pr_link(_branch_args(repo="owner/repo", branches="feature-x", author="alice"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feature-x")
        assert (cols["Opus"], cols["Sonnet"]) == ("1", "1")


class TestSubagentRecordsFollowTheDispatchingBranch:
    """skill-invocation --include-subagents and skill-pair attribute every subagent
    record to the branch its dispatching main thread was on at the record's time."""

    @staticmethod
    def _run_skill_invocation(capsys, **kw) -> str:
        skill_invocation.cmd_skill_invocation(_skill_inv_args(include_subagents=True, **kw))
        return capsys.readouterr().out

    def test_prefix_late_and_isolated_sidechain_invocations_count_under_the_branch_entered(
        self, fake_projects, capsys
    ):
        session_id = "sess-prefix"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst(_SONNET, branch="main", sidechain=True, ts=_attr_ts(2),
                  content=[_skill_use("p1", "plan-it")]),
            _asst(_SONNET, branch="main", sidechain=True, ts=_attr_ts(8),
                  content=[_skill_use("p2", "plan-review")]),
            _asst(_SONNET, branch="worktree-agent-abc123", sidechain=True, ts=_attr_ts(9),
                  content=[_skill_use("p3", "code-review")]),
        ])
        out = self._run_skill_invocation(capsys, branches="feature-x")
        for skill in ("plan-it", "plan-review", "code-review"):
            assert re.search(rf"^{skill}\s+sidechain", out, re.MULTILINE), f"{skill} missing: {out}"

    def test_sidechain_invocation_in_a_later_branch_stretch_counts_under_that_branch_whatever_its_tag(
        self, fake_projects, capsys
    ):
        session_id = "sess-switch"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-y", ts=_attr_ts(5)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst(_SONNET, branch="feature-x", sidechain=True, ts=_attr_ts(8),
                  content=[_skill_use("s1", "code-review")]),
        ])
        under_y = self._run_skill_invocation(capsys, branches="feature-y")
        under_x = self._run_skill_invocation(capsys, branches="feature-x")
        assert "code-review" in under_y
        assert "No skill invocations found." in under_x

    def test_skill_pair_counts_isolated_and_foreign_tagged_sidechain_followers_under_the_branch_entered(
        self, fake_projects, capsys
    ):
        leader_use = [_skill_use("l1", "plan-it")]
        for session_id, follower_tag in (("sess-a", "worktree-agent-abc123"), ("sess-b", "unrelated-branch")):
            _write_jsonl(fake_projects / f"{session_id}.jsonl", [
                _asst(_SONNET, branch="main", ts=_attr_ts(1), content=leader_use),
                _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
            ])
            _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
                _asst(_SONNET, branch=follower_tag, sidechain=True, ts=_attr_ts(8),
                      content=[_skill_use("f1", "plan-review")]),
            ])
        skill_pair.cmd_skill_pair(_skill_pair_args(branches="feature-x"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert (cols["Lead"], cols["Main"], cols["Side"]) == ("2", "0", "2")

    def test_foreign_cwd_sidechain_skill_name_counts_under_the_dispatching_branch(
        self, fake_projects, capsys
    ):
        """Pins a disclosed flow: a subagent dispatched into another repo has its skill
        names counted under the dispatching branch, so --branches X --include-subagents
        prints them whatever the subagent's own cwd (docs/transcript-analysis.md, Limits)."""
        session_id = "sess-cross-repo"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        foreign_record = _asst(_SONNET, branch="other-repo-branch", sidechain=True, ts=_attr_ts(3),
                               content=[_skill_use("f1", "privateco-plugin:internal-runbook")])
        foreign_record["cwd"] = "/elsewhere/other-repo"
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [foreign_record])
        out = self._run_skill_invocation(capsys, branches="feature-x")
        assert re.search(r"^privateco-plugin:internal-runbook\s+sidechain", out, re.MULTILINE), out
