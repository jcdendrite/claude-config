---
name: error-mode-analysis
description: Produce a signal-bucketed error-mode report from a delivered body of multi-session AI-assisted work — bucket every failure by which pipeline layer caught it, correlate transcript signals with PR review comments, and split output into a private, project-identifying report and a de-identified public lessons doc. Builds on transcript-narrative and transcript-analysis. Supports a multi-window trend pass to distinguish recurring patterns from one-off noise.
---

Analyzes a *delivered body of work* (many sessions and PRs, potentially spanning multiple projects and calendar time), not a single transcript. The organizing question is: where is the review pipeline self-correcting, and where is it blind? Answering that requires two data sources (transcript signals AND PR review comments — they are not the same signal) and a bucketing scheme that groups by detection layer rather than by investigative phase.

## Step 1 — Scope the delivery

Identify the branches, PRs, sessions, and date range under analysis. Use `transcript-analysis.py buckets` to enumerate branches and models:

```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets
```

`buckets`, `review-trace`, and `fail-seq` all accept `--projects GLOB` (cross-repo), `--branches B1,B2,...` (multiple branches/PRs at once), and (on `review-trace`) `--since`/`--until DATE` — the tooling already spans repos and calendar time; scope is a choice, not a limitation.

**Default to breadth, not the narrowest concrete option.** A single PR or session is the highest-noise, lowest-confidence sample available — one human comment or one bad turn looks identical to a systemic gap when it's the only data point. Absent an explicit request to narrow (the user names one PR/branch), default the scope to: the current project, all branches, last 6 weeks. If a scoping question to the user goes unanswered, widen the default rather than narrowing it — the cost of over-scoping is a longer report; the cost of under-scoping is a false pattern promoted to a fix.

## Step 2 — Collect transcript signals

Invoke `transcript-narrative` and `transcript-analysis` by name — do not restate their procedures here:

- `transcript-narrative` produces the annotated per-phase timeline and a first pass of ranked lessons.
- `transcript-analysis` produces the quantitative appendix (`fail-seq`, `review-trace`, `duration`, `subagents`, `pr-link`).

`review-trace` is the most load-bearing subcommand for this step — it locates every skill invocation, hook denial, and reviewer-agent spawn per session, which is exactly the "pipeline working" and "bot findings" evidence Step 4 needs.

## Step 3 — Collect PR review comments

A distinct second source, not a subset of the transcript. Human PR reviewers comment on the PR itself; those exchanges never appear in the session transcript unless the AI was asked to read them. Fetch all three comment types with `--paginate` — skipping any one, or omitting `--paginate`, silently truncates the signal and undercounts human-caught findings:

```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments --paginate
gh api repos/{owner}/{repo}/pulls/{number}/reviews --paginate --jq '.[] | select(.body != "")'
gh api repos/{owner}/{repo}/issues/{number}/comments --paginate
```

Correlate each comment against the error-mode list being built in Step 4. A finding raised and resolved only in a PR thread — never surfaced in the session — is exactly what an analysis based on transcripts alone would silently drop.

## Step 4 — Assign each error mode to a detection-layer bucket

Score every error mode on two independent axes: which layer detected it (the bucket), and how urgently it needs a fix (the priority). The bucket says *where* a control belongs; the priority says *in what order* to add it.

| Bucket | Definition | Detection layer | Remediation priority |
|---|---|---|---|
| **Cross-session process** | A recurring un-learned pattern or systematically omitted step, visible only across sessions — no single reviewable artifact catches it. Usually an efficiency failure (wasted turns), not a correctness failure, because the pipeline self-recovers. | Post-hoc only | Lower than the other two — but only while the self-recovery signal (a hook denial, a convergent retry) still exists; remove that signal and the class becomes a silent correctness failure. |
| **Post-commit bot** | A defect in a reviewable artifact caught by automated post-commit analysis (static analysis, security scan) but missed pre-commit by reviewer agents. | Post-commit bot | High — the pattern is already machine-recognizable; the reviewer-agent prompt is improvable to catch it pre-commit. |
| **Human-unique** | A defect caught only by a human PR reviewer. | Human review | Medium — depends on the finding; some are skill gaps, some are handoff-quality problems, some resist systematizing. |
| **Pipeline working** | A defect caught by reviewer agents as intended. Not a gap — included for completeness and calibration. | Reviewer agent | Informational. |

Ambiguous-case rule: if a bot caught something a reviewer agent's checklist already covers, file it as **post-commit bot** (the miss is real regardless of theoretical coverage) and note the checklist gap in the candidate fix. If a human raised something a bot would also have caught had the PR reached that stage, file it as **human-unique** — bucket by what actually happened, not what might have.

The bucket determines the control: cross-session → new enforcement mechanism or telemetry (a hook, a checklist entry) — strengthening a review station cannot help, since there is no artifact for it to review; post-commit bot → strengthen the reviewer-agent prompt that should have caught it; human-unique → case by case (skill gap, handoff-quality problem, or genuinely hard to systematize).

## Step 5 — Split into two artifacts + pre-transport boundary checklist

**Artifact A** — the private report. Full detail: error modes with evidence, session context, quantitative metrics, PR-comment correlation. May contain project-identifying content. Stays on the private machine; never transported to a public repo or personal tooling.

**Artifact B** — the de-identified lessons doc (Step 6 skeleton). Scrubbed of project-identifying content. May cross into a public repo.

Scrub Artifact B against these categories before it leaves the private machine:
- Tracker IDs — replace with `PROJ-<n>` / `TICKET-<n>` placeholders.
- Project or org names, codenames, internal URLs/hostnames/paths.
- Internal tool or product names not generally known outside the project.
- **Structural fingerprints** — a verbatim policy shape, a rare column-naming pattern, an unusual error-code namespace, or any other structure that identifies the project via shape alone even with names stripped. Generalize the example.

Two things this scrub relies on that are easy to get backwards:

1. **A stowed commit hook is not a universal safety net.** If the destination repo is `claude-config`, its `deny-private-project-refs` commit hook mechanically re-checks the tracker-ID and project-name tiers on `git commit` — a genuine second layer there. For every *other* destination repo, that hook is not present or does not fire, and the manual scrub above is the only defense. Structural fingerprints are manual-only in every destination; no hook catches those.
2. **Scrub-complete and diffed is a precondition of transport, not a reminder to keep in mind.** Do not commit or push Artifact B until the checklist above has been walked and the document has been diffed line by line.

## Step 6 — Artifact B skeleton

Write Artifact B fresh from the bucket taxonomy built in Step 4 — do not produce it by redacting a copy of Artifact A. A document authored clean from the taxonomy never contains the private span to begin with; a redaction pass that misses one span leaks it.

```markdown
# [De-identified] Delivery error-mode lessons

## Bucket: Cross-session process
- **Failure mode:** …
  **Mechanism:** why the pipeline missed it across sessions
  **Candidate fix:** …

## Bucket: Post-commit bot
- **Failure mode:** …
  **Mechanism:** which reviewer-agent checklist should have caught this pre-commit
  **Candidate fix:** …

## Bucket: Human-unique
- **Failure mode:** …
  **Mechanism:** skill gap / handoff-quality problem / hard to systematize
  **Candidate fix:** …

## Bucket: Pipeline working (informational)
- **Observation:** …

## Candidate GitHub issue draft
Title: …
Body: …
```

## Step 7 — Multi-window trend pass

A single run buckets one flat window. That's enough to tell you a failure mode exists; it isn't enough to tell you whether it's recurring, growing, or a one-off — and that distinction changes the priority column in Step 4 (a human-unique finding seen once across six weeks is a different fix priority than the same finding recurring every PR).

Split the Step 1 date range into successive sub-windows (weekly, or biweekly for a longer range) and re-run Steps 2–4 per sub-window using `--since`/`--until` on `review-trace` (and `--branches`/`--projects` held constant across sub-windows so the only thing varying is time):

```bash
python3 ~/.claude/scripts/transcript-analysis.py review-trace --projects '<glob>' --since <window-start> --until <window-end>
```

For each distinct error mode identified in Step 4, classify it against the sub-window sequence:

- **Recurring** — present in two or more non-adjacent sub-windows. Treat as a systemic gap, not an incident; the priority in Step 4's table is a floor, not a ceiling — recurrence across windows is grounds to raise it.
- **Growing** — present in an increasing share of sessions across successive sub-windows. Flag regardless of current bucket/priority; a growing trend outranks a static one at the same nominal priority.
- **One-off** — present in exactly one sub-window. Report it (per Step 6's "do not silently drop a finding" rule) but do not let it drive a checklist or process change on its own — that's exactly the overfit-to-one-session failure this step exists to prevent. Note it as a candidate to watch, not a confirmed pattern.

Add a "Trend" column to the bucket table (Step 4) and a one-line trend caveat to any candidate fix in Artifact B that was promoted primarily on the strength of one sub-window.

For raw quantitative metrics without this bucketing, use `transcript-analysis`; for a narrative case study without the detection-layer lens, use `transcript-narrative`. This skill composes both and adds the PR-comment source, the bucket taxonomy, the two-artifact boundary, and the multi-window trend pass.
