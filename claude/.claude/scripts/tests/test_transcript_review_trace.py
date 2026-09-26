"""Tests for transcript_analysis/review_trace.py (cmd_review_trace, --deny-summary)."""
import argparse
import importlib.util
import sys
from pathlib import Path

import pytest
from helpers import CONSULT_CLASSIFICATION_TABLE

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _hook_deny,
    _hook_deny_current,
    _review_trace_args,
    _skill_use,
    _table_cols,
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


def _since_until_epochs(since: str | None, until: str | None) -> tuple[float | None, float | None]:
    """Mirror cmd_review_trace's own --since/--until date-string -> epoch-second
    boundary conversion, so a test calling _review_trace_session_events directly
    passes boundaries in the same form the CLI itself would compute."""
    return _mod._parse_absolute_window_args(argparse.Namespace(since=since, until=until), "review-trace")


# ---------------------------------------------------------------------------
# review-trace
# ---------------------------------------------------------------------------


class TestReviewTrace:
    def test_skill_invocation_appears_in_output(self):
        """Main-thread Skill call for a review skill produces a 'skill' event."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"
        assert events[0]["skill"] == "code-review"

    def test_skill_invocation_plugin_qualified_spelling_matches_review_trace_skills(self):
        """A plugin-qualified spelling (skill-management:skill-review) matches
        REVIEW_TRACE_SKILLS membership. This exercises _round_skill_name's
        plugin:/dir: strip branch."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "skill-management:skill-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_skill_invocation_worktree_qualified_spelling_matches_review_trace_skills(self):
        """A worktree-path-qualified spelling matches REVIEW_TRACE_SKILLS
        membership. This test doesn't isolate the leading "/"-strip branch:
        the trailing ":"-strip alone already reduces every real
        fixture shape to the bare name."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", ".claude/worktrees/some-branch/claude:skill-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_skill_filter_matches_qualified_spelling(self):
        """--skill's bare-name filter still matches a raw qualified spelling
        (claude:plan-it), since the filter comparison normalizes the same
        way the REVIEW_TRACE_SKILLS membership check does."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "claude:plan-it")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None, skill_filter="plan-it",
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_skill_filter_excludes_non_matching_qualified_spelling(self):
        """--skill's filter drops a skill invocation whose normalized name
        does not equal the filter, even when another invocation in the same
        session matches."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "claude:plan-it")]),
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:01:00.000Z",
                  content=[_skill_use("s2", "claude:code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None, skill_filter="plan-it",
        )
        assert len(events) == 1
        assert events[0]["skill"] == "claude:plan-it"

    def test_skill_event_field_keeps_display_normalization_not_bare_form(self):
        """The emitted skill event field keeps _normalize_skill_name's lighter
        directory-only strip, including any plugin:/dir: prefix.
        _round_skill_name's fully-bare strip is used only for the
        REVIEW_TRACE_SKILLS membership test, not for this field."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", ".claude/worktrees/some-branch/claude:skill-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["skill"] == "claude:skill-review"

    def test_slash_invocation_appears_in_output(self):
        """A /slash-invoked review skill (`<command-name>` tag on a user
        record, no Skill tool_use block) produces a 'skill' event. This
        matches review-round-cost's own detection of the same shape."""
        records = [
            _user_msg("<command-name>/code-review</command-name>", branch="feat",
                       ts="2026-05-19T10:00:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"
        assert events[0]["skill"] == "code-review"

    def test_slash_invocation_plugin_qualified_spelling_matches_review_trace_skills(self):
        """A plugin-qualified slash spelling (skill-management:skill-review)
        matches REVIEW_TRACE_SKILLS membership via _round_skill_name. Mirrors
        the Skill-tool_use plugin-qualified test above, for the slash shape."""
        records = [
            _user_msg("<command-name>/skill-management:skill-review</command-name>",
                       branch="feat", ts="2026-05-19T10:00:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_slash_skill_filter_matches_qualified_spelling(self):
        """--skill's bare-name filter matches a raw qualified slash spelling
        (claude:plan-it). Mirrors test_skill_filter_matches_qualified_spelling,
        for the slash shape."""
        records = [
            _user_msg("<command-name>/claude:plan-it</command-name>", branch="feat",
                       ts="2026-05-19T10:00:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None, skill_filter="plan-it",
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_slash_skill_filter_excludes_non_matching_qualified_spelling(self):
        """--skill's filter drops a slash-invoked skill whose normalized name
        does not equal the filter, even when another slash invocation in the
        same session matches."""
        records = [
            _user_msg("<command-name>/claude:plan-it</command-name>", branch="feat",
                       ts="2026-05-19T10:00:00.000Z"),
            _user_msg("<command-name>/claude:code-review</command-name>", branch="feat",
                       ts="2026-05-19T10:01:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None, skill_filter="plan-it",
        )
        assert len(events) == 1
        assert events[0]["skill"] == "claude:plan-it"

    def test_slash_skill_event_field_keeps_display_normalization_not_bare_form(self):
        """The emitted skill event field for a slash invocation keeps
        _normalize_skill_name's lighter directory-only strip, including any
        plugin:/dir: prefix. This mirrors the Skill-tool_use version of this
        test, for the slash shape."""
        records = [
            _user_msg("<command-name>/.claude/worktrees/some-branch/claude:skill-review</command-name>",
                       branch="feat", ts="2026-05-19T10:00:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["skill"] == "claude:skill-review"

    def test_slash_invocation_of_non_review_trace_skill_produces_no_event(self):
        """A /slash-command tag for a skill outside REVIEW_TRACE_SKILLS (e.g.
        /handoff) produces no skill event. The user-record slash-detection
        branch must not over-match every slash invocation."""
        records = [
            _user_msg("<command-name>/handoff</command-name>", branch="feat",
                       ts="2026-05-19T10:00:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_slash_invocation_detected_with_list_form_user_content(self):
        """A /slash-invoked review skill is still detected when message.content
        is a list of blocks rather than a bare string. Mirrors
        test_slash_detection_with_list_form_user_content's coverage of the
        same _content_text fallback, for the review-trace path."""
        records = [
            _user_msg(
                [{"type": "text", "text": "<command-name>/code-review</command-name>"}],
                branch="feat", ts="2026-05-19T10:00:00.000Z",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_skill_tool_use_and_slash_invocation_both_produce_events(self):
        """A Skill tool_use record and a `<command-name>` slash tag for two
        different REVIEW_TRACE_SKILLS members both produce 'skill' events.
        Each event is attributed to its own record's branch. The slash
        event's model is carried forward from the last assistant record."""
        records = [
            _asst("claude-opus-4-7", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _user_msg("<command-name>/plan-review</command-name>", branch="fix",
                       ts="2026-05-19T10:01:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 2
        skill_event = next(e for e in events if e["skill"] == "code-review")
        slash_event = next(e for e in events if e["skill"] == "plan-review")
        assert skill_event["kind"] == "skill"
        assert skill_event["branch"] == "feat"
        assert skill_event["model"] == "opus"
        assert slash_event["kind"] == "skill"
        assert slash_event["branch"] == "fix"
        assert slash_event["model"] == "opus"

    def test_multiple_slash_tags_in_one_user_record_produce_two_events(self):
        """Two `<command-name>` tags for two different REVIEW_TRACE_SKILLS
        members in a single user record both produce 'skill' events. Mirrors
        cmd_skill_invocation's own test_multiple_slash_tags_in_one_user_record,
        for the review-trace path."""
        records = [
            _user_msg(
                "<command-name>/plan-it</command-name>\n<command-name>/code-review</command-name>",
                branch="main", ts="2026-05-19T10:00:00.000Z",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 2
        assert {e["skill"] for e in events} == {"plan-it", "code-review"}

    def test_whitespace_only_command_name_tag_produces_no_event(self):
        """A `<command-name>` tag whose captured name is whitespace-only
        normalizes to a string that isn't a REVIEW_TRACE_SKILLS member, so it
        produces no event. Pins the current safe-by-construction behavior."""
        records = [
            _user_msg("<command-name>/ </command-name>", branch="main",
                       ts="2026-05-19T10:00:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_denial_dict_blockingError_parsed(self):
        """hook_blocking_error with blockingError as a dict produces a denial event."""
        records = [_hook_deny("require-code-review", stringified=False)]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert events[0]["hook_name"] == "require-code-review"

    def test_denial_stringified_blockingError_parsed_identically(self):
        """hook_blocking_error with blockingError as a JSON string produces
        an identical denial event to the dict form."""
        dict_events, _tuc1, _pr1 = _mod.review_trace._review_trace_session_events(
            [_hook_deny("require-code-review", stringified=False)], None, None, None,
        )
        str_events, _tuc2, _pr2 = _mod.review_trace._review_trace_session_events(
            [_hook_deny("require-code-review", stringified=True)], None, None, None,
        )
        assert len(dict_events) == 1
        assert len(str_events) == 1
        assert dict_events[0]["hook_name"] == "require-code-review"
        assert str_events[0]["hook_name"] == "require-code-review"
        # The human-readable message text must appear in both forms, not a dict repr.
        assert "blocked the operation" in dict_events[0]["message"]
        assert "blocked the operation" in str_events[0]["message"]
        assert "{'blockingError'" not in dict_events[0]["message"]
        assert "{'blockingError'" not in str_events[0]["message"]

    def test_hook_non_blocking_error_produces_zero_denial_events(self):
        """hook_non_blocking_error records must NOT produce a denial event."""
        non_blocking_rec = {
            "type": "attachment",
            "attachment": {
                "type": "hook_non_blocking_error",
                "hookName": "some-hook",
                "toolUseID": "toolu_abc",
                "blockingError": {"message": "non-fatal"},
            },
        }
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [non_blocking_rec], None, None, None,
        )
        assert events == []

    def test_reviewer_spawn_detected_general_purpose_excluded(self):
        """staff-backend-engineer spawn produces a reviewer-spawn event; general-purpose does not."""
        records = [
            _asst("claude-opus-4-7", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _agent_use("a1", "staff-backend-engineer"),
                      _agent_use("a2", "general-purpose"),
                  ]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        reviewer_events = [e for e in events if e["kind"] == "reviewer-spawn"]
        assert len(reviewer_events) == 1
        assert reviewer_events[0]["subagent_type"] == "staff-backend-engineer"

    def test_reviewer_spawn_detected_comment_discipline_and_skill_fidelity(self):
        """comment-discipline-reviewer and skill-fidelity-reviewer are exact-name
        reviewer-spawn matches, not just the staff- prefix or ciso-reviewer."""
        records = [
            _asst("claude-opus-4-7", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _agent_use("a1", "comment-discipline-reviewer"),
                      _agent_use("a2", "skill-fidelity-reviewer"),
                  ]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        reviewer_types = {e["subagent_type"] for e in events if e["kind"] == "reviewer-spawn"}
        assert reviewer_types == {"comment-discipline-reviewer", "skill-fidelity-reviewer"}

    # -----------------------------------------------------------------------
    # architect-consult classification
    # -----------------------------------------------------------------------

    def test_architect_consult_mode_consult_first_line_emits_event(self):
        """A plan-architect dispatch whose prompt's first line is the literal
        MODE=consult emits an architect-consult event."""
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nSome question.")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert sum(1 for e in events if e["kind"] == "architect-consult") == 1

    def test_architect_mode_plan_sections_first_line_emits_no_consult_event(self):
        """A plan-architect dispatch whose prompt's first line is the literal
        MODE=plan-sections emits no architect-consult event."""
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=plan-sections\n## Context")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert not any(e["kind"] == "architect-consult" for e in events)

    def test_architect_consult_empty_prompt_emits_event_fail_safe(self):
        """An empty-string prompt is not the MODE=plan-sections literal, so
        the fail-safe direction classifies it as a consult."""
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert sum(1 for e in events if e["kind"] == "architect-consult") == 1

    def test_architect_consult_missing_prompt_key_emits_event_fail_safe(self):
        """A block whose `input` dict lacks the `prompt` key entirely also
        classifies as a consult -- `_agent_use` always populates `prompt` and
        can't build this shape, so this constructs the raw dict literal."""
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[{
                      "type": "tool_use", "id": "a1", "name": "Agent",
                      "input": {"subagent_type": "plan-architect", "description": "x"},
                  }]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert sum(1 for e in events if e["kind"] == "architect-consult") == 1

    @pytest.mark.parametrize(
        "first_line,expect_consult",
        [pytest.param(fl, ec, id=tid) for fl, ec, tid in CONSULT_CLASSIFICATION_TABLE],
    )
    def test_classification_matches_table(self, first_line, expect_consult):
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt=first_line)]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        emitted_consult = any(e["kind"] == "architect-consult" for e in events)
        assert emitted_consult is expect_consult

    def test_sidechain_architect_consult_now_detected(self):
        """A plan-architect consult dispatch inside a sidechain record
        produces an architect-consult event, tagged thread=sidechain."""
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  sidechain=True,
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nquestion")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "architect-consult"
        assert events[0]["thread"] == "sidechain"

    def test_non_plan_architect_dispatch_never_misclassified_as_consult(self):
        """A staff-backend-engineer dispatch with no MODE=plan-sections first
        line still emits zero architect-consult events and exactly one
        reviewer-spawn -- guards against the `stype ==` gate being dropped,
        which would reclassify every ordinary reviewer dispatch as a consult."""
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer", prompt="Review this diff.")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert sum(1 for e in events if e["kind"] == "architect-consult") == 0
        assert sum(1 for e in events if e["kind"] == "reviewer-spawn") == 1

    def test_architect_consult_event_attributed_to_own_branch_not_session_first_branch(self):
        """Mirrors test_events_attributed_to_own_branch_not_session_first_branch
        for the architect-consult kind: a session opening on one branch, then
        moving to another before the consult dispatch, attributes the event
        to its own (later) branch and model."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T09:00:00.000Z"),
            _asst("claude-opus-4-7", branch="feature-x", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nquestion")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        consult_events = [e for e in events if e["kind"] == "architect-consult"]
        assert len(consult_events) == 1
        assert consult_events[0]["branch"] == "feature-x"
        assert consult_events[0]["model"] == "opus"

    def test_architect_consult_event_key_set_carries_no_prompt_derived_field(self):
        """The blindness property pinned at the layer it is defined: the
        event dict itself carries only the classification result plus the
        metadata every event kind carries, never a prompt-derived field.
        thread is record-structural -- derived from the record's own
        isSidechain flag, never from the prompt -- so its presence here
        does not weaken the pin."""
        records = [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect",
                                       prompt="MODE=consult\nSecret rationale nobody should see.")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        consult_events = [e for e in events if e["kind"] == "architect-consult"]
        assert len(consult_events) == 1
        assert consult_events[0].keys() == {"kind", "ts", "line_no", "branch", "model", "thread"}

    def test_architect_consult_prompt_body_never_reaches_review_trace_output(self, fake_projects, capsys):
        """The prompt string is never stored on the event dict, so it can
        never leak into printed output -- a distinctive rationale substring
        embedded in the prompt must not appear anywhere in review-trace's
        stdout."""
        secret_rationale = "UNIQUE_RATIONALE_MARKER_892"
        _write_jsonl(fake_projects / "consult-session.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect",
                                       prompt=f"MODE=consult\n{secret_rationale}")]),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert secret_rationale not in out
        assert "consult-session.jsonl" in out

    def test_architect_consults_header_count_renders(self, fake_projects, capsys):
        """The per-session header line's architect-consults=<N> count reflects
        the number of architect-consult events emitted for that session."""
        _write_jsonl(fake_projects / "consult-session.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nquestion")]),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "architect-consults=1" in out

    def test_session_holding_only_consult_event_emits_session_block(self, fake_projects, capsys):
        """A session whose only review-relevant event is an architect-consult
        dispatch still emits a session block -- consult-only sessions aren't
        silently dropped like a session with zero events."""
        _write_jsonl(fake_projects / "consult-only.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nquestion")]),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "consult-only.jsonl" in out
        assert "consult      " in out

    def test_sidechain_skill_invocation_now_detected(self):
        """A code-review Skill call inside a sidechain record produces a
        skill event, tagged thread=sidechain."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  sidechain=True,
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"
        assert events[0]["thread"] == "sidechain"

    # -----------------------------------------------------------------------
    # GH-896: merge/sort of main and subagent records
    # -----------------------------------------------------------------------

    def test_review_trace_end_to_end_detects_consult_dispatched_from_subagent_file(
        self, fake_projects, capsys
    ):
        """End to end through cmd_review_trace's own scope resolution (not
        just the direct _review_trace_session_events call the other tests in
        this class use): a plan-architect consult dispatched from inside a
        subagent's own <session_id>/subagents/*.jsonl file is detected now
        that include_subagents=True is the default, and its timeline row
        prints thread=sidechain with its line number suppressed as n/a,
        since a sidechain event's line_no indexes no real file."""
        session_id = "sess-subagent-consult"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a2", "plan-architect", prompt="MODE=consult\nquestion")]),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "architect-consults=1" in out
        assert "thread=sidechain" in out
        assert "line   n/a" in out

    def test_merged_stream_interleaves_main_and_sidechain_chronologically(self):
        """Main and subagent records are merged into one chronological
        stream before detection, not left in main-then-subagent
        concatenation order: a subagent record timestamped between two
        main-thread records lands between their events in the emitted
        timeline."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:10:00.000Z",
                  content=[_skill_use("s2", "plan-review")]),
            _asst("claude-opus-4-7", branch="feat", sidechain=True, ts="2026-05-19T10:05:00.000Z",
                  content=[_skill_use("s3", "ready-for-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["skill"] for e in events] == ["code-review", "ready-for-review", "plan-review"]
        assert [e["thread"] for e in events] == ["main", "sidechain", "main"]

    def test_line_no_still_resolves_to_main_file_line_after_sort(self):
        """A thread=main event's line_no still equals its position in the
        main transcript after the merge sort reorders it relative to a
        later-positioned but earlier-timestamped subagent record."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:10:00.000Z",
                  content=[_skill_use("s1", "code-review")]),  # main file line 1, later ts
            _asst("claude-opus-4-7", branch="feat", sidechain=True, ts="2026-05-19T09:00:00.000Z",
                  content=[_skill_use("s2", "plan-review")]),  # subagent record, earlier ts
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events[0]["thread"] == "sidechain"
        assert events[1]["thread"] == "main"
        assert events[1]["line_no"] == 1

    def test_interior_unparseable_timestamp_forward_fills_and_stays_adjacent(self):
        """An interior record whose timestamp fails to parse forward-fills
        the immediately preceding record's effective_ts, so it sorts
        adjacent to that neighbour rather than raising TypeError or
        drifting elsewhere in the stream."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="not-a-timestamp",
                  content=[_skill_use("s2", "plan-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:10:00.000Z",
                  content=[_skill_use("s3", "ready-for-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["skill"] for e in events] == ["code-review", "plan-review", "ready-for-review"]

    def test_leading_unparseable_timestamp_sorts_to_head_not_typeerror(self):
        """A leading record whose timestamp fails to parse has no preceding
        record to forward-fill from -- it falls back to float("-inf") and
        sorts to the head of the stream instead of raising TypeError
        against its neighbour's float key, a distinct case from an interior
        unparseable timestamp above."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="not-a-timestamp",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s2", "plan-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["skill"] for e in events] == ["code-review", "plan-review"]

    def test_same_timestamp_main_sidechain_tie_break_main_first(self):
        """Two records sharing one timestamp, one main and one sidechain,
        place the main event first -- the tie-break is a stated choice
        (cause before effect), not inherited from the records' own list
        position, which here has the sidechain record listed ahead of the
        main one."""
        records = [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T10:00:00.000Z", content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z", content=[_skill_use("s2", "plan-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["thread"] for e in events] == ["main", "sidechain"]
        assert [e["skill"] for e in events] == ["plan-review", "code-review"]

    def test_sidechain_event_inherits_main_threads_branch_not_its_own(self):
        """A sidechain event's branch is the carried-forward value from the
        preceding main-thread record, never the sidechain record's own
        gitBranch -- the isSidechain guard on the carry-forward trackers
        (docs/design-decisions.md §58) is load-bearing specifically because
        lifting it would attribute a sidechain event to its own record's
        branch instead of the dispatching main thread's. Every other
        sidechain fixture in this file sets the sidechain record's own
        branch equal to the preceding main record's, so only a fixture with
        a genuinely differing sidechain gitBranch can catch a regression
        that reads it directly."""
        records = [
            _asst("claude-sonnet-4-6", branch="A", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-4-7", branch="B", sidechain=True, ts="2026-05-19T10:05:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["branch"] == "A"

    def test_denial_dedup_by_tool_use_id_survives_sort_reorder(self):
        """A denial recorded as both a sidechain attachment record and its
        earlier-timestamped main-thread current-format twin still collapses
        to one event once the merge sort reorders them ahead of their
        original list position -- dedup runs against the sorted stream, not
        the pre-sort one."""
        attach = _hook_deny("worktree", ts="2026-05-19T10:05:00.000Z")  # toolUseID == toolu_worktree
        attach["isSidechain"] = True
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree", ts="2026-05-19T10:00:00.000Z",
        )
        # attach (sidechain, later ts) is listed first; twin (main, earlier
        # ts) is listed second -- pre-sort order is the reverse of
        # chronological order, so this exercises the merge sort as well as
        # dedup.
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [attach, twin], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        # hook_name=="" (rather than "worktree") proves the sort actually ran
        # twin (main, earlier ts) ahead of attach before dedup, not merely
        # that dedup collapsed whichever one happened to be processed first.
        assert events[0]["hook_name"] == ""
        assert events[0]["thread"] == "main"

    def test_forward_fill_does_not_cross_subagent_file_boundary(self, fake_projects, capsys):
        """An unparseable-timestamp record that is the first record of its
        own subagent file resets to the earliest sort position instead of
        forward-filling from a different subagent file's last record --
        filename-sorted subagent files carry no chronological relationship
        to each other, so a global (rather than per-source-group)
        forward-fill would wrongly inherit agent-1's late timestamp into
        agent-2's own first record. Exercised end to end through
        cmd_review_trace, since the per-file group boundary this needs only
        exists once records are read via real subagent files, not via a
        hand-built flat records list."""
        session_id = "sess-cross-file-forward-fill"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T15:00:00.000Z",
                  content=[_skill_use("s-main", "skill-review")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T20:00:00.000Z",
                  content=[_skill_use("s-a1", "code-review")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-2", [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="not-a-timestamp",
                  content=[_skill_use("s-a2-bad", "plan-review")]),
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s-a2-good", "ready-for-review")]),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        # agent-2's own unparseable-ts record (plan-review) must sort ahead
        # of agent-2's own later, real-ts record (ready-for-review), and
        # both ahead of main (skill-review, 15:00) and agent-1 (code-review,
        # 20:00, the last record of the *other*, filename-earlier-sorted
        # subagent file) -- not fall back to agent-1's 20:00 timestamp,
        # which would instead sort plan-review last.
        assert (
            out.index("plan-review") < out.index("ready-for-review")
            < out.index("skill-review") < out.index("code-review")
        )

    def test_since_boundary_inclusive_record_included(self):
        """A record whose timestamp matches exactly --since is included."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T00:00:00Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs("2026-05-19", None)
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_until_boundary_inclusive_record_included(self):
        """A record whose timestamp matches exactly --until is included."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T23:59:59Z",
                  content=[_skill_use("s1", "plan-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs(None, "2026-05-19")
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["skill"] == "plan-review"

    def test_record_with_no_timestamp_excluded_no_crash(self, fake_projects, capsys):
        """A record with no parseable timestamp is excluded when a date filter is active; no crash."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  content=[_skill_use("s1", "code-review")]),  # no ts field
        ])
        # No exception must be raised; the missing-timestamp record is silently skipped.
        _mod.review_trace.cmd_review_trace(_review_trace_args(since="2026-05-01"))
        # No crash; output may be empty (no matching events survive the date filter).
        capsys.readouterr()  # consume; success if no exception raised above

    def test_deny_only_restricts_to_denial_sessions(self):
        """--deny-only retains sessions with a denial event; a session with a
        reviewer spawn but no denial does not qualify."""
        session_a, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [_hook_deny("require-code-review")], None, None, None,
        )
        session_b, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [
                _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                      content=[_agent_use("a1", "staff-backend-engineer")]),
            ],
            None, None, None,
        )
        assert any(e["kind"] == "denial" for e in session_a)
        assert not any(e["kind"] == "denial" for e in session_b)

    def test_until_subsecond_record_included(self):
        """A record at T23:59:59.500Z on the --until date IS included (sub-second gap fix)."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-10T23:59:59.500Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs(None, "2026-05-10")
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_denial_blockingError_key_used_for_display_message(self):
        """Denial event's message carries the nested blockingError string, not a dict repr."""
        human_message = "Hook 'require-code-review' blocked the operation"
        error_dict = {"blockingError": human_message, "command": "git commit -m x"}
        denial_rec = {
            "type": "attachment",
            "attachment": {
                "type": "hook_blocking_error",
                "hookName": "require-code-review",
                "toolUseID": "toolu_abc",
                "blockingError": error_dict,
            },
        }
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [denial_rec], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["message"] == human_message
        assert "{'blockingError'" not in events[0]["message"]

    def test_no_match_session_produces_no_output(self):
        """A session with only non-review tool_use (Bash) produces no events."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git status")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_current_format_denial_detected(self):
        """A current-format is_error tool_result with a hook-denial signature produces a denial event."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert "code-review gate" in events[0]["message"]

    def test_current_format_ordinary_error_is_not_a_denial(self):
        """An is_error tool_result without a hook-denial signature produces no events."""
        records = [_hook_deny_current("npm ERR! command failed with exit code 1")]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_current_format_denial_text_without_is_error_ignored(self):
        """A tool_result with denial-shaped text but no is_error flag produces no events."""
        rec = _hook_deny_current("Blocked by worktree-enforcement hook: not allowed.")
        rec["message"]["content"][0]["is_error"] = False
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [rec], None, None, None,
        )
        assert events == []

    def test_legacy_and_current_shapes_deduped_by_tool_use_id(self):
        """A denial recorded as both an attachment and an is_error tool_result for one
        tool_use_id collapses to one event. Dedup keeps whichever record appears first
        in the transcript; here the attachment is written ahead of its twin, so the
        retained event carries the hook name the attachment record provides."""
        attach = _hook_deny("worktree")  # toolUseID == "toolu_worktree", hookName "worktree"
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree",
        )
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [attach, twin], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        # Dedup retains the first-seen record. The attachment is written ahead of the
        # current-format twin above, so the retained event carries hook=worktree; had
        # the twin come first, hook_name would be empty.
        assert events[0]["hook_name"] == "worktree"

    def test_multiple_distinct_current_format_denials_each_counted(self):
        """Two current-format denials with distinct tool_use_ids count as two events —
        dedup collapses same-id pairs, not distinct denials."""
        records = [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        assert len(denial_events) == 2

    def test_current_format_denial_with_list_content_detected(self):
        """A current-format denial whose tool_result content is a list of text blocks
        (not a bare string) is still detected — hook_denial_key's signature match
        relies on _content_text to decode the list shape before matching."""
        rec = _hook_deny_current("placeholder")
        rec["message"]["content"][0]["content"] = [
            {"type": "text", "text": "Commit blocked by code-review gate: run /code-review."},
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [rec], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert "code-review gate" in events[0]["message"]

    def test_deny_only_matches_current_format_denial(self):
        """--deny-only retains a session whose only denial is current-format."""
        denial_events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [_hook_deny_current("Push to a branch blocked by ready-for-review gate.")], None, None, None,
        )
        no_denial_events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [
                _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                      content=[_agent_use("a1", "staff-sdet")]),
            ],
            None, None, None,
        )
        assert any(e["kind"] == "denial" for e in denial_events)
        assert not any(e["kind"] == "denial" for e in no_denial_events)

    def test_deny_only_plain_timeline_restricts_to_denial_sessions(self, fake_projects, capsys):
        """--deny-only's session-skip (the `if deny_only and not has_denial:
        continue` gate inside cmd_review_trace itself, not either accessor) drops
        a session with a matched event but no denial from the plain (non-
        --deny-summary) timeline — a session with a denial still prints."""
        _write_jsonl(fake_projects / "denial-session.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review."),
        ])
        _write_jsonl(fake_projects / "skill-only-session.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args(deny_only=True))
        out = capsys.readouterr().out
        assert "denial-session.jsonl" in out
        assert "skill-only-session.jsonl" not in out

    def test_default_timeline_denial_line_carries_cause_field(self, fake_projects, capsys):
        """The plain (non-deny-summary) timeline's denial line prints
        cause=<kind> classified from the denial's own message, alongside
        the existing hook= field."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Blocked by code-review gate: could not source _lib.sh."),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "cause=lib-source" in out

    # -----------------------------------------------------------------------
    # GH-482: per-record branch/model attribution
    # -----------------------------------------------------------------------

    def test_events_attributed_to_own_branch_not_session_first_branch(self):
        """A session opening on one branch, then moving to another before any review
        event fires, must attribute every event to its own (later) branch — and
        branch_filter must select by that per-event value, not the session's
        first record's branch."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T09:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feature-x", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="feature-x", ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ]

        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 2
        for evt in events:
            assert evt["branch"] == "feature-x", f"event must attribute to feature-x, not main: {evt!r}"

        events_feature_x, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, {"feature-x"},
        )
        assert {e["kind"] for e in events_feature_x} == {"skill", "reviewer-spawn"}

        events_main_only, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, {"main"},
        )
        assert events_main_only == [], "the session's first-record branch must return zero events"

    def test_header_branches_and_models_are_distinct_sorted_sets(self):
        """The per-event branch/model values a session contributes are the distinct
        set cmd_review_trace's header line joins and sorts, not a single session-wide value."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat-a", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="feat-b", ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert {e["branch"] for e in events} == {"feat-a", "feat-b"}
        assert {e["model"] for e in events} == {"sonnet", "opus"}

    def test_denial_stamped_with_its_own_branch_not_carried_forward(self):
        """An attachment denial record carrying its own gitBranch, differing from the
        carried-forward branch, is stamped with the record's own value."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
            _hook_deny("require-code-review", branch="feature-y", ts="2026-05-19T10:05:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        denial_event = next(e for e in events if e["kind"] == "denial")
        assert denial_event["branch"] == "feature-y"

    def test_denial_inherits_last_assistant_model_not_other(self):
        """A denial carries no message.model of its own — it must inherit the last
        main-thread assistant model family, not render 'other'."""
        records = [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
            _hook_deny("require-code-review", branch="main", ts="2026-05-19T10:05:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        denial_event = next(e for e in events if e["kind"] == "denial")
        assert denial_event["model"] == "opus"

    def test_unresolvable_branch_renders_sentinel(self):
        records = [
            _asst("claude-sonnet-4-6", branch="", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events[0]["branch"] == "?"

    def test_branch_carry_forward_crosses_since_boundary(self):
        """An in-window event with no gitBranch of its own inherits the branch of an
        out-of-window record — carry-forward crosses the --since boundary."""
        records = [
            _asst("claude-sonnet-4-6", branch="old-branch", ts="2026-05-01T10:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs("2026-05-10", None)
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["branch"] == "old-branch"

    def test_deny_only_with_branches_filters_before_gating(self):
        """The sole denial sits on a branch the filter excludes: branch filtering
        drops it before deny_only's has_denial check ever sees it (filter-then-deny),
        not a session that still qualifies because it had a denial before filtering."""
        records = [_hook_deny("require-code-review", branch="wrong-branch")]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, {"right-branch"},
        )
        assert events == []

    def test_dedup_before_branch_filter_pins_ordering(self):
        """A duplicate-id denial recorded on two different branches must still
        collapse to one event when both branches are in scope — dedup (step 3)
        is global and runs before branch filtering (step 5), not scoped per branch."""
        attach = _hook_deny("worktree", branch="branch-a")
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree", branch="branch-b",
        )
        records = [attach, twin]

        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        assert len(denial_events) == 1

        # attach (branch-a) is the first-occurring record, so dedup collapses the
        # pair to a single event attributed to branch-a — filtering to branch-b
        # alone (the second-occurring, non-surviving branch) must then drop that
        # event entirely. A filter-before-dedup implementation would instead
        # exclude attach before dedup ever runs, letting twin (branch-b) through
        # undeduped and yielding one event — the regression this pins against.
        events_branch_b_only, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, {"branch-b"},
        )
        assert events_branch_b_only == []

    def test_deny_summary_groups_by_hook_and_command_shape(self):
        """--deny-summary groups denials by hook/gate name and by attempted command
        shape, mixing multiple hook names (code-review x2, ready-for-review x1) and
        multiple git-command shapes (git commit x2, git push x1)."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b2", "git push origin main")]),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="b2"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:02:00.000Z",
                  content=[_bash_use("b3", "git commit -m y")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b3"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"code-review": 2, "ready-for-review": 1}
        assert dict(data["command_shape_counts"]) == {"git commit": 2, "git push": 1}

    def test_deny_summary_command_shape_empty_command_bucketed_as_other(self):
        """A denial with an enumerated hook name but no paired Bash tool_use (an
        empty command string) still lands the command-shape axis in 'other' — the
        shape-axis counterpart to test_deny_summary_unmatched_hook_name_bucketed_not_dropped,
        isolated from that test's hook-axis unmatched-ness."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {_mod.denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_git_dash_c_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git -C <path> commit' buckets as 'git commit', not 'other' and not a
        naive misread of <path> as the subcommand — -C is
        require-worktree-for-git-writes.sh's own resolution mechanism for a
        compliant worktree write, so this is the dominant separate-token flag
        shape in the worktree-enforcement denial category. The path itself never
        appears in the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git -C ~/repo commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_git_dash_lowercase_c_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git -c key=value commit' (a separate-token config override, the value
        itself containing '=') buckets as 'git commit', not 'other'."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git -c user.name=eng commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_git_dir_equals_attached_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git --git-dir=<path> status' (an =-attached flag, consuming only its
        own token) buckets as 'git status', not 'other'. The path never leaks
        into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git --git-dir=~/repo/.git status")]),
            _hook_deny_current("Blocked by worktree-enforcement gate: not in a linked worktree.", tool_id="b1"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git status": 1}

    def test_deny_summary_work_tree_separate_token_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git --work-tree <path> commit' (a separate-token flag) buckets as
        'git commit', not 'other'. The path never leaks into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git --work-tree ~/repo commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_env_assignment_prefix_stripped_before_classification(self):
        """A leading NAME=VALUE environment-assignment prefix (the corpus shape
        wrapping a marker.sh invocation with a live per-machine token) is
        stripped before classification — the denial buckets as 'marker.sh
        write' and the env value never leaks into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use(
                      "b1",
                      "CLAUDE_CONFIG_DIR=~/.config/claude-accounts/proj "
                      "~/.claude/scripts/marker.sh write code-review",
                  )]),
            _hook_deny_current(
                "marker.sh invocation denied (path traversal '..' detected). "
                "Command (truncated): ~/.claude/scripts/marker.sh write code-review",
                tool_id="b1",
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"marker.sh write": 1}

    def test_deny_summary_absolute_marker_script_path_basenamed_not_leaked(self):
        """An absolute marker.sh invocation path (rather than the tilde form) is
        basenamed before classification — the denial buckets as 'marker.sh
        activate', with no home-rooted path surviving into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "~/.claude/scripts/marker.sh activate plan-review")]),
            _hook_deny_current(
                "marker.sh invocation denied (path traversal '..' detected). "
                "Command (truncated): ~/.claude/scripts/marker.sh activate plan-review",
                tool_id="b1",
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"marker.sh activate": 1}

    def test_deny_summary_unenumerated_attached_flag_before_subcommand_falls_to_other_no_leak(self):
        """A git global flag outside the named value-taking set (e.g.
        --exec-path=<path>) is left in place by _drop_denial_command_flag_values,
        but since it looks like a flag it must never be read as, and bucketed as,
        the subcommand — the denial falls to 'other' rather than leaking the
        attached path into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git --exec-path=~/secret-tools status")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {_mod.denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_esc_byte_in_trailing_argument_never_reaches_stdout(self):
        """An ESC byte embedded in an argument past the subcommand (e.g. a commit
        message) never survives into the returned command-shape data — the
        classifier only ever keeps the command and one subcommand token, so the
        denial buckets as 'git commit' with the control byte discarded along with
        the rest of the argument."""
        esc_message = "\x1b[31mFAKE PROMPT\x1b[0m"
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", f'git commit -m "{esc_message}"')]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_unenumerated_non_flag_subcommand_token_falls_to_other_no_leak(self):
        """A credential-shaped token occupying the subcommand position itself
        (not a flag, not a member of _DENIAL_COMMAND_SUBCOMMANDS) must never be
        read as, and bucketed as, the subcommand — the denial falls to 'other'
        and the token never appears as a key in the returned shape counts."""
        credential_token = "AKIA_FAKE_SECRET_ACCESS_KEY_ABCDEFGHIJKL"
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", f"git {credential_token} status")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {_mod.denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_unmatched_hook_name_bucketed_not_dropped(self):
        """A denial matched via _HOOK_DENIAL_SIGNATURE's 'invocation denied' alternative,
        which names no hook, lands in the 'unmatched' bucket rather than being silently
        dropped from --deny-summary's total. Its unresolvable tool_use_id also lands in
        the command-shape grouping's 'other' bucket."""
        records = [_hook_deny_current("Skill invocation denied.")]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod.denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}
        assert dict(data["command_shape_counts"]) == {_mod.denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_covers_marker_invocation_denied_wording(self):
        """enforce-marker-script-shape.sh's 'marker.sh invocation denied ...' wording
        names no hook via the 'blocked by <name> hook/gate' idiom, but the
        '<name> invocation denied' pattern extracts 'marker.sh' as an enumerated
        label rather than falling to unmatched."""
        records = [
            _hook_deny_current(
                "marker.sh invocation denied (path traversal '..' detected). "
                "Command (truncated): ~/.claude/scripts/marker.sh write ../foo"
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"marker.sh": 1}

    def test_deny_summary_covers_self_labeled_gate_colon_wording(self):
        """check-skill-length.sh states its own label as the message's own prefix
        ('Skill length gate: ...') rather than via 'blocked by' — the
        '<name> gate:' pattern extracts 'Skill length' as an enumerated label."""
        records = [
            _hook_deny_current(
                "Skill length gate: one or more SKILL.md files grew past their "
                "per-skill limit. Reduce to the limit before committing."
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"Skill length": 1}

    def test_deny_summary_unenumerated_colon_wording_falls_to_unmatched_no_leak(self):
        """deny-credential-file-reads.sh's 'Read of '<path>' denied by the
        credential-file read gate: ...' wording now matches _HOOK_DENIAL_SIGNATURE's
        colon-anchored alternative (previously invisible), but the captured span
        includes the 'denied by the' prefix and so isn't an enumerated label —
        it falls to 'unmatched' rather than fabricating a new hook bucket, and the
        credential-shaped path never appears as a key in the returned hook counts."""
        records = [
            _hook_deny_current(
                "Read of './secrets/.netrc' denied by the credential-file "
                "read gate: the path is credential-shaped."
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod.denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    @pytest.mark.parametrize(
        "message_template",
        [
            "Blocked by {name} gate: could not source _lib.sh.",
            "{name} invocation denied. Command (truncated): ~/.claude/scripts/marker.sh write foo",
            "{name} gate: some detail.",
        ],
    )
    def test_deny_summary_over_max_chars_hook_name_candidate_falls_to_unmatched_no_leak(self, message_template):
        """A candidate hook-name span longer than _DENIAL_HOOK_NAME_MAX_CHARS
        (40) across each of the three extraction patterns never yields an
        enumerated label — it falls to 'unmatched', and the credential-shaped
        name is never returned as the label."""
        over_cap_name = "AKIA_FAKE_SECRET_ACCESS_KEY_" + "X" * 20  # 48 chars, over the 40-char cap
        records = [_hook_deny_current(message_template.format(name=over_cap_name))]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod.denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    def test_deny_summary_attachment_hookname_not_enumerated_falls_to_unmatched(self):
        """The legacy attachment branch's hookName field is bounded the same way as
        the regex-extracted branch: an unenumerated hookName (legacy transcripts
        predate this bound, so any historical value is unverified) is not echoed
        verbatim into the returned hook counts — it falls to 'unmatched'."""
        records = [_hook_deny("legacy-hook-slug")]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod.denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    def test_deny_summary_replaces_per_session_listing(self, fake_projects, capsys):
        """--deny-summary suppresses the normal per-session event listing entirely —
        no '### <file>' block appears, only the two grouped-count tables."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate."),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args(deny_summary=True))
        out = capsys.readouterr().out
        assert "### " not in out
        assert "Denials by hook/gate" in out
        assert "Denials by attempted command shape" in out

    def test_deny_summary_with_matching_session_but_zero_denials_prints_explicit_message(
        self, fake_projects, capsys
    ):
        """A scope with a matching session (a skill event, no denial) under
        --deny-summary prints an explicit 'no denials found' message with the
        scope header — not byte-for-byte empty output, which would be
        indistinguishable from a broken --branches/scope flag matching nothing."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args(deny_summary=True))
        out = capsys.readouterr().out
        assert "No denials found in scope." in out
        assert "Denials by hook/gate" not in out

    def test_absent_toolDenialKind_produces_no_friction_event(self):
        """A current-format denial with no toolDenialKind field produces only a
        `denial` event — no `friction` event, since a falsy toolDenialKind means
        the field is absent, not friction."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"

    def test_already_gate_denied_record_produces_denial_not_friction(self):
        """A record whose text matches the hook-denial signature AND carries a
        non-gate toolDenialKind produces only a `denial` event, never also a
        `friction` one — already_gate_denied short-circuits
        _is_nongate_friction_kind so one record can't double-count across both
        axes."""
        records = [
            _hook_deny_current(
                "Commit blocked by code-review gate: run /code-review.",
                tool_id="toolu_both", tool_denial_kind="user-rejected",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"

    def test_multi_block_record_produces_one_friction_event_for_the_errored_block_only(self):
        """toolDenialKind lives once on the parent user record, but a parallel
        tool call can carry multiple tool_result blocks under it — only the
        block whose own is_error is True is the one the interruption applies
        to. A sibling successful block (is_error False) must not also be
        promoted to its own spurious friction event carrying its unrelated
        successful output."""
        records = [
            {
                "type": "user",
                "gitBranch": "main",
                "isSidechain": False,
                "toolDenialKind": "interrupted",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_errored",
                     "content": "Request interrupted by user for tool use", "is_error": True},
                    {"type": "tool_result", "tool_use_id": "toolu_ok",
                     "content": "some unrelated successful output", "is_error": False},
                ]},
            },
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(friction_events) == 1
        assert friction_events[0]["tool_use_id"] == "toolu_errored"

    def test_legacy_attachment_denial_and_friction_kind_coexist(self):
        """A legacy attachment denial and a separate current-format friction
        record (distinct tool_use_ids) in the same session produce one denial
        event and one friction event — the legacy shape never carries
        toolDenialKind, so it cannot itself become friction, and the two axes
        don't interfere with each other."""
        records = [
            _hook_deny("require-code-review"),
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="toolu_interrupt",
                tool_denial_kind="interrupted",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(denial_events) == 1
        assert len(friction_events) == 1

    def test_friction_dedup_set_independent_of_denial_dedup_set(self):
        """A legacy attachment denial and a current-format record sharing the
        SAME tool_use_id, where the current-format record carries a non-gate
        toolDenialKind and non-signature-matching text, still produces a
        friction event — friction dedups against its own set, never
        seen_denial_ids, so an id already recorded there doesn't suppress a
        later friction event."""
        shared_id = "toolu_worktree"
        attach = _hook_deny("worktree")  # toolUseID == "toolu_worktree"
        friction_twin = _hook_deny_current(
            "Request interrupted by user for tool use", tool_id=shared_id,
            tool_denial_kind="interrupted",
        )
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            [attach, friction_twin], None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(denial_events) == 1
        assert len(friction_events) == 1

    def test_friction_event_with_empty_tool_use_id_not_deduped_against_others(self):
        """Multiple friction records with no tool_use_id (empty string) each
        still produce their own event — an empty id is falsy and so is never
        added to seen_friction_ids, matching hook_denial_key's own 'empty
        string is a valid id' contract for denials."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="",
                tool_denial_kind="interrupted",
            ),
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="",
                tool_denial_kind="interrupted", ts="2026-05-19T10:01:00.000Z",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(friction_events) == 2

    def test_unrecognized_toolDenialKind_prints_as_other_kind_not_raw_value(self):
        """A toolDenialKind value outside the closed four-value enumeration
        still produces a friction event, carrying the raw field value verbatim
        on the returned event — _friction_kind_label (already unit-tested
        separately) is what maps it to `other-kind` at print/count time, not
        the accessor itself."""
        records = [
            _hook_deny_current(
                "Some new denial shape not yet enumerated.", tool_id="toolu_future",
                tool_denial_kind="some-future-kind",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(friction_events) == 1
        assert friction_events[0]["friction_kind"] == "some-future-kind"

    def test_friction_only_session_survives_deny_only_with_deny_summary(self):
        """A session with only friction events (no denial-kind events at all) is
        not dropped by --deny-only when --deny-summary also runs: the friction
        tally reads the full per-session events list before deny_only's
        has_denial skip is applied."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="toolu_a",
                tool_denial_kind="interrupted",
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data(
            [("friction_only.jsonl", records)], deny_only=True,
        )
        assert data["any_session_matched"] is True
        assert dict(data["friction_counts"]) == {"interrupted": 1}

    def test_friction_only_session_renders_timeline_line_default_output(self):
        """Without --deny-summary, a friction-only session's events list carries
        a `friction`-kind event rather than nothing, and pinning the flip side,
        no `denial`-kind event is present — has_denial and --deny-only's own
        session-selection semantics stay denial-kind-only."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="toolu_a",
                tool_denial_kind="interrupted",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod.review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "friction"
        assert not any(e["kind"] == "denial" for e in events)

    def test_deny_summary_prints_corpus_window(self):
        """--deny-summary computes the earliest/latest in-scope event
        timestamp as the corpus window, not just the grouped counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.",
                                tool_id="b1", ts="2026-07-01T10:00:01.000Z"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-15T09:00:00.000Z",
                  content=[_bash_use("b2", "git push origin main")]),
            _hook_deny_current("Push blocked by ready-for-review gate.",
                                tool_id="b2", ts="2026-07-15T09:00:01.000Z"),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["corpus_min_ts"] == _mod._parse_ts("2026-07-01T10:00:01.000Z")
        assert data["corpus_max_ts"] == _mod._parse_ts("2026-07-15T09:00:01.000Z")

    def test_deny_summary_corpus_window_widened_by_consult_event_outside_denial_range(self):
        """The corpus min/max window reads every event kind, not just denial.
        An architect-consult event timestamped outside the range the
        corpus's own denial events establish must move the window -- proving
        the widening is real, not merely that the session registers."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.",
                                tool_id="b1", ts="2026-07-01T10:00:01.000Z"),
            _asst("claude-opus-4-7", branch="main", ts="2026-07-20T09:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nquestion")]),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["corpus_min_ts"] == _mod._parse_ts("2026-07-01T10:00:01.000Z")
        assert data["corpus_max_ts"] == _mod._parse_ts("2026-07-20T09:00:00.000Z")

    def test_deny_summary_pre_regime_record_excluded_from_kind_breakdown_and_counted_separately(self):
        """An errored, non-gate-signature tool_result timestamped before
        toolDenialKind's 2026-07-20 introduction structurally cannot carry
        the field — it produces neither a denial nor a friction event (the
        exact record shape this design would silently read as zero friction),
        but pre_regime_tool_result_count reports it separately rather than
        folding it into a zero. A same-shaped record dated inside the
        regime with a real toolDenialKind is included as a control, pinning
        that the pre-regime count is date-gated, not 'every non-denial
        record'. A gate-matching denial dated before the regime is also
        included, pinning that already-gate-denied records — already
        correctly classified on the hook/gate axis regardless of era — are
        excluded from the pre-regime count, which counts only the population
        whose kind is genuinely unknowable, not every old record."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use",
                tool_id="pre_regime", ts="2026-06-25T10:00:00.000Z",
            ),
            _hook_deny_current(
                "Commit blocked by code-review gate: run /code-review.",
                tool_id="pre_regime_gate", ts="2026-06-25T10:01:00.000Z",
            ),
            _hook_deny_current(
                "Request interrupted by user for tool use",
                tool_id="in_regime", tool_denial_kind="interrupted",
                ts="2026-07-25T10:00:00.000Z",
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["pre_regime_tool_result_count"] == 1
        assert dict(data["friction_counts"]) == {"interrupted": 1}

    def test_deny_summary_cross_tab_shows_joint_counts_not_just_marginals(self):
        """Two hooks each deny two command shapes with symmetric marginals
        (code-review: 2 commits + 1 checkout = 3; worktree-enforcement: 1
        commit + 2 checkouts = 3; git commit: 2+1=3; git checkout: 1+2=3) —
        the marginal hook and shape counts alone can't distinguish which hook
        denied which shape how many times. hook_shape_counts must carry the
        true joint counts (code-review x git commit = 2, worktree-enforcement
        x git checkout = 2), not the marginal-implied even split."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:01:00.000Z",
                  content=[_bash_use("b2", "git commit -m y")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b2"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:02:00.000Z",
                  content=[_bash_use("b3", "git checkout main")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b3"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:03:00.000Z",
                  content=[_bash_use("b4", "git commit -m z")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git commit' is not on the read-only allowlist.",
                tool_id="b4",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:04:00.000Z",
                  content=[_bash_use("b5", "git checkout main")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git checkout' is not on the read-only allowlist.",
                tool_id="b5",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:05:00.000Z",
                  content=[_bash_use("b6", "git checkout main")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git checkout' is not on the read-only allowlist.",
                tool_id="b6",
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        # Marginals confirm the symmetric setup (both hooks 3, both shapes 3).
        assert dict(data["command_shape_counts"]) == {"git commit": 3, "git checkout": 3}
        # The cross-tab is what actually distinguishes the two hooks' shapes.
        assert data["hook_shape_counts"][("code-review", "git commit")] == 2
        assert data["hook_shape_counts"][("code-review", "git checkout")] == 1
        assert data["hook_shape_counts"][("worktree-enforcement", "git commit")] == 1
        assert data["hook_shape_counts"][("worktree-enforcement", "git checkout")] == 2

    def test_deny_summary_cause_cross_tab_shows_joint_counts_not_just_marginals(self):
        """Two hooks each produce a mix of behavioral and lib-source denials
        with symmetric marginals (code-review: 2 behavioral + 1 lib-source;
        worktree-enforcement: 1 behavioral + 2 lib-source) — the cause
        marginal alone can't say which hook hit which failure family.
        hook_cause_counts must carry the true joint counts."""
        records = [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b2"),
            _hook_deny_current("Blocked by code-review gate: could not source _lib.sh.", tool_id="b3"),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git commit' is not on the read-only allowlist.",
                tool_id="b4",
            ),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh.",
                tool_id="b5",
            ),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh.",
                tool_id="b6",
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["hook_cause_counts"][("code-review", "behavioral")] == 2
        assert data["hook_cause_counts"][("code-review", "lib-source")] == 1
        assert data["hook_cause_counts"][("worktree-enforcement", "behavioral")] == 1
        assert data["hook_cause_counts"][("worktree-enforcement", "lib-source")] == 2

    def test_deny_summary_cause_table_prints_header_and_correct_cross_tab_cell(
        self, fake_projects, capsys
    ):
        """--deny-summary's printed output carries the hook/gate x cause table
        header and a joint count in the right column — not just the
        marginal hook/gate and friction tables that predate this axis."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _hook_deny_current("Blocked by code-review gate: could not source _lib.sh.", tool_id="b2"),
        ])
        _mod.review_trace.cmd_review_trace(_review_trace_args(deny_summary=True))
        out = capsys.readouterr().out
        assert "## Denials by hook/gate x cause" in out
        cols = _table_cols(out, header_contains="behavioral", row_contains="code-review")
        assert cols["behavioral"] == "1"
        assert cols["lib-source"] == "1"

    def test_deny_summary_real_corpus_shapes_all_classify_no_other_or_unmatched(self):
        """A fixture drawn from real transcript-analysis.py corpus denials —
        realistic multi-line/chained commands and full hook-message wording,
        not minimal strings copied from A3's own allowlist — across both of
        GH-557's named categories (worktree-enforcement/other-git, marker.sh)
        plus two more hooks (code-review, respond-pr) for label diversity.
        Every one of these shapes was observed actually landing in
        --deny-summary's 'other'/'unmatched' buckets before A2/A3, and must
        classify cleanly now: both denominators are 0 for this fixture. The
        four non-gate friction kinds never contribute to either denominator
        in the first place — hook_counts/command_shape_counts are populated
        only from `denial`-kind events, never `friction`-kind ones — so they
        are irrelevant to, not merely absent from, this fixture."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z", content=[_bash_use(
                "b1", "git checkout main && git pull --ff-only && git worktree add "
                      ".claude/worktrees/some-feature -b some-feature",
            )]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git checkout' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b1",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:01:00.000Z", content=[_bash_use(
                "b2", "git -C ~/repo/.claude/worktrees/some-feature add -A",
            )]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git add' targets a working directory outside "
                "this repository (or its git state could not be determined), so it cannot be confirmed safe.",
                tool_id="b2",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:02:00.000Z",
                  content=[_bash_use("b3", "git push -u origin some-feature 2>&1")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git push' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b3",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:03:00.000Z",
                  content=[_bash_use("b4", "git -C /tmp/ignoretest init -q")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git init' targets a working directory outside "
                "this repository (or its git state could not be determined), so it cannot be confirmed safe.",
                tool_id="b4",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:04:00.000Z",
                  content=[_bash_use("b5", "git pull --ff-only")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git pull' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b5",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:05:00.000Z",
                  content=[_bash_use("b6", "git config --system --show-origin --get-all credential.helper")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git config' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b6",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:06:00.000Z", content=[_bash_use(
                "b7", "~/.claude/scripts/marker.sh write ready-for-review\n"
                      "~/.claude/scripts/marker.sh deactivate ready-for-review 2>&1 || true",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh write "
                "ready-for-review",
                tool_id="b7",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:07:00.000Z",
                  content=[_bash_use("b8", "~/.claude/scripts/marker.sh activate ready-for-review 2>&1")]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh activate "
                "ready-for-review",
                tool_id="b8",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:08:00.000Z", content=[_bash_use(
                "b9", "~/.claude/scripts/marker.sh deactivate plan-review && "
                      "~/.claude/scripts/marker.sh write plan-review && echo \"markers updated\"",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh deactivate "
                "plan-review",
                tool_id="b9",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:09:00.000Z", content=[_bash_use(
                "b10", "~/.claude/scripts/marker.sh status plan-review 2>&1 || "
                       "ls -la ~/.claude/plan-review-markers/ 2>&1 | head",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh status "
                "plan-review",
                tool_id="b10",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:10:00.000Z", content=[_bash_use(
                "b11", "~/.claude/scripts/marker.sh clear-stale\necho \"--- after ---\"\n"
                       "ls ~/.claude/.plan-review-active.d/ 2>/dev/null",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh clear-stale",
                tool_id="b11",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:11:00.000Z", content=[_bash_use(
                "b12", "git commit --amend --no-edit\ngit log --oneline -3",
            )]),
            _hook_deny_current(
                "Commit blocked by code-review gate: the currently staged changes have not been reviewed, "
                "or the staged state has changed since the last review. Run the /code-review skill now.",
                tool_id="b12",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:12:00.000Z", content=[_bash_use(
                "b13", "gh api repos/example-org/example-repo/pulls/1/reviews "
                       "--jq '.[] | {user: .user.login, state: .state, body: .body}' 2>&1 | head -60",
            )]),
            _hook_deny_current(
                "PR comment access blocked by respond-pr gate. Run the /respond-pr skill instead.",
                tool_id="b13",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:13:00.000Z", content=[_bash_use(
                "b14", "gh pr review 1 --repo example-org/example-repo --comment "
                       "--body-file ~/handoffs/pr-1-review-body.md",
            )]),
            _hook_deny_current(
                "PR/issue comment write blocked by respond-pr gate. Writes are denied for every repo.",
                tool_id="b14",
            ),
        ]
        data = _mod.review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["command_shape_counts"].get(_mod.denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE, 0) == 0
        assert data["hook_counts"].get(_mod.denials._DENY_SUMMARY_UNMATCHED_HOOK, 0) == 0


class TestComputeDenySummaryDataGroupBoundaryFreshRead:
    """_compute_deny_summary_data must derive group_boundaries from the same
    read that produces its records, not from session_iter's own records
    paired with an independent, later _read_session_file_partitioned call --
    the two reads can observe a growing transcript file differently."""

    def test_stale_session_iter_records_are_replaced_by_the_fresh_disk_read(self, fake_projects):
        """session_iter hands in a deliberately empty records list for this
        session -- simulating a read taken before the main and subagent
        files carried their denials -- while the real on-disk files already
        have one denial each. Both denials must still surface, proving
        detection runs against a fresh read of the files, not the stale,
        empty tuple session_iter provided."""
        session_id = "sess-toctou"
        jsonl = fake_projects / f"{session_id}.jsonl"
        _write_jsonl(jsonl, [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="b2"),
        ])

        data = _mod.review_trace.compute_deny_summary_data([(jsonl, [])])

        assert dict(data["hook_counts"]) == {"code-review": 1, "ready-for-review": 1}

