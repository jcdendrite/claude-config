# Reports

Point-in-time empirical audits of this repo and the workflow it ships. Each
report is dated, pinned to the commit it was derived from, and left unedited
afterwards — a report records what was true when it ran, so it is a historical
record rather than living documentation.

**The one permitted edit** is a dated `## Status` section near the top, recording
which findings have since been closed and by which commit. Findings themselves
are never revised: a reader deciding whether to act on one needs to know it is
already fixed, but rewriting the finding would destroy the record of what was
true at the baseline. An instruction aimed at a future actor — a sequencing
constraint, a "do X before Y" — may additionally be marked superseded in place,
since leaving it unmarked reads as a live obligation.

This makes reports distinct from two neighbours:

- **`docs/case-studies/`** — narrative accounts of a single incident or model
  behaviour, indexed in `docs/case-studies.md`.
- **The rest of `docs/`** — living reference that tracks the current code.

A finding here may therefore be stale. Check the report's pinned commit before
acting on a `file:line` citation.

| Date | Report | Subject |
|---|---|---|
| 2026-08-22 | [Discovery audit](2026-08-22-discovery-audit/findings.md) | This repo's first severity-rated, security-inclusive audit applying an external audit-methodology playbook's S/I/C/SC/D taxonomy — 8 domain-auditor passes, a baseline reconciliation of the 2026-08-10 report's 8 findings, and 4 specialist-verification passes. |
| 2026-08-10 | [Repo quality audit](2026-08-10-repo-quality-audit/findings.md) | Repo-wide audit of hooks, scripts, skills, the instruction surface, docs, and CI after five months of feature and bug-fix PRs. Ships a prioritised cleanup backlog. |
| 2026-05-20 | [/code-review trend audit](2026-05-20-code-review-trend-audit/findings.md) | Whether the `/code-review` gate rate diverges by `permissionMode`, measured across session transcripts. Produced the `commit-gate` subcommand in `transcript-analysis.py`. |

## Adding a report

Create `docs/reports/<YYYY-MM-DD>-<slug>/findings.md`, pin the baseline commit in
its header, and add a row above — newest first. A `plan.md` alongside `findings.md`
is optional; the 2026-05 report carries one.
