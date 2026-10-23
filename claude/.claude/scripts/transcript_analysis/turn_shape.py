"""The turn-shape commands: cmd_turn_shape, the per-turn tool-call-count and single-call
streak-length distributions weighted by priced dollars; cmd_turn_shape_samples and
cmd_turn_shape_holdout_samples, random samples of flagged and unflagged streaks; the
mutating-git classifier the delegation-rule streak excludes; and the three subparser
registrations.

Imports corpus, denials, pricing, render, and scope by module (attribute access, not by
name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys

from transcript_analysis import corpus, denials, pricing, render, scope

# Mutating git subcommands, excluded wholesale from the delegation streak with
# no read-only carve-out (e.g. "git tag -l" still excluded) -- matches
# deny-reviewer-tree-mutation.sh's posture. See
# .claude/plans/tool-call-compliance-enforcement.md for the enumeration's rationale.
_TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    "commit", "push", "merge", "rebase", "cherry-pick", "reset", "revert",
    "stash", "tag", "checkout", "switch", "restore", "add", "rm", "mv",
    "branch", "clean", "remote", "fetch", "reflog", "symbolic-ref", "fsck",
    "worktree",
})


# A single env-var-assignment token, e.g. "FOO=bar" -- the per-token form of
# _DENIAL_COMMAND_ENV_PREFIX_RE's leading-assignment character classes, applied
# per shell-operator segment (not just once at the start of the whole
# command) so "cd dir && FOO=bar git commit" strips the second segment's own
# env prefix too.
_TURN_SHAPE_ENV_ASSIGNMENT_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")


def _command_segment_is_mutating_git(tokens: list[str]) -> bool:
    """Return True iff one already-tokenized shell segment invokes a mutating git subcommand.

    Strips this segment's own leading env-var-assignment tokens and a leading
    "sudo", reuses _denial_command_shape's git repo-selection flag-value drop,
    then checks the resulting subcommand token against
    _TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS.
    """
    while tokens and _TURN_SHAPE_ENV_ASSIGNMENT_TOKEN_RE.match(tokens[0]):
        tokens = tokens[1:]
    if tokens and os.path.basename(tokens[0]) == "sudo":
        tokens = tokens[1:]
    if not tokens:
        return False
    tokens[0] = os.path.basename(tokens[0])
    tokens = denials._drop_denial_command_flag_values(tokens)
    if len(tokens) < 2 or tokens[0] != "git":
        return False
    return tokens[1] in _TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS


def _bash_command_is_mutating_git(command: str) -> bool:
    """Return True iff any &&/;/|/||-chained segment of `command` invokes a
    mutating git subcommand (see _command_segment_is_mutating_git).
    """
    return any(
        _command_segment_is_mutating_git(segment)
        for segment in corpus.split_command_segments(command)
    )


_TURN_SHAPE_CALL_COUNT_BUCKETS: tuple[str, ...] = ("0", "1", "2-3", "4-7", "8+")
_TURN_SHAPE_STREAK_BUCKETS: tuple[str, ...] = ("1", "2", "3-5", "6-10", "11+")


def _turn_shape_call_count_bucket(call_count: int) -> str:
    """Map a turn's tool-call count to its _TURN_SHAPE_CALL_COUNT_BUCKETS label."""
    if call_count == 0:
        return "0"
    if call_count == 1:
        return "1"
    if call_count <= 3:
        return "2-3"
    if call_count <= 7:
        return "4-7"
    return "8+"


def _turn_shape_streak_bucket(streak_len: int) -> str:
    """Map a streak length to its _TURN_SHAPE_STREAK_BUCKETS label."""
    if streak_len == 1:
        return "1"
    if streak_len == 2:
        return "2"
    if streak_len <= 5:
        return "3-5"
    if streak_len <= 10:
        return "6-10"
    return "11+"


def _turn_shape_session_turns(records: list[dict], since_ts: float | None, session_id: str) -> list[dict]:
    """Build one dict per qualifying assistant turn in `records`, post-dedup.

    Population is every assistant turn with usage, across every model —
    deliberately independent of cmd_audit_routing_shape's own Opus-only,
    judgment-span-scoped population, which measures a different rule
    entirely. isSidechain turns are excluded per-record after dedup, not via
    iter_sessions' own include_subagents flag, so a sidechain record written
    inline into the main transcript file is caught the same as one from a
    split subagent file. Each entry carries enough to both aggregate
    (call_count, dollars, unpriced_tokens) and, for a single-call turn,
    render a sample (tool_name, command).
    """
    records = pricing.dedup_turns_by_request_id(records)
    turns: list[dict] = []
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        usage = msg.get("usage")
        if not usage:
            continue
        if since_ts is not None:
            rec_ts = corpus._parse_ts(rec.get("timestamp"))
            if rec_ts is None or rec_ts < since_ts:
                continue

        content = msg.get("content") or []
        tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        call_count = len(tool_use_blocks)
        tool_name = tool_use_blocks[0].get("name", "") if call_count == 1 else ""
        command = (tool_use_blocks[0].get("input") or {}).get("command", "") if tool_name == "Bash" else ""

        dollars_by_class, _, turn_unpriced_tokens = pricing._price_turn(msg.get("model", ""), usage)
        dollars = sum(dollars_by_class.values()) if dollars_by_class is not None else 0.0

        turns.append({
            "session_id": session_id,
            "branch": rec.get("gitBranch") or "",
            "call_count": call_count,
            "dollars": dollars,
            "tool_name": tool_name,
            "command": command,
            "is_single_bash": tool_name == "Bash",
            "is_mutating_git": tool_name == "Bash" and _bash_command_is_mutating_git(command),
            "unpriced_tokens": turn_unpriced_tokens if dollars_by_class is None else 0,
        })
    return turns


def _turn_shape_streaks(session_turns: list[dict], *, require_bash: bool) -> list[list[dict]]:
    """Return each maximal streak of qualifying turns in session_turns, in order.

    require_bash=False qualifies any single-call turn (the batching-rule
    population); require_bash=True additionally requires that call be Bash and
    excludes _TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS (the delegation-rule
    population) — this exclusion applies only here, not to the
    require_bash=False streak. A gitBranch change or a non-qualifying turn
    ends the current streak; an interleaved user-type record never reaches
    this list at all (_turn_shape_session_turns keeps assistant turns only),
    so it cannot break a streak either.
    """
    streaks: list[list[dict]] = []
    current: list[dict] = []
    prev_branch: str | None = None
    for turn in session_turns:
        if prev_branch is not None and turn["branch"] != prev_branch and current:
            streaks.append(current)
            current = []

        qualifies = turn["call_count"] == 1 and (
            not require_bash or (turn["is_single_bash"] and not turn["is_mutating_git"])
        )
        if qualifies:
            current.append(turn)
        else:
            if current:
                streaks.append(current)
            current = []
        prev_branch = turn["branch"]
    if current:
        streaks.append(current)
    return streaks


def cmd_turn_shape(args: argparse.Namespace) -> None:
    """Per-turn tool-call-count distribution, plus streak-length distributions
    for consecutive single-call turns (the batching-rule signal) and
    consecutive Bash-only single-call turns excluding mutating-git commands
    (the delegation-rule signal), each weighted by the turn's own priced
    dollar cost.
    """
    since_ts, since_raw = scope._parse_since_nd_arg(args, "turn-shape")
    since_label = since_raw or ""

    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "turn-shape", roots=roots)

    call_count_turns: dict[str, int] = {b: 0 for b in _TURN_SHAPE_CALL_COUNT_BUCKETS}
    call_count_dollars: dict[str, float] = {b: 0.0 for b in _TURN_SHAPE_CALL_COUNT_BUCKETS}
    batching_streaks: dict[str, int] = {b: 0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    batching_dollars: dict[str, float] = {b: 0.0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    delegation_streaks: dict[str, int] = {b: 0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    delegation_dollars: dict[str, float] = {b: 0.0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    unpriced_turns = 0
    unpriced_tokens = 0

    scope.print_resolved_scope("turn-shape", scope_label, roots)

    for jsonl, records in session_iter:
        session_turns = _turn_shape_session_turns(records, since_ts, jsonl.stem)

        for turn in session_turns:
            bucket = _turn_shape_call_count_bucket(turn["call_count"])
            call_count_turns[bucket] += 1
            call_count_dollars[bucket] += turn["dollars"]
            if turn["unpriced_tokens"]:
                unpriced_turns += 1
                unpriced_tokens += turn["unpriced_tokens"]

        for streak in _turn_shape_streaks(session_turns, require_bash=False):
            bucket = _turn_shape_streak_bucket(len(streak))
            batching_streaks[bucket] += 1
            batching_dollars[bucket] += sum(t["dollars"] for t in streak)

        for streak in _turn_shape_streaks(session_turns, require_bash=True):
            bucket = _turn_shape_streak_bucket(len(streak))
            delegation_streaks[bucket] += 1
            delegation_dollars[bucket] += sum(t["dollars"] for t in streak)

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Turn shape ({title_since})\n")

    print("### Tool calls per turn\n")
    header = f"{'Bucket':<8} {'Turns':>8} {'$':>12}"
    print(header)
    print("─" * len(header))
    for bkt in _TURN_SHAPE_CALL_COUNT_BUCKETS:
        print(f"{bkt:<8} {call_count_turns[bkt]:>8,} {render._fmt_usd(call_count_dollars[bkt]):>12}")

    print("\n### Single-call streak length (batching rule)\n")
    header = f"{'Bucket':<8} {'Streaks':>8} {'$':>12}"
    print(header)
    print("─" * len(header))
    for bkt in _TURN_SHAPE_STREAK_BUCKETS:
        print(f"{bkt:<8} {batching_streaks[bkt]:>8,} {render._fmt_usd(batching_dollars[bkt]):>12}")

    print("\n### Bash-only single-call streak length, excluding mutating git (delegation rule)\n")
    header = f"{'Bucket':<8} {'Streaks':>8} {'$':>12}"
    print(header)
    print("─" * len(header))
    for bkt in _TURN_SHAPE_STREAK_BUCKETS:
        print(f"{bkt:<8} {delegation_streaks[bkt]:>8,} {render._fmt_usd(delegation_dollars[bkt]):>12}")

    if unpriced_turns:
        print(f"\n  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")


# A length-1 "streak" has no adjacency to flag. This governs sampling for
# manual calibration only; a downstream advisory mechanism reading this
# subcommand's aggregate output sets its own threshold from the resulting
# precision/recall figures, not from this constant.
_TURN_SHAPE_SAMPLES_MIN_STREAK_LEN = 2

# The unflagged (holdout) population is exactly the streaks turn-shape-samples
# excludes — length == 1, not >= 2.
_TURN_SHAPE_HOLDOUT_STREAK_LEN = 1


def cmd_turn_shape_samples(args: argparse.Namespace) -> None:
    """Emit a random sample of flagged turn-shape streaks (length >=
    _TURN_SHAPE_SAMPLES_MIN_STREAK_LEN) as plain text, for manual calibration
    of the batching and delegation rules against cmd_turn_shape's aggregate.

    Plain text, not JSON, unlike audit-routing-samples: this output is
    stamped with _DO_NOT_PUBLISH_BANNER, and prepending a banner line would
    corrupt a JSON stream. (audit-routing-samples never stamps this banner at
    all — a pre-existing gap in that subcommand, not addressed here.)
    """
    since_ts, _since_raw = scope._parse_since_nd_arg(args, "turn-shape-samples")
    sample_n: int = getattr(args, "sample", 30) or 30
    seed: int | None = getattr(args, "seed", None)
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "turn-shape-samples", roots=roots)
    # stderr, not stdout: stdout is this subcommand's plain-text data stream.
    scope.print_resolved_scope("turn-shape-samples", scope_label, roots, file=sys.stderr)

    candidates: list[dict] = []
    for jsonl, records in session_iter:
        session_turns = _turn_shape_session_turns(records, since_ts, jsonl.stem)
        for rule, require_bash in (("batching", False), ("delegation", True)):
            for streak in _turn_shape_streaks(session_turns, require_bash=require_bash):
                if len(streak) >= _TURN_SHAPE_SAMPLES_MIN_STREAK_LEN:
                    candidates.append({"rule": rule, "streak": streak})

    rng = random.Random(seed)
    rng.shuffle(candidates)
    candidates = candidates[:sample_n]

    print(scope._DO_NOT_PUBLISH_BANNER)
    print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)
    for candidate in candidates:
        streak = candidate["streak"]
        dollars = sum(t["dollars"] for t in streak)
        print(
            f"\n--- {candidate['rule']} streak, length={len(streak)}, "
            f"{render._fmt_usd(dollars)}, session={streak[0]['session_id']} ---"
        )
        for i, turn in enumerate(streak, 1):
            detail = f": {turn['command']}" if turn["command"] else ""
            print(f"  {i}. {turn['tool_name']}{detail}")


def cmd_turn_shape_holdout_samples(args: argparse.Namespace) -> None:
    """Emit a random sample of unflagged turn-shape streaks (length ==
    _TURN_SHAPE_HOLDOUT_STREAK_LEN) as plain text, for manual recall
    calibration of the batching and delegation rules — the complement of
    cmd_turn_shape_samples's flagged population.

    --seed defaults to a fixed constant (not None, unlike turn-shape-samples):
    --offset pages this same shuffled population across repeated invocations,
    which is only coherent if every invocation shuffles it identically.
    """
    since_ts, _since_raw = scope._parse_since_nd_arg(args, "turn-shape-holdout-samples")
    sample_n: int = getattr(args, "sample", 30)
    if sample_n is None:
        sample_n = 30
    seed: int = getattr(args, "seed", 0)
    offset: int = getattr(args, "offset", 0)
    if offset < 0:
        print(
            "turn-shape-holdout-samples: --offset must not be negative",
            file=sys.stderr,
        )
        sys.exit(2)
    if sample_n < 0:
        print(
            "turn-shape-holdout-samples: --sample must not be negative",
            file=sys.stderr,
        )
        sys.exit(2)

    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "turn-shape-holdout-samples", roots=roots)
    # stderr, not stdout: stdout is this subcommand's plain-text data stream.
    scope.print_resolved_scope("turn-shape-holdout-samples", scope_label, roots, file=sys.stderr)

    candidates: list[dict] = []
    for jsonl, records in session_iter:
        session_turns = _turn_shape_session_turns(records, since_ts, jsonl.stem)
        for rule, require_bash in (("batching", False), ("delegation", True)):
            for streak in _turn_shape_streaks(session_turns, require_bash=require_bash):
                if len(streak) == _TURN_SHAPE_HOLDOUT_STREAK_LEN:
                    candidates.append({"rule": rule, "streak": streak})

    rng = random.Random(seed)
    rng.shuffle(candidates)
    total_candidates = len(candidates)
    window = candidates[offset:offset + sample_n]
    # Keyed on offset vs. total_candidates, not on window emptiness: a
    # window can also be empty because --sample=0, which is not "past the end".
    if total_candidates and offset >= total_candidates:
        print(
            f"turn-shape-holdout-samples: --offset={offset} is past the end of the"
            f" {total_candidates} unflagged candidates in scope",
            file=sys.stderr,
        )
    print(
        f"(offset={offset}, window={len(window)} of {total_candidates} unflagged candidates)",
        file=sys.stderr,
    )

    print(scope._DO_NOT_PUBLISH_BANNER)
    print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)
    for candidate in window:
        streak = candidate["streak"]
        dollars = sum(t["dollars"] for t in streak)
        print(
            f"\n--- {candidate['rule']} streak, length={len(streak)}, "
            f"{render._fmt_usd(dollars)}, session={streak[0]['session_id']} ---"
        )
        for i, turn in enumerate(streak, 1):
            detail = f": {turn['command']}" if turn["command"] else ""
            print(f"  {i}. {turn['tool_name']}{detail}")


def _add_turn_shape_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the turn-shape subparser on build_parser()'s subparsers action,
    binding cmd_turn_shape through set_defaults(func=...).
    """
    p_turn_shape = subparsers.add_parser(
        "turn-shape",
        help=(
            "Per-turn tool-call-count distribution, plus streak-length distributions for"
            " consecutive single-call turns (batching rule) and consecutive Bash-only"
            " single-call turns excluding mutating git (delegation rule), dollar-weighted."
        ),
    )
    scope._add_project_scope_args(p_turn_shape)
    p_turn_shape.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_turn_shape.set_defaults(func=cmd_turn_shape)


def _add_turn_shape_samples_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the turn-shape-samples subparser on build_parser()'s subparsers action,
    binding cmd_turn_shape_samples through set_defaults(func=...).
    """
    p_turn_shape_samples = subparsers.add_parser(
        "turn-shape-samples",
        help=(
            "Emit a random sample of flagged turn-shape streaks (length >= 2) as plain text,"
            " for manual calibration of the batching and delegation rules."
        ),
    )
    scope._add_project_scope_args(p_turn_shape_samples)
    p_turn_shape_samples.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_turn_shape_samples.add_argument(
        "--sample", type=int, default=30, metavar="N",
        help="Maximum number of sample streaks to emit (default: 30).",
    )
    p_turn_shape_samples.add_argument(
        "--seed", type=int, default=None, metavar="N",
        help="Random seed for reproducible sampling.",
    )
    p_turn_shape_samples.set_defaults(func=cmd_turn_shape_samples)


def _add_turn_shape_holdout_samples_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the turn-shape-holdout-samples subparser on build_parser()'s subparsers action,
    binding cmd_turn_shape_holdout_samples through set_defaults(func=...).
    """
    p_turn_shape_holdout_samples = subparsers.add_parser(
        "turn-shape-holdout-samples",
        help=(
            "Emit a random sample of unflagged turn-shape streaks (length == 1) as plain"
            " text, for manual recall calibration of the batching and delegation rules."
        ),
    )
    scope._add_project_scope_args(p_turn_shape_holdout_samples)
    p_turn_shape_holdout_samples.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_turn_shape_holdout_samples.add_argument(
        "--sample", type=int, default=30, metavar="N",
        help="Maximum number of sample streaks to emit (default: 30).",
    )
    p_turn_shape_holdout_samples.add_argument(
        "--seed", type=int, default=0, metavar="N",
        help=(
            "Random seed for reproducible sampling (default: 0, not OS entropy — "
            "so --offset pages the same shuffle across repeated invocations)."
        ),
    )
    p_turn_shape_holdout_samples.add_argument(
        "--offset", type=int, default=0, metavar="N",
        help="Skip the first N candidates of the shuffled population (for paging).",
    )
    p_turn_shape_holdout_samples.set_defaults(func=cmd_turn_shape_holdout_samples)
