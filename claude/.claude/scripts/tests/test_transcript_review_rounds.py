"""Tests for transcript_analysis/review_rounds.py (review-round-cost)."""
import argparse
import importlib.util
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from helpers import REPO_ROOT, heading_texts, normalize_heading
from transcript_analysis import corpus, pricing, render, review_rounds, scope

from .conftest import (
    _agent_use,
    _priced,
    _skill_block,
    _slash_user,
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
    show_withheld: bool = False,
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
        "show_withheld": show_withheld,
        "config_dir": config_dir,
    })()


def _session_iter(fake_projects):
    return corpus.iter_sessions(fake_projects.parent, "*")


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

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_dispatch_under_an_unreadable_session_dir_is_counted_dangling_without_raising(self, fake_projects):
        """A session directory that cannot be searched makes the dispatch
        index unreadable, so the dispatch reads as dangling (no priced
        dollars, no resolved agent) instead of aborting the scan. The
        dispatch's own files exist and would be priced if readable."""
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
            [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:30.000Z")],  # $1.00
        )
        session_dir = fake_projects / session_id
        os.chmod(session_dir, 0o000)
        try:
            data = review_rounds.compute_review_round_costs(_session_iter(fake_projects))
        finally:
            os.chmod(session_dir, 0o755)
        assert len(data["rounds"]) == 1
        r = data["rounds"][0]
        assert r["dangling"] == 1
        assert r["agents"] == 0
        assert r["agent_dollars"] == pytest.approx(0.0)

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
    """Two branches with unequal branch_dollars (0.40/1.00) so share-of-sums
    (57.1%) and mean-of-shares (55.0%) diverge, catching a mean-of-shares
    regression; reused by TestCmdReviewRoundCostPooled's point-estimate test
    via an equivalent JSONL fixture.
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
    """Pure-math tests for --pooled's aggregation and bootstrap helpers: 2-5
    synthetic per_branch tuples, no _write_jsonl, no fixture corpus, no CLI.
    TestCmdReviewRoundCostPooled below covers wiring, refusal enforcement,
    redaction, and banner suppression instead -- not re-proving the math.
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

    def test_resample_percentile_at_the_half_index_rounding_boundary(self):
        """B=61 lands the high-index computation exactly on the .5 tie
        (0.975*60=58.5), a case that genuinely discriminates Python's
        round-half-to-even from round-half-up: 58 is even and 59 is odd, so
        round-half-to-even picks 58, while round-half-up would pick 59.
        B=9 above never reaches a tie at all.

        lo's own computation at this B (round(0.025*60)) isn't an exact
        tie. Floating-point error in `tail = (1 - _CI_LEVEL) / 2` pushes it
        just past 1.5, so only hi is asserted here.

        Production B varies per stat because a zero-denominator draw is
        dropped before indexing, so this boundary is reachable in
        practice."""
        sample = [float(i) for i in range(61)]
        lo, hi = review_rounds._resample_percentile(sample)
        assert hi == 58.0

    def test_bootstrap_share_intervals_deterministic_and_matches_hand_computed_point(self):
        """Same seed/fixture, two in-process calls agree exactly
        (cross-process half is the subprocess test below); also asserts the
        point estimate against the hand-computed value, since determinism
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

    def test_bootstrap_share_intervals_pin_the_enumerable_two_branch_interval(self):
        """Two branches give three possible resampled shares: both draws
        branch A (0.40 / 0.80 = 50.0%, probability 1/4), one of each
        (0.80 / 1.40 = 57.1%, probability 1/2), both draws branch B
        (1.20 / 2.00 = 60.0%, probability 1/4). Each tail holds 2.5% of the
        draws, far less than the 25% mass of each end block, so the bounds
        are the two end blocks' values whatever the RNG stream. This pins
        that resampling with replacement spans both branches, giving a
        non-degenerate interval at the support's extremes. The percentile
        indices and CI level are pinned by the _resample_percentile tests.
        Draw size and resample count are not pinned here.
        """
        point, lo, hi = review_rounds._bootstrap_share_intervals(
            _asymmetric_two_branch_pooled_totals()
        )["spend_inside"]
        assert (point, lo, hi) == pytest.approx((57.142857142857146, 50.0, 60.0))

    def test_bootstrap_share_intervals_with_partial_zero_denominator_draws(self):
        """Adds two zero-branch_dollars branches to the asymmetric
        two-branch fixture. A draw with an all-zero branch_dollars
        denominator has probability (2/4)**4 = 1/16, well above the 2.5%
        lower tail, so it reliably occurs and drops that draw's
        spend_inside share rather than counting it as 0.0. This confirms a
        share still gets a CI from fewer than _BOOTSTRAP_RESAMPLES values.
        gap_unpriced's denominator is round_count, nonzero on every branch
        here, so it never drops a draw and still gets a CI too.
        """
        zero_branch = review_rounds._PooledBranchTotals(
            round_dollars=0.0, agent_dollars=0.0, branch_dollars=0.0,
            skill_round_counts={"code-review": 0, "plan-review": 0, "ready-for-review": 1},
            skill_round_dollars={"code-review": 0.0, "plan-review": 0.0, "ready-for-review": 0.0},
            rounds_with_dangling=0, rounds_with_unpriced=0, round_count=1,
        )
        per_branch = _asymmetric_two_branch_pooled_totals() + [zero_branch, zero_branch]
        intervals = review_rounds._bootstrap_share_intervals(per_branch)

        point, lo, hi = intervals["spend_inside"]
        assert lo is not None
        assert 50.0 <= lo <= point <= hi <= 60.0

        _, gap_lo, _ = intervals["gap_unpriced"]
        assert gap_lo is not None

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

    def test_pooled_share_denominator_field_covers_exactly_pooled_stat_keys(self):
        """A key added to _POOLED_STAT_KEYS with no matching
        _POOLED_SHARE_DENOMINATOR_FIELD entry would raise KeyError inside
        _pooled_dominance_breach the first time a pool reaches it."""
        assert set(review_rounds._POOLED_SHARE_DENOMINATOR_FIELD) == set(review_rounds._POOLED_STAT_KEYS)

    @pytest.mark.parametrize("key", review_rounds._POOLED_STAT_KEYS)
    def test_pooled_share_denominator_field_names_the_field_pooled_shares_divides_by(self, key):
        """`_POOLED_SHARE_DENOMINATOR_FIELD` feeds the dominance-precision
        floor's account weights, so it must name the same field
        `_pooled_shares` actually divides by. Zeroing only the mapped field
        of an otherwise positive aggregate must make that share undefined
        (None). Zeroing either other denominator field must leave it
        defined. The three literal field names below are independent of the
        map under test.
        """
        positive = review_rounds._PooledBranchTotals(
            round_dollars=6.0, agent_dollars=2.0, branch_dollars=20.0,
            skill_round_counts={"code-review": 1, "plan-review": 2, "ready-for-review": 3},
            skill_round_dollars={"code-review": 1.0, "plan-review": 2.0, "ready-for-review": 3.0},
            rounds_with_dangling=1, rounds_with_unpriced=2, round_count=6,
        )
        assert review_rounds._pooled_shares(positive)[key] is not None
        mapped_field = review_rounds._POOLED_SHARE_DENOMINATOR_FIELD[key]

        assert review_rounds._pooled_shares(positive._replace(**{mapped_field: 0}))[key] is None
        for other_field in {"branch_dollars", "round_dollars", "round_count"} - {mapped_field}:
            assert review_rounds._pooled_shares(positive._replace(**{other_field: 0}))[key] is not None


def _pooled_two_root_fixture(tmp_path, monkeypatch) -> list[Path]:
    """Two declared roots, two branches each (four total), one
    distinctively named. Reused by the grammar, totals-absence,
    banner-suppression, branch-name-leak, header, and stderr-diagnostic
    tests below, all of which need the same non-degenerate, >1-root,
    >1-branch shape.
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
    """Root A mostly code-review (3 branches) plus one plan-review branch,
    root B the mirror image, so the correct pooled share (50%) differs
    from either root's own share (75%/25%) -- catching a one-root-only
    regression an equal-mix fixture would miss. Four branches per root,
    so the pool clears the four-branch bootstrap floor with room to
    spare. Every branch reports 100% of its own dollars inside a round
    window, so this fixture does not clear the dominance-precision floor
    (`_pooled_dominance_breach`); every test reading it stubs that floor.
    Every code-review branch costs $1.00 (500,000 input tokens) and every
    plan-review branch $2.00 (1,000,000 input tokens), so skill_spend's
    dollar-weighted share (33.3/66.7) can never coincide with
    skill_rounds's count-weighted share (50.0/50.0) this test means to
    match.
    """
    roots = _two_declared_roots(tmp_path, monkeypatch)
    input_tokens_for_skill = {"code-review": 500_000, "plan-review": 1_000_000}
    proj_a = roots[0] / "-home-user-repo-a"
    proj_a.mkdir(parents=True)
    for i, skill in enumerate(["code-review", "code-review", "code-review", "plan-review"]):
        branch = f"feat-a{i}"
        _write_jsonl(proj_a / f"sess-a{i}.jsonl", [
            _priced(
                "claude-sonnet-5", input=input_tokens_for_skill[skill], branch=branch,
                ts="2026-08-01T10:00:00.000Z", content=[_skill_block(f"sa{i}", skill)],
            ),
            _user_msg("thanks", branch=branch, ts="2026-08-01T10:01:00.000Z"),
        ])
    proj_b = roots[1] / "-home-user-repo-b"
    proj_b.mkdir(parents=True)
    for i, skill in enumerate(["plan-review", "plan-review", "plan-review", "code-review"]):
        branch = f"feat-b{i}"
        _write_jsonl(proj_b / f"sess-b{i}.jsonl", [
            _priced(
                "claude-sonnet-5", input=input_tokens_for_skill[skill], branch=branch,
                ts="2026-08-01T10:00:00.000Z", content=[_skill_block(f"sb{i}", skill)],
            ),
            _user_msg("thanks", branch=branch, ts="2026-08-01T10:01:00.000Z"),
        ])
    return roots


def _pooled_two_root_zero_priced_dollars_fixture(tmp_path, monkeypatch) -> list[Path]:
    """Two declared roots, four branches each, every round's only turn
    priced via an unrecognized model id -- branch_dollars sums to zero
    across the pool, so every dollar-based pooled share degenerates to "no
    priced branch spend" while the round-count-based shares stay numeric,
    since the rounds themselves still open.

    Root A splits its four branches between code-review and plan-review
    (two apiece); root B's four are all ready-for-review. Four branches
    per account, so the pool clears the four-branch bootstrap
    floor with room to spare. The round-count-keyed `skill_rounds:*` shares
    trip the dominance-precision floor (`_pooled_dominance_breach`), so every
    test reading this fixture stubs that floor. The dollar-keyed shares have
    zero denominators, which the floor skips.
    """
    roots = _two_declared_roots(tmp_path, monkeypatch)
    proj_a = roots[0] / "-home-user-repo-a"
    proj_a.mkdir(parents=True)
    for i, skill in enumerate(["code-review", "code-review", "plan-review", "plan-review"]):
        branch = f"feat-a{i}"
        _write_jsonl(proj_a / f"sess-a{i}.jsonl", [
            _priced(
                "some-unrecognized-model-id", input=100_000, branch=branch, ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block(f"sa{i}", skill)],
            ),
            _user_msg("thanks", branch=branch, ts="2026-08-01T10:01:00.000Z"),
        ])
    proj_b = roots[1] / "-home-user-repo-b"
    proj_b.mkdir(parents=True)
    for i in range(4):
        branch = f"feat-b{i}"
        _write_jsonl(proj_b / f"sess-b{i}.jsonl", [
            _priced(
                "some-unrecognized-model-id", input=100_000, branch=branch, ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block(f"sb{i}", "ready-for-review")],
            ),
            _user_msg("thanks", branch=branch, ts="2026-08-01T10:01:00.000Z"),
        ])
    return roots


_NUMERIC_SHARE_LINE_RE = re.compile(r"^    .{30}\d{1,3}\.\d% \(95% CI \d{1,3}\.\d-\d{1,3}\.\d%\)$", re.MULTILINE)


def _count_numeric_share_lines(pooled_block: str) -> int:
    """Number of figure lines in a printed pooled block that carry a
    numeric share and CI, as opposed to the withheld or zero-denominator
    wording."""
    return len(_NUMERIC_SHARE_LINE_RE.findall(pooled_block))


class TestSingleAccountWithinStatedPrecision:
    """Direct calls to _single_account_within_stated_precision -- no
    fixture, no corpus. TestCmdReviewRoundCostPooled below covers wiring
    this check into _pooled_dominance_breach and _render_pooled_block."""

    @pytest.mark.parametrize("w_max", [0.5, 0.99])
    def test_point_outside_ci_breaches_regardless_of_the_primary_formula(self, w_max):
        """p_estimate=100.0 sits strictly outside [85.0, 95.0], so the
        fail-closed backstop returns True before the exact-interval
        containment formula runs at all. Parametrized over a low and a high
        w_max to prove the backstop fires independently of w_max.
        """
        assert review_rounds._single_account_within_stated_precision(w_max, 100.0, 85.0, 95.0) is True

    @pytest.mark.parametrize("p_estimate,ci_lo,ci_hi", [(85.0, 85.0, 95.0), (95.0, 85.0, 95.0)])
    def test_point_exactly_on_ci_boundary_falls_through_to_the_primary_formula(
        self, p_estimate, ci_lo, ci_hi,
    ):
        """p_estimate exactly at ci_lo or ci_hi is non-strictly inside
        [ci_lo, ci_hi], so the backstop does not fire. w_max=0.99 puts the
        exact interval outside the CI on the side p_estimate sits at:

        - p_estimate=85.0: exact_lo=(85.0-1.0)/0.99=84.848..., which
          ci_lo=85.0 does not contain from below.
        - p_estimate=95.0: exact_hi=95.0/0.99=95.959..., which ci_hi=95.0
          does not contain from above.
        """
        assert review_rounds._single_account_within_stated_precision(0.99, p_estimate, ci_lo, ci_hi) is False

    def test_primary_formula_breaches_at_its_own_exact_containment_boundary(self):
        """p_estimate=50.0 sits strictly inside [37.5, 62.5], so the
        backstop doesn't fire. w_max=0.8 puts the exact consistent-value
        interval for the dominant account at
        [(50.0 - 20.0) / 0.8, 50.0 / 0.8] = [37.5, 62.5], identical to the CI
        itself. That is exact equality on both of the primary formula's own
        `<=` containment tests, which must still resolve to breach.
        """
        assert review_rounds._single_account_within_stated_precision(0.8, 50.0, 37.5, 62.5) is True

    def test_exact_interval_catches_a_non_boundary_breach(self):
        """w_max=0.9, p_estimate=50.0 against CI [44.0, 56.0]: the exact
        consistent-value interval for the dominant account is
        [(50.0 - 10.0) / 0.9, 50.0 / 0.9] = [44.44..., 55.55...], which sits
        entirely inside [44.0, 56.0] without touching either edge exactly --
        a real breach distinct from the exact-equality boundary case above.
        """
        assert review_rounds._single_account_within_stated_precision(0.9, 50.0, 44.0, 56.0) is True

    # The sweep is dense because the algebraically equivalent form `(p - (1 - w) * 100) / w` misses
    # 100.0 by one ulp at some weights (for example 0.68), which three friendly weights never hit.
    @pytest.mark.parametrize("w_max", [k / 1000 for k in range(1, 1001)])
    @pytest.mark.parametrize("exact_share", [0.0, 100.0])
    def test_exact_zero_or_hundred_percent_share_breaches_at_any_weight(self, w_max, exact_share):
        """An exact 0% or 100% share carries the degenerate CI [p, p], and
        the exact consistent-value interval collapses onto the same point,
        so containment holds however small the dominant account's weight.
        This is the intended floor behavior, not a false positive: such a
        share pins every account to that value.
        """
        assert review_rounds._single_account_within_stated_precision(
            w_max, exact_share, exact_share, exact_share,
        ) is True

    # Each of 0.1, 0.3, 1.1, 2.6 and 100 * 2 / 7 is a p where `100.0 - (100.0 - p) / 1.0` lands
    # one ulp below p; 12.5 and 50.0 are exactly representable and pass under either form.
    @pytest.mark.parametrize("interior_share", [0.1, 0.3, 1.1, 2.6, 100 * 2 / 7, 12.5, 50.0])
    def test_collapsed_interior_ci_breaches_at_full_weight(self, interior_share):
        """w_max=1.0 means one account is the whole denominator, so the exact
        consistent-value interval is the single point [p, p]. A CI collapsed
        onto p contains it exactly, and float rounding in the interval's
        lower edge must not hide that breach.
        """
        assert review_rounds._single_account_within_stated_precision(
            1.0, interior_share, interior_share, interior_share,
        ) is True

    @pytest.mark.parametrize("w_max", [0.0, -0.1])
    def test_nonpositive_w_max_is_fail_closed(self, w_max):
        """w_max <= 0 is structurally unreachable from _pooled_dominance_breach
        (it skips any share whose interval is None, which a zero denominator
        total forces, so w_max > 0), but this direct call pins the guard's own
        fail-closed return against a future caller that loses that guarantee.
        """
        assert review_rounds._single_account_within_stated_precision(w_max, 50.0, 44.0, 56.0) is True


class TestPooledDominanceBreachKeySet:
    """Direct calls to _pooled_dominance_breach with hand-built intervals:
    which shares the floor weighs, independent of any bootstrap."""

    @staticmethod
    def _balanced_account_totals() -> dict[int | None, dict[str, float]]:
        return {
            0: {"branch_dollars": 50.0, "round_dollars": 25.0, "round_count": 10},
            1: {"branch_dollars": 50.0, "round_dollars": 25.0, "round_count": 10},
        }

    @staticmethod
    def _interior_intervals() -> dict[str, tuple[float, float, float]]:
        """Every key at 50% with a CI (45-55) far narrower than a balanced
        pair's exact interval (0-100), so no share breaches on its own."""
        return dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (50.0, 45.0, 55.0))

    def test_all_interior_shares_do_not_breach(self):
        breach = review_rounds._pooled_dominance_breach(
            self._interior_intervals(), self._balanced_account_totals(),
        )
        assert breach is False

    @pytest.mark.parametrize("zero_share_key", review_rounds._POOLED_PUBLISHED_STAT_KEYS)
    def test_one_exact_zero_share_line_breaches_even_at_balanced_weights(self, zero_share_key):
        intervals = {**self._interior_intervals(), zero_share_key: (0.0, 0.0, 0.0)}
        breach = review_rounds._pooled_dominance_breach(intervals, self._balanced_account_totals())
        assert breach is True

    @pytest.mark.parametrize("gap_key", review_rounds._POOLED_GAP_STAT_KEYS)
    def test_exact_zero_gap_share_does_not_breach_because_it_is_never_printed_under_plain_pooled(self, gap_key):
        intervals = {**self._interior_intervals(), gap_key: (0.0, 0.0, 0.0)}
        breach = review_rounds._pooled_dominance_breach(intervals, self._balanced_account_totals())
        assert breach is False

    def test_gap_keys_are_exactly_the_stat_keys_absent_from_the_published_set(self):
        assert set(review_rounds._POOLED_GAP_STAT_KEYS) == {"gap_dangling", "gap_unpriced"}
        assert set(review_rounds._POOLED_PUBLISHED_STAT_KEYS) | set(review_rounds._POOLED_GAP_STAT_KEYS) == set(
            review_rounds._POOLED_STAT_KEYS
        )
        assert not set(review_rounds._POOLED_PUBLISHED_STAT_KEYS) & set(review_rounds._POOLED_GAP_STAT_KEYS)


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
        """Slices on `_POOLED_CAPTION` to exclude the caption's own
        compliant digit and the publication-pointer's illustrative `$/PR
        rate` prose from the grammar check.

        Stubs `_pooled_dominance_breach` to isolate this test from the
        dominance-precision floor. This fixture's every branch reports 100%
        of its own dollars as inside a round window, and 0% as
        reviewer-only. The floor's exact-interval formula correctly flags
        both of those degenerate shares as breaches, which is irrelevant to
        what this test checks.
        """
        monkeypatch.setattr(review_rounds, "_pooled_dominance_breach", lambda _intervals, _totals: False)
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
            "Share of branch spend", "Round-window spend by skill", "Rounds by skill",
        ]

        expected_labels = (
            "inside round windows", "outside every round window", "reviewer dispatches only",
            "code-review", "plan-review", "ready-for-review",
            "code-review", "plan-review", "ready-for-review",
        )
        found_labels = tuple(line[4:_POOLED_FIGURE_LABEL_FIELD_END].strip() for line in figure_lines)
        assert found_labels == expected_labels
        # Scoped to the figure-line labels, not the raw block: the block
        # also contains _POOLED_PUBLICATION_POINTER's own "Before citing…"
        # prose, an unrelated match this check must not trip on.
        assert not any(re.search(r"\b(before|after|pivot)\b", label) for label in found_labels)

        figure_re = re.compile(
            r"^(?:\d{1,3}\.\d% \(95% CI \d{1,3}\.\d-\d{1,3}\.\d%\)|\(95% CI not computed — [a-z ]+\))$"
        )
        for line in figure_lines:
            remainder = line[_POOLED_FIGURE_LABEL_FIELD_END:].strip()
            assert not any(c.isdigit() for c in remainder) or figure_re.match(remainder)

        # Second corpus, same test: an in-scope round with zero priced
        # dollars (an unrecognized model id) so branch_dollars sums to zero
        # across the pool, degenerating every dollar-based share to "no
        # priced branch spend" -- the figure_re alternative the first
        # corpus above never exercises, since every branch there is priced.
        _pooled_two_root_zero_priced_dollars_fixture(tmp_path / "zero-priced", monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        degenerate_block = capsys.readouterr().out
        _, degenerate_sep, degenerate_after_caption = degenerate_block.partition(review_rounds._POOLED_CAPTION)
        assert degenerate_sep, "_POOLED_CAPTION not found verbatim in the printed block"
        degenerate_figure_lines = [
            line for line in degenerate_after_caption.splitlines() if line.startswith("    ")
        ]
        degenerate_remainders = [
            line[_POOLED_FIGURE_LABEL_FIELD_END:].strip() for line in degenerate_figure_lines
        ]
        assert "(95% CI not computed — no priced branch spend)" in degenerate_remainders
        for remainder in degenerate_remainders:
            assert not any(c.isdigit() for c in remainder) or figure_re.match(remainder)

    def test_pooled_render_binds_each_figure_line_to_its_own_stat_key(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The "Round-window spend by skill" section (keyed by
        skill_spend:*) and the "Rounds by skill" section (keyed by
        skill_rounds:*) print the same three skill labels back to back. The
        data-quality-gap section's two labels likewise sit under one header
        shared by both. A bare `expected_line in out` assertion, as the
        figure-line tests elsewhere in this file use, cannot distinguish
        "right value, right section" from "right value, wrong section" once
        the label text repeats across sections.

        Runs with --show-withheld, the only mode that prints the
        data-quality-gap section. Stubs _bootstrap_share_intervals with a
        pairwise-distinct point per _POOLED_STAT_KEYS entry, then asserts
        the exact ordered (header, label, value) triples
        _render_pooled_block emits. A key-swap between
        skill_spend/skill_rounds, or between gap_dangling/gap_unpriced,
        changes which triple appears under which header even though every
        individual line stays well-formed.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        # feat-a-filler/feat-b-filler: zero branch_dollars each, needed only
        # to clear the four-branch bootstrap floor -- _bootstrap_share_intervals
        # is stubbed below, so their content doesn't otherwise matter.
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 0.20, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 0.60, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 0.40, (0, "feat-a-filler"): 0.0,
            (1, "feat-b"): 1.00, (1, "feat-b-filler"): 0.0,
        }
        args = _review_round_cost_args(pooled=True, show_withheld=True)

        intervals = {
            key: (10.0 + i, 10.0 + i - 0.5, 10.0 + i + 0.5)
            for i, key in enumerate(review_rounds._POOLED_STAT_KEYS)
        }
        assert len({point for point, _, _ in intervals.values()}) == len(intervals)
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        block = capsys.readouterr().out
        _, sep, after_caption = block.partition(review_rounds._POOLED_CAPTION)
        assert sep, "_POOLED_CAPTION not found verbatim in the printed block"

        expected_sequence = [
            ("Share of branch spend", "inside round windows", "spend_inside"),
            ("Share of branch spend", "outside every round window", "spend_outside"),
            ("Share of branch spend", "reviewer dispatches only", "spend_reviewer_only"),
            *(("Round-window spend by skill", skill, f"skill_spend:{skill}") for skill in review_rounds.REVIEW_SKILLS),
            *(("Rounds by skill", skill, f"skill_rounds:{skill}") for skill in review_rounds.REVIEW_SKILLS),
            ("Rounds affected by a data-quality gap", "dangling dispatch", "gap_dangling"),
            ("Rounds affected by a data-quality gap", "unpriced turn", "gap_unpriced"),
        ]
        expected_triples = [
            (header, label, review_rounds._fmt_share_with_ci(*intervals[key]))
            for header, label, key in expected_sequence
        ]

        current_header = None
        actual_triples = []
        for line in after_caption.splitlines():
            if not line.strip():
                continue
            if not line.startswith("    "):
                current_header = line.strip()
                continue
            label = line[4:_POOLED_FIGURE_LABEL_FIELD_END].strip()
            value = line[_POOLED_FIGURE_LABEL_FIELD_END:].strip()
            actual_triples.append((current_header, label, value))

        assert actual_triples == expected_triples

        # A key added to _POOLED_STAT_KEYS with no matching fmt() call would
        # compute a stat that's silently never printed -- checked here
        # against the actual rendered text, not parsed source, so a
        # behavior-preserving refactor of _render_pooled_block never forces
        # a rewrite of this test.
        for key in review_rounds._POOLED_STAT_KEYS:
            formatted_value = review_rounds._fmt_share_with_ci(*intervals[key])
            assert formatted_value in block, f"formatted value for {key!r} missing from the rendered block"

    def test_data_quality_gap_lines_print_only_under_show_withheld(self, tmp_path, monkeypatch, capsys):
        """The printed line set is fixed by the flag alone. Plain --pooled
        prints nine share lines and no data-quality-gap section, and
        --show-withheld adds the section's two lines, whatever the data.
        Stubs the bootstrap so both runs render numeric figures.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (root_idx, f"feat-{root_idx}-{i}"), "skill": "code-review",
             "main_dollars": 1.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0}
            for root_idx in (0, 1) for i in range(2)
        ]
        branch_totals = {round_["branch_key"]: 2.0 for round_ in rounds}
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (50.0, 45.0, 55.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(
            _review_round_cost_args(pooled=True), roots, "*", rounds, branch_totals, scan_gaps=Counter(),
        )
        # The publication pointer names the gap shares, so strip it before
        # asserting the gap section's own wording is absent.
        plain_out = capsys.readouterr().out.replace(review_rounds._POOLED_PUBLICATION_POINTER, "")
        review_rounds._render_pooled_block(
            _review_round_cost_args(pooled=True, show_withheld=True), roots, "*", rounds, branch_totals,
            scan_gaps=Counter(),
        )
        show_withheld_out = capsys.readouterr().out

        figure_line = "50.0% (95% CI 45.0-55.0%)"
        assert plain_out.count(figure_line) == 9
        assert "data-quality gap" not in plain_out
        assert "dangling dispatch" not in plain_out
        assert "unpriced turn" not in plain_out
        assert show_withheld_out.count(figure_line) == 11
        assert "Rounds affected by a data-quality gap" in show_withheld_out

    def test_real_floor_and_real_bootstrap_print_every_published_share_on_a_varied_pool(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Neither the floor nor the bootstrap is stubbed: 120 hand-built
        branches split evenly across both accounts, each with its own
        varying inside-round, reviewer-dispatch, and skill mix, print a
        numeric line for every published share under plain --pooled. This is
        the composed behavior every stubbed test in this class skips.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = []
        branch_totals = {}
        for i in range(120):
            branch_key = (i % 2, f"feat-{i}")
            main_dollars = 0.10 + 0.01 * (i % 7)
            agent_dollars = 0.02 * (i % 5)
            rounds.append({
                "branch_key": branch_key, "skill": review_rounds.REVIEW_SKILLS[i % 3],
                "main_dollars": main_dollars, "agent_dollars": agent_dollars,
                "unpriced_turns": 1 if i % 13 == 0 else 0, "dangling": 1 if i % 9 == 0 else 0,
            })
            branch_totals[branch_key] = main_dollars + agent_dollars + 0.05 + 0.03 * (i % 11)

        review_rounds._render_pooled_block(
            _review_round_cost_args(pooled=True), roots, "*", rounds, branch_totals, scan_gaps=Counter(),
        )
        out = capsys.readouterr().out

        _, sep, after_caption = out.partition(review_rounds._POOLED_CAPTION)
        assert sep
        numeric_figure_re = re.compile(r"\d{1,3}\.\d% \(95% CI \d{1,3}\.\d-\d{1,3}\.\d%\)$")
        figure_lines = [line for line in after_caption.splitlines() if line.startswith("    ")]
        assert len(figure_lines) == len(review_rounds._POOLED_PUBLISHED_STAT_KEYS)
        for line in figure_lines:
            assert numeric_figure_re.search(line), line
        assert "not computed" not in out

    def test_pool_with_an_exact_zero_percent_share_prints_all_nine_share_lines_withheld(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Neither the floor nor the bootstrap is stubbed. The two-root
        fixture has no reviewer dispatches, so "reviewer dispatches only" is
        an exact 0% share. Plain --pooled must then withhold all nine
        published lines, not just that one, with no numeric percent anywhere
        below the caption. The same corpus under --show-withheld prints
        numeric lines, so the withholding comes from the floor, not from too
        few branches.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        plain_out = capsys.readouterr().out
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        show_withheld_out = capsys.readouterr().out

        _, sep, after_caption = plain_out.partition(review_rounds._POOLED_CAPTION)
        assert sep, "_POOLED_CAPTION not found verbatim in the printed block"
        figure_lines = [line for line in after_caption.splitlines() if line.startswith("    ")]
        assert len(figure_lines) == len(review_rounds._POOLED_PUBLISHED_STAT_KEYS)
        for line in figure_lines:
            assert line[_POOLED_FIGURE_LABEL_FIELD_END:].strip() == "(95% CI not computed — too few branches in scope)"
        assert _count_numeric_share_lines(plain_out) == 0
        assert _count_numeric_share_lines(show_withheld_out) == len(review_rounds._POOLED_STAT_KEYS)

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

    def test_publication_pointer_present_and_show_withheld_banner_absent_without_the_flag(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Plain --pooled with no --show-withheld: the publication pointer
        prints exactly once, and the --show-withheld banner is absent from
        both streams. TestPooledShowWithheld pins the banner's presence
        when the flag is passed; this is that test's negative case.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out, err = capsys.readouterr()
        assert out.count(review_rounds._POOLED_PUBLICATION_POINTER) == 1
        assert review_rounds._POOLED_SHOW_WITHHELD_BANNER not in out
        assert review_rounds._POOLED_SHOW_WITHHELD_BANNER not in err

    def test_no_branch_name_leak(self, tmp_path, monkeypatch, capsys):
        """A distinctively-named fixture branch does not appear in pooled
        output. Asserts presence in the disclosed render and absence from
        the pooled render separately. Matches the existing disclosed/
        redacted pairing convention (see
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
        """Exact-string, not merely digit-free, since scope_label can't
        vary once --projects/--this-repo are refused; also asserts no
        `_root_count_desc`-shaped substring anywhere in the block, not just
        absence from the header line.
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
        citation-resolution tests check. This .py file is outside both
        that corpus and docs/*.md's parametrize list, so only this test
        catches a stale heading here.
        """
        doc_headings = heading_texts(
            (REPO_ROOT / "docs" / "private-project-redaction.md").read_text()
        )
        for pointer in (
            review_rounds._POOLED_PUBLICATION_POINTER,
            review_rounds._POOLED_REFUSAL_DOC_POINTER,
        ):
            cited = re.search(r'§\s+"([^"\n]+)"', pointer)
            assert cited, f"{pointer!r} does not cite a heading in the § \"...\" form"
            assert normalize_heading(cited.group(1)) in doc_headings, (
                f"{pointer!r} cites heading {cited.group(1)!r}, which does not "
                "exist in docs/private-project-redaction.md"
            )

    def test_pooled_publication_pointer_routes_show_withheld_relay_and_small_pool_statement(self):
        """Pins the sentences that route a proposer through --show-withheld
        to the two data-quality gap shares plain --pooled never prints, and
        that require the proposal to state, without digits, whether either
        gap share or its upper bound prints above zero and whether a
        skipped-account notice appeared, and that keep that statement out
        of the artifact and its citation. Also pins the separate sentence
        requiring the proposal to state, without digits, whether the pool
        is small, its pointer to the Small-pool residual, and its own
        keep-out-of-the-artifact clause.
        Plain --pooled does print the skipped-account notice on stderr, so
        the notice may appear only in the relay clause, never in the
        --show-withheld instruction. docs/transcript-analysis.md's sample
        output must carry the same sentences as the constant.

        Compares whitespace-normalized text so a line-wrap reflow does not
        fail the pin; the docs containment check stays exact.
        """
        pointer = review_rounds._POOLED_PUBLICATION_POINTER
        proposer_sentences = pointer[pointer.index("Before proposing"):]
        normalized = " ".join(proposer_sentences.split())
        run_instruction, relay_marker, relay_and_small_pool = normalized.partition("In the proposal")
        assert relay_marker, "the relay clause 'In the proposal' is missing"
        relay_clause, small_pool_marker, small_pool_clause = relay_and_small_pool.partition(
            "The proposal must also say"
        )
        assert small_pool_marker, "the small-pool sentence 'The proposal must also say' is missing"
        assert "--show-withheld" in run_instruction
        assert "data-quality gap shares" in run_instruction
        assert "plain --pooled never prints" in run_instruction
        assert "skipped" not in run_instruction
        assert "without digits" in relay_clause
        assert "whether either gap share or its upper bound prints above zero" in relay_clause
        assert "skipped-account notice" in relay_clause
        assert "stderr" in relay_clause
        assert "out of the artifact and its citation" in relay_clause
        assert "without digits" in small_pool_clause
        assert "pool is small" in small_pool_clause
        assert "Small-pool residual" in small_pool_clause
        assert "out of the artifact and its citation" in small_pool_clause
        docs_text = (REPO_ROOT / "docs" / "transcript-analysis.md").read_text()
        assert proposer_sentences in docs_text
        review_round_cost_section = docs_text.split("\n## review-round-cost\n", 1)[1].split("\n## ", 1)[0]
        assert "**Small-pool residual" in review_round_cost_section

    def test_pooled_docs_sample_output_carries_the_publication_pointer_and_caption(self):
        """docs/transcript-analysis.md's sample --pooled output must contain
        both constants verbatim, so a reworded constant cannot drift from
        the documented output."""
        docs_text = (REPO_ROOT / "docs" / "transcript-analysis.md").read_text()
        assert review_rounds._POOLED_PUBLICATION_POINTER in docs_text
        assert review_rounds._POOLED_CAPTION in docs_text

    def test_pooled_caption_names_both_small_pool_residuals(self):
        """The caption states that a small pool can reveal the round count
        through the Rounds by skill shares and that the interval endpoints
        can approximate the per-branch spread, without pointing at any
        other printed element, since --show-withheld prints no pointer."""
        caption = " ".join(review_rounds._POOLED_CAPTION.split())
        assert "the Rounds by skill shares can still reveal the round count" in caption
        assert "interval endpoints can approximate the per-branch spread" in caption
        assert "pointer" not in caption

    def test_pooled_printed_ci_parameters_track_their_constants(self):
        """The caption's resample count and every figure's CI level are
        literals in printed text, so each must equal the constant the
        bootstrap actually runs with."""
        resample_phrase = f"{review_rounds._BOOTSTRAP_RESAMPLES:,}-resample"
        ci_level_label = f"{round(review_rounds._CI_LEVEL * 100)}% CI"
        assert resample_phrase in review_rounds._POOLED_CAPTION
        assert f"{ci_level_label[:-len(' CI')]} level" in review_rounds._POOLED_CAPTION
        assert review_rounds._fmt_share_with_ci(40.0, 35.0, 45.0) == f"40.0% ({ci_level_label} 35.0-45.0%)"
        assert review_rounds._fmt_share_with_ci(None, None, None).startswith(f"({ci_level_label} not computed")
        assert review_rounds._fmt_share_with_ci(0.0, None, None).startswith(f"({ci_level_label} not computed")

    def test_cross_root_pooling_has_no_account_label_and_reflects_both_roots(
        self, tmp_path, monkeypatch, capsys,
    ):
        """rounds under two roots produce one block with no account- label,
        and a code-review rounds-by-skill share reflecting the pool
        (50.0%) rather than either root's own share alone (75.0% for the
        code-review-majority root, 25.0% for the plan-review-majority
        root).

        Stubs `_pooled_dominance_breach` to isolate this test from the
        dominance-precision floor. This fixture's every branch reports 100%
        of its own dollars as inside a round window. The floor's
        exact-interval formula correctly flags that degenerate share as a
        breach, which is irrelevant to what this test checks.
        """
        monkeypatch.setattr(review_rounds, "_pooled_dominance_breach", lambda _intervals, _totals: False)
        _pooled_two_root_discriminating_skill_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        assert "account-" not in out

        # Eight branches, one round each, matching the fixture's real
        # per-branch totals in the same sorted branch order
        # _render_pooled_block itself iterates (root 0's four branches,
        # then root 1's). skill_rounds:* depends only on round_count and
        # skill_round_counts, not on dollar amounts, so this reproduces the
        # fixture's real skill_rounds:* CI exactly.
        branch_dollars_for_skill = {"code-review": 1.0, "plan-review": 2.0}

        def single_round_branch(skill: str) -> review_rounds._PooledBranchTotals:
            dollars = branch_dollars_for_skill[skill]
            skill_counts = {"code-review": 0, "plan-review": 0, "ready-for-review": 0}
            skill_counts[skill] = 1
            skill_dollars = {"code-review": 0.0, "plan-review": 0.0, "ready-for-review": 0.0}
            skill_dollars[skill] = dollars
            return review_rounds._PooledBranchTotals(
                round_dollars=dollars, agent_dollars=0.0, branch_dollars=dollars,
                skill_round_counts=skill_counts, skill_round_dollars=skill_dollars,
                rounds_with_dangling=0, rounds_with_unpriced=0, round_count=1,
            )

        root_a_skills = ["code-review", "code-review", "code-review", "plan-review"]
        root_b_skills = ["plan-review", "plan-review", "plan-review", "code-review"]
        per_branch = [single_round_branch(s) for s in root_a_skills + root_b_skills]
        intervals = review_rounds._bootstrap_share_intervals(per_branch)
        for skill, key in (
            ("code-review", "skill_rounds:code-review"), ("plan-review", "skill_rounds:plan-review"),
        ):
            point, lo, hi = intervals[key]
            assert point == 50.0
            expected_line = f"    {skill:<30}{review_rounds._fmt_share_with_ci(point, lo, hi)}"
            assert expected_line in out

    def test_refuses_branches_flag(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, branches="feat"))
        assert exc.value.code == 2
        assert "--branches" in capsys.readouterr().err

    @pytest.mark.parametrize("projects", ["feat-*", ""], ids=["named-glob", "empty-string"])
    def test_refuses_non_default_projects_glob(self, projects, fake_projects, capsys):
        """The empty string is included because _single_level_projects_glob
        admits it, so this check, not argparse, refuses it under --pooled.
        On this single-root fixture the root-count refusal also exits 2, so
        the message assertion, not the exit code, pins this check."""
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, projects=projects))
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

    def test_refuses_top_level_config_dir_through_the_real_parser(self, tmp_path, capsys):
        """CLI-level counterpart to test_refuses_top_level_config_dir above:
        that test builds a hand-rolled Namespace directly, bypassing
        build_parser() entirely, so a regression in how --config-dir is
        wired through argparse (its top-level placement, its dest name)
        would go uncaught there. In-process via build_parser() + a direct
        cmd_review_round_cost(args) call, matching test_transcript_cost.py's
        own build_parser().parse_args(...) convention -- no subprocess
        needed since this claim is about argparse wiring, not hash-seed
        determinism."""
        args = _mod.build_parser().parse_args(
            ["--config-dir", str(tmp_path), "review-round-cost", "--pooled"]
        )
        assert args.func == _mod.cmd_review_round_cost
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(args)
        assert exc.value.code == 2
        assert "--config-dir" in capsys.readouterr().err

    def test_pooled_and_show_withheld_parse_together_through_the_real_parser(self):
        """--pooled and --show-withheld are independent store_true flags on
        the same subparser: confirms both attributes come back True from one
        parse, with no mutually-exclusive-group wiring blocking the pair."""
        args = _mod.build_parser().parse_args(
            ["review-round-cost", "--pooled", "--show-withheld"]
        )
        assert args.pooled is True
        assert args.show_withheld is True

    def test_show_withheld_without_pooled_refuses_through_the_real_parser(self, capsys):
        """Real-parser counterpart to
        test_show_withheld_without_pooled_refuses_before_any_scan: proves the
        requires-pooled refusal fires when --show-withheld reaches
        cmd_review_round_cost via build_parser() + parse_args, not only
        through the hand-rolled Namespace stand-in."""
        args = _mod.build_parser().parse_args(["review-round-cost", "--show-withheld"])
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(args)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "--show-withheld" in err
        assert "--pooled" in err

    def test_refuses_branches_flag_through_the_real_parser(self, fake_projects, capsys):
        """Real-parser counterpart to test_refuses_branches_flag: proves the
        --branches refusal fires when the flag reaches cmd_review_round_cost
        via build_parser() + parse_args, not only through the hand-rolled
        Namespace stand-in every other refusal test in this class uses."""
        args = _mod.build_parser().parse_args(
            ["review-round-cost", "--pooled", "--branches", "feat"]
        )
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(args)
        assert exc.value.code == 2
        assert "--branches" in capsys.readouterr().err

    def test_every_review_round_cost_option_is_either_refused_under_pooled_or_scope_neutral(self):
        """Walks every option build_parser() registers for review-round-cost
        (its own and the top-level parser's), so a new option cannot ship
        without a decision on whether --pooled must refuse it. A refused
        option must also trip _pooled_scope_refusal when set, so the
        classification cannot drift from the refusal chain."""
        refused = {"branches", "projects", "skill", "since", "until", "config_dir", "this_repo"}
        scope_neutral = {
            "pooled",  # selects the mode itself
            "show_withheld",  # widens what the pooled block prints, never what is scanned
            "help",  # exits before any scan
            "subcommand",  # the top-level parser's own subparser selector
        }
        parser = _mod.build_parser()
        subparsers_action = next(
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        )
        registered_dests = {
            action.dest
            for registering_parser in (parser, subparsers_action.choices["review-round-cost"])
            for action in registering_parser._actions
        }
        assert registered_dests == refused | scope_neutral

        assert review_rounds._pooled_scope_refusal(_review_round_cost_args(pooled=True)) is None
        for dest in sorted(refused):
            truthy_value = True if dest == "this_repo" else "named-value"
            refusal = review_rounds._pooled_scope_refusal(
                _review_round_cost_args(pooled=True, **{dest: truthy_value})
            )
            assert refusal is not None, dest

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
        """Unlike the other refusal tests, this row has no flag to name;
        asserts against scope.TRANSCRIPT_CONFIG_DIRS_LABEL's real value, and
        only the plain single-root path (not --this-repo-on-single-root)
        ever reaches this check.
        """
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert scope.TRANSCRIPT_CONFIG_DIRS_LABEL in err

    def test_render_pooled_block_called_directly_still_refuses(self, tmp_path, monkeypatch):
        """Defense-in-depth: bypasses cmd_review_round_cost's own
        CLI-boundary refusal entirely by calling _render_pooled_block
        directly."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        args = _review_round_cost_args(pooled=True, branches="feat")
        with pytest.raises(SystemExit) as exc:
            review_rounds._render_pooled_block(args, roots, "*", [], {}, scan_gaps=Counter())
        assert exc.value.code == 2

    def test_render_pooled_block_called_directly_with_none_roots_still_refuses(self):
        """The roots=None sentinel means "defer" only at
        cmd_review_round_cost's own pre-scan refusal call. A direct caller
        reaching _render_pooled_block's own defense-in-depth refusal call
        with roots=None must still fail the root-count floor, not silently
        skip it the way the pre-scan sentinel does."""
        args = _review_round_cost_args(pooled=True)
        with pytest.raises(SystemExit) as exc:
            review_rounds._render_pooled_block(args, None, "*", [], {}, scan_gaps=Counter())
        assert exc.value.code == 2

    def test_render_pooled_block_called_directly_with_empty_roots_still_refuses(self):
        """Same floor as the None case above, reached instead with an
        empty (not None) roots list."""
        args = _review_round_cost_args(pooled=True)
        with pytest.raises(SystemExit) as exc:
            review_rounds._render_pooled_block(args, [], "*", [], {}, scan_gaps=Counter())
        assert exc.value.code == 2

    def test_render_pooled_block_called_directly_with_single_root_still_refuses(self):
        """Same floor with a genuinely single-element roots list -- the
        realistic shape the root-count clause is meant to catch,
        pinned here at the direct-call layer specifically. The clause only
        inspects len(roots) and returns before any filesystem access, so a
        fabricated path stands in for a real declared root."""
        args = _review_round_cost_args(pooled=True)
        with pytest.raises(SystemExit) as exc:
            review_rounds._render_pooled_block(args, [Path("/fake/root")], "*", [], {}, scan_gaps=Counter())
        assert exc.value.code == 2

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_refuses_unreadable_scan_root_via_cmd_review_round_cost(self, tmp_path, monkeypatch, capsys):
        """A resolved root whose projects/ directory exists but can't be
        listed (locked permissions) must refuse, distinct from the
        fail-open declared-root-file-entry case covered by the
        stderr-diagnostic tests below: here the account's data provably
        exists and would silently drop out of the pool. The traversal's
        own root-level gap record triggers the refusal after the full
        scan. No session content is written, so the readable roots' own
        scan is empty and contributes no digit either."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        os.chmod(roots[1], 0o000)
        try:
            with pytest.raises(SystemExit) as exc:
                _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        finally:
            os.chmod(roots[1], 0o755)
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert not any(c.isdigit() for c in err)
        assert scope.TRANSCRIPT_CONFIG_DIRS_LABEL in err
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL in err
        assert str(roots[1]) not in err

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_refuses_unreadable_active_profile_scan_root_via_cmd_review_round_cost(self, tmp_path, monkeypatch, capsys):
        """Same refusal as above, but for the active profile's own
        PROJECTS_DIR (roots[0]) going unreadable, not a declared secondary
        root (roots[1]) -- the traversal's root-level listing runs
        uniformly over every resolved root, so the active profile's own
        root must record a gap too."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        os.chmod(roots[0], 0o000)
        try:
            with pytest.raises(SystemExit) as exc:
                _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        finally:
            os.chmod(roots[0], 0o755)
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert not any(c.isdigit() for c in err)
        assert scope.TRANSCRIPT_CONFIG_DIRS_LABEL in err
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL in err
        assert str(roots[0]) not in err

    def test_render_pooled_block_called_directly_refuses_on_a_nonempty_scan_gaps_counter(self, tmp_path, monkeypatch):
        """Defense-in-depth for the scan-gap clause. A direct
        caller that already holds a non-empty scan_gaps counter (e.g. one
        it built itself, or reused from a prior scan) must still refuse.
        No chmod needed, since the clause reads only the counter."""
        roots = [tmp_path / "acct-a" / "projects", tmp_path / "acct-b" / "projects"]
        args = _review_round_cost_args(pooled=True)
        with pytest.raises(SystemExit) as exc:
            review_rounds._render_pooled_block(
                args, roots, "*", [], {}, scan_gaps=Counter({scope._SCAN_GAP_PROJECT_DIR: 1}),
            )
        assert exc.value.code == 2

    def test_missing_active_profile_projects_dir_is_not_refused_as_a_scan_gap(self, tmp_path, monkeypatch, capsys):
        """An active profile whose projects/ directory doesn't exist yet
        (never populated) is the empty-scope case, not a scan gap -- the
        traversal's own missing-path handling must not record it. Two
        other valid, readable declared roots keep the run poolable, so the
        end-to-end run must render without raising."""
        acct_a = tmp_path / "acct-a"  # never created: PROJECTS_DIR doesn't exist
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        acct_c = tmp_path / "acct-c"
        (acct_c / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n{acct_c}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))  # raises SystemExit on failure
        out = capsys.readouterr().out
        assert out.splitlines()[0] == "REVIEW ROUND COST SOURCES (*; pooled)"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_refuses_on_unreadable_project_dir_via_cmd_review_round_cost(self, tmp_path, monkeypatch, capsys):
        """roots[1] keeps its existing readable project dir (still
        contributing a branch), plus a second, unreadable one -- the
        refusal can only come from the scan-gap clause, not the
        root-count clause, since both roots resolve and one of them
        still contributes."""
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        sealed_proj = roots[1] / "-home-user-repo-b-sealed"
        sealed_proj.mkdir(parents=True)
        _write_jsonl(sealed_proj / "sess-sealed.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-sealed", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s5", "code-review")],
            ),
            _user_msg("thanks", branch="feat-sealed", ts="2026-08-01T10:01:00.000Z"),
        ])
        os.chmod(sealed_proj, 0o000)
        try:
            with pytest.raises(SystemExit) as exc:
                _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        finally:
            os.chmod(sealed_proj, 0o755)
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL in err
        assert not any(c.isdigit() for c in err)
        assert str(sealed_proj) not in err

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_refuses_on_unreadable_transcript_via_cmd_review_round_cost(self, tmp_path, monkeypatch, capsys):
        """Same shape as the project-dir case above, one level down: a
        transcript, not a project directory, goes unreadable inside
        roots[1]'s existing readable project."""
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        sealed_jsonl = roots[1] / "-home-user-repo-b" / "sess-b1.jsonl"
        os.chmod(sealed_jsonl, 0o000)
        try:
            with pytest.raises(SystemExit) as exc:
                _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        finally:
            os.chmod(sealed_jsonl, 0o644)
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL in err
        assert not any(c.isdigit() for c in err)
        assert str(sealed_jsonl) not in err

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_refuses_on_symlinked_project_dir_with_unreadable_target_via_cmd_review_round_cost(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A project-dir entry that is a symlink resolving through a sealed
        ancestor directory makes the candidate's stat raise PermissionError (an
        OSError), not return False -- _dedup_new_project_dirs must catch it
        as a scan gap instead of letting it propagate uncaught."""
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        sealed_target_parent = tmp_path / "sealed-target-parent"
        (sealed_target_parent / "child").mkdir(parents=True)
        (roots[1] / "-home-user-repo-b-linked").symlink_to(sealed_target_parent / "child")
        os.chmod(sealed_target_parent, 0o000)
        try:
            with pytest.raises(SystemExit) as exc:
                _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        finally:
            os.chmod(sealed_target_parent, 0o755)
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert "Traceback" not in err
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL in err
        assert not any(c.isdigit() for c in err)
        assert str(sealed_target_parent) not in err

    @pytest.mark.parametrize("linked_entry", ["project-dir", "transcript"])
    def test_entry_symlinked_outside_every_declared_root_aborts_with_the_generic_message(
        self, tmp_path, monkeypatch, capsys, linked_entry,
    ):
        """Single-root scans keep a project directory or transcript symlinked
        outside the scan root in scope. Under --pooled (always multi-root)
        the session's real path matches no declared root, so root attribution
        raises. The abort backstop turns that into the generic refusal: exit
        2, no figure, no traceback, and neither the link name nor the target
        path in the output.
        """
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        outside_proj = tmp_path / "outside-every-root" / "-home-user-repo-elsewhere"
        outside_proj.mkdir(parents=True)
        outside_transcript = outside_proj / "sess-outside.jsonl"
        _write_jsonl(outside_transcript, [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat-outside", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s5", "code-review")],
            ),
            _user_msg("thanks", branch="feat-outside", ts="2026-08-01T10:01:00.000Z"),
        ])
        link_name = "-home-user-repo-linked-outside"
        if linked_entry == "project-dir":
            (roots[1] / link_name).symlink_to(outside_proj, target_is_directory=True)
        else:
            (roots[1] / "-home-user-repo-b" / f"{link_name}.jsonl").symlink_to(outside_transcript)

        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))

        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert review_rounds._POOLED_SCAN_ABORTED_MESSAGE in err
        assert "Traceback" not in err
        assert link_name not in err
        assert str(outside_proj) not in err

    def test_project_dir_symlinked_to_another_directory_inside_a_declared_root_still_completes(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Paired with the outside-every-root abort above: an alias whose
        real path stays under a declared root dedups against its target, so
        the run completes with the same figures as without the alias. Runs
        with --show-withheld so the compared output carries numeric figures.
        """
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        without_alias_out, _ = capsys.readouterr()
        assert _count_numeric_share_lines(without_alias_out) == len(review_rounds._POOLED_STAT_KEYS)

        (roots[1] / "-home-user-repo-b-alias").symlink_to(roots[1] / "-home-user-repo-b", target_is_directory=True)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        with_alias_out, with_alias_err = capsys.readouterr()

        assert with_alias_out == without_alias_out
        assert review_rounds._POOLED_SCAN_ABORTED_MESSAGE not in with_alias_err

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_session_dir_is_not_a_scan_gap_via_cmd_review_round_cost(self, tmp_path, monkeypatch, capsys):
        """A session directory holding subagents/ that cannot be searched
        reads as a session with no dispatches, so it does not refuse: the
        scan-gap refusal covers scan roots, project directories, and
        main-thread transcripts only. docs/transcript-analysis.md's Pooled
        mode section documents the resulting dangling-dispatch residual.
        """
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        proj_b = roots[1] / "-home-user-repo-b"
        session_dir = proj_b / "sess-b1"  # paired with the existing sess-b1.jsonl
        session_dir.mkdir()
        (session_dir / "subagents").mkdir()
        os.chmod(session_dir, 0o000)
        try:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        finally:
            os.chmod(session_dir, 0o755)
        out, err = capsys.readouterr()
        assert out.splitlines()[0] == "REVIEW ROUND COST SOURCES (*; pooled)"
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL not in err
        assert "Traceback" not in err
        assert str(session_dir) not in err

    def test_non_utf8_line_in_a_transcript_is_skipped_and_does_not_refuse_or_change_the_figures(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A non-UTF-8 line is skipped on its own like any malformed line,
        so it neither refuses the pooled run (the file is readable, and the
        refusal's `find ! -readable` remediation would not find it) nor
        discards the file's valid records. Runs with --show-withheld so the
        compared output carries numeric figures.
        """
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        clean_out, _clean_err = capsys.readouterr()
        assert _count_numeric_share_lines(clean_out) == len(review_rounds._POOLED_STAT_KEYS)

        with open(roots[1] / "-home-user-repo-b" / "sess-b1.jsonl", "ab") as fh:
            fh.write(b"\xff\xfe not valid utf-8\n")

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        out, err = capsys.readouterr()
        assert out == clean_out
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL not in err
        assert "Traceback" not in err

    def test_pooled_clean_scan_with_harmless_entries_does_not_refuse(self, tmp_path, monkeypatch, capsys):
        """A stray non-project file, a readable-empty transcript, and a
        directory named *.jsonl are all an empty scope, not a gap, so a
        clean scan must not trip the scan-gap clause. Adding them must not
        change the rendered output. Runs with --show-withheld so the
        compared output carries numeric figures, not the withheld wording
        this fixture's degenerate shares would otherwise produce.
        """
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        first_out, first_err = capsys.readouterr()
        assert _count_numeric_share_lines(first_out) == len(review_rounds._POOLED_STAT_KEYS)
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL not in first_err
        assert review_rounds._POOLED_STDERR_WITHHELD_NOTICE not in first_err

        (roots[1] / ".DS_Store").write_text("")
        proj_b = roots[1] / "-home-user-repo-b"
        (proj_b / "empty.jsonl").write_text("")
        (proj_b / "stray.jsonl").mkdir()

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        second_out, second_err = capsys.readouterr()
        assert second_out == first_out
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL not in second_err
        assert review_rounds._POOLED_STDERR_WITHHELD_NOTICE not in second_err

    def test_pooled_backstop_catches_an_unanticipated_exception_without_leaking_its_message(
        self, tmp_path, monkeypatch, capsys,
    ):
        """An exception the scan-gap accounting doesn't anticipate (not an
        OSError/RuntimeError from a read failure) must still be caught by
        cmd_review_round_cost's own try/except backstop and rendered as the
        generic _POOLED_SCAN_ABORTED_MESSAGE -- never as a raw traceback, and
        never with the caught exception's own str() (which could carry a
        filesystem path)."""
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        marker = "synthetic-marker-3f9a2b"

        def _raise(*_args, **_kwargs):
            raise RuntimeError(marker)

        monkeypatch.setattr(review_rounds, "compute_review_round_costs", _raise)
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert review_rounds._POOLED_SCAN_ABORTED_MESSAGE in err
        assert "Traceback" not in out
        assert "Traceback" not in err
        assert marker not in out
        assert marker not in err

    def test_pooled_backstop_covers_resolve_scan_roots_failure(
        self, tmp_path, monkeypatch, capsys,
    ):
        """An unanticipated exception raised by scope.resolve_scan_roots(...),
        outside compute_review_round_costs, must still be caught by the
        backstop and rendered as _POOLED_SCAN_ABORTED_MESSAGE."""
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        marker = "synthetic-marker-7c1d4e"

        def _raise(*_args, **_kwargs):
            raise RuntimeError(marker)

        monkeypatch.setattr(scope, "resolve_scan_roots", _raise)
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert review_rounds._POOLED_SCAN_ABORTED_MESSAGE in err
        assert "Traceback" not in out
        assert "Traceback" not in err
        assert marker not in out
        assert marker not in err

    def test_pooled_render_emits_nothing_when_interval_computation_fails(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Pins _render_pooled_block's ordering: every print (header,
        publication pointer, caption, figure lines) happens only after
        by_branch, per_branch, and the bootstrap intervals are fully
        computed. An exception raised during that computation -- here via
        _bootstrap_share_intervals, the last step before the first print --
        must reach the pooled backstop with zero stdout output, not a
        truncated pooled block."""
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        marker = "synthetic-marker-9b3e1a"

        def _raise(*_args, **_kwargs):
            raise RuntimeError(marker)

        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", _raise)
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert review_rounds._POOLED_SCAN_ABORTED_MESSAGE in err
        assert marker not in err

    def test_pooled_backstop_does_not_engage_on_the_non_pooled_path(self, tmp_path, monkeypatch):
        """Paired with the pooled case above: the same unanticipated
        exception under pooled=False must propagate unmodified, confirming
        the try/except backstop only swallows it when pooled is
        True."""
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        marker = "synthetic-marker-3f9a2b"

        def _raise(*_args, **_kwargs):
            raise RuntimeError(marker)

        monkeypatch.setattr(review_rounds, "compute_review_round_costs", _raise)
        with pytest.raises(RuntimeError, match=marker):
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=False))

    def test_pooled_output_is_byte_identical_across_two_separate_subprocesses(self, tmp_path):
        """Two real `python3` subprocesses, not two in-process calls, since
        PYTHONHASHSEED is fixed per process. Each gets its own explicit
        seed (1 and 2 iterate the fixture's branch names in different set
        order), so an ambient exported seed cannot make them match. Six
        branches with distinct dollar amounts and non-round activity widen
        the ordering space so a coincidental pass can't mask a
        `set`-ordering bug.

        Runs with --show-withheld and asserts numeric figure lines with a
        non-degenerate CI, since a fully withheld block is identical
        whatever the ordering.
        """
        acct_a = tmp_path / "acct-a"
        proj_a = acct_a / "projects" / "-home-user-repo-a"
        proj_a.mkdir(parents=True)
        acct_b = tmp_path / "acct-b"
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        skills = ["code-review", "plan-review", "ready-for-review"]
        for i in range(6):
            proj = proj_a if i % 2 == 0 else proj_b
            branch = f"feat-{i}"
            _write_jsonl(proj / f"sess-{i}.jsonl", [
                _priced(  # non-round activity, distinct per branch
                    "claude-sonnet-5", input=50_000 * (i + 1), branch=branch, ts="2026-08-01T09:00:00.000Z",
                ),
                _priced(
                    "claude-sonnet-5", input=100_000 + 30_000 * i, branch=branch, ts="2026-08-01T10:00:00.000Z",
                    content=[_skill_block(f"s{i}", skills[i % 3])],
                ),
                _user_msg("thanks", branch=branch, ts="2026-08-01T10:01:00.000Z"),
            ])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(acct_a),
            "TRANSCRIPT_CONFIG_DIRS_FILE": str(roots_file),
        }

        def _run(hash_seed: int) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(_SCRIPT), "review-round-cost", "--pooled", "--show-withheld"],
                capture_output=True, text=True, timeout=60, env={**env, "PYTHONHASHSEED": str(hash_seed)},
            )

        first = _run(1)
        second = _run(2)
        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        assert first.stdout == second.stdout
        assert _count_numeric_share_lines(first.stdout) == len(review_rounds._POOLED_STAT_KEYS)
        ci_bounds = re.findall(r"\(95% CI (\d+\.\d)-(\d+\.\d)%\)", first.stdout)
        assert any(lo != hi for lo, hi in ci_bounds), "every CI is degenerate, so ordering could not change it"

    def test_bootstrap_ci_bounds_are_invariant_to_branch_insertion_order(
        self, tmp_path, monkeypatch, capsys,
    ):
        """_bootstrap_share_intervals resamples per_branch positionally, so
        an unsorted by_branch construction would let two runs over the
        same branch content in different insertion orders print different
        CI bounds. _render_pooled_block sorts by_branch into per_branch
        before bootstrapping specifically to rule that out. Four branches
        (not two) widen the resample-index space enough that a coincidental
        pass can't mask a reordering regression here, the way the
        cross-process subprocess test above does for hash-seed variance.

        Runs with --show-withheld so the block prints numeric CI lines
        instead of the withheld wording, which would be identical whatever
        the branch order.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 0.20, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 0.60, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-c"), "skill": "ready-for-review",
             "main_dollars": 0.35, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-d"), "skill": "code-review",
             "main_dollars": 0.15, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 0.40, (1, "feat-b"): 1.00, (0, "feat-c"): 0.55, (1, "feat-d"): 0.30,
        }
        args = _review_round_cost_args(pooled=True, show_withheld=True)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        forward_out = capsys.readouterr().out

        review_rounds._render_pooled_block(
            args, roots, "*", list(reversed(rounds)), branch_totals, scan_gaps=Counter(),
        )
        reversed_out = capsys.readouterr().out

        assert forward_out == reversed_out
        assert _count_numeric_share_lines(forward_out) == len(review_rounds._POOLED_STAT_KEYS)

    def test_bootstrap_ci_bounds_are_invariant_to_root_scan_order(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Scan order puts the active profile's root first, so the same two
        physical roots carry swapped root indexes under a different active
        profile. The bootstrap's branch order must follow the resolved-path
        root ordinal, not that index, or the printed CI bounds would change
        with which profile ran the report.

        Runs with --show-withheld so the block prints numeric CI lines
        instead of the withheld wording, which would be identical whatever
        the branch order.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        skills = ["code-review", "plan-review", "ready-for-review"]
        branch_count = 12
        rounds = []
        branch_totals = {}
        for i in range(branch_count):
            branch_key = (i % 2, f"feat-{i:02d}")
            round_dollars = 0.10 + 0.07 * i
            rounds.append({
                "branch_key": branch_key, "skill": skills[i % 3],
                "main_dollars": round_dollars, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0,
            })
            branch_totals[branch_key] = round_dollars + 0.05 * (i + 1)

        def _swap_root_idx(branch_key: tuple[int, str]) -> tuple[int, str]:
            root_idx, branch = branch_key
            return (1 - root_idx, branch)

        swapped_rounds = [{**entry, "branch_key": _swap_root_idx(entry["branch_key"])} for entry in rounds]
        swapped_branch_totals = {_swap_root_idx(key): dollars for key, dollars in branch_totals.items()}
        args = _review_round_cost_args(pooled=True, show_withheld=True)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        forward_out = capsys.readouterr().out

        review_rounds._render_pooled_block(
            args, list(reversed(roots)), "*", swapped_rounds, swapped_branch_totals, scan_gaps=Counter(),
        )
        swapped_out = capsys.readouterr().out

        assert forward_out == swapped_out
        assert _count_numeric_share_lines(forward_out) == len(review_rounds._POOLED_STAT_KEYS)

    def test_point_estimate_is_share_of_sums_not_mean_of_per_branch_shares(
        self, tmp_path, monkeypatch, capsys,
    ):
        """CLI-layer counterpart of _asymmetric_two_branch_pooled_totals's
        own share-of-sums-vs-mean-of-shares check, via an equivalent
        priced-round JSONL fixture instead of a hand-built
        _PooledBranchTotals list.

        Stubs `_pooled_dominance_breach` to isolate this test from the
        dominance-precision floor. No branch here dispatches a subagent, so
        spend_reviewer_only is 0% with zero variance across every branch.
        The floor's exact-interval formula correctly flags that degenerate
        share as a breach, which is irrelevant to what this test checks.
        """
        monkeypatch.setattr(review_rounds, "_pooled_dominance_breach", lambda _intervals, _totals: False)
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
            # feat-a-filler: a zero-dollar round, needed only to clear the
            # four-branch bootstrap floor without moving feat-a's own totals.
            _priced(
                "claude-sonnet-5", branch="feat-a-filler", ts="2026-08-01T11:00:00.000Z",
                content=[_skill_block("f1", "code-review")],
            ),  # round: $0.00
            _user_msg("thanks", branch="feat-a-filler", ts="2026-08-01T11:01:00.000Z"),
        ])
        proj_b = roots[1] / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=200_000, branch="feat-b", ts="2026-08-01T09:00:00.000Z"),  # non-round: $0.40
            _priced(
                "claude-sonnet-5", input=300_000, branch="feat-b", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s2", "code-review")],
            ),  # round: $0.60
            _user_msg("thanks", branch="feat-b", ts="2026-08-01T10:01:00.000Z"),
            # feat-b-filler: a zero-dollar round, needed only to clear the
            # four-branch bootstrap floor without moving feat-b's own totals.
            _priced(
                "claude-sonnet-5", branch="feat-b-filler", ts="2026-08-01T11:00:00.000Z",
                content=[_skill_block("f2", "code-review")],
            ),  # round: $0.00
            _user_msg("thanks", branch="feat-b-filler", ts="2026-08-01T11:01:00.000Z"),
        ])

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out

        share_of_sums = round(100 * (0.20 + 0.60) / (0.40 + 1.00), 1)  # 57.1 -- correct
        mean_of_shares = round((50.0 + 60.0) / 2, 1)  # 55.0 -- the regression this fixture rules out
        assert share_of_sums != mean_of_shares

        # Both branches share the same code-review skill, matching
        # _asymmetric_two_branch_pooled_totals above.
        #
        # This JSONL fixture's dollar amounts are numerically identical to
        # _asymmetric_two_branch_pooled_totals's, plus the two zero-dollar
        # filler rounds above. The real production bootstrap over that
        # equivalent fixture therefore reproduces exactly what the CLI run
        # above computed.
        # Branch order must match too, because _bootstrap_share_intervals's
        # resample draws are order-sensitive.
        # _render_pooled_block sorts by (root ordinal, branch_name), which
        # places each filler immediately after its own account's real branch
        # ("feat-a" before "feat-a-filler", "feat-b" before "feat-b-filler").
        filler_branch = review_rounds._PooledBranchTotals(
            round_dollars=0.0, agent_dollars=0.0, branch_dollars=0.0,
            skill_round_counts={"code-review": 1, "plan-review": 0, "ready-for-review": 0},
            skill_round_dollars={"code-review": 0.0, "plan-review": 0.0, "ready-for-review": 0.0},
            rounds_with_dangling=0, rounds_with_unpriced=0, round_count=1,
        )
        branch_a, branch_b = _asymmetric_two_branch_pooled_totals()
        point, lo, hi = review_rounds._bootstrap_share_intervals(
            [branch_a, filler_branch, branch_b, filler_branch]
        )["spend_inside"]
        assert round(point, 1) == share_of_sums
        expected_line = f"    {'inside round windows':<30}{review_rounds._fmt_share_with_ci(point, lo, hi)}"
        assert expected_line in out

    def test_multi_round_branch_accumulates_skill_totals_across_rounds(
        self, tmp_path, monkeypatch, capsys,
    ):
        """feat-a has two in-scope code-review rounds with distinct dollar
        amounts; skill_round_counts/skill_round_dollars must sum both, not
        retain only the last one -- no other branch in this pooled fixture
        gives any branch more than one in-scope round, so an
        `=`-instead-of-`+=` accumulation regression would otherwise go
        uncaught. Hand-built rounds/branch_totals via a direct
        _render_pooled_block call. Five padding branches, split across both
        accounts and repeating skills, clear the four-branch bootstrap floor.

        Stubs `_pooled_dominance_breach` to isolate this test from the
        dominance-precision floor. Every branch here sets branch_dollars
        equal to round_dollars, so spend_inside is 100% with zero variance
        across every branch. The floor's exact-interval formula correctly
        flags that degenerate share as a breach, which is irrelevant to what
        this test checks.
        """
        monkeypatch.setattr(review_rounds, "_pooled_dominance_breach", lambda _intervals, _totals: False)
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 0.20, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 0.60, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a2"), "skill": "plan-review",
             "main_dollars": 0.10, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a3"), "skill": "ready-for-review",
             "main_dollars": 0.10, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 0.20, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b2"), "skill": "code-review",
             "main_dollars": 0.10, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b3"), "skill": "ready-for-review",
             "main_dollars": 0.10, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 0.80, (0, "feat-a2"): 0.10, (0, "feat-a3"): 0.10,
            (1, "feat-b"): 0.20, (1, "feat-b2"): 0.10, (1, "feat-b3"): 0.10,
        }
        args = _review_round_cost_args(pooled=True)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out

        # Sums both of feat-a's code-review rounds ($0.20 + $0.60 = $0.80,
        # count 2), not just the second one -- what an `=`-instead-of-`+=`
        # regression would leave behind. The padding branches each hold a
        # single round, with skills repeating across them, matching `rounds`
        # above.
        def _single_round_branch(dollars: float, skill: str) -> review_rounds._PooledBranchTotals:
            skill_counts = {"code-review": 0, "plan-review": 0, "ready-for-review": 0}
            skill_counts[skill] = 1
            skill_dollars = {"code-review": 0.0, "plan-review": 0.0, "ready-for-review": 0.0}
            skill_dollars[skill] = dollars
            return review_rounds._PooledBranchTotals(
                round_dollars=dollars, agent_dollars=0.0, branch_dollars=dollars,
                skill_round_counts=skill_counts, skill_round_dollars=skill_dollars,
                rounds_with_dangling=0, rounds_with_unpriced=0, round_count=1,
            )

        branch_a = review_rounds._PooledBranchTotals(
            round_dollars=0.80, agent_dollars=0.0, branch_dollars=0.80,
            skill_round_counts={"code-review": 2, "plan-review": 0, "ready-for-review": 0},
            skill_round_dollars={"code-review": 0.80, "plan-review": 0.0, "ready-for-review": 0.0},
            rounds_with_dangling=0, rounds_with_unpriced=0, round_count=2,
        )
        per_branch = [
            branch_a, _single_round_branch(0.10, "plan-review"), _single_round_branch(0.10, "ready-for-review"),
            _single_round_branch(0.20, "plan-review"), _single_round_branch(0.10, "code-review"),
            _single_round_branch(0.10, "ready-for-review"),
        ]
        intervals = review_rounds._bootstrap_share_intervals(per_branch)
        for label, key in (
            ("code-review", "skill_spend:code-review"), ("code-review", "skill_rounds:code-review"),
        ):
            point, lo, hi = intervals[key]
            expected_line = f"    {label:<30}{review_rounds._fmt_share_with_ci(point, lo, hi)}"
            assert expected_line in out

    def test_render_pooled_block_hands_the_bootstrap_each_branch_totals_field_by_field(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The render seam's per-branch aggregation: every round field is
        summed per branch and the result reaches _bootstrap_share_intervals.
        Each branch carries a distinct nonzero agent_dollars, distinct from
        its main_dollars, so a swapped or dropped field changes a total.
        Hand-built rounds via a direct _render_pooled_block call, with
        _bootstrap_share_intervals recorded instead of run so the assertion
        sees the totals rather than the interval math.
        """
        monkeypatch.setattr(review_rounds, "_pooled_dominance_breach", lambda _intervals, _totals: False)
        captured_per_branch: list[list[review_rounds._PooledBranchTotals]] = []

        def recording_stub(per_branch):
            captured_per_branch.append(list(per_branch))
            return dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (50.0, 40.0, 60.0))

        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", recording_stub)
        roots = _two_declared_roots(tmp_path, monkeypatch)

        def _round(branch_key, skill, main, agent, dangling=0, unpriced_turns=0):
            return {
                "branch_key": branch_key, "skill": skill, "main_dollars": main, "agent_dollars": agent,
                "unpriced_turns": unpriced_turns, "dangling": dangling,
            }

        rounds = [
            _round((0, "feat-a"), "code-review", 0.20, 0.05, dangling=2),
            _round((0, "feat-a"), "plan-review", 0.10, 0.03, unpriced_turns=2),
            _round((0, "feat-a2"), "ready-for-review", 0.30, 0.07),
            _round((1, "feat-b"), "code-review", 0.40, 0.11),
            _round((1, "feat-b2"), "plan-review", 0.15, 0.02),
            _round((1, "feat-b3"), "code-review", 0.25, 0.09, unpriced_turns=1),
        ]
        # Each branch_dollars is distinct, so it keys the captured totals
        # without depending on the order the render sorts branches into.
        branch_totals = {
            (0, "feat-a"): 1.0, (0, "feat-a2"): 2.0, (1, "feat-b"): 3.0, (1, "feat-b2"): 4.0, (1, "feat-b3"): 5.0,
        }

        review_rounds._render_pooled_block(
            _review_round_cost_args(pooled=True), roots, "*", rounds, branch_totals, scan_gaps=Counter(),
        )
        capsys.readouterr()

        assert len(captured_per_branch) == 1
        totals_by_branch_dollars = {totals.branch_dollars: totals for totals in captured_per_branch[0]}
        assert len(totals_by_branch_dollars) == len(branch_totals)

        def _skills(code_review=0.0, plan_review=0.0, ready_for_review=0.0):
            return {"code-review": code_review, "plan-review": plan_review, "ready-for-review": ready_for_review}

        expected = {
            # branch_dollars: (round_dollars, agent_dollars, skill counts, skill dollars, dangling, unpriced, rounds)
            1.0: (0.38, 0.08, _skills(1, 1), _skills(0.25, 0.13), 1, 1, 2),
            2.0: (0.37, 0.07, _skills(ready_for_review=1), _skills(ready_for_review=0.37), 0, 0, 1),
            3.0: (0.51, 0.11, _skills(code_review=1), _skills(code_review=0.51), 0, 0, 1),
            4.0: (0.17, 0.02, _skills(plan_review=1), _skills(plan_review=0.17), 0, 0, 1),
            5.0: (0.34, 0.09, _skills(code_review=1), _skills(code_review=0.34), 0, 1, 1),
        }
        for branch_dollars, (
            round_dollars, agent_dollars, skill_counts, skill_dollars, dangling, unpriced, round_count,
        ) in expected.items():
            totals = totals_by_branch_dollars[branch_dollars]
            assert totals.round_dollars == pytest.approx(round_dollars)
            assert totals.agent_dollars == pytest.approx(agent_dollars)
            assert totals.skill_round_counts == skill_counts
            assert totals.skill_round_dollars == pytest.approx(skill_dollars)
            assert totals.rounds_with_dangling == dangling
            assert totals.rounds_with_unpriced == unpriced
            assert totals.round_count == round_count

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
        """Eight branches (four per root), each with an in-scope round but zero priced dollars
        (an unrecognized-model turn) -- proves _render_pooled_block never
        raises ZeroDivisionError, printing the zero-denominator wording for
        every dollar-based share instead.

        Stubs `_pooled_dominance_breach` to isolate this test from the
        dominance-precision floor. This fixture's round-count split, though
        evenly balanced across both accounts, still lets the floor's
        exact-interval formula legitimately flag a round-count-keyed share
        as a breach. That breach is irrelevant to what this test checks, but
        would otherwise blank the dollar-based zero-denominator wording this
        test asserts on along with it.
        """
        monkeypatch.setattr(review_rounds, "_pooled_dominance_breach", lambda _intervals, _totals: False)
        _pooled_two_root_zero_priced_dollars_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        out = capsys.readouterr().out
        assert "(95% CI not computed — no priced branch spend)" in out

    def test_zero_denominator_shares_skip_the_dominance_floor_while_round_count_shares_print(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The real dominance floor runs against a pool whose every dollar
        denominator is zero. Each of the four branches holds one round of
        every review skill, so each account holds the same skill mix and no
        round-count share is an exact 0% or 100% (either would trip the
        floor for a reason unrelated to the zero denominators). The six
        dollar-keyed shares must print the zero-denominator wording instead
        of dividing by their zero pooled denominator, and the three
        round-count shares must print figures. Hand-built rounds via a
        direct _render_pooled_block call, with no stub on the floor.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        branch_keys = [(0, "feat-a1"), (0, "feat-a2"), (1, "feat-b1"), (1, "feat-b2")]
        rounds = [
            {"branch_key": branch_key, "skill": skill, "main_dollars": 0.0, "agent_dollars": 0.0,
             "unpriced_turns": 0, "dangling": 0}
            for branch_key in branch_keys
            for skill in review_rounds.REVIEW_SKILLS
        ]
        branch_totals = dict.fromkeys(branch_keys, 0.0)

        review_rounds._render_pooled_block(
            _review_round_cost_args(pooled=True), roots, "*", rounds, branch_totals, scan_gaps=Counter(),
        )
        out = capsys.readouterr().out

        zero_denominator_wording = "(95% CI not computed — no priced branch spend)"
        dollar_keyed_labels = [
            "inside round windows", "outside every round window", "reviewer dispatches only",
            *review_rounds.REVIEW_SKILLS,
        ]
        assert [line for line in out.splitlines() if zero_denominator_wording in line] == [
            f"    {label:<30}{zero_denominator_wording}" for label in dollar_keyed_labels
        ]
        rounds_by_skill_section = out.split("  Rounds by skill\n")[1]
        assert _count_numeric_share_lines(rounds_by_skill_section) == len(review_rounds.REVIEW_SKILLS)

    def test_branch_with_no_in_scope_rounds_does_not_change_the_pooled_figures(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Only a branch key present in the in-scope rounds enters the pool,
        mirroring the per-root footer; a branch with priced non-round spend
        but zero rounds must not enter via branch_totals alone. The pool is
        four round-bearing branches across two roots, enough to clear both
        count floors, so a numeric figure prints and any change the extra
        branch caused would show. Runs with --show-withheld so the
        dominance-precision floor cannot blank the compared figures.
        Hand-built rounds/branch_totals via a direct _render_pooled_block
        call.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        round_dollars_by_branch = {
            (0, "feat-a1"): 1.0, (0, "feat-a2"): 2.0, (1, "feat-b1"): 3.0, (1, "feat-b2"): 1.5,
        }
        rounds = [
            {"branch_key": branch_key, "skill": "code-review",
             "main_dollars": dollars, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0}
            for branch_key, dollars in round_dollars_by_branch.items()
        ]
        branch_totals = {branch_key: dollars * 2 for branch_key, dollars in round_dollars_by_branch.items()}
        args = _review_round_cost_args(pooled=True, show_withheld=True)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out_without_idle_branch = capsys.readouterr().out
        # Priced spend but no round: its $50.00 dwarfs the pool's own spend,
        # so admitting it would move the inside- and outside-round-window spend shares.
        branch_totals_with_idle_branch = {**branch_totals, (0, "feat-idle"): 50.0}
        review_rounds._render_pooled_block(
            args, roots, "*", rounds, branch_totals_with_idle_branch, scan_gaps=Counter(),
        )
        out_with_idle_branch = capsys.readouterr().out

        assert re.search(r"\d+\.\d% \(95% CI \d+\.\d-\d+\.\d%\)", out_without_idle_branch)
        assert "too few branches" not in out_without_idle_branch
        assert out_with_idle_branch == out_without_idle_branch

    @pytest.mark.parametrize("show_withheld", [False, True])
    def test_pool_with_two_roots_but_one_contributing_account_stays_degenerate(
        self, tmp_path, monkeypatch, capsys, show_withheld,
    ):
        """Two resolved roots and four branches, all from the same root --
        only one account actually contributed data. Four same-root branches
        clear the branch-count floor, so only the contributing-roots floor
        can withhold: without it the pool would bootstrap into a real
        percentage that is 100% one account's data. Must degrade to the
        same "too few branches" wording as a genuinely single-branch pool.
        The show_withheld=True leg fails if the contributing-roots clause is
        removed, because --show-withheld skips the dominance floor that
        would otherwise catch the pool. The show_withheld=False leg pins
        that a one-account pool never prints a figure.
        Hand-built rounds/branch_totals via a direct _render_pooled_block
        call.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        # roots[1] (acct-b) resolves but contributes no branch at all.
        round_dollars_by_branch = {
            (0, "feat-a1"): 0.20, (0, "feat-a2"): 0.30, (0, "feat-a3"): 0.25, (0, "feat-a4"): 0.15,
        }
        rounds = [
            {"branch_key": branch_key, "skill": "code-review",
             "main_dollars": dollars, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0}
            for branch_key, dollars in round_dollars_by_branch.items()
        ]
        branch_totals = dict(round_dollars_by_branch)
        args = _review_round_cost_args(pooled=True, show_withheld=show_withheld)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert not any(re.search(r"\d+\.\d%", line) for line in out.splitlines())

    @pytest.mark.parametrize("show_withheld", [False, True])
    def test_pool_with_exactly_three_branches_across_two_roots_stays_degenerate(
        self, tmp_path, monkeypatch, capsys, show_withheld,
    ):
        """Pins the bootstrap-validity floor's own threshold
        (len(per_branch) < 4): three branches across two contributing roots
        clear the contributing-roots floor (>= 2) but must still trip the
        branch-count floor. This is the only test with two contributing
        roots and fewer than four branches.
        The show_withheld=True leg fails if the branch-count clause is
        removed or relaxed, because --show-withheld skips the dominance floor
        that would otherwise withhold this exact-0%/100%-share fixture. The
        show_withheld=False leg pins that the pool never prints a figure end
        to end, but the dominance floor alone would also satisfy it.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a1"), "skill": "code-review",
             "main_dollars": 10.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a2"), "skill": "plan-review",
             "main_dollars": 10.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b1"), "skill": "ready-for-review",
             "main_dollars": 10.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a1"): 10.0, (0, "feat-a2"): 10.0, (1, "feat-b1"): 10.0,
        }
        args = _review_round_cost_args(pooled=True, show_withheld=show_withheld)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert not any(re.search(r"\d+\.\d%", line) for line in out.splitlines())

    def test_dominance_precision_floor_withholds_the_whole_block_on_an_extreme_split(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A 99/1 split between two contributing accounts, with filler
        branches bringing the pool to four, clears the count floor but
        must still be withheld by the dominance-precision floor: even a
        CI as wide as (85, 95) around a point of 90 can't rule out that
        the dominant account (99% of this share's own denominator) alone
        drives the figure -- (1 - 0.99) * 100 = 1.0 percentage point of
        possible swing is well inside that 10-point-wide CI. Stubs
        _bootstrap_share_intervals so the CI is exact and deterministic,
        matching test_pooled_render_binds_each_figure_line_to_its_own_stat_key's
        own stubbing convention; the account weights come from the real
        branch_totals/rounds this call is given.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        # feat-a-filler/feat-b-filler: zero branch_dollars each, so they clear
        # the four-branch bootstrap floor without moving either account's
        # w_max away from the 99/1 split this test pins.
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 99.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 1.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 99.0, (0, "feat-a-filler"): 0.0,
            (1, "feat-b"): 1.0, (1, "feat-b-filler"): 0.0,
        }
        args = _review_round_cost_args(pooled=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (90.0, 85.0, 95.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert not any(re.search(r"\d+\.\d%", line) for line in out.splitlines())

    def test_dominance_precision_floor_does_not_fire_on_a_moderate_balanced_split(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A 60/40 split is nowhere near the dominance-precision floor at
        this share's own stubbed CI width -- (1 - 0.6) * 100 = 40
        percentage points of possible swing is well outside the stubbed
        CI's own 20-point half-width, so real figures must still print
        rather than degrade to the extreme-split test's wording above."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        # feat-a-filler/feat-b-filler: zero branch_dollars each, so they clear
        # the four-branch bootstrap floor without moving either account's
        # w_max away from the 60/40 split this test pins.
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 60.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 40.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 60.0, (0, "feat-a-filler"): 0.0,
            (1, "feat-b"): 40.0, (1, "feat-b-filler"): 0.0,
        }
        args = _review_round_cost_args(pooled=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (50.0, 30.0, 70.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        assert "too few branches" not in out
        assert "50.0% (95% CI 30.0-70.0%)" in out

    def test_dominance_precision_floor_extreme_split_depends_on_the_dominance_floor(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Mutation-style guard for the extreme-split test above: bypasses
        _pooled_dominance_breach (forcing it to never fire) and reruns
        the identical fixture and CI stub, confirming the block prints
        real figures once the dominance-precision floor is removed. This
        proves the extreme-split test's withheld assertion is pinned to
        that floor specifically -- the fixture already clears the
        `contributing_roots` count floor on its own (two roots, four
        branches including the fillers), so that count floor alone cannot be what makes the
        extreme-split test pass.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        # feat-a-filler/feat-b-filler: zero branch_dollars each, so they clear
        # the four-branch bootstrap floor without moving either account's
        # w_max away from the 99/1 split this test pins.
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 99.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 1.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 99.0, (0, "feat-a-filler"): 0.0,
            (1, "feat-b"): 1.0, (1, "feat-b-filler"): 0.0,
        }
        args = _review_round_cost_args(pooled=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (90.0, 85.0, 95.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)
        monkeypatch.setattr(review_rounds, "_pooled_dominance_breach", lambda _intervals, _totals: False)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        assert "too few branches" not in out
        assert "90.0% (95% CI 85.0-95.0%)" in out

    def test_dominance_precision_floor_withholds_on_a_round_dollars_imbalance_alone(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The other dominance tests imbalance round_dollars and branch_dollars
        together. This fixture holds branch_dollars (the
        spend_inside/outside/reviewer_only denominator) perfectly balanced
        (50/50 between the two accounts) while splitting round_dollars --
        the skill_spend:* denominator -- 99/1, proving
        _pooled_dominance_breach also catches an imbalance that shows up
        only in a share other than the three spend-of-branch shares.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 99.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 1.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        # branch_dollars: 50/50 within each account (100.0 total each), so
        # w_max=0.5 for spend_inside/outside/reviewer_only -- balanced.
        # round_dollars (main_dollars + agent_dollars, above): 99/1 between
        # accounts -- the only imbalanced field in this fixture.
        branch_totals = {
            (0, "feat-a"): 50.0, (0, "feat-a-filler"): 50.0,
            (1, "feat-b"): 50.0, (1, "feat-b-filler"): 50.0,
        }
        args = _review_round_cost_args(pooled=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (90.0, 85.0, 95.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert not any(re.search(r"\d+\.\d%", line) for line in out.splitlines())

    def test_dominance_precision_floor_withholds_on_a_round_count_imbalance_alone(
        self, tmp_path, monkeypatch, capsys,
    ):
        """_POOLED_STAT_KEYS iterates branch_dollars-keyed shares and
        round_dollars-keyed shares before the round_count-keyed shares
        (skill_rounds:*, gap_dangling, gap_unpriced), and
        _pooled_dominance_breach returns on the first breaching key --
        every dominance fixture above imbalances branch_dollars or
        round_dollars, so none of them ever let the loop reach a
        round_count-keyed key. This fixture holds branch_dollars and
        round_dollars perfectly balanced (50/50 between the two accounts)
        while skewing round_count 99-vs-2 via zero-dollar filler rounds on
        account 0 alone, proving _pooled_dominance_breach also catches an
        imbalance that shows up only in a round_count-keyed share.

        account 0 contributes 99 of 101 total rounds (feat-a's own round
        plus 98 zero-dollar filler rounds on feat-a-filler); account 1
        contributes 2 (feat-b's own round plus 1 filler on feat-b-filler).
        w_max = 99/101 ~= 0.9802, so max_swing = (1 - w_max) * 100 ~= 1.98
        percentage points -- well inside the stubbed CI's 5-point
        half-width, so the round_count-keyed shares breach on their own.
        A misattributed or skipped round_count accumulation would leave
        every key's w_max at the balanced 0.5 that branch_dollars and
        round_dollars share here, so no key would breach and the block
        would print real figures instead of withholding.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 50.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            *(
                {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
                 "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0}
                for _ in range(98)
            ),
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 50.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        # branch_dollars: 50/50 within each account (100.0 total each) --
        # balanced, mirroring the round_dollars-imbalance fixture above.
        # round_dollars (main_dollars + agent_dollars, above): 50/50
        # between accounts -- also balanced.
        # round_count: 99 (feat-a's 1 real round + 98 fillers) vs 2
        # (feat-b's 1 real round + 1 filler) -- the only imbalanced field.
        branch_totals = {
            (0, "feat-a"): 50.0, (0, "feat-a-filler"): 50.0,
            (1, "feat-b"): 50.0, (1, "feat-b-filler"): 50.0,
        }
        args = _review_round_cost_args(pooled=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (90.0, 85.0, 95.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert not any(re.search(r"\d+\.\d%", line) for line in out.splitlines())

    def test_dominance_precision_floor_extreme_split_generalizes_past_root_zero(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Every dominance-floor fixture above puts the larger dollar figure
        on branch_key[0] (root 0).
        Root 0 is also always the first account _render_pooled_block inserts
        into account_denominator_totals, because that insertion loop walks
        branches in sorted branch_key order regardless of dollar amounts.
        So "true max over every account" and "always read the first-inserted
        account" coincide in every one of those fixtures.
        This fixture reruns the extreme-split shape with the dollar split
        reversed: root 1 dominant (99), root 0 minor (1).
        A first-inserted-account bug would read root 0's 1% weight and never
        breach.
        A true max reads root 1's 99% weight and still withholds, exactly
        like the un-reversed extreme-split test above.
        The moderate-split shape can't make this distinction, because even
        its full 60% weight doesn't breach against its own stubbed CI.
        Root 0 vs root 1 therefore makes no observable difference there.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 1.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 99.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 1.0, (0, "feat-a-filler"): 0.0,
            (1, "feat-b"): 99.0, (1, "feat-b-filler"): 0.0,
        }
        args = _review_round_cost_args(pooled=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (90.0, 85.0, 95.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out = capsys.readouterr().out
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert not any(re.search(r"\d+\.\d%", line) for line in out.splitlines())

    def test_no_print_before_refusal_on_single_root_case(self, fake_projects, capsys):
        """The single-root refusal fires last among _pooled_scope_refusal's
        pre-scan checks, so it's most exposed to a reordering regression;
        confirms no header/pointer/banner text reaches stdout ahead of the
        exit(2). Every other refusal test in this class besides this one
        and its unreadable-declared-root sibling below checks the exit and
        the message alone, not the printed side effect.
        """
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        out = capsys.readouterr().out
        assert out == ""

    def test_no_print_before_refusal_on_single_root_case_with_unreadable_declared_root(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Same ordering invariant as the test above, reached instead by
        declared_transcript_roots() dropping the sole declared entry as
        invalid. Its own "declared root N unreadable" diagnostic must not
        leak the dropped entry's index on stderr either, on this same
        refuse-and-print-nothing path.
        """
        bare_dir = tmp_path / "bare-account"
        bare_dir.mkdir()
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{bare_dir}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert not any(c.isdigit() for c in err)

    def test_pooled_scope_refusal_skips_root_count_clause_when_roots_is_none(self):
        """The pre-scan refusal call path: no narrowing flag set,
        and roots=None (the default), returns None without raising."""
        args = _review_round_cost_args(pooled=True)
        assert review_rounds._pooled_scope_refusal(args, roots=None) is None

    def test_pooled_run_prints_no_root_count_diagnostic_to_stderr(self, tmp_path, monkeypatch, capsys):
        """A successful two-root --pooled run prints no digit on stderr.

        scope's own multi-root diagnostic prints "scanning root N/M..." on
        stderr whenever more than one root is scanned, which would disclose
        the resolved root count on the one output stream
        _pooled_resolved_scope_header doesn't reach.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        err = capsys.readouterr().err
        assert not any(c.isdigit() for c in err)
        assert review_rounds._POOLED_STDERR_WITHHELD_NOTICE not in err

    def test_pooled_run_with_unreadable_declared_root_entry_prints_no_digit_to_stderr(
        self, tmp_path, monkeypatch, capsys,
    ):
        """declared_transcript_roots()'s own "declared root N unreadable"
        warning fires inside scope.resolve_scan_roots(), before the
        post-resolution refusal. It reveals the root count the same way
        "scanning root N/M..." does, so both need the same filter.

        Poolable case: two valid roots plus two invalid declared-
        roots-file entries (directories with no projects/ subdirectory).
        The replacement notice's own per-call dedup is exercised this way,
        not just its digit-free wording.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        bare_dir_1 = tmp_path / "bare-account-1"
        bare_dir_1.mkdir()
        bare_dir_2 = tmp_path / "bare-account-2"
        bare_dir_2.mkdir()
        roots_file = tmp_path / "roots"
        with roots_file.open("a") as f:
            f.write(f"{bare_dir_1}\n{bare_dir_2}\n")

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        err = capsys.readouterr().err
        assert not any(c.isdigit() for c in err)
        assert err.count(review_rounds._DECLARED_ROOT_SKIPPED_NOTICE) == 1
        assert review_rounds._POOLED_STDERR_WITHHELD_NOTICE not in err

    def test_pooled_stderr_filter_withholds_an_unrecognized_diagnostic(self, capsys):
        """The filter fails closed: a non-"scanning root" diagnostic is
        withheld behind _POOLED_STDERR_WITHHELD_NOTICE instead of reaching
        stderr raw, while a "scanning root N/M..." line is still dropped
        entirely. The injected diagnostic is a representative unsafe-shaped
        string, not one --pooled's real call graph can emit: only
        _iter_scoped_sessions emits it, and the pooled path always resolves
        through _iter_glob_scoped_sessions instead.
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
        assert "cannot scan account-1" not in err
        assert err.count(review_rounds._POOLED_STDERR_WITHHELD_NOTICE) == 1

    def test_pooled_stderr_filter_withholds_buffered_lines_when_wrapped_call_raises(
        self, monkeypatch, capsys,
    ):
        """A diagnostic buffered before the wrapped call raises is withheld
        behind the notice too, not reemitted raw."""
        def fake_compute(*args, **kwargs):
            print("cannot scan account-9 (Permission denied) — skipping", file=sys.stderr)
            raise RuntimeError("boom")

        monkeypatch.setattr(review_rounds, "compute_review_round_costs", fake_compute)
        with pytest.raises(RuntimeError):
            review_rounds._pooled_compute_review_round_costs()
        err = capsys.readouterr().err
        assert "cannot scan account-9" not in err
        assert err.count(review_rounds._POOLED_STDERR_WITHHELD_NOTICE) == 1

    def test_pooled_stderr_filter_withholds_a_pricing_non_contiguous_merge_notice(
        self, monkeypatch, capsys,
    ):
        """pricing.dedup_turns_by_request_id's own NOTICE line
        (pricing._log_non_contiguous_merge_decision) is reachable under
        --pooled through the filtered compute call, and it names a raw
        requestId. It matches none of the known diagnostic shapes, so it
        must be withheld like any other unrecognized line. This is a real
        production print reachable under --pooled.
        """
        monkeypatch.setattr(pricing, "_non_contiguous_merge_notices_logged", set())
        review_rounds._pooled_filtered_stderr_call(
            pricing._log_non_contiguous_merge_decision, "<placeholder-request-id>", 2, merged=True,
        )
        err = capsys.readouterr().err
        assert "<placeholder-request-id>" not in err
        assert err.count(review_rounds._POOLED_STDERR_WITHHELD_NOTICE) == 1

    def test_withheld_diagnostic_reaches_stderr_on_a_non_pooled_rerun(self, tmp_path, monkeypatch, capsys):
        """A diagnostic --pooled withholds still reaches an operator who
        reruns without --pooled. One monkeypatch on
        review_rounds.compute_review_round_costs reaches both the pooled
        and non-pooled call sites, since each looks the function up as a
        module global at call time.
        """
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        real_compute = review_rounds.compute_review_round_costs

        def wrapped_compute(*args, **kwargs):
            print("<placeholder-diagnostic>", file=sys.stderr)
            return real_compute(*args, **kwargs)

        monkeypatch.setattr(review_rounds, "compute_review_round_costs", wrapped_compute)

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True))
        pooled_err = capsys.readouterr().err
        assert "<placeholder-diagnostic>" not in pooled_err
        assert pooled_err.count(review_rounds._POOLED_STDERR_WITHHELD_NOTICE) == 1

        _mod.cmd_review_round_cost(_review_round_cost_args())
        non_pooled_err = capsys.readouterr().err
        assert "<placeholder-diagnostic>" in non_pooled_err
        assert review_rounds._POOLED_STDERR_WITHHELD_NOTICE not in non_pooled_err


class TestPooledShowWithheld:
    """--show-withheld: the view of whoever runs the command, of a share the
    dominance-precision floor would otherwise withhold. Reuses
    TestCmdReviewRoundCostPooled's dominance-floor fixtures above."""

    def test_show_withheld_prints_the_real_figure_on_an_extreme_split(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The 99/1-split fixture from
        test_dominance_precision_floor_withholds_the_whole_block_on_an_extreme_split,
        with --show-withheld: the banner replaces the publication pointer
        on both streams, and the real figure prints instead of being
        blanked."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 99.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 1.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 99.0, (0, "feat-a-filler"): 0.0,
            (1, "feat-b"): 1.0, (1, "feat-b-filler"): 0.0,
        }
        args = _review_round_cost_args(pooled=True, show_withheld=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (90.0, 85.0, 95.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out, err = capsys.readouterr()
        assert out.count(review_rounds._POOLED_SHOW_WITHHELD_BANNER) == 1
        assert err.count(review_rounds._POOLED_SHOW_WITHHELD_BANNER) == 1
        assert review_rounds._POOLED_PUBLICATION_POINTER not in out
        assert "90.0% (95% CI 85.0-95.0%)" in out

    def test_show_withheld_banner_prints_even_on_a_non_breaching_split(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The 60/40-split fixture from
        test_dominance_precision_floor_does_not_fire_on_a_moderate_balanced_split,
        with --show-withheld: the banner still prints on both streams and
        the publication pointer is still absent, even though this fixture
        never breaches -- pins that the banner depends on the flag alone,
        not on _pooled_dominance_breach's own result.
        """
        roots = _two_declared_roots(tmp_path, monkeypatch)
        rounds = [
            {"branch_key": (0, "feat-a"), "skill": "code-review",
             "main_dollars": 60.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (0, "feat-a-filler"), "skill": "code-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b"), "skill": "plan-review",
             "main_dollars": 40.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
            {"branch_key": (1, "feat-b-filler"), "skill": "plan-review",
             "main_dollars": 0.0, "agent_dollars": 0.0, "unpriced_turns": 0, "dangling": 0},
        ]
        branch_totals = {
            (0, "feat-a"): 60.0, (0, "feat-a-filler"): 0.0,
            (1, "feat-b"): 40.0, (1, "feat-b-filler"): 0.0,
        }
        args = _review_round_cost_args(pooled=True, show_withheld=True)
        stub_intervals = dict.fromkeys(review_rounds._POOLED_STAT_KEYS, (50.0, 30.0, 70.0))
        monkeypatch.setattr(review_rounds, "_bootstrap_share_intervals", lambda _per_branch: stub_intervals)

        review_rounds._render_pooled_block(args, roots, "*", rounds, branch_totals, scan_gaps=Counter())
        out, err = capsys.readouterr()
        assert out.count(review_rounds._POOLED_SHOW_WITHHELD_BANNER) == 1
        assert err.count(review_rounds._POOLED_SHOW_WITHHELD_BANNER) == 1
        assert review_rounds._POOLED_PUBLICATION_POINTER not in out

    def test_show_withheld_without_pooled_refuses_before_any_scan(self, monkeypatch, capsys):
        """--show-withheld requires --pooled; refuses before
        resolve_scan_roots runs at all."""
        def _fail_if_called(*_args, **_kwargs):
            raise AssertionError("resolve_scan_roots must not run")

        monkeypatch.setattr(scope, "resolve_scan_roots", _fail_if_called)

        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(show_withheld=True))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "--show-withheld" in err
        assert "--pooled" in err

    def test_show_withheld_never_bypasses_an_existing_scope_narrowing_refusal(
        self, tmp_path, monkeypatch, capsys,
    ):
        """--show-withheld plus --pooled plus --branches still exits 2 on
        the --branches refusal, not a --show-withheld-specific
        one -- proves --show-withheld never bypasses an existing
        scope-narrowing refusal."""
        _pooled_two_root_fixture(tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(
                _review_round_cost_args(pooled=True, show_withheld=True, branches="feat-a")
            )
        assert exc.value.code == 2
        assert "--branches" in capsys.readouterr().err

    def test_show_withheld_never_bypasses_the_single_resolved_root_refusal(self, fake_projects, capsys):
        """--show-withheld plus --pooled over one resolved root still exits
        2 with nothing on stdout, so the flag never prints a figure for a
        single-account pool."""
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert scope.TRANSCRIPT_CONFIG_DIRS_LABEL in err
        assert review_rounds._POOLED_SCAN_ABORTED_MESSAGE not in err

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_show_withheld_never_bypasses_the_scan_gap_refusal(self, tmp_path, monkeypatch, capsys):
        """--show-withheld plus --pooled over a root whose projects/
        directory is unreadable still exits 2 with nothing on stdout, so
        the flag never prints a figure for a pool missing an account."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        os.chmod(roots[1], 0o000)
        try:
            with pytest.raises(SystemExit) as exc:
                _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        finally:
            os.chmod(roots[1], 0o755)
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert review_rounds._POOLED_SCAN_GAP_REFUSAL in err
        assert review_rounds._POOLED_SCAN_ABORTED_MESSAGE not in err

    def test_show_withheld_never_manufactures_a_figure_under_the_too_few_branches_floor(
        self, tmp_path, monkeypatch, capsys,
    ):
        """--show-withheld skips only the `_pooled_dominance_breach` blank;
        `too_few_for_bootstrap` blanks both ways unaffected by the flag. Reuses
        test_degenerate_single_branch_prints_too_few_branches_wording_with_no_digit's
        fixture: two declared roots (clears the root-count refusal) but only one
        branch with an in-scope round (trips too_few_for_bootstrap)."""
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

        _mod.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        out, err = capsys.readouterr()
        assert out.count(review_rounds._POOLED_SHOW_WITHHELD_BANNER) == 1
        assert err.count(review_rounds._POOLED_SHOW_WITHHELD_BANNER) == 1
        too_few_lines = [line for line in out.splitlines() if "too few branches" in line]
        assert too_few_lines
        assert all(line.strip().endswith("(95% CI not computed — too few branches in scope)") for line in too_few_lines)
        assert not any(c.isdigit() for line in too_few_lines for c in line.rsplit("—", 1)[-1])
