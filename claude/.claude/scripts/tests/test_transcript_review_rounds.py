"""Tests for transcript_analysis/review_rounds.py (review-round-cost)."""
import importlib.util
import sys
from pathlib import Path

import pytest
from transcript_analysis import corpus, render, review_rounds

from .conftest import (
    _agent_use,
    _priced,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _review_round_cost_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    skill: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "until": until,
        "skill": skill,
    })()


def _session_iter(fake_projects):
    return corpus.iter_sessions(fake_projects.parent, "*")


def _skill_block(tool_id: str, skill: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}


def _slash_user(skill: str, *, branch: str = "main", ts: str | None = None) -> dict:
    return _user_msg(f"<command-name>/{skill}</command-name>", branch=branch, ts=ts)


def _nested_subagent_session_id(top_session_id: str, top_agent_id: str) -> str:
    """The session_id argument that makes _write_subagent_dispatch write
    under <top_session_id>/subagents/<top_agent_id>/subagents/ -- a nested
    dispatch's own subagents/ directory, one level below the top-level
    dispatch's own."""
    return f"{top_session_id}/{corpus.SUBAGENT_SUBDIR}/{top_agent_id}"


def _two_declared_roots(tmp_path, monkeypatch) -> list[Path]:
    """Active profile (acct-a) plus one declared root (acct-b, via
    TRANSCRIPT_CONFIG_DIRS_FILE) -- the minimal setup resolve_scan_roots
    resolves to more than one root with no --config-dir flag involved."""
    acct_a = tmp_path / "acct-a"
    (acct_a / "projects").mkdir(parents=True)
    acct_b = tmp_path / "acct-b"
    (acct_b / "projects").mkdir(parents=True)
    monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{acct_b}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    return [acct_a / "projects", acct_b / "projects"]


class TestComputeReviewRoundCosts:
    """compute_review_round_costs: round-window detection and per-round
    dollar attribution, exercised directly against exact dollar amounts."""

    def test_three_invocations_with_no_intervening_prompt_produce_three_rounds(self, fake_projects):
        """Every invocation of a review skill is its own round -- no dedup
        by diff-state. Two of the three code-review invocations here sit
        back-to-back with no user prompt between them; each still opens and
        closes its own round (the second invocation itself is what closes
        the first round's window)."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s2", "code-review")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:03:00.000Z",
                content=[_skill_block("s3", "code-review")],
            ),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 3
        assert all(r["skill"] == "code-review" for r in data["rounds"])

    def test_slash_only_invocation_is_detected_and_counted_exactly_once(self, fake_projects):
        """A /slash user record with no Skill tool_use block anywhere is
        still detected as a round-opener, and counted exactly once."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _slash_user("code-review", branch="feat", ts="2026-08-01T10:00:00.000Z"),
            _user_msg("done reviewing", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        assert data["rounds"][0]["skill"] == "code-review"

    def test_directory_qualified_skill_name_is_detected(self, fake_projects):
        """A worktree-directory-qualified Skill name still normalizes to its
        bare REVIEW_SKILLS member."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", ".claude/worktrees/b/claude:code-review")],
            ),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        assert data["rounds"][0]["skill"] == "code-review"

    def test_unrelated_skill_names_open_no_round(self, fake_projects):
        """A skill outside REVIEW_SKILLS never opens a round."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "handoff")],
            ),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s2", "plan-it")],
            ),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert data["rounds"] == []

    def test_round_dollars_include_opening_turn_later_main_turns_and_subagent_dispatch(self, fake_projects):
        """A round's dollars sum the opening assistant turn (the one firing
        the Skill block), every further main-thread turn up to the window
        end, and every subagent transcript dispatched inside the window --
        asserted against exact _priced amounts."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(  # opening turn: $0.20, also spawns dispatch a1
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _priced(  # second main-thread turn, still inside the window: $0.40
                "claude-sonnet-5", input=200_000, branch="feat", ts="2026-08-01T10:01:00.000Z",
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),  # closes the window
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:30.000Z")],  # $1.00
        )
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["main_dollars"] == pytest.approx(0.20 + 0.40)
        assert r["agent_dollars"] == pytest.approx(1.00)
        assert r["agents"] == 1

    def test_tool_result_and_ismeta_records_do_not_close_the_window(self, fake_projects):
        """A tool-result-bearing user record and an isMeta-injected record
        both fail _is_fresh_user_prompt and must not close a round's window
        -- only a genuine fresh user prompt does."""
        tool_result_rec = _user_msg(
            [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}], branch="feat",
            ts="2026-08-01T10:01:00.000Z",
        )
        ismeta_rec = _user_msg("injected", branch="feat", ts="2026-08-01T10:02:00.000Z")
        ismeta_rec["isMeta"] = True
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            tool_result_rec,
            ismeta_rec,
            _priced("claude-sonnet-5", input=200_000, branch="feat", ts="2026-08-01T10:03:00.000Z"),
            _user_msg("actual reply", branch="feat", ts="2026-08-01T10:04:00.000Z"),  # closes it
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        assert data["rounds"][0]["main_dollars"] == pytest.approx(0.20 + 0.40)

    def test_nested_dispatch_from_inside_a_subagent_is_priced_into_the_round(self, fake_projects):
        """An Agent/Task tool_use inside a subagent's own transcript, with
        its own meta.json living in that subagent's own subagents/
        directory, is priced into the same round as its parent dispatch."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        nested_spawn_rec = _priced(
            "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:10.000Z",
            content=[_agent_use("n1", "staff-sdet")],
        )  # $0.20, also spawns nested n1
        nested_spawn_rec["isSidechain"] = True
        _write_subagent_dispatch(fake_projects, session_id, "agent-1", "a1", [nested_spawn_rec])
        nested_session_id = _nested_subagent_session_id(session_id, "agent-1")
        nested_rec = _priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:20.000Z")  # $1.00
        nested_rec["isSidechain"] = True
        _write_subagent_dispatch(fake_projects, nested_session_id, "agent-2", "n1", [nested_rec])

        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["agent_dollars"] == pytest.approx(0.20 + 1.00)
        assert r["agents"] == 2

    def test_dangling_dispatch_with_no_matching_meta_json_contributes_no_dollars(self, fake_projects):
        """An Agent/Task tool_use inside a round window with no matching
        subagents/*.meta.json increments dangling and contributes nothing --
        never dropped silently or guessed at."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        # No _write_subagent_dispatch call for tool_use_id "a1" -- dangling.
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["agent_dollars"] == pytest.approx(0.0)
        assert r["dangling"] == 1

    def test_round_dollars_plus_non_round_dollars_equal_branch_total(self, fake_projects):
        """No subagent turn is double-counted: a branch's round dollars plus
        its non-round dollars equal its total priced dollars."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T09:00:00.000Z"),  # non-round: $0.20
            _priced(  # round open: $0.20 main, spawns a1
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),  # closes the round
            _priced(  # non-round: $0.20 main, spawns a2 outside any window
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T11:00:00.000Z",
                content=[_agent_use("a2", "staff-sdet")],
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:10.000Z")],  # $1.00, round
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-2", "a2",
            [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T11:00:10.000Z")],  # $1.00, non-round
        )
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        branch_total = data["branch_totals"][(None, "feat")]
        round_dollars = sum(r["main_dollars"] + r["agent_dollars"] for r in data["rounds"])
        non_round_dollars = 0.20 + 0.20 + 1.00  # the two non-round main turns plus a2's dispatch
        assert branch_total == pytest.approx(round_dollars + non_round_dollars)

    def test_round_order_follows_timestamp_not_file_path_order(self, fake_projects):
        """Sessions are iterated in file-path sort order, not chronological
        order -- these two files sort sess-1 then sess-2 by path, but
        sess-1's own round has the LATER timestamp. Sorting by each round's
        own sort_key must still put sess-2's earlier round first."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-03T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
        ])
        _write_jsonl(fake_projects / "sess-2.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        ordered = sorted(data["rounds"], key=lambda r: r["sort_key"])
        assert [r["skill"] for r in ordered] == ["plan-review", "code-review"]

    def test_unrecognized_model_turn_inside_round_increments_unpriced_turns(self, fake_projects):
        """A turn on an unrecognized model ID inside a round window
        increments unpriced_turns and adds no dollars -- never priced at
        $0 as if it were a genuinely zero-cost turn."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _priced("some-unrecognized-model-id", input=100_000, branch="feat", ts="2026-08-01T10:01:00.000Z"),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["main_dollars"] == pytest.approx(0.20)
        assert r["unpriced_turns"] == 1

    def test_skill_round_immediately_followed_by_slash_round_produces_two_correct_rounds(self, fake_projects):
        """A Skill-shape round immediately followed by a /slash-shape round,
        with zero fresh-user-prompt records between them, produces exactly
        two correctly-bounded rounds -- the /slash user record's own text
        closes the Skill round (it is itself a fresh user prompt) at the
        same boundary its own round-open detection would use anyway."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _slash_user("plan-review", branch="feat", ts="2026-08-01T10:01:00.000Z"),
            _priced("claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:02:00.000Z"),
            _user_msg("done", branch="feat", ts="2026-08-01T10:03:00.000Z"),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        ordered = sorted(data["rounds"], key=lambda r: r["sort_key"])
        assert [r["skill"] for r in ordered] == ["code-review", "plan-review"]
        assert ordered[0]["main_dollars"] == pytest.approx(0.20)  # only its own opening turn
        assert ordered[1]["main_dollars"] == pytest.approx(0.20)  # the turn after the /slash opener

    def test_slash_round_immediately_followed_by_skill_round_produces_two_correct_rounds(self, fake_projects):
        """The reverse adjacency: a /slash-shape round immediately followed
        by a Skill-shape round, with zero fresh-user-prompt records between
        them, also produces exactly two correctly-bounded rounds."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _slash_user("plan-review", branch="feat", ts="2026-08-01T10:00:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("done", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        ordered = sorted(data["rounds"], key=lambda r: r["sort_key"])
        assert [r["skill"] for r in ordered] == ["plan-review", "code-review"]
        assert ordered[0]["main_dollars"] == pytest.approx(0.0)  # /slash opener has no usage block
        assert ordered[1]["main_dollars"] == pytest.approx(0.20)

    def test_round_left_open_at_session_end_is_priced_through_the_last_record(self, fake_projects):
        """A round with no closing fresh user prompt and no next invocation
        before EOF is still priced through the transcript's last record --
        never dropped, mis-priced, or thrown on."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _priced("claude-sonnet-5", input=200_000, branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["main_dollars"] == pytest.approx(0.20 + 0.40)
        assert data["branch_totals"][(None, "feat")] == pytest.approx(r["main_dollars"])

    def test_colliding_nested_tool_use_id_is_priced_exactly_once(self, fake_projects):
        """A subagent's own transcript emitting the same Agent/Task
        toolUseId twice (a corrupted/retried-dispatch shape) does not price
        the second occurrence again -- the recursive walk terminates and the
        colliding dispatch's dollars are counted exactly once."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        dup_rec = _priced(
            "claude-sonnet-5", input=0, branch="feat", ts="2026-08-01T10:00:10.000Z",
            content=[_agent_use("dup", "staff-sdet"), _agent_use("dup", "staff-sdet")],
        )
        dup_rec["isSidechain"] = True
        _write_subagent_dispatch(fake_projects, session_id, "agent-1", "a1", [dup_rec])
        nested_session_id = _nested_subagent_session_id(session_id, "agent-1")
        nested_rec = _priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:20.000Z")  # $1.00
        nested_rec["isSidechain"] = True
        _write_subagent_dispatch(fake_projects, nested_session_id, "agent-dup", "dup", [nested_rec])

        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["agent_dollars"] == pytest.approx(1.00)  # "dup" priced once, not twice
        assert r["agents"] == 2  # dispatch a1 itself, plus "dup" counted once

    def test_inline_sidechain_record_does_not_corrupt_last_branch_for_branch_totals(self, fake_projects):
        """An isSidechain record's own gitBranch can be an isolation:"worktree"
        subagent dispatch's ephemeral worktree-agent-* branch, not this
        session's real one -- it must never become last_branch for
        branch_totals purposes, mirroring cost.py's _session_branch_index
        main-thread-only carry-forward. The sidechain record's own dollars
        (it is itself a priced assistant record) must land on the real
        branch ("feat"), never a phantom worktree-agent-* bucket."""
        sidechain_rec = _priced(
            "claude-sonnet-5", input=150_000, branch="worktree-agent-abc123",
            ts="2026-08-01T10:00:30.000Z",
        )
        sidechain_rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),  # round open: $0.20
            sidechain_rec,  # $0.30, inline sidechain, must not move last_branch
            _priced("claude-sonnet-5", input=200_000, branch="feat", ts="2026-08-01T10:01:00.000Z"),  # $0.40
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),  # closes the round
        ])
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert "worktree-agent-abc123" not in {branch for _root_idx, branch in data["branch_totals"]}
        branch_total = data["branch_totals"][(None, "feat")]
        assert branch_total == pytest.approx(0.20 + 0.30 + 0.40)
        r = data["rounds"][0]
        round_dollars = r["main_dollars"] + r["agent_dollars"]
        pct = 100 * round_dollars / branch_total
        assert pct == pytest.approx(100.0)  # round dollars == branch total in this fixture

    def test_gitbranch_drift_inside_a_round_window_attributes_dollars_to_the_opening_branch(self, fake_projects):
        """(cumulative /code-review finding, revision -- row 12a) A round's
        dollars must land in the branch_totals bucket the round itself is
        keyed to even when the session's own gitBranch changes between the
        round's opening record and its window end. One session, one open
        window: the opening record fires the code-review Skill block on
        feat-a; a later main-thread record inside the same window, on
        feat-b, also dispatches a subagent (exercises both accumulation
        sites the fix names -- the priced turn and _price_dispatch's
        return -- not just the first); a fresh user prompt then closes the
        window. The round's own branch_key stays feat-a, and both the
        drifted main-thread turn's dollars and the subagent dispatch's
        dollars land in feat-a's branch_totals bucket -- feat-b gets no
        bucket of its own."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(  # round open, feat-a: $0.20
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _priced(  # still inside the window, gitBranch drifted to feat-b: $0.40, spawns a1
                "claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T10:01:00.000Z",
                content=[_agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:02:00.000Z"),  # closes the window
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat-b", ts="2026-08-01T10:01:10.000Z")],  # $1.00
        )
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["branch_key"] == (None, "feat-a")
        assert r["main_dollars"] == pytest.approx(0.20 + 0.40)
        assert r["agent_dollars"] == pytest.approx(1.00)
        assert data["branch_totals"][(None, "feat-a")] == pytest.approx(0.20 + 0.40 + 1.00)
        assert (None, "feat-b") not in data["branch_totals"]

    def test_gitbranch_drift_fix_leaves_out_of_window_attribution_unaffected(self, fake_projects):
        """Paired with the drift test above: a main-thread record added
        after the window closes, carrying no gitBranch of its own, still
        attributes to feat-b via the ordinary forward-pass carry-forward --
        proving the row-12a fix changes in-window attribution only,
        leaving out-of-window (non-round) attribution exactly as it was."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _priced(
                "claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T10:01:00.000Z",
                content=[_agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:02:00.000Z"),  # closes the window
            _priced(  # after the window, no gitBranch of its own: $0.20
                "claude-sonnet-5", input=100_000, branch="", ts="2026-08-01T10:03:00.000Z",
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat-b", ts="2026-08-01T10:01:10.000Z")],
        )
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert data["branch_totals"][(None, "feat-a")] == pytest.approx(0.20 + 0.40 + 1.00)
        assert data["branch_totals"][(None, "feat-b")] == pytest.approx(0.20)

    def test_since_ts_is_inclusive_of_a_round_opening_exactly_on_the_boundary(self, fake_projects):
        """A round opening exactly at since_ts is kept, and one opening
        strictly earlier is dropped -- since_ts is an inclusive lower bound
        at the raw-epoch level compute_review_round_costs receives."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("u1", branch="feat", ts="2026-08-01T10:01:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-02T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("u2", branch="feat", ts="2026-08-02T10:01:00.000Z"),
        ])
        since_ts = corpus._parse_ts("2026-08-02T10:00:00.000Z")
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects), since_ts=since_ts)
        assert [r["skill"] for r in data["rounds"]] == ["plan-review"]

    def test_until_ts_excludes_a_round_opening_exactly_on_the_boundary(self, fake_projects):
        """A round opening exactly at until_ts is dropped, and one opening
        strictly earlier is kept -- until_ts is an exclusive upper bound at
        the raw-epoch level compute_review_round_costs receives (the CLI's
        own --until DATE resolves to the start of the *next* day before
        calling in, making the user-facing flag inclusive of the whole
        until-day -- see scope._parse_absolute_window_args)."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("u1", branch="feat", ts="2026-08-01T10:01:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-02T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("u2", branch="feat", ts="2026-08-02T10:01:00.000Z"),
        ])
        until_ts = corpus._parse_ts("2026-08-02T10:00:00.000Z")
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects), until_ts=until_ts)
        assert [r["skill"] for r in data["rounds"]] == ["code-review"]

    def test_skill_filter_is_a_pure_output_filter_invariant_to_dollars_and_list_membership(self, fake_projects):
        """(engineer decision, row 19) skill_filter never narrows
        round-window detection -- only which already-detected rounds are
        returned. Fixture is the shape a detection-time narrowing would
        mis-bound: a code-review invocation immediately followed by a
        plan-review invocation, with no fresh user prompt between them, so
        the plan-review opener is the code-review round's own closing
        bound. If skill_filter narrowed detection instead, excluding
        plan-review would stop it from closing the code-review window,
        letting the code-review round's own turns extend past it and
        inflate its own dollar figure. Passing skill_filter={"code-review"}
        must yield the same code-review round -- identical main_dollars,
        agent_dollars, and agents -- as passing no filter at all. Also
        carries forward the list-membership coverage the pre-row-19 test
        this replaces provided (mirroring
        test_branch_filter_narrows_rounds_but_not_branch_totals's pattern):
        data["rounds"] must contain no plan-review entry when
        skill_filter={"code-review"} is passed, so a skill_filter bug that
        leaves non-matching rounds in the returned list is caught at the
        compute layer, not only inferred from the dollar-invariance
        assertion."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("done", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:10.000Z")],  # $1.00
        )
        unfiltered = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        filtered = review_rounds.compute_review_round_costs(
            _session_iter(fake_projects), skill_filter={"code-review"},
        )
        unfiltered_cr = next(r for r in unfiltered["rounds"] if r["skill"] == "code-review")
        filtered_cr = next(r for r in filtered["rounds"] if r["skill"] == "code-review")
        assert filtered_cr["main_dollars"] == pytest.approx(unfiltered_cr["main_dollars"])
        assert filtered_cr["agent_dollars"] == pytest.approx(unfiltered_cr["agent_dollars"])
        assert filtered_cr["agents"] == unfiltered_cr["agents"]
        assert [r["skill"] for r in filtered["rounds"]] == ["code-review"]

    def test_branch_filter_narrows_rounds_but_not_branch_totals(self, fake_projects):
        """branch_filter drops a non-matching branch's own rounds from the
        returned rounds list, but branch_totals still reflects every
        branch's full, unwindowed corpus activity -- the asymmetry
        compute_review_round_costs's own docstring documents, since
        branch_totals is the reconciliation line's denominator."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("u1", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
        ])
        _write_jsonl(fake_projects / "sess-2.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "plan-review")],
            ),
            _user_msg("u1", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
        ])
        data = review_rounds.compute_review_round_costs(
            _session_iter(fake_projects), branch_filter={"feat-a"},
        )
        assert [r["skill"] for r in data["rounds"]] == ["code-review"]
        assert (None, "feat-b") in data["branch_totals"]
        assert data["branch_totals"][(None, "feat-b")] == pytest.approx(0.20)

    def test_dangling_dispatch_with_meta_json_but_missing_jsonl_contributes_no_dollars(self, fake_projects):
        """A subagents/*.meta.json entry that indexes cleanly but whose
        paired .jsonl doesn't exist on disk hits _parse_jsonl_records' own
        None return -- counted as dangling the same as a missing meta.json
        entirely (see test_dangling_dispatch_with_no_matching_meta_json_
        contributes_no_dollars above), not silently treated as a
        zero-turn success."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:10.000Z")],
        )
        (fake_projects / session_id / corpus.SUBAGENT_SUBDIR / "agent-1.jsonl").unlink()

        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["agent_dollars"] == pytest.approx(0.0)
        assert r["dangling"] == 1


class TestCmdReviewRoundCost:
    """cmd_review_round_cost: CLI-surface rendering, redaction, and
    zero/degenerate-fixture rendering."""

    def test_empty_corpus_prints_no_rounds_message(self, fake_projects, capsys):
        """No session files at all -- the empty-state branch prints its own
        message and returns, with no traceback."""
        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert out.rstrip("\n").splitlines()[-1] == "No review rounds found in scope."

    def test_corpus_with_no_review_skill_invocations_prints_no_rounds_message(self, fake_projects, capsys):
        """Sessions exist and have priced activity, but none of them ever
        invoke a review skill -- still the empty-state branch, not a
        divide-by-zero over an empty rounds list."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z"),
        ])
        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert out.rstrip("\n").splitlines()[-1] == "No review rounds found in scope."

    def test_per_skill_subbreakdown_and_grand_total_agree_including_ready_for_review(self, fake_projects, capsys):
        """The per-branch skill sub-breakdown and the corpus-wide Totals
        line agree, and ready-for-review rounds appear in both."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("u1", branch="feat", ts="2026-08-01T10:01:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:02:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("u2", branch="feat", ts="2026-08-01T10:03:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:04:00.000Z",
                content=[_skill_block("s3", "ready-for-review")],
            ),
            _user_msg("u3", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert "rounds=3  (code-review=1  plan-review=1  ready-for-review=1)" in out
        assert "Totals: 1 branches, 3 rounds (code-review=1  plan-review=1  ready-for-review=1)" in out

    def test_branch_label_raw_under_this_repo_and_redacted_otherwise_multi_root(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Under more than one scan root, a branch prints raw only under
        --this-repo, and opaque (account-<K>/branch-<N>) otherwise, with the
        raw branch name absent from that redacted output -- presence and
        absence are asserted separately, since a redaction bug typically
        shows up as the raw value leaking alongside the label, not replacing
        it. DO NOT PUBLISH prints above one root in both cases."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="secret-branch", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("done", branch="secret-branch", ts="2026-08-01T10:05:00.000Z"),
        ])

        disclosed_args = _review_round_cost_args(this_repo=True)
        disclosed_args._this_repo_slugs = ["-home-user-repo-a"]
        _mod.cmd_review_round_cost(disclosed_args)
        disclosed_out = capsys.readouterr().out
        assert "account-1/secret-branch" in disclosed_out
        assert _mod._DO_NOT_PUBLISH_BANNER in disclosed_out

        _mod.cmd_review_round_cost(_review_round_cost_args())
        redacted_out = capsys.readouterr().out
        assert "account-1/branch-1" in redacted_out
        assert "secret-branch" not in redacted_out
        assert _mod._DO_NOT_PUBLISH_BANNER in redacted_out

    def test_skill_with_zero_invocations_anywhere_reports_no_data_mean(self, fake_projects, capsys):
        """A fixture corpus where two of the three REVIEW_SKILLS have zero
        invocations anywhere still renders without error, with a defined
        "no data" mean for each -- never a computed zero or a
        division-by-zero."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert "plan-review no data" in out
        assert "ready-for-review no data" in out

    def test_skill_tool_use_args_field_never_surfaces_in_output(self, fake_projects, capsys):
        """Only input["skill"] is ever extracted from a Skill tool_use block
        -- input["args"] can carry an absolute local path and must never
        reach review-round-cost's output, mirroring cmd_skill_invocation's
        own extraction contract."""
        secret_path = "/home/<username>/secret-private-project/notes.md"
        skill_block = _skill_block("s1", "code-review")
        skill_block["input"]["args"] = secret_path
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[skill_block],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert secret_path not in out

    def test_skill_flag_narrows_printed_rounds_to_one_skill(self, fake_projects, capsys):
        """--skill NAME narrows what is printed to that one REVIEW_SKILLS
        member, ignoring the other two entirely -- it is a post-hoc output
        filter over already-detected rounds, never a detection-time
        narrowing (row 19)."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("u1", branch="feat", ts="2026-08-01T10:01:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:02:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("u2", branch="feat", ts="2026-08-01T10:03:00.000Z"),
        ])
        _mod.cmd_review_round_cost(_review_round_cost_args(skill="plan-review"))
        out = capsys.readouterr().out
        assert "rounds=1  (code-review=0  plan-review=1  ready-for-review=0)" in out

    def test_gitbranch_drift_inside_round_window_prints_coherent_reconciliation_line(self, fake_projects, capsys):
        """(cumulative /code-review finding, revision -- row 12a) The same
        gitBranch-drift fixture at the CLI layer: the printed
        reconciliation line reads 100.0% (all of the branch's dollars fell
        inside its one round) and the corpus-wide "Non-round dollars"
        footer is not negative -- the pre-revision accumulator produced a
        200%/-100% reconciliation-line output on this exact shape, so it
        stays a permanent required case rather than a one-off check."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _priced(
                "claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T10:01:00.000Z",
                content=[_agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:02:00.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat-b", ts="2026-08-01T10:01:10.000Z")],
        )
        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert "round $1.60 of $1.60 branch $ (100.0%)" in out
        assert "Non-round dollars: 0.0% of branch dollars fell outside every round window" in out

    def test_totals_mean_and_non_round_footer_aggregate_across_two_branches(self, fake_projects, capsys):
        """The Totals/Mean rounds per branch/Non-round dollars footer lines
        aggregate and average across every reported branch, not just the
        last one rendered. Also regression coverage for the last_branch
        carry-forward fix: an inline isSidechain record's own dollars must
        land on feat-a (its real branch), not a phantom worktree-agent-*
        bucket, or this test's own expected totals would be wrong."""
        sidechain_rec = _priced(
            "claude-sonnet-5", input=150_000, branch="worktree-agent-zzz",
            ts="2026-08-01T10:00:30.000Z",
        )
        sidechain_rec["isSidechain"] = True
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T09:00:00.000Z"),  # non-round: $0.20
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),  # round open: $0.20
            sidechain_rec,  # $0.30, inline sidechain, still inside the window
            _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),  # closes it
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced(
                "claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "plan-review")],
            ),  # round open, no non-round activity: $0.40
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
        ])

        branch_a_total = 0.20 + 0.20 + 0.30
        branch_a_round = 0.20 + 0.30
        branch_b_total = 0.40
        branch_b_round = 0.40
        total_branch_dollars = branch_a_total + branch_b_total
        total_round_dollars = branch_a_round + branch_b_round
        non_round_dollars = total_branch_dollars - total_round_dollars
        expected_pct = render._pct_of(non_round_dollars, total_branch_dollars)

        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert "Totals: 2 branches, 2 rounds (code-review=1  plan-review=1  ready-for-review=0)" in out
        assert "Mean rounds per branch: 1.00" in out
        assert f"Non-round dollars: {expected_pct} of branch dollars fell outside every round window" in out
