"""The sessions command: cmd_sessions, the resolved scope's transcript paths one per line,
plus split subagent files under --include-subagents, and its subparser registration.

Imports corpus and scope by module (attribute access, not by name) -- see
scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import sys

from transcript_analysis import corpus, scope


def cmd_sessions(args: argparse.Namespace) -> None:
    """Emit transcript file paths for the resolved scope, one absolute path per line.

    --paths is required: it names the one action this subcommand supports today,
    leaving room for a second sessions action later without a bare `sessions`
    invocation silently doing nothing. Sourced from scope.resolve_scan_roots plus
    scope._resolve_project_scope (not a flat glob), so a main session file only
    reaches this repo's own worktrees under --this-repo the same way every other
    subcommand does. --include-subagents additionally emits each split subagent
    file's own path, found the same way read_session_file locates
    them for its own record merge -- <session>/subagents/*.jsonl under the main
    file's own directory -- rather than a flat glob across the whole scope, so a
    caller that reads only the emitted paths gets exactly the same file set
    read_session_file(include_subagents=True) would have merged, split back out
    into individually readable files. The resolved-scope header goes to stderr,
    matching audit-routing-samples' convention — stdout here is meant to be
    piped to xargs/Read, not mixed with a header line.
    """
    if not bool(getattr(args, "paths", False)):
        print("sessions: --paths is required (no other sessions action exists yet)", file=sys.stderr)
        sys.exit(2)

    include_subagents = bool(getattr(args, "include_subagents", False))
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(
        args, "sessions", include_subagents=include_subagents, roots=roots
    )
    scope.print_resolved_scope("sessions", scope_label, roots, file=sys.stderr)
    for jsonl, _records in session_iter:
        print(jsonl)
        if include_subagents:
            subagent_dir = jsonl.parent / jsonl.stem / corpus.SUBAGENT_SUBDIR
            if subagent_dir.is_dir():
                for sub_jsonl in sorted(subagent_dir.glob("*.jsonl")):
                    print(sub_jsonl)


def _add_sessions_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the sessions subparser on build_parser()'s subparsers action,
    binding cmd_sessions through set_defaults(func=...).
    """
    p_sessions = subparsers.add_parser(
        "sessions",
        help="Emit transcript file paths for the resolved scope, one absolute path per line.",
    )
    scope._add_project_scope_args(p_sessions)
    p_sessions.add_argument(
        "--paths", action="store_true",
        help="Print one absolute transcript path per line (the only sessions action today; required).",
    )
    p_sessions.add_argument(
        "--include-subagents", action="store_true",
        help="Also emit split subagent transcript paths under <session>/subagents/*.jsonl.",
    )
    p_sessions.set_defaults(func=cmd_sessions)
