---
name: error-mode-analysis
description: Produce a signal-bucketed error-mode report from a delivered body of multi-session AI-assisted work — bucket every failure by which pipeline layer caught it, correlate transcript signals with PR review comments, and split output into a private, project-identifying report and a de-identified public lessons doc. Builds on transcript-narrative and transcript-analysis. Supports a multi-window trend pass to distinguish recurring patterns from one-off noise.
context: fork
background: false
argument-hint: "[optional output directory]"
---

Analyzes a *delivered body of work* (many sessions and PRs, potentially spanning multiple projects and calendar time), not a single transcript. The organizing question is: where is the review pipeline self-correcting, and where is it blind? Answering that requires two data sources (transcript signals AND PR review comments — they are not the same signal) and a bucketing scheme that groups by detection layer rather than by investigative phase.

## Step 0 — Confirm Bash is available

This skill runs as a forked context and depends on `Bash` for every step below, including the `gh api graphql` call in Step 3. If `Bash` is unavailable, stop and report that this skill requires Claude Code v2.1.218 or later — an earlier version honors `context: fork` without honoring `background: false`, producing a background fork whose narrowed tool set may omit `Bash`.

This skill never invokes `marker.sh` and never invokes a review skill, directly or by dispatching a subagent to do either on its behalf.

## Step 1 — Scope the delivery

Identify the branches, PRs, sessions, and date range under analysis. Use `transcript-analysis.py buckets` to enumerate branches and models:

```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo
```

Always scope `--projects`/`--this-repo` — an unscoped run pools every project on the machine. See `docs/transcript-analysis.md`'s "Scoping to this repo" section for the derivation, its gaps, and the subdirectory-session fallback.

`buckets`, `review-trace`, and `fail-seq` all accept `--projects GLOB`/`--this-repo` (cross-repo), `--branches B1,B2,...` (multiple branches/PRs at once), and (on `review-trace`) `--since`/`--until DATE` — the tooling already spans repos and calendar time; scope is a choice, not a limitation.

**Default to breadth, not the narrowest concrete option.** Default scope (unless the user names one PR/branch): the current project, all branches, last 6 weeks — widen rather than narrow if a scoping question goes unanswered, since under-scoping risks promoting a false pattern to a fix. Take the analysis window from `review-trace --since/--until`, not `buckets`' Date range column — `buckets` takes no `--since`/`--until` flags, so its Date range column reflects each branch's full session span, not the requested window.

## Step 2 — Collect transcript signals

Invoke `transcript-narrative` and `transcript-analysis` by name — do not restate their procedures here:

- `transcript-narrative` runs as its own fork and returns a file path plus a first pass of ranked lessons — the annotated per-phase timeline itself is file-only now, not returned inline. Record the returned path; Step 5 reads it before assembling Artifact A.
- `transcript-analysis` produces the quantitative appendix (`fail-seq`, `review-trace`, `duration`, `subagents`, `pr-link`).

`review-trace` locates every skill invocation, hook denial, and reviewer-agent spawn per session — exactly the Pipeline working and Post-commit bot evidence Step 4 needs.

`user-input --corrections-only` pulls verbatim FOLLOWUP/EXPLICIT_CORRECTION prompts for Step 4's Human-unique and Cross-session process buckets, skipping a manual transcript re-read.

`user-input --corrections-only`/`struggle`'s task-notification false-flag caveat (`transcript-analysis`'s own Caveats section) applies here: verify each flagged turn is inside an actual human message before counting it toward Step 4's buckets.

## Step 3 — Collect PR review comments

A distinct second source, not a subset of the transcript. Human PR reviewers comment on the PR itself; those exchanges never appear in the session transcript unless the AI was asked to read them. Fetch all three comment kinds in one read-only GraphQL round trip rather than three separate paginated REST calls:

`-F pr=` is required — `-f` sends a string and the `Int!` variable rejects it.

<!-- HOOK_TEST_FIXTURE: fetch-pr-comments — the hook-alignment test suite reads this exact fenced block to verify require-respond-pr.sh allows it, so a regression to a denied REST form cannot land silently. Do not duplicate elsewhere; the test re-reads it from here. -->

```bash
gh api graphql -f owner=OWNER -f repo=REPO -F pr=NUMBER -f query='
query($owner:String!, $repo:String!, $pr:Int!) {
  repository(owner:$owner, name:$repo) { pullRequest(number:$pr) {
    comments(first:100)      { totalCount pageInfo{endCursor} nodes{ author{login} body } }
    reviews(first:100)       { totalCount pageInfo{endCursor} nodes{ author{login} state body } }
    reviewThreads(first:100) { totalCount pageInfo{endCursor} nodes{ comments(first:100){
                                 totalCount nodes{ author{login} path body } } } }
  } }
}'
```

Compare each `totalCount` to its returned node count; re-run the query with `after:"<endCursor>"` only on connections still short — a re-queried short connection returns the same first page, not new data, so merge in only the connections you added the cursor to. `reviewThreads.comments` carries its own `totalCount`/`pageInfo` per thread, one level below the top-level cursor — a thread over 100 comments needs this same after-cursor merge applied to that one thread alone. Never use `--paginate` on this GraphQL query — it shares one cursor across all three connections, so it can silently truncate `reviewThreads` while exiting 0; this doesn't extend to `respond-pr`'s three separate REST `--paginate` calls, each of which follows its own response `Link` header and is unaffected.

Skip reviews whose `body` is empty — a bare approval carries no finding to correlate.

This query reads. Posting any reply goes through `/respond-pr`, never this surface.

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

Read the file at the path `transcript-narrative` returned in Step 2 before assembling Artifact A — its annotated timeline arrives as a path now, not inline.

**Artifact A** — the private report. Full detail: error modes with evidence, session context, quantitative metrics, PR-comment correlation. May contain project-identifying content. Write it to `<output-dir>/artifact-a-private-report.md` — the output directory given as this skill's argument, or a `mktemp -d` directory when none is given; state plainly to the caller that the default location is temporary. Before writing, confirm the target does not resolve inside a git-tracked tree unless that tree's `.gitignore` covers it, and manually scan the assembled content for customer PII and credentials before persisting it — Artifact A has no later scrub-before-promotion gate the way Artifact B does in Step 6, so this scan is the only thing standing between it and disk. Stays on the private machine regardless of where it is written; never transported to a public repo or personal tooling.

**Artifact B** — the de-identified lessons doc, built from the Step 6 skeleton and written to `<output-dir>/artifact-b-lessons.md`. Scrubbed of project-identifying content. May cross into a public repo once Step 6's checklist has been walked.

Scrub Artifact B against these categories before it leaves the private machine:
- Tracker IDs — replace with `PROJ-<n>` / `TICKET-<n>` placeholders.
- Project or org names, codenames, internal URLs/hostnames/paths.
- Internal tool or product names not generally known outside the project.
- **Structural fingerprints** — a verbatim policy shape, a rare column-naming pattern, an unusual error-code namespace, or any other structure that identifies the project via shape alone even with names stripped. Generalize the example.

Two things this scrub relies on that are easy to get backwards:

1. **A stowed commit hook is not a universal safety net.** The `deny-private-project-refs` hook only re-checks tracker-ID/project-name tiers, and only when the destination repo is `claude-config`; the manual scrub is the sole defense everywhere else and the only defense against structural fingerprints anywhere.
2. **Scrub-complete and diffed is a precondition of transport, not a reminder to keep in mind.** Do not commit or push Artifact B until the checklist above has been walked and the document has been diffed line by line.

## Step 6 — Artifact B skeleton

Derive Artifact B by scrubbing a working copy of Artifact A into the skeleton below, preserving verbatim quotes, per-session evidence, and each bucket's priority reasoning. Keep that working copy outside any git repo (or under a path `.gitignore` covers) until it has been scrubbed and diffed line by line per the checklist above — it is not Artifact B until that gate passes. Once it passes, write it to the Artifact B path named in Step 5. Every error mode identified in Step 4 must appear in Artifact B; do not silently drop a finding for brevity.

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

Return the paths to Artifact A and Artifact B, plus the Step 4 bucket table, rather than either report inline.

## Step 7 — Multi-window trend pass

A single run buckets one flat window. That's enough to tell you a failure mode exists; it isn't enough to tell you whether it's recurring, growing, or a one-off — and that distinction changes the priority column in Step 4 (a human-unique finding seen once across six weeks is a different fix priority than the same finding recurring every PR).

Split the Step 1 date range into successive sub-windows (weekly, or biweekly for a longer range) and re-run Steps 2–4 per sub-window using `--since`/`--until` on `review-trace` (and `--branches`/`--projects` held constant across sub-windows so the only thing varying is time). Each sub-window's own Step 2 re-invocation of `transcript-narrative` returns its own file path — record each sub-window's path (e.g. by sub-window label) rather than overwriting a single variable, since Step 5 needs the full set once every sub-window has run:

```bash
python3 ~/.claude/scripts/transcript-analysis.py review-trace --projects '<glob>' --since <window-start> --until <window-end>
```

For each distinct error mode identified in Step 4, classify it against the sub-window sequence:

- **Recurring** — present in two or more non-adjacent sub-windows. Treat as a systemic gap, not an incident; the priority in Step 4's table is a floor, not a ceiling — recurrence across windows is grounds to raise it.
- **Growing** — present in an increasing share of sessions across successive sub-windows. Flag regardless of current bucket/priority; a growing trend outranks a static one at the same nominal priority.
- **One-off** — present in exactly one sub-window. Report it (per Step 6's "do not silently drop a finding" rule) but do not let it drive a checklist or process change on its own — that's exactly the overfit-to-one-session failure this step exists to prevent. Note it as a candidate to watch, not a confirmed pattern.

Add a "Trend" column to the bucket table (Step 4) and a one-line trend caveat to any candidate fix in Artifact B that was promoted primarily on the strength of one sub-window.

For raw quantitative metrics without this bucketing, use `transcript-analysis`; for a narrative case study without the detection-layer lens, use `transcript-narrative`. This skill composes both and adds the PR-comment source, the bucket taxonomy, the two-artifact boundary, and the multi-window trend pass.
