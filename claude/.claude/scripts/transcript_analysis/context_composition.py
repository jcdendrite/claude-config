"""The context-composition command: cmd_context_composition, rate-weighted token-turns by
content-item category over each context sequence, gated by the static-prefix reconciliation
residual's per-sequence instability, and its subparser registration.

Imports corpus, pricing, read_scope, render, scope, and subagents by module (attribute access, not
by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from transcript_analysis import corpus, pricing, read_scope, render, scope, subagents

# See .claude/plans/context-composition-analyzer.md for the full design.

# Closed content-item taxonomy. tool_call/tool_result are further qualified
# with a tool-name suffix (see _normalize_composition_tool_name) at the point
# they're accumulated, keeping the label set bounded (known tool names plus
# the shared MCP bucket) rather than an open per-session vocabulary.
_CATEGORY_USER_TEXT = "user_text"
_CATEGORY_ASSISTANT_TEXT = "assistant_text"
_CATEGORY_ASSISTANT_THINKING = "assistant_thinking"
_CATEGORY_COMPACT_SUMMARY = "compact_summary"
_CATEGORY_TOOL_CALL = "tool_call"
_CATEGORY_TOOL_RESULT = "tool_result"
_CATEGORY_UNCLASSIFIED = "unclassified"


def _normalize_composition_tool_name(name: str | None) -> str:
    """A missing name (e.g. a tool_result whose owning tool_use isn't in this sequence) reports
    as "unknown", never a raw id."""
    if not name:
        return "unknown"
    return subagents._MCP_TOOL_BUCKET_LABEL if name.startswith("mcp__") else name


def _classify_content_item(record_type: str, item, *, is_compact_summary: bool = False) -> tuple[str, int]:
    """`is_compact_summary` marks a carried-forward compaction digest, not a fresh prompt."""
    if isinstance(item, dict):
        block_type = item.get("type")
        if block_type == "text":
            category = (
                _CATEGORY_COMPACT_SUMMARY if is_compact_summary
                else _CATEGORY_USER_TEXT if record_type == "user"
                else _CATEGORY_ASSISTANT_TEXT
            )
            return category, len(item.get("text") or "") // read_scope._READ_SCOPE_CHARS_PER_TOKEN
        if block_type == "thinking":
            return _CATEGORY_ASSISTANT_THINKING, len(item.get("thinking") or "") // read_scope._READ_SCOPE_CHARS_PER_TOKEN
        if block_type == "tool_use":
            payload = json.dumps(item.get("input") or {}, separators=(",", ":"))
            return _CATEGORY_TOOL_CALL, len(payload) // read_scope._READ_SCOPE_CHARS_PER_TOKEN
        if block_type == "tool_result":
            text = render._content_text(item.get("content", ""))
            return _CATEGORY_TOOL_RESULT, len(text) // read_scope._READ_SCOPE_CHARS_PER_TOKEN
        try:
            payload = json.dumps(item, separators=(",", ":"))
        except TypeError:
            payload = str(item)
        return _CATEGORY_UNCLASSIFIED, len(payload) // read_scope._READ_SCOPE_CHARS_PER_TOKEN
    if isinstance(item, str):
        category = (
            _CATEGORY_COMPACT_SUMMARY if is_compact_summary
            else _CATEGORY_USER_TEXT if record_type == "user"
            else _CATEGORY_ASSISTANT_TEXT
        )
        return category, len(item) // read_scope._READ_SCOPE_CHARS_PER_TOKEN
    return _CATEGORY_UNCLASSIFIED, 0


def _split_context_sequences(records: list[dict]) -> list[list[dict]]:
    """Splits at a compact_boundary record or an isSidechain toggle between consecutive records."""
    sequences: list[list[dict]] = []
    current: list[dict] = []
    current_sidechain: bool | None = None
    for rec in records:
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            if current:
                sequences.append(current)
            current = []
            current_sidechain = None
            continue
        rec_sidechain = bool(rec.get("isSidechain"))
        if current and rec_sidechain != current_sidechain:
            sequences.append(current)
            current = []
        current.append(rec)
        current_sidechain = rec_sidechain
    if current:
        sequences.append(current)
    return sequences


def _context_composition_turn_rate_scale(usage: dict) -> float:
    """Fast-mode (2x) / US-inference-geo (1.1x) multiplier scale for one turn, applied uniformly
    to every rate class that turn -- the same usage.get("speed")/"inference_geo" checks
    _price_turn applies at its own dollar-scaling step, reused here for the
    multiplier-only (not dollar) context-composition weighting."""
    scale = 1.0
    if usage.get("speed") == "fast":
        scale *= pricing._FAST_MODE_RATE_MULTIPLIER
    if usage.get("inference_geo") == "us":
        scale *= pricing._INFERENCE_GEO_US_RATE_MULTIPLIER
    return scale


# Engineer-chosen starting point, not a vendor-specified value: a sequence's residual range
# spanning more than half its own mean (range/mean >= 0.5) trips the refusal gate.
_CONTEXT_COMPOSITION_RESIDUAL_INSTABILITY_REFUSAL_THRESHOLD = 0.5

# Same reasoning as above: an engineer-chosen tolerance for the introduced-vs-resident split
# diagnostic, which is informational only (see _print_context_composition_report) and never gates
# the refusal decision above.
_CONTEXT_COMPOSITION_SPLIT_DISCREPANCY_TOLERANCE = 0.2


def _context_composition_residual_instability(residuals: Sequence[int]) -> float:
    """Range-over-mean instability of one sequence's reconciliation residuals; 0.0 if empty or
    all-zero, infinite if any residual is negative."""
    if not residuals:
        return 0.0
    if min(residuals) < 0:
        return math.inf
    mean = sum(residuals) / len(residuals)
    if mean == 0:
        return 0.0
    return (max(residuals) - min(residuals)) / mean


def _new_context_composition_stats() -> dict:
    return {
        "weighted_by_category": Counter(),  # category -> rate-weighted token-turns (float)
        "item_counts": Counter(),  # category -> classified item count
        "unclassified_count": 0,
        "sequences_scanned": 0,
        "turns_scanned": 0,
        "residuals": [],  # list of per-sequence [context_at_turn(t) - resident_size(t), ...] lists
        "since_excluded_turns": 0,  # turns whose rate contribution --since excluded (see below)
        "introduced_size_total": 0,  # Sigma of our own per-turn "newly introduced" token bookkeeping
        "actual_new_size_total": 0,  # Sigma of (context_at_turn - cache_read_input_tokens) from usage directly
    }


def _merge_context_composition_stats(dst: dict, src: dict) -> None:
    dst["weighted_by_category"].update(src["weighted_by_category"])
    dst["item_counts"].update(src["item_counts"])
    dst["unclassified_count"] += src["unclassified_count"]
    dst["sequences_scanned"] += src["sequences_scanned"]
    dst["turns_scanned"] += src["turns_scanned"]
    dst["residuals"].extend(src["residuals"])
    dst["since_excluded_turns"] += src["since_excluded_turns"]
    dst["introduced_size_total"] += src["introduced_size_total"]
    dst["actual_new_size_total"] += src["actual_new_size_total"]


def _scan_context_composition_sequence(records: list[dict], since_ts: float | None) -> dict:
    """turn_introduced uses the NEXT assistant turn, since an assistant turn's own generated
    content is that turn's OUTPUT, not its input."""
    stats = _new_context_composition_stats()

    turn_usages: list[dict] = []
    turn_timestamps: list[float | None] = []
    items_by_intro: dict[int, list[tuple[str, int]]] = defaultdict(list)
    tool_name_by_id: dict[str, str] = {}
    item_counts: Counter = stats["item_counts"]
    unclassified_count = 0

    def _record_items(rec: dict) -> list[tuple[str, int]]:
        nonlocal unclassified_count
        msg = rec.get("message") or {}
        content = msg.get("content", "")
        record_type = rec.get("type")
        is_compact_summary = bool(rec.get("isCompactSummary"))
        blocks = content if isinstance(content, list) else ([content] if content else [])
        entries: list[tuple[str, int]] = []
        for block in blocks:
            category, size = _classify_content_item(record_type, block, is_compact_summary=is_compact_summary)
            if category == _CATEGORY_UNCLASSIFIED:
                unclassified_count += 1
            elif category == _CATEGORY_TOOL_CALL and isinstance(block, dict):
                tool_id = block.get("id")
                tool_name = _normalize_composition_tool_name(block.get("name"))
                if tool_id:
                    tool_name_by_id[tool_id] = tool_name
                category = f"{category}:{tool_name}"
            elif category == _CATEGORY_TOOL_RESULT and isinstance(block, dict):
                owner_name = tool_name_by_id.get(block.get("tool_use_id") or "", "unknown")
                category = f"{category}:{owner_name}"
            item_counts[category] += 1
            entries.append((category, size))
        return entries

    turn_count = 0
    for rec in records:
        rec_type = rec.get("type")
        if rec_type == "assistant":
            usage = (rec.get("message") or {}).get("usage") or {}
            for entry in _record_items(rec):
                items_by_intro[turn_count + 1].append(entry)
            turn_usages.append(usage)
            turn_timestamps.append(corpus._parse_ts(rec.get("timestamp")))
            turn_count += 1
        elif rec_type == "user":
            for entry in _record_items(rec):
                items_by_intro[turn_count].append(entry)

    stats["unclassified_count"] = unclassified_count
    stats["sequences_scanned"] = 1
    stats["turns_scanned"] = turn_count

    if turn_count == 0:
        return stats

    read_mult = [0.0] * turn_count
    write_mult = [0.0] * turn_count
    context_at_turn = [0] * turn_count
    actual_new = [0] * turn_count
    since_excluded = 0

    for t, usage in enumerate(turn_usages):
        in_window = True
        if since_ts is not None:
            ts = turn_timestamps[t]
            in_window = ts is not None and ts >= since_ts
            if not in_window:
                since_excluded += 1

        scale = _context_composition_turn_rate_scale(usage)
        read_mult[t] = pricing._CACHE_READ_MULTIPLIER * scale if in_window else 0.0
        eph_1h, eph_5m = pricing._cache_write_split(usage)
        if eph_1h + eph_5m > 0:
            write_base = (
                eph_1h * pricing._CACHE_WRITE_1H_MULTIPLIER + eph_5m * pricing._CACHE_WRITE_5M_MULTIPLIER
            ) / (eph_1h + eph_5m)
        else:
            # No cache-write tokens this turn -- _price_turn's own rate for that case is the
            # plain input rate (1x), not a cache-write tier.
            write_base = 1.0
        write_mult[t] = write_base * scale if in_window else 0.0

        context_at_turn[t] = pricing._context_at_turn(usage)
        actual_new[t] = context_at_turn[t] - int(usage.get("cache_read_input_tokens", 0))

    stats["since_excluded_turns"] = since_excluded

    read_mult_prefix = [0.0] * (turn_count + 1)
    for t in range(turn_count):
        read_mult_prefix[t + 1] = read_mult_prefix[t] + read_mult[t]

    last_turn = turn_count - 1
    introduced_size = [0] * turn_count
    weighted_by_category = stats["weighted_by_category"]

    for intro, entries in items_by_intro.items():
        if intro > last_turn:
            continue  # generated on the sequence's own last turn's output; never sent back, never resident
        introduced_size[intro] += sum(size for _category, size in entries)
        read_span = read_mult_prefix[last_turn + 1] - read_mult_prefix[intro + 1]
        per_item_multiplier = read_span + write_mult[intro]
        for category, size in entries:
            weighted_by_category[category] += size * per_item_multiplier

    resident_size = [0] * turn_count
    running = 0
    for t in range(turn_count):
        running += introduced_size[t]
        resident_size[t] = running

    stats["residuals"] = [[context_at_turn[t] - resident_size[t] for t in range(turn_count)]]
    stats["introduced_size_total"] = sum(introduced_size)
    stats["actual_new_size_total"] = sum(actual_new)

    return stats


def _scan_context_composition_session(groups: list[list[dict]], since_ts: float | None) -> dict:
    """One session's composition scan: each source-file group (main transcript, then each
    subagents/*.jsonl -- per _read_session_file_partitioned) is deduped by requestId and split
    into context sequences independently, since a group boundary and a compact_boundary/
    isSidechain-toggle boundary are both real context-window resets that must never blend two
    sequences' turn indexing together."""
    stats = _new_context_composition_stats()
    for group in groups:
        deduped = pricing.dedup_turns_by_request_id(group)
        for sequence in _split_context_sequences(deduped):
            _merge_context_composition_stats(stats, _scan_context_composition_sequence(sequence, since_ts))
    return stats


def cmd_context_composition(args: argparse.Namespace) -> None:
    """CLI entry point for the context-composition subcommand.

    Root resolution happens here, mirroring cmd_context_distribution, so --config-dir validation
    exits before any scan work.
    """
    roots = scope._resolve_cost_roots(args, subcommand="context-composition")
    _context_composition_report(args, roots)


def _context_composition_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Redaction contract mirrors context-distribution: no redact map, no per-root/per-account/per-project breakdown."""
    redact: bool = not bool(getattr(args, "no_redact", False))

    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement point for this refusal,
    # but every direct caller of this function (including this module's own tests) bypasses that
    # boundary.
    if not redact and multi_root:
        print(
            "context-composition: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = scope._parse_since_nd_arg(args, "context-composition")
    since_label = since_raw or ""

    session_iter, scope_label = scope._resolve_project_scope(
        args, "context-composition", include_subagents=True, roots=roots
    )

    if roots is not None:
        # Mirrors cmd_cost's/context-distribution's own per-root scan diagnostic
        # (_scan_root_transcripts) -- pure counts, no composition data, so it stays outside the
        # "no per-root breakdown" redaction contract above.
        glob = scope._projects_glob(args)
        this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
        redact_ordinals: dict[Path, int] = scope._redaction_ordinals(scan_roots)
        for root in scan_roots:
            root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
            try:
                scanned, skipped = scope._scan_root_transcripts(root, glob, slugs=this_repo_slugs)
            except PermissionError as exc:
                detail = str(exc) if not redact else "permission denied"
                print(
                    f"context-composition: {root_label}: cannot scan ({detail})"
                    " — treating as 0 transcripts",
                    file=sys.stderr,
                )
                scanned, skipped = 0, 0
            print(
                f"context-composition: {root_label}: scanned {scanned:,} transcripts,"
                f" {skipped:,} skipped (unreadable)"
            )
            if scanned == 0:
                print(
                    f"WARNING: context-composition: {root_label}: no transcripts found for this scope"
                    " — check the config dir and --projects/--this-repo filter."
                )

    scope.print_resolved_scope("context-composition", scope_label, scan_roots)

    stats = _new_context_composition_stats()
    for jsonl, _records in session_iter:
        # session_iter already read and parsed this file once internally (to decide whether to
        # yield it at all); this second, partitioned read is the cost of reusing
        # _resolve_project_scope's shared iterator, the same tradeoff _read_scope_report makes.
        groups = corpus._read_session_file_partitioned(jsonl, include_subagents=True)
        _merge_context_composition_stats(stats, _scan_context_composition_session(groups, since_ts))

    _print_context_composition_report(stats, since_label)


def _print_context_composition_report(stats: dict, since_label: str) -> None:
    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Context composition report ({title_since})\n")

    print(
        f"Sequences scanned: {stats['sequences_scanned']:,}   Turns scanned: {stats['turns_scanned']:,}"
        f"   Unclassified items: {stats['unclassified_count']:,}"
    )
    if since_label:
        print(
            "Turns excluded from weighting (--since active, unparseable/out-of-window timestamp):"
            f" {stats['since_excluded_turns']:,}"
        )

    residuals_by_sequence = stats["residuals"]
    flat_residuals = [r for sequence in residuals_by_sequence for r in sequence]
    print(
        "\n## Reconciliation (static-prefix residual: context_at_turn - reconstructed resident size)\n"
    )
    if flat_residuals:
        mean = sum(flat_residuals) / len(flat_residuals)
        print(
            f"turns: {len(flat_residuals):,}   mean={mean:,.0f}   min={min(flat_residuals):,}"
            f"   max={max(flat_residuals):,}"
        )
    else:
        print("turns: 0 (nothing scanned)")

    # Gated on the worst single sequence, never on residuals pooled across sequences -- two
    # individually-stable sequences with different static-prefix baselines must not combine into
    # a spurious refusal.
    instability = max(
        (_context_composition_residual_instability(sequence) for sequence in residuals_by_sequence),
        default=0.0,
    )
    instability_str = "inf" if math.isinf(instability) else f"{instability:.2f}"
    print(
        f"instability (range/mean): {instability_str}"
        f"   refusal threshold: {_CONTEXT_COMPOSITION_RESIDUAL_INSTABILITY_REFUSAL_THRESHOLD}"
    )

    if instability >= _CONTEXT_COMPOSITION_RESIDUAL_INSTABILITY_REFUSAL_THRESHOLD:
        print(
            "\nREFUSED: the static-prefix residual is not approximately constant across turns"
            " in this scope (instability at or above threshold) -- the per-item residency model"
            " disagrees with itself too much to trust a category ranking. No ranking is printed."
        )
        return

    actual_new_total = stats["actual_new_size_total"]
    introduced_total = stats["introduced_size_total"]
    if actual_new_total:
        split_discrepancy = abs(introduced_total - actual_new_total) / actual_new_total
        print(
            f"\nIntroduced-vs-resident split (corpus-wide, not scoped by --since): our"
            f" bookkeeping={introduced_total:,} tok, usage's own new-token split={actual_new_total:,} tok"
            f" (discrepancy {split_discrepancy:.1%})"
        )
        if split_discrepancy > _CONTEXT_COMPOSITION_SPLIT_DISCREPANCY_TOLERANCE:
            print(
                "  NOTE: discrepancy exceeds tolerance -- ambiguous between a wrong write-timing"
                " rule and chars//4 estimation bias correlated with introduced-vs-resident"
                " content, not necessarily a rate-classification bug."
            )

    weighted = stats["weighted_by_category"]
    total_weighted = sum(weighted.values())
    print("\n## Category (rate-weighted token-turns share)\n")
    if not total_weighted:
        print("No priced turns in scope.")
        return
    print(f"{'Category':<32} {'Token-turns':>16} {'Share':>8} {'Items':>10}")
    for category, value in sorted(weighted.items(), key=lambda kv: -kv[1]):
        count = stats["item_counts"].get(category, 0)
        print(f"{category:<32} {value:>16,.0f} {render._pct_of(value, total_weighted):>8} {count:>10,}")


def _add_context_composition_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the context-composition subparser on build_parser()'s subparsers action,
    binding cmd_context_composition through set_defaults(func=...).
    """
    p_context_comp = subparsers.add_parser(
        "context-composition",
        help=(
            "Rate-weighted token-turns by content-item category (user/assistant text, thinking,"
            " tool calls/results, compact summaries), gated by a reconciliation check against"
            " _context_at_turn -- the static-prefix residual refuses to print a ranking above a"
            " named instability threshold. Aggregate-only output; redacted by default."
        ),
    )
    scope._add_project_scope_args(p_context_comp)
    p_context_comp.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_context_comp.add_argument(
        "--since", metavar="Nd",
        help=(
            "Limit rate-weighted turns to timestamps in the last N days (e.g. 35d); reconciliation"
            " and the introduced-vs-resident split diagnostic both still scan/accumulate every turn."
        ),
    )
    p_context_comp.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_context_comp.set_defaults(func=cmd_context_composition)
