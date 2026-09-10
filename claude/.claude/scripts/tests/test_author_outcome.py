"""Tests for transcript_analysis/author_outcome.py (author-outcome)."""
import importlib.util
import sys
from pathlib import Path

import pytest
from transcript_analysis import author_outcome as ao
from transcript_analysis import corpus

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _skill_block,
    _tool_result,
    _user_msg,
    _write_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _session_iter(fake_projects):
    return corpus.iter_sessions(fake_projects.parent, "*")


def _append_use(tool_id: str, disposition: str, *, authoring_agent: str | None = None, finding: str = "some finding") -> dict:
    agent_flag = f" --authoring-agent {authoring_agent}" if authoring_agent is not None else ""
    command = (
        f'review-ledger.sh append code-review --finding "{finding}" --disposition {disposition}'
        f'{agent_flag} --rationale "why" --source n/a'
    )
    return _bash_use(tool_id, command)


def _marker_write_use(tool_id: str) -> dict:
    return _bash_use(tool_id, "marker.sh write code-review")


def _dispatch_start(tool_id: str, ts: str, *, agent_type: str = "code-writer", tool_name: str = "Agent") -> dict:
    return _asst("claude-sonnet-5", branch="feat", ts=ts, content=[_agent_use(tool_id, agent_type, tool_name=tool_name)])


def _dispatch_complete(tool_id: str, ts: str) -> dict:
    return _user_msg([_tool_result(tool_id, "done")], branch="feat", ts=ts)


class TestParseLedgerAppendFlags:
    """_parse_ledger_append_flags against one already-tokenized segment --
    the load-bearing boundary between the shell command shape and the
    disposition/authoring-agent values the classifier reads."""

    def _segment(self, command: str) -> list[str]:
        segments = corpus.split_command_segments(command)
        assert len(segments) == 1
        return segments[0]

    def test_append_segment_yields_disposition_and_authoring_agent(self):
        segment = self._segment(
            'review-ledger.sh append code-review --finding "fix the bug"'
            ' --disposition ADDRESS --authoring-agent code-writer --rationale "why"'
        )
        assert ao._parse_ledger_append_flags(segment) == {"disposition": "ADDRESS", "authoring_agent": "code-writer"}

    def test_append_segment_with_authoring_agent_absent_omits_the_key(self):
        """Entirely absent (not merely empty) -- the shape every
        pre-migration transcript has."""
        segment = self._segment('review-ledger.sh append code-review --finding "x" --disposition DEFER --rationale "why"')
        assert ao._parse_ledger_append_flags(segment) == {"disposition": "DEFER"}

    def test_unrelated_segment_returns_empty_dict_not_none(self):
        """An ordinary segment that isn't an append call at all is not a
        parse failure -- None is reserved for a segment that matches the
        append shape but fails to yield a --disposition value."""
        assert ao._parse_ledger_append_flags(["git", "status"]) == {}

    def test_append_shaped_segment_missing_disposition_returns_none(self):
        segment = self._segment('review-ledger.sh append code-review --finding "x" --rationale "why"')
        assert ao._parse_ledger_append_flags(segment) is None

    def test_finding_value_containing_a_dollar_var_does_not_affect_parsing(self):
        """This module never invokes a shell, so a `--finding "$VAR"` value
        is read back as the literal token "$VAR" -- irrelevant here, since
        only --disposition/--authoring-agent are read."""
        segment = self._segment('review-ledger.sh append code-review --finding "$FINDING_VAR" --disposition ADDRESS')
        assert ao._parse_ledger_append_flags(segment) == {"disposition": "ADDRESS"}

    def test_append_call_inside_a_shell_and_chain_is_matched(self):
        segments = corpus.split_command_segments(
            'cd worktree && review-ledger.sh append code-review --finding "fix the bug"'
            ' --disposition ADDRESS --rationale "why"'
        )
        matched = [flags for seg in segments if (flags := ao._parse_ledger_append_flags(seg))]
        assert matched == [{"disposition": "ADDRESS"}]

    def test_path_qualified_append_command_still_matches_via_basename(self):
        """Every other fixture in this class uses a bare command name --
        this is the only case proving os.path.basename()'s path-stripping
        step, not just the literal "review-ledger.sh" comparison, is
        load-bearing."""
        segment = self._segment(
            '~/.claude/scripts/review-ledger.sh append code-review --finding "x" --disposition ADDRESS'
        )
        assert ao._parse_ledger_append_flags(segment) == {"disposition": "ADDRESS"}

    def test_full_six_flag_invocation_shape_from_skill_md_still_parses(self):
        """Mirrors code-review/SKILL.md's own real six-flag order --
        --finding, --disposition, --rationale, --source, --authoring-agent,
        --authoring-effort -- distinct from every other fixture in this
        class, which omits --source/--authoring-effort or uses a different
        flag order."""
        segment = self._segment(
            'review-ledger.sh append code-review --finding "fix the bug" --disposition ADDRESS'
            ' --rationale "why" --source "file.py:10" --authoring-agent code-writer --authoring-effort high'
        )
        assert ao._parse_ledger_append_flags(segment) == {"disposition": "ADDRESS", "authoring_agent": "code-writer"}


class TestIsCleanMarkerWrite:
    def test_marker_write_chained_with_git_commit_is_matched(self):
        assert ao._is_clean_marker_write("marker.sh write code-review && git commit -m wip") is True

    def test_unrelated_bash_command_does_not_match(self):
        assert ao._is_clean_marker_write("git status") is False

    def test_path_qualified_marker_write_still_matches_via_basename(self):
        """Mirrors TestParseLedgerAppendFlags' own path-qualified case --
        every other fixture in this class uses a bare command name."""
        assert ao._is_clean_marker_write("~/.claude/scripts/marker.sh write code-review") is True


class TestIsAppendCallRejected:
    def test_true_when_paired_tool_result_is_error(self):
        records = [_user_msg([{"type": "tool_result", "tool_use_id": "b1", "content": "invalid enum", "is_error": True}])]
        tool_result_index = ao._build_tool_result_index_map(records)
        assert ao._is_append_call_rejected(tool_result_index, "b1") is True

    def test_false_when_paired_tool_result_is_not_error(self):
        records = [_user_msg([_tool_result("b1", "ok")])]
        tool_result_index = ao._build_tool_result_index_map(records)
        assert ao._is_append_call_rejected(tool_result_index, "b1") is False

    def test_false_when_no_paired_tool_result_exists(self):
        assert ao._is_append_call_rejected(ao._build_tool_result_index_map([]), "b1") is False


class TestComputeAuthorOutcomesBuckets:
    """compute_author_outcomes: each of the four per-dispatch buckets."""

    def test_dispatch_is_failure_when_its_attributed_round_has_an_address_finding(self, fake_projects):
        session_id = "sess-1"
        chained_append = _bash_use(
            "b1",
            'cd worktree && review-ledger.sh append code-review --finding "fix the bug"'
            ' --disposition ADDRESS --rationale "why"',
        )
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), chained_append],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 1
        assert result["outcomes"][ao._OUTCOME_PASS] == 0

    def test_dispatch_is_pass_when_its_attributed_round_has_only_defer_findings(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "DEFER")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0

    def test_dispatch_is_pass_when_its_attributed_round_is_clean_with_no_findings(self, fake_projects):
        """A zero-append round -- review found nothing -- is a PASS when the
        marker-write call is present, never inferred from the absence of
        an ADDRESS finding alone."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:10.000Z", content=[_marker_write_use("m1")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1

    def test_dispatch_is_unattributed_when_its_last_round_has_no_append_and_no_marker(self, fake_projects):
        """A round that ran (opened) but left neither an append call nor a
        clean-marker write -- both prose steps skipped -- even when it is
        the session's own last round."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 1

    def test_task_tool_name_dispatch_is_picked_up_identically_to_agent_tool_name(self, fake_projects):
        """pricing._SPAWN_TOOL_NAMES covers both "Agent" and "Task" --
        a dispatch spawned via the "Task" tool must classify the same as
        every other fixture in this class, which all use "Agent"."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z", tool_name="Task"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "DEFER")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0

    def test_dispatch_is_unresolved_when_no_code_review_round_follows_it(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _user_msg("thanks, no review needed", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_UNRESOLVED] == 1


class TestOrderingAndUndecidable:
    def test_dispatch_still_running_when_a_round_opens_attributes_to_the_following_round(self, fake_projects):
        """A round that opens before a still-running dispatch's tool_result
        returns must not claim that dispatch -- attribution is keyed on
        completion index, not start index."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),  # idx0: a1 dispatch starts
            _asst(  # idx1: round1 opens while a1 is still running
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:10.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _dispatch_complete("a1", "2026-08-01T10:00:20.000Z"),  # idx2: a1 completes
            _asst(  # idx3: round2 opens
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:30.000Z",
                content=[_skill_block("s2", "code-review"), _append_use("b1", "DEFER")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        # Attributed to round2 (PASS, its own DEFER call) -- if round1 had
        # wrongly claimed it, round1's own zero-append/no-marker span would
        # instead classify it UNATTRIBUTED.
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 0

    def test_dispatch_with_no_paired_tool_result_is_undecidable(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            # no tool_result for a1 anywhere -- dispatch never completes in this transcript
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert sum(result["outcomes"].values()) == 0
        assert result["data_quality"][ao._DQ_UNDECIDABLE] == 1

    def test_out_of_scope_dispatch_with_no_paired_tool_result_does_not_increment_undecidable(self, fake_projects):
        """_DQ_UNDECIDABLE only tracks in-scope work -- a dispatch that
        falls before a --since cutoff and never completes must not
        inflate the counter."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            # no tool_result for a1 anywhere -- dispatch never completes in this transcript
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        since_ts = corpus._parse_ts("2026-08-01T10:00:30.000Z")
        result = ao.compute_author_outcomes(_session_iter(fake_projects), since_ts=since_ts)
        assert sum(result["outcomes"].values()) == 0
        assert result["data_quality"][ao._DQ_UNDECIDABLE] == 0


class TestInlineAndCoAuthored:
    def test_session_with_no_agent_dispatch_contributes_zero_dispatches(self, fake_projects):
        """A zero-Agent-record session: the round's own append call is real
        (and attributable), but no code-writer dispatch exists to charge
        it to -- the "inline" case."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "ADDRESS", authoring_agent="inline")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert sum(result["outcomes"].values()) == 0
        # declared "inline" against a transcript with zero attributing
        # dispatches (transcript_side "inline" too) -- consistent, not counted.
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_two_dispatches_attributed_to_the_same_round_are_a_co_authored_fan_out(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _dispatch_start("a2", "2026-08-01T10:00:20.000Z"),
            _dispatch_complete("a2", "2026-08-01T10:00:30.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "ADDRESS", authoring_agent="mixed")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 2
        assert result["data_quality"][ao._DQ_CO_AUTHORED_ROUNDS] == 1
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_mixed_declared_against_zero_attributing_dispatches_is_inconsistent(self, fake_projects):
        """declared "mixed" against a round with zero attributing
        code-writer dispatches (transcript_side "inline") -- the
        inconsistent side of the mixed branch; the co-authored-fan-out
        test above covers only the consistent side (two dispatches,
        transcript_side "code-writer")."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "ADDRESS", authoring_agent="mixed")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1

    def test_authoring_agent_inline_declared_against_a_code_writer_round_is_inconsistent(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "DEFER", authoring_agent="inline")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1

    def test_authoring_agent_absent_is_skipped_not_miscounted(self, fake_projects):
        """--authoring-agent entirely absent (not merely empty) -- the
        shape every pre-migration transcript has -- is skipped by the
        cross-check rather than treated as an inconsistency."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "DEFER")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0


class TestSinceFilterBifurcation:
    def test_authoring_agent_inconsistent_uses_unfiltered_dispatch_count_not_since_filtered(self, fake_projects):
        """A --since cutoff landing after the attributing dispatch's own
        completion timestamp but before the round's own span is the only
        shape distinguishing "correctly unfiltered" from "still filtered":
        a1 fails the --since filter (excluded from "Dispatches in scope")
        yet still completed inside the span that attributes it to this
        round, so the cross-check's own transcript_side must still read
        "code-writer", not silently fall back to "inline"."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "DEFER", authoring_agent="inline")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        since_ts = corpus._parse_ts("2026-08-01T10:00:30.000Z")
        result = ao.compute_author_outcomes(_session_iter(fake_projects), since_ts=since_ts)
        assert sum(result["outcomes"].values()) == 0
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1


class TestUnparseableAppend:
    def test_round_with_only_unrelated_bash_commands_does_not_increment_unparseable(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _bash_use("b1", "git status")],
            ),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_UNPARSEABLE_APPEND] == 0

    def test_append_shaped_segment_missing_disposition_increments_unparseable(self, fake_projects):
        session_id = "sess-1"
        bad_append = _bash_use("b1", 'review-ledger.sh append code-review --finding "x" --rationale "why"')
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), bad_append],
            ),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_UNPARSEABLE_APPEND] == 1


class TestRejectedAppend:
    def test_append_call_rejected_by_review_ledger_is_excluded_and_counted(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "ADDRESS")],
            ),
            _user_msg(
                [{"type": "tool_result", "tool_use_id": "b1", "content": "invalid enum", "is_error": True}],
                branch="feat", ts="2026-08-01T10:01:10.000Z",
            ),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 1
        assert result["data_quality"][ao._DQ_REJECTED_APPEND] == 1


class TestCoOccurrencePrecedence:
    """Pins the Failure-definition's stated precedence rule: ADDRESS
    presence decides FAILURE regardless of what else co-occurs in the
    same span."""

    def test_address_append_and_marker_write_in_same_span_is_failure_not_pass(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "ADDRESS"), _marker_write_use("m1")],
            ),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 1
        assert result["outcomes"][ao._OUTCOME_PASS] == 0

    def test_two_append_calls_address_and_defer_in_same_span_is_failure(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "ADDRESS"), _append_use("b2", "DEFER")],
            ),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 1


class TestBuildParserWiring:
    """build_parser() as a testable seam -- the argparse layer without
    executing a subcommand or shelling out to git."""

    def test_author_outcome_defaults(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["author-outcome"])
        assert parsed.agent == "code-writer"
        assert parsed.since is None
        assert parsed.this_repo is False

    def test_author_outcome_this_repo_and_projects_mutually_exclusive(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["author-outcome", "--this-repo", "--projects", "x"])

    def test_author_outcome_agent_and_since_flags_parse(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["author-outcome", "--agent", "staff-sdet", "--since", "14d"])
        assert parsed.agent == "staff-sdet"
        assert parsed.since == "14d"


class TestCmdAuthorOutcomeReport:
    """cmd_author_outcome end-to-end."""

    def _args(self, *, agent: str = "code-writer", since: str | None = None, this_repo: bool = False, projects: str = "*"):
        return type("A", (), {"agent": agent, "since": since, "this_repo": this_repo, "projects": projects})()

    def test_report_prints_header_and_bucket_lines(self, fake_projects, capsys):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst(
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                content=[_skill_block("s1", "code-review"), _append_use("b1", "ADDRESS")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        _mod.cmd_author_outcome(self._args())
        out = capsys.readouterr().out
        assert "AUTHOR OUTCOME SOURCES" in out
        assert "agent=code-writer  window=all time" in out
        assert "Dispatches in scope" in out
        assert "Failure share: 1 of 1 resolved dispatches (100.0%)" in out
        assert "Data quality" in out

    def test_report_prints_zero_resolved_dispatches_without_zero_division(self, fake_projects, capsys):
        """A session with only an UNRESOLVED dispatch (no code-review round
        ever follows it) contributes zero to both failure and passed, so
        resolved computes to 0 -- render._pct_of's pre-existing zero-guard
        renders that as "0 of 0 resolved dispatches (0.0%)"."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _user_msg("thanks, no review needed", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        _mod.cmd_author_outcome(self._args())
        out = capsys.readouterr().out
        assert "Failure share: 0 of 0 resolved dispatches (0.0%)" in out
