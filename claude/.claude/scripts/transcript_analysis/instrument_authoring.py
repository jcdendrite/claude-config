"""The instrument-authoring command: cmd_instrument_authoring, the aggregate-only census of Bash
heredoc and inline-program calls and Write-to-scratchpad calls, size-bucketed by main-thread and
subagent scope and split into zero-dispatch and dispatched session cohorts, and its subparser
registration.

Imports pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own
top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

from transcript_analysis import pricing, render, scope

# See .claude/plans/delegate-instrument-authoring.md's "Detection design" for
# the full spec this section implements.

# A heredoc opener (<<EOF, <<'EOF', <<-EOF, <<-'EOF'), matching the real
# delimiter word -- double-quoted delimiters (<<"EOF") count too since POSIX
# treats a quoted delimiter the same as single-quoted for body-scanning
# purposes -- with a negative lookbehind so a match never consumes the last
# two `<` of a `<<<` here-string operator as if they were its own `<<`.
_INSTRUMENT_AUTHORING_HEREDOC_OPEN_RE = re.compile(r"(?<!<)<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Inline-program interpreters this classifier recognizes, each mapped to the
# flag letter that takes the inline program as its argument. sh/bash -c and
# python/python3 -c are included deliberately -- the most common inline-
# script shapes, and omitting them would be a systematic false negative.
_INSTRUMENT_AUTHORING_INLINE_INTERPRETER_FLAGS: dict[str, str] = {
    "python3": "c", "python": "c", "sh": "c", "bash": "c",
    "node": "e", "perl": "e", "ruby": "e",
}

# -c is overloaded (curl -c, tar -cf, ssh -c, mysql -c) so a bare "-c" never
# matches -- only <interpreter> -c/-e bound to a recognized argv[0] does, and
# the trailing lookahead refuses a flag glued to further letters (-cf) so a
# non-interpreter flag combination never mimics an inline-program invocation.
# python3/python also match a dotted version suffix (python3.11) -- stripped
# before the flag-table lookup in _extract_inline_program_payloads.
_INSTRUMENT_AUTHORING_INLINE_INTERPRETER_RE = re.compile(
    r"\b(python3(?:\.\d+)?|python(?:\.\d+)?|node|perl|ruby|sh|bash)\b\s+-([a-zA-Z])(?=\s|$)"
)

_INSTRUMENT_AUTHORING_SCOPE_MAIN = "main"
_INSTRUMENT_AUTHORING_SCOPE_SUBAGENT = "subagent"

_INSTRUMENT_AUTHORING_SHAPE_BASH = "bash"
_INSTRUMENT_AUTHORING_SHAPE_WRITE = "write"

_INSTRUMENT_AUTHORING_COHORT_ZERO_DISPATCH = "zero_dispatch"
_INSTRUMENT_AUTHORING_COHORT_DISPATCHED = "dispatched"

# Authored-payload size buckets, in characters -- a call's heredoc body /
# inline-program argument / Write content length, each (exclusive upper
# bound, label) tried in order; a length at or past every bound falls to
# _INSTRUMENT_AUTHORING_SIZE_OVERFLOW_LABEL.
_INSTRUMENT_AUTHORING_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (100, "0-99"),
    (500, "100-499"),
    (2000, "500-1999"),
    (10000, "2000-9999"),
)
_INSTRUMENT_AUTHORING_SIZE_OVERFLOW_LABEL = "10000+"


def _instrument_authoring_size_bucket(chars: int) -> str:
    for upper_bound, label in _INSTRUMENT_AUTHORING_SIZE_BUCKETS:
        if chars < upper_bound:
            return label
    return _INSTRUMENT_AUTHORING_SIZE_OVERFLOW_LABEL


def _extract_heredoc_payloads(command: str) -> tuple[list[str], list[tuple[int, int]]]:
    """Extract each heredoc body in a Bash command string in the order the
    heredocs open (handling multiple openers on one logical line, e.g.
    `cmd1 <<A && cmd2 <<B`, by consuming bodies in declaration order), plus
    each opener's own [start, end) character span so a caller scanning the
    same string for another shape (e.g. inline -c/-e invocations) can skip
    heredoc-body text rather than mistake it for a second, independent
    invocation."""
    lines = command.split("\n")
    n = len(lines)
    line_starts = [0] * n
    offset = 0
    for i, line in enumerate(lines):
        line_starts[i] = offset
        offset += len(line) + 1  # +1 for the "\n" this split() consumed between lines

    def _line_start(i: int) -> int:
        return line_starts[i] if i < n else len(command)

    payloads: list[str] = []
    spans: list[tuple[int, int]] = []
    line_idx = 0
    while line_idx < n:
        openers = [
            (m.group(0).startswith("<<-"), m.group(2))
            for m in _INSTRUMENT_AUTHORING_HEREDOC_OPEN_RE.finditer(lines[line_idx])
        ]
        if not openers:
            line_idx += 1
            continue
        cursor = line_idx + 1
        for dash, delimiter in openers:
            body_lines: list[str] = []
            while cursor < n:
                candidate = lines[cursor].lstrip("\t") if dash else lines[cursor]
                cursor += 1
                if candidate == delimiter:
                    break
                body_lines.append(candidate)
            payloads.append("\n".join(body_lines))
        spans.append((_line_start(line_idx + 1), _line_start(cursor)))
        line_idx = cursor
    return payloads, spans


def _extract_shell_arg_at(command: str, start_idx: int) -> str:
    """Extract the shell argument (quoted or bare) starting at start_idx,
    returning its content with surrounding quotes stripped. Approximate --
    a double-quoted argument's own backslash escapes are skipped over, not
    unescaped, since this classifier only needs the argument's raw length,
    not its evaluated value."""
    idx = start_idx
    n = len(command)
    while idx < n and command[idx] in " \t":
        idx += 1
    if idx >= n:
        return ""
    quote = command[idx]
    if quote in ("'", '"'):
        idx += 1
        start = idx
        while idx < n:
            if quote == '"' and command[idx] == "\\" and idx + 1 < n:
                idx += 2
                continue
            if command[idx] == quote:
                break
            idx += 1
        return command[start:idx]
    start = idx
    while idx < n and command[idx] not in " \t\n;&|":
        idx += 1
    return command[start:idx]


def _extract_inline_program_payloads(command: str, excluded_spans: Sequence[tuple[int, int]] = ()) -> list[str]:
    """Extract each <interpreter> -c/-e program argument in a Bash command
    string, in the order the invocations appear.

    A match starting inside one of `excluded_spans` is skipped -- data
    written inside a heredoc body that happens to be shaped like an inline-
    program invocation (e.g. example code) is not a second, independent
    invocation, and counting it would double the payload the heredoc body
    already accounts for.
    """
    payloads: list[str] = []
    for m in _INSTRUMENT_AUTHORING_INLINE_INTERPRETER_RE.finditer(command):
        if any(start <= m.start() < end for start, end in excluded_spans):
            continue
        interpreter, flag = m.group(1).split(".", 1)[0], m.group(2)
        if _INSTRUMENT_AUTHORING_INLINE_INTERPRETER_FLAGS.get(interpreter) != flag:
            continue
        payloads.append(_extract_shell_arg_at(command, m.end()))
    return payloads


def _bash_authoring_payload_chars(command: str) -> int:
    """One Bash tool_use's authored-payload size: every heredoc body plus
    every inline-program argument the command carries outside those heredoc
    bodies, summed -- a command chaining several of either (&&, ;, |) is one
    authoring act split across invocations. Zero means the command is not
    instrument-authoring shaped."""
    heredoc_payloads, heredoc_spans = _extract_heredoc_payloads(command)
    total = sum(len(body) for body in heredoc_payloads)
    total += sum(len(arg) for arg in _extract_inline_program_payloads(command, heredoc_spans))
    return total


def _is_scratchpad_write_path(file_path: str) -> bool:
    """True when a Write's file_path targets a scratchpad/temp location: the
    path's first component is a temp root (/tmp or /private/tmp -- macOS
    resolves /tmp through a symlink to /private/tmp, and transcripts carry
    the resolved form, so both must match), or the path contains a
    "scratchpad" component. Session-UUID path segments are never matched on."""
    if not file_path:
        return False
    normalized = file_path.rstrip("/")
    if normalized == "/tmp" or normalized.startswith("/tmp/"):
        return True
    if normalized == "/private/tmp" or normalized.startswith("/private/tmp/"):
        return True
    return "scratchpad" in PurePosixPath(file_path).parts


def _new_instrument_authoring_stats() -> dict:
    return {
        "call_n": Counter(),  # (shape, scope) -> count of classified-authoring calls
        "payload_chars": Counter(),  # (shape, scope) -> summed payload chars
        "size_hist": Counter(),  # (scope, bucket label) -> count
        "size_hist_chars": Counter(),  # (scope, bucket label) -> summed chars
        "unparsed_n": Counter(),  # scope -> count of __unparsedToolInput-only Bash/Write blocks
        "spawn_dispatch_n": 0,  # this session's own main-thread Agent+Task tool_use count
        "main_payload_chars": 0,  # this session's own main-thread authored-payload total
    }


def _merge_instrument_authoring_stats(dst: dict, src: dict) -> None:
    dst["call_n"].update(src["call_n"])
    dst["payload_chars"].update(src["payload_chars"])
    dst["size_hist"].update(src["size_hist"])
    dst["size_hist_chars"].update(src["size_hist_chars"])
    dst["unparsed_n"].update(src["unparsed_n"])


def _new_instrument_authoring_cohort_totals() -> dict[str, dict[str, int]]:
    return {
        _INSTRUMENT_AUTHORING_COHORT_ZERO_DISPATCH: {"session_n": 0, "payload_chars": 0},
        _INSTRUMENT_AUTHORING_COHORT_DISPATCHED: {"session_n": 0, "payload_chars": 0},
    }


def _scan_instrument_authoring_session(records: list[dict]) -> dict:
    """One session's inline-instrument-authoring census, over the flattened
    main-thread + merged-subagent record order (per _resolve_project_scope's
    include_subagents=True): classifies each Bash heredoc/inline-program call
    and each Write-to-scratchpad call as authoring, buckets its payload size
    by main/subagent scope, and counts this session's own main-thread
    Agent/Task spawn-dispatch calls -- the count the cohort split reads --
    returning every figure as a pure aggregate (counts and char sums) with no
    command text, file content, file path, or session identifier retained
    past this function or printed by any caller.
    """
    stats = _new_instrument_authoring_stats()

    for rec in records:
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        is_subagent = bool(rec.get("isSidechain"))
        scope = _INSTRUMENT_AUTHORING_SCOPE_SUBAGENT if is_subagent else _INSTRUMENT_AUTHORING_SCOPE_MAIN

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            tool_input = block.get("input") or {}

            if name in pricing._SPAWN_TOOL_NAMES:
                if not is_subagent:
                    stats["spawn_dispatch_n"] += 1
                continue

            if name == "Bash":
                command = tool_input.get("command")
                if not command:
                    stats["unparsed_n"][scope] += 1
                    continue
                payload_chars = _bash_authoring_payload_chars(command)
                if payload_chars <= 0:
                    continue
                shape = _INSTRUMENT_AUTHORING_SHAPE_BASH
            elif name == "Write":
                file_path = tool_input.get("file_path")
                if not file_path:
                    stats["unparsed_n"][scope] += 1
                    continue
                if not _is_scratchpad_write_path(file_path):
                    continue
                payload_chars = len(tool_input.get("content") or "")
                shape = _INSTRUMENT_AUTHORING_SHAPE_WRITE
            else:
                continue

            stats["call_n"][(shape, scope)] += 1
            stats["payload_chars"][(shape, scope)] += payload_chars
            bucket = _instrument_authoring_size_bucket(payload_chars)
            stats["size_hist"][(scope, bucket)] += 1
            stats["size_hist_chars"][(scope, bucket)] += payload_chars
            if not is_subagent:
                stats["main_payload_chars"] += payload_chars

    return stats


def _aggregate_instrument_authoring_sessions(session_stats_iter: Iterable[dict]) -> tuple[dict, dict]:
    """Reduce a stream of per-session instrument-authoring stats into merged
    call/payload stats and zero_dispatch/dispatched cohort totals."""
    stats = _new_instrument_authoring_stats()
    cohort_totals = _new_instrument_authoring_cohort_totals()
    for session_stats in session_stats_iter:
        _merge_instrument_authoring_stats(stats, session_stats)
        cohort = (
            _INSTRUMENT_AUTHORING_COHORT_DISPATCHED
            if session_stats["spawn_dispatch_n"] > 0
            else _INSTRUMENT_AUTHORING_COHORT_ZERO_DISPATCH
        )
        cohort_totals[cohort]["session_n"] += 1
        cohort_totals[cohort]["payload_chars"] += session_stats["main_payload_chars"]
    return stats, cohort_totals


def cmd_instrument_authoring(args: argparse.Namespace) -> None:
    """CLI entry point for the instrument-authoring subcommand.

    Root resolution happens here, mirroring cmd_edit_format/cmd_read_scope,
    so --config-dir validation exits before any scan work.
    """
    roots = scope._resolve_cost_roots(args, subcommand="instrument-authoring")
    _instrument_authoring_report(args, roots)


def _instrument_authoring_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Census of inline instrument-authoring: main-thread and subagent Bash
    heredoc/inline-program calls and Write-to-scratchpad calls, size-bucketed
    by scope, correlated against each session's own main-thread Agent/Task
    spawn-dispatch count, split into zero_dispatch/dispatched cohorts.

    roots is None for every direct caller other than cmd_instrument_authoring
    (this module's own tests included) -- mirrors edit-format's and
    read-scope's own contract.

    This report's content is aggregate-only (size buckets, counts, cohort
    totals -- never raw command text, file content, file paths, or session
    identifiers), so unlike cost/context-distribution it needs no
    session-redact map and no --no-redact / DO NOT PUBLISH gate.
    """
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)

    session_iter, scope_label = scope._resolve_project_scope(
        args, "instrument-authoring", include_subagents=True, roots=roots
    )
    scope.print_resolved_scope("instrument-authoring", scope_label, scan_roots)

    stats, cohort_totals = _aggregate_instrument_authoring_sessions(
        _scan_instrument_authoring_session(records) for _jsonl, records in session_iter
    )

    _print_instrument_authoring_report(stats, cohort_totals)


def _print_instrument_authoring_report(stats: dict, cohort_totals: dict[str, dict[str, int]]) -> None:
    call_n = stats["call_n"]
    payload_chars = stats["payload_chars"]

    print("\n## Inline instrument-authoring census\n")
    for shape, shape_label in (
        (_INSTRUMENT_AUTHORING_SHAPE_BASH, "Bash (heredoc/-c/-e)"),
        (_INSTRUMENT_AUTHORING_SHAPE_WRITE, "Write (scratchpad)"),
    ):
        for call_scope in (_INSTRUMENT_AUTHORING_SCOPE_MAIN, _INSTRUMENT_AUTHORING_SCOPE_SUBAGENT):
            count = call_n.get((shape, call_scope), 0)
            chars = payload_chars.get((shape, call_scope), 0)
            print(f"  {shape_label:22} {call_scope:9} count={count:8,}  chars=~{chars:12,}")

    unparsed_total = sum(stats["unparsed_n"].values())
    print(
        "\nunparsed_input (Bash/Write tool_use whose input carried no command/file_path, e.g."
        f" only __unparsedToolInput -- shape unknowable): {unparsed_total:,}"
    )

    print("\n## Authored-payload size distribution (chars, by scope)\n")
    bucket_labels = [label for _upper, label in _INSTRUMENT_AUTHORING_SIZE_BUCKETS] + [
        _INSTRUMENT_AUTHORING_SIZE_OVERFLOW_LABEL
    ]
    for call_scope in (_INSTRUMENT_AUTHORING_SCOPE_MAIN, _INSTRUMENT_AUTHORING_SCOPE_SUBAGENT):
        for label in bucket_labels:
            count = stats["size_hist"].get((call_scope, label), 0)
            chars = stats["size_hist_chars"].get((call_scope, label), 0)
            print(f"  {call_scope:9} {label:10} count={count:6,}  chars=~{chars:10,}")

    print("\n## Spawn-dispatch cohorts (this session's own main-thread Agent/Task count)\n")
    zero = cohort_totals[_INSTRUMENT_AUTHORING_COHORT_ZERO_DISPATCH]
    dispatched = cohort_totals[_INSTRUMENT_AUTHORING_COHORT_DISPATCHED]
    total_sessions = zero["session_n"] + dispatched["session_n"]
    total_payload = zero["payload_chars"] + dispatched["payload_chars"]
    print(
        f"  zero_dispatch  sessions={zero['session_n']:6,} ({render._pct_of(zero['session_n'], total_sessions)} of sessions)"
        f"  main-thread authored chars=~{zero['payload_chars']:10,}"
        f" ({render._pct_of(zero['payload_chars'], total_payload)} of authored mass)"
    )
    print(
        f"  dispatched     sessions={dispatched['session_n']:6,}"
        f" ({render._pct_of(dispatched['session_n'], total_sessions)} of sessions)"
        f"  main-thread authored chars=~{dispatched['payload_chars']:10,}"
        f" ({render._pct_of(dispatched['payload_chars'], total_payload)} of authored mass)"
    )


def _add_instrument_authoring_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the instrument-authoring subparser on build_parser()'s subparsers action,
    binding cmd_instrument_authoring through set_defaults(func=...).
    """
    p_instrument_authoring = subparsers.add_parser(
        "instrument-authoring",
        help=(
            "Census of inline instrument-authoring: Bash heredoc/inline-program (-c/-e) calls and"
            " Write-to-scratchpad calls, size-bucketed by main-thread/subagent scope and correlated"
            " against each session's own main-thread Agent/Task spawn-dispatch count."
            " Aggregate-only output."
        ),
    )
    scope._add_project_scope_args(p_instrument_authoring)
    p_instrument_authoring.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root."
        ),
    )
    p_instrument_authoring.set_defaults(func=cmd_instrument_authoring)
