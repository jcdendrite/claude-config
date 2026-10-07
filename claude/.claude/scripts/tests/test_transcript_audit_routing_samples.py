"""Tests for transcript_analysis/audit_routing.py (cmd_audit_routing_samples): seeded
code-read turn samples, JSON and markdown."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from ._audit_routing_helpers import (
    _audit_routing_samples_args,
    _audit_routing_shape_args,
    _extract_shape_d1,
)
from .conftest import (
    _agent_use,
    _edit_use,
    _opus,
    _read_use,
    _skill_use,
    _thinking_block,
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


class TestAuditRoutingSamples:
    def test_code_read_turn_emitted(self, fake_projects, capsys):
        """A single code-read turn produces one record; verify session_id, turn_index,
        and assistant_tool_call fields."""
        _write_jsonl(fake_projects / "abc12345-test.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        rec = records[0]
        assert rec["session_id"] == "abc12345-test"
        assert rec["turn_index"] == 0
        assert rec["assistant_tool_call"] == {"name": "Read", "input": {"file_path": "/foo/bar.py"}}

    def test_prior_user_message_captured(self, fake_projects, capsys):
        """A user message immediately before the code-read turn is captured in prior_user_message."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "Please read the file"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["prior_user_message"] == "Please read the file"

    def test_prior_user_message_empty_when_none(self, fake_projects, capsys):
        """No preceding user turn → prior_user_message is empty string."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["prior_user_message"] == ""

    def test_next_action_edit(self, fake_projects, capsys):
        """Next Opus turn is Edit (code-write) → next_assistant_action is 'edit'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_edit_use("e1")], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "edit"

    def test_next_action_dispatch(self, fake_projects, capsys):
        """Next Opus turn spawns an Agent (orchestration) → next_assistant_action is 'dispatch'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "dispatch"

    def test_next_action_another_read(self, fake_projects, capsys):
        """Next Opus turn is another Read (code-read) → next_assistant_action is 'another-read'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_read_use("r2", "/b.py")], out=150),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        # Two code-read turns produce two records; only check the first turn here
        first = next(r for r in records if r["turn_index"] == 0)
        assert first["next_assistant_action"] == "another-read"

    def test_next_action_respond_to_user(self, fake_projects, capsys):
        """Next Opus turn has no tool_use (text-only 'other' class) → next_assistant_action is 'respond-to-user'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": "Here is what I found"}], out=50),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "respond-to-user"

    def test_next_action_other_when_no_next(self, fake_projects, capsys):
        """Code-read is the last turn in the session → next_assistant_action is 'other'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "other"

    def test_next_turn_excerpt_populated(self, fake_projects, capsys):
        """next_turn_excerpt contains text from the next Opus turn."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": "I read the file and found the answer"}], out=50),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert "I read the file" in records[0]["next_turn_excerpt"]

    def test_sample_limit_respected(self, fake_projects, capsys):
        """10 eligible turns with sample=5 → exactly 5 records returned."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(10)
        ])
        args = _audit_routing_samples_args(sample=5, seed=0)
        _mod.audit_routing.cmd_audit_routing_samples(args)
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 5

    def test_seed_produces_deterministic_output(self, fake_projects, capsys):
        """Same seed on the same corpus produces the same turn selection."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(20)
        ])
        args_a = _audit_routing_samples_args(sample=5, seed=7)
        _mod.audit_routing.cmd_audit_routing_samples(args_a)
        records_a = json.loads(capsys.readouterr().out)

        args_b = _audit_routing_samples_args(sample=5, seed=7)
        _mod.audit_routing.cmd_audit_routing_samples(args_b)
        records_b = json.loads(capsys.readouterr().out)

        assert [r["turn_index"] for r in records_a] == [r["turn_index"] for r in records_b]

    def test_judgment_span_excluded(self, fake_projects, capsys):
        """Read turns inside a judgment span (opened by a judgment skill) are NOT included."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Opens judgment span; turn itself → judgment
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span — must be excluded
            _opus([_read_use("r1", "/span-file.py")], out=99),
            # User turn closes span
            _user_msg([{"type": "text", "text": "continue"}]),
            # Read after span closed — must be included
            _opus([_read_use("r2", "/normal.py")], out=77),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        # Only the post-span Read should appear
        assert len(records) == 1
        assert records[0]["assistant_tool_call"]["input"]["file_path"] == "/normal.py"

    def test_since_filter_excludes_old_turns(self, fake_projects, capsys):
        """Turns with timestamps older than --since Nd are excluded."""
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/old.py")], out=100, ts=old_ts),
            _opus([_read_use("r2", "/new.py")], out=100, ts=new_ts),
        ])
        args = _audit_routing_samples_args(since="1d")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["assistant_tool_call"]["input"]["file_path"] == "/new.py"

    def test_non_code_read_turns_not_in_output(self, fake_projects, capsys):
        """code-write, orchestration, and other turns produce no records."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_use("e1")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
            _opus([], out=50),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 0

    def test_cross_validation_with_audit_routing(self, fake_projects, capsys):
        """The count of code-read samples (with sample large enough) equals the D1 total turn
        count from cmd_audit_routing_shape for the same fixture.

        This guards against state-machine drift between the two subcommands. A Read turn
        inside a judgment span (99 tokens) must be excluded from both counts."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Skill invocation opens a judgment span; turn itself → judgment (50 tokens)
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span → excluded from both subcommands
            _opus([_read_use("rx", "/span-file.txt")], out=99),
            # User turn resets span
            _user_msg("done"),
            # 3 code-read turns outside span + 1 code-write
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([_edit_use("e1")], out=200),
        ])

        # Run audit-routing-shape: sum D1 turn counts across all buckets
        _mod.audit_routing.cmd_audit_routing_shape(_audit_routing_shape_args())
        shape_out = capsys.readouterr().out
        d1_total_turns = sum(_extract_shape_d1(shape_out, bkt)[0] for bkt in _mod.audit_routing._D1_BUCKETS)

        # Run audit-routing-samples with a large sample to capture all eligible turns
        args = _audit_routing_samples_args(sample=1000, seed=0)
        _mod.audit_routing.cmd_audit_routing_samples(args)
        samples = json.loads(capsys.readouterr().out)

        assert len(samples) == d1_total_turns  # both should be 3

    def test_user_turn_skipped_in_forward_scan(self, fake_projects, capsys):
        """A user turn between a code-read and a code-write is skipped in the forward scan;
        next_assistant_action must still be 'edit' not 'other'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _user_msg([{"type": "text", "text": "looks good"}]),
            _opus([_edit_use("e1")], out=200),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "edit"

    def test_prior_user_message_nonadjacent(self, fake_projects, capsys):
        """The backward walk finds the most recent user turn even with intervening code-read turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "investigate this"}]),
            _opus([_read_use("r1", "/first.py")], out=100),
            _opus([_read_use("r2", "/second.py")], out=100),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 2
        # Both reads should trace back to the same user message
        assert records[0]["prior_user_message"] == "investigate this"
        assert records[1]["prior_user_message"] == "investigate this"

    def test_next_turn_excerpt_truncated_at_200(self, fake_projects, capsys):
        """next_turn_excerpt is truncated to at most 200 characters."""
        long_text = "x" * 400
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": long_text}], out=50),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert len(records[0]["next_turn_excerpt"]) == 200

    def test_different_seed_produces_different_output(self, fake_projects, capsys):
        """Different seeds on the same corpus produce different selections (shuffle is live)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(20)
        ])
        args_a = _audit_routing_samples_args(sample=5, seed=1)
        _mod.audit_routing.cmd_audit_routing_samples(args_a)
        records_a = json.loads(capsys.readouterr().out)

        args_b = _audit_routing_samples_args(sample=5, seed=99)
        _mod.audit_routing.cmd_audit_routing_samples(args_b)
        records_b = json.loads(capsys.readouterr().out)

        assert [r["turn_index"] for r in records_a] != [r["turn_index"] for r in records_b]

    def test_format_md_emits_markdown_document(self, fake_projects, capsys):
        """--format md produces a markdown document, not a JSON array."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert out.startswith("# audit-routing-samples curation")

    def test_format_md_one_section_per_record(self, fake_projects, capsys):
        """--format md produces exactly one ## section header per sampled record."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_read_use("r2", "/b.py")], out=100),
            _opus([_read_use("r3", "/c.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md", sample=10)
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Count lines that start with '## ' (section headers, not the h1)
        section_headers = [line for line in out.splitlines() if line.startswith("## ")]
        assert len(section_headers) == 3

    def test_format_md_renders_read_tool_call(self, fake_projects, capsys):
        """--format md renders a Read tool call as **Read:** `<file_path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "src/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Read:** `src/foo/bar.py`" in out

    def test_format_md_renders_grep_tool_call(self, fake_projects, capsys):
        """--format md renders a Grep tool call as **Grep:** `<pattern>` in `<path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "g1", "name": "Grep",
                    "input": {"pattern": "render_widget", "path": "src/"}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Grep:** `render_widget` in `src/`" in out

    def test_format_md_renders_bash_tool_call_truncated(self, fake_projects, capsys):
        """--format md truncates a Bash command longer than 80 chars to exactly 80 chars + '…'."""
        long_command = "a" * 100
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "b1", "name": "Bash",
                    "input": {"command": long_command}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Bash:**" in out
        # Extract the backtick-delimited command portion
        match = re.search(r"\*\*Bash:\*\* `([^`]*)`", out)
        assert match is not None
        rendered_command = match.group(1)
        assert rendered_command == "a" * _mod.render._BASH_COMMAND_DISPLAY_CHARS + "…"

    def test_format_md_fallback_for_unknown_tool(self):
        """_pretty_tool_call renders an unrecognised tool name using the **<Name>:** fallback."""
        # SomeOtherTool is not in _CODE_READ_TOOLS, so a turn using only it would never
        # be classified as code-read and would not reach _pretty_tool_call via the full
        # pipeline.  Testing the helper directly exercises the fallback rendering path.
        rendered = _mod.render._pretty_tool_call({"name": "SomeOtherTool", "input": {"x": 1}})
        assert "**SomeOtherTool:**" in rendered

    def test_format_md_blockquotes_multiline_user_message(self, fake_projects, capsys):
        """--format md blockquotes each line of a multiline prior user message."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "line one\nline two"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "> line one" in out
        assert "> line two" in out

    def test_format_json_unchanged_by_default(self, fake_projects, capsys):
        """Default output (no --format flag) is a JSON array starting with '['."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args()  # output_format defaults to "json"
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert out.strip().startswith("[")

    def test_format_md_renders_glob_tool_call(self, fake_projects, capsys):
        """--format md renders a Glob tool call as **Glob:** `<pattern>` in `<path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "gl1", "name": "Glob",
                    "input": {"pattern": "**/*.ts", "path": "src/"}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Glob:** `**/*.ts` in `src/`" in out

    def test_format_md_renders_bash_tool_call_short(self, fake_projects, capsys):
        """--format md renders a short Bash command without truncation or ellipsis."""
        short_command = "git status"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "b2", "name": "Bash",
                    "input": {"command": short_command}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert f"**Bash:** `{short_command}`" in out
        assert "…" not in out

    def test_format_md_blockquotes_blank_lines_in_user_message(self, fake_projects, capsys):
        """--format md preserves blank-line continuity: blank lines render as '>' not bare empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "para one\n\npara two"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        lines = out.splitlines()
        # Find the blockquoted user section; the blank line between paragraphs must be ">"
        # (not an empty string), so blockquote continuity is preserved in markdown renderers.
        assert ">" in lines   # at least one plain ">" line exists
        # Verify the blank line between the two paragraphs is ">" not ""
        # by finding the two paragraph lines and checking what's between them
        para1_idx = next(i for i, ln in enumerate(lines) if ln == "> para one")
        para2_idx = next(i for i, ln in enumerate(lines) if ln == "> para two")
        for between_line in lines[para1_idx + 1:para2_idx]:
            assert between_line == ">"

    def test_pretty_tool_call_bash_with_description(self):
        """_pretty_tool_call renders Bash with description as 'description — `cmd`'."""
        result = _mod.render._pretty_tool_call({
            "name": "Bash",
            "input": {"command": "grep -rn foo bar/", "description": "Find foo"},
        })
        assert result == "**Bash:** Find foo — `grep -rn foo bar/`"

    def test_pretty_tool_call_bash_without_description(self):
        """_pretty_tool_call Bash without description falls back to bare command."""
        result = _mod.render._pretty_tool_call({
            "name": "Bash",
            "input": {"command": "ls /tmp"},
        })
        assert result == "**Bash:** `ls /tmp`"

    def test_format_md_narration_section_present(self, fake_projects, capsys):
        """--format md includes **Recent agent narration:** when prior text exists."""
        # Two Opus turns: first has text, second is the code-read (sampled).
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Checking dependencies first."},
                   _read_use("r0", "/other.py")], out=100),
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent agent narration:**" in out
        assert "Checking dependencies first." in out

    def test_format_md_narration_section_absent_when_no_prior_text(self, fake_projects, capsys):
        """--format md omits **Recent agent narration:** when there is no prior assistant text."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent agent narration:**" not in out

    def test_format_md_tool_trail_section_present(self, fake_projects, capsys):
        """--format md includes **Recent tool trail:** when prior tool calls exist."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r0", "/other.py")], out=100),
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent tool trail:**" in out
        assert "Read: /other.py" in out

    def test_format_md_tool_trail_section_absent_at_session_start(self, fake_projects, capsys):
        """--format md omits **Recent tool trail:** when candidate is the first turn."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent tool trail:**" not in out

    def test_format_md_trail_and_narration_cap_at_n(self, fake_projects, capsys):
        """Lookback caps at 3: 10 prior assistant turns yields exactly 3 narration/trail entries."""
        prior_turns = [
            _opus([{"type": "text", "text": f"Step {i}."},
                   _read_use(f"r{i}", f"/f{i}.py")], out=100)
            for i in range(10)
        ]
        candidate = _opus([_read_use("rc", "/candidate.py")], out=100)
        _write_jsonl(fake_projects / "sess.jsonl", prior_turns + [candidate])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Isolate only the card for /candidate.py (the last candidate turn)
        sections = out.split("---")
        candidate_section = next((s for s in sections if "/candidate.py" in s), None)
        assert candidate_section is not None, "No section found for /candidate.py"
        # Count narration blockquote lines starting with "> " that contain "Step"
        narration_lines = [ln for ln in candidate_section.splitlines() if ln.startswith("> ") and "Step" in ln]
        assert len(narration_lines) == 3
        # Count trail bullet lines
        trail_lines = [ln for ln in candidate_section.splitlines() if ln.startswith("- Read:") or ln.startswith("- Bash:")]
        assert len(trail_lines) == 3

    def test_format_md_fewer_than_3_priors(self, fake_projects, capsys):
        """Lookback with 1 prior turn yields exactly 1 narration and 1 trail entry."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Only prior."},
                   _read_use("r0", "/only.py")], out=100),
            _opus([_read_use("rc", "/candidate.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        narration_lines = [ln for ln in out.splitlines() if ln.startswith("> ") and "Only prior." in ln]
        assert len(narration_lines) == 1
        trail_lines = [ln for ln in out.splitlines() if "Read: /only.py" in ln]
        assert len(trail_lines) == 1

    def test_format_md_tool_only_turns_skipped_by_narration(self, fake_projects, capsys):
        """Narration walker skips turns with no text block; still collects text-bearing turns."""
        # 3 text-bearing turns, then 3 tool-only turns, then candidate.
        # Walking backward from candidate: hit 3 tool-only (skipped), then 3 text-bearing (collected).
        tool_only = [_opus([_read_use(f"r{i}", f"/t{i}.py")], out=100) for i in range(3)]
        text_bearing = [
            _opus([{"type": "text", "text": f"Text {i}."}, _read_use(f"rt{i}", f"/tb{i}.py")], out=100)
            for i in range(3)
        ]
        candidate = _opus([_read_use("rc", "/candidate.py")], out=100)
        _write_jsonl(fake_projects / "sess.jsonl", text_bearing + tool_only + [candidate])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Isolate the candidate card and assert narration contains the text-bearing entries
        sections = out.split("---")
        candidate_section = next((s for s in sections if "/candidate.py" in s), None)
        assert candidate_section is not None, "No section found for /candidate.py"
        for i in range(3):
            assert f"Text {i}." in candidate_section

    def test_format_md_newlines_stripped_in_narration(self, fake_projects, capsys):
        """Newlines in narration text are replaced with spaces (no blockquote corruption)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Line one.\nLine two."},
                   _read_use("r0", "/a.py")], out=100),
            _opus([_read_use("rc", "/candidate.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Should appear as a single blockquote line with a space, not two separate lines
        assert "> Line one. Line two." in out

    def test_format_md_cross_session_isolation(self, fake_projects, capsys):
        """Candidate in session B does not pick up session A's narration or trail."""
        # Session A: has a text turn with distinctive content.
        (fake_projects / "sess_a.jsonl").write_text(
            "\n".join(
                json.dumps(r) for r in [
                    _opus([{"type": "text", "text": "Session A unique text."},
                           _read_use("ra", "/a.py")], out=100),
                ]
            ) + "\n"
        )
        # Session B: single code-read candidate, no prior turns.
        (fake_projects / "sess_b.jsonl").write_text(
            "\n".join(
                json.dumps(r) for r in [
                    _opus([_read_use("rb", "/b.py")], out=100),
                ]
            ) + "\n"
        )
        args = _audit_routing_samples_args(output_format="md", sample=10)
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Split by section headers and check B's card specifically.
        sections = out.split("---")
        b_section = next((s for s in sections if "/b.py" in s), None)
        if b_section:  # only assert if B was sampled
            assert "Session A unique text." not in b_section

    def test_format_json_record_key_set(self, fake_projects, capsys):
        """JSON output record has exactly the expected key set (no missing, no extra)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo.py")], out=100),
        ])
        args = _audit_routing_samples_args()  # default JSON format
        _mod.audit_routing.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        records = json.loads(out)
        assert len(records) == 1
        assert set(records[0].keys()) == {
            "session_id", "turn_index", "prior_user_message",
            "assistant_tool_call", "next_assistant_action", "next_turn_excerpt",
            "recent_assistant_text", "recent_tool_trail",
        }

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        """A malformed --since value fails closed with the audit-routing-samples-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args(since="not-a-window"))
        assert "audit-routing-samples: --since: expected Nd like '35d'" in capsys.readouterr().err

    def test_request_id_group_tool_use_on_later_record_still_one_turn_at_index_zero(
        self, fake_projects, capsys
    ):
        """A requestId group whose Read tool_use lands on the second raw
        record (the first record is thinking-only) still emits exactly one
        code-read candidate at turn_index 0 with assistant_tool_call set from
        the merged content's own tool_use block — without dedup the
        thinking-only first record becomes its own phantom pure-thinking
        turn, pushing the real turn to index 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_thinking_block()], out=20, request_id="req-1"),
            _opus([_read_use("r1", "/a.py")], out=20, request_id="req-1"),
        ])
        _mod.audit_routing.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["turn_index"] == 0
        assert records[0]["assistant_tool_call"] == {"name": "Read", "input": {"file_path": "/a.py"}}
