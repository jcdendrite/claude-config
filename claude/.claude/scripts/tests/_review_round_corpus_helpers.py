"""Free-text, token and malformed-usage corpus builders shared by test_transcript_review_rounds.py and
test_transcript_pr_cost.py."""
from pathlib import Path

from .conftest import _agent_use, _asst, _priced, _tool_result, _user_msg, _write_jsonl, _write_subagent_dispatch

_ORCHESTRATOR_AGENT_TYPE = "review-orchestrator"

# Plain-ASCII prefix on every free-text value a review-orchestrator dispatch
# carries that no round-consuming subcommand extracts. A prefix that appears in
# any output means a subcommand surfaced a field it must not.
_LEAK_SENTINEL_PREFIX = "LEAKSENTINEL-"


def _leak_sentinel(field_name: str) -> str:
    return f"{_LEAK_SENTINEL_PREFIX}{field_name}"


# A record outside the transcript format: the current decode contract raises AttributeError on a non-string
# timestamp. Tests pinning that raise (and its rounds_only containment) share this one literal.
_NON_STRING_TIMESTAMP_RECORD = {"type": "user", "timestamp": 12345}


def _priced_with_classes(model: str, base: int, **kwargs) -> dict:
    """A priced record whose five token classes hold the distinct nonzero counts _token_classes(base) returns."""
    return _priced(
        model, input=base + 1, output=base + 2, cache_read=base + 3, ephemeral_1h=base + 4, ephemeral_5m=base + 5,
        **kwargs,
    )


def _token_classes(base: int) -> dict[str, int]:
    return {
        "input": base + 1, "output": base + 2, "cache_read": base + 3,
        "cache_write_1h": base + 4, "cache_write_5m": base + 5,
    }


def _malformed_usage_turn(
    usage, *, ts: str, branch: str = "feat", model: str = "claude-sonnet-5", content=None,
    request_id: str | None = None,
) -> dict:
    """An assistant record whose usage is `usage`, a value the pricing gate must reject."""
    turn = _asst(model, branch=branch, ts=ts, content=content, request_id=request_id)
    turn["message"]["usage"] = usage
    return turn


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


def _write_orchestrator_free_text_corpus(
    proj: Path, session_id: str = "sess-1", branch: str = "feat", *, with_slash_round_and_unpriced_marker_model: bool = False,
) -> None:
    """A main-thread code-writer dispatch, then an orchestrator round, then a fresh prompt.

    with_slash_round_and_unpriced_marker_model appends a `/slash` plan-review round whose command args and
    one unpriced turn's model ID carry sentinels.
    """
    records = [
        _priced(
            "claude-sonnet-5", input=100_000, branch=branch, ts="2026-08-01T09:59:00.000Z",
            content=[_dispatch_block_with_free_text("w1", "code-writer")],
        ),
        _user_msg(
            [_tool_result("w1", _leak_sentinel("w1-result"))], branch=branch, ts="2026-08-01T09:59:30.000Z",
        ),
        _orchestrator_spawn_record("o1", ts="2026-08-01T10:00:00.000Z", branch=branch),
        _orchestrator_return_record("o1", ts="2026-08-01T10:01:00.000Z", branch=branch),
        _user_msg(_leak_sentinel("user-prompt"), branch=branch, ts="2026-08-01T10:05:00.000Z"),
    ]
    if with_slash_round_and_unpriced_marker_model:
        records += [
            _user_msg(
                f"<command-name>/plan-review</command-name><command-args>{_leak_sentinel('slash-args')}</command-args>",
                branch=branch, ts="2026-08-01T10:06:00.000Z",
            ),
            _priced(
                _leak_sentinel("marker-model"), input=1_000, branch=branch, ts="2026-08-01T10:07:00.000Z",
            ),
            _user_msg("thanks", branch=branch, ts="2026-08-01T10:08:00.000Z"),
        ]
    _write_jsonl(proj / f"{session_id}.jsonl", records)
    _write_orchestrator_subagent_tree(proj, session_id, branch=branch)
