# Plan: encode a "question the foundation before accepting a localized patch" instinct

## Context

An error-mode analysis (de-identified from a real client case,
`local-optimum-acceptance-issue.md` in Google Drive) names a failure mode the
current config does not cover: the agent ships a solution that is *locally*
correct and passes every automated and reviewer-agent gate, without questioning
whether the **foundation** it patched — a construct's placement, a label's
semantics, a type's shape — is itself right. The correction only arrives when a
human asks "why is this shaped this way?". Three instances appeared in one
delivery (a misplaced helper accepted because its call site validated it; a
mislabeled generic log tag corrected only incidentally; a localized cast
accepted instead of the upstream type fix that would dissolve it).

The observation frames this as the **un-covered opposite** of two instincts
already in global CLAUDE.md — *"Compounding defensive layers are a
wrong-foundation tell"* and *"Default-suspect over-powered primitives"* — both of
which guard the *over-building* direction. This failure mode is the
*under-questioning* direction. The observation explicitly defers the *where does
the guardrail live* decision to a claude-config session (this one).

**Intended outcome:** add a single engineering-judgment instinct to the global
stowed CLAUDE.md so the authoring agent questions a localized patch's foundation
at the moment it reaches for the patch — the decision point where the failure
actually happens. Confirmed scope (user): encode now, one instinct, CLAUDE.md
only.

## Approach

Add **one bullet** to `claude/.claude/CLAUDE.md` §Engineering Judgment, placed
in the existing wrong-foundation cluster (immediately after *"Audit structural
siblings before scoping a fix narrowly."*, `CLAUDE.md:11`). No new reviewer
agent, no new `code-review` checklist item, no `code-writer` baseline line.

**Why this surface, and why nothing more.** A single §Engineering Judgment
bullet propagates into both the authoring path and the review path through
references that already exist — so the DRY single-source-of-truth home is the
instinct itself, and the pipeline picks it up for free:

- `code-writer.md:55` — the authoring baseline tells the agent to let
  "CLAUDE.md §Engineering Judgment and §Working Style actively steer choices" at
  each decision point.
- `code-writer.md:81` — the mandatory self-review re-reads the diff "against
  CLAUDE.md §Engineering Judgment and §Working Style."
- `code-review/SKILL.md:47-54` — Step 1.5 (Judgment-activation pass) evaluates
  the diff against the same two sections.

Restating the instinct as its own `code-review` checklist item or `code-writer`
baseline line would duplicate the text across surfaces (a DRY defect this repo
treats as a defect absent a named exception) and would add review machinery on
one-window evidence — itself the over-powered-primitive / compounding-layers
move the subject instinct warns against. Proportionate response to a
"candidate-to-watch" finding is the lightest primitive that reaches the decision
point: one bullet.

### Draft instinct text (final wording vetted in verification)

Calibrated to sibling length: the two closest bullets (`CLAUDE.md:10,11`) run
2–3 sentences with a parenthetical example list and no lineage framing. Draft
matches that shape — two sentences, one trigger-example list, self-contained:

> **A locally-valid patch can signal a wrong foundation.** When you reach for a
> localized escape-hatch — a cast to fix a type mismatch, a helper parked where
> its one call site happens to live, a label scoped to where the symptom
> surfaced — treat it as a hypothesis, not a solution: check whether a change one
> level up (the upstream type, the call site's own placement, the canonical name)
> dissolves the need for it at a smaller overall diff. You do not need layers to
> compound before questioning the foundation — one patch that passes every gate
> is signal enough.

**What stays out of the bullet (moves to the commit message).** Two things that
were bloating the draft are rationale/lineage, not instruction — per this repo's
"move the rationale to the commit message" rule (`CLAUDE.md` §Code Comments →
*Self-test: content must survive the PR description being lost*), they go in the
commit message, not the load-every-session bullet:

- **Lineage** — that this instinct is the "under-questioning complement" of
  *Compounding defensive layers* / *Default-suspect over-powered primitives*, and
  that it was surfaced by an error-mode analysis. Useful provenance for the
  reviewer of *this* PR; dead weight for every future session reading the bullet.
- **Efficacy caveat** — the instinct's primary bite is at **authoring time** (the
  moment the agent inserts the cast/helper/label). The `code-review` Step 1.5
  pickup is a weaker secondary net: its tripwires "fire on diff surface, not on
  internal reasoning" (`code-review/SKILL.md:49`), and a locally-valid patch is
  exactly what does *not* show on diff surface. We are not fixing that limitation
  this round — doing so would be the review-machinery expansion this plan
  declines. This belongs in the commit message as a known-limitation note.

Alternatives weighed and set aside: (a) **watch-item only** — rejected by the
user in favor of encoding now, since the cost is ~one bullet and the shape is a
coherent complement of two already-encoded rules; (b) **fold into "Audit
structural siblings"** — that instinct governs *horizontal* scope (other arms of
the same structure); this one governs *vertical* scope (the foundation one level
up). Folding would muddy both; a separate bullet is cleaner; (c) **place in
§Working Style beside "Compounding defensive layers"** — §Engineering Judgment is
the better home because the instinct is judgment on fix altitude, and its two
closest siblings ("Audit structural siblings", "Default-suspect over-powered
primitives") already live in §Engineering Judgment.

## Critical files

- `claude/.claude/CLAUDE.md` — **modify.** Insert the one bullet after line 11
  (`Audit structural siblings…`), inside §Engineering Judgment. This is the only
  file changed. It is stowed to `~/.claude/CLAUDE.md`, so the change goes live
  for every stow user on `git pull` — the instinct is fully generic (no vendor,
  stack, or client tokens), which is required for a global stowed surface.

Reuse / do-not-touch:
- **Do not** edit `code-writer.md` or `code-review/SKILL.md` — their existing
  §Engineering Judgment references (cited above) are the reuse mechanism; adding
  a name-check for the new bullet there is optional polish that risks
  duplication, so it stays out of scope.
- **Do not** edit the two sibling instincts (`CLAUDE.md:10`, `CLAUDE.md:25`) —
  the new bullet references them by their bolded lead phrases; those phrases are
  the stable anchor.

## Verification

1. **`ai-instruction-and-memory-files` skill review** (mandatory, and where a
   CLAUDE.md-only diff routes). Running `/code-review` on a CLAUDE.md-only change
   dispatches to `ai-instruction-and-memory-files` per the dispatcher
   (`code-review/SKILL.md:171`) and the skill's own trigger. It owns placement
   (right section), altitude, duplication vs the two siblings, length cap, and
   the behavior test — vet the final wording against it and tighten if flagged.
2. **De-identification / redaction check.** Confirm the instinct and commit
   message carry no client, project, or stack identifiers — the observation is
   already de-identified ("domain X", "shared layer Y"); the instinct must stay
   fully generic. `deny-private-project-refs.sh` fires on commit as the backstop.
3. **Reference integrity read.** Grep the final CLAUDE.md to confirm the two lead
   phrases the new bullet cites — "Compounding defensive layers" and
   "Default-suspect over-powered primitives" — still appear verbatim, so the
   cross-references resolve.
4. **Test suite + lint** (fast, catches nothing behavioral here but is the repo's
   standard gate): `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` from the worktree.

## Out of scope

- Any change to `code-review` checklist items, `code-writer` baseline, or a
  reviewer agent (declined: DRY + proportionality).
- Fixing Step 1.5's "diff-surface-only" limitation for foundation-altitude
  findings (would be the review-machinery expansion this plan deliberately
  avoids on one-window evidence).
- The §Working Style placement of the existing "Compounding defensive layers"
  instinct (arguably it belongs in §Engineering Judgment, but that is not this
  ticket).
