# Add project-layer loader to global `/plan-it`

## Context

The goal is to give the global `/plan-it` skill the same project-layer loading mechanism that `/plan-review` and `/code-review` already have, so per-project `plan-it-<project>` skills load deterministically by glob instead of relying on ambient description routing.

Why now: a downstream project has authored a `plan-it-<project>` skill containing project-specific plan-it rules. Until the global skill grows an explicit loader step, that layer is reachable only via `user-invocable: true` plus ambient description routing — both fragile compared to the glob-driven loader used by the other two review skills. Once the loader lands, downstream layers can flip to `user-invocable: false` and drop ambient routing from their frontmatter, matching the pattern already used by `plan-review-*` and `code-review-*` layers.

Intended outcome: `claude/.claude/skills/plan-it/SKILL.md` gains a `Step 2.5 — Load project-specific layer` step that mirrors the existing loaders structurally, glob-matches `.claude/skills/plan-it-*/SKILL.md` from the repo root, and invokes a single match (or stops on multiple). No project-specific rules are bundled into the global skill — loader is plumbing, not content. Three durable docs that enumerate which skills have this loader (`README.md`, `docs/skills.md`, `docs/design-decisions.md`) are updated in lockstep so the public docs describe the mechanism accurately.

User surface: all `claude-config` stow users. The change ships under `claude/`, which is stow-distributed to every user who has cloned and stowed this repo. Behavioral impact for sessions without a `plan-it-*` skill in their working repo is nil — the glob matches nothing and Step 2.5 proceeds without a layer. Behavioral impact for sessions with a `plan-it-*` layer present: the layer is invoked via the Skill tool at Step 2.5, and its rules apply to the subsequent plan-it steps.

## Approach

Insert a new `Step 2.5 — Load project-specific layer` into `plan-it/SKILL.md` between Step 2 — Discovery and Step 3 — Codebase exploration. The body mirrors the structure used by `plan-review/SKILL.md:46-48` and `code-review/SKILL.md:23-25`, with two adaptations:

1. Glob pattern is `.claude/skills/plan-it-*/SKILL.md` (instead of `plan-review-*` / `code-review-*`).
2. The "where rules apply" clause is generic — "the layer's rules apply to the steps below" — rather than the plan-review/code-review checklist-merge phrasing, because `plan-it` is workflow-driven (numbered steps), not checklist-driven. Generic phrasing also keeps the loader durable: future project layers can add rules at any subsequent step (3, 4, 5, or 6) without needing the global skill text updated.

Rationale for placement (after Step 2, before Step 3): Step 2 — Discovery establishes problem/why/outcome, which is project-agnostic. Step 3 — Codebase exploration is the first step where project-specific conventions (e.g. a design-doc grep, a UI-touch scope check) plausibly bite. Loading the layer at the Step 2 → Step 3 boundary gives the layer's rules effect from the first project-aware step onward.

Alternatives considered and set aside:
- Pin the loader body to specific step numbers (e.g. "the layer's rules apply during Step 3 and Step 4"). Rejected: encodes one project's current application points into the global skill, making the loader text false the first time another project's layer adds a rule at Step 5 or Step 6.
- Skip the loader entirely and keep relying on ambient description routing + `user-invocable: true` on the project layer. Rejected: that's the status quo the existing plan-review and code-review loaders were introduced to replace; the same fragility argument applies here.

### Exact body to insert

```
## Step 2.5 — Load project-specific layer

If a project-specific layer exists for this skill, invoke it now — the layer's rules apply to the steps below. Glob for `.claude/skills/plan-it-*/SKILL.md` from the repo root (resolved via `git rev-parse --show-toplevel`); if exactly one matches, invoke it via the Skill tool. If multiple match, list them and stop — that's a config error in the project, not a review you can resolve. If none match, proceed without a layer.
```

## Critical files

- `claude/.claude/skills/plan-it/SKILL.md` — insert the new Step 2.5 between current line 25 (end of Step 2 — Discovery) and current line 27 (start of Step 3 — Codebase exploration). No other edits.
- `README.md:66` — extend the "Project-layer composition by glob + Skill-tool dispatch" bullet's skill list from "`/plan-review` and `/code-review`" to "`/plan-it`, `/plan-review`, and `/code-review`".
- `docs/skills.md:101-105` — extend the "Project-specific layers" section: add `/plan-it` to the skill list at line 101, add `plan-it-<project>` to the location examples at line 103, and adapt the behavior line at line 105 to note that workflow-driven skills (plan-it) apply layer rules to subsequent steps rather than merging into a checklist.
- `docs/design-decisions.md:37` — extend decision 8's skill list from "`/code-review` and `/plan-review`" to "`/plan-it`, `/code-review`, and `/plan-review`".

Reuse / mirror reference (no edits needed to these files):
- `claude/.claude/skills/plan-review/SKILL.md:46-48` — canonical loader pattern source.
- `claude/.claude/skills/code-review/SKILL.md:23-25` — second canonical instance, confirms the pattern is repeated verbatim across review skills.

## Verification

- Visual diff: confirm the SKILL.md change is a new `## Step 2.5 — Load project-specific layer` heading plus its one-paragraph body, inserted between Step 2 and Step 3, with no edits to surrounding steps.
- Doc-update consistency: diff `README.md:66`, `docs/skills.md:101-105`, and `docs/design-decisions.md:37` against the prior text and confirm each location now lists all three skills (`/plan-it`, `/plan-review`, `/code-review`). Grep `README.md docs/` for any remaining string of the form "`/plan-review` and `/code-review`" in the project-layer context to catch missed locations.
- Pattern fidelity: diff the new body against the plan-review and code-review loader bodies and confirm the only intentional differences are the glob pattern (`plan-it-*`) and the "where rules apply" clause (`apply to the steps below` instead of `merge its checklist into the items below`).
- Live load smoke test (downstream): in a project that has a `plan-it-<project>` skill installed, run `/plan-it` against any topic. Confirm Step 2.5 fires, globs to the single layer, invokes it via the Skill tool, and the layer's rules take effect during the subsequent steps. If no project layer is installed in any reachable repo, this step is deferred to the downstream project where the layer lives.
- Multiple-match negative path: not exercised in this repo (no `plan-it-*` skills live in `claude-config`); verified by inspection that the loader text matches the plan-review/code-review pattern, both of which already exercise the multiple-match-stop branch in their downstream consumers.
- Run `/plan-review` against this plan file before handing off; run `/skill-review` against the SKILL.md edit before committing (hook-enforced on staged SKILL.md changes).

## Out of scope

- Downstream-side flip of the project's `plan-it-<project>` skill from `user-invocable: true` to `user-invocable: false` and removal of ambient description routing. That edit happens in the downstream project's repo after this loader lands, not in `claude-config`.
- Any change to the project layer's rules. The loader is plumbing; layer content stays in the layer's own SKILL.md.
- Hook enforcement of single-match invariant (e.g. a PreToolUse hook that pre-checks the glob and blocks Skill invocation on multiple matches). The existing loaders rely on the skill body's "list them and stop" instruction; matching that behavior is enough for consistency.
