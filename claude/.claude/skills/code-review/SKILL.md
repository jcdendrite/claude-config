---
name: code-review
description: Principal engineer code review of changed/new code before presenting to user
allowed-tools: Read, Grep, Glob, Bash
---

Review the code that was just written or modified. Act as a principal engineer reviewing a junior engineer's work. Be thorough but not pedantic.

**Core principle: review the ripple effects, not just the change.** The checklist catches in-change issues; the ripple-effect triage step (end) catches cross-boundary impacts — a migration breaking frontend workflows, an API shape change breaking consumers, a rename breaking callers in another domain.

## Step 0 — Detect changed domains

Before reviewing, determine which files were changed (from context, git diff, or the conversation). Classify each changed file into one or more domains:

- **Infrastructure**: `.github/`, `*.tf`, `Dockerfile`, `docker-compose*`, CI/CD configs
- **Data infrastructure**: `**/migrations/**`, `*.sql` (outside warehouse-model paths), schema definitions, CDC / change-stream config, ETL/ELT pipeline code, warehouse ingestion connector configs
- **Analytics modeling**: `models/**/*.sql`, `models/**/*.yml`, `dbt_project.yml`, `macros/**`, `tests/**` (dbt-style), `seeds/**`, scheduled-query / view definitions in warehouse-config repos, semantic-layer files
- **Frontend**: `*.tsx`, `*.jsx`, `*.css`, `src/components/**`, `src/pages/**`
- **Backend**: edge functions, API routes, server-side utilities, `*.go`, `*.py`, NoSQL access-pattern code, schema design files (Prisma, ORM models, NoSQL ODM models)
- **Claude Code config**: `.claude/**`
- **Lovable config**: `.lovable/**`

Apply the **Base checklist** always. Apply each **Domain checklist** only when at least one changed file matches that domain.

Schema-touching diffs route three ways in parallel — backend (designs the schema), data infrastructure (operational / pipeline impact, DDL execution shape), analytics modeling (ELT-readiness review). Each agent self-scopes against the diff and returns early when out of lane.

## Step 1 — Implementation-fitness gate

Before reviewing for gaps, answer: **is the implementation appropriately sized for what the change needed to accomplish?** Gap-finding on an over-elaborate change elaborates it further, and the checklist won't surface "the whole implementation is the wrong shape."

Markers of over-elaboration:

- Captured outputs / artifacts / fields with no reader downstream.
- Layers that duplicate a higher-level abstraction already running the same work.
- Defensive paths against attack scenarios that haven't been observed.
- Granularity exceeding the actual consumer's need.
- Conditional logic for future phases that may not arrive.

If over-elaborated: stop. Surface the simpler implementation as the primary review output before any checklist item. Otherwise proceed to the checklist.

Question implementation choices, not feature scope. Feature scope is fixed by the ticket; implementation choice is reviewer-tunable.

## Base checklist

Evaluate the code against each item. Only flag items where there is a concrete issue — do not flag items just to show you checked them.

**Read every changed file fully, including generated ones.** Generated files (Supabase types, OpenAPI clients, GraphQL codegen) need the same scrutiny — runners like Vitest+esbuild strip types without validating, so npm warnings or build noise can leak into the file head undetected. Check `head -5` of generated files even when tests are green.

### Correctness

1. **API misuse** — Are libraries, frameworks, and language APIs used as designed? Flag any reliance on accidental or undocumented behavior (passing invalid arguments that happen to work, using internal methods, relying on side effects of unrelated calls).

2. **Error handling changes** — Are there catch blocks, fallback defaults, or error handlers that hide failures the caller would want to know about? Empty catches, catch-and-return-null, and catch-and-log-only are all suspects. If this change modifies error/fallback behavior, trace the error paths — most production incidents come from changed catch blocks and silent fallbacks, not happy-path logic.

3. **Race conditions** — Is shared mutable state accessed concurrently without synchronization? Check module-level variables, singletons, caches, and lazy-init patterns.

4. **Silent defaults for unexpected values** — Does the code silently substitute a default for an unexpected value (unknown enum variant, unrecognized config key)? In infrastructure and test code, prefer throwing over guessing.

5. **Feature flag coverage** — If the change adds or modifies feature flags or conditional rollout logic, are all flag states tested? Check for stale flags that are always-on/always-off and should be removed; verify the default-off state doesn't break existing behavior.

### Hygiene

6. **Dead exports** — Are there exported types, functions, or constants not imported by any other file? Check with grep before flagging.

7. **Unnecessary wrappers** — Are there functions that simply delegate to another function without adding logic, type narrowing, or meaningful naming?

8. **Inline business logic where a library method exists** — Is there hand-rolled logic (regex parsing, string manipulation, date math, data structure ops) where the project's existing dependencies already provide a tested function for the same thing?

### Clarity

9. **Undocumented limitations** — Does the code make assumptions or have known constraints invisible to future readers (only handling the first element, assuming single-tenant, ignoring edge cases by design)?

10. **Misleading names** — Do function or variable names promise more or less than they deliver? A `validateUser` that only checks one field, an `allItems` that holds a filtered subset.

### Security

11. **Test adequacy for security controls** — For code enforcing security invariants (access control, input validation, privilege boundaries), are there tests for both allow and deny paths? This overrides the general "add tests" exclusion — untested security controls are indistinguishable from absent ones. Check: for each boundary, is the unauthorized caller rejected AND the authorized caller accepted?

### Scope discipline

12. **Pre-existing issues in unchanged code** — If you notice issues in code NOT written or modified in this change, flag them in a separate "Pre-existing issues" section. Do NOT fix them — informational only, out of scope.

## Domain: Infrastructure

13. **Concurrency and parallelism scoping** — Do concurrency groups, mutex locks, or job dependencies match their intended scope? A workflow-level concurrency group affects all jobs, including no-op or unrelated ones — check that cancel-in-progress won't kill an important job due to an unrelated trigger.

14. **Secret exposure** — Are secrets used in contexts that could log them? Check for secrets in `run:` commands that echo or pipe output, in `env:` blocks visible to steps that don't need them, and in artifact uploads. Ensure secrets are not passed as command-line arguments (visible in process lists).

15. **Permissions least privilege** — Are workflow permissions, IAM roles, or service accounts scoped to what's actually needed? Flag `contents: write` when only `read` is required, `admin` when `write` suffices, or wildcard permissions.

16. **Idempotency** — Is the workflow/script safe to re-run? Check for unconditional creates (without "if not exists"), non-atomic operations that leave partial state on failure, and missing cleanup on retry.

17. **Trigger-condition alignment** — Do trigger filters (branch, path, actor, event type) match the job's purpose? A job intended only for bot commits but triggered on all pushes is a mismatch even if individual steps have `if` guards.

## Domain: Data

18. **Migration reversibility** — Destructive operations (`DROP COLUMN`, type narrowing, table drops) need a backup or reversal path.

19. **Index coverage** — New `WHERE`/`JOIN`/`ORDER BY` columns and foreign keys need supporting indexes, especially on growing tables.

20. **Lock safety** — Flag long-locking DDL on large tables — `ALTER` with defaults, `CREATE INDEX` without `CONCURRENTLY`, in-migration backfills.

21. **RLS and access control on new tables** — New tables exposed via auto-generated APIs (PostgREST, Hasura, generated resolvers) need row-security enabled with policies.

## Domain: Frontend

22. **Accessibility** — Do interactive elements have accessible names (aria-label, visible label, alt text)? Are click handlers on non-button elements keyboard-accessible? Check for missing focus management in modals and drawers.

23. **Render performance** — Are there new inline object/array/function literals in JSX props that would cause child re-renders on every parent render? Check for missing `key` props on list items and expensive computations not wrapped in `useMemo`/`useCallback` where the component re-renders frequently.

24. **Bundle impact** — Does the change add a large new dependency where a smaller alternative or existing utility exists? Flag full-library imports (e.g., all of lodash) when only one function is used.

25. **State-dependent rendering coverage** — Does the change modify which UI state a component enters (conditional branches, state machines, context-driven rendering)? If so, check whether tests verify the affected states render correctly. For each new or changed condition, is there a test that the component renders the expected output for each branch?

## Domain: Backend

26. **Auth boundary coverage** — Every new endpoint or RPC needs both authentication (who) and authorization (can they) — and the check must not be bypassable by hitting the endpoint outside the UI flow.

27. **Input validation at system boundaries** — Validate/sanitize user input before SQL, shell, file paths, or outbound API calls — framework parameterization counts, string concatenation does not.

28. **Error response leakage** — Error responses must not expose internal details (stack traces, internal IDs, DB error text, file paths) — log server-side, return generic to the client.

29. **Dependency upgrades** — Does the change upgrade a runtime or build dependency? Read the changelog for breaking changes. Check for peer dependency conflicts and, for frontend deps, bundle size regression.

30. **Third-party API integration** — Does the change add or modify a third-party API call? Verify retry/timeout behavior, credential scoping (least-privilege keys), and failure modes (API down, unexpected data).

31. **Sensitive data in logs** — Does the change add or modify logging? Verify logs do not include tokens, credentials, PII, or full API responses from auth/OAuth endpoints. Extract only the fields needed for debugging.

32. **Performance-sensitive code paths** — Does the change modify a hot path (queries in loops, N+1, cache read/write, large list ops)? Verify with representative data volumes, not just test fixtures.

## Domain: Claude Code config

For `.claude/skills/**/SKILL.md` review (frontmatter, trigger design, voice, length, behavior test, cross-reference vs duplication, and behavioral-equivalence audit on compressions), see the `skill-review` skill — do not assert behavioral equivalence on prose compressions yourself; that audit is `skill-review`'s job.

35. **Permission scope** — Do `permissions.allow` rules in settings.json follow least-privilege? Flag blanket allows (`"Bash"`) where scoped (`"Bash(git:*)"`) would suffice. If permissions.allow rules were added or modified, invoke `/review-permissions` for deep security analysis.

For hook reviews (`claude/.claude/hooks/*.sh`, hook entries in `settings.json`), see the `claude-hook-review` skill.

## Domain: Lovable config

Apply when changed files match `.lovable/**`. See the `lovable-knowledge` skill for the review checklist (perspective, specificity, scope split between project/workspace knowledge, char budget, sync status).

## Exclusions — do NOT flag these

- Issues that a linter, typechecker, or compiler would catch (imports, type errors, formatting)
- Stylistic nitpicks in unchanged code (naming conventions, whitespace, comment style)
- Generic improvement suggestions ("add tests," "add docs," "improve error messages") not tied to a specific finding from the checklist above, **except** for security controls (see item 11)
- Domain checklist items for domains where no files were changed

## Output format

Start with a one-line summary of which domains were detected (e.g., "Domains: Infrastructure, Backend").

If ripple-effect subagents were spawned, follow with a `Consulted:` line listing every persona by name, comma-separated and case-exact (e.g., "Consulted: ciso-reviewer, staff-backend-engineer"). Omit the line entirely when no subagents were spawned. The fixed prefix `Consulted: ` makes the spawn grep-able post hoc.

For each finding, state:

1. **Which checklist item** (by number and name)
2. **File and line**
3. **What the issue is** (one sentence)
4. **Why it matters** (one sentence)
5. **Suggested fix** (concrete, not "consider improving")

If no issues are found, say: "No issues found" — do not pad with praise or generic observations.

## Ripple effect triage

Same escalation logic as plan-review's Reviewer roles — repeated because skills load on demand, not because the rule differs.

After the implementation-fitness gate and the checklist review, consider whether the change crosses system boundaries in a way that exceeds your own judgment depth.

You are the principal-engineer-generalist running this skill; specialists below have narrower lane depth, not broader context. **Form your own ripple judgment first** using the table as a reference for what boundaries exist in the change.

This is the last gate before ship — a specialist miss here ships as a regression. Lean conservative; misses are not recoverable downstream the way they are at plan-review. Spawn a specialist when at least one applies:

- **Below-staff-level depth.** Specialized kernels (query-planner internals, cryptography primitives, fine-grained a11y, kernel-level concurrency) — not "general knowledge in domain X."
- **High-stakes boundary.** RLS, auth, billing, payment, data migration, privileged ops — a specialist's eyes are worth it even at generalist depth.
- **Holistic-reasoning overload.** Multi-domain ripple you can't hold in working memory at once.
- **Convergence-as-design-tell** from a prior round (see Reconciliation).
- **Explicit user request.**

Always spawn `ciso-reviewer` when the change touches auth/authz, secrets, tokens, data exposure, sensitive-data logging, third-party data sharing, or infra permissions — high-stakes-boundary case is non-optional, **unless** the change is declared dev-only or internal-only (no privilege boundary crossed) (e.g., a dev-only flow with no production reachability, or an internal-only path where engineers themselves are the only callers and the change crosses no privilege boundary they shouldn't cross). When skipping on those grounds, name the surface in the review output — never silently skip. Always spawn `staff-product-engineer` when the change alters user-facing behavior.

Spawn per question (not per file-path domain) — "change touches `.github/`" isn't enough; the question needs a specific shape.

When you spawn: spawn on the CODE, not on this review's output (each subagent reads the diff fresh); pick the specialist that serves the question (table is reference, not roster); pass diff scope, specific question, AND — for re-review — prior findings + what's been applied. Reviewers without prior context re-discover; that's wasted spawn.

The Change type column keys on what the change *does* for an operator or consumer, not on file types — a markdown-only diff can cross a runtime-config or CI/CD boundary if it restructures the taxonomy operators use to provision secrets, identify deploy targets, or reason about config layering. Use the table as a checklist of boundaries to *consider*, not as default-fire triggers.

| Change type | Spawn |
|-------------|-------|
| Restricts DB access (RLS, GRANT, triggers) | `ciso-reviewer` + `staff-backend-engineer` — trace restrictions against caller code and check for privilege escalation |
| Changes API response shape | `staff-product-engineer` + `staff-backend-engineer` — verify all consumers handle new shape |
| Adds/modifies security controls | `staff-sdet` + `ciso-reviewer` — verify test pyramid, coverage, and threat model |
| Changes auth model (JWT, roles, permissions) | `ciso-reviewer` + `staff-backend-engineer` — trace all auth paths including token refresh, session expiry, and error fallbacks |
| Modifies shared utilities (helpers, hooks, contexts) | `staff-backend-engineer` + `staff-frontend-engineer` — verify all call sites and check for behavioral assumptions |
| Changes data model (columns, types, defaults, migrations) | Route by change type: new nullable column, index, or view → `staff-backend-engineer` only; new table → `staff-backend-engineer` + `staff-analytics-engineer`; rename, drop, type change, NOT NULL constraint added, partition key, or RLS policy → `staff-backend-engineer` + `staff-data-engineer` + `staff-analytics-engineer`. Add `staff-product-engineer` if user-visible. |
| Adds or changes warehouse models / dbt transformations / semantic-layer files | `staff-analytics-engineer` (modeling, transformation correctness, materialization, test coverage) |
| Adds or changes CDC / change-stream / ETL/ELT pipeline / warehouse ingestion connector | `staff-data-engineer` (transport, schema-drift, observability) + `staff-platform-engineer` (operational footprint) |
| Modifies CI/CD pipelines or deploy config | `staff-platform-engineer` + `staff-backend-engineer` — verify pipelines and environment consistency |
| Changes runtime config (env vars, secrets, feature flags) | `staff-platform-engineer` + `ciso-reviewer` — verify config is consistent across environments, check for leaked secrets |
| Reshapes reviewer ownership (substantive edits to plan-review/code-review skill routing tables, or scope language in `agents/*.md`) | Spawn every persona named in the pre- or post-edit table — each evaluates whether their row (or its removal) is accurate, scoped, and not bleeding into another lane. The pre/post union ensures a row deletion still spawns the affected persona. For an `agents/*.md` edit, spawn the edited persona plus their Item-ownership co-owners. Skip whitespace / typo / copy-edit-only diffs. |

**Output:** State which boundaries you considered and what your generalist judgment was on each. If you escalated, list which specialists and why; if you didn't, name the boundary and your read. Empty findings on a boundary you considered is a valid result — write the read, don't omit it.

When you do spawn a specialist, be specific. "Spawn `ciso-reviewer`" is useless; "Spawn `ciso-reviewer` and ask it to verify the checkout flow in CheckoutPage.tsx still enforces ownership after the new validation" is actionable.

Specialist agents must return ≤2K tokens of structured findings (checklist-item-keyed bullets), not narrative prose. If findings genuinely exceed the budget, the agent must prioritize by severity and explicitly note that lower-severity items were omitted. When spawning, include this constraint in the agent prompt.

## Reconciliation

Same logic as plan-review's Reconciliation — repeated because skills load on demand.

After spawned reviewers return findings, pause if findings concentrate on a single surface — the same feature, implementation detail, or design choice attracting multiple gaps. If two specialists flag the same `file:line` with the same root cause, present the finding once with both reviewer attributions rather than as duplicate findings. Two readings:

- **Implementation-wrong-shape.** The surface is the wrong abstraction; gaps will keep multiplying as you patch. Replace, don't patch-by-patch.
- **Prompt-overlap artifact.** Reviewers given similar prompts produce N voices of the same observation. Convergence looks like signal but is framing-induced.

You judge which applies. Don't treat convergence as automatic authority for "patch each gap." If implementation-wrong-shape, replace the surface and re-run Step 1. If prompt-overlap, apply the underlying finding once, skip duplicates, and note the overlap so the next spawn uses tighter prompts.

## Item ownership

Routes each checklist item to the reviewer subagent(s) that file findings on it. Bold shorthands match titles above; numbers are the dispatcher's primary key. **Primary owner** files findings; **co-owners** are spawned where the item touches their turf. When in doubt, this table wins over inline mentions.

The dispatcher fires reviewers per file-path domain detection. Each agent self-scopes against the diff and returns early ("No X concerns") when out of lane.

| Item | Primary owner | Co-owners |
|------|---------------|-----------|
| **1. API misuse** | `staff-backend-engineer` (server-side library / SDK use) | `staff-platform-engineer` (build / CI tools), `staff-frontend-engineer` (client-side library use) |
| **2. Error handling changes** | `staff-backend-engineer` (server error paths), `staff-frontend-engineer` (client UX) | `ciso-reviewer` (sensitive-data leak), `staff-sdet` (catch-branch coverage) |
| **3. Race conditions** | `staff-backend-engineer` | — |
| **4. Silent defaults** | `staff-backend-engineer`, `staff-platform-engineer` (infra / test code) | — |
| **5. Feature flag coverage** | `staff-product-engineer` (default-off semantics) | `staff-platform-engineer` (rollout) |
| **6–8. Hygiene** (dead exports, unnecessary wrappers, inline business logic) | judgment (any reviewer) | — |
| **9. Undocumented limitations** | `staff-product-engineer` (user-visible limitations) | judgment (others) |
| **10. Misleading names** | `staff-product-engineer` (API / copy facing) | `staff-frontend-engineer` (component / hook), `staff-backend-engineer` (server) |
| **11. Test adequacy for security controls** | `ciso-reviewer` (designated writer) | `staff-sdet` (second-reader) |
| **12. Pre-existing issues in unchanged code** | judgment (any reviewer) | — |
| **13–17. Infrastructure** (concurrency scoping, secret exposure, least-privilege, idempotency, trigger alignment) | `staff-platform-engineer` | `ciso-reviewer` (14 secret exposure, 15 least-privilege) |
| **18. Migration reversibility** | `staff-data-engineer` (rollback safety, pipeline impact) | `staff-backend-engineer` |
| **19. Index coverage** | `staff-backend-engineer` (app-query coverage) | `staff-data-engineer` (DDL risk and bloat) |
| **20. Lock safety** | `staff-data-engineer` (DDL execution shape, pipeline impact) | `staff-platform-engineer` (deploy-window, lock-budget) |
| **21. RLS / access control on new tables** | `staff-data-engineer` (enforceability) | `ciso-reviewer` (threat framing) |
| **22. Accessibility** | `staff-frontend-engineer` (technical a11y) | `staff-product-engineer` (a11y as spec fidelity) |
| **23. Render performance** | `staff-frontend-engineer` | — |
| **24. Bundle impact** | `staff-frontend-engineer` | `staff-platform-engineer` (build tooling) |
| **25. State-dependent rendering coverage** | `staff-frontend-engineer` (branch implementation) | `staff-sdet` (test coverage), `staff-product-engineer` (right branches for spec) |
| **26. Auth boundary coverage** | `staff-backend-engineer` | `ciso-reviewer` |
| **27. Input validation at boundaries** | `staff-backend-engineer` | `ciso-reviewer` |
| **28. Error response leakage** | `staff-backend-engineer` | `ciso-reviewer` |
| **29. Dependency upgrades** | `staff-backend-engineer` (runtime deps) | `staff-platform-engineer` (CI / build deps) |
| **30. Third-party API integration** | `staff-backend-engineer` | `ciso-reviewer` (credential scoping) |
| **31. Sensitive data in logs** | `staff-backend-engineer` | `ciso-reviewer` |
| **32. Performance-sensitive paths** | `staff-backend-engineer` (app-level query patterns) | `staff-data-engineer` (DDL / index / read-path) |
| **35. Permission scope** | `ciso-reviewer` | — |
| **36. Hook correctness** — reviewed via `claude-hook-review` skill | `staff-platform-engineer` | `ciso-reviewer` |

## Step — Record review completion

If the review is **clean** (no blockers, no unresolved critical findings, and you reviewed the currently staged changes), record it by running this command exactly once:

<!-- HOOK_TEST_FIXTURE: marker-write — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/code-review/SKILL.md) to verify it matches require-code-review.sh's marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
SESSION_ID=$(cat "$HOME/.claude/sessions/$PPID") && [ -n "$SESSION_ID" ] && mkdir -p "$HOME/.claude/review-markers" && REPO_HASH=$(git rev-parse --show-toplevel | tr -d '\n' | sha256sum | awk '{print $1}') && git diff --cached | sha256sum | awk '{print $1}' > "$HOME/.claude/review-markers/$REPO_HASH.$SESSION_ID"
```

The `tr -d '\n'` is load-bearing: `git rev-parse` adds a trailing newline, and the hook computes the repo hash without it (`printf '%s' "$REPO_ROOT"`). Without `tr`, the marker lands at a path the hook never checks.

This writes the hash of the currently staged diff into a per-session marker keyed by `<repo-hash>.<session-id>`. The pre-commit hook reads the same session-id from its JSON payload and compares the staged-diff hash against THIS session's marker — match means the commit is allowed through. Per-session keying prevents two parallel sessions in the same worktree from overwriting each other's markers. Re-staging any change invalidates the marker automatically.

If the chain fails (empty `SESSION_ID`, etc.), the `capture-session-id.sh` SessionStart hook didn't run — abort and report; do not proceed without the marker, since `git commit` will be blocked by the gate.

**Do NOT write the marker if:**

- The review found blockers or unresolved critical findings
- You reviewed a different state than what is currently staged
- The user asked you to present findings without committing
- You are not in a git repository

If you skip it, say so explicitly so the user knows the commit gate will block until issues are fixed and the review is re-run on the final staged state.
