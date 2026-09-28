"""transcript-analysis.py's decomposed modules -- the leaf modules (corpus,
scope, redaction, pricing, render, cache_rebuild_rules), the command-group
modules (cost, reviewer_yield, review_rounds, author_outcome, denials,
review_trace, read_scope, pr_cost, pr_cost_export, cache_rebuild, audit_routing),
and the shared ledger/gh-access modules (ledger_common, gh_cli, pr_cost_ledger)
(see docs/transcript-analysis-architecture.md).

transcript-analysis.py itself stays a plain top-level script at its own path
(not part of this package) and imports these modules; see that file's own
top-of-file comment for the import discipline reassignable globals require.
"""
