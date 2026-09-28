"""Tests for transcript_analysis/audit_routing.py (cmd_audit_routing_shape): D1/D2/D3
code-read turn-shape distributions."""
import importlib.util
import sys
from pathlib import Path

import pytest

from ._audit_routing_helpers import (
    _audit_routing_shape_args,
    _extract_corpus_class_tokens,
    _extract_shape_d1,
)
from .conftest import (
    _agent_use,
    _audit_routing_args,
    _bash_use,
    _opus,
    _read_use,
    _skill_use,
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


def _grep_use(tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Grep", "input": {"pattern": "x", "path": "."}}


def _glob_use(tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Glob", "input": {"pattern": "**/*.py", "path": "."}}


def _extract_shape_d2(out: str, bucket: str) -> tuple[int, int]:
    """Parse (streak_count, output_tokens) for a D2 bucket from audit-routing-shape output."""
    in_d2 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D2" in stripped and "streak" in stripped.lower():
            in_d2 = True
            continue
        if in_d2 and stripped.startswith("###"):
            break
        if in_d2 and "Bucket" in stripped and "Streaks" in stripped:
            header_line = stripped
            continue
        if in_d2 and stripped.startswith(bucket):
            parts = stripped.split()
            # Anchor Streaks and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            streaks_idx = header_line.split().index("Streaks") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else streaks_idx + 1
            if len(parts) > out_idx:
                return int(parts[streaks_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d3(out: str, case: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 case from audit-routing-shape output."""
    in_d3 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D3" in stripped and "Read-then-edit" in stripped:
            in_d3 = True
            continue
        if in_d3 and stripped.startswith("###"):
            break
        if in_d3 and stripped.startswith("####"):
            break
        if in_d3 and "Case" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_d3 and stripped.startswith(case):
            parts = stripped.split()
            # Anchor Turns and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            turns_idx = header_line.split().index("Turns") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else turns_idx + 1
            if len(parts) > out_idx:
                return int(parts[turns_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d3_xtab(out: str, case: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 × D1 cross-tab cell."""
    in_xtab = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D3 × D1 cross-tab" in stripped or "D3 x D1 cross-tab" in stripped.lower():
            in_xtab = True
            continue
        if in_xtab and (stripped.startswith("###") or stripped.startswith("Dispatchable")):
            break
        if in_xtab and "Case" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_xtab and stripped.startswith(case) and bucket in stripped:
            parts = stripped.split()
            # "D1 bucket" is a 2-token header name but maps to 1 data token (e.g. "2-3").
            # header.split().index("Turns") = 3, but the data-row Turns value is at position 2;
            # subtract 1 to correct for the header's extra token from the multi-word column.
            turns_idx = header_line.split().index("Turns") - 1 if header_line else 2
            if len(parts) > turns_idx + 1:
                return int(parts[turns_idx].replace(",", "")), int(parts[turns_idx + 1].replace(",", ""))
    return 0, 0


class TestAuditRoutingShape:
    def test_d1_buckets(self, fake_projects, capsys):
        """One turn per D1 bucket (0, 1, 2, 5, 9 Reads); each bucket has turn count 1 and correct tokens."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # 0 Reads → bucket "0"
            _opus([_bash_use("b1", "ls")], out=10),
            # 1 Read → bucket "1"
            _opus([_read_use("r1", "/a.txt")], out=20),
            # 2 Reads → bucket "2-3"
            _opus([_read_use("r2", "/a.txt"), _read_use("r3", "/b.txt")], out=30),
            # 5 Reads → bucket "4-7"
            _opus([_read_use(f"r{i}", f"/f{i}.txt") for i in range(10, 15)], out=40),
            # 9 Reads → bucket "8+"
            _opus([_read_use(f"r{i}", f"/f{i}.txt") for i in range(20, 29)], out=50),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert _extract_shape_d1(out, "0") == (1, 10)
        assert _extract_shape_d1(out, "1") == (1, 20)
        assert _extract_shape_d1(out, "2-3") == (1, 30)
        assert _extract_shape_d1(out, "4-7") == (1, 40)
        assert _extract_shape_d1(out, "8+") == (1, 50)

    def test_d1_excludes_grep_glob_bash(self, fake_projects, capsys):
        """Turns with only Grep or Glob tool_use land in bucket '0' (neither counts as a Read)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_grep_use("g1")], out=77),
            _opus([_glob_use("gl1")], out=88),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns_0, tokens_0 = _extract_shape_d1(out, "0")
        assert turns_0 == 2
        assert tokens_0 == 165  # 77 + 88
        # bucket "1" must be empty
        assert _extract_shape_d1(out, "1") == (0, 0)

    def test_d2_streak_1(self, fake_projects, capsys):
        """Two sessions each with one code-read turn surrounded by non-code-read turns produce
        two distinct streaks of length 1; bucket '1' streak count = 2."""
        # Session A: code-write, code-read, code-write → streak of length 1
        _write_jsonl(fake_projects / "sess_a.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
            _opus([_read_use("r1", "/a.txt")], out=50),
            _opus([{"type": "tool_use", "id": "e2", "name": "Edit", "input": {}}], out=100),
        ])
        # Session B: code-write, code-read, code-write → streak of length 1
        _write_jsonl(fake_projects / "sess_b.jsonl", [
            _opus([{"type": "tool_use", "id": "e3", "name": "Edit", "input": {}}], out=100),
            _opus([_read_use("r2", "/b.txt")], out=60),
            _opus([{"type": "tool_use", "id": "e4", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "1")
        assert streak_count == 2
        assert streak_out == 110  # 50 + 60

    def test_d2_streak_2(self, fake_projects, capsys):
        """Two consecutive code-read turns followed by a non-code-read: one streak, bucket '2', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=30),
            _opus([_read_use("r2", "/b.txt")], out=40),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "2")
        assert streak_count == 1
        assert streak_out == 70  # 30 + 40

    def test_d2_streak_3_5(self, fake_projects, capsys):
        """A session with 4 consecutive code-read turns: one streak, bucket '3-5', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=25) for i in range(4)
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "3-5")
        assert streak_count == 1
        assert streak_out == 100  # 4 × 25

    def test_d2_streak_6_10(self, fake_projects, capsys):
        """Seven consecutive code-read turns: one streak, bucket '6-10', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=10) for i in range(7)
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "6-10")
        assert streak_count == 1
        assert streak_out == 70  # 7 × 10

    def test_d2_streak_11_plus(self, fake_projects, capsys):
        """Twelve consecutive code-read turns: one streak, bucket '11+', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=10) for i in range(12)
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "11+")
        assert streak_count == 1
        assert streak_out == 120  # 12 × 10

    def test_d2_streak_split_by_user(self, fake_projects, capsys):
        """A user turn between two code-read turns splits them into two streaks of length 1.
        Specifically, [r1, user-turn, r2] must NOT be treated as one streak of 2."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=10),
            _user_msg("continue", branch="main"),
            _opus([_read_use("r2", "/b.txt")], out=10),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        # User turn breaks the streak — two separate streaks of length 1, not one streak of 2
        streak_1_count, _ = _extract_shape_d2(out, "1")
        streak_2_count, _ = _extract_shape_d2(out, "2")
        streak_35_count, _ = _extract_shape_d2(out, "3-5")
        assert streak_1_count == 2
        assert streak_2_count == 0
        assert streak_35_count == 0

    def test_d3_inline_edit(self, fake_projects, capsys):
        """A code-read turn followed within 3 turns by a code-write with no orchestration → case inline-edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100

    def test_d3_dispatched(self, fake_projects, capsys):
        """A code-read turn followed within 3 turns by an orchestration turn → case dispatched."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "dispatched")
        assert turns == 1
        assert tokens == 100

    def test_d3_neither(self, fake_projects, capsys):
        """A code-read turn with no code-write or orchestration within 3 turns → case neither."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            # Three non-code-write, non-orchestration turns
            _opus([], out=10),
            _opus([], out=10),
            _opus([], out=10),
            # code-write beyond the 3-turn window — must not affect classification
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "neither")
        assert turns == 1
        assert tokens == 100
        # inline-edit must be 0 turns (the edit was outside the 3-turn window)
        assert _extract_shape_d3(out, "inline-edit") == (0, 0)

    def test_d3_inline_edit_at_window_boundary(self, fake_projects, capsys):
        """A code-write at exactly lookahead position 3 (the last inclusive position) is inline-edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([], out=10),  # lookahead 1: other
            _opus([], out=10),  # lookahead 2: other
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),  # lookahead 3
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100
        assert _extract_shape_d3(out, "neither") == (0, 0)

    def test_d3_inline_edit_across_user_turn(self, fake_projects, capsys):
        """A code-read turn followed by a user turn then a code-write within 3 Opus turns
        must still classify as inline-edit — user turns don't consume lookahead budget."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _user_msg("continue", branch="main"),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100
        assert _extract_shape_d3(out, "neither") == (0, 0)

    def test_d3_cross_tab_file_bucket(self, fake_projects, capsys):
        """A code-read turn with 2 Reads followed by an inline edit appears in cross-tab inline-edit × 2-3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt"), _read_use("r2", "/b.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3_xtab(out, "inline-edit", "2-3")
        assert turns == 1
        assert tokens == 100

    def test_d2_and_d3_streak_then_edit(self, fake_projects, capsys):
        """Three consecutive code-read turns followed by a code-write: D2 sees one streak in
        '3-5' AND D3 sees all three turns as inline-edit (the edit is within 3 Opus turns of each)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "3-5")
        assert streak_count == 1
        assert streak_out == 300  # 3 × 100
        d3_turns, d3_out = _extract_shape_d3(out, "inline-edit")
        assert d3_turns == 3  # all three code-read turns see the edit within 3 Opus turns
        assert d3_out == 300

    def test_cross_validation_with_audit_routing(self, fake_projects, capsys):
        """D1 total output tokens across all buckets must equal audit-routing's corpus code-read total.
        This guards against drift between the duplicated judgment-span state machines.
        A Read turn inside a judgment span (99 tokens) must be excluded from both totals."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Skill invocation opens a judgment span; turn itself → judgment (50 tokens)
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span → judgment, NOT code-read (99 tokens — must not appear in D1)
            _opus([_read_use("rx", "/span-file.txt")], out=99),
            # User turn resets span
            _user_msg("done", branch="main"),
            # 3 code-read turns outside span (100 each) + 1 code-write turn (200)
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        # Run audit-routing and capture code-read corpus total
        _mod.audit_routing.cmd_audit_routing(_audit_routing_args())
        routing_out = capsys.readouterr().out
        routing_code_read = _extract_corpus_class_tokens(routing_out, "code-read")

        # Run audit-routing-shape and sum D1 output tokens across all buckets
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        shape_out = capsys.readouterr().out
        d1_total = sum(_extract_shape_d1(shape_out, bkt)[1] for bkt in _mod.audit_routing._D1_BUCKETS)

        assert d1_total == routing_code_read  # both should be 300 (judgment-span Read excluded from both)

    def test_judgment_span_excluded_from_distributions(self, fake_projects, capsys):
        """A Read turn inside a judgment span must not appear in D1, D2, or D3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Open judgment span
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span — must be excluded from all distributions
            _opus([_read_use("r1", "/a.txt")], out=99),
            # User turn closes span
            _user_msg("done", branch="main"),
            # Read after span closed — must appear in D1
            _opus([_read_use("r2", "/b.txt")], out=77),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        # Only the post-span Read (77 tokens, 1 Read → bucket "1") should appear
        turns_1, tokens_1 = _extract_shape_d1(out, "1")
        assert turns_1 == 1
        assert tokens_1 == 77
        # Total D1 output tokens across all buckets must be 77 (not 176)
        d1_total = sum(_extract_shape_d1(out, bkt)[1] for bkt in _mod.audit_routing._D1_BUCKETS)
        assert d1_total == 77

    def test_dispatchable_share_summary_value(self, fake_projects, capsys):
        """Dispatchable-share percentage is computed correctly for a controlled fixture.
        4 code-read turns: A(D1=2-3, D3=inline-edit), B(D1=1, D3=inline-edit),
        C(D1=1, D3=dispatched), D(D1=1, D3=neither). Tokens: 100+200+300+400=1000.
        Dispatchable via D1∪D3: A+C+D = 100+300+400=800 → 80%."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Turn A: 2 Reads → D1="2-3" (dispatchable). Followed by inline-edit.
            _opus([_read_use("a1", "/a1.txt"), _read_use("a2", "/a2.txt")], out=100),
            _opus([{"type": "tool_use", "id": "ea", "name": "Edit", "input": {}}], out=50),
            # Turn B: 1 Read → D1="1". Followed by inline-edit within 3 turns.
            _opus([_read_use("b1", "/b1.txt")], out=200),
            _opus([{"type": "tool_use", "id": "eb", "name": "Edit", "input": {}}], out=50),
            # Turn C: 1 Read → D1="1". Followed by orchestration.
            _opus([_read_use("c1", "/c1.txt")], out=300),
            _opus([_agent_use("ag1", "code-writer")], out=50),
            # Turn D: 1 Read → D1="1". No code-write or orchestration within 3 turns.
            _opus([_read_use("d1", "/d1.txt")], out=400),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert "Dispatchable share: 80%" in out

    def test_dispatchable_share_empty_corpus(self, fake_projects, capsys):
        """No code-read turns → dispatchable share prints '—'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert "Dispatchable share: —" in out

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        """A malformed --since value fails closed with the audit-routing-shape-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args(since="not-a-window"))
        assert "audit-routing-shape: --since: expected Nd like '35d'" in capsys.readouterr().err

    def test_request_id_group_reads_split_across_records_bucketed_by_union(self, fake_projects, capsys):
        """A requestId group whose 2 Read tool_use blocks land on separate
        raw records is bucketed as one code-read turn with D1='2-3' (the
        union of both blocks), not as two separate D1='1' turns — dedup
        merges the group's content before _count_read_file_paths sees it."""
        rec_a = _opus([_read_use("r1", "/a.txt")], out=20, request_id="req-1")
        rec_b = _opus([_read_use("r2", "/b.txt")], out=20, request_id="req-1")
        _write_jsonl(fake_projects / "sess.jsonl", [rec_a, rec_b])
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert _extract_shape_d1(out, "2-3") == (1, 20)
        assert _extract_shape_d1(out, "1") == (0, 0)
