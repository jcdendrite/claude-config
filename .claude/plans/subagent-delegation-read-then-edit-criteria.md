# Read-then-edit delegation criteria for `subagent-delegation`

## Context

**Goal:** add a narrow, affirmative-signal-gated rule to `subagent-delegation/SKILL.md` that tells a parent session exactly when a read-then-edit sequence routes to `code-writer` — without softening the standing default that inline edit is the safe choice.

Monthly transcript analysis (`~/.claude/research/transcript-analysis-2026-06-02.md`, lines 207–243) found 1,532 Opus turns where Opus read 0–1 files then edited inline, against only 506 where it delegated first (a 75/25 inline split), and estimated ~33% of Opus output in the window was Sonnet-tier work. The existing stay-inline bullet — "`Edit`/`Write` sequences (judgment-dense, not scratch)" — overfires: it keeps clearly-mechanical read-locate-then-apply edits inline. An existing test-case file (`~/.claude/skill-test-cases/2026-05-19-delegation-gate-bypassed-on-small-task.md`) already documents this exact ambiguity and proposes the key sharpening: *the delegation decision keys on parent context spent (input footprint), not output size.*

**Intended outcome:** a tight set of criteria (plus a regression test-case file) that captures the clean code-writer cases, leaves discovery reads on the existing Explore/general-purpose path, and keeps judgment-dense edits inline.

## Approach

**The discriminating principle: was the change decided *before* the read?**

A read-then-edit routes to `code-writer` only when **both**:
1. **The change is already decided before you read** — you read to *locate* a known target and apply a change whose content and approach are already fixed (not to figure out scope or design a fix from what the file reveals).
2. **Reaching that target costs non-trivial context** that will sit in the parent for the rest of the session (the input-footprint signal from the 2026-05-19 case: ~700 lines read to write ~15).

If you are still deciding *what* to change as you read — scope, approach, or the substantive content of the edit — that judgment is the parent's, and it **stays inline**. This includes authoring a memory entry (the framing *is* the judgment) and designing a fix from what the file reveals. Discovery reads that map an unfamiliar area *before* you decide route to `Explore`/`general-purpose` (existing "Codebase discovery" section), not `code-writer`.

**Why this principle and not the brief's candidate criteria.** The brief floated "scope fully specified," "read target already known," and ">1 file." The first two are subsumed by "decision made before the read"; the third (`>1 file`) is a poor proxy — case 1 (multi-file migration) is multi-read *discovery*, not a code-writer case, so file count would misclassify it. "Decision made before the read" is the single signal that produces the right verdict on all six grounded cases (below) and naturally excludes discovery (cases 1, 2, 5), because in discovery the decision is precisely what's still being formed.

**Verdicts the principle produces** (matching the analysis):
- Case 3 (ChatPanel catch block — decided fix, read locates) → **code-writer** ✓
- 2026-05-19 (test edit — decided spec, ~700-line read footprint) → **code-writer** ✓
- Case 4 (memory write — parent authors content) → **inline** ✓ *(per your decision)*
- Cases 1, 2, 5 (migration compare / stale-ref scan / skill discovery — deciding scope) → **discovery dispatch (Explore/general-purpose)**, not code-writer ✓

**Scope decisions (confirmed):**
- **Memory writes stay inline** — the criteria name "parent authors the substantive content of the edit" as a stay-inline marker.
- **SKILL.md only** — no CLAUDE.md change. The criteria are narrow affirmative exceptions; CLAUDE.md's "substitution for the code-writing path only — does not change when the parent delegates vs writes inline" holds at its altitude and already defers detail to the skill.

**Format (canonical home + one pointer, to avoid DRY drift):**
- **Authoritative home:** add the criteria to the `### Implementation work → code-writer` section as a short "Read-then-edit: the decision-made test" paragraph (~8 lines).
- **Pointer:** refine the existing stay-inline `Edit`/`Write` bullet to name *what* keeps it inline (still deciding approach/scope/content) and defer the exception to the code-writer section — no restated criteria, just a cross-reference.

## Critical files

- **`claude/.claude/skills/subagent-delegation/SKILL.md`** — the only behavioral edit.
  - Refine the stay-inline bullet at line ~50 (`Edit`/`Write` sequences …) to name the judgment-dense condition and point to the code-writer section.
  - Add the "decision-made test" paragraph to the `### Implementation work → code-writer` section (lines ~170–177).
  - **Reuse, do not restate:** the existing locate-vs-reason split (lines ~165–168) and the discovery section (lines ~143–168) already route cases 1/2/5; the new text references them rather than duplicating the discovery rule.
- **`~/.claude/skill-test-cases/subagent-delegation-read-then-edit.md`** (new) — regression test set: the 5 transcript samples + the 2026-05-19 case, each with the verdict the criteria produce and a one-line reason. Mirrors the format of the existing `2026-05-19-…` file in that directory.
- **No CLAUDE.md change.** **No hook/settings change.**

## Verification

- Apply each of the six cases to the drafted criteria and confirm the verdict matches the analysis (table above) — done in the test-case file, which doubles as the regression record.
- `/skill-review` on the SKILL.md diff (hook-enforced via `require-skill-review.sh` before commit) — checks verbosity, trigger accuracy, conflict with existing rules. Per repo convention, run the skill on its own diff.
- `/code-review` on the full diff.
- `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` from the worktree (the `.venv` lives only at the main worktree root).
- Sanity check: the `description` frontmatter still accurately summarizes triggers after the edit (no trigger surface added that the description omits).

## Out of scope

- Flipping the default to delegate-by-default for read-then-edit (separate decision, separate plan).
- Changing check-runner or reviewer-constellation delegation rules (analysis confirmed these work well).
- The ~12,417 D3=neither single-read turns and the Ops-state-read category (analysis confirmed largely legitimate orchestration).
- Any hook or deny-message edit.
- **Making gate evaluation observable/enforced.** The 2026-05-19 case was a *silent skip* (the parent never evaluated), not a misclassification. This plan sharpens the boundary; forcing the parent to consult the gate (e.g. a state-before-edit declaration, or a hook) is a separate, likely hook-shaped change. A sharper boundary helps only once the gate is consulted — but enforcement is out of scope here.
