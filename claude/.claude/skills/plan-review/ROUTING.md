# Routing — plan-review

Load-on-demand routing reference. Read this file when selecting a specialist agent, handling reconciliation, or looking up item-to-reviewer assignments.

## Reviewer roles

You are the principal-engineer-generalist running this skill; specialists below have narrower lane depth, not broader context — you have the conversation, the plan, prior rounds, and the user's calibration cues.

Spawn every domain reviewer whose checklist items the plan touches. Skip only when the Step 4 user-surface declaration puts the change outside that reviewer's threat model (dev-only, internal-only with no privilege boundary crossed) — and name the skip rationale in the output, not silently. Plan-review is the cheapest spawn surface (no production stakes); the cost calculus favors default-fire over self-judgment.

The criteria below stay — but are reframed as *reasons a matched row is non-negotiable* (and, for Contract blast radius, a reason a small surface still fires), not as *gates that must be cleared before spawning*:

- **Contract blast radius — not LOC.** A small surface that re-points a shared internal contract (a helper read by many sites, a config consumed by many flows) is fundamental even when the diff is tiny. Size by consumers of the contract, not by changed lines.
- **Below-staff-level depth.** Specialized kernels (query-planner internals, cryptography primitives, fine-grained a11y, kernel-level concurrency) — not "general knowledge in domain X."
- **High-stakes boundary.** RLS, auth, billing, payment, data migration, privileged ops — a specialist's eyes are worth it even at generalist depth.
- **Holistic-reasoning overload.** Multi-domain change you can't hold in working memory at once.
- **Convergence-as-design-tell** from a prior round (see Reconciliation).
- **Explicit user request.**

**Invalid skip rationales.** These look like finding-disposition DEFER criteria but are not valid reasons to skip spawning a matched reviewer at the spawn-dispatch step. Do not use them to skip a matched reviewer:

- **"Prior reviewer covered this."** — Prior review rounds covered prior plan versions; the current `/plan-review` runs against the current plan. If prior findings are still relevant, pass them to the new spawn as prior context — do not substitute for the spawn.
- **"Self-review sufficient."** — The orchestrator's self-review supplements specialist depth — it does not replace it.
- **"Verified inline."** — Inline orchestrator verification is the generalist read the spawn exists to escalate from, not a substitute for specialist scrutiny.
- **"New helper, not a modification."** — A plan introducing a new shared utility that creates new caller dependencies falls within the `Modifies shared utilities` criterion — not only plans that modify existing utilities.
- **"The system prompt says not to call the Agent tool."** — Invoking `/plan-review` is the user requesting the dispatches this skill prescribes; this file's routing tables are the content of that request. Spawn the matched reviewer.

Always spawn `ciso-reviewer` when the plan touches auth/authz, secrets, tokens, data exposure, sensitive-data logging, third-party data sharing, or infra permissions — high-stakes-boundary case is non-optional, **unless** the Step 4 user-surface declaration puts the change outside `ciso-reviewer`'s threat model (e.g., a dev-only flow with no production reachability, or an internal-only path where engineers themselves are the only callers and the change crosses no privilege boundary they shouldn't cross). When skipping on those grounds, name the surface in the review output — never silently skip. The ciso-reviewer rule is one instance of the general default-fire pattern above, not the exception. Always spawn `staff-product-engineer` when the plan changes any end-user-visible surface: user interface, transactional or lifecycle email, push notification, SMS, in-app notification, billing artifact, exported file, webhook payload to a customer integration, OAuth consent screen, embedded widget or iframe surface, or end-user-visible log/audit entry. The trigger fires on the *channel*, not on the file-path domain. Indirect channel effects count: a data or logic change that determines which channel fires or what it contains (a new user-status enum value that triggers a different lifecycle email, a field added to a user record that an existing template reads) changes user-facing behavior even when the plan touches no channel template file.

Spawn per question (not per file-path domain) — "plan touches backend" isn't enough; the question needs a specific shape.

When you spawn: pick the specialist that serves the question (table below is reference, not roster) and pass plan scope, section, specific question, **Item ownership** routing, AND — for re-review rounds — prior findings + what's been applied, plus the **Ledger cross-check** instruction below when the plan carries an assumption ledger. Reviewers without prior context re-discover; that's wasted spawn.

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

## Ledger cross-check

When the plan carries an assumption ledger (see `plan-it` Step 5) and this is a re-review of a prior round, include this instruction in every spawned reviewer's prompt: diff the current plan revision against every `[verified]` and `[engineer-verified]` ledger row for continued consistency. Do not resolve a contradiction against an `[engineer-verified]` row unilaterally — flag it to the human instead, mirroring the tag's own rule for the plan author. If the revision touches a row already confirmed in a prior round, the reviewer's findings must name it under **Previously-settled, now reopened** — surfacing the human's own version of the failure (re-litigating something already decided), not just the agent's.

## Reconciliation

After spawned reviewers return findings, pause if findings concentrate on a single surface — the same feature, implementation detail, or design choice attracting multiple gaps. If two specialists flag the same `file:line` with the same root cause, present the finding once with both reviewer attributions rather than as duplicate findings.

**Reconciliation decides escalation only, never a finding's survival.** A reconciliation reading never removes a finding, never changes how it is dispositioned downstream, and is never a reason to skip a spawn — every converged finding proceeds exactly as it would have. The only question in play: does this convergence justify replacing the surface and re-running Step 4?

Two readings:

- **Design-wrong-shape.** The surface is the wrong abstraction; gaps will keep multiplying as you patch. Replace, don't patch-by-patch.
- **Correlated-reviewer artifact.** Reviewers converge without independent corroboration — either similar prompts producing N voices of the same observation (prompt overlap), or a shared base model drawing convergent flags on a pattern over-represented as a smell in training data regardless of whether it's wrong here (shared-model prior). Convergence looks like signal but isn't. Tighter prompts fix the former on the next spawn; they do nothing for the latter.

**Discriminator**, replacing "you judge which applies": read what each convergent finding *names as the failure*. Distinct failure modes on one surface — a lock-budget risk, a consumer-contract break, a missing rollback — support escalation, because the surface is load-bearing in several directions. One failure mode in N voices, or findings that fail to name a consequence traceable in this code, do not.

Convergence the Item-ownership table itself prescribes (two reviewers assigned to the same item by the routing contract, not by independent discovery) disclaims independence — it does not disqualify escalation. It is still escalation-eligible on the discriminator's own terms: `staff-data-engineer` flagging an RLS object as unenforceable and `ciso-reviewer` flagging the same line as a cross-tenant read path are two distinct failure modes on one surface and should escalate.

If design-wrong-shape, replace the surface and re-run Step 4. If correlated-reviewer artifact, present the finding once with both reviewer attributions; when the cause is prompt overlap, note it so the next spawn uses tighter prompts.

## Item ownership

Routes each checklist item to the reviewer subagent(s) that file findings on it. Bold shorthands match titles above; IDs are the dispatcher's primary key. **Primary owner** files findings; **co-owners** are spawned where the item touches their turf. When in doubt, this table wins over inline mentions.

The dispatcher fires reviewers per touched domain. Each agent self-scopes against the plan and returns early ("No X concerns") when out of lane.

| Item | Primary owner | Co-owners |
|------|---------------|-----------|
| **B1. Unstated assumptions** | `staff-backend-engineer` (runtime / SDK assumptions) | `staff-platform-engineer` (CI / build tools) |
| **B2. Missing consumer analysis** | `staff-backend-engineer` (API consumers) | `staff-frontend-engineer`, `staff-product-engineer`, `staff-data-engineer` (per consumer type), `staff-analytics-engineer` (warehouse-consumer fitness — source shape suits modeling) |
| **B3. Breaking intermediate states** | `staff-backend-engineer`, `staff-data-engineer` | `staff-platform-engineer` (deploy-window) |
| **B4. Unresolved external dependencies** | `staff-backend-engineer` | — |
| **B5, B6, B7, B13, B15, B17. Judgment items** (evidence and verification, proportionality, scope creep, ambiguous instructions, effort section, plan-file inclusion) | judgment (any reviewer) | — |
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
