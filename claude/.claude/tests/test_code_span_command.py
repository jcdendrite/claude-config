"""Contract tests for helpers.code_span_command, the tests' one definition of a two-line hook message."""
from __future__ import annotations

import pytest
from helpers import code_span_command

_COMMAND = "/resume-context example-handoff.md"


def test_well_formed_message_with_a_backtick_pair_in_its_lead_returns_the_command() -> None:
    lead = "Resume in this session, because the file's resume command targets this worktree (`/work/tree`). Run /clear, then:"

    assert code_span_command(f"{lead}\n`{_COMMAND}`") == _COMMAND


@pytest.mark.parametrize(
    ("message", "clause_message"),
    [
        pytest.param(f"Run /clear, then:\n`{_COMMAND}`\n", "code_span_command line count", id="trailing-newline"),
        pytest.param(f"Run /clear,\nthen:\n`{_COMMAND}`", "code_span_command line count", id="two-line-lead"),
        pytest.param(f"Run /clear, then: `{_COMMAND}`", "code_span_command line count", id="one-line-message"),
        pytest.param(f"\n`{_COMMAND}`", "code_span_command non-empty LEAD", id="empty-lead"),
        pytest.param(f"Run ` /clear, then:\n`{_COMMAND}`", "code_span_command even LEAD backticks", id="odd-lead-backticks"),
        pytest.param(f"Run /clear, then:\n{_COMMAND}`", "code_span_command opening backtick", id="no-opening-backtick"),
        pytest.param(f"Run /clear, then:\n`{_COMMAND}", "code_span_command closing backtick", id="no-closing-backtick"),
        pytest.param("Run /clear, then:\n``", "code_span_command non-empty command", id="empty-command"),
        pytest.param(
            "Run /clear, then:\n`resume-context `x`", "code_span_command no backtick in the command", id="backtick-in-command"
        ),
        pytest.param(
            f"Run `{_COMMAND}` now:\n`{_COMMAND}`", "code_span_command command absent from the LEAD", id="command-in-lead"
        ),
    ],
)
def test_message_breaking_one_clause_fails_with_that_clauses_message(message: str, clause_message: str) -> None:
    with pytest.raises(AssertionError, match=clause_message):
        code_span_command(message)
