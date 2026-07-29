# GH-481: Reviewer independence is context-level, not model-level

## Context

**Goal:** correct `docs/design-decisions.md` §3, which claims the eight
specialists flag a surface "independently" and treats their convergence as a
verdict, and give the two Reconciliation blocks the discriminator they
currently lack — scoped so it adjudicates escalation only, never a finding's
survival.

Issue #481 argues that spawning eight personas with fresh context decorrelates
*reasoning contamination* but not *model-level bias* — all eight run
`model: sonnet`, so a blind spot in the base model is shared by all of them,
and convergence could equally mean the pattern is over-represented as a smell
in training data rather than wrong in this code.

The critique lands on §3 specifically. The operational skills already carry a
weaker, more honest form: both `code-review/SKILL.md:267-272` and
`plan-review/ROUTING.md:55-60` present convergence as **two competing
readings** — implementation/design-wrong-shape versus prompt-overlap artifact —
and end with "You judge which applies." §3 is the only surface that states
convergence-is-signal flatly. So the primary defect is a documentation claim
stronger than the mechanism it describes and stronger than the repo's own
operational rule.

The secondary defect is real too: "You judge which applies" hands the
orchestrator a decision with no stated basis for deciding. The issue's first
mitigation — check whether convergent findings cite *different* failure modes
or the same one — is the missing test.

**Outcome:** §3 states what the fan-out does and does not decorrelate, names
the residual it cannot fix, and defers the operational rule to the skills that
own it; the two Reconciliation blocks gain a discriminator bounded to the
escalation decision; a test enforces that the two blocks stay identical.

## Approach

Prose changes across two surfaces, one new test, plus citation entries. No
agent frontmatter, model pin, or hook changes.

### Why the model-diversity mitigation is rejected

The issue's second mitigation — route `ciso-reviewer` to a different model
family — is not adopted.

**It is not expressible as stated.** Claude Code's subagent `model:` field
accepts "`sonnet`, `opus`, `haiku`, `fable`, a full model ID (for example,
`claude-opus-5`), or `inherit`" ([Create custom
subagents](https://code.claude.com/docs/en/sub-agents), frontmatter table).
Every option is Anthropic. Cross-vendor decorrelation has no expression at this
layer.

**The achievable version is not grounded.** Kim, Garg et al., *Correlated
Errors in Large Language Models* (ICML 2025): "We identify factors driving
model correlation, including shared architectures and providers. Crucially,
however, larger and more accurate models have highly correlated errors, even
with distinct architectures and providers." Cross-provider diversity already
fails to decorrelate at the frontier; Opus 5 paired with Sonnet 5 is same
provider, same family, same generation — strictly the weakest case. Goel et
al., *Great Models Think Alike and this Undermines AI Oversight* (ICML 2025),
adds the trend: "model mistakes are becoming more similar with increasing
capabilities, pointing to risks from correlated failures."

The only lineage difference Anthropic publishes between the two is
training-data cutoff — Opus 5 at May 2026, Sonnet 5 at Jan 2026 ([Models
overview](https://platform.claude.com/docs/en/about-claude/models/overview)).
That is knowledge recency, not corpus independence. Anthropic publishes nothing
about pretraining-lineage independence between tiers.

**Record it as *not established*, not as *near-zero*.** Neither paper measures
cross-tier decorrelation benefit — exactly what ledger row 5 flags as
unpublished. §3 must say the benefit is ungrounded and the option stays
honestly re-openable if Anthropic publishes lineage data. Claiming the sources
show the benefit is near-zero would outrun them, in public stow-distributed
prose.

Goel et al. also supplies a second-order argument *for* the discriminator:
"LLM-as-a-judge scores favor models similar to the judge." The orchestrator
adjudicating reviewer findings is itself an LLM judge running the same family
as the reviewers it judges — a reason to give the step a structural test rather
than leave it to unaided judgment.

### Change 1 — `docs/design-decisions.md` §3

Keep the first two sentences (roster rationale). Replace the two convergence
sentences with four things:

1. **What the fan-out decorrelates** — each reviewer reads the diff fresh and
   sees no other reviewer's findings; reasoning contamination is genuinely
   broken.
2. **What it does not** — a shared base model, so a pattern over-represented as
   a smell in training data draws convergent flags regardless of whether it is
   wrong here. Convergence therefore opens the reconciliation test rather than
   settling it; point to the skills that own that test rather than restating it.
3. **That some convergence is prescribed, not emergent** — the Item-ownership
   tables deliberately assign one item to a primary owner plus co-owners
   (`ciso-reviewer` alone co-owns ten code-review items across nine rows:
   `SKILL.md:328, 347, 351, 356, 357, 358, 360, 361, 365` — row 347 covers two
   items), so two reviewers landing on one
   `file:line` is often the routing contract working, not independent
   corroboration.
4. **The residual this change does not fix** — reconciliation runs only over
   findings that *exist*. A shared blind spot on what all eight reviewers fail
   to flag is untouched by any test applied after they return. Reviewer silence
   is not evidence of absence.

Point 4 lands in `design-decisions.md` rather than in a skill body **by
choice**: the operator moment for it is `code-review/SKILL.md:211` ("If no
issues are found, say: 'No issues found'"), but a caveat there would fire on
every clean review to no actionable end. §3 is the altitude where a reader
asking "how much should I trust this roster?" is already looking. Naming the
choice so it reads as deliberate, not as an artifact of trimming.

Then the rejected-mitigation record and a `### Sources` block, matching the
convention in §11, §13, and §14.

**`test_doc_counts.py` constraint — the hazard is addition, not rewording.**
Two regexes anchor §3: `r"## 3\. Specialist reviewer roster \((\d+)
personas\)"` and `r"(\w+) stack-specific agents \(CISO,"`
(`claude/.claude/hooks/tests/test_doc_counts.py:194-203`). Both target text the
plan *keeps*, so they provide zero validation of the rewrite — a green pytest
does not mean the new prose is right. The real failure vector is that the
assertion is **exactly one match per file** (`test_doc_counts.py:16-19`).
A third pattern in the same fact block, `r"All (\w+) reviewer agents write
structured"` (`:204-208`), anchors §12 rather than §3 — but it is
file-scoped, and §3's new point 3 and point 4 both discuss the full roster, so
it is in play too. **Required property: the new prose introduces no second
match for any of the three `docs/design-decisions.md` occurrence patterns.**

### Changes 2 and 3 — both Reconciliation blocks

`code-review/SKILL.md` and `plan-review/ROUTING.md` get the same text. The
blocks are near-identical today and stay duplicated by design — no shared
partials (root `CLAUDE.md`; `design-decisions.md` §4).

**Reconciliation owns escalation, not survival.** This scoping is the whole
design and every other property follows from it. Reconciliation's output is one
question: *does this convergence justify replacing the surface and re-running
Step 1 (code-review) / Step 4 (plan-review)?* Whether a finding survives is
adjudicated downstream. Writing the discriminator as a verdict on findings
leaks into three other decisions at once: it becomes a spawn-skip rationale, it
collides with prescribed co-ownership, and in `plan-review` it would land
terminally because that pipeline has no disposition station at all. Bounding it
to escalation dissolves all three rather than requiring a patch for each.

**Ordering is load-bearing.** The block reads: pause/attribution sentence →
**collapsing rule** → the two readings → discriminator → branch actions. The
collapsing rule must come *before* the readings and must use the literal word
**skip**. If it lands after the discriminator, an orchestrator who has already
read "one failure mode in N voices does not support escalation" has formed a
skip rationale and no reason to keep reading.

**The collapsing rule:** a reconciliation reading never removes a finding,
never changes how it is dispositioned downstream, and is never a reason to skip
a spawn. Every converged finding proceeds exactly as it would have. Word it
**station-agnostically** — never cite `ADDRESS`/`DEFER`, which exist only in
`code-review` (ledger row 10); a station name here would dangle in `ROUTING.md`
and force a third normalization token in the drift test.

**Rename the artifact bullet.** `Prompt-overlap artifact` becomes a label
covering both causes — *correlated-reviewer artifact* — with prompt overlap and
shared-model prior named underneath it, and the tighter-prompts next-round
clause attached only to prompt overlap (tighter prompts do not fix a shared
prior). The label is what an orchestrator classifies into: leaving it as
"prompt-overlap" while shared-prior convergence hides inside means an
orchestrator matching observation-to-label finds neither bullet fits and falls
through to *wrong-shape* → escalation, inverting issue #481's entire point.

**Two readings, not three.** An earlier draft added shared-model prior as a
co-equal third bullet. Folding it in costs the orchestrator one fewer
classification on every review of the repo's highest-traffic dispatcher, and
the genuine delta is one clause. Length is not the constraint (389 of a 500
cap — `check-skill-length.sh:55` sets the limit, `:67` fires the gate only when
`new > limit && new > old`); classification load is.

**The discriminator**, replacing the bare "You judge which applies": read what
each convergent finding *names as the failure*. Distinct failure modes on one
surface (a lock-budget risk, a consumer-contract break, a missing rollback)
support escalation — the surface is load-bearing in several directions. One
failure mode in N voices, or findings that **fail to name a consequence
traceable in this code**, do not. Phrase it as failure-to-name-a-consequence,
**not** as "cites a pattern name": every reviewer's mandated output format
requires a checklist-item or angle label (`code-review/SKILL.md:205`,
`plan-review/SKILL.md:230`), so a pattern-name test would tilt systematically
against findings formatted exactly to spec.

**Prescribed co-ownership disclaims independence — it does not disqualify
escalation.** Convergence the Item-ownership table itself prescribes carries no
*independent-corroboration* weight, because the routing contract put both
reviewers there. It is still escalation-eligible on the failure-mode test's own
terms: `staff-data-engineer` flagging an RLS object as unenforceable and
`ciso-reviewer` flagging the same line as a cross-tenant read path are two
distinct failure modes and should escalate. An earlier draft excluded prescribed
convergence from escalation "either way," which would have zeroed the signal
across roughly eleven CISO-co-owned items and nowhere else — a security-shaped
hole. The narrower disclaimer defers to the failure-mode test already in the
rule rather than adding a second mechanism.

**Disposition of the existing "skip duplicates" sentence** (`SKILL.md:272`,
`ROUTING.md:60`): it is rewritten, not kept. As written it is in literal
tension with "never removes a finding." Replace with wording that preserves the
existing guarantee one line above — "present the finding once with both
reviewer attributions" — so deduplication merges presentation without dropping
either reviewer's distinct framing.

### Change 4 — drift test

New `claude/.claude/hooks/tests/test_reconciliation_block_consistency.py`,
modeled on `TestFileBasedOutputBlockConsistency`
(`test_agent_roster.py:156-244`). The plan grows an intentionally duplicated
block whose added content is multi-sentence judgment prose — far easier to
paraphrase apart than the current four lines — so the enforcing test ships with
it.

Four properties, each closing a specific way the template does *not* transfer
unchanged:

1. **Both casings per token pair.** The wrong-shape token appears capitalized
   in the bullet (`**Implementation-wrong-shape.**`, `SKILL.md:269`) and
   lowercase in the branch sentence (`If implementation-wrong-shape,`,
   `SKILL.md:272`); same in `ROUTING.md:57` and `:60`. Normalizing only the
   capitalized form leaves the branch sentence divergent, and the
   assert-before-replace guard still passes — reproducing exactly the confusing
   diff the guard exists to prevent.
2. **Heading-bounded, not literal-sentinel-bounded.** The template's
   `_BLOCK_END_SENTINEL` is a literal sentence, but here the section's last line
   carries the per-file `Step 1`/`Step 4` token and the following heading differs
   (`## Finding disposition` vs `## Item ownership`), so no literal sentinel
   works for both. Bound extraction at the next line matching `^## `
   (exclusive), and **assert a terminating heading was found** rather than
   falling off EOF — otherwise a section relocated to end-of-file passes
   silently.
3. **Per-file token map.** The template derives one token from `path.stem`; here
   two files carry different maps and `re-run Step 4` does not exist in
   `SKILL.md`. Use `{path: {token: canonical}}` with each token asserted present
   only in its own file, or the shared-list assert fails spuriously and an
   implementer drops the guard.
4. **Presence, not only equality.** Byte-equivalence passes cleanly if a future
   edit deletes the collapsing rule and discriminator from *both* files. Assert
   the collapsing-rule sentence and the failure-mode-distinctness clause are
   present in the extracted block, so quiet symmetric removal fails too
   (`test-conventions` §5, regression-test intent).

This is the gap that would otherwise go unwatched, because **`ROUTING.md` sits
outside every mechanical gate**: `check-skill-length.sh:72-74` greps
`SKILL\.md` only, and `plugins/skill-management/hooks/require-skill-review.sh`
scopes both its early-exit and its marker hash to `**/SKILL.md` (`:74`, `:185`).
The `ROUTING.md` half rides along on the `SKILL.md` marker and is independently
gated by nothing — so drift enters through the file nothing watches.

### Change 5 — citation pointers

`code-review/REFERENCES.md` and `plan-review/REFERENCES.md` each get a short
entry recording what grounds the discriminator, pointing at §3's `### Sources`
rather than restating quotes. `REFERENCES.md` is the edit-time co-located
reference (root `CLAUDE.md`), so a future editor asking "why is this
discriminator here?" finds the answer without the citations duplicating three
ways.

### Assumption ledger

**Root problem:** §3 asserts an independence property the eight-persona fan-out
only partly has, and the operational test that claim leans on has no stated
basis for deciding.

| # | Assumption | Tag |
|---|---|---|
| 1 | Subagent `model:` accepts only Anthropic aliases/IDs/`inherit` — cross-vendor is inexpressible | `[verified: code.claude.com/docs/en/sub-agents frontmatter table]` |
| 2 | Frontier models show highly correlated errors even across distinct architectures and providers | `[verified: Kim, Garg et al., ICML 2025, arXiv:2506.07962 abstract]` |
| 3 | Error similarity rises with capability; LLM judges favor models similar to themselves | `[verified: Goel et al., ICML 2025, arXiv:2502.04313 abstract]` |
| 4 | Opus 5 and Sonnet 5 are one family/generation; only published lineage delta is training-data cutoff (May 2026 vs Jan 2026) | `[verified: platform.claude.com models overview comparison table]` |
| 5 | Anthropic publishes nothing on pretraining-lineage independence between tiers; neither cited paper measures cross-tier benefit | `[unverified — absence of evidence. §3 must say "not established," never "near-zero"]` |
| 6 | Three `test_doc_counts.py` occurrence patterns are file-scoped to `design-decisions.md` (two in §3, one in §12); the assertion is exactly-one-match-per-file | `[verified: test_doc_counts.py:16-19, 194-208]` |
| 7 | `code-review/SKILL.md` cap is 500, currently 389; gate fires only when `new > limit && new > old` | `[verified: check-skill-length.sh:55 (limit), :67 (gate), :72-74 (staged-file grep)]` |
| 8 | Both Reconciliation blocks are intentionally duplicated, so both get the same text | `[verified: root CLAUDE.md no-shared-partials; design-decisions.md §4]` |
| 9 | `ROUTING.md` is covered by no length cap and no independent review marker | `[verified: check-skill-length.sh:72-74; plugins/skill-management/hooks/require-skill-review.sh:74,185]` |
| 10 | `plan-review` has no ADDRESS/DEFER disposition station | `[verified: grep ADDRESS\|DEFER across plan-review/ returns only ROUTING.md:20's contrastive reference]` |
| 11 | The collapsing rule is *sufficient* to make that absence safe | `[unverified — claim of sufficiency, not established by row 10's grep. See "Stated limits" below]` |
| 12 | `ciso-reviewer` co-owns 10 code-review items across 9 rows, so a blanket co-ownership exclusion would be security-shaped | `[verified: code-review/SKILL.md:328, 347, 351, 356, 357, 358, 360, 361, 365]` |
| 13 | Scope is six files: §3, both Reconciliation blocks, the drift test, and two `REFERENCES.md` entries; no case-study file | `[engineer-verified — original three-file scope widened to six on the user's explicit confirmation after review rounds surfaced the drift-test gap]` |
| 14 | The model-pairing question was to be settled by source verification, not assumed | `[engineer-verified]` |

**Mechanism justification.** The prose changes are the lightest primitive
available — edits to the files the defect lives in (anchors: root). The drift
test is the repo's existing pattern for this exact problem, not a new mechanism
(anchors: rows 8, 9). Two heavier alternatives were weighed and rejected:
pinning a reviewer to a different model, on rows 1–5; and a hook enforcing the
discriminator at reconciliation time, which has no tool-call boundary to attach
to — reconciliation is a reasoning step, the same reason `design-decisions.md`
§11 gives for routing staying advisory.

## Critical files

| File | Change |
|---|---|
| `docs/design-decisions.md` | §3 convergence paragraph rewritten (4 points above); `### Sources` block added |
| `claude/.claude/skills/code-review/SKILL.md` | Reconciliation (~L267-272): collapsing rule first, renamed artifact bullet, escalation-scoped discriminator, rewritten branch sentence |
| `claude/.claude/skills/plan-review/ROUTING.md` | Reconciliation (~L55-60): same text |
| `claude/.claude/hooks/tests/test_reconciliation_block_consistency.py` | **New** — byte-equivalence + presence of the two Reconciliation blocks |
| `claude/.claude/skills/code-review/REFERENCES.md` | Discriminator-grounding entry pointing at §3 |
| `claude/.claude/skills/plan-review/REFERENCES.md` | Same entry |

**Reuse:** `TestFileBasedOutputBlockConsistency` (`test_agent_roster.py:156-244`)
is the template for the new test — sentinel-bounded extraction and
assert-before-replace normalization; adapt per Change 4's four properties rather
than hand-rolling a different shape. The `### Sources` block format is
established in `design-decisions.md` §11, §13, §14. Apply identical wording to
both Reconciliation blocks so the new test passes by construction.

## Verification

Run from the worktree (the `.venv` lives at the main worktree root only, three
levels up):

1. `../../../.venv/bin/pytest claude/.claude/` — must pass. Load-bearing:
   the new `test_reconciliation_block_consistency.py`, and `test_doc_counts.py`
   (no *second* match introduced for any of the three `design-decisions.md`
   patterns — see Change 1).
2. `../../../.venv/bin/ruff check claude/.claude/` — the new test file is the
   only Python added. No shell changes, so `shellcheck` is unaffected.
3. `wc -l claude/.claude/skills/code-review/SKILL.md` — confirm under 500.
4. `/skill-review` — **hook-enforced**; `require-skill-review.sh` blocks
   `git commit` until a marker covers the `code-review/SKILL.md` diff. It does
   *not* cover the `ROUTING.md` half (ledger row 9); the new test does.
5. `/code-review` — dispatches `/skill-review` per
   `.claude/rules/review-pipeline-dispatch.md`. This diff changes the
   reconciliation rule every persona's findings flow through, so the
   "Reshapes reviewer ownership" row must be weighed and the Spawn decisions
   output must record the read either way.
6. No `plugin-semver` bump — nothing under a plugin directory changes.

### Stated limits — recorded, not solved

- **No mechanism verifies the discriminator improves reconciliation
  *decisions*.** `/skill-review` audits behavioral equivalence of edited prose,
  and `code-review/evals/trigger-cases.json` is trigger-classification only
  (`should_trigger` booleans). There is no behavioral eval surface for
  skill-body reasoning steps in this repo. Step 4 does not cover it.
- **The collapsing rule is a negative constraint only** (ledger row 11).
  `code-review/SKILL.md:276` supplies a positive counterpart — "walk *every*
  reviewer-spawned finding and tag it" — and `plan-review/SKILL.md` has no
  equivalent, so a converged finding could go simply un-enumerated in
  plan-review output without violating any rule. That gap pre-exists this
  change and this change does not widen it, but the collapsing rule does not
  close it either. Listed under Out of scope.

## Out of scope

- Any model pin change: `ciso-reviewer.md` frontmatter, the CLAUDE.md Model
  Routing rule, and `NON_REVIEWER_MODELS` (`test_agent_roster.py:68`) are
  untouched. §3 records the rejection as *not established*, keeping it
  re-openable.
- **A positive finding-enumeration requirement in `plan-review/SKILL.md`'s
  Output format** — the fix for ledger row 11's gap. Pre-existing (the
  Reconciliation rewrite narrows this gap — it replaces `ROUTING.md:60`'s
  affirmative "skip duplicates" drop-license with a never-removes-a-finding
  rule — but does not close it), and orthogonal to this ticket; filing it here
  would put a seventh file in scope. Filed as
  [jcdendrite/claude-config#503](https://github.com/jcdendrite/claude-config/issues/503).
- A length cap or independent review marker for `ROUTING.md` (ledger row 9).
  Real gap, wider than this ticket. Filed as
  [jcdendrite/claude-config#504](https://github.com/jcdendrite/claude-config/issues/504).
- A `docs/case-studies/` writeup. `review-vs-babysitting.md:70` records a
  fitting instance (CISO and SDET converged on the same `-ge 0` guard; CISO's
  sign analysis was wrong and its recommendation would have introduced a bug),
  but one instance does not warrant a new doc surface, and the user scoped it
  out.
- The roster size, §9's persona-roster operations, and the eight agent bodies.

## Review surface

Six files: five prose, one new test. One domain (Claude Code config). Risk concentrates in
`code-review/SKILL.md`, the repo's highest-traffic dispatcher, edited in the
block every reviewer finding passes through; and in §3, where the
exactly-one-match doc-count assertion constrains what the new prose may
restate. The new test is low-risk but has four non-obvious spec properties
(Change 4) that a template-copy would miss.
