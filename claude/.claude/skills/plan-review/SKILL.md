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
SESSION_ID=$(cat "$HOME/.claude/sessions/$PPID") && [ -n "$SESSION_ID" ] && mkdir -p "$HOME/.claude/.plan-review-active.d" && touch "$HOME/.claude/.plan-review-active.d/$SESSION_ID"
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

## Step 3 — Design-fitness gate

Before evaluating gaps, answer two questions in order:

1. **What user surface and threat model does this serve?** A line or two — production users, internal-only, dev-only, or whatever framing fits. Persona reviewers must scope findings to the declared surface, not default to a worst-case external-attacker model.

2. **Is the design appropriately sized for the user pain it solves on that surface?** Gap-finding on an over-elaborate design elaborates it further (each finding closes a gap by adding more machinery), and the checklist won't surface "this whole design is the wrong shape."

Markers of over-elaboration:

- Defensive layers stacked beyond what the declared user surface and threat model justify.
- Conditional logic for future phases that may not arrive.
- Layers that duplicate a higher-level abstraction.
- Granularity exceeding any concrete consumer's need.
- Captured outputs / fields with no reader downstream.
- "Could be done in N lines" stays a valid challenge even after persona reviewers have shaped the plan — persona-shaped is not persona-locked.

If over-elaborated: stop. Surface the simpler design as the primary review output before any checklist findings. Otherwise proceed to Step 4 — gap-finding will surface what's missing.

Question implementation choices, not feature scope — the ticket itself isn't reviewed here, that goes back to the author.

## Step 4 — Evaluate

Evaluate the plan against the **Base checklist** first, then each detected **Domain checklist**. For multi-phase plans, evaluate each phase against the relevant checklists. Reference the specific phase/section when reporting findings.

If this project also has a project-level plan-review skill, both skills will trigger independently. This skill covers generic plan quality; the project skill covers project-specific concerns.

## Base checklist

Evaluate the plan against each item. Only flag items where there is a concrete issue — do not flag items just to show you checked them.

### Feasibility

B1. **Unstated assumptions** — Does the plan assume library, framework, or SDK behavior without verifying it? Look for claims about API/client/protocol behavior the author hasn't tested.

B2. **Missing consumer analysis** — Does the plan account for all callers/importers/consumers of the changed code? A response-format change without enumerating consumers will break things.

B3. **Breaking intermediate states** — During phased migrations, is there a window where mixed old/new components cause runtime failures?

B4. **Unresolved external dependencies** — Does the plan depend on external services, APIs, or tools whose availability, rate limits, or behavior the author hasn't verified?

B5. **Evidence** — Does the plan cite a source for each finding/assertion (file:line, tool that flagged it, or how it was discovered)? Conclusions without evidence force reviewers to re-derive them.

### Scope

B6. **Proportionality** — Whole-design proportionality belongs to Step 3's gate. At checklist time, flag local issues only: a helper overkill for one caller, an abstraction at a single call site.

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

B16. **Tech-debt intersection** — Does the plan touch, expand, or work around an existing tech-debt mechanism (grandfathered-violations list, legacy shim, `// TODO: refactor` marker, dual code path that exists for migration reasons)? If yes, the plan must explicitly choose between:

- **(a) Expand the workaround for now** — with rationale (typically "the surgical fix is out-of-scope and would dilate the PR").
- **(b) Include a surgical fix in this PR** — and adjust scope accordingly.

Don't silently expand. The choice between (a) and (b) is calibrated to team size and PR-scope discipline — surface the intersection here, not after the fact in code-review. Flag as **missing scope (B8)** if the plan touches a tech-debt mechanism without acknowledging it.

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

## Reviewer roles

Same escalation logic as code-review's Ripple-effect triage — repeated because skills load on demand, not because the rule differs.

You are the principal-engineer-generalist running this skill; specialists below have narrower lane depth, not broader context — you have the conversation, the plan, prior rounds, and the user's calibration cues.

This is the design-stage gate. Specialist misses here are recoverable in code-review, so self-judge on borderline calls — committing to specialist depth at design-stage adds delay without the safety it adds at the last gate. **Default to your own review across all detected domains using the checklists.** Spawn a specialist only when at least one criterion applies:

- **Below-staff-level depth.** Specialized kernels (query-planner internals, cryptography primitives, fine-grained a11y, kernel-level concurrency) — not "general knowledge in domain X."
- **High-stakes boundary.** RLS, auth, billing, payment, data migration, privileged ops — a specialist's eyes are worth it even at generalist depth.
- **Holistic-reasoning overload.** Multi-domain change you can't hold in working memory at once.
- **Convergence-as-design-tell** from a prior round (see Reconciliation).
- **Explicit user request.**

Always spawn `ciso-reviewer` when the plan touches auth/authz, secrets, tokens, data exposure, sensitive-data logging, third-party data sharing, or infra permissions — high-stakes-boundary case is non-optional, **unless** the Step 3 user-surface declaration puts the change outside `ciso-reviewer`'s threat model (e.g., a dev-only flow with no production reachability, or an internal-only path where engineers themselves are the only callers and the change crosses no privilege boundary they shouldn't cross). When skipping on those grounds, name the surface in the review output — never silently skip. Always spawn `staff-product-engineer` when the plan changes user-facing behavior.

Spawn per question (not per file-path domain) — "plan touches backend" isn't enough; the question needs a specific shape.

When you spawn: pick the specialist that serves the question (table below is reference, not roster) and pass plan scope, section, specific question, **Item ownership** routing, AND — for re-review rounds — prior findings + what's been applied. Reviewers without prior context re-discover; that's wasted spawn.

| Domain | Agent | Focus |
|--------|-------|-------|
| Backend | `staff-backend-engineer` | API contracts, error handling, idempotency, retry semantics, service boundaries, SDK behavior, application data-store schema design (relational + NoSQL) |
| Frontend | `staff-frontend-engineer` | Component patterns, state management, data fetching and cache consistency, accessibility, i18n, UX impact |
| Security | `ciso-reviewer` | Threat modeling, auth boundaries, privilege escalation, data exposure, defense in depth |
| Data infrastructure | `staff-data-engineer` | Migration pipeline impact, DDL execution shape, CDC / change-stream config, ETL/ELT pipelines, warehouse ingestion transport, schema-drift detection, catalog / lineage tracking |
| Analytics modeling | `staff-analytics-engineer` | Warehouse-side modeling (fact/dim, SCD, partitioning, materialization), transformation correctness, source-schema review for ELT-readiness |
| Infrastructure | `staff-platform-engineer` | CI/CD, IaC, shell, deployment ordering, secret provisioning, observability/alerting/SLO, runbook linkage, load characteristics, cost / operational footprint, deploy-window and lock-budget for migrations |
| Testing | `staff-sdet` | Testability of the design, edge cases the plan omits, test strategy coverage vs risk areas, test data; production code with non-trivial logic that lacks tests |
| Product | `staff-product-engineer` | Whether the plan solves the user problem, UX impact during migrations, feature interactions, user-facing regressions hidden behind technical framing, telemetry semantics |

Project-level plan-review skills may extend this table with project-specific reviewer roles, but must not remove or narrow the `ciso-reviewer` trigger conditions.

Specialist agents must return ≤2K tokens of structured findings (checklist-item-keyed bullets), not narrative prose. If findings genuinely exceed the budget, the agent must prioritize by severity and explicitly note that lower-severity items were omitted. When spawning, include this constraint in the agent prompt.

## Reconciliation

Same logic as code-review's Reconciliation — repeated because skills load on demand.

After spawned reviewers return findings, pause if findings concentrate on a single surface — the same feature, implementation detail, or design choice attracting multiple gaps. If two specialists flag the same `file:line` with the same root cause, present the finding once with both reviewer attributions rather than as duplicate findings. Two readings:

- **Design-wrong-shape.** The surface is the wrong abstraction; gaps will keep multiplying as you patch. Replace, don't patch-by-patch.
- **Prompt-overlap artifact.** Reviewers given similar prompts produce N voices of the same observation. Convergence looks like signal but is framing-induced.

You judge which applies. Don't treat convergence as automatic authority for "patch each gap." If design-wrong-shape, replace the surface and re-run Step 3. If prompt-overlap, apply the underlying finding once, skip duplicates, and note the overlap so the next spawn uses tighter prompts.

## Item ownership

Routes each checklist item to the reviewer subagent(s) that file findings on it. Bold shorthands match titles above; IDs are the dispatcher's primary key. **Primary owner** files findings; **co-owners** are spawned where the item touches their turf. When in doubt, this table wins over inline mentions.

The dispatcher fires reviewers per touched domain. Each agent self-scopes against the plan and returns early ("No X concerns") when out of lane.

| Item | Primary owner | Co-owners |
|------|---------------|-----------|
| **B1. Unstated assumptions** | `staff-backend-engineer` (runtime / SDK assumptions) | `staff-platform-engineer` (CI / build tools) |
| **B2. Missing consumer analysis** | `staff-backend-engineer` (API consumers) | `staff-frontend-engineer`, `staff-product-engineer`, `staff-data-engineer` (per consumer type), `staff-analytics-engineer` (warehouse-consumer fitness — source shape suits modeling) |
| **B3. Breaking intermediate states** | `staff-backend-engineer`, `staff-data-engineer` | `staff-platform-engineer` (deploy-window) |
| **B4. Unresolved external dependencies** | `staff-backend-engineer` | — |
| **B5, B6, B7, B13, B15. Judgment items** (evidence, proportionality, scope creep, ambiguous instructions, effort section) | judgment (any reviewer) | — |
| **B8. Missing scope** | `staff-product-engineer` (user-facing gaps), `staff-sdet` (test gaps) | `staff-data-engineer`, `staff-platform-engineer` (ops gaps) |
| **B9. Phase independence** | `staff-platform-engineer` | `staff-backend-engineer` |
| **B10. Test realism** | `staff-sdet` | `staff-product-engineer` (user-flow realism) |
| **B11. Rollback strategy** | `staff-data-engineer`, `staff-platform-engineer`, `staff-backend-engineer` | `staff-sdet` (testability of rollback) |
| **B12. Dependency risk** | `staff-backend-engineer` (runtime deps), `staff-platform-engineer` (CI / build deps) | — |
| **B14. Missing decision rationale** | `staff-product-engineer` (user-impact decisions) | judgment (others) |
| **B16. Tech-debt intersection** | `staff-product-engineer` (scope decision: fix now vs. defer) | domain reviewer for the affected area — whichever of `staff-backend-engineer`, `staff-frontend-engineer`, `staff-data-engineer`, or `staff-platform-engineer` owns the subsystem the tech debt lives in (surgical-fix feasibility) |
| **I1–I4. Infrastructure** (env parity, idempotency, deployment ordering, secret/config provisioning) | `staff-platform-engineer` | `staff-data-engineer` (I2 migration-level idempotency); `ciso-reviewer` (I4 secret threat framing) |
| **D1. Migration safety** | `staff-data-engineer` (pipeline impact, DDL form) | `staff-backend-engineer` (correctness), `staff-platform-engineer` (deploy-window, lock-budget) |
| **D2. Migration reversibility** | `staff-data-engineer` | `staff-backend-engineer` |
| **D3. Deploy-time compatibility** | `staff-data-engineer` | `staff-backend-engineer`, `staff-platform-engineer` |
| **D4. Access control on new objects** | `staff-data-engineer` (enforceability) | `ciso-reviewer` (threat framing) |
| **D5. Index coverage** | `staff-backend-engineer` (app-query coverage) | `staff-data-engineer` (DDL risk and bloat) |
| **F1. User-facing impact** | `staff-frontend-engineer` | `staff-product-engineer` |
| **F2. State management** | `staff-frontend-engineer` | — |
| **F3. Query contract mapping** | `staff-frontend-engineer` | `staff-backend-engineer`, `staff-product-engineer` (user-visible drift) |
| **F4. Loading / error / empty states** | `staff-frontend-engineer` (implementation) | `staff-product-engineer` (UX-matches-spec), `staff-sdet` (per-state coverage) |
| **F5. Auth state transitions** | `staff-frontend-engineer` | `staff-product-engineer`, `ciso-reviewer` (auth state security) |
| **K1. Contract compatibility** | `staff-backend-engineer` | `staff-frontend-engineer` (client adaptation) |
| **K2. Error handling completeness** | `staff-backend-engineer` | `staff-sdet` (error-path tests), `staff-frontend-engineer` (UI surfacing) |
| **S1–S2. Threat model + defense in depth** | `ciso-reviewer` | — |
| **S3–S5. Auth boundary, privilege escalation, data minimization** | `ciso-reviewer` | `staff-backend-engineer` |
| **S6. Secret lifecycle** | `ciso-reviewer` | `staff-platform-engineer` (provisioning) |

## Output format

Start with which domains were detected and which plan sections/phases were reviewed.

For each finding, state:
1. **Which checklist item** (ID and name, e.g., "B3 — Breaking intermediate states")
2. **Which plan section or phase** the finding applies to
3. **What the issue is** (one sentence)
4. **Why it matters** (one sentence)
5. **Suggested resolution** (concrete, not "consider improving")

If any items were flagged by B7 (scope creep), include an **Out of Scope** section listing them. The reviewer can decide whether to bring them into scope or create follow-up tickets.

End with a verdict: **Approve**, **Approve with changes** (list what), or **Request changes** (list blockers).

## Record review completion + deactivate

After delivering the verdict, write the completion marker and remove the active-session marker.

Write the completion marker only when the verdict is **Approve** or **Approve with changes** and all required changes have been applied to the plan. Do not write it on **Request changes** — write it only after the plan author revises the plan and a clean re-review completes.

<!-- HOOK_TEST_FIXTURE: record-completion — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's completion-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
SESSION_ID=$(cat "$HOME/.claude/sessions/$PPID") && [ -n "$SESSION_ID" ] && mkdir -p "$HOME/.claude/plan-review-markers" && REPO_HASH=$(git rev-parse --show-toplevel | tr -d '\n' | sha256sum | awk '{print $1}') && printf 'reviewed\n' > "$HOME/.claude/plan-review-markers/$REPO_HASH.$SESSION_ID"
```

Then remove the active-session marker:

<!-- HOOK_TEST_FIXTURE: deactivate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's active-marker cleanup. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
SESSION_ID=$(cat "$HOME/.claude/sessions/$PPID" 2>/dev/null) && [ -n "$SESSION_ID" ] && rm -f "$HOME/.claude/.plan-review-active.d/$SESSION_ID"
```

Removes only this session's file. If the skill errors out before reaching this step, don't manually clean up — the hook's 60-minute staleness cutoff handles the orphan automatically.
