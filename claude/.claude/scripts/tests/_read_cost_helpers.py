"""Constants and builders shared by the test_transcript_read_cost_* files."""
from transcript_analysis import pricing, read_cost

from .conftest import (
    _asst,
    _read_result,
    _read_use,
)

_PROJECT_SLUG = "-home-user-testrepo"
# An agent type this repo tracks under agents/, and one that no repo tracks.
_TRACKED_AGENT_TYPE = "staff-backend-engineer"
_UNTRACKED_AGENT_TYPE = "sentinel-untracked-agent-type"

# 400 characters are 100 estimated tokens (chars // 4).
_HUNDRED_TOKEN_TEXT = "x" * 400


def _weights() -> dict[str, float]:
    return pricing._cache_read_equivalent_weights()


def _turn(message_id: str, content: list | None = None, *, usage: dict | None = None) -> dict:
    rec = _asst("claude-sonnet-5", content=content)
    rec["message"]["id"] = message_id
    if usage is not None:
        rec["message"]["usage"] = usage
    return rec


def _sidechain_copies(records: list[dict]) -> list[dict]:
    """Every record a subagent transcript file holds, user records included, carries isSidechain true."""
    return [{**rec, "isSidechain": True} for rec in records]


def _read_cost_args(*, this_repo: bool = True, projects: str = "*") -> object:
    """Pre-seeds args._this_repo_slugs -- the cache _resolve_project_scope reads first -- so scope resolution
    does not ask git for this repo's worktrees."""
    return type("A", (), {
        "this_repo": this_repo,
        "projects": projects,
        "_this_repo_slugs": [_PROJECT_SLUG],
    })()


def _scan(records: list[dict], seen_tool_use_ids: set[str] | None = None) -> read_cost._WindowScan:
    return read_cost._scan_window(records, set() if seen_tool_use_ids is None else seen_tool_use_ids)


def _read_exchange(message_id: str, tool_id: str, path: str, *, text: str = _HUNDRED_TOKEN_TEXT) -> list[dict]:
    return [_turn(message_id, [_read_use(tool_id, file_path=path)]), _read_result(tool_id, text)]
