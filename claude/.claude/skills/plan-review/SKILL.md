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

Review an implementation plan. Act as a review board evaluating a proposal before
engineering effort is committed. Be thorough but practical — flag real risks, not
hypothetical ones.

## Step 0 — Identify the plan

Find the plan to review. Check, in order:
1. If a plan file path was provided as an argument, read it
2. If a plan was just written in `.claude/plans/`, read the most recent one
3. If a plan exists in the current conversation context, use that

## Step 1 — Detect domains

Read the plan and classify which domains it touches:

- **Infrastructure**: CI/CD, workflows, deployment, hosting, config files
- **Data infrastructure**: Database migrations, schema DDL, RLS policies, CDC / change-stream config, ETL/ELT pipelines, warehouse ingestion connectors, raw landing schemas, schema-drift handling
- **Application data**: Application schema design (relational tables, NoSQL document/item shape, partition keys, GSI/LSI design, application access patterns) — this routes through Backend
- **Analytics modeling**: Warehouse-side modeling (fact/dim, SCD, partitioning, materialization, dbt-shape transformations, semantic layer, scheduled queries) — and source-schema review for ELT-readiness when backend schema changes feed the warehouse
- **Frontend**: Client components, hooks, client-side state, UI behavior, routing, forms, optimistic mutations
- **Backend**: Server-side code (HTTP/RPC handlers, edge functions, background jobs, queue consumers, SDK integrations, shared utilities) AND application data-store schema design
- **Security**: Authentication or authorization, token handling, secret management, data exposure, RLS / RBAC / ACL changes

## Step 2 — Design-fitness gate

Before evaluating gaps, answer: **is the design appropriately sized for
the ticket scope?** Gap-finding on an over-elaborate design elaborates
it further (each finding closes a gap by adding more machinery), and
the checklist won't surface "this whole design is the wrong shape."

Markers of over-elaboration:

- Conditional logic for future phases that may not arrive.
- Defensive paths against attack scenarios that haven't been observed.
- Layers that duplicate a higher-level abstraction.
- Granularity exceeding any concrete consumer's need.
- Captured outputs / fields with no reader downstream.

If over-elaborated: stop. Surface the simpler design as the primary
review output before any checklist findings.

Otherwise (fit, or under-elaborated): proceed to Step 3 — gap-finding
will surface what's missing.

Question implementation choices, not feature scope. A ticket says
"implement X"; whether X is built with one layer or three is the gate's
inspection. The ticket itself isn't reviewed here — that goes back to
the author.

## Step 3 — Evaluate

Evaluate the plan against the **Base checklist** first, then each detected
**Domain checklist**. For multi-phase plans, evaluate each phase against the
relevant checklists. Reference the specific phase/section when reporting findings.

If this project also has a project-level plan-review skill, both skills will
trigger independently. This skill covers generic plan quality; the project
skill covers project-specific concerns.

## Base checklist

Evaluate the plan against each item. Only flag items where there is a concrete
issue — do not flag items just to show you checked them.

### Feasibility

B1. **Unstated assumptions** — Does the plan assume behavior of a library, framework,
   or SDK without verifying it? Check for claims about how APIs, clients, or protocols
   work. The most dangerous plans are the ones that sound correct but rely on behavior
   the author hasn't tested.

B2. **Missing consumer analysis** — Does the plan account for all callers, importers,
   or consumers of the code being changed? A plan that changes a response format
   without enumerating who reads that response will break things.

B3. **Breaking intermediate states** — During phased migrations, is there a window
   where some components use the old format and others use the new? Is that window
   safe, or will it cause runtime failures?

B4. **Unresolved external dependencies** — Does the plan depend on external services,
   APIs, or third-party tools whose availability, rate limits, or behavior the author
   hasn't verified? A plan that assumes an API endpoint exists or a service has a
   specific capability without checking is fragile.

B5. **Evidence** — Does the plan cite the source for each finding or assertion
   about the codebase? "Remove unused variable" should reference which file and
   line, which tool flagged it, or how it was discovered. Plans that state
   conclusions without evidence force reviewers to re-derive them.

### Scope

B6. **Proportionality** — Whole-design proportionality belongs to the gate
   (Step 2). At checklist time, flag local issues only: a helper that's
   overkill for one caller, an abstraction at a single call site.

B7. **Scope creep** — Does the plan include work that isn't required to solve the
   stated problem? Improvements to adjacent code, premature optimizations, or
   "while we're here" refactors should be captured in the **Out of Scope** section
   of the review output — don't lose the observation, but don't plan for it either.

B8. **Missing scope** — Does the plan omit work that IS required? Common gaps:
   test updates for breaking changes, documentation updates, migration rollback
   strategy, frontend changes for backend format changes.

### Risk

B9. **Phase independence** — For multi-phase plans, can each phase be merged and
   deployed independently without breaking the system? Can any phase be reverted
   without reverting all subsequent phases? Are there cross-phase dependencies
   that would leave the system in a broken state if a later phase is delayed?

B10. **Test realism** — Are the planned test assertions realistic given the changes?
   Will existing tests actually break as claimed? Are new test scenarios sufficient
   to catch regressions?

B11. **Rollback strategy** — For destructive or hard-to-reverse changes (data
   migrations, API format changes, dependency removals), is there a rollback plan?
   Or is the change structured to be safely reversible by default?

B12. **Dependency risk** — Does the plan add, upgrade, or remove dependencies? If so,
   does it account for transitive dependency conflicts, license implications, and
   the maintenance health of new dependencies?

### Clarity

B13. **Ambiguous instructions** — Could an implementer misinterpret the plan and
    produce the wrong result? Look for instructions that describe the wrong file,
    wrong pattern, or make claims about code structure that don't match reality.

B14. **Missing decision rationale** — Are design choices explained? A plan that says
    "use approach X" without explaining why X was chosen over Y leaves the implementer
    unable to make judgment calls when they encounter edge cases.

B15. **Effort section reality** — If the plan has an "Estimated Effort" (or
    similar) section, does it describe **review surface** — file count, domain
    complexity, risk concentration, what the reviewer needs to look at — rather
    than **implementation hours**? When Claude writes the code, hour-based
    estimates anchored in human coding speed mislead the reviewer about where
    to focus. Flag any effort section that cites hours, days, or "time to
    implement"; rewrite in review-surface terms.

## Domain: Infrastructure

Apply when the plan touches CI/CD, workflows, deployment, or config.

I1. **Environment parity** — Does the plan work the same across local, CI, staging,
    and production (OS, installed tools, permissions)?

I2. **Idempotency** — Can each infrastructure change be applied multiple times
    safely (migrations, deployments, config rollouts)?

I3. **Deployment ordering** — Does the plan make infrastructure ordering explicit
    when application changes depend on it (env var provisioned before code reads it,
    migration before new column access)?

I4. **Secret and config provisioning** — Does the plan specify where and how new
    secrets / env vars / config values are provisioned in each environment?

## Domain: Data

Apply when the plan touches database schema, migrations, pipelines, or warehouse
modeling. Schema-change plans are reviewed three ways: `staff-backend-engineer`
owns the design, `staff-data-engineer` owns the operational / pipeline impact and
DDL execution shape, and `staff-analytics-engineer` reviews the change for
ELT-readiness when the schema feeds the warehouse. See **Item ownership** below
for per-item routing.

D1. **Migration safety** — Does the plan describe how the migration runs on a live
    database without downtime — avoiding long-locking ALTERs, in-transaction
    backfills, rewrite-triggering type changes, and `CREATE INDEX` without
    `CONCURRENTLY` on large tables?

D2. **Migration reversibility** — Does the plan name a backup or reversal path for
    destructive operations (`DROP COLUMN`, `DROP TABLE`, type narrowing)?

D3. **Deploy-time compatibility** — Does the plan account for failures users hit
    mid-deploy when old code runs against new schema (or vice versa) — column
    renames, premature `NOT NULL` constraints?

D4. **Access control on new objects** — Does the plan declare row security / grants
    on new tables, views, and functions exposed via auto-generated APIs?

D5. **Index coverage** — Does the plan provide indexes for new query patterns
    (`WHERE` / `JOIN` / `ORDER BY` columns, foreign keys), especially on growing
    tables?

## Domain: Frontend

Apply when the plan touches React components, hooks, or client-side code.

F1. **User-facing impact** — Does the plan account for how changes affect the user
    experience? Error message changes, loading state changes, and behavioral changes
    should be called out explicitly.

F2. **State management** — Does the plan account for client-side state that depends
    on the changed backend behavior? Cached data, optimistic updates, and polling
    intervals may need updating.

F3. **Query contract mapping** — If the plan changes a backend response format, does
    the frontend consume the new shape correctly? Check that React Query keys,
    selector functions, and type definitions are updated to match the new contract.

F4. **Loading, error, and empty states** — Does the plan cover all three states for
    new or changed data-fetching paths? Plans that describe only the happy path leave
    the implementer to improvise error and empty states, which often results in
    missing or inconsistent UX.

F5. **Auth state transitions** — If the plan touches authentication or session
    handling, does it account for auth state transitions (logged-in to logged-out,
    token refresh, session expiry) and how they affect the UI? Stale auth state is
    a common source of broken UX.

## Domain: Backend

Apply when the plan touches edge functions, API routes, or server-side code.

K1. **Contract compatibility** — Does the plan maintain backward compatibility with
    existing callers during the transition? If not, is the breaking change coordinated
    with frontend/consumer updates?

K2. **Error handling completeness** — Does the plan cover both success and error paths
    for new or changed endpoints? Plans that only describe the happy path miss half
    the implementation.

## Domain: Security

Apply when the plan touches auth, authorization, secrets, tokens, or data exposure.

S1. **Threat model** — Does the plan identify what an attacker could do if the
    implementation has a bug? Plans that add auth or access control should enumerate
    bypass vectors.

S2. **Defense in depth** — Does the plan rely on a single control, or are there
    layered defenses? A plan that says "RLS will handle it" without in-code checks
    is single-layer.

S3. **Auth boundary coverage** — Does the plan specify both authentication (who)
    and authorization (can they) on every new endpoint, RPC, or data path?

S4. **Privilege escalation paths** — Does the plan close IDOR vectors, role-check
    gaps, and ownership-verification gaps for user-supplied IDs?

S5. **Data minimization** — Does the plan minimize exposure in API responses, logs,
    and error payloads (full-object returns, stack traces, internal IDs)?

S6. **Secret lifecycle** — Does the plan describe provisioning, storage, rotation,
    and revocation for secrets it introduces or references?

## Exclusions — do NOT flag these

- Style preferences (naming, formatting, file organization) unless they cause ambiguity
- "Consider adding" suggestions not tied to a specific checklist finding
- Theoretical risks with no concrete attack vector or failure scenario
- Domain checklist items for domains the plan doesn't touch
- Generic "add more tests" suggestions, **except** for security controls where
  untested invariants are indistinguishable from absent ones (see S1)

## Reviewer roles

You are the principal-engineer-generalist running this skill. The
specialist subagents below have narrower lane depth, not broader
context — you have the conversation, the plan in full, prior rounds
of review, and the user's calibration cues; they have one lane each.

**Default to your own review across all detected domains using the
checklists.** Spawn a specialist subagent only when at least one
escalation criterion applies:

- **Below-staff-level depth in a specific domain.** "General backend
  knowledge" doesn't qualify; specialized kernels do — query-planner
  internals, cryptography primitives, fine-grained accessibility,
  kernel-level concurrency, etc.
- **High-stakes domain boundary.** RLS, auth, billing, payment, data
  migration, privileged operations. A specialist's second pair of eyes
  is worth the spawn even at staff generalist depth.
- **Holistic-reasoning overload.** A multi-domain change where you
  genuinely can't hold all the lanes in working memory simultaneously.
- **Convergence-as-design-tell signal** from a prior round (see
  Reconciliation below).
- **Explicit user request** for specialist review.

Always spawn `ciso-reviewer` when the plan touches authentication or
authorization, secrets, tokens, data exposure, logging of sensitive
data, third-party data sharing, or infrastructure permissions —
high-stakes-boundary case is non-optional. Always spawn
`staff-product-engineer` when the plan changes user-facing behavior.

Spawn per question, not per file-path domain — "plan touches backend"
isn't enough; the question needs a specific shape.

When you spawn, pick the specific specialist that serves the question
(table below is reference, not roster) and pass: the plan scope, the
section under review, the specific question, the items routed per
**Item ownership**, AND — for re-review rounds — the prior round's
findings + what's been applied since. Reviewers without prior context
re-discover; that's wasted spawn.

| Domain | Agent | Focus |
|--------|-------|-------|
| Backend | `staff-backend-engineer` | API contracts, error handling, idempotency, retry semantics, service boundaries, SDK behavior, application data-store schema design (relational + NoSQL) |
| Frontend | `staff-frontend-engineer` | Component patterns, state management, data fetching and cache consistency, accessibility, i18n, UX impact |
| Security | `ciso-reviewer` | Threat modeling, auth boundaries, privilege escalation, data exposure, defense in depth |
| Data infrastructure | `staff-data-engineer` | Migration pipeline impact, DDL execution shape, CDC / change-stream config, ETL/ELT pipelines, warehouse ingestion transport, schema-drift detection, catalog / lineage tracking |
| Analytics modeling | `staff-analytics-engineer` | Warehouse-side modeling (fact/dim, SCD, partitioning, materialization), transformation correctness, source-schema review for ELT-readiness |
| Infrastructure | `staff-platform-engineer` | CI/CD, IaC, shell discipline, deployment ordering, secret provisioning; observability coverage, alerting, SLO impact, runbook linkage, load characteristics, cost / operational footprint; deploy-window ordering and lock-budget for migrations |
| Testing | `staff-sdet` | Testability of the design, edge cases the plan omits, test strategy coverage vs risk areas, test data requirements; production code with non-trivial logic that lacks tests |
| Product | `staff-product-engineer` | Whether the plan solves the actual user problem, UX impact during migrations, feature interactions, user-facing regressions hidden behind technical framing, telemetry event semantics |

Project-level plan-review skills may extend this table with
project-specific reviewer roles and focus areas, but must not remove or
narrow the `ciso-reviewer` trigger conditions.

## Reconciliation

After spawned reviewers return findings, pause if findings concentrate
on a single surface — the same feature, implementation detail, or
design choice attracting multiple gaps. Two readings:

- **Design-wrong-shape.** The surface is the wrong abstraction; gaps
  will keep multiplying as you patch. Replace, don't patch-by-patch.
- **Prompt-overlap artifact.** Reviewers given similar prompts produce
  N voices of the same observation. Convergence looks like signal but
  is framing-induced.

You judge which applies. Don't treat convergence as automatic authority
for "patch each gap." If design-wrong-shape, replace the surface and
re-run Step 2. If prompt-overlap, apply the underlying finding once,
skip duplicates, and note the overlap so the next spawn uses tighter
prompts.

## Item ownership

Routes each Base checklist item and each Domain checklist item to the
reviewer subagent(s) that file findings on it. The checklists above
define **what to look for**; this table defines **who looks**. Bold
shorthands match the item title in the body; IDs are the dispatcher's
primary key.

When in doubt, this table wins over inline mentions elsewhere.
**Primary owner** is the reviewer expected to file findings on the
item; **co-owners** are spawned where the item touches their turf.

The dispatcher's job is coarse — fire the relevant reviewers based on
which domains the plan touches. Each agent self-scopes against the
plan content and returns early ("No X concerns") when the work is out
of its lane. Trust the agents; don't second-guess at the dispatcher.

| Item | Primary owner | Co-owners |
|------|---------------|-----------|
| **B1. Unstated assumptions** | `staff-backend-engineer` (runtime / SDK assumptions) | `staff-platform-engineer` (CI / build tools) |
| **B2. Missing consumer analysis** | `staff-backend-engineer` (API consumers) | `staff-frontend-engineer`, `staff-product-engineer`, `staff-data-engineer` (per consumer type) |
| **B3. Breaking intermediate states** | `staff-backend-engineer`, `staff-data-engineer` | `staff-platform-engineer` (deploy-window) |
| **B4. Unresolved external dependencies** | `staff-backend-engineer` | — |
| **B5. Evidence** | judgment (any reviewer) | — |
| **B6. Proportionality** | judgment (any reviewer) | — |
| **B7. Scope creep** | judgment (any reviewer) | — |
| **B8. Missing scope** | `staff-product-engineer` (user-facing gaps), `staff-sdet` (test gaps) | `staff-data-engineer`, `staff-platform-engineer` (ops gaps) |
| **B9. Phase independence** | `staff-platform-engineer` | `staff-backend-engineer` |
| **B10. Test realism** | `staff-sdet` | `staff-product-engineer` (user-flow realism) |
| **B11. Rollback strategy** | `staff-data-engineer`, `staff-platform-engineer`, `staff-backend-engineer` | `staff-sdet` (testability of rollback) |
| **B12. Dependency risk** | `staff-backend-engineer` (runtime deps), `staff-platform-engineer` (CI / build deps) | — |
| **B13. Ambiguous instructions** | judgment (any reviewer) | — |
| **B14. Missing decision rationale** | `staff-product-engineer` (user-impact decisions) | judgment (others) |
| **B15. Effort section reality** | judgment (any reviewer) | — |
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

If any items were flagged by B6 (scope creep), include an **Out of Scope** section
listing them. These are observations worth preserving — the reviewer can decide
whether to bring them into scope or create follow-up tickets.

End with a verdict: **Approve**, **Approve with changes** (list what), or
**Request changes** (list blockers).
