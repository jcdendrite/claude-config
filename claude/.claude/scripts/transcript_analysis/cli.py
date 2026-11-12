"""build_parser() and main() for transcript-analysis.py, which re-exports both."""

import argparse
import sys
from pathlib import Path

# Only this file's build_parser() and main() read the names imported below.
from transcript_analysis import scope
from transcript_analysis.audit_routing import (
    _add_audit_routing_samples_subparser,
    _add_audit_routing_shape_subparser,
    _add_audit_routing_subparser,
)
from transcript_analysis.author_outcome import _add_author_outcome_subparser
from transcript_analysis.buckets import (
    _add_buckets_subparser,
)
from transcript_analysis.cache_efficiency import (
    _add_cache_efficiency_subparser,
)
from transcript_analysis.cache_rebuild import _add_cache_rebuild_subparser
from transcript_analysis.commit_gate import (
    _add_commit_gate_subparser,
)
from transcript_analysis.context_composition import (
    _add_context_composition_subparser,
)
from transcript_analysis.context_distribution import (
    _add_context_distribution_subparser,
)
from transcript_analysis.cost import (
    cmd_cost,
    cmd_cost_trend,
)
from transcript_analysis.cost_ledger import _add_cost_ledger_subparser
from transcript_analysis.duration import (
    _add_duration_subparser,
)
from transcript_analysis.edit_format import (
    _add_edit_format_subparser,
)
from transcript_analysis.fail_seq import (
    _add_fail_seq_subparser,
)
from transcript_analysis.friction_count import (
    _add_friction_count_subparser,
)
from transcript_analysis.handoff_signal_response import _add_handoff_signal_response_subparser
from transcript_analysis.instrument_authoring import (
    _add_instrument_authoring_subparser,
)
from transcript_analysis.judgment_pair import (
    _add_judgment_pair_subparser,
)
from transcript_analysis.period_cost import (
    cmd_period_cost,
)
from transcript_analysis.plan_boundary import _add_plan_boundary_subparser
from transcript_analysis.pr_cost import (
    _DEFAULT_PR_COST_PLAN_FILE_GLOB,
    _PR_COST_ASOF_WINDOW_DAYS_DEFAULT,
    cmd_pr_cost,
)
from transcript_analysis.pr_cost_export import _add_pr_cost_export_subparser
from transcript_analysis.pr_link import (
    _add_pr_link_subparser,
)
from transcript_analysis.read_cost import (
    _add_read_cost_subparser,
)
from transcript_analysis.read_scope import _add_read_scope_subparser
from transcript_analysis.rearm_backtest import _add_rearm_backtest_subparser
from transcript_analysis.review_cost_by_size import (
    _add_review_cost_by_size_subparser,
)
from transcript_analysis.review_rounds import (
    REVIEW_SKILLS,
    cmd_review_round_cost,
)
from transcript_analysis.review_trace import _add_review_trace_subparser
from transcript_analysis.reviewer_yield import _add_reviewer_yield_subparser
from transcript_analysis.routing_guard import (
    _add_routing_guard_subparser,
)
from transcript_analysis.scope import (
    _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR,
    _add_project_scope_args,
    _iso_date,
)
from transcript_analysis.sessions import (
    _add_sessions_subparser,
)
from transcript_analysis.skill_invocation import (
    _add_skill_invocation_subparser,
)
from transcript_analysis.skill_pair import (
    _add_skill_pair_subparser,
)
from transcript_analysis.spend_over_threshold import _add_spend_over_threshold_subparser
from transcript_analysis.struggle import (
    _add_struggle_subparser,
)
from transcript_analysis.subagent_mix import _add_cost_counts_subparser, _add_subagent_mix_subparser
from transcript_analysis.subagents import _add_subagents_subparser
from transcript_analysis.turn_shape import (
    _add_turn_shape_holdout_samples_subparser,
    _add_turn_shape_samples_subparser,
    _add_turn_shape_subparser,
)
from transcript_analysis.user_input import (
    _add_user_input_subparser,
)
from transcript_analysis.workstream_cost import _add_workstream_cost_subparser


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
    _add_subagents_subparser(sub)
    _add_subagent_mix_subparser(sub)
    _add_reviewer_yield_subparser(sub)
    _add_pr_link_subparser(sub)
    _add_skill_pair_subparser(sub)
    _add_commit_gate_subparser(sub)
    _add_skill_invocation_subparser(sub)
    _add_review_trace_subparser(sub)
    _add_judgment_pair_subparser(sub)
    _add_audit_routing_subparser(sub)

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
    _add_read_scope_subparser(sub)
    _add_read_cost_subparser(sub)
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

    _add_cache_rebuild_subparser(sub)
    _add_cost_ledger_subparser(sub)

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

    _add_pr_cost_export_subparser(sub)
    _add_spend_over_threshold_subparser(sub)
    _add_workstream_cost_subparser(sub)

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

    _add_review_cost_by_size_subparser(sub)
    _add_author_outcome_subparser(sub)
    _add_cost_counts_subparser(sub)

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

    _add_rearm_backtest_subparser(sub)
    _add_handoff_signal_response_subparser(sub)
    _add_plan_boundary_subparser(sub)
    _add_audit_routing_shape_subparser(sub)
    _add_audit_routing_samples_subparser(sub)
    _add_turn_shape_subparser(sub)
    _add_turn_shape_samples_subparser(sub)
    _add_turn_shape_holdout_samples_subparser(sub)
    _add_sessions_subparser(sub)
    _add_friction_count_subparser(sub)
    _add_routing_guard_subparser(sub)

    return parser


def main() -> None:
    parser = build_parser()
    parsed = parser.parse_args()
    if parsed.config_dir:
        # These subcommands each refuse the top-level --config-dir outright
        # rather than let it silently diverge from whatever scan roots the
        # subcommand actually resolves (this top-level one would validate
        # one account while the subcommand scans another). Most resolve
        # their own scan roots via their own --config-dir (scope._resolve_cost_roots).
        # The comment above scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR
        # names the members that register no --config-dir flag of their own.
        if parsed.subcommand in _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR:
            # hasattr, not a hardcoded subcommand list: True only when the
            # invoked subparser itself registered --config-dir (dest
            # extra_config_dirs), so the hint never recommends a flag that
            # doesn't exist on the invoked subcommand's own parser.
            if hasattr(parsed, "extra_config_dirs"):
                own_scope_clause = (
                    "this subcommand resolves its own scan roots via its own --config-dir "
                    "(repeatable, additive) -- use that instead: "
                    f"transcript-analysis.py {parsed.subcommand} --config-dir PATH"
                )
            else:
                own_scope_clause = (
                    "this subcommand resolves its scan roots itself"
                )
            print(
                f"{parsed.subcommand}: the top-level --config-dir has no effect here, since "
                f"{own_scope_clause}",
                file=sys.stderr,
            )
            sys.exit(2)
        # Every other subcommand's scan roots funnel through scope.resolve_scan_roots,
        # which reads parsed.config_dir directly for its override branch rather
        # than through this reassignment, so this cannot diverge from a
        # subcommand's actual scan roots.
        scope.PROJECTS_DIR = Path(parsed.config_dir) / "projects"
    parsed.func(parsed)
