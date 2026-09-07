"""JSONL transcript read/parse and session iteration -- no dependency on any
cmd_* subcommand, scope resolution, redaction, or pricing.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

# Subdirectory name where Claude Code writes split subagent transcripts.
SUBAGENT_SUBDIR = "subagents"


def _index_subagent_dispatches(jsonl: Path) -> tuple[dict[str, tuple[Path, str | None]], int]:
    """Map each subagent dispatch's toolUseId to (its paired .jsonl path,
    requested model), for one session.

    Reads subagents/*.meta.json directly rather than through iter_sessions'
    include_subagents merge -- that merge flattens every subagent file's
    records into one list with no per-file boundary, which cannot answer
    "this specific dispatch's own last assistant text." The requested model
    is meta.json's own "model" key (absent when the dispatch carried no
    explicit model request) -- reading it here, alongside the toolUseId this
    function already parses meta.json for, avoids a second per-dispatch
    meta.json read in subagent-mix's model-mix join. review_rounds.py's
    _price_dispatch also calls this function recursively, once per nested
    subagent transcript, to descend into a dispatch's own further spawns.

    Returns (index, meta_read_errors): meta_read_errors counts *.meta.json
    files present but unusable -- invalid JSON, valid JSON missing a
    string-typed toolUseId, or valid JSON whose "model" key is present but
    not a string -- distinct from a dispatch with no meta.json at all (the
    caller's own, separately-documented exclusion path). meta.json is
    written by Claude Code's own harness, not by this repo, so its "model"
    and "toolUseId" fields are external input: a non-string value for either
    (a future harness change, or a corrupted file) is excluded here rather
    than reaching a caller that would use it as a dict key and crash with an
    uncaught TypeError.
    """
    subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
    index: dict[str, tuple[Path, str | None]] = {}
    meta_read_errors = 0
    if not subagent_dir.is_dir():
        return index, meta_read_errors
    for meta_path in sorted(subagent_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta_read_errors += 1
            continue
        tool_use_id = meta.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            meta_read_errors += 1
            continue
        requested_model = meta.get("model")
        if requested_model is not None and not isinstance(requested_model, str):
            meta_read_errors += 1
            continue
        agent_id = meta_path.name.removesuffix(".meta.json")
        index[tool_use_id] = (meta_path.parent / f"{agent_id}.jsonl", requested_model)
    return index, meta_read_errors


def _parse_jsonl_records(jsonl: Path) -> list[dict] | None:
    """Parse one .jsonl file into records, skipping malformed lines.

    Returns None when the file cannot be opened, which is distinct from an empty
    but readable file ([]): an unreadable main transcript aborts the whole
    session, while an empty one still carries its subagent files.
    """
    records: list[dict] = []
    try:
        with open(jsonl) as fh:
            for raw in fh:
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    return records


def _read_session_file_partitioned(jsonl: Path, include_subagents: bool) -> list[list[dict]]:
    """Read one transcript file's records, keeping each source file's records separate.

    Returns one list per source file: the main transcript first, then each
    <session_id>/subagents/*.jsonl in sorted order. An unreadable main file
    yields [] (no groups at all); an unreadable subagent file is skipped. A
    readable-but-empty main file still yields its subagent groups, matching
    read_session_file's long-standing behaviour.

    The per-file boundary matters to any caller that differences consecutive
    turns: separate files are separate context windows, so a delta taken across
    a boundary compares two unrelated conversations. read_session_file flattens
    this for the callers that only need the records. A group's own source-file
    stem is not a reliable stand-in for its records' sessionId -- a subagent
    file is named by its own agent id but its records carry the *parent*
    session's sessionId -- so a caller needing per-record session identity
    (e.g. _read_scope_growth_for_group) keys off each record's own sessionId,
    never off this function's file-level grouping.
    """
    main = _parse_jsonl_records(jsonl)
    if main is None:
        return []
    groups = [main]

    if include_subagents:
        subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
        if subagent_dir.is_dir():
            for sub_jsonl in sorted(subagent_dir.glob("*.jsonl")):
                sub_records = _parse_jsonl_records(sub_jsonl)
                if sub_records:
                    groups.append(sub_records)

    return groups


def read_session_file(jsonl: Path, include_subagents: bool) -> list[dict]:
    """Read one transcript file's records, merging its subagent files when asked.

    Shared by iter_sessions (which selects files by glob) and _iter_scoped_sessions
    (which selects project dirs by exact-name identity, then reads each file).
    Keeping the per-file read here means the two selection strategies cannot drift
    in how they parse records or merge subagent files.

    When include_subagents=True, records from split subagent files under
    <session_id>/subagents/*.jsonl are appended. Those files carry
    isSidechain: true on assistant records. Returns [] for an unreadable file.

    Flattens _read_session_file_partitioned in source-file order, so the merged
    sequence is exactly the concatenation it has always been.
    """
    return [rec for group in _read_session_file_partitioned(jsonl, include_subagents) for rec in group]


def iter_sessions(
    projects_dir: Path,
    projects_glob: str = "*",
    include_subagents: bool = False,
) -> Iterator[tuple[Path, list[dict]]]:
    """Yield (jsonl_path, records) for each transcript file matching the glob.

    Files are yielded in a single flat sort over their full paths — NOT grouped
    by directory. Grouping by directory would misorder projects whenever one
    project-dir name is a lexical prefix of a sibling's (exactly this repo's
    own worktree naming, e.g. -home-u-repo vs -home-u-repo--claude-worktrees-b).
    Redact-label assignment (_build_redact_map) does not depend on this order —
    it sorts the collected labels itself before assigning placeholders — but
    every other caller iterates these results directly, so the flat sort is
    what keeps their output order deterministic and reproducible across runs.
    See read_session_file for the per-file read and the include_subagents
    merge behavior.
    """
    for jsonl in sorted(projects_dir.glob(f"{projects_glob}/*.jsonl")):
        records = read_session_file(jsonl, include_subagents)
        if records:
            yield jsonl, records


def _parse_ts(ts_str: str | None) -> float | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


# Tool names that write code to disk, classifying a tool_use block's record
# shape -- used by both reviewer-yield's parent-edit index and audit-routing's
# per-turn classifier.
_CODE_WRITE_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
