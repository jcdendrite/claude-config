# GH-503: Positive finding-enumeration requirement for plan-review's Output format

## Context

**Goal:** give `plan-review/SKILL.md`'s Output format section a positive
requirement that every finding a spawned reviewer returns actually appears in
the rendered review output — the counterpart `code-review/SKILL.md`'s Finding
disposition step already supplies for code-review.

GitHub issue #503 (filed as an explicit out-of-scope item during review of the
now-merged GH-481 Reconciliation-discriminator change) observes that
`plan-review/SKILL.md`'s Output format section specifies formatting for
findings that do get listed ("For each finding, state: ...") but never
requires the *set* of findings in the rendered output to equal what spawned
reviewers actually returned. I verified this directly against the current
`main` tip (after GH-481 merged): the Output format section (`SKILL.md:225-240`)
has no such clause, while `code-review/SKILL.md:274-276`'s Finding disposition
step mandates walking "**every** reviewer-spawned finding" and tagging it
ADDRESS or DEFER. A converged or any other finding from a spawned
plan-review specialist could be silently dropped from the final output with
no rule in `plan-review/SKILL.md` violated.

Now is a good time because GH-481 just strengthened the *adjacent* rule —
`ROUTING.md`'s Reconciliation block now states a reconciliation reading "never
removes a finding, never changes how it is dispositioned downstream, and is
never a reason to skip a spawn" — without closing this gap; that block governs
escalation-vs-survival for *convergent* findings, a different question from
whether every *returned* finding lands in the output at all. **Outcome:**
Output format gains a positive enumeration requirement, sized to plan-review's
own pipeline rather than importing code-review's disposition machinery.

## Approach

Add one paragraph to `plan-review/SKILL.md`'s Output format section, placed
after the spawned-specialists listing sentence and before "For each finding,
state:". It requires cross-checking the assembled findings against what each
spawned reviewer returned, and treats a finding merged under Reconciliation's
existing dedup rule (`ROUTING.md`) as present only when both reviewers are
attributed at the merged entry — reusing that rule's language ("present the
finding once with both reviewer attributions") rather than restating it.

**Alternatives considered:**

1. **Import code-review's full ADDRESS/DEFER disposition station.** Rejected:
   plan-review has no disposition step to hang tags on — a grep for
   `ADDRESS|DEFER` across `plan-review/` returns only `ROUTING.md`'s
   contrastive reference to code-review's stations (also recorded in GH-481's
   own ledger row 10). The actual defect is a finding going unlisted, not a
   missing ADDRESS/DEFER taxonomy; building the taxonomy to fix the listing
   gap is a heavier mechanism than the gap requires.
2. **Add a dedicated drift/presence test**, mirroring
   `test_reconciliation_block_consistency.py`. Rejected: that test exists
   because `ROUTING.md` sits outside every mechanical gate that would
   otherwise catch drift (`check-skill-length.sh` greps `SKILL\.md` only;
   `require-skill-review.sh` scopes its marker to `**/SKILL.md`) and its
   content is duplicated across two files. This change lives entirely inside
   `SKILL.md` — a single, non-duplicated location already covered by both of
   those gates. Code-review's own analogous Finding disposition sentence
   (`SKILL.md:274-276`) has no dedicated presence test either; a grep across
   `claude/.claude/hooks/tests/` for "Finding disposition" or "walk every
   reviewer-spawned finding" returns no hits, so a new test here would exceed
   the repo's own precedent for this content class.
3. **Edit `ROUTING.md`'s Reconciliation block instead.** Rejected: that block
   already governs escalation-vs-survival for convergent findings (per
   GH-481's explicit scoping — "Reconciliation decides escalation only, never
   a finding's survival"). The gap this issue names is a different question —
   whether the *returned* set equals the *rendered* set — and belongs in
   Output format, where the issue itself locates it.

### Assumption ledger

**Root problem:** `plan-review/SKILL.md`'s Output format has no positive
requirement that every spawned reviewer's returned finding appear in the
rendered output.

| # | Assumption | Tag |
|---|---|---|
| 1 | Output format section (`SKILL.md:225-240`) has no enumeration/completeness clause today | `[verified: Read of claude/.claude/skills/plan-review/SKILL.md this session]` |
| 2 | code-review's Finding disposition step supplies the positive counterpart: "walk every reviewer-spawned finding and tag it ADDRESS or DEFER" | `[verified: Read of claude/.claude/skills/code-review/SKILL.md:274-276 this session]` |
| 3 | plan-review has no ADDRESS/DEFER disposition station | `[verified: grep for ADDRESS\|DEFER across claude/.claude/skills/plan-review/ returns only ROUTING.md:20's contrastive reference]` |
| 4 | GH-481 already merged; `ROUTING.md`'s Reconciliation block now carries the "never removes a finding... never a reason to skip a spawn" collapsing rule | `[verified: Read of claude/.claude/skills/plan-review/ROUTING.md:53-60 in this worktree, anchored at origin/main tip]` |
| 5 | That collapsing rule governs escalation-vs-survival only and has no enumeration/completeness clause | `[verified: same Read — no such clause in the block's text]` |
| 6 | `plan-review/SKILL.md` is capped at 500 lines (per-skill override), currently 256 — a 2–3 line addition has ample headroom | `[verified: check-skill-length.sh:52-58 limit_for(); wc -l claude/.claude/skills/plan-review/SKILL.md]` |
| 7 | No existing test enforces textual presence of code-review's analogous Finding disposition sentence | `[verified: grep for "Finding disposition" and "walk every reviewer-spawned finding" across claude/.claude/hooks/tests/ returns no hits]` |

**Mechanism justification.** The chosen primitive — one requirement sentence
in Output format that references the existing Reconciliation dedup rule
rather than restating it — is the lightest mechanism that closes the gap
(anchors: root, rows 1–2). A full disposition station was rejected as
heavier than the gap requires, since plan-review's pipeline has no
disposition step to attach tags to (anchors: row 3). A dedicated presence
test was rejected because the target location is already covered by two
mechanical gates `ROUTING.md` lacks, and code-review's own analogous sentence
carries no such test either (anchors: rows 6–7).

## Critical files

| File | Change |
|---|---|
| `claude/.claude/skills/plan-review/SKILL.md` | Output format section: add one paragraph after the spawned-specialists listing sentence, before "For each finding, state:" — requiring every spawned reviewer's returned finding to appear in the rendered output, with Reconciliation-merged findings counted present only when both reviewers are attributed |
| `claude/.claude/skills/plan-review/REFERENCES.md` | Add a short entry recording why the requirement exists and its relationship to code-review's Finding disposition step (parity, not duplication — no ADDRESS/DEFER import) |

**Reuse:** `ROUTING.md`'s existing Reconciliation dedup language ("present the
finding once with both reviewer attributions rather than as duplicate
findings") — the new paragraph points to it rather than re-deriving merge
semantics.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` — must pass; no new test is
   added (see Approach, alternative 2), so this confirms nothing existing
   regresses.
2. `wc -l claude/.claude/skills/plan-review/SKILL.md` — confirm the file
   stays under its 500-line cap.
3. `/skill-review` — hook-enforced (`require-skill-review.sh`); must produce
   a clean marker before commit.
4. `/code-review` — dispatches `/skill-review` per
   `.claude/rules/review-pipeline-dispatch.md`; Domain: Claude Code config.
5. Manual re-read of the edited Output format section end-to-end to confirm
   the new paragraph doesn't contradict the B7 Out-of-Scope or ledger
   cross-check clauses that follow it in the same section.

## Out of scope

- A full ADDRESS/DEFER-style disposition station for plan-review, mirroring
  code-review's Finding disposition step exactly. See Approach, alternative 1.
- A dedicated drift/presence test for the new sentence. See Approach,
  alternative 2.
- Any edit to `ROUTING.md`'s Reconciliation block. See Approach,
  alternative 3.

## Review surface

Two files, both prose: one requirement paragraph in `SKILL.md`'s Output
format, one short `REFERENCES.md` entry. One domain (Claude Code config). No
test, hook, or agent-frontmatter changes.
