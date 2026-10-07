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
- `review-round-cost --pooled --show-withheld` output in any artifact — it is
  barred outright with no owner-authorization path; see
  `docs/transcript-analysis.md` § "review-round-cost"
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
- a diff that weakens `review-round-cost --pooled`'s withholding floors (the
  count floor and the dominance-precision floor), its fail-closed stderr
  default (an unrecognized diagnostic stays withheld behind one fixed
  notice), its fixed set of conditional lines (a new data-dependent
  line, or a conditional line carrying a digit, a dollar amount, or a path),
  or the block's printed-content invariant (no dollar amount, raw count, or
  per-account, per-project, or per-branch split on any line, conditional or
  not)

Give a rounded or generalized figure more scrutiny, not less. The six
always-on structural detectors already catch raw pastes, so what reaches
this item is disproportionately content already generalized enough to
clear them.

## Finding disposition addition

Before dispositioning a finding against a `hook-class: gate` hook, read `docs/hooks.md` § "Threat-model tiers". That section decides whether the finding is a defect in the gate and, when it is not, where the gap is recorded. Non-gate hooks and shared library code get no disposition change from this section.

- Ask first whether the finding is a per-vector gap or a genuinely-lax failure. A per-vector gap needs a shape a cooperative agent would never emit, against a gate whose tier line omits `untrusted-input`. A genuinely-lax failure is the gate's rule failing on a shape a cooperative agent does emit. That section's `cooperative` bullet and its mis-parse paragraph draw the line. A genuinely-lax failure stays under the base rules unless it is already in that section's closed existing-debt set. Outside that set, a genuinely-lax failure that is not a regression is never DEFERred. Leaving it unfixed takes a blocking stop-and-ask that tells the engineer a keep with no scope or time limit admits it as permanent existing debt. Before the engineer answers, that stop also gives the base contradiction-route rule's human-keep disclosures. Log the engineer's keep SETTLED `--decided-by engineer` as the base contradiction-route rule logs a human keep, adding `--carry-forward` only under that rule's conditions for it. Log any other keep without it, and ask again on each re-raise.
- A regression, as that section defines it, is an enforcement-invariant finding under the enforcement-invariant rule at every tier. No recording, in this diff or an earlier one, changes that.
- A finding against a gate whose tier line lists `untrusted-input` stays under the base rules, as does one against a gate that another gate's header names as its backstop against evasion.
- Any other finding that section waives or routes is not an enforcement-invariant finding under the enforcement-invariant rule. Tag it DEFER under criterion 3 (`gold-plating-beyond-declared-user-surface`): the gate's tier line, read with that section's regression-only rule, is its declared threat model. `--source` names the gate's header block. `--rationale` names the gate's tier and the shape.
- Before the next `/ready-for-review`, add every shape this PR DEFERred under criterion 3 as a waived or routed finding against a gate, and every genuinely-lax failure the engineer's own SETTLED row kept with `--carry-forward` (never a carry row or a regression), to one line in that gate's header Known-gaps section. Create the section when the header has none. Extend that line rather than adding another. That line is the recording that section requires; the PR body's rendered DEFER or SETTLED row is the in-PR record.
- A finding that section does not explicitly waive or route stays under the base rules.
