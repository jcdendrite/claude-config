"""transcript-analysis.py's decomposed modules -- the leaf modules (corpus,
scope, redaction, pricing, render, cache_rebuild_rules), the command-group
modules (cost, cost_ledger, reviewer_yield, review_rounds, author_outcome,
denials, review_trace, read_scope, pr_cost, pr_cost_export, cache_rebuild,
audit_routing, workstream_cost, subagents, subagent_mix, rearm_backtest,
spend_over_threshold), and the shared ledger/gh-access/handoff-nudge modules
(ledger_common, gh_cli, pr_cost_ledger, handoff_nudge) (see
docs/transcript-analysis-architecture.md).

transcript-analysis.py itself stays a plain top-level script at its own path
(not part of this package) and imports these modules; see that file's own
top-of-file comment for the import discipline reassignable globals require.
"""
