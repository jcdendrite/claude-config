#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
Every subcommand is a local read except the few that call gh and the few that
write a file; docs/transcript-analysis.md lists both sets.
"""

import argparse
import json
import math
import os  # noqa: F401 -- read only via _mod.os from test files, as a stdlib passthrough
import re
import sys
import tempfile  # noqa: F401 -- test_transcript_ledger_common.py reads _mod.tempfile as a stdlib passthrough
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

# cache_rebuild/cache_rebuild_rules/corpus/cost/cost_ledger/gh_cli/handoff_nudge/
# handoff_signal_response/ledger_common/period_cost/plan_boundary/pr_cost/pr_cost_export/
# pr_cost_ledger/pricing/redaction/render/subagent_mix are read only via _mod.<module> from
# test files (unit-testing a private helper, or patching module-owned state). scope is the
# one module this file's own code reads bare, as scope.PROJECTS_DIR.
# A module dropped from this list (e.g. audit_routing, denials) has no `_mod`
# reader left. Reintroducing a shim-loading test file for it without restoring its
# binding here breaks that file's collection.
from transcript_analysis import (  # noqa: F401
    cache_rebuild,
    cache_rebuild_rules,
    corpus,
    cost,
    cost_ledger,
    gh_cli,
    handoff_nudge,
    handoff_signal_response,
    ledger_common,
    period_cost,
    plan_boundary,
    pr_cost,
    pr_cost_export,
    pr_cost_ledger,
    pricing,
    redaction,
    render,
    scope,
    subagent_mix,
)
from transcript_analysis.audit_routing import (
    # All three names below are read bare by this file's own still-monolithic build_parser
    # (each audit-routing subcommand's own set_defaults).
    cmd_audit_routing,
    cmd_audit_routing_samples,
    cmd_audit_routing_shape,
)
from transcript_analysis.author_outcome import _AUTHORING_AGENT_CODE_WRITER, cmd_author_outcome
from transcript_analysis.buckets import (
    # _add_buckets_subparser is read bare by this file's own build_parser (the buckets
    # subparser's registration). cmd_buckets is read only via _mod.cmd_buckets from test files.
    _add_buckets_subparser,
    cmd_buckets,  # noqa: F401
)
from transcript_analysis.cache_rebuild import (
    # Both names below are read bare by this file's own still-monolithic code:
    #   _CACHE_REBUILD_DEFAULT_SINCE/_CACHE_REBUILD_DEFAULT_THRESHOLD -> build_parser's own
    #     --since/--threshold defaults
    #   cmd_cache_rebuild                                             -> p_cache_rebuild.set_defaults
    _CACHE_REBUILD_DEFAULT_SINCE,
    _CACHE_REBUILD_DEFAULT_THRESHOLD,
    cmd_cache_rebuild,
)
from transcript_analysis.commit_gate import (
    # _add_commit_gate_subparser is read bare by this file's own build_parser (the commit-gate
    # subparser's registration). cmd_commit_gate is read only via _mod.cmd_commit_gate from test files.
    _add_commit_gate_subparser,
    cmd_commit_gate,  # noqa: F401
)
from transcript_analysis.corpus import (
    SUBAGENT_SUBDIR,  # noqa: F401 -- read only via _mod.SUBAGENT_SUBDIR from test files
    _parse_ts,
    _read_session_file_partitioned,
    iter_sessions,  # noqa: F401 -- read only via _mod.iter_sessions from test files
)
from transcript_analysis.cost import (
    # The seven noqa'd names below are read only via _mod.<name> from test files (unit-testing
    # a private helper directly, or a monkeypatch retarget).
    # cmd_cost and cmd_cost_trend are the two names this file's own code calls bare -- via
    # p_cost/p_cost_trend.set_defaults below.
    _accumulate_per_account_turn,  # noqa: F401
    _cost_report,  # noqa: F401
    _cost_trend_report,  # noqa: F401
    _print_branch_exclusion_diagnostic,  # noqa: F401
    _print_model_id_table,  # noqa: F401
    _print_thread_table,  # noqa: F401
    _print_token_class_table,  # noqa: F401
    cmd_cost,
    cmd_cost_trend,
)
from transcript_analysis.cost_ledger import (
    # Read bare by this file's own still-monolithic build_parser (the cost-ledger
    # subcommand's own set_defaults).
    cmd_cost_ledger,
)
from transcript_analysis.duration import (
    # _add_duration_subparser is read bare by this file's own build_parser (the duration
    # subparser's registration). cmd_duration is read only via _mod.cmd_duration from test files.
    _add_duration_subparser,
    cmd_duration,  # noqa: F401
)
from transcript_analysis.fail_seq import (
    # _add_fail_seq_subparser is read bare by this file's own build_parser (the fail-seq
    # subparser's registration). cmd_fail_seq is read only via _mod.cmd_fail_seq from test files.
    _add_fail_seq_subparser,
    cmd_fail_seq,  # noqa: F401
)
from transcript_analysis.friction_count import (
    # Read bare by this file's own build_parser (the friction-count subcommand's registration).
    _add_friction_count_subparser,
)
from transcript_analysis.handoff_signal_response import (
    # Read bare by this file's own build_parser (the handoff-signal-response
    # subcommand's own set_defaults).
    cmd_handoff_signal_response,
)
from transcript_analysis.judgment_pair import (
    # _add_judgment_pair_subparser is read bare by this file's own build_parser (the
    # judgment-pair subcommand's registration). cmd_judgment_pair is read only via
    # _mod.cmd_judgment_pair from test files.
    _add_judgment_pair_subparser,
    cmd_judgment_pair,  # noqa: F401
)
from transcript_analysis.period_cost import (
    # Read bare by this file's own build_parser (the period-cost
    # subcommand's own set_defaults).
    cmd_period_cost,
)
from transcript_analysis.plan_boundary import (
    # Read bare by this file's own build_parser (the plan-boundary
    # subcommand's own set_defaults).
    cmd_plan_boundary,
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
from transcript_analysis.pr_link import (
    # _add_pr_link_subparser is read bare by this file's own build_parser (the pr-link
    # subparser's registration). cmd_pr_link is read only via _mod.cmd_pr_link from test files.
    _add_pr_link_subparser,
    cmd_pr_link,  # noqa: F401
)
from transcript_analysis.pricing import (
    _CACHE_READ_MULTIPLIER,
    _CACHE_WRITE_1H_MULTIPLIER,
    _CACHE_WRITE_5M_MULTIPLIER,
    _CONTEXT_DISTRIBUTION_THRESHOLD_ABS,
    _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS,
    _FAST_MODE_RATE_MULTIPLIER,
    _INFERENCE_GEO_US_RATE_MULTIPLIER,
    _SPAWN_TOOL_NAMES,
    _TOKEN_CLASSES,  # noqa: F401 -- read only via _mod._TOKEN_CLASSES from test files
    _cache_miss_reason,  # noqa: F401 -- read only via _mod._cache_miss_reason from test files
    _cache_write_split,
    _context_at_turn,
    _context_bucket,  # noqa: F401 -- read only via _mod._context_bucket from test files
    _context_window_for_model,
    _count_subagent_spawns,
    _model_rates,  # noqa: F401 -- read only via _mod._model_rates from test files
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
from transcript_analysis.rearm_backtest import (
    # Read bare by this file's own build_parser (the rearm-backtest
    # subcommand's own set_defaults).
    cmd_rearm_backtest,
)
from transcript_analysis.redaction import (
    _BUILT_IN_AGENT_TYPES,  # noqa: F401 -- read only via _mod._BUILT_IN_AGENT_TYPES from test files
    _REDACT_MAP_MISS_TOKEN,  # noqa: F401 -- read only via _mod._REDACT_MAP_MISS_TOKEN from test files
    _build_redact_map,  # noqa: F401 -- read only via _mod._build_redact_map from test files
    _corpus_fingerprint,  # noqa: F401 -- read only via _mod._corpus_fingerprint from test files
    _project_family,  # noqa: F401 -- read only via _mod._project_family from test files
    _redact_proj_label,  # noqa: F401 -- read only via _mod._redact_proj_label from test files
    _redact_session_id,  # noqa: F401 -- read only via _mod._redact_session_id from test files
    _repo_tracked_agent_type_names,  # noqa: F401 -- read only via _mod._repo_tracked_agent_type_names from test files
)
from transcript_analysis.render import (
    _content_text,
    _context_distribution_rows,
    _fam,  # noqa: F401 -- read only via _mod._fam from test files
    _fmt_date,  # noqa: F401 -- read only via _mod._fmt_date from test files
    _is_fresh_user_prompt,  # noqa: F401 -- read only via _mod._is_fresh_user_prompt from test files
    _pct_of,
    _sanitize_table_cell,  # noqa: F401 -- read only via _mod._sanitize_table_cell from test files
    _strip_task_notifications,  # noqa: F401 -- read only via _mod._strip_task_notifications from test files
)
from transcript_analysis.review_rounds import (
    # REVIEW_SKILLS is read bare by build_parser's review-round-cost --skill choices.
    REVIEW_SKILLS,
    cmd_review_round_cost,
)
from transcript_analysis.review_trace import (
    # Both names below are read bare by this file's own still-monolithic
    # build_parser (the review-trace subcommand's set_defaults).
    REVIEW_TRACE_SKILLS,
    cmd_review_trace,
)
from transcript_analysis.reviewer_yield import (
    # Read bare by this file's own still-monolithic code, via
    # p_reviewer_yield.set_defaults.
    cmd_reviewer_yield,
)
from transcript_analysis.scope import (
    _DO_NOT_PUBLISH_BANNER,
    _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR,
    _add_project_scope_args,
    _iso_date,
    _iter_scoped_sessions,  # noqa: F401 -- read only via _mod._iter_scoped_sessions from test files
    _parse_absolute_window_args,  # noqa: F401 -- read only via _mod._parse_absolute_window_args from test files
    _parse_since_nd_arg,
    _projects_glob,
    _redaction_ordinals,
    _repo_scoped_project_slugs,  # noqa: F401 -- read only via _mod._repo_scoped_project_slugs from test files
    _resolve_cost_roots,
    _resolve_project_scope,
    _resolved_scope_header,  # noqa: F401 -- read only via _mod._resolved_scope_header from test files
    _root_index_for_path,
    _scan_root_transcripts,
)
from transcript_analysis.scope import print_resolved_scope as _print_resolved_scope

# _resolve_scan_roots is read only via _mod._resolve_scan_roots from test files.
from transcript_analysis.scope import resolve_scan_roots as _resolve_scan_roots  # noqa: F401
from transcript_analysis.sessions import (
    # _add_sessions_subparser is read bare by this file's own build_parser (the sessions
    # subparser's registration). cmd_sessions is read only via _mod.cmd_sessions from test files.
    _add_sessions_subparser,
    cmd_sessions,  # noqa: F401
)
from transcript_analysis.skill_invocation import (
    # _add_skill_invocation_subparser is read bare by this file's own build_parser (the
    # skill-invocation subcommand's registration). cmd_skill_invocation is read only via
    # _mod.cmd_skill_invocation from test files.
    _add_skill_invocation_subparser,
    cmd_skill_invocation,  # noqa: F401
)
from transcript_analysis.skill_pair import (
    # _add_skill_pair_subparser is read bare by this file's own build_parser (the
    # skill-pair subcommand's registration). cmd_skill_pair is read only via
    # _mod.cmd_skill_pair from test files.
    _add_skill_pair_subparser,
    cmd_skill_pair,  # noqa: F401
)
from transcript_analysis.spend_over_threshold import (
    # Read bare by this file's own build_parser (the spend-over-threshold
    # subcommand's own set_defaults).
    cmd_spend_over_threshold,
)
from transcript_analysis.struggle import (
    # _add_struggle_subparser is read bare by this file's own build_parser (the struggle
    # subparser's registration). cmd_struggle is read only via _mod.cmd_struggle from test files.
    _add_struggle_subparser,
    cmd_struggle,  # noqa: F401
)
from transcript_analysis.subagent_mix import (
    # Both names below are read bare by this file's own still-monolithic build_parser
    # (the subagent-mix and cost-counts subcommands' own set_defaults).
    cmd_cost_counts,
    cmd_subagent_mix,
)
from transcript_analysis.subagents import (
    # Both names below are read bare by this file's own still-monolithic code:
    #   _MCP_TOOL_BUCKET_LABEL -> _normalize_composition_tool_name
    #   cmd_subagents          -> p_sub.set_defaults
    _MCP_TOOL_BUCKET_LABEL,
    cmd_subagents,
)
from transcript_analysis.turn_shape import (
    # Read bare by this file's own build_parser (each turn-shape subcommand's registration).
    _add_turn_shape_holdout_samples_subparser,
    _add_turn_shape_samples_subparser,
    _add_turn_shape_subparser,
)
from transcript_analysis.user_input import (
    # _add_user_input_subparser is read bare by this file's own build_parser (the user-input
    # subparser's registration). cmd_user_input is read only via _mod.cmd_user_input from test files.
    _add_user_input_subparser,
    cmd_user_input,  # noqa: F401
)
from transcript_analysis.workstream_cost import (
    # Read bare by this file's own still-monolithic build_parser (the workstream-cost
    # subcommand's own set_defaults).
    cmd_workstream_cost,
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

    _add_buckets_subparser(sub)
    _add_fail_seq_subparser(sub)
    _add_struggle_subparser(sub)
    _add_user_input_subparser(sub)
    _add_duration_subparser(sub)

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

    _add_pr_link_subparser(sub)
    _add_skill_pair_subparser(sub)
    _add_commit_gate_subparser(sub)
    _add_skill_invocation_subparser(sub)

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

    _add_judgment_pair_subparser(sub)

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
        help=(
            "Append the current ISO week's row. Requires the cost_ledger_recording config key"
            " to resolve true (set via ./install.sh or claude-config.toml; legacy-file fallback: docs/config-file.md)."
        ),
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
            " resolves. Each account's own pr_cost_recording config key still individually"
            " gates whether that account's row is recorded -- see docs/pr-cost.md."
        ),
    )
    p_pr_cost.add_argument(
        "--record", action="store_true",
        help=(
            "Capture ledger rows for eligible merged PRs. Requires the pr_cost_recording config key"
            " to resolve true (set via ./install.sh or claude-config.toml; legacy-file fallback: docs/config-file.md)."
        ),
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
            " raw counts printed directly, no per-branch rows). Refuses every scope-narrowing flag; see"
            " docs/private-project-redaction.md."
        ),
    )
    p_review_round_cost.add_argument(
        "--show-withheld", action="store_true",
        help=(
            "With --pooled: print the figures the dominance-precision floor would"
            " otherwise withhold, plus the two data-quality-gap lines, under a DO NOT"
            " PUBLISH banner. Never publish this output; cite a plain --pooled run"
            " instead."
        ),
    )
    p_review_round_cost.set_defaults(func=cmd_review_round_cost)

    p_author_outcome = sub.add_parser(
        "author-outcome",
        help=(
            "For each --agent-typed dispatch (default code-writer), what share of its own diffs"
            " drew a must-fix (ADDRESS) finding on downstream code-review. Reads the transcript for"
            " round/dispatch structure and the review-narrative-ledger rows each session wrote for"
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

    p_period_cost = sub.add_parser(
        "period-cost",
        help=(
            "Per-PR and per-review Claude Code spend for an inclusive UTC date range, grouped by repository, as"
            " Slack mrkdwn. The scope is fixed to the active account's whole projects directory, for an account"
            " that works for one project owner; no scope flags. Calls gh. --sample prints a synthetic report."
        ),
    )
    p_period_cost.add_argument(
        "--since", metavar="DATE", type=_iso_date, help="Inclusive start date, zero-padded YYYY-MM-DD; required unless --sample",
    )
    p_period_cost.add_argument(
        "--until", metavar="DATE", type=_iso_date, help="Inclusive end date, zero-padded YYYY-MM-DD; required unless --sample",
    )
    p_period_cost.add_argument(
        "--sample", action="store_true",
        help="Print the synthetic sample report; reads no transcript and runs no git or gh. Refuses --since and --until.",
    )
    p_period_cost.set_defaults(func=cmd_period_cost)

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

    _add_turn_shape_subparser(sub)
    _add_turn_shape_samples_subparser(sub)
    _add_turn_shape_holdout_samples_subparser(sub)
    _add_sessions_subparser(sub)

    _add_friction_count_subparser(sub)

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
        # cost-counts and period-cost are the two exceptions, each registering no
        # --config-dir flag of its own at all (see scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR).
        if parsed.subcommand in _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR:
            # hasattr, not a hardcoded subcommand list: True only when the
            # invoked subparser itself registered --config-dir (dest
            # extra_config_dirs), so the hint never recommends a flag that
            # doesn't exist on cost-counts's or period-cost's own parser.
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
