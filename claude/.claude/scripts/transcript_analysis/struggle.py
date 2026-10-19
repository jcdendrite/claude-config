"""The struggle command: cmd_struggle, each branch's count of user turns matching
STRUGGLE_PHRASES by the preceding model family, the STRUGGLE_PHRASES lexicon
user_input.py and friction_count.py also read, and its subparser registration.

Imports render and scope by module (attribute access, not by name) -- see
scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from transcript_analysis import render, scope

STRUGGLE_PHRASES: list[str] = [
    # attested in transcripts
    "hold on",
    "why did you",
    "try again",
    "no not that",
    # predicted patterns
    "no, that",
    "that's wrong",
    "not right",
    "you're wrong",
    "stop doing",
    "don't do that",
    "still broken",
    "still failing",
    "you missed",
    "incorrect",
    "not what i asked",
    "not what i wanted",
    "wrong approach",
    "that doesn't work",
    "please don't",
    # attested in transcripts but missed by prior lexicon (see test_transcript_struggle.py)
    # Excluded: bare "stale" — legitimate technical term with high false-positive risk
    "hallucinat",  # matches "hallucinated", "hallucinating", etc.
    "are you saying",
    "you should be able to",
    "that doesn't exist",
    "that doesn't match",
]


def cmd_struggle(args: argparse.Namespace) -> None:
    branch_filter = scope._branch_filter(args)
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "struggle", roots=roots)
    scope.print_resolved_scope("struggle", scope_label, roots)

    branch_data: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for _jsonl, records in session_iter:
        last_fam: dict[str, str] = {}
        branch_context = scope._session_branch_context(records)
        for rec in records:
            branch = scope._attributed_branch(rec, branch_context) or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            if bool(rec.get("isSidechain")):
                continue
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype == "assistant":
                last_fam[branch] = render._fam(msg.get("model", ""))
            elif rtype in ("user", "human"):
                text = render._strip_task_notifications(render._content_text(msg.get("content", ""))).lower()
                if any(phrase in text for phrase in STRUGGLE_PHRASES):
                    branch_data[branch][last_fam.get(branch, "unknown")] += 1

    if not branch_data:
        print("No struggle signals found.")
        return

    print(f"{'Branch':<40} {'Opus':>6} {'Sonnet':>7} {'Haiku':>6} {'Other':>6} {'Unknown':>8}")
    print("-" * 82)
    for branch in sorted(branch_data):
        d = branch_data[branch]
        print(
            f"{branch:<40} {d.get('opus', 0):>6} {d.get('sonnet', 0):>7} "
            f"{d.get('haiku', 0):>6} {d.get('other', 0):>6} {d.get('unknown', 0):>8}"
        )


def _add_struggle_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the struggle subparser on build_parser()'s subparsers action,
    binding cmd_struggle through set_defaults(func=...).
    """
    p_struggle = subparsers.add_parser("struggle", help="Correction/frustration signal phrases in user turns, split by model.")
    p_struggle.add_argument("--branches", metavar="B1,B2,...")
    scope._add_project_scope_args(p_struggle)
    p_struggle.set_defaults(func=cmd_struggle)
