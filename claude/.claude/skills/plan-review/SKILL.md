---
name: plan-review
description: >
  Review implementation plans before presenting to the user. Evaluates against
  domain-specific checklists (backend, frontend, security, infrastructure, data)
  based on which domains the plan touches.
  TRIGGER when: an implementation plan has been written or updated in .claude/plans/
  or is about to be presented to the user for review.
  DO NOT TRIGGER when: the plan is a trivial one-liner (single migration, config
  change), or the user has explicitly said to skip review.
user-invocable: true
---

Review an implementation plan. Act as a review board evaluating a proposal before engineering effort is committed. Be thorough but practical — flag real risks, not hypothetical ones.

## Step 0 — Activate gate session

Write the active-session marker so this skill's own Write/Edit operations are not blocked by the `require-plan-review.sh` hook while the review is in progress:

<!-- HOOK_TEST_FIXTURE: activate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's active-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh activate plan-review
```

If the chain fails (empty `SESSION_ID`, etc.), the `capture-session-id.sh` SessionStart hook didn't run — abort and report; do not proceed without the marker, since the hook would block any Write/Edit during the review.

## Step 1 — Identify the plan

Find the plan to review. Check, in order:
1. If a plan file path was provided as an argument, read it
2. If a plan was just written in `.claude/plans/`, read the most recent one
3. If a plan exists in the current conversation context, use that

## Step 2 — Detect domains

Read the plan and classify which domains it touches. When using an agent to explore the codebase for plan context, use `general-purpose` — not `Explore`. The `Explore` agent reads excerpts and is not suitable for design-doc auditing or cross-file consistency checks; it misses content past its read window.

- **Infrastructure**: CI/CD, workflows, deployment, hosting, config files
- **Data infrastructure**: Database migrations, schema DDL, RLS policies, CDC / change-stream config, ETL/ELT pipelines, warehouse ingestion connectors, raw landing schemas, schema-drift handling
- **Application data**: Application schema design (relational tables, NoSQL document/item shape, partition keys, GSI/LSI design, application access patterns) — this routes through Backend
- **Analytics modeling**: Warehouse-side modeling (fact/dim, SCD, partitioning, materialization, dbt-shape transformations, semantic layer, scheduled queries) — and source-schema review for ELT-readiness when backend schema changes feed the warehouse
- **Frontend**: Client components, hooks, client-side state, UI behavior, routing, forms, optimistic mutations
- **Backend**: Server-side code (HTTP/RPC handlers, edge functions, background jobs, queue consumers, SDK integrations, shared utilities) AND application data-store schema design
- **Security**: Authentication or authorization, token handling, secret management, data exposure, RLS / RBAC / ACL changes

**Schema change routing:** Route schema changes by change type: new nullable column, index, or view → `staff-backend-engineer` only; new table → `staff-backend-engineer` + `staff-analytics-engineer`; rename, drop, type change, NOT NULL constraint added, partition key, or RLS policy → `staff-backend-engineer` + `staff-data-engineer` + `staff-analytics-engineer`. Add `staff-product-engineer` if user-visible.

## Step 2.5 — Load project-specific layer

If a project-specific layer exists for this skill, invoke it now and merge its checklist into the items below. Glob for `.claude/skills/plan-review-*/SKILL.md` from the repo root (resolved via `git rev-parse --show-toplevel`); if exactly one matches, invoke it via the Skill tool. If multiple match, list them and stop — that's a config error in the project, not a review you can resolve. If none match, proceed without a layer.

## Step 3 — Plan structure requirements

**NO PLACEHOLDERS.** Every step must name the actual file, function, or change. "TBD," "based on findings," and "implement later" defer decisions to execution time — when context is thinnest. Check: no conditional language in action items.

**BITE-SIZED STEPS.** Each step is one target, one change, one decision — if you'd need to pause and re-plan mid-execution, split it. Check: each step names one target and one change.

**CONTEXT-COMPLETE STEPS.** Each step contains file path, before/after description, and why the change is needed. Check: no step requires scrolling up for context.

A plan failing any of these is not ready to implement — return it to the author before evaluating gaps.

## Step 4 — Design-fitness gate

Before evaluating gaps, answer two questions in order:

1. **What user surface and threat model does this serve?** A line or two — production users, internal-only, dev-only, or whatever framing fits. Persona reviewers must scope findings to the declared surface, not default to a worst-case external-attacker model.

2. **Is the design appropriately sized for the user pain it solves on that surface?** Gap-finding on an over-elaborate design elaborates it further (each finding closes a gap by adding more machinery), and the checklist won't surface "this whole design is the wrong shape."

Markers of over-elaboration:

- Defensive layers beyond the declared user surface and threat model; conditional logic for phases that may not arrive.
- Layers duplicating an existing abstraction; granularity exceeding any consumer's need.
- Captured outputs / fields with no downstream reader.
- "Could be done in N lines" stays valid even after personas shaped the plan.

If over-elaborated: stop. Surface the simpler design as the primary review output before any checklist findings. Otherwise proceed to Step 5 — gap-finding will surface what's missing.

Question implementation choices, not feature scope — the ticket itself isn't reviewed here, that goes back to the author.

## Step 5 — Evaluate

Evaluate the plan against the **Base checklist** first, then each detected **Domain checklist**. For multi-phase plans, evaluate each phase against the relevant checklists. Reference the specific phase/section when reporting findings.

## Base checklist

Evaluate the plan against each item. Only flag items where there is a concrete issue — do not flag items just to show you checked them.

### Feasibility

B1. **Unstated assumptions** — Does the plan assume library, framework, or SDK behavior without verifying it? Look for claims about API/client/protocol behavior the author hasn't tested.

B2. **Missing consumer analysis** — Does the plan account for all callers/importers/consumers of the changed code? A response-format change without enumerating consumers will break things.

B3. **Breaking intermediate states** — During phased migrations, is there a window where mixed old/new components cause runtime failures?

B4. **Unresolved external dependencies** — Does the plan depend on external services, APIs, or tools whose availability, rate limits, or behavior the author hasn't verified?

B5. **Evidence and verification** — Does the plan cite a source for each finding/assertion (file:line, tool that flagged it, or how it was discovered)? When the plan asserts a specific code shape (function signature, exact line number, type field, import path), verify the cited source actually matches — citation alone is insufficient if the assertion misquotes it. **When the plan asserts a shape is "canonical," "the existing pattern," or "the convention," verify the cited example represents the broader population — run `git grep` and check the count. A single-call-site citation does not establish a pattern.** Conclusions without evidence force reviewers to re-derive them; misquoted or unrepresentative citations produce phantom code-review findings when the implementer correctly diverges.

### Scope

B6. **Proportionality** — Whole-design proportionality belongs to Step 4's gate. At checklist time, flag local issues only: a helper overkill for one caller, an abstraction at a single call site.

B7. **Scope creep** — Does the plan include work not required to solve the stated problem (adjacent improvements, premature optimization, "while we're here" refactors)? Capture these in **Out of Scope** — don't lose the observation, don't plan for it either.

B8. **Missing scope** — Does the plan omit work that IS required? Common gaps: test updates for breaking changes, documentation updates, migration rollback strategy, frontend changes for backend format changes.

### Risk

B9. **Phase independence** — Can each phase merge/deploy independently without breaking the system? Can any phase be reverted without reverting subsequent phases?

B10. **Test realism** — Are the planned test assertions realistic? Will existing tests actually break as claimed? Are new test scenarios sufficient to catch regressions?

B11. **Rollback strategy** — For destructive or hard-to-reverse changes (data migrations, API format changes, dependency removals), is there a rollback plan, or is the change structured to be safely reversible by default?

B12. **Dependency risk** — Does the plan add, upgrade, or remove dependencies? If so, does it account for transitive conflicts, license implications, and the maintenance health of new dependencies?

### Clarity

B13. **Ambiguous instructions** — Could an implementer misinterpret the plan and produce the wrong result? Look for instructions that describe the wrong file, wrong pattern, or make claims about code structure that don't match reality.

B14. **Missing decision rationale** — Are design choices explained? "Use approach X" without explaining why X over Y leaves the implementer unable to make judgment calls at edge cases.

B15. **Effort section reality** — If the plan has an "Estimated Effort" section, does it describe **review surface** (file count, domain complexity, risk concentration) rather than **implementation hours**? Hour-based estimates anchored in human coding speed mislead when Claude writes the code. Flag any effort section citing hours/days; rewrite in review-surface terms.

B16. **Tech-debt intersection** — Does the plan touch or expand an existing tech-debt mechanism (grandfathered-violations list, legacy shim, `// TODO: refactor` marker, dual code path)? If yes, the plan must explicitly choose: expand for now with rationale (e.g. "surgical fix out-of-scope"), or surgical fix in this PR. Don't silently expand; flag as **missing scope (B8)** if unacknowledged. At solo-engineer or near-solo team scale, default to the surgical fix — follow-up tickets rarely land without sprint ceremony to pull them back in.

### PR packaging

B17. **Plan and implementation in sync** — If the plan lives in `.claude/plans/`, are the plan files included in the same PR as the implementation? A plan that ships in a separate branch creates orphaned plan files and makes reviewers evaluate the plan without seeing what it produced. Exception: a standalone plan PR opened *before* implementation begins (for pre-implementation review) is fine — flag only when implementation is underway and the plan is in a separate branch.

## Domain: Infrastructure

Apply when the plan touches CI/CD, workflows, deployment, or config.

I1. **Environment parity** — Does the plan work the same across local, CI, staging, and production (OS, installed tools, permissions)?

I2. **Idempotency** — Can each infrastructure change be applied multiple times safely (migrations, deployments, config rollouts)?

I3. **Deployment ordering** — Does the plan make ordering explicit when application changes depend on it (env var before code reads it, migration before new column access)?

I4. **Secret and config provisioning** — Does the plan specify where and how new secrets/env vars/config values are provisioned in each environment?

## Domain: Data

Apply when the plan touches database schema, migrations, pipelines, or warehouse modeling.

D1. **Migration safety** — Does the plan describe how the migration runs without downtime — no long-locking ALTERs, in-transaction backfills, rewrite-triggering type changes, or `CREATE INDEX` without `CONCURRENTLY` on large tables?

D2. **Migration reversibility** — Does the plan name a backup or reversal path for destructive operations (`DROP COLUMN`, `DROP TABLE`, type narrowing)?

D3. **Deploy-time compatibility** — Does the plan account for mid-deploy failures (old code on new schema or vice versa — column renames, premature `NOT NULL` constraints)?

D4. **Access control on new objects** — Does the plan declare row security/grants on new tables, views, and functions exposed via auto-generated APIs?

D5. **Index coverage** — Does the plan provide indexes for new query patterns (`WHERE`/`JOIN`/`ORDER BY` columns, foreign keys), especially on growing tables?

## Domain: Frontend

Apply when the plan touches React components, hooks, or client-side code.

F1. **User-facing impact** — Does the plan account for how changes affect UX (error messages, loading states, behavioral changes)?

F2. **State management** — Does the plan account for client-side state dependent on changed backend behavior (cached data, optimistic updates, polling)?

F3. **Query contract mapping** — If the plan changes a backend response format, does the frontend consume the new shape correctly (React Query keys, selectors, type definitions)?

F4. **Loading, error, and empty states** — Does the plan cover all three states for new/changed data-fetching paths? Happy-path-only plans force the implementer to improvise.

F5. **Auth state transitions** — If the plan touches auth/session, does it account for state transitions (login/logout, token refresh, expiry) and their UI impact?

## Domain: Backend

Apply when the plan touches edge functions, API routes, or server-side code.

K1. **Contract compatibility** — Does the plan maintain backward compatibility during the transition? If not, is the breaking change coordinated with consumer updates?

K2. **Error handling completeness** — Does the plan cover both success and error paths for new/changed endpoints?

## Domain: Security

Apply when the plan touches auth, authorization, secrets, tokens, or data exposure.

S1. **Threat model** — Does the plan identify what an attacker could do if the implementation has a bug? Plans that add auth or access control should enumerate bypass vectors.

S2. **Defense in depth** — Does the plan rely on a single control, or are there layered defenses? "RLS will handle it" without in-code checks is single-layer.

S3. **Auth boundary coverage** — Does the plan specify both authentication (who) and authorization (can they) on every new endpoint, RPC, or data path?

S4. **Privilege escalation paths** — Does the plan close IDOR vectors, role-check gaps, and ownership-verification gaps for user-supplied IDs?

S5. **Data minimization** — Does the plan minimize exposure in API responses, logs, and error payloads (full-object returns, stack traces, internal IDs)?

S6. **Secret lifecycle** — Does the plan describe provisioning, storage, rotation, and revocation for secrets it introduces or references?

## Exclusions — do NOT flag these

- Style preferences (naming, formatting, file organization) unless they cause ambiguity
- "Consider adding" suggestions not tied to a specific checklist finding
- Theoretical risks with no concrete attack vector or failure scenario
- Domain checklist items for domains the plan doesn't touch
- Generic "add more tests" suggestions, **except** for security controls where untested invariants are indistinguishable from absent ones (see S1)

## Reviewer routing

Read `~/.claude/skills/plan-review/ROUTING.md` before any spawn decision.

## Output format

Start with which domains were detected and which plan sections/phases were reviewed. Then list spawned specialists with owned item IDs (from ROUTING.md's Item ownership table), e.g.:

> - staff-data-engineer: D1 (migration safety), D4 (RLS enforceability)
> - ciso-reviewer: S1 (threat model), S3–S5 (auth/IDOR/data minimization), D4 co-ownership

If none spawned: "No specialists spawned — generalist review only."

For each finding, state:
1. **Which checklist item** (ID and name, e.g., "B3 — Breaking intermediate states")
2. **Which plan section or phase** the finding applies to
3. **What the issue is** (one sentence)
4. **Why it matters** (one sentence)
5. **Suggested resolution** (concrete, not "consider improving")

If any items were flagged by B7 (scope creep), include an **Out of Scope** section listing them. The reviewer can decide whether to bring them into scope or create follow-up tickets.

End with a verdict: **Approve**, **Approve with changes** (list what), or **Request changes** (list blockers).

## Record review completion + deactivate

Write the completion marker only when the verdict is **Approve** or **Approve with changes** and all required changes have been applied to the plan. Do not write it on **Request changes** — write it only after the plan author revises the plan and a clean re-review completes.

<!-- HOOK_TEST_FIXTURE: record-completion — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's completion-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh write plan-review
```

Then remove the active-session marker:

<!-- HOOK_TEST_FIXTURE: deactivate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's active-marker cleanup. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh deactivate plan-review
```
