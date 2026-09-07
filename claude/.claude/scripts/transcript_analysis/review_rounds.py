"""The review-round-cost command family: cmd_review_round_cost, every helper
used only by this command, and REVIEW_SKILLS.

REVIEW_SKILLS is the round-opening skill set. The shim's cmd_judgment_pair
back-imports REVIEW_SKILLS for its own default.

This module detects per-branch review-round windows across both the `Skill`
tool_use and `/slash` invocation shapes. It attributes each round's dollars
recursively, via corpus._index_subagent_dispatches' toolUseId join.

Imports corpus, pricing, redaction, render, and scope by module (attribute
access, not by name) — see scope.py's own top-of-file comment for why.

This module never imports cost.py. A round's own branch is the opening
record's own gitBranch, carried forward when absent. A main-thread
round-opening record is never a worktree-agent-* sidechain record, so
cost._attributed_branch's worktree-agent-* resolution is never needed for a
round's own branch key (see docs/transcript-analysis-architecture.md).
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from transcript_analysis import corpus, pricing, redaction, render, scope

# The three review-loop skills a round opens on; the shim's cmd_judgment_pair
# back-imports this name for its own default (the one-directional exception
# documented in docs/transcript-analysis-architecture.md).
REVIEW_SKILLS: tuple[str, ...] = ("code-review", "plan-review", "ready-for-review")

_REVIEW_SKILL_SET: frozenset[str] = frozenset(REVIEW_SKILLS)


def _is_fresh_user_prompt(rec: dict) -> bool:
    """A genuine new user message, not a tool result or injected record.

    Mirrors transcript-analysis.py:199-223's own _is_fresh_user_prompt —
    re-expressed here since the package may not import back from the shim.
    """
    if rec.get("type") != "user":
        return False
    if rec.get("isSidechain"):
        return False
    if rec.get("isMeta"):
        return False
    if rec.get("isCompactSummary"):
        return False
    content = (rec.get("message") or {}).get("content", "")
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        return False
    return bool(render._content_text(content).strip())


# Mirrors cmd_skill_invocation's own <command-name> regex
# (transcript-analysis.py:2371) — re-expressed here since the package may
# not import back from the shim.
_SLASH_COMMAND_RE = re.compile(r"<command-name>/([^<]+)</command-name>")


def _round_skill_name(raw: str) -> str:
    """Normalize one Skill/`/slash` invocation name for REVIEW_SKILLS
    membership.

    Mirrors _normalize_skill_name (transcript-analysis.py:2253-2277)'s
    directory-qualifier strip (segment after the last "/"), then also strips
    a remaining `plugin:`/`dir:` qualifier by taking the segment after the
    last ":" — safe here, unlike _normalize_skill_name (which deliberately
    keeps such a prefix for its own display-label use), because REVIEW_SKILLS
    is a closed three-name membership test, not a display label.
    """
    normalized = raw.rsplit("/", 1)[-1]
    return normalized.rsplit(":", 1)[-1]


def _round_open_skill(rec: dict) -> str | None:
    """The REVIEW_SKILLS member this main-thread record opens a round for,
    or None.

    Two disjoint invocation shapes: a `Skill` tool_use's input.skill, and a
    `/slash` user record's <command-name> tag.

    OUTPUT INVARIANT: only input["skill"] is ever read from a Skill
    tool_use block, mirroring cmd_skill_invocation's own comment
    (transcript-analysis.py:2311-2323). input["args"] can carry an absolute
    local path and must never be extracted or surfaced here.
    """
    if rec.get("isSidechain"):
        return None
    rtype = rec.get("type")
    if rtype == "assistant":
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Skill":
                continue
            raw_skill = (block.get("input") or {}).get("skill") or ""
            matched = _round_skill_name(raw_skill)
            if matched in _REVIEW_SKILL_SET:
                return matched
        return None
    if rtype == "user":
        content_raw = (rec.get("message") or {}).get("content", "")
        content_str = content_raw if isinstance(content_raw, str) else render._content_text(content_raw)
        for m in _SLASH_COMMAND_RE.finditer(content_str):
            matched = _round_skill_name(m.group(1))
            if matched in _REVIEW_SKILL_SET:
                return matched
        return None
    return None


def _detect_round_windows(records: list[dict]) -> list[tuple[int, int, str]]:
    """Every (open_idx, window_end, skill) round window in one session's
    (already deduped, main-thread-only) records.

    Re-expresses cmd_judgment_pair's own boundary rule
    (transcript-analysis.py:2166-2197). window_end is the index of the next
    fresh user prompt or the next round-open record, whichever comes first
    (exclusive of window_end itself). The window is inclusive of its own
    opening record. No cross-path dedup is needed between the two
    invocation shapes. They are disjoint by construction: a Skill tool_use
    lives on an assistant record, a /slash tag lives on a user record.
    """
    n = len(records)
    windows: list[tuple[int, int, str]] = []
    for idx, rec in enumerate(records):
        skill = _round_open_skill(rec)
        if skill is None:
            continue
        window_end = n
        for scan_idx in range(idx + 1, n):
            scan_rec = records[scan_idx]
            if _is_fresh_user_prompt(scan_rec):
                window_end = scan_idx
                break
            if _round_open_skill(scan_rec) is not None:
                window_end = scan_idx
                break
        windows.append((idx, window_end, skill))
    return windows


def _session_record_branches(records: list[dict], windows: list[tuple[int, int, str]]) -> list[str]:
    """Per-record branch attribution for one session, computed once before
    pricing as a pure function of (records, windows).

    Pass 1 carries forward the last non-empty gitBranch from a
    non-sidechain record at every index, mirroring cost.py's
    _session_branch_index main-thread-only carry-forward. A sidechain
    record's own gitBranch can be an isolation:"worktree" dispatch's
    ephemeral worktree-agent-* branch, not this session's real one, so it
    is read but never becomes the carried value.

    Pass 2 overwrites every index inside a round's window
    [open_idx, window_end) with that window's own opening-record branch,
    so a round's dollars always land in the branch_totals bucket the
    round itself is keyed to, even when the session's own gitBranch
    changes mid-window. Windows are disjoint and in index order, so this
    never writes one index twice.
    """
    branches: list[str] = [""] * len(records)
    last_branch = ""
    for idx, rec in enumerate(records):
        if not rec.get("isSidechain"):
            branch = rec.get("gitBranch") or ""
            if branch:
                last_branch = branch
        branches[idx] = last_branch
    for open_idx, window_end, _skill in windows:
        window_branch = branches[open_idx]
        for idx in range(open_idx, window_end):
            branches[idx] = window_branch
    return branches


def _price_dispatch(
    tool_use_id: str,
    dispatch_index: dict[str, tuple[Path, str | None]],
    visited: set[str],
) -> tuple[float, int, int, int]:
    """Price one subagent dispatch and recurse into every Agent/Task spawn
    inside its own transcript.

    Recursion works via corpus._index_subagent_dispatches' recursive
    layout: a subagent's own jsonl resolves its *own* nested subagents/
    directory the same way a session's jsonl does.

    `visited` is a toolUseId set shared across one session's whole recursive
    walk. A colliding toolUseId (a corrupted/retried-dispatch shape) is
    therefore never priced twice — the walk terminates and each dispatch is
    priced at most once.

    Each dispatch's own turns are deduped before pricing
    (pricing.dedup_turns_by_request_id must run before pricing — see
    pricing.py). This matches _compute_pr_cost_branch_totals's own
    dedup-then-price sequence, so these dollars are derived the same way
    cost's and pr-cost's are.

    Returns (dollars, unpriced_turns, dangling, resolved_dispatches).
    dangling counts both a dispatch_index lookup miss and an
    unreadable/missing sibling .jsonl as one bucket, mirroring subagent-mix's
    own Dangling column. resolved_dispatches is this dispatch plus every
    nested one successfully priced — review-round-cost's own "agents"
    column.
    """
    if tool_use_id in visited:
        return 0.0, 0, 0, 0
    visited.add(tool_use_id)

    paired = dispatch_index.get(tool_use_id)
    if paired is None:
        return 0.0, 0, 1, 0
    jsonl_path, _requested_model = paired
    records = corpus._parse_jsonl_records(jsonl_path)
    if records is None:
        return 0.0, 0, 1, 0
    records = pricing.dedup_turns_by_request_id(records)
    nested_index, _meta_errors = corpus._index_subagent_dispatches(jsonl_path)

    dollars = 0.0
    unpriced_turns = 0
    dangling = 0
    resolved = 1
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        usage = (rec.get("message") or {}).get("usage")
        if usage:
            model = (rec.get("message") or {}).get("model", "")
            dollars_by_class, _context_at_turn, _unpriced_tokens = pricing._price_turn(model, usage)
            if dollars_by_class is None:
                unpriced_turns += 1
            else:
                dollars += sum(dollars_by_class.values())
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in pricing._SPAWN_TOOL_NAMES:
                continue
            nested_tool_use_id = block.get("id") or ""
            if not nested_tool_use_id:
                continue
            n_dollars, n_unpriced, n_dangling, n_resolved = _price_dispatch(nested_tool_use_id, nested_index, visited)
            dollars += n_dollars
            unpriced_turns += n_unpriced
            dangling += n_dangling
            resolved += n_resolved

    return dollars, unpriced_turns, dangling, resolved


def compute_review_round_costs(
    session_iter,
    *,
    skill_filter: set[str] | None = None,
    branch_filter: set[str] | None = None,
    since_ts: float | None = None,
    until_ts: float | None = None,
    resolved_roots: Sequence[Path] | None = None,
) -> dict:
    """Single pass over session_iter, main-thread only.

    Rounds are detected on the main thread only. A review skill run inside a
    dispatched subagent is already priced as that dispatch's own cost, so
    counting it as its own round would double-count its dollars.

    Computes every review-skill round window, its main-thread plus
    recursively-priced subagent dollars, and each touched branch's total
    dollars (main + subagent, round or not) — see docs/transcript-analysis.md's
    review-round-cost section for the round/non-round reconciliation-line
    formula this branch total feeds.

    A round's branch is keyed (root_idx, branch). branch is the round's own
    opening record's gitBranch, carried forward from the last non-empty
    main-thread value when absent, and every record inside that round's
    window — not only the opening record — is attributed to that same
    branch for branch_totals purposes (_session_record_branches), so a
    round's dollars always land in the branch_totals bucket the round
    itself is keyed to even when the session's own gitBranch changes
    mid-window. A record outside every window still carries forward the
    last non-empty gitBranch, unaffected by any window. root_idx is None
    under a single scan root, else the 0-based index into resolved_roots
    the session's own jsonl resolves under (scope._root_index_for_path).
    This keying keeps two different roots' identically-named branches from
    merging into one row, mirroring subagent-mix's own (root_idx, branch)
    keying. resolved_roots is only consulted when it has more than one
    entry; caller passes None (or an empty/single-element sequence) under a
    single root.

    skill_filter/branch_filter/since_ts/until_ts are applied as a final
    filter over the detected rounds, by the round's own skill / raw branch
    name / opening timestamp, matching judgment-pair's convention. They do
    not narrow which records are priced or which windows are detected, so
    branch_totals always reflects each branch's full, unwindowed corpus
    activity and a round's own main_dollars/agent_dollars/agents are
    invariant to skill_filter.

    Returns {"rounds": [...], "branch_totals": {(root_idx, branch): dollars}}.
    Each round dict holds branch_key, skill, ts (raw timestamp string or
    None), main_dollars, agent_dollars, agents, unpriced_turns, dangling,
    and sort_key. sort_key is (opening-timestamp-or-+inf, session path
    string, opening record index) — the ordering rule sessions written in
    reverse file-path order need.
    """
    multi_root = bool(resolved_roots) and len(resolved_roots) > 1
    all_rounds: list[dict] = []
    branch_totals: dict[tuple[int | None, str], float] = defaultdict(float)

    for jsonl, records in session_iter:
        records = pricing.dedup_turns_by_request_id(records)  # dedup before pricing — see pricing.py
        root_idx = scope._root_index_for_path(jsonl, resolved_roots) if multi_root else None
        windows = _detect_round_windows(records)
        record_branches = _session_record_branches(records, windows)
        dispatch_index, _meta_errors = corpus._index_subagent_dispatches(jsonl)
        visited: set[str] = set()

        round_entries: list[dict] = [
            {
                "branch_key": (root_idx, record_branches[open_idx]), "skill": skill,
                "ts": records[open_idx].get("timestamp"),
                "main_dollars": 0.0, "agent_dollars": 0.0, "agents": 0,
                "unpriced_turns": 0, "dangling": 0, "open_idx": open_idx,
            }
            for open_idx, _window_end, skill in windows
        ]

        window_ptr = 0
        for idx, rec in enumerate(records):
            while window_ptr < len(windows) and idx >= windows[window_ptr][1]:
                window_ptr += 1
            in_window = window_ptr < len(windows) and windows[window_ptr][0] <= idx
            round_entry = round_entries[window_ptr] if in_window else None

            if rec.get("type") != "assistant":
                continue

            usage = (rec.get("message") or {}).get("usage")
            if usage:
                model = (rec.get("message") or {}).get("model", "")
                dollars_by_class, _context_at_turn, _unpriced_tokens = pricing._price_turn(model, usage)
                if dollars_by_class is None:
                    if round_entry is not None:
                        round_entry["unpriced_turns"] += 1
                else:
                    turn_dollars = sum(dollars_by_class.values())
                    branch_totals[(root_idx, record_branches[idx])] += turn_dollars
                    if round_entry is not None:
                        round_entry["main_dollars"] += turn_dollars

            for block in (rec.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in pricing._SPAWN_TOOL_NAMES:
                    continue
                tool_use_id = block.get("id") or ""
                if not tool_use_id:
                    continue
                dollars, unpriced, dangling, resolved = _price_dispatch(tool_use_id, dispatch_index, visited)
                branch_totals[(root_idx, record_branches[idx])] += dollars
                if round_entry is not None:
                    round_entry["agent_dollars"] += dollars
                    round_entry["unpriced_turns"] += unpriced
                    round_entry["dangling"] += dangling
                    round_entry["agents"] += resolved

        for entry in round_entries:
            open_idx = entry.pop("open_idx")
            sort_ts = corpus._parse_ts(entry["ts"])
            entry["sort_key"] = (sort_ts if sort_ts is not None else float("inf"), str(jsonl), open_idx)
            all_rounds.append(entry)

    filtered_rounds: list[dict] = []
    for entry in all_rounds:
        rts = corpus._parse_ts(entry["ts"])
        if since_ts is not None and (rts is None or rts < since_ts):
            continue
        if until_ts is not None and (rts is None or rts >= until_ts):
            continue
        if branch_filter is not None and entry["branch_key"][1] not in branch_filter:
            continue
        if skill_filter is not None and entry["skill"] not in skill_filter:
            continue
        filtered_rounds.append(entry)

    return {"rounds": filtered_rounds, "branch_totals": dict(branch_totals)}


def cmd_review_round_cost(args: argparse.Namespace) -> None:
    """Per-branch review-round dollar cost.

    Opens a round at every `code-review`/`plan-review`/`ready-for-review`
    invocation, both the `Skill` tool_use path and the `/slash` path.
    Closes it at the next round-open or the next fresh user prompt,
    whichever comes first. See compute_review_round_costs's own docstring
    for how each round's dollars are priced.

    Every invocation is its own round. Diff-state is not deduped, because
    token cost is incurred whether or not the round produced findings.
    `ready-for-review` counts toward a branch's rounds total uniformly
    with the other two skills, with its own per-skill sub-breakdown.

    Rows are keyed by the round's own opening record's gitBranch, carried
    forward when absent. A branch with zero rounds in scope is not
    reported.

    See docs/transcript-analysis.md's review-round-cost section for the
    round/non-round reconciliation-line formula, and for the corpus-wide
    "Non-round dollars"/"Dangling dispatches"/"Unpriced turns" footer lines.

    Output redaction follows subagent-mix's documented contract exactly:
    under more than one scan root, a branch name prints raw only under
    --this-repo (account-<K>/<branch>), else opaque
    (account-<K>/branch-<N>), with DO NOT PUBLISH on stdout and stderr;
    under a single root there is nothing to redact, so branch names print
    raw unconditionally.
    """
    this_repo = bool(getattr(args, "this_repo", False))
    branches_arg: str | None = getattr(args, "branches", None) or None
    branch_filter = {b for b in branches_arg.split(",") if b} if branches_arg else None
    skill_arg: str | None = getattr(args, "skill", None) or None
    skill_filter = {skill_arg} if skill_arg else None
    since_ts, until_ts = scope._parse_absolute_window_args(args, "review-round-cost")

    roots = scope.resolve_scan_roots(args)
    multi_root = len(roots) > 1
    if multi_root:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = scope._resolve_project_scope(args, "review-round-cost", roots=roots)
    scope.print_resolved_scope("review-round-cost", scope_label, roots)

    resolved_roots = [root.resolve() for root in roots] if multi_root else None
    data = compute_review_round_costs(
        session_iter,
        skill_filter=skill_filter,
        branch_filter=branch_filter,
        since_ts=since_ts,
        until_ts=until_ts,
        resolved_roots=resolved_roots,
    )
    rounds = data["rounds"]
    branch_totals = data["branch_totals"]

    if not rounds:
        print("\nNo review rounds found in scope.")
        return

    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(roots) if multi_root else {}
    branch_redact_map: dict[tuple[int, str], str] = {}

    def _branch_label(branch_key: tuple[int | None, str]) -> str:
        root_idx, branch = branch_key
        if root_idx is None:
            return render._sanitize_table_cell(branch)
        return redaction._root_scoped_display_label(
            "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map,
            disclose=this_repo,
        )

    by_branch: dict[tuple[int | None, str], list[dict]] = defaultdict(list)
    for entry in rounds:
        by_branch[entry["branch_key"]].append(entry)

    total_rounds = 0
    skill_round_counts: dict[str, int] = defaultdict(int)
    skill_dollar_totals: dict[str, float] = defaultdict(float)
    total_round_dollars = 0.0
    total_branch_dollars = 0.0
    total_unpriced_turns = 0
    total_dangling = 0

    print()
    for branch_key in sorted(by_branch, key=_branch_label):
        branch_rounds = sorted(by_branch[branch_key], key=lambda e: e["sort_key"])
        label = _branch_label(branch_key)
        branch_dollars = branch_totals.get(branch_key, 0.0)
        branch_round_dollars = sum(e["main_dollars"] + e["agent_dollars"] for e in branch_rounds)

        per_skill_counts: dict[str, int] = defaultdict(int)
        for e in branch_rounds:
            per_skill_counts[e["skill"]] += 1
        skill_summary = "  ".join(f"{s}={per_skill_counts.get(s, 0)}" for s in REVIEW_SKILLS)

        print(label)
        print(f"  rounds={len(branch_rounds)}  ({skill_summary})")
        print(
            f"  round {render._fmt_usd(branch_round_dollars)} of {render._fmt_usd(branch_dollars)} branch $"
            f" ({render._pct_of(branch_round_dollars, branch_dollars)})"
        )
        print(f"   {'#':>2}  {'skill':<17} {'n':>2}  {'date':<10}  {'main $':>8}  {'agent $':>8}  {'agents':>6}  {'total $':>8}")

        per_skill_running: dict[str, int] = defaultdict(int)
        for ordinal, e in enumerate(branch_rounds, start=1):
            per_skill_running[e["skill"]] += 1
            n = per_skill_running[e["skill"]]
            ts_epoch = corpus._parse_ts(e["ts"])
            date_label = render._fmt_date(ts_epoch) if ts_epoch is not None else "?"
            round_total_dollars = e["main_dollars"] + e["agent_dollars"]
            print(
                f"  {ordinal:>2}  {e['skill']:<17} {n:>2}  {date_label:<10}  {e['main_dollars']:>8.2f}"
                f"  {e['agent_dollars']:>8.2f}  {e['agents']:>6}  {round_total_dollars:>8.2f}"
            )
        print()

        total_rounds += len(branch_rounds)
        for e in branch_rounds:
            skill_round_counts[e["skill"]] += 1
            skill_dollar_totals[e["skill"]] += e["main_dollars"] + e["agent_dollars"]
            total_unpriced_turns += e["unpriced_turns"]
            total_dangling += e["dangling"]
        total_round_dollars += branch_round_dollars
        total_branch_dollars += branch_dollars

    skill_counts_str = "  ".join(f"{s}={skill_round_counts.get(s, 0)}" for s in REVIEW_SKILLS)
    print(f"Totals: {len(by_branch)} branches, {total_rounds} rounds ({skill_counts_str})")
    print(f"Mean rounds per branch: {total_rounds / len(by_branch):.2f}")

    mean_parts = []
    for s in REVIEW_SKILLS:
        count = skill_round_counts.get(s, 0)
        mean_parts.append(f"{s} {skill_dollar_totals[s] / count:.2f}" if count else f"{s} no data")
    print("Mean $ per round — " + "  ".join(mean_parts))

    non_round_dollars = total_branch_dollars - total_round_dollars
    print(
        f"Non-round dollars: {render._pct_of(non_round_dollars, total_branch_dollars)}"
        " of branch dollars fell outside every round window"
    )
    print(f"Dangling dispatches inside round windows: {total_dangling} (no readable meta.json/jsonl pair)")
    print(f"Unpriced turns inside round windows: {total_unpriced_turns}")
