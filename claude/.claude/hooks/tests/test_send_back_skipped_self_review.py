"""Tests for send-back-skipped-self-review.sh and its evaluator,
evaluate-code-writer-self-review.py.

Four tiers:
  1. Pure functions, loaded through importlib (the evaluator's filename is not
     a valid Python identifier), as parametrized tables.
  2. Contract: the hand-authored transcript fixture, the ledger block in
     code-writer.md, and the reviewer names in code-writer.md's domain table.
  3. The evaluator's main() against directories under tmp_path and transcripts
     built in the fixture's record shapes.
  4. The hook as a subprocess, run by direct exec through a private raw runner.

Every path, name and message here is a placeholder.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import time
from collections import namedtuple
from pathlib import Path

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    REPO_ROOT,
    build_path_without,
    extract_skill_command,
    heading_texts,
    init_git_repo_with_commit,
    normalize_heading,
    symlink_hooks_lib_chain,
)

HOOK_PATH = HOOKS_DIR / "send-back-skipped-self-review.sh"
EVALUATOR_PATH = HOOKS_DIR / "evaluate-code-writer-self-review.py"
PARSE_GIT_COMMAND_PATH = HOOKS_DIR / "parse-git-command.py"
CODE_WRITER_PATH = CLAUDE_DIR / "agents" / "code-writer.md"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "code-writer-self-review" / "agent-transcript.jsonl"

_evaluator_spec = importlib.util.spec_from_file_location("evaluate_code_writer_self_review", EVALUATOR_PATH)
evaluator = importlib.util.module_from_spec(_evaluator_spec)
_evaluator_spec.loader.exec_module(evaluator)

_git_spec = importlib.util.spec_from_file_location("parse_git_command", PARSE_GIT_COMMAND_PATH)
_parse_git_command = importlib.util.module_from_spec(_git_spec)
_git_spec.loader.exec_module(_parse_git_command)

AGENT_ID = "a0123456789abcdef"
SUCCESS = evaluator.OUTCOME_SUCCESS
FAILED = evaluator.OUTCOME_FAILED
PENDING = evaluator.OUTCOME_PENDING

LEDGER_PLATFORM = "**Self-review:**\n- `staff-platform-engineer`: quoting angle applied; clean\n\n**Still uncertain:** nothing"
LEDGER_PLATFORM_AND_SDET = (
    "**Self-review:**\n"
    "- `staff-platform-engineer`: quoting angle applied; clean\n"
    "- `staff-sdet`: isolation angle applied; clean\n\n"
    "**Still uncertain:** nothing"
)


# ==========================================================================
# Tier 1 — pure functions
# ==========================================================================


@pytest.mark.parametrize(
    ("path", "expected_reviewers"),
    [
        ("tests/a.py", {"staff-sdet"}),
        ("pkg/test_a.py", {"staff-sdet"}),
        ("pkg/a_test.go", {"staff-sdet"}),
        ("web/a.test.ts", {"staff-sdet"}),
        ("web/a.spec.ts", {"staff-sdet"}),
        ("pkg/conftest.py", {"staff-sdet"}),
        ("pkg/__tests__/a.js", {"staff-sdet"}),
        ("pkg/test/a.py", {"staff-sdet"}),
        ("pkg/spec/a.rb", {"staff-sdet"}),
        ("docs/test/overview.md", {"comment-discipline-reviewer"}),
        ("scripts/a.zsh", {"staff-platform-engineer"}),
        ("latest.py", set()),
        ("contest.py", set()),
        ("attestation.py", set()),
        ("my-tests/a.py", set()),
        ("docs/spec/overview.md", {"comment-discipline-reviewer"}),
        ("scripts/a.sh", {"staff-platform-engineer"}),
        ("scripts/a.bash", {"staff-platform-engineer"}),
        ("infra/main.tf", {"staff-platform-engineer"}),
        ("infra/prod.tfvars", {"staff-platform-engineer"}),
        (".github/workflows/ci.yml", {"staff-platform-engineer"}),
        ("x.github/workflows/a.yml", set()),
        ("Dockerfile", {"staff-platform-engineer"}),
        ("Dockerfile.dev", {"staff-platform-engineer"}),
        ("README.md", {"comment-discipline-reviewer"}),
        ("docs/CLAUDE.md", {"comment-discipline-reviewer", "ai-instruction-and-memory-files"}),
        ("AGENTS.md", {"comment-discipline-reviewer", "ai-instruction-and-memory-files"}),
        (".claude/rules/a.md", {"comment-discipline-reviewer", "ai-instruction-and-memory-files"}),
        ("claude/.claude/rules/a.md", {"comment-discipline-reviewer", "ai-instruction-and-memory-files"}),
        ("docs/rules/a.md", {"comment-discipline-reviewer"}),
        ("src/lib.py", set()),
        ("tests/test_a.sh", {"staff-sdet", "staff-platform-engineer"}),
    ],
)
def test_path_floor_reviewers_table(path, expected_reviewers):
    assert set(evaluator.path_floor_reviewers(path)) == expected_reviewers


@pytest.mark.parametrize(
    ("path", "line", "expected"),
    [
        ("a.sh", "  # note", True),
        ("a.sh", "#!/usr/bin/env bash", False),
        ("a.py", "# note", True),
        ("a.py", '"""Module docstring."""', True),
        ("a.py", "'''text", True),
        ("a.py", '"""', False),
        ("a.py", "x = 1  # trailing", False),
        ("a.yml", "# note", True),
        ("Makefile", "# note", True),
        ("Dockerfile.dev", "# note", True),
        ("a.js", "// note", True),
        ("a.ts", "/* note", True),
        ("a.go", "*ptr = x", False),
        ("a.rs", "*ptr = x;", False),
        ("a.c", "*ptr = x;", False),
        ("a.c", "#" "include <stdio.h>", False),
        ("a.c", " * continuation", False),
        ("a.sql", "-- note", True),
        ("a.sql", "# note", False),
        ("a.css", "/* note */", False),
        ("a.md", "# Heading", False),
    ],
)
def test_is_comment_line_families(path, line, expected):
    assert evaluator.is_comment_line(path, line) is expected


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected_added"),
    [
        (
            "Edit",
            {"old_string": "# keep\nx = 1\n", "new_string": "# keep\nx = 2\n"},
            ["x = 2"],
        ),
        (
            "Edit",
            {"old_string": "x = 1\n", "new_string": "# new note\nx = 1\n"},
            ["# new note"],
        ),
        ("Write", {"content": "# one\ncode\n"}, ["# one", "code", ""]),
        ("Edit", {"old_string": "a", "new_string": None}, []),
        ("Bash", {"command": "ls"}, []),
    ],
)
def test_added_lines_from_edit_and_write_inputs(tool_name, tool_input, expected_added):
    assert evaluator.added_lines(tool_name, tool_input) == expected_added


@pytest.mark.parametrize(
    ("report", "expected_names", "expected_lines", "expected_unrecognized"),
    [
        ("**Self-review:**\n- `staff-sdet`: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n    `staff-sdet`: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n* `staff-sdet`: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n1. `staff-sdet`: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n2) `staff-sdet`: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n**`staff-sdet`**: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n- **`staff-sdet`**: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n- `staff-sdet`, `ciso-reviewer`: clean", ["staff-sdet", "ciso-reviewer"], 1, 0),
        ("**Self-review:**\n- `staff-sdet` and `ciso-reviewer`: clean", ["staff-sdet", "ciso-reviewer"], 1, 0),
        ("**Self-review:**\n- `staff-sdet`/`ciso-reviewer`: clean", ["staff-sdet", "ciso-reviewer"], 1, 0),
        ("**Self-review:**\n- `~/.claude/agents/staff-sdet.md`: clean", ["staff-sdet"], 1, 0),
        (
            "**Self-review:**\n- `/x/.claude/skills/ai-instruction-and-memory-files/SKILL.md`: clean",
            ["ai-instruction-and-memory-files"],
            1,
            0,
        ),
        ("**Self-review:**\n```\n- `staff-sdet`: clean\n```", ["staff-sdet"], 1, 0),
        ("**Self-review:**\r\n- `staff-sdet`: clean\r\n", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n- `staff-sdet`: a\n- `staff-sdet`: b", ["staff-sdet"], 2, 0),
        ("**Self-review:**\n- `not-a-reviewer`: clean", [], 1, 1),
        ("**Self-review:**\n- `not-a-reviewer`: a\n- `not-a-reviewer`: b", [], 2, 1),
        ("**Self-review:**\n- `review-orchestrator`: clean", [], 1, 1),
        (
            "**Self-review:**\n- `staff-sdet`: clean\n**Still uncertain:**\n- `ciso-reviewer`: nothing",
            ["staff-sdet"],
            1,
            0,
        ),
        ("**Self-review:** `staff-sdet`: clean", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n- `staff-sdet`: Still uncertain about the retry path", ["staff-sdet"], 1, 0),
        ("**Self-review:**\n- `staff-sdet`: clean\n- `ciso-reviewer`: clean", ["staff-sdet", "ciso-reviewer"], 2, 0),
        ("## Self-review\n- `staff-sdet`: clean", [], 0, 0),
        ("- `staff-sdet`: clean", [], 0, 0),
        ("**Self-review:**\nstaff-sdet: clean", [], 0, 0),
    ],
)
def test_parse_ledger_variants(report, expected_names, expected_lines, expected_unrecognized):
    ledger = evaluator.parse_ledger(report)

    assert ledger.names == expected_names
    assert ledger.line_count == expected_lines
    assert ledger.unrecognized_count == expected_unrecognized


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git diff -- a.sh", True),
        ("git diff --stat", True),
        ("git -C /x diff", True),
        ("git status", False),
        ('echo "git diff"', False),
        ("ls", False),
        ("git status\ngit diff -- a", True),
        ("cd /w\ngit diff -- a", True),
        ("git status && \\\ngit diff -- a", True),
        ("git diff \\\n  --stat", True),
        ("git status\ncd /w\nls -la", False),
        ("git status \\\n  --short\ngit log", False),
    ],
)
def test_runs_git_diff_command_shapes(command, expected):
    assert (
        evaluator.runs_git_diff(command, _parse_git_command.build_records, _parse_git_command.FIELD_SEP)
        is expected
    )


def _transcript_line(record):
    return json.dumps(record)


def _assistant(call_id, name, tool_input):
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]},
    }


def _result_record(call_id, record_type="user", *, is_error=None):
    block = {"type": "tool_result", "tool_use_id": call_id, "content": "placeholder"}
    if is_error is not None:
        block["is_error"] = is_error
    return {"type": record_type, "message": {"role": "user", "content": [block]}}


def _extract(records):
    lines = [_transcript_line(record) if isinstance(record, dict) else record for record in records]
    return evaluator.extract_tool_calls(lines)


def test_extract_tool_calls_dedups_recurring_id_in_first_seen_order():
    calls = _extract([
        _assistant("c1", "Read", {"file_path": "/a"}),
        _assistant("c2", "Read", {"file_path": "/b"}),
        _assistant("c1", "Read", {"file_path": "/changed"}),
        _result_record("c1"),
        _result_record("c2"),
    ])

    assert [(call.name, call.tool_input["file_path"]) for call in calls] == [("Read", "/a"), ("Read", "/b")]


def test_extract_tool_calls_ignores_blob_copy_with_different_key_set():
    blob_block = {"type": "tool_use", "id": "c1", "name": "Read", "input": {"path": "/blob"}}
    blob = {"type": "api-request-blob", "message": {"role": "assistant", "content": [blob_block]}}

    calls = _extract([blob, _assistant("c1", "Read", {"file_path": "/real"}), _result_record("c1")])

    assert [call.tool_input for call in calls] == [{"file_path": "/real"}]


def test_extract_tool_calls_prefers_non_blob_result_over_blob_result():
    blob_result = _result_record("c1", record_type="api-request-blob", is_error=True)

    calls = _extract([_assistant("c1", "Read", {}), blob_result, _result_record("c1")])

    assert calls[0].outcome == SUCCESS


def test_extract_tool_calls_first_non_blob_result_wins():
    calls = _extract([_assistant("c1", "Read", {}), _result_record("c1", is_error=True), _result_record("c1")])

    assert calls[0].outcome == FAILED


def test_extract_tool_calls_result_only_in_blob_record_leaves_call_pending():
    calls = _extract([_assistant("c1", "Read", {}), _result_record("c1", record_type="api-request-blob")])

    assert calls[0].outcome == PENDING


def test_extract_tool_calls_pairs_results_out_of_call_order():
    calls = _extract([
        _assistant("c1", "Read", {}),
        _assistant("c2", "Bash", {}),
        _result_record("c2", is_error=True),
        _result_record("c1"),
    ])

    assert [(call.name, call.outcome) for call in calls] == [("Read", SUCCESS), ("Bash", FAILED)]


def test_extract_tool_calls_pairs_every_block_of_a_multi_block_record():
    assistant_record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "placeholder"},
                {"type": "tool_use", "id": "c1", "name": "Read", "input": {"file_path": "/a"}},
                {"type": "tool_use", "id": "c2", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    }
    result_record = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "placeholder"},
                {"type": "tool_result", "tool_use_id": "c2", "content": "placeholder", "is_error": True},
            ],
        },
    }

    calls = _extract([assistant_record, result_record])

    assert [(call.name, call.tool_input, call.outcome) for call in calls] == [
        ("Read", {"file_path": "/a"}, SUCCESS),
        ("Bash", {"command": "ls"}, FAILED),
    ]


@pytest.mark.parametrize(
    ("is_error", "expected_outcome"),
    [(None, SUCCESS), (False, SUCCESS), (True, FAILED)],
    ids=["absent", "false", "true"],
)
def test_extract_tool_calls_is_error_absent_false_and_true(is_error, expected_outcome):
    calls = _extract([_assistant("c1", "Read", {}), _result_record("c1", is_error=is_error)])

    assert calls[0].outcome == expected_outcome


def test_extract_tool_calls_skips_undecodable_line():
    calls = _extract(['{"type": "tool_use" not json', _assistant("c1", "Read", {}), _result_record("c1")])

    assert [call.outcome for call in calls] == [SUCCESS]


def test_claim_marker_on_existing_marker_is_claim_raced(tmp_path):
    marker_dir = tmp_path / "markers"
    (marker_dir / AGENT_ID).mkdir(parents=True)

    assert evaluator.claim_marker(str(marker_dir), AGENT_ID) == "allowed-claim-raced"


def test_claim_marker_on_free_agent_id_claims_it(tmp_path):
    marker_dir = tmp_path / "markers"

    assert evaluator.claim_marker(str(marker_dir), AGENT_ID) is None
    assert (marker_dir / AGENT_ID).is_dir()


@pytest.mark.parametrize("tool_input", [{}, {"message": None}, {"message": 7}, {"message": ["x"]}, "not-a-dict", None])
def test_read_report_non_string_message_reads_as_empty(tool_input):
    payload = {"hook_event_name": "PreToolUse", "tool_input": tool_input}

    assert evaluator._read_report(payload, "PreToolUse") == ""


@pytest.mark.parametrize("last_message", [None, 3, ["x"]])
def test_read_report_non_string_stop_message_reads_as_empty(last_message):
    payload = {"hook_event_name": "SubagentStop"}
    if last_message is not None:
        payload["last_assistant_message"] = last_message

    assert evaluator._read_report(payload, "SubagentStop") == ""


# ==========================================================================
# Tier 2 — contract
# ==========================================================================


def _fixture_lines():
    return FIXTURE_PATH.read_text().splitlines()


def _handback_ids_in(lines):
    return {
        block["id"]
        for line in lines
        for block in json.loads(line).get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "SubagentHandback"
    }


def _drop_handbacks(lines, ids_to_drop):
    kept = []
    for line in lines:
        record = json.loads(line)
        blocks = record.get("message", {}).get("content", [])
        referenced = {
            block.get("id") or block.get("tool_use_id") for block in blocks if isinstance(block, dict)
        }
        if referenced & ids_to_drop:
            continue
        kept.append(line)
    return kept


def _signature(calls):
    key_paths = ("file_path", "command", "message")
    return [
        (call.name, next(call.tool_input[key] for key in key_paths if key in call.tool_input), call.outcome == SUCCESS)
        for call in calls
    ]


FIXTURE_NON_HANDBACK_SIGNATURE = [
    ("Read", "/opt/placeholder/.claude/agents/staff-platform-engineer.md", True),
    ("Read", "~/.claude/agents/staff-sdet.md", True),
    ("Edit", "/placeholder/repo/scripts/example.sh", True),
    ("Write", "/placeholder/repo/tests/test_example.py", True),
    ("Bash", "git diff -- scripts/example.sh", True),
    ("Read", "/placeholder/repo/scripts/example.sh", True),
]


def test_fixture_extracts_to_its_expected_sequence_with_both_handbacks():
    calls = evaluator.extract_tool_calls(_fixture_lines())

    assert _signature(calls) == FIXTURE_NON_HANDBACK_SIGNATURE + [
        ("SubagentHandback", "Placeholder first report.", False),
        ("SubagentHandback", "Placeholder complete report.", True),
    ]


def test_fixture_pretooluse_variant_drops_the_final_handback():
    lines = _drop_handbacks(_fixture_lines(), {"call_08"})

    assert _signature(evaluator.extract_tool_calls(lines)) == FIXTURE_NON_HANDBACK_SIGNATURE + [
        ("SubagentHandback", "Placeholder first report.", False),
    ]


def test_fixture_plain_text_variant_drops_every_handback():
    lines = _drop_handbacks(_fixture_lines(), _handback_ids_in(_fixture_lines()))

    assert _signature(evaluator.extract_tool_calls(lines)) == FIXTURE_NON_HANDBACK_SIGNATURE


def test_fixture_parallel_batch_results_land_out_of_call_order():
    lines = _fixture_lines()
    result_order = [
        block["tool_use_id"]
        for line in lines
        for block in json.loads(line).get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

    assert result_order.index("call_06") < result_order.index("call_05")


def test_transcript_in_unknown_record_envelope_is_unparsed(tmp_path):
    workspace = Workspace(tmp_path)
    unknown_envelope = {"event": "tool", "payload": {"type": "tool_use", "id": "c1", "name": "Edit", "input": {}}}
    workspace.agent_transcript.parent.mkdir(parents=True)
    workspace.agent_transcript.write_text(json.dumps(unknown_envelope) + "\n")

    result = workspace.evaluate("PreToolUse", calls=None, report="done")

    assert result.verdict == "allowed-unparsed-transcript"
    assert result.stdout == ""


def _earlier_batch_then_handback_records(workspace, result_block):
    script = workspace.make_file("scripts/a.sh")
    return [
        _assistant("c1", "Edit", {"file_path": str(script), "old_string": "x\n", "new_string": "y\n"}),
        {"type": "user", "message": {"role": "user", "content": [result_block]}},
        _assistant("c2", "SubagentHandback", {"message": "done"}),
    ]


@pytest.mark.parametrize(
    "renamed_result_block",
    [
        {"type": "tool_response", "tool_use_id": "c1", "content": "placeholder"},
        {"type": "tool_result", "toolUseId": "c1", "content": "placeholder"},
    ],
    ids=["renamed-block-type", "renamed-id-key"],
)
def test_earlier_batch_whose_results_decode_to_no_tool_result_block_is_unparsed(tmp_path, renamed_result_block):
    workspace = Workspace(tmp_path)
    records = _earlier_batch_then_handback_records(workspace, renamed_result_block)
    workspace.agent_transcript.parent.mkdir(parents=True)
    workspace.agent_transcript.write_text("".join(_transcript_line(record) + "\n" for record in records))

    result = workspace.evaluate("PreToolUse", calls=None, report="done")

    assert (result.verdict, result.stdout) == ("allowed-unparsed-transcript", "")


def test_earlier_batch_whose_result_decodes_is_assessed_not_unparsed(tmp_path):
    workspace = Workspace(tmp_path)
    recognized_result_block = {"type": "tool_result", "tool_use_id": "c1", "content": "placeholder"}
    records = _earlier_batch_then_handback_records(workspace, recognized_result_block)
    workspace.agent_transcript.parent.mkdir(parents=True)
    workspace.agent_transcript.write_text("".join(_transcript_line(record) + "\n" for record in records))

    result = workspace.evaluate("PreToolUse", calls=None, report="done")

    assert (result.verdict, result.fields["edits"]) == ("sent-back", "1")


def test_results_that_pair_with_no_recorded_call_are_unparsed(tmp_path):
    workspace = Workspace(tmp_path)
    script = workspace.make_file("scripts/a.sh")
    records = [
        _assistant("c1", "Edit", {"file_path": str(script), "old_string": "x\n", "new_string": "y\n"}),
        _result_record("diverged-id"),
        _assistant("c2", "SubagentHandback", {"message": "done"}),
    ]
    workspace.agent_transcript.parent.mkdir(parents=True)
    workspace.agent_transcript.write_text("".join(_transcript_line(record) + "\n" for record in records))

    result = workspace.evaluate("PreToolUse", None, "done")

    assert (result.verdict, result.stdout) == ("allowed-unparsed-transcript", "")


def test_results_with_no_decodable_call_are_unparsed(tmp_path):
    workspace = Workspace(tmp_path)
    renamed_call_record = _assistant("c1", "Read", {"file_path": "placeholder"})
    renamed_call_record["message"]["content"][0]["type"] = "tool_call"
    records = [renamed_call_record, _result_record("c1")]
    workspace.agent_transcript.parent.mkdir(parents=True)
    workspace.agent_transcript.write_text("".join(_transcript_line(record) + "\n" for record in records))

    result = workspace.evaluate("PreToolUse", None, "done")

    assert (result.verdict, result.stdout) == ("allowed-unparsed-transcript", "")


def test_empty_transcript_is_clean_not_unparsed(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.agent_transcript.parent.mkdir(parents=True)
    workspace.agent_transcript.write_text("")

    result = workspace.evaluate("PreToolUse", None, "done")

    assert (result.verdict, result.fields["calls"]) == ("clean", "0")


def test_text_only_transcript_is_clean_not_unparsed(tmp_path):
    workspace = Workspace(tmp_path)
    text_only_record = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "cannot proceed, need input"}]},
    }
    workspace.agent_transcript.parent.mkdir(parents=True)
    workspace.agent_transcript.write_text(json.dumps(text_only_record) + "\n")

    result = workspace.evaluate("PreToolUse", None, "done")

    assert (result.verdict, result.fields["calls"], result.stdout) == ("clean", "0", "")


def _domain_table_rows():
    rows = []
    in_table = False
    for line in CODE_WRITER_PATH.read_text().splitlines():
        if line.startswith("| Your diff touches"):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            reviewer_tokens = [token for token in re.findall(r"`([^`]+)`", cells[1]) if not token.endswith(".md")]
            rows.append((cells[0], reviewer_tokens))
        elif in_table:
            break
    return rows


def test_ledger_example_in_code_writer_parses_to_its_concrete_reviewer():
    block = extract_skill_command(CODE_WRITER_PATH, "code-writer-self-review-ledger")

    ledger = evaluator.parse_ledger(block)

    assert ledger.names == ["staff-platform-engineer"]
    assert ledger.line_count == 1


@pytest.mark.parametrize(
    "mangle",
    [lambda text: text.replace("`", ""), lambda text: text.replace("`:", "`;")],
    ids=["backticks-dropped", "colon-replaced"],
)
def test_mangled_ledger_example_parses_to_nothing(mangle):
    block = extract_skill_command(CODE_WRITER_PATH, "code-writer-self-review-ledger")

    assert evaluator.parse_ledger(mangle(block)).line_count == 0


def _return_format_self_review_bullet():
    """First paragraph of the Return format's Self-review bullet, whitespace-normalized."""
    paragraph_lines = []
    for line in CODE_WRITER_PATH.read_text().splitlines():
        is_bullet_start = line.startswith("- **Self-review:**")
        is_continuation = bool(paragraph_lines) and bool(line.strip()) and not line.startswith("- ")
        if is_bullet_start or is_continuation:
            paragraph_lines.append(line)
        elif paragraph_lines:
            break
    return " ".join(line.strip() for line in paragraph_lines)


def test_return_format_self_review_bullet_asks_for_the_ledger_content_the_send_back_reason_asks_for():
    requested_content = evaluator.LEDGER_CONTENT_REQUEST.strip("<>")

    bullet_without_quotes = _return_format_self_review_bullet().replace('"', "")

    assert requested_content in bullet_without_quotes


def _return_format_section():
    """Text of code-writer.md's Return format section, up to the next level-2 heading."""
    section_lines = []
    in_section = False
    for line in CODE_WRITER_PATH.read_text().splitlines():
        if line.startswith("## "):
            if in_section:
                break
            in_section = line == "## Return format"
        elif in_section:
            section_lines.append(line)
    return "\n".join(section_lines)


def test_return_format_section_carries_the_label_that_ends_the_ledger():
    assert evaluator.STILL_UNCERTAIN_LABEL in _return_format_section()


def test_domain_table_maps_code_writer_floor_rows_to_the_evaluator_reviewers():
    rows = dict(_domain_table_rows())

    assert rows["Test code"] == ["staff-sdet"]
    assert rows["CI/CD config, IaC, shell scripts, build, tool and environment config"] == ["staff-platform-engineer"]
    prose_names = next(names for label, names in rows.items() if label.startswith("Comments, docstrings"))
    assert prose_names == ["comment-discipline-reviewer", "ai-instruction-and-memory-files"]
    last_label, last_names = _domain_table_rows()[-1]
    assert last_label.startswith("Any other file")
    assert last_names == ["staff-backend-engineer"]


def test_evaluator_accepted_names_equal_the_domain_table_names():
    table_names = {name for _label, names in _domain_table_rows() for name in names}

    assert set(evaluator.ACCEPTED_REVIEWERS) == table_names


SKILL_REVIEWER_READ_PATH = "~/.claude/skills/ai-instruction-and-memory-files/SKILL.md"


def _literal_reviewer_read_path(reviewer_name):
    """The tilde path code-writer.md tells a writer to Read, spelled out here rather than built by the evaluator."""
    if reviewer_name == "ai-instruction-and-memory-files":
        return SKILL_REVIEWER_READ_PATH
    return f"~/.claude/agents/{reviewer_name}.md"


def _stowed_file_for_tilde_path(tilde_path):
    """Map a `~/.claude/...` path through the stow layout to the file in this repository."""
    agents_prefix = "~/.claude/agents/"
    skills_prefix = "~/.claude/skills/"
    if tilde_path.startswith(agents_prefix):
        return CLAUDE_DIR / "agents" / tilde_path[len(agents_prefix):]
    assert tilde_path.startswith(skills_prefix), tilde_path
    return REPO_ROOT / "claude-skills" / "skills" / tilde_path[len(skills_prefix):]


@pytest.mark.parametrize("reviewer_name", evaluator.ACCEPTED_REVIEWERS)
def test_each_accepted_name_reads_from_its_literal_path_which_resolves_to_a_reviewer_file(reviewer_name):
    read_path = evaluator.reviewer_read_path(reviewer_name)

    assert read_path == _literal_reviewer_read_path(reviewer_name)
    assert _stowed_file_for_tilde_path(read_path).is_file()
    assert evaluator.reviewer_name_for_read_path(read_path) == reviewer_name


def test_code_writer_names_the_skill_reviewer_read_path_literally():
    assert SKILL_REVIEWER_READ_PATH in CODE_WRITER_PATH.read_text()


def _code_writer_declared_tools():
    tools_line = next(line for line in CODE_WRITER_PATH.read_text().splitlines() if line.startswith("tools:"))
    return {tool.strip() for tool in tools_line.removeprefix("tools:").split(",")}


def test_evaluator_edit_tools_equal_the_editing_tools_code_writer_declares():
    known_editing_tools = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

    assert _code_writer_declared_tools() & known_editing_tools == set(evaluator.EDIT_TOOLS)


def test_cited_heading_exists_in_code_writer():
    headings = heading_texts(CODE_WRITER_PATH.read_text())

    assert normalize_heading(evaluator.SELF_REVIEW_HEADING) in headings


# ==========================================================================
# Tier 3 — evaluator main() against tmp_path workspaces
# ==========================================================================

Result = namedtuple("Result", "status stdout envelope verdict fields marker_exists new_log_lines")


def _call(name, tool_input, outcome=SUCCESS):
    return evaluator.ToolCall(name, tool_input, outcome)


def _dispatch_prompt_record():
    return {"type": "user", "message": {"role": "user", "content": "Placeholder dispatch prompt."}}


def _records_for(calls):
    records = [_dispatch_prompt_record()]
    for index, call in enumerate(calls):
        call_id = "call_%04d" % index
        records.append(_assistant(call_id, call.name, call.tool_input))
        if call.outcome == PENDING:
            continue
        records.append(_result_record(call_id, is_error=True if call.outcome == FAILED else None))
    return records


def _read_call(path, outcome=SUCCESS):
    return _call("Read", {"file_path": str(path)}, outcome)


def _reviewer_read(reviewer_name, outcome=SUCCESS):
    return _call("Read", {"file_path": _literal_reviewer_read_path(reviewer_name)}, outcome)


def _git_diff_call(outcome=SUCCESS):
    return _call("Bash", {"command": "git diff -- scripts/a.sh"}, outcome)


def _edit_call(path, new_string="y\n", old_string="x\n", outcome=SUCCESS):
    return _call("Edit", {"file_path": str(path), "old_string": old_string, "new_string": new_string}, outcome)


def _write_call(path, content="y\n", outcome=SUCCESS):
    return _call("Write", {"file_path": str(path), "content": content}, outcome)


def _parse_log_line(line):
    parts = line.split()
    fields = dict(part.split("=", 1) for part in parts[3:] if "=" in part)
    return parts[2], fields


def _assess(calls, report):
    build_records, field_separator = evaluator.load_git_record_builder()
    return evaluator.assess(calls, report, build_records, field_separator)


def _item_fields(assessment):
    return [(item.kind, item.name, item.need_read, item.need_ledger) for item in assessment.items]


class _BrokenPipeStdout(io.StringIO):
    def write(self, text):
        raise BrokenPipeError("placeholder")


class Workspace:
    """A tmp_path with a fake repository, a config dir and the writer's transcript."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.repo = tmp_path / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.config_dir = tmp_path / "config"
        self.config_dir.mkdir()
        self.marker_dir = self.config_dir / ".code-writer-self-review.d"
        self.log_path = self.config_dir / ".code-writer-self-review.log"
        self.session_transcript = tmp_path / "session.jsonl"
        self.subagents_dir = tmp_path / "session" / "subagents"
        self.agent_transcript = self.transcript_path_for(AGENT_ID)

    def transcript_path_for(self, agent_id):
        return self.subagents_dir / f"agent-{agent_id}.jsonl"

    def marker_path_for(self, agent_id):
        return self.marker_dir / agent_id

    def make_file(self, relative, text="x\n", base=None):
        path = (base or self.repo) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_transcript(self, calls, agent_id=AGENT_ID):
        transcript_path = self.transcript_path_for(agent_id)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text("".join(_transcript_line(r) + "\n" for r in _records_for(calls)))

    def payload(self, event, report, *, stop_hook_active=False, tool_input_extra=None, agent_id=AGENT_ID):
        base: dict[str, object] = {
            "hook_event_name": event, "agent_type": "code-writer", "agent_id": agent_id, "cwd": str(self.repo),
        }
        if event == "PreToolUse":
            tool_input = {"message": report}
            tool_input.update(tool_input_extra or {})
            base.update(
                tool_name="SubagentHandback", tool_input=tool_input, transcript_path=str(self.session_transcript)
            )
        else:
            base.update(
                agent_transcript_path=str(self.transcript_path_for(agent_id)),
                last_assistant_message=report,
                stop_hook_active=stop_hook_active,
            )
        return base

    def evaluate(self, event, calls, report, *, payload=None, stdout=None, agent_id=AGENT_ID, **payload_options):
        if calls is not None:
            self.write_transcript(calls, agent_id)
        if payload is None:
            payload = self.payload(event, report, agent_id=agent_id, **payload_options)
        lines_before = self.log_path.read_text().splitlines() if self.log_path.is_file() else []
        stdout = io.StringIO() if stdout is None else stdout
        status = evaluator.main(
            [agent_id, str(self.marker_dir), str(self.log_path)], stdin=io.StringIO(json.dumps(payload)), stdout=stdout
        )
        lines_after = self.log_path.read_text().splitlines() if self.log_path.is_file() else []
        new_lines = lines_after[len(lines_before):]
        verdict, fields = _parse_log_line(new_lines[-1]) if new_lines else (None, {})
        output = stdout.getvalue()
        return Result(
            status, output, json.loads(output) if output else None, verdict, fields,
            self.marker_path_for(agent_id).exists(), new_lines,
        )

    def sh_script_calls(self, *, reviewer_reads=("staff-platform-engineer",), git_diff=True, script_name="scripts/a.sh"):
        script = self.make_file(script_name)
        calls = [_reviewer_read(name) for name in reviewer_reads]
        calls.append(_edit_call(script))
        if git_diff:
            calls.append(_git_diff_call())
        return script, calls


def _assert_no_git_entry_above(path):
    for ancestor in [path, *path.parents]:
        if (ancestor / ".git").exists():
            pytest.skip("an ancestor of the scratch directory holds a .git entry, so the no-repository case cannot be built")


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def scrubbed_git_environment(monkeypatch):
    """Drop git variables a hook-run pytest inherits, which would point `git worktree add` at the wrong repository."""
    for variable in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(variable, raising=False)


def test_sh_edit_with_read_ledger_and_diff_reread_is_clean(workspace):
    _script, calls = workspace.sh_script_calls()

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert (result.status, result.verdict, result.stdout) == (0, "clean", "")
    assert (result.fields["edits"], result.fields["changed"], result.fields["failed"]) == ("1", "1", "-")


def test_skipped_reviewer_read_logs_failed_requirements_and_sends_back(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert result.verdict == "sent-back"
    assert (result.fields["ledger"], result.fields["reads"], result.fields["failed"]) == ("1", "0", "R2,R3,R4")


LEDGER_INSTRUCTION_FILE = (
    "**Self-review:**\n"
    "- `comment-discipline-reviewer`: prose angles applied; clean\n"
    "- `ai-instruction-and-memory-files`: altitude angle applied; clean\n\n"
    "**Still uncertain:** nothing"
)


def _instruction_file_calls(workspace, *, skill_read_path):
    instruction_file = workspace.make_file("CLAUDE.md", "x\n")
    calls = [_reviewer_read("comment-discipline-reviewer")]
    if skill_read_path is not None:
        calls.append(_call("Read", {"file_path": skill_read_path}))
    calls.extend([_edit_call(instruction_file), _git_diff_call()])
    return calls


@pytest.mark.parametrize(
    "skill_read_path",
    [SKILL_REVIEWER_READ_PATH, "/opt/placeholder/.claude/skills/ai-instruction-and-memory-files/SKILL.md"],
    ids=["tilde-path", "absolute-path"],
)
def test_instruction_file_edit_is_clean_when_the_skill_reviewer_file_was_read(workspace, skill_read_path):
    calls = _instruction_file_calls(workspace, skill_read_path=skill_read_path)

    result = workspace.evaluate("PreToolUse", calls, LEDGER_INSTRUCTION_FILE)

    assert (result.verdict, result.fields["failed"]) == ("clean", "-")


def test_instruction_file_edit_without_the_skill_reviewer_read_names_its_literal_path(workspace):
    calls = _instruction_file_calls(workspace, skill_read_path=None)

    result = workspace.evaluate("PreToolUse", calls, LEDGER_INSTRUCTION_FILE)

    reason = result.envelope["hookSpecificOutput"]["permissionDecisionReason"]
    assert (result.verdict, result.fields["failed"]) == ("sent-back", "R3,R4")
    assert SKILL_REVIEWER_READ_PATH in reason


def test_ledger_format_miss_logs_ledger_zero_with_reads_above_zero(workspace):
    _script, calls = workspace.sh_script_calls()

    result = workspace.evaluate("PreToolUse", calls, "All done, everything is clean.")

    assert result.verdict == "sent-back"
    assert (result.fields["ledger"], result.fields["reads"], result.fields["failed"]) == ("0", "1", "R2,R4")


def test_missing_diff_reread_after_last_edit_fails_r5_only(workspace):
    _script, calls = workspace.sh_script_calls(git_diff=False)

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert (result.verdict, result.fields["failed"]) == ("sent-back", "R5")


def test_diff_before_the_last_edit_does_not_count_as_reread(workspace):
    script, calls = workspace.sh_script_calls()
    calls.append(_edit_call(script, new_string="z\n", old_string="y\n"))

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert (result.verdict, result.fields["failed"]) == ("sent-back", "R5")


def test_scratch_edit_in_no_repository_after_the_diff_does_not_trip_the_reread_rule(workspace):
    scratch_dir = workspace.root / "scratch"
    _assert_no_git_entry_above(scratch_dir)
    scratch_script = workspace.make_file("s.sh", base=scratch_dir)
    _script, calls = workspace.sh_script_calls()
    calls.append(_edit_call(scratch_script))

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert (result.verdict, result.fields["changed"], result.fields["dropped"]) == ("clean", "1", "1")


@pytest.mark.parametrize(
    "trailing_call_factory",
    [
        lambda workspace: _reviewer_read("staff-platform-engineer"),
        lambda workspace: _read_call(workspace.make_file("src/unchanged.py")),
        lambda workspace: _call("Bash", {"command": "git status --short"}),
    ],
    ids=["reviewer-read", "read-of-an-unchanged-path", "bash-that-is-not-a-diff"],
)
def test_trailing_call_that_is_not_a_diff_or_changed_path_read_fails_r5_alone(workspace, trailing_call_factory):
    _script, calls = workspace.sh_script_calls(git_diff=False)
    calls.append(trailing_call_factory(workspace))

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert (result.verdict, result.fields["failed"]) == ("sent-back", "R5")


@pytest.mark.parametrize("unlanded_outcome", [PENDING, FAILED])
@pytest.mark.parametrize("unlanded_call_kind", ["reviewer-read", "diff"])
def test_unlanded_call_does_not_count_but_landed_control_does(workspace, unlanded_call_kind, unlanded_outcome):
    script = workspace.make_file("scripts/a.sh")

    def calls_with(outcome):
        reviewer_outcome = outcome if unlanded_call_kind == "reviewer-read" else SUCCESS
        diff_outcome = outcome if unlanded_call_kind == "diff" else SUCCESS
        return [
            _reviewer_read("staff-platform-engineer", reviewer_outcome),
            _edit_call(script),
            _git_diff_call(diff_outcome),
        ]

    unlanded_result = workspace.evaluate("PreToolUse", calls_with(unlanded_outcome), LEDGER_PLATFORM)
    (workspace.marker_dir / AGENT_ID).rmdir()
    control_result = workspace.evaluate("PreToolUse", calls_with(SUCCESS), LEDGER_PLATFORM)

    assert unlanded_result.verdict == "sent-back"
    assert control_result.verdict == "clean"


def test_pending_edit_does_not_count_but_landed_control_does(workspace):
    script = workspace.make_file("scripts/a.sh")

    pending_result = workspace.evaluate("PreToolUse", [_edit_call(script, outcome=PENDING)], "done")
    control_result = workspace.evaluate("PreToolUse", [_edit_call(script)], "done")

    assert (pending_result.verdict, pending_result.fields["edits"]) == ("clean", "0")
    assert (control_result.verdict, control_result.fields["edits"]) == ("sent-back", "1")


def test_failed_edit_does_not_count_as_an_edit(workspace):
    script = workspace.make_file("scripts/a.sh")

    result = workspace.evaluate("PreToolUse", [_edit_call(script, outcome=FAILED)], "done")

    assert (result.verdict, result.fields["edits"]) == ("clean", "0")


def test_unchanged_comment_line_in_edit_context_requires_no_prose_reviewer(workspace):
    script = workspace.make_file("src/a.py", "# keep\nx = 1\n")
    calls = [
        _reviewer_read("staff-backend-engineer"),
        _edit_call(script, old_string="# keep\nx = 1\n", new_string="# keep\nx = 2\n"),
        _read_call(script),
    ]

    result = workspace.evaluate("PreToolUse", calls, "**Self-review:**\n- `staff-backend-engineer`: clean")

    assert result.verdict == "clean"


def test_added_comment_line_requires_the_prose_reviewer(workspace):
    script = workspace.make_file("src/a.py", "x = 1\n")
    calls = [
        _reviewer_read("staff-backend-engineer"),
        _edit_call(script, old_string="x = 1\n", new_string="# why\nx = 1\n"),
        _read_call(script),
    ]

    report = "**Self-review:**\n- `staff-backend-engineer`: clean"

    result = workspace.evaluate("PreToolUse", calls, report)

    assert result.verdict == "sent-back"
    assert _item_fields(_assess(calls, report)) == [(evaluator.KIND_REVIEWER, "comment-discipline-reviewer", True, True)]


def test_comment_added_to_an_edit_outside_any_repository_demands_no_reviewer(workspace):
    scratch_dir = workspace.root / "scratch"
    _assert_no_git_entry_above(scratch_dir)
    scratch_module = workspace.make_file("m.py", "x = 1\n", base=scratch_dir)
    _script, calls = workspace.sh_script_calls()
    calls.append(_edit_call(scratch_module, old_string="x = 1\n", new_string="# why\nx = 1\n"))

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert (result.verdict, result.fields["changed"], result.fields["dropped"]) == ("clean", "1", "1")


def test_catch_all_requires_a_read_backed_ledger_line_when_a_path_changed(workspace):
    script = workspace.make_file("src/a.py", "x = 1\n")
    calls = [_edit_call(script), _read_call(script)]

    result = workspace.evaluate("PreToolUse", calls, "done")

    assert (result.verdict, result.fields["failed"]) == ("sent-back", "R2")
    assert _item_fields(_assess(calls, "done")) == [(evaluator.KIND_ANY_REVIEWER, None, True, True)]


def test_symlinked_edited_path_counts_by_its_resolved_location(workspace):
    real_script = workspace.make_file("scripts/real.sh")
    link_dir = workspace.root / "links"
    link_dir.mkdir()
    _assert_no_git_entry_above(link_dir)
    link = link_dir / "a.sh"
    link.symlink_to(real_script)
    calls = [_edit_call(link), _git_diff_call()]

    result = workspace.evaluate("PreToolUse", calls, "done")

    assert (result.verdict, result.fields["changed"]) == ("sent-back", "1")


def test_tilde_path_reread_of_a_changed_file_counts_for_r5(workspace, monkeypatch):
    monkeypatch.setenv("HOME", str(workspace.root))
    script = workspace.make_file("scripts/a.sh")
    base_calls = [_reviewer_read("staff-platform-engineer"), _edit_call(script)]

    without_reread = workspace.evaluate("PreToolUse", base_calls, LEDGER_PLATFORM)
    (workspace.marker_dir / AGENT_ID).rmdir()
    with_tilde_reread = workspace.evaluate(
        "PreToolUse", [*base_calls, _call("Read", {"file_path": "~/repo/scripts/a.sh"})], LEDGER_PLATFORM
    )

    assert without_reread.fields["failed"] == "R5"
    assert with_tilde_reread.verdict == "clean"


@pytest.mark.parametrize(
    "ancestor_segments",
    [("tests",), ("spec",), (".github", "workflows")],
    ids=["tests", "spec", "github-workflows"],
)
def test_ancestor_directories_above_the_repository_root_do_not_feed_the_path_floor(tmp_path, ancestor_segments):
    workspace = Workspace(tmp_path.joinpath(*ancestor_segments))
    source = workspace.make_file("src/a.py", "x = 1\n")
    calls = [_reviewer_read("staff-backend-engineer"), _edit_call(source), _read_call(source)]

    result = workspace.evaluate("PreToolUse", calls, "**Self-review:**\n- `staff-backend-engineer`: clean")

    assert (result.verdict, result.fields["changed"], result.fields["failed"]) == ("clean", "1", "-")


def _add_linked_worktree(main_checkout, worktree_path, branch):
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", branch, str(worktree_path)], cwd=main_checkout, check=True
    )


def test_edits_in_a_linked_worktree_nested_under_the_main_checkout_count(tmp_path, scrubbed_git_environment):
    workspace = Workspace(tmp_path)
    main_checkout = init_git_repo_with_commit(tmp_path / "main")
    nested_worktree = main_checkout / ".claude" / "worktrees" / "feature"
    _add_linked_worktree(main_checkout, nested_worktree, "feature-nested")
    script = workspace.make_file("scripts/a.sh", base=nested_worktree)

    result = workspace.evaluate("PreToolUse", [_edit_call(script), _git_diff_call()], "done")

    assert (result.verdict, result.fields["changed"]) == ("sent-back", "1")


def test_worktree_directory_name_nested_under_the_main_checkout_does_not_feed_the_path_floor(
    tmp_path, scrubbed_git_environment
):
    workspace = Workspace(tmp_path)
    main_checkout = init_git_repo_with_commit(tmp_path / "main")
    nested_worktree = main_checkout / ".claude" / "worktrees" / "spec"
    _add_linked_worktree(main_checkout, nested_worktree, "feature-spec")
    source = workspace.make_file("src/a.py", base=nested_worktree)
    calls = [_reviewer_read("staff-backend-engineer"), _edit_call(source), _read_call(source)]

    result = workspace.evaluate("PreToolUse", calls, "**Self-review:**\n- `staff-backend-engineer`: clean")

    assert (result.verdict, result.fields["failed"], result.fields["changed"]) == ("clean", "-", "1")


def test_edits_in_a_sibling_linked_worktree_count_by_its_root_git_file(tmp_path, scrubbed_git_environment):
    workspace = Workspace(tmp_path)
    main_checkout = init_git_repo_with_commit(tmp_path / "main")
    sibling_worktree = tmp_path / "sibling-wt"
    _add_linked_worktree(main_checkout, sibling_worktree, "feature-sibling")
    assert (sibling_worktree / ".git").is_file()
    script = workspace.make_file("scripts/a.sh", base=sibling_worktree)

    result = workspace.evaluate("PreToolUse", [_edit_call(script), _git_diff_call()], "done")

    assert (result.verdict, result.fields["changed"]) == ("sent-back", "1")


def test_scratch_edit_in_no_repository_drops_beside_a_repository_edit(workspace):
    scratch_dir = workspace.root / "scratch"
    _assert_no_git_entry_above(scratch_dir)
    scratch_script = workspace.make_file("s.sh", base=scratch_dir)
    repo_script = workspace.make_file("scripts/a.sh")
    calls = [_edit_call(scratch_script), _edit_call(repo_script), _git_diff_call()]

    result = workspace.evaluate("PreToolUse", calls, "done")

    assert (result.verdict, result.fields["changed"], result.fields["dropped"]) == ("sent-back", "1", "1")


def test_edits_all_in_no_repository_or_deleted_log_allowed_no_repo_path(workspace):
    scratch_dir = workspace.root / "scratch"
    _assert_no_git_entry_above(scratch_dir)
    scratch_script = workspace.make_file("s.sh", base=scratch_dir)
    deleted_path = workspace.repo / "gone.sh"
    (workspace.marker_dir / AGENT_ID).mkdir(parents=True)

    result = workspace.evaluate("PreToolUse", [_edit_call(scratch_script), _edit_call(deleted_path)], "done")

    assert (result.verdict, result.fields["changed"], result.fields["dropped"]) == ("allowed-no-repo-path", "0", "2")
    assert result.marker_exists is False


def test_send_back_for_unread_ledger_name_then_read_with_edits_in_no_repository(workspace):
    scratch_dir = workspace.root / "scratch"
    _assert_no_git_entry_above(scratch_dir)
    scratch_script = workspace.make_file("s.sh", base=scratch_dir)
    edit = _edit_call(scratch_script)

    first = workspace.evaluate("PreToolUse", [edit], LEDGER_PLATFORM)
    second = workspace.evaluate("PreToolUse", [_reviewer_read("staff-platform-engineer"), edit], LEDGER_PLATFORM)

    assert (first.verdict, first.marker_exists) == ("sent-back", True)
    assert (second.verdict, second.marker_exists) == ("allowed-no-repo-path", False)


def test_missing_transcript_logs_allowed_no_transcript(workspace):
    payload = workspace.payload("PreToolUse", "done")

    result = workspace.evaluate("PreToolUse", None, "done", payload=payload)

    assert (result.verdict, result.stdout) == ("allowed-no-transcript", "")


def test_transcript_found_below_the_subagents_directory_when_not_directly_derived(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    nested_transcript = workspace.root / "session" / "subagents" / "workflow" / f"agent-{AGENT_ID}.jsonl"
    nested_transcript.parent.mkdir(parents=True)
    nested_transcript.write_text("".join(_transcript_line(r) + "\n" for r in _records_for(calls)))

    result = workspace.evaluate("PreToolUse", None, "done")

    assert result.verdict == "sent-back"


@pytest.mark.parametrize("entry_kind", ["dangling-symlink", "directory"])
def test_non_regular_entry_named_like_the_transcript_below_subagents_is_skipped(workspace, entry_kind):
    entry = workspace.subagents_dir / "workflow" / f"agent-{AGENT_ID}.jsonl"
    entry.parent.mkdir(parents=True)
    if entry_kind == "directory":
        entry.mkdir()
    else:
        entry.symlink_to(workspace.root / "no-such-target")

    result = workspace.evaluate("PreToolUse", None, "done")

    assert (result.verdict, result.stdout) == ("allowed-no-transcript", "")


def test_read_only_marker_directory_logs_allowed_claim_failed_and_prints_nothing(workspace):
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    workspace.marker_dir.mkdir()
    workspace.marker_dir.chmod(0o500)
    try:
        with pytest.raises(OSError):
            (workspace.marker_dir / "probe").mkdir()
        _script, calls = workspace.sh_script_calls(reviewer_reads=())

        result = workspace.evaluate("PreToolUse", calls, "done")
    finally:
        workspace.marker_dir.chmod(0o700)

    assert (result.verdict, result.stdout, result.marker_exists) == ("allowed-claim-failed", "", False)


def test_log_path_that_is_a_directory_still_emits_the_send_back(workspace):
    workspace.log_path.mkdir()
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    workspace.write_transcript(calls)
    stdout = io.StringIO()

    status = evaluator.main(
        [AGENT_ID, str(workspace.marker_dir), str(workspace.log_path)],
        stdin=io.StringIO(json.dumps(workspace.payload("PreToolUse", "done"))),
        stdout=stdout,
    )

    assert status == 0
    assert json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_allow_envelope_preserves_every_other_tool_input_field(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    options = {"tool_input_extra": {"summary": "kept", "count": 3}}

    first = workspace.evaluate("PreToolUse", calls, "report body", **options)
    second = workspace.evaluate("PreToolUse", calls, "report body", **options)

    updated = second.envelope["hookSpecificOutput"]["updatedInput"]
    assert first.verdict == "sent-back"
    assert second.verdict == "passed-flagged"
    assert (updated["summary"], updated["count"]) == ("kept", 3)
    assert updated["message"].startswith("SELF-REVIEW INCOMPLETE (hook-verified): ")
    assert updated["message"].endswith("\n\nreport body")


@pytest.mark.parametrize("message", [None, 7, ["x"]], ids=["null", "number", "list"])
def test_non_string_message_reads_as_empty_report_and_second_return_is_unflaggable(workspace, message):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    payload = workspace.payload("PreToolUse", "unused")
    payload["tool_input"] = {"message": message}

    first = workspace.evaluate("PreToolUse", calls, "unused", payload=payload)
    second = workspace.evaluate("PreToolUse", calls, "unused", payload=payload)

    assert (first.verdict, second.verdict, second.stdout) == ("sent-back", "passed-unflaggable", "")


def test_absent_message_reads_as_empty_report_and_second_return_is_unflaggable(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    payload = workspace.payload("PreToolUse", "unused")
    payload["tool_input"] = {}

    first = workspace.evaluate("PreToolUse", calls, "unused", payload=payload)
    second = workspace.evaluate("PreToolUse", calls, "unused", payload=payload)

    assert (first.verdict, second.verdict, second.stdout) == ("sent-back", "passed-unflaggable", "")


def test_missing_items_are_ordered_by_name_deduplicated_and_count_unrecognized_names(workspace):
    script = workspace.make_file("scripts/a.sh")
    test_file = workspace.make_file("tests/test_a.py")
    calls = [_edit_call(script), _write_call(test_file)]
    report = "**Self-review:**\n- `staff-platform-engineer`: a\n- `bogus-name`: b\n- `bogus-name`: c\n"

    result = workspace.evaluate("PreToolUse", calls, report)

    assert _item_fields(_assess(calls, report)) == [
        (evaluator.KIND_REVIEWER, "staff-platform-engineer", True, False),
        (evaluator.KIND_REVIEWER, "staff-sdet", True, True),
        (evaluator.KIND_REREAD, None, False, False),
    ]
    assert result.fields["unrecognized"] == "1"


def test_ledger_only_item_asks_for_the_ledger_line_and_no_read(workspace):
    _script, calls = workspace.sh_script_calls()

    assert _item_fields(_assess(calls, "done")) == [(evaluator.KIND_REVIEWER, "staff-platform-engineer", False, True)]


def test_send_back_reasons_carry_the_complete_report_request_and_the_stop_label_instruction(workspace):
    script = workspace.make_file("scripts/a.sh")
    calls = [_edit_call(script)]
    report = "**Self-review:**\n- `staff-platform-engineer`: a\n- `bogus-name`: b\n"

    pretooluse = workspace.evaluate("PreToolUse", calls, report)
    (workspace.marker_dir / AGENT_ID).rmdir()
    stop = workspace.evaluate("SubagentStop", calls, report)

    pretooluse_reason = pretooluse.envelope["hookSpecificOutput"]["permissionDecisionReason"]
    stop_reason = stop.envelope["reason"]
    assert "complete report" in pretooluse_reason and "SubagentHandback" in pretooluse_reason
    assert ", ".join(evaluator.ACCEPTED_REVIEWERS) in pretooluse_reason
    assert "bogus-name" not in pretooluse_reason and "bogus-name" not in stop_reason
    assert "goes through" not in pretooluse_reason and "second" not in pretooluse_reason
    assert "complete report" in stop_reason
    assert "start your report with the line `SELF-REVIEW INCOMPLETE: " in stop_reason


@pytest.mark.parametrize("relative_path", ["scripts/a.sh", "src/a.py"], ids=["named-reviewer-item", "any-reviewer-item"])
def test_send_back_reason_asks_for_each_angle_its_finding_what_was_fixed_or_clean(workspace, relative_path):
    calls = [_edit_call(workspace.make_file(relative_path)), _git_diff_call()]

    reason = workspace.evaluate("PreToolUse", calls, "done").envelope["hookSpecificOutput"]["permissionDecisionReason"]

    assert "<each applied angle, its finding and what you fixed, or clean>" in reason


@pytest.mark.parametrize(
    ("relative_path", "expected_names"),
    [("scripts/a.sh", ["staff-platform-engineer"]), ("src/a.py", ["staff-backend-engineer"])],
    ids=["named-reviewer-item", "any-reviewer-item"],
)
def test_ledger_line_the_send_back_reason_requests_parses_as_a_ledger_line(workspace, relative_path, expected_names):
    calls = [_edit_call(workspace.make_file(relative_path)), _git_diff_call()]
    build_records, field_separator = evaluator.load_git_record_builder()
    assessment = evaluator.assess(calls, "done", build_records, field_separator)
    reason = evaluator.build_reason(assessment.items, assessment.unrecognized, "PreToolUse")
    requested_line = re.search(r"`([^`\n]+)`: (<[^>\n]+>)", reason)
    assert requested_line is not None
    requested_name, content_placeholder = requested_line.groups()
    # An any-reviewer item asks for a `<reviewer>` placeholder, which the writer replaces with a chosen reviewer.
    ledger_name = expected_names[0] if requested_name == "<reviewer>" else requested_name
    requested_template = requested_line.group(0)
    ledger_line = requested_template.replace(requested_name, ledger_name).replace(
        content_placeholder, "quoting angle applied; clean"
    )

    ledger = evaluator.parse_ledger(f"**Self-review:**\n{ledger_line}\n\n**Still uncertain:** nothing")

    assert (ledger.names, ledger.line_count) == (expected_names, 1)


# ---- verdict table -------------------------------------------------------


def test_stop_sent_back_then_second_stop_without_label_is_passed_unlabeled(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    first = workspace.evaluate("SubagentStop", calls, "done")
    second = workspace.evaluate("SubagentStop", calls, "done", stop_hook_active=True)

    assert (first.verdict, first.marker_exists) == ("sent-back", True)
    assert first.envelope["decision"] == "block" and "hookSpecificOutput" not in first.envelope
    assert (second.verdict, second.stdout, second.marker_exists) == ("passed-unlabeled", "", False)


def test_stop_second_return_starting_with_the_incomplete_line_is_passed_labeled(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    workspace.evaluate("SubagentStop", calls, "done")
    second = workspace.evaluate("SubagentStop", calls, "\n\nSELF-REVIEW INCOMPLETE: no Read of x\nbody")

    assert second.verdict == "passed-labeled"


def test_stop_second_return_with_the_incomplete_line_on_its_second_line_is_passed_unlabeled(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    workspace.evaluate("SubagentStop", calls, "done")
    second = workspace.evaluate("SubagentStop", calls, "Summary of the work\nSELF-REVIEW INCOMPLETE: no Read of x")

    assert second.verdict == "passed-unlabeled"


def test_stop_with_stop_hook_active_and_no_marker_never_blocks(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    result = workspace.evaluate("SubagentStop", calls, "done", stop_hook_active=True)

    assert (result.verdict, result.stdout, result.marker_exists) == ("allowed-continuation", "", False)


def test_stop_after_any_handback_call_is_not_owned_and_keeps_the_marker(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    (workspace.marker_dir / AGENT_ID).mkdir(parents=True)

    result = workspace.evaluate(
        "SubagentStop", [*calls, _call("SubagentHandback", {"message": "r"}, FAILED)], "done"
    )

    assert (result.verdict, result.stdout, result.marker_exists) == ("not-owned", "", True)


def test_stop_after_only_a_pending_handback_is_not_owned_and_keeps_the_marker(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    (workspace.marker_dir / AGENT_ID).mkdir(parents=True)

    pending = workspace.evaluate("SubagentStop", [*calls, _call("SubagentHandback", {"message": "r"}, PENDING)], "done")
    (workspace.marker_dir / AGENT_ID).rmdir()
    control = workspace.evaluate("SubagentStop", calls, "done")

    assert (pending.verdict, pending.stdout, pending.marker_exists) == ("not-owned", "", True)
    assert control.verdict == "sent-back"


def test_first_turn_pending_reviewer_read_and_handback_batch_is_assessed_not_unparsed(workspace):
    calls = [_reviewer_read("staff-platform-engineer", PENDING), _call("SubagentHandback", {"message": "r"}, PENDING)]

    result = workspace.evaluate("PreToolUse", calls, LEDGER_PLATFORM)

    assert (result.verdict, result.fields["reads"]) == ("sent-back", "0")


def test_stop_clean_removes_a_leftover_marker(workspace):
    _script, calls = workspace.sh_script_calls()
    (workspace.marker_dir / AGENT_ID).mkdir(parents=True)

    result = workspace.evaluate("SubagentStop", calls, LEDGER_PLATFORM)

    assert (result.verdict, result.marker_exists) == ("clean", False)


def test_pretooluse_sent_back_then_plain_text_stop_is_not_owned_then_handback_passes_flagged(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    handback_denied = _call("SubagentHandback", {"message": "done"}, FAILED)

    first = workspace.evaluate("PreToolUse", calls, "done")
    stop_after_denied_handback = workspace.evaluate("SubagentStop", [*calls, handback_denied], "done")
    second = workspace.evaluate("PreToolUse", None, "done")

    assert (first.verdict, first.marker_exists) == ("sent-back", True)
    assert (stop_after_denied_handback.verdict, stop_after_denied_handback.marker_exists) == ("not-owned", True)
    assert (second.verdict, second.marker_exists) == ("passed-flagged", False)


def test_pretooluse_second_return_with_the_denied_handback_in_the_transcript_ends_passed_flagged(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    denied_handback = _call("SubagentHandback", {"message": "done"}, FAILED)

    first = workspace.evaluate("PreToolUse", calls, "done")
    second = workspace.evaluate("PreToolUse", [*calls, denied_handback], "done")

    assert (first.verdict, first.marker_exists) == ("sent-back", True)
    assert (second.verdict, second.marker_exists) == ("passed-flagged", False)
    assert second.envelope["hookSpecificOutput"]["updatedInput"]["message"].startswith(
        "SELF-REVIEW INCOMPLETE (hook-verified): "
    )


def test_stop_sent_back_then_handback_with_items_missing_ends_passed_flagged(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    first = workspace.evaluate("SubagentStop", calls, "done")
    second = workspace.evaluate("PreToolUse", calls, "done")

    assert (first.verdict, first.marker_exists) == ("sent-back", True)
    assert (second.verdict, second.marker_exists) == ("passed-flagged", False)


def test_later_cycle_on_the_same_agent_id_sends_back_again(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    verdicts = [workspace.evaluate("PreToolUse", calls, "done").verdict for _ in range(3)]

    assert verdicts == ["sent-back", "passed-flagged", "sent-back"]


def test_other_events_and_tools_exit_zero_with_no_output_or_log(workspace):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "agent_type": "code-writer", "agent_id": AGENT_ID}

    result = workspace.evaluate("PreToolUse", None, "x", payload=payload)

    assert (result.status, result.stdout, result.new_log_lines) == (0, "", [])


def test_evaluator_error_logs_the_exception_class_and_exits_one(workspace, monkeypatch):
    script = workspace.make_file("scripts/a.sh")

    def raise_oserror():
        raise OSError("placeholder")

    monkeypatch.setattr(evaluator, "load_git_record_builder", raise_oserror)

    result = workspace.evaluate("PreToolUse", [_edit_call(script)], "done")

    assert (result.status, result.stdout) == (1, "")
    assert result.verdict == "allowed-evaluator-error=OSError"


def test_failed_envelope_write_removes_the_marker_and_logs_the_error_class(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())

    result = workspace.evaluate("PreToolUse", calls, "done", stdout=_BrokenPipeStdout())

    assert (result.status, result.stdout, result.marker_exists) == (1, "", False)
    assert result.verdict == "allowed-evaluator-error=BrokenPipeError"


def test_lost_claim_race_prints_nothing_and_leaves_the_winners_marker(workspace, monkeypatch):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    (workspace.marker_dir / AGENT_ID).mkdir(parents=True)
    monkeypatch.setattr(evaluator, "marker_present", lambda marker_dir, agent_id: False)

    result = workspace.evaluate("PreToolUse", calls, "done")

    assert (result.status, result.stdout, result.verdict, result.marker_exists) == (0, "", "allowed-claim-raced", True)


@pytest.mark.parametrize(
    "hostile_agent_id",
    ["../escape", "a/b", AGENT_ID + "\n", "", "a b"],
    ids=["traversal", "separator", "trailing-newline", "empty", "space"],
)
def test_evaluator_rejects_an_invalid_agent_id_without_writing_a_marker_or_log(workspace, hostile_agent_id):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    workspace.write_transcript(calls)

    status = evaluator.main(
        [hostile_agent_id, str(workspace.marker_dir), str(workspace.log_path)],
        stdin=io.StringIO(json.dumps(workspace.payload("PreToolUse", "done"))),
        stdout=io.StringIO(),
    )

    assert status == 1
    assert not workspace.marker_dir.exists()
    assert not workspace.log_path.exists()


def test_log_holds_only_accepted_reviewer_names_and_no_report_or_path_text(workspace):
    _script, calls = workspace.sh_script_calls(reviewer_reads=())
    report = (
        "**Self-review:**\n- `staff-platform-engineer`: a\n- `leaked-reviewer-name`: b\n\n"
        "report-text-sentinel secret-path-sentinel"
    )

    first = workspace.evaluate("PreToolUse", calls, report)
    second = workspace.evaluate("PreToolUse", calls, report)

    log_text = workspace.log_path.read_text()
    assert (first.verdict, second.verdict) == ("sent-back", "passed-flagged")
    assert (first.fields["reviewers"], second.fields["reviewers"]) == ("staff-platform-engineer",) * 2
    for forbidden_text in ("leaked-reviewer-name", "report-text-sentinel", "secret-path-sentinel", str(workspace.root)):
        assert forbidden_text not in log_text


def test_non_ascii_transcript_and_comment_text_are_read_and_the_prose_reviewer_is_required(workspace):
    source = workspace.make_file("src/a.py", "x = 1\n")
    calls = [
        _reviewer_read("staff-backend-engineer"),
        _write_call(source, content="# caf\u00e9 \u2014 na\u00efve cache\nx = 1\n"),
        _read_call(source),
    ]
    workspace.agent_transcript.parent.mkdir(parents=True, exist_ok=True)
    with workspace.agent_transcript.open("wb") as transcript_file:
        for record in _records_for(calls):
            transcript_file.write(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")
        transcript_file.write(b"\xff\xfe not utf-8, not json\n")
    backend_only = "**Self-review:**\n- `staff-backend-engineer`: clean"

    first = workspace.evaluate("PreToolUse", None, backend_only)

    assert (first.verdict, first.fields["failed"], first.fields["calls"]) == ("sent-back", "R4", "3")
    assert _item_fields(_assess(calls, backend_only)) == [(evaluator.KIND_REVIEWER, "comment-discipline-reviewer", True, True)]


SECOND_AGENT_ID = "b9876543210fedcba"


def test_two_agents_sharing_marker_and_subagents_directories_are_judged_and_cleared_independently(workspace):
    _script_a, incomplete_calls_a = workspace.sh_script_calls(reviewer_reads=(), script_name="scripts/a.sh")
    _script_b, incomplete_calls_b = workspace.sh_script_calls(reviewer_reads=(), script_name="scripts/b.sh")
    _script_b, complete_calls_b = workspace.sh_script_calls(script_name="scripts/b.sh")

    first_a = workspace.evaluate("PreToolUse", incomplete_calls_a, LEDGER_PLATFORM)
    marker_b_after_first_a = workspace.marker_path_for(SECOND_AGENT_ID).exists()
    first_b = workspace.evaluate("PreToolUse", incomplete_calls_b, LEDGER_PLATFORM, agent_id=SECOND_AGENT_ID)
    marker_a_after_first_b = workspace.marker_path_for(AGENT_ID).exists()
    second_a = workspace.evaluate("PreToolUse", None, LEDGER_PLATFORM)
    marker_b_after_second_a = workspace.marker_path_for(SECOND_AGENT_ID).exists()
    # A's transcript is still incomplete here, so a clean verdict for B shows B read its own transcript.
    complete_b = workspace.evaluate("PreToolUse", complete_calls_b, LEDGER_PLATFORM, agent_id=SECOND_AGENT_ID)

    assert (first_a.verdict, first_a.marker_exists, marker_b_after_first_a) == ("sent-back", True, False)
    assert (first_b.verdict, first_b.marker_exists, marker_a_after_first_b) == ("sent-back", True, True)
    assert (second_a.verdict, second_a.marker_exists, marker_b_after_second_a) == ("passed-flagged", False, True)
    assert (complete_b.verdict, complete_b.marker_exists) == ("clean", False)


@pytest.mark.timing
def test_several_thousand_records_with_hundreds_of_edited_paths_finish_within_half_the_cap(workspace):
    edited_count = 300
    read_count = 3000
    calls = [_reviewer_read("staff-backend-engineer")]
    for index in range(edited_count):
        calls.append(_edit_call(workspace.make_file("src/module_%03d.py" % index, "x = 1\n")))
    calls.extend(_call("Read", {"file_path": "/placeholder/other/file_%04d.txt" % index}) for index in range(read_count))
    calls.append(_git_diff_call())
    report = "**Self-review:**\n- `staff-backend-engineer`: clean"
    workspace.write_transcript(calls)

    started = time.monotonic()
    result = workspace.evaluate("PreToolUse", None, report)
    elapsed_seconds = time.monotonic() - started

    assert result.verdict == "clean"
    assert (result.fields["calls"], result.fields["edits"], result.fields["changed"]) == (
        str(len(calls)), str(edited_count), str(edited_count),
    )
    assert elapsed_seconds < 2.5


# ==========================================================================
# Tier 4 — the hook as a subprocess
# ==========================================================================


def _run_hook_raw(payload, config_dir, *, hook=HOOK_PATH, path=None, cwd=None):
    """Direct exec; returns (returncode, stdout, stderr)."""
    environment = dict(os.environ)
    environment["CLAUDE_CONFIG_DIR"] = str(config_dir)
    if path is not None:
        environment["PATH"] = path
    stdin_text = payload if isinstance(payload, str) else json.dumps(payload)
    completed = subprocess.run(
        [str(hook)], input=stdin_text, capture_output=True, text=True, env=environment, timeout=60, check=False,
        cwd=cwd,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _hook_log_lines(workspace):
    return workspace.log_path.read_text().splitlines() if workspace.log_path.is_file() else []


def _hook_verdict(workspace):
    return _hook_log_lines(workspace)[-1].split()[2]


def _run_hook(workspace, payload, **options):
    returncode, stdout, stderr = _run_hook_raw(payload, workspace.config_dir, **options)
    assert returncode == 0, f"the hook must never exit non-zero: {stderr!r}"
    return stdout


def _sh_and_test_edit_calls(workspace, *, complete):
    script = workspace.make_file("scripts/a.sh")
    test_file = workspace.make_file("tests/test_a.py")
    calls = [_edit_call(script), _write_call(test_file)]
    if complete:
        calls = [
            _reviewer_read("staff-platform-engineer"), _reviewer_read("staff-sdet"), *calls, _git_diff_call(),
        ]
    return calls


def test_hook_file_is_executable():
    assert os.access(HOOK_PATH, os.X_OK)


def test_raw_runner_positive_control_returns_the_send_back_envelope(workspace):
    workspace.write_transcript(_sh_and_test_edit_calls(workspace, complete=False))

    returncode, stdout, _stderr = _run_hook_raw(workspace.payload("PreToolUse", "done"), workspace.config_dir)

    assert returncode == 0
    assert json.loads(stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_scripted_sequence_sends_back_then_passes_silently_when_complete(workspace):
    workspace.write_transcript(_sh_and_test_edit_calls(workspace, complete=False))
    first_output = _run_hook(workspace, workspace.payload("PreToolUse", "done"))
    reason = json.loads(first_output)["hookSpecificOutput"]["permissionDecisionReason"]
    workspace.write_transcript(_sh_and_test_edit_calls(workspace, complete=True))

    second_output = _run_hook(workspace, workspace.payload("PreToolUse", LEDGER_PLATFORM_AND_SDET))

    assert "staff-platform-engineer" in reason and "staff-sdet" in reason
    assert second_output == ""
    assert _hook_verdict(workspace) == "clean"
    assert not (workspace.marker_dir / AGENT_ID).exists()


def test_hook_output_shape_per_arm(workspace):
    workspace.write_transcript(_sh_and_test_edit_calls(workspace, complete=False))

    deny = json.loads(_run_hook(workspace, workspace.payload("PreToolUse", "done")))
    allow = json.loads(_run_hook(workspace, workspace.payload("PreToolUse", "done")))
    block = json.loads(_run_hook(workspace, workspace.payload("SubagentStop", "done")))

    for envelope, decision in ((deny, "deny"), (allow, "allow")):
        assert "decision" not in envelope
        assert envelope["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert envelope["hookSpecificOutput"]["permissionDecision"] == decision
    assert "permissionDecisionReason" in deny["hookSpecificOutput"]
    assert "updatedInput" in allow["hookSpecificOutput"]
    assert block["decision"] == "block" and block["reason"]
    assert "hookSpecificOutput" not in block


def test_hook_hostile_report_text_round_trips_through_the_pretooluse_arm(workspace):
    hostile = 'quote " backslash \\ newline-escape \\n subst $(touch x) tick `id` and a trailing newline\n'
    workspace.write_transcript(_sh_and_test_edit_calls(workspace, complete=False))
    payload = workspace.payload("PreToolUse", hostile)

    _run_hook(workspace, payload)
    second_output = _run_hook(workspace, payload)

    updated_message = json.loads(second_output)["hookSpecificOutput"]["updatedInput"]["message"]
    assert updated_message.endswith("\n\n" + hostile)


def _sends_back_control(workspace):
    workspace.write_transcript(_sh_and_test_edit_calls(workspace, complete=False))
    return workspace.payload("PreToolUse", "done")


def _assert_allowed_without_new_marker(workspace, payload, **options):
    marker_dir_existed = workspace.marker_dir.exists()
    stdout = _run_hook(workspace, payload, **options)

    assert stdout == ""
    assert workspace.marker_dir.exists() is marker_dir_existed
    assert not (workspace.marker_dir / AGENT_ID).exists()


def test_hook_allow_branch_agent_type_not_exactly_code_writer_though_the_prefilter_passes(workspace):
    control = _sends_back_control(workspace)
    payload = dict(control, agent_type="plugin:code-writer", description="code-writer")
    assert '"code-writer"' in json.dumps(payload)

    _assert_allowed_without_new_marker(workspace, payload)

    assert _hook_log_lines(workspace) == []
    assert json.loads(_run_hook(workspace, control))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_allow_branch_invalid_agent_id(workspace):
    control = _sends_back_control(workspace)

    _assert_allowed_without_new_marker(workspace, dict(control, agent_id="../escape"))

    assert _hook_verdict(workspace) == "allowed-invalid-agent-id"
    assert json.loads(_run_hook(workspace, control))["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("missing_binary", "expected_verdict"),
    [("jq", "allowed-jq-failed"), ("python3", "allowed-evaluator-exit=127")],
)
def test_hook_allow_branch_required_tool_absent(workspace, tmp_path_factory, missing_binary, expected_verdict):
    control = _sends_back_control(workspace)
    farm_dir = tmp_path_factory.mktemp("path-farm")
    path_without_binary = build_path_without(missing_binary, farm_dir)

    _assert_allowed_without_new_marker(workspace, control, path=path_without_binary)

    assert _hook_verdict(workspace) == expected_verdict


@pytest.mark.parametrize(
    "hostile_agent_id",
    [
        # Guards a `read`-style parse that truncates the id at its first newline.
        "abc\n../x",
        # Guards `$(...)` stripping a trailing newline, which the trailing `end` sentinel field prevents.
        AGENT_ID + "\n",
    ],
    ids=["embedded-newline", "trailing-newline"],
)
def test_hook_allow_branch_agent_id_holding_a_newline_is_rejected_whole(workspace, hostile_agent_id):
    control = _sends_back_control(workspace)

    _assert_allowed_without_new_marker(workspace, dict(control, agent_id=hostile_agent_id))

    assert _hook_verdict(workspace) == "allowed-invalid-agent-id"
    assert json.loads(_run_hook(workspace, control))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_unreadable_transcript_logs_the_evaluator_error_then_exit_one(workspace):
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    control = _sends_back_control(workspace)
    workspace.agent_transcript.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            workspace.agent_transcript.read_text()

        _assert_allowed_without_new_marker(workspace, control)
    finally:
        workspace.agent_transcript.chmod(0o600)

    logged_verdicts = [line.split()[2] for line in _hook_log_lines(workspace)]
    assert logged_verdicts == ["allowed-evaluator-error=PermissionError", "allowed-evaluator-exit=1"]


def test_hook_allow_branch_evaluator_file_absent_logs_exit_two_and_allows(workspace, tmp_path_factory):
    control = _sends_back_control(workspace)
    hooks_copy = tmp_path_factory.mktemp("hooks-without-evaluator")
    shutil.copy2(HOOK_PATH, hooks_copy / HOOK_PATH.name)
    symlink_hooks_lib_chain(hooks_copy)

    _assert_allowed_without_new_marker(workspace, control, hook=hooks_copy / HOOK_PATH.name)

    assert _hook_verdict(workspace) == "allowed-evaluator-exit=2"


def test_hook_ignores_other_subagent_types_without_logging(workspace):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "SubagentHandback", "agent_type": "Explore", "agent_id": AGENT_ID}

    stdout = _run_hook(workspace, payload)

    assert stdout == ""
    assert _hook_log_lines(workspace) == []


def test_hook_allow_branch_lib_sh_missing_beside_the_hook(workspace, tmp_path_factory):
    control = _sends_back_control(workspace)
    hooks_copy = tmp_path_factory.mktemp("hooks-without-lib")
    shutil.copy2(HOOK_PATH, hooks_copy / HOOK_PATH.name)

    _assert_allowed_without_new_marker(workspace, control, hook=hooks_copy / HOOK_PATH.name)

    assert _hook_log_lines(workspace) == []
    assert json.loads(_run_hook(workspace, control))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_allow_branch_relative_config_dir_logs_nothing_and_creates_nothing(workspace, tmp_path):
    control = _sends_back_control(workspace)

    returncode, stdout, _stderr = _run_hook_raw(control, "relative-config-dir", cwd=tmp_path)

    assert (returncode, stdout) == (0, "")
    assert not (tmp_path / "relative-config-dir").exists()
    assert json.loads(_run_hook(workspace, control))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_prints_nothing_when_the_evaluator_prints_an_envelope_then_exits_nonzero(workspace, tmp_path_factory):
    control = _sends_back_control(workspace)
    shadow_dir = tmp_path_factory.mktemp("python3-shadow")
    shadow = shadow_dir / "python3"
    shadow.write_text("#!/bin/sh\ncat > /dev/null\nprintf '%s' '{\"decision\": \"block\", \"reason\": \"x\"}'\nexit 7\n")
    shadow.chmod(0o755)

    _assert_allowed_without_new_marker(workspace, control, path=f"{shadow_dir}{os.pathsep}{os.environ['PATH']}")

    assert _hook_verdict(workspace) == "allowed-evaluator-exit=7"
    assert json.loads(_run_hook(workspace, control))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_second_return_with_a_report_over_ten_thousand_characters_is_a_valid_envelope(workspace):
    long_report = "".join(f"line {number} of a long report \u2014 caf\u00e9\n" for number in range(1000))
    assert len(long_report) > 10_000
    workspace.write_transcript(_sh_and_test_edit_calls(workspace, complete=False))
    payload = workspace.payload("PreToolUse", long_report)

    _run_hook(workspace, payload)
    second_output = _run_hook(workspace, payload)

    updated_message = json.loads(second_output)["hookSpecificOutput"]["updatedInput"]["message"]
    assert updated_message.endswith("\n\n" + long_report)


# Two capped calls (jq, the evaluator), each 5 seconds plus the 2-second kill grace.
IN_SCRIPT_WORST_CASE_SECONDS = 14


def _hook_registrations(settings, event):
    return [
        (group.get("matcher"), hook)
        for group in settings["hooks"][event]
        for hook in group["hooks"]
        if hook["command"].endswith("send-back-skipped-self-review.sh")
    ]


@pytest.mark.parametrize(
    ("event", "matcher"), [("PreToolUse", "SubagentHandback"), ("SubagentStop", "code-writer")]
)
def test_settings_registers_the_hook_exactly_once_per_event_as_a_fail_open_turn_gate(event, matcher):
    settings = json.loads(SETTINGS_PATH.read_text())

    registrations = _hook_registrations(settings, event)

    assert len(registrations) == 1
    registered_matcher, hook = registrations[0]
    assert registered_matcher == matcher
    assert hook["command"] == "~/.claude/hooks/send-back-skipped-self-review.sh"
    assert hook["timeout"] > IN_SCRIPT_WORST_CASE_SECONDS
    assert not {"async", "if"} & set(hook)
