"""Tests for review_trace.py's reviewer-spawn round labels and --omit-plan-review-spawns."""
import functools
import re

import pytest
from helpers import SKILLS_DIR, extract_skill_command
from transcript_analysis import (
    pricing,
    review_rounds,
    review_trace,
)

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _review_trace_args,
    _skill_use,
    _skill_use_rec,
    _slash_user,
    _tool_result,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
)


def _spawn_rec(
    subagent_type: str, ts: str, *, branch: str = "feat", tool_id: str = "a1",
    model: str = "claude-sonnet-4-6", **kwargs,
) -> dict:
    return _asst(model, branch=branch, ts=ts, content=[_agent_use(tool_id, subagent_type)], **kwargs)


_PLAN_REVIEW_SKILL_MD = SKILLS_DIR / "plan-review" / "SKILL.md"


@functools.cache
def _deactivate_plan_review() -> str:
    """Read at test time so a drifted marker fails only the tests that consume the recipe."""
    return extract_skill_command(_PLAN_REVIEW_SKILL_MD, "deactivate-gate")


@functools.cache
def _write_plan_review() -> str:
    return extract_skill_command(_PLAN_REVIEW_SKILL_MD, "record-completion")


def _expand_recipe_placeholders(template: str) -> str:
    """Brace-safe: shell braces in a template stay literal, and only a recipe the template names is extracted."""
    for placeholder, extract_recipe in (("{deactivate}", _deactivate_plan_review), ("{write}", _write_plan_review)):
        if placeholder in template:
            template = template.replace(placeholder, extract_recipe())
    return template


_READY_FOR_REVIEW_SKILL_MD = SKILLS_DIR / "ready-for-review" / "SKILL.md"


def _bash_rec(command: str, ts: str, *, tool_id: str = "b1", **kwargs) -> dict:
    """Main-thread assistant record running one Bash command."""
    return _asst("claude-sonnet-4-6", branch="feat", ts=ts, content=[_bash_use(tool_id, command)], **kwargs)


def _spawn_rounds(records: list[dict]) -> list[str | None]:
    """round_skill of every reviewer-spawn event, in timeline order, after labeling."""
    events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
        records, None, None, None,
    )
    review_trace._attach_reviewer_spawn_rounds(events, records)
    return [e["round_skill"] for e in events if e["kind"] == "reviewer-spawn"]


class TestReviewerSpawnRoundLabels:
    """_attach_reviewer_spawn_rounds and the round= / --omit-plan-review-spawns surface."""

    def test_spawn_after_a_code_review_skill_call_reads_code_review(self):
        assert _spawn_rounds([
            _skill_use_rec("code-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z"),
        ]) == ["code-review"]

    def test_human_message_closes_the_window_so_a_later_spawn_reads_none(self):
        """A spawn before the human message reads plan-review; one after it is outside every window."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:01:00.000Z", tool_id="a1"),
            _user_msg("go on", branch="feat", ts="2026-05-19T10:02:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:03:00.000Z", tool_id="a2"),
        ]) == ["plan-review", None]

    def test_slash_code_review_inside_a_plan_review_window_takes_over(self):
        """A /code-review slash record after a plan-review Skill call opens the window the next spawn reads."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _slash_user("code-review", branch="feat", ts="2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["code-review"]

    def test_skill_review_call_inside_a_code_review_round_opens_no_window(self):
        """skill-review is in REVIEW_TRACE_SKILLS but not REVIEW_SKILLS, so it never relabels the round."""
        assert _spawn_rounds([
            _skill_use_rec("code-review", "2026-05-19T10:00:00.000Z", tool_id="s1", branch="feat"),
            _skill_use_rec("skill-review", "2026-05-19T10:01:00.000Z", tool_id="s2", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["code-review"]

    def test_code_review_invoked_inside_ready_for_review_takes_over(self):
        """A spawn after ready-for-review invokes code-review reads code-review."""
        assert _spawn_rounds([
            _skill_use_rec("ready-for-review", "2026-05-19T10:00:00.000Z", tool_id="s1", branch="feat"),
            _skill_use_rec("code-review", "2026-05-19T10:01:00.000Z", tool_id="s2", branch="feat"),
            _spawn_rec("comment-discipline-reviewer", "2026-05-19T10:02:00.000Z"),
        ]) == ["code-review"]

    def test_record_holding_a_skill_block_and_a_spawn_block_reads_its_own_window(self):
        """The record before reads plan-review, so the line_no - 1 lookup picks each record's own window."""
        plan_review_rec = _asst(
            "claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
            content=[_skill_use("s1", "plan-review"), _agent_use("a1", "ciso-reviewer")],
        )
        both_blocks_rec = _asst(
            "claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:01:00.000Z",
            content=[_skill_use("s2", "code-review"), _agent_use("a2", "staff-sdet")],
        )
        assert _spawn_rounds([plan_review_rec, both_blocks_rec]) == ["plan-review", "code-review"]

    def test_sidechain_spawn_reads_none_even_inside_a_main_thread_window(self):
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z", sidechain=True),
        ]) == [None]

    def test_spawn_and_code_review_call_split_across_one_request_id_read_code_review(self):
        """Claude Code writes one record per content block; deduping first puts the spawn in the code-review window."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z", tool_id="a1", request_id="r1"),
            _skill_use_rec("code-review", "2026-05-19T10:01:01.000Z", tool_id="s2", request_id="r1", branch="feat"),
        ]) == ["code-review"]

    def test_merged_request_ids_before_a_spawn_keep_each_record_mapped_to_its_deduped_record(self):
        """The r2 merge shifts later deduped positions, and the r3 spawn is a dropped later member."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:01:00.000Z", request_id="r2",
                  content=[{"type": "text", "text": "a"}]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:01:01.000Z", request_id="r2",
                  content=[{"type": "text", "text": "b"}]),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z", tool_id="a1"),
            _skill_use_rec("code-review", "2026-05-19T10:03:00.000Z", tool_id="s2", request_id="r3", branch="feat"),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:03:01.000Z", tool_id="a2", request_id="r3"),
        ]) == ["plan-review", "code-review"]

    @staticmethod
    def _non_contiguous_request_id_records(*, later_member_output_tokens: int) -> list[dict]:
        """One requestId split around a code-review Skill record: plan-review Skill first, spawn last."""
        usage = {
            "input_tokens": 10, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0, "output_tokens": 5,
        }
        first_member = _skill_use_rec(
            "plan-review", "2026-05-19T10:00:00.000Z", tool_id="s1", request_id="r1", branch="feat",
        )
        interleaved = _skill_use_rec(
            "code-review", "2026-05-19T10:01:00.000Z", tool_id="s2", request_id="r2", branch="feat",
        )
        later_member = _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z", tool_id="a1", request_id="r1")
        first_member["message"]["usage"] = dict(usage)
        later_member["message"]["usage"] = {**usage, "output_tokens": later_member_output_tokens}
        return [first_member, interleaved, later_member]

    def test_merged_non_contiguous_request_id_spawn_reads_its_first_members_window(self):
        """Pins the residual that `_deduped_position_of_each_record` documents: a later
        member of a merged non-contiguous run reads its first member's window.

        This is a characterization pin, so a deliberate change to that mapping
        updates this test.
        """
        assert _spawn_rounds(self._non_contiguous_request_id_records(later_member_output_tokens=5)) == ["plan-review"]

    def test_rejected_non_contiguous_request_id_spawn_reads_its_own_records_window(self):
        """Differing usage leaves the run unmerged, so the later-member spawn reads the code-review window."""
        assert _spawn_rounds(self._non_contiguous_request_id_records(later_member_output_tokens=6)) == ["code-review"]

    def test_sidechain_record_before_a_main_thread_spawn_does_not_shift_the_lookup(self):
        """The thread guard and the records-to-main-thread index translation hold with a sidechain record between."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z", tool_id="a1", sidechain=True),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:02:00.000Z", tool_id="a2"),
        ]) == [None, "plan-review"]

    def test_leading_sidechain_record_does_not_shift_the_lookup(self):
        """A sidechain record at index 0 offsets records but not the main-thread list."""
        assert _spawn_rounds([
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True, ts="2026-05-19T09:59:00.000Z"),
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z"),
        ]) == ["plan-review"]

    def test_session_shaped_records_label_a_ready_for_review_spawn_and_a_spawn_opened_window(self):
        """Tool results and isMeta skill-body records between the dispatches keep each spawn on its own record's window.

        The second spawn shares a record with the review-orchestrator dispatch that opens its window, so a
        position mapping that lands one record early reads the ready-for-review window instead.
        """
        ts = "2026-05-19T10:{minute:02d}:00.000Z"
        assert _spawn_rounds([
            _skill_use_rec("ready-for-review", ts.format(minute=0), tool_id="s1", branch="feat"),
            _user_msg([_tool_result("s1", "Launching skill")], branch="feat", ts=ts.format(minute=1)),
            {**_user_msg("Base directory for this skill", branch="feat", ts=ts.format(minute=2)), "isMeta": True},
            _spawn_rec("staff-sdet", ts.format(minute=3), tool_id="a1"),
            _user_msg([_tool_result("a1", "No concerns")], branch="feat", ts=ts.format(minute=4)),
            _bash_rec("ls", ts.format(minute=5), tool_id="b1"),
            _user_msg([_tool_result("b1", "ok")], branch="feat", ts=ts.format(minute=6)),
            _user_msg([_tool_result("x1", "earlier output")], branch="feat", ts=ts.format(minute=7)),
            _user_msg([_tool_result("x2", "earlier output")], branch="feat", ts=ts.format(minute=8)),
            _asst(
                "claude-sonnet-4-6", branch="feat", ts=ts.format(minute=9),
                content=[_agent_use("o1", "review-orchestrator"), _agent_use("a2", "ciso-reviewer")],
            ),
            _user_msg([_tool_result("a2", "No concerns")], branch="feat", ts=ts.format(minute=10)),
        ]) == ["ready-for-review", "code-review"]

    @staticmethod
    def _write_plan_review_session_with_two_dispatches(proj, session_id: str) -> None:
        """Main thread: a plan-review Skill call, a ciso-reviewer spawn, and two general-purpose
        dispatches. The first dispatch holds a code-review Skill call and a staff-sdet spawn, the
        second a staff-backend-engineer spawn."""
        _write_jsonl(proj / f"{session_id}.jsonl", [
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:01:00.000Z", tool_id="a1"),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:02:00.000Z",
                  content=[_agent_use("g1", "general-purpose")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:03:00.000Z",
                  content=[_agent_use("g2", "general-purpose")]),
        ])
        _write_subagent_dispatch(
            proj, session_id, "agent-1", "g1",
            [_skill_use_rec("code-review", "2026-05-19T10:02:30.000Z", tool_id="s9", sidechain=True, branch="feat"),
             _spawn_rec("staff-sdet", "2026-05-19T10:02:40.000Z", tool_id="a9", sidechain=True)],
            agent_type="general-purpose",
        )
        _write_subagent_dispatch(
            proj, session_id, "agent-2", "g2",
            [_spawn_rec("staff-backend-engineer", "2026-05-19T10:03:30.000Z", tool_id="a8", sidechain=True)],
            agent_type="general-purpose",
        )

    def test_default_timeline_prints_round_on_each_reviewer_row(self, fake_projects, capsys):
        """Main-thread spawns print their window's skill; sidechain spawns print round=?."""
        self._write_plan_review_session_with_two_dispatches(fake_projects, "sess-rounds")

        review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out

        assert "ciso-reviewer  round=plan-review  (branch=feat" in out
        assert "staff-sdet  round=?  (branch=feat" in out
        assert "staff-backend-engineer  round=?  (branch=feat" in out

    def test_row_for_a_plan_review_spawn_carries_the_round_token_ready_for_review_names(self, fake_projects, capsys):
        """The [SILENT-SKIP] recovery step tells the human which round= token to look for in the rerun."""
        silent_skip_paragraph = next(
            (
                paragraph for paragraph in _READY_FOR_REVIEW_SKILL_MD.read_text().split("\n\n")
                if paragraph.startswith("**Spawn-dispatch `[SILENT-SKIP]`.**")
            ),
            None,
        )
        assert silent_skip_paragraph is not None, (
            "ready-for-review/SKILL.md no longer has a paragraph starting with '**Spawn-dispatch `[SILENT-SKIP]`.**'"
        )
        round_token_match = re.search(r"`(round=[^`]+)`", silent_skip_paragraph)
        assert round_token_match, "ready-for-review/SKILL.md's [SILENT-SKIP] paragraph no longer names a backticked round= token"
        round_token = round_token_match.group(1)
        _write_jsonl(fake_projects / "sess-recovery-token.jsonl", [
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:01:00.000Z"),
        ])

        review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out

        ciso_reviewer_row = next((line for line in out.splitlines() if "ciso-reviewer" in line), None)
        assert ciso_reviewer_row is not None, f"review-trace printed no ciso-reviewer row:\n{out}"
        assert round_token in ciso_reviewer_row

    def test_omit_flag_drops_plan_review_rows_and_prints_no_round_field(self, fake_projects, capsys):
        """The flag removes the round=plan-review row, keeps sidechain rows, and counts only what it prints."""
        self._write_plan_review_session_with_two_dispatches(fake_projects, "sess-omit")

        review_trace.cmd_review_trace(_review_trace_args(omit_plan_review_spawns=True))
        out = capsys.readouterr().out

        assert "ciso-reviewer" not in out
        assert "staff-sdet" in out
        assert "staff-backend-engineer" in out
        assert "reviewer-spawns=2" in out
        assert "round=" not in out

    def test_omit_flag_keeps_a_spawn_after_a_later_code_review_call(self, fake_projects, capsys):
        """Only the spawn made before the code-review call reads plan-review; the later one survives."""
        _write_jsonl(fake_projects / "sess-later.jsonl", [
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", tool_id="s1", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z", tool_id="a1"),
            _skill_use_rec("code-review", "2026-05-19T10:02:00.000Z", tool_id="s2", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:03:00.000Z", tool_id="a2"),
        ])

        review_trace.cmd_review_trace(_review_trace_args(omit_plan_review_spawns=True))
        out = capsys.readouterr().out

        reviewer_rows = [line for line in out.splitlines() if "reviewer     staff-sdet" in line]
        assert len(reviewer_rows) == 1
        assert "2026-05-19T10:03:00.000Z" in reviewer_rows[0]

    def test_label_ignores_since_branches_and_skill_filters(self, fake_projects, capsys):
        """The plan-review Skill call is filtered out of events by branch, date, and skill, yet still labels the spawn."""
        _write_jsonl(fake_projects / "sess-filters.jsonl", [
            _skill_use_rec("plan-review", "2026-05-18T10:00:00.000Z", branch="other"),
            _spawn_rec("staff-sdet", "2026-05-19T10:00:00.000Z", branch="feat"),
        ])

        review_trace.cmd_review_trace(
            _review_trace_args(since="2026-05-19", branches="feat", skill="code-review")
        )
        out = capsys.readouterr().out

        assert "staff-sdet  round=plan-review" in out

    def test_omit_flag_leaves_dropped_spawn_out_of_the_header_branches_and_models(self, fake_projects, capsys):
        """The drop happens before the header counts, so a dropped spawn's branch and model never print."""
        _write_jsonl(fake_projects / "sess-header.jsonl", [
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", tool_id="s1", branch="feat"),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:01:00.000Z", tool_id="a1",
                       branch="ghost", model="claude-opus-4-7"),
            _skill_use_rec("ready-for-review", "2026-05-19T10:02:00.000Z", tool_id="s2", branch="feat"),
            _spawn_rec("skill-fidelity-reviewer", "2026-05-19T10:03:00.000Z", tool_id="a2"),
        ])

        review_trace.cmd_review_trace(_review_trace_args(omit_plan_review_spawns=True))
        out = capsys.readouterr().out

        reviewer_rows = [line for line in out.splitlines() if "  reviewer     " in line]
        assert len(reviewer_rows) == 1
        assert "skill-fidelity-reviewer" in reviewer_rows[0]
        assert "branches=feat  models=sonnet" in out
        assert "ghost" not in out
        assert "opus" not in out

    def test_omit_flag_with_every_event_dropped_prints_no_session_header(self, fake_projects, capsys):
        """The skill filter removes the plan-review skill event and the omit flag removes the spawn, leaving no events."""
        _write_jsonl(fake_projects / "sess-empty.jsonl", [
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z"),
        ])

        review_trace.cmd_review_trace(_review_trace_args(skill="code-review"))
        out_without_flag = capsys.readouterr().out
        review_trace.cmd_review_trace(_review_trace_args(omit_plan_review_spawns=True, skill="code-review"))
        out = capsys.readouterr().out

        assert "### " in out_without_flag
        assert "###" not in out

    def test_spawn_after_the_deactivate_call_reads_none(self):
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:01:00.000Z", tool_id="a1"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:02:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:03:00.000Z", tool_id="a2"),
        ]) == ["plan-review", None]

    @pytest.mark.parametrize("spawn_first", [True, False], ids=["spawn-first", "call-first"])
    def test_spawn_sharing_the_deactivate_calls_response_reads_none(self, spawn_first):
        """Dedup merges the response into one record at the first member's position, and that record carries the call."""
        spawn = _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z", tool_id="a1", request_id="r1")
        call = _bash_rec(_deactivate_plan_review(), "2026-05-19T10:01:01.000Z", request_id="r1")
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            *([spawn, call] if spawn_first else [call, spawn]),
        ]) == [None]

    def test_each_plan_review_round_ends_at_its_own_deactivate_call(self):
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", tool_id="s1", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z", tool_id="a1"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:02:00.000Z", tool_id="b1"),
            _spawn_rec("staff-sdet", "2026-05-19T10:03:00.000Z", tool_id="a2"),
            _skill_use_rec("plan-review", "2026-05-19T10:04:00.000Z", tool_id="s2", branch="feat"),
            _spawn_rec("staff-sdet", "2026-05-19T10:05:00.000Z", tool_id="a3"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:06:00.000Z", tool_id="b2"),
            _spawn_rec("staff-sdet", "2026-05-19T10:07:00.000Z", tool_id="a4"),
        ]) == ["plan-review", None, "plan-review", None]

    def test_window_ends_at_the_first_deactivate_call_when_two_follow(self):
        """The scan stops at the first call, so a spawn between two calls is outside the window."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:01:00.000Z", tool_id="b1"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z", tool_id="a1"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:03:00.000Z", tool_id="b2"),
            _spawn_rec("staff-sdet", "2026-05-19T10:04:00.000Z", tool_id="a2"),
        ]) == [None, None]

    def test_deactivate_call_after_a_human_message_does_not_reopen_the_window(self):
        """The end only shortens a window, so a call past the human message labels nothing."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _user_msg("go on", branch="feat", ts="2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:03:00.000Z"),
        ]) == [None]

    def test_deactivate_call_after_a_nested_code_review_call_leaves_that_window_alone(self):
        """The code-review window that took over is not a plan-review window."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", tool_id="s1", branch="feat"),
            _skill_use_rec("code-review", "2026-05-19T10:01:00.000Z", tool_id="s2", branch="feat"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:02:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:03:00.000Z"),
        ]) == ["code-review"]

    def test_sidechain_records_neither_shift_the_end_nor_end_a_main_thread_window(self):
        """The end is located among main-thread records only, and a sidechain call never ends a window."""
        assert _spawn_rounds([
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True, ts="2026-05-19T09:59:00.000Z"),
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True, ts="2026-05-19T10:01:00.000Z"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:02:00.000Z", tool_id="b9", sidechain=True),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:03:00.000Z", tool_id="a1"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:04:00.000Z", tool_id="b1"),
            _spawn_rec("staff-sdet", "2026-05-19T10:05:00.000Z", tool_id="a2"),
        ]) == ["plan-review", None]

    def test_sidechain_deactivate_call_leaves_the_main_thread_window_open(self):
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:01:00.000Z", sidechain=True),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["plan-review"]

    def test_deactivate_call_in_the_openers_own_response_does_not_end_its_window(self):
        """The search starts after the opener, so a stale call run before invoking /plan-review keeps the window."""
        assert _spawn_rounds([
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:00:00.000Z", request_id="r1"),
            _skill_use_rec("plan-review", "2026-05-19T10:00:01.000Z", branch="feat", request_id="r1"),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z"),
        ]) == ["plan-review"]

    @pytest.mark.parametrize(
        "other_marker_call",
        [
            "~/.claude/scripts/marker.sh activate plan-review",
            "~/.claude/scripts/marker.sh deactivate ready-for-review",
            "echo deactivate plan-review",
            "~/.claude/scripts/marker.sh deactivate",
        ],
        ids=["activate-plan-review", "deactivate-other-skill", "echo-naming-the-args", "deactivate-without-skill"],
    )
    def test_other_marker_calls_leave_the_window_open(self, other_marker_call):
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(other_marker_call, "2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["plan-review"]

    @pytest.mark.parametrize(
        "command",
        [
            "{deactivate} && {write}",
            "{write} && {deactivate}",
            "{deactivate}&&{write}",
            "{deactivate}; git status",
            "git status; {deactivate}",
            "/srv/claude/.claude/scripts/marker.sh deactivate plan-review 2>/dev/null",
            "cat > notes.txt <<'EOF'\nIt's done\nEOF\n{deactivate}",
            "false || {deactivate}",
            "git status\n{deactivate}",
            "git log --grep=fix#12; {deactivate}",
        ],
        ids=[
            "call-then-write", "write-then-call", "unspaced-and", "call-then-attached-semicolon",
            "attached-semicolon-then-call", "absolute-path-with-redirect", "call-after-heredoc-with-apostrophe",
            "after-or-list", "second-line-after-plain-command", "mid-word-hash-before-call",
        ],
    )
    def test_command_running_the_deactivate_call_ends_the_window(self, command):
        """The call is read however a session joins it to another command, and a rejected line leaves the rest readable."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(
                _expand_recipe_placeholders(command),
                "2026-05-19T10:01:00.000Z",
            ),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == [None]

    @pytest.mark.parametrize(
        "command",
        [
            "{write}",
            'grep -n "marker.sh deactivate plan-review" claude-skills/skills/plan-review/SKILL.md',
            "python3 - <<'EOF'\nprint(\"{deactivate}\")\nEOF",
        ],
        ids=["write-only", "grep-naming-the-call", "heredoc-printing-the-call"],
    )
    def test_command_not_running_the_deactivate_call_leaves_the_window_open(self, command):
        """A command that only names the call ends nothing."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(
                _expand_recipe_placeholders(command),
                "2026-05-19T10:01:00.000Z",
            ),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["plan-review"]

    @pytest.mark.parametrize(
        "command",
        [
            "bash {deactivate}",
            "~/.claude/scripts/marker.sh deactivate \\\nplan-review",
        ],
        ids=["known-miss-bash-wrapper", "known-miss-backslash-continuation"],
    )
    def test_known_miss_spelling_of_the_deactivate_call_leaves_the_window_open(self, command):
        """Characterization pin: a shell runs these calls but the matcher leaves them unread; a matcher change updates them."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(
                _expand_recipe_placeholders(command),
                "2026-05-19T10:01:00.000Z",
            ),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["plan-review"]

    def test_known_miss_heredoc_body_line_starting_with_the_call_ends_the_window(self):
        """Characterization pin: with no heredoc awareness, a body line that is the call ends the window.

        A deliberate matcher change updates this case.
        """
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(f"cat <<'EOF'\n{_deactivate_plan_review()}\nEOF", "2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == [None]

    def test_known_miss_comment_text_with_a_segment_starting_with_the_call_ends_the_window(self):
        """Characterization pin: with no comment awareness, a segment in comment text that is the call ends the window.

        A deliberate matcher change updates this case.
        """
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(f"echo hi # note; {_deactivate_plan_review()}", "2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == [None]

    @pytest.mark.parametrize(
        ("padding_chars", "expected_rounds"),
        [(0, [None]), (1, ["plan-review"])],
        ids=["line-at-the-cap-is-read", "line-one-over-the-cap-is-not-read"],
    )
    def test_call_line_ends_the_window_only_when_within_the_length_cap(self, padding_chars, expected_rounds):
        padding = "a" * (review_trace._MAX_LEXED_LINE_CHARS - len(_deactivate_plan_review()) - 1 + padding_chars)
        command = f"{_deactivate_plan_review()} {padding}"
        assert len(command) == review_trace._MAX_LEXED_LINE_CHARS + padding_chars
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(command, "2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == expected_rounds

    def test_call_on_a_line_after_an_over_cap_line_ends_the_window(self):
        """The cap skips only the over-cap line, measured per line, so a later line is still read."""
        over_cap_line = "echo " + "a" * review_trace._MAX_LEXED_LINE_CHARS
        assert len(over_cap_line) > review_trace._MAX_LEXED_LINE_CHARS
        assert len(_deactivate_plan_review()) <= review_trace._MAX_LEXED_LINE_CHARS
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(f"{over_cap_line}\n{_deactivate_plan_review()}", "2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == [None]

    @pytest.fixture()
    def lexed_lines(self, monkeypatch):
        """Every line handed to shlex.shlex, in call order."""
        lines: list[str] = []
        real_shlex = review_trace.shlex.shlex

        def recording_shlex(line, *args, **kwargs):
            lines.append(line)
            return real_shlex(line, *args, **kwargs)

        monkeypatch.setattr(review_trace.shlex, "shlex", recording_shlex)
        return lines

    def test_line_past_the_length_cap_is_never_lexed(self, lexed_lines):
        over_cap_call_line = f"{_deactivate_plan_review()} {'a' * review_trace._MAX_LEXED_LINE_CHARS}"
        assert review_trace._runs_plan_review_deactivate(_bash_rec(over_cap_call_line, "2026-05-19T10:01:00.000Z")) is False
        assert lexed_lines == []

    def test_line_within_the_length_cap_is_lexed(self, lexed_lines):
        """Control for the never-lexed case: the recording wrapper sees a line within the cap."""
        assert review_trace._runs_plan_review_deactivate(_bash_rec(_deactivate_plan_review(), "2026-05-19T10:01:00.000Z"))
        assert lexed_lines == [_deactivate_plan_review()]

    def test_non_bash_tool_use_carrying_the_call_leaves_the_window_open(self):
        """Only a Bash tool_use runs the call, whatever another tool's input.command holds."""
        non_bash_block = {
            "type": "tool_use", "id": "w1", "name": "Write", "input": {"command": _deactivate_plan_review()},
        }
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:01:00.000Z", content=[non_bash_block]),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["plan-review"]

    @pytest.mark.parametrize(
        "build_text_record",
        [
            lambda call: {**_user_msg(call, branch="feat", ts="2026-05-19T10:01:00.000Z"), "isMeta": True},
            lambda call: _user_msg([_tool_result("b1", call)], branch="feat", ts="2026-05-19T10:01:00.000Z"),
        ],
        ids=["meta-user-record", "tool-result"],
    )
    def test_user_record_carrying_the_call_as_text_leaves_the_window_open(self, build_text_record):
        """Only an assistant record's Bash tool_use runs the call."""
        assert _spawn_rounds([
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            build_text_record(_deactivate_plan_review()),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]) == ["plan-review"]

    @pytest.mark.parametrize(
        "build_bash_input",
        [
            lambda get_call: "not a dict",
            lambda get_call: {},
            lambda get_call: {"command": None},
            lambda get_call: {"command": [get_call()]},
        ],
        ids=["string-input", "absent-command", "null-command", "list-command"],
    )
    def test_malformed_bash_block_runs_no_deactivate_call(self, build_bash_input):
        """A Bash block with a non-dict input or a non-string command reads as no call, without raising.

        The string-input param is a predicate-level pin: _review_trace_session_events evaluates
        `(block.get("input") or {}).get("command", "")` first and raises on a string input, so that case never
        reaches the predicate through cmd_review_trace. The other params are reachable.
        """
        bash_block = {"type": "tool_use", "id": "b1", "name": "Bash", "input": build_bash_input(_deactivate_plan_review)}
        rec = _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:01:00.000Z", content=[bash_block])
        assert review_trace._runs_plan_review_deactivate(rec) is False

    def test_shared_round_windows_still_run_past_the_deactivate_call(self):
        """The end lives in review_trace, so detect_round_windows keeps its window for its four other consumers."""
        records = [
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:01:00.000Z"),
            _spawn_rec("staff-sdet", "2026-05-19T10:02:00.000Z"),
        ]
        assert review_rounds.detect_round_windows(records) == [(0, 3, "plan-review")]

    def test_omit_flag_keeps_a_spawn_made_after_the_deactivate_call(self, fake_projects, capsys):
        """A session that keeps dispatching reviewers after /plan-review finishes keeps those rows in the fidelity view."""
        _write_jsonl(fake_projects / "sess-after-deactivate.jsonl", [
            _skill_use_rec("plan-review", "2026-05-19T10:00:00.000Z", branch="feat"),
            _spawn_rec("ciso-reviewer", "2026-05-19T10:01:00.000Z", tool_id="a1"),
            _bash_rec(_deactivate_plan_review(), "2026-05-19T10:02:00.000Z"),
            _spawn_rec("comment-discipline-reviewer", "2026-05-19T10:03:00.000Z", tool_id="a2"),
        ])

        review_trace.cmd_review_trace(_review_trace_args(omit_plan_review_spawns=True))
        out = capsys.readouterr().out

        reviewer_rows = [line for line in out.splitlines() if "  reviewer     " in line]
        assert len(reviewer_rows) == 1
        assert "comment-discipline-reviewer" in reviewer_rows[0]

    @staticmethod
    def _drifted_two_record_run() -> list[dict]:
        """One requestId run whose two records disagree on input_tokens, the usage-drift trigger."""
        first_record = _skill_use_rec("code-review", "2026-05-19T10:00:00.000Z", branch="feat", request_id="r1")
        second_record = _asst(
            "claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:01.000Z", request_id="r1",
            content=[{"type": "text", "text": "done"}],
        )
        first_record["message"]["usage"] = {"input_tokens": 10}
        second_record["message"]["usage"] = {"input_tokens": 11}
        return [first_record, second_record]

    def test_session_without_a_reviewer_spawn_never_dedups_a_drifted_run(self, fake_projects, capsys):
        """The spawn guard keeps the dedup, and its stderr drift notice, off sessions that never dispatched a reviewer."""
        _write_jsonl(fake_projects / "sess-drift.jsonl", self._drifted_two_record_run())

        review_trace.cmd_review_trace(_review_trace_args())
        captured = capsys.readouterr()

        assert "skills=1" in captured.out
        assert captured.err == ""
        assert pricing._usage_drift_warned is False

    def test_session_with_a_reviewer_spawn_dedups_a_drifted_run_and_warns(self, fake_projects, capsys):
        """Pairs the no-spawn test: one reviewer spawn is what lets the same drifted run reach the dedup."""
        _write_jsonl(fake_projects / "sess-drift-spawn.jsonl", [
            *self._drifted_two_record_run(),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z"),
        ])

        review_trace.cmd_review_trace(_review_trace_args())
        captured = capsys.readouterr()

        assert "reviewer-spawns=1" in captured.out
        assert "WARNING: requestId 'r1' has non-identical input/cache usage" in captured.err
        assert pricing._usage_drift_warned is True

    def test_deny_summary_never_dedups_a_spawn_bearing_drifted_run_with_or_without_the_omit_flag(self, fake_projects, capsys):
        """--deny-summary returns before the spawn labeler and dedup run, so a drifted run
        that also holds a reviewer spawn prints no drift warning."""
        _write_jsonl(fake_projects / "sess-deny-summary-spawn.jsonl", [
            *self._drifted_two_record_run(),
            _spawn_rec("staff-sdet", "2026-05-19T10:01:00.000Z"),
        ])

        review_trace.cmd_review_trace(_review_trace_args(deny_summary=True))
        without_flag = capsys.readouterr()
        review_trace.cmd_review_trace(_review_trace_args(deny_summary=True, omit_plan_review_spawns=True))
        with_flag = capsys.readouterr()

        assert "No denials found in scope." in without_flag.out
        assert "No denials found in scope." in with_flag.out
        assert without_flag.err == ""
        assert with_flag.err == ""
        assert pricing._usage_drift_warned is False
