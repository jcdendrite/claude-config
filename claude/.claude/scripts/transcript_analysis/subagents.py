"""The subagents command: cmd_subagents -- per-branch isSidechain turn counts
by model family, plus tool-result text bytes per thread and per producing
tool, every MCP tool name collapsed into _MCP_TOOL_BUCKET_LABEL.

Imports corpus, pricing, redaction, render, and scope by module (attribute
access, not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from transcript_analysis import corpus, pricing, redaction, render, scope


def cmd_subagents(args: argparse.Namespace) -> None:
    """isSidechain turn counts and model split per branch, plus total
    tool-result text bytes per thread-type (main vs. sidechain) per
    branch — a measured signal for whether verbose tool output is being
    delegated to subagents (see the subagent-delegation skill) rather than
    accumulated in the main thread's own prefix. Byte totals cover only
    text-typed tool-result blocks (via _content_text) — non-text blocks
    (e.g. images) are not counted. Byte totals are aggregate only: no
    tool-result content, file paths, session IDs, or cwd are ever printed.
    A second table breaks those same bytes down by the tool name that
    produced them (Read, Bash, Agent, …), still aggregate-only and with
    every mcp__<server>__<tool> name collapsed into one _MCP_TOOL_BUCKET_LABEL
    row — an MCP server name is a per-account integration identifier.

    --since limits both tables to records with a timestamp on or after the
    window start. The corpus-wide spawn and sidechain-turn counters feeding
    _warn_if_subagent_format_drift are read before this filter and are never
    narrowed by it, so a narrow --since window cannot manufacture a false
    format-drift warning.

    --config-dir (repeatable) scans additional Claude Code config
    directories the same way cost does.
    Under more than one root, branch names are redacted (via
    _root_scoped_display_label, account-<K>/branch-<N>) since a raw branch
    slug from a foreign account would otherwise be printed.
    _DO_NOT_PUBLISH_BANNER is stamped on stdout and stderr under multi-root.
    Under --this-repo, a branch prints raw (account-<K>/<branch>) only when
    a non-sidechain record attested that same (root, branch) pair anywhere
    in the corpus -- a branch seen only on a sidechain record stays opaque,
    since a subagent's own gitBranch can silently name a different repo than
    its parent session's. This attestation is corpus-wide, not narrowed by
    --since: a branch used by a real main-thread session outside the
    current --since window still discloses, since the question being
    answered is "did this repo ever use this branch," not "does this
    exact record appear in the displayed table." The same is true of
    --branches -- attestation is recorded before that filter too, though
    only the --since case carries a dedicated regression test.
    --this-repo's disclosure is repo-agnostic: it applies to whichever repo
    --this-repo resolves to for the invoking CWD, not specifically to
    claude-config.
    """
    roots = scope._resolve_cost_roots(args, "subagents")
    multi_root = len(roots) > 1
    this_repo = args.this_repo
    branch_filter = scope._branch_filter(args)
    since_ts, _since_raw = scope._parse_since_nd_arg(args, "subagents")

    if multi_root:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = scope._resolve_project_scope(
        args, "subagents", include_subagents=True, roots=roots
    )
    scope.print_resolved_scope("subagents", scope_label, roots)

    resolved_roots = [root.resolve() for root in roots] if multi_root else []
    # Resolved-path-sorted, not _root_index_for_path's raw scan-order position
    # — the same physical root must read as the same account-N here as in
    # every other multi-root diagnostic in this file (_build_redact_map,
    # cost's per-row key), regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(roots) if multi_root else {}
    branch_redact_map: dict[tuple[int, str], str] = {}

    # Keyed on (root_index_or_None, raw gitBranch) — root_index is always None
    # under single-root scope (the common case, unchanged from before
    # --config-dir existed); a real index under multi-root keeps two
    # accounts' identically-named branch from merging into one row. This
    # index is scan-order, purely for in-run grouping — the printed label
    # (_branch_label, below) translates it through redact_ordinals before
    # ever reaching output.
    branch_data: dict[tuple[int | None, str], dict[str, dict[str, int]]] = defaultdict(
        lambda: {"main": defaultdict(int), "sidechain": defaultdict(int)}
    )
    branch_bytes: dict[tuple[int | None, str], dict[str, int]] = defaultdict(
        lambda: {"main": 0, "sidechain": 0}
    )
    branch_tool_bytes: dict[tuple[int | None, str], dict[str, dict[str, int]]] = defaultdict(
        lambda: {"main": defaultdict(int), "sidechain": defaultdict(int)}
    )
    # (root_index_or_None, raw gitBranch) pairs a non-sidechain record attested.
    # --this-repo discloses a branch raw only when it's a member of this set,
    # since a sidechain record's own gitBranch can silently name a different
    # repo than its parent session's.
    main_thread_branches: set[tuple[int | None, str]] = set()
    corpus_spawns = 0
    corpus_sidechain_turns = 0

    for jsonl, records in session_iter:
        root_idx = scope._root_index_for_path(jsonl, resolved_roots) if multi_root else None
        records = pricing.dedup_turns_by_request_id(records)
        corpus_spawns += pricing._count_subagent_spawns(records)
        # tool_use id -> tool name, built inline as records are walked in
        # order: a tool_result always follows its own tool_use within the
        # same file, and include_subagents=True appends each subagent file
        # as a contiguous block after the main file, so one sequential pass
        # (no second corpus pass) is enough to pair every tool_result seen
        # below with the tool name that produced it.
        tool_use_names: dict[str, str] = {}
        for rec in records:
            rec_type = rec.get("type")
            if rec_type == "assistant":
                # corpus_sidechain_turns counts every isSidechain assistant
                # turn read, before the branch filter below — it feeds
                # _warn_if_subagent_format_drift's corpus-wide sanity check,
                # not the per-branch table, so it must not be filtered.
                if bool(rec.get("isSidechain")):
                    corpus_sidechain_turns += 1
                for block in ((rec.get("message") or {}).get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                        tool_use_names[block["id"]] = block.get("name") or "unknown"
                branch = rec.get("gitBranch") or ""
                if branch and not bool(rec.get("isSidechain")):
                    main_thread_branches.add((root_idx, branch))
                if not branch or (branch_filter and branch not in branch_filter):
                    continue
                if since_ts is not None:
                    rec_ts = corpus._parse_ts(rec.get("timestamp"))
                    if rec_ts is None or rec_ts < since_ts:
                        continue
                fam = render._fam((rec.get("message") or {}).get("model", ""))
                thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
                branch_data[(root_idx, branch)][thread][fam] += 1
            elif rec_type == "user":
                branch = rec.get("gitBranch") or ""
                if branch and not bool(rec.get("isSidechain")):
                    main_thread_branches.add((root_idx, branch))
                if not branch or (branch_filter and branch not in branch_filter):
                    continue
                if since_ts is not None:
                    rec_ts = corpus._parse_ts(rec.get("timestamp"))
                    if rec_ts is None or rec_ts < since_ts:
                        continue
                thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
                content = (rec.get("message") or {}).get("content") or []
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    key = (root_idx, branch)
                    nbytes = len(render._content_text(block.get("content", "")).encode())
                    branch_bytes[key][thread] += nbytes
                    tool_name = tool_use_names.get(block.get("tool_use_id") or "", "unknown")
                    if tool_name.startswith("mcp__"):
                        tool_name = _MCP_TOOL_BUCKET_LABEL
                    branch_tool_bytes[key][thread][tool_name] += nbytes

    pricing._warn_if_subagent_format_drift(corpus_spawns, corpus_sidechain_turns)

    if not branch_data and not branch_bytes:
        print("No data found.")
        return

    def _branch_label(key: tuple[int | None, str]) -> str:
        root_idx, branch = key
        return (
            redaction._root_scoped_display_label(
                "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map,
                disclose=this_repo and key in main_thread_branches,
            )
            if root_idx is not None
            else render._sanitize_table_cell(branch)
        )

    print(
        f"{'Branch':<40} {'Thread':<10} {'Opus':>6} {'Sonnet':>7} {'Haiku':>6} {'Other':>6}"
        f" {'Bytes':>18}"
    )
    print("-" * 99)
    for key in sorted(set(branch_data) | set(branch_bytes)):
        label = _branch_label(key)
        first = True
        for thread in ("main", "sidechain"):
            d = branch_data[key][thread]
            bytes_total = branch_bytes[key][thread]
            if not any(d.values()) and not bytes_total:
                continue
            row_label = label if first else ""
            first = False
            print(
                f"{row_label:<40} {thread:<10} {d.get('opus', 0):>6} {d.get('sonnet', 0):>7} "
                f"{d.get('haiku', 0):>6} {d.get('other', 0):>6} {bytes_total:>18,}"
            )

    if any(any(tb.values()) for by_thread in branch_tool_bytes.values() for tb in by_thread.values()):
        # Header says "Side", not "Thread" (unlike the table above): several
        # existing tests anchor _table_cols on header_contains="Thread" and
        # require it to match exactly one printed line.
        print(f"\n{'Branch':<40} {'Side':<10} {'Tool':<20} {'Bytes':>18}")
        print("-" * 92)
        for key in sorted(branch_tool_bytes):
            label = _branch_label(key)
            first = True
            for thread in ("main", "sidechain"):
                tool_bytes = branch_tool_bytes[key][thread]
                for tool_name in sorted(tool_bytes, key=lambda t: (-tool_bytes[t], t)):
                    nbytes = tool_bytes[tool_name]
                    if not nbytes:
                        continue
                    row_label = label if first else ""
                    first = False
                    print(f"{row_label:<40} {thread:<10} {render._sanitize_table_cell(tool_name):<20} {nbytes:>18,}")


# subagents' tool-result byte grouping bucket for every mcp__<server>__<tool>
# tool name — an MCP server name is a per-account integration identifier, so
# every MCP tool call collapses into this one row instead of one row per server.
_MCP_TOOL_BUCKET_LABEL = "mcp__*"
