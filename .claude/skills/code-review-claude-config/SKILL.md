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
  or the artifact cites the owner's timestamped authorization naming
  the exact figure and the command that produced it
- `--share-only` output in any artifact, regardless of dimensionality —
  it exists only to keep a wider corpus's raw absolutes out of the
  agent's own context, never to publish from
- a figure citing no command, or citing one that cannot refuse a wider
  corpus, unless the artifact cites the owner's timestamped
  authorization naming the exact figure and the command that produced
  it
- a figure with its own calendar-time axis (per-week, per-month, or a
  two-point before/after split) drawn from a corpus wider than this
  repository on one account — barred regardless of authorization
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

## Finding disposition addition

Before dispositioning a finding against a `hook-class: gate` hook, read `docs/hooks.md` § "Threat-model tiers". That section decides whether the finding is a defect in the gate and, when it is not, where the gap is recorded. Non-gate hooks and shared library code get no disposition change from this section.

- A regression, as that section defines it, stays under the base rules at every tier. No recording, in this diff or an earlier one, changes that.
- Judge regression from the gate's behavior at the merge-base against the staged state, covering its matcher, its early-exit and error paths, and the helpers it calls, never from the reviewer's wording or the header's Known gaps. When that comparison is unclear, the gate has no merge-base counterpart, a fail-open path has no deny test, or the diff edits the gate's tier line, its tracking pointer, or `docs/hooks.md` § "Threat-model tiers", treat the finding as a regression.
- Any other finding that section waives or routes is not an enforcement-invariant finding under the enforcement-invariant rule.
- Tag it ADDRESS. Its in-change action is the recording that section requires, not a fix. `--rationale` names the gate's tier, the recording's location, and the shape it covers.
- It counts as resolved under `code-review/SKILL.md` § "Step — Record review completion" once that recording exists.
- A finding that section does not explicitly waive or route stays under the base rules.
