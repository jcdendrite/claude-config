#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
Every subcommand is a local read except the few that call gh and the few that
write a file; docs/transcript-analysis.md lists both sets.
"""

import argparse
import sys
from pathlib import Path

from transcript_analysis import scope
from transcript_analysis.audit_routing import (
    # All three names below are read bare by this file's own still-monolithic build_parser
    # (each audit-routing subcommand's own set_defaults).
    cmd_audit_routing,
    cmd_audit_routing_samples,
    cmd_audit_routing_shape,
)
from transcript_analysis.author_outcome import _AUTHORING_AGENT_CODE_WRITER, cmd_author_outcome
from transcript_analysis.buckets import (
    # Read bare by this file's own build_parser (the buckets subparser's registration).
    _add_buckets_subparser,
)
from transcript_analysis.cache_efficiency import (
    # Read bare by this file's own build_parser (the cache-efficiency subparser's registration).
    _add_cache_efficiency_subparser,
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
    # Read bare by this file's own build_parser (the commit-gate subparser's registration).
    _add_commit_gate_subparser,
)
from transcript_analysis.context_composition import (
    # Read bare by this file's own build_parser (the context-composition subparser's registration).
    _add_context_composition_subparser,
)
from transcript_analysis.context_distribution import (
    # Read bare by this file's own build_parser (the context-distribution subparser's registration).
    _add_context_distribution_subparser,
)
from transcript_analysis.cost import (
    cmd_cost,
    cmd_cost_trend,
)
from transcript_analysis.cost_ledger import (
    # Read bare by this file's own still-monolithic build_parser (the cost-ledger
    # subcommand's own set_defaults).
    cmd_cost_ledger,
)
from transcript_analysis.duration import (
    # Read bare by this file's own build_parser (the duration subparser's registration).
    _add_duration_subparser,
)
from transcript_analysis.edit_format import (
    # Read bare by this file's own build_parser (the edit-format subparser's registration).
    _add_edit_format_subparser,
)
from transcript_analysis.fail_seq import (
    # Read bare by this file's own build_parser (the fail-seq subparser's registration).
    _add_fail_seq_subparser,
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
from transcript_analysis.instrument_authoring import (
    # Read bare by this file's own build_parser (the instrument-authoring subparser's registration).
    _add_instrument_authoring_subparser,
)
from transcript_analysis.judgment_pair import (
    # Read bare by this file's own build_parser (the judgment-pair subcommand's registration).
    _add_judgment_pair_subparser,
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
    # Read bare by this file's own build_parser (the pr-link subparser's registration).
    _add_pr_link_subparser,
)
from transcript_analysis.read_scope import (
    # Read bare by this file's own build_parser (the read-scope subcommand's own set_defaults).
    cmd_read_scope,
)
from transcript_analysis.rearm_backtest import (
    # Read bare by this file's own build_parser (the rearm-backtest
    # subcommand's own set_defaults).
    cmd_rearm_backtest,
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
    _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR,
    _add_project_scope_args,
    _iso_date,
)
from transcript_analysis.sessions import (
    # Read bare by this file's own build_parser (the sessions subparser's registration).
    _add_sessions_subparser,
)
from transcript_analysis.skill_invocation import (
    # Read bare by this file's own build_parser (the skill-invocation subcommand's registration).
    _add_skill_invocation_subparser,
)
from transcript_analysis.skill_pair import (
    # Read bare by this file's own build_parser (the skill-pair subcommand's registration).
    _add_skill_pair_subparser,
)
from transcript_analysis.spend_over_threshold import (
    # Read bare by this file's own build_parser (the spend-over-threshold
    # subcommand's own set_defaults).
    cmd_spend_over_threshold,
)
from transcript_analysis.struggle import (
    # Read bare by this file's own build_parser (the struggle subparser's registration).
    _add_struggle_subparser,
)
from transcript_analysis.subagent_mix import (
    # Both names below are read bare by this file's own still-monolithic build_parser
    # (the subagent-mix and cost-counts subcommands' own set_defaults).
    cmd_cost_counts,
    cmd_subagent_mix,
)
from transcript_analysis.subagents import (
    # Read bare by this file's own build_parser (the subagents subcommand's own set_defaults).
    cmd_subagents,
)
from transcript_analysis.turn_shape import (
    # Read bare by this file's own build_parser (each turn-shape subcommand's registration).
    _add_turn_shape_holdout_samples_subparser,
    _add_turn_shape_samples_subparser,
    _add_turn_shape_subparser,
)
from transcript_analysis.user_input import (
    # Read bare by this file's own build_parser (the user-input subparser's registration).
    _add_user_input_subparser,
)
from transcript_analysis.workstream_cost import (
    # Read bare by this file's own still-monolithic build_parser (the workstream-cost
    # subcommand's own set_defaults).
    cmd_workstream_cost,
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

    _add_context_distribution_subparser(sub)

    _add_edit_format_subparser(sub)

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

    _add_instrument_authoring_subparser(sub)

    _add_context_composition_subparser(sub)

    _add_cache_efficiency_subparser(sub)

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
        # their own scan roots via their own --config-dir (scope._resolve_cost_roots);
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
        # Every other subcommand's scan roots funnel through scope.resolve_scan_roots,
        # which reads parsed.config_dir directly for its override branch rather
        # than through this reassignment -- so this can no longer diverge from
        # a subcommand's actual scan roots the way it could before.
        scope.PROJECTS_DIR = Path(parsed.config_dir) / "projects"
    parsed.func(parsed)


if __name__ == "__main__":
    main()
