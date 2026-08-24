#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
pr-link is the only subcommand that touches the network (via gh).
judgment-pair --out writes a file; all other subcommands are read-only.
"""

import argparse
import bisect
import contextlib
import errno
import fcntl
import fnmatch
import hashlib  # noqa: F401 -- read only via _mod.hashlib from test files
import json
import math
import os
import random
import re
import shlex
import socket
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

from _config_dir import config_dir

# corpus/cost/pricing/redaction/render/reviewer_yield are read only via _mod.<module> from
# test files (unit-testing a private helper, or patching module-owned state like
# scope.PROJECTS_DIR below) -- scope is the only one this file's own code reads bare, as
# scope.PROJECTS_DIR.
from transcript_analysis import corpus, cost, pricing, redaction, render, reviewer_yield, scope  # noqa: F401
from transcript_analysis.corpus import SUBAGENT_SUBDIR, _parse_ts, _read_session_file_partitioned, iter_sessions
from transcript_analysis.cost import (
    # The nine names below are read only via _mod.<name> from test files (unit-testing a
    # private helper directly, or a monkeypatch retarget) -- cmd_cost/cmd_cost_trend are the
    # only two this file's own code calls bare, via p_cost/p_cost_trend.set_defaults below.
    _accumulate_per_account_turn,  # noqa: F401
    _attributed_branch,  # noqa: F401
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
from transcript_analysis.cost import compute_cost_trend_data as _compute_cost_trend_data
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
    _PRICING_FETCH_DATE,
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
    _token_counts,
    _warn_if_subagent_format_drift,
)
from transcript_analysis.pricing import dedup_turns_by_request_id as _dedup_turns_by_request_id
from transcript_analysis.redaction import (
    _REDACT_MAP_MISS_TOKEN,  # noqa: F401 -- read only via _mod._REDACT_MAP_MISS_TOKEN from test files
    _assign_root_scoped_redact_label,
    _assign_session_redact_label,
    _build_redact_map,
    _corpus_fingerprint,
    _derive_proj_label,
    _redact_proj_label,
    _redact_session_id,
    _RedactMapKey,
)
from transcript_analysis.render import (
    _RECENT_LOOKBACK_N,
    _content_text,
    _context_distribution_rows,
    _fam,
    _fmt_date,
    _fmt_usd,
    _format_samples_as_markdown,
    _pct_of,
    _pct_value,
    _recent_assistant_text,
    _recent_tool_trail,
    _sanitize_table_cell,
)
from transcript_analysis.reviewer_yield import (
    # The seven names below are read only via _mod.<name> from test files (unit-testing a
    # private helper directly) -- cmd_reviewer_yield, _is_reviewer_subagent_type,
    # _index_subagent_dispatches, and the two _REVIEWER_VERDICT_* names below are also read
    # bare by this file's own still-monolithic code (p_reviewer_yield.set_defaults,
    # _review_trace_session_events, cmd_subagent_mix, and _reviewer_gap_pp respectively).
    _CITED_PATH_CANDIDATE_MAX_CHARS,  # noqa: F401
    _REVIEWER_VERDICT_FINDINGS_FOUND,
    _REVIEWER_VERDICT_ZERO_FINDING,
    _build_tool_result_ts_map,  # noqa: F401
    _dispatch_self_reference_keys,  # noqa: F401
    _extract_cited_paths,  # noqa: F401
    _index_parent_edits,  # noqa: F401
    _index_subagent_dispatches,
    _is_reviewer_subagent_type,
    _normalize_cited_path,  # noqa: F401
    _reviewer_yield_cited_keys,  # noqa: F401
    cmd_reviewer_yield,
)
from transcript_analysis.reviewer_yield import compute_reviewer_yield_data as _compute_reviewer_yield_data
from transcript_analysis.scope import (
    _DO_NOT_PUBLISH_BANNER,
    _SUBCOMMANDS_WITH_OWN_CONFIG_DIR,
    _branch_filter,
    _iter_glob_scoped_sessions,
    _iter_scoped_sessions,
    _parse_since_nd_arg,
    _projects_glob,
    _redaction_ordinals,
    _repo_scoped_project_slugs,
    _resolve_cost_roots,
    _resolve_project_scope,
    _resolved_scope_header,
    _root_index_for_path,
    _scan_root_transcripts,
)
from transcript_analysis.scope import print_resolved_scope as _print_resolved_scope
from transcript_analysis.scope import resolve_scan_roots as _resolve_scan_roots

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
            d["projects"].add(jsonl.parent.name)
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
                text = _content_text(msg.get("content", "")).lower()
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
    for EXPLICIT_CORRECTION.
    """
    if is_initial:
        return "INITIAL", ""
    lowered = text.lower()
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

    since_str: str | None = getattr(args, "since", None) or None
    until_str: str | None = getattr(args, "until", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    until_epoch: float | None = None
    if until_str:
        day_start = _parse_ts(f"{until_str}T00:00:00Z")
        if day_start is not None:
            until_epoch = day_start + 86400

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
        total_projects_seen.add(proj_label)

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
    window start; the corpus-wide spawn and sidechain-turn counters feeding
    _warn_if_subagent_format_drift are read before this filter and are never
    narrowed by it, so a narrow --since window cannot manufacture a false
    format-drift warning. --config-dir (repeatable) scans additional Claude
    Code config directories the same way cost does; under more than one root,
    branch names are redacted (via _assign_root_scoped_redact_label, account-<K>/
    branch-<N>) since a raw branch slug from a foreign account would
    otherwise be printed, and _DO_NOT_PUBLISH_BANNER is stamped on stdout
    and stderr.
    """
    roots = _resolve_cost_roots(args, "subagents")
    multi_root = len(roots) > 1
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
            _assign_root_scoped_redact_label(
                "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map
            )
            if root_idx is not None
            else branch
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
                    print(f"{row_label:<40} {thread:<10} {tool_name:<20} {nbytes:>18,}")


REVIEW_SKILLS: tuple[str, ...] = ("code-review", "plan-review", "ready-for-review")

# subagents' tool-result byte grouping bucket for every mcp__<server>__<tool>
# tool name — an MCP server name is a per-account integration identifier, so
# every MCP tool call collapses into this one row instead of one row per server.
_MCP_TOOL_BUCKET_LABEL = "mcp__*"

# subagent-mix's model-mix table bucket for a dispatch whose meta.json carries
# no "model" key at all (no explicit model was requested).
_UNREQUESTED_MODEL_LABEL = "(none)"

# Skills counted as review invocations in review-trace.
REVIEW_TRACE_SKILLS: frozenset[str] = frozenset(
    {"code-review", "plan-review", "ready-for-review", "skill-review", "agent-review", "plan-it"}
)

# Shared by review-trace's two zero-match termini (default timeline and
# --deny-summary) so both read identically under the scope header.
_REVIEW_TRACE_NO_SESSIONS_MSG = "No sessions matched in scope."

# Skills that open a judgment span in audit-routing: any turn within an active span
# (from skill invocation until the next user turn) is classified as `judgment`, not
# by its tool-use contents. Extends REVIEW_TRACE_SKILLS with security-review,
# respond-pr, and ultrareview.
AUDIT_JUDGMENT_SKILLS: frozenset[str] = frozenset({
    "code-review", "plan-review", "ready-for-review", "skill-review",
    "agent-review", "security-review", "respond-pr", "ultrareview", "plan-it",
})

# Shared bound for every hook-name/label capture below (detection and
# extraction alike): a name-shaped character class (word chars, spaces, '.',
# '-') capped at this many characters, matching every hook's own static
# "<name> hook/gate" wording — never an unbounded `.+?`, which would echo
# arbitrary denial-message text (a dynamic file path, say) into
# --deny-summary's output if a future hook ever interpolated one into this
# span.
_DENIAL_HOOK_NAME_MAX_CHARS = 40

# Current-format transcripts record a hook denial as an is_error tool_result,
# distinguishable from an ordinary tool error only by the deny message text —
# hook_denial_key deliberately does not read the parent user record's
# toolDenialKind field, a separate friction-class axis classified by
# _is_nongate_friction_kind below. These patterns match the Claude Code
# hook-denial idiom ("Blocked by <hook>", "blocked by <X> gate", "… invocation
# denied", "<name> gate: …" / "<name> hook: …" — a hook stating its own label
# directly, e.g. "Skill length gate: ..."). Detection is therefore best-effort
# in both directions: an atypically worded hook denial is missed, and an
# ordinary tool error whose text happens to contain the idiom is a false
# positive. review-trace is a candidate locator, not an exact counter —
# callers treat denial counts as approximate. Legacy transcripts additionally
# carry an explicit
# hook_blocking_error attachment record, matched separately and exactly.
_HOOK_DENIAL_SIGNATURE = re.compile(
    r"blocked by .{0,80}?\b(?:hook|gate)\b"
    r"|invocation denied\b"
    rf"|[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}\s+(?:hook|gate):",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_blocking_error(raw) -> dict | str:
    """Normalize blockingError — may arrive as a dict or a JSON-stringified dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
        if isinstance(parsed, dict):
            return parsed
        return raw
    return raw if raw is not None else {}


def hook_denial_key(item: dict) -> tuple[str, dict | str] | None:
    """Return (tool_use_id, extra) if `item` is a hook denial, else None.

    Detects both denial shapes: a legacy `attachment` record (`item["type"] ==
    "attachment"` with a nested `hook_blocking_error`), or a current-format
    `tool_result` block (`item["type"] == "tool_result"`) carrying `is_error`
    and text matching _HOOK_DENIAL_SIGNATURE. An empty string is a valid
    tool_use_id — a denial whose transcript recorded no tool_use_id; only
    None means "not a denial". `extra` is the already-fetched data this
    predicate needed internally to classify the item — the `attachment`
    dict for the legacy shape, the decoded content text for the tool_result
    shape — returned so callers building an event/message don't re-fetch or
    re-decode it. Callers own the seen-id dedup set and the event/message
    construction; this predicate only classifies.
    """
    item_type = item.get("type")
    if item_type == "attachment":
        att = item.get("attachment") or {}
        if att.get("type") != "hook_blocking_error":
            return None
        return att.get("toolUseID") or "", att
    if item_type == "tool_result":
        if not item.get("is_error"):
            return None
        message = _content_text(item.get("content"))
        if not _HOOK_DENIAL_SIGNATURE.search(message):
            return None
        return item.get("tool_use_id") or "", message
    return None


# Extracts the hook/gate name from a denial message's own "blocked by <name>
# hook/gate" wording — every hook's emit_deny call already writes this shape
# (e.g. "Blocked by code-review gate: ..."), so the name is read off the
# denial text itself rather than an invented category label.
_DENIAL_HOOK_NAME_RE = re.compile(
    rf"blocked by (?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+(?:hook|gate)\b", re.IGNORECASE
)

# Two wordings hooks emit that the "blocked by <name> hook/gate" idiom above
# doesn't cover: enforce-marker-script-shape.sh's path-traversal and
# shape-mismatch denials name their own script directly ("marker.sh
# invocation denied ..."), and several hooks state their own label as the
# message's own prefix rather than via "blocked by" (e.g. check-skill-length.sh's
# "Skill length gate: ..."). Both inherit the same bounded character class and
# _DENIAL_HOOK_NAME_MAX_CHARS cap as the pattern above.
_DENIAL_HOOK_NAME_INVOCATION_DENIED_RE = re.compile(
    rf"(?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+invocation denied\b", re.IGNORECASE
)
_DENIAL_HOOK_NAME_COLON_RE = re.compile(
    rf"(?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+(?:hook|gate):", re.IGNORECASE
)

# The hand-maintained set of prose labels hooks/*.sh actually emits — sourced
# by grepping every hook's emit_deny call, not from hooks/*.sh basenames
# (which match none of these; see the module-level docstring for why). A
# captured name is trusted only if it's a member of this set; anything else
# (a coincidental match, an unanticipated wording, an unbounded interpolated
# value that happened to survive the character-class bound) falls to
# _DENY_SUMMARY_UNMATCHED_HOOK rather than being echoed verbatim. Regression
# coverage: TestDenialHookLabelEnumeration in test_transcript_analysis.py
# drives each hook's real deny-path wording and asserts the label it
# produces is a member here, so a hook's wording change or a new hook shows
# up as a test failure rather than a silently stale set.
_DENIAL_HOOK_LABELS: frozenset[str] = frozenset({
    # "blocked by <name> hook/gate" — one entry per hooks/*.sh label.
    "gh-pr-merge",  # block-gh-pr-merge.sh:49
    "CLAUDE.md length",  # check-claude-md-length.sh:42
    "skill length",  # check-skill-length.sh:41
    "credential-path Bash",  # deny-credential-bash-reads.sh:27
    "credential-file read",  # deny-credential-file-reads.sh:27
    "data-file read",  # deny-data-file-reads.sh:65
    "env-read",  # deny-env-reads.sh:47
    "backtick-escape",  # deny-escaped-backticks-in-pr-body.sh:46
    "network-install",  # deny-network-installs.sh:40
    "PII commit",  # deny-pii-in-commits.sh:127
    "redaction",  # deny-private-project-refs.sh:180
    "repo-relocation",  # deny-repo-relocation.sh:63
    "reviewer-tree-mutation",  # deny-reviewer-tree-mutation.sh:146
    "marker-script-shape",  # enforce-marker-script-shape.sh:68
    "settings session-keys",  # guard-settings-session-keys.sh:49
    "code-review",  # require-code-review.sh:48
    "memory-skill",  # require-memory-skill.sh:59
    "ai-instruction-and-memory-files",  # require-memory-skill.sh:125
    "plan-review",  # require-plan-review.sh:66
    "plan-review routing",  # require-routing-read.sh:68
    "ready-for-review",  # require-ready-for-review.sh:80
    "respond-pr",  # require-respond-pr.sh:69
    "routing-read",  # require-routing-read.sh:27
    "stow-reminder",  # require-stow-reminder.sh:71
    "worktree-enforcement",  # require-worktree-for-file-writes.sh:50, require-worktree-for-git-writes.sh:91
    # "<name> invocation denied" (_DENIAL_HOOK_NAME_INVOCATION_DENIED_RE).
    "marker.sh",  # enforce-marker-script-shape.sh:277,353
    # "<name> gate:"/"<name> hook:" (_DENIAL_HOOK_NAME_COLON_RE) — a hook
    # stating its own label as the message's own prefix. check-claude-md-length.sh:85's
    # message reads "CLAUDE.md/AGENTS.md length gate: ..."; '/' isn't in the
    # name-shaped class, so only the AGENTS.md half of the label survives.
    "AGENTS.md length",  # check-claude-md-length.sh:85
    "Skill length",  # check-skill-length.sh:87
})

# --deny-summary's unmatched-hook-name bucket: a denial matched by
# _HOOK_DENIAL_SIGNATURE (e.g. via the "invocation denied" alternative, which
# names no hook) but from which no enumerated hook/gate name can be extracted.
_DENY_SUMMARY_UNMATCHED_HOOK = "unmatched"

# --deny-summary's attempted-command-shape classifier: an allowlist, not a
# free-text sanitizer. A command failing to normalize into one of the
# multiplexer shapes below falls into "other".
_DENY_SUMMARY_OTHER_COMMAND_SHAPE = "other"

# Strips a leading NAME=VALUE environment-assignment prefix (one such prefix
# is observed in the corpus, wrapping a marker.sh invocation with a live
# per-machine token) before any other normalization runs, so that token never
# reaches printed output.
_DENIAL_COMMAND_ENV_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)+")

# The multiplexer commands the corpus is dominated by — only these get a
# command+subcommand shape; every other command shape, including an empty
# command, falls to "other". "marker.sh" is matched post-basename, since the
# real invocation is always a tilde or absolute script path.
_DENIAL_COMMAND_MULTIPLEXERS: frozenset[str] = frozenset({"git", "gh", "marker.sh"})

# Per-multiplexer closed allowlist of real subcommands --deny-summary trusts
# in the printed "<multiplexer> <subcommand>" shape — same discipline as
# _DENIAL_HOOK_LABELS: a candidate subcommand token that isn't a member (a
# credential-shaped string, a path, an unenumerated wording) falls to "other"
# rather than being echoed verbatim. Sourced from the corpus's observed
# denied invocations plus _LIB_READONLY_GIT_SUBCMDS in hooks/_lib.sh for the
# git read-only entries; new entries are added deliberately, not accreted.
_DENIAL_COMMAND_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({
        "add", "checkout", "commit", "config", "diff", "fetch", "init", "log",
        "merge", "pull", "push", "restore", "rev-parse", "show", "status",
        "symbolic-ref",
    }),
    "gh": frozenset({"api", "auth", "issue", "pr"}),
    "marker.sh": frozenset({"activate", "clear-stale", "deactivate", "status", "write"}),
}

# Second layer beyond the allowlist above, matching _DENIAL_HOOK_NAME_MAX_CHARS's
# defense-in-depth pattern: bounds a future malformed allowlist entry rather
# than serving as the primary defense, which is allowlist membership itself.
_DENIAL_COMMAND_SUBCOMMAND_MAX_CHARS = 20
_DENIAL_COMMAND_SUBCOMMAND_RE = re.compile(rf"^[\w-]{{1,{_DENIAL_COMMAND_SUBCOMMAND_MAX_CHARS}}}$")

# git flags that take their value as the following token — dropping (not
# skipping) both the flag and its value keeps the value from being misread
# as the subcommand, e.g. "git -C <path> commit" would otherwise leave
# <path> at index 1 once "-C" alone is skipped, so a naive scan reads <path>
# as the subcommand instead of "commit" at index 2. -C is
# require-worktree-for-git-writes.sh's own resolution mechanism for a
# compliant worktree write, so it's the dominant separate-token form in the
# worktree-enforcement denial category.
_DENIAL_COMMAND_FLAGS_WITH_SEPARATE_VALUE: frozenset[str] = frozenset({"-C", "-c", "--git-dir", "--work-tree"})

# git flags whose value is glued to the flag by "=" — the value already
# lives inside this one token, so nothing further needs dropping.
_DENIAL_COMMAND_FLAG_VALUE_ATTACHED_PREFIXES: tuple[str, ...] = ("--git-dir=", "--work-tree=")


def _drop_denial_command_flag_values(tokens: list[str]) -> list[str]:
    """Drop (not skip) the values of git's value-taking repo-selection flags.

    A separate-token flag (-C, -c, --git-dir, --work-tree) consumes itself
    and the token after it; an =-attached flag (--git-dir=<path>,
    --work-tree=<path>) consumes only itself, since its value is already
    inside that token. Skipping a flag without dropping its value would
    leave the value in place to be misread as the subcommand.
    """
    kept: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _DENIAL_COMMAND_FLAGS_WITH_SEPARATE_VALUE:
            i += 2  # drop the flag token and its value token
            continue
        if token.startswith(_DENIAL_COMMAND_FLAG_VALUE_ATTACHED_PREFIXES):
            i += 1  # value is glued to this token; nothing further to drop
            continue
        kept.append(token)
        i += 1
    return kept

# Extraction patterns tried in order against a current-shape denial message;
# the first to yield a name in _DENIAL_HOOK_LABELS wins.
_DENIAL_HOOK_NAME_PATTERNS: tuple[re.Pattern, ...] = (
    _DENIAL_HOOK_NAME_RE,
    _DENIAL_HOOK_NAME_INVOCATION_DENIED_RE,
    _DENIAL_HOOK_NAME_COLON_RE,
)


def _denial_hook_label(hook_name: str, message: str) -> str:
    """Return the originating hook/gate name for one denial event.

    Legacy-shape denials carry the name directly (hook_name, from the
    attachment record's hookName field); current-shape denials carry no
    structured hook identity, so the name is extracted from the denial
    message text via each pattern in _DENIAL_HOOK_NAME_PATTERNS in turn.
    Either source is trusted only if the candidate is a member of
    _DENIAL_HOOK_LABELS — an unenumerated hookName (legacy transcripts predate
    this bound entirely) or an unenumerated extracted candidate both fall to
    _DENY_SUMMARY_UNMATCHED_HOOK rather than being echoed verbatim.
    """
    candidate = (hook_name or "").strip()
    if candidate:
        return candidate if candidate in _DENIAL_HOOK_LABELS else _DENY_SUMMARY_UNMATCHED_HOOK
    for pattern in _DENIAL_HOOK_NAME_PATTERNS:
        m = pattern.search(message)
        if m is None:
            continue
        name = m.group("name").strip().removeprefix("the ")
        if name in _DENIAL_HOOK_LABELS:
            return name
    return _DENY_SUMMARY_UNMATCHED_HOOK


def _denial_command_shape(command: str) -> str:
    """Classify a denied Bash command's shape for --deny-summary.

    Normalizes before matching, in order: strips a leading NAME=VALUE
    environment assignment, basenames the first token (an absolute script
    path is a home-rooted path, one of this repo's six always-on structural
    redaction detectors), and drops the values of git's repo-selection flags
    (see _drop_denial_command_flag_values). Only a multiplexer command
    (_DENIAL_COMMAND_MULTIPLEXERS) gets a command+subcommand shape, and only
    when the candidate subcommand token doesn't itself look like a flag — an
    unenumerated flag (one _drop_denial_command_flag_values doesn't know
    about) is left in place rather than dropped, so this guards it from
    being read as, and printed as, the subcommand — and is itself a member of
    that multiplexer's _DENIAL_COMMAND_SUBCOMMANDS allowlist, so an
    unenumerated non-flag token (a credential-shaped string, a raw control
    byte) falls to "other" instead of being echoed verbatim. Anything else,
    including an empty command, falls to "other". Nothing past the
    subcommand token is ever printed, so an argument value — a commit
    message, a path, a control character — never survives to stdout.
    """
    stripped = _DENIAL_COMMAND_ENV_PREFIX_RE.sub("", command)
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        tokens = stripped.split()
    if not tokens:
        return _DENY_SUMMARY_OTHER_COMMAND_SHAPE
    tokens[0] = os.path.basename(tokens[0])
    tokens = _drop_denial_command_flag_values(tokens)
    if len(tokens) > 1 and tokens[0] in _DENIAL_COMMAND_MULTIPLEXERS and not tokens[1].startswith("-"):
        subcommand = tokens[1]
        allowed_subcommands = _DENIAL_COMMAND_SUBCOMMANDS.get(tokens[0], frozenset())
        if subcommand in allowed_subcommands and _DENIAL_COMMAND_SUBCOMMAND_RE.match(subcommand):
            return f"{tokens[0]} {subcommand}"
    return _DENY_SUMMARY_OTHER_COMMAND_SHAPE


# toolDenialKind's gate-axis value — a permission-layer denial (hook denial or
# allowlist miss), already covered by hook_denial_key's message-signature
# match. The four other values (user-rejected, automode-blocked,
# automode-unavailable, interrupted) are friction, not a gate denial.
_GATE_TOOL_DENIAL_KIND = "permission-rule"

# The date toolDenialKind first appears in the corpus (the corpus itself
# starts 2026-06-24), determined by a corpus scan rather than a documented
# Claude Code rollout date — a user/tool_result record timestamped before
# this date structurally cannot carry the field, so --deny-summary's
# friction-kind breakdown must not read a pre-regime record's absent kind as
# zero friction.
_TOOL_DENIAL_KIND_REGIME_START = "2026-07-20"
_TOOL_DENIAL_KIND_REGIME_START_TS = _parse_ts(f"{_TOOL_DENIAL_KIND_REGIME_START}T00:00:00Z")


def _is_nongate_friction_kind(tool_denial_kind: str, already_gate_denied: bool) -> bool:
    """True if a user record's toolDenialKind marks non-gate friction.

    A falsy toolDenialKind means the field is absent from this record — not
    friction. already_gate_denied guards against double-classifying a block
    hook_denial_key already matched via the message-text signature, so a
    record can never produce both a denial event and a friction event.
    """
    if already_gate_denied or not tool_denial_kind:
        return False
    return tool_denial_kind != _GATE_TOOL_DENIAL_KIND


# --deny-summary's/review-trace's printed friction_kind vocabulary — closed,
# so a future harness-added toolDenialKind value prints as _FRICTION_KIND_OTHER
# rather than echoing the raw field verbatim.
_FRICTION_KINDS: frozenset[str] = frozenset({
    "user-rejected",
    "automode-blocked",
    "automode-unavailable",
    "interrupted",
})
_FRICTION_KIND_OTHER = "other-kind"


def _friction_kind_label(tool_denial_kind: str) -> str:
    """Map a friction event's toolDenialKind to its printed label."""
    return tool_denial_kind if tool_denial_kind in _FRICTION_KINDS else _FRICTION_KIND_OTHER


def _print_deny_summary(
    hook_counts: dict[str, int],
    command_shape_counts: dict[str, int],
    hook_shape_counts: Counter[tuple[str, str]],
    friction_counts: dict[str, int],
    pre_regime_tool_result_count: int,
    corpus_min_ts: float | None,
    corpus_max_ts: float | None,
) -> None:
    """Print --deny-summary's grouped denial-count tables plus the friction breakout.

    hook_shape_counts cross-tabs the hook/gate axis against the command-shape
    axis — the two marginal tables alone can't say which hook denied which
    command shape, which is the whole point of the census this feeds.
    """
    if corpus_min_ts is not None and corpus_max_ts is not None:
        print(f"\nCorpus window: {_fmt_date(corpus_min_ts)} to {_fmt_date(corpus_max_ts)}")

    total = sum(hook_counts.values())
    print(f"\n## Denials by hook/gate ({total} total)\n")
    print(f"{'Hook/gate':<40} {'Count':>6}")
    print("-" * 47)
    for label, count in sorted(hook_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{_sanitize_table_cell(label):<40} {count:>6}")

    print(f"\n## Denials by attempted command shape ({total} total)\n")
    print(f"{'Shape':<16} {'Count':>6}")
    print("-" * 23)
    for label, count in sorted(command_shape_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{_sanitize_table_cell(label):<16} {count:>6}")

    # Column set is the observed shapes only (already restricted to A3's
    # classifier output plus "other"), not a fixed enumeration — the
    # multiplexer+subcommand shape space is open-ended by construction.
    # Skipped entirely (rather than rendering a header-only, zero-row table)
    # when scope has zero denials — a friction-only report has nothing to
    # cross-tab.
    if hook_counts or command_shape_counts:
        shapes = sorted(command_shape_counts.keys())
        hooks = sorted(hook_counts.keys())
        col_width = max((len(s) for s in shapes), default=5) + 2
        # Rows/header are indented two spaces — unlike the marginal tables above,
        # deliberately, so a hook-label row here never collides with a
        # column-0 row-label match against the hook/gate marginal table.
        print(f"\n## Denials by hook/gate x command shape ({total} total)\n")
        header = f"  {'Hook':<40}" + "".join(
            f"{_sanitize_table_cell(shape):>{col_width}}" for shape in shapes
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for hook in hooks:
            row = f"  {_sanitize_table_cell(hook):<40}" + "".join(
                f"{hook_shape_counts.get((hook, shape), 0):>{col_width}}" for shape in shapes
            )
            print(row)

    friction_total = sum(friction_counts.values())
    print(f"\n## Friction events by kind ({friction_total} total)\n")
    print(f"{'Kind':<24} {'Count':>6}")
    print("-" * 31)
    for label, count in sorted(friction_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{_sanitize_table_cell(label):<24} {count:>6}")
    print(
        f"\n{pre_regime_tool_result_count} errored, non-gate tool result(s) predate the"
        f" per-record denial-kind field's introduction ({_TOOL_DENIAL_KIND_REGIME_START})"
        " and are excluded from the breakdown above — kind is structurally unmeasurable"
        " before that date, not zero."
    )


def _review_trace_session_events(
    records: list[dict],
    since_ts: float | None,
    until_epoch: float | None,
    branch_filter: set[str] | None,
    skill_filter: str | None = None,
) -> tuple[list[dict], dict[str, str], int]:
    """Detect cmd_review_trace's four per-session event kinds (skill, denial,
    friction, reviewer-spawn) from one session's records.

    Shared by cmd_review_trace's timeline printer and _compute_deny_summary_data
    so the denial/friction detection and dedup rules exist in one place rather
    than two copies kept in sync by hand. The third return value, a count of
    errored tool results predating toolDenialKind's introduction, is always
    computed (cheap) even though only --deny-summary reports it — see
    _print_deny_summary's own explanation of what it means.
    """
    events: list[dict] = []  # ordered, tagged with type/ts/line_no/branch/model
    # Tracks tool_use_ids already emitted as a denial. A legacy denial
    # appears as both an attachment record and an is_error tool_result
    # sharing one tool_use_id; this set collapses the pair to one event.
    seen_denial_ids: set[str] = set()

    # Friction events dedup against their own set, never seen_denial_ids
    # above — sharing it would let a friction event suppress a later
    # legitimate denial sharing a tool_use_id.
    seen_friction_ids: set[str] = set()

    # tool_use_id -> attempted command, for --deny-summary's by-command-shape
    # grouping. Indexed from every assistant tool_use block on the main
    # thread — review-trace's session_iter doesn't request subagent
    # records, so sidechain tool_use blocks are never present to index.
    # Independent of the --since/--until window, since a denial's own
    # event already applies it.
    tool_use_commands: dict[str, str] = {}

    # Carry-forward trackers, updated on every main-thread record before the
    # date filter below — the branch/model attributed to a denial (which
    # carries no message.model of its own) is whatever a prior main-thread
    # record last set, including one outside the --since/--until window.
    last_branch = ""
    last_model = ""
    pre_regime_tool_result_count = 0

    for line_no, rec in enumerate(records, start=1):
        if not bool(rec.get("isSidechain")):
            b = rec.get("gitBranch") or ""
            if b:
                last_branch = b
            if rec.get("type") == "assistant":
                m = (rec.get("message") or {}).get("model") or ""
                if m:
                    last_model = m

        if rec.get("type") == "assistant":
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tid = block.get("id")
                if tid:
                    tool_use_commands[tid] = (block.get("input") or {}).get("command", "")

        rec_ts_str: str | None = rec.get("timestamp")
        rec_ts: float | None = _parse_ts(rec_ts_str)

        # Apply date filter: records with no parseable timestamp are excluded when
        # a date boundary is active.
        if (since_ts is not None or until_epoch is not None):
            if rec_ts is None:
                continue
            if since_ts is not None and rec_ts < since_ts:
                continue
            if until_epoch is not None and rec_ts >= until_epoch:
                continue

        rec_type = rec.get("type", "")
        evt_branch = last_branch or "?"
        evt_model = _fam(last_model) if last_model else "?"

        # --- Signals 1 + 3: skill invocations and reviewer-agent spawns ---
        # Both are main-thread assistant tool_use blocks; a single pass over
        # content dispatches on tool name to avoid iterating the list twice.
        if rec_type == "assistant" and not bool(rec.get("isSidechain")):
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                block_name = block.get("name")
                if block_name == "Skill":
                    skill_name = (block.get("input") or {}).get("skill") or ""
                    if skill_name not in REVIEW_TRACE_SKILLS:
                        continue
                    if skill_filter and skill_name != skill_filter:
                        continue
                    events.append({
                        "kind": "skill",
                        "skill": skill_name,
                        "ts": rec_ts_str,
                        "line_no": line_no,
                        "branch": evt_branch,
                        "model": evt_model,
                    })
                elif block_name in ("Agent", "Task"):
                    stype = (block.get("input") or {}).get("subagent_type") or ""
                    if not _is_reviewer_subagent_type(stype):
                        continue
                    events.append({
                        "kind": "reviewer-spawn",
                        "subagent_type": stype,
                        "ts": rec_ts_str,
                        "line_no": line_no,
                        "branch": evt_branch,
                        "model": evt_model,
                    })

        # --- Signal 2a: hook denials, legacy shape (attachment record) ---
        if rec_type == "attachment":
            denial = hook_denial_key(rec)
            if denial is None:
                continue
            tool_use_id, att = denial
            if tool_use_id and tool_use_id in seen_denial_ids:
                continue
            raw_error = att.get("blockingError")
            normalized = _normalize_blocking_error(raw_error)
            hook_name = att.get("hookName") or ""
            if isinstance(normalized, dict):
                # Real transcripts nest the human-readable text in a "blockingError"
                # key alongside a "command" key; fall back to "message" then repr.
                message = (
                    normalized.get("blockingError")
                    or normalized.get("message")
                    or str(normalized)
                )
            else:
                message = str(normalized) if normalized else ""
            if tool_use_id:
                seen_denial_ids.add(tool_use_id)
            events.append({
                "kind": "denial",
                "hook_name": hook_name,
                "tool_use_id": tool_use_id,
                "message": message,
                "ts": rec_ts_str,
                "line_no": line_no,
                "branch": evt_branch,
                "model": evt_model,
            })

        # --- Signal 2b: hook denials, current shape (is_error tool_result) ---
        # Claude Code stopped emitting the hook_blocking_error attachment
        # record; current transcripts surface a denial only as an is_error
        # tool_result, identified by the hook-denial message signature.
        #
        # --- Signal 2c: non-gate friction, current shape (toolDenialKind) ---
        # toolDenialKind lives on this same `user` record, not on the
        # tool_result block — read once, but classification below still
        # requires the individual block's own is_error, since a parallel
        # tool call can carry an unrelated successful block alongside it.
        if rec_type == "user":
            tool_denial_kind = rec.get("toolDenialKind") or ""
            # A falsy tool_denial_kind this far before the regime start
            # means the field structurally could not exist yet, not that
            # this record measured zero friction. Scoped to the same
            # is_error-and-non-gate-signature population
            # _is_nongate_friction_kind would classify below, so the
            # count reflects records that could plausibly have been
            # friction, not every tool_result in the era (which would
            # count ordinary successful tool calls too) — tallied
            # separately and reported apart from the friction-kind
            # breakdown.
            pre_regime = (
                not tool_denial_kind
                and rec_ts is not None
                and _TOOL_DENIAL_KIND_REGIME_START_TS is not None
                and rec_ts < _TOOL_DENIAL_KIND_REGIME_START_TS
                and (not branch_filter or evt_branch in branch_filter)
            )
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                denial = hook_denial_key(block)
                already_gate_denied = denial is not None
                if denial is not None:
                    tool_use_id, message = denial
                    if not (tool_use_id and tool_use_id in seen_denial_ids):
                        if tool_use_id:
                            seen_denial_ids.add(tool_use_id)
                        events.append({
                            "kind": "denial",
                            "hook_name": "",
                            "tool_use_id": tool_use_id,
                            "message": message,
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                        })

                if block.get("is_error") and _is_nongate_friction_kind(tool_denial_kind, already_gate_denied):
                    friction_tool_use_id = block.get("tool_use_id") or ""
                    if not (friction_tool_use_id and friction_tool_use_id in seen_friction_ids):
                        if friction_tool_use_id:
                            seen_friction_ids.add(friction_tool_use_id)
                        events.append({
                            "kind": "friction",
                            "friction_kind": tool_denial_kind,
                            "tool_use_id": friction_tool_use_id,
                            "message": _content_text(block.get("content")),
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                        })
                elif pre_regime and not already_gate_denied and block.get("is_error"):
                    pre_regime_tool_result_count += 1

    # Branch filtering happens after dedup (seen_denial_ids was populated
    # above over every event, unconditionally) so a duplicate-id denial on
    # a differently-branched record is suppressed, not re-emitted as a
    # distinct in-scope event.
    if branch_filter:
        events = [e for e in events if e["branch"] in branch_filter]

    return events, tool_use_commands, pre_regime_tool_result_count


def _compute_deny_summary_data(
    session_iter,
    since_ts: float | None = None,
    until_ts: float | None = None,
    branch_filter: set[str] | None = None,
    deny_only: bool = False,
) -> dict:
    """Corpus-wide --deny-summary accumulation, extracted so cost-ledger's
    per-week denial count and cmd_review_trace's own report share one pass
    over session_iter instead of two implementations kept in sync by hand.

    since_ts/until_ts are explicit epoch-second boundaries (until_ts
    exclusive, the same convention as cmd_review_trace's own until_epoch)
    rather than the CLI's date-string args, so a caller can pass exact week
    boundaries the CLI itself has no flag to reach.
    """
    hook_counts: dict[str, int] = defaultdict(int)
    command_shape_counts: dict[str, int] = defaultdict(int)
    hook_shape_counts: Counter[tuple[str, str]] = Counter()
    friction_counts: dict[str, int] = defaultdict(int)
    corpus_min_ts: float | None = None
    corpus_max_ts: float | None = None
    pre_regime_tool_result_count = 0
    any_session_matched = False

    for _jsonl, records in session_iter:
        events, tool_use_commands, session_pre_regime = _review_trace_session_events(
            records, since_ts, until_ts, branch_filter
        )
        if not events:
            continue
        any_session_matched = True
        pre_regime_tool_result_count += session_pre_regime

        # Corpus window reads the full branch-filtered per-session events list
        # before the deny_only skip below, same as the friction tally below —
        # so the reported window matches whatever --branches/--since/--until
        # actually put in scope, not the pre-branch-filter raw record range.
        for evt in events:
            evt_ts = _parse_ts(evt.get("ts"))
            if evt_ts is None:
                continue
            if corpus_min_ts is None or evt_ts < corpus_min_ts:
                corpus_min_ts = evt_ts
            if corpus_max_ts is None or evt_ts > corpus_max_ts:
                corpus_max_ts = evt_ts

        has_denial = any(e["kind"] == "denial" for e in events)

        # Friction tally reads the full per-session events list before the
        # deny_only skip below, so a friction-only session (has_denial False)
        # still contributes when --deny-only and --deny-summary run together.
        # deny_only's own session-selection stays denial-kind-only, unchanged.
        for evt in events:
            if evt["kind"] == "friction":
                friction_counts[_friction_kind_label(evt["friction_kind"])] += 1

        if deny_only and not has_denial:
            continue

        for evt in events:
            if evt["kind"] != "denial":
                continue
            hook_label = _denial_hook_label(evt["hook_name"], evt["message"])
            command = tool_use_commands.get(evt["tool_use_id"], "")
            command_shape = _denial_command_shape(command)
            hook_counts[hook_label] += 1
            command_shape_counts[command_shape] += 1
            hook_shape_counts[(hook_label, command_shape)] += 1

    return {
        "hook_counts": hook_counts,
        "command_shape_counts": command_shape_counts,
        "hook_shape_counts": hook_shape_counts,
        "friction_counts": friction_counts,
        "corpus_min_ts": corpus_min_ts,
        "corpus_max_ts": corpus_max_ts,
        "pre_regime_tool_result_count": pre_regime_tool_result_count,
        "any_session_matched": any_session_matched,
    }


def cmd_review_trace(args: argparse.Namespace) -> None:
    """Emit an ordered review-event timeline per session.

    Four event types are detected per session:
    - skill: main-thread Skill tool_use where input.skill is in REVIEW_TRACE_SKILLS
    - denial: a hook-blocking denial in either transcript shape — a legacy
      `attachment` record (type==hook_blocking_error) or a current-format
      `tool_result` block with is_error and a hook-denial message signature.
      A denial recorded as both shapes is collapsed to one event by tool_use_id.
    - friction: a current-format `user` record whose own toolDenialKind field
      marks non-gate friction (user-rejected, automode-blocked,
      automode-unavailable, interrupted) — see _is_nongate_friction_kind.
      Deduped by tool_use_id in its own set, independent of denial dedup.
    - reviewer: Agent/Task spawn where subagent_type is a reviewer type per
      _is_reviewer_subagent_type

    denial and friction are deliberately separate event kinds: has_denial,
    denials=N, and --deny-only's session-selection all stay denial-kind-only,
    so a non-gate toolDenialKind value never broadens what those three
    surfaces report — only the default timeline and --deny-summary's own
    friction breakout render friction events.

    Branch and model are resolved per event from the record that produced it,
    not from the session's first record: each is the last non-empty value
    carried forward up to that point, so a session that moves from one branch
    (or model) to another attributes each event correctly instead of labelling
    every event with whatever the session started on. An event whose branch or
    model cannot be resolved renders '?'. --branches filters the emitted event
    list by this per-event value, not by a single session-wide branch.

    --deny-summary delegates its entire accumulation to
    _compute_deny_summary_data instead of running its own pass over
    session_iter, so the corpus-wide grouped-count report and cost-ledger's
    per-week denial count can never drift apart.
    """
    branch_filter = _branch_filter(args)
    deny_only: bool = bool(getattr(args, "deny_only", False))
    deny_summary: bool = bool(getattr(args, "deny_summary", False))
    skill_filter: str | None = getattr(args, "skill", None) or None
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "review-trace", roots=roots)

    since_str: str | None = getattr(args, "since", None) or None
    until_str: str | None = getattr(args, "until", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    # Inclusive-day boundary: compute start of the *next* day and compare with strict <.
    # Adding 86400 seconds covers the entire until-day at any sub-second precision.
    until_epoch: float | None = None
    if until_str:
        day_start = _parse_ts(f"{until_str}T00:00:00Z")
        if day_start is not None:
            until_epoch = day_start + 86400

    if deny_summary:
        # Ahead of the scan, matching the default arm below: a crash partway
        # through the corpus still leaves the scanned scope on stdout.
        _print_resolved_scope("review-trace", scope_label, roots)
        data = _compute_deny_summary_data(
            session_iter, since_ts=since_ts, until_ts=until_epoch,
            branch_filter=branch_filter, deny_only=deny_only,
        )
        if sum(data["hook_counts"].values()) or sum(data["friction_counts"].values()):
            _print_deny_summary(
                data["hook_counts"], data["command_shape_counts"], data["hook_shape_counts"],
                data["friction_counts"], data["pre_regime_tool_result_count"],
                data["corpus_min_ts"], data["corpus_max_ts"],
            )
        elif data["any_session_matched"]:
            # Sessions matched but none carried a denial — distinct from the
            # scope matching no sessions at all, which the else covers.
            print("\nNo denials found in scope.")
        else:
            print(f"\n{_REVIEW_TRACE_NO_SESSIONS_MSG}")
        return

    # Printed before the scan, not on the first emitted block: a run matching
    # no session must still state the corpus it read, or a wrongly-scoped scan
    # is indistinguishable from a correctly-scoped empty one.
    _print_resolved_scope("review-trace", scope_label, roots)
    emitted_any_session = False

    for jsonl, records in session_iter:
        events, tool_use_commands, _pre_regime = _review_trace_session_events(
            records, since_ts, until_epoch, branch_filter, skill_filter=skill_filter
        )
        if not events:
            continue

        has_denial = any(e["kind"] == "denial" for e in events)
        if deny_only and not has_denial:
            continue

        skill_count = sum(1 for e in events if e["kind"] == "skill")
        denial_count = sum(1 for e in events if e["kind"] == "denial")
        spawn_count = sum(1 for e in events if e["kind"] == "reviewer-spawn")
        branches_seen = ",".join(sorted({e["branch"] for e in events}))
        models_seen = ",".join(sorted({e["model"] for e in events}))

        emitted_any_session = True

        print(f"\n### {jsonl}")
        print(
            f"branches={branches_seen}  models={models_seen}  skills={skill_count}"
            f"  denials={denial_count}  reviewer-spawns={spawn_count}"
        )
        for evt in events:
            ts_label = evt.get("ts") or "?"
            lno = evt["line_no"]
            kind = evt["kind"]
            suffix = f"  (branch={evt['branch']} model={evt['model']})"
            if kind == "skill":
                print(f"  [{ts_label}] line {lno:>5}  skill        {evt['skill']}{suffix}")
            elif kind == "denial":
                hook = evt['hook_name']
                uid = evt['tool_use_id']
                msg = evt['message']
                print(f"  [{ts_label}] line {lno:>5}  denial       hook={hook}  id={uid}  msg={msg!r}{suffix}")
            elif kind == "friction":
                fkind = _friction_kind_label(evt['friction_kind'])
                uid = evt['tool_use_id']
                msg = evt['message']
                print(f"  [{ts_label}] line {lno:>5}  friction     kind={fkind}  id={uid}  msg={msg!r}{suffix}")
            elif kind == "reviewer-spawn":
                print(f"  [{ts_label}] line {lno:>5}  reviewer     {evt['subagent_type']}{suffix}")

    if not emitted_any_session:
        print(f"\n{_REVIEW_TRACE_NO_SESSIONS_MSG}")


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

    since_str: str | None = getattr(args, "since", None) or None
    until_str: str | None = getattr(args, "until", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    until_epoch: float | None = None
    if until_str:
        day_start = _parse_ts(f"{until_str}T00:00:00Z")
        if day_start is not None:
            until_epoch = day_start + 86400

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
    directories the same way cost does; under more than one root, both
    branch names and subagent_type values are redacted
    (_assign_root_scoped_redact_label) — subagent_type can name a
    project-scoped custom agent definition, the same disclosure risk
    gitBranch carries — and the model-mix table is keyed on the redacted
    (root, subagent_type) pair so two accounts' same-named agentType never
    merge into one row. --per-session is refused outright under multi-root,
    since it would otherwise join a foreign account's own session-id prefix
    to its branch name.
    """
    roots = _resolve_cost_roots(args, "subagent-mix")
    multi_root = len(roots) > 1
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
    since_date_str: str | None = getattr(args, "since_date", None) or None
    until_date_str: str | None = getattr(args, "until_date", None) or None
    dollar_since_ts: float | None = _parse_ts(f"{since_date_str}T00:00:00Z") if since_date_str else None
    dollar_until_ts: float | None = None
    if until_date_str:
        day_start = _parse_ts(f"{until_date_str}T00:00:00Z")
        if day_start is not None:
            dollar_until_ts = day_start + 86400

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

    data: dict[str, dict] = defaultdict(
        lambda: {"sessions": 0, "spawns": defaultdict(int), "skills": defaultdict(int)}
    )
    # (possibly redacted) agentType label -> model-mix row. Only created for
    # a type that has at least one meta.json match (even a dangling one) —
    # a dispatch with no matching meta.json at all is excluded entirely,
    # matching cmd_reviewer_yield's own precedent for the same join. Under
    # multi-root, keying on the redacted label (rather than the raw
    # subagent_type) also root-scopes this table: two accounts' same-named
    # agentType get distinct labels and never merge into one row.
    model_mix: dict[str, dict] = defaultdict(lambda: {
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
                    stype = inp.get("subagent_type") or "unknown"
                    stype_label = (
                        _assign_root_scoped_redact_label(
                            "agent-type", redact_ordinals[resolved_roots[root_idx]],
                            stype, subagent_type_redact_map
                        )
                        if root_idx is not None
                        else stype
                    )
                    session_data[branch]["spawns"][stype_label] += 1

                    paired = dispatch_index.get(block.get("id") or "")
                    if paired is not None:
                        paired_jsonl, requested_model = paired
                        row = model_mix[stype_label]
                        # _declared_pin reads from the on-disk agent file, so it
                        # needs the real subagent_type (stype), never the
                        # redacted display label (stype_label).
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
            branch_label = (
                _assign_root_scoped_redact_label(
                    "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map
                )
                if root_idx is not None
                else branch
            )
            key = f"{branch_label} [{jsonl.stem[:8]}]" if per_session else branch_label
            d = data[key]
            d["sessions"] += 1
            for stype, cnt in sd["spawns"].items():
                d["spawns"][stype] += cnt
            for skill, cnt in sd["skills"].items():
                d["skills"][skill] += cnt

    if not data:
        print("No data found.")
        return

    print(f"{'Branch':<45} {'Sess':>5} {'Spawns':>7} {'CR':>3} {'PR':>3} {'RR':>3}  Top subagent types")
    print("-" * 120)
    for key in sorted(data):
        d = data[key]
        spawns_total = sum(d["spawns"].values())
        top = sorted(d["spawns"].items(), key=lambda kv: (-kv[1], kv[0]))
        top_str = ", ".join(f"{t}({n})" for t, n in top[:5]) or "—"
        print(
            f"{key:<45} {d['sessions']:>5} {spawns_total:>7} "
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
        for stype_label in sorted(model_mix):
            row = model_mix[stype_label]
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
    different root. "built-in" when no on-disk agent file exists
    (general-purpose, claude-code-guide, Plan carry none), the file has no
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


def cmd_pr_link(args: argparse.Namespace) -> None:
    if not getattr(args, "repo", None):
        print("--repo is required for pr-link", file=sys.stderr)
        sys.exit(1)
    if not getattr(args, "branches", None):
        print("--branches is required for pr-link", file=sys.stderr)
        sys.exit(1)

    branches: list[str] = [b.strip() for b in args.branches.split(",") if b.strip()]
    repo: str = args.repo
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
                ["gh", "pr", "list", "--head", branch, "--repo", repo, "--state", "all", "--json", "number", "--limit", "1"],
                capture_output=True, text=True, check=True,
            )
            prs = json.loads(pr_result.stdout or "[]")
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
            print(f"{branch:<35} {'?':>5} {opus_n:>6} {sonnet_n:>7} {'gh-err':>9} {'':>10}")
            continue

        if not prs:
            print(f"{branch:<35} {'none':>5} {opus_n:>6} {sonnet_n:>7} {'—':>9} {'—':>10}")
            continue

        pr_number = prs[0]["number"]
        issue_comments = review_comments = 0

        try:
            ic = subprocess.run(
                ["gh", "api", f"repos/{repo}/issues/{pr_number}/comments", "--paginate", "--jq", ".[].user.login"],
                capture_output=True, text=True, check=True,
            )
            issue_logins = [ln.strip() for ln in ic.stdout.splitlines() if ln.strip()]
            issue_comments = sum(1 for ln in issue_logins if not author or ln == author)

            rc = subprocess.run(
                ["gh", "api", f"repos/{repo}/pulls/{pr_number}/comments", "--paginate", "--jq", ".[].user.login"],
                capture_output=True, text=True, check=True,
            )
            review_logins = [ln.strip() for ln in rc.stdout.splitlines() if ln.strip()]
            review_comments = sum(1 for ln in review_logins if not author or ln == author)
        except subprocess.CalledProcessError:
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


_AUDIT_CLASSES: tuple[str, ...] = (
    "orchestration", "judgment", "code-write", "code-read", "pure-thinking", "other"
)

_ORCHESTRATION_TOOLS: frozenset[str] = frozenset({"Agent", "Task"})
_CODE_READ_TOOLS: frozenset[str] = frozenset({"Read", "Grep", "Glob", "Bash"})


def _classify_opus_turn(
    content: list,
    in_judgment_span: bool,
    plan_mode_active: bool,
) -> str:
    """Classify one Opus assistant turn into an audit routing class.

    Classification is first-match among:
      orchestration  — any Agent/Task tool_use
      judgment       — turn is within an active judgment span (skill or plan-mode)
      code-write     — any Edit/Write/MultiEdit/NotebookEdit tool_use
      code-read      — at least one tool_use, all from Read/Grep/Glob/Bash
      pure-thinking  — thinking blocks only, no tool_use
      other          — none of the above
    """
    tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    tool_names = {b.get("name") for b in tool_use_blocks}

    if tool_names & _ORCHESTRATION_TOOLS:
        return "orchestration"
    if in_judgment_span or plan_mode_active:
        return "judgment"
    if tool_names & corpus._CODE_WRITE_TOOLS:
        return "code-write"
    if tool_use_blocks and tool_names <= _CODE_READ_TOOLS:
        return "code-read"
    has_thinking = any(isinstance(b, dict) and b.get("type") == "thinking" for b in content)
    if has_thinking and not tool_use_blocks:
        return "pure-thinking"
    return "other"


def cmd_audit_routing(args: argparse.Namespace) -> None:
    """Per-turn Opus token breakdown by routing class across all sessions.

    Classifies every Opus assistant turn into: orchestration, judgment,
    code-write, code-read, pure-thinking, or other — then aggregates
    output_tokens and cache_read_input_tokens per class. Emits per-session
    rows sorted descending by total output tokens, plus a corpus aggregate.
    """
    top_n: int = getattr(args, "top", 20) or 20
    redact: bool = bool(getattr(args, "redact", False))

    since_ts, since_raw = _parse_since_nd_arg(args, "audit-routing")
    since_label = since_raw or ""

    roots = _resolve_scan_roots(args)
    multi_root = len(roots) > 1

    # _resolve_project_scope's fail-closed --this-repo check runs before
    # _build_redact_map's full-corpus disk scan, so an out-of-repo failure
    # exits without paying for that scan.
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing", roots=roots)
    _print_resolved_scope("audit-routing", scope_label, roots)

    redact_map: dict[_RedactMapKey, str] = _build_redact_map(roots) if redact else {}
    session_redact_map: dict[str, str] = {}
    # Only computed under multi-root redaction: _root_index_for_path needs
    # already-resolved roots, and _redaction_ordinals is the same
    # resolved-path-sorted mapping _build_redact_map's keys and cost's own
    # per-row lookup share, so a row's ordinal always agrees with the map's.
    resolved_roots = [r.resolve() for r in roots] if (redact and multi_root) else []
    redact_ordinals = _redaction_ordinals(roots) if (redact and multi_root) else {}

    # Per-session accumulators: session_key → {class → {out, cr, dollars}}
    session_rows: list[dict] = []
    # Corpus totals: class → {out, cr, dollars}
    corpus_totals: dict[str, dict[str, float]] = {cls: {"out": 0, "cr": 0, "dollars": 0.0} for cls in _AUDIT_CLASSES}
    # Opus turns whose model ID has no _MODEL_BASE_INPUT_RATES entry — excluded from
    # the dollar headline, counted here so a corpus with unpriced turns doesn't
    # silently under-report (mirrors _cost_report's unpriced-tokens convention).
    unpriced_turns = 0
    unpriced_tokens = 0

    for jsonl, records in session_iter:
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so the classification and judgment-span
        # tracking below see every block (e.g. a Skill/ExitPlanMode tool_use
        # on a later block), while the run's dollars are attributed once.
        records = _dedup_turns_by_request_id(records)
        proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact:
            _assign_session_redact_label(session_id, session_redact_map)
        if redact and multi_root:
            root_position = _root_index_for_path(jsonl, resolved_roots)
            redact_key: _RedactMapKey = (redact_ordinals[resolved_roots[root_position]], proj_label)
        else:
            redact_key = proj_label

        # Per-session class token accumulators
        session_class_tokens: dict[str, dict[str, float]] = {
            cls: {"out": 0, "cr": 0, "dollars": 0.0} for cls in _AUDIT_CLASSES
        }

        # Judgment span state machine (reset per session)
        in_judgment_span: bool = False
        plan_mode_active: bool = False

        for rec in records:
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            # --- State machine updates for user/human records ---
            if rtype in ("user", "human"):
                # Judgment span closes at next user turn
                in_judgment_span = False
                # Detect plan-mode activation
                content_text = _content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                continue

            if rtype != "assistant":
                continue

            # Filter to Opus turns with usage data
            model = msg.get("model", "")
            if _fam(model) != "opus":
                # Still update span state from non-Opus assistant turns (ExitPlanMode)
                content = msg.get("content") or []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "ExitPlanMode"
                    ):
                        plan_mode_active = False
                continue

            usage = msg.get("usage")
            if not usage:
                continue

            # Apply --since filter
            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            content = msg.get("content") or []
            out_tokens: int = usage.get("output_tokens", 0)
            cr_tokens: int = usage.get("cache_read_input_tokens", 0)
            dollars_by_class, _context_at_turn, turn_unpriced_tokens = _price_turn(model, usage)
            if dollars_by_class is None:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
                turn_dollars = 0.0
            else:
                turn_dollars = sum(dollars_by_class.values())

            # Open a judgment span if this turn invokes a judgment skill — evaluated
            # before classification so the invoking turn itself counts as judgment.
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                    and (block.get("input") or {}).get("skill") in AUDIT_JUDGMENT_SKILLS
                ):
                    in_judgment_span = True
                    break

            turn_class = _classify_opus_turn(content, in_judgment_span, plan_mode_active)

            # ExitPlanMode clears plan-mode on the *next* turn (the current turn is still in-span).
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                ):
                    plan_mode_active = False
                    break

            session_class_tokens[turn_class]["out"] += out_tokens
            session_class_tokens[turn_class]["cr"] += cr_tokens
            session_class_tokens[turn_class]["dollars"] += turn_dollars

        session_total_out = sum(v["out"] for v in session_class_tokens.values())
        if not session_total_out:
            continue

        session_rows.append({
            "session_id": session_id,
            "proj_label": proj_label,
            "redact_key": redact_key,
            "classes": session_class_tokens,
            "total_out": session_total_out,
        })

        for cls in _AUDIT_CLASSES:
            corpus_totals[cls]["out"] += session_class_tokens[cls]["out"]
            corpus_totals[cls]["cr"] += session_class_tokens[cls]["cr"]
            corpus_totals[cls]["dollars"] += session_class_tokens[cls]["dollars"]

    # --- Emit per-session table ---
    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Opus turn-class breakdown ({title_since})\n")

    header = (
        f"{'Session':<16} {'Proj':<20} "
        f"{'orch':>8} {'judgment':>9} {'code-write':>11} {'code-read':>10} "
        f"{'thinking':>9} {'other':>7} {'total_out':>11} {'cache_rd':>10}"
    )
    print(header)
    print("─" * len(header))

    sorted_rows = sorted(session_rows, key=lambda r: r["total_out"], reverse=True)
    for row in sorted_rows[:top_n]:
        sid = _redact_session_id(row["session_id"], session_redact_map) if redact else row["session_id"]
        proj = _redact_proj_label(row["redact_key"], redact_map) if redact else row["proj_label"]
        cls = row["classes"]
        total_cr = sum(v["cr"] for v in cls.values())
        print(
            f"{sid:<16} {proj:<20} "
            f"{cls['orchestration']['out']:>8,} {cls['judgment']['out']:>9,} "
            f"{cls['code-write']['out']:>11,} {cls['code-read']['out']:>10,} "
            f"{cls['pure-thinking']['out']:>9,} {cls['other']['out']:>7,} "
            f"{row['total_out']:>11,} {total_cr:>10,}"
        )

    # --- Emit corpus aggregate ---
    print("\n## Corpus aggregate\n")
    print(f"{'Class':<16} {'Output tokens':>15} {'Cache read tokens':>18}")
    total_out_all = 0
    total_cr_all = 0
    for cls in _AUDIT_CLASSES:
        out_val = corpus_totals[cls]["out"]
        cr_val = corpus_totals[cls]["cr"]
        print(f"{cls:<16} {out_val:>15,} {cr_val:>18,}")
        total_out_all += out_val
        total_cr_all += cr_val
    print("─" * 51)
    print(f"{'total':<16} {total_out_all:>15,} {total_cr_all:>18,}")

    sonnet_tier_dollars = corpus_totals["code-write"]["dollars"] + corpus_totals["code-read"]["dollars"]
    priced_total_dollars = sum(corpus_totals[cls]["dollars"] for cls in _AUDIT_CLASSES)
    dollar_pct = f"{100 * sonnet_tier_dollars / priced_total_dollars:.0f}%" if priced_total_dollars else "—"
    print(f"\nSonnet-tier estimate: ${sonnet_tier_dollars:,.2f}")
    print(f"  = {dollar_pct} of priced Opus spend in this window")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")

    sonnet_tier_out = corpus_totals["code-write"]["out"] + corpus_totals["code-read"]["out"]
    sonnet_pct = f"{100 * sonnet_tier_out / total_out_all:.0f}%" if total_out_all else "—"
    print(f"\nSonnet-tier estimate: {sonnet_tier_out:,} output tokens (secondary diagnostic)")
    print(f"  = {sonnet_pct} of Opus output in this window")


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
# edit-family call, matched case-insensitively — a *different*, narrower
# purpose than _denial_hook_label's general "blocked by <name> hook/gate"
# extraction above: three of these six (path-spelling, permissions,
# worktree-isolation) are harness-native denial text, never a hook's own
# "blocked by ... hook/gate" wording, so _denial_hook_label's enumerated
# label set does not cover them.
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


# ~4 chars/token, the same rough English/code estimate _EDIT_FORMAT_CHARS_PER_TOKEN
# uses. A deliberate second pin rather than a shared constant: recalibrating one
# report's published figures must not silently move the other's.
_READ_SCOPE_CHARS_PER_TOKEN = 4

# Tools that answer "which part of this file do I need" before a targeted Read.
# Their mean result size prices the locate step a narrow-read discipline adds.
_READ_SCOPE_LOCATE_TOOLS = frozenset({"Grep", "Glob"})

_READ_SCOPE_COHORT_TARGETED = "targeted"
_READ_SCOPE_COHORT_WHOLE_FILE = "whole_file"
_READ_SCOPE_COHORT_PAGES = "pages"
_READ_SCOPE_COHORT_UNPARSED = "unparsed_input"

_READ_SCOPE_SCOPE_MAIN = "main"
_READ_SCOPE_SCOPE_SUBAGENT = "subagent"

# Result-size histogram buckets, in estimated tokens (chars // 4) — a call's
# result text length, not its input. The 2,000-token boundary lines up with
# the gross-ceiling threshold the case study's ceiling arithmetic uses.
_READ_SCOPE_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (500, "0-499"),
    (2000, "500-1999"),
    (5000, "2000-4999"),
    (15000, "5000-14999"),
)
_READ_SCOPE_SIZE_OVERFLOW_LABEL = "15000+"


def _read_scope_size_bucket(tokens: int) -> str:
    for upper_bound, label in _READ_SCOPE_SIZE_BUCKETS:
        if tokens < upper_bound:
            return label
    return _READ_SCOPE_SIZE_OVERFLOW_LABEL


def _classify_read_call(tool_input: dict) -> str:
    """Classify one Read tool_use's input by scope shape.

    A missing file_path (e.g. only __unparsedToolInput, or an empty input) is
    unparsed_input regardless of any other field: its scope is unknowable, and
    filing it as whole-file would inflate the cohort every published share is
    stated against. pages is checked before offset/limit since a PDF page-range
    read scopes via a different mechanism entirely, not layered on offset/limit.
    offset and limit are each checked with `is not None`, never truthiness --
    offset=0 is a valid first-line read and is falsy in Python.
    """
    if not tool_input.get("file_path"):
        return _READ_SCOPE_COHORT_UNPARSED
    if tool_input.get("pages") is not None:
        return _READ_SCOPE_COHORT_PAGES
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return _READ_SCOPE_COHORT_TARGETED
    return _READ_SCOPE_COHORT_WHOLE_FILE


def _new_read_scope_stats() -> dict:
    return {
        "read_total": 0,
        "offset_n": 0,  # Read calls carrying offset (is not None)
        "limit_n": 0,  # Read calls carrying limit (is not None)
        "both_n": 0,  # Read calls carrying both offset and limit
        "cohort_n": Counter(),  # cohort label -> Read call count
        "unpaired": 0,  # Read tool_use with no matching tool_result in this session
        "error_result": 0,  # is_error tool_result for a Read call
        "non_text_result": 0,  # non-string tool_result content for a Read call (e.g. an image block)
        "cohort_scope_count": Counter(),  # (cohort, scope) -> result count reaching the histogram
        "cohort_scope_tokens": Counter(),  # (cohort, scope) -> est. token sum
        "size_hist": Counter(),  # (cohort, scope, bucket label) -> count
        # Same key -> summed est. tokens. Counts alone cannot answer "what share
        # of whole-file-read tokens sits above N", which is what any ceiling
        # estimate and the case study's revisit trigger are both stated against.
        "size_hist_tokens": Counter(),
        "read_result_tokens_total": 0,  # every Read tool_result's est. tokens, any cohort/error/text shape
        "all_tool_result_tokens_total": 0,  # every tool_result's est. tokens, any tool -- cross-check denominator
        # Locate-step sizing: a targeted read has to be located first, so any
        # saving quoted against whole-file reads is gross until this is netted.
        "locate_call_n": 0,
        "locate_result_tokens_total": 0,
        "repeat_whole_file_reads": 0,  # whole-file re-reads of a path within the same source-file/sessionId partition
        "repeat_whole_file_tokens": 0,
        "repeat_whole_file_output_log_reads": 0,  # sub-count of the above whose path ends .output/.log
        "growth_tokens": 0,  # prompt-token growth, per "Computing the denominator"
        "growth_unparseable_ts_excluded": 0,  # growth deltas dropped: --since active, owning turn's ts didn't parse
    }


def _merge_read_scope_stats(dst: dict, src: dict) -> None:
    dst["read_total"] += src["read_total"]
    dst["offset_n"] += src["offset_n"]
    dst["limit_n"] += src["limit_n"]
    dst["both_n"] += src["both_n"]
    dst["cohort_n"].update(src["cohort_n"])
    dst["unpaired"] += src["unpaired"]
    dst["error_result"] += src["error_result"]
    dst["non_text_result"] += src["non_text_result"]
    dst["cohort_scope_count"].update(src["cohort_scope_count"])
    dst["cohort_scope_tokens"].update(src["cohort_scope_tokens"])
    dst["size_hist"].update(src["size_hist"])
    dst["size_hist_tokens"].update(src["size_hist_tokens"])
    dst["read_result_tokens_total"] += src["read_result_tokens_total"]
    dst["all_tool_result_tokens_total"] += src["all_tool_result_tokens_total"]
    dst["locate_call_n"] += src["locate_call_n"]
    dst["locate_result_tokens_total"] += src["locate_result_tokens_total"]
    dst["repeat_whole_file_reads"] += src["repeat_whole_file_reads"]
    dst["repeat_whole_file_tokens"] += src["repeat_whole_file_tokens"]
    dst["repeat_whole_file_output_log_reads"] += src["repeat_whole_file_output_log_reads"]
    dst["growth_tokens"] += src["growth_tokens"]
    dst["growth_unparseable_ts_excluded"] += src["growth_unparseable_ts_excluded"]


def _read_scope_growth_for_group(group: list[dict], since_ts: float | None) -> tuple[int, int]:
    """Sum of positive prompt-token growth deltas within one source-file group
    (one _read_session_file_partitioned entry: the main transcript or one
    subagent file).

    Keys the delta chain by each assistant record's own sessionId rather than
    picking one reference id for the whole group: a subagent transcript file
    is named by its own agent id, but its records carry the *parent*
    session's sessionId, so neither the file's own stem nor a first-seen-in-
    iteration guess is a valid "this group's session" ground truth. Keying by
    sessionId means an interleaved foreign-session record simply forms its
    own chain and contributes its own real growth, instead of being dropped
    or corrupting a neighbouring session's delta. A record with no sessionId
    at all folds into the ""-keyed chain -- neither excluded nor treated as
    its own session, preserving this function's long-standing leniency for
    absent ids. Resets the whole per-session map at each compact_boundary
    record, since a compaction boundary applies to the file, not to one
    session. The first turn of every resulting per-session sequence has no
    predecessor and so contributes nothing. A turn with absent or malformed
    usage is skipped without breaking its session's chain for the turn after
    it. A shrinking context contributes nothing (never negative). Every
    chain is built over every record regardless of --since; --since filters
    only the completed deltas, by the later (owning) turn's own timestamp --
    fail-closed: a delta whose owning turn's timestamp doesn't parse is
    excluded rather than included, and counted in the returned exclusion
    total so the report's own growth figure stays auditable against
    --since's promise.

    Returns (growth_tokens, deltas_excluded_for_unparseable_timestamp).
    """
    prev_context_by_session: dict[str, int] = {}
    total = 0
    unparseable_ts_excluded = 0

    for rec in group:
        rec_type = rec.get("type")
        if rec_type == "system" and rec.get("subtype") == "compact_boundary":
            # Resets every session's chain, not just the compacted one: a
            # compact_boundary record carries no sessionId, so which session it
            # belongs to is not recoverable from the data. Costs one real delta
            # for any other session mid-chain in the same file. Inert on every
            # transcript shape seen so far -- a main file's records all carry
            # its own session id, a subagent file's all carry its parent's --
            # so a file holding two live chains does not currently arise.
            prev_context_by_session.clear()
            continue
        if rec_type != "assistant":
            continue

        usage = (rec.get("message") or {}).get("usage")
        if not usage:
            continue

        session_key = rec.get("sessionId") or ""
        context_at_turn = _context_at_turn(usage)
        prev_context = prev_context_by_session.get(session_key)
        if prev_context is not None:
            delta = context_at_turn - prev_context
            if delta > 0:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if since_ts is not None and rec_ts is None:
                    unparseable_ts_excluded += 1
                elif since_ts is None or rec_ts >= since_ts:
                    total += delta
        prev_context_by_session[session_key] = context_at_turn

    return total, unparseable_ts_excluded


def _read_scope_repeat_whole_file_reads(groups: list[list[dict]]) -> tuple[int, int, int]:
    """Repeat whole-file-read detection, scoped to the same partition the
    growth chain uses (per source-file group, and per sessionId within a
    group) rather than to the whole flattened session.

    A parent transcript and its subagent are separate context windows: a
    subagent re-reading a file its parent already read is not a redundant
    read, it's the only way that subagent can see the file at all. Scoping
    to the flattened session would count every such cross-file read as a
    repeat, inflating the figure with reads that were never avoidable.

    Pairs each group's own Read tool_use/tool_result independently (a Read's
    result always lives in the same source file as its call), since this
    detection needs its own per-partition token histories and cannot reuse
    the flat pass's already-merged read_calls table. Returns
    (repeat_reads, repeat_tokens, repeat_output_log_reads) — pure aggregates;
    no file path is retained past this function.
    """
    # (group_index, sessionId-or-"") -> file_path -> [est_tokens, ...] in read order
    sizes_by_partition: dict[tuple[int, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for group_idx, group in enumerate(groups):
        read_calls: dict[str, dict] = {}  # tool_use_id -> {"file_path", "partition_key"}
        for rec in group:
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            session_id = rec.get("sessionId") or ""
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "tool_use":
                    if block.get("name") != "Read":
                        continue
                    tool_input = block.get("input") or {}
                    if _classify_read_call(tool_input) != _READ_SCOPE_COHORT_WHOLE_FILE:
                        continue
                    read_calls[block.get("id")] = {
                        "file_path": tool_input.get("file_path") or "",
                        "partition_key": (group_idx, session_id),
                    }
                elif block_type == "tool_result":
                    owner = read_calls.pop(block.get("tool_use_id"), None)
                    if owner is None or block.get("is_error"):
                        continue
                    result_content = block.get("content")
                    if not isinstance(result_content, str):
                        continue
                    tokens = len(result_content) // _READ_SCOPE_CHARS_PER_TOKEN
                    sizes_by_partition[owner["partition_key"]][owner["file_path"]].append(tokens)

    repeat_reads = 0
    repeat_tokens = 0
    repeat_output_log_reads = 0
    for by_path in sizes_by_partition.values():
        for file_path, sizes in by_path.items():
            if len(sizes) < 2:
                continue
            repeats = sizes[1:]
            repeat_reads += len(repeats)
            repeat_tokens += sum(repeats)
            if file_path.endswith((".output", ".log")):
                repeat_output_log_reads += len(repeats)

    return repeat_reads, repeat_tokens, repeat_output_log_reads


def _scan_read_scope_session(records: list[dict], groups: list[list[dict]], since_ts: float | None) -> dict:
    """One session's Read-call census, single pass over the flattened record
    order, plus per-group prompt-token growth and repeat-whole-file-read
    detection.

    `records` is the main thread + merged subagent files in flat, file-
    concatenation order (per read_session_file) — everything here except
    growth and repeat-whole-file-read detection runs over it, since
    isSidechain is all that scope (main vs subagent) bucketing needs.
    `groups` is the same session's records kept separate per source file
    (per _read_session_file_partitioned) — growth and repeat-whole-file-read
    detection both need the file (and, within a file, sessionId) boundary
    the flat order discards; see _read_scope_growth_for_group and
    _read_scope_repeat_whole_file_reads.

    Every returned figure is a pure aggregate (counts and token sums) — no
    file path, filename, path fragment, or session identifier is retained
    past this function or printed by any caller.
    """
    stats = _new_read_scope_stats()

    read_calls: dict[str, dict] = {}  # tool_use_id -> {"cohort", "scope", "file_path"}
    locate_calls: set[str] = set()  # tool_use_ids of Grep/Glob calls, for locate-step sizing
    matched: set[str] = set()

    for rec in records:
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        is_subagent = bool(rec.get("isSidechain"))
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_name = block.get("name")
                if tool_name in _READ_SCOPE_LOCATE_TOOLS:
                    # A targeted read has to be located first. Sizing that step
                    # is what lets a saving be quoted net rather than gross.
                    locate_calls.add(block.get("id"))
                    stats["locate_call_n"] += 1
                    continue
                if tool_name != "Read":
                    continue
                tool_id = block.get("id")
                tool_input = block.get("input") or {}
                has_offset = tool_input.get("offset") is not None
                has_limit = tool_input.get("limit") is not None
                if has_offset:
                    stats["offset_n"] += 1
                if has_limit:
                    stats["limit_n"] += 1
                if has_offset and has_limit:
                    stats["both_n"] += 1
                cohort = _classify_read_call(tool_input)
                stats["read_total"] += 1
                stats["cohort_n"][cohort] += 1
                read_calls[tool_id] = {
                    "cohort": cohort,
                    "scope": _READ_SCOPE_SCOPE_SUBAGENT if is_subagent else _READ_SCOPE_SCOPE_MAIN,
                    "file_path": tool_input.get("file_path") or "",
                }
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                result_content = block.get("content")
                is_error = bool(block.get("is_error"))
                result_tokens = (
                    len(result_content) // _READ_SCOPE_CHARS_PER_TOKEN if isinstance(result_content, str) else 0
                )
                # Cross-check denominator: every tool's own result, Read or not.
                stats["all_tool_result_tokens_total"] += result_tokens
                if tool_use_id in locate_calls:
                    stats["locate_result_tokens_total"] += result_tokens

                owner = read_calls.get(tool_use_id)
                if owner is None:
                    continue
                matched.add(tool_use_id)
                stats["read_result_tokens_total"] += result_tokens

                if is_error:
                    stats["error_result"] += 1
                    continue
                if not isinstance(result_content, str):
                    stats["non_text_result"] += 1
                    continue

                cohort = owner["cohort"]
                if cohort not in (_READ_SCOPE_COHORT_TARGETED, _READ_SCOPE_COHORT_WHOLE_FILE):
                    continue
                thread_scope = owner["scope"]
                stats["cohort_scope_count"][(cohort, thread_scope)] += 1
                stats["cohort_scope_tokens"][(cohort, thread_scope)] += result_tokens
                size_bucket = _read_scope_size_bucket(result_tokens)
                stats["size_hist"][(cohort, thread_scope, size_bucket)] += 1
                stats["size_hist_tokens"][(cohort, thread_scope, size_bucket)] += result_tokens

    stats["unpaired"] = len(read_calls) - len(matched)

    (
        stats["repeat_whole_file_reads"],
        stats["repeat_whole_file_tokens"],
        stats["repeat_whole_file_output_log_reads"],
    ) = _read_scope_repeat_whole_file_reads(groups)

    for group in groups:
        growth, unparseable_ts_excluded = _read_scope_growth_for_group(group, since_ts)
        stats["growth_tokens"] += growth
        stats["growth_unparseable_ts_excluded"] += unparseable_ts_excluded

    return stats


def cmd_read_scope(args: argparse.Namespace) -> None:
    """CLI entry point for the read-scope subcommand.

    Root resolution happens here, mirroring cmd_edit_format, so --config-dir
    validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="read-scope")
    _read_scope_report(args, roots)


def _read_scope_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Single-pass Read-call scope census: offset/limit/pages classification
    against the full call count, result-token distribution by targeted/
    whole-file cohort and main/subagent scope, repeat-whole-file-read
    aggregates, and per-file-and-sessionId-partitioned prompt-token growth.
    One reproducible scan producing every figure the case study cites.

    roots is None for every direct caller other than cmd_read_scope (this
    module's own tests included) — mirrors edit-format's own contract,
    including the absence of the per-account breakdown below.

    This report's own content never varies with `redact`: like edit-format,
    it carries no project name, session ID, file path, or path fragment —
    the repeat-whole-file-read aggregates are pure counts and token sums, and
    per-account rows use account-N labels. --no-redact is still accepted and
    still enforces the same multi-root refusal and DO NOT PUBLISH banner, for
    CLI parity.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "read-scope: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = _parse_since_nd_arg(args, "read-scope")
    since_label = since_raw or ""

    session_iter, scope_label = _resolve_project_scope(args, "read-scope", include_subagents=True, roots=roots)
    _print_resolved_scope("read-scope", scope_label, scan_roots)

    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []

    stats = _new_read_scope_stats()
    per_account: list[dict] = [_new_read_scope_stats() for _ in scan_roots] if multi_root else []

    for jsonl, records in session_iter:
        # session_iter already read and parsed this file once internally (to
        # decide whether to yield it at all); this second, partitioned read
        # is the cost of reusing _resolve_project_scope's shared iterator,
        # which has no variant that also exposes the per-file boundary the
        # growth chain and repeat-whole-file-read detection need.
        groups = _read_session_file_partitioned(jsonl, include_subagents=True)
        session_stats = _scan_read_scope_session(records, groups, since_ts)
        _merge_read_scope_stats(stats, session_stats)
        if multi_root:
            idx = _root_index_for_path(jsonl, resolved_scan_roots)
            _merge_read_scope_stats(per_account[idx], session_stats)

    _print_read_scope_report(stats, per_account if multi_root else None, since_label)


def _read_scope_cohort_bucket_token_total(stats: dict, cohort: str) -> int:
    """Sum of `cohort`'s size_hist_tokens across both main and subagent scope
    -- the shared denominator each size-histogram bucket line's percentage
    divides by, so a subagent-dominated cohort's tokens aren't hidden behind
    the printing scope's own, smaller total."""
    bucket_labels = [label for _upper, label in _READ_SCOPE_SIZE_BUCKETS] + [_READ_SCOPE_SIZE_OVERFLOW_LABEL]
    return sum(
        stats["size_hist_tokens"].get((cohort, thread_scope, label), 0)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
        for label in bucket_labels
    )


def _print_read_scope_report(stats: dict, per_account: list[dict] | None, since_label: str) -> None:
    read_total = stats["read_total"]
    cohort_n = stats["cohort_n"]
    targeted_n = cohort_n.get(_READ_SCOPE_COHORT_TARGETED, 0)
    whole_file_n = cohort_n.get(_READ_SCOPE_COHORT_WHOLE_FILE, 0)
    pages_n = cohort_n.get(_READ_SCOPE_COHORT_PAGES, 0)
    unparsed_n = cohort_n.get(_READ_SCOPE_COHORT_UNPARSED, 0)

    print("\n## Read-call census\n")
    print(f"Read calls: {read_total:,}")
    print(f"  offset present: {stats['offset_n']:,}")
    print(f"  limit present:  {stats['limit_n']:,}")
    print(f"  both present:   {stats['both_n']:,}")
    # Cohort shares divide by the full Read call census, never targeted +
    # whole_file — pages/unparsed_input calls are real Read calls that would
    # otherwise silently vanish from the arithmetic.
    print(f"\ntargeted    {targeted_n:,}  ({_pct_of(targeted_n, read_total)} of Read calls)")
    print(f"whole_file  {whole_file_n:,}  ({_pct_of(whole_file_n, read_total)} of Read calls)")
    print(f"\npages (Read calls scoping a PDF via `pages` rather than offset/limit): {pages_n:,}")
    print(
        "unparsed_input (Read tool_use whose input carried no file_path, e.g. only"
        f" __unparsedToolInput -- scope unknowable): {unparsed_n:,}"
    )
    print(f"unpaired (Read tool_use with no matching tool_result found in this session): {stats['unpaired']:,}")
    print(f"error_result (is_error tool_result for a Read call, excluded from the size histogram): {stats['error_result']:,}")
    print(
        "non_text_result (non-string tool_result content for a Read call, e.g. an image"
        f" block, excluded from the size histogram): {stats['non_text_result']:,}"
    )

    print("\n## Result token distribution by cohort x scope\n")
    cpt = _READ_SCOPE_CHARS_PER_TOKEN
    for cohort, cohort_label in (
        (_READ_SCOPE_COHORT_TARGETED, "targeted"),
        (_READ_SCOPE_COHORT_WHOLE_FILE, "whole_file"),
    ):
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT):
            count = stats["cohort_scope_count"].get((cohort, thread_scope), 0)
            tokens = stats["cohort_scope_tokens"].get((cohort, thread_scope), 0)
            print(f"  {cohort_label:12} {thread_scope:9} count={count:8,}  tokens=~{tokens:12,}")

    whole_file_tokens = sum(
        stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_WHOLE_FILE, thread_scope), 0)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
    )
    targeted_tokens = sum(
        stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_TARGETED, thread_scope), 0)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
    )
    result_tokens_total = whole_file_tokens + targeted_tokens
    print(f"\nwhole_file share of targeted+whole_file result tokens: {_pct_of(whole_file_tokens, result_tokens_total)}")
    subagent_whole_file_tokens = stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_WHOLE_FILE, _READ_SCOPE_SCOPE_SUBAGENT), 0)
    print(f"whole_file tokens inside subagents: {_pct_of(subagent_whole_file_tokens, whole_file_tokens)}")

    print("\nsize histogram (est. tokens):\n")
    bucket_labels = [label for _upper, label in _READ_SCOPE_SIZE_BUCKETS] + [_READ_SCOPE_SIZE_OVERFLOW_LABEL]
    for cohort, cohort_label in (
        (_READ_SCOPE_COHORT_TARGETED, "targeted"),
        (_READ_SCOPE_COHORT_WHOLE_FILE, "whole_file"),
    ):
        cohort_tokens = _read_scope_cohort_bucket_token_total(stats, cohort)
        for thread_scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT):
            cohort_scope_count = stats["cohort_scope_count"].get((cohort, thread_scope), 0)
            print(f"  {cohort_label} / {thread_scope}:")
            for label in bucket_labels:
                count = stats["size_hist"].get((cohort, thread_scope, label), 0)
                tokens = stats["size_hist_tokens"].get((cohort, thread_scope, label), 0)
                print(
                    f"    {label:10} {count:6,}  ({_pct_of(count, cohort_scope_count)})"
                    f"  ~{tokens:11,} tok  ({_pct_of(tokens, cohort_tokens)} of {cohort_label} tokens)"
                )

    print(
        "\n## Repeat whole-file reads (same path read whole-file more than once in the same"
        " source file / sessionId -- a subagent re-reading what its parent already read is a"
        " separate context window, not a repeat)\n"
    )
    print(f"repeat reads: {stats['repeat_whole_file_reads']:,}  (~{stats['repeat_whole_file_tokens']:,} tok)")
    print(f"  of which .output/.log suffixed: {stats['repeat_whole_file_output_log_reads']:,}")

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Prompt-token growth ({title_since})\n")
    growth_tokens = stats["growth_tokens"]
    print(f"prompt-token growth: {growth_tokens:,}")
    print(
        "growth deltas excluded (--since active, owning turn's timestamp unparseable):"
        f" {stats['growth_unparseable_ts_excluded']:,}"
    )
    print(f"Read-result tokens as share of prompt-token growth: {_pct_of(stats['read_result_tokens_total'], growth_tokens)}")
    print(
        "Read-result tokens as share of total tool-result tokens (self-consistent"
        f" cross-check, both ~{cpt} chars/tok): "
        f"{_pct_of(stats['read_result_tokens_total'], stats['all_tool_result_tokens_total'])}"
    )
    locate_n = stats["locate_call_n"]
    locate_mean = stats["locate_result_tokens_total"] // locate_n if locate_n else 0
    print(
        f"\nlocate-step cost (Grep/Glob calls, the step a targeted read adds): {locate_n:,} calls, "
        f"~{stats['locate_result_tokens_total']:,} tok, mean ~{locate_mean:,} tok/call -- "
        f"any saving quoted against whole-file reads is gross until this is netted against it"
    )

    if per_account is not None:
        print("\n## Per-account breakdown\n")
        for idx, account_stats in enumerate(per_account):
            account_label = f"account-{idx + 1}"
            a_read_total = account_stats["read_total"]
            if a_read_total == 0:
                print(f"  {account_label:10} no Read calls")
                continue
            a_cohort_n = account_stats["cohort_n"]
            a_targeted = a_cohort_n.get(_READ_SCOPE_COHORT_TARGETED, 0)
            a_whole_file = a_cohort_n.get(_READ_SCOPE_COHORT_WHOLE_FILE, 0)
            print(
                f"  {account_label:10} calls={a_read_total:6,}  "
                f"targeted={_pct_of(a_targeted, a_read_total):>6}  whole_file={_pct_of(a_whole_file, a_read_total):>6}"
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


# --- cache-rebuild: idle-gap prompt-cache TTL-expiry measurement ----------
# See .claude/plans/context-cost-root-cause.md for the corpus finding this
# subcommand reproduces: a full-prefix cache rebuild after the vendor's 5m/1h
# cache TTL expires during a gap, priced against a warm-cache read at the
# same token count.

_CACHE_REBUILD_DEFAULT_THRESHOLD = 100_000
_CACHE_REBUILD_DEFAULT_SINCE = "30d"

# Idle-gap boundaries mirror the vendor's own 5-minute/1-hour cache tiers
# (_CACHE_WRITE_5M_MULTIPLIER/_CACHE_WRITE_1H_MULTIPLIER above, same source).
_CACHE_REBUILD_IDLE_5M_SECONDS = 300
_CACHE_REBUILD_IDLE_1H_SECONDS = 3600

_CAUSE_SESSION_START = "session start"
_CAUSE_IDLE_5M_1H = "idle 5m-1h"
_CAUSE_IDLE_OVER_1H = "idle >1h"
_CAUSE_MODEL_SWITCH = "model switch"
_CAUSE_UNEXPLAINED = "unexplained"
_CAUSE_TS_ANOMALY = "excluded (timestamp anomaly)"

# Print order for the cause-breakdown table: the two TTL-explained idle
# buckets first, then the non-idle tail, then the excluded diagnostic bucket
# last -- a malformed/out-of-order timestamp pair gets its own explicit row
# here rather than silently falling into "unexplained" or an idle bucket.
_CACHE_REBUILD_CAUSES: tuple[str, ...] = (
    _CAUSE_SESSION_START, _CAUSE_IDLE_5M_1H, _CAUSE_IDLE_OVER_1H,
    _CAUSE_MODEL_SWITCH, _CAUSE_UNEXPLAINED, _CAUSE_TS_ANOMALY,
)

# Only these two causes are TTL-expiry rebuilds eligible for priced excess
# and the concurrency split below -- session start has no prior cache to
# have hit, and model switch/unexplained are not gap-driven.
_CACHE_REBUILD_IDLE_GAP_CAUSES: tuple[str, ...] = (_CAUSE_IDLE_5M_1H, _CAUSE_IDLE_OVER_1H)


def _cache_rebuild_gap_seconds(prev_ts: float | None, cur_ts: float | None) -> float | None:
    """Seconds since the previous call in this transcript's own turn
    sequence, or None when either endpoint is unparseable or the delta is
    negative (clock skew) -- both must classify as a timestamp anomaly
    (_CAUSE_TS_ANOMALY) rather than a silently computed idle bucket."""
    if prev_ts is None or cur_ts is None:
        return None
    gap = cur_ts - prev_ts
    return gap if gap >= 0 else None


def _classify_cache_rebuild_cause(
    is_first_call: bool, gap_seconds: float | None, model_changed: bool, pure_1h_tier_write: bool
) -> str:
    """Classify one threshold-crossing cache-write call's cause.

    Idle buckets take priority over model switch -- the latter only applies
    inside the still-warm 5-minute window. pure_1h_tier_write is True when
    the call's cache-write tokens are entirely ephemeral_1h-tier (no
    ephemeral_5m) -- such a write can't have been forced by a <1h gap, since
    the 1h-TTL cache would still be warm, so it falls to "unexplained"
    instead of "idle 5m-1h".
    """
    if is_first_call:
        return _CAUSE_SESSION_START
    if gap_seconds is None:
        return _CAUSE_TS_ANOMALY
    if gap_seconds >= _CACHE_REBUILD_IDLE_1H_SECONDS:
        return _CAUSE_IDLE_OVER_1H
    if gap_seconds >= _CACHE_REBUILD_IDLE_5M_SECONDS:
        return _CAUSE_UNEXPLAINED if pure_1h_tier_write else _CAUSE_IDLE_5M_1H
    if model_changed:
        return _CAUSE_MODEL_SWITCH
    return _CAUSE_UNEXPLAINED


def _cache_rebuild_excess_dollars(model: str, usage: dict) -> tuple[float | None, int]:
    """Priced excess for one idle-gap rebuild call: the dollar delta between
    what its cache-write tokens were actually billed and what the same
    token count would have cost at the cache-read rate (a warm hit).

    Returns (excess_dollars, unpriced_tokens): excess_dollars is None, and
    unpriced_tokens carries the turn's total token count, when the model has
    no _MODEL_BASE_INPUT_RATES entry -- matching _price_turn's own
    unpriced-model contract.
    """
    dollars_by_class, _context_at_turn, unpriced_tokens = _price_turn(model, usage)
    if dollars_by_class is None:
        return None, unpriced_tokens
    write_dollars = dollars_by_class["cache_write_1h"] + dollars_by_class["cache_write_5m"]
    eph_1h, eph_5m = _cache_write_split(usage)
    rates = _model_rates(model)
    warm_read_dollars = (eph_1h + eph_5m) / 1_000_000 * rates["cache_read"]
    # Mirrors _price_turn's own fast/geo multiplier application so the
    # counterfactual warm read is priced under the same settled infra
    # conditions as the actual write.
    if usage.get("speed") == "fast":
        warm_read_dollars *= _FAST_MODE_RATE_MULTIPLIER
    if usage.get("inference_geo") == "us":
        warm_read_dollars *= _INFERENCE_GEO_US_RATE_MULTIPLIER
    return write_dollars - warm_read_dollars, 0


def cmd_cache_rebuild(args: argparse.Namespace) -> None:
    """CLI entry point for the cache-rebuild subcommand.

    Root resolution happens here, at the CLI boundary, mirroring cmd_cost --
    --config-dir validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="cache-rebuild")
    _cache_rebuild_report(args, roots)


def _cache_rebuild_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Idle-gap prompt-cache TTL-expiry rebuild measurement: per-call write
    distribution, cause classification, concurrency split, and priced
    excess, broken down by account-N ordinal. Redacted by default.

    Each session's own deduped turn sequence is scanned once per
    _read_session_file_partitioned group (the main thread, then each of its
    own subagent files) to classify every threshold-crossing cache write and
    to append every parseable-timestamp call into one corpus-wide
    (timestamp, transcript) index. is_first_call/gap_seconds/model_changed
    reset at every group boundary -- a group is its own context, so a delta
    taken across a boundary would compare two unrelated conversations (see
    _read_session_file_partitioned's own docstring). Binary-searches one
    pre-sorted global (timestamp, transcript) index per idle-gap call
    instead of re-scanning per gap -- O(n log n) total, not O(gaps x calls).

    `--since` only gates whether a call is *counted*, never whether it can
    see its own prior turn (same contract as _cost_report's since_ts).

    roots is None only for this module's own tests exercising the report
    body directly; --this-repo/--config-dir CLI validation happens once in
    cmd_cache_rebuild.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "cache-rebuild: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    threshold_arg = getattr(args, "threshold", None)
    threshold: int = _CACHE_REBUILD_DEFAULT_THRESHOLD if threshold_arg is None else int(threshold_arg)
    since_ts, since_raw = _parse_since_nd_arg(args, "cache-rebuild")
    since_label = since_raw or ""

    session_iter, scope_label = _resolve_project_scope(args, "cache-rebuild", include_subagents=True, roots=roots)

    # redact_map is used only for the fingerprint below (this report prints
    # no per-row project label), but the fingerprint must hash the same
    # full-corpus label set every other --redact caller does to stay
    # cross-run comparable, and that set can differ from session_iter's own
    # (possibly --projects-narrowed) scope, so this second disk scan cannot
    # be folded into the one below.
    redact_map: dict[_RedactMapKey, str] = _build_redact_map(roots) if redact else {}
    if redact:
        print(
            f"Corpus fingerprint: {_corpus_fingerprint(redact_map)}"
            "  (private-project labels are not comparable across a different fingerprint)"
        )
    _print_resolved_scope("cache-rebuild", scope_label, scan_roots)

    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots)
    single_root_ordinal: int | None = redact_ordinals[scan_roots[0].resolve()] if not multi_root else None

    total_calls_in_scope = 0
    tail_write_sizes: list[int] = []
    cause_counts: dict[str, int] = dict.fromkeys(_CACHE_REBUILD_CAUSES, 0)
    idle_gap_candidates: list[dict] = []
    global_timeline: list[tuple[float, str]] = []
    unpriced_idle_gap_turns = 0
    unpriced_idle_gap_tokens = 0
    # Every account ordinal in scope is pre-seeded with a zero row -- a
    # valid-but-empty root, or one with no idle-gap rebuilds, still renders
    # a clean zero-state row instead of vanishing from the breakdown.
    per_account_rebuilds: dict[int, int] = dict.fromkeys(redact_ordinals.values(), 0) if multi_root else {}
    per_account_excess: dict[int, float] = dict.fromkeys(redact_ordinals.values(), 0.0) if multi_root else {}

    for jsonl, _flat_records in session_iter:
        session_key = str(jsonl.resolve())

        account_ordinal: int | None = None
        if multi_root:
            root_position = _root_index_for_path(jsonl, resolved_scan_roots)
            account_ordinal = redact_ordinals[resolved_scan_roots[root_position]]
        elif single_root_ordinal is not None:
            account_ordinal = single_root_ordinal

        # session_iter already read and parsed this file once internally (to
        # decide whether to yield it at all); this second, partitioned read
        # is the cost of reusing _resolve_project_scope's shared iterator,
        # which has no variant that also exposes the per-file group boundary
        # classification needs (mirrors read-scope's own _scan_read_scope_session
        # call site). Classification (is_first_call/gap_seconds/model_changed)
        # resets at every group boundary: each group is its own context (the
        # main thread, or one subagent's own turn sequence), so a delta taken
        # across a boundary would compare two unrelated conversations (see
        # _read_session_file_partitioned's own docstring). session_key stays
        # file-level across every group, though -- a subagent's own calls are
        # still this session's activity for the concurrency check below, not
        # another session's.
        for group in _read_session_file_partitioned(jsonl, include_subagents=True):
            group_records = _dedup_turns_by_request_id(group)

            i = 0
            prev_ts: float | None = None
            prev_model: str | None = None

            for rec in group_records:
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                model = msg.get("model", "")
                if model == "<synthetic>":
                    continue

                cur_ts = _parse_ts(rec.get("timestamp"))
                is_first_call = i == 0
                gap_seconds = None if is_first_call else _cache_rebuild_gap_seconds(prev_ts, cur_ts)
                model_changed = not is_first_call and model != prev_model
                eph_1h, eph_5m = _cache_write_split(usage)
                pure_1h_tier_write = eph_1h > 0 and eph_5m == 0
                cause = _classify_cache_rebuild_cause(is_first_call, gap_seconds, model_changed, pure_1h_tier_write)

                # Every parseable-timestamp call is corpus "activity", tail or
                # not -- the concurrency check below asks whether ANY call
                # happened during a gap, regardless of that call's own size.
                if cur_ts is not None:
                    global_timeline.append((cur_ts, session_key))

                in_scope = since_ts is None or (cur_ts is not None and cur_ts >= since_ts)
                if in_scope:
                    total_calls_in_scope += 1

                write_tokens = eph_1h + eph_5m
                in_tail = write_tokens >= threshold

                if in_tail and in_scope:
                    tail_write_sizes.append(write_tokens)
                    cause_counts[cause] += 1

                    if cause in _CACHE_REBUILD_IDLE_GAP_CAUSES:
                        excess_dollars, turn_unpriced_tokens = _cache_rebuild_excess_dollars(model, usage)
                        if excess_dollars is None:
                            unpriced_idle_gap_turns += 1
                            unpriced_idle_gap_tokens += turn_unpriced_tokens
                        else:
                            idle_gap_candidates.append({
                                "session_key": session_key,
                                "gap_start_ts": prev_ts,
                                "gap_end_ts": cur_ts,
                                "excess_dollars": excess_dollars,
                                "account_ordinal": account_ordinal,
                            })

                prev_ts = cur_ts if cur_ts is not None else prev_ts
                prev_model = model
                i += 1

    # One sort, once, over the whole corpus -- every idle-gap candidate below
    # binary-searches this same index rather than re-scanning per gap.
    global_timeline.sort(key=lambda entry: entry[0])
    global_ts = [ts for ts, _key in global_timeline]
    global_keys = [key for _ts, key in global_timeline]

    concurrent_rebuilds = 0
    concurrent_excess = 0.0
    idle_break_rebuilds = 0
    idle_break_excess = 0.0

    for cand in idle_gap_candidates:
        # bisect_right/bisect_left: an open (gap_start_ts, gap_end_ts)
        # interval, so a call from this same transcript at exactly one of
        # the gap's own endpoints -- the gap's start and end ARE this
        # transcript's own calls -- is excluded from the window. The
        # exclusion is timestamp-value-based, not (timestamp, session_key)-
        # identity-based: a genuinely different concurrent session whose own
        # call happens to land at exactly one of those same endpoints is
        # also excluded.
        lo = bisect.bisect_right(global_ts, cand["gap_start_ts"])
        hi = bisect.bisect_left(global_ts, cand["gap_end_ts"])
        # Indexed range with early exit, not global_keys[lo:hi], so a
        # concurrent-activity match short-circuits without first copying the
        # whole candidate window.
        other_active = any(global_keys[j] != cand["session_key"] for j in range(lo, hi))
        if other_active:
            concurrent_rebuilds += 1
            concurrent_excess += cand["excess_dollars"]
        else:
            idle_break_rebuilds += 1
            idle_break_excess += cand["excess_dollars"]
        if multi_root and cand["account_ordinal"] is not None:
            per_account_rebuilds[cand["account_ordinal"]] += 1
            per_account_excess[cand["account_ordinal"]] += cand["excess_dollars"]

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Cache-rebuild report ({title_since}, threshold >= {threshold:,} cache-write tokens)\n")

    total_tail_calls = sum(cause_counts.values())
    print(f"Calls scanned: {total_calls_in_scope:,}")
    print(
        f"Calls writing >= {threshold:,} tokens: {total_tail_calls:,}"
        f" ({_pct_of(total_tail_calls, total_calls_in_scope)} of calls)"
    )

    if tail_write_sizes:
        sorted_sizes = sorted(tail_write_sizes)
        median = statistics.median(sorted_sizes)
        p90 = sorted_sizes[min(len(sorted_sizes) - 1, int(0.9 * (len(sorted_sizes) - 1)))]
        print(
            f"Per-call write distribution: min={sorted_sizes[0]:,}  median={median:,.0f}"
            f"  p90={p90:,}  max={sorted_sizes[-1]:,}"
        )

    print("\n## Cause breakdown\n")
    print(f"{'Cause':<32} {'Calls':>8} {'Share':>7}")
    for cause in _CACHE_REBUILD_CAUSES:
        count = cause_counts[cause]
        print(f"{cause:<32} {count:>8,} {_pct_of(count, total_tail_calls):>7}")

    idle_gap_total = concurrent_rebuilds + idle_break_rebuilds
    idle_gap_excess = concurrent_excess + idle_break_excess
    print(
        "\n## Idle-gap concurrency split [unverified]\n\n"
        "Classifies each idle-gap rebuild by whether any other transcript, in any\n"
        "account, had a call inside the gap window. This is an association, not proof\n"
        "the operator was attending that other session. [unverified]\n"
    )
    print(f"{'':<28} {'Rebuilds':>9} {'Excess $':>12}")
    print(f"{'Another session active':<28} {concurrent_rebuilds:>9,} {concurrent_excess:>12,.2f}")
    print(f"{'Everything idle (a break)':<28} {idle_break_rebuilds:>9,} {idle_break_excess:>12,.2f}")
    print(f"{'Total idle-gap rebuilds':<28} {idle_gap_total:>9,} {idle_gap_excess:>12,.2f}")

    if multi_root:
        print("\n## Idle-gap excess by account\n")
        print(f"{'Account':<16} {'Rebuilds':>9} {'Excess $':>12}")
        for ordinal in sorted(per_account_rebuilds):
            print(f"{f'account-{ordinal}':<16} {per_account_rebuilds[ordinal]:>9,} {per_account_excess[ordinal]:>12,.2f}")

    if unpriced_idle_gap_turns:
        print(
            f"\n  ({unpriced_idle_gap_turns:,} idle-gap tail calls / {unpriced_idle_gap_tokens:,} tokens"
            " excluded from priced excess -- model has no price-table entry)"
        )


# --- cost-ledger: local per-week cost/efficiency ledger read/append -------
#
# See docs/cost-ledger.md for the schema and .claude/plans/cost-trend-ledger.md
# for the design rationale (why each column exists, what got dropped, and the
# error-path contracts implemented below).

_COST_LEDGER_COLUMNS = (
    "week", "machine", "rates", "usd", "context_pct", "opus_pct",
    "ge200k_pct", "denials", "reviewer_gap_pp", "note",
)
_COST_LEDGER_HEADER_LINE = "| " + " | ".join(_COST_LEDGER_COLUMNS) + " |"
_COST_LEDGER_SEPARATOR_LINE = "|" + "|".join(["---"] * len(_COST_LEDGER_COLUMNS)) + "|"
# Real git conflict markers are exactly these 7-character prefixes (each
# followed by a ref name on <<<<<<</>>>>>>> or nothing on =======).
_COST_LEDGER_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
_COST_LEDGER_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
# Short, lowercase-alphanumeric, no spaces or unicode -- wide enough for
# "m1"/"laptop2", narrow enough that a hostname or username can't be
# expressed in it (see docs/cost-ledger.md). \Z (not $) so a trailing
# newline doesn't slip past the anchor.
_MACHINE_LABEL_RE = re.compile(r"^[a-z0-9]{1,8}\Z")
# A note is a rendered markdown table cell (docs/cost-ledger.md, viewed on
# GitHub) and a terminal string (cost-ledger's own read mode) -- printable
# ASCII only blocks both raw control/escape bytes and non-ASCII lookalikes.
_COST_LEDGER_NOTE_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
# Local-lock convenience bound, not a protocol-grounded value -- this guards
# an interactive CLI's own read-check-write window against another local
# --record, not a network call, so no vendor timeout spec applies. 30s
# comfortably exceeds a single --record's read-check-write step (parse,
# upsert, temp-write, atomic rename) while still surfacing a wedged or
# long-running concurrent recorder within one interactive command.
_COST_LEDGER_LOCK_TIMEOUT_S = 30.0
_COST_LEDGER_LOCK_POLL_INTERVAL_S = 0.1


class _CostLedgerParseError(Exception):
    """Raised by _parse_cost_ledger_file_text on any malformed ledger content
    -- the canonical parser fails loud rather than mis-parsing a hand-edited
    or merge-conflicted row."""


def _cost_ledger_path() -> Path:
    """Return the active cost-ledger file path: $COST_LEDGER_PATH if set
    (must be absolute), else config_dir() / "cost-ledger.md"."""
    override = os.environ.get("COST_LEDGER_PATH")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"COST_LEDGER_PATH must be an absolute path, got: {override!r}")
        return path
    return config_dir() / "cost-ledger.md"


def _ledger_path_is_git_tracked(ledger_path: Path, subcommand: str = "cost-ledger") -> bool:
    """Return True iff the nearest existing ancestor of ledger_path sits
    inside a git working tree -- scopes --record's multi-root refusal to
    paths git could actually commit/push, not every ledger destination.
    Fails closed (True) on any ambiguous result: a missing git binary, a
    timeout, or a non-zero exit that isn't git's clean "not a git
    repository" signal (a bare repository, for instance, exits 0 with
    stdout "false" -- tracked by git but not a work tree, so this returns
    False for it). `subcommand` labels this function's own stderr
    diagnostics (default "cost-ledger", its original caller); pr-cost passes
    its own name so a git-tracked check failure isn't misattributed."""
    ancestor = ledger_path.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    # Explicit env, not the inherited one: a GIT_DIR/GIT_WORK_TREE exported
    # in the caller's shell would otherwise redirect this check to an
    # unrelated repo; removing (not blanking) them restores git's normal
    # discovery. LC_ALL=C pins the fatal-error text checked below to stable
    # English regardless of the operator's locale.
    env = os.environ.copy()
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    env["LC_ALL"] = "C"
    try:
        # Same local-git timeout rationale as _repo_scoped_project_slugs's
        # git calls: no network/credential work, so 10s only bounds a
        # wedged invocation.
        # encoding/errors pinned explicitly: text=True alone decodes with the
        # parent process's own locale, not LC_ALL=C above (that only governs
        # what bytes git emits) -- under a narrow-locale parent, a non-ASCII
        # ancestor path embedded in git's stderr could otherwise raise
        # UnicodeDecodeError uncaught, defeating fail-closed.
        proc = subprocess.run(
            ["git", "-C", str(ancestor), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10, check=False, env=env,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        # Not exc's str(): TimeoutExpired renders the full argv, which
        # includes `ancestor` -- a home-rooted path this module otherwise
        # never echoes to stderr.
        print(f"{subcommand}: git-tracked check timed out", file=sys.stderr)
        return True
    except OSError as exc:
        print(f"{subcommand}: git-tracked check failed ({exc})", file=sys.stderr)
        return True
    if proc.returncode == 0:
        # git only ever emits "true"/"false" here on success; anything else
        # is treated as "false" rather than validated against that literal.
        return proc.stdout.strip() == "true"
    if "not a git repository" in proc.stderr:
        return False
    print(
        f"{subcommand}: git-tracked check exited {proc.returncode} unexpectedly",
        file=sys.stderr,
    )
    return True


def _parse_cost_ledger_iso_week(week_str: str) -> None:
    """Raise _CostLedgerParseError unless week_str is a genuine ISO week
    label -- both the YYYY-Www shape and a week number the given year
    actually has (year 2025 has no W53, for instance)."""
    m = _COST_LEDGER_ISO_WEEK_RE.match(week_str)
    if not m:
        raise _CostLedgerParseError(f"malformed week label {week_str!r} (expected YYYY-Www)")
    year, week = int(m.group(1)), int(m.group(2))
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise _CostLedgerParseError(f"malformed week label {week_str!r}: {exc}") from None


def _parse_cost_ledger_pct_cell(label: str, cell: str) -> float:
    """Parse a "N.N%"-shaped percentage cell, raising _CostLedgerParseError
    on anything else (missing '%', a non-numeric value in front of it, or a
    non-finite float -- float() itself accepts "nan"/"inf"/"-infinity" with
    no error, which a percentage column must reject the same as any other
    malformed cell)."""
    if not cell.endswith("%"):
        raise _CostLedgerParseError(f"malformed {label} {cell!r} (expected a trailing '%')")
    try:
        value = float(cell[:-1])
    except ValueError:
        raise _CostLedgerParseError(f"non-numeric {label} {cell!r}") from None
    if math.isnan(value) or math.isinf(value):
        raise _CostLedgerParseError(f"non-finite {label} {cell!r}")
    return value


def _cost_ledger_note_violation(note: str) -> str | None:
    """Return a human-readable reason `note` is rejected, or None if it's
    valid. Shared between --record-time validation and the canonical row
    parser, so a hand-edited or PR-introduced row is held to the same
    contract as one written by --record: a raw '|' or newline would corrupt
    the table's row format, a non-printable-ASCII byte (e.g. an ANSI/OSC
    terminal escape sequence) would be interpolated unescaped into
    cost-ledger's terminal read-mode output, and markdown link/image syntax
    would beacon an external server on every GitHub render of
    docs/cost-ledger.md.
    """
    if "|" in note or "\n" in note or "\r" in note:
        return "must not contain '|' or a newline -- either would corrupt the table's row format"
    if not all(32 <= ord(c) <= 126 for c in note):
        return "must contain only printable ASCII -- control and terminal-escape characters are rejected"
    if _COST_LEDGER_NOTE_MARKDOWN_LINK_RE.search(note):
        return "must not contain markdown link/image syntax ('[text](url)' or '![alt](url)')"
    return None


def _parse_cost_ledger_row_cells(cells: list[str], line_no: int) -> dict:
    """Validate and coerce one already-split, already-stripped data row's
    cells into a typed row dict. Raises _CostLedgerParseError naming the
    offending line on any field that doesn't match its column's contract.
    """
    if len(cells) != len(_COST_LEDGER_COLUMNS):
        raise _CostLedgerParseError(
            f"line {line_no}: expected {len(_COST_LEDGER_COLUMNS)} columns, got {len(cells)}"
            " (a stray '|' inside a cell produces this same error)"
        )
    week, machine, rates, usd_s, context_s, opus_s, ge200k_s, denials_s, gap_s, note = cells

    note_violation = _cost_ledger_note_violation(note)
    if note_violation is not None:
        raise _CostLedgerParseError(f"line {line_no}: malformed note {note!r} ({note_violation})")

    try:
        _parse_cost_ledger_iso_week(week)
    except _CostLedgerParseError as exc:
        raise _CostLedgerParseError(f"line {line_no}: {exc}") from None

    if not _MACHINE_LABEL_RE.match(machine):
        raise _CostLedgerParseError(f"line {line_no}: malformed machine label {machine!r}")

    try:
        datetime.strptime(rates, "%Y-%m-%d")
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: malformed rates date {rates!r}") from None

    try:
        usd = float(usd_s)
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: non-numeric usd {usd_s!r}") from None
    if math.isnan(usd) or math.isinf(usd):
        raise _CostLedgerParseError(f"line {line_no}: non-finite usd {usd_s!r}")

    try:
        context_pct = _parse_cost_ledger_pct_cell("context_pct", context_s)
        opus_pct = _parse_cost_ledger_pct_cell("opus_pct", opus_s)
        ge200k_pct = _parse_cost_ledger_pct_cell("ge200k_pct", ge200k_s)
    except _CostLedgerParseError as exc:
        raise _CostLedgerParseError(f"line {line_no}: {exc}") from None

    try:
        denials = int(denials_s)
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: non-numeric denials {denials_s!r}") from None

    reviewer_gap_pp: float | None = None
    if gap_s:
        if not gap_s.endswith("pp"):
            raise _CostLedgerParseError(
                f"line {line_no}: malformed reviewer_gap_pp {gap_s!r} (expected a trailing 'pp')"
            )
        try:
            reviewer_gap_pp = float(gap_s[:-2])
        except ValueError:
            raise _CostLedgerParseError(f"line {line_no}: non-numeric reviewer_gap_pp {gap_s!r}") from None
        if math.isnan(reviewer_gap_pp) or math.isinf(reviewer_gap_pp):
            raise _CostLedgerParseError(f"line {line_no}: non-finite reviewer_gap_pp {gap_s!r}")

    return {
        "week": week, "machine": machine, "rates": rates, "usd": usd,
        "context_pct": context_pct, "opus_pct": opus_pct, "ge200k_pct": ge200k_pct,
        "denials": denials, "reviewer_gap_pp": reviewer_gap_pp, "note": note,
    }


def _parse_cost_ledger_file_text(text: str) -> tuple[str, list[dict]]:
    """Canonical parser for docs/cost-ledger.md's full content.

    Returns (preamble, rows): preamble is everything up to and including the
    table's header and separator rows, verbatim, so a re-render (preamble +
    one rendered line per row) is a byte-identical round trip for an
    already-canonical file. Fails loud (_CostLedgerParseError) on an
    unresolved git merge-conflict marker anywhere in the file, a missing or
    malformed header/separator pair, a data row not wrapped in '|...|', a
    wrong column count (a stray '|' inside a cell surfaces this same error),
    a non-ISO week label, or a non-numeric numeric/percentage cell -- never
    silently misparses a malformed row.
    """
    lines = text.splitlines()
    for marker in _COST_LEDGER_CONFLICT_MARKERS:
        for line_no, line in enumerate(lines, start=1):
            if line.startswith(marker):
                raise _CostLedgerParseError(f"line {line_no}: unresolved merge-conflict marker {marker!r}")

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == _COST_LEDGER_HEADER_LINE:
            header_idx = i
            break
    if header_idx is None:
        raise _CostLedgerParseError("could not find the cost-ledger table header row")
    if header_idx + 1 >= len(lines) or lines[header_idx + 1].strip() != _COST_LEDGER_SEPARATOR_LINE:
        raise _CostLedgerParseError("cost-ledger table header is not followed by its separator row")

    rows: list[dict] = []
    for line_no, line in enumerate(lines[header_idx + 2:], start=header_idx + 3):
        if not line.strip():
            continue
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            raise _CostLedgerParseError(f"line {line_no}: malformed table row {line!r}")
        cells = [c.strip() for c in stripped[1:-1].split("|")]
        rows.append(_parse_cost_ledger_row_cells(cells, line_no))

    preamble = "\n".join(lines[: header_idx + 2]) + "\n"
    return preamble, rows


def _format_cost_ledger_row(row: dict) -> str:
    """Render one row dict as its markdown table line -- the exact inverse
    of _parse_cost_ledger_row_cells."""
    gap = row["reviewer_gap_pp"]
    gap_s = "" if gap is None else f"{gap:+.1f}pp"
    cells = [
        row["week"], row["machine"], row["rates"],
        f"{row['usd']:.2f}", f"{row['context_pct']:.1f}%", f"{row['opus_pct']:.1f}%",
        f"{row['ge200k_pct']:.1f}%", str(row["denials"]), gap_s, row["note"],
    ]
    return "| " + " | ".join(cells) + " |"


def _upsert_cost_ledger_row(existing_rows: list[dict], new_row: dict, force: bool) -> list[dict]:
    """Insert new_row into existing_rows, keyed by (week, machine).

    Refuses (raises ValueError) when a row for that key already exists and
    force is False -- a week's numbers change as the week fills, and
    silently rewriting history is how a ledger stops being one. With force,
    replaces that row in place; every other row's order and content is
    untouched.
    """
    key = (new_row["week"], new_row["machine"])
    for i, row in enumerate(existing_rows):
        if (row["week"], row["machine"]) == key:
            if not force:
                raise ValueError(
                    f"a row for week={new_row['week']} machine={new_row['machine']} already exists"
                    " -- pass --force to overwrite it"
                )
            return [*existing_rows[:i], new_row, *existing_rows[i + 1:]]
    return [*existing_rows, new_row]


def _write_cost_ledger_file(ledger_path: Path, preamble: str, rows: list[dict]) -> None:
    """Crash-safe write: render to a temp file in the ledger's own directory,
    read it back to confirm the write landed intact and parses on the
    canonical parser, then atomically replace the ledger file -- a killed
    process leaves either the old file intact or an orphaned temp file,
    never a half-written row the next run's duplicate check could misread.

    The read-back is a byte-equality check against the text just written,
    not a round trip through row dicts: _format_cost_ledger_row rounds usd
    to cents and percentages to one decimal, so a row's raw float and its
    formatted-then-reparsed value legitimately differ -- that is expected
    precision loss, not a serialization bug, and comparing rows would
    refuse almost every real (non-round-number) row.

    The temp file is chmod'd to the existing ledger file's permission bits
    before the replace -- tempfile.mkstemp creates it 0600, and os.replace
    swaps that mode in along with the content, silently downgrading the
    ledger file's existing permissions on every --record otherwise. If
    ledger_path does not exist yet (this --record is the first ever run
    against it), the chmod is skipped and tempfile.mkstemp's own 0600
    default is left in place.
    """
    new_text = preamble + "\n".join(_format_cost_ledger_row(r) for r in rows) + ("\n" if rows else "")
    fd, tmp_name = tempfile.mkstemp(dir=str(ledger_path.parent), prefix=".cost-ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
        written_text = Path(tmp_name).read_text()
        if written_text != new_text:
            raise _CostLedgerParseError("write verification mismatch -- refusing to publish")
        _parse_cost_ledger_file_text(written_text)  # fails loud on the canonical parser before publishing
        if ledger_path.exists():
            os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))
        os.replace(tmp_name, ledger_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _reviewer_gap_pp(agg2: dict[tuple[str, str], dict[str, int]]) -> float | None:
    """Percentage-point gap between the findings-found and zero-finding
    cited-path edit rates, aggregated across every reviewer agent type --
    cost-ledger's reviewer_gap_pp column. None (left empty in the row) when
    either side's Active denominator is zero, rather than dividing by zero
    or silently substituting 0%.
    """
    findings_active = sum(
        v["active"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_FINDINGS_FOUND
    )
    findings_edited = sum(
        v["edited"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_FINDINGS_FOUND
    )
    zero_active = sum(
        v["active"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_ZERO_FINDING
    )
    zero_edited = sum(
        v["edited"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_ZERO_FINDING
    )
    if findings_active == 0 or zero_active == 0:
        return None
    return 100 * (findings_edited / findings_active - zero_edited / zero_active)


_COST_LEDGER_READ_HEADER = (
    f"{'Week':<10} {'Machine':<9} {'Rates':<11} {'$':>12} {'Context%':>9} "
    f"{'Opus%':>7} {'>=200k%':>8} {'Denials':>8} {'GapPP':>11}  Note"
)


def _format_cost_ledger_read_row(row: dict) -> str:
    """Render one row dict as a fixed-width terminal line for read mode --
    distinct from _format_cost_ledger_row, which renders the file's own
    markdown-pipe format. Empty reviewer_gap_pp prints as the literal token
    "unmeasured" (never a blank cell) so every column stays a single
    whitespace-delimited token."""
    gap = row["reviewer_gap_pp"]
    gap_s = "unmeasured" if gap is None else f"{gap:+.1f}pp"
    note = row["note"] or "-"
    return (
        f"{row['week']:<10} {row['machine']:<9} {row['rates']:<11} {row['usd']:>12,.2f} "
        f"{row['context_pct']:>8.1f}% {row['opus_pct']:>6.1f}% {row['ge200k_pct']:>7.1f}% "
        f"{row['denials']:>8} {gap_s:>11}  {note}"
    )


def _print_cost_ledger_read(existing_rows: list[dict], args: argparse.Namespace, roots: Sequence[Path]) -> None:
    """Read-mode output: every existing ledger row, then any ISO week present
    in the live corpus that no row (for any machine) has captured yet -- the
    gap between "recorded" and "still recoverable" this ledger exists to close.
    """
    session_iter, scope_label = _resolve_project_scope(args, "cost-ledger", include_subagents=True, roots=roots)
    _print_resolved_scope("cost-ledger", scope_label, roots)

    if not existing_rows:
        print("\nNo rows recorded yet.")
    else:
        print()
        print(_COST_LEDGER_READ_HEADER)
        print("-" * len(_COST_LEDGER_READ_HEADER))
        for row in existing_rows:
            print(_format_cost_ledger_read_row(row))

    cost_weeks, _unpriced_turns, _unpriced_tokens = _compute_cost_trend_data(session_iter)
    recorded_weeks = {row["week"] for row in existing_rows}
    missing_weeks = sorted(w for w in cost_weeks if w not in recorded_weeks)
    if missing_weeks:
        print("\nWeeks present in the live corpus with no ledger row yet:")
        for week_str in missing_weeks:
            print(f"  {week_str}")


def _acquire_cost_ledger_lock(lock_f) -> None:
    """Acquire an exclusive, non-blocking lock on lock_f, retrying at
    _COST_LEDGER_LOCK_POLL_INTERVAL_S intervals until
    _COST_LEDGER_LOCK_TIMEOUT_S elapses. Exits non-zero with a clear message
    rather than blocking indefinitely (fcntl.flock's plain LOCK_EX) on a
    wedged or long-running concurrent --record.
    """
    deadline = time.monotonic() + _COST_LEDGER_LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                print(
                    "cost-ledger: another cost-ledger --record appears to be running"
                    " (lock held on the ledger's own .lock sibling file -- see"
                    " _cost_ledger_path()) -- timed out after"
                    f" {_COST_LEDGER_LOCK_TIMEOUT_S:.0f}s",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(_COST_LEDGER_LOCK_POLL_INTERVAL_S)


def cmd_cost_ledger(args: argparse.Namespace) -> None:
    """CLI entry point for the cost-ledger subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_ledger_report, which takes `today` as an explicit parameter — the
    same split cmd_cost/cmd_cost_trend use so week-boundary logic is
    deterministic under test.
    """
    roots = _resolve_scan_roots(args)
    _cost_ledger_report(args, datetime.now(UTC).date(), roots)


def _default_cost_ledger_preamble() -> str:
    """Preamble for a ledger file --record creates fresh at a not-yet-existing
    path -- header/separator are byte-identical to the module constants so
    _parse_cost_ledger_file_text round-trips a freshly created file the same
    as an already-canonical one."""
    lines = [
        "# cost-ledger",
        "",
        "Weekly cost/efficiency snapshots recorded by `cost-ledger --record`.",
        _COST_LEDGER_HEADER_LINE,
        _COST_LEDGER_SEPARATOR_LINE,
    ]
    return "\n".join(lines) + "\n"


def _cost_ledger_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Read (default) or append (--record) one row of the cost ledger at
    _cost_ledger_path().

    --record's row reuses _compute_cost_trend_data for usd/context_pct/
    opus_pct/ge200k_pct, and windows _compute_deny_summary_data/
    _compute_reviewer_yield_data to the current ISO week's Monday-through-
    next-Monday UTC boundary for denials/reviewer_gap_pp — see
    docs/cost-ledger.md for why those two are per-week rather than
    corpus-lifetime figures. The corpus scan that computes the row runs
    unlocked; only the final read-check-write step (re-read the ledger,
    check for an existing (week, machine) row, write) holds an exclusive
    lock on a sibling .lock file (never the ledger file itself, so lock
    identity survives the atomic replace in _write_cost_ledger_file), so two
    racing recorders can't both pass the duplicate-row check.
    _acquire_cost_ledger_lock bounds the wait rather than blocking
    indefinitely on a wedged concurrent recorder.
    """
    record: bool = bool(getattr(args, "record", False))
    force: bool = bool(getattr(args, "force", False))
    machine_label: str | None = getattr(args, "machine_label", None) or None
    note: str = getattr(args, "note", None) or ""

    try:
        ledger_path = _cost_ledger_path()
    except ValueError as exc:
        # COST_LEDGER_PATH is new, operator-set, and easy to mistype relative
        # -- route it through this module's standard stderr+exit convention
        # rather than letting a raw traceback reach the terminal.
        print(f"cost-ledger: {exc}", file=sys.stderr)
        sys.exit(1)

    if roots is None:
        roots = _resolve_scan_roots(args)

    if not record:
        if not ledger_path.exists():
            # Omits the resolved path entirely -- it's home-rooted, and this
            # repo's own redaction convention treats home-rooted paths as
            # always-sensitive (command output here routinely gets pasted
            # into public issues).
            print(
                "cost-ledger: no ledger recorded here yet -- --record has never"
                " run against this path (or COST_LEDGER_PATH/CLAUDE_CONFIG_DIR"
                " resolves somewhere unexpected); see docs/cost-ledger.md",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            _preamble, existing_rows = _parse_cost_ledger_file_text(ledger_path.read_text())
        except _CostLedgerParseError as exc:
            print(f"cost-ledger: {exc}", file=sys.stderr)
            sys.exit(1)
        _print_cost_ledger_read(existing_rows, args, roots)
        return

    sentinel_path = config_dir() / ".cost-ledger-enabled"
    if not sentinel_path.exists():
        # Hardcodes the conventional ~/.claude path rather than sentinel_path
        # itself -- same don't-print-a-resolved-home-rooted-path discipline
        # as the ledger-file-missing message above, applied to a fixed
        # docstring instead of an f-string since CLAUDE_CONFIG_DIR overrides
        # are rare enough that the canonical hint reads clearer.
        print(
            "cost-ledger: --record requires the opt-in sentinel ~/.claude/.cost-ledger-enabled"
            " -- see docs/cost-ledger.md",
            file=sys.stderr,
        )
        sys.exit(1)

    if not machine_label:
        print("cost-ledger: --record requires --machine-label", file=sys.stderr)
        sys.exit(1)
    if not _MACHINE_LABEL_RE.match(machine_label):
        print(f"cost-ledger: --machine-label {machine_label!r} must match ^[a-z0-9]{{1,8}}$", file=sys.stderr)
        sys.exit(1)
    if len(roots) > 1 and _ledger_path_is_git_tracked(ledger_path):
        # --record writes to a single resolved ledger path; unioning multiple
        # declared accounts into that one write only risks silently
        # committing/pushing one account's figures if the write actually
        # lands somewhere git could commit it -- non-record reads still
        # return the union regardless. Doesn't echo ledger_path itself,
        # matching this function's other home-rooted-path redaction above.
        print(
            "cost-ledger: --record is refused when more than one root is in"
            " scope and the ledger path is inside a git working tree; scope"
            " to a single account (--config-dir, or a roots file declaring"
            " only this account), move COST_LEDGER_PATH outside git, or drop"
            " --record",
            file=sys.stderr,
        )
        sys.exit(2)
    # Rejection names the rule, never the compared hostname value -- echoing
    # it would persist recon-value data into the session transcript, the same
    # discipline deny-private-project-refs.sh applies to its own matches.
    # Covers the POSIX hostname only, not macOS's separate ComputerName
    # (`scutil --get ComputerName`) -- see docs/cost-ledger.md.
    if machine_label.lower() == socket.gethostname().lower():
        print(
            "cost-ledger: --machine-label must not equal this machine's hostname"
            " -- publishing a hostname risks deanonymizing this repo's corpus; choose an opaque label instead",
            file=sys.stderr,
        )
        sys.exit(1)
    note_violation = _cost_ledger_note_violation(note)
    if note_violation is not None:
        print(f"cost-ledger: --note {note_violation}", file=sys.stderr)
        sys.exit(1)

    iso = today.isocalendar()
    week_str = f"{iso.year}-W{iso.week:02d}"
    monday = date.fromisocalendar(iso.year, iso.week, 1)
    week_start_ts = datetime(monday.year, monday.month, monday.day, tzinfo=UTC).timestamp()
    week_end_ts = week_start_ts + 7 * 86400

    cost_session_iter, scope_label = _resolve_project_scope(args, "cost-ledger", include_subagents=True, roots=roots)
    deny_session_iter, _scope_label2 = _resolve_project_scope(args, "cost-ledger", roots=roots)
    reviewer_session_iter, _scope_label3 = _resolve_project_scope(args, "cost-ledger", roots=roots)
    _print_resolved_scope("cost-ledger", scope_label, roots)

    cost_weeks, _unpriced_turns, _unpriced_tokens = _compute_cost_trend_data(cost_session_iter)

    if week_str not in cost_weeks:
        print(
            f"cost-ledger: no priced turns found for the current week ({week_str});"
            " refusing to record a blank/zero row",
            file=sys.stderr,
        )
        sys.exit(1)

    max_observed_week = max(cost_weeks)
    if max_observed_week != week_str:
        print(
            f"cost-ledger: clock skew detected -- the corpus's most recent activity is dated"
            f" {max_observed_week}, but this machine's clock resolves the current week as"
            f" {week_str}; refusing to record under a possibly-wrong week label",
            file=sys.stderr,
        )
        sys.exit(1)

    week_data = cost_weeks[week_str]
    # Two distinct metrics: context_pct is the context-class (cache_read +
    # both cache_write tiers) dollar share of the week's total, GH-554 F1's
    # "context is ~88% of the bill" thesis; ge200k_pct is cost-trend's own
    # existing >=200k-context-bucket dollar share (_context_bucket) -- a
    # different corpus slice, not a second name for the same number.
    context_share = _pct_value(week_data["context_class_dollars"], week_data["total"])
    ge200k_share = _pct_value(week_data["context_over"], week_data["total"])
    opus_share = _pct_value(week_data["opus"], week_data["total"])

    deny_data = _compute_deny_summary_data(deny_session_iter, since_ts=week_start_ts, until_ts=week_end_ts)
    denials = sum(deny_data["hook_counts"].values())

    reviewer_data = _compute_reviewer_yield_data(reviewer_session_iter, since_ts=week_start_ts, until_ts=week_end_ts)
    reviewer_gap_pp = _reviewer_gap_pp(reviewer_data["agg2"])

    new_row = {
        "week": week_str,
        "machine": machine_label,
        "rates": _PRICING_FETCH_DATE.isoformat(),
        "usd": week_data["total"],
        "context_pct": context_share,
        "opus_pct": opus_share,
        "ge200k_pct": ge200k_share,
        "denials": denials,
        "reviewer_gap_pp": reviewer_gap_pp,
        "note": note,
    }

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    with open(lock_path, "w") as lock_f:
        _acquire_cost_ledger_lock(lock_f)
        try:
            try:
                preamble, existing_rows = _parse_cost_ledger_file_text(ledger_path.read_text())
            except FileNotFoundError:
                # Treated uniformly as "never recorded here" -- doesn't
                # distinguish a dangling symlink or an externally-removed
                # directory from a genuinely fresh path; both are edge cases
                # requiring external tampering, not a normal --record flow.
                preamble, existing_rows = _default_cost_ledger_preamble(), []
            except _CostLedgerParseError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)

            try:
                new_rows = _upsert_cost_ledger_row(existing_rows, new_row, force)
            except ValueError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)

            try:
                _write_cost_ledger_file(ledger_path, preamble, new_rows)
            except _CostLedgerParseError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

    print(f"cost-ledger: recorded {week_str} / {machine_label}")


# ---------------------------------------------------------------------------
# pr-cost: per-PR AI-tooling dollar cost, joined against gh PR size, rework,
# and review-surface data. See docs/pr-cost.md for the full row schema and
# design rationale. Unlike cost-ledger, this ledger's rows carry
# branch and repo values, so every stdout/stderr path routes them through
# _assign_root_scoped_redact_label before printing -- never raw.
# ---------------------------------------------------------------------------

_PR_COST_LEDGER_COLUMNS: tuple[str, ...] = (
    # Key.
    "host", "repo", "pr_number", "machine",
    # Identity / provenance.
    "head_branch", "merged_at", "rate_stamp", "captured_at",
    "join_confidence", "supersedes", "status",
    # Dollars and tokens by class, in _TOKEN_CLASSES order.
    "cache_read_usd", "cache_write_5m_usd", "cache_write_1h_usd", "output_usd", "input_usd",
    "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "output_tokens", "input_tokens",
    "unpriced_turns", "unpriced_tokens",
    "turn_count", "session_count",
    "opus_dollars", "opus_dollar_share_pct",
    "sum_context_at_turn", "mean_context_at_turn",
    # gh-sourced PR size/rework.
    "additions", "deletions", "changed_files", "commit_count", "review_comment_count",
    # Mechanical review-surface proxies -- configurable, with claude-config defaults.
    "distinct_top_level_dirs", "distinct_file_extensions",
    "tests_changed", "plan_file_added", "risk_surface_flag",
)
_PR_COST_LEDGER_HEADER_LINE = "\t".join(_PR_COST_LEDGER_COLUMNS)

# Legacy header (no "host" column): every row under it is implicitly
# _PR_COST_LEDGER_LEGACY_HOST_DEFAULT. _parse_pr_cost_ledger_file_text
# recognizes both headers and normalizes a legacy row to the current column
# shape before parsing -- see docs/pr-cost.md's backward-compat contract for
# a new key column.
if _PR_COST_LEDGER_COLUMNS[0] != "host":  # the slice below assumes this position; `assert` would
    raise RuntimeError("_PR_COST_LEDGER_COLUMNS[0] must be 'host'")  # vanish under python -O
_PR_COST_LEDGER_LEGACY_COLUMNS: tuple[str, ...] = _PR_COST_LEDGER_COLUMNS[1:]
_PR_COST_LEDGER_LEGACY_HEADER_LINE = "\t".join(_PR_COST_LEDGER_LEGACY_COLUMNS)
_PR_COST_LEDGER_LEGACY_HOST_DEFAULT = "github.com"

_PR_COST_FLOAT_COLUMNS = (
    "cache_read_usd", "cache_write_5m_usd", "cache_write_1h_usd", "output_usd", "input_usd",
    "opus_dollars", "opus_dollar_share_pct", "mean_context_at_turn",
)
# Excludes pr_number, part of the key and parsed separately alongside repo/machine.
_PR_COST_INT_COLUMNS = (
    "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "output_tokens", "input_tokens",
    "unpriced_turns", "unpriced_tokens", "turn_count", "session_count", "sum_context_at_turn",
    "additions", "deletions", "changed_files", "commit_count", "review_comment_count",
    "distinct_top_level_dirs", "distinct_file_extensions",
)
_PR_COST_BOOL_COLUMNS = ("tests_changed", "plan_file_added", "risk_surface_flag")

# status is a fixed enum carrying no embedded gh diagnostic text --
# _GH_CALL_DEGRADED_AUTH and _GH_CALL_DEGRADED_HOST_MISMATCH from
# _gh_call_with_backoff both fold into _PR_COST_STATUS_DEGRADED_NETWORK
# here, since a mid-run auth or local-misconfiguration failure and a
# generic transient one both just mean "this row's enrichment is
# incomplete," not distinguishable data states.
_PR_COST_STATUS_OK = "ok"
_PR_COST_STATUS_DEGRADED_RATE_LIMIT = "degraded_rate_limit"
_PR_COST_STATUS_DEGRADED_NETWORK = "degraded_network"
_PR_COST_STATUS_VALUES = (_PR_COST_STATUS_OK, _PR_COST_STATUS_DEGRADED_RATE_LIMIT, _PR_COST_STATUS_DEGRADED_NETWORK)

# "high": direct headRefName match corroborated by plan-slug or SHA overlap.
# "medium": direct match, uncorroborated. "low": resolved only via the
# branch-reuse tie-break (highest commit-SHA overlap, most recent
# mergedAt), or unresolved (no row written).
_PR_COST_JOIN_CONFIDENCE_HIGH = "high"
_PR_COST_JOIN_CONFIDENCE_MEDIUM = "medium"
_PR_COST_JOIN_CONFIDENCE_LOW = "low"
_PR_COST_JOIN_CONFIDENCE_VALUES = (
    _PR_COST_JOIN_CONFIDENCE_HIGH, _PR_COST_JOIN_CONFIDENCE_MEDIUM, _PR_COST_JOIN_CONFIDENCE_LOW,
)

# The gh-call-level outcomes _gh_call_with_backoff itself returns on an
# auth-shaped or local-misconfiguration-shaped failure -- never ledger
# status values; every caller folds them into _PR_COST_STATUS_DEGRADED_NETWORK
# before they reach a row (see above).
_GH_CALL_DEGRADED_AUTH = "degraded_auth"
_GH_CALL_DEGRADED_HOST_MISMATCH = "degraded_host_mismatch"

# Provisional placeholder ("As-of rule"): a merged PR's branch keeps
# accruing local transcript activity for a while after merge, so
# capturing too early understates its cost. A future measurement pass is
# expected to replace this default with a percentile of (last priced turn
# - mergedAt) across the surviving corpus; 3 days is a defensible guess
# pending that measurement, not a validated figure -- see docs/pr-cost.md.
_PR_COST_ASOF_WINDOW_DAYS_DEFAULT = 3.0

_DEFAULT_PR_COST_PLAN_FILE_GLOB = ".claude/plans/*.md"

# Provisional default risk-surface globs for claude-config itself (paths
# whose review stakes are higher than an average file change: hooks gate
# operations, install scripts run with the operator's own shell, CI
# workflows run with repo secrets, and permission rules govern what future
# agents can do). Not empirically validated against this repo's own
# incident history -- overridable via --risk-surface-glob for another repo.
_DEFAULT_PR_COST_RISK_SURFACE_GLOBS: tuple[str, ...] = (
    "claude/.claude/hooks/**",
    "claude/.claude/settings*.json",
    ".github/workflows/**",
    "install*.sh",
    "claude/.claude/rules/**",
)

# Best-effort, ecosystem-generic test-file heuristic (a tests/ path segment,
# a test_/_test.py Python name, or a .test./.spec. JS/TS suffix) -- not
# claude-config-specific, unlike the risk-surface globs above.
_PR_COST_TEST_FILE_RE = re.compile(
    r"(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$"
)

_PR_COST_GH_TIMEOUT_S = 30.0  # Operational default: gh publishes no single
# per-call timeout recommendation, so this is a considered guess generous
# enough for one REST round trip, not a network SLA citation.
_PR_COST_RATE_LIMIT_MIN_BACKOFF_S = 60.0  # GitHub REST API docs, "Rate
# limits for the REST API" -- secondary-rate-limit guidance: wait at least
# one minute between retries when no Retry-After header is present.
_PR_COST_RATE_LIMIT_MAX_ATTEMPTS = 5  # Operational default, not vendor-specified:
# GitHub's rate-limit guidance above bounds the per-retry wait, not how many
# retries to attempt before giving up on one gh call.
_PR_COST_RATE_LIMIT_MAX_ELAPSED_S = 15 * 60  # Operational default, not
# vendor-specified: a per-call ceiling generous enough to ride out one
# secondary-rate-limit window without letting a single gh call stall the run.
_PR_COST_GH_PR_LIST_LIMIT = 1000  # gh pr list's own default (30) silently
# truncates any larger population with no error. This repo's own population
# is a few hundred merged PRs; 1000 is a generous fixed ceiling, not a
# per-run population count -- --limit is a
# pagination bound, not a network timeout, so no vendor citation applies here
# the way it does to the backoff constants above.

_GIT_REMOTE_ORIGIN_TIMEOUT_S = 10  # Matches this file's other local git
# calls (_ledger_path_is_git_tracked, _repo_scoped_project_slugs): no
# network/credential work, so this only bounds a wedged invocation.
# Anchored at the start (after an optional scheme/git@ prefix) so the captured
# host is the URL's actual host, never merely a substring appearing later in
# a malicious or misconfigured remote (e.g. https://attacker.example/github.com/x/y) --
# whatever hostname it turns out to be, github.com or a GHE host alike.
_GIT_REMOTE_HOST_OWNER_REPO_RE = re.compile(
    r"^(?:https?://|git://|ssh://(?:git@)?|git@)?(?P<host>[A-Za-z0-9.-]+)[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
# The host character class above has no port syntax, so a GHE remote on a
# non-standard port (host:8443, ssh://git@host:2222/...) fails to parse and
# the run aborts rather than misrouting.
_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")

# Best-effort classification of a failed gh call's stderr text -- gh has no
# structured error-kind field on stderr, so this is pattern matching against
# gh's own documented error phrasing, not a guarantee.
_GH_AUTH_ERROR_RE = re.compile(r"not logged into|gh auth login|authentication failed|http\s?401", re.IGNORECASE)
# Matches gh's own stderr for an ambient GH_HOST that doesn't match any
# configured git remote (verified against gh 2.97.0: "none of the git
# remotes configured for this repository correspond to the GH_HOST
# environment variable") -- a local shell-config mismatch, not a transient
# failure, so it must not consume the retry budget the way a genuine
# network error does.
_GH_HOST_MISMATCH_ERROR_RE = re.compile(r"GH_HOST environment variable", re.IGNORECASE)
_GH_RATE_LIMIT_ERROR_RE = re.compile(r"rate limit|http\s?429|http\s?403", re.IGNORECASE)
_GH_RETRY_AFTER_RE = re.compile(r"retry.{0,3}after[:\s]+(\d+)", re.IGNORECASE)


class _PrCostLedgerParseError(Exception):
    """Raised by _parse_pr_cost_ledger_file_text on any malformed pr-cost
    ledger content -- the canonical parser fails loud rather than mis-parsing
    a hand-edited or corrupted row."""


def _pr_cost_ledger_path(config_dir_override: Path | None = None) -> Path:
    """Active pr-cost ledger path: $PR_COST_LEDGER_PATH if set (must be
    absolute), else (config_dir_override or config_dir()) / "pr-cost-ledger.tsv".
    config_dir_override lets --all-accounts resolve each account's own
    ledger path without reassigning the process-wide CLAUDE_CONFIG_DIR."""
    override = os.environ.get("PR_COST_LEDGER_PATH")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"PR_COST_LEDGER_PATH must be an absolute path, got: {override!r}")
        return path
    return (config_dir_override or config_dir()) / "pr-cost-ledger.tsv"


def _parse_pr_cost_ledger_row_cells(cells: list[str], line_no: int) -> dict:
    """Validate and coerce one already-split, already-tab-separated data
    row's cells into a typed row dict. Raises _PrCostLedgerParseError naming
    the offending line on any field that doesn't match its column's
    contract."""
    if len(cells) != len(_PR_COST_LEDGER_COLUMNS):
        raise _PrCostLedgerParseError(
            f"line {line_no}: expected {len(_PR_COST_LEDGER_COLUMNS)} columns, got {len(cells)}"
        )
    row = dict(zip(_PR_COST_LEDGER_COLUMNS, cells, strict=True))

    if not row["host"] or row["host"] != row["host"].lower():
        raise _PrCostLedgerParseError(
            f"line {line_no}: malformed host value (must be lowercase) -- value omitted from"
            " this diagnostic since the ledger's host column is never scrubbed at rest"
        )
    if not row["repo"] or row["repo"] != row["repo"].lower():
        raise _PrCostLedgerParseError(
            f"line {line_no}: malformed repo value (must be lowercase owner/name) -- value omitted from"
            " this diagnostic since the ledger's repo column is never scrubbed at rest"
        )
    try:
        row["pr_number"] = int(row["pr_number"])
    except ValueError:
        raise _PrCostLedgerParseError(f"line {line_no}: non-numeric pr_number {row['pr_number']!r}") from None
    if not _MACHINE_LABEL_RE.match(row["machine"]):
        raise _PrCostLedgerParseError(f"line {line_no}: malformed machine label {row['machine']!r}")
    for required_col in ("head_branch", "merged_at", "captured_at"):
        if not row[required_col]:
            raise _PrCostLedgerParseError(f"line {line_no}: {required_col} must not be empty")
    # _latest_pr_cost_row picks the "latest" row via a lexicographic string
    # max() on captured_at, which silently misresolves on a malformed ISO8601 value.
    for ts_col in ("merged_at", "captured_at"):
        try:
            datetime.fromisoformat(row[ts_col].replace("Z", "+00:00"))
        except ValueError:
            raise _PrCostLedgerParseError(f"line {line_no}: malformed {ts_col} {row[ts_col]!r}") from None
    try:
        datetime.strptime(row["rate_stamp"], "%Y-%m-%d")
    except ValueError:
        raise _PrCostLedgerParseError(f"line {line_no}: malformed rate_stamp {row['rate_stamp']!r}") from None
    if row["join_confidence"] not in _PR_COST_JOIN_CONFIDENCE_VALUES:
        raise _PrCostLedgerParseError(f"line {line_no}: unknown join_confidence {row['join_confidence']!r}")
    if row["status"] not in _PR_COST_STATUS_VALUES:
        raise _PrCostLedgerParseError(f"line {line_no}: unknown status {row['status']!r}")

    for float_col in _PR_COST_FLOAT_COLUMNS:
        try:
            row[float_col] = float(row[float_col])
        except ValueError:
            raise _PrCostLedgerParseError(f"line {line_no}: non-numeric {float_col} {row[float_col]!r}") from None
        if math.isnan(row[float_col]) or math.isinf(row[float_col]):
            raise _PrCostLedgerParseError(f"line {line_no}: non-finite {float_col} {row[float_col]!r}")

    for int_col in _PR_COST_INT_COLUMNS:
        try:
            row[int_col] = int(row[int_col])
        except ValueError:
            raise _PrCostLedgerParseError(f"line {line_no}: non-numeric {int_col} {row[int_col]!r}") from None

    for bool_col in _PR_COST_BOOL_COLUMNS:
        if row[bool_col] not in ("true", "false"):
            raise _PrCostLedgerParseError(
                f"line {line_no}: malformed {bool_col} {row[bool_col]!r} (expected true/false)"
            )
        row[bool_col] = row[bool_col] == "true"

    return row


def _parse_pr_cost_ledger_file_text(text: str) -> list[dict]:
    """Canonical parser for the pr-cost ledger's tab-separated content.

    Unlike the weekly cost-ledger's markdown table, this format has no
    preamble: line 1 must be exactly _PR_COST_LEDGER_HEADER_LINE (or the
    pre-host-column _PR_COST_LEDGER_LEGACY_HEADER_LINE, the one documented
    backward-compat exception -- see its own comment), and every following
    non-blank line is one tab-separated data row. Fails loud
    (_PrCostLedgerParseError) on an unresolved git merge-conflict marker
    (reusing _COST_LEDGER_CONFLICT_MARKERS -- a generic git marker, not
    specific to the weekly ledger's own format), a missing/mismatched
    header, or a row with the wrong column count or a malformed cell --
    never silently misparses a malformed or hand-edited row.
    """
    lines = text.splitlines()
    for marker in _COST_LEDGER_CONFLICT_MARKERS:
        for line_no, line in enumerate(lines, start=1):
            if line.startswith(marker):
                raise _PrCostLedgerParseError(f"line {line_no}: unresolved merge-conflict marker {marker!r}")

    if not lines:
        raise _PrCostLedgerParseError("missing or mismatched pr-cost ledger header row")
    if lines[0] == _PR_COST_LEDGER_HEADER_LINE:
        is_legacy_header = False
    elif lines[0] == _PR_COST_LEDGER_LEGACY_HEADER_LINE:
        is_legacy_header = True
    else:
        raise _PrCostLedgerParseError("missing or mismatched pr-cost ledger header row")

    rows: list[dict] = []
    for line_no, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if is_legacy_header:
            cells = [_PR_COST_LEDGER_LEGACY_HOST_DEFAULT, *cells]
        rows.append(_parse_pr_cost_ledger_row_cells(cells, line_no))
    return rows


def _format_pr_cost_ledger_row(row: dict) -> str:
    """Render one row dict as its tab-separated line -- the exact inverse of
    _parse_pr_cost_ledger_row_cells. Refuses (raises _PrCostLedgerParseError)
    to render any cell containing a tab or newline, which would corrupt the
    row's own column structure -- every free-text-shaped cell here is
    program-generated (a redacted placeholder, or an ISO8601 timestamp this
    module itself formatted), never raw external text, so this should never
    fire in practice; it exists as a last-resort guard against writing a
    corrupt row rather than as an expected code path."""
    cells: list[str] = []
    for col in _PR_COST_LEDGER_COLUMNS:
        value = row[col]
        if col in _PR_COST_BOOL_COLUMNS:
            cell = "true" if value else "false"
        elif col in _PR_COST_FLOAT_COLUMNS:
            cell = f"{value:.6f}"
        else:
            cell = str(value)
        if "\t" in cell or "\n" in cell or "\r" in cell:
            raise _PrCostLedgerParseError(
                f"column {col!r} value {cell!r} contains a tab or newline -- refusing to write a corrupt row"
            )
        cells.append(cell)
    return "\t".join(cells)


def _latest_pr_cost_row(
    rows: Sequence[dict], host: str, repo: str, pr_number: int, machine_label: str | None,
) -> dict | None:
    """Latest row (by captured_at) matching (host, repo, pr_number[, machine_label]).

    host and repo are compared as-is (no re-lowering here): both are
    case-folded by the caller before reaching this function (host via
    _git_remote_origin_host_and_owner_repo, repo via _resolve_pinned_gh_repo),
    and every stored row's own host/repo cells are validated lowercase by
    the parser -- the same convention _pr_cost_report's other identity
    comparisons already rely on. machine_label=None matches any machine --
    read mode's default (an operator checking "has any machine captured
    this PR yet" doesn't care which one); --record always passes its own
    resolved machine_label, matching the ledger's own (host, repo,
    pr_number, machine) key.
    """
    matches = [
        r for r in rows
        if r["host"] == host and r["repo"] == repo and r["pr_number"] == pr_number
        and (machine_label is None or r["machine"] == machine_label)
    ]
    if not matches:
        return None
    return max(matches, key=lambda r: r["captured_at"])


def _append_pr_cost_ledger_row(existing_rows: list[dict], new_row: dict, already: dict | None, force: bool) -> list[dict]:
    """Append new_row to existing_rows, keyed by (host, repo, pr_number, machine).

    Unlike the weekly ledger's in-place replace, a duplicate key with
    --force APPENDS a new row (carrying new_row["supersedes"], already set
    by the caller to the prior latest row's own captured_at) rather than
    overwriting -- this ledger is append-only by design, so a correction
    keeps the full history instead of losing everything before the latest
    edit. Refuses (raises ValueError) on a duplicate key without --force. `already`
    is the caller's own _latest_pr_cost_row lookup, passed in rather than
    re-derived here so there is one call site for that lookup per write.
    """
    if already is not None and not force:
        raise ValueError(
            f"a row for pr_number={new_row['pr_number']} machine={new_row['machine']}"
            " already exists -- pass --force with --pr to append a correcting row"
        )
    return [*existing_rows, new_row]


def _write_pr_cost_ledger_file(ledger_path: Path, rows: list[dict]) -> None:
    """Crash-safe write mirroring _write_cost_ledger_file's temp-file/
    read-back/atomic-replace pattern, adapted for this ledger's plain-TSV
    format (no markdown preamble) and its own 0600 creation mode -- these
    rows carry branch/repo data the public weekly ledger's rows don't, so a
    freshly created file gets 0600 explicitly (an existing file's mode bits
    are preserved instead, matching _write_cost_ledger_file's own rationale)
    rather than silently depending on tempfile.mkstemp's own default.
    """
    new_text = "\n".join([_PR_COST_LEDGER_HEADER_LINE] + [_format_pr_cost_ledger_row(r) for r in rows]) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(ledger_path.parent), prefix=".pr-cost-ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
        written_text = Path(tmp_name).read_text()
        if written_text != new_text:
            raise _PrCostLedgerParseError("write verification mismatch -- refusing to publish")
        _parse_pr_cost_ledger_file_text(written_text)  # fails loud on the canonical parser before publishing
        if ledger_path.exists():
            os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))
        else:
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, ledger_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _acquire_pr_cost_ledger_lock(lock_f) -> None:
    """Acquire an exclusive, non-blocking lock on lock_f, retrying at
    _COST_LEDGER_LOCK_POLL_INTERVAL_S intervals until
    _COST_LEDGER_LOCK_TIMEOUT_S elapses -- the same local-lock convenience
    bound the weekly ledger uses (_acquire_cost_ledger_lock), reused here
    since it guards the same kind of wait (a local read-check-write window
    against another --record), not a network call.
    """
    deadline = time.monotonic() + _COST_LEDGER_LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                print(
                    "pr-cost: another pr-cost --record appears to be running (lock held on the"
                    " ledger's own .lock sibling file) -- timed out after"
                    f" {_COST_LEDGER_LOCK_TIMEOUT_S:.0f}s",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(_COST_LEDGER_LOCK_POLL_INTERVAL_S)


def _git_remote_origin_host_and_owner_repo() -> tuple[str, str]:
    """Case-folded (host, owner/name) parsed from this invocation's own
    `git remote get-url origin` -- the corpus-root side of _resolve_pinned_gh_repo's
    identity comparison, run from cwd (this subcommand's own worktree) rather
    than against the ~/.claude/projects/ transcript scan root, which is never
    a git repository itself. Accepts any host (github.com, a GitHub
    Enterprise host, ...); whether gh actually holds credentials for that
    host is left to the caller and to gh itself, not decided by this parse.
    """
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=_GIT_REMOTE_ORIGIN_TIMEOUT_S, check=True,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        print("pr-cost: could not resolve this repo's own remote (git remote get-url origin failed)", file=sys.stderr)
        sys.exit(1)
    m = _GIT_REMOTE_HOST_OWNER_REPO_RE.search(proc.stdout.strip())
    if not m:
        print("pr-cost: this repo's origin remote is not a recognizable host/owner/repo URL", file=sys.stderr)
        sys.exit(1)
    return m.group("host").lower(), f"{m.group('owner')}/{m.group('repo')}".lower()


_GH_ERROR_KIND_AUTH = "auth"
_GH_ERROR_KIND_HOST_MISMATCH = "host_mismatch"
_GH_ERROR_KIND_RATE_LIMIT = "rate_limit"
_GH_ERROR_KIND_NETWORK = "network"


def _classify_gh_error(stderr: str) -> str:
    """Best-effort classification of a failed gh call's stderr text into
    one of the _GH_ERROR_KIND_* constants."""
    if _GH_AUTH_ERROR_RE.search(stderr):
        return _GH_ERROR_KIND_AUTH
    if _GH_HOST_MISMATCH_ERROR_RE.search(stderr):
        return _GH_ERROR_KIND_HOST_MISMATCH
    if _GH_RATE_LIMIT_ERROR_RE.search(stderr):
        return _GH_ERROR_KIND_RATE_LIMIT
    return _GH_ERROR_KIND_NETWORK


def _parse_gh_retry_after_seconds(stderr: str) -> float | None:
    """Seconds to wait before retrying, parsed from a "retry after N" /
    "retry-after: N" phrase in gh's own stderr text when present, else None
    (caller falls back to the exponential backoff base)."""
    m = _GH_RETRY_AFTER_RE.search(stderr)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _gh_call_with_backoff(argv: Sequence[str], *, label: str) -> tuple[subprocess.CompletedProcess | None, str]:
    """Run one gh call, retrying a rate-limit- or network-shaped failure with
    exponential backoff (starting at _PR_COST_RATE_LIMIT_MIN_BACKOFF_S,
    doubling each attempt, honoring a parsed "retry after" hint from gh's own
    stderr when present) up to _PR_COST_RATE_LIMIT_MAX_ATTEMPTS attempts or
    _PR_COST_RATE_LIMIT_MAX_ELAPSED_S total elapsed -- a budget local to this
    one call (attempt/elapsed/backoff are all function-local state), not
    shared across the run: a --record sweep over many PRs can spend up to
    that budget on each one in the worst case. An auth-shaped or
    GH_HOST-mismatch-shaped failure is never retried: gh auth status already
    ran as a preflight, and a local shell-config mismatch doesn't self-resolve
    by waiting either way.

    Returns (proc, "") on success. On exhaustion, returns (None, status)
    with status one of _GH_CALL_DEGRADED_AUTH, _GH_CALL_DEGRADED_HOST_MISMATCH,
    _PR_COST_STATUS_DEGRADED_RATE_LIMIT, _PR_COST_STATUS_DEGRADED_NETWORK --
    callers with no row yet to degrade (repo-identity resolution, discovery)
    abort the whole run on any non-empty status; per-PR enrichment instead
    marks that row's own status column (folding _GH_CALL_DEGRADED_AUTH and
    _GH_CALL_DEGRADED_HOST_MISMATCH into _PR_COST_STATUS_DEGRADED_NETWORK
    there -- see _PR_COST_STATUS_VALUES).
    """
    attempt = 0
    elapsed = 0.0
    backoff = _PR_COST_RATE_LIMIT_MIN_BACKOFF_S
    while True:
        stderr = ""
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=_PR_COST_GH_TIMEOUT_S,
                encoding="utf-8", errors="replace",
            )
        except (subprocess.TimeoutExpired, OSError):
            kind = _GH_ERROR_KIND_NETWORK
        else:
            if proc.returncode == 0:
                return proc, ""
            stderr = proc.stderr or ""
            kind = _classify_gh_error(stderr)

        attempt += 1
        if kind == _GH_ERROR_KIND_AUTH:
            print(f"pr-cost: {label} failed ({kind}), not retrying (auth failures don't self-resolve)", file=sys.stderr)
            return None, _GH_CALL_DEGRADED_AUTH
        if kind == _GH_ERROR_KIND_HOST_MISMATCH:
            # Never echoes gh's own stderr (same discipline as every other
            # print site here), but this specific failure has one fix an
            # operator can actually act on, so name it instead of falling
            # through to the generic network-failure message below.
            print(
                f"pr-cost: {label} failed ({kind}), not retrying -- gh's ambient GH_HOST"
                " environment variable does not match this repo's own git remote host;"
                " unset GH_HOST or point it at the correct host",
                file=sys.stderr,
            )
            return None, _GH_CALL_DEGRADED_HOST_MISMATCH
        if attempt >= _PR_COST_RATE_LIMIT_MAX_ATTEMPTS or elapsed >= _PR_COST_RATE_LIMIT_MAX_ELAPSED_S:
            print(f"pr-cost: {label} failed ({kind}), giving up after {attempt} attempt(s)", file=sys.stderr)
            return None, (
                _PR_COST_STATUS_DEGRADED_RATE_LIMIT if kind == _GH_ERROR_KIND_RATE_LIMIT
                else _PR_COST_STATUS_DEGRADED_NETWORK
            )
        # Capped to the remaining elapsed budget: an unbounded or malformed
        # "retry after" hint from gh's own stderr must not let one sleep
        # jump past _PR_COST_RATE_LIMIT_MAX_ELAPSED_S in a single call.
        sleep_for = min(_parse_gh_retry_after_seconds(stderr) or backoff, _PR_COST_RATE_LIMIT_MAX_ELAPSED_S - elapsed)
        print(f"pr-cost: {label} failed ({kind}), retrying in {sleep_for:g}s (attempt {attempt})...", file=sys.stderr)
        time.sleep(sleep_for)
        elapsed += sleep_for
        backoff *= 2


def _pr_cost_abort_on_gh_failure(label: str, degraded: str) -> None:
    """Print a run-abort message and exit(1) for a gh call that has no row
    yet to degrade into (repo-identity resolution, or discovery) -- only
    these two calls abort the whole run; every later per-PR `gh pr view`
    failure degrades that row's status instead (see _pr_cost_report's main
    loop). Never echoes gh's own raw stderr text (the underlying diagnostic
    gh emits, which can itself echo the queried repo verbatim) -- only this
    module's own `degraded` classification reaches stdout/stderr.
    """
    print(f"pr-cost: {label} failed ({degraded}) before any row could be captured", file=sys.stderr)
    sys.exit(1)


def _gh_auth_preflight_ok(hostname: str) -> bool:
    """A single, non-retried `gh auth status --hostname` check, run before
    anything else in this subcommand -- an auth failure caught here is
    cheaper than one surfacing mid-run after a local corpus scan and gh
    discovery call. Scoped to one host because a bare `gh auth status`
    evaluates every host it has ever held credentials for and fails
    aggregate-wide on any one of them, including hosts irrelevant to this
    run (e.g. a GHE-only token still triggers a github.com check)."""
    try:
        proc = subprocess.run(
            ["gh", "auth", "status", "--hostname", hostname], capture_output=True, text=True,
            timeout=_PR_COST_GH_TIMEOUT_S, encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _resolve_pinned_gh_repo(corpus_host: str, corpus_repo: str, ordinal: int) -> tuple[str, dict]:
    """Resolve gh's effective repo identity once, refuse (exit 2) if its
    host or owner/name (case-folded) disagrees with corpus_host/corpus_repo
    (this repo's own git remote identity, resolved by the caller), and
    return the confirmed owner/name to pin on every subsequent gh call this
    run makes -- gh's ambient target repo (a stale GH_REPO, `gh repo
    set-default`, or an ambient cwd mismatch) can otherwise silently
    diverge from the repo this invocation's own corpus and git remote
    actually belong to, including a same-named repo on a different host.
    Also returns a fresh repo-kind redact map, used to scrub this same repo
    value at every later print site this run needs. `ordinal` is the
    redact label to use for this call's own mismatch-refusal message --
    this resolution happens once per run, before any single account is
    "the" account under --all-accounts, so the caller supplies it rather
    than this function hardcoding one.
    """
    # The mismatch check below folds a gh-side parse failure into gh_host=""
    # and relies on that never coincidentally equaling corpus_host -- true
    # today only because the sole caller resolves corpus_host via
    # _git_remote_origin_host_and_owner_repo(), which itself never returns
    # an empty string. Assert it here so a future caller violating that
    # invariant fails loud instead of silently disabling the mismatch check.
    if not corpus_host or not corpus_repo:
        raise ValueError("_resolve_pinned_gh_repo requires a non-empty corpus_host and corpus_repo")
    proc, degraded = _gh_call_with_backoff(
        ["gh", "repo", "view", "--json", "nameWithOwner,url"], label="repo view"
    )
    if degraded:
        _pr_cost_abort_on_gh_failure("gh repo view", degraded)
    try:
        payload = json.loads(proc.stdout or "{}")
        gh_repo = str(payload["nameWithOwner"]).lower()
        gh_url = str(payload["url"])
    except (json.JSONDecodeError, KeyError, TypeError):
        print("pr-cost: gh repo view returned unparseable JSON -- no row was captured", file=sys.stderr)
        sys.exit(1)
    # url is the same https://host/owner/repo shape _GIT_REMOTE_HOST_OWNER_REPO_RE
    # already parses for the local git remote, so reuse it here instead of a
    # second host-parsing implementation.
    url_match = _GIT_REMOTE_HOST_OWNER_REPO_RE.search(gh_url)
    gh_host = url_match.group("host").lower() if url_match else ""

    repo_map: dict[tuple[int, str], str] = {}
    if gh_host != corpus_host or gh_repo != corpus_repo:
        print(
            "pr-cost: gh's effective target repo does not match this repo's own git remote identity"
            f" ({_assign_root_scoped_redact_label('repo', ordinal, f'{gh_host}/{gh_repo}', repo_map)} vs."
            f" {_assign_root_scoped_redact_label('repo', ordinal, f'{corpus_host}/{corpus_repo}', repo_map)}) --"
            " check GH_REPO, `gh repo set-default`, or an ambient cwd mismatch",
            file=sys.stderr,
        )
        sys.exit(2)
    return gh_repo, repo_map


def _gh_host_qualified_repo(corpus_host: str, pinned_repo: str) -> str:
    """`HOST/OWNER/REPO` form for a gh `--repo` argument -- gh's bare
    `OWNER/REPO` form resolves against whichever host the ambient `GH_HOST`
    environment variable names (api.github.com when unset), regardless of
    the invoking directory's own git remote, so every gh call this
    subcommand makes must host-qualify `--repo` to actually reach the
    intended host instead of silently querying the wrong one under the
    same owner/repo.
    """
    return f"{corpus_host}/{pinned_repo}"


def _gh_discover_merged_prs(corpus_host: str, pinned_repo: str) -> list[dict]:
    """Bulk-discover every merged PR for the pinned repo in one call, with
    an explicit --limit -- never gh's own 30-item default, which would
    silently truncate a larger population with no error. Auth/config-shaped
    failures abort the whole run immediately (no retry); rate-limit/network
    failures retry under the shared backoff budget before aborting the same
    way -- discovery has no per-row granularity to degrade into. `--repo` is
    host-qualified (see _gh_host_qualified_repo) so a GHE-pinned repo is
    queried on its own host rather than on api.github.com.
    """
    argv = [
        "gh", "pr", "list", "--repo", _gh_host_qualified_repo(corpus_host, pinned_repo), "--state", "merged",
        "--limit", str(_PR_COST_GH_PR_LIST_LIMIT),
        "--json", "number,headRefName,additions,deletions,changedFiles,mergedAt",
    ]
    proc, degraded = _gh_call_with_backoff(argv, label="pr list")
    if degraded:
        _pr_cost_abort_on_gh_failure("gh pr list", degraded)
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        print("pr-cost: gh pr list returned unparseable JSON -- no row was captured", file=sys.stderr)
        sys.exit(1)


def _gh_pr_view_enrichment(corpus_host: str, pinned_repo: str, pr_number: int) -> tuple[dict | None, str]:
    """Per-PR enrichment call: commits/reviews/files, none of which
    `gh pr list` returns. Returns (payload, _PR_COST_STATUS_OK) on success,
    else (None, degraded) with degraded one of _gh_call_with_backoff's own
    status strings -- the caller folds _GH_CALL_DEGRADED_AUTH and
    _GH_CALL_DEGRADED_HOST_MISMATCH into _PR_COST_STATUS_DEGRADED_NETWORK
    before either reaches a ledger row's status column. `--repo` is
    host-qualified (see _gh_host_qualified_repo) so a GHE-pinned repo is
    queried on its own host rather than on api.github.com.
    """
    argv = [
        "gh", "pr", "view", str(pr_number),
        "--repo", _gh_host_qualified_repo(corpus_host, pinned_repo), "--json", "commits,reviews,files",
    ]
    proc, degraded = _gh_call_with_backoff(argv, label=f"pr view {pr_number}")
    if degraded:
        return None, degraded
    try:
        return json.loads(proc.stdout or "{}"), _PR_COST_STATUS_OK
    except json.JSONDecodeError:
        return None, _PR_COST_STATUS_DEGRADED_NETWORK


def _local_git_object_exists_batch(shas: Sequence[str]) -> set[str]:
    """Which of `shas` resolve to a real local git commit object, checked
    via one `git cat-file --batch-check` call fed the whole list over
    stdin -- avoids one subprocess per SHA. Non-hex-shaped entries are
    dropped before the call: SHAs come from gh's own JSON (commits[].oid),
    and while they're git-generated (hex digits can't start with "-", so
    they're inherently safe in an option position), a malformed API response
    feeding a non-SHA line into the batch-check stdin stream could desync
    this function's own line-based output parsing below.
    """
    valid_shas = [s for s in shas if _GIT_SHA_RE.match(s)]
    if not valid_shas:
        return set()
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input="\n".join(valid_shas) + "\n",
            capture_output=True, text=True, timeout=_GIT_REMOTE_ORIGIN_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "commit":
            found.add(parts[0])
    return found


def _pr_cost_sha_overlap(commits_payload) -> int:
    """Count of a PR's pre-squash commit SHAs (gh pr view's own `commits`
    field) that still resolve to a real local git object. GitHub's squash
    merges leave these SHAs unreachable from any live ref once the source
    branch is deleted, but a commit fetched into this clone during the work
    itself can survive as a dangling object until git gc reaps it, giving
    the branch-to-PR join a corroboration signal independent of headRefName
    even after the source branch ref is gone.
    """
    shas = [c.get("oid", "") for c in (commits_payload or []) if isinstance(c, dict)]
    return len(_local_git_object_exists_batch(shas))


def _pr_cost_plan_slug_from_files(file_paths: Sequence[str], plan_glob: str) -> str | None:
    """The added plan file's slug (filename minus extension) when a PR's
    changed-file list matches plan_glob exactly once. Measured against this
    repo's own recent PR history: the overwhelming majority of in-window PRs
    add exactly one such file and none add more than one, so more than one
    match is treated as no usable slug rather than guessed.
    """
    matches = [p for p in file_paths if fnmatch.fnmatch(p, plan_glob)]
    if len(matches) != 1:
        return None
    return PurePosixPath(matches[0]).stem


def _direct_headref_matches(branch: str, merged_prs: Sequence[dict]) -> list[dict]:
    """Every merged PR whose own headRefName equals `branch` -- gh's
    headRefName is this join's authoritative signal. Normally at most one;
    more than one means a branch name was reused across two merged PRs,
    resolved by _resolve_branch_pr.
    """
    return [pr for pr in merged_prs if pr.get("headRefName") == branch]


def _pr_cost_join_corroborated(branch: str, enrichment: dict | None, plan_glob: str) -> bool:
    """True when either independent cross-check corroborates a direct
    headRefName match: the PR's own added plan-file slug equals `branch`, or
    at least one of its pre-squash commit SHAs still resolves locally
    (_pr_cost_sha_overlap). False when enrichment itself could not be
    fetched -- there is no data to corroborate with.
    """
    if enrichment is None:
        return False
    files = [f.get("path", "") for f in (enrichment.get("files") or []) if isinstance(f, dict)]
    if _pr_cost_plan_slug_from_files(files, plan_glob) == branch:
        return True
    return _pr_cost_sha_overlap(enrichment.get("commits")) > 0


def _resolve_branch_pr(
    branch: str, matches: Sequence[dict], enrichment_by_pr_number: dict[int, dict], plan_glob: str,
) -> tuple[dict | None, str]:
    """Join one branch already known to have >=1 direct headRefName match in
    `matches` (see _direct_headref_matches) to the merged PR it belongs to.
    A single match is "high" confidence when either cross-check corroborates
    it, else "medium" -- gh's headRefName is authoritative either way;
    corroboration only grades confidence. More than one match means this
    branch name was reused across two merged PRs: highest SHA overlap wins,
    ties broken by most recent mergedAt; a remaining tie returns no resolved
    PR with join_confidence "low". Rename/alias detection for a branch with
    *no* direct match at all is a separate, manual audit step, not
    automated here.
    """
    if len(matches) == 1:
        pr = matches[0]
        corroborated = _pr_cost_join_corroborated(branch, enrichment_by_pr_number.get(pr["number"]), plan_glob)
        return pr, (_PR_COST_JOIN_CONFIDENCE_HIGH if corroborated else _PR_COST_JOIN_CONFIDENCE_MEDIUM)

    def _overlap(pr: dict) -> int:
        enrichment = enrichment_by_pr_number.get(pr["number"])
        return _pr_cost_sha_overlap(enrichment.get("commits") if enrichment else None)

    best = max(matches, key=lambda pr: (_overlap(pr), pr["mergedAt"]))
    tied = [pr for pr in matches if (_overlap(pr), pr["mergedAt"]) == (_overlap(best), best["mergedAt"])]
    if len(tied) > 1:
        return None, _PR_COST_JOIN_CONFIDENCE_LOW
    return best, _PR_COST_JOIN_CONFIDENCE_LOW


def _top_level_dir(path: str) -> str:
    """First path segment of a changed-file path, or the bare filename when
    it has none (a repo-root file counts as its own single-item bucket)."""
    return path.split("/", 1)[0]


def _pr_cost_mechanical_proxies(file_paths: Sequence[str], *, plan_glob: str, risk_globs: Sequence[str]) -> dict:
    """Mechanical review-surface proxies, computed once from one PR's
    changed-file path list (gh pr view --json files)."""
    return {
        "distinct_top_level_dirs": len({_top_level_dir(p) for p in file_paths}),
        "distinct_file_extensions": len({PurePosixPath(p).suffix for p in file_paths}),
        "tests_changed": any(_PR_COST_TEST_FILE_RE.search(p) for p in file_paths),
        "plan_file_added": any(fnmatch.fnmatch(p, plan_glob) for p in file_paths),
        "risk_surface_flag": any(fnmatch.fnmatch(p, glob) for p in file_paths for glob in risk_globs),
    }


def _new_pr_cost_agg() -> dict:
    """Zero-valued per-branch aggregate shape accumulated by
    _compute_pr_cost_branch_totals, and reused as the zero-cost default for
    a merged PR whose branch carries no local corpus activity at all."""
    return {
        "dollars": dict.fromkeys(_TOKEN_CLASSES, 0.0),
        "tokens": dict.fromkeys(_TOKEN_CLASSES, 0),
        "unpriced_turns": 0, "unpriced_tokens": 0,
        "turn_count": 0, "sessions": set(), "opus_dollars": 0.0, "sum_context_at_turn": 0,
    }


def _compute_pr_cost_branch_totals(session_iter) -> tuple[dict[str, dict], dict]:
    """Single local corpus pass: every main+subagent turn's dollars/tokens,
    grouped by _attributed_branch, over the whole scan -- run exactly once
    per invocation regardless of how many PRs end up in scope. Mirrors
    _cost_report's own dedup-then-price sequence so pr-cost's numbers are
    derived the same way cost's are.

    Returns (branch_totals, unbranched_totals): branch_totals is keyed by
    each session's raw attributed branch string (never None); a record whose
    _attributed_branch resolves to None (no gitBranch anywhere in the
    session, not merely a worktree-agent carry-forward miss) accumulates
    into the single unbranched_totals aggregate instead of being dropped,
    unlike `buckets`, which silently skips records with no gitBranch.
    """
    branch_totals: dict[str, dict] = defaultdict(_new_pr_cost_agg)
    unbranched_totals: dict = _new_pr_cost_agg()

    for jsonl, records in session_iter:
        records = _dedup_turns_by_request_id(records)  # dedup before pricing (must run first, see pricing.py)
        branch_index = _session_branch_index(records)
        session_id = jsonl.stem
        for rec in records:
            if rec.get("type") != "assistant":
                continue
            usage = (rec.get("message") or {}).get("usage")
            if not usage:
                continue
            model = (rec.get("message") or {}).get("model", "")
            dollars_by_class, context_at_turn, unpriced_tokens = _price_turn(model, usage)

            branch = _attributed_branch(rec, branch_index)
            agg = branch_totals[branch] if branch is not None else unbranched_totals

            agg["turn_count"] += 1
            agg["sessions"].add(session_id)
            agg["sum_context_at_turn"] += context_at_turn

            if dollars_by_class is None:
                agg["unpriced_turns"] += 1
                agg["unpriced_tokens"] += unpriced_tokens
                continue

            token_counts = _token_counts(usage)
            for cls in _TOKEN_CLASSES:
                agg["dollars"][cls] += dollars_by_class[cls]
                agg["tokens"][cls] += token_counts[cls]
            if _fam(model) == "opus":
                agg["opus_dollars"] += sum(dollars_by_class.values())

    return dict(branch_totals), unbranched_totals


def _pr_cost_asof_window_ok(merged_at_iso: str, window_days: float, now: datetime) -> bool:
    """True once at least window_days have elapsed since merged_at_iso --
    the as-of rule's precondition."""
    merged_ts = _parse_ts(merged_at_iso)
    if merged_ts is None:
        return False
    return now.timestamp() - merged_ts >= window_days * 86400


def _new_pr_cost_row(
    *, host: str, pinned_repo: str, pr: dict, branch: str, agg: dict, enrichment: dict | None,
    join_confidence: str, status: str, machine: str, captured_at: str, supersedes: str,
    plan_glob: str, risk_globs: Sequence[str], ordinal: int, branch_map: dict,
) -> dict:
    """Assemble one ledger row dict from every piece the main loop resolved.
    head_branch is the SCRUBBED form (_assign_root_scoped_redact_label) --
    the join itself already ran on the raw `branch` value passed in here;
    this is the write boundary, the only point this run's branch value is
    allowed to reach the ledger or stdout/stderr. `host` and `repo` are
    stored raw: both are part of the row's own key and must stay stable and
    comparable across runs for the ledger to function at all (PR numbers are
    only unique per-(host, repo)) -- every *print* of `repo` still routes
    through the caller's own repo_map instead.
    """
    dollars = agg["dollars"]
    tokens = agg["tokens"]
    turn_count = agg["turn_count"]
    total_dollars = sum(dollars.values())

    files = [f.get("path", "") for f in ((enrichment or {}).get("files") or []) if isinstance(f, dict)]
    proxies = _pr_cost_mechanical_proxies(files, plan_glob=plan_glob, risk_globs=risk_globs)
    reviews = (enrichment or {}).get("reviews") or []
    commits = (enrichment or {}).get("commits") or []

    return {
        "host": host,
        "repo": pinned_repo,
        "pr_number": pr["number"],
        "machine": machine,
        "head_branch": _assign_root_scoped_redact_label("branch", ordinal, branch, branch_map),
        "merged_at": pr["mergedAt"],
        "rate_stamp": _PRICING_FETCH_DATE.isoformat(),
        "captured_at": captured_at,
        "join_confidence": join_confidence,
        "supersedes": supersedes,
        "status": status,
        "cache_read_usd": dollars["cache_read"], "cache_write_5m_usd": dollars["cache_write_5m"],
        "cache_write_1h_usd": dollars["cache_write_1h"], "output_usd": dollars["output"],
        "input_usd": dollars["input"],
        "cache_read_tokens": tokens["cache_read"], "cache_write_5m_tokens": tokens["cache_write_5m"],
        "cache_write_1h_tokens": tokens["cache_write_1h"], "output_tokens": tokens["output"],
        "input_tokens": tokens["input"],
        "unpriced_turns": agg["unpriced_turns"], "unpriced_tokens": agg["unpriced_tokens"],
        "turn_count": turn_count, "session_count": len(agg["sessions"]),
        "opus_dollars": agg["opus_dollars"], "opus_dollar_share_pct": _pct_value(agg["opus_dollars"], total_dollars),
        "sum_context_at_turn": agg["sum_context_at_turn"],
        "mean_context_at_turn": (agg["sum_context_at_turn"] / turn_count) if turn_count else 0.0,
        "additions": pr.get("additions", 0), "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changedFiles", 0),
        "commit_count": len(commits), "review_comment_count": len(reviews),
        "distinct_top_level_dirs": proxies["distinct_top_level_dirs"],
        "distinct_file_extensions": proxies["distinct_file_extensions"],
        "tests_changed": proxies["tests_changed"], "plan_file_added": proxies["plan_file_added"],
        "risk_surface_flag": proxies["risk_surface_flag"],
    }


def _print_pr_cost_ledger_rows(rows: list[dict], ordinal: int, branch_map: dict, repo_map: dict) -> None:
    """Read-mode's existing-rows preview -- scrubbed repo, no branch column
    at all (head_branch is already the scrubbed placeholder stored in the
    row, so re-scrubbing it through `branch_map` would double-redact it)."""
    if not rows:
        print("\nNo rows recorded yet.")
        return
    print()
    print(f"{'Repo':<28} {'PR':>6} {'Machine':<9} {'Status':<20} {'Join':<8} {'CapturedAt':<20}")
    for row in rows:
        repo_label = _assign_root_scoped_redact_label("repo", ordinal, row["repo"], repo_map)
        print(
            f"{repo_label:<28} {row['pr_number']:>6} {row['machine']:<9} {row['status']:<20}"
            f" {row['join_confidence']:<8} {row['captured_at']:<20}"
        )


def _print_pr_cost_uncaptured(
    branch_totals: dict[str, dict], merged_prs: Sequence[dict], existing_rows: list[dict],
    corpus_host: str, pinned_repo: str, machine_label: str | None, ordinal: int, branch_map: dict,
) -> None:
    """Read mode's gap listing: merged PRs with local corpus activity not
    yet captured in the ledger. Restricted to an unambiguous direct
    headRefName match (a branch with zero or more-than-one match is a
    separate manual audit's territory, not this quick gap check) --
    deliberately makes no extra gh calls beyond the bulk discovery this run
    already made, so read mode stays cheap enough to run often, closing the
    capture-trigger gap without needing a hook.
    """
    print("\nMerged PRs with local corpus activity not yet captured:")
    any_uncaptured = False
    for branch in sorted(branch_totals):
        matches = _direct_headref_matches(branch, merged_prs)
        if len(matches) != 1:
            continue
        pr = matches[0]
        if _latest_pr_cost_row(existing_rows, corpus_host, pinned_repo, pr["number"], machine_label) is not None:
            continue
        any_uncaptured = True
        label = _assign_root_scoped_redact_label("branch", ordinal, branch, branch_map)
        print(f"  PR #{pr['number']:<6} {label:<40} merged {pr['mergedAt']}")
    if not any_uncaptured:
        print("  (none)")


def cmd_pr_cost(args: argparse.Namespace) -> None:
    """CLI entry point for the pr-cost subcommand.

    Reads the wall-clock date/time exactly once, here, mirroring cost's and
    cost-ledger's own today-injection split so the as-of window's
    precondition check is deterministic under test.
    """
    roots = _resolve_cost_roots(args, "pr-cost")
    _pr_cost_report(args, datetime.now(UTC), roots)


def _pr_cost_report(args: argparse.Namespace, now: datetime, roots: Sequence[Path]) -> None:
    """Read (default) or capture (--record) pr-cost ledger rows, one full
    report per resolved account.

    Failure-handling order: gh auth preflight, then repo-identity resolution
    (retried under the shared rate-limit backoff, aborting the whole run on
    exhaustion since no row exists yet to mark degraded), then discovery --
    each resolved once for the whole run, since gh auth/identity and merged-PR
    discovery are account-independent (never scoped by CLAUDE_CONFIG_DIR).
    Everything else -- local corpus scan, per-branch enrichment (rate-limit/
    network failures degrade that branch's row instead of aborting), and the
    ledger read/print/write -- loops once per resolved root. Every
    stdout/stderr path below routes branch/repo values through
    _assign_root_scoped_redact_label -- no raw branch name or repo value is
    ever printed. There is deliberately no --no-redact escape hatch for this
    subcommand, unlike cost/subagents.
    """
    all_accounts: bool = bool(getattr(args, "all_accounts", False))
    if len(roots) > 1 and not all_accounts:
        # Refuses genuine multi-root ambiguity only, not a claude-config-only
        # scope requirement (this subcommand is never restricted to running
        # against claude-config itself) -- load-bearing here because pr-cost
        # durably writes, unlike a pure read command, and even read mode
        # could otherwise conflate two accounts' branch/repo data into one
        # listing.
        print(
            "pr-cost: more than one root resolved -- refusing a durable write (or a read that"
            " could conflate two accounts' branch/repo data) across accounts; pass --all-accounts"
            " to scan every declared account in one run (each account's own opt-in sentinel still"
            " gates its own write), or scope to a single profile (drop --config-dir)",
            file=sys.stderr,
        )
        sys.exit(2)
    if all_accounts and len(roots) > 1 and os.environ.get("PR_COST_LEDGER_PATH"):
        # A single forced path would commingle every account's rows into one
        # file, defeating the per-account separation the sentinel gate below
        # depends on.
        print(
            "pr-cost: PR_COST_LEDGER_PATH is refused with --all-accounts across more than one"
            " resolved root -- unset PR_COST_LEDGER_PATH (each account then defaults to its own"
            " ledger path) or drop --all-accounts",
            file=sys.stderr,
        )
        sys.exit(2)

    record: bool = bool(getattr(args, "record", False))
    force: bool = bool(getattr(args, "force", False))
    target_pr: int | None = getattr(args, "pr", None)
    machine_label: str | None = getattr(args, "machine_label", None) or None
    window_days: float = getattr(args, "asof_window_days", None) or _PR_COST_ASOF_WINDOW_DAYS_DEFAULT
    plan_glob: str = getattr(args, "plan_file_glob", None) or _DEFAULT_PR_COST_PLAN_FILE_GLOB
    risk_globs: tuple[str, ...] = tuple(
        getattr(args, "risk_surface_globs", None) or _DEFAULT_PR_COST_RISK_SURFACE_GLOBS
    )

    if force and target_pr is None:
        print("pr-cost: --force requires --pr (a correction targets exactly one PR)", file=sys.stderr)
        sys.exit(1)
    if record and not machine_label:
        print("pr-cost: --record requires --machine-label", file=sys.stderr)
        sys.exit(1)
    if machine_label is not None and not _MACHINE_LABEL_RE.match(machine_label):
        print(f"pr-cost: --machine-label {machine_label!r} must match ^[a-z0-9]{{1,8}}$", file=sys.stderr)
        sys.exit(1)
    if record and machine_label.lower() == socket.gethostname().lower():
        # Rejection names the rule, never the compared hostname -- same
        # discipline as cost-ledger's own equivalent check.
        print(
            "pr-cost: --machine-label must not equal this machine's hostname -- publishing a"
            " hostname risks deanonymizing this repo's corpus; choose an opaque label instead",
            file=sys.stderr,
        )
        sys.exit(1)

    corpus_host, corpus_repo = _git_remote_origin_host_and_owner_repo()
    if not _gh_auth_preflight_ok(corpus_host):
        print("pr-cost: gh auth status failed -- run `gh auth login` before pr-cost", file=sys.stderr)
        sys.exit(1)

    # gh auth/identity and merged-PR discovery are account-independent, so
    # they're resolved once for the whole run rather than once per account
    # below. redact_ordinals is computed first so _resolve_pinned_gh_repo's
    # own mismatch-refusal message has an ordinal to label with.
    redact_ordinals = _redaction_ordinals(roots)
    pinned_repo, repo_map = _resolve_pinned_gh_repo(corpus_host, corpus_repo, ordinal=redact_ordinals[roots[0].resolve()])
    merged_prs = _gh_discover_merged_prs(corpus_host, pinned_repo)
    branch_map: dict[tuple[int, str], str] = {}  # shared across accounts; key already includes ordinal

    recorded = skipped_no_sentinel = skipped_other = 0
    for root in roots:
        account_config_dir = root.parent
        ordinal = redact_ordinals[root.resolve()]

        session_iter, scope_label = _resolve_project_scope(
            args, "pr-cost", include_subagents=True, roots=[root]
        )
        _print_resolved_scope("pr-cost", scope_label, [root])
        branch_totals, unbranched_agg = _compute_pr_cost_branch_totals(session_iter)
        print(
            f"pr-cost: {_fmt_usd(sum(unbranched_agg['dollars'].values()))} across"
            f" {unbranched_agg['turn_count']} priced turns attributed to no branch at all"
            " (counted, not skipped, unlike `buckets`)",
            file=sys.stderr,
        )

        try:
            ledger_path = _pr_cost_ledger_path(config_dir_override=account_config_dir)
        except ValueError as exc:
            print(f"pr-cost: {exc}", file=sys.stderr)
            sys.exit(1)

        existing_rows: list[dict] = []
        if ledger_path.exists():
            try:
                existing_rows = _parse_pr_cost_ledger_file_text(ledger_path.read_text())
            except _PrCostLedgerParseError as exc:
                print(f"pr-cost: {exc}", file=sys.stderr)
                sys.exit(1)

        if not record:
            _print_pr_cost_ledger_rows(existing_rows, ordinal, branch_map, repo_map)
            _print_pr_cost_uncaptured(
                branch_totals, merged_prs, existing_rows, corpus_host, pinned_repo, machine_label, ordinal, branch_map
            )
            continue

        sentinel_path = account_config_dir / ".pr-cost-enabled"
        if not sentinel_path.exists():
            if all_accounts:
                # account-N, not sentinel_path, to avoid a resolved
                # home-rooted path in output -- same discipline as the
                # single-account refusal message below.
                print(
                    f"pr-cost: account-{ordinal} has no opt-in sentinel (.pr-cost-enabled) --"
                    " skipped, see docs/pr-cost.md",
                    file=sys.stderr,
                )
                skipped_no_sentinel += 1
                continue
            # Prints the conventional path, not sentinel_path, to avoid a
            # resolved home-rooted path in output -- same discipline as
            # cost-ledger's equivalent message above.
            print(
                "pr-cost: --record requires the opt-in sentinel ~/.claude/.pr-cost-enabled --"
                " see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        if _ledger_path_is_git_tracked(ledger_path, "pr-cost"):
            # Always refused for pr-cost (not gated on multi-root, unlike the
            # weekly ledger's own check): these rows carry branch/repo data
            # the public weekly ledger's rows don't, so this ledger must
            # never live inside a git working tree, full stop.
            if all_accounts:
                print(
                    f"pr-cost: account-{ordinal}'s ledger path is inside a git working tree --"
                    " skipped, see docs/pr-cost.md",
                    file=sys.stderr,
                )
                skipped_other += 1
                continue
            print(
                "pr-cost: --record is refused when the ledger path is inside a git working tree --"
                " move PR_COST_LEDGER_PATH outside git, or drop --record",
                file=sys.stderr,
            )
            sys.exit(2)

        if target_pr is not None:
            pr_by_number = {pr["number"]: pr for pr in merged_prs}
            target_pr_data = pr_by_number.get(target_pr)
            if target_pr_data is None:
                print(f"pr-cost: PR #{target_pr} was not found among this repo's merged PRs", file=sys.stderr)
                sys.exit(1)
            target_branches = [target_pr_data["headRefName"]]
        else:
            target_branches = sorted(branch_totals)

        account_recorded_a_row = False
        for branch in target_branches:
            branch_label = _assign_root_scoped_redact_label("branch", ordinal, branch, branch_map)
            print(f"pr-cost: resolving branch {branch_label}...", file=sys.stderr)
            matches = _direct_headref_matches(branch, merged_prs)
            if not matches:
                print("pr-cost:   no merged PR found for this branch -- skipped", file=sys.stderr)
                continue

            enrichment_by_pr_number: dict[int, dict] = {}
            degraded_status_by_pr_number: dict[int, str] = {}
            for pr in matches:
                print(f"pr-cost:   enriching PR #{pr['number']}...", file=sys.stderr)
                payload, degraded = _gh_pr_view_enrichment(corpus_host, pinned_repo, pr["number"])
                if payload is not None:
                    enrichment_by_pr_number[pr["number"]] = payload
                else:
                    degraded_status_by_pr_number[pr["number"]] = (
                        _PR_COST_STATUS_DEGRADED_NETWORK
                        if degraded in (_GH_CALL_DEGRADED_AUTH, _GH_CALL_DEGRADED_HOST_MISMATCH)
                        else degraded
                    )

            resolved_pr, join_confidence = _resolve_branch_pr(branch, matches, enrichment_by_pr_number, plan_glob)
            if resolved_pr is None:
                print(
                    "pr-cost:   ambiguous branch-to-PR match (ties unresolved after SHA-overlap"
                    " and mergedAt comparison) -- skipped",
                    file=sys.stderr,
                )
                continue

            enrichment = enrichment_by_pr_number.get(resolved_pr["number"])
            row_status = _PR_COST_STATUS_OK if enrichment is not None else degraded_status_by_pr_number.get(
                resolved_pr["number"], _PR_COST_STATUS_DEGRADED_NETWORK
            )

            if not _pr_cost_asof_window_ok(resolved_pr["mergedAt"], window_days, now):
                message = (
                    f"pr-cost:   PR #{resolved_pr['number']} merged too recently"
                    f" (as-of window is {window_days:g}d)"
                )
                if target_pr is not None and not all_accounts:
                    print(f"{message} -- refusing", file=sys.stderr)
                    sys.exit(1)
                print(f"{message} -- skipped", file=sys.stderr)
                continue

            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = ledger_path.with_name(ledger_path.name + ".lock")
            with open(lock_path, "w") as lock_f:
                _acquire_pr_cost_ledger_lock(lock_f)
                try:
                    try:
                        current_rows = _parse_pr_cost_ledger_file_text(ledger_path.read_text())
                    except FileNotFoundError:
                        current_rows = []
                    except _PrCostLedgerParseError as exc:
                        print(f"pr-cost: {exc}", file=sys.stderr)
                        sys.exit(1)

                    already = _latest_pr_cost_row(
                        current_rows, corpus_host, pinned_repo, resolved_pr["number"], machine_label
                    )
                    if already is not None and not force:
                        print(
                            f"pr-cost:   PR #{resolved_pr['number']} for machine={machine_label} is already"
                            " captured -- pass --force (with --pr) to append a correcting row",
                            file=sys.stderr,
                        )
                        if target_pr is not None and not all_accounts:
                            sys.exit(1)
                        continue

                    # A --pr target's branch comes from the shared, repo-wide
                    # merged_prs list, so under --all-accounts it can resolve
                    # here even for an account whose own local corpus never
                    # touched it; skip rather than fall through to a
                    # zero-valued-agg row (single-account --pr N still writes
                    # that row -- there is no other account to fall back to).
                    if all_accounts and target_pr is not None and branch not in branch_totals:
                        print(
                            f"pr-cost:   account-{ordinal} has no local corpus activity for this"
                            " branch -- skipped",
                            file=sys.stderr,
                        )
                        continue

                    if branch not in branch_totals and branch_totals:
                        # branch_totals non-empty but missing this exact key means the account
                        # saw local activity under some other branch name -- distinct from
                        # genuine branch-idle (branch_totals empty), which is a legitimate
                        # zero-cost case that must not warn.
                        print(
                            f"pr-cost:   PR #{resolved_pr['number']}'s branch has no matching"
                            " local corpus activity, but this account's scan attributed activity"
                            f" to {len(branch_totals)} other branch(es) -- this row may"
                            " under-report if the branch was renamed; investigate locally with"
                            " `cost --branches <branch>`",
                            file=sys.stderr,
                        )
                    agg = branch_totals.get(branch) or _new_pr_cost_agg()
                    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                    new_row = _new_pr_cost_row(
                        host=corpus_host, pinned_repo=pinned_repo, pr=resolved_pr, branch=branch, agg=agg,
                        enrichment=enrichment, join_confidence=join_confidence, status=row_status,
                        machine=machine_label, captured_at=captured_at,
                        supersedes=(already["captured_at"] if already else ""),
                        plan_glob=plan_glob, risk_globs=risk_globs, ordinal=ordinal, branch_map=branch_map,
                    )
                    try:
                        updated_rows = _append_pr_cost_ledger_row(current_rows, new_row, already, force)
                    except ValueError as exc:
                        print(f"pr-cost: {exc}", file=sys.stderr)
                        sys.exit(1)
                    try:
                        _write_pr_cost_ledger_file(ledger_path, updated_rows)
                    except _PrCostLedgerParseError as exc:
                        print(f"pr-cost: {exc}", file=sys.stderr)
                        sys.exit(1)
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)
            existing_rows = updated_rows
            account_recorded_a_row = True
            print(f"pr-cost: recorded PR #{resolved_pr['number']} / {machine_label}")

        if account_recorded_a_row:
            recorded += 1  # counts accounts that wrote a row, not total rows written
        elif all_accounts:
            # Covers every branch-loop skip reason (no PR match, ambiguous
            # match, already captured, asof-window, not-in-corpus) in one
            # place, so an account with zero recorded rows is never absent
            # from all three summary counters below.
            skipped_other += 1

    if record and all_accounts:
        print(
            f"pr-cost: recorded {recorded} of {len(roots)} declared accounts"
            f" ({skipped_no_sentinel} not opted in, {skipped_other} skipped)"
        )


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
    if not log_path.exists():
        return []
    try:
        if log_path.stat().st_size > _NUDGE_LOG_MAX_READ:
            raw = log_path.read_bytes()[-_NUDGE_LOG_MAX_READ:]
            return raw.decode(errors="ignore").splitlines()
        return log_path.read_text().splitlines()
    except OSError:
        return []


def _print_nudge_log_diagnostic() -> None:
    """Read ~/.claude/.handoff-nudge.log and report schema-drift count if present."""
    log_path = config_dir() / ".handoff-nudge.log"
    lines = _read_bounded_log_lines(log_path)
    drift_count = sum(1 for ln in lines if ln.startswith("schema-drift"))
    if drift_count:
        print(f"\nDiagnostic: {drift_count} schema-drift line(s) in {log_path}")
        print("  Schema-drift means the usage block was found but all token fields were 0 or null.")
        print("  The field paths in nudge-handoff-near-context-cap.sh may need updating.")


def _count_read_file_paths(content: list) -> int:
    """Count distinct file_path values across Read tool_use blocks in a turn's content.

    Only Read blocks are counted — Grep/Glob/Bash are intentionally excluded (conservative
    undercount). Returns 0 if there are no Read blocks.
    """
    file_paths: set[str] = set()
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "Read"
        ):
            fp = (block.get("input") or {}).get("file_path")
            if fp:
                file_paths.add(fp)
    return len(file_paths)


def _d1_bucket(file_count: int) -> str:
    """Map a Read file-path count to the D1 histogram bucket label."""
    if file_count == 0:
        return "0"
    if file_count == 1:
        return "1"
    if file_count <= 3:
        return "2-3"
    if file_count <= 7:
        return "4-7"
    return "8+"


def _d2_bucket(streak_len: int) -> str:
    """Map a code-read streak length to the D2 histogram bucket label."""
    if streak_len == 1:
        return "1"
    if streak_len == 2:
        return "2"
    if streak_len <= 5:
        return "3-5"
    if streak_len <= 10:
        return "6-10"
    return "11+"


_D1_BUCKETS: tuple[str, ...] = ("0", "1", "2-3", "4-7", "8+")
_D2_BUCKETS: tuple[str, ...] = ("1", "2", "3-5", "6-10", "11+")
_D3_CASES: tuple[str, ...] = ("inline-edit", "dispatched", "neither")

# Buckets / cases that satisfy the dispatchable criterion for the summary line
_D1_DISPATCHABLE_BUCKETS: frozenset[str] = frozenset({"2-3", "4-7", "8+"})
_D3_DISPATCHABLE_CASES: frozenset[str] = frozenset({"dispatched", "neither"})


def cmd_audit_routing_shape(args: argparse.Namespace) -> None:
    """Turn-shape distributions for Opus code-read turns: files-Read per turn (D1),
    code-read streak lengths (D2), and read-then-edit ratio (D3).

    Only code-read and code-write turns outside judgment spans are analysed. The
    judgment-span state machine is intentionally duplicated from cmd_audit_routing —
    tests cross-validate the two copies to guard against drift.
    """
    since_ts, since_raw = _parse_since_nd_arg(args, "audit-routing-shape")
    since_label = since_raw or ""

    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing-shape", roots=roots)

    # D1: file-count bucket → {turns, out}
    d1_turns: dict[str, int] = {b: 0 for b in _D1_BUCKETS}
    d1_out: dict[str, int] = {b: 0 for b in _D1_BUCKETS}

    # D2: streak-length bucket → {streak_count, out}
    d2_streaks: dict[str, int] = {b: 0 for b in _D2_BUCKETS}
    d2_out: dict[str, int] = {b: 0 for b in _D2_BUCKETS}

    # D3: case → {turns, out}
    d3_turns: dict[str, int] = {c: 0 for c in _D3_CASES}
    d3_out: dict[str, int] = {c: 0 for c in _D3_CASES}

    # D3 cross-tab: (case, d1_bucket) → {turns, out}
    d3_xtab_turns: dict[tuple[str, str], int] = {
        (case, bkt): 0 for case in _D3_CASES for bkt in _D1_BUCKETS
    }
    d3_xtab_out: dict[tuple[str, str], int] = {
        (case, bkt): 0 for case in _D3_CASES for bkt in _D1_BUCKETS
    }

    # Per-turn records collected per session. Each entry:
    #   class      — routing class string (or "user" for user-turn separators)
    #   out        — output_tokens; 0 for user-turn separators and non-qualifying Opus turns
    #   d1_bucket  — D1 file-count bucket (empty string for non-code-read turns)
    # code-read turns are qualifying entries that feed D1/D2/D3. All Opus-with-usage turns
    # plus user-turn separators are recorded so D2 streaks and D3 lookahead work correctly.
    # User-turn separators break D2 streaks but are skipped in D3's 3-turn Opus window.

    _print_resolved_scope("audit-routing-shape", scope_label, roots)

    for _jsonl, records in session_iter:
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so classification and file-count
        # counting below see every block, while the run's output tokens are
        # attributed once. Mirrors cmd_audit_routing's own dedup call.
        records = _dedup_turns_by_request_id(records)
        session_turns: list[dict] = []

        # Judgment span state machine — duplicated from cmd_audit_routing intentionally.
        in_judgment_span: bool = False
        plan_mode_active: bool = False

        for rec in records:
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype in ("user", "human"):
                in_judgment_span = False
                content_text = _content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                # User turns act as streak breakers for D2 (recorded as spacers; out=0 so
                # D3 lookahead does not count them against the 3-Opus-turn window).
                session_turns.append({"class": "user", "out": 0, "d1_bucket": ""})
                continue

            if rtype != "assistant":
                continue

            model = msg.get("model", "")
            if _fam(model) != "opus":
                # Still check for ExitPlanMode in non-Opus turns
                content = msg.get("content") or []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "ExitPlanMode"
                    ):
                        plan_mode_active = False
                continue

            usage = msg.get("usage")
            if not usage:
                continue

            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            content = msg.get("content") or []
            out_tokens: int = usage.get("output_tokens", 0)

            # Open judgment span before classification (same logic as cmd_audit_routing)
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                    and (block.get("input") or {}).get("skill") in AUDIT_JUDGMENT_SKILLS
                ):
                    in_judgment_span = True
                    break

            turn_class = _classify_opus_turn(content, in_judgment_span, plan_mode_active)

            # ExitPlanMode clears plan-mode for next turn
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                ):
                    plan_mode_active = False
                    break

            # code-read turns outside judgment spans qualify for D1/D2/D3 distributions.
            # code-write turns outside judgment spans are recorded so the D3 inline-edit
            # case can be detected in the lookahead window.
            # All other Opus-with-usage turns are recorded as spacers so D3 lookahead
            # correctly counts them against the 3-turn window.
            if turn_class == "code-read":
                file_count = _count_read_file_paths(content)
                bucket = _d1_bucket(file_count)
                session_turns.append({
                    "class": "code-read",
                    "out": out_tokens,
                    "d1_bucket": bucket,
                })
                d1_turns[bucket] += 1
                d1_out[bucket] += out_tokens
            else:
                session_turns.append({
                    "class": turn_class,
                    "out": out_tokens,
                    "d1_bucket": "",
                })

        # --- D2: streak analysis within this session ---
        # A streak is a maximal consecutive run of code-read turns (no non-code-read
        # Opus turn in between, per the recorded session_turns sequence).
        current_streak_len: int = 0
        current_streak_out: int = 0
        for turn in session_turns:
            if turn["class"] == "code-read":
                current_streak_len += 1
                current_streak_out += turn["out"]
            else:
                if current_streak_len > 0:
                    bkt = _d2_bucket(current_streak_len)
                    d2_streaks[bkt] += 1
                    d2_out[bkt] += current_streak_out
                current_streak_len = 0
                current_streak_out = 0
        # Flush trailing streak
        if current_streak_len > 0:
            bkt = _d2_bucket(current_streak_len)
            d2_streaks[bkt] += 1
            d2_out[bkt] += current_streak_out

        # --- D3: read-then-edit lookahead within this session ---
        # For each code-read turn, look ahead up to 3 Opus turns with usage.
        # User-turn separator entries (class="user", out=0) are skipped in the
        # lookahead count — they are not Opus turns with usage.
        for idx, turn in enumerate(session_turns):
            if turn["class"] != "code-read":
                continue
            lookahead_count = 0
            d3_case = "neither"
            for j in range(idx + 1, len(session_turns)):
                next_turn = session_turns[j]
                if next_turn["class"] == "user":
                    # Not an Opus turn with usage — skip without consuming the 3-turn budget.
                    # A user turn between a code-read and a code-write does not weaken the
                    # causal link; only Opus turns count against the lookahead window.
                    continue
                lookahead_count += 1
                if lookahead_count > 3:
                    break
                if next_turn["class"] == "code-write":
                    d3_case = "inline-edit"
                    break
                if next_turn["class"] == "orchestration":
                    d3_case = "dispatched"
                    break

            d3_turns[d3_case] += 1
            d3_out[d3_case] += turn["out"]
            d3_xtab_turns[(d3_case, turn["d1_bucket"])] += 1
            d3_xtab_out[(d3_case, turn["d1_bucket"])] += turn["out"]

    # --- Dispatchable share summary ---
    # A code-read turn is dispatchable via D1 (file-count bucket 2-3, 4-7, or 8+) or
    # D3 (case dispatched or neither). Their union is computed via the D3 cross-tab.
    # D2 streak data is shown separately above as a complementary view.
    total_code_read_out = sum(d1_out.values())
    d1_dispatch_out = sum(d1_out[b] for b in _D1_DISPATCHABLE_BUCKETS)
    d3_dispatch_out = sum(d3_out[c] for c in _D3_DISPATCHABLE_CASES)
    # Intersection of D1 and D3 dispatchable (via cross-tab)
    d1_and_d3_dispatch_out = sum(
        d3_xtab_out[(c, b)]
        for c in _D3_DISPATCHABLE_CASES
        for b in _D1_DISPATCHABLE_BUCKETS
    )
    union_dispatch_out = d1_dispatch_out + d3_dispatch_out - d1_and_d3_dispatch_out
    dispatch_pct = f"{100 * union_dispatch_out / total_code_read_out:.0f}%" if total_code_read_out else "—"

    # --- Emit output ---
    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Opus code-read turn-shape distributions ({title_since})\n")

    # D1
    print("### D1 — Files Read per turn (code-read turns, outside judgment spans)\n")
    d1_header = f"{'Bucket':<8} {'Turns':>8} {'Output tokens':>15}"
    print(d1_header)
    print("─" * len(d1_header))
    for bkt in _D1_BUCKETS:
        print(f"{bkt:<8} {d1_turns[bkt]:>8,} {d1_out[bkt]:>15,}")

    # D2
    print("\n### D2 — Code-read streak length\n")
    d2_header = f"{'Bucket':<8} {'Streaks':>8} {'Output tokens':>15}"
    print(d2_header)
    print("─" * len(d2_header))
    for bkt in _D2_BUCKETS:
        print(f"{bkt:<8} {d2_streaks[bkt]:>8,} {d2_out[bkt]:>15,}")

    # D3
    print("\n### D3 — Read-then-edit ratio (lookahead up to 3 Opus turns)\n")
    d3_header = f"{'Case':<14} {'Turns':>8} {'Output tokens':>15}"
    print(d3_header)
    print("─" * len(d3_header))
    for case in _D3_CASES:
        print(f"{case:<14} {d3_turns[case]:>8,} {d3_out[case]:>15,}")

    print("\n#### D3 × D1 cross-tab\n")
    d3x_header = f"{'Case':<14} {'D1 bucket':<10} {'Turns':>8} {'Output tokens':>15}"
    print(d3x_header)
    print("─" * len(d3x_header))
    for case in _D3_CASES:
        for bkt in _D1_BUCKETS:
            turns_val = d3_xtab_turns[(case, bkt)]
            out_val = d3_xtab_out[(case, bkt)]
            if turns_val > 0:
                print(f"{case:<14} {bkt:<10} {turns_val:>8,} {out_val:>15,}")

    print(
        f"\nDispatchable share: {dispatch_pct} of code-read output tokens"
        " (D1≥2 OR D3-neither/dispatched; D2-streak≥3 shown separately above)"
    )


def cmd_audit_routing_samples(args: argparse.Namespace) -> None:
    """Emit a random sample of Opus code-read turns with prior-user context and next-turn
    lookahead classification, as a JSON array to stdout.

    Each element provides a verbatim prior user message, the first tool_use block of the
    code-read turn, and the kind of action taken in the next non-user Opus turn. Designed
    for manual curation of which turns should/should not have been delegated.

    The judgment-span state machine is intentionally duplicated from cmd_audit_routing —
    tests cross-validate the two copies to guard against drift.
    """
    since_ts, since_raw = _parse_since_nd_arg(args, "audit-routing-samples")

    sample_n: int = getattr(args, "sample", 30) or 30
    seed: int | None = getattr(args, "seed", None)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing-samples", roots=roots)
    # stderr, not stdout: stdout is this subcommand's JSON/markdown data stream.
    _print_resolved_scope("audit-routing-samples", scope_label, roots, file=sys.stderr)

    candidates: list[dict] = []

    for jsonl, records in session_iter:
        session_id = jsonl.stem
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so classification below sees every
        # block (e.g. the first tool_use block promised by this function's
        # own docstring may land on a later raw record). Mirrors
        # cmd_audit_routing's own dedup call.
        records = _dedup_turns_by_request_id(records)

        # Build per-session records list with kind classification.
        # Judgment span state machine — duplicated from cmd_audit_routing intentionally.
        in_judgment_span: bool = False
        plan_mode_active: bool = False

        session_records: list[dict] = []

        for rec in records:
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype in ("user", "human"):
                in_judgment_span = False
                content_text = _content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                user_text = _content_text(msg.get("content", ""))
                session_records.append({
                    "kind": "user",
                    "content": [],
                    "user_text": user_text,
                })
                continue

            if rtype != "assistant":
                continue

            model = msg.get("model", "")
            content = msg.get("content") or []

            if _fam(model) != "opus":
                # Still update span state from non-Opus assistant turns (ExitPlanMode)
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "ExitPlanMode"
                    ):
                        plan_mode_active = False
                continue

            usage = msg.get("usage")
            if not usage:
                continue

            # Open judgment span before classification (same logic as cmd_audit_routing)
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                    and (block.get("input") or {}).get("skill") in AUDIT_JUDGMENT_SKILLS
                ):
                    in_judgment_span = True
                    break

            turn_class = _classify_opus_turn(content, in_judgment_span, plan_mode_active)

            # ExitPlanMode clears plan-mode for next turn
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                ):
                    plan_mode_active = False
                    break

            session_records.append({
                "kind": turn_class,
                "content": content,
                "user_text": "",
                "rec_ts": rec.get("timestamp"),
            })

        # Scan for code-read entries and collect sample candidates.
        for turn_idx, turn in enumerate(session_records):
            if turn["kind"] != "code-read":
                continue

            # Apply --since filter using record timestamp
            if since_ts is not None:
                rec_ts = _parse_ts(turn.get("rec_ts"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            # prior_user_message: walk backward to find the nearest user turn.
            prior_user_message = ""
            for i in range(turn_idx - 1, -1, -1):
                if session_records[i]["kind"] == "user":
                    prior_user_message = session_records[i]["user_text"]
                    break

            # assistant_tool_call: first tool_use block in this turn's content.
            assistant_tool_call: dict = {"name": "", "input": {}}
            for block in turn["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    assistant_tool_call = {
                        "name": block.get("name", ""),
                        "input": block.get("input") or {},
                    }
                    break

            # next_assistant_action and next_turn_excerpt: first non-user entry after this turn.
            next_assistant_action = "other"
            next_turn_excerpt = ""
            for j in range(turn_idx + 1, len(session_records)):
                next_entry = session_records[j]
                if next_entry["kind"] == "user":
                    continue
                # Classify the next non-user entry.
                if next_entry["kind"] == "code-write":
                    next_assistant_action = "edit"
                elif next_entry["kind"] == "orchestration":
                    next_assistant_action = "dispatch"
                elif next_entry["kind"] == "code-read":
                    next_assistant_action = "another-read"
                elif next_entry["kind"] == "other":
                    # "respond-to-user" if text-only (no tool_use blocks)
                    has_tool_use = any(
                        isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in next_entry["content"]
                    )
                    next_assistant_action = "other" if has_tool_use else "respond-to-user"
                else:
                    next_assistant_action = "other"
                next_turn_text = _content_text(next_entry["content"])
                next_turn_excerpt = next_turn_text[:200]
                break

            candidates.append({
                "session_id": session_id,
                "turn_index": turn_idx,
                "prior_user_message": prior_user_message,
                "assistant_tool_call": assistant_tool_call,
                "next_assistant_action": next_assistant_action,
                "next_turn_excerpt": next_turn_excerpt,
                "recent_assistant_text": _recent_assistant_text(session_records, turn_idx, _RECENT_LOOKBACK_N),
                "recent_tool_trail": _recent_tool_trail(session_records, turn_idx, _RECENT_LOOKBACK_N),
            })

    # Apply reproducible sampling.
    rng = random.Random(seed)
    rng.shuffle(candidates)
    candidates = candidates[:sample_n]

    output_format: str = getattr(args, "output_format", "json") or "json"
    if output_format == "md":
        print(_format_samples_as_markdown(
            candidates,
            since_raw=since_raw,
            sample_n=sample_n,
            seed=seed,
        ))
    else:
        print(json.dumps(candidates, indent=2))


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


# Shell operators that chain multiple invocations into one Bash command --
# splitting on these keeps a mutating git call from hiding in a later segment
# of e.g. "cd worktree && git commit -m wip".
_TURN_SHAPE_SHELL_OPERATOR_TOKENS: frozenset[str] = frozenset({"&&", "||", ";", "|"})


def _split_command_tokens_on_shell_operators(tokens: list[str]) -> list[list[str]]:
    """Split a shlex-tokenized command into segments at &&, ||, ;, and | operators.

    A quoted operator (e.g. a commit message containing "&&") survives as
    part of its enclosing token from shlex.split and is never treated as a
    separator here, since it can't equal one of these bare operator tokens.
    """
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _TURN_SHAPE_SHELL_OPERATOR_TOKENS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


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
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return any(
        _command_segment_is_mutating_git(segment)
        for segment in _split_command_tokens_on_shell_operators(tokens)
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
        text = _content_text(msg.get("content", "")).lower()
        if any(phrase in text for phrase in STRUGGLE_PHRASES):
            count += 1
    return count


def _friction_signals(records: list[dict]) -> dict[str, int]:
    """Return the per-signal friction breakdown plus the all-1-weighted composite."""
    denials = _friction_denial_events(records)
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
# (claude/.claude/skills/handoff/SKILL.md, "After writing: record the
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
    - nudged: session, est (int), model, window (int), event, and "action"
      (only present when the line carries it -- a hard-block fire logs
      action=block, an advisory fire logs no action field at all)
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
    location" table: session=/est=/model=/window=/event= only), so the join
    key is session_id plus a first-crossing rule: the fire turn is the
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

    log_entries = _parse_nudge_log_entries(config_dir() / ".handoff-nudge.log")
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


def _add_project_scope_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared --projects/--this-repo scope flags to a subparser.

    Mutually exclusive: --this-repo routes through _repo_scoped_project_slugs(),
    an identity-based minimization control; --projects keeps the pre-existing
    machine-wide glob default ("*") so no existing invocation's behavior changes.
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--projects", default="*", metavar="GLOB")
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
    p_user_input.add_argument("--projects", default="*", metavar="GLOB")
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
            " subdirectory, or it is rejected. Refused together with --this-repo. Branch names are"
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
            " subdirectory, or it is rejected. Refused together with --this-repo or --per-session."
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
        "--redact", action="store_true",
        help=(
            "No-op: reviewer-yield's output is aggregate-only per agent type and"
            " carries no project-label or session-id field to redact. Kept for CLI"
            " parity with cost/audit-routing."
        ),
    )
    p_reviewer_yield.set_defaults(func=cmd_reviewer_yield)

    p_pr = sub.add_parser("pr-link", help="Map branches to GitHub PRs and pull per-PR comment counts. Requires gh.")
    p_pr.add_argument("--repo", required=True, metavar="OWNER/REPO")
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
        "--projects", default=None, metavar="GLOB",
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
        help="Append the current ISO week's row. Requires ~/.claude/.cost-ledger-enabled and --machine-label.",
    )
    p_cost_ledger.add_argument(
        "--machine-label", metavar="LABEL",
        help="Opaque per-machine label for --record: ^[a-z0-9]{1,8}$, must not equal this machine's hostname.",
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
        help="Capture ledger rows for eligible merged PRs. Requires ~/.claude/.pr-cost-enabled and --machine-label.",
    )
    p_pr_cost.add_argument(
        "--pr", type=int, metavar="N",
        help="Target exactly one PR number instead of every branch with local corpus activity.",
    )
    p_pr_cost.add_argument(
        "--machine-label", metavar="LABEL",
        help=(
            "Opaque per-machine label for --record: ^[a-z0-9]{1,8}$, must not equal this"
            " machine's hostname. Also narrows read mode's uncaptured-PR listing to one machine."
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
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_rearm_backtest.add_argument(
        "--spacings", metavar="N1,N2,...", default="40000,80000,120000",
        help="Comma-separated candidate re-arm spacings in tokens past the first fire (default: 40000,80000,120000).",
    )
    p_rearm_backtest.set_defaults(func=cmd_rearm_backtest)

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
        # These subcommands resolve their own scan roots via their own
        # --config-dir (_resolve_cost_roots), never reading the reassignment
        # below -- refuse outright rather than let the two same-named flags
        # silently diverge (this top-level one would validate one account
        # while the subcommand scans another).
        if parsed.subcommand in _SUBCOMMANDS_WITH_OWN_CONFIG_DIR:
            print(
                f"{parsed.subcommand}: the top-level --config-dir has no effect here, since "
                f"this subcommand resolves its own scan roots via its own --config-dir "
                f"(repeatable, additive) -- use that instead: "
                f"transcript-analysis.py {parsed.subcommand} --config-dir PATH",
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
