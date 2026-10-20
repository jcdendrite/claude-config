"""Tests for transcript_analysis/handoff_signal_response.py's per-record extraction: the Bash and tool_use signal
classifiers, the excerpt and forward-context windows, and _handoff_signal_response_session_rows' single-pass
signal detection."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from ._handoff_nudge_helpers import _check_call_turn, _check_result_json, _handoff_advisory_attachment
from .conftest import _bash_use, _priced, _skill_use, _thinking_block, _tool_result, _user_msg, _write_use

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _handoff_hard_block_attachment() -> dict:
    """The real hard-block attachment record shape ("hook_stopped_continuation",
    carrying "message" but no "command"/"stdout" -- nudge-handoff-near-context-cap.sh
    lines 644-649)."""
    return {
        "type": "attachment",
        "attachment": {
            "type": "hook_stopped_continuation",
            "message": (
                "[~/.claude/hooks/nudge-handoff-near-context-cap.sh]: Context (507297 tokens) is"
                " past this session's handoff-nudge hard-block point (HANDOFF_NUDGE_BLOCK_AT=470000),"
                " after 4 ignored re-arms. Blocking rather than advising: run /handoff now -- it"
                " captures state in a /tmp file and resumes in a fresh session."
            ),
            "hookName": "PostToolBatch",
            "hookEvent": "PostToolBatch",
        },
    }


class TestHandoffSignalDetectorHelpers:
    """Unit coverage for the small Bash-command/tool_use classifiers
    _handoff_signal_response_session_rows' own single pass reuses."""

    def test_bash_check_call_detected_in_plain_invocation(self):
        block = _bash_use("t1", "~/.claude/hooks/nudge-handoff-near-context-cap.sh --check")
        assert _mod.handoff_signal_response._handoff_signal_bash_check_call(block) is True

    def test_bash_check_call_detected_inside_chained_segment(self):
        block = _bash_use("t1", "cd /tmp && ~/.claude/hooks/nudge-handoff-near-context-cap.sh --check")
        assert _mod.handoff_signal_response._handoff_signal_bash_check_call(block) is True

    def test_bash_call_without_check_flag_is_not_a_check_call(self):
        block = _bash_use("t1", "~/.claude/hooks/nudge-handoff-near-context-cap.sh")
        assert _mod.handoff_signal_response._handoff_signal_bash_check_call(block) is False

    def test_non_bash_tool_use_is_not_a_check_call(self):
        block = _skill_use("t1", "handoff")
        assert _mod.handoff_signal_response._handoff_signal_bash_check_call(block) is False

    def test_marker_activate_ready_for_review_detected(self):
        block = _bash_use("m1", "~/.claude/scripts/marker.sh activate ready-for-review")
        assert _mod.handoff_signal_response._handoff_signal_marker_transition(block) == "activate"

    def test_marker_deactivate_ready_for_review_detected(self):
        block = _bash_use("m1", "~/.claude/scripts/marker.sh deactivate ready-for-review")
        assert _mod.handoff_signal_response._handoff_signal_marker_transition(block) == "deactivate"

    def test_marker_transition_for_a_different_skill_is_ignored(self):
        """A marker.sh call for a DIFFERENT skill (e.g. handoff's own) must
        not be misread as a ready-for-review transition."""
        block = _bash_use("m1", "~/.claude/scripts/marker.sh activate handoff")
        assert _mod.handoff_signal_response._handoff_signal_marker_transition(block) is None

    def test_marker_activate_detected_inside_an_and_chained_segment(self):
        block = _bash_use("m1", "cd /tmp && ~/.claude/scripts/marker.sh activate ready-for-review")
        assert _mod.handoff_signal_response._handoff_signal_marker_transition(block) == "activate"

    def test_marker_deactivate_detected_inside_a_semicolon_chained_segment(self):
        block = _bash_use("m1", "cd /tmp ; ~/.claude/scripts/marker.sh deactivate ready-for-review")
        assert _mod.handoff_signal_response._handoff_signal_marker_transition(block) == "deactivate"

    def test_skill_handoff_invocation_is_a_handoff_event(self):
        assert _mod.handoff_signal_response._handoff_signal_is_handoff_event(_skill_use("h1", "handoff")) is True

    def test_skill_invocation_for_a_different_skill_is_not_a_handoff_event(self):
        assert _mod.handoff_signal_response._handoff_signal_is_handoff_event(_skill_use("h1", "plan-review")) is False

    def test_write_to_handoffs_file_is_a_handoff_event(self):
        block = _write_use("w1", "body", path="/repo/.claude/handoffs/my-task-handoff.md")
        assert _mod.handoff_signal_response._handoff_signal_is_handoff_event(block) is True

    def test_write_to_an_unrelated_path_is_not_a_handoff_event(self):
        block = _write_use("w1", "body", path="/repo/scratch/notes.md")
        assert _mod.handoff_signal_response._handoff_signal_is_handoff_event(block) is False


class TestHandoffSignalExcerptEligibility:
    """The source-turn-eligibility filter: an excerpt candidate is only
    ever a main-thread assistant record's own "text" content block."""

    _RATIONALIZATION_TEXT = "nearly complete, ignore this and finish -- no cost reasoning needed"

    def test_tool_use_block_inside_an_assistant_record_is_never_excerpt_eligible(self):
        """The record itself IS type=="assistant" -- pins that eligibility is
        filtered at the block level (type=="text" only), not merely by the
        record's own top-level type."""
        rec = _priced("claude-sonnet-5", content=[_bash_use("t1", f"echo '{self._RATIONALIZATION_TEXT}'")])
        assert _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(rec) == ""

    def test_tool_result_record_is_never_excerpt_eligible(self):
        rec = _user_msg([_tool_result("t1", self._RATIONALIZATION_TEXT)])
        assert _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(rec) == ""

    def test_plain_user_turn_record_is_never_excerpt_eligible(self):
        rec = _user_msg(self._RATIONALIZATION_TEXT)
        assert _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(rec) == ""

    def test_sidechain_assistant_text_is_not_excerpt_eligible(self):
        """A subagent's own text turn: the nudge never fires inside a
        subagent, so this isn't the orchestrating session's own rationalization."""
        rec = _priced("claude-sonnet-5", content=[{"type": "text", "text": self._RATIONALIZATION_TEXT}])
        rec["isSidechain"] = True
        assert _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(rec) == ""

    def test_plain_main_thread_assistant_text_is_excerpt_eligible(self):
        rec = _priced("claude-sonnet-5", content=[{"type": "text", "text": self._RATIONALIZATION_TEXT}])
        assert _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(rec) == self._RATIONALIZATION_TEXT

    def test_excerpt_skips_ineligible_records_and_finds_next_eligible_assistant_text(self):
        deduped = [
            _priced("claude-sonnet-5", content=[_bash_use("t1", "echo hi")], request_id="r1"),
            _user_msg([_tool_result("t1", self._RATIONALIZATION_TEXT)]),
            _priced(
                "claude-sonnet-5", request_id="r2",
                content=[{"type": "text", "text": "Continuing because remaining steps are few."}],
            ),
        ]
        assert _mod.handoff_signal_response._handoff_signal_excerpt(deduped, after_record_index=0) == (
            "Continuing because remaining steps are few."
        )

    def test_excerpt_truncates_eligible_text_to_max_chars(self):
        # Sequential digits, not a repeated character: a wrong-window slice
        # (e.g. text[50:450]) is byte-distinguishable from the correct prefix.
        long_text = "".join(str(i % 10) for i in range(_mod.handoff_signal_response._HANDOFF_SIGNAL_EXCERPT_MAX_CHARS + 50))
        turn = _priced("claude-sonnet-5", content=[{"type": "text", "text": long_text}], request_id="r1")
        deduped = [_priced("claude-sonnet-5", request_id="r0"), turn]
        assert _mod.handoff_signal_response._handoff_signal_excerpt(deduped, after_record_index=0) == (
            long_text[:_mod.handoff_signal_response._HANDOFF_SIGNAL_EXCERPT_MAX_CHARS]
        )

    def test_tool_use_block_content_never_leaks_into_the_base_excerpt(self):
        """Mirrors TestHandoffSignalForwardContext's own
        test_tool_use_block_content_never_leaks_into_forward_context_text_or_thinking:
        a tool_use block mixed into an otherwise-eligible assistant record's
        content must never surface in the excerpt -- pinned as a
        security-invariant regression, not just a coverage gap, since a
        future refactor could otherwise leak tool-call content into a
        published case-study excerpt."""
        identifiable_command = "cat /scratch/api-keys.txt"
        rec = _priced(
            "claude-sonnet-5",
            content=[_bash_use("t1", identifiable_command), {"type": "text", "text": "wrapping up now"}],
        )
        assert _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(rec) == "wrapping up now"
        assert identifiable_command not in _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(rec)


class TestHandoffSignalForwardContext:
    """_handoff_signal_forward_context's own turn-walking, truncation, and
    thinking-block contract. Unlike _handoff_signal_excerpt_eligible_text
    (text blocks only), this reads both text and thinking content -- closing
    the base excerpt's own blind spot for an agent's extended-thinking
    reasoning."""

    def test_returns_up_to_n_turns_in_order_with_correct_turn_offset(self):
        deduped = [
            _priced("claude-sonnet-5", request_id="r0"),
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "turn one"}], request_id="r1"),
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "turn two"}], request_id="r2"),
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "turn three"}], request_id="r3"),
        ]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=2)
        assert [(c["turn_offset"], c["text"]) for c in contexts] == [(1, "turn one"), (2, "turn two")]

    def test_returns_fewer_than_n_turns_when_the_transcript_runs_out(self):
        deduped = [
            _priced("claude-sonnet-5", request_id="r0"),
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "only turn"}], request_id="r1"),
        ]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=5)
        assert len(contexts) == 1
        assert contexts[0]["turn_offset"] == 1

    def test_thinking_only_turn_surfaces_in_forward_context_but_not_in_base_excerpt(self):
        """Regression test for the base excerpt's own thinking-block blind
        spot: a turn with a thinking block and no text block is invisible to
        _handoff_signal_excerpt_eligible_text, but must still appear here."""
        turn = _priced("claude-sonnet-5", content=[_thinking_block()], request_id="r1")
        assert _mod.handoff_signal_response._handoff_signal_excerpt_eligible_text(turn) == ""
        deduped = [_priced("claude-sonnet-5", request_id="r0"), turn]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=1)
        assert contexts == [{"turn_offset": 1, "text": "", "thinking": "some thought"}]

    def test_truncates_text_and_thinking_independently_to_max_chars(self):
        long_text = "t" * (_mod.handoff_signal_response._HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS + 50)
        long_thinking = "k" * (_mod.handoff_signal_response._HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS + 50)
        turn = _priced(
            "claude-sonnet-5", request_id="r1",
            content=[{"type": "text", "text": long_text}, {"type": "thinking", "thinking": long_thinking}],
        )
        deduped = [_priced("claude-sonnet-5", request_id="r0"), turn]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=1)
        assert contexts[0]["text"] == long_text[:_mod.handoff_signal_response._HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS]
        assert contexts[0]["thinking"] == long_thinking[:_mod.handoff_signal_response._HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS]

    def test_sidechain_and_non_assistant_records_are_never_counted_as_turns(self):
        sidechain = _priced("claude-sonnet-5", content=[{"type": "text", "text": "subagent text"}], request_id="r1")
        sidechain["isSidechain"] = True
        deduped = [
            _priced("claude-sonnet-5", request_id="r0"),
            _user_msg([_tool_result("t1", "irrelevant")]),
            sidechain,
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "real turn"}], request_id="r2"),
        ]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=5)
        assert len(contexts) == 1
        assert contexts[0]["text"] == "real turn"

    def test_no_session_id_or_path_field_ever_appears_in_an_entry(self):
        deduped = [
            _priced("claude-sonnet-5", request_id="r0"),
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "turn"}], request_id="r1"),
        ]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=1)
        assert set(contexts[0]) == {"turn_offset", "text", "thinking"}

    def test_n_turns_zero_or_negative_returns_empty_list(self):
        """Docstring says "up to n_turns" entries -- n_turns<=0 must return
        none at all, not one spurious entry from appending before checking
        the length guard (the CLI-reachable --context-turns -1 boundary)."""
        deduped = [
            _priced("claude-sonnet-5", request_id="r0"),
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "turn one"}], request_id="r1"),
        ]
        assert _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=0) == []
        assert _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=-1) == []

    def test_interior_turn_with_only_tool_use_content_still_consumes_a_turn_slot(self):
        """A turn with no text/thinking content (only a tool_use block) must
        still count as a visited turn -- keeping turn_offset stable for every
        turn that follows it -- not be silently skipped like a sidechain or
        non-assistant record is."""
        empty_turn = _priced("claude-sonnet-5", content=[_bash_use("t1", "git status")], request_id="r1")
        deduped = [
            _priced("claude-sonnet-5", request_id="r0"),
            empty_turn,
            _priced("claude-sonnet-5", content=[{"type": "text", "text": "turn two"}], request_id="r2"),
        ]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=2)
        assert contexts[0] == {"turn_offset": 1, "text": "", "thinking": ""}
        assert contexts[1]["turn_offset"] == 2
        assert contexts[1]["text"] == "turn two"

    def test_tool_use_block_content_never_leaks_into_forward_context_text_or_thinking(self):
        """Mirrors TestHandoffSignalExcerptEligibility's own
        test_tool_use_block_inside_an_assistant_record_is_never_excerpt_eligible:
        a tool_use block mixed into an otherwise-eligible assistant record's
        content must never surface in either forward_context field -- pinned
        as a security-invariant regression, not just a coverage gap, since a
        future refactor could otherwise leak tool-call content into a
        published case-study excerpt."""
        identifiable_command = "cat /scratch/api-keys.txt"
        turn = _priced(
            "claude-sonnet-5", request_id="r1",
            content=[_bash_use("t1", identifiable_command), {"type": "text", "text": "wrapping up now"}],
        )
        deduped = [_priced("claude-sonnet-5", request_id="r0"), turn]
        contexts = _mod.handoff_signal_response._handoff_signal_forward_context(deduped, after_record_index=0, n_turns=1)
        assert contexts[0]["text"] == "wrapping up now"
        assert identifiable_command not in contexts[0]["text"]
        assert identifiable_command not in contexts[0]["thinking"]


class TestHandoffSignalResponseSessionRows:
    """_handoff_signal_response_session_rows' own signal-detection and
    row-composition contract, per
    .claude/plans/handoff-nudge-rationalization-gap.md."""

    def test_over_threshold_true_already_fired_false_emits_one_check_signal(self):
        records = [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True, already_fired=False))]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert len(rows) == 1
        assert rows[0]["kind"] == _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK

    def test_over_threshold_false_already_fired_true_emits_one_check_signal(self):
        records = [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=False, already_fired=True))]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert len(rows) == 1
        assert rows[0]["kind"] == _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK

    def test_over_threshold_and_already_fired_both_true_is_still_one_row_not_two(self):
        records = [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True, already_fired=True))]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert len(rows) == 1

    def test_neither_over_threshold_nor_already_fired_emits_no_signal(self):
        records = [
            _check_call_turn(input=50_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=False, already_fired=False))]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows == []

    def test_cannot_resolve_status_emits_no_signal(self):
        """A --check refusal (status != "ok") must never be misread as a
        qualifying over_threshold/already_fired result."""
        records = [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", json.dumps({"status": "cannot-resolve", "reason": "transcript-not-found"}))]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows == []

    def test_session_with_check_and_advisory_signals_emits_two_distinct_rows(self):
        records = [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _handoff_advisory_attachment(),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert [r["kind"] for r in rows] == [
            _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK,
            _mod.handoff_signal_response._HANDOFF_SIGNAL_ADVISORY,
        ]

    def test_advisory_then_later_hard_block_emits_two_distinct_rows_not_collapsed(self):
        """The only shape a hard-block signal occurs in: an earlier
        same-session advisory record (the hook's LAST_FIRED_AT invariant
        makes a session's first-ever fire always advisory) followed by a
        later hard-block record."""
        records = [
            _priced("claude-sonnet-5", input=160_000, output=1_000, request_id="r1"),
            _handoff_advisory_attachment(),
            _priced("claude-sonnet-5", input=480_000, output=1_000, request_id="r2"),
            _handoff_hard_block_attachment(),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert [r["kind"] for r in rows] == [
            _mod.handoff_signal_response._HANDOFF_SIGNAL_ADVISORY,
            _mod.handoff_signal_response._HANDOFF_SIGNAL_HARD_BLOCK,
        ]
        assert rows[0]["record_index"] < rows[1]["record_index"]

    def test_hard_block_record_shape_alone_is_detected_in_isolation(self):
        """Parser-level supplement to the session-level fixture above --
        does not substitute for it."""
        records = [
            _priced("claude-sonnet-5", input=480_000, output=1_000, request_id="r1"),
            _handoff_hard_block_attachment(),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert len(rows) == 1
        assert rows[0]["kind"] == _mod.handoff_signal_response._HANDOFF_SIGNAL_HARD_BLOCK

    def test_marker_active_state_reflects_the_most_recent_transition_at_signal_time(self):
        records = [
            _priced(
                "claude-sonnet-5", input=160_000, output=1_000, request_id="r1",
                content=[_bash_use("m1", "~/.claude/scripts/marker.sh activate ready-for-review")],
            ),
            _handoff_advisory_attachment(),
            _priced(
                "claude-sonnet-5", input=480_000, output=1_000, request_id="r2",
                content=[_bash_use("m2", "~/.claude/scripts/marker.sh deactivate ready-for-review")],
            ),
            _handoff_hard_block_attachment(),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows[0]["marker_active"] is True
        assert rows[1]["marker_active"] is False

    def test_handoff_followed_true_when_handoff_skill_invoked_after_signal_same_session(self):
        records = [
            _priced("claude-sonnet-5", input=160_000, output=1_000, request_id="r1"),
            _handoff_advisory_attachment(),
            _priced("claude-sonnet-5", input=170_000, output=1_000, request_id="r2", content=[_skill_use("h1", "handoff")]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows[0]["handoff_followed"] is True

    def test_handoff_followed_false_when_no_handoff_event_this_session(self):
        records = [
            _priced("claude-sonnet-5", input=160_000, output=1_000, request_id="r1"),
            _handoff_advisory_attachment(),
            _priced("claude-sonnet-5", input=170_000, output=1_000, request_id="r2"),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows[0]["handoff_followed"] is False

    def test_handoff_followed_true_via_write_to_handoffs_file(self):
        records = [
            _priced("claude-sonnet-5", input=160_000, output=1_000, request_id="r1"),
            _handoff_advisory_attachment(),
            _priced(
                "claude-sonnet-5", input=170_000, output=1_000, request_id="r2",
                content=[_write_use("w1", "body", path="/repo/.claude/handoffs/task-handoff.md")],
            ),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows[0]["handoff_followed"] is True

    def test_turns_and_dollars_after_signal_count_only_turns_strictly_after_the_signal(self):
        rec_before = _priced("claude-sonnet-5", input=160_000, output=1_000, request_id="r1")
        rec_after_1 = _priced("claude-sonnet-5", input=170_000, output=2_000, request_id="r2")
        rec_after_2 = _priced("claude-sonnet-5", input=180_000, output=2_000, request_id="r3")
        records = [rec_before, _handoff_advisory_attachment(), rec_after_1, rec_after_2]

        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)

        dollars_after_1, _ctx1, _u1 = _mod._price_turn("claude-sonnet-5", rec_after_1["message"]["usage"])
        dollars_after_2, _ctx2, _u2 = _mod._price_turn("claude-sonnet-5", rec_after_2["message"]["usage"])
        expected_dollars = sum(dollars_after_1.values()) + sum(dollars_after_2.values())

        assert rows[0]["turns_after_signal"] == 2
        assert rows[0]["dollars_after_signal"] == pytest.approx(expected_dollars)

    def test_pct_spend_after_signal_is_relative_to_the_whole_session_not_the_tail(self):
        """Two signals in one session, at different positions: each row's
        own pct_spend_after_signal must divide by the WHOLE session's total
        dollars (session_total_dollars), not by the spend remaining between
        the two signals -- a bug here would make the second signal's
        percentage look artificially high."""
        rec1 = _priced("claude-sonnet-5", input=100_000, output=1_000, request_id="r1")
        rec2 = _priced("claude-sonnet-5", input=110_000, output=2_000, request_id="r2")
        rec3 = _priced("claude-sonnet-5", input=120_000, output=3_000, request_id="r3")
        records = [
            rec1,
            _handoff_advisory_attachment(),  # signal A fires after rec1
            rec2,
            _handoff_hard_block_attachment(),  # signal B fires after rec2
            rec3,
        ]

        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert len(rows) == 2
        row_a, row_b = rows

        d1 = sum(_mod._price_turn("claude-sonnet-5", rec1["message"]["usage"])[0].values())
        d2 = sum(_mod._price_turn("claude-sonnet-5", rec2["message"]["usage"])[0].values())
        d3 = sum(_mod._price_turn("claude-sonnet-5", rec3["message"]["usage"])[0].values())
        total = d1 + d2 + d3

        assert row_a["session_total_dollars"] == pytest.approx(total)
        assert row_a["dollars_after_signal"] == pytest.approx(d2 + d3)
        assert row_a["pct_spend_after_signal"] == pytest.approx((d2 + d3) / total)

        assert row_b["session_total_dollars"] == pytest.approx(total)
        assert row_b["dollars_after_signal"] == pytest.approx(d3)
        assert row_b["pct_spend_after_signal"] == pytest.approx(d3 / total)

    def test_pct_spend_after_signal_is_none_when_session_total_dollars_is_zero(self):
        """A session whose every priced turn is $0 (zero usage counts) must
        report pct_spend_after_signal as None rather than raising
        ZeroDivisionError."""
        records = [
            _check_call_turn(input=0, output=0, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert len(rows) == 1
        assert rows[0]["session_total_dollars"] == 0.0
        assert rows[0]["pct_spend_after_signal"] is None

    def test_signal_at_transcript_end_reports_zero_turns_and_dollars_after_not_none(self):
        """The firing tool_result is the LAST record, with nothing after it --
        distinct from the zero-cost-session case above (session_total_dollars
        is nonzero here), this pins that "no turns follow" still resolves to
        0.0/0.0, never None, when there IS spend to divide against."""
        records = [
            _priced("claude-sonnet-5", input=100_000, output=1_000, request_id="r1"),
            _check_call_turn(input=200_000, output=1_000, request_id="r2"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert len(rows) == 1
        assert rows[0]["turns_after_signal"] == 0
        assert rows[0]["dollars_after_signal"] == 0.0
        assert rows[0]["pct_spend_after_signal"] == 0.0

    def test_malformed_json_in_check_tool_result_content_emits_no_signal(self):
        """The --check tool_result's own content is not valid JSON -- the
        (json.JSONDecodeError, ValueError) guard around that parse must
        swallow it silently, emitting no signal, rather than raise."""
        records = [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", "not valid json{")]),
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows == []

    def test_malformed_json_in_advisory_attachment_stdout_emits_no_signal(self):
        """The advisory hook_success attachment's own stdout is not valid
        JSON -- the (json.JSONDecodeError, ValueError) guard around that
        parse must swallow it silently, emitting no signal, rather than raise."""
        records = [
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_success",
                    "command": "~/.claude/hooks/nudge-handoff-near-context-cap.sh",
                    "hookEvent": "PostToolBatch",
                    "stdout": "not valid json{",
                    "stderr": "",
                    "exitCode": 0,
                },
            },
        ]
        rows, _deduped, _trace = _mod.handoff_signal_response._handoff_signal_response_session_rows(records)
        assert rows == []
