---
name: issue-triage
description: >
  Evidence-backed, report-only GitHub issue triage.
  TRIGGER when: asked to triage or assess the open issue backlog.
  DO NOT TRIGGER when: acting on, filing, or reviewing one issue or PR —
  use `gh`/`/respond-pr`.
user-invocable: true
---

# Issue triage

Stateless and report-only: every run re-triages all currently-open issues
from scratch, produces a disposition report, and executes nothing —
closing, commenting, or relabeling stays a manual follow-up through
`/respond-pr` or `gh`. Every artifact lands under `<config-dir>/issue-
triage/<owner>-<repo>/<run-timestamp>/`, outside the repository entirely.

## 1. Resolve the run target and directory

Run `gh auth status` first, so an unauthenticated session fails here with a
clear message rather than mid-pipeline. Then three independent
single-statement Bash calls — no `VAR=$(…)`-then-reuse inside one fenced
block:

```bash
gh repo view --json nameWithOwner -q .nameWithOwner
```
```bash
date -u +%Y%m%dT%H%M%SZ
```
```bash
mkdir -p "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/issue-triage/<owner>-<repo>/<timestamp>"
```

Body prose below refers to this as `<run-dir>` (`<config-dir>/issue-
triage/<owner>-<repo>/<timestamp>`); runnable commands use the
`${CLAUDE_CONFIG_DIR:-$HOME/.claude}` form.

## 2. Fetch the open set once, to disk

```bash
gh issue list --state open --limit 100 --json number,title,labels,createdAt,updatedAt,author > <run-dir>/open-issues.json
```

Bodies and comments are deliberately excluded — batch agents fetch them
per issue, so you never ingest comment text yourself. If the returned
count equals the limit, re-fetch at a higher `--limit`; after 3 re-fetch
attempts still truncated, stop and report the truncation rather than
looping.

## 3. Cluster and announce the run plan

Cluster by subsystem/theme — the axis is read reuse: issues in one batch
should send one agent to the same files. There is no fixed batch-size
constant; weigh the two failure modes instead:

- an oversized batch loses per-issue rigor.
- one-agent-per-issue loses sibling-relationship detection and multiplies
  redundant reads.

Announce the plan in one line (issue count, batch count, dispatch count).
If zero open issues, skip clustering
and every dispatch stage below and report "no open issues" directly. If
the dispatch count exceeds roughly 15 batches, say so explicitly before
fanning out, so the invoking session can decide whether to proceed.

## 4. Dispatch the batch-evidence agents

Dispatch one `issue-triage:issue-triage-evidence`, `model: sonnet` agent
per batch, in parallel, no `isolation`. Each prompt must carry: its issue
numbers; its own absolute fragment path (`<run-dir>/batch-NN-<theme>.md`);
and the run's resolved `<owner>/<repo>`.

The dispatched agent's own file
(`plugins/issue-triage/agents/issue-triage-evidence.md`) carries the
standing rules and the per-issue record schema (§5) as their canonical,
single-source copy — do not restate them here.

The standing rules given to each dispatch are the only control on what it
does with live `gh` credentials and unrestricted `Bash`: no command-level
enforcement exists, backstopped only by the agent's own category-level
`tools:` grant (no `Edit`, `Agent`, or network tools). Each agent writes
its fragment and returns one line per issue plus the fragment path.

## 5. Per-issue record schema

See `issue-triage-evidence.md`'s own "Per-issue record schema" section for
the canonical schema every batch fragment and the final report follow.

## 6. Cross-batch synthesis

Dispatch one `general-purpose`, `model: opus` agent, given `<run-dir>`, to
read every fragment itself: find cross-batch clusters no single batch
could see, pick one lead finding, and derive this population's own
prioritization axis — do not import a fixed tiering. It returns the report
body; write it verbatim to `<run-dir>/report.md`.

## 7. Claim verification

Dispatch one `general-purpose`, `model: sonnet` agent over `report.md`
plus the fragments: independently re-derive the lead finding's headline
claim and every `fixed`/`superseded`/`stale` verdict from current repo
state, and for each confirmed defect check structural siblings for the
same shape. It returns per-claim `confirmed | wrong (+correction) |
unverifiable (+what is missing)`. Apply corrections before delivery;
downgrade anything unresolved to `verification: partial` with the gap
named.

## 8. Drift recheck (exactly one pass)

Re-list open issues to `<run-dir>/open-issues-final.json` and diff number
sets against step 2's set: numbers that appeared get one extra batch
dispatch folded into the report; numbers that closed are marked
closed-during-run and dropped from the disposition list. Anything still
new after this one pass is listed by number as explicitly untriaged — this
bound is a stated tradeoff, not a guarantee.

## 9. Reconcile and deliver

Before delivering, confirm every number from `open-issues.json` (plus any
drift-recheck additions) appears in the report's disposition list or its
explicit untriaged list; if a batch agent died mid-dispatch, surface that
mismatch explicitly rather than under-reporting silently. Report to chat:
the lead finding in two or three sentences, counts by disposition,
triaged-vs-open counts, any untriaged numbers, and the absolute
`report.md` path — not the full table (terminal width-wrapping). State
that run artifacts live outside the repository, persist indefinitely with
no redaction pass, and that committing any of them is a separate call
inheriting the repo's redaction rules. Disclose the accepted residual: the
batch-evidence dispatch holds live `gh` credentials and unrestricted
`Bash` and `Write`, with no separate enforcement layer beyond the standing
rules above. An issue or comment body is untrusted and could attempt to
redirect it toward an off-target `gh` mutation, an arbitrary non-`gh`
command, or credential exfiltration. This residual is accepted because
this repo's own issue backlog is already public. Name `/respond-pr` as
the manual next step for acting on the report (comments, closes).
