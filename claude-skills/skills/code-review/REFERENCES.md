# References

Canonical sources that informed the rules in `SKILL.md`. Not loaded at
runtime — read this when editing the skill to verify a rule still
holds or to ground a new one.

## Tripwire → CLAUDE.md principle mapping

The items below operationalize named principles from CLAUDE.md. This table
lives here (edit-time reference) rather than in SKILL.md (runtime-loaded body)
because it is design provenance for a skill editor, not an instruction that
changes review behavior.

| SKILL.md item | Canonical CLAUDE.md principle |
|---|---|
| Step 1.5 — Out-of-scope file edits | §Working Style — Scope discipline Axis 1 (file-boundary rule) |
| Step 1.5 — Preserved-record edits | §Working Style — Scope discipline Axis 3 (preserved-content exception) |
| Item 9 — Repeated in-house logic | §Engineering Judgment — Single source of truth (DRY governs knowledge) |
| Item 9a — Repeated domain discriminants | §Engineering Judgment — Ground every choice (discriminator literals where a canonical symbol exists) |
| Item 9c — Ungrounded numeric literal | §Engineering Judgment — Ground every choice (numeric literals in network/timeout/retry contexts) |
| Item 9d — Suppression without rationale | §Engineering Judgment — Ground every choice (inline lint/type-check suppressions) |
| Item 9e — New dependency without provenance | §Engineering Judgment — Ground every choice (new third-party dependencies) |

## Reconciliation

The escalation-only discriminator in the Reconciliation section — what
convergence does and does not decorrelate, and why prescribed co-ownership
disclaims independence without disqualifying escalation — is grounded in
`docs/design-decisions.md` §3's `### Sources` block. See that block for the
citations rather than restating them here.

## Finding disposition

### Default ADDRESS / opportunistic refactoring

**Martin Fowler — *Opportunistic Refactoring***
<https://martinfowler.com/bliki/OpportunisticRefactoring.html>

Key passages:

> "at any time someone sees some code that isn't as clear as it should
> be, they should take the opportunity to fix it right there and then
> — or at least within a few minutes."

> "Sometimes you see an opportunity when you're in the middle of
> something else. Rather than interrupt your current thought it's
> useful to make a note of it and come back to it when you are ready.
> Don't leave it for long, come back the same day, before you've hit
> that final point of being done."

> "Refactoring does depend on having a good regression suite."

> "There is a genuine danger of going down a rabbit hole here, as you
> fix one thing you spot another, and another, and before long you're
> deep in yak hair. Skillful opportunistic refactoring requires good
> judgement, where you decide when to call it a day."

Grounds the "Default ADDRESS" paragraph, criterion #1's
"tests already running" condition, and the 3+-DEFER smell test.

- **Criterion 1 "Orthogonal scope" touch/activate guard** — grounds in CLAUDE.md §Engineering Judgment "Audit structural siblings before scoping a fix narrowly" ("scope is set by the bug, not by where the symptom surfaced") and "Prove your change caused a failing check" (a change activating a latent bug makes it in-scope).

### Pre-existing problems and ticket discipline

**Google Engineering Practices — *What to look for in a code review***
<https://google.github.io/eng-practices/review/reviewer/looking-for.html>

Key passage:

> "encourage the author to file a bug and add a TODO for cleaning up
> existing code."

> "Don't accept CLs that degrade the code health of the system."

> "Encourage developers to solve the problem they know needs to be
> solved *now*, not the problem that the developer speculates *might*
> need to be solved in the future."

Grounds criterion #2's ticket-filing requirement when DEFER is
applied to pre-existing structural debt.

### Reviewer disposition framing

**Google Engineering Practices — *The Standard of Code Review***
<https://google.github.io/eng-practices/review/reviewer/standard.html>

Key passage:

> "In general, reviewers should favor approving a CL once it is in a
> state where it definitely improves the overall code health of the
> system being worked on, even if the CL isn't perfect."

Frames the bias toward ADDRESS without requiring perfection — every
finding gets disposed, but disposition includes ADDRESS-with-grouping
into this PR vs follow-ups.

### Author response to review concerns

**Google Engineering Practices — *Handling reviewer comments***
<https://google.github.io/eng-practices/review/developer/handling-comments.html>

Key passages:

> "If a reviewer says that they don't understand something in your
> code, your first response should be to clarify the code itself."

> "Writing a response in the code review tool doesn't help future code
> readers, but clarifying your code or adding code comments does help
> them."

> "if you understand the comments but disagree with them, it's
> important to think collaboratively, not combatively or defensively."

Grounds the principle that disposition discipline pushes toward
ADDRESS-via-code-change rather than ADDRESS-via-explanation-only — a
reviewer's "this is confusing" produces a code clarification, not a
reviewer-thread comment dismissing the concern.

## Reviewer read methodology

Primary sources for the reviewer entry-read rule proposed in
`.claude/plans/code-file-size-splits.md`'s "Reviewer read methodology:
recommendation" section (implemented by that plan's child issue B). Not yet
reflected in this skill's rules — read this to ground that rule when it lands.

### Whole-file reads are the conditional case, not the default

**Google Engineering Practices — *What to look for in a code review*, "Context"**
<https://google.github.io/eng-practices/review/reviewer/looking-for.html>

> "Usually the code review tool will only show you a few lines of code
> around the parts that are being changed. Sometimes you have to look at
> the whole file to be sure that the change actually makes sense."

Not yet verified: the rest of this section, past the quoted sentences,
continues to whole-system code health — check before citing further.

### Understanding a small, unfamiliar change needs more context than the diff shows

**Bacchelli & Bird, *Expectations, Outcomes, and Challenges of Modern
Code Review*, ICSE 2013**

> "When reviewing a small, unfamiliar change, it is often necessary to
> read through much more code than that being reviewed."

### Diff-only trades recall for precision — grounds what the rule rejects

**Anthropic's code-review plugin**, bug-finding subagent instructions:

> "Focus only on the diff itself without reading extra context." and "Do
> not flag issues that you cannot validate without looking at context
> outside of the git diff."

This skill's own scope rule — "A defect outside the boundary that the
change causes, activates, or newly reaches stays in scope" — takes the
opposite duty, so this source grounds the rejected alternative, not the
adopted rule.

### Security review already traces beyond the diff

**Anthropic's security-review skill:**

> "Trace data flow from user inputs to sensitive operations" and "Use
> the repository exploration tools to understand the codebase context".

### Call-graph reach should follow a question, not a fixed hop count

**Pascarella et al., *Information Needs in Contemporary Code Review*, CSCW 2018**

Reviewers "may reconstruct the invocation path of a given function to
understand the impact" — ranked below other information needs in that
paper. No source found sets a fixed hop count.

### Large-file entry unit: git's own function-context diff

**`git help gitattributes`, "Defining a custom hunk-header" section**,
and `git diff -W` / `--function-context`:

> "Show whole function as context lines for each change."

Without a `diff=<driver>` attribute, git's default hunk-header rule
treats only a line starting with a letter, `_`, or `$` as a function
header — an indented method is not one.

### Not yet verified — check before citing

Leads noted during research but not fetched as of this section's
authoring (2026-09-25):
- Liu et al., "Lost in the Middle" (long-context attention degradation).
- Anthropic's context-engineering guidance on just-in-time context loading.

### Read-tool per-call token cap

Claude Code's Read tool caps a single call by tokens, not lines — a
whole-file read beyond the cap returns a `PARTIAL view` notice instead
of the rest of the file. One live default Read in this repo measured
the cap once, against
`claude/.claude/scripts/tests/test_transcript_reviewer_yield.py` (1,935
lines): the tool returned lines 1–956, with the notice `[Truncated:
PARTIAL view — <path>: showing lines 1-956 of 1935 total (43009 tokens,
cap 25000). ...]`.

- The cap is 25,000 tokens.
- That file averages about 22 tokens per line, predicting roughly
  1,100–1,125 lines per page. The observed page held 956 lines, about
  15% fewer, for an unexplained reason (candidates: per-page overhead,
  uneven density).
- This is one sample — token density varies by file, so ~1,000 lines is
  a rough predictor of where the cap falls, not a fixed boundary.

Source: `code.claude.com/docs/en/tools-reference` "Read tool behavior",
fetched by a `verify-sources` subagent 2026-09-25; re-verify by
2026-12-25, since this is the harness's own tool behavior rather than a
versioned spec — plus the live Read result quoted above.

Cited from `docs/design-decisions/code-file-line-limit.md` as a
feasibility coincidence, not a quality threshold: this cap is the Read
tool's own window, not evidence that 1,000 lines is where defects
start.
