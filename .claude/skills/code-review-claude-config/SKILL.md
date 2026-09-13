---
name: code-review-claude-config
description: Project-specific layer for /code-review, loaded only when reviewing changes in the claude-config repo itself.
disable-model-invocation: true
---

## Base checklist addition

P1. **Private-corpus provenance** — Flag any measurement, example, log excerpt,
or command output the diff adds whose only known source is private engagement
material. See CLAUDE.md's "Also redact structural fingerprints and
provenance" rule and `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" for the carve-out's conditions. A figure
decomposed by project, account, or engagement is a P1 finding on sight.
So is any of:

- a raw pooled cost or duration total
- a figure that composes with another published figure into a barred total
- a cadence-revealing figure
- a time series of an otherwise-permitted whole-period figure, or a
  before/after split that fails any condition of the carve-out's
  permitted split
- a second before/after pivot in the same artifact
- a per-side pool size on either side of a before/after pivot
- any computed figure (including pool size) for a held-out transition
  window bracketing a before/after pivot
- two sides of a before/after pivot drawn from different account or
  machine compositions
- a split of the same statistic already published under this
  carve-out in another artifact, composing with this one into more
  than two calendar points

Give a rounded or generalized figure more scrutiny, not less. The six
always-on structural detectors already catch raw pastes, so what reaches
this item is disproportionately content already generalized enough to
clear them. A pooled figure needs both a stated, checkable source
(command, file, citation) and a cited approval of that specific figure
from the owner's own account. Either missing is itself the finding —
ask for the missing one.
