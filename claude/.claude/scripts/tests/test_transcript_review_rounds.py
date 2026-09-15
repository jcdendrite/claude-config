"""Tests for transcript_analysis/review_rounds.py (review-round-cost)."""
import importlib.util
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from transcript_analysis import corpus, render, review_rounds, scope

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
    pooled: bool = False,
    config_dir: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "until": until,
        "skill": skill,
        "pooled": pooled,
        "config_dir": config_dir,
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

    def test_unrecognized_model_turn_inside_dispatched_subagent_increments_unpriced_turns(self, fake_projects):
        """_price_dispatch has its own unpriced_turns accumulation,
        independent of the main-thread loop's equivalent above -- an
        unrecognized-model turn inside a dispatched subagent's own
        transcript increments the round's unpriced_turns and contributes
        no dollars to agent_dollars."""
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
            [
                _priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:10.000Z"),  # $1.00
                _priced(
                    "some-unrecognized-model-id", input=100_000, branch="feat",
                    ts="2026-08-01T10:00:20.000Z",
                ),
            ],
        )
        data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["agent_dollars"] == pytest.approx(1.00)
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
        """A round's dollars must land in the branch_totals bucket the
        round itself is keyed to even when the session's own gitBranch
        changes between the round's opening record and its window end. One
        session, one open window: the opening record fires the code-review
        Skill block on feat-a; a later main-thread record inside the same
        window, on feat-b, also dispatches a subagent (exercises both
        accumulation sites -- the priced turn and _price_dispatch's return
        -- not just the first); a fresh user prompt then closes the
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
        proving the fix changes in-window attribution only, leaving
        out-of-window (non-round) attribution exactly as it was."""
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
        """skill_filter never narrows round-window detection -- only which
        already-detected rounds are returned. Fixture is the shape a
        detection-time narrowing would mis-bound: a code-review invocation
        immediately followed by a plan-review invocation, with no fresh
        user prompt between them, so the plan-review opener is the
        code-review round's own closing bound. If skill_filter narrowed
        detection instead, excluding plan-review would stop it from
        closing the code-review window, letting the code-review round's
        own turns extend past it and inflate its own dollar figure.
        Passing skill_filter={"code-review"} must yield the same
        code-review round -- identical main_dollars, agent_dollars, and
        agents -- as passing no filter at all. Also asserts
        list-membership directly (mirroring
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

    def test_root_idx_keeps_identically_named_branches_in_different_roots_from_merging(
        self, tmp_path, monkeypatch,
    ):
        """(root_idx, branch) keying keeps two different roots'
        identically-named branches from merging into one branch_totals/
        round row -- two roots here each carry a session on a branch named
        "feat", and each must land in its own distinct branch_totals entry
        and its own round, keyed by differing root_idx."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced(
                "claude-sonnet-5", input=200_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        resolved_roots = [root.resolve() for root in roots]
        session_iter = list(corpus.iter_sessions(roots[0], "*")) + list(corpus.iter_sessions(roots[1], "*"))
        data = review_rounds.compute_review_round_costs(session_iter, resolved_roots=resolved_roots)
        assert {r["branch_key"] for r in data["rounds"]} == {(0, "feat"), (1, "feat")}
        assert set(data["branch_totals"]) == {(0, "feat"), (1, "feat")}
        assert data["branch_totals"][(0, "feat")] == pytest.approx(0.20)
        assert data["branch_totals"][(1, "feat")] == pytest.approx(0.40)


def _corpus_multi_window(fake_projects) -> None:
    """Skill-shape round immediately followed by a /slash-shape round, zero
    fresh-user-prompt records between them -- one session, two windows."""
    _write_jsonl(fake_projects / "sess-1.jsonl", [
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s1", "code-review")],
        ),
        _slash_user("plan-review", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        _priced("claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:02:00.000Z"),
        _user_msg("done", branch="feat", ts="2026-08-01T10:03:00.000Z"),
    ])


def _corpus_nested_dispatch(fake_projects) -> None:
    """A round whose own subagent dispatch itself dispatches a further
    nested subagent."""
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
    )
    nested_spawn_rec["isSidechain"] = True
    _write_subagent_dispatch(fake_projects, session_id, "agent-1", "a1", [nested_spawn_rec])
    nested_session_id = _nested_subagent_session_id(session_id, "agent-1")
    nested_rec = _priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:20.000Z")
    nested_rec["isSidechain"] = True
    _write_subagent_dispatch(fake_projects, nested_session_id, "agent-2", "n1", [nested_rec])


def _corpus_slash_path_open(fake_projects) -> None:
    """A /slash-shape round with no Skill tool_use block anywhere."""
    _write_jsonl(fake_projects / "sess-1.jsonl", [
        _slash_user("code-review", branch="feat", ts="2026-08-01T10:00:00.000Z"),
        _user_msg("done reviewing", branch="feat", ts="2026-08-01T10:05:00.000Z"),
    ])


def _corpus_dedup_affecting_requestid_run(fake_projects) -> None:
    """Two contiguous same-requestId records, each carrying a Skill
    tool_use block for the same skill -- models the harness splitting one
    API call's content across multiple JSONL records. Without dedup running
    before detection, this opens two rounds instead of one."""
    _write_jsonl(fake_projects / "sess-1.jsonl", [
        _priced(
            "claude-sonnet-5", input=50_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
            request_id="req-1", content=[_skill_block("s1", "code-review")],
        ),
        _priced(
            "claude-sonnet-5", input=50_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
            request_id="req-1", content=[_skill_block("s2", "code-review")],
        ),
        _user_msg("done", branch="feat", ts="2026-08-01T10:05:00.000Z"),
    ])


def _corpus_branch_carry_forward_mid_window(fake_projects) -> None:
    """A round's window drifts to a different gitBranch mid-window; the
    round's own branch stays the opening record's branch."""
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


def _corpus_non_round_dollar_interleaving(fake_projects) -> None:
    """Non-round dollars before and after one round window, plus a
    dispatch outside the window -- proves round detection isn't perturbed
    by unrelated dollar-bearing activity."""
    session_id = "sess-1"
    _write_jsonl(fake_projects / f"{session_id}.jsonl", [
        _priced("claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T09:00:00.000Z"),
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
        ),
        _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T11:00:00.000Z",
            content=[_agent_use("a2", "staff-sdet")],
        ),
    ])
    _write_subagent_dispatch(
        fake_projects, session_id, "agent-1", "a1",
        [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:10.000Z")],
    )
    _write_subagent_dispatch(
        fake_projects, session_id, "agent-2", "a2",
        [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T11:00:10.000Z")],
    )


# Every distinct round shape TestComputeReviewRoundCosts already exercises
# elsewhere in this file, reused for the bidirectional agreement guard below
# rather than one new hand-picked corpus.
_ROUND_SHAPE_CORPUS_BUILDERS = [
    pytest.param(_corpus_multi_window, id="multi_window_skill_then_slash"),
    pytest.param(_corpus_nested_dispatch, id="nested_dispatch"),
    pytest.param(_corpus_slash_path_open, id="slash_path_open"),
    pytest.param(_corpus_dedup_affecting_requestid_run, id="dedup_affecting_requestid_run"),
    pytest.param(_corpus_branch_carry_forward_mid_window, id="branch_carry_forward_mid_window"),
    pytest.param(_corpus_non_round_dollar_interleaving, id="non_round_dollar_interleaving"),
]


class TestComputeReviewRoundCounts:
    """compute_review_round_counts: count-only round detection, exercised
    directly against exact per-skill counts."""

    def test_skill_and_slash_invocations_both_counted(self, fake_projects):
        _corpus_multi_window(fake_projects)
        counts = review_rounds.compute_review_round_counts(_session_iter(fake_projects))
        assert counts == {"code-review": 1, "plan-review": 1, "ready-for-review": 0}

    def test_absent_skills_report_zero(self, fake_projects):
        """Every REVIEW_SKILLS member is a key in the result, zeros
        included, even when a skill has no invocations anywhere in scope."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:05:00.000Z"),
        ])
        counts = review_rounds.compute_review_round_counts(_session_iter(fake_projects))
        assert counts == {"code-review": 1, "plan-review": 0, "ready-for-review": 0}

    def test_branch_filter_narrows_counts_to_matching_branch(self, fake_projects):
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-b", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
        ])
        counts = review_rounds.compute_review_round_counts(
            _session_iter(fake_projects), branch_filter={"feat-a"},
        )
        assert counts == {"code-review": 1, "plan-review": 0, "ready-for-review": 0}

    def test_dedup_merges_split_requestid_run_before_counting(self, fake_projects):
        """Without dedup running before detection, this would open two
        rounds instead of one -- guards compute_review_round_counts's own
        dedup-before-detection contract."""
        _corpus_dedup_affecting_requestid_run(fake_projects)
        counts = review_rounds.compute_review_round_counts(_session_iter(fake_projects))
        assert counts["code-review"] == 1


class TestReviewRoundCountsAgreesWithComputeReviewRoundCosts:
    """compute_review_round_counts must never disagree with
    compute_review_round_costs's own rounds list about how many rounds of
    each skill exist -- checked across every distinct round shape this
    file's dollar tests already exercise, not one new hand-picked corpus."""

    @pytest.mark.parametrize("build_corpus", _ROUND_SHAPE_CORPUS_BUILDERS)
    def test_per_skill_tally_matches_across_shapes(self, fake_projects, build_corpus):
        build_corpus(fake_projects)
        counts = review_rounds.compute_review_round_counts(_session_iter(fake_projects))
        costs_data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        expected = Counter(r["skill"] for r in costs_data["rounds"])
        assert counts == {skill: expected.get(skill, 0) for skill in review_rounds.REVIEW_SKILLS}


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
        narrowing."""
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
        """The same gitBranch-drift fixture at the CLI layer: the printed
        reconciliation line reads 100.0% (all of the branch's dollars fell
        inside its one round) and the corpus-wide "Non-round dollars"
        footer is not negative -- guards the round/branch-totals
        reconciliation identity under mid-window branch drift; kept as a
        permanent regression case, not a one-off check."""
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
        total_agent_dollars = 0.0  # no _agent_use dispatch in this fixture
        expected_agent_pct = render._pct_of(total_agent_dollars, total_branch_dollars)

        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out
        assert "Totals: 2 branches, 2 rounds (code-review=1  plan-review=1  ready-for-review=0)" in out
        assert "Mean rounds per branch: 1.00" in out
        assert f"Non-round dollars: {expected_pct} of branch dollars fell outside every round window" in out
        assert f"Reviewer-dispatch dollars: {expected_agent_pct} of branch dollars, inside round windows" in out

    def test_footer_is_partitioned_per_root_and_does_not_blend_dollars_or_round_counts_across_roots(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Under more than one declared root, the footer prints one
        account-<K>-prefixed block per root, summed only from that root's
        own branches. A blended block would let a reader subtract out one
        account's known spend to recover the other's. Root A's round also
        carries a resolved (non-dangling) subagent dispatch, so its
        Reviewer-dispatch-dollars figure is nonzero while root B's stays at
        0%. A shared accumulator that summed total_agent_dollars across
        roots instead of partitioning it would leak root A's dollars into
        root B's line -- indistinguishable from a correct partition if both
        figures were 0%."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a1.jsonl", [
            _priced("claude-sonnet-5", input=100_000, branch="feat-a1", ts="2026-08-01T09:00:00.000Z"),  # non-round: $0.20
            _priced(  # round open: $0.20, also spawns dispatch a1
                "claude-sonnet-5", input=100_000, branch="feat-a1", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat-a1", ts="2026-08-01T10:01:00.000Z"),
        ])
        _write_subagent_dispatch(
            proj_a, "sess-a1", "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat-a1", ts="2026-08-01T10:00:30.000Z")],  # $1.00
        )
        _write_jsonl(proj_a / "sess-a2.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a2", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "code-review")],
            ),  # round, no non-round activity: $0.20
            _user_msg("thanks", branch="feat-a2", ts="2026-08-01T10:01:00.000Z"),
        ])
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced(
                "claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s3", "plan-review")],
            ),  # round, no non-round activity: $0.40
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
        ])

        root_a_agent_dollars = 1.00  # dispatch a1, priced above
        root_a_branch_dollars = 0.20 + 0.20 + 0.20 + root_a_agent_dollars
        root_a_round_dollars = 0.20 + 0.20 + root_a_agent_dollars
        root_a_pct = render._pct_of(root_a_branch_dollars - root_a_round_dollars, root_a_branch_dollars)
        root_a_agent_pct = render._pct_of(root_a_agent_dollars, root_a_branch_dollars)
        root_b_branch_dollars = 0.40
        root_b_round_dollars = 0.40
        root_b_pct = render._pct_of(root_b_branch_dollars - root_b_round_dollars, root_b_branch_dollars)
        root_b_agent_pct = render._pct_of(0.0, root_b_branch_dollars)  # no _agent_use dispatch in root B's fixture

        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out

        assert "account-1 Totals: 2 branches, 2 rounds (code-review=2  plan-review=0  ready-for-review=0)" in out
        assert "account-1 Mean rounds per branch: 1.00" in out
        assert f"account-1 Non-round dollars: {root_a_pct} of branch dollars fell outside every round window" in out
        assert f"account-1 Reviewer-dispatch dollars: {root_a_agent_pct} of branch dollars, inside round windows" in out
        assert "account-2 Totals: 1 branches, 1 rounds (code-review=0  plan-review=1  ready-for-review=0)" in out
        assert "account-2 Mean rounds per branch: 1.00" in out
        assert f"account-2 Non-round dollars: {root_b_pct} of branch dollars fell outside every round window" in out
        assert f"account-2 Reviewer-dispatch dollars: {root_b_agent_pct} of branch dollars, inside round windows" in out
        # Asserts the footer never blends root A's and root B's totals into one combined figure.
        assert "3 branches, 3 rounds" not in out
        assert "code-review=2  plan-review=1" not in out

    def test_footer_partitions_mean_per_round_dangling_and_unpriced_turns_across_roots(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Extends the partition test above to the three footer lines it
        doesn't cover: Mean $ per round, Dangling dispatches, and Unpriced
        turns. Root A's round carries one dangling dispatch and one
        unpriced-model turn. Root B's carries neither, so a shared
        accumulator bug would leak root A's nonzero counts into root B's
        line."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced(  # round open: $0.20, spawns dangling dispatch a1
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
            # unrecognized model: unpriced turn, still inside the window
            _priced("some-unrecognized-model-id", input=100_000, branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
            _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:02:00.000Z"),
        ])
        # No _write_subagent_dispatch call for tool_use_id "a1" -- dangling.
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced(  # round, no dangling dispatch or unpriced turn: $0.40
                "claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
        ])

        _mod.cmd_review_round_cost(_review_round_cost_args())
        out = capsys.readouterr().out

        assert "account-1 Mean $ per round — code-review 0.20  plan-review no data  ready-for-review no data" in out
        assert "account-2 Mean $ per round — code-review no data  plan-review 0.40  ready-for-review no data" in out
        assert "account-1 Dangling dispatches inside round windows: 1 (no readable meta.json/jsonl pair)" in out
        assert "account-2 Dangling dispatches inside round windows: 0 (no readable meta.json/jsonl pair)" in out
        assert "account-1 Unpriced turns inside round windows: 1" in out
        assert "account-2 Unpriced turns inside round windows: 0" in out

    def test_root_with_no_rounds_left_after_skill_filter_is_absent_from_footer(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Filtering by --skill narrows root B's rounds to zero while root A
        keeps its own. A root with no rounds left in scope is absent from
        the footer rather than printed as a zeroed block. Asserts exactly
        one account-<K>-prefixed footer block prints, and root B's own
        ordinal never appears in output."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
        ])
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced(
                "claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
        ])

        _mod.cmd_review_round_cost(_review_round_cost_args(skill="code-review"))
        out = capsys.readouterr().out

        assert "account-1 Totals: 1 branches, 1 rounds (code-review=1  plan-review=0  ready-for-review=0)" in out
        assert out.count("Totals:") == 1
        assert "account-2" not in out


def _asymmetric_two_branch_pooled_totals() -> list[review_rounds._PooledBranchTotals]:
    """Two branches with deliberately unequal branch_dollars (0.40 and
    1.00).

    "Share of sums" (correct) and "mean of per-branch shares" (a plausible
    regression) disagree on this fixture: 57.1% vs 55.0%. An equal-weight
    fixture would make the two computations coincide, hiding the
    regression.

    Reused directly here, and via an equivalent priced-round JSONL fixture
    in TestCmdReviewRoundCostPooled's own point-estimate test, so both
    layers check the same arithmetic from a different entry point.
    """
    branch_a = review_rounds._PooledBranchTotals(
        round_dollars=0.20, agent_dollars=0.0, branch_dollars=0.40,
        skill_round_counts={"code-review": 1, "plan-review": 0, "ready-for-review": 0},
        skill_round_dollars={"code-review": 0.20, "plan-review": 0.0, "ready-for-review": 0.0},
        rounds_with_dangling=0, rounds_with_unpriced=0, round_count=1,
    )
    branch_b = review_rounds._PooledBranchTotals(
        round_dollars=0.60, agent_dollars=0.0, branch_dollars=1.00,
        skill_round_counts={"code-review": 1, "plan-review": 0, "ready-for-review": 0},
        skill_round_dollars={"code-review": 0.60, "plan-review": 0.0, "ready-for-review": 0.0},
        rounds_with_dangling=0, rounds_with_unpriced=0, round_count=1,
    )
    return [branch_a, branch_b]


class TestBootstrapShareIntervals:
    """Pure-math tests for --pooled's aggregation and bootstrap helpers.

    2-5 synthetic per_branch tuples, no _write_jsonl, no fixture corpus,
    no CLI -- the fast layer for the feature's actual arithmetic risk.
    TestCmdReviewRoundCostPooled below is scoped to what only that layer
    can prove (wiring, refusal enforcement, redaction, banner
    suppression), not to re-proving the math.
    """

    def test_pooled_branch_aggregates_sums_every_accumulated_field(self):
        """_pooled_branch_aggregates elementwise-sums a hand-built
        per_branch list across every _PooledBranchTotals field, including
        the two per-skill dicts summed independently of one another."""
        a = review_rounds._PooledBranchTotals(
            round_dollars=1.0, agent_dollars=0.5, branch_dollars=4.0,
            skill_round_counts={"code-review": 1, "plan-review": 2, "ready-for-review": 0},
            skill_round_dollars={"code-review": 0.6, "plan-review": 0.4, "ready-for-review": 0.0},
            rounds_with_dangling=1, rounds_with_unpriced=0, round_count=3,
        )
        b = review_rounds._PooledBranchTotals(
            round_dollars=2.0, agent_dollars=1.5, branch_dollars=6.0,
            skill_round_counts={"code-review": 0, "plan-review": 1, "ready-for-review": 2},
            skill_round_dollars={"code-review": 0.0, "plan-review": 1.0, "ready-for-review": 1.0},
            rounds_with_dangling=0, rounds_with_unpriced=2, round_count=3,
        )
        summed = review_rounds._pooled_branch_aggregates([a, b])
        assert summed.round_dollars == 3.0
        assert summed.agent_dollars == 2.0
        assert summed.branch_dollars == 10.0
        assert summed.skill_round_counts == {"code-review": 1, "plan-review": 3, "ready-for-review": 2}
        assert summed.skill_round_dollars == {"code-review": 0.6, "plan-review": 1.4, "ready-for-review": 1.0}
        assert summed.rounds_with_dangling == 1
        assert summed.rounds_with_unpriced == 2
        assert summed.round_count == 6

    def test_pooled_shares_computes_every_stat_key_against_a_hand_computed_value(self):
        """One hand-built aggregate covering every _POOLED_STAT_KEYS entry
        with pairwise-distinct values, including gap_dangling and
        gap_unpriced.

        Every other fixture in this file gives rounds_with_dangling and
        rounds_with_unpriced the same 0/1 ratio, so a swap between those
        two keys in _pooled_shares would go undetected by them alone.
        """
        agg = review_rounds._PooledBranchTotals(
            round_dollars=60.0, agent_dollars=20.0, branch_dollars=200.0,
            skill_round_counts={"code-review": 6, "plan-review": 14, "ready-for-review": 30},
            skill_round_dollars={"code-review": 9.0, "plan-review": 39.0, "ready-for-review": 12.0},
            rounds_with_dangling=9, rounds_with_unpriced=22, round_count=50,
        )
        shares = review_rounds._pooled_shares(agg)
        expected = {
            "spend_inside": 30.0,
            "spend_outside": 70.0,
            "spend_reviewer_only": 10.0,
            "skill_spend:code-review": 15.0,
            "skill_spend:plan-review": 65.0,
            "skill_spend:ready-for-review": 20.0,
            "skill_rounds:code-review": 12.0,
            "skill_rounds:plan-review": 28.0,
            "skill_rounds:ready-for-review": 60.0,
            "gap_dangling": 18.0,
            "gap_unpriced": 44.0,
        }
        assert set(expected) == set(review_rounds._POOLED_STAT_KEYS)
        assert len(set(expected.values())) == len(expected)  # every value is pairwise distinct
        for key, expected_value in expected.items():
            assert shares[key] == pytest.approx(expected_value)

    def test_resample_percentile_matches_documented_index_formula(self):
        """round(0.025 * (B-1)) / round(0.975 * (B-1)), checked against a
        small fixed B (9) where the indices are easy to hand-verify:
        lo_idx = round(0.2) = 0, hi_idx = round(7.8) = 8."""
        sample = [float(i) for i in range(9)]
        lo, hi = review_rounds._resample_percentile(sample)
        assert (lo, hi) == (0.0, 8.0)

    def test_bootstrap_share_intervals_deterministic_and_matches_hand_computed_point(self):
        """Same seed, same fixture, two direct in-process calls agree
        exactly -- the in-process half of the determinism property (the
        cross-process half is TestCmdReviewRoundCostPooled's own
        subprocess test).

        Also asserts the point estimate against the hand-computed value
        documented on _asymmetric_two_branch_pooled_totals: determinism
        alone would pass a function that always returns 0.0.
        """
        per_branch = _asymmetric_two_branch_pooled_totals()
        first = review_rounds._bootstrap_share_intervals(per_branch)
        second = review_rounds._bootstrap_share_intervals(per_branch)
        assert first == second

        point, lo, hi = first["spend_inside"]
        share_of_sums = round(100 * (0.20 + 0.60) / (0.40 + 1.00), 1)  # 57.1 -- correct
        mean_of_shares = round((50.0 + 60.0) / 2, 1)  # 55.0 -- the regression this fixture rules out
        assert share_of_sums != mean_of_shares
        assert round(point, 1) == share_of_sums
        assert lo <= point <= hi

    def test_fmt_share_with_ci_renders_numeric_and_both_degenerate_forms(self):
        """Numeric form, plus both degenerate parentheticals -- the "95% CI"
        frame is a fixed literal shared by every line (numeric and
        degenerate alike, per the grammar regex below), but neither
        degenerate reason clause past the em dash contains a digit."""
        assert review_rounds._fmt_share_with_ci(40.0, 35.0, 45.0) == "40.0% (95% CI 35.0-45.0%)"

        too_few = review_rounds._fmt_share_with_ci(None, None, None)
        assert too_few == "(95% CI not computed — too few branches in scope)"
        assert not any(c.isdigit() for c in too_few.rsplit("—", 1)[-1])

        zero_denom = review_rounds._fmt_share_with_ci(0.0, None, None)
        assert zero_denom == "(95% CI not computed — no priced branch spend)"
        assert not any(c.isdigit() for c in zero_denom.rsplit("—", 1)[-1])


def _pooled_two_root_fixture(tmp_path, monkeypatch) -> list[Path]:
    """Two declared roots, two branches each (four total), one
    distinctively named.

    The cross-process determinism test elsewhere in this file has its own
    >=4-branch requirement; this fixture happens to satisfy it too, though
    it isn't used there.

    Reused by the grammar, totals-absence, banner-suppression,
    branch-name-leak, header, and stderr-diagnostic tests below, all of
    which need the same non-degenerate, >1-root, >1-branch shape.
    """
    roots = _two_declared_roots(tmp_path, monkeypatch)
    proj_a = roots[0] / "-home-user-repo-a"
    proj_a.mkdir(parents=True)
    _write_jsonl(proj_a / "sess-a1.jsonl", [
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat-a1-secret-branch", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s1", "code-review")],
        ),
        _user_msg("thanks", branch="feat-a1-secret-branch", ts="2026-08-01T10:01:00.000Z"),
    ])
    _write_jsonl(proj_a / "sess-a2.jsonl", [
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat-a2", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s2", "plan-review")],
        ),
        _user_msg("thanks", branch="feat-a2", ts="2026-08-01T10:01:00.000Z"),
    ])
    proj_b = roots[1] / "-home-user-repo-b"
    proj_b.mkdir(parents=True)
    _write_jsonl(proj_b / "sess-b1.jsonl", [
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat-b1", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s3", "ready-for-review")],
        ),
        _user_msg("thanks", branch="feat-b1", ts="2026-08-01T10:01:00.000Z"),
    ])
    _write_jsonl(proj_b / "sess-b2.jsonl", [
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat-b2", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s4", "code-review")],
        ),
        _user_msg("thanks", branch="feat-b2", ts="2026-08-01T10:01:00.000Z"),
    ])
    return roots


def _pooled_two_root_discriminating_skill_fixture(tmp_path, monkeypatch) -> list[Path]:
    """Two declared roots, root A entirely code-review and root B entirely
    plan-review.

    The correctly-pooled code-review share is 50.0%, versus 100.0% for
    root A alone and 0.0% for root B alone. This fixture distinguishes
    correct cross-root pooling from a one-root-only regression. An
    equal-mix fixture would not: every root's own share would already
    agree with the pooled share.
    """
    roots = _two_declared_roots(tmp_path, monkeypatch)
    proj_a = roots[0] / "-home-user-repo-a"
    proj_a.mkdir(parents=True)
    _write_jsonl(proj_a / "sess-a.jsonl", [
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s1", "code-review")],
        ),
        _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
    ])
    proj_b = roots[1] / "-home-user-repo-b"
    proj_b.mkdir(parents=True)
    _write_jsonl(proj_b / "sess-b.jsonl", [
        _priced(
            "claude-sonnet-5", input=100_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
            content=[_skill_block("s2", "plan-review")],
        ),
        _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
    ])
    return roots


# _render_pooled_block's own figure-line format is `f"    {label:<30}{value}"`
# -- 4 spaces of indent, then a 30-char label field. TestCmdReviewRoundCostPooled's
# grammar test slices on this exact width instead of guessing at whitespace.
_POOLED_FIGURE_LABEL_FIELD_END = 4 + 30


class TestCmdReviewRoundCostPooled:
    """cmd_review_round_cost's --pooled render path: grammar/redaction
    enforcement, refusal-table coverage, and CLI-level correctness. See
    TestBootstrapShareIntervals above for the underlying arithmetic."""

    def test_grammar_every_figure_line_is_digit_free_or_matches_share_ci_regex(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The convention-enforcing test: no `$` in the actual figure
        section, and every figure line after the literal _POOLED_CAPTION
        constant either has no digit at all or matches the share/CI
        grammar exactly.

        Slicing on the _POOLED_CAPTION constant excludes the caption
        paragraph above it, which has its own compliant digit (e.g.
        "2,000-resample"). It also excludes the earlier publication-pointer
        paragraph, whose illustrative "$/PR rate" prose names a hypothetical
        figure published elsewhere, never one this block itself prints.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        block = capsys.readouterr().out

        _, sep, after_caption = block.partition(review_rounds._POOLED_CAPTION)
        assert sep, "_POOLED_CAPTION not found verbatim in the printed block"
        assert "$" not in after_caption
        lines = [line for line in after_caption.splitlines() if line.strip()]

        section_headers = [line.strip() for line in lines if not line.startswith("    ")]
        figure_lines = [line for line in lines if line.startswith("    ")]

        assert section_headers == [
            "Share of branch spend", "Round-window spend by skill",
            "Rounds by skill", "Rounds affected by a data-quality gap",
        ]

        expected_labels = (
            "inside round windows", "outside every round window", "reviewer dispatches only",
            "code-review", "plan-review", "ready-for-review",
            "code-review", "plan-review", "ready-for-review",
            "dangling dispatch", "unpriced turn",
        )
        found_labels = tuple(line[4:_POOLED_FIGURE_LABEL_FIELD_END].strip() for line in figure_lines)
        assert found_labels == expected_labels
        # Checked against the raw block, not just found_labels: a future
        # before/after-split label could slip in anywhere in the pooled
        # render without ever matching a pinned expected_labels entry.
        assert not re.search(r"\b(before|after|pivot)\b", block)

        figure_re = re.compile(r"^\d{1,3}\.\d% \(95% CI (\d{1,3}\.\d-\d{1,3}\.\d%|not computed — [a-z ]+)\)$")
        for line in figure_lines:
            remainder = line[_POOLED_FIGURE_LABEL_FIELD_END:].strip()
            assert not any(c.isdigit() for c in remainder) or figure_re.match(remainder)

    def test_no_mean_rounds_per_branch_or_totals_line(self, tmp_path, monkeypatch, capsys):
        """A branch is declined as a countable or reportable unit entirely
        under --pooled: the existing footer's Mean-rounds-per-branch line,
        and the per-branch Totals: line, have no pooled counterpart in any
        form -- asserted by absence."""
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        assert "Mean rounds per branch" not in out
        assert "Totals:" not in out

    def test_banner_suppressed_under_pooled_but_present_without_it(self, tmp_path, monkeypatch, capsys):
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        pooled_out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER not in pooled_out

        _mod.cmd_review_round_cost(_review_round_cost_args())
        unpooled_out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER in unpooled_out

    def test_no_branch_name_leak(self, tmp_path, monkeypatch, capsys):
        """A distinctively-named fixture branch does not appear in pooled
        output -- presence in the --this-repo-disclosed render (proving the
        fixture really contains it) and absence from the pooled render are
        asserted separately, matching the existing --this-repo-disclosed/
        redacted pairing convention used elsewhere in this file (e.g.
        TestCmdReviewRoundCost.test_branch_label_raw_under_this_repo_and_redacted_otherwise_multi_root)."""
        _pooled_two_root_fixture(tmp_path, monkeypatch)

        disclosed_args = _review_round_cost_args(this_repo=True)
        disclosed_args._this_repo_slugs = ["-home-user-repo-a", "-home-user-repo-b"]
        _mod.cmd_review_round_cost(disclosed_args)
        disclosed_out = capsys.readouterr().out
        assert "feat-a1-secret-branch" in disclosed_out

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        pooled_out = capsys.readouterr().out
        assert "feat-a1-secret-branch" not in pooled_out

    def test_pooled_header_is_exact_fixed_string_and_no_root_count_pattern_anywhere(
        self, tmp_path, monkeypatch, capsys,
    ):
        """An exact-string assertion, not merely digit-free, since
        scope_label can no longer vary once --projects and --this-repo are
        both refused.

        Also asserts absence, across the entire block, of any
        `_root_count_desc`-shaped substring. A test that only checks the
        sanitized line is *present* would still pass if an unsanitized,
        root-count-bearing header also printed ahead of it.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        assert out.splitlines()[0] == "REVIEW ROUND COST SOURCES (*; pooled)"
        assert not re.search(r"\d+\s+roots?\b", out)

    def test_pooled_publication_and_refusal_pointers_cite_a_real_heading(self):
        """_POOLED_PUBLICATION_POINTER and _POOLED_REFUSAL_DOC_POINTER cite
        docs/private-project-redaction.md by heading text embedded in a
        plain Python string, not the backtick-quoted, single-line markdown
        citation grammar claude-skills/skills/tests/test_skills.py's
        citation-resolution tests check. This .py file sits outside both
        that test's scanned skill corpus and
        test_tooling_measurement_citation_resolves_to_real_heading's
        docs/*.md parametrize list, so a stale heading here would otherwise
        go unnoticed by every other citation-resolution test.
        """
        repo_root = Path(__file__).resolve().parents[4]
        doc_lines = (repo_root / "docs" / "private-project-redaction.md").read_text().splitlines()
        doc_headings: set[str] = set()
        in_fence = False
        for line in doc_lines:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = re.match(r"^#{1,6}\s+(.+)$", line)
            if match:
                doc_headings.add(match.group(1).strip())
        for pointer in (
            review_rounds._POOLED_PUBLICATION_POINTER,
            review_rounds._POOLED_REFUSAL_DOC_POINTER,
        ):
            cited = re.search(r'§\s+"([^"\n]+)"', pointer)
            assert cited, f"{pointer!r} does not cite a heading in the § \"...\" form"
            assert cited.group(1) in doc_headings, (
                f"{pointer!r} cites heading {cited.group(1)!r}, which does not "
                "exist in docs/private-project-redaction.md"
            )

    def test_cross_root_pooling_has_no_account_label_and_reflects_both_roots(
        self, tmp_path, monkeypatch, capsys,
    ):
        """rounds under two roots produce one block with no account- label,
        and a code-review rounds-by-skill share reflecting the pool
        (50.0%) rather than either root's own share alone (100.0% for the
        code-review-only root, 0.0% for the plan-review-only root)."""
        _pooled_two_root_discriminating_skill_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        assert "account-" not in out
        assert re.search(r"code-review\s+50\.0% \(95% CI", out)
        assert re.search(r"plan-review\s+50\.0% \(95% CI", out)

    def test_refuses_branches_flag(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, branches="feat"))
        assert exc.value.code == 2
        assert "--branches" in capsys.readouterr().err

    def test_refuses_non_default_projects_glob(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, projects="feat-*"))
        assert exc.value.code == 2
        assert "--projects" in capsys.readouterr().err

    def test_refuses_skill_flag(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, skill="code-review"))
        assert exc.value.code == 2
        assert "--skill" in capsys.readouterr().err

    def test_refuses_since_flag(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, since="2026-08-01"))
        assert exc.value.code == 2
        assert "--since/--until" in capsys.readouterr().err

    def test_refuses_until_flag(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, until="2026-08-01"))
        assert exc.value.code == 2
        assert "--since/--until" in capsys.readouterr().err

    def test_refuses_top_level_config_dir(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, config_dir="/tmp/some-other-account"))
        assert exc.value.code == 2
        assert "--config-dir" in capsys.readouterr().err

    def test_refuses_this_repo_on_single_root_fixture(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, this_repo=True))
        assert exc.value.code == 2
        assert "--this-repo" in capsys.readouterr().err

    def test_refuses_this_repo_on_two_root_fixture(self, tmp_path, monkeypatch, capsys):
        """--this-repo is checked in the flag block, not the root-count
        clause, so it must refuse identically on a machine that already has
        more than one declared root -- not only on the single-root fixture
        above."""
        _two_declared_roots(tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, this_repo=True))
        assert exc.value.code == 2
        assert "--this-repo" in capsys.readouterr().err

    def test_refuses_single_resolved_root_naming_declared_roots_file(self, fake_projects, capsys):
        """The one-resolved-root row has no flag to name, unlike every other
        refusal test above.

        Asserted against scope.TRANSCRIPT_CONFIG_DIRS_LABEL's actual value
        (imported and compared, never a hardcoded duplicate).

        The plain single-root path is the only path that ever reaches the
        root-count clause: the --this-repo refusal fires first when that
        flag is set, even on this same single-root fixture.
        """
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert scope.TRANSCRIPT_CONFIG_DIRS_LABEL in err

    def test_render_pooled_block_called_directly_still_refuses(self, tmp_path, monkeypatch):
        """Defense-in-depth, layer 2: bypasses cmd_review_round_cost's own
        CLI-boundary refusal entirely by calling _render_pooled_block
        directly, the way this module's own tests (and any other direct
        caller) do."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        args = _review_round_cost_args(pooled=True, branches="feat")
        with pytest.raises(SystemExit) as exc:
            review_rounds._render_pooled_block(args, roots, "*", [], {})
        assert exc.value.code == 2

    def test_pooled_output_is_byte_identical_across_two_separate_subprocesses(self, tmp_path):
        """Two independently hash-seeded real `python3` invocations, not two
        in-process calls: PYTHONHASHSEED is fixed once per interpreter
        process, so two in-process calls would share a hash seed even if a
        `set` iteration somewhere in the pooled path varied silently
        across real, separate runs.

        Four branches, not the two-root/two-branch shape reused elsewhere
        in this file: with only two branches there are only two possible
        orderings, so a fresh hash seed per process would have a
        non-trivial chance of agreeing by luck even with the bug present.
        """
        acct_a = tmp_path / "acct-a"
        proj_a = acct_a / "projects" / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        acct_b = tmp_path / "acct-b"
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a1", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("thanks", branch="feat-a1", ts="2026-08-01T10:01:00.000Z"),
        ])
        _write_jsonl(proj_a / "sess-a2.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a2", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("thanks", branch="feat-a2", ts="2026-08-01T10:01:00.000Z"),
        ])
        _write_jsonl(proj_b / "sess-b1.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-b1", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s3", "ready-for-review")],
            ),
            _user_msg("thanks", branch="feat-b1", ts="2026-08-01T10:01:00.000Z"),
        ])
        _write_jsonl(proj_b / "sess-b2.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-b2", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s4", "code-review")],
            ),
            _user_msg("thanks", branch="feat-b2", ts="2026-08-01T10:01:00.000Z"),
        ])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(acct_a),
            "TRANSCRIPT_CONFIG_DIRS_FILE": str(roots_file),
        }

        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(_SCRIPT), "review-round-cost", "--pooled"],
                capture_output=True, text=True, timeout=60, env=env,
            )

        first = _run()
        second = _run()
        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        assert first.stdout == second.stdout

    def test_point_estimate_is_share_of_sums_not_mean_of_per_branch_shares(
        self, tmp_path, monkeypatch, capsys,
    ):
        """CLI-layer counterpart of _asymmetric_two_branch_pooled_totals's
        own share-of-sums-vs-mean-of-shares check, via an equivalent
        priced-round JSONL fixture instead of a hand-built
        _PooledBranchTotals list."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T09:00:00.000Z"),  # non-round: $0.20
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),  # round: $0.20
            _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
        ])
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T09:00:00.000Z"),  # non-round: $0.40
            _priced(
                "claude-sonnet-5", input=300_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),  # round: $0.60
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
        ])

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out

        share_of_sums = round(100 * (0.20 + 0.60) / (0.40 + 1.00), 1)  # 57.1 -- correct
        mean_of_shares = round((50.0 + 60.0) / 2, 1)  # 55.0 -- the regression this fixture rules out
        assert share_of_sums != mean_of_shares
        match = re.search(r"inside round windows\s+(\d+\.\d)% \(95% CI (\d+\.\d)-(\d+\.\d)%\)", out)
        assert match is not None
        point = float(match.group(1))
        assert point == share_of_sums
        lo, hi = float(match.group(2)), float(match.group(3))
        assert lo <= point <= hi

    def test_degenerate_single_branch_prints_too_few_branches_wording_with_no_digit(
        self, tmp_path, monkeypatch, capsys,
    ):
        """--pooled requires more than one resolved *root*, but the
        too-few-branches wording is about pooled *branches* -- this fixture
        satisfies the root-count refusal gate with two declared roots while
        leaving only one branch with an in-scope round."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
        ])

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert all(line.strip().endswith("(95% CI not computed — too few branches in scope)") for line in too_few_lines)
        # "95% CI" is a fixed literal frame shared by every line (numeric and
        # degenerate alike); only the reason clause past the em dash must be
        # digit-free.
        assert not any(c.isdigit() for line in too_few_lines for c in line.rsplit("—", 1)[-1])

    def test_degenerate_zero_priced_branch_dollars_does_not_raise(self, tmp_path, monkeypatch, capsys):
        """Two branches, each with an in-scope round but zero priced dollars
        (an unrecognized-model turn) -- proves _render_pooled_block never
        raises ZeroDivisionError, printing the zero-denominator wording for
        every dollar-based share instead."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced(
                "some-unrecognized-model-id", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
        ])
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced(
                "some-unrecognized-model-id", input=100_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "plan-review")],
            ),
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
        ])

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        assert "(95% CI not computed — no priced branch spend)" in out

    def test_degenerate_branch_with_no_in_scope_rounds_is_excluded_from_the_pool(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The pooled denominator only ever includes a branch key present
        in the in-scope rounds list, mirroring the existing per-root
        footer's own accumulation.

        A branch with priced non-round spend but zero rounds must not
        silently enter the pool via branch_totals alone. No other test in
        this class exercises this shape.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        proj_a = roots[0] / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-a", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review")],
            ),  # round: $0.20
            _user_msg("thanks", branch="feat-a", ts="2026-08-01T10:01:00.000Z"),
        ])
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            # No review-skill invocation anywhere -- priced activity, zero rounds.
            _priced("claude-sonnet-5", input=500_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z"),
        ])

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        # feat-b's $1.00 never enters the pool: if it wrongly did (via
        # branch_totals alone), the pool would hold two branches instead of
        # one and "too few branches" would not fire.
        assert "(95% CI not computed — too few branches in scope)" in out

    def test_no_print_before_refusal_on_single_root_case(self, fake_projects, capsys):
        """The single-root refusal fires last in _pooled_scope_refusal's own
        check order, so it is the one most exposed to a reordering
        regression.

        Confirms no pooled header, publication pointer, or DO NOT PUBLISH
        banner ever reaches stdout ahead of the eventual exit(2). Every
        other refusal test in this class checks the exit and the message
        alone; only this one inspects the printed side effect.
        """
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        out = capsys.readouterr().out
        assert out == ""

    def test_pooled_scope_refusal_skips_root_count_clause_when_roots_is_none(self):
        """The layer-1, pre-resolution call path: no narrowing flag set,
        and roots=None (the default), returns None without raising -- no
        other test in this class exercises _pooled_scope_refusal directly
        with roots=None."""
        args = _review_round_cost_args(pooled=True)
        assert review_rounds._pooled_scope_refusal(args, roots=None) is None

    def test_pooled_run_prints_no_root_count_diagnostic_to_stderr(self, tmp_path, monkeypatch, capsys):
        """Regression test for the "scanning root N/M..." leak.

        scope's own multi-root diagnostic prints that line on stderr
        whenever more than one root is scanned, disclosing the resolved
        root count on the one output stream _pooled_resolved_scope_header
        doesn't reach -- stderr, not stdout.

        A successful two-root --pooled run must produce no digit on
        stderr at all.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        err = capsys.readouterr().err
        assert not any(c.isdigit() for c in err)

    def test_pooled_stderr_filter_passes_through_a_genuine_diagnostic(self, capsys):
        """The filter is selective, not a blanket stderr suppression: a
        genuine non-"scanning root" diagnostic emitted during the wrapped
        call still reaches stderr, while a "scanning root N/M..." line
        does not.
        """
        def fake_session_iter():
            print("scanning root 1/2...", file=sys.stderr)
            print(
                "_iter_scoped_sessions: cannot scan account-1 (Permission denied) — skipping",
                file=sys.stderr,
            )
            return
            yield  # pragma: no cover -- makes this a generator function

        review_rounds._pooled_compute_review_round_costs(fake_session_iter())
        err = capsys.readouterr().err
        assert "scanning root 1/2..." not in err
        assert "cannot scan account-1" in err

    def test_pooled_stderr_filter_reemits_buffered_lines_when_wrapped_call_raises(
        self, monkeypatch, capsys,
    ):
        """A diagnostic buffered before the wrapped call raises still reaches stderr."""
        def fake_compute(*args, **kwargs):
            print("cannot scan account-9 (Permission denied) — skipping", file=sys.stderr)
            raise RuntimeError("boom")

        monkeypatch.setattr(review_rounds, "compute_review_round_costs", fake_compute)
        with pytest.raises(RuntimeError):
            review_rounds._pooled_compute_review_round_costs()
        err = capsys.readouterr().err
        assert "cannot scan account-9" in err
