"""The read-cost command: what share of spend whole-file Read results account for, split by main thread and
subagent type, with the cuts that show how much of it a read gate could reach -- plus its subparser registration.

Every printed figure is an aggregate: no path, session identifier, or project name is retained past the scan.

Imports sibling modules by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from transcript_analysis import corpus, cost, pricing, redaction, render, scope, subagent_mix

# ~4 chars/token, the estimate read-scope uses. A deliberate second pin rather than a shared constant, so
# recalibrating one report's figures does not silently move the other's.
_READ_COST_CHARS_PER_TOKEN = 4

_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

_LINE_THRESHOLDS = (350, 800)

_LARGE_READ_TOKENS = 2000

_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (_LARGE_READ_TOKENS, f"<{_LARGE_READ_TOKENS}"),
    (5000, f"{_LARGE_READ_TOKENS}-4999"),
)
_SIZE_OVERFLOW_LABEL = "5000+"

# Scan-gap level tags for the subagent directories, files, and sidecars this module reads itself. The
# project-directory and main-file tags come from scope.
_GAP_SUBAGENT_DIR = "subagent-dir"
_GAP_SUBAGENT_FILE = "subagent-file"
_GAP_SUBAGENT_META = "subagent-meta"

_UNKNOWN_AGENT_TYPE = "unknown"

_SCOPE_MAIN = "main"
_SCOPE_SUBAGENT = "subagent"

_KIND_INSTRUCTION = "instruction"
_KIND_OTHER_PROSE = "other_prose"
_KIND_CODE_OR_OTHER = "code/other"
_PROSE_EXTENSIONS = frozenset({".md", ".mdx", ".txt"})
_INSTRUCTION_PATH_FRAGMENTS = ("/skills/", "/agents/", "/rules/")


class _ReadEvent(NamedTuple):
    """`path` is blanked before an event is kept in the report totals."""

    path: str
    whole_file: bool
    tokens: int
    lines: int
    remaining_turns: int
    edited_later: bool


class _WindowScan(NamedTuple):
    events: list[_ReadEvent]
    turn_message_ids: frozenset[str]
    usage_by_message: dict[str, dict]
    repeated_reads: int
    invalid_usage_message_ids: frozenset[str]


@dataclass
class _SeenIds:
    """`message_ids` is the set of billed ids and `turn_message_ids` the set of counted turns. A turn whose usage
    was invalid in one window and valid in a later one is billed in the later window but was counted as a turn
    in the earlier.
    """

    tool_use_ids: set[str] = field(default_factory=set)
    message_ids: set[str] = field(default_factory=set)
    invalid_usage_message_ids: set[str] = field(default_factory=set)
    turn_message_ids: set[str] = field(default_factory=set)


@dataclass
class _AgentTypeRow:
    agents: int = 0
    turns: int = 0
    billed: float = 0.0
    whole_file_reads: int = 0
    whole_file_tokens: int = 0
    whole_file_weight: float = 0.0
    all_read_weight: float = 0.0
    large_weight: float = 0.0
    large_unedited_weight: float = 0.0
    repeat_in_agent_weight: float = 0.0
    seen_elsewhere_weight: float = 0.0
    kind_weight: Counter = field(default_factory=Counter)

    def merge(self, other: _AgentTypeRow) -> None:
        self.agents += other.agents
        self.turns += other.turns
        self.billed += other.billed
        self.whole_file_reads += other.whole_file_reads
        self.whole_file_tokens += other.whole_file_tokens
        self.whole_file_weight += other.whole_file_weight
        self.all_read_weight += other.all_read_weight
        self.large_weight += other.large_weight
        self.large_unedited_weight += other.large_unedited_weight
        self.repeat_in_agent_weight += other.repeat_in_agent_weight
        self.seen_elsewhere_weight += other.seen_elsewhere_weight
        self.kind_weight.update(other.kind_weight)


@dataclass
class _ReadCostTotals:
    sessions: int = 0
    billed_main: float = 0.0
    billed_subagent: float = 0.0
    api_calls: int = 0
    main_whole_file_events: list[_ReadEvent] = field(default_factory=list)
    # (scope, size bucket label) -> whole-file read count / summed est. tokens
    bucket_calls: Counter = field(default_factory=Counter)
    bucket_tokens: Counter = field(default_factory=Counter)
    rows_by_agent_type: dict[str, _AgentTypeRow] = field(default_factory=dict)
    repeated_reads: int = 0
    invalid_usage_turns: int = 0
    # Level tag -> count of unreadable directories, transcripts, and sidecars the scan skipped.
    scan_gaps: Counter = field(default_factory=Counter)

    @property
    def billed_total(self) -> float:
        return self.billed_main + self.billed_subagent


def _size_bucket(tokens: int) -> str:
    for upper_bound, label in _SIZE_BUCKETS:
        if tokens < upper_bound:
            return label
    return _SIZE_OVERFLOW_LABEL


def _file_kind(path: str) -> str:
    lowered = path.lower()
    if any(fragment in lowered for fragment in _INSTRUCTION_PATH_FRAGMENTS) or os.path.basename(lowered) == "claude.md":
        return _KIND_INSTRUCTION
    if os.path.splitext(lowered)[1] in _PROSE_EXTENSIONS:
        return _KIND_OTHER_PROSE
    return _KIND_CODE_OR_OTHER


def _billed_weight(usage: dict, weights: dict[str, float]) -> float:
    cache_write_1h, cache_write_5m = pricing._cache_write_split(usage)
    return (
        int(usage.get("cache_read_input_tokens") or 0) * weights["cache_read"]
        + cache_write_5m * weights["cache_write_5m"]
        + cache_write_1h * weights["cache_write_1h"]
        + int(usage.get("input_tokens") or 0) * weights["input"]
        + int(usage.get("output_tokens") or 0) * weights["output"]
    )


def _read_weight(event: _ReadEvent, weights: dict[str, float]) -> float:
    return event.tokens * (weights["cache_write_5m"] + event.remaining_turns * weights["cache_read"])


def _scan_window(records: Sequence[dict], seen_tool_use_ids: set[str]) -> _WindowScan:
    """Copies of request content carry the same tool blocks and would double every figure, so only conversation
    records count."""
    pending: dict[str, dict] = {}
    reads: list[dict] = []
    edited_at_turn: dict[str, int] = {}
    boundary_turns: list[int] = []
    usage_by_message: dict[str, dict] = {}
    invalid_usage_ids: set[str] = set()
    turn_ids: set[str] = set()
    turn_index = 0
    repeated_reads = 0

    for rec in records:
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            boundary_turns.append(turn_index)
            continue
        if rec.get("type") not in corpus.CONVERSATION_RECORD_TYPES:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") == "assistant" and msg.get("model") == pricing._SYNTHETIC_MODEL_ID:
            continue
        message_id = msg.get("id")
        if rec.get("type") == "assistant" and message_id:
            if message_id not in turn_ids:
                turn_ids.add(message_id)
                turn_index += 1
            usage = msg.get("usage")
            if usage:
                if cost._usage_is_invalid(usage):
                    invalid_usage_ids.add(message_id)
                else:
                    usage_by_message[message_id] = usage
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    tool_input = {}
                path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
                tool_id = block.get("id")
                if block.get("name") in _EDIT_TOOLS and path:
                    edited_at_turn[path] = turn_index
                elif block.get("name") == "Read" and path and isinstance(tool_id, str) and tool_id:
                    if tool_id in seen_tool_use_ids:
                        repeated_reads += 1
                    else:
                        pending[tool_id] = {
                            "path": path,
                            "turn": turn_index,
                            "whole_file": tool_input.get("offset") is None and tool_input.get("limit") is None,
                        }
            elif block.get("type") == "tool_result":
                result_tool_id = block.get("tool_use_id")
                owner = pending.pop(result_tool_id, None) if isinstance(result_tool_id, str) else None
                result = block.get("content")
                if owner is not None and not block.get("is_error") and isinstance(result, str):
                    seen_tool_use_ids.add(result_tool_id)
                    owner["tokens"] = len(result) // _READ_COST_CHARS_PER_TOKEN
                    owner["lines"] = result.count("\n")
                    reads.append(owner)

    events = []
    for read in reads:
        window_end = next((turn for turn in boundary_turns if turn >= read["turn"]), turn_index)
        events.append(_ReadEvent(
            path=read["path"],
            whole_file=read["whole_file"],
            tokens=read["tokens"],
            lines=read["lines"],
            remaining_turns=max(window_end - read["turn"], 0),
            edited_later=edited_at_turn.get(read["path"], -1) >= read["turn"],
        ))
    return _WindowScan(
        events=events,
        turn_message_ids=frozenset(turn_ids),
        usage_by_message=usage_by_message,
        repeated_reads=repeated_reads,
        invalid_usage_message_ids=frozenset(invalid_usage_ids.difference(usage_by_message)),
    )


def _new_billed_weight(scan: _WindowScan, seen_message_ids: set[str], weights: dict[str, float]) -> tuple[float, int]:
    billed = 0.0
    calls = 0
    for message_id, usage in scan.usage_by_message.items():
        if message_id in seen_message_ids:
            continue
        seen_message_ids.add(message_id)
        billed += _billed_weight(usage, weights)
        calls += 1
    return billed, calls


def _count_window_diagnostics(totals: _ReadCostTotals, seen: _SeenIds, scan: _WindowScan) -> None:
    """Call after `_new_billed_weight`."""
    totals.repeated_reads += scan.repeated_reads
    seen.invalid_usage_message_ids |= scan.invalid_usage_message_ids
    totals.invalid_usage_turns = len(seen.invalid_usage_message_ids - seen.message_ids)


def _agent_type_row(
    scan: _WindowScan, new_turns: int, billed: float, weights: dict[str, float], session_whole_file_paths: set[str],
) -> tuple[_AgentTypeRow, set[str]]:
    row = _AgentTypeRow(agents=int(new_turns > 0), turns=new_turns, billed=billed)
    read_in_this_window: set[str] = set()
    for event in scan.events:
        weight = _read_weight(event, weights)
        row.all_read_weight += weight
        if not event.whole_file:
            continue
        row.whole_file_reads += 1
        row.whole_file_tokens += event.tokens
        row.whole_file_weight += weight
        row.kind_weight[_file_kind(event.path)] += weight
        if event.tokens >= _LARGE_READ_TOKENS:
            row.large_weight += weight
            if not event.edited_later:
                row.large_unedited_weight += weight
        if event.path in read_in_this_window:
            row.repeat_in_agent_weight += weight
        elif event.path in session_whole_file_paths:
            row.seen_elsewhere_weight += weight
        read_in_this_window.add(event.path)
    return row, read_in_this_window


def _accumulate_session(
    totals: _ReadCostTotals,
    seen: _SeenIds,
    main_records: Sequence[dict],
    subagent_windows: Iterable[tuple[str, Sequence[dict]]],
    weights: dict[str, float],
) -> None:
    """Subagent windows are consumed one at a time, so a generator keeps only one parsed file in memory."""
    totals.sessions += 1
    main_scan = _scan_window([rec for rec in main_records if not rec.get("isSidechain")], seen.tool_use_ids)
    main_billed, main_calls = _new_billed_weight(main_scan, seen.message_ids, weights)
    totals.billed_main += main_billed
    totals.api_calls += main_calls
    _count_window_diagnostics(totals, seen, main_scan)
    seen.turn_message_ids |= main_scan.turn_message_ids

    session_whole_file_paths: set[str] = set()
    for event in main_scan.events:
        if not event.whole_file:
            continue
        session_whole_file_paths.add(event.path)
        totals.main_whole_file_events.append(event._replace(path=""))
        bucket_key = (_SCOPE_MAIN, _size_bucket(event.tokens))
        totals.bucket_calls[bucket_key] += 1
        totals.bucket_tokens[bucket_key] += event.tokens

    for agent_type, agent_records in subagent_windows:
        scan = _scan_window(agent_records, seen.tool_use_ids)
        billed, calls = _new_billed_weight(scan, seen.message_ids, weights)
        totals.billed_subagent += billed
        totals.api_calls += calls
        _count_window_diagnostics(totals, seen, scan)
        for event in scan.events:
            if event.whole_file:
                bucket_key = (_SCOPE_SUBAGENT, _size_bucket(event.tokens))
                totals.bucket_calls[bucket_key] += 1
                totals.bucket_tokens[bucket_key] += event.tokens
        new_turn_ids = scan.turn_message_ids - seen.turn_message_ids
        seen.turn_message_ids |= new_turn_ids
        row, read_in_window = _agent_type_row(scan, len(new_turn_ids), billed, weights, session_whole_file_paths)
        totals.rows_by_agent_type.setdefault(agent_type, _AgentTypeRow()).merge(row)
        session_whole_file_paths |= read_in_window


def _agent_type_for(agent_file: Path, scan_gaps: Counter | None = None) -> str:
    try:
        meta = json.loads(agent_file.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _UNKNOWN_AGENT_TYPE
    except (OSError, ValueError):
        if scan_gaps is not None:
            scan_gaps[_GAP_SUBAGENT_META] += 1
        return _UNKNOWN_AGENT_TYPE
    agent_type = meta.get("agentType") if isinstance(meta, dict) else None
    return agent_type if isinstance(agent_type, str) and agent_type else _UNKNOWN_AGENT_TYPE


def _read_subagent_windows(main_jsonl: Path, scan_gaps: Counter) -> Iterator[tuple[str, list[dict]]]:
    subagent_dir = main_jsonl.parent / main_jsonl.stem / corpus.SUBAGENT_SUBDIR
    for agent_file in scope._list_dir_recording_gaps(subagent_dir, scan_gaps, _GAP_SUBAGENT_DIR):
        if agent_file.suffix != ".jsonl":
            continue
        records = corpus._parse_jsonl_records(agent_file)
        if records is None:
            scan_gaps[_GAP_SUBAGENT_FILE] += 1
            continue
        yield _agent_type_for(agent_file, scan_gaps), records


def _fold_untracked_agent_types(
    rows_by_agent_type: dict[str, _AgentTypeRow], tracked_agent_types: frozenset[str],
) -> tuple[dict[str, _AgentTypeRow], _AgentTypeRow | None]:
    named: dict[str, _AgentTypeRow] = {}
    withheld: _AgentTypeRow | None = None
    for agent_type, row in rows_by_agent_type.items():
        if agent_type in tracked_agent_types:
            named[agent_type] = row
        else:
            if withheld is None:
                withheld = _AgentTypeRow()
            withheld.merge(row)
    return named, withheld


def _read_cost_roots() -> list[Path]:
    try:
        return [scope.config_dir() / "projects"]
    except ValueError as exc:
        print(f"read-cost: {exc}", file=sys.stderr)
        sys.exit(2)


def _refuse_wider_scope(args: argparse.Namespace, roots: Sequence[Path]) -> None:
    """Exit 2 unless the scan is this repo on exactly one account -- the scope that makes the output publishable."""
    if not bool(getattr(args, "this_repo", False)) or getattr(args, "projects", None) not in (None, "*"):
        print(
            "read-cost: requires --this-repo and refuses any --projects scope -- see docs/transcript-analysis.md",
            file=sys.stderr,
        )
        sys.exit(2)
    # Defense-in-depth: _read_cost_roots yields one root, but a direct caller of _read_cost_report bypasses it.
    if len(roots) != 1:
        print(
            "read-cost: resolved to more than one root -- refusing to report a multi-account figure",
            file=sys.stderr,
        )
        sys.exit(2)


def cmd_read_cost(args: argparse.Namespace) -> None:
    """See docs/transcript-analysis.md's `read-cost` section for the flags, the unit, and the publication scope."""
    _read_cost_report(args, _read_cost_roots())


def _scope_header(scope_label: str) -> str:
    return (
        f"READ COST SOURCES ({scope_label}; reads one account (the active account), this repository only;"
        f" {scope.TRANSCRIPT_CONFIG_DIRS_LABEL} not consulted)"
    )


def _read_cost_report(args: argparse.Namespace, roots: Sequence[Path]) -> None:
    _refuse_wider_scope(args, roots)
    weights = pricing._cache_read_equivalent_weights()

    totals = _ReadCostTotals()
    session_iter, scope_label = scope._resolve_project_scope(
        args, "read-cost", roots=roots, scan_gaps=totals.scan_gaps,
    )
    print(_scope_header(scope_label))

    seen = _SeenIds()
    for main_jsonl, main_records in session_iter:
        _accumulate_session(
            totals, seen, main_records, _read_subagent_windows(main_jsonl, totals.scan_gaps), weights,
        )

    _print_read_cost_report(totals, weights, redaction._repo_tracked_agent_type_names())


def _print_read_cost_report(
    totals: _ReadCostTotals, weights: dict[str, float], tracked_agent_types: frozenset[str],
) -> None:
    write_weight = weights["cache_write_5m"]
    print(
        "\nUnit: cache-read-token equivalents. A read's weight is its estimated tokens"
        f" ({_READ_COST_CHARS_PER_TOKEN} chars/token) times one 5-minute cache write ({write_weight:g}) plus one"
        " cache read (1) per later turn in its context window."
    )
    print(
        "One price ratio, taken from the base multipliers in pricing.py, is applied across all models;"
        " per-model cache-read exceptions, the fast-mode multiplier and the inference-geo multiplier are not"
        " applied, so every share is a token-equivalent share, not a dollar share."
    )
    print(
        "Class weights: " + ", ".join(f"{cls}={weight:g}" for cls, weight in weights.items()) + "."
    )

    total_billed = totals.billed_total
    print(
        f"\nsessions={totals.sessions:,} api_calls={totals.api_calls:,}"
        f" repeated_reads_skipped={totals.repeated_reads:,}"
    )
    print(_skipped_input_line(totals))
    print(
        f"billed share: main={render._pct_of(totals.billed_main, total_billed)}"
        f" subagent={render._pct_of(totals.billed_subagent, total_billed)}"
    )
    print(f"main-thread whole-file reads scanned={len(totals.main_whole_file_events):,}")

    for threshold in _LINE_THRESHOLDS:
        cohort = [event for event in totals.main_whole_file_events if event.lines > threshold]
        print(f"\n## Main-thread whole-file reads over {threshold} lines: {len(cohort):,}")
        for edited_later in (True, False):
            subset = [event for event in cohort if event.edited_later is edited_later]
            weight = sum(_read_weight(event, weights) for event in subset)
            median_remaining = statistics.median_high(event.remaining_turns for event in subset) if subset else 0
            print(
                f"  edited_later={edited_later}: n={len(subset):,} tokens={sum(e.tokens for e in subset):,}"
                f" median_remaining_turns={median_remaining}"
                f" share_of_total={render._pct_of(weight, total_billed)}"
                f" share_of_main={render._pct_of(weight, totals.billed_main)}"
            )

    print("\n## Whole-file reads by size bucket (est. tokens)")
    for thread_scope in (_SCOPE_MAIN, _SCOPE_SUBAGENT):
        for label in [bucket_label for _upper, bucket_label in _SIZE_BUCKETS] + [_SIZE_OVERFLOW_LABEL]:
            key = (thread_scope, label)
            print(
                f"  {thread_scope:9} {label:10} calls={totals.bucket_calls[key]:,}"
                f" tokens={totals.bucket_tokens[key]:,}"
            )

    named, withheld = _fold_untracked_agent_types(totals.rows_by_agent_type, tracked_agent_types)
    all_rows = _AgentTypeRow()
    for row in totals.rows_by_agent_type.values():
        all_rows.merge(row)
    ordered = sorted(named.items(), key=lambda item: (-item[1].whole_file_weight, item[0]))
    rows_to_print = [("TOTAL", all_rows), *ordered]
    if withheld is not None:
        rows_to_print.append((subagent_mix._WITHHELD_AGENT_TYPE_LABEL, withheld))

    print(
        "\n## Subagent whole-file reads by agent type"
        " (agent types this repo does not track are folded into one withheld row)"
    )
    for label, row in rows_to_print:
        _print_agent_type_row(label, row, total_billed)
        if withheld is not None and row is withheld:
            print(
                "    also holds agents with a missing, unreadable or typeless .meta.json;"
                f" unreadable: {totals.scan_gaps[_GAP_SUBAGENT_META]:,}"
            )


def _skipped_input_line(totals: _ReadCostTotals) -> str:
    gaps = totals.scan_gaps
    return (
        "skipped input:"
        f" unreadable_roots={gaps[scope._SCAN_GAP_ROOT]:,}"
        f" unreadable_project_dirs={gaps[scope._SCAN_GAP_PROJECT_DIR]:,}"
        f" unreadable_main_files={gaps[scope._SCAN_GAP_SESSION_FILE]:,}"
        f" unreadable_subagent_dirs={gaps[_GAP_SUBAGENT_DIR]:,}"
        f" unreadable_subagent_files={gaps[_GAP_SUBAGENT_FILE]:,}"
        f" unreadable_subagent_meta={gaps[_GAP_SUBAGENT_META]:,}"
        f" invalid_usage_turns={totals.invalid_usage_turns:,}"
    )


def _print_agent_type_row(label: str, row: _AgentTypeRow, total_billed: float) -> None:
    whole_weight = row.whole_file_weight
    mean_turns = row.turns / max(row.agents, 1)
    kinds = " ".join(
        f"{kind}={render._pct_of(weight, whole_weight)}" for kind, weight in sorted(row.kind_weight.items())
    )
    print(
        f"{label}: agents={row.agents:,} mean_turns={mean_turns:.0f}"
        f" share_of_total_spend={render._pct_of(row.billed, total_billed)}"
        f" whole_file_reads={row.whole_file_reads:,} whole_file_tokens={row.whole_file_tokens:,}"
        f" whole_file_weight_share={render._pct_of(whole_weight, total_billed)}"
        f" all_read_weight_share={render._pct_of(row.all_read_weight, total_billed)}"
    )
    print(
        f"    of whole: large={render._pct_of(row.large_weight, whole_weight)}"
        f" large_unedited={render._pct_of(row.large_unedited_weight, whole_weight)}"
        f" repeat_in_agent={render._pct_of(row.repeat_in_agent_weight, whole_weight)}"
        f" seen_elsewhere={render._pct_of(row.seen_elsewhere_weight, whole_weight)}"
        f" kinds: {kinds}"
    )


def _add_read_cost_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the read-cost subparser on build_parser()'s subparsers action,
    binding cmd_read_cost through set_defaults(func=...).
    """
    p_read_cost = subparsers.add_parser(
        "read-cost",
        help=(
            "What share of spend whole-file Read results account for, main thread versus subagent type, in"
            " cache-read-token equivalents. Requires --this-repo; reads one account and refuses a wider scope."
            " Aggregate-only output."
        ),
    )
    scope._add_project_scope_args(p_read_cost)
    p_read_cost.set_defaults(func=cmd_read_cost)
