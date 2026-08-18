"""Tests for transcript_analysis/reviewer_yield.py (cmd_reviewer_yield)."""
import importlib.util
import json
import signal
import sys
from pathlib import Path

import pytest
from conftest import (
    _agent_use,
    _asst,
    _edit_use,
    _reviewer_yield_args,
    _table_cols,
    _tool_result,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
    _write_use,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# reviewer-yield
# ---------------------------------------------------------------------------


def _n_cited_reviewer_dispatches(
    proj: Path, session_id: str, subagent_type: str, count: int, *, cited_path: str = "src/foo.py",
) -> list[dict]:
    """Build `count` reviewer dispatches of `subagent_type`, each citing
    `cited_path` and each paired with its own tool_result at a distinct,
    increasing timestamp — records to prepend to the session's own list. Each
    dispatch's subagent transcript/meta.json is written as a side effect.
    Sanctioned by test-conventions §6 for the Active=N floor boundary fixture.

    The verdict text ends with a word after `cited_path`, not a period
    directly against it — _CITED_PATH_CANDIDATE_RE's char class includes
    ".", so a trailing sentence period would extract as part of the
    candidate and normalize to a different key than the edit side's clean
    file_path/notebook_path string.
    """
    records: list[dict] = []
    for i in range(count):
        tool_id = f"a{i}"
        dispatch_ts = f"2026-05-19T10:{i:02d}:00.000Z"
        result_ts = f"2026-05-19T10:{i:02d}:30.000Z"
        records.append(_asst("claude-opus-4-7", ts=dispatch_ts, content=[_agent_use(tool_id, subagent_type)]))
        records.append(_user_msg([_tool_result(tool_id, "ok")], ts=result_ts))
        _write_subagent_dispatch(
            proj, session_id, f"agent-{tool_id}", tool_id,
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": f"Found 1 issue in {cited_path} needing a fix"},
            ])],
            agent_type=subagent_type,
        )
    return records


class TestIsReviewerSubagentType:
    def test_recognizes_prefix_and_all_exact_names(self):
        """staff- prefix, plus each of the three exact-name reviewers sharing
        _REVIEWER_EXACT_NAMES with review-trace's own detection."""
        assert _mod._is_reviewer_subagent_type("staff-backend-engineer")
        assert _mod._is_reviewer_subagent_type("ciso-reviewer")
        assert _mod._is_reviewer_subagent_type("comment-discipline-reviewer")
        assert _mod._is_reviewer_subagent_type("skill-fidelity-reviewer")
        assert not _mod._is_reviewer_subagent_type("general-purpose")


class TestReviewerYield:
    def test_comment_discipline_reviewer_joins_and_classifies_zero_finding(self, fake_projects, capsys):
        """comment-discipline-reviewer is a newly-eligible _REVIEWER_EXACT_NAMES
        member (previously recognized by neither review-trace nor
        reviewer-yield) — this exercises its dispatch-to-verdict join
        end-to-end, not just the _is_reviewer_subagent_type predicate."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "comment-discipline-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No comment-discipline concerns**"}])],
            agent_type="comment-discipline-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains="comment-discipline-reviewer",
            row_startswith=True, occurrence=1,
        )
        assert cols["Dispatches"] == "1"
        assert cols["Zero"] == "1"

    def test_no_concerns_verdict_adjacent_to_bold_markers_classified_zero_finding(self, fake_projects, capsys):
        """`\\b` word-boundary anchors match identically next to whitespace or
        markdown punctuation (`**`), so a verdict wrapped in bold markers
        classifies the same as unadorned text — this guards that punctuation
        adjacency, not a "bold" feature of the regex."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="ciso-reviewer", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"
        assert cols["Zero"] == "1"

    def test_request_changes_verdict_classified_findings_found(self, fake_projects, capsys):
        """A 'Request changes' verdict classifies as findings-found, not
        unclassified — the only coverage of `_REVIEWER_REQUEST_CHANGES_RE`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Request changes\n- No test covers the retry path."},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Found"] == "1"
        assert cols["Unclass"] == "0"

    def test_approve_with_concerns_verdict_contributes_zero_to_findings_total(self, fake_projects, capsys):
        """An 'Approve with concerns' verdict carries no derivable count, so it
        lands in findings-found but contributes 0 to the total-findings sum —
        distinct from a numeric 'Found <N> issues' verdict."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "**Approve with concerns**\n- Rotate the leaked token."},
            ])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="ciso-reviewer", row_startswith=True, occurrence=1)
        assert cols["Found"] == "1"
        assert cols["Findings"] == "0"

    def test_singular_issue_verdict_classified_findings_found(self, fake_projects, capsys):
        """'Found 1 issue' (singular) — the loosened regex's singular/plural
        relaxation — classifies as findings-found with 1 total finding."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", ts="2026-05-19T10:00:00.000Z",
                content=[_agent_use("a1", "staff-backend-engineer")],
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Wrote findings to /tmp/x.md. Found 1 issue. Missing null check."},
            ])],
            agent_type="staff-backend-engineer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains="staff-backend-engineer",
            row_startswith=True, occurrence=1,
        )
        assert cols["Found"] == "1"
        assert cols["Findings"] == "1"

    def test_plural_issues_verdict_classified_findings_found(self, fake_projects, capsys):
        """'Found 3 issues' (plural) classifies as findings-found with 3 total findings."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Wrote findings to /tmp/x.md. Found 3 issues. Coverage gaps."},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Found"] == "1"
        assert cols["Findings"] == "3"

    def test_case_insensitive_verdict_matching(self, fake_projects, capsys):
        """An all-caps 'FOUND 2 ISSUES' verdict — the loosened regex's
        case-insensitivity relaxation — still classifies as findings-found."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", ts="2026-05-19T10:00:00.000Z",
                content=[_agent_use("a1", "staff-platform-engineer")],
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "FOUND 2 ISSUES IN THE PIPELINE CONFIG."},
            ])],
            agent_type="staff-platform-engineer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains="staff-platform-engineer",
            row_startswith=True, occurrence=1,
        )
        assert cols["Found"] == "1"
        assert cols["Findings"] == "2"

    def test_non_reviewer_subagent_type_excluded_entirely(self, fake_projects, capsys):
        """A general-purpose dispatch (not in the reviewer set) is excluded from
        aggregation entirely, even though its subagent transcript resolves fine."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "general-purpose")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Found 5 issues."}])],
            agent_type="general-purpose",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "general-purpose" not in out
        assert "No reviewer-agent dispatches found." in out

    def test_dispatch_with_no_matching_meta_json_excluded_not_unclassified(self, fake_projects, capsys):
        """A reviewer-type dispatch with no subagents/*.meta.json at all must not
        crash, and is excluded entirely (not counted as unclassified) — meta.json
        is the only signal that a subagent transcript for the dispatch exists."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "No reviewer-agent dispatches found." in out
        assert "staff-sdet" not in out

    def test_empty_subagent_transcript_classified_unclassified(self, fake_projects, capsys):
        """A resolved subagent .jsonl with zero assistant text blocks (only
        tool_use content, no text) — distinct from the missing-meta.json case
        above — must not crash, and classifies as unclassified."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"
        assert cols["Unclass"] == "1"

    def test_default_run_has_no_project_or_session_fields(self, fake_projects, capsys):
        """Default (no --redact) run: output is aggregate-only per agent type and
        carries no raw project label or session id — proving the schema's
        aggregate-only claim rather than leaving it unverified."""
        _write_jsonl(fake_projects / "distinctive-session-id.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "distinctive-session-id", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "distinctive-session-id" not in out
        assert "testrepo" not in out

    def test_redact_flag_is_true_no_op(self, fake_projects, capsys):
        """--redact produces byte-identical output to the default run, proving
        the flag is genuinely inert given the aggregate-only schema — not just
        documented as such."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args(redact=False))
        default_out = capsys.readouterr().out
        _mod.cmd_reviewer_yield(_reviewer_yield_args(redact=True))
        redacted_out = capsys.readouterr().out
        assert default_out == redacted_out

    def test_since_filter_excludes_out_of_window_dispatch(self, fake_projects, capsys):
        """A dispatch outside the --since window is excluded from the aggregate;
        one inside it is still counted."""
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts=old_ts, content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", ts=new_ts, content=[_agent_use("a2", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args(since="1d"))
        out = capsys.readouterr().out
        assert "ciso-reviewer" not in out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"

    def test_same_agent_type_dispatched_twice_accumulates_not_overwrites(self, fake_projects, capsys):
        """Two dispatches of the same subagent_type within one aggregation run
        sum into the same row instead of the second overwriting the first."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Found 2 issues. Missing checks."}])],
            agent_type="ciso-reviewer",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Found 3 issues. Leaked token."}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="ciso-reviewer", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "2"
        assert cols["Findings"] == "5"

    def test_unreadable_meta_json_files_excluded_and_counted(self, fake_projects, capsys):
        """An invalid-JSON meta.json and a valid-JSON meta.json missing
        toolUseId are both excluded from aggregation (not crashed) and both
        counted in the printed meta-read-errors line — distinct from the
        no-meta.json-at-all exclusion, which is not counted."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
        ])
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-a1.meta.json").write_text("{not valid json")
        (subdir / "agent-a2.meta.json").write_text(
            json.dumps({"agentType": "staff-sdet", "description": "review", "spawnDepth": 1})
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "No reviewer-agent dispatches found." in out
        assert "(2 meta.json files failed to parse, excluded)" in out

    def test_unreadable_meta_json_counted_alongside_a_resolved_table_row(self, fake_projects, capsys):
        """The meta-read-errors count is also reported when other dispatches in
        the same run resolve fine and produce a normal agent-type table — the
        counter line isn't only reachable from the all-excluded empty-table path."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        (subdir / "agent-a2.meta.json").write_text("{not valid json")
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"
        assert "(1 meta.json files failed to parse, excluded)" in out

    def test_malformed_meta_field_types_excluded_and_counted(self, fake_projects, capsys):
        """A toolUseId of the wrong type (int, not str) and a "model" value
        of the wrong type (list, not str/None) are both malformed-meta.json
        cases, excluded and counted the same as invalid JSON or a missing
        toolUseId -- not silently absorbed into the "no matching meta.json"
        path, and not left to reach a caller that would use either value as
        a dict key."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
        ])
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-a1.meta.json").write_text(
            json.dumps({"agentType": "staff-sdet", "description": "d", "toolUseId": 12345, "spawnDepth": 1})
        )
        (subdir / "agent-a2.meta.json").write_text(json.dumps({
            "agentType": "staff-sdet", "description": "d", "toolUseId": "a2",
            "model": ["opus"], "spawnDepth": 1,
        }))
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "No reviewer-agent dispatches found." in out
        assert "(2 meta.json files failed to parse, excluded)" in out
    # -----------------------------------------------------------------
    # Table 2: cited-path edit overlap
    # -----------------------------------------------------------------

    def test_cited_dispatch_with_no_subsequent_edit_stays_cited_not_active(self, fake_projects, capsys):
        """A dispatch with an extracted citation but no later edit anywhere
        in the session stays in Cited and is absent from Active — the
        Cited/Active denominator arithmetic, asserted on specific counts.
        Active=0 also pins the zero-Active sentinel, not a ZeroDivisionError."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"
        assert cols["Rate"] == "insufficient"

    def test_zero_extraction_dispatch_stays_in_dispatches_absent_from_cited(self, fake_projects, capsys):
        """A dispatch whose verdict text yields zero extracted citations
        stays counted in table 1's Dispatches but contributes nothing to
        table 2's Cited — not entered as a citation event."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols1 = _table_cols(
            out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1,
        )
        assert cols1["Dispatches"] == "1"
        cols2 = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols2["Cited"] == "0"

    def test_findings_found_and_zero_finding_rows_are_adjacent_for_the_same_agent_type(self, fake_projects, capsys):
        """Reviewer-major, bucket-minor ordering: an agent type's two
        verdict-bucket rows in table 2 sit next to each other, not separated
        by another agent type's row."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
            _user_msg([_tool_result("a2", "ok")], ts="2026-05-19T11:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        lines = out.splitlines()
        found_idx = next(i for i, ln in enumerate(lines) if "staff-sdet" in ln and "findings-found" in ln)
        zero_idx = next(i for i, ln in enumerate(lines) if "staff-sdet" in ln and "zero-finding" in ln)
        assert abs(found_idx - zero_idx) == 1

    def test_unclassified_bucket_row_prints_excluded_sentinels(self, fake_projects, capsys):
        """The unclassified bucket is not scored — its row prints the
        literal 'excluded' sentinel for Cited/Active/Edited/Rate, asserted
        by bucket name rather than left implicit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Reviewed the code."}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "unclassified"), occurrence=2,
        )
        assert cols["Cited"] == "excluded"
        assert cols["Active"] == "excluded"
        assert cols["Edited"] == "excluded"
        assert cols["Rate"] == "excluded"

    def test_active_below_floor_of_ten_renders_insufficient(self, fake_projects, capsys):
        """Active=9 (one below the N=10 floor) renders 'insufficient', not a
        computed percentage."""
        records = _n_cited_reviewer_dispatches(fake_projects, "sess", "staff-sdet", 9)
        records.append(
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")])
        )
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "9"
        assert cols["Rate"] == "insufficient"

    def test_active_at_floor_of_ten_renders_exact_rate(self, fake_projects, capsys):
        """Active=10 (the N=10 floor) with Edited=10 renders an exact
        100.0% rate, not merely a non-'insufficient' value — pins the >=
        boundary, not just != 'insufficient'."""
        records = _n_cited_reviewer_dispatches(fake_projects, "sess", "staff-sdet", 10)
        records.append(
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")])
        )
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "10"
        assert cols["Edited"] == "10"
        assert cols["Rate"] == "100.0%"

    def test_edit_preceding_dispatch_return_does_not_count(self, fake_projects, capsys):
        """An edit timestamped after the dispatch started but before its own
        tool_result returned must not count toward Active — pins the
        threshold against the return time specifically, not dispatch start
        (an edit before dispatch start would pass this check too loosely to
        tell the two thresholds apart)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:15.000Z", content=[_edit_use("e1", path="src/foo.py")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_edit_at_exact_tool_result_timestamp_does_not_count(self, fake_projects, capsys):
        """An edit timestamped exactly at the dispatch's tool_result
        timestamp must not count — the ordering rule is strict >, not >=."""
        ts = "2026-05-19T10:00:30.000Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts=ts),
            _asst("claude-opus-4-7", ts=ts, content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "0"

    def test_edit_one_second_after_tool_result_counts(self, fake_projects, capsys):
        """An edit timestamped one second after the dispatch's tool_result
        does count — the earliest instant strict > admits."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:31.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_unparseable_tool_result_timestamp_excludes_active_not_cited(self, fake_projects, capsys):
        """A tool_result with an unparseable timestamp leaves the dispatch's
        Active/Edited ordering undecidable — excluded from Active regardless
        of a later edit — but does not affect Cited."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="not-a-timestamp"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_dispatch_with_no_paired_tool_result_excludes_active_not_cited(self, fake_projects, capsys):
        """A dispatch whose Agent tool_use has no paired tool_result at all
        is excluded from Active regardless of a later edit, but not from
        Cited."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_unreadable_subagent_transcript_excluded_from_cited_and_counted_in_read_error_line(
        self, fake_projects, capsys
    ):
        """A reviewer dispatch whose meta.json resolves but whose .jsonl is
        unreadable is excluded from Cited (never entered as a legitimate
        zero) and counted in a printed read-error line — distinguishable
        from the zero-extraction case, which prints no such line."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        (fake_projects / "sess" / _mod.SUBAGENT_SUBDIR / "agent-a1.jsonl").unlink()
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols1 = _table_cols(
            out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1,
        )
        assert cols1["Unclass"] == "1"
        assert "(1 reviewer transcripts failed to read, excluded from Cited)" in out

    def test_parent_write_edit_counts_toward_active_and_edited(self, fake_projects, capsys):
        """A parent main-thread Write (not just Edit) to the cited path
        counts toward Active/Edited — Write is part of _CODE_WRITE_TOOLS."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _write_use("w1", "new content", path="src/foo.py"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_parent_multiedit_counts_toward_edited(self, fake_projects, capsys):
        """A parent MultiEdit (single file_path, plus an edits list) counts
        toward Edited alongside plain Edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                {"type": "tool_use", "id": "m1", "name": "MultiEdit", "input": {
                    "file_path": "src/foo.py", "edits": [{"old_string": "a", "new_string": "b"}],
                }},
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Edited"] == "1"

    def test_notebook_edit_counts_via_notebook_path_fallback(self, fake_projects, capsys):
        """NotebookEdit carries notebook_path instead of file_path — the
        edit index falls back to it rather than missing notebook edits
        entirely."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                {"type": "tool_use", "id": "n1", "name": "NotebookEdit", "input": {
                    "notebook_path": "nb/analysis.ipynb",
                }},
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in nb/analysis.ipynb needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Edited"] == "1"

    def test_subagent_authored_edit_never_counts_under_parent_only_index(self, fake_projects, capsys):
        """Pins the shipped parent-only-index behavior: an edit made inside a
        code-writer subagent transcript does not count toward Active/Edited
        even though it followed the reviewer dispatch. This does not exercise
        a subagent_type-based reviewer-write exclusion — no such mechanism
        exists in the shipped code (removed by the cost-gate fallback); the
        edit index simply never reads subagent transcripts at all."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("cw1", "code-writer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-cw1", "cw1",
            [_asst("claude-sonnet-4-6", ts="2026-05-19T11:05:00.000Z", content=[_edit_use("e1", path="src/foo.py")])],
            agent_type="code-writer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "0"
        assert cols["Edited"] == "0"

    def test_sibling_reviewer_findings_write_never_counts_under_parent_only_index(self, fake_projects, capsys):
        """Pins the shipped parent-only-index behavior: a sibling reviewer
        dispatched after a zero-finding dispatch writes only its own findings
        file, and that write does not satisfy Active for the zero-finding
        dispatch. This does not exercise a subagent_type-based reviewer-write
        exclusion — no such mechanism exists in the shipped code (removed by
        the cost-gate fallback); the edit index simply never reads subagent
        transcripts at all, reviewer or otherwise."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
            _user_msg([_tool_result("a2", "ok")], ts="2026-05-19T11:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "**No concerns** src/foo.py is clean"},
            ])],
            agent_type="ciso-reviewer",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", ts="2026-05-19T11:05:00.000Z", content=[
                _write_use("w1", "No issues found.", path="/scratch/sibling-findings.md"),
                {"type": "text", "text": "**No testing concerns**"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("ciso-reviewer", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_dispatch_own_write_target_excluded_from_cited(self, fake_projects, capsys):
        """The dispatch's own findings-file Write target, later echoed in
        the reviewer's own output, is excluded from Cited — a
        path-normalized set-membership check against the reviewer's own
        recorded Write target, not free-text prose matching against the
        dispatching prompt (which would risk excluding files the prompt
        also happens to name)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "No issues found.", path="/scratch/my-findings.md"),
                {"type": "text", "text": "**No concerns** Findings written to /scratch/my-findings.md, nothing else found"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "0"

    def test_prompt_named_file_legitimately_cited_in_findings_is_not_excluded(self, fake_projects, capsys):
        """Regression: a file named in the dispatching prompt ('review
        src/foo.py') that the reviewer then legitimately cites in its own
        findings must still count as Cited — a prompt routinely names the
        very files under review, so keying the self-reference exclusion off
        prompt text (rather than the reviewer's own Write target) would
        silently drop exactly the citations most likely to be real."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[
                _agent_use(
                    "a1", "staff-sdet",
                    prompt="Review the diff in src/foo.py and write findings to /scratch/findings.md",
                ),
            ]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "Missing null check.", path="/scratch/findings.md"),
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"

    def test_citation_normalizes_against_subagent_own_cwd_not_parent_cwd(self, fake_projects, capsys):
        """A relative citation in the reviewer's own output normalizes
        against the reviewer subagent's own transcript cwd, not the
        dispatching parent's — the two can diverge under an
        isolation:worktree reviewer dispatch, which this repo's own
        CLAUDE.md sanctions specifically."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            {**_asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
             "cwd": "/parent/wrong/cwd"},
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            {**_asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _edit_use("e1", path="/repo/src/foo.py"),
             ]), "cwd": "/parent/wrong/cwd"},
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [{**_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
             ]), "cwd": "/repo"}],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Edited"] == "1"

    def test_plan_file_citations_excluded_from_cited_both_home_and_repo_forms(self, fake_projects, capsys):
        """A cited plan file under ~/.claude/plans/ or an in-repo
        .claude/plans/ is excluded — a /plan-review dispatch routinely
        cites the very plan the parent session then edits, a guaranteed
        self-match with no fix-work signal."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{
                "type": "text",
                "text": "**No concerns** See ~/.claude/plans/foo.md and .claude/plans/bar.md for context",
            }])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "0"

    def test_multi_path_citation_with_one_edited_counts_dispatch_once(self, fake_projects, capsys):
        """A dispatch citing two paths, only one of which is later edited,
        still counts once in Cited/Active/Edited — not once per cited
        path."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 2 issues in src/foo.py and src/bar.py needing review"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_citation_from_write_blob_content_is_recognized(self, fake_projects, capsys):
        """A path cited only in the reviewer's own Write blob (its findings
        body), not in the last assistant text, is still recognized — both
        citation sources are scanned, not just the final text block."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "Reviewed src/foo.py, no issues found."),
                {"type": "text", "text": "**No concerns**"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "1"

    def test_path_cited_in_both_text_and_write_blob_counts_once(self, fake_projects, capsys):
        """A path cited in both the last assistant text and the reviewer's
        own Write blob dedupes to one citation — Cited/Active/Edited stay
        dispatch-level, not inflated by the overlap between the two
        sources."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "Reviewed src/foo.py, confirmed the issue."),
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_table_two_output_deterministic_across_two_runs(self, fake_projects, capsys):
        """Table 2's output is byte-identical across two runs over the same
        corpus — reviewer-major/bucket-minor ordering is name-sorted, not
        dependent on dict-iteration order."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "ciso-reviewer")]),
            _user_msg([_tool_result("a2", "ok")], ts="2026-05-19T11:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        first_out = capsys.readouterr().out
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        second_out = capsys.readouterr().out
        assert first_out == second_out

    def test_cited_and_edited_paths_never_appear_in_output(self, fake_projects, capsys):
        """A distinctive sentinel path, cited and later edited, must not
        reach stdout or stderr — cited-path candidates are held only as
        sha256 digests, so no path can print by construction. Scoped to the
        table bodies: the pre-existing --projects scope-header leak (ledger
        row O) is a separate, unfixed channel this plan does not fix."""
        sentinel_path = "SENTINEL-ROOT/SENTINEL-PROJ/x.py"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _edit_use("e1", path=sentinel_path),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": f"Found 1 issue in {sentinel_path} needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        captured = capsys.readouterr()
        assert sentinel_path not in captured.out
        assert sentinel_path not in captured.err

    def test_cited_via_write_blob_and_edited_paths_never_appear_in_output(self, fake_projects, capsys):
        """The same non-leakage guarantee as
        test_cited_and_edited_paths_never_appear_in_output, but with the
        sentinel path cited only through a reviewer's own Write blob
        (input.content), not the last assistant text — both citation-
        extraction code paths must not leak a raw path."""
        sentinel_path = "SENTINEL-ROOT/SENTINEL-PROJ/y.py"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _edit_use("e1", path=sentinel_path),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", f"Reviewed {sentinel_path}, confirmed the issue."),
                {"type": "text", "text": "**No concerns**"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        captured = capsys.readouterr()
        assert sentinel_path not in captured.out
        assert sentinel_path not in captured.err


class TestExtractCitedPaths:
    """_extract_cited_paths(text) -> set[str]: a pure tokenizer over one
    bounded, length-capped character class. Deliberately unselective — a
    bare word matches too — so these tests document what the function
    returns *before* _normalize_cited_path filters it, not "real paths only."
    """

    def test_extracts_a_slash_containing_candidate_verbatim(self):
        result = _mod._extract_cited_paths("claude/.claude/scripts/transcript-analysis.py:2549")
        assert result == {"claude/.claude/scripts/transcript-analysis.py:2549"}

    def test_extracts_a_tilde_path_without_expanding_it(self):
        """Extraction is purely lexical tokenization — ~-expansion is
        _normalize_cited_path's job, not this function's."""
        result = _mod._extract_cited_paths("~/.claude/plans/x.md")
        assert result == {"~/.claude/plans/x.md"}

    def test_bare_prose_words_are_extracted_too(self):
        """No `/` or `.` is required by the regex — separator filtering
        happens downstream in _normalize_cited_path's step 2, not here."""
        result = _mod._extract_cited_paths("no findings here")
        assert result == {"no", "findings", "here"}

    def test_real_path_recovered_from_surrounding_prose(self):
        text = "Reviewed the diff; see claude/.claude/scripts/transcript-analysis.py:2455 for the join."
        result = _mod._extract_cited_paths(text)
        assert "claude/.claude/scripts/transcript-analysis.py:2455" in result

    def test_non_ascii_path_extracted_verbatim(self):
        """_CITED_PATH_CANDIDATE_RE's \\w is Unicode-by-default (no re.ASCII
        flag), relied on deliberately so a non-ASCII path segment is not
        split at the boundary of its non-ASCII characters."""
        result = _mod._extract_cited_paths("see café/日本語/foo.py for the fix")
        assert "café/日本語/foo.py" in result

    def test_empty_text_returns_empty_set(self):
        assert _mod._extract_cited_paths("") == set()

    def test_candidate_longer_than_the_cap_splits_into_bounded_chunks(self):
        """A single unbroken run longer than _CITED_PATH_CANDIDATE_MAX_CHARS
        is not dropped or truncated silently — the greedy bounded quantifier
        emits consecutive capped matches that together cover the whole run."""
        max_chars = _mod._CITED_PATH_CANDIDATE_MAX_CHARS
        run = "a" * (max_chars + 50)
        result = _mod._extract_cited_paths(run)
        assert result == {"a" * max_chars, "a" * 50}

    def test_adversarial_slash_run_completes_under_one_second(self):
        """A 100 KB non-matching slash-heavy line (no terminating token) must
        not trigger catastrophic backtracking. _CITED_PATH_CANDIDATE_RE's
        flat character class is linear-time regardless of input shape, unlike
        a nested-quantifier "(?:[\\w.-]+/)+[\\w.-]+" pattern, which hangs on
        this exact input. A hard deadline is required rather than a
        post-hoc wall-clock assertion: catastrophic backtracking hangs
        rather than returns slowly, so a measured-after-the-fact assertion
        can never fail on the failure mode it exists to catch."""
        if not hasattr(signal, "SIGALRM"):
            pytest.skip("signal.alarm is POSIX-only")
        adversarial_input = "a/" * 50_000  # 100,000 chars, no terminating token

        def _raise_timeout(signum, frame):
            raise TimeoutError("_extract_cited_paths did not complete within 1 second")

        previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
        signal.alarm(1)
        try:
            _mod._extract_cited_paths(adversarial_input)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)


class TestNormalizeCitedPath:
    """_normalize_cited_path(candidate, cwd) -> key | None: the six ordered,
    lexical-only normalization steps that turn a raw extracted candidate
    into (or discard it from) the edit-index join key."""

    def _key(self, path: str) -> str:
        """The same digest _normalize_cited_path computes, for expected-value
        construction — not a re-implementation of any normalization step."""
        return _mod.hashlib.sha256(path.encode()).hexdigest()[:16]

    def test_tilde_path_expands_against_home_before_relative_resolution(self, monkeypatch, tmp_path):
        """Pins step 3 (~-expansion) running before step 4 (relative-path
        resolution): if step 4 ran first, '~/.claude/plans/x.md' would be
        joined onto `cwd` unexpanded rather than resolved against $HOME."""
        home = tmp_path / "home" / "reviewer"
        monkeypatch.setenv("HOME", str(home))
        result = _mod._normalize_cited_path("~/.claude/plans/x.md", cwd="/repo/unrelated/cwd")
        assert result == self._key(f"{home}/.claude/plans/x.md")

    def test_unexpandable_other_user_tilde_is_discarded(self, monkeypatch, tmp_path):
        """A '~otheruser/...' candidate is discarded outright, not resolved
        via a pwd.getpwnam directory-service lookup — expanduser leaves it
        starting with '~' when the user doesn't exist locally."""
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "reviewer"))
        result = _mod._normalize_cited_path(
            "~definitely-not-a-real-account-xyz/notes.md", cwd="/repo"
        )
        assert result is None

    def test_private_tmp_collapses_to_tmp(self):
        result = _mod._normalize_cited_path("/private/tmp/scratch/report.md", cwd="/repo")
        assert result == self._key("/tmp/scratch/report.md")

    def test_trailing_line_suffix_stripped(self):
        with_line = _mod._normalize_cited_path("src/foo.py:42", cwd="/repo")
        without_line = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert with_line == without_line == self._key("/repo/src/foo.py")

    def test_trailing_line_col_suffix_stripped(self):
        with_line_col = _mod._normalize_cited_path("src/foo.py:42:7", cwd="/repo")
        without_suffix = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert with_line_col == without_suffix == self._key("/repo/src/foo.py")

    def test_relative_and_absolute_citations_of_the_same_file_match(self):
        relative = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        absolute = _mod._normalize_cited_path("/repo/src/foo.py", cwd="/anywhere")
        assert relative == absolute == self._key("/repo/src/foo.py")

    def test_worktree_rooted_absolute_path_matches_plain_repo_relative(self):
        """A worktree-rooted absolute citation normalizes to the same key as
        a plain repo-relative one once the worktree-prefix segment is
        stripped — that's the join key's whole purpose."""
        worktree_rooted = _mod._normalize_cited_path(
            "/repo/.claude/worktrees/gh558-branch/src/foo.py", cwd="/irrelevant"
        )
        plain_relative = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert worktree_rooted == plain_relative == self._key("/repo/src/foo.py")

    def test_absolute_citation_matches_worktree_rooted_edit_on_a_different_branch(self):
        """Worktree-prefix stripping is branch-name-agnostic by construction
        (`.claude/worktrees/[^/]+/` matches any single branch segment): an
        edit made in a different branch's worktree of the *same* repo
        normalizes to the same key as a plain absolute citation of that
        repo's file — the join key carries no branch identity."""
        absolute_citation = _mod._normalize_cited_path("/repo/src/foo.py", cwd="/repo")
        worktree_edit = _mod._normalize_cited_path(
            "src/foo.py", cwd="/repo/.claude/worktrees/some-other-branch"
        )
        assert absolute_citation == worktree_edit == self._key("/repo/src/foo.py")

    def test_two_repos_sharing_a_relative_suffix_do_not_match(self):
        repo_a = _mod._normalize_cited_path("src/foo.py", cwd="/projects/repo-a")
        repo_b = _mod._normalize_cited_path("src/foo.py", cwd="/projects/repo-b")
        assert repo_a != repo_b

    def test_nested_worktree_stripped_to_fixpoint(self):
        """An isolation:worktree agent spawned under a worktree-anchored
        parent leaves two '.claude/worktrees/<branch>/' segments in the
        path; both must be stripped, not just the first."""
        nested = _mod._normalize_cited_path(
            "/repo/.claude/worktrees/outer-branch/.claude/worktrees/inner-agent/src/foo.py",
            cwd="/irrelevant",
        )
        assert nested == self._key("/repo/src/foo.py")

    def test_slash_containing_branch_slug_under_strips_to_first_segment(self):
        """A hand-created branch named 'docs/x' (violating this repo's own
        single-segment branch-slug convention) is not losslessly decidable
        from the path alone with zero filesystem access — the normalizer
        takes only the first segment ('docs') as the branch, leaving 'x/'
        as an unstripped leftover. Documents the bias; does not crash."""
        result = _mod._normalize_cited_path(
            "/repo/.claude/worktrees/docs/x/src/foo.py", cwd="/irrelevant"
        )
        assert result == self._key("/repo/x/src/foo.py")

    def test_dotdot_resolves_against_unstripped_cwd_before_worktree_stripping(self):
        """'../../../.venv/bin/pytest' (this repo's own CLAUDE.md idiom) means
        three levels above the *worktree* — resolving it against the
        unstripped `cwd` (not a pre-stripped one) is what makes that true."""
        result = _mod._normalize_cited_path(
            "../../../.venv/bin/pytest", cwd="/repo/.claude/worktrees/gh558-branch"
        )
        assert result == self._key("/repo/.venv/bin/pytest")

    def test_bare_filename_with_no_directory_separator_is_rejected(self):
        assert _mod._normalize_cited_path("SKILL.md", cwd="/repo") is None

    def test_no_resolvable_repo_still_normalizes(self):
        """A cwd with no .claude/worktrees marker at all (a plain, non-worktree
        checkout) is not an error case — the function needs no repo
        detection, only lexical joining."""
        result = _mod._normalize_cited_path("src/foo.py", cwd="/srv/plain-checkout")
        assert result == self._key("/srv/plain-checkout/src/foo.py")

    def test_non_ascii_path_round_trips_through_extraction_and_normalization(self):
        """A non-ASCII path (café/日本語-style) survives extraction and
        normalization to a stable key — pins that a future 'safety' narrowing
        of _CITED_PATH_CANDIDATE_RE to ASCII-only would silently break
        non-ASCII paths with no test catching it."""
        candidates = _mod._extract_cited_paths("see café/日本語/foo.py for the fix")
        candidate = next(c for c in candidates if "/" in c)
        result = _mod._normalize_cited_path(candidate, cwd="/repo")
        assert result == self._key("/repo/café/日本語/foo.py")


class TestBuildToolResultTsMap:
    """_build_tool_result_ts_map(records, since_ts) -> {tool_use_id: timestamp}:
    the tool_result side of reviewer-yield's Active/Edited ordering join."""

    def test_tool_result_on_user_type_record_maps_to_its_own_timestamp(self):
        ts = "2026-05-19T10:00:30.000Z"
        records = [_user_msg([_tool_result("a1", "ok")], ts=ts)]
        result = _mod._build_tool_result_ts_map(records, since_ts=None)
        assert result == {"a1": _mod._parse_ts(ts)}

    def test_since_excludes_out_of_window_tool_result(self):
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        records = [
            _user_msg([_tool_result("old", "ok")], ts=old_ts),
            _user_msg([_tool_result("new", "ok")], ts=new_ts),
        ]
        since_ts = _mod._parse_ts("2050-01-01T00:00:00.000Z")
        result = _mod._build_tool_result_ts_map(records, since_ts)
        assert result == {"new": _mod._parse_ts(new_ts)}

    def test_unparseable_timestamp_omitted(self):
        records = [_user_msg([_tool_result("a1", "ok")], ts="not-a-timestamp")]
        result = _mod._build_tool_result_ts_map(records, since_ts=None)
        assert result == {}


class TestIndexParentEdits:
    """_index_parent_edits(records, since_ts) -> {normalized_key: latest_ts}:
    the parent-main-thread edit side of reviewer-yield's overlap join."""

    def test_write_tool_use_produces_keyed_entry(self):
        ts = "2026-05-19T11:00:00.000Z"
        records = [
            {**_asst("claude-opus-4-7", ts=ts, content=[_write_use("w1", "content", path="src/foo.py")]),
             "cwd": "/repo"},
        ]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_edit_tool_use_produces_keyed_entry(self):
        ts = "2026-05-19T11:00:00.000Z"
        records = [
            {**_asst("claude-opus-4-7", ts=ts, content=[_edit_use("e1", path="src/foo.py")]), "cwd": "/repo"},
        ]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_multiedit_tool_use_produces_keyed_entry(self):
        ts = "2026-05-19T11:00:00.000Z"
        multiedit = {"type": "tool_use", "id": "m1", "name": "MultiEdit", "input": {
            "file_path": "src/foo.py", "edits": [{"old_string": "a", "new_string": "b"}],
        }}
        records = [{**_asst("claude-opus-4-7", ts=ts, content=[multiedit]), "cwd": "/repo"}]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_notebook_edit_tool_use_produces_keyed_entry(self):
        """NotebookEdit carries notebook_path, not file_path — the index's
        _code_write_target_path fallback."""
        ts = "2026-05-19T11:00:00.000Z"
        notebook_edit = {"type": "tool_use", "id": "n1", "name": "NotebookEdit", "input": {
            "notebook_path": "nb/analysis.ipynb",
        }}
        records = [{**_asst("claude-opus-4-7", ts=ts, content=[notebook_edit]), "cwd": "/repo"}]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("nb/analysis.ipynb", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_since_excludes_out_of_window_edit(self):
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        records = [
            {**_asst("claude-opus-4-7", ts=old_ts, content=[_edit_use("e1", path="src/old.py")]), "cwd": "/repo"},
            {**_asst("claude-opus-4-7", ts=new_ts, content=[_edit_use("e2", path="src/new.py")]), "cwd": "/repo"},
        ]
        since_ts = _mod._parse_ts("2050-01-01T00:00:00.000Z")
        result = _mod._index_parent_edits(records, since_ts)
        assert list(result) == [_mod._normalize_cited_path("src/new.py", cwd="/repo")]


class TestReviewerYieldCitedKeys:
    """_reviewer_yield_cited_keys(last_assistant_text, write_content_blobs, cwd,
    self_ref_keys) -> set[key]: the citation-extraction join key set for one
    reviewer dispatch."""

    def test_text_channel_candidate_surfaces(self):
        result = _mod._reviewer_yield_cited_keys(
            "Found 1 issue in src/foo.py needing a fix", [], cwd="/repo", self_ref_keys=set(),
        )
        assert result == {_mod._normalize_cited_path("src/foo.py", cwd="/repo")}

    def test_blob_channel_candidate_surfaces(self):
        result = _mod._reviewer_yield_cited_keys(
            "", ["Reviewed src/foo.py, no issues found."], cwd="/repo", self_ref_keys=set(),
        )
        assert result == {_mod._normalize_cited_path("src/foo.py", cwd="/repo")}

    def test_self_ref_key_excluded(self):
        self_ref_key = _mod._normalize_cited_path("/scratch/findings.md", cwd="/repo")
        result = _mod._reviewer_yield_cited_keys(
            "Findings written to /scratch/findings.md", [], cwd="/repo", self_ref_keys={self_ref_key},
        )
        assert result == set()

    def test_plan_file_candidate_excluded(self):
        result = _mod._reviewer_yield_cited_keys(
            "See .claude/plans/foo.md for context", [], cwd="/repo", self_ref_keys=set(),
        )
        assert result == set()

    def test_same_path_in_both_channels_dedupes_to_one_key(self):
        result = _mod._reviewer_yield_cited_keys(
            "Found 1 issue in src/foo.py needing a fix",
            ["Reviewed src/foo.py, confirmed the issue."],
            cwd="/repo", self_ref_keys=set(),
        )
        assert result == {_mod._normalize_cited_path("src/foo.py", cwd="/repo")}


class TestDispatchSelfReferenceKeys:
    """_dispatch_self_reference_keys(write_target_paths, transcript_cwd) ->
    set[key]: the reviewer's own Write-target self-reference exclusion set."""

    def test_normalizes_each_write_target_path(self):
        result = _mod._dispatch_self_reference_keys(
            ["/scratch/findings.md", "src/foo.py"], transcript_cwd="/repo",
        )
        assert result == {
            _mod._normalize_cited_path("/scratch/findings.md", cwd="/repo"),
            _mod._normalize_cited_path("src/foo.py", cwd="/repo"),
        }

    def test_empty_write_target_paths_returns_empty_set(self):
        assert _mod._dispatch_self_reference_keys([], transcript_cwd="/repo") == set()


