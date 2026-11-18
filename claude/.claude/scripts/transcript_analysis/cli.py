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
from transcript_analysis.cost import _add_cost_subparser, _add_cost_trend_subparser
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
from transcript_analysis.period_cost import _add_period_cost_subparser
from transcript_analysis.plan_boundary import _add_plan_boundary_subparser
from transcript_analysis.pr_cost import _add_pr_cost_subparser
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
from transcript_analysis.review_rounds import _add_review_round_cost_subparser
from transcript_analysis.review_trace import _add_review_trace_subparser
from transcript_analysis.reviewer_yield import _add_reviewer_yield_subparser
from transcript_analysis.routing_guard import (
    _add_routing_guard_subparser,
)
from transcript_analysis.scope import _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR
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
    _add_cost_subparser(sub)
    _add_context_distribution_subparser(sub)
    _add_edit_format_subparser(sub)
    _add_read_scope_subparser(sub)
    _add_read_cost_subparser(sub)
    _add_instrument_authoring_subparser(sub)
    _add_context_composition_subparser(sub)
    _add_cache_efficiency_subparser(sub)
    _add_cost_trend_subparser(sub)
    _add_cache_rebuild_subparser(sub)
    _add_cost_ledger_subparser(sub)
    _add_pr_cost_subparser(sub)
    _add_pr_cost_export_subparser(sub)
    _add_spend_over_threshold_subparser(sub)
    _add_workstream_cost_subparser(sub)
    _add_review_round_cost_subparser(sub)
    _add_review_cost_by_size_subparser(sub)
    _add_author_outcome_subparser(sub)
    _add_cost_counts_subparser(sub)
    _add_period_cost_subparser(sub)
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
