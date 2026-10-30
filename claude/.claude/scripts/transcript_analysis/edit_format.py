"""The edit-format command: cmd_edit_format, the Edit/Write/MultiEdit call census with per-tool
failure classification, governance-hook re-bucketing, not_found cause attribution, and
old_string/new_string/Write token overhead, with a per-account breakdown above one root, and its
subparser registration.

Imports pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own
top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from transcript_analysis import pricing, render, scope

# Edit/Write/MultiEdit are the only tools whose call/failure counts
# edit-format tracks; MultiEdit is kept as a member even though the tool no
# longer exists in current transcripts, so a future rename or reintroduction
# is counted rather than silently zeroing its denominator.
EDIT_FAMILY_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# Known str_replace-mechanical failure shapes, matched case-sensitively
# against a failed Edit/Write/MultiEdit tool_result's own text, in order —
# the first match wins. "noop" matches the literal message Claude Code's
# Edit tool emits when old_string and new_string are identical.
_EDIT_KNOWN_FAILURE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("String to replace not found", "not_found"),
    ("has not been read yet", "unread"),
    ("but replace_all is false", "multi_match"),
    ("old_string and new_string are exactly the same", "noop"),
)

# This repo's own governance-hook/harness denial wordings that can deny an
# edit-family call, matched case-insensitively.
# Serves a narrower purpose than _denial_hook_label's general "blocked by
# <name> hook/gate" extraction above. Three of these six (path-spelling,
# permissions, worktree-isolation) are harness-native denial text rather
# than a hook's own wording, so they fall outside _denial_hook_label's
# enumerated label set.
_EDIT_GOVERNANCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("blocked by plan-review gate", "plan-review"),
    ("reviewer-tree-mutation", "reviewer-tree"),
    ("worktree-enforcement", "worktree"),
    ("cannot be safely resolved", "path-spelling"),
    ("denied by your permission settings", "permissions"),
    ("isolated in the worktree", "worktree-isolation"),
)

_EDIT_FORMAT_UNCLASSIFIED = "unclassified"

_EDIT_CAUSE_REDACTED_CREDENTIAL = "redacted_credential"
_EDIT_CAUSE_WHITESPACE_ONLY = "whitespace_only"
_EDIT_CAUSE_CONTENT_DIFFERS = "content_differs"
_EDIT_CAUSE_ABANDONED_NO_RETRY = "abandoned_no_retry"
_EDIT_CAUSE_IDENTICAL_RETRY = "identical_retry"
# A not_found failure whose owner isn't "Edit" (MultiEdit's historical
# failure shape can emit the same text): edit_order only tracks Edit's own
# old_string, so there is nothing to pair this failure against for cause
# attribution -- counted here rather than silently dropped or crashing.
_EDIT_CAUSE_OWNER_NOT_TRACKED = "owner_not_tracked"

# redact-credential-values.sh's fixed replacement token (_lib.sh:980) — a
# not_found failure whose old_string or error text carries this was caused by
# the redactor rewriting file content the model had already read, not by a
# genuine uniqueness or staleness mismatch.
_REDACTED_CREDENTIAL_TOKEN = "[REDACTED-CREDENTIAL]"

# old_string length buckets for the size-distribution histogram, each
# (exclusive upper bound, label) tried in order; a length at or past every
# bound falls to _EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL.
_EDIT_OLD_STRING_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (100, "0-99"),
    (300, "100-299"),
    (700, "300-699"),
    (1500, "700-1499"),
)
_EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL = "1500+"

# ~4 chars/token, a standard rough estimate (not a per-tokenizer
# measurement) for English/code text — the same ratio measure_overhead.py
# used to produce this plan's headline token figures.
_EDIT_FORMAT_CHARS_PER_TOKEN = 4


def _old_string_size_bucket(length: int) -> str:
    for upper_bound, label in _EDIT_OLD_STRING_SIZE_BUCKETS:
        if length < upper_bound:
            return label
    return _EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL


def _tool_result_text(content) -> str:
    """A tool_result's content is either a plain string or a content-block
    list; either way, render it to one string for substring matching."""
    return content if isinstance(content, str) else json.dumps(content)


def _new_edit_format_stats() -> dict:
    return {
        "calls": Counter(),  # tool name -> call count
        "known_failures": Counter(),  # (tool, label) -> count
        "governance": Counter(),  # governance label -> count
        "unclassified": 0,  # edit-family errors matching neither list above
        "unpaired": 0,  # is_error tool_result whose tool_use_id has no known owner
        "cause": Counter(),  # not_found cause label -> count
        "old_chars": 0,
        "new_chars": 0,
        "write_chars": 0,
        "output_tokens": 0,
        "old_string_size_hist": Counter(),  # size-bucket label -> count
    }


def _merge_edit_format_stats(dst: dict, src: dict) -> None:
    dst["calls"].update(src["calls"])
    dst["known_failures"].update(src["known_failures"])
    dst["governance"].update(src["governance"])
    dst["unclassified"] += src["unclassified"]
    dst["unpaired"] += src["unpaired"]
    dst["cause"].update(src["cause"])
    dst["old_chars"] += src["old_chars"]
    dst["new_chars"] += src["new_chars"]
    dst["write_chars"] += src["write_chars"]
    dst["output_tokens"] += src["output_tokens"]
    dst["old_string_size_hist"].update(src["old_string_size_hist"])


def _edit_notfound_cause(tool_use_id: str, err_text: str, edit_order: list[tuple[str, str, str]]) -> str:
    """Attribute one Edit `not_found` failure's cause by pairing it with the
    NEXT Edit call on the same file_path (in this session's own record
    order) and diffing the two old_strings under whitespace normalization —
    not by pattern-matching the failed old_string alone, which cannot
    distinguish "this string contains indentation" (true of most code) from
    "this edit failed because of whitespace."
    """
    idx = next((i for i, (tid, _fp, _old) in enumerate(edit_order) if tid == tool_use_id), None)
    if idx is None:
        # owner == "Edit" was already confirmed via `ids` before this is
        # called, so the failing call's own tool_use must already be in
        # edit_order — every Edit tool_use is appended there unconditionally,
        # and a tool_result always follows its tool_use in record order.
        raise AssertionError(
            "edit-format: a not_found failure's owning Edit tool_use is missing from"
            " this session's own edit_order — ids and edit_order disagree"
        )
    _tid, file_path, old = edit_order[idx]
    if _REDACTED_CREDENTIAL_TOKEN in old or _REDACTED_CREDENTIAL_TOKEN in err_text:
        return _EDIT_CAUSE_REDACTED_CREDENTIAL
    next_old = next((o for _tid2, fp2, o in edit_order[idx + 1 :] if fp2 == file_path), None)
    if next_old is None:
        return _EDIT_CAUSE_ABANDONED_NO_RETRY
    if next_old == old:
        return _EDIT_CAUSE_IDENTICAL_RETRY
    # Full whitespace strip, not run-collapse: a spacing-convention change
    # ("x=1" -> "x = 1") inserts whitespace where none existed, which a
    # collapse-runs-to-one-space comparison would treat as still different --
    # stripping entirely is what classifies that case as whitespace_only.
    # Known narrow false-positive this accepts: two strings that differ only
    # in WHERE a whitespace run sits at a token boundary ("foo bar" vs
    # "foob ar") collide after stripping. Below current scale to fix (see
    # docs/case-studies/hashline-edit-format.md's classifier-honesty
    # discussion) -- revisit if this bucket's share grows.
    if re.sub(r"\s+", "", next_old) == re.sub(r"\s+", "", old):
        return _EDIT_CAUSE_WHITESPACE_ONLY
    return _EDIT_CAUSE_CONTENT_DIFFERS


def _scan_edit_format_session(records: list[dict]) -> dict:
    """One session's (main thread + merged subagent files, per read_session_file)
    Edit/Write/MultiEdit call and failure census, single pass.

    `ids` records every tool_use's id -> name, not just edit-family ones, so
    an is_error tool_result naming a non-edit-family owner (e.g. Bash) can be
    told apart from one whose owner is genuinely unknown (unpaired) rather
    than counting both the same way.
    """
    stats = _new_edit_format_stats()
    ids: dict[str, str] = {}
    edit_order: list[tuple[str, str, str]] = []  # (tool_use_id, file_path, old_string)
    # not_found cause attribution needs the FULL edit_order (including edits
    # that come after the failure) to find the retry, so classification is
    # deferred to a second pass below rather than run inline as each failure
    # is seen -- an inline lookup could only ever see edits already scanned.
    pending_notfound: list[tuple[str, str]] = []  # (tool_use_id, err_text)

    # One API call = one turn: dedup merges a requestId run's usage into a
    # single record, so output_tokens below is summed once per turn, not
    # once per content block.
    records = pricing.dedup_turns_by_request_id(records)

    for rec in records:
        msg = rec.get("message") or {}
        usage = msg.get("usage") or {}
        if isinstance(usage.get("output_tokens"), int):
            stats["output_tokens"] += usage["output_tokens"]
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                name = block.get("name")
                tool_id = block.get("id")
                ids[tool_id] = name
                tool_input = block.get("input") or {}
                if name == "Edit":
                    stats["calls"]["Edit"] += 1
                    old = tool_input.get("old_string") or ""
                    new = tool_input.get("new_string") or ""
                    stats["old_chars"] += len(old)
                    stats["new_chars"] += len(new)
                    stats["old_string_size_hist"][_old_string_size_bucket(len(old))] += 1
                    edit_order.append((tool_id, tool_input.get("file_path", "?"), old))
                elif name == "Write":
                    stats["calls"]["Write"] += 1
                    stats["write_chars"] += len(tool_input.get("content") or "")
                elif name == "MultiEdit":
                    stats["calls"]["MultiEdit"] += 1
            elif block_type == "tool_result" and block.get("is_error"):
                tool_use_id = block.get("tool_use_id")
                owner = ids.get(tool_use_id)
                if owner is None:
                    stats["unpaired"] += 1
                    continue
                if owner not in EDIT_FAMILY_TOOLS:
                    continue
                text = _tool_result_text(block.get("content"))
                label = next((lbl for pat, lbl in _EDIT_KNOWN_FAILURE_PATTERNS if pat in text), None)
                if label is not None:
                    stats["known_failures"][(owner, label)] += 1
                    if label == "not_found":
                        if owner == "Edit":
                            pending_notfound.append((tool_use_id, text))
                        else:
                            stats["cause"][_EDIT_CAUSE_OWNER_NOT_TRACKED] += 1
                    continue
                lowered = text.lower()
                gov_label = next((lbl for pat, lbl in _EDIT_GOVERNANCE_PATTERNS if pat in lowered), None)
                if gov_label is not None:
                    stats["governance"][gov_label] += 1
                else:
                    stats["unclassified"] += 1

    for tool_use_id, err_text in pending_notfound:
        stats["cause"][_edit_notfound_cause(tool_use_id, err_text, edit_order)] += 1
    return stats


def cmd_edit_format(args: argparse.Namespace) -> None:
    """CLI entry point for the edit-format subcommand.

    Root resolution happens here, mirroring cmd_cost/cmd_context_distribution,
    so --config-dir validation exits before any scan work.
    """
    roots = scope._resolve_cost_roots(args, subcommand="edit-format")
    _edit_format_report(args, roots)


def _edit_format_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Single-pass Edit/Write/MultiEdit call census: per-tool failure
    classification, governance-hook re-bucketing, not_found cause
    attribution, and old_string/new_string/Write token overhead — one
    reproducible scan producing every figure, so separate runs at different
    corpus sizes cannot disagree with each other the way separate ad hoc
    scripts did.

    roots is None for every direct caller other than cmd_edit_format (this
    module's own tests included) — mirrors cost/context-distribution's own
    single-root-by-default contract, including the absence of the per-account
    breakdown below, which only a multi-root scan (an explicit roots list of
    more than one root) emits.

    This report's own content never varies with `redact` — like
    context-distribution, it carries no project name or session ID, and its
    per-account breakdown is always labelled account-N. --no-redact is still
    accepted and still enforces the same multi-root refusal and DO NOT
    PUBLISH banner as cost/context-distribution, for CLI parity.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "edit-format: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = scope._resolve_project_scope(args, "edit-format", include_subagents=True, roots=roots)
    scope.print_resolved_scope("edit-format", scope_label, scan_roots)

    # Resolved once, outside the per-session loop below, mirroring cost's own
    # _root_index_for_path usage — re-resolving every root on every session
    # would be a per-element filesystem stat inside that loop.
    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    # Keyed by _redaction_ordinals, not _root_index_for_path's raw scan-order
    # position — the same physical root must read as the same account-N here
    # as in cost's and context-distribution's own per-account breakdowns,
    # regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(scan_roots) if multi_root else {}

    stats = _new_edit_format_stats()
    per_account: dict[int, dict] = (
        {ordinal: _new_edit_format_stats() for ordinal in redact_ordinals.values()} if multi_root else {}
    )

    for jsonl, records in session_iter:
        session_stats = _scan_edit_format_session(records)
        _merge_edit_format_stats(stats, session_stats)
        if multi_root:
            root_position = scope._root_index_for_path(jsonl, resolved_scan_roots)
            ordinal = redact_ordinals[resolved_scan_roots[root_position]]
            _merge_edit_format_stats(per_account[ordinal], session_stats)

    _print_edit_format_report(stats, per_account if multi_root else None)


def _print_edit_format_report(stats: dict, per_account: dict[int, dict] | None) -> None:
    calls = stats["calls"]
    edit_n = calls.get("Edit", 0)
    write_n = calls.get("Write", 0)
    multi_edit_n = calls.get("MultiEdit", 0)

    print("\n## Edit-family call census\n")
    print(f"Edit       {edit_n:,}")
    print(f"Write      {write_n:,}")
    print(f"MultiEdit  {multi_edit_n:,}  (recognized tool; expect 0 in a current corpus)")
    print(f"TOTAL      {edit_n + write_n + multi_edit_n:,}")

    print("\n## Failures by tool (str_replace-mechanical + no-op)\n")
    for (tool, label), count in sorted(stats["known_failures"].items()):
        denom = calls.get(tool, 0)
        print(f"  {tool:10} {label:12} count={count:6,}  rate={render._pct_of(count, denom)} of {tool}")

    mechanical = sum(
        count
        for (tool, label), count in stats["known_failures"].items()
        if tool == "Edit" and label in ("not_found", "unread", "multi_match")
    )
    noop = stats["known_failures"].get(("Edit", "noop"), 0)
    print(
        "\nstr_replace-mechanical (Edit not_found+unread+multi_match, no-ops excluded): "
        f"{mechanical:,} / {edit_n:,} ({render._pct_of(mechanical, edit_n)})"
    )
    print(
        "all non-governance Edit errors (no-ops included): "
        f"{mechanical + noop:,} / {edit_n:,} ({render._pct_of(mechanical + noop, edit_n)})"
    )

    print("\n## not_found cause attribution (next-edit-same-file diff, whitespace-normalized)\n")
    cause_total = sum(stats["cause"].values())
    for cause, count in stats["cause"].most_common():
        print(f"  {cause:22} count={count:4,}  share={render._pct_of(count, cause_total)}")

    print("\n## Governance-hook denials (excluded from the format failure rate)\n")
    governance_total = sum(stats["governance"].values())
    for _pattern, label in _EDIT_GOVERNANCE_PATTERNS:
        print(f"  {label:20} count={stats['governance'].get(label, 0):6,}")
    print(f"  {'TOTAL':20} count={governance_total:6,}")
    print(f"\n{_EDIT_FORMAT_UNCLASSIFIED} (edit-family errors matching neither list above): {stats['unclassified']:,}")

    print(f"\nunpaired (is_error tool_result with no matching tool_use in this session): {stats['unpaired']:,}")

    print("\n## Token/char overhead\n")
    old_chars = stats["old_chars"]
    new_chars = stats["new_chars"]
    write_chars = stats["write_chars"]
    output_tokens = stats["output_tokens"]
    edit_payload = old_chars + new_chars
    cpt = _EDIT_FORMAT_CHARS_PER_TOKEN
    print(f"old_string chars: {old_chars:,}  (~{old_chars // cpt:,} tok)")
    print(f"new_string chars: {new_chars:,}  (~{new_chars // cpt:,} tok)")
    print(f"old_string share of Edit payload: {render._pct_of(old_chars, edit_payload)}")
    print(f"mean old_string chars/edit: {old_chars / edit_n:.0f}" if edit_n else "mean old_string chars/edit: n/a")
    print(f"write content chars: {write_chars:,}  (~{write_chars // cpt:,} tok)")
    print(f"total assistant output tokens (all sessions): {output_tokens:,}")
    print(f"old_string share of total output tokens: {render._pct_of(old_chars // cpt, output_tokens)}")
    print(f"(old_string + new_string) share of total output tokens: {render._pct_of(edit_payload // cpt, output_tokens)}")

    print("\nold_string size distribution:\n")
    bucket_labels = [label for _upper, label in _EDIT_OLD_STRING_SIZE_BUCKETS] + [_EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL]
    for label in bucket_labels:
        count = stats["old_string_size_hist"].get(label, 0)
        print(f"  {label:10} {count:6,}  ({render._pct_of(count, edit_n)})")

    if per_account is not None:
        print("\n## Per-account breakdown\n")
        for ordinal in sorted(per_account):
            account_stats = per_account[ordinal]
            account_label = f"account-{ordinal}"
            a_calls = account_stats["calls"]
            a_edit_n = a_calls.get("Edit", 0)
            if a_edit_n == 0 and a_calls.get("Write", 0) == 0 and a_calls.get("MultiEdit", 0) == 0:
                print(f"  {account_label:10} no edit-family calls")
                continue
            unread = account_stats["known_failures"].get(("Edit", "unread"), 0)
            not_found = account_stats["known_failures"].get(("Edit", "not_found"), 0)
            multi = account_stats["known_failures"].get(("Edit", "multi_match"), 0)
            addressable = not_found + multi
            print(
                f"  {account_label:10} calls={a_edit_n:6,}  unread={unread:4,}  "
                f"not_found={not_found:4,}  multi={multi:3,}  addressable={render._pct_of(addressable, a_edit_n)}"
            )


def _add_edit_format_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the edit-format subparser on build_parser()'s subparsers action,
    binding cmd_edit_format through set_defaults(func=...).
    """
    p_edit_format = subparsers.add_parser(
        "edit-format",
        help=(
            "Edit/Write/MultiEdit call census: per-tool failure classification, governance-hook"
            " re-bucketing, not_found cause attribution, and old_string/new_string/Write token"
            " overhead. Aggregate-only output; redacted by default."
        ),
    )
    scope._add_project_scope_args(p_edit_format)
    p_edit_format.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_edit_format.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_edit_format.set_defaults(func=cmd_edit_format)
