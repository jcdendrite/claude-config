---
name: plan-it
description: >
  Produce an implementation plan in .claude/plans/<topic-slug>.md
  through Discovery, Codebase Exploration, Clarifying Questions, and
  Architecture Design, then hand off to /plan-review for QA.
  TRIGGER when: the user asks for a plan, design doc, or implementation
  strategy for non-trivial work; before starting a feature branch where
  the change spans multiple files or domains.
  Triggers in plan mode too — compose with plan mode per Steps 1 and 6
  rather than following plan mode's built-in workflow.
  DO NOT TRIGGER when: the change is a one-line config tweak, a
  single-file refactor with obvious shape, or the user explicitly said
  "just implement it"; on the default branch with no intent to branch;
  when a plan already exists and needs QA (use /plan-review instead).
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

## Step 3 — Codebase exploration

Find similar features, the target subsystem, and integration points. Spawn `general-purpose` subagents in parallel when scope warrants — judge fan-out from surface area, do not default to a fixed count. Read the files each subagent flags before designing. Do not use `Explore` here; its read-excerpt window is wrong for design-context analysis.

**Pattern claims require a grep, not a single example.** Before the plan asserts that any code shape is "canonical," "the existing pattern," "how the codebase does X," or equivalent, run `git grep` (or ripgrep) for the shape across the relevant directory and count call sites. Cite the count in the plan ("12 of 13 edge functions use form X; the one exception is `path/to/file.ts:NN`") rather than naming a single example. A single-call-site citation establishes that the shape compiles, not that it is canonical — outliers and one-offs read identically to canonical examples until you check the population.

## Step 4 — Clarifying questions

List every underspecified decision (edge cases, error handling, scope boundaries, backward compatibility) and ask the user. Do not proceed until answered or the user delegates the call to you.

## Step 5 — Architecture design

Choose the approach. Always include brief rationale — what alternatives were weighed and why they were set aside. For trivial choices one sentence suffices; no separate alternatives section is needed. Consult `code-review`, `test-conventions`, and `verify-primary-sources` if their domains are implicated. Write the plan with these sections:

1. **Context** — problem, why now, intended outcome (lead with a one-sentence goal)
2. **Approach** — chosen design with rationale; note alternatives considered and why they were set aside (inline in this section, not a separate block)
3. **Critical files** — paths to create/modify, with **reuse opportunities** (existing functions/utilities to call rather than reimplement)
4. **Verification** — how to test end-to-end
5. **Out of scope** — only if scope creep was observed

Effort sections optional; if present, describe review surface (file count, domain spread, risk concentration), never hours or days.

## Step 6 — Hand off to /plan-review

Invoke `/plan-review` against the written plan file. Address any findings before presenting the plan to the user.

**If plan mode is active:** after `/plan-review` is clean and findings are addressed, call `ExitPlanMode` to request approval. The harness shows the plan file's contents in the approval UI; do not also ask conversationally.
