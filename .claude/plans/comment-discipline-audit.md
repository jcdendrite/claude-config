# Comment-discipline corpus audit

## Context

Run `comment-discipline-reviewer` against the repo's live-loaded documentation
corpus — not a diff — to find and trim pre-existing verbosity that predates
the reviewer's own conventions (and predates output-format discipline the
user picked up partway through this repo's history), because that verbosity
sits in the same files agents read into context and can bias new
agent-written comments and docs toward the same wordiness even while
`comment-discipline-reviewer` enforces the rule on every new diff. The
deliverable is an actual trimmed diff across the corpus, reviewed the normal
way (as a diff, via `/code-review`) — not a findings report for manual
triage.

## Approach

Dispatch `comment-discipline-reviewer` once per file across the 105-file
live-loaded doc corpus via the `Workflow` tool, with a per-call prompt
override that tells the agent to treat the entire current file as in-scope
(its default behavior explicitly excludes pre-existing violations, which is
correct for its diff-triggered use in `/code-review` but wrong for this
audit). Each file with findings then goes through a second pipeline stage —
`code-writer` applies exactly the fixes those findings named, nothing more.
The corpus lands as one reviewable diff on this branch; no separate report
file.

**Root problem:** legacy verbose prose in the live-loaded doc/skill/agent
corpus may be pattern-matched by agents writing new comments/docs, undermining
`comment-discipline-reviewer`'s per-diff enforcement.

**Givens:**
- row1: `Workflow` scripts have no filesystem or Node.js API access — a
  platform constraint of the tool itself, not something this plan can
  change. This is why the audit stage hands its findings to the fix stage
  in-memory (via `schema`, mechanism 4) rather than through a file on disk.
  [verified: `Workflow` tool description]

Two decisions below look like givens but aren't — each is a condition this
plan *could* have gone the other way on, so per `plan-it`'s ledger grammar
they're recorded as scope/mechanism choices instead, not beyond-reach
conditions:
- **Corpus is "live-loaded docs only"** (excludes `.claude/plans/*.md` and
  `docs/reports/*`) — the user's explicit choice; see **Out of scope**
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

2. **`Workflow` tool, `pipeline()` with two stages over the 105 files** —
   audit, then fix — no barrier between them, so file A can be fixing while
   file B is still auditing. anchors: root. Lighter primitives considered:
   `parallel()` (a barrier) between the stages — rejected, no stage needs
   cross-file context, so a barrier would only add latency for every file to
   catch up to the slowest; sequential `Agent`-tool calls one at a time —
   rejected by the user's own throughput choice, and 105 serial turns is
   impractical in one session. This assumes `Workflow`'s dispatched agents
   inherit the session's already-anchored worktree cwd the same way the
   `Agent` tool does, with no `isolation: 'worktree'` passed (that flag
   would put edits in a throwaway worktree, not this branch) — per
   `CLAUDE.md`'s Agent Briefing on anchoring before a write-capable
   dispatch. [unverified] — this is exactly what the pilot run in
   Verification checks first, before the fix stage touches all 105 files.

3. **Audit stage reuses `comment-discipline-reviewer` unmodified via
   `agentType`**, overriding only its default diff-scoping through the
   per-call prompt text (not editing the agent file). Its own "How to work"
   step 3 explicitly excludes pre-existing violations by default
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

4. **Audit stage returns findings via `schema` (StructuredOutput), not the
   agent's own `findings_path` file-write mode.** anchors: root. Lighter
   primitive considered: `findings_path` per-file — rejected, would produce
   105 scratch files the fix stage would then need to re-read, where
   `schema` hands the fix stage the same data as a normal pipeline argument.

5. **Fix stage dispatches `code-writer` per file, but only when the audit
   stage found something** (`findings.length > 0`; a clean file short-circuits
   to `null` with no agent call). anchors: root. `code-writer` is this
   repo's standard code-writing dispatch — [verified: `CLAUDE.md`'s Model &
   Effort Routing, "Delegated code-writing dispatches to `code-writer`"].
   Lighter primitives considered: have `comment-discipline-reviewer` itself
   apply the fixes — rejected, its own frontmatter states it explicitly
   "does not write code" and "does not rewrite the text" itself; it names
   violations and fixes, it doesn't apply them; `general-purpose` — rejected,
   `CLAUDE.md`'s Model & Effort Routing scopes its routine use to read-only
   discovery/research now that code-writing routes through `code-writer`.
   `code-writer`'s own trigger list is framed around code ("feature code,
   bug fixes, refactors, migrations, schema, config, scripts") and doesn't
   name prose/documentation explicitly, but its charter and tools
   (Read/Edit/Write plus a self-review pass) are format-agnostic — trimming
   a flagged paragraph is "implement exactly what the dispatch prompt
   specifies," the same shape as any other targeted edit.

6. **The fix-stage prompt passes only that file's findings array (violation
   type, line, quoted text, the suggested fix) and instructs `code-writer`
   to apply exactly those fixes and nothing else** — no broader pass over
   the file. anchors: root. This keeps Scope Discipline Axis 4 (minimal,
   targeted changes) intact across 105 independently-edited files: each
   diff traces 1:1 to a named finding, not to `code-writer`'s own judgment
   about what else looks verbose.

**Per-file audit prompt** must explicitly supersede the agent's own
pre-existing-violation filter for this dispatch — e.g.: "This is a full-file
audit, not a diff review. Treat the entire current content of `<file>` as in
scope, including content that predates any recent change — your default
instruction to flag only diff-introduced/worsened violations does not apply
to this dispatch." followed by the five review angles it already knows and a
request to return every finding via the schema.

**Findings schema** (per file; the pipeline zips it with the file path from
`originalItem` before handing it to the fix stage):

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

- **Modify, up to 105 files** — every file the corpus filter (mechanism 1)
  matches that the audit stage flags at least one finding for. Each file's
  diff is scoped to exactly its reported findings (mechanism 6) — no
  wholesale rewrites.
- **Reuse, unmodified:** `claude/.claude/agents/comment-discipline-reviewer.md`
  (audit stage, `agentType` dispatch, scope overridden only through the
  per-call prompt — mechanism 3) and `claude/.claude/agents/code-writer.md`
  (fix stage, standard dispatch — mechanism 5).
- No hooks, skills, or production code are touched by this plan directly —
  the corpus is documentation (`SKILL.md`, agent bodies, `CLAUDE.md`, `docs/`,
  `REFERENCES.md`, `README.md`), not code. `/code-review`'s own per-file-type
  dispatch (`.claude/rules/review-pipeline-dispatch.md`) still applies at
  commit time to whichever `SKILL.md`/agent files land in the diff.

## Verification

- **Pilot before the full run.** `comment-discipline-reviewer`'s own
  instructions are emphatic about excluding pre-existing violations (the
  cited "How to work" step 3) — a one-paragraph prompt override may not
  reliably suppress that on every dispatch. Before running the full
  105-file pipeline, run both stages against 3–5 files already known to
  carry legacy verbosity (e.g. long-standing skill `REFERENCES.md` files):
  confirm the audit stage's findings include pre-existing paragraphs (not
  only diff-shaped ones), confirm the fix stage's edits land in this
  worktree (not the main tree), and read the resulting diffs by hand to
  confirm each edit traces to a named finding and nothing else moved. If
  either stage doesn't hold up, fix the prompt before scaling to the full
  corpus — don't discover this after 105 files.
- Re-run the `git ls-files` filter from mechanism 1 after the full run and
  diff its output against the file list the workflow actually processed —
  any mismatch (new file landed, one renamed) means the corpus drifted
  mid-run and should be re-scoped, not silently under- or over-counted.
- Run `/code-review` on the full aggregate diff before presenting it or
  committing — this is the mandatory, hook-enforced gate regardless of this
  plan, and it's the real correctness backstop here: it dispatches
  `comment-discipline-reviewer` again, in its normal diff mode, against
  every file this plan actually touched.
- If the aggregate diff is large enough that reviewing it in one pass is
  impractical, split the commit/PR by corpus category (skills, agents,
  docs, references) rather than committing all 105 as one undifferentiated
  diff — a judgment call at execution time, not a fixed rule here.

## Out of scope

- `.claude/plans/*.md` and `docs/reports/*` — excluded as historical records
  per the corpus-scope decision above.
- Any new hook or automation to prevent future recurrence — `/code-review`'s
  existing per-diff dispatch of `comment-discipline-reviewer` already covers
  that going forward; this plan is a one-time backlog sweep of what predates
  it.
