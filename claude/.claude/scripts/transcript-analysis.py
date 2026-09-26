#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
pr-link is the only subcommand that touches the network (via gh).
Every subcommand is read-only except an explicit write flag: judgment-pair
--out, pr-cost --record, and pr-cost-export --out each write a file.
"""

import argparse
import contextlib
import fnmatch
import hashlib  # noqa: F401 -- test_transcript_reviewer_yield.py reads _mod.hashlib as a stdlib passthrough
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

import _config  # noqa: F401 -- test files patch _mod._config's attributes as a module passthrough
from _config_dir import config_dir

# audit_routing/cache_rebuild/cache_rebuild_rules/corpus/cost/cost_ledger/denials/gh_cli/
# ledger_common/pr_cost/pr_cost_export/pr_cost_ledger/pricing/read_scope/redaction/render/
# review_trace/reviewer_yield/workstream_cost are read only via _mod.<module> from test files
# (unit-testing a private helper, or patching module-owned state like scope.PROJECTS_DIR
# below) -- scope is the only one this file's own code reads bare, as scope.PROJECTS_DIR.
from transcript_analysis import (  # noqa: F401
    audit_routing,
    cache_rebuild,
    cache_rebuild_rules,
    corpus,
    cost,
    cost_ledger,
    denials,
    gh_cli,
    ledger_common,
    pr_cost,
    pr_cost_export,
    pr_cost_ledger,
    pricing,
    read_scope,
    redaction,
    render,
    review_trace,
    reviewer_yield,
    scope,
    workstream_cost,
)
from transcript_analysis.audit_routing import (
    # All three names below are read bare by this file's own still-monolithic build_parser
    # (each audit-routing subcommand's own set_defaults).
    cmd_audit_routing,
    cmd_audit_routing_samples,
    cmd_audit_routing_shape,
)
from transcript_analysis.author_outcome import _AUTHORING_AGENT_CODE_WRITER, cmd_author_outcome
from transcript_analysis.cache_rebuild import (
    # Both names below are read bare by this file's own still-monolithic code:
    #   _CACHE_REBUILD_DEFAULT_SINCE/_CACHE_REBUILD_DEFAULT_THRESHOLD -> build_parser's own
    #     --since/--threshold defaults
    #   cmd_cache_rebuild                                             -> p_cache_rebuild.set_defaults
    _CACHE_REBUILD_DEFAULT_SINCE,
    _CACHE_REBUILD_DEFAULT_THRESHOLD,
    cmd_cache_rebuild,
)
from transcript_analysis.corpus import (
    SUBAGENT_SUBDIR,
    _index_subagent_dispatches,
    _parse_ts,
    _read_session_file_partitioned,
    iter_sessions,
)
from transcript_analysis.cost import (
    # The ten noqa'd names below are read only via _mod.<name> from test files (unit-testing
    # a private helper directly, or a monkeypatch retarget). _compute_pr_cost_branch_totals is
    # one of them: pr_cost.py's own code reads it as cost._compute_pr_cost_branch_totals instead.
    # cmd_cost/cmd_cost_trend and _compute_workstream_dollars are the three this file's own code
    # also calls bare -- via p_cost/p_cost_trend.set_defaults below, and
    # handoff-signal-response's own call site.
    _accumulate_per_account_turn,  # noqa: F401
    _attributed_branch,  # noqa: F401
    _compute_pr_cost_branch_totals,  # noqa: F401
    _compute_workstream_dollars,
    _cost_report,  # noqa: F401
    _cost_trend_report,  # noqa: F401
    _print_branch_exclusion_diagnostic,  # noqa: F401
    _print_model_id_table,  # noqa: F401
    _print_thread_table,  # noqa: F401
    _print_token_class_table,  # noqa: F401
    _session_branch_index,  # noqa: F401
    cmd_cost,
    cmd_cost_trend,
)
from transcript_analysis.cost_ledger import (
    # Read bare by this file's own still-monolithic build_parser (the cost-ledger
    # subcommand's own set_defaults).
    cmd_cost_ledger,
)
from transcript_analysis.denials import (
    # Both names below are read bare by this file's own still-monolithic code:
    #   hook_denial_key                       -> _friction_denial_events
    #   _drop_denial_command_flag_values      -> _command_segment_is_mutating_git
    _drop_denial_command_flag_values,
    hook_denial_key,
)
from transcript_analysis.gh_cli import (
    # Read bare by pr-link: _classify_gh_error, _GH_ERROR_KIND_NETWORK, _gh_host_qualified_repo,
    # _GH_CALL_TIMEOUT_S, _git_remote_origin_host_and_owner_repo.
    _GH_CALL_TIMEOUT_S,
    _GH_ERROR_KIND_NETWORK,
    _classify_gh_error,
    _gh_host_qualified_repo,
    _git_remote_origin_host_and_owner_repo,
)
from transcript_analysis.pr_cost import (
    # _DEFAULT_PR_COST_PLAN_FILE_GLOB and _PR_COST_ASOF_WINDOW_DAYS_DEFAULT are read bare by
    #   build_parser's own --plan-file-glob/--asof-window-days defaults.
    # cmd_pr_cost is read bare by build_parser's own p_pr_cost.set_defaults.
    _DEFAULT_PR_COST_PLAN_FILE_GLOB,
    _PR_COST_ASOF_WINDOW_DAYS_DEFAULT,
    cmd_pr_cost,
)
from transcript_analysis.pr_cost_export import (
    # Read bare by this file's own still-monolithic build_parser (the pr-cost-export
    # subcommand's own set_defaults).
    cmd_pr_cost_export,
)
from transcript_analysis.pricing import (
    _CACHE_READ_MULTIPLIER,
    _CACHE_WRITE_1H_MULTIPLIER,
    _CACHE_WRITE_5M_MULTIPLIER,
    _CONTEXT_DISTRIBUTION_THRESHOLD_ABS,
    _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS,
    _FAST_MODE_RATE_MULTIPLIER,
    _INFERENCE_GEO_US_RATE_MULTIPLIER,
    _MODEL_BASE_INPUT_RATES,
    _MODEL_RATE_EXPIRES,
    _PRICING_SOURCE_URL,
    _SPAWN_TOOL_NAMES,
    _TOKEN_CLASSES,  # noqa: F401 -- read only via _mod._TOKEN_CLASSES from test files
    _cache_miss_reason,
    _cache_write_split,
    _context_at_turn,
    _context_bucket,  # noqa: F401 -- read only via _mod._context_bucket from test files
    _context_window_for_model,
    _count_subagent_spawns,
    _model_rates,
    _price_turn,
    _session_peak_context,
    _warn_if_subagent_format_drift,
)
from transcript_analysis.pricing import dedup_turns_by_request_id as _dedup_turns_by_request_id
from transcript_analysis.read_scope import (
    # Both names below are read bare by this file's own still-monolithic code:
    #   _READ_SCOPE_CHARS_PER_TOKEN -> _classify_content_item
    #   cmd_read_scope              -> p_read_scope.set_defaults
    _READ_SCOPE_CHARS_PER_TOKEN,
    cmd_read_scope,
)
from transcript_analysis.redaction import (
    _BUILT_IN_AGENT_TYPES,  # noqa: F401 -- read only via _mod._BUILT_IN_AGENT_TYPES from test files
    _REDACT_MAP_MISS_TOKEN,  # noqa: F401 -- read only via _mod._REDACT_MAP_MISS_TOKEN from test files
    _assign_session_redact_label,
    _build_redact_map,
    _corpus_fingerprint,  # noqa: F401 -- read only via _mod._corpus_fingerprint from test files
    _derive_proj_label,
    _project_family,
    _redact_proj_label,
    _redact_session_id,
    _repo_tracked_agent_type_names,
    _root_scoped_display_label,
)
from transcript_analysis.render import (
    _content_text,
    _context_distribution_rows,
    _fam,
    _fmt_date,
    _fmt_usd,
    _pct_of,
    _sanitize_table_cell,
    _strip_task_notifications,
)
from transcript_analysis.review_rounds import (
    # REVIEW_SKILLS is read bare by this file's own still-monolithic
    # cmd_judgment_pair (its own --skills default) -- the one-directional
    # exception documented in docs/transcript-analysis-architecture.md.
    REVIEW_SKILLS,
    cmd_review_round_cost,
    compute_review_round_counts,
)
from transcript_analysis.review_trace import (
    # Both names below are read bare by this file's own still-monolithic
    # build_parser (the review-trace subcommand's set_defaults).
    REVIEW_TRACE_SKILLS,
    cmd_review_trace,
)
from transcript_analysis.reviewer_yield import (
    # The names below are read only via _mod.<name> from test files (unit-testing a
    # private helper directly) -- cmd_reviewer_yield and _is_reviewer_subagent_type.
    # cmd_reviewer_yield is also read bare by this file's own still-monolithic code, via
    # p_reviewer_yield.set_defaults.
    _CITED_PATH_CANDIDATE_MAX_CHARS,  # noqa: F401
    _build_tool_result_ts_map,  # noqa: F401
    _dispatch_self_reference_keys,  # noqa: F401
    _extract_cited_paths,  # noqa: F401
    _index_session_edits,  # noqa: F401
    _is_reviewer_subagent_type,  # noqa: F401
    _normalize_cited_path,  # noqa: F401
    _reviewer_yield_cited_keys,  # noqa: F401
    cmd_reviewer_yield,
)
from transcript_analysis.scope import (
    _DO_NOT_PUBLISH_BANNER,
    _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR,
    _branch_filter,
    _iter_glob_scoped_sessions,
    _iter_scoped_sessions,
    _parse_absolute_window_args,
    _parse_since_nd_arg,
    _projects_glob,
    _redaction_ordinals,
    _repo_scoped_project_slugs,
    _resolve_cost_roots,
    _resolve_project_scope,
    _resolved_scope_header,
    _root_index_for_path,
    _scan_root_transcripts,
    _single_level_projects_glob,
)
from transcript_analysis.scope import print_resolved_scope as _print_resolved_scope
from transcript_analysis.scope import resolve_scan_roots as _resolve_scan_roots
from transcript_analysis.workstream_cost import (
    # Read bare by this file's own still-monolithic build_parser (the workstream-cost
    # subcommand's own set_defaults).
    cmd_workstream_cost,
)

TEST_RUNNER_RE = re.compile(
    r"\b(vitest|jest|pytest|deno\s+test|npm\s+run\s+(verify|test|lint)|ruff\s+check|cargo\s+test|go\s+test)\b"
)
FAILED_RE = re.compile(r"\b(\d+)\s+failed\b")

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
    # attested in transcripts but missed by prior lexicon (see test_transcript_analysis.py)
    # Excluded: bare "stale" — legitimate technical term with high false-positive risk
    "hallucinat",  # matches "hallucinated", "hallucinating", etc.
    "are you saying",
    "you should be able to",
    "that doesn't exist",
    "that doesn't match",
]


def _is_fresh_user_prompt(rec: dict) -> bool:
    """Return True iff rec is a genuine new user message (not a tool result or injected record).

    Filters out:
    - Records that are not type=="user"
    - Sidechain records (isSidechain=True)
    - Meta-injected records (isMeta=True)
    - Compaction summary records (isCompactSummary=True)
    - Tool-result-bearing records (content is a list with any block whose type=="tool_result")
    - Records with empty text content
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
    return bool(_content_text(content).strip())


def _iso_date(s: str) -> str:
    """argparse type: validate a YYYY-MM-DD date string."""
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a valid YYYY-MM-DD date: {s!r}") from None
    return s


def _longest_fail_streak(failed_flags: list[bool]) -> int:
    """Return the longest consecutive run of True values in failed_flags."""
    max_streak = current = 0
    for flag in failed_flags:
        if flag:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def cmd_buckets(args: argparse.Namespace) -> None:
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "buckets", roots=roots)
    _print_resolved_scope("buckets", scope_label, roots)

    branch_data: dict[str, dict] = defaultdict(
        lambda: {
            "sessions": 0, "projects": set(), "opus": 0, "sonnet": 0, "haiku": 0, "other": 0,
            "ts_min": float("inf"), "ts_max": float("-inf"),
        }
    )

    for jsonl, records in session_iter:
        file_branches: dict[str, dict] = defaultdict(
            lambda: {"opus": 0, "sonnet": 0, "haiku": 0, "other": 0, "ts_min": float("inf"), "ts_max": float("-inf")}
        )
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            ts = _parse_ts(rec.get("timestamp"))
            if ts is not None:
                file_branches[branch]["ts_min"] = min(file_branches[branch]["ts_min"], ts)
                file_branches[branch]["ts_max"] = max(file_branches[branch]["ts_max"], ts)
            if rec.get("type") == "assistant" and not bool(rec.get("isSidechain")):
                fam = _fam((rec.get("message") or {}).get("model", ""))
                file_branches[branch][fam] += 1

        for branch, fb in file_branches.items():
            d = branch_data[branch]
            d["sessions"] += 1
            d["projects"].add(_project_family(jsonl.parent.name))
            for fam in ("opus", "sonnet", "haiku", "other"):
                d[fam] += fb[fam]
            if fb["ts_min"] < float("inf"):
                d["ts_min"] = min(d["ts_min"], fb["ts_min"])
            if fb["ts_max"] > float("-inf"):
                d["ts_max"] = max(d["ts_max"], fb["ts_max"])

    if not branch_data:
        print("No data found.")
        return

    print(
        f"{'Branch':<40} {'Proj':>4} {'Sess':>5} {'Total':>7} {'Opus':>6} {'Sonnet':>7} "
        f"{'Haiku':>6} {'Other':>6}  Date range"
    )
    print("-" * 113)
    for branch in sorted(branch_data):
        d = branch_data[branch]
        total = d["opus"] + d["sonnet"] + d["haiku"] + d["other"]
        ts_min = _fmt_date(d["ts_min"]) if d["ts_min"] < float("inf") else "?"
        ts_max = _fmt_date(d["ts_max"]) if d["ts_max"] > float("-inf") else "?"
        print(
            f"{branch:<40} {len(d['projects']):>4} {d['sessions']:>5} {total:>7} {d['opus']:>6} {d['sonnet']:>7} "
            f"{d['haiku']:>6} {d['other']:>6}  {ts_min}..{ts_max}"
        )


def cmd_fail_seq(args: argparse.Namespace) -> None:
    if not getattr(args, "branches", None):
        print("--branches is required for fail-seq", file=sys.stderr)
        sys.exit(1)
    branches: set[str] = {b for b in args.branches.split(",") if b}
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "fail-seq", roots=roots)
    _print_resolved_scope("fail-seq", scope_label, roots)

    branch_runs: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for _jsonl, records in session_iter:
        if not ({r.get("gitBranch", "") for r in records} & branches):
            continue

        pending: dict[str, str] = {}  # tool_use_id → model_family
        current_branch: str = ""

        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch != current_branch:
                pending.clear()
                current_branch = branch
            if branch not in branches or bool(rec.get("isSidechain")):
                continue

            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype == "assistant":
                fam = _fam(msg.get("model", ""))
                for block in (msg.get("content") or []):
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "Bash"
                    ):
                        cmd = (block.get("input") or {}).get("command", "")
                        if TEST_RUNNER_RE.search(cmd):
                            pending[block["id"]] = fam

            elif rtype in ("user", "human"):
                content = msg.get("content") or []
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    tid = block.get("tool_use_id", "")
                    if block.get("type") == "tool_result" and tid in pending:
                        fam = pending.pop(tid)
                        result_text = _content_text(block.get("content", ""))
                        counts = [int(m) for m in FAILED_RE.findall(result_text)]
                        branch_runs[branch].append((fam, max(counts) if counts else 0))

    if not branch_runs:
        print("No test runs found for the specified branches.")
        return

    for branch in sorted(branch_runs):
        runs = branch_runs[branch]
        total = len(runs)
        failing = sum(1 for _, f in runs if f > 0)
        streak = _longest_fail_streak([f > 0 for _, f in runs])
        fail_rate = f"{100 * failing / total:.1f}%" if total else "—"

        fam_total: dict[str, int] = defaultdict(int)
        fam_fail: dict[str, int] = defaultdict(int)
        for fam, f in runs:
            fam_total[fam] += 1
            if f > 0:
                fam_fail[fam] += 1

        print(f"\n### {branch}")
        print(f"Total runs: {total}  Failing: {failing} ({fail_rate})  Longest consecutive-failing streak: {streak}")
        for fam in ("opus", "sonnet", "haiku", "other"):
            if fam_total[fam]:
                fr = f"{100 * fam_fail[fam] / fam_total[fam]:.1f}%"
                print(f"  {fam:<8}: {fam_total[fam]} runs, {fam_fail[fam]} failing ({fr})")
        print(f"Sequence: {' '.join(str(f) for _, f in runs)}")


def cmd_struggle(args: argparse.Namespace) -> None:
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "struggle", roots=roots)
    _print_resolved_scope("struggle", scope_label, roots)

    branch_data: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for _jsonl, records in session_iter:
        last_fam: dict[str, str] = {}
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            if bool(rec.get("isSidechain")):
                continue
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype == "assistant":
                last_fam[branch] = _fam(msg.get("model", ""))
            elif rtype in ("user", "human"):
                text = _strip_task_notifications(_content_text(msg.get("content", ""))).lower()
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


def _is_fresh_user_prompt_for_narrative(rec: dict) -> bool:
    """Return True when rec is a genuine user keystroke — not a tool result or system injection.

    Distinct from `_is_fresh_user_prompt` (judgment-pair's discriminator): this one excludes
    on toolUseResult/sourceToolUseID/sourceToolAssistantUUID keys instead of tool_result content
    blocks, and accepts promptId-bearing list content instead of requiring a bare string.

    Accepts two content shapes:
    - Plain string (the common case).
    - List-of-blocks with extractable text and a promptId (text+image pastes, or list-shape
      prompts from older Claude Code versions). promptId is the positive signal that
      distinguishes these from isMeta injections that also use list-of-blocks content.
    """
    if rec.get("type") != "user":
        return False
    if rec.get("isMeta") or rec.get("isSidechain"):
        return False
    if "toolUseResult" in rec or "sourceToolUseID" in rec or "sourceToolAssistantUUID" in rec:
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list) and "promptId" in rec:
        return bool(_content_text(content).strip())
    return False


def _is_unrecognized_user_list_record(rec: dict) -> bool:
    """Return True for user records with list-of-blocks content not handled by any known discriminator.

    Known shapes explicitly excluded:
    - isMeta=True: skill/system injections (correctly excluded from prompts).
    - promptId present: accepted as fresh prompts by _is_fresh_user_prompt_for_narrative (list-content variant).
    - toolUseResult / sourceToolUseID / sourceToolAssistantUUID: tool-result records.

    A non-zero count from this function indicates a genuinely unexpected schema variant
    that the fresh-prompt discriminator may be silently missing.
    """
    return (
        rec.get("type") == "user"
        and isinstance(rec.get("message", {}).get("content"), list)
        and not rec.get("isMeta")
        and "promptId" not in rec
        and "toolUseResult" not in rec
        and "sourceToolUseID" not in rec
        and "sourceToolAssistantUUID" not in rec
    )


def _classify_prompt(text: str, is_initial: bool) -> tuple[str, str]:
    """Classify a fresh prompt as INITIAL, FOLLOWUP, or EXPLICIT_CORRECTION.

    Returns (classification, matched_phrase). matched_phrase is non-empty only
    for EXPLICIT_CORRECTION. Phrase-matching runs on text with any forwarded
    `<task-notification>` envelope stripped, so a STRUGGLE_PHRASES entry inside
    one does not register.
    """
    if is_initial:
        return "INITIAL", ""
    lowered = _strip_task_notifications(text).lower()
    for phrase in STRUGGLE_PHRASES:
        if phrase in lowered:
            return "EXPLICIT_CORRECTION", phrase
    return "FOLLOWUP", ""


def _attribute_model_to_prompt(records: list[dict], prompt_index: int, session_id: str) -> str:
    """Scan forward from prompt_index for the next assistant record sharing the session's ID.

    Returns the model family string, or 'unknown' when no attribution is found.
    The session ID is read from the prompt record itself so cross-session attribution
    is not possible.
    """
    prompt_rec = records[prompt_index]
    prompt_session_id = prompt_rec.get("sessionId") or ""
    for rec in records[prompt_index + 1 :]:
        if rec.get("type") != "assistant":
            continue
        if prompt_session_id and rec.get("sessionId") != prompt_session_id:
            continue
        model = (rec.get("message") or {}).get("model", "")
        if model:
            return _fam(model)
    return "unknown"


def _truncate_prompt_text(text: str, limit: int) -> str:
    """Truncate text to limit chars, appending an ellipsis annotation when truncated.

    limit=0 disables truncation entirely.
    """
    if limit == 0 or len(text) <= limit:
        return text
    return text[:limit] + f"… (truncated, {len(text)} chars total)"


def cmd_user_input(args: argparse.Namespace) -> None:
    """Per-session fresh user prompts, classified as INITIAL / FOLLOWUP / EXPLICIT_CORRECTION."""
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)
    corrections_only: bool = bool(getattr(args, "corrections_only", False))
    _truncate_raw = getattr(args, "truncate_chars", None)
    truncate_chars: int = _truncate_raw if _truncate_raw is not None else 500
    out_path: str | None = getattr(args, "out", None) or None
    redact: bool = bool(getattr(args, "redact", False))

    since_ts, until_epoch = _parse_absolute_window_args(args, "user-input")

    redact_map: dict[str, str] = _build_redact_map() if redact else {}
    session_redact_map: dict[str, str] = {}

    # Shape-drift counter: user records with list content missing all tool-result keys.
    unrecognized_shape_count = 0

    # Collected session data for rendering, sorted by first-prompt timestamp.
    # Each entry: {proj_label, branch, date, session_id_prefix, prompts: [...], first_ts}
    session_entries: list[dict] = []

    # Corpus-level counters.
    total_projects_seen: set[str] = set()
    total_session_count = 0
    total_fresh_prompts = 0
    initial_count = 0
    followup_count = 0
    correction_count = 0
    phrase_hits: dict[str, int] = defaultdict(int)
    earliest_ts: float | None = None
    latest_ts: float | None = None

    for jsonl, records in iter_sessions(scope.PROJECTS_DIR, projects_glob):
        proj_label = _derive_proj_label(jsonl)
        total_projects_seen.add(_project_family(jsonl.parent.name))

        # Count unrecognized shapes regardless of other filters.
        for rec in records:
            if _is_unrecognized_user_list_record(rec):
                unrecognized_shape_count += 1

        # Collect fresh prompts for this session, applying all filters.
        session_prompts: list[dict] = []
        is_first_in_session = True

        for idx, rec in enumerate(records):
            if not _is_fresh_user_prompt_for_narrative(rec):
                continue

            # Branch filter
            branch = rec.get("gitBranch") or ""
            if branch_filter and branch not in branch_filter:
                continue

            # Date filter
            rec_ts = _parse_ts(rec.get("timestamp"))
            if since_ts is not None or until_epoch is not None:
                if rec_ts is None:
                    continue
                if since_ts is not None and rec_ts < since_ts:
                    continue
                if until_epoch is not None and rec_ts >= until_epoch:
                    continue

            text = _content_text(rec["message"]["content"])
            classification, matched_phrase = _classify_prompt(text, is_first_in_session)
            is_first_in_session = False

            model_fam = _attribute_model_to_prompt(records, idx, rec.get("sessionId") or "")

            date_str = _fmt_date(rec_ts) if rec_ts is not None else "?"
            time_str = (
                datetime.fromtimestamp(rec_ts, tz=UTC).strftime("%H:%M")
                if rec_ts is not None
                else "?"
            )

            session_prompts.append({
                "text": text,
                "date": date_str,
                "time": time_str,
                "ts": rec_ts,
                "branch": branch,
                "session_id": rec.get("sessionId") or jsonl.stem,
                "classification": classification,
                "matched_phrase": matched_phrase,
                "model_fam": model_fam,
            })

        if not session_prompts:
            continue

        total_session_count += 1
        first_ts = session_prompts[0]["ts"]
        if first_ts is not None:
            if earliest_ts is None or first_ts < earliest_ts:
                earliest_ts = first_ts
            last_ts = session_prompts[-1]["ts"]
            if last_ts is not None and (latest_ts is None or last_ts > latest_ts):
                latest_ts = last_ts

        # Accumulate corpus counters.
        for p in session_prompts:
            total_fresh_prompts += 1
            if p["classification"] == "INITIAL":
                initial_count += 1
            elif p["classification"] == "FOLLOWUP":
                followup_count += 1
            elif p["classification"] == "EXPLICIT_CORRECTION":
                correction_count += 1
                if p["matched_phrase"]:
                    phrase_hits[p["matched_phrase"]] += 1

        # Derive session-level metadata.
        session_branch = session_prompts[0]["branch"]
        session_date = session_prompts[0]["date"]
        raw_session_id = session_prompts[0]["session_id"]
        if redact:
            _assign_session_redact_label(raw_session_id, session_redact_map)
            session_id_prefix = _redact_session_id(raw_session_id, session_redact_map)
        else:
            session_id_prefix = raw_session_id[:8]
        session_models = sorted({p["model_fam"] for p in session_prompts})
        session_correction_count = sum(1 for p in session_prompts if p["classification"] == "EXPLICIT_CORRECTION")
        session_followup_count = sum(1 for p in session_prompts if p["classification"] == "FOLLOWUP")
        display_label = _redact_proj_label(proj_label, redact_map) if redact else proj_label

        session_entries.append({
            "proj_label": display_label,
            "branch": session_branch,
            "date": session_date,
            "first_ts": first_ts,
            "session_id_prefix": session_id_prefix,
            "session_models": session_models,
            "prompts": session_prompts,
            "correction_count": session_correction_count,
            "followup_count": session_followup_count,
        })

    # Sort sessions by first-prompt timestamp ascending.
    session_entries.sort(key=lambda e: (e["first_ts"] or 0.0))

    # Build top-5 struggle-phrase list.
    top_phrases = sorted(phrase_hits.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    top_phrases_str = ", ".join(f'"{ph}" ({n})' for ph, n in top_phrases) if top_phrases else "none"

    date_range_str = (
        f"{_fmt_date(earliest_ts)} → {_fmt_date(latest_ts)}"
        if earliest_ts is not None and latest_ts is not None
        else "no data"
    )

    lines: list[str] = []
    lines.append("# User Input — Conversation Narrative")
    lines.append("")
    lines.append(f"Generated: {_fmt_date(time.time())}")
    lines.append(
        f"Scope: {len(total_projects_seen)} projects, {total_session_count} sessions, "
        f"{total_fresh_prompts} fresh prompts"
    )
    lines.append(f"Date range: {date_range_str}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Fresh prompts: {total_fresh_prompts}")
    lines.append(f"- Initial: {initial_count}")
    lines.append(f"- Followups (quiet redirects): {followup_count}")
    lines.append(f"- Explicit corrections (struggle-phrase match): {correction_count}")
    lines.append(f"- Top struggle phrases: {top_phrases_str}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Sessions")

    for entry in session_entries:
        lines.append("")
        lines.append(f"### {entry['proj_label']} · {entry['branch']} · {entry['date']}")
        models_str = ", ".join(entry["session_models"])
        prompt_count = len(entry["prompts"])
        lines.append(
            f"Session `{entry['session_id_prefix']}` · models: {models_str} · "
            f"{prompt_count} prompt{'s' if prompt_count != 1 else ''} "
            f"({entry['correction_count']} explicit correction{'s' if entry['correction_count'] != 1 else ''}, "
            f"{entry['followup_count']} followup{'s' if entry['followup_count'] != 1 else ''})"
        )

        for prompt in entry["prompts"]:
            classification = prompt["classification"]
            if corrections_only and classification == "INITIAL":
                continue

            lines.append("")
            if classification == "EXPLICIT_CORRECTION":
                lines.append(
                    f"**[{prompt['time']} · {classification} · {prompt['model_fam']}]**"
                    f" (matched: \"{prompt['matched_phrase']}\")"
                )
            else:
                lines.append(f"**[{prompt['time']} · {classification} · {prompt['model_fam']}]**")
            lines.append("~~~text")
            lines.append(_truncate_prompt_text(prompt["text"], truncate_chars))
            lines.append("~~~")

    output = "\n".join(lines) + "\n"

    # Shape-audit line always printed to stderr so it doesn't pollute --out file content.
    print(f"Shape audit: {unrecognized_shape_count} unrecognized user records skipped", file=sys.stderr)

    if out_path:
        try:
            Path(out_path).write_text(output, encoding="utf-8")
            print(f"Wrote output to {out_path}")
        except OSError as exc:
            print(f"user-input: failed to write {out_path}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print(output, end="")


def cmd_duration(args: argparse.Namespace) -> None:
    branch_filter = _branch_filter(args)
    gap_secs: int = (getattr(args, "gap_minutes", None) or 30) * 60
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "duration", roots=roots)
    _print_resolved_scope("duration", scope_label, roots)

    branch_timestamps: dict[str, list[float]] = defaultdict(list)

    for _jsonl, records in session_iter:
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            ts = _parse_ts(rec.get("timestamp"))
            if ts is not None:
                branch_timestamps[branch].append(ts)

    if not branch_timestamps:
        print("No timestamp data found.")
        return

    print(f"{'Branch':<40} {'Span(min)':>10} {'Active(min)':>11} {'Idle(min)':>10} {'Sessions':>9} {'GapMin':>7}")
    print("-" * 95)
    for branch in sorted(branch_timestamps):
        tss = sorted(branch_timestamps[branch])
        if len(tss) < 2:
            continue
        span_secs = tss[-1] - tss[0]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        idle_secs = sum(idle_gaps)
        active_secs = span_secs - idle_secs
        session_count = len(idle_gaps) + 1
        print(
            f"{branch:<40} {span_secs / 60:>10.0f} {active_secs / 60:>11.0f} "
            f"{idle_secs / 60:>10.0f} {session_count:>9} {gap_secs / 60:>7.0f}"
        )


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
    roots = _resolve_cost_roots(args, "subagents")
    multi_root = len(roots) > 1
    this_repo = args.this_repo
    branch_filter = _branch_filter(args)
    since_ts, _since_raw = _parse_since_nd_arg(args, "subagents")

    if multi_root:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = _resolve_project_scope(
        args, "subagents", include_subagents=True, roots=roots
    )
    _print_resolved_scope("subagents", scope_label, roots)

    resolved_roots = [root.resolve() for root in roots] if multi_root else []
    # Resolved-path-sorted, not _root_index_for_path's raw scan-order position
    # — the same physical root must read as the same account-N here as in
    # every other multi-root diagnostic in this file (_build_redact_map,
    # cost's per-row key), regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = _redaction_ordinals(roots) if multi_root else {}
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
        root_idx = _root_index_for_path(jsonl, resolved_roots) if multi_root else None
        records = _dedup_turns_by_request_id(records)
        corpus_spawns += _count_subagent_spawns(records)
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
                    rec_ts = _parse_ts(rec.get("timestamp"))
                    if rec_ts is None or rec_ts < since_ts:
                        continue
                fam = _fam((rec.get("message") or {}).get("model", ""))
                thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
                branch_data[(root_idx, branch)][thread][fam] += 1
            elif rec_type == "user":
                branch = rec.get("gitBranch") or ""
                if branch and not bool(rec.get("isSidechain")):
                    main_thread_branches.add((root_idx, branch))
                if not branch or (branch_filter and branch not in branch_filter):
                    continue
                if since_ts is not None:
                    rec_ts = _parse_ts(rec.get("timestamp"))
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
                    nbytes = len(_content_text(block.get("content", "")).encode())
                    branch_bytes[key][thread] += nbytes
                    tool_name = tool_use_names.get(block.get("tool_use_id") or "", "unknown")
                    if tool_name.startswith("mcp__"):
                        tool_name = _MCP_TOOL_BUCKET_LABEL
                    branch_tool_bytes[key][thread][tool_name] += nbytes

    _warn_if_subagent_format_drift(corpus_spawns, corpus_sidechain_turns)

    if not branch_data and not branch_bytes:
        print("No data found.")
        return

    def _branch_label(key: tuple[int | None, str]) -> str:
        root_idx, branch = key
        return (
            _root_scoped_display_label(
                "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map,
                disclose=this_repo and key in main_thread_branches,
            )
            if root_idx is not None
            else _sanitize_table_cell(branch)
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
                    print(f"{row_label:<40} {thread:<10} {_sanitize_table_cell(tool_name):<20} {nbytes:>18,}")


# subagents' tool-result byte grouping bucket for every mcp__<server>__<tool>
# tool name — an MCP server name is a per-account integration identifier, so
# every MCP tool call collapses into this one row instead of one row per server.
_MCP_TOOL_BUCKET_LABEL = "mcp__*"

# subagent-mix's model-mix table bucket for a dispatch whose meta.json carries
# no "model" key at all (no explicit model was requested).
_UNREQUESTED_MODEL_LABEL = "(none)"


def cmd_judgment_pair(args: argparse.Namespace) -> None:
    """Emit (review-skill output, user response) pairs from sessions containing review invocations.

    For each matching Skill invocation in a session:
    - REVIEW OUTPUT: the last main-thread assistant turn with non-empty text in the
      window between the invocation and the next fresh user prompt (or next matching
      invocation, whichever comes first).
    - USER RESPONSE: the first fresh user prompt text after that window closes.

    Uses _is_fresh_user_prompt() to skip tool-result turns, isMeta injections,
    and isCompactSummary injections when locating the user response.

    --branches filters on the invocation record's own gitBranch, not a single
    session-wide branch — a session whose branch changes between the
    invocation and the user response is filtered by where the invocation
    itself happened.
    """
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "judgment-pair", roots=roots)

    since_ts, until_epoch = _parse_absolute_window_args(args, "judgment-pair")

    skills_arg: str = getattr(args, "skills", None) or ",".join(REVIEW_SKILLS)
    skill_set: set[str] = {s for s in skills_arg.split(",") if s}
    truncate_chars: int = getattr(args, "truncate_chars", 1000) or 1000
    out_path: str | None = getattr(args, "out", None) or None

    output_blocks: list[str] = []

    for jsonl, records in session_iter:
        proj_label = _derive_proj_label(jsonl)
        session_id_prefix = jsonl.stem[:8]

        for rec_idx, rec in enumerate(records):
            line_no = rec_idx + 1  # 1-based line number for output

            # Detect matching skill invocation: main-thread assistant with Skill tool_use
            # whose input.skill is in the target set.
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            content_blocks = (rec.get("message") or {}).get("content") or []
            inv_skill: str | None = None
            for block in content_blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Skill":
                    continue
                skill_name = (block.get("input") or {}).get("skill") or ""
                if skill_name in skill_set:
                    inv_skill = skill_name
                    break
            if inv_skill is None:
                continue

            invocation_branch = rec.get("gitBranch") or ""
            if branch_filter and invocation_branch not in branch_filter:
                continue

            inv_ts_str: str | None = rec.get("timestamp")
            inv_ts_epoch: float | None = _parse_ts(inv_ts_str)

            # Apply date filter to invocation timestamp.
            if since_ts is not None or until_epoch is not None:
                if inv_ts_epoch is None:
                    continue
                if since_ts is not None and inv_ts_epoch < since_ts:
                    continue
                if until_epoch is not None and inv_ts_epoch >= until_epoch:
                    continue

            # Scan forward to find window_end: the minimum of
            #   (a) the index of the next fresh user prompt, and
            #   (b) the index of the next matching skill invocation after this one.
            # window_end is exclusive (records[rec_idx+1 : window_end]).
            window_end = len(records)
            found_boundary = False
            for scan_idx in range(rec_idx + 1, len(records)):
                scan_rec = records[scan_idx]
                # Bound (a): next fresh user prompt closes the window.
                if _is_fresh_user_prompt(scan_rec):
                    window_end = scan_idx
                    found_boundary = True
                    break
                # Bound (b): next matching skill invocation closes the window.
                if scan_rec.get("type") == "assistant" and not bool(scan_rec.get("isSidechain")):
                    scan_content = (scan_rec.get("message") or {}).get("content") or []
                    for scan_block in scan_content:
                        if not isinstance(scan_block, dict) or scan_block.get("type") != "tool_use":
                            continue
                        if scan_block.get("name") != "Skill":
                            continue
                        scan_skill = (scan_block.get("input") or {}).get("skill") or ""
                        if scan_skill in skill_set:
                            window_end = scan_idx
                            found_boundary = True
                            break
                    if found_boundary:
                        break

            # REVIEW OUTPUT: last main-thread assistant turn with non-empty text in the window.
            review_text = ""
            for window_rec in records[rec_idx + 1 : window_end]:
                if window_rec.get("type") != "assistant" or bool(window_rec.get("isSidechain")):
                    continue
                candidate_text = _content_text(
                    (window_rec.get("message") or {}).get("content", "")
                ).strip()
                if candidate_text:
                    review_text = candidate_text

            # USER RESPONSE: the fresh user prompt at window_end (if it is one).
            if window_end < len(records) and _is_fresh_user_prompt(records[window_end]):
                user_response_text = _content_text(
                    (records[window_end].get("message") or {}).get("content", "")
                ).strip()
            else:
                user_response_text = "(no user response — end of session)"

            # Format the output block.
            date_label = _fmt_date(inv_ts_epoch) if inv_ts_epoch is not None else "?"
            if review_text:
                review_display = review_text[:truncate_chars] + "…" if len(review_text) > truncate_chars else review_text
            else:
                review_display = "(no review text found)"

            block = (
                f"### {proj_label} · {session_id_prefix} · {date_label}\n"
                f"Skill: {inv_skill}  branch={invocation_branch or '?'}  (line {line_no})\n"
                f"\n"
                f"--- REVIEW OUTPUT (truncated to {truncate_chars} chars) ---\n"
                f"{review_display}\n"
                f"\n"
                f"--- USER RESPONSE ---\n"
                f"{user_response_text}\n"
                f"---"
            )
            output_blocks.append(block)

    if not output_blocks:
        _print_resolved_scope("judgment-pair", scope_label, roots)
        print("No judgment pairs found.")
        return

    output_text = "\n\n".join(output_blocks)

    if out_path:
        # Nothing goes to stdout in this branch — --out means the caller wants
        # only the file written. The scope header is still prepended to the
        # file's content so a saved/curated file stays self-documenting about
        # its scope even if pasted elsewhere without the terminal output.
        header = _resolved_scope_header("judgment-pair", scope_label, roots)
        Path(out_path).write_text(header + "\n" + output_text + "\n")
    else:
        _print_resolved_scope("judgment-pair", scope_label, roots)
        print(output_text)


def _normalize_skill_name(raw: str) -> str:
    """Strip a directory qualifier from a transcript skill name.

    input["skill"] is a display label, not a stable identifier. Claude Code
    qualifies project-scoped skills by the directory they were found in, so the
    same skill is recorded under several spellings depending on the invoking
    session's working directory:

        plan-it
        claude:plan-it
        .claude/worktrees/some-branch/claude:plan-it
        claude/.claude/worktrees/some-branch/nested/claude:plan-it

    Collapsing these spellings stops one skill from splitting across several rows.
    This is row-hygiene, not the security control: within a repo-scoped read the
    only path fragment that can appear is *this* repo's own worktree branch (its
    project dirs are the only ones read), which is public. Cross-project
    minimization is enforced upstream by _repo_scoped_project_slugs, not here.

    A remaining ``plugin:skill`` or ``dir:skill`` prefix is deliberately kept: it
    carries no path, and dropping it would need this script to hardcode the stow
    package name, which varies per installation. Resolving such a prefix to a
    skill body is the reviewer agent's job, not the extractor's.
    """
    return raw.rsplit("/", 1)[-1]


def cmd_skill_invocation(args: argparse.Namespace) -> None:
    """Per-skill invocation-source tally across the full corpus.

    Three invocation buckets are counted per skill:
    - top-level: Skill tool_use on a main-thread assistant turn with no attributionSkill.
    - routed: Skill tool_use on a main-thread assistant turn where attributionSkill is
      non-empty (the call was fired while another skill's body was active).
    - user-slash: user record whose message content contains a
      <command-name>/skillname</command-name> tag (the /slash invocation path, which
      injects the skill body directly without a Skill tool_use).

    Identifies routed-only candidates (zero top-level or slash) and slash-only candidates
    (zero top-level or routed) for skill-description budget analysis.

    Two consumers ask different questions of this data, so subagent turns are opt-in:

    - Skill-description budget analysis asks whether a skill's *description* draws
      auto-triggers on the main thread. Sidechain turns are noise there, so they are
      excluded by default.
    - Procedural-fidelity review asks which procedures a branch's work committed to.
      A skill invoked inside a spawned agent binds exactly as much as a main-thread
      one, so --include-subagents folds those in and adds a thread column keeping the
      two distinguishable rather than silently merged.

    --branches scopes to named gitBranch values; --projects scopes to project dirs and
    defaults to every project on the machine, which is rarely what a branch-scoped
    caller wants (branch names are not unique across repos).
    """
    branch_filter = _branch_filter(args)
    include_subagents = bool(getattr(args, "include_subagents", False))

    # OUTPUT INVARIANT — provenance, not shape. This output is routinely quoted
    # into public PR descriptions. Its safety rests on WHAT records are read, not
    # on scrubbing names after the fact: skill names are user-defined strings and
    # can themselves be private-project identifiers (a plugin namespace with no
    # path separator at all). So the control is to scope the read to this repo's
    # own project dirs (_repo_scoped_project_slugs) — the default when --projects
    # is unset. An explicit --projects is an escape hatch for corpus analysis;
    # the caller then owns that the output is no longer publish-safe.
    #
    # Supporting rules: only input["skill"] is extracted (never input["args"],
    # which holds absolute paths even for in-scope sessions); and
    # _normalize_skill_name collapses this repo's own worktree-qualified spellings
    # for row-hygiene. Neither is the security boundary — scoping is.
    roots = _resolve_scan_roots(args)

    projects_arg = getattr(args, "projects", None)
    if projects_arg:
        if len(roots) > 1:
            session_iter = _iter_glob_scoped_sessions(roots, projects_arg, include_subagents)
        else:
            session_iter = iter_sessions(roots[0], projects_arg, include_subagents=include_subagents)
    else:
        session_iter = _iter_scoped_sessions(_repo_scoped_project_slugs(), include_subagents, roots=roots)

    # Counters are keyed by (skill, thread). Without --include-subagents every
    # thread is "main", which keeps the default output shape unchanged.
    skill_top: dict[tuple[str, str], int] = defaultdict(int)     # -> top-level count
    skill_routed: dict[tuple[str, str], int] = defaultdict(int)  # -> routed count
    skill_slash: dict[tuple[str, str], int] = defaultdict(int)   # -> user-slash count
    routed_pairs: dict[tuple[str, str], int] = defaultdict(int)  # (parent, child) -> count

    for _jsonl, records in session_iter:
        for rec in records:
            sidechain = bool(rec.get("isSidechain"))
            if sidechain and not include_subagents:
                continue
            # Unfiltered runs must still count records that carry no gitBranch,
            # so the branch test applies only when a filter was requested.
            if branch_filter and (rec.get("gitBranch") or "") not in branch_filter:
                continue
            thread = "sidechain" if sidechain else "main"
            rtype = rec.get("type")
            if rtype == "assistant":
                for block in ((rec.get("message") or {}).get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") != "Skill":
                        continue
                    skill = _normalize_skill_name((block.get("input") or {}).get("skill") or "")
                    if not skill:
                        continue
                    attribution = _normalize_skill_name(rec.get("attributionSkill") or "")
                    if attribution:
                        skill_routed[(skill, thread)] += 1
                        routed_pairs[(attribution, skill)] += 1
                    else:
                        skill_top[(skill, thread)] += 1
            elif rtype == "user":
                content_raw = (rec.get("message") or {}).get("content", "")
                content_str = content_raw if isinstance(content_raw, str) else _content_text(content_raw)
                for m in re.finditer(r"<command-name>/([^<]+)</command-name>", content_str):
                    skill_slash[(_normalize_skill_name(m.group(1)), thread)] += 1

    keyed = set(skill_top) | set(skill_routed) | set(skill_slash)
    all_skills: set[str] = {skill for skill, _thread in keyed}

    scope_parts = [
        "explicit --projects (not repo-scoped)" if projects_arg else "this repo",
        "main+subagents" if include_subagents else "main thread",
    ]
    if branch_filter:
        scope_parts.append(f"branches: {','.join(sorted(branch_filter))}")
    # Printed above the zero-match return, not after it: "found nothing" is only
    # interpretable alongside the corpus that was searched.
    _print_resolved_scope("skill-invocation", "; ".join(scope_parts), roots)

    if not all_skills:
        print("No skill invocations found.")
        return

    def _thread_total(s: str, thread: str) -> int:
        return skill_top[(s, thread)] + skill_routed[(s, thread)] + skill_slash[(s, thread)]

    # Sort by total descending, then alphabetically for ties.
    def _skill_total(s: str) -> int:
        return sum(_thread_total(s, thread) for thread in ("main", "sidechain"))

    sorted_skills = sorted(all_skills, key=lambda s: (-_skill_total(s), s))

    if include_subagents:
        header = (
            f"{'skill':<40} {'thread':<10} {'top-level':>10}  "
            f"{'routed':>6}  {'user-slash':>10}  {'total':>7}"
        )
    else:
        header = f"{'skill':<40} {'top-level':>10}  {'routed':>6}  {'user-slash':>10}  {'total':>7}"
    print(header)
    print("-" * len(header))

    for skill in sorted_skills:
        # The skill label repeats on every row rather than blanking on
        # continuation rows: consumers grep individual lines out of this table,
        # and a blanked label makes a grepped line unattributable.
        threads = ("main", "sidechain") if include_subagents else ("main",)
        for thread in threads:
            if include_subagents and not _thread_total(skill, thread):
                continue
            top = skill_top[(skill, thread)]
            routed = skill_routed[(skill, thread)]
            slash = skill_slash[(skill, thread)]
            total = top + routed + slash
            if include_subagents:
                print(
                    f"{skill:<40} {thread:<10} {top:>10}  "
                    f"{routed:>6}  {slash:>10}  {total:>7}"
                )
            else:
                print(f"{skill:<40} {top:>10}  {routed:>6}  {slash:>10}  {total:>7}")

    if routed_pairs:
        print("\nROUTED PAIRS (parent -> child : count)")
        for (parent, child), count in sorted(routed_pairs.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {parent} -> {child} : {count}")

    # Classification summary: load-bearing, routed-only, slash-only. Counts
    # aggregate across threads — the classification answers a per-skill
    # question ("is this description load-bearing?"), not a per-thread one.
    def _agg(counter: dict[tuple[str, str], int], s: str) -> int:
        return sum(counter[(s, thread)] for thread in ("main", "sidechain"))

    load_bearing = [s for s in sorted_skills if _agg(skill_top, s) > 0 or _agg(skill_slash, s) > 0]
    routed_only = [
        s for s in sorted_skills
        if _agg(skill_top, s) == 0 and _agg(skill_slash, s) == 0 and _agg(skill_routed, s) > 0
    ]
    slash_only = [
        s for s in sorted_skills
        if _agg(skill_top, s) == 0 and _agg(skill_routed, s) == 0 and _agg(skill_slash, s) > 0
    ]

    print("\nCLASSIFICATION SUMMARY")
    print("  Load-bearing (any top-level or slash invocations):")
    if load_bearing:
        for s in load_bearing:
            print(f"    {s} ({_agg(skill_top, s)} top, {_agg(skill_slash, s)} slash)")
    else:
        print("    (none)")

    print("  Routed-only candidates (zero top-level and zero slash — name-only eligible):")
    if routed_only:
        for s in routed_only:
            print(f"    {s} (0 top, {_agg(skill_routed, s)} routed, 0 slash)")
    else:
        print("    (none)")

    print("  Slash-only candidates (zero top, zero routed — disable-model-invocation eligible):")
    if slash_only:
        for s in slash_only:
            print(f"    {s} (0 top, 0 routed, {_agg(skill_slash, s)} slash)")
    else:
        print("    (none)")


def cmd_subagent_mix(args: argparse.Namespace) -> None:
    """Subagent_type spawn counts per branch, plus a second, agentType-keyed
    table of each type's model mix: Runs (dispatches with a readable
    meta.json + sibling .jsonl — a dangling pair is excluded from this count
    and reported separately under Dangling), Declared (frontmatter `model:`
    from the dispatch's own root's agents/<agentType>.md, or "built-in" with
    no on-disk file), Requested (meta.json's own "model" key, "(none)" when
    absent), and Observed (the modal real model ID across the dispatch's own
    sidechain, via _fam; "mixed" when two distinct real IDs appear), plus
    Actual $ (and, when --reprice-as is given, Counterfactual $ and Delta).

    --since limits both tables to records timestamped on or after the window
    start. --since-date/--until-date instead bound only the Actual $ /
    Counterfactual $ columns, and do so per sidechain assistant record (not
    per dispatch) — a dispatch straddling the window edge must not attribute
    its whole sidechain's dollars to the window just because it started
    inside it. --reprice-as re-prices that same in-window usage at an
    alternate model ID (validated against _MODEL_BASE_INPUT_RATES's keys),
    adding the Counterfactual $ and Delta (Actual − Counterfactual) columns.
    --config-dir (repeatable) scans additional Claude Code config
    directories the same way cost does.
    Under more than one root, both branch names and subagent_type values
    are redacted (_root_scoped_display_label) — subagent_type can name a
    project-scoped custom agent definition, the same disclosure risk
    gitBranch carries.
    Both tables aggregate on the raw (root, branch) / (root, subagent_type)
    pair, not the printed label — the redacted or disclosed label is
    computed lazily at print time (idempotently, so a value requested by
    both tables renders the same label each time), so two accounts'
    same-named agentType (or two raw values differing only in stripped
    control bytes) never merge into one row.
    --per-session is refused outright under multi-root, since it would
    otherwise join a foreign account's own session-id prefix to its branch
    name.
    Under --this-repo, every branch prints raw (account-<K>/<branch>) with
    no attestation gate: this function excludes isSidechain records before
    ever reading gitBranch, unlike cmd_subagents, so a subagent's own
    gitBranch never reaches this table.
    A subagent_type prints raw only when it is tracked in this repo's own
    agents/ directory or is a Claude Code built-in
    (_repo_tracked_agent_type_names); every other value stays opaque.
    --this-repo's disclosure is repo-agnostic: it applies to whichever repo
    --this-repo resolves to for the invoking CWD, not specifically to
    claude-config.
    """
    roots = _resolve_cost_roots(args, "subagent-mix")
    multi_root = len(roots) > 1
    this_repo = args.this_repo
    branch_filter = _branch_filter(args)
    per_session: bool = bool(getattr(args, "per_session", False))

    if multi_root and per_session:
        print(
            "subagent-mix: --per-session is refused when more than one root is in"
            " scope (--config-dir was given) — a per-session row would join a"
            " foreign account's own session-id prefix to its branch name; drop"
            " --per-session or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    reprice_as: str | None = getattr(args, "reprice_as", None) or None
    if reprice_as is not None and reprice_as not in _MODEL_BASE_INPUT_RATES:
        valid = ", ".join(sorted(_MODEL_BASE_INPUT_RATES))
        print(
            f"subagent-mix: --reprice-as: unknown model ID {reprice_as!r}; valid values: {valid}",
            file=sys.stderr,
        )
        sys.exit(1)

    if multi_root:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, _since_raw = _parse_since_nd_arg(args, "subagent-mix")

    # Bounds the Actual $ / Counterfactual $ columns only, per sidechain
    # assistant record (see _dispatch_usage_summary) -- independent of
    # since_ts above, which keeps its existing dispatch-level scope over
    # every other column in this table.
    dollar_since_ts, dollar_until_ts = _parse_absolute_window_args(
        args, "subagent-mix", since_attr="since_date", until_attr="until_date"
    )

    # Read once, matching cost's own "never read the clock inside the
    # per-record loop" rationale -- kept as a plain wall-clock read here
    # (rather than cost's separate entry/report split) since no existing or
    # new test in this file asserts on stale-pricing output for subagent-mix.
    today = datetime.now(UTC).date()
    total_unpriced_turns = 0
    total_unpriced_tokens = 0
    all_stale_models: set[str] = set()

    session_iter, scope_label = _resolve_project_scope(args, "subagent-mix", roots=roots)
    _print_resolved_scope("subagent-mix", scope_label, roots)

    resolved_roots = [root.resolve() for root in roots] if multi_root else []
    # Resolved-path-sorted, not _root_index_for_path's raw scan-order position
    # — the same physical root must read as the same account-N here as in
    # every other multi-root diagnostic in this file (_build_redact_map,
    # cost's per-row key), regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = _redaction_ordinals(roots) if multi_root else {}
    branch_redact_map: dict[tuple[int, str], str] = {}
    # subagent_type redact map is separate from branch_redact_map so the two
    # kinds' per-account counters (account-<K>/branch-<N> vs.
    # account-<K>/agent-type-<N>) never share a numbering sequence.
    subagent_type_redact_map: dict[tuple[int, str], str] = {}
    # Each root's own agents/ directory, so a dispatch's Declared pin is read
    # from the account it actually came from, not this process's own
    # config_dir() — index 0 is always this process's own root (roots[0]),
    # matching root_idx's None-under-single-root convention below.
    agent_dirs = [root.parent / "agents" for root in roots]

    # Keyed on (root_index_or_None, raw gitBranch, session_suffix_or_None) —
    # raw, never the (possibly-sanitized) display label, so two raw branch
    # values that differ only in stripped control bytes stay distinct rows
    # instead of silently merging their spawn/session counts. session_suffix
    # is jsonl.stem[:8] under --per-session (always root_idx=None, since
    # multi-root refuses --per-session), and None when sessions on the same
    # branch aggregate into one row. The printed label (_mix_branch_label,
    # below) translates root_idx through redact_ordinals only at print time.
    data: dict[tuple[int | None, str, str | None], dict] = defaultdict(
        lambda: {"sessions": 0, "spawns": defaultdict(int), "skills": defaultdict(int)}
    )
    # (root_index_or_None, raw subagent_type) -> model-mix row. Only created
    # for a type that has at least one meta.json match (even a dangling
    # one) — a dispatch with no matching meta.json at all is excluded
    # entirely, matching cmd_reviewer_yield's own precedent for the same
    # join. Keyed on the raw tuple (not the display label) for the same
    # reason as `data` above: root_idx alone already root-scopes two
    # accounts' same-named agentType apart, with no dependency on the label
    # string encoding that uniqueness.
    model_mix: dict[tuple[int | None, str], dict] = defaultdict(lambda: {
        "runs": 0,
        "dangling": 0,
        "requested": defaultdict(int),
        "observed": defaultdict(int),
        "declared_seen": set(),
        "actual_dollars": 0.0,
        "counterfactual_dollars": 0.0,
    })
    declared_pin_cache: dict[tuple[Path, str], str] = {}
    total_meta_read_errors = 0

    for jsonl, records in session_iter:
        root_idx = _root_index_for_path(jsonl, resolved_roots) if multi_root else None
        agents_dir = agent_dirs[root_idx if root_idx is not None else 0]
        dispatch_index, session_meta_read_errors = _index_subagent_dispatches(jsonl)
        total_meta_read_errors += session_meta_read_errors
        session_data: dict[str, dict] = defaultdict(
            lambda: {"spawns": defaultdict(int), "skills": defaultdict(int)}
        )
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input") or {}
                if name in _SPAWN_TOOL_NAMES:
                    stype = inp.get("subagent_type") or _UNKNOWN_SUBAGENT_TYPE
                    session_data[branch]["spawns"][stype] += 1

                    paired = dispatch_index.get(block.get("id") or "")
                    if paired is not None:
                        paired_jsonl, requested_model = paired
                        row = model_mix[(root_idx, stype)]
                        # _declared_pin reads from the on-disk agent file, so it
                        # needs the real subagent_type (stype), never the
                        # (possibly-redacted) display label built at print time.
                        row["declared_seen"].add(_declared_pin(stype, agents_dir, declared_pin_cache))
                        (
                            observed, actual_dollars, _dollars_by_class, counterfactual_dollars,
                            dispatch_unpriced_turns, dispatch_unpriced_tokens, dispatch_stale_models,
                        ) = _dispatch_usage_summary(
                            paired_jsonl, dollar_since_ts, dollar_until_ts, reprice_as, today
                        )
                        total_unpriced_turns += dispatch_unpriced_turns
                        total_unpriced_tokens += dispatch_unpriced_tokens
                        all_stale_models |= dispatch_stale_models
                        if observed is None:
                            row["dangling"] += 1
                        else:
                            row["runs"] += 1
                            row["requested"][requested_model or _UNREQUESTED_MODEL_LABEL] += 1
                            row["observed"][observed] += 1
                            row["actual_dollars"] += actual_dollars
                            if reprice_as:
                                row["counterfactual_dollars"] += counterfactual_dollars or 0.0
                elif name == "Skill":
                    skill = inp.get("skill") or ""
                    if skill in REVIEW_SKILLS:
                        session_data[branch]["skills"][skill] += 1

        for branch, sd in session_data.items():
            key = (root_idx, branch, jsonl.stem[:8] if per_session else None)
            d = data[key]
            d["sessions"] += 1
            for stype, cnt in sd["spawns"].items():
                d["spawns"][stype] += cnt
            for skill, cnt in sd["skills"].items():
                d["skills"][skill] += cnt

    if not data:
        print("No data found.")
        return

    def _mix_branch_label(key: tuple[int | None, str, str | None]) -> str:
        root_idx, branch, session_suffix = key
        label = (
            _root_scoped_display_label(
                "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map,
                disclose=this_repo,
            )
            if root_idx is not None
            else _sanitize_table_cell(branch)
        )
        return f"{label} [{session_suffix}]" if session_suffix is not None else label

    def _stype_label(key: tuple[int | None, str]) -> str:
        root_idx, stype = key
        return (
            _root_scoped_display_label(
                "agent-type", redact_ordinals[resolved_roots[root_idx]], stype, subagent_type_redact_map,
                disclose=this_repo and stype in _repo_tracked_agent_type_names(),
            )
            if root_idx is not None
            else _sanitize_table_cell(stype)
        )

    print(f"{'Branch':<45} {'Sess':>5} {'Spawns':>7} {'CR':>3} {'PR':>3} {'RR':>3}  Top subagent types")
    print("-" * 120)
    for key in sorted(data):
        d = data[key]
        root_idx = key[0]
        branch_label = _mix_branch_label(key)
        spawns_total = sum(d["spawns"].values())
        top = sorted(d["spawns"].items(), key=lambda kv: (-kv[1], kv[0]))
        top_str = ", ".join(f"{_stype_label((root_idx, t))}({n})" for t, n in top[:5]) or "—"
        print(
            f"{branch_label:<45} {d['sessions']:>5} {spawns_total:>7} "
            f"{d['skills'].get('code-review', 0):>3} {d['skills'].get('plan-review', 0):>3} "
            f"{d['skills'].get('ready-for-review', 0):>3}  {top_str}"
        )

    if model_mix:
        header = f"{'AgentType':<28} {'Runs':>5} {'Dangling':>9}  {'Declared':<10} {'Actual$':>12}"
        if reprice_as:
            header += f" {'Counterfactual$':>18} {'Delta':>12}"
        header += f" {'Requested':<30} Observed"
        print(f"\n{header}")
        print("-" * len(header))
        for mix_key in sorted(model_mix):
            row = model_mix[mix_key]
            stype_label = _stype_label(mix_key)
            declared = "/".join(sorted(row["declared_seen"])) or _DECLARED_PIN_BUILT_IN
            requested_str = ", ".join(
                f"{k}({v})" for k, v in sorted(row["requested"].items(), key=lambda kv: (-kv[1], kv[0]))
            ) or "—"
            observed_str = ", ".join(
                f"{k}({v})" for k, v in sorted(row["observed"].items(), key=lambda kv: (-kv[1], kv[0]))
            ) or "—"
            line = (
                f"{stype_label:<28} {row['runs']:>5} {row['dangling']:>9}  {declared:<10} "
                f"{_fmt_usd(row['actual_dollars']):>12}"
            )
            if reprice_as:
                delta = row["actual_dollars"] - row["counterfactual_dollars"]
                line += f" {_fmt_usd(row['counterfactual_dollars']):>18} {_fmt_usd(delta):>12}"
            line += f" {requested_str:<30} {observed_str}"
            print(line)
        # Matches cost's own "(N unpriced turns / M tokens excluded from
        # priced spend)" convention verbatim -- an unknown model ID would
        # otherwise silently read as a genuinely zero-cost dispatch.
        if total_unpriced_turns:
            print(
                f"  ({total_unpriced_turns:,} unpriced turns / {total_unpriced_tokens:,}"
                " tokens excluded from priced spend)"
            )
        # Matches cost's own STALE PRICING banner (_MODEL_RATE_EXPIRES),
        # simplified to the model list -- this table has no single "the
        # figures below" scope to point a successor-rate hint at.
        if all_stale_models:
            print(
                "STALE PRICING — today is past the re-verify-by date for: "
                + ", ".join(sorted(all_stale_models))
                + f". Re-check rates at {_PRICING_SOURCE_URL} before publishing this table's dollar figures."
            )
    # Printed even when model_mix is empty (every dispatch's meta.json was
    # malformed) -- mirrors cmd_reviewer_yield's identical diagnostic, which
    # prints on its own early-return path for the same reason.
    if total_meta_read_errors:
        print(f"\n  ({total_meta_read_errors:,} meta.json files failed to parse, excluded)")


def _spawn_counts_by_agent_type(
    session_iter, branch_filter: set[str] | None,
) -> dict[str, int]:
    """Raw (undisclosed) subagent_type spawn counts across session_iter,
    for cost-counts.

    Main-thread dispatches only: excludes isSidechain records before ever
    reading gitBranch, matching cmd_subagent_mix's own exclusion order. So a
    subagent's own gitBranch never reaches this count. branch_filter matches
    a record's own literal gitBranch, not review_rounds' carry-forward
    attribution. For a main-thread record the two agree in every case that
    matters here, since cost._attributed_branch's own carry-forward logic
    exists only to resolve a worktree-agent-* sidechain record.

    No disclosure gate applied here -- see _partition_spawn_counts_by_disclosure
    for the allowlist partition this raw count feeds.
    """
    counts: dict[str, int] = defaultdict(int)
    for _jsonl, records in session_iter:
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            branch = rec.get("gitBranch") or ""
            if branch_filter is not None and branch not in branch_filter:
                continue
            for block in (rec.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in _SPAWN_TOOL_NAMES:
                    continue
                stype = (block.get("input") or {}).get("subagent_type") or _UNKNOWN_SUBAGENT_TYPE
                counts[stype] += 1
    return dict(counts)


_AGENT_FRONTMATTER_MODEL_RE = re.compile(r"(?m)^model:\s*(\S+)\s*$")


def _agent_frontmatter_model(agent_file_text: str) -> str | None:
    """Extract the `model:` frontmatter value from one agent file's raw text.

    Scoped to the leading YAML block (between the first pair of `---` lines)
    so a `model:` mention in the agent's prose body is never matched. Returns
    None when the text has no frontmatter block or the block has no `model:`
    key — the caller renders that as "built-in".
    """
    if not agent_file_text.startswith("---"):
        return None
    end = agent_file_text.find("\n---", 3)
    if end == -1:
        return None
    match = _AGENT_FRONTMATTER_MODEL_RE.search(agent_file_text[3:end])
    return match.group(1) if match else None


_DECLARED_PIN_BUILT_IN = "built-in"

# Fallback subagent_type for a spawn tool_use whose input carries no
# subagent_type field -- shared between cmd_subagent_mix and
# _spawn_counts_by_agent_type so the two never disagree on which raw string
# means "input missing this field."
_UNKNOWN_SUBAGENT_TYPE = "unknown"

# subagent_type values are harness-generated identifiers (e.g. "staff-sdet",
# "general-purpose") -- never containing "/" or "..". _declared_pin enforces
# this shape before building a filesystem path from one, since under
# --config-dir that value can originate from a scanned foreign root's own
# transcript data, not just this process's own dispatches.
_AGENT_TYPE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _declared_pin(
    agent_type: str, agents_dir: Path, declared_pin_cache: dict[tuple[Path, str], str]
) -> str:
    """Declared model pin for one subagent_type, from agents_dir/<agent_type>.md
    frontmatter — agents_dir is the *dispatch's own* root's agents/ directory
    (not necessarily this process's own config_dir()), since under
    --config-dir a dispatch's declared pin must be read from the account it
    actually came from. Cached per (agents_dir, agent_type), since the same
    agent_type name can resolve to a different on-disk file under a
    different root. "built-in" when no on-disk agent file exists (see
    _BUILT_IN_AGENT_TYPES — none of those three carry one), the file has no
    `model:` frontmatter — Claude Code's own default, not a pin this repo
    can assert on — or agent_type fails the on-disk agent-file naming
    allowlist (agent_type is transcript-sourced data; without this guard, an
    absolute-path or `../`-laden value would build a path outside
    agents_dir via Path.__truediv__'s os.path.join semantics).
    """
    key = (agents_dir, agent_type)
    if key in declared_pin_cache:
        return declared_pin_cache[key]
    if not _AGENT_TYPE_NAME_RE.fullmatch(agent_type):
        pin = _DECLARED_PIN_BUILT_IN
    else:
        agent_file = agents_dir / f"{agent_type}.md"
        try:
            text = agent_file.read_text()
        except OSError:
            pin = _DECLARED_PIN_BUILT_IN
        else:
            pin = _agent_frontmatter_model(text) or _DECLARED_PIN_BUILT_IN
    declared_pin_cache[key] = pin
    return pin


# The one folded row a subagent_type this repo's own agents/ tree does not
# track (and is not a Claude Code built-in) collapses into -- an em dash,
# not a hyphen, and parenthesized so it reads unambiguously as a caption,
# never as an agent name.
_WITHHELD_AGENT_TYPE_LABEL = "(withheld — untracked agent type)"


def _partition_spawn_counts_by_disclosure(raw_counts: dict[str, int]) -> list[tuple[str, int]]:
    """Partition raw subagent_type spawn counts into disclosed rows plus one
    folded withheld row, for cost-counts.

    The _repo_tracked_agent_type_names() allowlist gate is applied here
    unconditionally, unlike _stype_label's `disclose=this_repo and stype in
    _repo_tracked_agent_type_names()` (reached only under multi-root):
    _repo_tracked_agent_type_names() resolves its allowlist from
    _REPO_AGENT_DEFINITIONS_DIR, this installed toolkit's own agents/ tree --
    never a consumer repo's own tracked agents/ directory (see that
    function's own docstring). So a stow consumer's own real,
    project-tracked agent types are exactly as unresolvable here as a
    genuinely ad hoc dispatch. Both fold into the same withheld row
    regardless of whose branch cost-counts is scoring.

    Disclosed rows sort by (-count, name), matching cmd_subagent_mix's own
    `top` ordering. The withheld row -- when its folded total is nonzero --
    is always appended last, never merged into that sort, so its count can
    never place it ahead of a named row and be mistaken for one.
    """
    tracked = _repo_tracked_agent_type_names()
    disclosed = sorted(
        ((stype, count) for stype, count in raw_counts.items() if stype in tracked),
        key=lambda kv: (-kv[1], kv[0]),
    )
    withheld_total = sum(count for stype, count in raw_counts.items() if stype not in tracked)
    rows = list(disclosed)
    if withheld_total:
        rows.append((_WITHHELD_AGENT_TYPE_LABEL, withheld_total))
    return rows


_COST_COUNTS_ROUNDS_CAPTION = (
    "Each invocation of a review skill is one round, whether or not it produced findings."
    " Counts reflect this section's last render; a review round that ran afterward may not"
    " be included yet."
)

_COST_COUNTS_SPAWNS_CAPTION = (
    "Counts main-thread dispatches only; an agent spawned from inside another agent is not"
    " counted."
)


def cmd_cost_counts(args: argparse.Namespace) -> None:
    """Per-branch review-round and subagent-spawn counts, for embedding in a
    public PR body -- counts only, no dollar attribution anywhere.

    Requires --this-repo and --branches; always resolves to a single root,
    `[config_dir() / "projects"]`, the active account alone. Prints exactly
    two GFM subsections, `### Review rounds` and `### Subagent spawns`, and
    nothing else. See docs/transcript-analysis.md's `cost-counts` section
    for the branch-attribution-model distinction, the rounds/spawns
    zero-count rendering asymmetry, and the disclosure-allowlist assertion
    backstop.
    """
    this_repo = bool(getattr(args, "this_repo", False))
    projects_arg = getattr(args, "projects", None)
    if not this_repo or projects_arg not in (None, "*"):
        print(
            "cost-counts: requires --this-repo and refuses any --projects scope"
            " (including the default glob) — see docs/transcript-analysis.md",
            file=sys.stderr,
        )
        sys.exit(2)

    branches_arg: str | None = getattr(args, "branches", None) or None
    if not branches_arg:
        print(
            "cost-counts: --branches is required — a corpus-wide count is never a"
            " legitimate PR-body figure",
            file=sys.stderr,
        )
        sys.exit(2)
    branch_filter = _branch_filter(args)

    roots = [config_dir() / "projects"]

    session_iter, _scope_label = _resolve_project_scope(args, "cost-counts", roots=roots)
    # Fully materialized (unlike every other cmd_* here, which streams one session at a
    # time): cost-counts is --this-repo-only, bounding this to one account's one-repo
    # session history, small enough to hold in memory at once.
    sessions = list(session_iter)

    round_counts = compute_review_round_counts(sessions, branch_filter=branch_filter)
    spawn_rows = _partition_spawn_counts_by_disclosure(
        _spawn_counts_by_agent_type(sessions, branch_filter)
    )

    print("### Review rounds\n")
    print(f"{_COST_COUNTS_ROUNDS_CAPTION}\n")
    print("| Skill | Rounds |")
    print("|---|---|")
    total_rounds = 0
    for skill in REVIEW_SKILLS:
        n = round_counts[skill]
        total_rounds += n
        print(f"| {_sanitize_table_cell(skill)} | {n} |")
    print(f"| **total** | **{total_rounds}** |")

    print("\n### Subagent spawns\n")
    print(f"{_COST_COUNTS_SPAWNS_CAPTION}\n")
    if not spawn_rows:
        print("No subagent spawns found in scope.")
    else:
        tracked = _repo_tracked_agent_type_names()
        print("| Agent type | Spawns |")
        print("|---|---|")
        total_spawns = 0
        for row_index, (label, count) in enumerate(spawn_rows):
            if not (label == _WITHHELD_AGENT_TYPE_LABEL or label in tracked):
                raise AssertionError(
                    f"cost-counts: spawn row {row_index} is neither the withheld label nor a"
                    " repo-tracked agent type. Refusing to print the value; it is withheld"
                    " from this message by design."
                )
            total_spawns += count
            print(f"| {_sanitize_table_cell(label)} | {count} |")
        print(f"| **total** | **{total_spawns}** |")


def _dispatch_usage_summary(
    jsonl_path: Path,
    since_ts: float | None,
    until_ts: float | None,
    reprice_as: str | None,
    today: date,
) -> tuple[str | None, float, dict[str, float], float | None, int, int, set[str]]:
    """Modal observed-model family plus priced dollar totals for one subagent
    dispatch's own transcript.

    Reads every assistant record's message.model in jsonl_path. Two or more
    distinct real (non-"<synthetic>") model IDs report the literal bucket
    "mixed", never collapsed into one family — an unstable dispatch should be
    visible, not silently assigned one of its models. A single distinct real
    model ID (regardless of how many turns used it) resolves via _fam. No
    real model ID at all (only "<synthetic>" turns) resolves via
    _fam("<synthetic>") -> "other". This bucket is computed over every
    assistant record in the file, regardless of since_ts/until_ts — a
    dispatch's model identity isn't scoped to a reporting window.

    actual_dollars/dollars_by_class price (via _price_turn) only the
    assistant records whose own timestamp falls in [since_ts, until_ts) —
    filtered per record, not by the dispatch's own start time, since a
    dispatch's sidechain can straddle a window edge and a start-time-only
    filter would attribute post-cutoff spend to an "in-window" total.
    counterfactual_dollars re-prices that same in-window usage at
    reprice_as, or is None when reprice_as is not given.

    unpriced_turns/unpriced_tokens count in-window turns _price_turn couldn't
    price (unknown model ID) — matches cost's own convention of surfacing
    this rather than letting it silently read as zero-cost spend.
    stale_models collects any priced model past its _MODEL_RATE_EXPIRES
    re-verify-by date, evaluated against the caller-supplied today (never
    read from the wall clock here, so a caller can hold this deterministic
    for tests) — mirrors cost's own staleness check.

    Returns (observed_bucket, actual_dollars, dollars_by_class,
    counterfactual_dollars, unpriced_turns, unpriced_tokens, stale_models).
    observed_bucket is None, and every other value is 0/0.0/{}/None/empty,
    when jsonl_path doesn't exist or can't be read — the caller's own
    "dangling meta.json" exclusion path (a run requires a readable sibling
    .jsonl, not just a valid meta.json).
    """
    if not jsonl_path.is_file():
        return None, 0.0, {}, None, 0, 0, set()
    real_model_ids: set[str] = set()
    saw_any_model = False
    dollars_by_class: dict[str, float] = defaultdict(float)
    counterfactual_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0
    stale_models: set[str] = set()
    try:
        with open(jsonl_path) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                model = msg.get("model")
                if not model:
                    continue
                saw_any_model = True
                if model != "<synthetic>":
                    real_model_ids.add(model)

                usage = msg.get("usage")
                if not usage:
                    continue
                if since_ts is not None or until_ts is not None:
                    rec_ts = _parse_ts(rec.get("timestamp"))
                    if rec_ts is None:
                        continue
                    if since_ts is not None and rec_ts < since_ts:
                        continue
                    if until_ts is not None and rec_ts >= until_ts:
                        continue
                turn_dollars, _ctx, turn_unpriced_tokens = _price_turn(model, usage)
                if turn_dollars is None:
                    unpriced_turns += 1
                    unpriced_tokens += turn_unpriced_tokens
                else:
                    for cls, amount in turn_dollars.items():
                        dollars_by_class[cls] += amount
                    if today > _MODEL_RATE_EXPIRES[model]:
                        stale_models.add(model)
                if reprice_as:
                    cf_dollars, _cf_ctx, _cf_unpriced = _price_turn(reprice_as, usage)
                    if cf_dollars is not None:
                        counterfactual_total += sum(cf_dollars.values())
    except OSError:
        return None, 0.0, {}, None, 0, 0, set()

    if len(real_model_ids) >= 2:
        observed = "mixed"
    elif real_model_ids:
        observed = _fam(next(iter(real_model_ids)))
    else:
        observed = _fam("<synthetic>") if saw_any_model else "other"

    actual_dollars = sum(dollars_by_class.values())
    counterfactual_dollars = counterfactual_total if reprice_as else None
    return (
        observed, actual_dollars, dict(dollars_by_class), counterfactual_dollars,
        unpriced_turns, unpriced_tokens, stale_models,
    )


def cmd_skill_pair(args: argparse.Namespace) -> None:
    """Pairing rate between two skills, bucketed by ISO week.

    Counts Skill tool_use blocks regardless of tool_result success — sessions
    where the Skill tool errored (e.g., harnesses without Skill-tool support)
    still count as leader-sessions. Filter such corpora via --exclude-projects.
    """
    leader: str = args.leader
    follower: str = args.follower
    exclude_glob: str | None = getattr(args, "exclude_projects", None)
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "skill-pair", include_subagents=True, roots=roots)
    _print_resolved_scope("skill-pair", scope_label, roots)

    # bin_str -> {leader_sessions, follower_main, follower_sidechain_only}
    data: dict[str, dict[str, int]] = defaultdict(
        lambda: {"leader_sessions": 0, "follower_main": 0, "follower_sidechain_only": 0}
    )
    corpus_spawns = 0
    corpus_sidechain_turns = 0

    for jsonl, records in session_iter:
        # --exclude-projects: skip project dirs whose basename matches the glob
        if exclude_glob and fnmatch.fnmatchcase(jsonl.parent.name, exclude_glob):
            continue

        corpus_spawns += _count_subagent_spawns(records)

        has_leader_hit = False
        leader_first_ts: float | None = None
        has_main_follower = False
        has_sidechain_follower = False

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            if bool(rec.get("isSidechain")):
                corpus_sidechain_turns += 1
            branch = rec.get("gitBranch") or ""
            if branch_filter and branch not in branch_filter:
                continue
            is_sidechain = bool(rec.get("isSidechain"))
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Skill":
                    continue
                skill = (block.get("input") or {}).get("skill") or ""
                if skill == leader and not is_sidechain:
                    if not has_leader_hit:
                        # Timestamp of first leader hit; skip session if unparseable
                        leader_first_ts = _parse_ts(rec.get("timestamp"))
                    has_leader_hit = True
                elif skill == follower:
                    if is_sidechain:
                        has_sidechain_follower = True
                    else:
                        has_main_follower = True

        if not has_leader_hit:
            continue
        # Skip session entirely if the first leader hit has no parseable timestamp
        if leader_first_ts is None:
            continue

        iso = datetime.fromtimestamp(leader_first_ts, tz=UTC).isocalendar()
        bin_str = f"{iso.year}-W{iso.week:02d}"

        d = data[bin_str]
        d["leader_sessions"] += 1
        if has_main_follower:
            d["follower_main"] += 1
        elif has_sidechain_follower:
            # sidechain-only: sidechain follower present AND no main-thread follower
            d["follower_sidechain_only"] += 1

    _warn_if_subagent_format_drift(corpus_spawns, corpus_sidechain_turns)

    if not data:
        print("No data found.")
        return

    print(f"{'Bin':<10} {'Lead':>5} {'Main':>5} {'Side':>5} {'Pair%':>7}")
    print(f"{'-------':<10} {'----':>5} {'----':>5} {'----':>5} {'-----':>7}")
    for bin_str in sorted(data):
        d = data[bin_str]
        lead = d["leader_sessions"]
        main = d["follower_main"]
        side = d["follower_sidechain_only"]
        pair_pct = 100.0 * main / lead if lead else 0.0
        print(f"{bin_str:<10} {lead:>5} {main:>5} {side:>5} {pair_pct:>6.1f}%")


def _pr_link_gh_failure_kind(exc: Exception) -> str:
    """This module's own label for why a pr-link gh call failed -- never gh's
    raw stderr, which can echo the queried repo verbatim."""
    if isinstance(exc, FileNotFoundError):
        return "gh not found"
    if isinstance(exc, json.JSONDecodeError):
        return "unparseable gh output"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    kind = _classify_gh_error(getattr(exc, "stderr", None) or "")
    # _classify_gh_error falls through to "network" for any stderr it does
    # not recognize, a wrong repo slug included.
    return "network or unrecognized" if kind == _GH_ERROR_KIND_NETWORK else kind


def _pr_link_report_gh_failure(branch: str, step: str, exc: Exception) -> None:
    print(f"pr-link: {step} failed for branch {branch} ({_pr_link_gh_failure_kind(exc)})", file=sys.stderr)


def cmd_pr_link(args: argparse.Namespace) -> None:
    if not getattr(args, "branches", None):
        print("--branches is required for pr-link", file=sys.stderr)
        sys.exit(1)

    branches: list[str] = [b.strip() for b in args.branches.split(",") if b.strip()]
    supplied_repo: str | None = getattr(args, "repo", None)
    if supplied_repo:
        repo = pr_list_repo = supplied_repo
        api_host_args: list[str] = []
    else:
        # `gh pr list --repo` takes the host-qualified slug directly; `gh api`
        # takes `--hostname` instead, since its path carries no host.
        # Either way, a GHE origin reaches the right host regardless of the
        # ambient GH_HOST.
        origin_host, repo = _git_remote_origin_host_and_owner_repo(
            subcommand="pr-link", failure_hint="pass --repo OWNER/REPO",
        )
        pr_list_repo = _gh_host_qualified_repo(origin_host, repo)
        api_host_args = ["--hostname", origin_host]
    author: str = getattr(args, "author", None) or ""
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "pr-link", roots=roots)

    branch_models: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _jsonl, records in session_iter:
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch not in branches or rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            fam = _fam((rec.get("message") or {}).get("model", ""))
            branch_models[branch][fam] += 1

    _print_resolved_scope("pr-link", scope_label, roots)
    print(f"{'Branch':<35} {'PR':>5} {'Opus':>6} {'Sonnet':>7} {'IssueCmt':>9} {'ReviewCmt':>10}")
    print("-" * 80)

    for branch in branches:
        model_split = branch_models.get(branch, {})
        opus_n = model_split.get("opus", 0)
        sonnet_n = model_split.get("sonnet", 0)

        try:
            pr_result = subprocess.run(
                [
                    "gh", "pr", "list", "--head", branch, "--repo", pr_list_repo,
                    "--state", "all", "--json", "number", "--limit", "1",
                ],
                capture_output=True, text=True, check=True, timeout=_GH_CALL_TIMEOUT_S,
            )
            prs = json.loads(pr_result.stdout or "[]")
        except (
            subprocess.CalledProcessError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired,
        ) as exc:
            _pr_link_report_gh_failure(branch, "gh pr list", exc)
            print(f"{branch:<35} {'?':>5} {opus_n:>6} {sonnet_n:>7} {'gh-err':>9} {'':>10}")
            continue

        if not prs:
            print(f"{branch:<35} {'none':>5} {opus_n:>6} {sonnet_n:>7} {'—':>9} {'—':>10}")
            continue

        pr_number = prs[0]["number"]
        issue_comments = review_comments = 0

        try:
            ic = subprocess.run(
                [
                    "gh", "api", *api_host_args, f"repos/{repo}/issues/{pr_number}/comments",
                    "--paginate", "--jq", ".[].user.login",
                ],
                capture_output=True, text=True, check=True, timeout=_GH_CALL_TIMEOUT_S,
            )
            issue_logins = [ln.strip() for ln in ic.stdout.splitlines() if ln.strip()]
            issue_comments = sum(1 for ln in issue_logins if not author or ln == author)

            rc = subprocess.run(
                [
                    "gh", "api", *api_host_args, f"repos/{repo}/pulls/{pr_number}/comments",
                    "--paginate", "--jq", ".[].user.login",
                ],
                capture_output=True, text=True, check=True, timeout=_GH_CALL_TIMEOUT_S,
            )
            review_logins = [ln.strip() for ln in rc.stdout.splitlines() if ln.strip()]
            review_comments = sum(1 for ln in review_logins if not author or ln == author)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            _pr_link_report_gh_failure(branch, "gh api comments", exc)
            issue_comments = review_comments = -1

        print(f"{branch:<35} {pr_number:>5} {opus_n:>6} {sonnet_n:>7} {issue_comments:>9} {review_comments:>10}")


# Matches `git commit` as a standalone command or after a shell separator,
# but NOT `git commit-tree` or other `git commit`-prefixed subcommands.
# Mirrors the regex in require-code-review.sh line 38.
_GIT_COMMIT_RE = re.compile(r"(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)")
_NO_VERIFY_RE = re.compile(r"\s--no-verify\b")


def cmd_commit_gate(args: argparse.Namespace) -> None:
    skill_name: str = args.skill
    by_mode: bool = bool(getattr(args, "by_permission_mode", False))
    branch_filter = _branch_filter(args)
    exclude_glob: str | None = getattr(args, "exclude_projects", None) or None
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "commit-gate", roots=roots)
    _print_resolved_scope("commit-gate", scope_label, roots)

    # bin_mode_key -> aggregated counts
    data: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "sessions": 0,
        "turns": 0,
        "skill_invocations": 0,
        "commits": 0,
        "commits_with_prior_skill": 0,
        "commits_without_prior_skill": 0,
        "commits_no_verify": 0,
    })

    for jsonl, records in session_iter:
        # Apply --exclude-projects: skip if project dir basename matches the glob.
        proj_dir_name = jsonl.parent.name
        if exclude_glob and Path(proj_dir_name).match(exclude_glob):
            continue

        # --- per-session derivation ---

        # 1. permissionMode: first record (any type) carrying a non-empty value.
        # Empirically the field lives on `user` records (session-meta initial-user
        # records), not on assistant records — filtering by type misses it.
        permission_mode = "default"
        for rec in records:
            pm = rec.get("permissionMode") or ""
            if pm:
                permission_mode = pm
                break

        # 2. first_turn_ts for ISO-week binning (any record with a timestamp).
        first_turn_ts: float | None = None
        for rec in records:
            ts = _parse_ts(rec.get("timestamp"))
            if ts is not None:
                first_turn_ts = ts
                break
        if first_turn_ts is None:
            continue
        iso_year, iso_week, _ = datetime.fromtimestamp(first_turn_ts, tz=UTC).isocalendar()
        bin_label = f"{iso_year}-W{iso_week:02d}"

        # 3. Branch filter — session contributes if ANY main-thread record is on an allowed branch.
        if branch_filter:
            session_branches = {
                rec.get("gitBranch") or ""
                for rec in records
                if rec.get("type") == "assistant" and not bool(rec.get("isSidechain"))
            }
            if not (session_branches & branch_filter):
                continue

        # 4. Walk records: count turns, skill invocations, and commits with ordering.
        #    Only main-thread (isSidechain != true) assistant records.
        #
        #    Commit gating is tracked by a "skill_since_last_commit" flag that
        #    resets each time a commit is detected.  Within a single assistant
        #    record, content-array index determines ordering between Skill and
        #    Bash blocks.
        session_turns = 0
        session_skill_invocations = 0
        session_commits = 0
        session_commits_with_prior_skill = 0
        session_commits_without_prior_skill = 0
        session_commits_no_verify = 0

        # Tracks whether a qualifying Skill invocation has occurred since the
        # last commit (or session start).
        skill_seen_since_last_commit = False

        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            session_turns += 1

            content = (rec.get("message") or {}).get("content") or []

            # Process each tool_use block in content-array order so that within
            # a single record the Skill/Bash ordering determines gating.
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                block_name = block.get("name")
                inp = block.get("input") or {}

                if block_name == "Skill":
                    if inp.get("skill") == skill_name:
                        session_skill_invocations += 1
                        skill_seen_since_last_commit = True

                elif block_name == "Bash":
                    cmd = inp.get("command", "")
                    if _GIT_COMMIT_RE.search(cmd):
                        session_commits += 1
                        is_no_verify = bool(_NO_VERIFY_RE.search(cmd))
                        if is_no_verify:
                            session_commits_no_verify += 1
                            # --no-verify bypasses the gate entirely; count in
                            # commits and commits-no-verify but NOT in
                            # commits-with-prior-skill.
                            session_commits_without_prior_skill += 1
                        elif skill_seen_since_last_commit:
                            session_commits_with_prior_skill += 1
                        else:
                            session_commits_without_prior_skill += 1
                        # Reset: the skill must fire again to gate the next commit.
                        skill_seen_since_last_commit = False

        bucket_key = (bin_label, permission_mode if by_mode else "all")
        d = data[bucket_key]
        d["sessions"] += 1
        d["turns"] += session_turns
        d["skill_invocations"] += session_skill_invocations
        d["commits"] += session_commits
        d["commits_with_prior_skill"] += session_commits_with_prior_skill
        d["commits_without_prior_skill"] += session_commits_without_prior_skill
        d["commits_no_verify"] += session_commits_no_verify

    if not data:
        print("No data found.")
        return

    if by_mode:
        header = (
            f"{'bin':<12} {'mode':<10} {'sessions':>8} {'turns':>7} "
            f"{'skill-inv':>10} {'skill/1k':>9} {'commits':>7} "
            f"{'w-skill':>8} {'wo-skill':>9} {'no-verify':>10}"
        )
    else:
        header = (
            f"{'bin':<12} {'sessions':>8} {'turns':>7} "
            f"{'skill-inv':>10} {'skill/1k':>9} {'commits':>7} "
            f"{'w-skill':>8} {'wo-skill':>9} {'no-verify':>10}"
        )
    print(header)
    print("-" * len(header))

    for (bin_label, mode) in sorted(data):
        d = data[(bin_label, mode)]
        skill_rate = f"{1000 * d['skill_invocations'] / d['turns']:.1f}" if d["turns"] else "—"
        if by_mode:
            print(
                f"{bin_label:<12} {mode:<10} {d['sessions']:>8} {d['turns']:>7} "
                f"{d['skill_invocations']:>10} {skill_rate:>9} {d['commits']:>7} "
                f"{d['commits_with_prior_skill']:>8} {d['commits_without_prior_skill']:>9} "
                f"{d['commits_no_verify']:>10}"
            )
        else:
            print(
                f"{bin_label:<12} {d['sessions']:>8} {d['turns']:>7} "
                f"{d['skill_invocations']:>10} {skill_rate:>9} {d['commits']:>7} "
                f"{d['commits_with_prior_skill']:>8} {d['commits_without_prior_skill']:>9} "
                f"{d['commits_no_verify']:>10}"
            )


def cmd_context_distribution(args: argparse.Namespace) -> None:
    """CLI entry point for the context-distribution subcommand.

    Root resolution happens here, at the CLI boundary, rather than inside
    _context_distribution_report, mirroring cmd_cost — --config-dir
    validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="context-distribution")
    _context_distribution_report(args, roots)


def _context_distribution_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Per-session peak context, bucketed two ways — grounds a handoff-nudge
    threshold choice against measured sessions instead of picking one blind.

    Peak-context tracking is restricted to main-thread (non-sidechain) turns:
    a subagent dispatch pays its own prefix from scratch and is never a
    candidate for /handoff, so folding sidechain turns into a session's peak
    would mix two different context-growth stories into one number. Each
    main-thread turn's context_at_turn (input_tokens + cache_read_input_tokens
    + ephemeral_1h + ephemeral_5m, from _price_turn, same formula cost's own
    bucket logic uses) feeds two independent per-session maxima, tracked by
    _session_peak_context:
    - peak_pct: context_at_turn expressed as a fraction of that turn's own
      model's context window via _context_window_for_model — so a session
      that mixes models with different windows is judged by how close each
      turn came to its own model's limit, not a single window assumed for
      the whole session. Reported against
      _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS.
    - peak_abs_tokens: context_at_turn plus that turn's output_tokens — the
      same four-field sum nudge-handoff-near-context-cap.sh's own ESTIMATE
      computes, so a threshold read off this table transfers directly to the
      hook's unit. Reported against _CONTEXT_DISTRIBUTION_THRESHOLD_ABS.
    Neither is derived from the other (peak_abs_tokens != peak_pct * window):
    on a session mixing a 200k-window turn with a 1M-window turn, the turn
    with the highest percentage of its own window need not be the turn with
    the highest absolute token count.

    Dollar totals per session sum ALL turns (main and sidechain), matching
    cost's own definition of a session's total spend — a session's dollar
    share reflects its full cost including any subagent work it spawned, not
    just its main-thread portion.

    roots is None for every direct caller other than cmd_context_distribution
    (this module's own tests included) — that keeps the single-root report
    byte-for-byte unchanged, including the absence of cmd_cost's per-root
    scan-summary lines, which only cost's own CLI path emits.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))

    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "context-distribution: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = _parse_since_nd_arg(args, "context-distribution")
    since_label = since_raw or ""

    session_iter, scope_label = _resolve_project_scope(
        args, "context-distribution", include_subagents=True, roots=roots
    )

    if roots is not None:
        # Mirrors cmd_cost's own per-root scan diagnostic (_scan_root_transcripts)
        # -- without it, a multi-root run matching nothing under every declared
        # root would print an empty report with no signal of which root(s) came
        # up empty, now that this subcommand scans multiple roots by default.
        glob = _projects_glob(args)
        this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
        # Resolved-path-sorted, like _cost_report's own copy -- the same
        # physical root must read as the same account-N here regardless of
        # scan_roots' iteration order (active profile first). Computed
        # unconditionally, not gated on multi_root: this loop runs at single
        # root too whenever roots is not None, and _redaction_ordinals is
        # correct and cheap on a single-element list.
        redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots)
        for root in scan_roots:
            root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
            try:
                scanned, skipped = _scan_root_transcripts(root, glob, slugs=this_repo_slugs)
            except PermissionError as exc:
                # str(exc) on a PermissionError typically embeds the offending
                # path — suppressed under default redaction so a permission
                # failure can't leak the raw config-dir path it's reporting on.
                detail = str(exc) if not redact else "permission denied"
                print(
                    f"context-distribution: {root_label}: cannot scan ({detail})"
                    " — treating as 0 transcripts",
                    file=sys.stderr,
                )
                scanned, skipped = 0, 0
            print(
                f"context-distribution: {root_label}: scanned {scanned:,} transcripts,"
                f" {skipped:,} skipped (unreadable)"
            )
            if scanned == 0:
                print(
                    f"WARNING: context-distribution: {root_label}: no transcripts found for this scope"
                    " — check the config dir and --projects/--this-repo filter."
                )

    _print_resolved_scope("context-distribution", scope_label, scan_roots)

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Context distribution report ({title_since})\n")

    session_peak_pcts: list[float] = []
    session_peak_abs_tokens: list[int] = []
    session_dollars: list[float] = []
    total_dollars = 0.0

    for _jsonl, records in session_iter:
        records = _dedup_turns_by_request_id(records)
        main_thread_turns: list[tuple[int, int, int]] = []
        session_total = 0.0

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue

            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            model = msg.get("model", "")
            dollars_by_class, context_at_turn, _turn_unpriced_tokens = _price_turn(model, usage)

            if dollars_by_class is not None:
                session_total += sum(dollars_by_class.values())

            if not bool(rec.get("isSidechain")):
                output_tokens = int(usage.get("output_tokens", 0))
                main_thread_turns.append((context_at_turn, output_tokens, _context_window_for_model(model)))

        peak_pct, peak_abs_tokens = _session_peak_context(main_thread_turns)

        if session_total == 0.0 and peak_pct == 0.0:
            continue

        session_peak_pcts.append(peak_pct)
        session_peak_abs_tokens.append(peak_abs_tokens)
        session_dollars.append(session_total)
        total_dollars += session_total

    total_sessions = len(session_peak_pcts)
    print(f"Sessions in scope: {total_sessions:,}   Total priced dollars: {total_dollars:,.2f}\n")

    print(
        "## Peak context-at-turn crossing thresholds (share of main-thread turns'"
        " own model context window; dollars include each session's subagent spend)\n"
    )
    print(f"{'Threshold':>10} {'Sessions':>9} {'SessShare':>10} {'$':>14} {'DollarShare':>12}")
    pct_rows = _context_distribution_rows(
        [pct / 100 for pct in _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS], session_peak_pcts, session_dollars
    )
    for pct, row in zip(_CONTEXT_DISTRIBUTION_THRESHOLD_PCTS, pct_rows, strict=True):
        print(
            f"{pct:>9}% {row['sessions']:>9,} {row['session_share']:>10}"
            f" {row['dollars']:>14,.2f} {row['dollar_share']:>12}"
        )

    print(
        "\n## Peak absolute-token crossing thresholds (input + cache_read + cache_creation"
        " + output tokens across main-thread turns — nudge-handoff-near-context-cap.sh's own"
        " ESTIMATE unit; dollars include each session's subagent spend)\n"
    )
    print(f"{'Threshold':>10} {'Sessions':>9} {'SessShare':>10} {'$':>14} {'DollarShare':>12}")
    abs_rows = _context_distribution_rows(
        _CONTEXT_DISTRIBUTION_THRESHOLD_ABS, session_peak_abs_tokens, session_dollars
    )
    for abs_threshold, row in zip(_CONTEXT_DISTRIBUTION_THRESHOLD_ABS, abs_rows, strict=True):
        print(
            f"{abs_threshold:>10,} {row['sessions']:>9,} {row['session_share']:>10}"
            f" {row['dollars']:>14,.2f} {row['dollar_share']:>12}"
        )


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
    records = _dedup_turns_by_request_id(records)

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
    roots = _resolve_cost_roots(args, subcommand="edit-format")
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
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = _resolve_project_scope(args, "edit-format", include_subagents=True, roots=roots)
    _print_resolved_scope("edit-format", scope_label, scan_roots)

    # Resolved once, outside the per-session loop below, mirroring cost's own
    # _root_index_for_path usage — re-resolving every root on every session
    # would be a per-element filesystem stat inside that loop.
    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    # Keyed by _redaction_ordinals, not _root_index_for_path's raw scan-order
    # position — the same physical root must read as the same account-N here
    # as in cost's and context-distribution's own per-account breakdowns,
    # regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots) if multi_root else {}

    stats = _new_edit_format_stats()
    per_account: dict[int, dict] = (
        {ordinal: _new_edit_format_stats() for ordinal in redact_ordinals.values()} if multi_root else {}
    )

    for jsonl, records in session_iter:
        session_stats = _scan_edit_format_session(records)
        _merge_edit_format_stats(stats, session_stats)
        if multi_root:
            root_position = _root_index_for_path(jsonl, resolved_scan_roots)
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
        print(f"  {tool:10} {label:12} count={count:6,}  rate={_pct_of(count, denom)} of {tool}")

    mechanical = sum(
        count
        for (tool, label), count in stats["known_failures"].items()
        if tool == "Edit" and label in ("not_found", "unread", "multi_match")
    )
    noop = stats["known_failures"].get(("Edit", "noop"), 0)
    print(
        "\nstr_replace-mechanical (Edit not_found+unread+multi_match, no-ops excluded): "
        f"{mechanical:,} / {edit_n:,} ({_pct_of(mechanical, edit_n)})"
    )
    print(
        "all non-governance Edit errors (no-ops included): "
        f"{mechanical + noop:,} / {edit_n:,} ({_pct_of(mechanical + noop, edit_n)})"
    )

    print("\n## not_found cause attribution (next-edit-same-file diff, whitespace-normalized)\n")
    cause_total = sum(stats["cause"].values())
    for cause, count in stats["cause"].most_common():
        print(f"  {cause:22} count={count:4,}  share={_pct_of(count, cause_total)}")

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
    print(f"old_string share of Edit payload: {_pct_of(old_chars, edit_payload)}")
    print(f"mean old_string chars/edit: {old_chars / edit_n:.0f}" if edit_n else "mean old_string chars/edit: n/a")
    print(f"write content chars: {write_chars:,}  (~{write_chars // cpt:,} tok)")
    print(f"total assistant output tokens (all sessions): {output_tokens:,}")
    print(f"old_string share of total output tokens: {_pct_of(old_chars // cpt, output_tokens)}")
    print(f"(old_string + new_string) share of total output tokens: {_pct_of(edit_payload // cpt, output_tokens)}")

    print("\nold_string size distribution:\n")
    bucket_labels = [label for _upper, label in _EDIT_OLD_STRING_SIZE_BUCKETS] + [_EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL]
    for label in bucket_labels:
        count = stats["old_string_size_hist"].get(label, 0)
        print(f"  {label:10} {count:6,}  ({_pct_of(count, edit_n)})")

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
                f"not_found={not_found:4,}  multi={multi:3,}  addressable={_pct_of(addressable, a_edit_n)}"
            )


# ---------------------------------------------------------------------------
# instrument-authoring
# ---------------------------------------------------------------------------
#
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

            if name in _SPAWN_TOOL_NAMES:
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
    roots = _resolve_cost_roots(args, subcommand="instrument-authoring")
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

    session_iter, scope_label = _resolve_project_scope(
        args, "instrument-authoring", include_subagents=True, roots=roots
    )
    _print_resolved_scope("instrument-authoring", scope_label, scan_roots)

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
        f"  zero_dispatch  sessions={zero['session_n']:6,} ({_pct_of(zero['session_n'], total_sessions)} of sessions)"
        f"  main-thread authored chars=~{zero['payload_chars']:10,}"
        f" ({_pct_of(zero['payload_chars'], total_payload)} of authored mass)"
    )
    print(
        f"  dispatched     sessions={dispatched['session_n']:6,} ({_pct_of(dispatched['session_n'], total_sessions)} of sessions)"
        f"  main-thread authored chars=~{dispatched['payload_chars']:10,}"
        f" ({_pct_of(dispatched['payload_chars'], total_payload)} of authored mass)"
    )


# ---------------------------------------------------------------------------
# context-composition
# ---------------------------------------------------------------------------
#
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
    return _MCP_TOOL_BUCKET_LABEL if name.startswith("mcp__") else name


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
            return category, len(item.get("text") or "") // _READ_SCOPE_CHARS_PER_TOKEN
        if block_type == "thinking":
            return _CATEGORY_ASSISTANT_THINKING, len(item.get("thinking") or "") // _READ_SCOPE_CHARS_PER_TOKEN
        if block_type == "tool_use":
            payload = json.dumps(item.get("input") or {}, separators=(",", ":"))
            return _CATEGORY_TOOL_CALL, len(payload) // _READ_SCOPE_CHARS_PER_TOKEN
        if block_type == "tool_result":
            text = _content_text(item.get("content", ""))
            return _CATEGORY_TOOL_RESULT, len(text) // _READ_SCOPE_CHARS_PER_TOKEN
        try:
            payload = json.dumps(item, separators=(",", ":"))
        except TypeError:
            payload = str(item)
        return _CATEGORY_UNCLASSIFIED, len(payload) // _READ_SCOPE_CHARS_PER_TOKEN
    if isinstance(item, str):
        category = (
            _CATEGORY_COMPACT_SUMMARY if is_compact_summary
            else _CATEGORY_USER_TEXT if record_type == "user"
            else _CATEGORY_ASSISTANT_TEXT
        )
        return category, len(item) // _READ_SCOPE_CHARS_PER_TOKEN
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
        scale *= _FAST_MODE_RATE_MULTIPLIER
    if usage.get("inference_geo") == "us":
        scale *= _INFERENCE_GEO_US_RATE_MULTIPLIER
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
            turn_timestamps.append(_parse_ts(rec.get("timestamp")))
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
        read_mult[t] = _CACHE_READ_MULTIPLIER * scale if in_window else 0.0
        eph_1h, eph_5m = _cache_write_split(usage)
        if eph_1h + eph_5m > 0:
            write_base = (eph_1h * _CACHE_WRITE_1H_MULTIPLIER + eph_5m * _CACHE_WRITE_5M_MULTIPLIER) / (eph_1h + eph_5m)
        else:
            # No cache-write tokens this turn -- _price_turn's own rate for that case is the
            # plain input rate (1x), not a cache-write tier.
            write_base = 1.0
        write_mult[t] = write_base * scale if in_window else 0.0

        context_at_turn[t] = _context_at_turn(usage)
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
        deduped = _dedup_turns_by_request_id(group)
        for sequence in _split_context_sequences(deduped):
            _merge_context_composition_stats(stats, _scan_context_composition_sequence(sequence, since_ts))
    return stats


def cmd_context_composition(args: argparse.Namespace) -> None:
    """CLI entry point for the context-composition subcommand.

    Root resolution happens here, mirroring cmd_context_distribution, so --config-dir validation
    exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="context-composition")
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
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = _parse_since_nd_arg(args, "context-composition")
    since_label = since_raw or ""

    session_iter, scope_label = _resolve_project_scope(
        args, "context-composition", include_subagents=True, roots=roots
    )

    if roots is not None:
        # Mirrors cmd_cost's/context-distribution's own per-root scan diagnostic
        # (_scan_root_transcripts) -- pure counts, no composition data, so it stays outside the
        # "no per-root breakdown" redaction contract above.
        glob = _projects_glob(args)
        this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
        redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots)
        for root in scan_roots:
            root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
            try:
                scanned, skipped = _scan_root_transcripts(root, glob, slugs=this_repo_slugs)
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

    _print_resolved_scope("context-composition", scope_label, scan_roots)

    stats = _new_context_composition_stats()
    for jsonl, _records in session_iter:
        # session_iter already read and parsed this file once internally (to decide whether to
        # yield it at all); this second, partitioned read is the cost of reusing
        # _resolve_project_scope's shared iterator, the same tradeoff _read_scope_report makes.
        groups = _read_session_file_partitioned(jsonl, include_subagents=True)
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
        print(f"{category:<32} {value:>16,.0f} {_pct_of(value, total_weighted):>8} {count:>10,}")



# T=0.50 is the case study's highest-scoring threshold for this read-collapse
# rule (max Youden's J) against the alternative cache_creation >
# cache_read_input_tokens rule; see docs/case-studies/cold-cache-attribution.md
# for the full comparison.
_COLD_READ_COLLAPSE_MARGIN = 0.50


def _cache_prefix_total(usage: dict) -> int:
    """cache_read_input_tokens plus both cache_creation tiers for one turn's
    usage -- the prefix total a warm cache would have served whole, and the
    read-collapse classifier's prior-turn denominator
    (docs/case-studies/cold-cache-attribution.md). Deliberately excludes
    input_tokens, unlike _context_at_turn: the classifier compares what the
    cache itself could have served, not the turn's total context."""
    eph_1h, eph_5m = _cache_write_split(usage)
    return int(usage.get("cache_read_input_tokens", 0)) + eph_1h + eph_5m


def _is_cold_read_collapse(prior_prefix_total: int, read_t: int) -> bool:
    """The read-collapse rule: cold when this turn's read falls more than
    _COLD_READ_COLLAPSE_MARGIN below the prior turn's own prefix total.
    prior_prefix_total <= 0 means there is no prefix to collapse from --
    including a session/thread's first turn, which has no prior turn at all
    -- and is never cold."""
    if prior_prefix_total <= 0:
        return False
    return (prior_prefix_total - read_t) / prior_prefix_total > _COLD_READ_COLLAPSE_MARGIN


def _new_cache_efficiency_stats() -> dict:
    return {
        thread: {
            "turns": 0,
            "read_tokens": 0,
            "write_1h_tokens": 0,
            "write_5m_tokens": 0,
            "cold_tokens": 0,
            "cold_events": 0,
        }
        for thread in ("main", "sidechain")
    }


def _merge_cache_efficiency_stats(dst: dict, src: dict) -> None:
    for thread in ("main", "sidechain"):
        d, s = dst[thread], src[thread]
        for key in ("turns", "read_tokens", "write_1h_tokens", "write_5m_tokens", "cold_tokens", "cold_events"):
            d[key] += s[key]


def _scan_cache_efficiency_group(group: list[dict], stats: dict) -> int:
    """Classify one source-file group's (main transcript, or one subagent
    file, per _read_session_file_partitioned) assistant turns for cold-cache
    read collapse and accumulate into `stats`, keyed by thread
    ("main"/"sidechain").

    `group` must already be deduped via _dedup_turns_by_request_id -- an
    un-deduped multi-record run shares one identical cache_read/cache_creation
    usage across every record in the run (see that function's docstring), so
    scanning raw records would compare a turn against itself mid-run and can
    spuriously read as cold whenever that turn's own write exceeds its read.

    Keys the prior-turn chain by each record's own (sessionId, thread),
    mirroring _read_scope_growth_for_group's sessionId keying -- a subagent
    file's records carry the *parent* session's sessionId, so sessionId
    alone is still the correct per-conversation boundary, not per-file
    identity. Thread is included because, unlike _read_scope_growth_for_group
    (which has no per-thread output), this function already buckets its
    output by thread: without it, a defensively-accepted mixed-thread group
    would let one thread's prior prefix leak into the other's first-turn
    classification. The first turn of every resulting per-(session, thread)
    sequence has no predecessor and so is never classified cold. Resets the
    chain at each compact_boundary record, same as
    _read_scope_growth_for_group -- the pre-compaction prefix no longer
    exists to collapse from, so treating it as this turn's "prior" would
    misclassify the first post-compaction turn as cold every time.

    Returns the count of isSidechain assistant records read in this group,
    counted unconditionally before the usage check -- feeds the drift canary
    independently of stats["sidechain"]["turns"] (which only counts turns
    with priced usage), mirroring _cost_report's total_sidechain_turns.
    """
    prior_prefix_by_thread_session: dict[tuple[str, str], int] = {}
    sidechain_turns_read = 0

    for rec in group:
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            prior_prefix_by_thread_session.clear()
            continue
        if rec.get("type") != "assistant":
            continue
        thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
        if thread == "sidechain":
            sidechain_turns_read += 1
        usage = (rec.get("message") or {}).get("usage")
        if not usage:
            continue

        session_key = rec.get("sessionId") or ""
        chain_key = (session_key, thread)
        read_t = int(usage.get("cache_read_input_tokens", 0))
        # _cache_write_split runs twice for this turn (here and inside
        # _cache_prefix_total below), mirroring _price_turn's own reuse of
        # it -- it's pure, and the per-tier accumulators need the split
        # separately from the combined prefix total the classifier compares.
        eph_1h, eph_5m = _cache_write_split(usage)
        prefix_total = _cache_prefix_total(usage)

        row = stats[thread]
        row["turns"] += 1
        row["read_tokens"] += read_t
        row["write_1h_tokens"] += eph_1h
        row["write_5m_tokens"] += eph_5m

        prior_prefix_total = prior_prefix_by_thread_session.get(chain_key)
        if prior_prefix_total is not None and _is_cold_read_collapse(prior_prefix_total, read_t):
            row["cold_tokens"] += eph_1h + eph_5m
            row["cold_events"] += 1

        prior_prefix_by_thread_session[chain_key] = prefix_total

    return sidechain_turns_read


def _print_cache_efficiency_table(stats: dict) -> None:
    # Every header label is a single whitespace token (Write1h, not "Write
    # 1h") so this table stays parseable by the test suite's own
    # header-anchored column reader (_table_cols), matching every other
    # fixed-width table in this file.
    print(
        f"{'Thread':<10} {'Turns':>10} {'Read':>16} {'Write1h':>14} {'Write5m':>14}"
        f" {'ColdTok':>16} {'Cold/Wr':>11} {'Cold/Rd':>10} {'ColdEvts':>10} {'AvgEvt':>10}"
    )
    for thread in ("main", "sidechain"):
        row = stats[thread]
        write_total = row["write_1h_tokens"] + row["write_5m_tokens"]
        cold_events = row["cold_events"]
        avg_event = row["cold_tokens"] / cold_events if cold_events else 0
        print(
            f"{thread:<10} {row['turns']:>10,} {row['read_tokens']:>16,} {row['write_1h_tokens']:>14,}"
            f" {row['write_5m_tokens']:>14,} {row['cold_tokens']:>16,} {_pct_of(row['cold_tokens'], write_total):>11}"
            f" {_pct_of(row['cold_tokens'], row['read_tokens']):>10} {cold_events:>10,} {avg_event:>10,.0f}"
        )


def _print_cache_efficiency_report(stats: dict, per_account: dict[int, dict] | None) -> None:
    print("\n## Cache efficiency by thread\n")
    _print_cache_efficiency_table(stats)

    if per_account is not None:
        print("\n## Cache efficiency by account\n")
        for ordinal in sorted(per_account):
            print(f"\n### account-{ordinal}\n")
            _print_cache_efficiency_table(per_account[ordinal])


def cmd_cache_efficiency(args: argparse.Namespace) -> None:
    """CLI entry point for the cache-efficiency subcommand.

    Root resolution happens here, mirroring cmd_cost/cmd_edit_format/
    cmd_read_scope, so --config-dir validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="cache-efficiency")
    _cache_efficiency_report(args, roots)


def _cache_efficiency_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Per-thread cold-cache read-collapse census: assistant turn counts,
    cache read/write token totals, and cold-write volume/rate, classified by
    the read-collapse rule at T=_COLD_READ_COLLAPSE_MARGIN
    (docs/case-studies/cold-cache-attribution.md). `cost` buckets spend by
    token class only; this distinguishes a cold prefix re-write from an
    ordinary incremental append within that spend.

    roots is None for every direct caller other than cmd_cache_efficiency
    (this module's own tests included) -- mirrors cost/edit-format/read-scope's
    own single-root-by-default contract, including the absence of the
    per-account breakdown below.

    This report's own content never varies with `redact`: like edit-format
    and read-scope, it carries no project name or session ID -- per-account
    rows use account-N labels. --no-redact is still accepted and still
    enforces the same multi-root refusal and DO NOT PUBLISH banner as
    cost/edit-format/read-scope, for CLI parity.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "cache-efficiency: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = _resolve_project_scope(
        args, "cache-efficiency", include_subagents=True, roots=roots
    )
    _print_resolved_scope("cache-efficiency", scope_label, scan_roots)

    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots) if multi_root else {}

    stats = _new_cache_efficiency_stats()
    per_account: dict[int, dict] = (
        {ordinal: _new_cache_efficiency_stats() for ordinal in redact_ordinals.values()} if multi_root else {}
    )
    total_spawns = 0
    total_sidechain_turns = 0

    for jsonl, records in session_iter:
        records = _dedup_turns_by_request_id(records)
        total_spawns += _count_subagent_spawns(records)
        # session_iter already read and parsed this file once internally (to
        # decide whether to yield it at all); this second, partitioned read
        # is the cost of reusing _resolve_project_scope's shared iterator,
        # mirroring read-scope's own growth-chain reuse note -- the
        # classifier's prior-turn chain needs the per-file boundary the flat
        # merge discards (a subagent's own cache prefix is not continuous
        # with the main thread's, or with a sibling subagent's).
        groups = _read_session_file_partitioned(jsonl, include_subagents=True)
        session_stats = _new_cache_efficiency_stats()
        for group in groups:
            total_sidechain_turns += _scan_cache_efficiency_group(_dedup_turns_by_request_id(group), session_stats)
        _merge_cache_efficiency_stats(stats, session_stats)
        if multi_root:
            root_position = _root_index_for_path(jsonl, resolved_scan_roots)
            ordinal = redact_ordinals[resolved_scan_roots[root_position]]
            _merge_cache_efficiency_stats(per_account[ordinal], session_stats)

    _warn_if_subagent_format_drift(total_spawns, total_sidechain_turns)

    _print_cache_efficiency_report(stats, per_account if multi_root else None)


# --- spend-over-threshold: per-week share of session spend at or above the handoff nudge's fire threshold ---


def cmd_spend_over_threshold(args: argparse.Namespace) -> None:
    """Per-week share of session dollar spend earned at or above the handoff
    nudge's own fire threshold.

    For each session, sums `actual_dollars` (via _extract_rearm_session_turns,
    shared with rearm-backtest) across main-thread turns whose context_at_turn
    is at or above that session's own _hook_effective_fire_threshold (from its
    first main-thread turn's model), against the session's total main-thread
    actual_dollars. A session with no main-thread turn carrying a usage block
    (session_threshold is None) or with total_dollars == 0 (every turn
    unpriced) is excluded from the report -- neither has a meaningful share to
    report.

    Output: per-ISO-week table with columns: week, sessions, above-threshold
    $, total $, share. Also reads ~/.claude/.handoff-nudge.log if present and
    reports schema-drift count as a diagnostic footer.
    """
    since_str: str | None = getattr(args, "since", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "spend-over-threshold", roots=roots)
    _print_resolved_scope("spend-over-threshold", scope_label, roots)

    # week_str -> {"sessions": int, "above": float, "total": float}
    data: dict[str, dict[str, float]] = defaultdict(lambda: {"sessions": 0.0, "above": 0.0, "total": 0.0})

    for _jsonl, records in session_iter:
        # A session's dollar totals depend on its full, un-truncated turn
        # sequence (_extract_rearm_session_turns), so --since scopes whole
        # sessions here (by first timestamp), not individual records within
        # one -- matching _session_matches_rearm_scope's own convention for
        # this same per-turn machinery.
        first_ts = next((ts for r in records if (ts := _parse_ts(r.get("timestamp"))) is not None), None)
        if since_ts is not None and (first_ts is None or first_ts < since_ts):
            continue
        if first_ts is None:
            continue

        extracted = _extract_rearm_session_turns(records)
        session_threshold = extracted["session_threshold"]
        if session_threshold is None:
            continue

        above_dollars = 0.0
        total_dollars = 0.0
        for context_at_turn, _output_tokens, actual_dollars in extracted["main_thread_turns"]:
            total_dollars += actual_dollars
            if context_at_turn >= session_threshold:
                above_dollars += actual_dollars
        if total_dollars == 0:
            continue

        iso = datetime.fromtimestamp(first_ts, tz=UTC).isocalendar()
        week_str = f"{iso.year}-W{iso.week:02d}"
        data[week_str]["sessions"] += 1
        data[week_str]["above"] += above_dollars
        data[week_str]["total"] += total_dollars

    if not data:
        print("No sessions with a resolvable handoff-nudge threshold and priced spend were found.")
        _print_nudge_log_diagnostic()
        return

    print(f"{'Week':<10} {'Sessions':>8} {'AboveUSD':>14} {'TotalUSD':>14} {'Share':>7}")
    print("-" * 57)
    total_sessions = 0
    total_above = total_total = 0.0
    for week_str in sorted(data):
        d = data[week_str]
        sessions = int(d["sessions"])
        above = d["above"]
        total = d["total"]
        total_sessions += sessions
        total_above += above
        total_total += total
        print(f"{week_str:<10} {sessions:>8} {above:>14,.2f} {total:>14,.2f} {_pct_of(above, total):>7}")

    print("-" * 57)
    print(
        f"{'Total':<10} {total_sessions:>8} {total_above:>14,.2f} {total_total:>14,.2f} "
        f"{_pct_of(total_above, total_total):>7}"
    )
    _print_nudge_log_diagnostic()


_NUDGE_LOG_MAX_READ = 2 * 1024 * 1024  # 2 MB


def _read_bounded_log_lines(log_path: Path) -> list[str]:
    """Read an append-only log file's lines, tail-truncated to the last
    _NUDGE_LOG_MAX_READ bytes so an unbounded log can't be pulled fully into
    memory. Returns [] when the file is absent or unreadable -- shared by
    _print_nudge_log_diagnostic and _parse_nudge_log_entries so both read
    ~/.claude/.handoff-nudge.log the same bounded way."""
    try:
        if not log_path.exists():
            return []
        if log_path.stat().st_size > _NUDGE_LOG_MAX_READ:
            raw = log_path.read_bytes()[-_NUDGE_LOG_MAX_READ:]
            return raw.decode(errors="ignore").splitlines()
        return log_path.read_text().splitlines()
    except OSError:
        return []


def _print_nudge_log_diagnostic() -> None:
    """Read ~/.claude/.handoff-nudge.log and report schema-drift count if
    present. Silently skips the diagnostic (never raises) when config_dir()
    can't resolve, since the primary report this footer follows has already
    printed and succeeded. Matches _read_bounded_log_lines' own
    absent/unreadable-file degrade above."""
    try:
        config_directory = config_dir()
    except ValueError:
        return
    log_path = config_directory / ".handoff-nudge.log"
    lines = _read_bounded_log_lines(log_path)
    drift_count = sum(1 for ln in lines if ln.startswith("schema-drift"))
    if drift_count:
        print(f"\nDiagnostic: {drift_count} schema-drift line(s) in {log_path}")
        print("  Schema-drift means the usage block was found but all token fields were 0 or null.")
        print("  The field paths in nudge-handoff-near-context-cap.sh may need updating.")


# ---------------------------------------------------------------------------
# turn-shape / turn-shape-samples
# ---------------------------------------------------------------------------

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
    tokens = _drop_denial_command_flag_values(tokens)
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
    records = _dedup_turns_by_request_id(records)
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
            rec_ts = _parse_ts(rec.get("timestamp"))
            if rec_ts is None or rec_ts < since_ts:
                continue

        content = msg.get("content") or []
        tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        call_count = len(tool_use_blocks)
        tool_name = tool_use_blocks[0].get("name", "") if call_count == 1 else ""
        command = (tool_use_blocks[0].get("input") or {}).get("command", "") if tool_name == "Bash" else ""

        dollars_by_class, _, turn_unpriced_tokens = _price_turn(msg.get("model", ""), usage)
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
    since_ts, since_raw = _parse_since_nd_arg(args, "turn-shape")
    since_label = since_raw or ""

    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "turn-shape", roots=roots)

    call_count_turns: dict[str, int] = {b: 0 for b in _TURN_SHAPE_CALL_COUNT_BUCKETS}
    call_count_dollars: dict[str, float] = {b: 0.0 for b in _TURN_SHAPE_CALL_COUNT_BUCKETS}
    batching_streaks: dict[str, int] = {b: 0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    batching_dollars: dict[str, float] = {b: 0.0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    delegation_streaks: dict[str, int] = {b: 0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    delegation_dollars: dict[str, float] = {b: 0.0 for b in _TURN_SHAPE_STREAK_BUCKETS}
    unpriced_turns = 0
    unpriced_tokens = 0

    _print_resolved_scope("turn-shape", scope_label, roots)

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
        print(f"{bkt:<8} {call_count_turns[bkt]:>8,} {_fmt_usd(call_count_dollars[bkt]):>12}")

    print("\n### Single-call streak length (batching rule)\n")
    header = f"{'Bucket':<8} {'Streaks':>8} {'$':>12}"
    print(header)
    print("─" * len(header))
    for bkt in _TURN_SHAPE_STREAK_BUCKETS:
        print(f"{bkt:<8} {batching_streaks[bkt]:>8,} {_fmt_usd(batching_dollars[bkt]):>12}")

    print("\n### Bash-only single-call streak length, excluding mutating git (delegation rule)\n")
    header = f"{'Bucket':<8} {'Streaks':>8} {'$':>12}"
    print(header)
    print("─" * len(header))
    for bkt in _TURN_SHAPE_STREAK_BUCKETS:
        print(f"{bkt:<8} {delegation_streaks[bkt]:>8,} {_fmt_usd(delegation_dollars[bkt]):>12}")

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
    since_ts, _since_raw = _parse_since_nd_arg(args, "turn-shape-samples")
    sample_n: int = getattr(args, "sample", 30) or 30
    seed: int | None = getattr(args, "seed", None)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "turn-shape-samples", roots=roots)
    # stderr, not stdout: stdout is this subcommand's plain-text data stream.
    _print_resolved_scope("turn-shape-samples", scope_label, roots, file=sys.stderr)

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

    print(_DO_NOT_PUBLISH_BANNER)
    print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)
    for candidate in candidates:
        streak = candidate["streak"]
        dollars = sum(t["dollars"] for t in streak)
        print(
            f"\n--- {candidate['rule']} streak, length={len(streak)}, "
            f"{_fmt_usd(dollars)}, session={streak[0]['session_id']} ---"
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
    since_ts, _since_raw = _parse_since_nd_arg(args, "turn-shape-holdout-samples")
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

    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "turn-shape-holdout-samples", roots=roots)
    # stderr, not stdout: stdout is this subcommand's plain-text data stream.
    _print_resolved_scope("turn-shape-holdout-samples", scope_label, roots, file=sys.stderr)

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

    print(_DO_NOT_PUBLISH_BANNER)
    print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)
    for candidate in window:
        streak = candidate["streak"]
        dollars = sum(t["dollars"] for t in streak)
        print(
            f"\n--- {candidate['rule']} streak, length={len(streak)}, "
            f"{_fmt_usd(dollars)}, session={streak[0]['session_id']} ---"
        )
        for i, turn in enumerate(streak, 1):
            detail = f": {turn['command']}" if turn["command"] else ""
            print(f"  {i}. {turn['tool_name']}{detail}")


# ---------------------------------------------------------------------------
# friction-count
# ---------------------------------------------------------------------------

# All three friction signals are weighted equally (1) in the composite —
# stated explicitly here rather than left implicit in the addition below.
_FRICTION_SIGNAL_WEIGHT = 1


def _friction_denial_events(records: list[dict]) -> int:
    """Count hook-denial events in `records`, deduped by tool_use_id.

    Flat, single-file count reusing hook_denial_key for both denial shapes
    (mirrors cmd_review_trace's detection and seen-id dedup exactly), but
    additionally skips isSidechain records — a friction-count-only filter;
    cmd_review_trace's denial detection is not itself isSidechain-filtered.
    """
    seen_denial_ids: set[str] = set()
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        rec_type = rec.get("type", "")
        if rec_type == "attachment":
            denial = hook_denial_key(rec)
            if denial is None:
                continue
            tool_use_id, _ = denial
            if tool_use_id and tool_use_id in seen_denial_ids:
                continue
            if tool_use_id:
                seen_denial_ids.add(tool_use_id)
            count += 1
        elif rec_type == "user":
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                denial = hook_denial_key(block)
                if denial is None:
                    continue
                tool_use_id, _ = denial
                if tool_use_id and tool_use_id in seen_denial_ids:
                    continue
                if tool_use_id:
                    seen_denial_ids.add(tool_use_id)
                count += 1
    return count


def _friction_failed_test_run_events(records: list[dict]) -> int:
    """Count failed test-run events: a Bash tool_use matching TEST_RUNNER_RE
    paired (by tool_use_id) with a tool_result whose max FAILED_RE count > 0.

    Flat, single-file pairing — re-implements the tool_use_id pairing state
    machine independently of cmd_fail_seq (which is branch-grouped and not
    modified), sharing only the TEST_RUNNER_RE/FAILED_RE constants. Counts
    only failing runs (FAILED_RE count > 0), not every matched run — this is
    cmd_fail_seq's "failing" subtotal, not its total run count. isSidechain
    records are skipped.
    """
    pending: set[str] = set()
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        rec_type = rec.get("type", "")
        msg = rec.get("message") or {}
        if rec_type == "assistant":
            for block in (msg.get("content") or []):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Bash"
                ):
                    cmd = (block.get("input") or {}).get("command", "")
                    if TEST_RUNNER_RE.search(cmd):
                        pending.add(block["id"])
        elif rec_type in ("user", "human"):
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                tid = block.get("tool_use_id", "")
                if block.get("type") == "tool_result" and tid in pending:
                    pending.discard(tid)
                    result_text = _content_text(block.get("content", ""))
                    counts = [int(m) for m in FAILED_RE.findall(result_text)]
                    if counts and max(counts) > 0:
                        count += 1
    return count


def _friction_struggle_turn_events(records: list[dict]) -> int:
    """Count user turns whose lowercased text contains a STRUGGLE_PHRASES entry.

    Flat, single-file count — no branch or model-family attribution.
    isSidechain records are skipped.
    """
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        if rec.get("type") not in ("user", "human"):
            continue
        msg = rec.get("message") or {}
        text = _strip_task_notifications(_content_text(msg.get("content", ""))).lower()
        if any(phrase in text for phrase in STRUGGLE_PHRASES):
            count += 1
    return count


def _friction_signals(records: list[dict]) -> dict[str, int]:
    """Return the per-signal friction breakdown plus the all-1-weighted composite."""
    # Shadows the module-level `denials` import (transcript_analysis.denials) -- this
    # function needs no denials.<name> access, only the count computed below.
    denials = _friction_denial_events(records)  # noqa: F811
    failed_test_runs = _friction_failed_test_run_events(records)
    struggle_turns = _friction_struggle_turn_events(records)
    composite = (
        _FRICTION_SIGNAL_WEIGHT * denials
        + _FRICTION_SIGNAL_WEIGHT * failed_test_runs
        + _FRICTION_SIGNAL_WEIGHT * struggle_turns
    )
    signals = {
        "denials": denials,
        "failed_test_runs": failed_test_runs,
        "struggle_turns": struggle_turns,
        "composite": composite,
    }
    # Pinned invariant: composite must equal the sum of the three signals.
    # An explicit raise (not `assert`) so the check survives python -O /
    # PYTHONOPTIMIZE, which strips bare asserts.
    if signals["composite"] != denials + failed_test_runs + struggle_turns:
        raise AssertionError(
            "friction-count: composite must equal the sum of denials + failed_test_runs + struggle_turns"
        )
    return signals


# friction-count --checkpoint: incremental byte-offset scan. Avoids reparsing
# the whole transcript on every hook fire — see the hook's own comment for
# why this exists. The checkpoint is a small JSON blob (offset + the three
# running per-signal totals); no cross-call dedup state is needed beyond the
# offset, because each call starts exactly where the previous one stopped, so
# every transcript line is read at most once across the checkpoint's lifetime.
_FRICTION_CHECKPOINT_SIGNAL_KEYS = ("denials", "failed_test_runs", "struggle_turns")


def _is_valid_checkpoint_int(value: object) -> bool:
    """True if `value` is a non-negative int — explicitly excluding bool,
    which subclasses int in Python (`isinstance(True, int)` is True), so an
    unvalidated check would silently accept `{"offset": true}` as offset 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _read_friction_checkpoint(checkpoint_path: Path, transcript_path: Path) -> tuple[int, dict[str, int]]:
    """Read a friction-count checkpoint: (byte_offset, running per-signal totals).

    Fails open to a full rescan (offset 0, zero totals) on any absent or
    malformed checkpoint — unreadable file, invalid JSON, wrong shape, a
    non-int/negative/bool offset or totals value, or a stored offset beyond
    the transcript's current size (a stale checkpoint from a transcript that
    was truncated or rewritten while the session_id-keyed checkpoint
    persisted — without this check, seeking past EOF would freeze friction
    counting for that session permanently rather than resetting it). Never
    raises.
    """
    zero_totals = {key: 0 for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS}
    try:
        data = json.loads(checkpoint_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0, dict(zero_totals)
    if not isinstance(data, dict):
        return 0, dict(zero_totals)
    offset = data.get("offset")
    totals = data.get("totals")
    if not _is_valid_checkpoint_int(offset) or not isinstance(totals, dict):
        return 0, dict(zero_totals)
    running_totals: dict[str, int] = {}
    for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS:
        value = totals.get(key)
        if not _is_valid_checkpoint_int(value):
            return 0, dict(zero_totals)
        running_totals[key] = value
    try:
        transcript_size = transcript_path.stat().st_size
    except OSError:
        # Transcript unreadable — let the caller's own transcript-read
        # attempt raise/report; the checkpoint content isn't the problem.
        return offset, running_totals
    if offset > transcript_size:
        return 0, dict(zero_totals)
    return offset, running_totals


def _write_friction_checkpoint(checkpoint_path: Path, offset: int, totals: dict[str, int]) -> None:
    """Best-effort persist of the new byte offset + running per-signal totals.

    Writes to a temp file in the checkpoint's own directory and atomically
    renames it into place (`os.replace`), rather than truncating the
    checkpoint file in place — an interrupted-and-resubmitted prompt can
    leave two hook invocations for the same session_id alive within the
    hook's 10s timeout, and an in-place write is a lost-update race between
    them. A failed write (unwritable directory, disk full) is swallowed
    rather than raised: the next call re-reads from the old offset and
    rescans those bytes again — correct, just not incremental for that one
    call — matching this subcommand's fail-open posture everywhere else.
    """
    with contextlib.suppress(OSError):
        fd, tmp_name = tempfile.mkstemp(
            dir=checkpoint_path.parent, prefix=f".{checkpoint_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps({"offset": offset, "totals": totals}))
            os.replace(tmp_name, checkpoint_path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


def _consume_new_transcript_lines(transcript_path: Path, offset: int) -> tuple[list[str], int]:
    """Read complete JSONL lines appended to `transcript_path` since `offset`.

    Returns (new_lines, new_offset). A trailing line with no terminating
    newline — the transcript may be mid-write by the harness while the hook
    reads it — is left unconsumed: new_offset stops at the end of the last
    complete line, so the next call picks up the partial line's bytes whole
    once it's terminated, rather than double-reading or losing them.
    """
    with open(transcript_path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    if not chunk:
        return [], offset
    pieces = chunk.split(b"\n")
    complete_pieces = pieces[:-1]  # last piece is "" (chunk ended in \n) or a partial line
    new_offset = offset + sum(len(piece) + 1 for piece in complete_pieces)
    new_lines = [piece.decode("utf-8", errors="replace") for piece in complete_pieces]
    return new_lines, new_offset


def _emit_friction_result(signals: dict[str, int], *, as_json: bool) -> None:
    """Print either the --json per-signal breakdown or the bare composite integer."""
    if as_json:
        print(json.dumps(signals))
    else:
        print(signals["composite"])


def cmd_sessions(args: argparse.Namespace) -> None:
    """Emit transcript file paths for the resolved scope, one absolute path per line.

    --paths is required: it names the one action this subcommand supports today,
    leaving room for a second sessions action later without a bare `sessions`
    invocation silently doing nothing. Sourced from _resolve_scan_roots plus
    _resolve_project_scope (not a flat glob), so a main session file only reaches
    this repo's own worktrees under --this-repo the same way every other
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
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(
        args, "sessions", include_subagents=include_subagents, roots=roots
    )
    _print_resolved_scope("sessions", scope_label, roots, file=sys.stderr)
    for jsonl, _records in session_iter:
        print(jsonl)
        if include_subagents:
            subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
            if subagent_dir.is_dir():
                for sub_jsonl in sorted(subagent_dir.glob("*.jsonl")):
                    print(sub_jsonl)


def cmd_friction_count(args: argparse.Namespace) -> None:
    """Composite friction-signal count for a single transcript file.

    Reads exactly one JSONL file (no iter_sessions, no gh) and prints the
    composite integer to stdout. --json prints the per-signal breakdown
    instead of the composite.

    --checkpoint <path> makes the scan incremental: only the bytes appended
    to the transcript since the checkpoint's stored byte offset are parsed,
    the resulting per-signal deltas are added to the checkpoint's running
    totals, the new offset + totals are written back, and the *cumulative*
    totals (not just this call's delta) are what gets printed. Without
    --checkpoint, behavior is unchanged: a full scan every call, no state
    read or written.
    """
    transcript_path = Path(args.transcript)
    checkpoint_arg = getattr(args, "checkpoint", None)

    if checkpoint_arg:
        checkpoint_path = Path(checkpoint_arg)
        offset, running_totals = _read_friction_checkpoint(checkpoint_path, transcript_path)
        try:
            new_lines, new_offset = _consume_new_transcript_lines(transcript_path, offset)
        except OSError:
            print(f"friction-count: cannot read transcript file: {transcript_path}", file=sys.stderr)
            sys.exit(1)

        records: list[dict] = []
        for raw in new_lines:
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

        deltas = _friction_signals(records)
        for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS:
            running_totals[key] += deltas[key]
        _write_friction_checkpoint(checkpoint_path, new_offset, running_totals)

        composite = sum(running_totals[key] for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS)
        signals = {**running_totals, "composite": composite}
        _emit_friction_result(signals, as_json=getattr(args, "json", False))
        return

    records = []
    try:
        # errors="replace" mirrors the --checkpoint branch's binary-read +
        # decode tolerance: a transcript with invalid Unicode bytes must
        # degrade to lossy decoding, not raise an uncaught
        # UnicodeDecodeError (a ValueError subclass the `except OSError`
        # below does not catch).
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                records.append(rec)
    except OSError:
        print(f"friction-count: cannot read transcript file: {transcript_path}", file=sys.stderr)
        sys.exit(1)

    signals = _friction_signals(records)
    _emit_friction_result(signals, as_json=getattr(args, "json", False))


# --- rearm-backtest: backtest candidate re-arm band spacings for the
# handoff nudge's one-shot fire against the recorded corpus. See
# .claude/plans/handoff-nudge-rearm-backtest.md for the full design.

# Mirrors nudge-handoff-near-context-cap.sh's own HANDOFF_NUDGE_ABS_CAP
# default (docs/handoff-nudge.md's "Why this cap" section). Duplicated
# rather than imported -- there is no mechanism to share a constant between
# a bash hook and a Python script, the same cross-language duplication
# _context_window_for_model's docstring already documents. Not a CLI flag:
# .claude/plans/token-cost-reduction.md's Phase 3 keeps this fixed, and a
# flag would invite a future run to quietly retune it through this tool.
_HANDOFF_NUDGE_ABS_CAP = 150_000

# Mirrors the hook's own `PCT_THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))` --
# the hook fires at the LESSER of 40% of the active model's context window
# and _HANDOFF_NUDGE_ABS_CAP, so a 200k-window model's real fire point
# (80,000) is well under the 1M-window arm's cap-governed 150,000. Neither
# this fraction nor _HANDOFF_NUDGE_ABS_CAP is backtested -- only re-arm
# spacing past whichever of the two governs a given session is.
_HANDOFF_NUDGE_PCT_THRESHOLD = 0.40

# PR #605's own turn-index bands (.claude/plans/handoff-boundary-decision-rule.md),
# reused here for comparability with that point-in-time measurement -- the
# dollar/context figures themselves are re-derived from the current corpus on
# every run, never hardcoded. Follows _EDIT_OLD_STRING_SIZE_BUCKETS' own
# cascading less-than convention: a turn index is tested against each bound
# in order and takes the first label whose bound it's under, so an index
# PR #605's own table never explicitly labeled (10-19, between "5-10" and
# "20-40") falls through to "20-40" rather than going unbucketed.
_RAMP_CURVE_TURN_INDEX_BUCKETS: tuple[tuple[int, str], ...] = (
    (5, "0-5"),
    (10, "5-10"),
    (40, "20-40"),
    (80, "40-80"),
    (150, "80-150"),
    (300, "150-300"),
)
_RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL = "300+"
_RAMP_CURVE_BUCKET_LABELS: tuple[str, ...] = tuple(
    label for _, label in _RAMP_CURVE_TURN_INDEX_BUCKETS
) + (_RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL,)

_REARM_BACKTEST_DEFAULT_SPACINGS: tuple[int, ...] = (40_000, 80_000, 120_000)

# The three line shapes _parse_nudge_log_entries recognizes: "nudged" and
# "schema-drift" are written by nudge-handoff-near-context-cap.sh itself
# (docs/handoff-nudge.md's "Log location" table); "handoff" is appended by
# the handoff skill's own conversion-signal step
# (claude-skills/skills/handoff/SKILL.md, "After writing: record the
# conversion signal").
_NUDGE_LOG_LINE_KINDS = ("nudged", "schema-drift", "handoff")


def _ramp_curve_turn_index_bucket(turn_index: int) -> str:
    """Bucket a 0-indexed main-thread turn position (turns since a real or
    simulated fresh session start) into one of PR #605's seven turn-index
    bands, via the cascading less-than lookup _RAMP_CURVE_TURN_INDEX_BUCKETS'
    own docstring explains."""
    for bound, label in _RAMP_CURVE_TURN_INDEX_BUCKETS:
        if turn_index < bound:
            return label
    return _RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL


def _hook_effective_fire_threshold(model: str) -> int:
    """The real hook's own fire threshold for one model: the lesser of 40% of
    that model's context window (_context_window_for_model, mirroring the
    bash hook's own CONTEXT_WINDOW case statement) and _HANDOFF_NUDGE_ABS_CAP.
    A 200k-window model's real threshold (80,000) is well under a 1M-window
    model's cap-governed one (150,000) -- using _HANDOFF_NUDGE_ABS_CAP alone
    for every session would overstate how early such sessions actually get
    nudged today."""
    pct_threshold = int(_context_window_for_model(model) * _HANDOFF_NUDGE_PCT_THRESHOLD)
    return min(pct_threshold, _HANDOFF_NUDGE_ABS_CAP)


def _hook_observable_boundaries(records: Sequence[dict]) -> list[int]:
    """Turn-count positions (0..N, where N is the session's own main-thread
    turn count) at which nudge-handoff-near-context-cap.sh could observe this
    session's growing context. `records` must already be
    _dedup_turns_by_request_id's output -- the same records a caller builds
    its own main_thread_turns list from -- so a returned boundary is directly
    usable as a slice/turn-count index into that list.

    UserPromptSubmit and Stop both check the transcript's latest recorded
    main-thread assistant usage (docs/handoff-nudge.md), so the two fire at
    the same observable point: right after a run of tool-call-only turns
    yields back to a genuine user message. Reusing _is_fresh_user_prompt for
    that user-message half (see its own docstring for what it filters) marks
    every INTERNAL boundary -- one per genuine user message, not one per
    turn, so a multi-tool-call stretch between two user messages contributes
    no boundary of its own. Session start (0, before any turn) and session
    end (N, the full turn count) are the two boundaries no user message can
    supply on their own, and both are always included: the hook's own header
    comment states it is "registered on both events so a session that
    crosses the threshold on its final turn, with no further user prompt,
    still gets warned" -- a boundary set with no session-end entry would make
    a last-turn crossing invisible to the simulation.

    A turn only counts toward the position (and thus toward a boundary) when
    it carries a usage block, matching exactly the predicate a caller uses to
    build main_thread_turns -- a main-thread assistant record with no usage
    block (a synthetic error record, see _dedup_turns_by_request_id's
    docstring) must not desync the two lists' shared indexing.
    """
    boundaries: list[int] = [0]
    main_turn_count = 0
    for rec in records:
        if rec.get("type") == "assistant" and not bool(rec.get("isSidechain")):
            if (rec.get("message") or {}).get("usage"):
                main_turn_count += 1
            continue
        if main_turn_count > 0 and _is_fresh_user_prompt(rec) and boundaries[-1] != main_turn_count:
            boundaries.append(main_turn_count)
    if boundaries[-1] != main_turn_count:
        boundaries.append(main_turn_count)
    return boundaries


def _extract_rearm_session_turns(records: Sequence[dict]) -> dict:
    """Single dedup+price pass over one session's raw records, shared by
    _ramp_curve_from_corpus and _rearm_backtest_report so each session's
    records are decoded/deduped/priced exactly once per report run instead
    of twice -- every sibling subcommand in this file (`cost`,
    `context-distribution`, etc.) does a single streaming pass over its
    corpus, not two.

    Returns a dict with:
    - "deduped": _dedup_turns_by_request_id's output, for a caller building
      _hook_observable_boundaries from the same records.
    - "main_thread_turns": one (context_at_turn, output_tokens, actual_dollars)
      tuple per main-thread assistant turn carrying a usage block
      (actual_dollars is 0.0 when the turn's model is unpriced), in
      _simulate_rearm_spacing's own input shape.
    - "main_thread_priced": one bool per entry in main_thread_turns, parallel
      to it, True when that turn's model was priced -- _ramp_curve_from_corpus
      only buckets priced turns.
    - "main_thread_models": one model ID per entry in main_thread_turns,
      parallel to it -- plan-boundary's own ground-truth model-switch check
      needs each turn's model, not just its price-table membership.
    - "main_thread_record_positions": one "deduped" list index per entry in
      main_thread_turns, parallel to it -- lets a caller (plan-boundary) fetch
      a main-thread turn's own raw record (and its usage/diagnostics fields)
      by main_thread_turns index without a second scan of "deduped", and
      without this list's own filtering (usage-block-only, main-thread-only)
      desyncing from a plain enumerate() over "deduped".
    - "sidechain_dollars_total": summed actual dollars across this session's
      priced sidechain turns.
    - "unpriced_turns" / "unpriced_tokens": counts across both main-thread and
      sidechain turns whose model has no price-table entry.
    - "session_threshold": _hook_effective_fire_threshold for this session's
      first main-thread turn's model (None if the session has no main-thread
      turn with a usage block).
    """
    deduped = _dedup_turns_by_request_id(records)
    main_thread_turns: list[tuple[int, int, float]] = []
    main_thread_priced: list[bool] = []
    main_thread_models: list[str] = []
    main_thread_record_positions: list[int] = []
    sidechain_dollars_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0
    session_threshold: int | None = None

    for record_index, rec in enumerate(deduped):
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        usage = msg.get("usage")
        if not usage:
            continue
        model = msg.get("model", "")
        dollars_by_class, context_at_turn, turn_unpriced_tokens = _price_turn(model, usage)
        output_tokens = int(usage.get("output_tokens", 0))
        if bool(rec.get("isSidechain")):
            if dollars_by_class is not None:
                sidechain_dollars_total += sum(dollars_by_class.values())
            else:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
            continue
        if dollars_by_class is None:
            unpriced_turns += 1
            unpriced_tokens += turn_unpriced_tokens
            actual_dollars = 0.0
        else:
            actual_dollars = sum(dollars_by_class.values())
        if session_threshold is None:
            session_threshold = _hook_effective_fire_threshold(model)
        main_thread_turns.append((context_at_turn, output_tokens, actual_dollars))
        main_thread_priced.append(dollars_by_class is not None)
        main_thread_models.append(model)
        main_thread_record_positions.append(record_index)

    return {
        "deduped": deduped,
        "main_thread_turns": main_thread_turns,
        "main_thread_priced": main_thread_priced,
        "main_thread_models": main_thread_models,
        "main_thread_record_positions": main_thread_record_positions,
        "sidechain_dollars_total": sidechain_dollars_total,
        "unpriced_turns": unpriced_turns,
        "unpriced_tokens": unpriced_tokens,
        "session_threshold": session_threshold,
    }


def _ramp_curve_from_corpus(sessions: Iterable[dict]) -> tuple[dict[str, dict[str, float]], int]:
    """Re-derive PR #605's fresh-session rebuild ramp from the current corpus
    instead of citing that PR's own table: its source document
    (.claude/plans/handoff-boundary-decision-rule.md) calls the table
    "a point-in-time measurement, not a reproducible report," so this
    subcommand recomputes it every run against whatever corpus is in scope.

    `sessions` is _extract_rearm_session_turns' own output, one dict per
    session -- this function does no I/O or dedup/pricing of its own, only
    the bucket aggregation, so a caller extracts each session's turns
    exactly once and fans the result out to both this function and its own
    main_thread_turns/session_traces bookkeeping.

    Buckets main-thread turns only (no sidechain/subagent turns -- a
    subagent dispatch pays its own prefix from scratch and never represents
    a "turns since a fresh session start" position) by
    _ramp_curve_turn_index_bucket. Each bucket's "rate" is $/1k output
    tokens (dollars / (output_tokens/1000), the same normalize-by-work
    convention PR #605's own table used) and "mean_context" is the
    output-token-weighted mean context_at_turn for turns in that bucket --
    both needed by _simulate_rearm_spacing to price and to estimate the
    context depth of a counterfactually-repriced turn.

    A bucket with zero output tokens in the resolved corpus falls back to the
    corpus-wide rate/mean_context (also 0.0 when the whole corpus has zero
    output tokens) rather than a division-by-zero or NaN -- a corpus that
    doesn't happen to have a session long enough to populate the "300+"
    bucket must still return a usable, defined number for that bucket.

    Returns (curve, total_output_tokens): total_output_tokens is the whole
    resolved corpus's own priced output-token count, letting a caller detect
    the corpus-wide-zero case (every bucket's rate/mean_context silently 0.0,
    with nothing in curve itself distinguishing that from a genuinely cheap
    ramp) distinctly from a normal, populated curve.
    """
    bucket_dollars: dict[str, float] = defaultdict(float)
    bucket_output_tokens: dict[str, int] = defaultdict(int)
    bucket_context_weighted: dict[str, float] = defaultdict(float)
    total_dollars = 0.0
    total_output_tokens = 0
    total_context_weighted = 0.0

    for session in sessions:
        turns = session["main_thread_turns"]
        for turn_index, is_priced in enumerate(session["main_thread_priced"]):
            if not is_priced:
                continue
            context_at_turn, output_tokens, turn_dollars = turns[turn_index]
            label = _ramp_curve_turn_index_bucket(turn_index)
            bucket_dollars[label] += turn_dollars
            bucket_output_tokens[label] += output_tokens
            bucket_context_weighted[label] += context_at_turn * output_tokens
            total_dollars += turn_dollars
            total_output_tokens += output_tokens
            total_context_weighted += context_at_turn * output_tokens

    fallback_rate = (total_dollars / (total_output_tokens / 1000)) if total_output_tokens else 0.0
    fallback_context = (total_context_weighted / total_output_tokens) if total_output_tokens else 0.0

    curve: dict[str, dict[str, float]] = {}
    for label in _RAMP_CURVE_BUCKET_LABELS:
        out_tok = bucket_output_tokens.get(label, 0)
        if out_tok:
            curve[label] = {
                "rate": bucket_dollars[label] / (out_tok / 1000),
                "mean_context": bucket_context_weighted[label] / out_tok,
            }
        else:
            curve[label] = {"rate": fallback_rate, "mean_context": fallback_context}
    return curve, total_output_tokens


def _parse_nudge_log_entries(log_path: Path) -> list[dict]:
    """Parse ~/.claude/.handoff-nudge.log into one dict per recognized line
    (see _NUDGE_LOG_LINE_KINDS), reusing _read_bounded_log_lines' bounded
    2MB tail-read rather than a fresh read implementation. A line that
    doesn't start with a recognized kind, or whose fields don't parse (a
    missing required key, or a non-integer est/window), is silently skipped
    -- this is a best-effort append-only operational log, not a format this
    tool controls.

    Each returned dict carries "kind" plus that kind's own fields:
    - nudged: session, est (int), model, window (int), event
      - action: present only on a hard-block fire (action=block); absent on an advisory fire
      - ignored (int), skills: present on log lines written by hook versions that record per-fire telemetry; absent on older lines
    - schema-drift: session, event
    - handoff: session
    """
    entries: list[dict] = []
    for line in _read_bounded_log_lines(log_path):
        tokens = line.split()
        if not tokens or tokens[0] not in _NUDGE_LOG_LINE_KINDS:
            continue
        kind = tokens[0]
        fields: dict[str, str] = {}
        malformed = False
        for tok in tokens[1:]:
            if "=" not in tok:
                malformed = True
                break
            key, _, value = tok.partition("=")
            fields[key] = value
        if malformed:
            continue

        if kind == "nudged":
            if not {"session", "est", "model", "window", "event"} <= fields.keys():
                continue
            try:
                est = int(fields["est"])
                window = int(fields["window"])
            except ValueError:
                continue
            entry = {
                "kind": "nudged", "session": fields["session"], "est": est,
                "model": fields["model"], "window": window, "event": fields["event"],
            }
            if "action" in fields:
                entry["action"] = fields["action"]
            if "ignored" in fields:
                with contextlib.suppress(ValueError):
                    entry["ignored"] = int(fields["ignored"])
            if "skills" in fields:
                entry["skills"] = fields["skills"]
            entries.append(entry)
        elif kind == "schema-drift":
            if not {"session", "event"} <= fields.keys():
                continue
            entries.append({"kind": "schema-drift", "session": fields["session"], "event": fields["event"]})
        else:  # handoff
            if "session" not in fields:
                continue
            entries.append({"kind": "handoff", "session": fields["session"]})
    return entries


def _operator_response_lag_from_log(
    session_traces: dict[str, list[int]], log_entries: list[dict]
) -> tuple[list[int], int]:
    """Measure how far past each logged nudge's fire point sessions in scope
    actually kept running, for the compliance-realistic backtest model.

    session_traces maps a full session id (jsonl.stem, matching the hook's
    own SESSION_ID) to that session's ordered per-main-thread-turn abs-token
    values (context_at_turn + output_tokens -- the hook's own ESTIMATE unit).
    A `nudged` log line carries no timestamp (docs/handoff-nudge.md's "Log
    location" table enumerates its fields), so the join key is session_id
    plus a first-crossing rule: the fire turn is the
    trace's first value >= est, matching the real hook's own semantics -- it
    fires once, at the first crossing, never later. A nearest-value join
    would instead risk landing on a turn *after* a mid-session compaction
    (isCompactSummary) dip whose abs-token value happens to be closer to est
    than the true, earlier first-crossing turn, silently corrupting the
    measured lag with no error and no other signal.

    Returns (lags, excluded_count): lags is one non-negative token delta
    (peak abs-tokens reached at or after the identified fire turn, minus est)
    per successfully joined `nudged` line. A `nudged` line whose session_id
    has no entry in session_traces (a since-deleted transcript, or a session
    from an account/root outside the resolved scope), or whose trace never
    reaches est at all, is excluded and counted rather than silently dropped.
    A hard-block fire (action=block) is excluded too: its overshoot is
    forced by the block, not the voluntary operator-response lag this
    function measures.
    """
    lags: list[int] = []
    excluded = 0
    for entry in log_entries:
        if entry.get("kind") != "nudged":
            continue
        if entry.get("action") == "block":
            excluded += 1
            continue
        trace = session_traces.get(entry["session"])
        if not trace:
            excluded += 1
            continue
        est = entry["est"]
        fire_idx = next((i for i, value in enumerate(trace) if value >= est), None)
        if fire_idx is None:
            excluded += 1
            continue
        lags.append(max(trace[fire_idx:]) - est)
    return lags, excluded


def _nudge_conversion_from_log(
    session_traces: dict[str, list[int]], log_entries_by_root: dict[Path, list[dict]]
) -> dict:
    """Classify each fired, in-scope session into one of four nudge-to-handoff
    conversion buckets, per the frozen classification in
    .claude/plans/handoff-nudge-deep-tail-lever.md.

    A session enters the classified population when it appears on at least
    one `nudged` log line AND has a surviving in-scope trace in
    session_traces -- the same population _operator_response_lag_from_log
    joins against. A nudged session with no in-scope trace is excluded and
    counted under "dropped" rather than guessed at (mirroring
    _operator_response_lag_from_log's own excluded_count). A `handoff` line
    for a session that never appears on a `nudged` line at all is outside
    this population and is neither classified nor counted -- this measures
    "did a nudged session convert," not "how many handoffs ran."

    log_entries_by_root groups _parse_nudge_log_entries' own per-root output
    -- a session's own nudged/handoff lines always land in one root's log,
    since the same account writes both. Neither line type carries a
    timestamp, so each root's own file order is the only chronological
    signal available. No cross-root merge is needed as a result.

    Buckets (mutually exclusive, decided by the first `handoff` line reached
    and whether any `action=block` line precedes it):
    - voluntary: a handoff line exists, no block precedes it
    - forced: a handoff line exists, at least one block precedes it
    - blocked_no_handoff: at least one block line, no handoff line
    - no_compliance: neither a block nor a handoff line

    Returns a dict with:
    - "voluntary", "forced", "blocked_no_handoff", "no_compliance": the four
      bucket counts above
    - "dropped": nudged sessions with no surviving in-scope trace, excluded
      rather than guessed at (see above)
    - "join_validity": voluntary + forced, the count of fired, in-scope
      sessions whose handoff line's session id actually matched a nudged
      session id
    - "ignored_values": the `ignored=` value on the last nudged line
      preceding the handoff line, one per voluntary session where that line
      carries the field
    - "no_ignored_field": voluntary sessions whose preceding nudged line
      carries no ignored= field -- counted separately and never defaulted to
      0, which would bias the distribution toward "complied immediately"

    A future nudge tier adding a third `action=` value must update this
    function's own `action == "block"` check (below) alongside
    _operator_response_lag_from_log's.
    """
    # A session id repeated across roots (stale symlink, merged log, PID
    # reuse) is not handled -- entries land in root-scan order.
    # Ordering is approximate because neither line type carries a
    # timestamp.
    per_session: dict[str, list[dict]] = defaultdict(list)
    for entries in log_entries_by_root.values():
        for entry in entries:
            if entry.get("kind") not in ("nudged", "handoff"):
                continue
            session = entry.get("session")
            if session:
                per_session[session].append(entry)

    voluntary = forced = blocked_no_handoff = no_compliance = 0
    dropped = no_ignored_field = 0
    ignored_values: list[int] = []

    for session, entries in per_session.items():
        if not any(e["kind"] == "nudged" for e in entries):
            continue
        if session not in session_traces:
            dropped += 1
            continue

        handoff_index = next((i for i, e in enumerate(entries) if e["kind"] == "handoff"), None)
        block_index = next(
            (i for i, e in enumerate(entries) if e["kind"] == "nudged" and e.get("action") == "block"),
            None,
        )

        if handoff_index is None:
            if block_index is not None:
                blocked_no_handoff += 1
            else:
                no_compliance += 1
            continue

        if block_index is not None and block_index < handoff_index:
            forced += 1
            continue

        voluntary += 1
        last_nudged = next(
            (e for e in reversed(entries[:handoff_index]) if e["kind"] == "nudged"), None
        )
        if last_nudged is not None and "ignored" in last_nudged:
            ignored_values.append(last_nudged["ignored"])
        else:
            no_ignored_field += 1

    return {
        "voluntary": voluntary,
        "forced": forced,
        "blocked_no_handoff": blocked_no_handoff,
        "no_compliance": no_compliance,
        "dropped": dropped,
        # Definitional sum of the two buckets above, not an independent join
        # re-check: the classification loop's own per-session bucket
        # assignment already requires a shared session id before either
        # bucket increments.
        "join_validity": voluntary + forced,
        "ignored_values": ignored_values,
        "no_ignored_field": no_ignored_field,
    }


def _simulate_rearm_spacing(
    main_thread_turns: Sequence[tuple[int, int, float]],
    boundaries: Sequence[int],
    spacing: int,
    ramp_curve: dict[str, dict[str, float]],
    threshold: int,
    *,
    response_lag_tokens: float = 0.0,
) -> tuple[float, float, float]:
    """Replay one session's main-thread turns under one candidate re-arm spacing.

    main_thread_turns is a (context_at_turn, output_tokens, actual_dollars)
    tuple per turn, in order; boundaries is _hook_observable_boundaries'
    output for the same session. Real context/output growth is replayed
    unmodified throughout -- band crossings are detected against the
    session's *actual* recorded trajectory, never a counterfactually-reset
    one. Turns before the first detected crossing keep their actual recorded
    dollars and context. Each crossing "splits" the session: turns from that
    point until the next crossing (or session end) are re-priced by mapping
    their distance from the split to a turns-since-a-fresh-restart position
    and applying ramp_curve's rate/mean_context at that position to the
    turn's own real output-token volume -- work stays constant, only the
    context-depth-driven rate changes, modeling what a fresh session would
    have billed for the same work rather than what the real, ever-growing
    prefix actually cost.

    A crossing is only detectable at a boundary in `boundaries`.
    response_lag_tokens (0.0 for the perfect-compliance model) shifts each
    band's trigger point later by that many tokens, modeling the empirically
    measured gap (_operator_response_lag_from_log) between a nudge firing and
    the operator actually acting on it, for the compliance-realistic model.

    Returns (total_dollars, context_weighted_sum, output_token_weight): the
    last two let a caller aggregate an output-token-weighted mean context
    ("C_bar", `cost ~= N x C_bar x rate` in .claude/plans/token-cost-reduction.md)
    across many sessions without re-deriving per-turn context outside this
    function.
    """
    boundary_set = set(boundaries)
    total = 0.0
    context_weighted = 0.0
    weight = 0.0
    fired_bands = 0
    turns_since_restart = 0
    in_actual_epoch = True

    for i, (context_at_turn, output_tokens, actual_dollars) in enumerate(main_thread_turns):
        if in_actual_epoch:
            total += actual_dollars
            context_weighted += context_at_turn * output_tokens
        else:
            label = _ramp_curve_turn_index_bucket(turns_since_restart)
            bucket = ramp_curve.get(label, {"rate": 0.0, "mean_context": 0.0})
            total += (output_tokens / 1000) * bucket["rate"]
            context_weighted += bucket["mean_context"] * output_tokens
            turns_since_restart += 1
        weight += output_tokens

        abs_tokens = context_at_turn + output_tokens
        band_trigger = threshold + fired_bands * spacing + response_lag_tokens
        if abs_tokens >= band_trigger and (i + 1) in boundary_set:
            fired_bands += 1
            in_actual_epoch = False
            turns_since_restart = 0

    return total, context_weighted, weight


def _parse_rearm_spacings_arg(args: argparse.Namespace) -> list[int]:
    """Parse --spacings' comma-separated token list into a list of positive
    ints, defaulting to _REARM_BACKTEST_DEFAULT_SPACINGS. Exits 2 on a
    non-integer or non-positive value."""
    raw: str = getattr(args, "spacings", None) or ",".join(str(s) for s in _REARM_BACKTEST_DEFAULT_SPACINGS)
    spacings: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            print(
                f"rearm-backtest: --spacings: expected comma-separated integers, got {token!r} in {raw!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        if value <= 0:
            print(f"rearm-backtest: --spacings: values must be positive, got {value}", file=sys.stderr)
            sys.exit(2)
        spacings.append(value)
    if not spacings:
        print("rearm-backtest: --spacings: at least one spacing value is required", file=sys.stderr)
        sys.exit(2)
    return spacings


def _session_matches_rearm_scope(
    records: Sequence[dict], since_ts: float | None, branch_filter: set[str] | None
) -> bool:
    """Whether a whole session belongs in a rearm-backtest run's scope.

    --since and --branches scope entire sessions here, not individual turns
    within one -- unlike `cost`'s per-record --branches filter, a re-arm
    simulation's turns-since-restart positioning depends on a session's own
    turn sequence staying intact, so silently dropping turns mid-session
    would desync _hook_observable_boundaries' turn-count boundaries from
    whatever's left of main_thread_turns.
    """
    if since_ts is not None:
        first_ts = next((ts for r in records if (ts := _parse_ts(r.get("timestamp"))) is not None), None)
        if first_ts is not None and first_ts < since_ts:
            return False
    return branch_filter is None or any(
        r.get("type") == "assistant" and not bool(r.get("isSidechain")) and r.get("gitBranch") in branch_filter
        for r in records
    )


def _rearm_backtest_log_size_lines(
    per_root_sizes: Sequence[tuple[Path, int | None]],
    *,
    multi_root: bool,
    redact: bool,
    redact_ordinals: dict[Path, int],
) -> list[str]:
    """Render the nudge-log byte-size disclosure line(s) for
    _rearm_backtest_report from each root's already-resolved byte size
    (None means unreadable). Multi-root scope pools every root into one
    aggregate line: a per-root byte count is itself a per-account figure,
    which docs/private-project-redaction.md's Account-cardinality bar
    prohibits. Single-root scope prints that root's own account-N-labeled
    (or raw path under --no-redact) line directly.

    Pure over already-resolved sizes so it's unit-testable without a
    filesystem.
    """
    if multi_root:
        total_bytes = sum(size for _root, size in per_root_sizes if size is not None)
        # Boolean-only, never a count: same cardinality-leak concern as above.
        any_truncated = any(
            size is not None and size > _NUDGE_LOG_MAX_READ for _root, size in per_root_sizes
        )
        any_unreadable = any(size is None for _root, size in per_root_sizes)
        note = ""
        if any_truncated:
            note += " (some roots truncated -- oldest lines dropped)"
        if any_unreadable:
            note += " (some roots unreadable)"
        return [f"  nudge logs across every resolved root: {total_bytes:,} bytes{note}"]

    # multi_root=False implies exactly one entry: the sole caller derives
    # multi_root from the same scan_roots that produced per_root_sizes.
    root, size = per_root_sizes[0]
    log_path = root.parent / ".handoff-nudge.log"
    root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(log_path)
    if size is None:
        return [f"  {root_label} nudge log: unreadable"]
    truncated_note = " [truncated -- oldest lines dropped]" if size > _NUDGE_LOG_MAX_READ else ""
    return [f"  {root_label} nudge log: {size:,} bytes{truncated_note}"]


def cmd_rearm_backtest(args: argparse.Namespace) -> None:
    """CLI entry point for the rearm-backtest subcommand.

    Root resolution happens here, at the CLI boundary, rather than inside
    _rearm_backtest_report, mirroring cmd_cost -- --config-dir validation
    exits before any scan work. The wall-clock date is read exactly once,
    here, mirroring cmd_cost_trend's own split.
    """
    roots = _resolve_cost_roots(args, subcommand="rearm-backtest")
    _rearm_backtest_report(args, datetime.now(UTC).date(), roots)


def _rearm_backtest_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Backtest candidate re-arm band spacings against the recorded corpus.

    One row per candidate spacing (--spacings, default
    _REARM_BACKTEST_DEFAULT_SPACINGS) plus an unmodified baseline row
    (today's real recorded one-shot totals, i.e. spacing = never re-arm),
    each under both the perfect-compliance and compliance-realistic models
    (see _simulate_rearm_spacing's response_lag_tokens). Each session's first
    fire point is its own effective threshold (_hook_effective_fire_threshold,
    from that session's own model) -- unchanged from today's real hook
    behavior -- and only re-arm spacing PAST that point varies; model routing
    and the threshold computation itself are held fixed and printed as such,
    so a reader can't mistake "spacing-only" for "everything."
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    if not redact and multi_root:
        print(
            "rearm-backtest: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)
    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    spacings = _parse_rearm_spacings_arg(args)
    since_ts, since_raw = _parse_since_nd_arg(args, "rearm-backtest")
    branch_filter = _branch_filter(args)

    session_iter, scope_label = _resolve_project_scope(args, "rearm-backtest", roots=roots)
    _print_resolved_scope("rearm-backtest", scope_label, scan_roots)
    # Each in-scope session's records are deduped and priced exactly once,
    # via _extract_rearm_session_turns, and the same extraction dict feeds
    # both _ramp_curve_from_corpus and this function's own
    # sessions_data/session_traces bookkeeping below -- a single pass over
    # the corpus, matching every sibling subcommand in this file (`cost`,
    # `context-distribution`, etc.).
    scoped_sessions = [
        (jsonl, _extract_rearm_session_turns(records)) for jsonl, records in session_iter
        if _session_matches_rearm_scope(records, since_ts, branch_filter)
    ]

    ramp_curve, ramp_curve_output_tokens = _ramp_curve_from_corpus(data for _jsonl, data in scoped_sessions)

    sessions_data: list[dict] = []
    session_traces: dict[str, list[int]] = {}
    sidechain_dollars_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0

    for jsonl, data in scoped_sessions:
        sidechain_dollars_total += data["sidechain_dollars_total"]
        unpriced_turns += data["unpriced_turns"]
        unpriced_tokens += data["unpriced_tokens"]
        main_thread_turns = data["main_thread_turns"]
        if not main_thread_turns:
            continue

        session_id = jsonl.stem
        sessions_data.append({
            "session_id": session_id,
            "main_thread_turns": main_thread_turns,
            "boundaries": _hook_observable_boundaries(data["deduped"]),
            # The real hook resolves its threshold from whichever model is
            # active when it checks (_hook_effective_fire_threshold); a
            # session almost always stays on one model family, so
            # session_threshold (from its first main-thread turn's model)
            # approximates that check well enough for a single per-session
            # scalar -- exactly what _simulate_rearm_spacing's `threshold`
            # param takes.
            "threshold": data["session_threshold"],
        })
        session_traces[session_id] = [c + o for c, o, _d in main_thread_turns]

    if not sessions_data:
        print("No priced main-thread turns found in scope.")
        if unpriced_turns:
            print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
        return

    # scan_roots is already resolved (with its own exit(2) handling) via
    # _resolve_cost_roots, so no config_dir() call is needed here. Per-root
    # join avoids biasing lag/conversion toward one account while
    # session_traces spans every root.
    log_entries_by_root: dict[Path, list[dict]] = {}
    per_root_sizes: list[tuple[Path, int | None]] = []
    for root in scan_roots:
        log_path = root.parent / ".handoff-nudge.log"
        log_entries_by_root[root] = _parse_nudge_log_entries(log_path)
        # This duplicates _read_bounded_log_lines' own exists/stat/read guard
        # rather than reusing it. The two stay in sync only because both
        # currently catch plain OSError -- re-check both sites together if
        # either's caught exception type narrows.
        try:
            log_size = log_path.stat().st_size if log_path.exists() else 0
        except OSError:
            per_root_sizes.append((root, None))
            continue
        per_root_sizes.append((root, log_size))
    redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots)
    for line in _rearm_backtest_log_size_lines(
        per_root_sizes, multi_root=multi_root, redact=redact, redact_ordinals=redact_ordinals
    ):
        print(line)
    log_entries = [entry for entries in log_entries_by_root.values() for entry in entries]
    lags, excluded_count = _operator_response_lag_from_log(session_traces, log_entries)
    if lags:
        sorted_lags = sorted(lags)
        mid = len(sorted_lags) // 2
        median_lag = float(sorted_lags[mid]) if len(sorted_lags) % 2 else (sorted_lags[mid - 1] + sorted_lags[mid]) / 2
    else:
        median_lag = 0.0

    # Baseline: today's real recorded totals, no counterfactual repricing at all.
    baseline_main_dollars = sum(d for s in sessions_data for _c, _o, d in s["main_thread_turns"])
    baseline_context_weighted = sum(c * o for s in sessions_data for c, o, _d in s["main_thread_turns"])
    baseline_output_tokens = sum(o for s in sessions_data for _c, o, _d in s["main_thread_turns"])
    baseline_total = baseline_main_dollars + sidechain_dollars_total
    baseline_c_bar = (baseline_context_weighted / baseline_output_tokens) if baseline_output_tokens else 0.0

    title_since = f"last {since_raw}" if since_raw else "all time"
    print(f"\n## Re-arm spacing backtest ({title_since}, generated {today.isoformat()})\n")
    print(f"Sessions in scope: {len(sessions_data):,}")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
    print(
        f"Operator-response-lag sample: {len(lags)} joined 'nudged' log line(s)"
        f" ({excluded_count} excluded -- no matching session in scope), median lag"
        f" {median_lag:,.0f} tokens past the fire point"
    )
    print(
        "\nModel routing and each session's own fire threshold (the lesser of 40% of its model's"
        " context window and the fixed 150,000-token _HANDOFF_NUDGE_ABS_CAP -- mirroring the hook's"
        " real behavior) are held fixed and are NOT backtested by this report -- only re-arm spacing"
        " past the first fire varies."
    )
    if ramp_curve_output_tokens == 0:
        print(
            "\nWARNING: no priced output tokens found anywhere in scope, so the re-arm ramp curve"
            " could not be computed -- every re-armed remainder below is priced at $0.00/1k, not a"
            " genuinely cheap ramp."
        )

    header = f"{'Spacing':>10} {'Model':>12} {'$':>14} {'DeltaUSD':>10} {'C_bar':>10} {'DeltaCbar':>12}"
    print(f"\n{header}")
    print("-" * len(header))
    print(
        f"{'baseline':>10} {'actual':>12} {baseline_total:>14,.2f} {'--':>10}"
        f" {baseline_c_bar:>10,.0f} {'--':>12}"
    )

    for spacing in spacings:
        for compliance_label, lag in (("perfect", 0.0), ("realistic", median_lag)):
            main_dollars = 0.0
            context_weighted = 0.0
            weight = 0.0
            for s in sessions_data:
                dollars, c_weighted, w = _simulate_rearm_spacing(
                    s["main_thread_turns"], s["boundaries"], spacing, ramp_curve,
                    s["threshold"], response_lag_tokens=lag,
                )
                main_dollars += dollars
                context_weighted += c_weighted
                weight += w
            total = main_dollars + sidechain_dollars_total
            c_bar = (context_weighted / weight) if weight else 0.0
            delta = total - baseline_total
            delta_c_bar = c_bar - baseline_c_bar
            print(
                f"{spacing:>10,} {compliance_label:>12} {total:>14,.2f} {delta:>+10,.2f}"
                f" {c_bar:>10,.0f} {delta_c_bar:>+12,.0f}"
            )

    conversion = _nudge_conversion_from_log(session_traces, log_entries_by_root)
    fired = (
        conversion["voluntary"] + conversion["forced"]
        + conversion["blocked_no_handoff"] + conversion["no_compliance"]
    )
    print(f"\n## Nudge->handoff conversion ({title_since}, generated {today.isoformat()})\n")
    print(f"Fired sessions in scope: {fired:,} ({conversion['dropped']:,} dropped -- no in-scope trace)")

    bucket_header = f"{'Bucket':<24} {'Count':>8} {'Rate':>8}"
    print(f"\n{bucket_header}")
    print("-" * len(bucket_header))
    for label, key in (
        ("voluntary", "voluntary"),
        ("forced", "forced"),
        ("blocked-no-handoff", "blocked_no_handoff"),
        ("no-compliance-observed", "no_compliance"),
    ):
        count = conversion[key]
        print(f"{label:<24} {count:>8,} {_pct_of(count, fired):>8}")

    converted = conversion["voluntary"] + conversion["forced"]
    block_reach = conversion["forced"] + conversion["blocked_no_handoff"]
    print(
        f"\nConversion rate (voluntary + forced / fired): {_pct_of(converted, fired)}"
        f" ({converted:,}/{fired:,})"
    )
    print(
        f"Block-reach rate (forced + blocked-no-handoff / fired): {_pct_of(block_reach, fired)}"
        f" ({block_reach:,}/{fired:,})"
    )
    print(
        "Join validity (fired sessions with a matching handoff line):"
        f" {conversion['join_validity']:,}"
    )

    ignored_values = conversion["ignored_values"]
    if ignored_values:
        median_ignored = statistics.median(ignored_values)
        print(
            f"Re-arms tolerated at voluntary compliance: median ignored={median_ignored:,.0f}"
            f" across {len(ignored_values):,} voluntary session(s)"
            f" ({conversion['no_ignored_field']:,} voluntary session(s) missing ignored=)"
        )
    else:
        print(
            "Re-arms tolerated at voluntary compliance: no voluntary session(s) with ignored="
            f" present ({conversion['no_ignored_field']:,} voluntary session(s) missing ignored=)"
        )


# --- plan-boundary: continue-vs-switch-vs-handoff repricing at the plan boundary ---

_PLAN_BOUNDARY_SONNET_MODEL = "claude-sonnet-5"


def _plan_boundary_turn_index(
    deduped: Sequence[dict], main_thread_record_positions: Sequence[int]
) -> int | None:
    """0-indexed main_thread_turns position of a session's plan boundary -- the
    FIRST main-thread assistant turn that calls ExitPlanMode or invokes the
    plan-review Skill.

    - First occurrence wins: a later ExitPlanMode/plan-review call is
      re-planning inside work this measurement already treats as post-boundary.
    - A sidechain occurrence of either signal is ignored.
    - Returns None when no such turn exists, or when the triggering record's
      "deduped" index has no matching entry in main_thread_record_positions --
      an unmapped boundary can't be repriced.
    """
    record_index_to_turn_index = {pos: i for i, pos in enumerate(main_thread_record_positions)}
    for record_index, rec in enumerate(deduped):
        if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
            continue
        content = (rec.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            is_plan_review = name == "Skill" and (block.get("input") or {}).get("skill") == "plan-review"
            if name == "ExitPlanMode" or is_plan_review:
                return record_index_to_turn_index.get(record_index)
    return None


def _arm_b_boundary_plus_one_dollars(usage: dict, boundary_context_tokens: int) -> float:
    """Arm B's boundary+1 turn: a Sonnet cache-write over the boundary
    context (never a scaled cache-read -- the prompt cache is model-keyed, so
    a model switch forces a full miss) plus Sonnet input/output on this
    turn's own new tokens, with the write priced at the 5m tier per
    _cache_write_split's own no-split fallback.
    """
    rates = _model_rates(_PLAN_BOUNDARY_SONNET_MODEL)
    input_t = int(usage.get("input_tokens", 0))
    output_t = int(usage.get("output_tokens", 0))
    return (
        boundary_context_tokens / 1_000_000 * rates["cache_write_5m"]
        + input_t / 1_000_000 * rates["input"]
        + output_t / 1_000_000 * rates["output"]
    )


def _arm_b_later_turn_dollars(usage: dict) -> float:
    """Arm B's own turns after boundary+1: the observed read/write split
    carried forward unchanged, priced at Sonnet rates instead of the turn's
    real (Opus) model."""
    dollars_by_class, _context_at_turn, _turn_unpriced_tokens = _price_turn(_PLAN_BOUNDARY_SONNET_MODEL, usage)
    return sum(dollars_by_class.values())


def _arm_c_turn_dollars(output_tokens: int, turns_since_boundary: int, ramp_curve: dict[str, dict[str, float]]) -> float:
    """Arm C's (fresh Sonnet handoff) post-boundary turn: (output_tokens/1000)
    * the ramp curve's own bucket rate for this many turns since a fresh
    session start -- _ramp_curve_from_corpus' own multiply-back convention,
    mirroring _simulate_rearm_spacing's non-actual-epoch branch. Never scales
    the turn's actual observed dollars: those already embed both the
    model-price gap and the context-growth gap, so scaling would double-count.
    `ramp_curve` is expected to be Sonnet-scoped (see _plan_boundary_report),
    since this arm models a fresh Sonnet session.
    """
    label = _ramp_curve_turn_index_bucket(turns_since_boundary)
    bucket = ramp_curve.get(label, {"rate": 0.0, "mean_context": 0.0})
    return (output_tokens / 1000) * bucket["rate"]


def _plan_boundary_work_inflation_breakeven(
    cheaper_dollars: float, delta_dollars: float, post_boundary_turns: int, post_boundary_output_tokens: int
) -> dict[str, float | None]:
    """breakeven_pct = delta_dollars / cheaper_dollars, the fraction of extra
    work that closes the cheaper arm's dollar advantage to zero; all three
    fields are None when cheaper_dollars <= 0 (no observed rate to extrapolate from).
    """
    if cheaper_dollars <= 0:
        return {"pct": None, "extra_turns": None, "extra_output_tokens": None}
    pct = delta_dollars / cheaper_dollars
    return {
        "pct": pct,
        "extra_turns": pct * post_boundary_turns,
        "extra_output_tokens": pct * post_boundary_output_tokens,
    }


def cmd_plan_boundary(args: argparse.Namespace) -> None:
    """CLI entry point for the plan-boundary subcommand.

    Root resolution happens here, mirroring cmd_rearm_backtest --
    --config-dir validation exits before any scan work. The wall-clock date
    is read exactly once, here, mirroring cmd_rearm_backtest's own split.
    """
    roots = _resolve_cost_roots(args, subcommand="plan-boundary")
    _plan_boundary_report(args, datetime.now(UTC).date(), roots)


def _plan_boundary_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Aggregate-only report; see docs/transcript-analysis.md's plan-boundary
    section for arm definitions and output contract.

    roots is None only for this module's own tests exercising the report
    body directly; --config-dir CLI validation happens once in
    cmd_plan_boundary.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but a direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "plan-boundary: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)
    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    # Arms B and C reprice every post-boundary turn at
    # _PLAN_BOUNDARY_SONNET_MODEL's rates; checked once here, before scanning
    # any session, so an unpriced model fails the whole report up front
    # instead of crashing mid-scan on an arbitrary turn.
    if _model_rates(_PLAN_BOUNDARY_SONNET_MODEL) is None:
        print(
            f"plan-boundary: {_PLAN_BOUNDARY_SONNET_MODEL} has no _MODEL_BASE_INPUT_RATES entry --"
            " arms B and C cannot be priced",
            file=sys.stderr,
        )
        sys.exit(1)

    since_ts, since_raw = _parse_since_nd_arg(args, "plan-boundary")

    session_iter, scope_label = _resolve_project_scope(args, "plan-boundary", roots=roots)
    _print_resolved_scope("plan-boundary", scope_label, scan_roots)

    # Each in-scope session's records are deduped and priced exactly once,
    # via _extract_rearm_session_turns, mirroring _rearm_backtest_report's
    # own single-pass convention.
    scoped_sessions = [
        _extract_rearm_session_turns(records) for _jsonl, records in session_iter
        if _session_matches_rearm_scope(records, since_ts, None)
    ]

    # Scoped to Sonnet-anchored sessions since arm C models a fresh Sonnet
    # session, not a family-mixed average -- falls back to the pooled corpus
    # when that slice has no priced output tokens, mirroring
    # _ramp_curve_from_corpus's own zero-bucket fallback.
    sonnet_scoped_sessions = [
        session for session in scoped_sessions
        if session["main_thread_models"] and _fam(session["main_thread_models"][0]) == "sonnet"
    ]
    ramp_curve, ramp_curve_output_tokens = _ramp_curve_from_corpus(sonnet_scoped_sessions)
    if ramp_curve_output_tokens == 0:
        ramp_curve, ramp_curve_output_tokens = _ramp_curve_from_corpus(scoped_sessions)

    sessions_scanned = 0
    opus_anchored_sessions = 0
    no_boundary_sessions = 0
    boundary_is_final_turn_sessions = 0
    boundary_sessions = 0
    unpriced_turns = 0
    unpriced_tokens = 0

    corpus_arm_a_dollars = 0.0
    corpus_arm_b_dollars = 0.0
    corpus_arm_c_dollars = 0.0
    corpus_post_boundary_turns = 0
    corpus_post_boundary_output_tokens = 0

    real_switch_sessions = 0
    cache_miss_reason_counts: dict[str, int] = defaultdict(int)

    for data in scoped_sessions:
        sessions_scanned += 1
        unpriced_turns += data["unpriced_turns"]
        unpriced_tokens += data["unpriced_tokens"]

        main_thread_turns = data["main_thread_turns"]
        main_thread_priced = data["main_thread_priced"]
        main_thread_models = data["main_thread_models"]
        main_thread_record_positions = data["main_thread_record_positions"]
        deduped = data["deduped"]

        if not main_thread_turns or _fam(main_thread_models[0]) != "opus":
            continue
        opus_anchored_sessions += 1

        boundary_index = _plan_boundary_turn_index(deduped, main_thread_record_positions)
        if boundary_index is None:
            no_boundary_sessions += 1
            continue

        post_boundary_turns = main_thread_turns[boundary_index + 1:]
        if not post_boundary_turns:
            boundary_is_final_turn_sessions += 1
            continue
        boundary_sessions += 1

        boundary_context_tokens = main_thread_turns[boundary_index][0]

        arm_a_dollars = sum(d for _c, _o, d in post_boundary_turns)
        post_boundary_output_tokens = sum(o for _c, o, _d in post_boundary_turns)

        arm_b_dollars = 0.0
        arm_c_dollars = 0.0
        for offset, turn_index in enumerate(range(boundary_index + 1, len(main_thread_turns))):
            # Arm A already contributes $0 for an unpriced turn (its actual_dollars is
            # 0.0); arms B/C must match that $0 instead of repricing raw tokens.
            if not main_thread_priced[turn_index]:
                continue
            rec = deduped[main_thread_record_positions[turn_index]]
            usage = (rec.get("message") or {}).get("usage") or {}
            if offset == 0:
                arm_b_dollars += _arm_b_boundary_plus_one_dollars(usage, boundary_context_tokens)
            else:
                arm_b_dollars += _arm_b_later_turn_dollars(usage)
            _context_at_turn, output_tokens, _actual_dollars = main_thread_turns[turn_index]
            arm_c_dollars += _arm_c_turn_dollars(output_tokens, offset, ramp_curve)

        corpus_arm_a_dollars += arm_a_dollars
        corpus_arm_b_dollars += arm_b_dollars
        corpus_arm_c_dollars += arm_c_dollars
        corpus_post_boundary_turns += len(post_boundary_turns)
        corpus_post_boundary_output_tokens += post_boundary_output_tokens

        # Ground truth: does the boundary+1 turn show a real model switch,
        # and does Claude Code's own cache_miss_reason diagnostic agree --
        # context only, never fed into the repricing formula above.
        if main_thread_models[boundary_index + 1] != main_thread_models[boundary_index]:
            real_switch_sessions += 1
            boundary_plus_one_rec = deduped[main_thread_record_positions[boundary_index + 1]]
            reason = _cache_miss_reason(boundary_plus_one_rec.get("message") or {})
            cache_miss_reason_counts[reason or "(missing/malformed)"] += 1

    title_since = f"last {since_raw}" if since_raw else "all time"
    print(f"\n## Plan boundary report ({title_since}, generated {today.isoformat()})\n")
    print(f"Sessions scanned: {sessions_scanned:,}")
    print(f"Opus-anchored: {opus_anchored_sessions:,}")
    print(f"  No plan boundary detected: {no_boundary_sessions:,}")
    print(
        "  Boundary is the session's final main-thread turn"
        f" (excluded, no post-boundary work): {boundary_is_final_turn_sessions:,}"
    )
    print(f"  Plan-boundary sessions repriced: {boundary_sessions:,}")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
    if ramp_curve_output_tokens == 0:
        print(
            "\nWARNING: no priced output tokens found anywhere in scope, so arm C's ramp curve could"
            " not be computed -- its figures below are priced at $0.00/1k, not a genuinely cheap ramp."
        )

    if boundary_sessions == 0:
        print("\nNo plan-boundary sessions with post-boundary work found in scope.")
        return

    print(f"\nPost-boundary main-thread turns repriced: {corpus_post_boundary_turns:,}")
    print(f"Post-boundary output tokens repriced: {corpus_post_boundary_output_tokens:,}")

    header = f"{'Arm':<24} {'$':>14}"
    print(f"\n{header}")
    print("-" * len(header))
    print(f"{'A: continue on Opus':<24} {corpus_arm_a_dollars:>14,.2f}")
    print(f"{'B: switch to Sonnet':<24} {corpus_arm_b_dollars:>14,.2f}")
    print(f"{'C: fresh Sonnet handoff':<24} {corpus_arm_c_dollars:>14,.2f}")

    print("\n## Work-inflation breakeven\n")
    print(
        "How much extra Sonnet work (post-boundary turns/output tokens) the cheaper arm in each"
        " pair could absorb before its dollar advantage disappears -- the mitigation for the"
        " unverifiable assumption that Sonnet completes the same post-boundary work Opus did."
    )
    pairs = (
        ("A vs B", corpus_arm_a_dollars, corpus_arm_b_dollars),
        ("A vs C", corpus_arm_a_dollars, corpus_arm_c_dollars),
        ("B vs C", corpus_arm_b_dollars, corpus_arm_c_dollars),
    )
    for label, left_dollars, right_dollars in pairs:
        left_label, right_label = label.split(" vs ")
        if left_dollars <= right_dollars:
            cheaper_dollars, delta_dollars, winner = left_dollars, right_dollars - left_dollars, left_label
        else:
            cheaper_dollars, delta_dollars, winner = right_dollars, left_dollars - right_dollars, right_label
        breakeven = _plan_boundary_work_inflation_breakeven(
            cheaper_dollars, delta_dollars, corpus_post_boundary_turns, corpus_post_boundary_output_tokens
        )
        if breakeven["pct"] is None:
            print(f"{label}: cheaper arm ({winner}) has $0.00 post-boundary spend -- no rate to extrapolate")
            continue
        print(
            f"{label}: {winner} cheaper by ${delta_dollars:,.2f} -- breakeven at"
            f" +{breakeven['pct'] * 100:,.1f}% more work"
            f" (~{breakeven['extra_turns']:,.0f} extra turns, ~{breakeven['extra_output_tokens']:,.0f}"
            " extra output tokens)"
        )

    print("\n## Ground truth: real model switch at boundary+1\n")
    print(
        f"Sessions with a real model change observed at boundary+1: {real_switch_sessions:,}"
        f" of {boundary_sessions:,}"
    )
    if cache_miss_reason_counts:
        print("cache_miss_reason at boundary+1, for those sessions:")
        for reason in sorted(cache_miss_reason_counts):
            print(f"  {reason}: {cache_miss_reason_counts[reason]:,}")


# --- handoff-signal-response: mechanical audit of the handoff-nudge rationalization gap ---
# .claude/plans/handoff-nudge-rationalization-gap.md owns the full design.

# Named constants, not inline strings, so a signal's kind is never a
# copy-pasted literal. Read at three call sites: the OR-condition detector,
# the aggregate table, and the curation-card formatter.
_HANDOFF_SIGNAL_CHECK = "check"
_HANDOFF_SIGNAL_ADVISORY = "advisory"
_HANDOFF_SIGNAL_HARD_BLOCK = "hard-block"

# nudge-handoff-near-context-cap.sh's own script basename, matched
# post-basename since the real invocation is always a tilde or absolute path
# -- mirrors _DENIAL_COMMAND_MULTIPLEXERS' own convention for marker.sh.
_HANDOFF_SIGNAL_HOOK_BASENAME = "nudge-handoff-near-context-cap.sh"

# The hard-block stderr message's own stable substring
# (nudge-handoff-near-context-cap.sh's printf, around line 647), distinct
# from the advisory clause's own text so it can't cross-match.
_HANDOFF_SIGNAL_HARD_BLOCK_TEXT = "handoff-nudge hard-block point"

# A /handoff write's own file-path shape: handoff/SKILL.md writes
# "<config-dir>/handoffs/<slug>-handoff.md".
_HANDOFF_SIGNAL_WRITE_PATH_RE = re.compile(r"/handoffs/[^/]+-handoff\.md$")

# Long enough to carry a full rationalization sentence on a curation card,
# short enough to keep the card scannable -- a display truncation, not a
# protocol-grounded value.
_HANDOFF_SIGNAL_EXCERPT_MAX_CHARS = 400

# Long enough to carry a full reasoning paragraph in a --context-turns
# entry, short enough to keep --sample output bounded -- a display
# truncation, not a protocol-grounded value.
_HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS = 1000


def _handoff_signal_bash_check_call(block: dict) -> bool:
    """True iff `block` is a Bash tool_use invoking
    nudge-handoff-near-context-cap.sh --check, in any &&/;/|-chained segment."""
    if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash"):
        return False
    command = (block.get("input") or {}).get("command", "") or ""
    for segment in corpus.split_command_segments(command):
        if segment and os.path.basename(segment[0]) == _HANDOFF_SIGNAL_HOOK_BASENAME and "--check" in segment[1:]:
            return True
    return False


def _handoff_signal_marker_transition(block: dict) -> str | None:
    """Return "activate"/"deactivate" iff `block` is a Bash tool_use invoking
    `marker.sh (activate|deactivate) ready-for-review`, else None. Best-effort,
    like every other command-shape classifier in this file: an unrecognized
    wrapping (an alias, a function) is silently missed."""
    if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash"):
        return None
    command = (block.get("input") or {}).get("command", "") or ""
    for segment in corpus.split_command_segments(command):
        if len(segment) < 3 or os.path.basename(segment[0]) != "marker.sh":
            continue
        if segment[1] in ("activate", "deactivate") and segment[2] == "ready-for-review":
            return segment[1]
    return None


def _handoff_signal_is_handoff_event(block: dict) -> bool:
    """True iff `block` is a same-session handoff event: a `/handoff` Skill
    invocation, or a Write/Edit whose file_path is a
    `<config-dir>/handoffs/<slug>-handoff.md` write."""
    if not (isinstance(block, dict) and block.get("type") == "tool_use"):
        return False
    name = block.get("name")
    inp = block.get("input") or {}
    if name == "Skill" and inp.get("skill") == "handoff":
        return True
    if name in ("Write", "Edit"):
        return bool(_HANDOFF_SIGNAL_WRITE_PATH_RE.search(inp.get("file_path", "") or ""))
    return False


def _handoff_signal_is_eligible_main_thread_turn(rec: dict) -> bool:
    """True iff `rec` is a main-thread assistant turn: type=="assistant",
    not isSidechain. Shared by _handoff_signal_excerpt_eligible_text and
    _handoff_signal_forward_context, whose turn-walking loops both need
    the same main-thread-turn definition."""
    return rec.get("type") == "assistant" and not bool(rec.get("isSidechain"))


def _handoff_signal_excerpt_eligible_text(rec: dict) -> str:
    """Excerpt-eligible: main-thread assistant `text` blocks only, never
    tool_use/tool_result/user records or sidechain turns."""
    if not _handoff_signal_is_eligible_main_thread_turn(rec):
        return ""
    content = (rec.get("message") or {}).get("content") or []
    if not isinstance(content, list):
        return ""
    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    return " ".join(texts)


def _handoff_signal_excerpt(deduped: Sequence[dict], after_record_index: int) -> str:
    """First excerpt-eligible text strictly after `after_record_index` in
    `deduped` (see _handoff_signal_excerpt_eligible_text), truncated to
    _HANDOFF_SIGNAL_EXCERPT_MAX_CHARS; "" when no eligible record follows."""
    for rec in deduped[after_record_index + 1:]:
        text = _handoff_signal_excerpt_eligible_text(rec)
        if text:
            return text[:_HANDOFF_SIGNAL_EXCERPT_MAX_CHARS]
    return ""


def _handoff_signal_forward_context(deduped: Sequence[dict], after_record_index: int, n_turns: int) -> list[dict]:
    """Forward-context window for a --context-turns caller:
    - up to `n_turns` main-thread turns strictly after `after_record_index` in `deduped`
    - same main-thread-turn definition as _handoff_signal_response_session_rows' own
      main_thread_turns: type=="assistant", not isSidechain
    - each entry carries both text AND thinking content -- unlike
      _handoff_signal_excerpt_eligible_text (text blocks only), reading both
      content-block kinds lets a caller see reasoning an agent confined to an
      extended-thinking block, invisible to the base excerpt
    - one entry per turn visited, even when both fields are empty, keeping
      turn_offset (1-based) stable
    - stops early once `n_turns` turns have been visited or the transcript
      runs out, whichever comes first
    - never includes session_id/jsonl_path or any other identifying field
    """
    if n_turns <= 0:
        return []
    contexts: list[dict] = []
    for rec in deduped[after_record_index + 1:]:
        if not _handoff_signal_is_eligible_main_thread_turn(rec):
            continue
        content = (rec.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            content = []
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
        thinkings = [
            b.get("thinking", "") for b in content
            if isinstance(b, dict) and b.get("type") == "thinking" and b.get("thinking")
        ]
        contexts.append({
            "turn_offset": len(contexts) + 1,
            "text": " ".join(texts)[:_HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS],
            "thinking": " ".join(thinkings)[:_HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS],
        })
        if len(contexts) >= n_turns:
            break
    return contexts


def _handoff_signal_response_session_rows(records: Sequence[dict]) -> tuple[list[dict], list[dict], list[int]]:
    """Detect every observed context-budget signal in one session's own
    transcript. Returns (rows, deduped, trace):
    - rows: one dict per signal (kind, record_index, position, context_at_turn,
      threshold, marker_active, handoff_followed, turns_after_signal,
      dollars_after_signal, session_total_dollars, pct_spend_after_signal)
      -- session_id is not included; the caller attaches it (this function
      has no I/O, so it never resolves jsonl.stem).
      -- exceeds_startup_burn_benchmark is not included either: it depends on
      a corpus-wide benchmark the caller alone can compute
      (_startup_burn_benchmark), not on anything local to one session.
    - deduped: this session's own _dedup_turns_by_request_id output, returned
      so a caller building curation-card excerpts can search forward from a
      row's own record_index without re-deduping the session a second time.
    - trace: one abs-token estimate (context_at_turn + output_tokens, the
      hook's own ESTIMATE unit) per main-thread turn, in
      _rearm_backtest_report's own session_traces shape -- lets a caller feed
      _operator_response_lag_from_log without a second dedup+price pass.

    A single ordered pass over `records` (post-dedup) tracks, in parallel:
    - running main-thread turn context/output/dollars (`_price_turn`, the
      same primitive every sibling subcommand in this file uses for this)
    - the `ready-for-review` active-marker state (marker.sh
      activate/deactivate Bash calls)
    - pending `--check` tool_use ids awaiting their tool_result
    - every same-session handoff event

    Each signal row captures the running marker-active state AS OF that
    point in the pass, not the state by the end of the session.
    """
    deduped = _dedup_turns_by_request_id(records)

    main_thread_turns: list[tuple[int, int, float]] = []  # (context_at_turn, output_tokens, dollars)
    main_thread_models: list[str] = []
    marker_active = False
    pending_check_calls: dict[str, int] = {}  # tool_use_id -> turn_index at call time
    handoff_record_indices: list[int] = []
    signals: list[dict] = []

    for record_index, rec in enumerate(deduped):
        rec_type = rec.get("type")

        if rec_type == "attachment":
            att = rec.get("attachment") or {}
            att_type = att.get("type")
            if att_type == "hook_success" and os.path.basename(att.get("command") or "") == _HANDOFF_SIGNAL_HOOK_BASENAME:
                try:
                    payload = json.loads(att.get("stdout") or "")
                except (json.JSONDecodeError, ValueError):
                    payload = {}
                additional_context = (payload.get("hookSpecificOutput") or {}).get("additionalContext")
                if additional_context:
                    signals.append({
                        "kind": _HANDOFF_SIGNAL_ADVISORY,
                        "record_index": record_index,
                        "turn_index": len(main_thread_turns),
                        "marker_active": marker_active,
                    })
            elif att_type == "hook_stopped_continuation" and _HANDOFF_SIGNAL_HARD_BLOCK_TEXT in (att.get("message") or ""):
                signals.append({
                    "kind": _HANDOFF_SIGNAL_HARD_BLOCK,
                    "record_index": record_index,
                    "turn_index": len(main_thread_turns),
                    "marker_active": marker_active,
                })
            continue

        if rec_type == "user":
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                        continue
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id is None or tool_use_id not in pending_check_calls:
                        continue
                    turn_index = pending_check_calls.pop(tool_use_id)
                    try:
                        payload = json.loads(_content_text(block.get("content")))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if payload.get("status") != "ok":
                        continue
                    if payload.get("over_threshold") or payload.get("already_fired"):
                        signals.append({
                            "kind": _HANDOFF_SIGNAL_CHECK,
                            "record_index": record_index,
                            "turn_index": turn_index,
                            "marker_active": marker_active,
                        })
            continue

        if rec_type != "assistant" or bool(rec.get("isSidechain")):
            continue

        msg = rec.get("message") or {}
        usage = msg.get("usage")
        if usage:
            model = msg.get("model", "")
            dollars_by_class, context_at_turn, _unpriced_tokens = _price_turn(model, usage)
            dollars = sum(dollars_by_class.values()) if dollars_by_class is not None else 0.0
            main_thread_turns.append((context_at_turn, int(usage.get("output_tokens", 0)), dollars))
            main_thread_models.append(model)

        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            transition = _handoff_signal_marker_transition(block)
            if transition == "activate":
                marker_active = True
            elif transition == "deactivate":
                marker_active = False
            if _handoff_signal_bash_check_call(block):
                tool_use_id = block.get("id")
                if tool_use_id:
                    pending_check_calls[tool_use_id] = len(main_thread_turns)
            if _handoff_signal_is_handoff_event(block):
                handoff_record_indices.append(record_index)

    total_turns = len(main_thread_turns)
    # Suffix sums: O(total_turns) once, vs. O(signals × turns) if re-summed per signal.
    suffix_dollars = [0.0] * (total_turns + 1)
    for i in range(total_turns - 1, -1, -1):
        suffix_dollars[i] = suffix_dollars[i + 1] + main_thread_turns[i][2]

    rows: list[dict] = []
    for sig in signals:
        turn_index = sig["turn_index"]
        if turn_index > 0:
            context_at_turn = main_thread_turns[turn_index - 1][0]
            threshold = _hook_effective_fire_threshold(main_thread_models[turn_index - 1])
        else:
            # Defensive edge case, not expected in practice: every real fire
            # already read a usage block before firing, so turn_index is 0
            # only for a malformed/synthetic fixture with no prior usage.
            context_at_turn, threshold = 0, None
        rows.append({
            "kind": sig["kind"],
            "record_index": sig["record_index"],
            "position": turn_index,
            "context_at_turn": context_at_turn,
            "threshold": threshold,
            "marker_active": sig["marker_active"],
            "handoff_followed": any(idx > sig["record_index"] for idx in handoff_record_indices),
            "turns_after_signal": total_turns - turn_index,
            "dollars_after_signal": suffix_dollars[turn_index],
            "session_total_dollars": suffix_dollars[0],
            "pct_spend_after_signal": (
                suffix_dollars[turn_index] / suffix_dollars[0] if suffix_dollars[0] > 0 else None
            ),
        })

    trace = [c + o for c, o, _d in main_thread_turns]
    return rows, deduped, trace


def _rank_signal_rows_by_spend(rows: list[dict], sample_n: int, seed: int | None) -> list[dict]:
    """Return the top `sample_n` rows descending by `dollars_after_signal`,
    with a seeded pre-shuffle tie-break; with no seed, ties keep input order."""
    if seed is not None:
        rng = random.Random(seed)
        rows = list(rows)
        rng.shuffle(rows)
    return sorted(rows, key=lambda row: row["dollars_after_signal"], reverse=True)[:sample_n]


def _handoff_signal_response_cards(sampled: list[dict], redact: bool, context_turns: int = 0) -> list[dict]:
    """Attach a curation-card excerpt to each sampled row, re-reading only
    the sampled rows' own sessions (not the whole scanned corpus) -- see
    _handoff_signal_excerpt's own eligibility rule. `redact` controls
    whether each card's session id is replaced by a run-scoped opaque
    label (_assign_session_redact_label/_redact_session_id, the same
    mechanism cost's own per-row redaction uses). `context_turns` > 0 adds
    a "forward_context" key (_handoff_signal_forward_context) to each card;
    0 (the default) omits the key entirely, so every existing call site's
    card shape is unchanged."""
    session_redact_map: dict[str, str] = {}
    deduped_cache: dict[Path, list[dict]] = {}
    cards: list[dict] = []
    for row in sampled:
        jsonl_path: Path = row["jsonl_path"]
        deduped = deduped_cache.get(jsonl_path)
        if deduped is None:
            deduped = _dedup_turns_by_request_id(corpus.read_session_file(jsonl_path, include_subagents=False))
            deduped_cache[jsonl_path] = deduped
        excerpt = _handoff_signal_excerpt(deduped, row["record_index"])
        session_id = row["session_id"]
        if redact:
            _assign_session_redact_label(session_id, session_redact_map)
            session_id = _redact_session_id(session_id, session_redact_map)
        card = {
            "session_id": session_id,
            "kind": row["kind"],
            "position": row["position"],
            "context_at_turn": row["context_at_turn"],
            "threshold": row["threshold"],
            "marker_active": row["marker_active"],
            "handoff_followed": row["handoff_followed"],
            "turns_after_signal": row["turns_after_signal"],
            "dollars_after_signal": round(row["dollars_after_signal"], 2),
            "session_total_dollars": round(row["session_total_dollars"], 2),
            "pct_spend_after_signal": (
                round(row["pct_spend_after_signal"], 4) if row["pct_spend_after_signal"] is not None else None
            ),
            "exceeds_startup_burn_benchmark": row["exceeds_startup_burn_benchmark"],
            "excerpt": excerpt,
        }
        if context_turns:
            card["forward_context"] = _handoff_signal_forward_context(deduped, row["record_index"], context_turns)
        cards.append(card)
    return cards


def _startup_burn_benchmark(workstream: dict[str, dict]) -> tuple[float | None, int, int]:
    """Session-count-weighted average of startup-burn dollars
    (_compute_workstream_dollars' own startup_burn_dollars) across every
    branch in scope -- not an unweighted per-branch average, which would let
    a low-continuation branch skew the result.

    Returns (benchmark_dollars, total_continuations, branch_count), where
    branch_count is every branch with corpus activity in scope (len(workstream),
    _compute_workstream_dollars' own population), not only branches with a
    continuation session. Returns None for benchmark_dollars when no branch
    has a non-first session to sum, to avoid a ZeroDivisionError.
    """
    total_burn = sum(agg["startup_burn_dollars"] for agg in workstream.values())
    total_continuations = sum(max(agg["session_count"] - 1, 0) for agg in workstream.values())
    benchmark = total_burn / total_continuations if total_continuations > 0 else None
    return benchmark, total_continuations, len(workstream)


def _format_startup_burn_benchmark(benchmark_dollars: float | None) -> str:
    """Shared "$X.XX per continuation session" / unavailable phrasing for
    the startup-burn benchmark -- used by both the aggregate report and the
    curation-card markdown header, so the two surfaces never drift apart."""
    if benchmark_dollars is None:
        return "unavailable (no continuation sessions found in scope)"
    return f"{_fmt_usd(benchmark_dollars)} per continuation session"


def _format_forward_context_turns_markdown(forward_context: list[dict]) -> str:
    """Render a card's own "forward_context" list (--context-turns only) as
    a markdown subsection; a turn whose text and thinking are both empty is
    skipped entirely to keep the card scannable. Caller only invokes this
    when the key is present on the card -- an empty (but present) list
    still renders a header, distinct from the key being absent."""
    if not forward_context:
        return "**Forward context:** (no eligible turns followed the signal)\n\n"
    lines = [f"**Forward context (next {len(forward_context)} turn(s)):**\n"]
    for turn in forward_context:
        text = turn.get("text") or ""
        thinking = turn.get("thinking") or ""
        if not text and not thinking:
            continue
        pieces = []
        if thinking:
            pieces.append(f"thinking: {thinking}")
        if text:
            pieces.append(f"text: {text}")
        lines.append(f"- turn +{turn['turn_offset']}: {' / '.join(pieces)}\n")
    lines.append("\n")
    return "".join(lines)


def _format_handoff_signal_cards_as_markdown(
    cards: list[dict], *, sample_n: int, seed: int | None, benchmark_dollars: float | None,
) -> str:
    """Return a full markdown document for human curation of
    handoff-signal-response --sample output, mirroring
    _format_samples_as_markdown's own curation-card shape."""
    today = date.today().isoformat()
    seed_display = str(seed) if seed is not None else "(none)"
    header = (
        f"# handoff-signal-response curation — {len(cards)} signal(s)\n"
        f"\n"
        f"Generated: {today}  ·  Filter: `--sample {sample_n}  --seed {seed_display}`\n"
        f"\n"
        f"Startup-burn benchmark (this scope): {_format_startup_burn_benchmark(benchmark_dollars)}.\n"
        f"\n"
        f"For each signal: read the excerpt (the agent's own next eligible text turn after the"
        f" signal, if any), then check ONE verdict box.\n"
    )
    sections: list[str] = []
    total = len(cards)
    for i, card in enumerate(cards):
        excerpt = card["excerpt"] or "(no eligible assistant text turn followed the signal)"
        threshold_display = f"{card['threshold']:,}" if card["threshold"] is not None else "n/a"
        pct_display = (
            f"{card['pct_spend_after_signal'] * 100:.1f}%" if card["pct_spend_after_signal"] is not None else "n/a"
        )
        exceeds_display = (
            "n/a" if card["exceeds_startup_burn_benchmark"] is None
            else ("yes" if card["exceeds_startup_burn_benchmark"] else "no")
        )
        forward_context_block = (
            _format_forward_context_turns_markdown(card["forward_context"]) if "forward_context" in card else ""
        )
        section = (
            f"## {i + 1}/{total} — session `{card['session_id']}` — {card['kind']} signal at turn {card['position']}\n"
            f"\n"
            f"- context_at_turn: {card['context_at_turn']:,}  ·  threshold: {threshold_display}\n"
            f"- ready-for-review marker active: {card['marker_active']}\n"
            f"- handoff followed (same session): {card['handoff_followed']}\n"
            f"- turns after signal: {card['turns_after_signal']:,}  ·  $ after signal: {card['dollars_after_signal']:,.2f}\n"
            f"- % of session spend after signal: {pct_display}  ·  exceeds startup-burn benchmark: {exceeds_display}\n"
            f"\n"
            f"**Excerpt:**\n"
            f"> {excerpt}\n"
            f"\n"
            f"{forward_context_block}"
            f"Verdict: [ ] cost-grounded  [ ] step-count/\"nearly-done\" (no cost reasoning)  "
            f"[ ] handed off  [ ] unclassifiable\n"
        )
        sections.append(section)
    return header + "\n" + "\n".join(sections)


def _handoff_signal_response_aggregate_report(
    rows: Sequence[dict], log_diagnostic: str | None,
    benchmark_dollars: float | None,
) -> None:
    """Print the census-mode aggregate report: signal counts, conversion
    rate, and post-signal spend distribution split by signal kind and by
    marker-active context.

    benchmark_dollars is the corpus-wide startup-burn benchmark
    (_startup_burn_benchmark), pre-computed by the caller from a second,
    independent scope pass -- this function never recomputes it from rows."""
    total = len(rows)
    print(f"\n## Handoff signal response ({total:,} signal(s) in scope)\n")
    print(f"Startup-burn benchmark (this scope): {_format_startup_burn_benchmark(benchmark_dollars)}.")
    if not total:
        print("No signals found in scope.")
        return

    sessions_with_signal = len({r["session_id"] for r in rows})
    followed = sum(1 for r in rows if r["handoff_followed"])
    print(f"Sessions with at least one signal: {sessions_with_signal:,}")
    print(
        "Conversion rate (a same-session /handoff followed the signal):"
        f" {_pct_of(followed, total)} ({followed:,}/{total:,})"
    )
    if benchmark_dollars is not None:
        exceeding = sum(1 for r in rows if r["exceeds_startup_burn_benchmark"])
        print(
            "Signals whose post-signal spend exceeded the benchmark:"
            f" {exceeding:,} ({_pct_of(exceeding, total)})"
        )
    if log_diagnostic:
        print(f"\n{log_diagnostic}")

    def _print_breakdown(title: str, key) -> None:
        print(f"\n### {title}\n")
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        header = f"{'Group':<14} {'Signals':>8} {'Handoff%':>9} {'Median $ after':>15}"
        print(header)
        print("-" * len(header))
        for label in sorted(groups):
            group_rows = groups[label]
            n = len(group_rows)
            hf = sum(1 for r in group_rows if r["handoff_followed"])
            dollars = [r["dollars_after_signal"] for r in group_rows]
            median = statistics.median(dollars) if dollars else 0.0
            print(f"{label:<14} {n:>8,} {_pct_of(hf, n):>9} {median:>15,.2f}")

    _print_breakdown("By signal kind", lambda r: r["kind"])
    _print_breakdown(
        "By ready-for-review active-marker context",
        lambda r: "active" if r["marker_active"] else "inactive",
    )


def cmd_handoff_signal_response(args: argparse.Namespace) -> None:
    """CLI entry point for the handoff-signal-response subcommand.

    Uses the shared `_resolve_scan_roots` scope machinery, not the
    cost-family per-subcommand `--config-dir` extras.

    Resolves scope a second time to feed `_compute_workstream_dollars`,
    since `session_iter` above is a single-pass generator already consumed
    by the main loop. Every other two-pass subcommand in this file (e.g.
    cost-ledger) accepts the same tradeoff.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    roots = _resolve_scan_roots(args)
    multi_root = len(roots) > 1
    sample_n: int = getattr(args, "sample", 0) or 0
    context_turns: int = getattr(args, "context_turns", 0) or 0

    if not redact and multi_root:
        print(
            "handoff-signal-response: --no-redact is refused when more than one root is in scope;"
            " drop --no-redact or scope to a single root (e.g. --this-repo with no additional"
            " declared roots)",
            file=sys.stderr,
        )
        sys.exit(2)
    if context_turns and not sample_n:
        print("handoff-signal-response: --context-turns requires --sample", file=sys.stderr)
        sys.exit(2)
    if context_turns < 0:
        print("handoff-signal-response: --context-turns must not be negative", file=sys.stderr)
        sys.exit(2)
    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = _resolve_project_scope(args, "handoff-signal-response", roots=roots)
    _print_resolved_scope("handoff-signal-response", scope_label, roots)

    seed: int | None = getattr(args, "seed", None)

    all_rows: list[dict] = []
    session_traces: dict[str, list[int]] = {}
    for jsonl, records in session_iter:
        session_id = jsonl.stem
        rows, _deduped, trace = _handoff_signal_response_session_rows(records)
        for row in rows:
            row["session_id"] = session_id
            row["jsonl_path"] = jsonl
        all_rows.extend(rows)
        if trace:
            session_traces[session_id] = trace

    benchmark_session_iter, _benchmark_scope_label = _resolve_project_scope(
        args, "handoff-signal-response", roots=roots
    )
    workstream = _compute_workstream_dollars(benchmark_session_iter)
    benchmark_dollars, _, _ = _startup_burn_benchmark(workstream)
    for row in all_rows:
        row["exceeds_startup_burn_benchmark"] = (
            row["dollars_after_signal"] > benchmark_dollars if benchmark_dollars is not None else None
        )

    # Corroborating diagnostic only -- every row above already comes from
    # this session's own transcript, never from this log. Mirrors
    # _rearm_backtest_report's own "Operator-response-lag sample" line.
    log_entries = _parse_nudge_log_entries(config_dir() / ".handoff-nudge.log")
    lags, excluded = _operator_response_lag_from_log(session_traces, log_entries)
    log_diagnostic: str | None = None
    if lags:
        median_lag = statistics.median(lags)
        log_diagnostic = (
            f"Operator-response-lag cross-check (.handoff-nudge.log 'nudged' lines): {len(lags):,}"
            f" joined ({excluded:,} excluded -- no matching session in scope), median lag"
            f" {median_lag:,.0f} tokens past the fire point"
        )

    if sample_n:
        # Rank by post-signal spend, since that is where a wrong
        # continue-decision actually cost something -- not the whole
        # population. See _rank_signal_rows_by_spend's own docstring for the
        # tie-break contract.
        sampled = _rank_signal_rows_by_spend(all_rows, sample_n, seed)
        cards = _handoff_signal_response_cards(sampled, redact, context_turns=context_turns)
        output_format: str = getattr(args, "output_format", "json") or "json"
        if output_format == "md":
            print(_format_handoff_signal_cards_as_markdown(
                cards, sample_n=sample_n, seed=seed, benchmark_dollars=benchmark_dollars,
            ))
        else:
            print(json.dumps(cards, indent=2))
        return

    for row in all_rows:
        row.pop("jsonl_path", None)
        row.pop("record_index", None)
    _handoff_signal_response_aggregate_report(
        all_rows, log_diagnostic, benchmark_dollars=benchmark_dollars,
    )


def _add_project_scope_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared --projects/--this-repo scope flags to a subparser.

    Mutually exclusive: --this-repo routes through _repo_scoped_project_slugs(),
    an identity-based minimization control; --projects keeps the pre-existing
    machine-wide glob default ("*") so no existing invocation's behavior changes.
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--projects", default="*", type=_single_level_projects_glob, metavar="GLOB")
    group.add_argument(
        "--this-repo", action="store_true",
        help="Scope to this repo's own worktrees only (see docs/transcript-analysis.md).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude Code transcript analysis toolkit.")
    # Top-level (not per-subcommand) so main() can reassign PROJECTS_DIR before
    # any subcommand runs, regardless of which one was chosen. Resolving the
    # path inside the script, via a plain CLI flag, rather than through a
    # `CLAUDE_CONFIG_DIR=... python3 ...` shell prefix keeps a non-personal
    # account's ~/.config/claude-accounts/<account> path out of the env-var-
    # assignment shape Claude Code's Bash permission classifier denies on.
    parser.add_argument(
        "--config-dir", metavar="PATH", default=None,
        help=(
            "Resolve sessions under PATH/projects instead of the default "
            "Claude Code config dir (CLAUDE_CONFIG_DIR, or ~/.claude). Must "
            "precede the subcommand name."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_buckets = sub.add_parser("buckets", help="Assistant turns bucketed by gitBranch × model family.")
    _add_project_scope_args(p_buckets)
    p_buckets.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_buckets.set_defaults(func=cmd_buckets)

    p_fail = sub.add_parser("fail-seq", help="Ordered test-run failed-count sequence per branch/model.")
    p_fail.add_argument("--branches", required=True, metavar="B1,B2,...")
    _add_project_scope_args(p_fail)
    p_fail.set_defaults(func=cmd_fail_seq)

    p_struggle = sub.add_parser("struggle", help="Correction/frustration signal phrases in user turns, split by model.")
    p_struggle.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_struggle)
    p_struggle.set_defaults(func=cmd_struggle)

    p_user_input = sub.add_parser(
        "user-input",
        help="All fresh user prompts per session, classified as initial / followup / explicit-correction.",
    )
    p_user_input.add_argument("--projects", default="*", type=_single_level_projects_glob, metavar="GLOB")
    p_user_input.add_argument("--branches", metavar="B1,B2,...")
    p_user_input.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_user_input.add_argument("--until", metavar="DATE", type=_iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_user_input.add_argument(
        "--corrections-only", action="store_true",
        help="Show only non-initial prompts.",
    )
    p_user_input.add_argument(
        "--truncate-chars", type=int, default=500, metavar="N",
        help="Truncate prompt text at N chars (0 = no truncation).",
    )
    p_user_input.add_argument("--out", metavar="PATH", help="Write output to a file instead of stdout.")
    p_user_input.add_argument(
        "--redact", action="store_true",
        help=(
            "Anonymize project labels and session IDs for public reporting "
            "(prompt text is not redacted — review before sharing)."
        ),
    )
    p_user_input.set_defaults(func=cmd_user_input)

    p_duration = sub.add_parser("duration", help="Active span vs idle-gap decomposition per branch.")
    p_duration.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_duration)
    p_duration.add_argument("--gap-minutes", type=int, default=30, metavar="N")
    p_duration.set_defaults(func=cmd_duration)

    p_sub = sub.add_parser(
        "subagents",
        help=(
            "isSidechain turn counts and model split per branch, plus tool-result bytes"
            " per thread and per tool name."
        ),
    )
    p_sub.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_sub)
    p_sub.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Branch names are"
            " redacted and _DO_NOT_PUBLISH_BANNER is printed whenever more than one root is in scope."
        ),
    )
    p_sub.add_argument(
        "--since", metavar="Nd",
        help="Limit the reported tables to records with timestamp in the last N days (e.g. 35d).",
    )
    p_sub.set_defaults(func=cmd_subagents)

    p_mix = sub.add_parser(
        "subagent-mix",
        help=(
            "Subagent_type spawn counts per branch, with code/plan/ready-for-review skill"
            " invocations, plus a per-agentType observed/requested/declared model-mix table."
        ),
    )
    p_mix.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_mix)
    p_mix.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Refused together with --per-session."
            " Branch names are redacted and _DO_NOT_PUBLISH_BANNER is printed whenever more than"
            " one root is in scope."
        ),
    )
    p_mix.add_argument(
        "--since", metavar="Nd",
        help="Limit the reported tables to records with timestamp in the last N days (e.g. 35d).",
    )
    p_mix.add_argument(
        "--per-session",
        action="store_true",
        help="Break out by individual session instead of aggregating per branch. Refused under --config-dir.",
    )
    p_mix.add_argument(
        "--since-date", metavar="DATE", type=_iso_date,
        help=(
            "Inclusive start date (YYYY-MM-DD) for the Actual $ / Counterfactual $ columns only,"
            " filtered per sidechain record — independent of --since Nd, which keeps its existing"
            " dispatch-level scope over every other column."
        ),
    )
    p_mix.add_argument(
        "--until-date", metavar="DATE", type=_iso_date,
        help="Inclusive end date (YYYY-MM-DD) for the Actual $ / Counterfactual $ columns only — see --since-date.",
    )
    p_mix.add_argument(
        "--reprice-as", metavar="MODEL_ID",
        help=(
            "Re-price each in-window dispatch's dollars at this model ID instead of its own real"
            " model, adding Counterfactual $ and Delta columns. Must be a key in"
            " _MODEL_BASE_INPUT_RATES; an unknown value is rejected listing the valid IDs."
        ),
    )
    p_mix.set_defaults(func=cmd_subagent_mix)

    p_reviewer_yield = sub.add_parser(
        "reviewer-yield",
        help=(
            "Per-reviewer-agent-type dispatch-to-verdict yield: findings-found vs."
            " zero-finding vs. unclassified, joined via each dispatch's subagents/*.meta.json."
        ),
    )
    _add_project_scope_args(p_reviewer_yield)
    p_reviewer_yield.add_argument(
        "--since", metavar="Nd",
        help="Limit to dispatches with timestamp in the last N days (e.g. 35d).",
    )
    p_reviewer_yield.add_argument(
        "--until", metavar="DATE", type=_iso_date,
        help=(
            "Inclusive end date (YYYY-MM-DD). Bounds dispatch detection (table 1) only —"
            " the cited-path edit-overlap table (table 2) is not date-windowed."
        ),
    )
    p_reviewer_yield.add_argument(
        "--redact", action="store_true",
        help=(
            "No-op: reviewer-yield's output is aggregate-only per agent type and"
            " carries no project-label or session-id field to redact. Kept for CLI"
            " parity with cost/audit-routing."
        ),
    )
    p_reviewer_yield.set_defaults(func=cmd_reviewer_yield)

    p_pr = sub.add_parser("pr-link", help="Map branches to GitHub PRs and pull per-PR comment counts. Requires gh.")
    p_pr.add_argument(
        "--repo", metavar="OWNER/REPO",
        help="GitHub repo to query (default: parsed from this checkout's origin remote, host-qualified)",
    )
    p_pr.add_argument("--branches", required=True, metavar="B1,B2,...")
    p_pr.add_argument("--author", metavar="LOGIN", help="Filter comments to this GitHub login")
    _add_project_scope_args(p_pr)
    p_pr.set_defaults(func=cmd_pr_link)

    p_skill_pair = sub.add_parser(
        "skill-pair",
        help=(
            "Pairing rate between two skills, bucketed by ISO week. "
            "Counts sessions where the leader fired and whether the follower also fired (main vs sidechain-only)."
        ),
    )
    p_skill_pair.add_argument("leader", metavar="LEADER", help="Leading skill name (exact match on input.skill)")
    p_skill_pair.add_argument("follower", metavar="FOLLOWER", help="Following skill name (exact match on input.skill)")
    _add_project_scope_args(p_skill_pair)
    p_skill_pair.add_argument(
        "--exclude-projects", default=None, metavar="GLOB",
        help="Skip project dirs whose basename matches this glob.",
    )
    p_skill_pair.add_argument("--branches", metavar="B1,B2,...")
    p_skill_pair.set_defaults(func=cmd_skill_pair)

    p_gate = sub.add_parser(
        "commit-gate",
        help=(
            "Per-commit gate-compliance: did <skill> precede each commit in the same session?"
            " Optionally split by permissionMode."
        ),
    )
    p_gate.add_argument("skill", help="Skill name to check (byte-equal match against Skill tool_use input.skill).")
    p_gate.add_argument("--by-permission-mode", action="store_true", help="Split rows by permissionMode.")
    _add_project_scope_args(p_gate)
    p_gate.add_argument(
        "--exclude-projects", default=None, metavar="GLOB",
        help="Exclude project dirs whose basename matches this glob.",
    )
    p_gate.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_gate.set_defaults(func=cmd_commit_gate)

    p_skill_inv = sub.add_parser(
        "skill-invocation",
        help=(
            "Per-skill invocation-source tally: top-level (description-dependent auto-trigger),"
            " routed (fired while another skill's body was active), and user /slash commands."
            " Identifies name-only and disable-model-invocation candidates for budget relief."
        ),
    )
    p_skill_inv_scope = p_skill_inv.add_mutually_exclusive_group()
    p_skill_inv_scope.add_argument(
        "--projects", default=None, type=_single_level_projects_glob, metavar="GLOB",
        help="Project-dir glob. Default: this repo's own worktrees only (publish-safe). "
             "Passing an explicit glob is an escape hatch — output is then not scoped to this repo.",
    )
    p_skill_inv_scope.add_argument(
        "--this-repo", action="store_true",
        help=(
            "Explicit no-op: skill-invocation already defaults to this repo's own "
            "worktrees. Kept for flag uniformity with every other --projects subcommand."
        ),
    )
    p_skill_inv.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_skill_inv.add_argument(
        "--include-subagents", action="store_true",
        help="Count skill invocations inside spawned subagents too, split by a thread column.",
    )
    p_skill_inv.set_defaults(func=cmd_skill_invocation)

    p_review_trace = sub.add_parser(
        "review-trace",
        help=(
            "Ordered review-event timeline per session: skill invocations, hook denials,"
            " and reviewer-agent spawns."
        ),
    )
    _add_project_scope_args(p_review_trace)
    p_review_trace.add_argument("--branches", metavar="B1,B2,...")
    p_review_trace.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_review_trace.add_argument("--until", metavar="DATE", type=_iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_review_trace.add_argument(
        "--deny-only", action="store_true",
        help="Restrict output to sessions that contain at least one hook denial.",
    )
    p_review_trace.add_argument(
        "--deny-summary", action="store_true",
        help=(
            "Replace the per-session event listing with grouped denial-count"
            " tables — by originating hook/gate, by attempted command shape"
            " (git commit / git checkout / git push / other), and a cross-tab"
            " of the two — plus the corpus date window covered."
        ),
    )
    p_review_trace.add_argument(
        "--skill", metavar="NAME", choices=sorted(REVIEW_TRACE_SKILLS),
        help="Restrict skill-invocation matching to one skill name.",
    )
    p_review_trace.set_defaults(func=cmd_review_trace)

    p_jp = sub.add_parser(
        "judgment-pair",
        help=(
            "Extract (review-skill output, user response) pairs from sessions"
            " where a review skill was invoked."
        ),
    )
    _add_project_scope_args(p_jp)
    p_jp.add_argument("--branches", metavar="B1,B2,...")
    p_jp.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_jp.add_argument("--until", metavar="DATE", type=_iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_jp.add_argument(
        "--skills",
        metavar="SKILL1,SKILL2,...",
        default=",".join(REVIEW_SKILLS),
        help=f"Comma-separated skill names to match (default: {','.join(REVIEW_SKILLS)}).",
    )
    p_jp.add_argument(
        "--truncate-chars",
        type=int,
        default=1000,
        metavar="N",
        help="Maximum characters for the review output block (default: 1000).",
    )
    p_jp.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Write output to this file instead of stdout.",
    )
    p_jp.set_defaults(func=cmd_judgment_pair)

    p_audit = sub.add_parser(
        "audit-routing",
        help=(
            "Per-turn Opus token breakdown by routing class (orchestration, judgment, code-write,"
            " code-read, pure-thinking, other). Aggregates output_tokens and cache_read_input_tokens"
            " per class across all sessions."
        ),
    )
    _add_project_scope_args(p_audit)
    p_audit.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_audit.add_argument(
        "--top", type=int, default=20, metavar="N",
        help="Maximum number of per-session rows to emit (default: 20).",
    )
    p_audit.add_argument(
        "--redact", action="store_true",
        help=(
            "Replace project dir names with anonymized labels (private-project-1, private-project-2, …)"
            " for public reporting. 'claude-config' is preserved as-is."
        ),
    )
    p_audit.set_defaults(func=cmd_audit_routing)

    p_cost = sub.add_parser(
        "cost",
        help=(
            "Price-weighted dollar cost by token class (cache read/write/output/input), model ID,"
            " and context-at-turn bucket, plus top-N sessions by dollars. Redacted by default."
        ),
    )
    _add_project_scope_args(p_cost)
    p_cost.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_cost.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_cost.add_argument(
        "--top", type=int, default=20, metavar="N",
        help="Maximum number of per-session rows in the top-N-by-dollars section (default: 20).",
    )
    p_cost.add_argument(
        "--by-project", action="store_true",
        help=(
            "Add a per-project cost breakdown, keyed on (account root, project family)."
            " Composes with --projects and --this-repo; one repo's own worktrees"
            " collapse into a single row instead of fragmenting per branch."
        ),
    )
    p_cost.add_argument(
        "--no-redact", action="store_true",
        help=(
            "Emit real project names and session IDs instead of anonymized labels."
            " Never publish --no-redact output — see docs/transcript-analysis.md."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_cost.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_cost.add_argument(
        "--summary", action="store_true",
        help=(
            "Compact, aggregate-only block for a PR body: dollars and tokens by token class,"
            " model ID, and thread, plus session and priced-turn counts — no per-session or"
            " per-project row. Requires --this-repo; refuses --projects, --by-project,"
            " --no-redact, and --config-dir."
        ),
    )
    p_cost.add_argument(
        "--share-only", action="store_true",
        help=(
            "Four dimensionless percentage-share tables (class, model, thread, context bucket) —"
            " never a dollar figure, a token count, or a grand total. Refuses --by-project,"
            " --no-redact, --summary, and --top. See docs/transcript-analysis.md."
        ),
    )
    p_cost.set_defaults(func=cmd_cost)

    p_context_dist = sub.add_parser(
        "context-distribution",
        help=(
            "Per-session peak context-at-turn, bucketed both at candidate threshold percentages"
            " (30/40/50/60%%) of the model's context window and at candidate absolute-token"
            " thresholds, with each threshold's session-share and dollar-cost share."
            " Redacted by default."
        ),
    )
    _add_project_scope_args(p_context_dist)
    p_context_dist.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_context_dist.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_context_dist.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_context_dist.set_defaults(func=cmd_context_distribution)

    p_edit_format = sub.add_parser(
        "edit-format",
        help=(
            "Edit/Write/MultiEdit call census: per-tool failure classification, governance-hook"
            " re-bucketing, not_found cause attribution, and old_string/new_string/Write token"
            " overhead. Aggregate-only output; redacted by default."
        ),
    )
    _add_project_scope_args(p_edit_format)
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

    p_read_scope = sub.add_parser(
        "read-scope",
        help=(
            "Read-call scope census: offset/limit/pages classification against the full call"
            " count, result-token distribution by targeted/whole-file cohort and main/subagent"
            " scope, repeat-whole-file-read aggregates, and prompt-token growth."
            " Aggregate-only output; redacted by default."
        ),
    )
    _add_project_scope_args(p_read_scope)
    p_read_scope.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Refused together with --this-repo or --no-redact."
        ),
    )
    p_read_scope.add_argument(
        "--since", metavar="Nd",
        help="Limit the prompt-token growth figure to deltas whose owning turn falls in the last N days (e.g. 35d).",
    )
    p_read_scope.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names, session IDs, or file"
            " paths), so --no-redact has no effect on its content, but it still prints the"
            " DO NOT PUBLISH banner and enforces the same multi-root refusal as cost, for CLI"
            " parity. Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_read_scope.set_defaults(func=cmd_read_scope)

    p_instrument_authoring = sub.add_parser(
        "instrument-authoring",
        help=(
            "Census of inline instrument-authoring: Bash heredoc/inline-program (-c/-e) calls and"
            " Write-to-scratchpad calls, size-bucketed by main-thread/subagent scope and correlated"
            " against each session's own main-thread Agent/Task spawn-dispatch count."
            " Aggregate-only output."
        ),
    )
    _add_project_scope_args(p_instrument_authoring)
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

    p_context_comp = sub.add_parser(
        "context-composition",
        help=(
            "Rate-weighted token-turns by content-item category (user/assistant text, thinking,"
            " tool calls/results, compact summaries), gated by a reconciliation check against"
            " _context_at_turn -- the static-prefix residual refuses to print a ranking above a"
            " named instability threshold. Aggregate-only output; redacted by default."
        ),
    )
    _add_project_scope_args(p_context_comp)
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

    p_cache_efficiency = sub.add_parser(
        "cache-efficiency",
        help=(
            "Per-thread (main/sidechain) cold-cache read-collapse census: assistant turn counts,"
            " cache read/write token totals, and cold-write volume/rate, classified by the"
            " validated read-collapse rule (docs/case-studies/cold-cache-attribution.md)."
            " Aggregate-only output; redacted by default."
        ),
    )
    _add_project_scope_args(p_cache_efficiency)
    p_cache_efficiency.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_cache_efficiency.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_cache_efficiency.set_defaults(func=cmd_cache_efficiency)

    p_cost_trend = sub.add_parser(
        "cost-trend",
        help="Per-ISO-week dollar spend, Opus-family share, and >=200k context-bucket share.",
    )
    _add_project_scope_args(p_cost_trend)
    p_cost_trend.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root."
        ),
    )
    p_cost_trend.set_defaults(func=cmd_cost_trend)

    p_cache_rebuild = sub.add_parser(
        "cache-rebuild",
        help=(
            "Idle-gap prompt-cache TTL-expiry rebuild measurement: per-call write distribution,"
            " cause classification (session start / idle 5m-1h / idle >1h / model switch /"
            " unexplained), concurrency split, and priced excess by account. Redacted by default."
        ),
    )
    _add_project_scope_args(p_cache_rebuild)
    p_cache_rebuild.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_cache_rebuild.add_argument(
        "--since", metavar="Nd", default=_CACHE_REBUILD_DEFAULT_SINCE,
        help=f"Limit to calls with timestamp in the last N days (e.g. 35d). Default: {_CACHE_REBUILD_DEFAULT_SINCE}.",
    )
    p_cache_rebuild.add_argument(
        "--threshold", type=int, default=_CACHE_REBUILD_DEFAULT_THRESHOLD, metavar="TOKENS",
        help=(
            "Minimum cache-write tokens (ephemeral_1h + ephemeral_5m) for a call to count as a"
            f" large rebuild. Default: {_CACHE_REBUILD_DEFAULT_THRESHOLD:,}."
        ),
    )
    p_cache_rebuild.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_cache_rebuild.add_argument(
        "--ttl-verdict", action="store_true",
        help=(
            "Also report a per-root, per-bucket (main / everything-else) adopt/decline verdict on"
            " each bucket's live prompt-cache TTL, covering both the 5m-to-1h and the mirrored,"
            " inferred 1h-to-5m direction. Ships a shared default only when every consistent root"
            " agrees -- see .claude/plans/cache-ttl-tuning-analysis.md's Approach section. Adds"
            " no new output when omitted; every figure without this flag is unchanged."
        ),
    )
    p_cache_rebuild.set_defaults(func=cmd_cache_rebuild)

    p_cost_ledger = sub.add_parser(
        "cost-ledger",
        help=(
            "Read or append the local per-week cost/efficiency ledger (see COST_LEDGER_PATH)."
            " Default: print existing rows plus any live-corpus weeks not yet recorded."
        ),
    )
    _add_project_scope_args(p_cost_ledger)
    p_cost_ledger.add_argument(
        "--record", action="store_true",
        help="Append the current ISO week's row. Requires ~/.claude/.cost-ledger-enabled.",
    )
    p_cost_ledger.add_argument(
        "--force", action="store_true",
        help="With --record, overwrite an existing row for the same (week, machine) instead of refusing.",
    )
    p_cost_ledger.add_argument(
        "--note", metavar="TEXT", default="",
        help="Free-text note for --record's row: what changed in the workflow this week.",
    )
    p_cost_ledger.set_defaults(func=cmd_cost_ledger)

    p_pr_cost = sub.add_parser(
        "pr-cost",
        help=(
            "Per-PR AI-tooling dollar cost, joined against PR size/rework/review-surface via"
            " gh. Default: list uncaptured merged PRs still in the local transcript window."
            " --record durably appends one row per captured PR to the pr-cost ledger (see"
            " PR_COST_LEDGER_PATH). Always redacted -- no --no-redact escape hatch. Requires gh."
        ),
    )
    _add_project_scope_args(p_pr_cost)
    p_pr_cost.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). pr-cost refuses"
            " (exit 2) whenever more than one root resolves, unless --all-accounts is given --"
            " see docs/pr-cost.md."
        ),
    )
    p_pr_cost.add_argument(
        "--all-accounts", action="store_true",
        help=(
            "Scan every declared account in one run instead of refusing when more than one root"
            " resolves. Each account's own ~/.claude/.pr-cost-enabled sentinel still individually"
            " gates whether that account's row is recorded -- see docs/pr-cost.md."
        ),
    )
    p_pr_cost.add_argument(
        "--record", action="store_true",
        help="Capture ledger rows for eligible merged PRs. Requires ~/.claude/.pr-cost-enabled.",
    )
    p_pr_cost.add_argument(
        "--pr", type=int, metavar="N",
        help="Target exactly one PR number instead of every branch with local corpus activity.",
    )
    p_pr_cost.add_argument(
        "--machine-label", metavar="LABEL",
        help=(
            "Narrow read mode's uncaptured-PR listing to one machine: ^[a-z0-9]{1,8}$. Refused"
            " (exit 1) together with --record -- machine identity is generated and persisted"
            " automatically there; see docs/pr-cost.md."
        ),
    )
    p_pr_cost.add_argument(
        "--force", action="store_true",
        help="With --record and --pr, append a correcting row for an already-captured PR instead of refusing.",
    )
    p_pr_cost.add_argument(
        "--asof-window-days", type=float, metavar="DAYS",
        help=(
            "Close-out window before a merged PR is eligible for capture (default:"
            f" {_PR_COST_ASOF_WINDOW_DAYS_DEFAULT:g}, a provisional placeholder -- see docs/pr-cost.md)."
        ),
    )
    p_pr_cost.add_argument(
        "--plan-file-glob", metavar="GLOB",
        help=(
            "Glob checked against a PR's added files for the plan-slug join cross-check"
            f" (default: {_DEFAULT_PR_COST_PLAN_FILE_GLOB!r})."
        ),
    )
    p_pr_cost.add_argument(
        "--risk-surface-glob", action="append", dest="risk_surface_globs", metavar="GLOB",
        help=(
            "Glob pattern considered risk surface for the risk_surface_flag proxy (repeatable;"
            " replaces the claude-config defaults entirely when given)."
        ),
    )
    p_pr_cost.set_defaults(func=cmd_pr_cost)

    p_pr_cost_export = sub.add_parser(
        "pr-cost-export",
        help=(
            "Export every declared account's current pr-cost ledger rows -- redacted,"
            " collapsed to one row per PR -- to a single operator-named TSV. Never writes to"
            " stdout. Makes no gh call and scans no transcript corpus. See docs/pr-cost.md."
        ),
    )
    p_pr_cost_export.add_argument(
        "--out", metavar="PATH",
        help=(
            "Required: destination TSV path, refused if it already exists (never"
            " overwritten). No stdout fallback -- stdout inside a Claude Code session is"
            " captured into that session's own transcript."
        ),
    )
    p_pr_cost_export.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help="Additional Claude Code config directory to scan (repeatable).",
    )
    p_pr_cost_export.set_defaults(func=cmd_pr_cost_export)

    p_spend_over_threshold = sub.add_parser(
        "spend-over-threshold",
        help=(
            "Per-week share of session dollar spend earned at or above the handoff nudge's"
            " own fire threshold."
        ),
    )
    _add_project_scope_args(p_spend_over_threshold)
    p_spend_over_threshold.add_argument(
        "--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)"
    )
    p_spend_over_threshold.set_defaults(func=cmd_spend_over_threshold)

    p_workstream_cost = sub.add_parser(
        "workstream-cost",
        help=(
            "Per-branch session count and continuation startup-burn dollars -- a handoff-overhead"
            " approximation from session/branch shape alone, no gh calls by default. --check-pr-status"
            " additionally lists every zero-PR-match branch's last-activity age. Corpus-wide."
        ),
    )
    _add_project_scope_args(p_workstream_cost)
    p_workstream_cost.add_argument(
        "--check-pr-status", action="store_true",
        help=(
            "Also classify every branch by merged/closed-unmerged/no-PR-match via gh, pinned to this"
            " invocation's own repo identity like pr-cost. Requires gh."
        ),
    )
    p_workstream_cost.set_defaults(func=cmd_workstream_cost)

    p_review_round_cost = sub.add_parser(
        "review-round-cost",
        help=(
            "Per-branch review-round dollar cost: prices every code-review/plan-review/"
            "ready-for-review invocation's own window (main-thread turns plus every subagent"
            " dispatched inside it), with a round-vs-non-round reconciliation line. Corpus-wide,"
            " no gh calls."
        ),
    )
    _add_project_scope_args(p_review_round_cost)
    p_review_round_cost.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_review_round_cost.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_review_round_cost.add_argument("--until", metavar="DATE", type=_iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_review_round_cost.add_argument(
        "--skill", metavar="NAME", choices=sorted(REVIEW_SKILLS),
        help="Print only rounds for one skill name (default: all three); never narrows detection.",
    )
    p_review_round_cost.add_argument(
        "--pooled", action="store_true",
        help=(
            "Print only a cross-account pooled block of shares (no dollar amounts, no"
            " raw counts, no per-branch rows). Refuses every scope-narrowing flag; see"
            " docs/private-project-redaction.md."
        ),
    )
    p_review_round_cost.set_defaults(func=cmd_review_round_cost)

    p_author_outcome = sub.add_parser(
        "author-outcome",
        help=(
            "For each --agent-typed dispatch (default code-writer), what share of its own diffs"
            " drew a must-fix (ADDRESS) finding on downstream code-review. Reads the transcript for"
            " round/dispatch structure and each session's own review-narrative-ledger file for"
            " disposition. Corpus-wide, no gh calls."
        ),
    )
    _add_project_scope_args(p_author_outcome)
    p_author_outcome.add_argument(
        "--agent", metavar="NAME", default=_AUTHORING_AGENT_CODE_WRITER,
        help="subagent_type to join dispatches against (default: code-writer).",
    )
    p_author_outcome.add_argument(
        "--since", metavar="Nd",
        help="Limit to dispatches with a timestamp in the last N days (e.g. 30d); default: all time.",
    )
    p_author_outcome.set_defaults(func=cmd_author_outcome)

    p_cost_counts = sub.add_parser(
        "cost-counts",
        help=(
            "Per-branch review-round and subagent-spawn counts, as two GFM subsections for a"
            " public PR body -- counts only, no dollar attribution. Requires --this-repo and"
            " --branches; always scoped to the active account alone."
        ),
    )
    _add_project_scope_args(p_cost_counts)
    p_cost_counts.add_argument(
        "--branches", metavar="B1,B2,...",
        help="Branch name filter. Required at runtime (see cmd_cost_counts's own docstring).",
    )
    p_cost_counts.set_defaults(func=cmd_cost_counts)

    p_rearm_backtest = sub.add_parser(
        "rearm-backtest",
        help=(
            "Backtest candidate re-arm band spacings for the handoff nudge's one-shot fire"
            " against the recorded corpus: predicted total $ and C_bar per spacing, under both"
            " perfect-compliance and compliance-realistic operator-response models."
            " Redacted by default."
        ),
    )
    _add_project_scope_args(p_rearm_backtest)
    p_rearm_backtest.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_rearm_backtest.add_argument(
        "--since", metavar="Nd",
        help="Limit to sessions with a first timestamp in the last N days (e.g. 35d); whole-session scope.",
    )
    p_rearm_backtest.add_argument(
        "--branches", metavar="B1,B2,...",
        help="Whole-session branch filter: a session with no matching main-thread turn is excluded (default: all).",
    )
    p_rearm_backtest.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on most of its content, but at single-root scope it"
            " prints the literal .handoff-nudge.log path (instead of an account-N label) in the"
            " per-root log-size line. It still prints the DO NOT PUBLISH banner and enforces the"
            " same multi-root refusal as cost, for CLI parity. Refused when --config-dir puts"
            " more than one root in scope."
        ),
    )
    p_rearm_backtest.add_argument(
        "--spacings", metavar="N1,N2,...", default="40000,80000,120000",
        help="Comma-separated candidate re-arm spacings in tokens past the first fire (default: 40000,80000,120000).",
    )
    p_rearm_backtest.set_defaults(func=cmd_rearm_backtest)

    p_handoff_signal_response = sub.add_parser(
        "handoff-signal-response",
        help=(
            "Per-session observed context-budget signals (--check over_threshold/already_fired,"
            " the advisory nudge injection, the hard-block stderr) and whether a same-session"
            " /handoff followed each one, split by marker context. Redacted by default."
        ),
    )
    _add_project_scope_args(p_handoff_signal_response)
    p_handoff_signal_response.add_argument(
        "--no-redact", action="store_true",
        help=(
            "Emit raw session IDs in --sample curation cards instead of a run-scoped opaque"
            " label, and print the DO NOT PUBLISH banner. Refused when scope resolves to more"
            " than one root -- narrow to a single root first, e.g. with --this-repo."
        ),
    )
    p_handoff_signal_response.add_argument(
        "--sample", type=int, default=0, metavar="N",
        help=(
            "Emit the top N signal rows by post-signal spend as curation cards instead of the"
            " aggregate report."
        ),
    )
    p_handoff_signal_response.add_argument(
        "--seed", type=int, default=None, metavar="N",
        help=(
            "Seed for reproducible tie-breaking among equal-spend rows in --sample (default:"
            " unseeded -- ties keep scan order)."
        ),
    )
    p_handoff_signal_response.add_argument(
        "--format", dest="output_format", choices=("json", "md"), default="json",
        help="--sample output format: json (default) or md (a human curation document).",
    )
    p_handoff_signal_response.add_argument(
        "--context-turns", type=int, default=0, metavar="N",
        help=(
            "With --sample, also attach the next N main-thread turns of text AND thinking-block"
            " content after each signal (forward_context) -- unlike the single-turn excerpt"
            " (text blocks only), this surfaces reasoning an agent confined to an"
            " extended-thinking block. Requires --sample."
        ),
    )
    p_handoff_signal_response.set_defaults(func=cmd_handoff_signal_response)

    p_plan_boundary = sub.add_parser(
        "plan-boundary",
        help=(
            "Re-price each Opus-anchored session's own post-plan-boundary main-thread turns under"
            " three arms -- continue on Opus, switch to Sonnet in place, fresh Sonnet handoff --"
            " plus the work-inflation breakeven for each arm pair. Aggregate-only, redacted by"
            " default."
        ),
    )
    _add_project_scope_args(p_plan_boundary)
    p_plan_boundary.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_plan_boundary.add_argument(
        "--since", metavar="Nd",
        help="Limit to sessions with a first timestamp in the last N days (e.g. 35d); whole-session scope.",
    )
    p_plan_boundary.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs, no plan"
            " text), so --no-redact has no effect on its content, but it still prints the DO NOT"
            " PUBLISH banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_plan_boundary.set_defaults(func=cmd_plan_boundary)

    p_audit_shape = sub.add_parser(
        "audit-routing-shape",
        help=(
            "Turn-shape distributions for Opus code-read turns: files-Read per turn (D1),"
            " code-read streak lengths (D2), and read-then-edit ratio (D3)."
        ),
    )
    _add_project_scope_args(p_audit_shape)
    p_audit_shape.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_audit_shape.set_defaults(func=cmd_audit_routing_shape)

    p_audit_samples = sub.add_parser(
        "audit-routing-samples",
        help=(
            "Emit a random sample of Opus code-read turns with prior-user context and"
            " next-turn lookahead classification. JSON array output for manual curation."
        ),
    )
    _add_project_scope_args(p_audit_samples)
    p_audit_samples.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_audit_samples.add_argument(
        "--sample", type=int, default=30, metavar="N",
        help="Maximum number of sample turns to emit (default: 30).",
    )
    p_audit_samples.add_argument(
        "--seed", type=int, default=None, metavar="N",
        help="Random seed for reproducible sampling.",
    )
    p_audit_samples.add_argument(
        "--format", choices=["json", "md"], default="json", dest="output_format",
        help="Output format: json (default) or md (human-readable markdown for curation).",
    )
    p_audit_samples.set_defaults(func=cmd_audit_routing_samples)

    p_turn_shape = sub.add_parser(
        "turn-shape",
        help=(
            "Per-turn tool-call-count distribution, plus streak-length distributions for"
            " consecutive single-call turns (batching rule) and consecutive Bash-only"
            " single-call turns excluding mutating git (delegation rule), dollar-weighted."
        ),
    )
    _add_project_scope_args(p_turn_shape)
    p_turn_shape.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_turn_shape.set_defaults(func=cmd_turn_shape)

    p_turn_shape_samples = sub.add_parser(
        "turn-shape-samples",
        help=(
            "Emit a random sample of flagged turn-shape streaks (length >= 2) as plain text,"
            " for manual calibration of the batching and delegation rules."
        ),
    )
    _add_project_scope_args(p_turn_shape_samples)
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

    p_turn_shape_holdout_samples = sub.add_parser(
        "turn-shape-holdout-samples",
        help=(
            "Emit a random sample of unflagged turn-shape streaks (length == 1) as plain"
            " text, for manual recall calibration of the batching and delegation rules."
        ),
    )
    _add_project_scope_args(p_turn_shape_holdout_samples)
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

    p_sessions = sub.add_parser(
        "sessions",
        help="Emit transcript file paths for the resolved scope, one absolute path per line.",
    )
    _add_project_scope_args(p_sessions)
    p_sessions.add_argument(
        "--paths", action="store_true",
        help="Print one absolute transcript path per line (the only sessions action today; required).",
    )
    p_sessions.add_argument(
        "--include-subagents", action="store_true",
        help="Also emit split subagent transcript paths under <session>/subagents/*.jsonl.",
    )
    p_sessions.set_defaults(func=cmd_sessions)

    p_friction = sub.add_parser(
        "friction-count",
        help=(
            "Composite friction-signal count (hook denials + failed test runs +"
            " user-correction phrases) for a single transcript file."
        ),
    )
    p_friction.add_argument(
        "--transcript", required=True, metavar="PATH",
        help="Path to one transcript .jsonl file.",
    )
    p_friction.add_argument(
        "--checkpoint", metavar="PATH", default=None,
        help=(
            "Path to a checkpoint file for incremental scanning: only bytes"
            " appended to the transcript since the checkpoint's stored offset"
            " are parsed, and the cumulative composite (not just this call's"
            " delta) is printed. An absent or malformed checkpoint fails open"
            " to a full scan from offset 0. Without this flag, every call"
            " does a full scan with no state read or written."
        ),
    )
    p_friction.add_argument(
        "--json", action="store_true",
        help="Emit the per-signal breakdown as JSON instead of the composite integer.",
    )
    p_friction.set_defaults(func=cmd_friction_count)

    return parser


def main() -> None:
    parser = build_parser()
    parsed = parser.parse_args()
    if parsed.config_dir:
        # These subcommands each refuse the top-level --config-dir outright
        # rather than let it silently diverge from whatever scan roots the
        # subcommand actually resolves (this top-level one would validate
        # one account while the subcommand scans another). Most resolve
        # their own scan roots via their own --config-dir (_resolve_cost_roots);
        # cost-counts is the one exception, registering no --config-dir flag
        # of its own at all (see scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR).
        if parsed.subcommand in _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR:
            # hasattr, not a hardcoded subcommand list: True only when the
            # invoked subparser itself registered --config-dir (dest
            # extra_config_dirs), so the hint never recommends a flag that
            # doesn't exist on cost-counts's own parser.
            if hasattr(parsed, "extra_config_dirs"):
                own_scope_clause = (
                    "this subcommand resolves its own scan roots via its own --config-dir "
                    "(repeatable, additive) -- use that instead: "
                    f"transcript-analysis.py {parsed.subcommand} --config-dir PATH"
                )
            else:
                own_scope_clause = (
                    "this subcommand is scoped to the active account's config dir with no override"
                )
            print(
                f"{parsed.subcommand}: the top-level --config-dir has no effect here, since "
                f"{own_scope_clause}",
                file=sys.stderr,
            )
            sys.exit(2)
        # Every other subcommand's scan roots funnel through _resolve_scan_roots,
        # which reads parsed.config_dir directly for its override branch rather
        # than through this reassignment -- so this can no longer diverge from
        # a subcommand's actual scan roots the way it could before.
        scope.PROJECTS_DIR = Path(parsed.config_dir) / "projects"
    parsed.func(parsed)


if __name__ == "__main__":
    main()
