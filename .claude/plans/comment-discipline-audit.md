# Comment-discipline corpus audit

## Context

Run `comment-discipline-reviewer` against the repo's live-loaded documentation
corpus — not a diff — to find pre-existing verbosity that predates the
reviewer's own conventions (and predates output-format discipline the user
picked up partway through this repo's history), because that verbosity sits
in the same files agents read into context and can bias new agent-written
comments and docs toward the same wordiness even while `comment-discipline-reviewer`
enforces the rule on every new diff. The deliverable is a findings report the
user can triage by hand — not automated fixes.

## Approach

Dispatch `comment-discipline-reviewer` once per file across the 105-file
live-loaded doc corpus via the `Workflow` tool, with a per-call prompt
override that tells the agent to treat the entire current file as in-scope
(its default behavior explicitly excludes pre-existing violations, which is
correct for its diff-triggered use in `/code-review` but wrong for this
audit). Aggregate the structured results into a new dated report under
`docs/reports/`, following the convention that directory already documents.

**Root problem:** legacy verbose prose in the live-loaded doc/skill/agent
corpus may be pattern-matched by agents writing new comments/docs, undermining
`comment-discipline-reviewer`'s per-diff enforcement.

**Givens:**
- row1: `Workflow` scripts have no filesystem or Node.js API access — a
  platform constraint of the tool itself, not something this plan can
  change. This is why mechanism 6 writes the final report from the
  dispatching session rather than from inside the script.
  [verified: `Workflow` tool description]

Three decisions below look like givens but aren't — each is a condition this
plan *could* have gone the other way on, so per `plan-it`'s ledger grammar
they're recorded as scope/mechanism choices instead, not beyond-reach
conditions:
- **Remediation is out of scope** (audit produces a findings report, not
  edits) — the user's explicit choice; see **Out of scope** below.
- **Corpus is "live-loaded docs only"** (excludes `.claude/plans/*.md` and
  `docs/reports/*`) — also the user's explicit choice; see **Out of scope**
  below. Nothing about `comment-discipline-reviewer` or `git ls-files`
  prevents including those trees; the plan simply wasn't asked to.
- **Orchestration goes through the `Workflow` tool** rather than sequential
  dispatches, above the repo's default 15-agent guideline — the user's
  explicit opt-in for that scale (see mechanism 2's own justification).

**Mechanisms:**

1. **Corpus enumeration via `git ls-files` with explicit include/exclude
   patterns**, re-run fresh at execution time (not a frozen list in this
   plan, since files land between planning and execution). anchors: root.
   Lighter primitives considered: `find` — rejected, sweeps untracked/gitignored
   scratch content that `git ls-files` correctly excludes; a hand-curated
   file list — rejected, goes stale as skills/agents/docs are added or
   renamed and nothing forces it to stay current.

   ```
   git ls-files | grep -E '(/SKILL\.md$|^claude/\.claude/agents/.*\.md$|^plugins/[^/]+/agents/.*\.md$|^claude/\.claude/rules/.*\.md$|^\.claude/rules/.*\.md$|(^|/)CLAUDE\.md$|(^|/)REFERENCES\.md$|(^|/)README\.md$)' | grep -v 'temp-project'
   git ls-files | grep -E '^docs/.*\.md$' | grep -v '^docs/reports/'
   ```
   105 files, ~14,850 lines, as of this plan's baseline (`6291b343`).

2. **`Workflow` tool, single `pipeline()` stage over the 105 files**, each
   item dispatched to `agentType: 'comment-discipline-reviewer'` with
   `schema` forcing structured output. anchors: root.
   Lighter primitives considered: `parallel()` (a barrier) — rejected, no
   stage needs cross-file context until aggregation, and aggregation is a
   deterministic array filter/flatten that needs no agent, so a barrier
   would only add latency; sequential `Agent`-tool calls one at a time —
   rejected by the user's own throughput choice, and 105 serial turns is
   impractical in one session.

3. **Reuse `comment-discipline-reviewer` unmodified via `agentType`**,
   overriding only its default diff-scoping through the per-call prompt text
   (not editing the agent file). Its own "How to work" step 3 explicitly
   excludes pre-existing violations by default
   [verified: `claude/.claude/agents/comment-discipline-reviewer.md:73-77`],
   which is correct for its `/code-review` diff trigger — this plan
   overrides it per-call rather than adding a permanent "full-audit" mode to
   the agent file, since only this one-off sweep needs it. anchors: root.
   Lighter primitives considered: a fresh one-off audit agent duplicating
   the five review angles — rejected, violates single-source-of-truth on the
   rule definitions, the existing agent already encodes them; a permanent
   "full-audit mode" added to the agent file — rejected, that changes the
   agent's behavior for every future `/code-review` dispatch to support a
   one-off sweep, when a per-call prompt gets the same result for this run
   alone.

4. **`schema` (StructuredOutput) instead of the agent's own `findings_path`
   file-write mode.** anchors: root. Lighter primitive considered:
   `findings_path` per-file — rejected, would produce 105 scratch files
   needing a second pass to merge, where `schema` hands the workflow script
   the same data in-memory for direct aggregation.

5. **Aggregation is plain JS (`filter`/`flatMap`) inside the script — no
   agent.** anchors: root. Merging deterministic JSON arrays needs no
   judgment call.

6. **Final report written by the dispatching session after the workflow
   returns**, not by the script itself. anchors: row1. Workflow scripts have
   no filesystem access, so the write must happen outside the script,
   following `docs/reports/README.md`'s existing
   `docs/reports/<YYYY-MM-DD>-<slug>/findings.md` convention (see its
   `## Adding a report` section) and mirroring
   `docs/reports/2026-08-10-repo-quality-audit/findings.md`'s structure
   (dated header, pinned baseline commit, `## Status` placeholder,
   `## Methodology`, findings, a severity-sorted `## Recommendations`).

**Per-file audit prompt** must explicitly supersede the agent's own
pre-existing-violation filter for this dispatch — e.g.: "This is a full-file
audit, not a diff review. Treat the entire current content of `<file>` as in
scope, including content that predates any recent change — your default
instruction to flag only diff-introduced/worsened violations does not apply
to this dispatch." followed by the five review angles it already knows and a
request to return every finding via the schema.

**Findings schema** (per file, zipped with the file path from the pipeline's
`originalItem` — the agent itself reports only what it found):

```js
const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          violation_type: {
            type: 'string',
            enum: ['comment_verbosity', 'wrong_altitude', 'pr_defined_terminology',
                   'used_to_be_x_framing', 'durable_doc_self_test_failure'],
          },
          line: { type: 'integer' },
          quoted_text: { type: 'string' },
          why: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['violation_type', 'line', 'quoted_text', 'why', 'fix'],
      },
    },
  },
  required: ['findings'],
}
```

## Critical files

- **New:** `docs/reports/<execution-date>-comment-discipline-corpus-audit/findings.md`
  — the audit deliverable, structured per mechanism 6 above.
- **Modify:** `docs/reports/README.md` — add one row to the reports table
  (newest-first), matching the existing row format.
- **Reuse, unmodified:** `claude/.claude/agents/comment-discipline-reviewer.md`
  — dispatched via `Workflow`'s `agentType`, scope overridden only through
  the per-call prompt (mechanism 3).
- **Reuse as structural template:**
  `docs/reports/2026-08-10-repo-quality-audit/findings.md` — header, `##
  Status`, and `## Methodology` section shapes.
- No hooks, skills, or production code are touched — this plan's tracked
  diff is small (one new report file, one README row) despite the 105-agent
  fan-out that produces it.

## Verification

- **Pilot before the full run.** `comment-discipline-reviewer`'s own
  instructions are emphatic about excluding pre-existing violations (the
  cited "How to work" step 3) — a one-paragraph prompt override may not
  reliably suppress that on every dispatch. Before running the full
  105-file pipeline, dispatch the same per-call prompt against 3–5 files
  already known to carry legacy verbosity (e.g. long-standing skill
  REFERENCES.md files) and confirm the returned findings actually include
  pre-existing paragraphs, not only content that happens to look
  diff-worthy. If the override doesn't reliably take, tighten the prompt
  before scaling to the full corpus — don't discover this after 105 calls.
- Re-run the `git ls-files` filter from mechanism 1 at execution time and
  diff its output against the file list the workflow actually processed —
  any mismatch (new file landed, one renamed) means the corpus drifted and
  the run should be re-scoped, not silently under- or over-counted.
- Spot-check 3–5 aggregated findings against the live file content: confirm
  the reported line number and quoted text actually match what's on disk at
  that line. Structured-output line numbers are exactly the kind of detail a
  model can misreport.
- Confirm the new `docs/reports/README.md` row matches the existing table's
  column format and stays newest-first.
- No test suite run needed — the diff is doc-only and doesn't touch
  `claude/.claude/`'s Python/shell surface.

## Out of scope

- Actually trimming or rewriting any flagged prose — this plan produces a
  findings report only; remediation is a separate, later pass the user
  triages by hand.
- `.claude/plans/*.md` and `docs/reports/*` — excluded as historical records
  per the corpus-scope decision above.
- Any new hook or automation to prevent future recurrence — `/code-review`'s
  existing per-diff dispatch of `comment-discipline-reviewer` already covers
  that going forward; this plan is a one-time backlog sweep of what predates
  it.
