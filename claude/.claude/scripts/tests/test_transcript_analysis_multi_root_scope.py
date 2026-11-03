"""Every subcommand's resolved-scope header and roots threading, plus the SKILL.md text describing multi-root scope."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import SKILLS_DIR
from transcript_analysis import (
    audit_routing,
    buckets,
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
)

from ._audit_routing_helpers import _audit_routing_samples_args, _audit_routing_shape_args
from ._cache_rebuild_helpers import _cache_rebuild_args
from ._commit_gate_helpers import _gate_args
from ._handoff_nudge_helpers import _rearm_backtest_args, _spend_over_threshold_args
from ._skill_helpers import _judgment_pair_args, _review_asst, _skill_inv_args, _skill_pair_args, _user_reply
from ._subagent_helpers import _subagents_args
from .conftest import (
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
    _skill_use_rec,
    _two_declared_roots,
    _write_jsonl,
)


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
