---
name: code-review-claude-config
description: Project-specific layer for /code-review, loaded only when reviewing changes in the claude-config repo itself.
disable-model-invocation: true
---

## Base checklist addition

P1. **Private-corpus provenance** — Flag any measurement, example, log excerpt,
or command output the diff adds whose only known source is private engagement
material. See CLAUDE.md's "Also redact structural fingerprints and
provenance" rule and `docs/private-project-redaction.md` § "Publishing a tooling measurement" for the publication bar. A figure
decomposed by project, account, or engagement is a P1 finding on sight.
So is any of:

- a figure computed over a corpus wider than this repository on one
  account, unless that section's own-history-count exemption covers it
  or the artifact cites the owner's timestamped authorization for that
  exact figure
- `--share-only` output in any artifact, regardless of dimensionality —
  it exists only to keep a wider corpus's raw absolutes out of the
  agent's own context, never to publish from
- a figure citing no command, or citing one that cannot refuse a wider
  corpus, unless the artifact cites the owner's timestamped
  authorization for that exact figure
- a figure with its own calendar-time axis drawn from a corpus wider
  than this repository on one account — barred regardless of
  authorization
- a count of how many accounts or declared config-dir roots exist
- a new figure that lets a reader subtract a previously-published
  pooled figure down to its non-this-repo remainder
- a diff that widens the scope of an auto-publishing script (for
  example, adding a flag to `pr-cost-section.sh` or loosening
  `cost-counts`'s hardcoded single root)
- a diff that weakens a scope refusal a publication instrument depends
  on

Give a rounded or generalized figure more scrutiny, not less. The six
always-on structural detectors already catch raw pastes, so what reaches
this item is disproportionately content already generalized enough to
clear them.
