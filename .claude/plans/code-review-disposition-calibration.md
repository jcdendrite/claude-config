# Plan: Calibrate reviewer-finding disposition by review surface, not coding time

## Context

**Goal:** make the orchestrator stop deferring reviewer-agent findings that are cheap to merge in, by wiring the existing "review surface, not implementation time" principle into the code-review finding-disposition decision.

**Problem.** When reviewer agents return findings, the parent tags each ADDRESS (fix now) or DEFER (follow-up). It defers too many — the user has pushed back across **10+ sessions in two repos** (verbatim: *"Why did you defer this - it's an easy fix"*; *"the pre-existing issue is worth doing - why not. You're touching this code anyway"*; *"cosmetic naming is cheap and avoids confusion - do it"*). The recurring over-defer labels are *"Orthogonal scope," "Pre-existing/independent fix," "follow-up," "neither blocks merge / advisory," "cosmetic / small."* In two cases the "pre-existing/independent" label was **wrong** — the PR's own change touched or activated the finding.

**Root cause.** The principle that the cost gating a change is *review surface and testing area*, not *implementation time*, is already recorded (`docs/design-decisions.md` — the "review surface, not implementation time" decision, 2026-05-29) and that entry's text even says implementation-time anchoring *"miscalibrates triage"* — but it is wired only into **plan effort sections** (`plan-it` Step 5, `plan-review` B15). It is **not referenced at the code-review finding-disposition step** (`code-review/SKILL.md:269–292`), whose DEFER criteria are scope-based and never counter the implicit human-coding-time intuition. The criteria already encode the user's own rule ("touched code + tests already running = ADDRESS; coordinated multi-PR effort = DEFER"); the leak is in *application* — the orchestrator stretches "orthogonal"/"pre-existing" and reaches for effort/non-blocking framing.

**Intended outcome.** At disposition time, the orchestrator calibrates ADDRESS-vs-DEFER on complexity, risk, and testing area — and treats "small / cosmetic / non-blocking / in code we're already touching" as the strongest ADDRESS signal, not a defer reason.

## Approach

Wire the existing review-surface principle into the disposition decision at the one place per-finding ADDRESS/DEFER lives (`code-review/SKILL.md`), plus a one-line always-loaded pointer so ad-hoc reviewer dispositions outside `/code-review` inherit the same guard. Record the extension as a new dated design-decisions entry. No new mechanism — same default-ADDRESS foundation, made harder to misapply, grounded in the transcript evidence.

*Alternatives set aside:* (a) New DEFER/ADDRESS machinery — rejected; the foundation is sound, only application leaks. (b) plan-review symmetry — rejected (user scope decision); plan-review has no per-finding disposition step and B15 already governs its effort sections. (c) Automated test enforcement — rejected; this is prose judgment guidance, not a structural invariant, and a regex assertion on disposition prose would be brittle and low-value (consistent with the effort-estimation case study's own "ad-hoc, not committed" proportionality stance).

## Critical files

### 1. `claude/.claude/skills/code-review/SKILL.md` — Finding disposition section

Three surgical edits. The skill body is stowed to all users, so additions stay platform-agnostic and self-contained (no repo-internal `docs/` cross-references — that linkage lives in the design-decisions entry). **Apply each edit by matching the quoted anchor text, not the cited line number** — line numbers shift as earlier edits in the same file land; the line numbers below are orientation only.

- **ADDRESS rationale (after line 273)** — add the calibration-axis guard and reinforce the opportunistic-tech-debt factor:
  > Calibrate disposition on complexity, risk, and testing area — never on implementation effort. For an agent, a one-line fix and a fifty-line fix in the same already-touched, already-tested code are equally cheap to make; "it's a small change, defer it" inverts the cost model — small *and* in-surface is the strongest ADDRESS signal, not a reason to push it to a follow-up. A tech-debt finding inside code this change already touches, covered by tests already running, is the textbook opportunistic-refactoring case (CLAUDE.md §Working Style Axis 2 — "opportunistic refactoring of code is encouraged"; Fowler) — ADDRESS it; that is the cheapest the fix will ever be.

- **Criterion 1 "Orthogonal scope" (line 279)** — append the touch/activate guard to close the two mislabel cases:
  > Not orthogonal if this change touches the code the finding lands in, or if this change is what activates the finding (a new caller reaching a latent bug, a migration that flips a table a bug reads) — those are ADDRESS: scope is set by the bug, not by where the symptom first surfaced.

- **Invalid DEFER rationales (after line 290)** — add one bullet covering the effort/severity framings:
  > - **"Small" / "quick" / "cosmetic" / "non-blocking" / "advisory"** — implementation size and merge-blocking severity are not disposition axes. A reviewer's advisory or a one-line cosmetic fix in already-touched, already-tested code is ADDRESS; "it doesn't block merge" is a severity label, not a DEFER criterion.

**Reuse:** the existing ADDRESS-default + closed-DEFER-list + Invalid-rationales + 3+-smell-test structure (lines 271–292). These edits extend that shape; they do not add a parallel mechanism. The "scope is set by the bug" phrasing mirrors CLAUDE.md §Engineering Judgment "Audit structural siblings" (also stowed globally, so the concept is shared, not duplicated as a new rule).

### 1b. `claude/.claude/skills/code-review/REFERENCES.md` — provenance line

Add one line mapping the new "Orthogonal scope" touch/activate guard to its source principle (edit-time reference; not loaded at runtime), e.g. under the existing criterion-1 provenance:
> - Criterion 1 "Orthogonal scope" touch/activate guard — grounds in CLAUDE.md §Engineering Judgment "Audit structural siblings before scoping a fix narrowly" ("scope is set by the bug, not by where the symptom surfaced") and "Prove your change caused a failing check" (a change activating a latent bug makes it in-scope). Surfaced by a transcript audit of 10+ over-defer pushbacks.

This is in-surface (the skill dir is already being edited) and small — ADDRESS by this change's own principle, not a follow-up.

### 2. `claude/.claude/CLAUDE.md` — `## Code Review` section

Add one bullet after the existing `/code-review` bullet so the guard also covers ad-hoc reviewer dispositions (the parent dismissing reviewer comments without running `/code-review`, where only CLAUDE.md is loaded):
> - **Disposition calibrates on review surface, not coding time.** When deciding whether a reviewer-agent finding is fixed now or deferred — via `/code-review` or an ad-hoc reviewer spawn — calibrate on complexity, risk, and testing area, not on how little time the fix takes. A finding in code this change already touches and that tests already running already cover is fix-now, however small; defer only against the code-review skill's named Finding-disposition criteria.

### 3. `docs/design-decisions.md` — fix duplicate heading, then new entry

**First, the in-scope tech-debt fix** (opportunistic — we're already editing this file): the doc has two `## 12` headings (line 120 `Reviewer file-based output via findings_path`; line 130 `Single source of truth…`). Renumber the second `## 12` and cascade so numbering is sequential again:
- `## 12. Single source of truth…` → `## 13`
- `## 13. Effort estimated by review surface…` → `## 14`
- `## 14. Convention skills wired by explicit pointer…` → `## 15`

Then update the **one** external cross-reference (verified by repo grep — only §10/§11/§13 have external refs, and only §13 moves): `docs/case-studies/effort-estimation-review-surface.md:3` points at `design-decisions.md §13` with GitHub anchor `#13-effort-estimated-by-review-surface-not-implementation-time-2026-05-29`. Update both the visible `§13` → `§14` and the anchor `#13-…` → `#14-…`. This is pointer-integrity maintenance to keep the link valid, not a content rewrite of the dated case-study record.

**Then append the new entry as `## 16`:**
> ## 16. Finding disposition calibrated by review surface, not coding time (2026-06-14)
>
> §14 established that effort anchors on review surface, not implementation time, and scoped the rule to plan effort sections. The same miscalibration surfaced at the code-review finding-disposition step: the orchestrator deferred reviewer findings cheap to ADDRESS — small, in already-touched code, covered by tests already running — on effort/size/non-blocking grounds. A transcript audit found the pushback recurring across 10+ sessions in two repos; in several the "pre-existing/independent" label was wrong because the PR's own change touched or activated the finding.
>
> The fix wires §14's principle into disposition: the code-review skill's Finding-disposition section now states disposition calibrates on complexity, risk, and testing area — not implementation effort — reinforces the opportunistic-refactoring license for tech debt in already-touched, already-tested code, hardens "Orthogonal scope" against the touch/activates-it mislabel, and adds "small/cosmetic/non-blocking/advisory" to the invalid-DEFER list. A one-line CLAUDE.md pointer carries the guard to ad-hoc reviewer dispositions outside `/code-review`. This adds the always-loaded pointer the effort-estimation case study deliberately declined — justified here because the disposition miscalibration recurs far more often (10+ flagged sessions vs the 6–8 conversational effort estimates that study measured) and ships as a merged-PR omission, not a stray sentence.
>
> ### Sources
> - `claude/.claude/skills/code-review/SKILL.md` — Finding disposition (ADDRESS/DEFER) machinery
> - `claude/.claude/CLAUDE.md` §Code Review — the always-loaded pointer
> - §14 — the parent principle this extends

## Verification

1. **Lint + tests** (from a worktree, paths three levels up): `../../../.venv/bin/ruff check claude/.claude/` and `../../../.venv/bin/pytest claude/.claude/` — confirm no structural breakage (these edits are prose; expect green).
2. **Skill self-review (hook-enforced):** invoke `/skill-review` on the `code-review/SKILL.md` diff. Confirm the additions don't violate the skill's own brevity/voice rules — specifically that the new bullet reads as a closed-list extension, not bloat. Invoke `/ai-instruction-and-memory-files` on the CLAUDE.md diff.
3. **`/code-review`** on the staged diff — it auto-dispatches `/skill-review` (SKILL.md) and the CLAUDE.md reviewer per file type.
4. **Behavioral smoke test** (manual; no automated `claude -p` harness in this repo): construct two sample reviewer findings — (a) a one-line naming fix in a file the diff already modifies, covered by a test already running; (b) a fix requiring a coordinated multi-PR migration — and confirm the disposition guidance now yields ADDRESS for (a) and still DEFER for (b).
5. **Plan/implementation sync (B17):** after `ExitPlanMode`, the plan-it flow moves this file to `.claude/plans/<slug>.md` on the implementation branch (in the claude-config repo); confirm it ships in the same PR as the edits above, not a separate branch.

## Out of scope

- **plan-review changes** — user scoped this to code-review disposition; plan-review has no per-finding ADDRESS/DEFER step and B15 already governs its effort sections.
- **The effort-estimation case study doc's *content*** — point-in-time dated record (Axis 3 preserved-content); its findings/prose are left untouched. Only the single stale `§13` cross-reference + anchor is updated for link integrity (see Critical files §3). The new design-decisions entry explains why the case study's anti-pointer reasoning doesn't apply here.
- **Automated test enforcement** — prose judgment guidance, not a structural invariant; rationale in Approach.
