"""Tests for the events review_trace.py emits per record: skills, denials, reviewer spawns, consults, and friction."""

import pytest
from helpers import CONSULT_CLASSIFICATION_TABLE
from transcript_analysis import (
    review_trace,
)

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _hook_deny,
    _hook_deny_current,
    _review_trace_args,
    _skill_use,
    _user_msg,
    _write_jsonl,
)


class TestReviewTrace:
    def test_skill_invocation_appears_in_output(self):
        """Main-thread Skill call for a review skill produces a 'skill' event."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_denial_dict_blockingError_parsed(self):
        """hook_blocking_error with blockingError as a dict produces a denial event."""
        records = [_hook_deny("require-code-review", stringified=False)]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert events[0]["hook_name"] == "require-code-review"

    def test_denial_stringified_blockingError_parsed_identically(self):
        """hook_blocking_error with blockingError as a JSON string produces
        an identical denial event to the dict form."""
        dict_events, _tuc1, _pr1 = review_trace._review_trace_session_events(
            [_hook_deny("require-code-review", stringified=False)], None, None, None,
        )
        str_events, _tuc2, _pr2 = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        review_trace.cmd_review_trace(_review_trace_args())
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
        review_trace.cmd_review_trace(_review_trace_args())
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
        review_trace.cmd_review_trace(_review_trace_args())
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"
        assert events[0]["thread"] == "sidechain"

    def test_deny_only_restricts_to_denial_sessions(self):
        """--deny-only retains sessions with a denial event; a session with a
        reviewer spawn but no denial does not qualify."""
        session_a, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            [_hook_deny("require-code-review")], None, None, None,
        )
        session_b, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            [
                _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                      content=[_agent_use("a1", "staff-backend-engineer")]),
            ],
            None, None, None,
        )
        assert any(e["kind"] == "denial" for e in session_a)
        assert not any(e["kind"] == "denial" for e in session_b)

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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_current_format_denial_detected(self):
        """A current-format is_error tool_result with a hook-denial signature produces a denial event."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert "code-review gate" in events[0]["message"]

    def test_current_format_ordinary_error_is_not_a_denial(self):
        """An is_error tool_result without a hook-denial signature produces no events."""
        records = [_hook_deny_current("npm ERR! command failed with exit code 1")]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_current_format_denial_text_without_is_error_ignored(self):
        """A tool_result with denial-shaped text but no is_error flag produces no events."""
        rec = _hook_deny_current("Blocked by worktree-enforcement hook: not allowed.")
        rec["message"]["content"][0]["is_error"] = False
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            [rec], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert "code-review gate" in events[0]["message"]

    def test_deny_only_matches_current_format_denial(self):
        """--deny-only retains a session whose only denial is current-format."""
        denial_events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            [_hook_deny_current("Push to a branch blocked by ready-for-review gate.")], None, None, None,
        )
        no_denial_events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            [
                _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                      content=[_agent_use("a1", "staff-sdet")]),
            ],
            None, None, None,
        )
        assert any(e["kind"] == "denial" for e in denial_events)
        assert not any(e["kind"] == "denial" for e in no_denial_events)

    def test_absent_toolDenialKind_produces_no_friction_event(self):
        """A current-format denial with no toolDenialKind field produces only a
        `denial` event — no `friction` event, since a falsy toolDenialKind means
        the field is absent, not friction."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(friction_events) == 1
        assert friction_events[0]["friction_kind"] == "some-future-kind"

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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "friction"
        assert not any(e["kind"] == "denial" for e in events)
