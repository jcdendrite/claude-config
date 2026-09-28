"""Tests for transcript_analysis/audit_routing.py (cmd_audit_routing): per-turn Opus
routing-class breakdown, judgment spans, and --redact."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from ._audit_routing_helpers import _extract_corpus_class_tokens
from .conftest import (
    _agent_use,
    _asst,
    _audit_routing_args,
    _exit_plan_mode,
    _opus,
    _priced_opus,
    _skill_use,
    _thinking_block,
    _two_declared_roots,
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


def _extract_sonnet_tier_dollar_estimate(out: str) -> float:
    """Parse the dollar-weighted 'Sonnet-tier estimate: $N' headline.

    The dollar headline prints first, ahead of the token-based secondary
    diagnostic line that reuses the same 'Sonnet-tier estimate:' label — this
    regex only matches the '$'-prefixed form, so it can't accidentally read
    the token line.
    """
    match = re.search(r"Sonnet-tier estimate: \$([\d,]+\.\d{2})", out)
    assert match is not None, "dollar Sonnet-tier estimate line not found in output"
    return float(match.group(1).replace(",", ""))


class TestAuditRouting:
    def test_basic_per_class_routing(self, fake_projects, capsys):
        """One Opus turn of each class; corpus aggregate totals must match synthesized usage."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # orchestration: Agent tool_use
            _opus([_agent_use("a1", "code-writer")], out=100),
            # judgment: Skill tool_use (opens span, turn itself is judgment)
            _opus([_skill_use("s1", "code-review")], out=200),
            # user turn resets span
            _user_msg("hi", branch="main"),
            # code-write: Edit tool_use (span closed)
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            # code-read: only Read tool_use
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
            # pure-thinking: thinking block, no tool_use
            _opus([_thinking_block()], out=500),
            # other: no tool_use, no thinking
            _opus([], out=600),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert _extract_corpus_class_tokens(out, "orchestration") == 100
        assert _extract_corpus_class_tokens(out, "judgment") == 200
        assert _extract_corpus_class_tokens(out, "code-write") == 300
        assert _extract_corpus_class_tokens(out, "code-read") == 400
        assert _extract_corpus_class_tokens(out, "pure-thinking") == 500
        assert _extract_corpus_class_tokens(out, "other") == 600

    def test_judgment_span_covers_subsequent_read_and_write_turns(self, fake_projects, capsys):
        """Skill invocation opens a span; Read/Write turns inside span → judgment, not code-read/write.
        User turn resets span; Edit turn after reset → code-write."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # User turn (normal) — no plan-mode text
            _user_msg("start", branch="main"),
            # Skill invocation: opens span; turn itself → judgment
            _opus([_skill_use("s1", "code-review")], out=10),
            # Read turn inside span → judgment (not code-read)
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=20),
            # Write turn inside span → judgment (not code-write)
            _opus([{"type": "tool_use", "id": "w1", "name": "Write", "input": {}}], out=30),
            # User turn resets span
            _user_msg("continue", branch="main"),
            # Edit turn after span closed → code-write
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=40),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = skill turn (10) + read inside span (20) + write inside span (30) = 60
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # code-write = edit after reset = 40
        assert _extract_corpus_class_tokens(out, "code-write") == 40
        # code-read = 0 (the Read turn was inside the span → judgment)
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_plan_mode_span_and_exit_plan_mode(self, fake_projects, capsys):
        """Plan-mode activation → subsequent turns are judgment until ExitPlanMode.
        ExitPlanMode turn itself is still judgment; Edit after exit → code-write."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # User turn that activates plan mode
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            # Read turn inside plan-mode → judgment
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=50),
            # ExitPlanMode turn: still in plan-mode span → judgment; clears flag for next turn
            _opus([_exit_plan_mode("epm1")], out=75),
            # Edit turn after plan-mode cleared → code-write
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=90),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = read (50) + ExitPlanMode (75) = 125
        assert _extract_corpus_class_tokens(out, "judgment") == 125
        # code-write = edit after exit = 90
        assert _extract_corpus_class_tokens(out, "code-write") == 90
        # code-read = 0
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_redact_flag_anonymizes_project_names(self, fake_projects, capsys):
        """--redact replaces project dir names with private-project-1/2/…; claude-config kept."""
        # Session in the default project (-home-user-testrepo → 'home/testrepo' via derivation)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=100),
        ])
        # A second project whose derived name is 'claude-config'
        proj_cc = fake_projects.parent / "-home-user-claude-config"
        proj_cc.mkdir(parents=True)
        _write_jsonl(proj_cc / "sess.jsonl", [
            _opus([_agent_use("a2", "code-writer")], out=200),
        ])
        args = _audit_routing_args(redact=True)
        _mod.audit_routing.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # claude-config must appear without redaction
        assert "claude-config" in out
        # The default project name should NOT appear verbatim (redacted)
        # (the derived label for -home-user-testrepo is 'user/testrepo' or 'testrepo')
        # We just verify that private-project labels appear in the output
        assert "private-project-" in out

    def test_since_filter_excludes_out_of_window_turns(self, fake_projects, capsys):
        """--since filter: turn outside window excluded; only in-window turn appears in aggregate."""
        old_ts = "2020-01-01T00:00:00.000Z"   # far in the past — always out-of-window
        new_ts = "2099-12-31T00:00:00.000Z"   # far in the future — always in-window
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=111, ts=old_ts),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=222, ts=new_ts),
        ])
        # Use "1d" window: old_ts is excluded, new_ts is included
        args = _audit_routing_args(since="1d")
        _mod.audit_routing.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # Only the new_ts turn (code-write, 222) should appear
        assert _extract_corpus_class_tokens(out, "code-write") == 222
        assert _extract_corpus_class_tokens(out, "orchestration") == 0

    def test_no_opus_turns_produces_empty_aggregate(self, fake_projects, capsys):
        """Session with only Sonnet turns produces no rows and zero corpus totals."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00Z",
                  content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}]),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Corpus aggregate section must be present but all classes are zero
        assert "Corpus aggregate" in out
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_sonnet_tier_estimate_printed(self, fake_projects, capsys):
        """Sonnet-tier estimate line appears and reflects code-write + code-read total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert "Sonnet-tier estimate: 700" in out

    def test_sonnet_tier_estimate_dollar_headline_printed(self, fake_projects, capsys):
        """Dollar-weighted Sonnet-tier headline reflects code-write + code-read priced spend,
        hand-computed against claude-opus-5's base $5/MTok rate (output at its 5x multiplier)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced_opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            _priced_opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Two turns, each input=50 (100 total); output 300+400=700; both code-write/code-read
        # so this is 100% of priced spend in the window.
        expected_dollars = (100 / 1_000_000 * 5.00) + (700 / 1_000_000 * 25.00)
        # abs tolerance matches the headline's own 2-decimal-place ($.NN) display rounding.
        assert _extract_sonnet_tier_dollar_estimate(out) == pytest.approx(expected_dollars, abs=0.005)
        assert "= 100% of priced Opus spend in this window" in out

    def test_dollar_headline_mixed_priced_and_unpriced_turns_not_double_counted(self, fake_projects, capsys):
        """A priced turn and an unpriced turn in the same corpus: the dollar headline
        reflects only the priced turn, and the unpriced turn is surfaced via its own
        counter rather than silently dropped or folded into the dollar figure at $0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced_opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),  # unpriced
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        expected_dollars = (50 / 1_000_000 * 5.00) + (300 / 1_000_000 * 25.00)
        # abs tolerance matches the headline's own 2-decimal-place ($.NN) display rounding.
        assert _extract_sonnet_tier_dollar_estimate(out) == pytest.approx(expected_dollars, abs=0.005)
        # _opus()'s turn: input 50 + output 400 + cache_read 0 = 450 unpriced tokens.
        assert "1 unpriced turns / 450 tokens excluded from priced spend" in out
        # Token-based secondary line still reflects BOTH turns' output tokens, unaffected
        # by pricing — proves the token and dollar accumulators are independent.
        assert _extract_corpus_class_tokens(out, "code-write") == 300
        assert _extract_corpus_class_tokens(out, "code-read") == 400

    def test_orchestration_takes_priority_over_active_judgment_span(self):
        """orchestration is first-match: Agent turn inside an open span → orchestration, not judgment."""
        result = _mod.audit_routing._classify_opus_turn(
            [_agent_use("a1", "code-writer")],
            in_judgment_span=True,
            plan_mode_active=False,
        )
        assert result == "orchestration"

    def test_opus_turn_with_empty_usage_is_skipped(self, fake_projects, capsys):
        """Opus turn with empty usage dict is excluded; corpus totals stay zero."""
        rec = _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z",
                    content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}])
        # usage is {} by default from _asst — cmd_audit_routing skips falsy usage
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_judgment_span_persists_across_unrecognized_skill_invocation(self, fake_projects, capsys):
        """An unrecognized Skill call inside an open span does not close the span."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Opens span
            _opus([_skill_use("s1", "code-review")], out=10),
            # Unrecognized skill — span stays open; turn is still judgment
            _opus([_skill_use("s2", "some-unknown-skill")], out=20),
            # Read turn inside still-open span → judgment, not code-read
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
            # User turn closes span
            _user_msg("continue", branch="main"),
            # Read turn after span closed → code-read
            _opus([{"type": "tool_use", "id": "r2", "name": "Read", "input": {}}], out=40),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # All three turns inside the span (10 + 20 + 30) → judgment
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # Only the post-span Read turn → code-read
        assert _extract_corpus_class_tokens(out, "code-read") == 40

    def test_combined_plan_mode_and_judgment_span_independent_tracking(self, fake_projects, capsys):
        """ExitPlanMode clears plan_mode_active but an open judgment span keeps the classification."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Activate plan-mode
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            # Judgment-skill invocation: opens span AND plan-mode is active
            _opus([_skill_use("s1", "code-review")], out=10),
            # ExitPlanMode: clears plan_mode_active; span from code-review still open
            _opus([_exit_plan_mode("epm1")], out=20),
            # Turn after ExitPlanMode: span still open (no user turn yet) → judgment
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
            # User turn resets span
            _user_msg("done", branch="main"),
            # Read turn after both flags cleared → code-read
            _opus([{"type": "tool_use", "id": "r2", "name": "Read", "input": {}}], out=40),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = skill-open (10) + ExitPlanMode (20) + post-exit-still-in-span read (30) = 60
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # code-read = post-user-reset read = 40
        assert _extract_corpus_class_tokens(out, "code-read") == 40

    def test_since_filter_excludes_turn_with_missing_timestamp(self, fake_projects, capsys):
        """With --since active, turns lacking a timestamp field are excluded."""
        rec_no_ts = _asst("claude-opus-4-7", branch="main",
                           content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}])
        rec_no_ts["message"]["usage"] = {"input_tokens": 50, "output_tokens": 200,
                                          "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 0}
        # No "timestamp" key on the record
        assert "timestamp" not in rec_no_ts
        _write_jsonl(fake_projects / "sess.jsonl", [rec_no_ts])
        args = _audit_routing_args(since="1d")
        _mod.audit_routing.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # Turn with no timestamp is excluded by the --since filter
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_request_id_group_later_block_skill_invocation_still_opens_judgment_span(
        self, fake_projects, capsys
    ):
        """A requestId group whose Skill tool_use block is not the group's
        first content block still opens a judgment span for that turn and the
        one after it — dedup merges every block in the group in order, so a
        later block's signal is never dropped the way keeping only the
        group's first record would drop it."""
        ts = "2026-05-19T10:00:00.000Z"
        rec_a = _opus([_thinking_block()], out=20, ts=ts, request_id="req-1")
        rec_b = _opus([_skill_use("s1", "code-review")], out=20, ts=ts, request_id="req-1")
        _write_jsonl(fake_projects / "sess.jsonl", [
            rec_a, rec_b,
            # Next turn, still inside the span the merged group's Skill block opened.
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Merged group (20, byte-identical usage priced/counted once) + the
        # next turn still in-span (30) = 50 judgment output tokens. A dedup
        # that kept only the group's first record would drop the Skill block,
        # classify the group as pure-thinking, never open the span, and
        # misclassify the next Read turn as code-read instead.
        assert _extract_corpus_class_tokens(out, "judgment") == 50
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_request_id_group_later_block_exit_plan_mode_clears_plan_mode_for_next_turn(
        self, fake_projects, capsys
    ):
        """A requestId group whose ExitPlanMode tool_use is not the group's
        first content block still clears plan-mode for the turn after it —
        dedup merges every block in the group in order, so a dedup that kept
        only the group's first record would drop the ExitPlanMode block,
        leave plan-mode stuck active, and misclassify the next turn as
        judgment instead of code-write."""
        rec_a = _opus([_thinking_block()], out=75, request_id="req-1")
        rec_b = _opus([_exit_plan_mode("epm1")], out=75, request_id="req-1")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            rec_a, rec_b,
            # Turn after the merged group, only code-write if plan-mode was
            # actually cleared by the group's (later-block) ExitPlanMode.
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=90),
        ])
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Merged group (75, byte-identical usage priced/counted once) is
        # still judgment (plan-mode was active during its own classification).
        assert _extract_corpus_class_tokens(out, "judgment") == 75
        assert _extract_corpus_class_tokens(out, "code-write") == 90

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        """A malformed --since value fails closed with the audit-routing-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod.audit_routing.cmd_audit_routing(_audit_routing_args(since="not-a-window"))
        assert "audit-routing: --since: expected Nd like '35d'" in capsys.readouterr().err


class TestAuditRoutingMultiRootRedaction:
    """audit-routing's --redact must look up each row's label via the shared
    _redaction_ordinals mapping, not a flat string key — proven by seeding
    the same raw project label under two declared roots and asserting both
    rows resolve to distinct account-N tokens instead of one colliding with
    (or being missed by) the other."""

    def test_same_raw_label_under_two_roots_resolves_to_distinct_account_tokens(
        self, tmp_path, monkeypatch, capsys
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for root in roots:
            proj = root / "-home-user-repo"
            proj.mkdir(parents=True)
            _write_jsonl(proj / "sess.jsonl", [_opus([_agent_use("a1", "code-writer")], out=100)])

        _mod.audit_routing.cmd_audit_routing(_audit_routing_args(redact=True))
        out = capsys.readouterr().out
        assert _mod._REDACT_MAP_MISS_TOKEN not in out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out
        assert "-home-user-repo" not in out
