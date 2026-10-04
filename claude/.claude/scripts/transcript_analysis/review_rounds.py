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
import contextlib
import io
import random
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

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
    """Normalize one Skill/`/slash` invocation name for REVIEW_SKILLS or
    REVIEW_TRACE_SKILLS membership: strip the directory qualifier (as
    _normalize_skill_name does) and then the plugin:/dir: qualifier too.

    _normalize_skill_name (transcript-analysis.py:2253-2277) strips the
    directory qualifier (segment after the last "/"). It keeps a
    plugin:/dir: prefix for its own display-label use. This function also
    strips that prefix, by taking the segment after the last ":". That is
    safe here because both REVIEW_SKILLS and REVIEW_TRACE_SKILLS are
    closed-set membership tests, not display labels.
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


def detect_round_windows(records: list[dict]) -> list[tuple[int, int, str]]:
    """Every (open_idx, window_end, skill) round window in one session's
    (already deduped, main-thread-only) records.

    Re-expresses cmd_judgment_pair's own boundary rule
    (transcript-analysis.py:2166-2197). window_end is the index of the next
    fresh user prompt or the next round-open record, whichever comes first
    (exclusive of window_end itself). The window is inclusive of its own
    opening record. No cross-path dedup is needed between the two
    invocation shapes. They are disjoint by construction: a Skill tool_use
    lives on an assistant record, a /slash tag lives on a user record.

    Public (no leading underscore): author_outcome.py is a second consumer,
    reading only each window's own open_idx/skill (its own outcome-span
    definition is not this function's window_end -- see
    docs/transcript-analysis.md's author-outcome section).
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
            n_dollars, n_unpriced, n_dangling, n_resolved = _price_dispatch(
                nested_tool_use_id, nested_index, visited,
            )
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
        windows = detect_round_windows(records)
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
                dollars, unpriced, dangling, resolved = _price_dispatch(
                    tool_use_id, dispatch_index, visited,
                )
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


def compute_review_round_counts(
    session_iter,
    *,
    branch_filter: set[str] | None = None,
) -> dict[str, int]:
    """Per-skill round counts across session_iter, main-thread only.

    Every REVIEW_SKILLS member is a key in the returned dict, zeros
    included, so a caller never has to special-case an absent skill.

    Detection reuses the same three helpers in the same order as
    compute_review_round_costs -- pricing.dedup_turns_by_request_id,
    detect_round_windows, _session_record_branches -- so the two functions
    can never disagree on what counts as one round. Dedup runs before
    detection here too, not only before pricing (see
    pricing.dedup_turns_by_request_id's own docstring): without it, one API
    call's Skill tool_use can appear in more than one record and count as
    two rounds.

    branch_filter matches a round's own opening record's raw gitBranch,
    carried forward when absent -- the same attribution
    compute_review_round_costs uses for branch_key's second element.

    No pricing, no dispatch index, no recursion: unlike
    compute_review_round_costs, this never opens a dispatched subagent's own
    transcript.
    """
    counts: dict[str, int] = dict.fromkeys(REVIEW_SKILLS, 0)
    for _jsonl, records in session_iter:
        records = pricing.dedup_turns_by_request_id(records)  # dedup before detection, not only before pricing -- see pricing.py
        windows = detect_round_windows(records)
        if not windows:
            continue
        record_branches = _session_record_branches(records, windows)
        for open_idx, _window_end, skill in windows:
            if branch_filter is not None and record_branches[open_idx] not in branch_filter:
                continue
            counts[skill] += 1
    return counts


# --- --pooled: cross-account share block ------------------------------------
#
# Everything below prints percentages and 95% confidence intervals only --
# no dollar amount, no raw count printed directly, no per-account/
# per-project/per-branch split. In a small pool the percentages can still
# reveal the round count and approximate the per-branch spread; see
# docs/transcript-analysis.md § "review-round-cost" Small-pool residual.
# See docs/private-project-redaction.md § "The owner can authorize
# one figure, case by case". Promotion trigger: when a second subcommand
# grows a pooled mode, move the doc pointer and approval pointer below to
# scope.py, beside scope._DO_NOT_PUBLISH_BANNER.

_POOLED_PUBLICATION_POINTER = (
    "POOLED — publishable only under docs/private-project-redaction.md\n"
    '§ "The owner can authorize one figure, case by case". Propose the figure, this exact\n'
    "command, and the destination artifact to the owner, then cite the owner's\n"
    "approval in that artifact. Nothing here checks that for you. Before citing\n"
    "this alongside any rate or count already published elsewhere (e.g. a $/PR\n"
    "rate or a branch count), name that composition in the proposal — these\n"
    "shares were not designed to be composed with a figure outside this block.\n"
    "Before proposing a plain --pooled figure, run the same command with\n"
    "--show-withheld first and check the two data-quality gap shares, which\n"
    "plain --pooled never prints. In the proposal, state without digits\n"
    "whether either gap share or its upper bound prints above zero and\n"
    "whether a skipped-account notice appeared on stderr. Keep that\n"
    "statement out of the artifact and its citation. The proposal must also say,\n"
    "without digits, whether the pool is small (few branch units or few rounds;\n"
    "docs/transcript-analysis.md § review-round-cost, Small-pool residual, says\n"
    "how to judge). Keep the small-pool statement out of the artifact and its\n"
    "citation as well."
)

_POOLED_SHOW_WITHHELD_BANNER = (
    "DO NOT PUBLISH — --show-withheld prints figures the dominance-precision\n"
    "floor would otherwise withhold from a plain --pooled run. Internal\n"
    "insight only. Never cite this run; cite a plain --pooled run instead."
)

_POOLED_CAPTION = (
    "Pooled across the scan roots resolved for this run, whole period. Every\n"
    "dollar share below is a share of list-price compute, never of billed\n"
    "spend; the Rounds by skill lines are shares of round count. Every figure\n"
    "covers only branches with at least one review round. No dollar amount, no\n"
    "raw count, and no per-account, per-project, or per-branch split is\n"
    "emitted directly. In a small pool, the Rounds by skill shares can still\n"
    "reveal the round count, and the interval endpoints can approximate the\n"
    "per-branch spread.\n"
    "Each interval is a 2,000-resample percentile bootstrap resampled over\n"
    "branches, so it reflects branch-to-branch variation, treating the branches\n"
    "in scope as a sample of ongoing work. The 95% level is nominal. Actual\n"
    "coverage is lower when few branches are in scope."
)

# In-band pricing-trust lines, printed only when their condition holds. The
# stale-rate line fires on the whole rate table, not on the models in scope, so
# it depends only on the date and the code. They differ from cost.py's STALE
# PRICING and PRICING INTEGRITY banners on purpose: digit-free, $-free, no model
# IDs, no per-model scoping, and no publish or cite step.
_POOLED_STALE_RATE_LINE = (
    "STALE PRICING — today is past the re-verify-by date for the model rates, so\n"
    "every dollar figure below may be wrong. The Rounds by skill lines do not\n"
    "depend on the rates.\n"
    f"Re-check the rates at {pricing._PRICING_SOURCE_URL}."
)
_POOLED_FORMAT_DRIFT_LINE = (
    "PRICING INTEGRITY — the transcript format may have drifted, so every\n"
    "figure below may be wrong. Rerun without --pooled to read the drift\n"
    "diagnostic on stderr."
)

# Percentile bootstrap resampled over branches. The 2,000-resample count
# matches this repo's own prior use of the same technique in
# docs/cost-levers-considered.md's "Opus-anchored plan boundary" section.
_BOOTSTRAP_RESAMPLES = 2000
# Fixed so a published figure is reproducible by whoever checks it -- the
# value itself is arbitrary. The digits are a function of the interpreter
# version as well as the corpus and resolved root paths, since CPython does
# not pin Random.choices' mapping from the stream to indices.
_BOOTSTRAP_SEED = 0
_CI_LEVEL = 0.95

_POOLED_REFUSAL_DOC_POINTER = (
    ' See docs/private-project-redaction.md § "The owner can authorize one figure, case by case".'
)

# The message's diagnosis and "restore read access" advice hold because a
# gap is recorded only when an existing directory or regular file fails to
# read (scope.py's _list_dir_recording_gaps/_failed_transcript_read_is_gap).
# A missing path, a stray non-directory, and a non-regular *.jsonl never
# reach it. A non-permission OSError on an existing path (e.g. EIO) still
# does. There, the advice and the find hint don't apply, but the refusal
# still correctly identifies a partial scan.
_POOLED_SCAN_GAP_REFUSAL = (
    "review-round-cost --pooled refuses a partial scan: a resolved scan root, or a project"
    " directory or main-thread transcript under one, exists but could not be read, so part of the corpus would"
    " silently drop out of the pooled figure. Check each account's projects/ directory for a"
    " directory or .jsonl transcript you cannot read (with GNU find:"
    " `find <projects-dir> ! -readable`; the paths it lists can name a private project, so keep"
    " them in your own session), then restore read access, or remove that account from"
    f" {scope.TRANSCRIPT_CONFIG_DIRS_LABEL} if it is a declared entry you no longer need."
)

# Defense-in-depth backstop for cmd_review_round_cost's pooled scan-and-render sequence,
# printed only when an exception the scan-gap accounting above doesn't anticipate escapes it.
# Never interpolates the caught exception's str(), which may embed a raw filesystem path.
_POOLED_SCAN_ABORTED_MESSAGE = (
    "review-round-cost --pooled produced no valid pooled figure: an unexpected error interrupted"
    " the corpus scan or the pooled render. Rerun without --pooled to read the diagnostics; if"
    " that run succeeds, the failure was transient or is in the pooled render itself."
)


class _PooledBranchTotals(NamedTuple):
    """One branch's totals, or (via _pooled_branch_aggregates) the
    elementwise sum of several -- the true pool, or one bootstrap
    resample's own draw.

    A branch with priced spend but no in-scope round is excluded from the
    pool entirely, matching the existing per-root footer's own
    accumulation.

    skill_round_counts/skill_round_dollars are keyed by every
    REVIEW_SKILLS member, zeros included.
    """
    round_dollars: float
    agent_dollars: float
    branch_dollars: float
    skill_round_counts: dict[str, int]
    skill_round_dollars: dict[str, float]
    rounds_with_dangling: int
    rounds_with_unpriced: int
    round_count: int


# The pooled share block's own fixed key space -- _pooled_shares (below)
# and the "too few branches" degenerate branch in _render_pooled_block
# must both cover exactly this set.
_POOLED_STAT_KEYS: tuple[str, ...] = (
    "spend_inside", "spend_outside", "spend_reviewer_only",
    *(f"skill_spend:{s}" for s in REVIEW_SKILLS),
    *(f"skill_rounds:{s}" for s in REVIEW_SKILLS),
    "gap_dangling", "gap_unpriced",
)

# The two data-quality-gap shares print only under --show-withheld. A corpus
# with no gap has an exact 0% share, which the dominance-precision floor
# withholds at any weight, so printing them under plain --pooled would
# withhold the whole block for every healthy corpus. The floor also skips
# them (_POOLED_PUBLISHED_STAT_KEYS), since a share that is never printed
# leaks nothing.
_POOLED_GAP_STAT_KEYS: tuple[str, ...] = ("gap_dangling", "gap_unpriced")
_POOLED_PUBLISHED_STAT_KEYS: tuple[str, ...] = tuple(
    key for key in _POOLED_STAT_KEYS if key not in _POOLED_GAP_STAT_KEYS
)

# Each share's own _PooledBranchTotals denominator field, used only by the
# dominance-precision floor below (_pooled_dominance_breach) to weigh a
# contributing account against the same total the share itself divides by.
# Keys must match _POOLED_STAT_KEYS exactly -- see
# test_pooled_share_denominator_field_covers_exactly_pooled_stat_keys.
_POOLED_SHARE_DENOMINATOR_FIELD: dict[str, str] = {
    "spend_inside": "branch_dollars",
    "spend_outside": "branch_dollars",
    "spend_reviewer_only": "branch_dollars",
    **{f"skill_spend:{s}": "round_dollars" for s in REVIEW_SKILLS},
    **{f"skill_rounds:{s}": "round_count" for s in REVIEW_SKILLS},
    "gap_dangling": "round_count",
    "gap_unpriced": "round_count",
}


def _pooled_scope_refusal(
    args: argparse.Namespace,
    *,
    roots: Sequence[Path] | None = None,
    scan_gaps: Counter[str] | None = None,
) -> str | None:
    """The first applicable --pooled refusal message, or None once every
    check passes -- evaluated in the order of the `--pooled` refusal list under
    docs/transcript-analysis.md § "review-round-cost".

    roots=None defers the root-count check. scan_gaps=None defers the
    scan-gap check. Only cmd_review_round_cost's own calls may rely on
    either deferral, since both precede the scan. Every other caller must
    pass a resolved list and a counter.
    """
    if getattr(args, "branches", None):
        return (
            "review-round-cost --pooled refuses --branches: naming branches makes the"
            " figure per-deliverable, not a pooled share." + _POOLED_REFUSAL_DOC_POINTER
        )
    if getattr(args, "projects", None) not in (None, "*"):
        return (
            "review-round-cost --pooled refuses a non-default --projects glob: a named"
            " glob is a per-project dimension." + _POOLED_REFUSAL_DOC_POINTER
        )
    if getattr(args, "skill", None):
        return (
            "review-round-cost --pooled refuses --skill: it narrows the numerator against"
            " an un-narrowed denominator and degenerates the per-skill lines." + _POOLED_REFUSAL_DOC_POINTER
        )
    if getattr(args, "since", None) or getattr(args, "until", None):
        return (
            "review-round-cost --pooled refuses --since/--until: whole period only, never"
            " a time series." + _POOLED_REFUSAL_DOC_POINTER
        )
    if getattr(args, "config_dir", None):
        return (
            "review-round-cost --pooled refuses top-level --config-dir: it collapses the"
            " pool to one named account." + _POOLED_REFUSAL_DOC_POINTER
        )
    if getattr(args, "this_repo", False):
        return (
            "review-round-cost --pooled refuses --this-repo: not implemented as a pooled"
            " scope -- a product decision, not a policy bar." + _POOLED_REFUSAL_DOC_POINTER
        )
    if roots is not None and len(roots) < 2:
        return (
            "review-round-cost --pooled requires more than one resolved scan root: a"
            " single-account figure is a per-account figure. Check that"
            f" {scope.TRANSCRIPT_CONFIG_DIRS_LABEL} exists, is readable, and declares another account."
            + _POOLED_REFUSAL_DOC_POINTER
        )
    if scan_gaps:
        return _POOLED_SCAN_GAP_REFUSAL + _POOLED_REFUSAL_DOC_POINTER
    return None


def _pooled_branch_aggregates(per_branch: Sequence[_PooledBranchTotals]) -> _PooledBranchTotals:
    """Elementwise-sum a list of _PooledBranchTotals into one pooled total
    -- reused identically for the true sample (point estimate) and for
    every bootstrap resample's own draw, so every reported statistic is
    computed the same way regardless of which list it was handed.
    """
    return _PooledBranchTotals(
        round_dollars=sum(b.round_dollars for b in per_branch),
        agent_dollars=sum(b.agent_dollars for b in per_branch),
        branch_dollars=sum(b.branch_dollars for b in per_branch),
        skill_round_counts={s: sum(b.skill_round_counts[s] for b in per_branch) for s in REVIEW_SKILLS},
        skill_round_dollars={s: sum(b.skill_round_dollars[s] for b in per_branch) for s in REVIEW_SKILLS},
        rounds_with_dangling=sum(b.rounds_with_dangling for b in per_branch),
        rounds_with_unpriced=sum(b.rounds_with_unpriced for b in per_branch),
        round_count=sum(b.round_count for b in per_branch),
    )


def _pooled_shares(agg: _PooledBranchTotals) -> dict[str, float | None]:
    """Computes every pooled share (_POOLED_STAT_KEYS) from one already-
    summed _PooledBranchTotals.

    Returns None where that share's denominator is zero, rather than
    render._pct_of's 0.0, since zero denominator here means undefined,
    not zero.
    """
    shares: dict[str, float | None] = {
        "spend_inside": render._pct_value(agg.round_dollars, agg.branch_dollars) if agg.branch_dollars else None,
        "spend_outside": (
            render._pct_value(agg.branch_dollars - agg.round_dollars, agg.branch_dollars)
            if agg.branch_dollars else None
        ),
        "spend_reviewer_only": render._pct_value(agg.agent_dollars, agg.branch_dollars) if agg.branch_dollars else None,
        "gap_dangling": render._pct_value(agg.rounds_with_dangling, agg.round_count) if agg.round_count else None,
        "gap_unpriced": render._pct_value(agg.rounds_with_unpriced, agg.round_count) if agg.round_count else None,
    }
    for skill in REVIEW_SKILLS:
        shares[f"skill_spend:{skill}"] = (
            render._pct_value(agg.skill_round_dollars[skill], agg.round_dollars) if agg.round_dollars else None
        )
        shares[f"skill_rounds:{skill}"] = (
            render._pct_value(agg.skill_round_counts[skill], agg.round_count) if agg.round_count else None
        )
    return shares


def _resample_percentile(sorted_values: Sequence[float]) -> tuple[float, float]:
    """2.5th/97.5th percentile bounds by index into an already-sorted
    resample distribution: lo_idx = round(tail * (B - 1)), hi_idx =
    round((1 - tail) * (B - 1)), tail = (1 - _CI_LEVEL) / 2.
    """
    b = len(sorted_values)
    tail = (1 - _CI_LEVEL) / 2
    lo_idx = round(tail * (b - 1))
    hi_idx = round((1 - tail) * (b - 1))
    return sorted_values[lo_idx], sorted_values[hi_idx]


def _bootstrap_share_intervals(
    per_branch: Sequence[_PooledBranchTotals],
) -> dict[str, tuple[float | None, float | None, float | None]]:
    """Branch-cluster percentile bootstrap over every pooled share.

    Resamples branches, not rounds, since every reported statistic is a
    ratio of two branch-level sums and resampling rounds instead would
    leave the denominator undefined. Each of _BOOTSTRAP_RESAMPLES draws
    resamples len(per_branch) branches with replacement via a local
    random.Random(_BOOTSTRAP_SEED) instance, never the module-global RNG.
    Every stat's CI is drawn from the same resample set, so CIs stay
    mutually consistent rather than each stat resampled independently.
    """
    point = _pooled_shares(_pooled_branch_aggregates(per_branch))
    rand = random.Random(_BOOTSTRAP_SEED)
    resample_values: dict[str, list[float]] = {key: [] for key in _POOLED_STAT_KEYS}
    for _ in range(_BOOTSTRAP_RESAMPLES):
        draw = rand.choices(per_branch, k=len(per_branch))
        for key, value in _pooled_shares(_pooled_branch_aggregates(draw)).items():
            if value is not None:
                # A key whose draws mostly hit a zero denominator still
                # renders a full-confidence CI off whatever few survive.
                resample_values[key].append(value)

    intervals: dict[str, tuple[float | None, float | None, float | None]] = {}
    for key in _POOLED_STAT_KEYS:
        point_value = point[key]
        values = sorted(resample_values[key])
        if point_value is None or not values:
            # point=0.0 here is a filler value, never printed (see
            # _fmt_share_with_ci).
            # lo=None is what actually signals a zero-denominator branch.
            # point=None is reserved for _render_pooled_block's separate
            # "too few branches" case, so the two degenerate reasons stay
            # distinguishable downstream.
            intervals[key] = (0.0, None, None)
        else:
            lo, hi = _resample_percentile(values)
            intervals[key] = (point_value, lo, hi)
    return intervals


def _single_account_within_stated_precision(
    w_max: float, p_estimate: float, ci_lo: float, ci_hi: float,
) -> bool:
    """True when one contributing account's own weight in a share's
    denominator makes that share functionally a single-account figure, at
    the precision its own already-computed 95% CI claims.

    Every pooled share is a sum of per-branch sums each belonging to one
    account, so P = w_max·d + (1 - w_max)·r, where `d` is the dominant
    account's own true share and `r` is the combined true share of every
    other account. Solving for `d` as `r` ranges over its full `[0, 100]`
    domain gives the exact set of `d` values consistent with the observed
    point estimate `p_estimate`: `[(p_estimate - (1 - w_max) * 100) / w_max,
    p_estimate / w_max]`, clipped to `[0, 100]`. A breach happens when that
    exact interval sits entirely inside `[ci_lo, ci_hi]` -- meaning the
    published CI pins the dominant account's own true share down to the
    CI's own stated precision. That is a true positive for exactly the
    per-account dimension CLAUDE.md's redaction rule bars.

    An exact 0% or 100% share always breaches, at any `w_max`: every branch
    contributes the same extreme value, so the CI collapses to that point and
    the exact interval collapses onto it too.
    """
    if not (ci_lo <= p_estimate <= ci_hi):
        # Fail-closed backstop: a percentile bootstrap over few branches can
        # place the point estimate outside its own resampled interval. The
        # exact-interval containment test below has no meaningful answer
        # against such an inconsistent CI.
        return True
    if w_max <= 0:
        # Structurally unreachable in production: _pooled_dominance_breach's
        # own caller only reaches this function for a share whose interval
        # exists (lo is not None), which requires a positive denominator
        # total, and w_max is a ratio of one account's contribution to that
        # total, so w_max > 0 always holds there. Fail closed anyway, since
        # dividing by w_max below would otherwise raise.
        return True
    # Two algebraically equal forms: each rounds one ulp low at different
    # inputs (the second at p == 100, the first at w_max == 1.0). A low
    # exact_lo can only hide a breach, so take the larger, fail-closed one.
    exact_lo = max(
        0.0,
        100.0 - (100.0 - p_estimate) / w_max,
        (p_estimate - (1 - w_max) * 100.0) / w_max,
    )
    exact_hi = min(100.0, p_estimate / w_max)
    return ci_lo <= exact_lo and exact_hi <= ci_hi


def _pooled_dominance_breach(
    intervals: dict[str, tuple[float | None, float | None, float | None]],
    account_denominator_totals: dict[int | None, dict[str, float]],
) -> bool:
    """True when any one share in `intervals` fails the dominance-precision
    floor (_single_account_within_stated_precision) against its own
    contributing accounts' weights in `account_denominator_totals`.

    Only the shares printed under plain --pooled
    (_POOLED_PUBLISHED_STAT_KEYS) are checked. A hit on any single share
    withholds the whole pooled block, because a blank on one share would
    itself reveal that it breached.
    """
    pooled_denominator_totals: dict[str, float] = defaultdict(float)
    for totals in account_denominator_totals.values():
        for field, value in totals.items():
            pooled_denominator_totals[field] += value
    for key in _POOLED_PUBLISHED_STAT_KEYS:
        point, lo, hi = intervals[key]
        if lo is None:
            # A zero-denominator share prints no figure to leak.
            # This guard also keeps the division by denom_total below defined.
            continue
        field = _POOLED_SHARE_DENOMINATOR_FIELD[key]
        denom_total = pooled_denominator_totals[field]
        w_max = max(totals[field] for totals in account_denominator_totals.values()) / denom_total
        if _single_account_within_stated_precision(w_max, point, lo, hi):
            return True
    return False


def _fmt_share_with_ci(point: float | None, lo: float | None, hi: float | None) -> str:
    """Render one pooled share line as `P.P% (95% CI L.L-H.H%)`.

    `point` is None when the whole pool has too few branches to bootstrap.
    `lo` is None (with `point` defined but unused) when this one share's
    own denominator is zero. Neither degenerate wording carries a figure:
    their reason clauses contain no digit, and the only digits are in the
    fixed "95% CI" frame.
    """
    if point is None:
        return "(95% CI not computed — too few branches in scope)"
    if lo is None:
        return "(95% CI not computed — no priced branch spend)"
    return f"{point:.1f}% (95% CI {lo:.1f}-{hi:.1f}%)"


def _pooled_resolved_scope_header(scope_label: str) -> str:
    """The --pooled header line, built locally instead of calling
    scope.print_resolved_scope: that shared function's own root-count
    clause is unconditional, even at one root, and would otherwise
    disclose the number of resolved scan roots (a per-account dimension)
    even under --pooled.
    """
    return f"REVIEW ROUND COST SOURCES ({scope_label}; pooled)"


def _pooled_rate_table_past_reverify_by(today: date) -> bool:
    """True once `today` is past the earliest re-verify-by date in the whole
    rate table, so the result depends only on the date and the code.
    """
    return today > min(pricing._MODEL_RATE_EXPIRES.values())


def _render_pooled_block(
    args: argparse.Namespace,
    roots: Sequence[Path],
    scope_label: str,
    rounds: list[dict],
    branch_totals: dict[tuple[int | None, str], float],
    *,
    scan_gaps: Counter[str],
    today: date,
) -> None:
    """--pooled's entire render path: shares and 95% confidence intervals
    only, never a dollar amount, a directly printed raw count, or a
    per-account/per-project/per-branch split. A small pool can still reveal
    its round count through the shares; see docs/transcript-analysis.md
    § "review-round-cost" Small-pool residual. See
    docs/private-project-redaction.md
    § "The owner can authorize one figure, case by case".

    Re-derives cmd_review_round_cost's own refusal check as defense in
    depth: every direct caller of this function, including this module's
    own tests, bypasses that CLI-boundary check, so this call is the only
    enforcement a direct caller ever sees. Passes `roots or []`, never
    `roots` bare. This makes a caller that passes None or an empty
    sequence fail the root-count floor instead of silently skipping it.

    scan_gaps fills only as the session iterator is consumed, so this
    function's refusal call is the only point the scan-gap clause can fire.

    The printed line set is fixed by the flags, apart from the two pricing-trust
    lines: nine share lines under plain --pooled, plus the two
    data-quality-gap lines under --show-withheld (see _POOLED_GAP_STAT_KEYS).
    `today` is the UTC date the caller read once, so the stale-rate line
    depends only on that date and the code.

    The drift line is the one deliberate data-dependent exception. Its trigger
    is a contiguous multi-record requestId run whose records disagree on an
    invariant input/cache usage class, merged by dedup_turns_by_request_id. A
    disagreeing non-contiguous group is rejected before the canary and never
    sets it. Every main transcript and every spawn's dispatched-subagent
    transcript the scan reads or prices counts, whether or not a round window
    contains it, so the bit is scan-wide. Only the usage-drift canary can set
    it on this command, because the subagent-format canary has no caller on
    this path. _pooled_filtered_stderr_call's withheld notice fires on drift
    and also on any unrecognized line, so it is a superset of the drift
    condition. Any further data-dependent bit needs its own review.
    """
    refusal = _pooled_scope_refusal(args, roots=roots or [], scan_gaps=scan_gaps)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        sys.exit(2)

    # show_withheld-requires-pooled is enforced only at cmd_review_round_cost's
    # own CLI boundary, not re-checked here. See `docs/transcript-analysis.md`
    # § "review-round-cost" for the --show-withheld flag's own contract.
    show_withheld = bool(getattr(args, "show_withheld", False))

    by_branch: dict[tuple[int | None, str], list[dict]] = defaultdict(list)
    for entry in rounds:
        by_branch[entry["branch_key"]].append(entry)

    per_branch: list[_PooledBranchTotals] = []
    # Per-account totals of every field _POOLED_SHARE_DENOMINATOR_FIELD
    # names, used only by the dominance-precision floor below.
    denominator_fields = set(_POOLED_SHARE_DENOMINATOR_FIELD.values())
    account_denominator_totals: dict[int | None, dict[str, float]] = defaultdict(
        lambda: dict.fromkeys(denominator_fields, 0.0)
    )
    # The seeded RNG picks branches by position, so the order must not depend
    # on rounds-list order or on scan-order root indexes.
    # The order is fixed by the resolved-path root ordinal and the branch name.
    # The ordinal is a sort key only and is never printed.
    root_ordinals = scope._redaction_ordinals(roots)
    ordinal_by_root_idx = {root_idx: root_ordinals[root.resolve()] for root_idx, root in enumerate(roots)}
    for branch_key in sorted(by_branch, key=lambda k: (ordinal_by_root_idx[k[0]], k[1])):
        branch_rounds = by_branch[branch_key]
        skill_round_counts: dict[str, int] = dict.fromkeys(REVIEW_SKILLS, 0)
        skill_round_dollars: dict[str, float] = dict.fromkeys(REVIEW_SKILLS, 0.0)
        for e in branch_rounds:
            skill_round_counts[e["skill"]] += 1
            skill_round_dollars[e["skill"]] += e["main_dollars"] + e["agent_dollars"]
        branch_pooled_totals = _PooledBranchTotals(
            round_dollars=sum(e["main_dollars"] + e["agent_dollars"] for e in branch_rounds),
            agent_dollars=sum(e["agent_dollars"] for e in branch_rounds),
            branch_dollars=branch_totals.get(branch_key, 0.0),
            skill_round_counts=skill_round_counts,
            skill_round_dollars=skill_round_dollars,
            rounds_with_dangling=sum(1 for e in branch_rounds if e["dangling"] > 0),
            rounds_with_unpriced=sum(1 for e in branch_rounds if e["unpriced_turns"] > 0),
            round_count=len(branch_rounds),
        )
        per_branch.append(branch_pooled_totals)
        account_totals = account_denominator_totals[branch_key[0]]
        for field in denominator_fields:
            account_totals[field] += getattr(branch_pooled_totals, field)

    # Two independent floors collapse to the same degenerate wording below:
    # `contributing_roots` bounds contributing-account count,
    # `_pooled_dominance_breach` bounds relative weight. See
    # `docs/transcript-analysis.md` § "review-round-cost" for both floors'
    # own residual.
    contributing_roots = {branch_key[0] for branch_key in by_branch}
    # docs/transcript-analysis.md's Small-pool residual cites these literals as
    # "four branch units and two accounts".
    too_few_for_bootstrap = len(per_branch) < 4 or len(contributing_roots) < 2
    intervals = (
        dict.fromkeys(_POOLED_STAT_KEYS, (None, None, None))
        if too_few_for_bootstrap
        else _bootstrap_share_intervals(per_branch)
    )
    if not show_withheld and not too_few_for_bootstrap and _pooled_dominance_breach(
        intervals, account_denominator_totals
    ):
        intervals = dict.fromkeys(_POOLED_STAT_KEYS, (None, None, None))

    def fmt(key: str) -> str:
        return _fmt_share_with_ci(*intervals[key])

    # Both trust predicates resolve before the first print, so the render never
    # prints a partial block when one raises.
    rate_table_past_reverify_by = _pooled_rate_table_past_reverify_by(today)
    format_drift_detected = pricing._format_drift_detected()

    print(_pooled_resolved_scope_header(scope_label))
    print()
    print(_POOLED_SHOW_WITHHELD_BANNER if show_withheld else _POOLED_PUBLICATION_POINTER)
    if show_withheld:
        print(_POOLED_SHOW_WITHHELD_BANNER, file=sys.stderr)
    print()
    print(_POOLED_CAPTION)
    print()
    if rate_table_past_reverify_by:
        print(_POOLED_STALE_RATE_LINE)
        print()
    if format_drift_detected:
        print(_POOLED_FORMAT_DRIFT_LINE)
        print()
    print("  Share of branch spend")
    print(f"    {'inside round windows':<30}{fmt('spend_inside')}")
    print(f"    {'outside every round window':<30}{fmt('spend_outside')}")
    print(f"    {'reviewer dispatches only':<30}{fmt('spend_reviewer_only')}")
    print("  Round-window spend by skill")
    for skill in REVIEW_SKILLS:
        print(f"    {skill:<30}{fmt(f'skill_spend:{skill}')}")
    print("  Rounds by skill")
    for skill in REVIEW_SKILLS:
        print(f"    {skill:<30}{fmt(f'skill_rounds:{skill}')}")
    if show_withheld:
        print("  Rounds affected by a data-quality gap")
        print(f"    {'dangling dispatch':<30}{fmt('gap_dangling')}")
        print(f"    {'unpriced turn':<30}{fmt('gap_unpriced')}")


_SCANNING_ROOT_DIAGNOSTIC_RE = re.compile(r"^scanning root \d+/\d+\.\.\.$")
# declared_transcript_roots()'s own per-line warning
# (_config_dir.py's declared_roots_matching, warn_prefix="declared_transcript_roots").
# It is raised by scope.resolve_scan_roots(), before the post-resolution refusal.
# The line index it names is a lower bound on the declared-roots file's size, so
# it reveals the root count.
_DECLARED_ROOT_DIAGNOSTIC_RE = re.compile(r"^declared_transcript_roots: declared root \d+ unreadable$")
# pricing's calibration-audit line, matched in full: any scanned transcript with one
# non-contiguous same-requestId run prints it, so it is routine and not a per-account signal.
_PRICING_NON_CONTIGUOUS_MERGE_NOTICE_RE = re.compile(
    r"^NOTICE: non-contiguous requestId run "
    r"(?:merged -- requestId .+? has \d+ non-contiguous assistant records, "
    r"merged into one turn \(usage matched on every record\) "
    r"\(further merged occurrences this run of the CLI are suppressed\)\."
    r"|rejected -- requestId .+? has \d+ non-contiguous assistant records, "
    r"left as separate turns \(usage did not match on every record\) "
    r"\(further rejected occurrences this run of the CLI are suppressed\)\.)$"
)
# Fail-open notice for the declared-root case: declared_roots_matching's own
# docstring documents skipping an invalid entry as intended. This restores
# the non-pooled path's drop-out signal without naming which account or how
# many.
_DECLARED_ROOT_SKIPPED_NOTICE = (
    f"review-round-cost --pooled: one or more entries in {scope.TRANSCRIPT_CONFIG_DIRS_LABEL} were"
    " skipped (invalid or unreadable); any account they name is not in this pool."
)
# Known diagnostic shapes and their replacements. Any stderr line matching
# none of them is withheld behind _POOLED_STDERR_WITHHELD_NOTICE. A None
# replacement drops every matching line entirely. A string replacement
# prints once per call, in place of every matching line however many there
# are.
_POOLED_STDERR_DIAGNOSTIC_RES: tuple[tuple[re.Pattern[str], str | None], ...] = (
    (_SCANNING_ROOT_DIAGNOSTIC_RE, None),
    (_DECLARED_ROOT_DIAGNOSTIC_RE, _DECLARED_ROOT_SKIPPED_NOTICE),
    (_PRICING_NON_CONTIGUOUS_MERGE_NOTICE_RE, None),
)
# Withheld in place of any stderr line matching none of the known shapes
# above, since an unrecognized line has not been checked for per-account
# content.
_POOLED_STDERR_WITHHELD_NOTICE = (
    "review-round-cost --pooled: one or more diagnostics were withheld; rerun"
    " without --pooled to read them before citing any figure."
)


def _pooled_filtered_stderr_call(fn, *args, **kwargs):
    """Call fn with its stderr filtered per _POOLED_STDERR_DIAGNOSTIC_RES's
    rules, including any line printed before fn raises.
    """
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            result = fn(*args, **kwargs)
    finally:
        printed_notices: set[str] = set()
        for line in captured.getvalue().splitlines():
            stripped = line.strip()
            for pattern, replacement in _POOLED_STDERR_DIAGNOSTIC_RES:
                if pattern.match(stripped):
                    if replacement is not None and replacement not in printed_notices:
                        printed_notices.add(replacement)
                        print(replacement, file=sys.stderr)
                    break
            else:
                if _POOLED_STDERR_WITHHELD_NOTICE not in printed_notices:
                    printed_notices.add(_POOLED_STDERR_WITHHELD_NOTICE)
                    print(_POOLED_STDERR_WITHHELD_NOTICE, file=sys.stderr)
    return result


def _pooled_compute_review_round_costs(*args, **kwargs) -> dict:
    """compute_review_round_costs, routed through _pooled_filtered_stderr_call.

    --pooled must never print the resolved root count (see
    _pooled_resolved_scope_header).
    """
    return _pooled_filtered_stderr_call(compute_review_round_costs, *args, **kwargs)


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
    round/non-round reconciliation-line formula, and for the
    "Non-round dollars"/"Reviewer-dispatch dollars"/"Dangling dispatches"/
    "Unpriced turns" footer lines.
    Under multi-root scope the footer prints one block per root, never
    blended across roots.

    Output redaction follows subagent-mix's documented contract exactly for
    per-branch rows: under more than one scan root, a branch name prints raw
    only under --this-repo (account-<K>/<branch>), else opaque
    (account-<K>/branch-<N>), with DO NOT PUBLISH on stdout and stderr;
    under a single root there is nothing to redact, so branch names print
    raw unconditionally. Unlike subagent-mix (which has no cross-branch
    footer), this command's own footer is partitioned per root the same way.

    `--pooled` instead renders a fixed cross-account share block with
    bootstrap CIs (_render_pooled_block), refusing every scope-narrowing
    flag first and requiring more than one resolved scan root. Everything
    above this line describes the non-pooled path only.
    """
    pooled = bool(getattr(args, "pooled", False))
    show_withheld = bool(getattr(args, "show_withheld", False))
    if show_withheld and not pooled:
        print(
            "review-round-cost --show-withheld requires --pooled." + _POOLED_REFUSAL_DOC_POINTER,
            file=sys.stderr,
        )
        sys.exit(2)
    # Everything through the pooled render, both refusal calls included, runs
    # inside this try so no raw traceback escapes under --pooled.
    try:
        if pooled:
            # Pre-scan refusal: before resolve_scan_roots, so a refused run
            # never scans the corpus at all. roots=None skips the root-count
            # clause, re-checked below once roots are known.
            refusal = _pooled_scope_refusal(args)
            if refusal is not None:
                print(refusal, file=sys.stderr)
                sys.exit(2)

        this_repo = bool(getattr(args, "this_repo", False))
        branches_arg: str | None = getattr(args, "branches", None) or None
        branch_filter = {b for b in branches_arg.split(",") if b} if branches_arg else None
        skill_arg: str | None = getattr(args, "skill", None) or None
        skill_filter = {skill_arg} if skill_arg else None
        since_ts, until_ts = scope._parse_absolute_window_args(args, "review-round-cost")

        # resolve_scan_roots runs through the diagnostic filter too, because
        # declared_transcript_roots()'s own "declared root N unreadable" warning
        # reveals the root count.
        roots = (
            _pooled_filtered_stderr_call(scope.resolve_scan_roots, args)
            if pooled
            else scope.resolve_scan_roots(args)
        )
        multi_root = len(roots) > 1

        if pooled:
            # Post-resolution refusal: must return before any print side effect
            # below, including the banner-suppression and header-suppression
            # branches this same `pooled` flag gates.
            refusal = _pooled_scope_refusal(args, roots=roots)
            if refusal is not None:
                print(refusal, file=sys.stderr)
                sys.exit(2)

        if multi_root and not pooled:
            print(scope._DO_NOT_PUBLISH_BANNER)
            print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

        # Only --pooled records scan gaps. Every other caller gets
        # scan_gaps=None, which skips an unreadable path silently.
        scan_gaps: Counter[str] | None = Counter() if pooled else None
        session_iter, scope_label = scope._resolve_project_scope(
            args, "review-round-cost", roots=roots, scan_gaps=scan_gaps,
        )
        if not pooled:
            # --pooled prints _pooled_resolved_scope_header instead, because
            # print_resolved_scope discloses the root count.
            scope.print_resolved_scope("review-round-cost", scope_label, roots)

        resolved_roots = [root.resolve() for root in roots] if multi_root else None
        # --pooled routes through the diagnostic-filtering wrapper: scope's own
        # "scanning root N/M..." print would otherwise disclose the resolved
        # root count on stderr. Every other path calls compute_review_round_costs
        # directly, with stderr unchanged.
        compute = _pooled_compute_review_round_costs if pooled else compute_review_round_costs
        data = compute(
            session_iter,
            skill_filter=skill_filter,
            branch_filter=branch_filter,
            since_ts=since_ts,
            until_ts=until_ts,
            resolved_roots=resolved_roots,
        )
        rounds = data["rounds"]
        branch_totals = data["branch_totals"]

        if pooled:
            _render_pooled_block(
                args, roots, scope_label, rounds, branch_totals,
                scan_gaps=scan_gaps, today=datetime.now(UTC).date(),
            )
            return
    except Exception:
        # sys.exit from a deliberate refusal (_pooled_scope_refusal and friends) raises
        # BaseException, so this except Exception clause never catches it.
        # A non-pooled run re-raises whatever exception does land here, unchanged.
        if not pooled:
            raise
        print(_POOLED_SCAN_ABORTED_MESSAGE, file=sys.stderr)
        sys.exit(2)

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

    # One totals accumulator per root_idx (always {None: ...} under a single
    # root), so the footer below can be partitioned per root instead of
    # blending every root's dollars/round counts into one figure.
    per_root_totals: dict[int | None, dict] = defaultdict(lambda: {
        "num_branches": 0,
        "total_rounds": 0,
        "skill_round_counts": defaultdict(int),
        "skill_dollar_totals": defaultdict(float),
        "total_round_dollars": 0.0,
        "total_branch_dollars": 0.0,
        "total_agent_dollars": 0.0,
        "total_unpriced_turns": 0,
        "total_dangling": 0,
    })

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

        root_totals = per_root_totals[branch_key[0]]
        root_totals["num_branches"] += 1
        root_totals["total_rounds"] += len(branch_rounds)
        for e in branch_rounds:
            root_totals["skill_round_counts"][e["skill"]] += 1
            root_totals["skill_dollar_totals"][e["skill"]] += e["main_dollars"] + e["agent_dollars"]
            root_totals["total_unpriced_turns"] += e["unpriced_turns"]
            root_totals["total_dangling"] += e["dangling"]
        root_totals["total_round_dollars"] += branch_round_dollars
        root_totals["total_branch_dollars"] += branch_dollars
        root_totals["total_agent_dollars"] += sum(e["agent_dollars"] for e in branch_rounds)

    def _print_footer(totals: dict, *, prefix: str = "") -> None:
        skill_round_counts = totals["skill_round_counts"]
        skill_dollar_totals = totals["skill_dollar_totals"]
        skill_counts_str = "  ".join(f"{s}={skill_round_counts.get(s, 0)}" for s in REVIEW_SKILLS)
        print(f"{prefix}Totals: {totals['num_branches']} branches, {totals['total_rounds']} rounds ({skill_counts_str})")
        print(f"{prefix}Mean rounds per branch: {totals['total_rounds'] / totals['num_branches']:.2f}")

        mean_parts = []
        for s in REVIEW_SKILLS:
            count = skill_round_counts.get(s, 0)
            mean_parts.append(f"{s} {skill_dollar_totals[s] / count:.2f}" if count else f"{s} no data")
        print(f"{prefix}Mean $ per round — " + "  ".join(mean_parts))

        non_round_dollars = totals["total_branch_dollars"] - totals["total_round_dollars"]
        print(
            f"{prefix}Non-round dollars: {render._pct_of(non_round_dollars, totals['total_branch_dollars'])}"
            " of branch dollars fell outside every round window"
        )
        print(
            f"{prefix}Reviewer-dispatch dollars: {render._pct_of(totals['total_agent_dollars'], totals['total_branch_dollars'])}"
            " of branch dollars, inside round windows"
        )
        print(f"{prefix}Dangling dispatches inside round windows: {totals['total_dangling']} (no readable meta.json/jsonl pair)")
        print(f"{prefix}Unpriced turns inside round windows: {totals['total_unpriced_turns']}")

    if multi_root:
        # One footer block per root, ordered by the same resolved-path-sorted
        # ordinal the per-branch account-<K> labels use.
        # A root with no rounds left in scope after filtering is simply
        # absent from the footer, not printed as a zeroed block.
        root_idxs_by_ordinal = sorted(per_root_totals, key=lambda idx: redact_ordinals[resolved_roots[idx]])
        for i, root_idx in enumerate(root_idxs_by_ordinal):
            if i > 0:
                print()
            ordinal = redact_ordinals[resolved_roots[root_idx]]
            _print_footer(per_root_totals[root_idx], prefix=f"account-{ordinal} ")
    else:
        _print_footer(per_root_totals[None])
