---
name: plan-it
description: >
  Produces an implementation plan and hands off to /plan-review.
  TRIGGER when: asked for a plan or implementation strategy
  for work spanning multiple files or domains. DO NOT TRIGGER when:
  single-file tweaks, "just implement it" requests, or when a plan
  already exists (use /plan-review instead).
user-invocable: true
argument-hint: "[optional topic or ticket id]"
---

# Plan-it

## Step 1 — Branch + plan file

**If plan mode is active:** write the plan to the harness-provided plan path (named in the plan-mode system-reminder). Skip branch creation here — plan mode would block it. Resume the branch-creation flow only after `ExitPlanMode` is approved: derive the slug, run `branch-creation`, and move the plan file to `.claude/plans/<topic-slug>.md` on the new branch.

**Otherwise:** if on the default branch, invoke the `branch-creation` skill to pick a slug and start from a fresh default tip. If already on a feature branch, keep it and derive the slug from the branch name — if the branch name contains `/` (e.g. `GH-42/add-auth`), use only the portion after the last `/`. Plan path is `.claude/plans/<topic-slug>.md` on the implementation branch (per `branch-creation`'s "plan files go on the implementation branch" rule).

If `.claude/plans/<topic-slug>.md` already exists, open it for revision in place rather than scaffolding a new file.

## Step 2 — Discovery

Restate the problem, why now, and the intended outcome in one short paragraph. If any of the three is unclear, ask the user before moving on. This becomes the lead of the plan's **Context** section, with the first sentence stating the goal.

## Step 2.5 — Load project-specific layer

If a project-specific layer exists for this skill, invoke it now — the layer's rules apply to the steps below. Glob for `.claude/skills/plan-it-*/SKILL.md` from the repo root (resolved via `git rev-parse --show-toplevel`); if exactly one matches, invoke it via the Skill tool. If multiple match, list them and stop — that's a config error in the project, not a review you can resolve. If none match, proceed without a layer.

## Step 3 — Codebase exploration

Find similar features, the target subsystem, and integration points. Spawn `general-purpose` subagents in parallel when scope warrants — judge fan-out from surface area, do not default to a fixed count. Read the files each subagent flags before designing. Do not use `Explore` here; its read-excerpt window is wrong for design-context analysis.

**Pattern claims require a grep, not a single example.** Before asserting a code shape is "canonical," "the existing pattern," or "how the codebase does X," run `git grep` (or ripgrep) and count call sites. Cite the count ("12 of 13 modules use form X; one exception at `path/to/file:NN`") rather than a single example. A single-call-site citation establishes that the shape compiles, not that it is canonical — outliers look identical to canonical examples until you check the population.

## Step 4 — Clarifying questions

List every underspecified decision (edge cases, error handling, scope boundaries, backward compatibility) and ask the user. Do not proceed until answered or the user delegates the call to you.

## Step 5 — Architecture design

Choose the approach. Always include brief rationale — what alternatives were weighed and why they were set aside. For trivial choices one sentence suffices; no separate alternatives section is needed. Consult `code-review`, `test-conventions`, and `verify-primary-sources` if their domains are implicated.

**External-pattern grounding.** When the chosen approach invokes a pattern from external documentation (a library, framework, or vendor doc), quote the literal source lines that establish the pattern — not a paraphrase, not a summary, not the section heading. This extends Step 3's grep-the-population rule from in-codebase patterns to external sources. A capitalized pattern name ("the X pattern") lifted from prose without a verbatim source quote is a hazard: names crystallize an interpretation that may not match the source.

**Lighter-alternatives subsection.** If the chosen approach adopts a more powerful, invasive, or wider-scope mechanism than the task requires — a heavier abstraction, a more privileged execution context, a more complex coordination pattern, a more invasive integration — include a **Lighter alternatives considered** subsection in the plan. Enumerate at least two lighter primitives from the source documentation/system; for each, state in one sentence why it does not solve the problem. If you cannot find two lighter alternatives in the source, that itself is the signal that you have not read the source carefully enough — re-read with the specific question "what mechanisms exist that do NOT require this heavier choice?" before continuing.

Write the plan with these sections:

1. **Context** — problem, why now, intended outcome (lead with a one-sentence goal)
2. **Approach** — chosen design with rationale; note alternatives considered and why they were set aside (inline in this section, not a separate block)
3. **Critical files** — paths to create/modify, with **reuse opportunities** (existing functions/utilities to call rather than reimplement)
4. **Verification** — how to test end-to-end
5. **Out of scope** — only if scope creep was observed

Effort sections optional; if present, describe review surface (file count, domain spread, risk concentration), never hours or days.

## Step 6 — Hand off to /plan-review

Invoke `/plan-review` against the written plan file. Address any findings before presenting the plan to the user.

**If plan mode is active:** after `/plan-review` is clean and findings are addressed, call `ExitPlanMode` to request approval. The harness shows the plan file's contents in the approval UI; do not also ask conversationally.

**Draft-PR handoff for design-doc or cross-team contract changes.** If the plan introduces a new design document (e.g., a new file under `docs/design/`) or defines a cross-team data contract (a schema shape, enum, or API surface that downstream teams or analytical pipelines depend on), after `ExitPlanMode` is approved and the plan + doc are committed on the implementation branch, push the branch and open a **draft** PR before starting implementation. Async comments on the rendered diff are easier to thread than prose in a plan file, and downstream reviewers may need lead time. Skip this for plans that are implementation-only (no new design doc, no cross-team contract).
