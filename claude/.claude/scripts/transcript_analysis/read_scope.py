"""The read-scope command family: cmd_read_scope and every helper used only by
it -- the per-session Read-call census, repeat-whole-file-read detection, and
per-file-and-sessionId prompt-token growth.

Imports corpus, pricing, render, and scope by module (attribute access, not
by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from transcript_analysis import corpus, pricing, render, scope

# ~4 chars/token, the same rough English/code estimate _EDIT_FORMAT_CHARS_PER_TOKEN
# uses. A deliberate second pin rather than a shared constant: recalibrating one
# report's published figures must not silently move the other's.
_READ_SCOPE_CHARS_PER_TOKEN = 4

# Tools that answer "which part of this file do I need" before a targeted Read.
# Their mean result size prices the locate step a narrow-read discipline adds.
_READ_SCOPE_LOCATE_TOOLS = frozenset({"Grep", "Glob"})

_READ_SCOPE_COHORT_TARGETED = "targeted"
_READ_SCOPE_COHORT_WHOLE_FILE = "whole_file"
_READ_SCOPE_COHORT_PAGES = "pages"
_READ_SCOPE_COHORT_UNPARSED = "unparsed_input"

_READ_SCOPE_SCOPE_MAIN = "main"
_READ_SCOPE_SCOPE_SUBAGENT = "subagent"

# Result-size histogram buckets, in estimated tokens (chars // 4) — a call's
# result text length, not its input. The 2,000-token boundary lines up with
# the gross-ceiling threshold the case study's ceiling arithmetic uses.
_READ_SCOPE_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (500, "0-499"),
    (2000, "500-1999"),
    (5000, "2000-4999"),
    (15000, "5000-14999"),
)
_READ_SCOPE_SIZE_OVERFLOW_LABEL = "15000+"


def _read_scope_size_bucket(tokens: int) -> str:
    for upper_bound, label in _READ_SCOPE_SIZE_BUCKETS:
        if tokens < upper_bound:
            return label
    return _READ_SCOPE_SIZE_OVERFLOW_LABEL


def _classify_read_call(tool_input: dict) -> str:
    """Classify one Read tool_use's input by scope shape.

    A missing file_path (e.g. only __unparsedToolInput, or an empty input) is
    unparsed_input regardless of any other field: its scope is unknowable, and
    filing it as whole-file would inflate the cohort every published share is
    stated against. pages is checked before offset/limit since a PDF page-range
    read scopes via a different mechanism entirely, not layered on offset/limit.
    offset and limit are each checked with `is not None`, never truthiness --
    offset=0 is a valid first-line read and is falsy in Python.
    """
    if not tool_input.get("file_path"):
        return _READ_SCOPE_COHORT_UNPARSED
    if tool_input.get("pages") is not None:
        return _READ_SCOPE_COHORT_PAGES
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return _READ_SCOPE_COHORT_TARGETED
    return _READ_SCOPE_COHORT_WHOLE_FILE


def _new_read_scope_stats() -> dict:
    return {
        "read_total": 0,
        "offset_n": 0,  # Read calls carrying offset (is not None)
        "limit_n": 0,  # Read calls carrying limit (is not None)
        "both_n": 0,  # Read calls carrying both offset and limit
        "cohort_n": Counter(),  # cohort label -> Read call count
        "unpaired": 0,  # Read tool_use with no matching tool_result in this session
        "error_result": 0,  # is_error tool_result for a Read call
        "non_text_result": 0,  # non-string tool_result content for a Read call (e.g. an image block)
        "cohort_scope_count": Counter(),  # (cohort, scope) -> result count reaching the histogram
        "cohort_scope_tokens": Counter(),  # (cohort, scope) -> est. token sum
        "size_hist": Counter(),  # (cohort, scope, bucket label) -> count
        # Same key -> summed est. tokens. Counts alone cannot answer "what share
        # of whole-file-read tokens sits above N", which is what any ceiling
        # estimate and the case study's revisit trigger are both stated against.
        "size_hist_tokens": Counter(),
        "read_result_tokens_total": 0,  # every Read tool_result's est. tokens, any cohort/error/text shape
        "all_tool_result_tokens_total": 0,  # every tool_result's est. tokens, any tool -- cross-check denominator
        # Locate-step sizing: a targeted read has to be located first, so any
        # saving quoted against whole-file reads is gross until this is netted.
        "locate_call_n": 0,
        "locate_result_tokens_total": 0,
        "repeat_whole_file_reads": 0,  # whole-file re-reads of a path within the same source-file/sessionId partition
        "repeat_whole_file_tokens": 0,
        "repeat_whole_file_output_log_reads": 0,  # sub-count of the above whose path ends .output/.log
        "growth_tokens": 0,  # prompt-token growth, per "Computing the denominator"
        "growth_unparseable_ts_excluded": 0,  # growth deltas dropped: --since active, owning turn's ts didn't parse
    }


def _merge_read_scope_stats(dst: dict, src: dict) -> None:
    dst["read_total"] += src["read_total"]
    dst["offset_n"] += src["offset_n"]
    dst["limit_n"] += src["limit_n"]
    dst["both_n"] += src["both_n"]
    dst["cohort_n"].update(src["cohort_n"])
    dst["unpaired"] += src["unpaired"]
    dst["error_result"] += src["error_result"]
    dst["non_text_result"] += src["non_text_result"]
    dst["cohort_scope_count"].update(src["cohort_scope_count"])
    dst["cohort_scope_tokens"].update(src["cohort_scope_tokens"])
    dst["size_hist"].update(src["size_hist"])
    dst["size_hist_tokens"].update(src["size_hist_tokens"])
    dst["read_result_tokens_total"] += src["read_result_tokens_total"]
    dst["all_tool_result_tokens_total"] += src["all_tool_result_tokens_total"]
    dst["locate_call_n"] += src["locate_call_n"]
    dst["locate_result_tokens_total"] += src["locate_result_tokens_total"]
    dst["repeat_whole_file_reads"] += src["repeat_whole_file_reads"]
    dst["repeat_whole_file_tokens"] += src["repeat_whole_file_tokens"]
    dst["repeat_whole_file_output_log_reads"] += src["repeat_whole_file_output_log_reads"]
    dst["growth_tokens"] += src["growth_tokens"]
    dst["growth_unparseable_ts_excluded"] += src["growth_unparseable_ts_excluded"]


def _read_scope_growth_for_group(group: list[dict], since_ts: float | None) -> tuple[int, int]:
    """Sum of positive prompt-token growth deltas within one source-file group
    (one _read_session_file_partitioned entry: the main transcript or one
    subagent file).

    Keys the delta chain by each assistant record's own sessionId rather than
    picking one reference id for the whole group: a subagent transcript file
    is named by its own agent id, but its records carry the *parent*
    session's sessionId, so neither the file's own stem nor a first-seen-in-
    iteration guess is a valid "this group's session" ground truth. Keying by
    sessionId means an interleaved foreign-session record simply forms its
    own chain and contributes its own real growth, instead of being dropped
    or corrupting a neighbouring session's delta. A record with no sessionId
    at all folds into the ""-keyed chain -- neither excluded nor treated as
    its own session, preserving this function's long-standing leniency for
    absent ids. Resets the whole per-session map at each compact_boundary
    record, since a compaction boundary applies to the file, not to one
    session. The first turn of every resulting per-session sequence has no
    predecessor and so contributes nothing. A turn with absent or malformed
    usage is skipped without breaking its session's chain for the turn after
    it. A shrinking context contributes nothing (never negative). Every
    chain is built over every record regardless of --since; --since filters
    only the completed deltas, by the later (owning) turn's own timestamp --
    fail-closed: a delta whose owning turn's timestamp doesn't parse is
    excluded rather than included, and counted in the returned exclusion
    total so the report's own growth figure stays auditable against
    --since's promise.

    Returns (growth_tokens, deltas_excluded_for_unparseable_timestamp).
    """
    prev_context_by_session: dict[str, int] = {}
    total = 0
    unparseable_ts_excluded = 0

    for rec in group:
        rec_type = rec.get("type")
        if rec_type == "system" and rec.get("subtype") == "compact_boundary":
            # Resets every session's chain, not just the compacted one: a
            # compact_boundary record carries no sessionId, so which session it
            # belongs to is not recoverable from the data. Costs one real delta
            # for any other session mid-chain in the same file. Inert on every
            # transcript shape seen so far -- a main file's records all carry
            # its own session id, a subagent file's all carry its parent's --
            # so a file holding two live chains does not currently arise.
            prev_context_by_session.clear()
            continue
        if rec_type != "assistant":
            continue

        usage = (rec.get("message") or {}).get("usage")
        if not usage:
            continue

        session_key = rec.get("sessionId") or ""
        context_at_turn = pricing._context_at_turn(usage)
        prev_context = prev_context_by_session.get(session_key)
        if prev_context is not None:
            delta = context_at_turn - prev_context
            if delta > 0:
                rec_ts = corpus._parse_ts(rec.get("timestamp"))
                if since_ts is not None and rec_ts is None:
                    unparseable_ts_excluded += 1
                elif since_ts is None or rec_ts >= since_ts:
                    total += delta
        prev_context_by_session[session_key] = context_at_turn

    return total, unparseable_ts_excluded


def _read_scope_repeat_whole_file_reads(groups: list[list[dict]]) -> tuple[int, int, int]:
    """Repeat whole-file-read detection, scoped to the same partition the
    growth chain uses (per source-file group, and per sessionId within a
    group) rather than to the whole flattened session.

    A parent transcript and its subagent are separate context windows: a
    subagent re-reading a file its parent already read is not a redundant
    read, it's the only way that subagent can see the file at all. Scoping
    to the flattened session would count every such cross-file read as a
    repeat, inflating the figure with reads that were never avoidable.

    Pairs each group's own Read tool_use/tool_result independently (a Read's
    result always lives in the same source file as its call), since this
    detection needs its own per-partition token histories and cannot reuse
    the flat pass's already-merged read_calls table. Returns
    (repeat_reads, repeat_tokens, repeat_output_log_reads) — pure aggregates;
    no file path is retained past this function.
    """
    # (group_index, sessionId-or-"") -> file_path -> [est_tokens, ...] in read order
    sizes_by_partition: dict[tuple[int, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for group_idx, group in enumerate(groups):
        read_calls: dict[str, dict] = {}  # tool_use_id -> {"file_path", "partition_key"}
        for rec in group:
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            session_id = rec.get("sessionId") or ""
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "tool_use":
                    if block.get("name") != "Read":
                        continue
                    tool_input = block.get("input") or {}
                    if _classify_read_call(tool_input) != _READ_SCOPE_COHORT_WHOLE_FILE:
                        continue
                    read_calls[block.get("id")] = {
                        "file_path": tool_input.get("file_path") or "",
                        "partition_key": (group_idx, session_id),
                    }
                elif block_type == "tool_result":
                    owner = read_calls.pop(block.get("tool_use_id"), None)
                    if owner is None or block.get("is_error"):
                        continue
                    result_content = block.get("content")
                    if not isinstance(result_content, str):
                        continue
                    tokens = len(result_content) // _READ_SCOPE_CHARS_PER_TOKEN
                    sizes_by_partition[owner["partition_key"]][owner["file_path"]].append(tokens)

    repeat_reads = 0
    repeat_tokens = 0
    repeat_output_log_reads = 0
    for by_path in sizes_by_partition.values():
        for file_path, sizes in by_path.items():
            if len(sizes) < 2:
                continue
            repeats = sizes[1:]
            repeat_reads += len(repeats)
            repeat_tokens += sum(repeats)
            if file_path.endswith((".output", ".log")):
                repeat_output_log_reads += len(repeats)

    return repeat_reads, repeat_tokens, repeat_output_log_reads


def _scan_read_scope_session(records: list[dict], groups: list[list[dict]], since_ts: float | None) -> dict:
    """One session's Read-call census, single pass over the flattened record
    order, plus per-group prompt-token growth and repeat-whole-file-read
    detection.

    `records` is the main thread + merged subagent files in flat, file-
    concatenation order (per read_session_file) — everything here except
    growth and repeat-whole-file-read detection runs over it, since
    isSidechain is all that scope (main vs subagent) bucketing needs.
    `groups` is the same session's records kept separate per source file
    (per _read_session_file_partitioned) — growth and repeat-whole-file-read
    detection both need the file (and, within a file, sessionId) boundary
    the flat order discards; see _read_scope_growth_for_group and
    _read_scope_repeat_whole_file_reads.

    Every returned figure is a pure aggregate (counts and token sums) — no
    file path, filename, path fragment, or session identifier is retained
    past this function or printed by any caller.
    """
    stats = _new_read_scope_stats()

    read_calls: dict[str, dict] = {}  # tool_use_id -> {"cohort", "scope", "file_path"}
    locate_calls: set[str] = set()  # tool_use_ids of Grep/Glob calls, for locate-step sizing
    matched: set[str] = set()

    for rec in records:
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        is_subagent = bool(rec.get("isSidechain"))
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_name = block.get("name")
                if tool_name in _READ_SCOPE_LOCATE_TOOLS:
                    # A targeted read has to be located first. Sizing that step
                    # is what lets a saving be quoted net rather than gross.
                    locate_calls.add(block.get("id"))
                    stats["locate_call_n"] += 1
                    continue
                if tool_name != "Read":
                    continue
                tool_id = block.get("id")
                tool_input = block.get("input") or {}
                has_offset = tool_input.get("offset") is not None
                has_limit = tool_input.get("limit") is not None
                if has_offset:
                    stats["offset_n"] += 1
                if has_limit:
                    stats["limit_n"] += 1
                if has_offset and has_limit:
                    stats["both_n"] += 1
                cohort = _classify_read_call(tool_input)
                stats["read_total"] += 1
                stats["cohort_n"][cohort] += 1
                read_calls[tool_id] = {
                    "cohort": cohort,
                    "scope": _READ_SCOPE_SCOPE_SUBAGENT if is_subagent else _READ_SCOPE_SCOPE_MAIN,
                    "file_path": tool_input.get("file_path") or "",
                }
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                result_content = block.get("content")
                is_error = bool(block.get("is_error"))
                result_tokens = (
                    len(result_content) // _READ_SCOPE_CHARS_PER_TOKEN if isinstance(result_content, str) else 0
                )
                # Cross-check denominator: every tool's own result, Read or not.
                stats["all_tool_result_tokens_total"] += result_tokens
                if tool_use_id in locate_calls:
                    stats["locate_result_tokens_total"] += result_tokens

                owner = read_calls.get(tool_use_id)
                if owner is None:
                    continue
                matched.add(tool_use_id)
                stats["read_result_tokens_total"] += result_tokens

                if is_error:
                    stats["error_result"] += 1
                    continue
                if not isinstance(result_content, str):
                    stats["non_text_result"] += 1
                    continue

                cohort = owner["cohort"]
                if cohort not in (_READ_SCOPE_COHORT_TARGETED, _READ_SCOPE_COHORT_WHOLE_FILE):
                    continue
                thread_scope = owner["scope"]
                stats["cohort_scope_count"][(cohort, thread_scope)] += 1
                stats["cohort_scope_tokens"][(cohort, thread_scope)] += result_tokens
                size_bucket = _read_scope_size_bucket(result_tokens)
                stats["size_hist"][(cohort, thread_scope, size_bucket)] += 1
                stats["size_hist_tokens"][(cohort, thread_scope, size_bucket)] += result_tokens

    stats["unpaired"] = len(read_calls) - len(matched)

    (
        stats["repeat_whole_file_reads"],
        stats["repeat_whole_file_tokens"],
        stats["repeat_whole_file_output_log_reads"],
    ) = _read_scope_repeat_whole_file_reads(groups)

    for group in groups:
        growth, unparseable_ts_excluded = _read_scope_growth_for_group(group, since_ts)
        stats["growth_tokens"] += growth
        stats["growth_unparseable_ts_excluded"] += unparseable_ts_excluded

    return stats


def cmd_read_scope(args: argparse.Namespace) -> None:
    """CLI entry point for the read-scope subcommand.

    Root resolution happens here, mirroring cmd_edit_format, so --config-dir
    validation exits before any scan work.
    """
    roots = scope._resolve_cost_roots(args, subcommand="read-scope")
    _read_scope_report(args, roots)


def _read_scope_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Single-pass Read-call scope census: offset/limit/pages classification
    against the full call count, result-token distribution by targeted/
    whole-file cohort and main/subagent scope, repeat-whole-file-read
    aggregates, and per-file-and-sessionId-partitioned prompt-token growth.
    One reproducible scan producing every figure the case study cites.

    roots is None for every direct caller other than cmd_read_scope (this
    module's own tests included) — mirrors edit-format's own contract,
    including the absence of the per-account breakdown below.

    This report's own content never varies with `redact`: like edit-format,
    it carries no project name, session ID, file path, or path fragment —
    the repeat-whole-file-read aggregates are pure counts and token sums, and
    per-account rows use account-N labels. --no-redact is still accepted and
    still enforces the same multi-root refusal and DO NOT PUBLISH banner, for
    CLI parity.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "read-scope: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = scope._parse_since_nd_arg(args, "read-scope")
    since_label = since_raw or ""

    session_iter, scope_label = scope._resolve_project_scope(args, "read-scope", include_subagents=True, roots=roots)
    scope.print_resolved_scope("read-scope", scope_label, scan_roots)

    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []

    stats = _new_read_scope_stats()
    per_account: list[dict] = [_new_read_scope_stats() for _ in scan_roots] if multi_root else []

    for jsonl, records in session_iter:
        # session_iter already read and parsed this file once internally (to
        # decide whether to yield it at all); this second, partitioned read
        # is the cost of reusing _resolve_project_scope's shared iterator,
        # which has no variant that also exposes the per-file boundary the
        # growth chain and repeat-whole-file-read detection need.
        groups = corpus._read_session_file_partitioned(jsonl, include_subagents=True)
        session_stats = _scan_read_scope_session(records, groups, since_ts)
        _merge_read_scope_stats(stats, session_stats)
        if multi_root:
            idx = scope._root_index_for_path(jsonl, resolved_scan_roots)
            _merge_read_scope_stats(per_account[idx], session_stats)

    _print_read_scope_report(stats, per_account if multi_root else None, since_label)


def _read_scope_cohort_bucket_token_total(stats: dict, cohort: str) -> int:
    """Sum of `cohort`'s size_hist_tokens across both main and subagent scope
    -- the shared denominator each size-histogram bucket line's percentage
    divides by, so a subagent-dominated cohort's tokens aren't hidden behind
    the printing scope's own, smaller total."""
    bucket_labels = [label for _upper, label in _READ_SCOPE_SIZE_BUCKETS] + [_READ_SCOPE_SIZE_OVERFLOW_LABEL]
    return sum(
        stats["size_hist_tokens"].get((cohort, thread_scope, label), 0)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
        for label in bucket_labels
    )


def _print_read_scope_report(stats: dict, per_account: list[dict] | None, since_label: str) -> None:
    read_total = stats["read_total"]
    cohort_n = stats["cohort_n"]
    targeted_n = cohort_n.get(_READ_SCOPE_COHORT_TARGETED, 0)
    whole_file_n = cohort_n.get(_READ_SCOPE_COHORT_WHOLE_FILE, 0)
    pages_n = cohort_n.get(_READ_SCOPE_COHORT_PAGES, 0)
    unparsed_n = cohort_n.get(_READ_SCOPE_COHORT_UNPARSED, 0)

    print("\n## Read-call census\n")
    print(f"Read calls: {read_total:,}")
    print(f"  offset present: {stats['offset_n']:,}")
    print(f"  limit present:  {stats['limit_n']:,}")
    print(f"  both present:   {stats['both_n']:,}")
    # Cohort shares divide by the full Read call census, never targeted +
    # whole_file — pages/unparsed_input calls are real Read calls that would
    # otherwise silently vanish from the arithmetic.
    print(f"\ntargeted    {targeted_n:,}  ({render._pct_of(targeted_n, read_total)} of Read calls)")
    print(f"whole_file  {whole_file_n:,}  ({render._pct_of(whole_file_n, read_total)} of Read calls)")
    print(f"\npages (Read calls scoping a PDF via `pages` rather than offset/limit): {pages_n:,}")
    print(
        "unparsed_input (Read tool_use whose input carried no file_path, e.g. only"
        f" __unparsedToolInput -- scope unknowable): {unparsed_n:,}"
    )
    print(f"unpaired (Read tool_use with no matching tool_result found in this session): {stats['unpaired']:,}")
    print(f"error_result (is_error tool_result for a Read call, excluded from the size histogram): {stats['error_result']:,}")
    print(
        "non_text_result (non-string tool_result content for a Read call, e.g. an image"
        f" block, excluded from the size histogram): {stats['non_text_result']:,}"
    )

    print("\n## Result token distribution by cohort x scope\n")
    cpt = _READ_SCOPE_CHARS_PER_TOKEN
    for cohort, cohort_label in (
        (_READ_SCOPE_COHORT_TARGETED, "targeted"),
        (_READ_SCOPE_COHORT_WHOLE_FILE, "whole_file"),
    ):
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT):
            count = stats["cohort_scope_count"].get((cohort, thread_scope), 0)
            tokens = stats["cohort_scope_tokens"].get((cohort, thread_scope), 0)
            print(f"  {cohort_label:12} {thread_scope:9} count={count:8,}  tokens=~{tokens:12,}")

    whole_file_tokens = sum(
        stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_WHOLE_FILE, thread_scope), 0)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
    )
    targeted_tokens = sum(
        stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_TARGETED, thread_scope), 0)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
    )
    result_tokens_total = whole_file_tokens + targeted_tokens
    print(f"\nwhole_file share of targeted+whole_file result tokens: {render._pct_of(whole_file_tokens, result_tokens_total)}")
    subagent_whole_file_tokens = stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_WHOLE_FILE, _READ_SCOPE_SCOPE_SUBAGENT), 0)
    print(f"whole_file tokens inside subagents: {render._pct_of(subagent_whole_file_tokens, whole_file_tokens)}")

    print("\nsize histogram (est. tokens):\n")
    bucket_labels = [label for _upper, label in _READ_SCOPE_SIZE_BUCKETS] + [_READ_SCOPE_SIZE_OVERFLOW_LABEL]
    for cohort, cohort_label in (
        (_READ_SCOPE_COHORT_TARGETED, "targeted"),
        (_READ_SCOPE_COHORT_WHOLE_FILE, "whole_file"),
    ):
        cohort_tokens = _read_scope_cohort_bucket_token_total(stats, cohort)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT):
            cohort_scope_count = stats["cohort_scope_count"].get((cohort, thread_scope), 0)
            print(f"  {cohort_label} / {thread_scope}:")
            for label in bucket_labels:
                count = stats["size_hist"].get((cohort, thread_scope, label), 0)
                tokens = stats["size_hist_tokens"].get((cohort, thread_scope, label), 0)
                print(
                    f"    {label:10} {count:6,}  ({render._pct_of(count, cohort_scope_count)})"
                    f"  ~{tokens:11,} tok  ({render._pct_of(tokens, cohort_tokens)} of {cohort_label} tokens)"
                )

    print(
        "\n## Repeat whole-file reads (same path read whole-file more than once in the same"
        " source file / sessionId -- a subagent re-reading what its parent already read is a"
        " separate context window, not a repeat)\n"
    )
    print(f"repeat reads: {stats['repeat_whole_file_reads']:,}  (~{stats['repeat_whole_file_tokens']:,} tok)")
    print(f"  of which .output/.log suffixed: {stats['repeat_whole_file_output_log_reads']:,}")

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Prompt-token growth ({title_since})\n")
    growth_tokens = stats["growth_tokens"]
    print(f"prompt-token growth: {growth_tokens:,}")
    print(
        "growth deltas excluded (--since active, owning turn's timestamp unparseable):"
        f" {stats['growth_unparseable_ts_excluded']:,}"
    )
    print(
        "Read-result tokens as share of prompt-token growth: "
        f"{render._pct_of(stats['read_result_tokens_total'], growth_tokens)}"
    )
    print(
        "Read-result tokens as share of total tool-result tokens (self-consistent"
        f" cross-check, both ~{cpt} chars/tok): "
        f"{render._pct_of(stats['read_result_tokens_total'], stats['all_tool_result_tokens_total'])}"
    )
    locate_n = stats["locate_call_n"]
    locate_mean = stats["locate_result_tokens_total"] // locate_n if locate_n else 0
    print(
        f"\nlocate-step cost (Grep/Glob calls, the step a targeted read adds): {locate_n:,} calls, "
        f"~{stats['locate_result_tokens_total']:,} tok, mean ~{locate_mean:,} tok/call -- "
        f"any saving quoted against whole-file reads is gross until this is netted against it"
    )

    if per_account is not None:
        print("\n## Per-account breakdown\n")
        for idx, account_stats in enumerate(per_account):
            account_label = f"account-{idx + 1}"
            a_read_total = account_stats["read_total"]
            if a_read_total == 0:
                print(f"  {account_label:10} no Read calls")
                continue
            a_cohort_n = account_stats["cohort_n"]
            a_targeted = a_cohort_n.get(_READ_SCOPE_COHORT_TARGETED, 0)
            a_whole_file = a_cohort_n.get(_READ_SCOPE_COHORT_WHOLE_FILE, 0)
            print(
                f"  {account_label:10} calls={a_read_total:6,}  "
                f"targeted={render._pct_of(a_targeted, a_read_total):>6}  "
                f"whole_file={render._pct_of(a_whole_file, a_read_total):>6}"
            )
