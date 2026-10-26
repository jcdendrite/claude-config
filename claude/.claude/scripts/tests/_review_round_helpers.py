"""Fixture builders shared by the tests of transcript_analysis subcommands that read review rounds."""
from pathlib import Path

from .conftest import (
    _agent_use,
    _bash_use,
    _priced,
    _tool_result,
    _user_msg,
    _write_subagent_dispatch,
)

_ORCHESTRATOR_AGENT_TYPE = "review-orchestrator"
_SONNET = "claude-sonnet-5"

# Plain-ASCII prefix on every free-text value a review-orchestrator dispatch
# carries that no round-consuming subcommand extracts. A prefix that appears in
# any output means a subcommand surfaced a field it must not.
_LEAK_SENTINEL_PREFIX = "LEAKSENTINEL-"


def _leak_sentinel(field_name: str) -> str:
    return f"{_LEAK_SENTINEL_PREFIX}{field_name}"


def _dispatch_block_with_free_text(tool_use_id: str, subagent_type: str, *, tool_name: str = "Agent") -> dict:
    """An Agent/Task tool_use block whose every input key except
    subagent_type carries a sentinel."""
    block = _agent_use(tool_use_id, subagent_type, tool_name=tool_name)
    block["input"].update({
        "description": _leak_sentinel(f"{tool_use_id}-description"),
        "prompt": _leak_sentinel(f"{tool_use_id}-prompt"),
        "name": _leak_sentinel(f"{tool_use_id}-name"),
    })
    return block


def _orchestrator_spawn_record(
    tool_use_id: str, *, ts: str, branch: str = "feat", tool_name: str = "Agent", extra_blocks: tuple = (),
) -> dict:
    """A main-thread priced assistant record ($0.20) that dispatches a
    review-orchestrator."""
    return _priced(
        "claude-sonnet-5", input=100_000, branch=branch, ts=ts,
        content=[
            *extra_blocks,
            {"type": "text", "text": _leak_sentinel(f"{tool_use_id}-assistant-text")},
            _dispatch_block_with_free_text(tool_use_id, _ORCHESTRATOR_AGENT_TYPE, tool_name=tool_name),
        ],
    )


def _orchestrator_return_record(tool_use_id: str, *, ts: str, branch: str = "feat") -> dict:
    return _user_msg(
        [_tool_result(tool_use_id, _leak_sentinel(f"{tool_use_id}-result"))], branch=branch, ts=ts,
    )


def _as_sidechain(record: dict) -> dict:
    record["isSidechain"] = True
    return record


def _write_orchestrator_subagent_tree(
    proj: Path, session_id: str, *, orchestrator_tool_use_id: str = "o1", branch: str = "feat",
) -> None:
    """The orchestrator's own transcript ($0.20: its Skill call plus a nested
    reviewer dispatch) and that reviewer's depth-2 transcript ($1.00), both in
    the session's flat subagents/ directory with isSidechain on every record."""
    orchestrator_records = [
        _as_sidechain(_priced(
            "claude-sonnet-5", input=100_000, branch=branch, ts="2026-08-01T10:00:10.000Z",
            content=[
                {"type": "text", "text": _leak_sentinel("orchestrator-text")},
                {
                    "type": "tool_use", "id": "inner-skill", "name": "Skill",
                    "input": {"skill": "code-review", "args": _leak_sentinel("skill-args")},
                },
                _dispatch_block_with_free_text("n1", "staff-sdet"),
            ],
        )),
    ]
    reviewer_records = [
        _as_sidechain(_priced(
            "claude-sonnet-5", input=500_000, branch=branch, ts="2026-08-01T10:00:20.000Z",
            content=[{"type": "text", "text": _leak_sentinel("reviewer-text")}],
        )),
    ]
    _write_subagent_dispatch(
        proj, session_id, "agent-o", orchestrator_tool_use_id, orchestrator_records,
        agent_type=_ORCHESTRATOR_AGENT_TYPE, description=_leak_sentinel("orchestrator-meta-description"),
    )
    _write_subagent_dispatch(
        proj, session_id, "agent-n", "n1", reviewer_records,
        agent_type="staff-sdet", description=_leak_sentinel("reviewer-meta-description"), spawn_depth=2,
    )


_MARKER_WRITE_COMMAND = "marker.sh write code-review"


def _marker_write_use(tool_id: str) -> dict:
    return _bash_use(tool_id, _MARKER_WRITE_COMMAND)
