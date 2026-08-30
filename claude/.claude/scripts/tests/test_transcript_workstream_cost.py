"""Tests for transcript_analysis/cost.py's _compute_workstream_dollars and
transcript-analysis.py's workstream-cost subcommand (cmd_workstream_cost)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import (
    _priced,
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


def _workstream_cost_args(*, projects: str = "*", this_repo: bool = False, check_pr_status: bool = False) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "check_pr_status": check_pr_status,
    })()


def _session_iter(fake_projects, *, roots=None):
    args = _workstream_cost_args()
    roots = roots if roots is not None else [fake_projects.parent]
    session_iter, _scope = _mod._resolve_project_scope(args, "workstream-cost", include_subagents=True, roots=roots)
    return session_iter


class TestComputeWorkstreamDollars:
    """_compute_workstream_dollars: per-branch session count and
    continuation "startup burn" -- exercised through the function itself,
    reusing _compute_pr_cost_branch_totals's own single-pass aggregation."""

    def test_single_session_branch_contributes_zero_startup_burn(self, fake_projects):
        """A branch with exactly one session has no non-first session to
        sum, so startup_burn_dollars is 0 even though the branch itself has
        priced dollars."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z"),
        ])  # $2.00

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        agg = workstream["feature-a"]
        assert agg["session_count"] == 1
        assert agg["total_dollars"] == pytest.approx(2.00)
        assert agg["startup_burn_dollars"] == pytest.approx(0.0)

    def test_three_sessions_in_reverse_file_path_order_use_timestamp_not_iteration_order(self, fake_projects):
        """iter_sessions yields sessions in file-path sort order, not
        chronological order -- these three files sort sess-1, sess-2, sess-3
        by path, but their timestamps run the opposite way (sess-3 earliest,
        sess-1 latest). Only the two chronologically-later sessions
        (sess-2, sess-1) contribute startup-burn; sess-3, the true first
        session, contributes 0 despite being iterated last."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=2_000_000, branch="feature-b", ts="2026-08-03T10:00:00.000Z"),
        ])  # $4.00, latest -- iterated first
        _write_jsonl(fake_projects / "sess-2.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-b", ts="2026-08-02T10:00:00.000Z"),
        ])  # $2.00, middle -- iterated second
        _write_jsonl(fake_projects / "sess-3.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-b", ts="2026-08-01T10:00:00.000Z"),
        ])  # $1.00, earliest (the true first session) -- iterated last

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        agg = workstream["feature-b"]
        assert agg["session_count"] == 3
        assert agg["total_dollars"] == pytest.approx(7.00)
        # sess-3 (earliest) excluded; sess-2 + sess-1 (the two non-first-by-time
        # sessions) included -- a file-order bug would instead exclude sess-1
        # (iterated first) and produce 3.00 (sess-2 + sess-3) here.
        assert agg["startup_burn_dollars"] == pytest.approx(6.00)

    def test_non_first_session_with_two_main_thread_turns_sums_exactly_those_two(self, fake_projects):
        """A non-first session with fewer main-thread turns than
        until_first_n_turns sums exactly the turns it has -- never padded or
        scaled up to the full window."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-c", ts="2026-08-01T10:00:00.000Z"),
        ])  # $1.00, first session by time
        _write_jsonl(fake_projects / "sess-2.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-c", ts="2026-08-02T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=1_500_000, branch="feature-c", ts="2026-08-02T10:01:00.000Z"),
        ])  # $2.00 + $3.00, non-first session, exactly 2 main-thread turns

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        agg = workstream["feature-c"]
        assert agg["session_count"] == 2
        assert agg["total_dollars"] == pytest.approx(6.00)
        assert agg["startup_burn_dollars"] == pytest.approx(5.00)

    def test_non_first_session_with_more_than_five_turns_caps_at_the_first_five(self, fake_projects):
        """A non-first session with more main-thread turns than the default
        until_first_n_turns=5 window sums only the first 5 -- the cap
        _RAMP_CURVE_TURN_INDEX_BUCKETS' own bucket boundary exists to
        enforce, not the whole session's dollars."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-g", ts="2026-08-01T10:00:00.000Z"),
        ])  # $1.00, first session by time
        six_turns = [
            _priced(
                "claude-sonnet-5", input=500_000, branch="feature-g",
                ts=f"2026-08-02T10:{i:02d}:00.000Z",
            )  # $1.00 each
            for i in range(6)
        ]
        _write_jsonl(fake_projects / "sess-2.jsonl", six_turns)  # non-first session, 6 main-thread turns

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        agg = workstream["feature-g"]
        assert agg["session_count"] == 2
        assert agg["total_dollars"] == pytest.approx(7.00)  # 1.00 + 6 x 1.00
        assert agg["startup_burn_dollars"] == pytest.approx(5.00)  # only the first 5 of sess-2's 6 turns

    def test_worktree_agent_session_rolls_up_to_its_real_branch(self, fake_projects):
        """A worktree-agent-* labelled subagent record's dollars fold into
        the branch active in its own session at dispatch time -- the same
        carry-forward _attributed_branch already gives pr-cost, reused here
        via _compute_pr_cost_branch_totals -- rather than creating a
        separate "worktree-agent-*" branch entry."""
        session_id = "sess-carry"
        main_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-d", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T11:00:00.000Z",
        )  # $1.00, later than main_rec
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [main_rec])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        assert "worktree-agent-abc123" not in workstream
        agg = workstream["feature-d"]
        assert agg["session_count"] == 1
        assert agg["total_dollars"] == pytest.approx(3.00)

    def test_non_first_session_that_is_subagent_only_contributes_zero_startup_burn(self, fake_projects):
        """A non-first session whose only records are sidechain (subagent)
        turns has zero main-thread turns of its own, so
        _first_main_thread_turns_dollars_by_branch's isSidechain exclusion
        means it contributes nothing to startup burn. The session's subagent
        dollars are still priced and counted toward the branch's
        total_dollars."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-k", ts="2026-08-01T10:00:00.000Z"),
        ])  # $1.00, first session by time
        subagent_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-k", ts="2026-08-02T10:00:00.000Z",
        )  # $2.00
        subagent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess-2.jsonl", [])  # readable-but-empty main file
        _write_subagent_jsonl(fake_projects, "sess-2", "agent-1", [subagent_rec])

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        agg = workstream["feature-k"]
        assert agg["session_count"] == 2
        assert agg["total_dollars"] == pytest.approx(3.00)
        assert agg["startup_burn_dollars"] == pytest.approx(0.0)

    def test_empty_corpus_returns_empty_dict(self, fake_projects):
        """No session files at all -- no branch to report, not a KeyError
        or a spurious zero-valued entry."""
        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        assert workstream == {}

    def test_unbranched_session_excluded_from_workstream_result_entirely(self, fake_projects):
        """A record with no gitBranch key at all accumulates into
        _compute_pr_cost_branch_totals's separate unbranched_totals bucket,
        which _compute_workstream_dollars's result loop never surfaces since
        it iterates only branch_totals. The fixture also includes a real
        branch to confirm its own totals are unaffected."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-x", ts="2026-08-01T10:00:00.000Z"),
        ])  # $2.00
        unbranched_rec = _priced("claude-sonnet-5", input=500_000, ts="2026-08-01T11:00:00.000Z")  # $1.00
        del unbranched_rec["gitBranch"]
        _write_jsonl(fake_projects / "sess-2.jsonl", [unbranched_rec])

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        assert "" not in workstream
        assert None not in workstream
        assert set(workstream) == {"feature-x"}
        assert workstream["feature-x"]["total_dollars"] == pytest.approx(2.00)

    def test_startup_burn_never_exceeds_total_dollars_across_multi_session_branches(self, fake_projects):
        """Sanity invariant, pinned as a merge gate: for every branch,
        startup_burn_dollars never exceeds total_dollars. Exercised here
        across a multi-session, multi-branch fixture -- a violation means
        the non-first-session filter double-counted the first session."""
        _write_jsonl(fake_projects / "e-sess-1.jsonl", [
            _priced("claude-sonnet-5", input=2_000_000, branch="feature-e", ts="2026-08-04T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "e-sess-2.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-e", ts="2026-08-03T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "e-sess-3.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-e", ts="2026-08-02T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "f-sess-1.jsonl", [
            _priced("claude-sonnet-5", input=100_000, branch="feature-f", ts="2026-08-01T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "f-sess-2.jsonl", [
            _priced("claude-sonnet-5", input=300_000, branch="feature-f", ts="2026-08-02T10:00:00.000Z"),
        ])

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        assert len(workstream) == 2
        for branch, agg in workstream.items():
            assert agg["startup_burn_dollars"] <= agg["total_dollars"], (
                f"{branch}: startup_burn_dollars ({agg['startup_burn_dollars']}) exceeds"
                f" total_dollars ({agg['total_dollars']})"
            )

    def test_mid_session_branch_switch_scopes_startup_burn_to_the_branchs_own_turns(self, fake_projects):
        """A session that starts on one branch and switches to another
        mid-session must not have its whole-session dollars credited to
        every branch it touches -- each branch's startup burn counts only
        the turns actually attributed to that branch."""
        _write_jsonl(fake_projects / "switch-sess.jsonl", [
            _priced("claude-sonnet-5", input=2_000_000, branch="branch-b", ts="2026-08-01T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=2_000_000, branch="branch-b", ts="2026-08-01T10:01:00.000Z"),
            _priced("claude-sonnet-5", input=1_000, branch="branch-a", ts="2026-08-01T11:00:00.000Z"),
        ])  # $4.00 + $4.00 on branch-b, then $0.002 on branch-a, all in one session
        _write_jsonl(fake_projects / "a-early-sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, branch="branch-a", ts="2026-07-31T09:00:00.000Z"),
        ])  # $0.20, branch-a's true first session by time

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        branch_a = workstream["branch-a"]
        # switch-sess is branch-a's non-first session, but only its one
        # branch-a-attributed turn ($0.002) counts toward branch-a's startup
        # burn -- not the $8.00 of branch-b turns from the same session.
        assert branch_a["total_dollars"] == pytest.approx(0.202)
        assert branch_a["startup_burn_dollars"] == pytest.approx(0.002)
        assert branch_a["startup_burn_dollars"] <= branch_a["total_dollars"]

    def test_session_starts_recorded_once_despite_leading_unparseable_timestamp_turn(self, fake_projects):
        """A session's first turn has an unparseable timestamp and its
        second turn has a valid one -- session_count must still count the
        session once, not drop it permanently because the first turn marked
        it "seen" before any session_starts entry existed for it."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-h", ts=""),  # unparseable, first turn
            _priced("claude-sonnet-5", input=500_000, branch="feature-h", ts="2026-08-01T10:00:00.000Z"),
        ])

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        assert workstream["feature-h"]["session_count"] == 1

    def test_all_unparseable_timestamps_in_session_report_zero_session_count(self, fake_projects):
        """Every turn in a session/branch carries an unparseable timestamp --
        session_count is 0 despite the branch having priced corpus activity
        (turn_count > 0), the case _compute_workstream_dollars's own
        docstring documents but this suite left untested until now."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-i", ts=""),
            _priced("claude-sonnet-5", input=500_000, branch="feature-i", ts=""),
        ])

        branch_totals, _unbranched = _mod._compute_pr_cost_branch_totals(_session_iter(fake_projects))
        assert branch_totals["feature-i"]["turn_count"] == 2

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        assert workstream["feature-i"]["session_count"] == 0

    def test_last_activity_ts_reflects_the_latest_turn_even_when_iterated_first(self, fake_projects):
        """last_activity_ts is the max timestamp across a branch's turns,
        not the last one iterated -- these two files sort sess-1, sess-2 by
        path, but sess-1's turn is timestamped later than sess-2's,
        mirroring this file's own reverse-file-order pattern used above for
        startup_burn_dollars."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-j", ts="2026-08-03T10:00:00.000Z"),
        ])  # latest -- iterated first
        _write_jsonl(fake_projects / "sess-2.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-j", ts="2026-08-01T10:00:00.000Z"),
        ])  # earliest -- iterated second

        workstream = _mod._compute_workstream_dollars(_session_iter(fake_projects))
        assert workstream["feature-j"]["last_activity_ts"] == _mod._parse_ts("2026-08-03T10:00:00.000Z")


def _fake_workstream_cost_subprocess_run(
    *, repo: str = "owner/repo", host: str = "github.com",
    merged_prs: list[dict] | None = None, closed_unmerged_prs: list[dict] | None = None,
):
    """Minimal subprocess.run double covering exactly the local git/gh calls
    cmd_workstream_cost's --check-pr-status path makes: origin-remote
    resolution, gh auth preflight, gh repo view (repo-identity pin), and the
    two gh pr list discovery calls (--state merged, --state closed) --
    distinguished by their own --state value, unlike
    _fake_pr_cost_subprocess_run's single-state gh pr list stub, since this
    subcommand makes both calls in one run. Raises AssertionError on any
    other command shape."""
    merged_prs = merged_prs if merged_prs is not None else []
    closed_unmerged_prs = closed_unmerged_prs if closed_unmerged_prs is not None else []

    def fake_run(cmd, *args, **kwargs):
        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        proc = _Proc()
        if cmd[:3] == ["git", "remote", "get-url"]:
            proc.stdout = f"https://{host}/{repo}.git\n"
        elif cmd[:2] == ["gh", "auth"]:
            proc.stdout = ""
        elif cmd[:3] == ["gh", "repo", "view"]:
            proc.stdout = json.dumps({"nameWithOwner": repo, "url": f"https://{host}/{repo}"})
        elif cmd[:3] == ["gh", "pr", "list"]:
            state = cmd[cmd.index("--state") + 1]
            proc.stdout = json.dumps(merged_prs if state == "merged" else closed_unmerged_prs)
        else:
            raise AssertionError(f"unexpected subprocess.run call in workstream-cost test: {cmd}")
        return proc

    return fake_run


class TestPrintWorkstreamSessionStats:
    """Direct unit coverage of _print_workstream_session_stats, mirroring
    TestCostThreadSplit's _print_thread_table unit test in
    test_transcript_cost.py -- pins the exact "Sessions per branch"/
    "Startup-burn dollars" wording against a hand-built workstream dict,
    without paying cmd_workstream_cost's full corpus-scan fixture cost."""

    def test_prints_exact_sessions_per_branch_and_startup_burn_lines(self, capsys):
        # session_counts = [1, 2, 4] -> mean 7/3 = 2.33, median 2.00.
        # total_dollars = 1.00 + 3.00 + 4.00 = 8.00;
        # startup_burn_dollars = 0 + 2.00 + 3.00 = 5.00; 5/8 = 62.5%.
        workstream = {
            "branch-a": {"session_count": 1, "total_dollars": 1.00, "startup_burn_dollars": 0.0},
            "branch-b": {"session_count": 2, "total_dollars": 3.00, "startup_burn_dollars": 2.00},
            "branch-c": {"session_count": 4, "total_dollars": 4.00, "startup_burn_dollars": 3.00},
        }
        _mod._print_workstream_session_stats(workstream)
        out = capsys.readouterr().out
        assert out == (
            "Sessions per branch -- mean: 2.33, median: 2.00\n"
            "Startup-burn dollars: $5.00 of $8.00 total branch dollars (62.5%)\n"
        )


class TestCmdWorkstreamCostDefaultMode:
    def test_prints_branch_count_from_corpus_scan(self, fake_projects, capsys):
        """Smoke-level wiring check: cmd_workstream_cost's own corpus scan
        and _compute_workstream_dollars call reach the printed "Branches: N"
        line. The summary lines' exact math is unit-tested directly against
        _print_workstream_session_stats instead (see
        TestPrintWorkstreamSessionStats)."""
        _write_jsonl(fake_projects / "a-sess-1.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="branch-a", ts="2026-08-01T10:00:00.000Z"),
        ])

        _mod.cmd_workstream_cost(_workstream_cost_args())
        out = capsys.readouterr().out

        assert "Branches: 1" in out

    def test_empty_corpus_prints_no_branches_message(self, fake_projects, capsys):
        """Zero session files in scope -- cmd_workstream_cost's own
        `if not workstream` branch prints the exact message and returns,
        with no traceback."""
        _mod.cmd_workstream_cost(_workstream_cost_args())
        out = capsys.readouterr().out

        assert out.rstrip("\n").splitlines()[-1] == "No branches with corpus activity were found."


class TestCmdWorkstreamCostCheckPrStatus:
    def test_classifies_merged_closed_and_no_match_branches_by_last_activity_age(
        self, fake_projects, capsys, monkeypatch,
    ):
        """Three branches, one per PR-status category: a branch matched by
        the merged discovery call, one matched only by the closed-unmerged
        discovery call, and one matched by neither -- only the third
        appears in the "no PR match at all" listing."""
        _write_jsonl(fake_projects / "merged-sess.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="merged-branch", ts="2026-08-01T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "closed-sess.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="closed-branch", ts="2026-08-01T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "no-match-sess.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="no-match-branch", ts="2026-08-01T10:00:00.000Z"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_workstream_cost_subprocess_run(
            merged_prs=[{"headRefName": "merged-branch"}],
            closed_unmerged_prs=[{"headRefName": "closed-branch"}],
        ))

        _mod.cmd_workstream_cost(_workstream_cost_args(check_pr_status=True))
        out = capsys.readouterr().out

        assert "Branches: 3" in out
        header = "Branches with no PR match at all (merged or closed-unmerged), by last-activity age (days), oldest first:"
        assert header in out
        section = out.split(header, 1)[1]
        age_lines = [ln for ln in section.splitlines() if ln.strip() and ln.strip() != "(none)"]
        assert len(age_lines) == 1  # only no-match-branch has no PR match at all

    def test_branch_with_only_unparseable_timestamps_omitted_from_no_match_listing(
        self, fake_projects, capsys, monkeypatch,
    ):
        """A branch whose every turn carries an unparseable timestamp gets
        last_activity_ts == 0.0, and the no-PR-match filter's truthy check
        on last_activity_ts silently excludes it -- documented behavior
        (see cmd_workstream_cost's docstring), not a bug this test guards
        against changing."""
        _write_jsonl(fake_projects / "unparseable-sess.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="unparseable-branch", ts=""),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_workstream_cost_subprocess_run())

        _mod.cmd_workstream_cost(_workstream_cost_args(check_pr_status=True))
        out = capsys.readouterr().out

        assert "Branches: 1" in out
        header = "Branches with no PR match at all (merged or closed-unmerged), by last-activity age (days), oldest first:"
        section = out.split(header, 1)[1]
        age_lines = [ln for ln in section.splitlines() if ln.strip() and ln.strip() != "(none)"]
        assert age_lines == []
