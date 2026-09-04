---
name: plan-review
description: >
  Review implementation plans before presenting to the user. A plan file in .claude/plans/
  gates Write/Edit/MultiEdit/ExitPlanMode until this runs; triviality and user waivers do not release it.
  TRIGGER when: a plan is written or updated in .claude/plans/, or is about to be presented.
  DO NOT TRIGGER when: the plan lives outside .claude/plans/ (chat-level or /tmp drafts).
user-invocable: true
---

Review an implementation plan. Act as a review board evaluating a proposal before engineering effort is committed. Be thorough but practical — flag real risks, not hypothetical ones.

## Step 0 — Activate gate session

Write the active-session marker so this skill's own Write/Edit operations are not blocked by the `require-plan-review.sh` hook while the review is in progress:

<!-- HOOK_TEST_FIXTURE: activate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's active-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh activate plan-review
```

If the chain fails (empty `SESSION_ID`, etc.), `marker.sh` could not resolve this session's id — abort and report; do not proceed without the marker, since the hook would block any Write/Edit during the review.

If this session is in harness plan mode, the plan-mode system reminder names a file path under the Claude Code config directory's `plans/` subdirectory (e.g. `<config-dir>/plans/<slug>.md`) that the eventual `ExitPlanMode` call reads from directly — distinct from any plan committed under this repo's `.claude/plans/`.

1. **Condition:** a plan-mode system reminder is present in this session. Declare the exact plan-mode file path so `require-plan-review.sh` can gate `ExitPlanMode` against it.
2. **Action:**
   - Resolve this session's id via `~/.claude/scripts/marker.sh resolve-session-id` — a plain read, not a marker-shape write.
   - The write-authority restriction below doesn't apply to this read.
   - If it fails, abort and report — do not write a sibling file with no session id in its name.
   - Write the plan-mode file's path, with no trailing newline, to `<config-dir>/.plan-review-active.d/<the resolved id>.planmode-path`.
   - Use the Write tool for this write, not Bash.
   - Resolve `<config-dir>` to this session's actual config directory before the call.
   - Never paste the literal `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` expression into the Write tool's path argument — it is never shell-expanded there.
3. **Why Write, not Bash:** a Bash-written file at this path isn't covered by the same subagent-write restriction a Write tool call is, and `marker.sh` has no argument for this path — `marker.sh write plan-review` reads this file (via the same config-dir resolution) to decide which plan set the completion marker covers, falling back to the repo-relative plan set when it is absent.
4. **Skip** this sub-step entirely when no plan-mode reminder is present.

`marker.sh` invocations stay hardcoded to `~/.claude/scripts/marker.sh` across this repo, uniformly and by design — see `settings.json`'s `Bash(~/.claude/scripts/marker.sh …)` allow-rule and `enforce-marker-script-shape.sh`'s anchor, neither of which recognizes a config-dir-aware form.

<!-- HOOK_TEST_FIXTURE: declare-planmode-path — the hook-alignment test suite executes this recipe (a bash equivalent of the Write tool call above) to verify the resulting sibling file lands at the path and content require-plan-review.sh and marker.sh expect. Do not duplicate the recipe elsewhere; the test re-reads it from here. This fenced block is a pytest-executed simulation, never typed into an agent's Bash tool — test_skills.py's Trigger-A regression scan (see docs/worktree-bash-guard.md) excludes it by this same comment. -->
```bash
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SESSION_ID=$(~/.claude/scripts/marker.sh resolve-session-id) || exit 1
printf '%s' "$PLAN_MODE_FILE_PATH" > "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.planmode-path"
```

## Step 1 — Identify the plan

Find the plan to review. Check, in order:
1. If a plan file path was provided as an argument, read it
2. If a plan was just written in `.claude/plans/`, read the most recent one
3. If a plan exists in the current conversation context, use that

Resolve the identified file to an absolute path now (`pwd`-join a relative match; use an argument path as-is if already absolute) and carry that exact string forward — Output format's closing line states it, not a value re-derived at write time.

## Step 2 — Detect domains

Read the plan and classify which domains it touches. When using an agent to explore the codebase for plan context, use `general-purpose` — not `Explore`, which misses content past its read window and can't audit cross-file consistency — with an explicit `model: sonnet` per `CLAUDE.md`'s Model Routing rule.

- **Infrastructure**: CI/CD, workflows, deployment, hosting, config files
- **Data infrastructure**: Database migrations, schema DDL, RLS policies, CDC / change-stream config, ETL/ELT pipelines, warehouse ingestion connectors, raw landing schemas, schema-drift handling
- **Application data**: Application schema design (relational tables, NoSQL document/item shape, partition keys, GSI/LSI design, application access patterns) — this routes through Backend
- **Analytics modeling**: Warehouse-side modeling (fact/dim, SCD, partitioning, materialization, dbt-shape transformations, semantic layer, scheduled queries) — and source-schema review for ELT-readiness when backend schema changes feed the warehouse
- **Frontend**: Client components, hooks, client-side state, UI behavior, routing, forms, optimistic mutations
- **Backend**: Server-side code (HTTP/RPC handlers, edge functions, background jobs, queue consumers, SDK integrations, shared utilities) AND application data-store schema design
- **Security**: Authentication or authorization, token handling, secret management, data exposure, RLS / RBAC / ACL changes
- **Claude Code config**: New or modified CLAUDE.md, AGENTS.md, SKILL.md, agent files, hooks, memory files (<config-dir>/projects/*/memory/), or permissions.allow rules proposed by the plan

**Schema change routing:** Route schema changes by change type: new nullable column, index, or view → `staff-backend-engineer` only; new table → `staff-backend-engineer` + `staff-analytics-engineer`; rename, drop, type change, NOT NULL constraint added, partition key, or RLS policy → `staff-backend-engineer` + `staff-data-engineer` + `staff-analytics-engineer`. Add `staff-product-engineer` if user-visible.

## Step 2.5 — Load project-specific layer

If a project-specific layer exists for this skill, load it now. Glob for `.claude/skills/plan-review-*/SKILL.md` from the repo root (resolved via `git rev-parse --show-toplevel`); if exactly one matches, read it with the Read tool and merge its checklist into the items below. If multiple match, list them and stop — that's a config error in the project, not something this review resolves. If none match, proceed without a layer.

## Step 3 — Plan structure requirements

**NO PLACEHOLDERS.** Every step must name the actual file, function, or change. "TBD," "based on findings," and "implement later" defer decisions to execution time — when context is thinnest. Check: no conditional language in action items.

**BITE-SIZED STEPS.** Each step is one target, one change, one decision — if you'd need to pause and re-plan mid-execution, split it. Check: each step names one target and one change.

**CONTEXT-COMPLETE STEPS.** Each step contains file path, before/after description, and why the change is needed. Check: no step requires scrolling up for context.

A plan failing any of these is not ready to implement — return it to the author before evaluating gaps.

## Step 4 — Design-fitness gate

Before evaluating gaps, answer three questions in order:
1. **What user surface and threat model does this serve?** A line or two — production users, internal-only, dev-only, or whatever framing fits. Persona reviewers must scope findings to the declared surface, not default to a worst-case external-attacker model.
2. **Is the design appropriately sized for the user pain it solves on that surface?** Gap-finding on an over-elaborate design elaborates it further (each finding closes a gap by adding more machinery), and the checklist won't surface "this whole design is the wrong shape."

Markers of over-elaboration:
- Defensive layers beyond the declared user surface and threat model; conditional logic for phases that may not arrive; layers duplicating an existing abstraction; granularity exceeding any consumer's need.
- Captured outputs / fields with no downstream reader.
- "Could be done in N lines" stays valid even after personas shaped the plan.

3. **Are foundation-correctness tripwires clean?** These fire on observable plan text, not on judgment calls. If any fire, stop — output "Foundation concern: [one sentence]" + "Lighter alternative: [one sentence pointing to source]" as the primary output; do not spawn specialists until the foundation question is resolved.

   - **Over-powered primitive.** Plan uses a mechanism heavier, more invasive, or wider-scope than the task needs. Required: **at least two** lighter primitives named from the source documentation, each with a one-sentence justification for why it fails — the same threshold `plan-it` Step 5 sets for the author, so a one-alternative plan fails here rather than passing review while violating the authoring rule. If fewer than two are enumerated, the foundation is the finding, not the hardening on top of it.
   - **Compounding layers.** Plan stacks multiple layers (validation, retry, fallback, defense, schema-drift handling, etc.), each closing a gap the prior layer's existence created. Required: ask "what foundational change dissolves these?" before scoring any layer individually.
   - **Self-referential findings.** Plan cites its own prior findings ("addresses the gap from the previous draft," "closes the issue raised in the prior pass"). Required: treat each self-reference as evidence the foundation generates problems faster than patches close them.
   - **Misordered observe-then-mutate steps.** Plan caveats a step's output as possibly-stale or adds a re-check because a *later* step in the same plan changes the state that output reads. Required: re-sequence — move the observing step (check / read / capture / query) after the last step that mutates the state it reads — do not caveat a self-inflicted staleness.
   - **Overcorrection that negates a named allowance.** Plan introduces a blanket rule or revert whose text contradicts an allowance named in CLAUDE.md (e.g., "revert every out-of-scope edit" contradicts the §Working Style Axis-2 in-file opportunistic-refactoring license; "block all suppressions" contradicts a project-layer rule that permits them with rationale). A fix that trades one defect for its opposite is the finding — require the narrower rule that resolves the original defect without negating the named allowance. Fire only when the contradiction is observable in the plan text, not inferred from intent.
   - **Unjustified given.** A given is unjustified if the plan could change or remove dependence on it by editing any reachable repository's own artifacts — reach stops at the platform or protocol a mechanism runs on top of. Reach extends past the plan's own Critical-files list and past artifacts a third party merely owns. Examples: a peer repository's untouched script is in reach; a harness's hook contract, language runtime, or network protocol is not; `[engineer-verified]` doesn't exempt a condition from reach; a condition in reach the plan deliberately declines to change belongs in **Out of scope**, not among the givens. Required: name the given, name what puts it inside or outside reach, and state what the design becomes without it. Fires only on plans carrying a ledger; a missing givens line is an author-side gap (B8), not a foundation concern.
   - **Evidence restated across mechanisms.** Two or more mechanisms write the same measurement, citation, or investigation result into different files in full, rather than one holding it and the others pointing at it. Required: name the site that holds it and reduce the others to a pointer, or state per mechanism why its site must carry the evidence in full. A compressed summary that points at the holding site is not a finding; a rule restated at sites that must each stand alone is not one either.

If over-elaborated or any foundation tripwire fires: stop. Surface the simpler design or the foundation question as the primary review output before any checklist findings. Otherwise proceed to Step 5 — gap-finding will surface what's missing.

Question implementation choices and the conditions the design accepts as fixed. A condition that defines *what* the plan delivers is feature scope and goes back to the author; a condition that constrains *how* it delivers is in bounds — whether or not the plan lists the file that would change it.

If the plan carries an assumption ledger and this is a re-review round, note it here — the cross-check itself runs in each spawned reviewer's fresh context at Step 5 (see `ROUTING.md`'s Ledger cross-check).

## Step 5 — Evaluate

Evaluate the plan against the **Base checklist** first, then each detected **Domain checklist**. For multi-phase plans, evaluate each phase against the relevant checklists. Reference the specific phase/section when reporting findings. When the plan carries an assumption ledger, every spawned reviewer also gets the ledger cross-check instruction from `ROUTING.md` — diffing the revision against every `[verified]`/`[engineer-verified]` row for continued consistency.

## Base checklist

Evaluate the plan against each item. Only flag items where there is a concrete issue — do not flag items just to show you checked them.

### Feasibility

B1. **Unstated assumptions** — Does the plan assume library, framework, or SDK behavior without verifying it? Look for claims about API/client/protocol behavior the author hasn't tested.

B2. **Missing consumer analysis** — Does the plan account for all callers/importers/consumers of the changed code? A response-format change without enumerating consumers will break things. When the change re-points a read to a different data source, also enumerate the write paths to the prior source — they must now feed the new source or the contract drifts.

B3. **Breaking intermediate states** — During phased migrations, is there a window where mixed old/new components cause runtime failures?

B4. **Unresolved external dependencies** — Does the plan depend on external services, APIs, or tools whose availability, rate limits, or behavior the author hasn't verified?

B5. **Evidence and verification** — Does the plan cite a source for each finding/assertion (file:line, tool that flagged it, or how it was discovered)? When the plan asserts a specific code shape (function signature, exact line number, type field, import path), verify the cited source actually matches — citation alone is insufficient if the assertion misquotes it. **When the plan asserts a shape is "canonical," "the existing pattern," or "the convention," verify the cited example represents the broader population — run `git grep` and check the count. A single-call-site citation does not establish a pattern.** Conclusions without evidence force reviewers to re-derive them; misquoted or unrepresentative citations produce phantom code-review findings when the implementer correctly diverges. This extends to **external state the author cannot observe** — which env vars or secrets exist, CI/config contents, whether a migration was already applied, deployment status — assert these only with tool output or an explicit "unverified, confirm before relying on this" flag, never as fact.

**An invoked-then-abbreviated skill is a missing evidence base.** When the plan, or the session that produced it, names an invoked skill, verify its specified artifacts were produced — watch for the skill being reframed as a "lens," "philosophy," or "principle to keep in mind" rather than a set of steps to execute. A stated, reasoned abbreviation is not a finding; a silent one is. A rationale the invoked skill's own body already rebuts is not reasoned — re-read that body before accepting one.

### Scope

B6. **Proportionality** — Whole-design proportionality belongs to Step 4's gate. At checklist time, flag local issues only: a helper overkill for one caller, an abstraction at a single call site.

B7. **Scope creep** — Does the plan include work not required to solve the stated problem (adjacent improvements, premature optimization, "while we're here" refactors)? Capture these in **Out of Scope** — don't lose the observation, don't plan for it either.

B8. **Missing scope** — Does the plan omit work that IS required? Common gaps: test updates for breaking changes, documentation updates, migration rollback strategy, frontend changes for backend format changes.

### Risk

B9. **Phase independence** — Can each phase merge/deploy independently without breaking the system? Can any phase be reverted without reverting subsequent phases?

B10. **Test realism** — Are the planned test assertions realistic? Will existing tests actually break as claimed? Are new test scenarios sufficient to catch regressions?

B11. **Rollback strategy** — For destructive or hard-to-reverse changes (data migrations, API format changes, dependency removals), is there a rollback plan, or is the change structured to be safely reversible by default?

B12. **Dependency risk** — Does the plan add, upgrade, or remove dependencies? If so, does it account for transitive conflicts, license implications, and the maintenance health of new dependencies? When a plan removes a direct dependency and asserts the package remains available via transitive resolution, require verification that the project's package manager isolation model actually makes it visible to build tools — not all package managers hoist transitives to root-level resolution paths.

### Clarity

B13. **Ambiguous instructions** — Could an implementer misinterpret the plan and produce the wrong result? Look for instructions that describe the wrong file, wrong pattern, or make claims about code structure that don't match reality.

B14. **Missing decision rationale** — Are design choices explained? "Use approach X" without explaining why X over Y leaves the implementer unable to make judgment calls at edge cases.

B15. **Effort section reality** — If the plan has an "Estimated Effort" section, does it describe **review surface** (file count, domain complexity, risk concentration) rather than **implementation hours**? Hour-based estimates anchored in human coding speed mislead when Claude writes the code. Flag any effort section citing hours/days; rewrite in review-surface terms.

B16. **Tech-debt intersection** — Does the plan touch or expand an existing tech-debt mechanism (grandfathered-violations list, legacy shim, `// TODO: refactor` marker, dual code path)? The plan must explicitly choose surgical fix or justified expansion (flag as **missing scope (B8)** if silent); default to the surgical fix at solo/near-solo team scale.

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

K2. **Error handling completeness** — Does the plan cover both success and error paths for new/changed endpoints? If the plan introduces or modifies an error response envelope, code namespace, or call-site mapping pattern, invoke the `error-handling` skill for the standard.

## Domain: Security

Apply when the plan touches auth, authorization, secrets, tokens, or data exposure.

S1. **Threat model** — Does the plan identify what an attacker could do if the implementation has a bug? Plans that add auth or access control should enumerate bypass vectors.

S2. **Defense in depth** — Does the plan rely on a single control, or are there layered defenses? "RLS will handle it" without in-code checks is single-layer.

S3. **Auth boundary coverage** — Does the plan specify both authentication (who) and authorization (can they) on every new endpoint, RPC, or data path?

S4. **Privilege escalation paths** — Does the plan close IDOR vectors, role-check gaps, and ownership-verification gaps for user-supplied IDs?

S5. **Data minimization** — Does the plan minimize exposure in API responses, logs, and error payloads (full-object returns, stack traces, internal IDs)?

S6. **Secret lifecycle** — Does the plan describe provisioning, storage, rotation, and revocation for secrets it introduces or references?

## Domain: Claude Code config

Apply when the plan proposes new or modified content for `.claude/skills/**/SKILL.md`, `claude/.claude/agents/*.md` or `plugins/*/agents/*.md`, `CLAUDE.md`/`AGENTS.md`/`.claude/rules/*.md`/`<config-dir>/projects/*/memory/`, hooks (`claude/.claude/hooks/*.sh`, `settings.json` hook entries), or `permissions.allow` rules.

For SKILL.md content, invoke `skill-review` against the plan's drafted text. For agent-file content, invoke `agent-review`. Each owns frontmatter contract, trigger design, voice, length, behavior test, and cross-reference vs duplication for its file type.

For CLAUDE.md, AGENTS.md, memory-file, or path-scoped rule-file content, invoke `ai-instruction-and-memory-files` against the plan's drafted text — it owns placement (which surface), altitude, duplication, length cap, and the behavior test. Running it here, on the plan's proposed text, is the point: a placement or verbosity defect caught at `/code-review` has already been signed off on by the user at plan approval.

For hook content, invoke `claude-hook-review`. For `permissions.allow` rules, invoke `/review-permissions`.

## Exclusions — do NOT flag these

- Style preferences (naming, formatting, file organization) unless they cause ambiguity
- "Consider adding" suggestions not tied to a specific checklist finding
- Theoretical risks with no concrete attack vector or failure scenario
- Domain checklist items for domains the plan doesn't touch
- Generic "add more tests" suggestions, **except** for security controls where untested invariants are indistinguishable from absent ones (see S1)

## Reviewer routing

Read `${CLAUDE_SKILL_DIR}/ROUTING.md` with the Read tool before any spawn decision — a Bash read (`cat`, `sed`, `grep`) does not satisfy this gate.

## Output format

Start with which domains were detected and which plan sections/phases were reviewed. Then list spawned specialists with owned item IDs from ROUTING.md's Item ownership table (e.g., `staff-data-engineer: D1, D4; ciso-reviewer: S1, S3–S5, D4 co-ownership`); if none, write "No specialists spawned — generalist review only."

**Every finding a spawned reviewer returns must appear in the rendered output.** Before writing the verdict, cross-check the assembled findings list against what each spawned reviewer actually returned — its findings file when one was written, its inline return otherwise — a finding present there and omitted from the final output is a defect in this step, not a judgment call. A finding folded into another under Reconciliation's dedup rule (ROUTING.md) counts as present only if both reviewers are attributed at the merged entry.

For each finding, state:
1. **Which checklist item** (ID and name, e.g., "B3 — Breaking intermediate states")
2. **Which plan section or phase** the finding applies to
3. **What the issue is** (one sentence)
4. **Why it matters** (one sentence)
5. **Suggested resolution** (concrete, not "consider improving")

If any items were flagged by B7 (scope creep), include an **Out of Scope** section listing them. The reviewer can decide whether to bring them into scope or create follow-up tickets.

If any spawned reviewer's ledger cross-check finds the revision touching a row already confirmed in a prior round, include a **Previously-settled, now reopened** section naming the row and what changed — this surfaces the human's own version of the failure (re-litigating something already decided), not just the agent's.

<!-- DISPOSITION_RULE:plan-review-fix-or-ask start -->
**Enforcement-invariant findings are fix-or-ask.** When a finding is that the plan opens a path around an enforcement invariant — a gate, hook, permission check, required-approval, or marker guarantee that some mechanism currently makes unbypassable — the verdict may not be "Approve with changes: disclose in PR body." This finding class has exactly two dispositions: **Request changes** until the plan closes the hole, or a blocking one-line decision point via `AskUserQuestion` (e.g., "this design lets a UI flip bypass the full gate — accept?") before the verdict is finalized. Disclosure without a fix or explicit user acceptance is not informed consent — approval of a plan does not surface a hole buried mid-document to the human reading it.
<!-- DISPOSITION_RULE:plan-review-fix-or-ask end -->

End with the absolute path resolved in Step 1, then a verdict: **Approve**, **Approve with changes** (list what), or **Request changes** (list blockers).

## Record review completion + deactivate

Always remove the active-session marker at the end of the skill run, regardless of verdict — including **Request changes**, error, or user-cancel paths:

<!-- HOOK_TEST_FIXTURE: deactivate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's active-marker cleanup. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh deactivate plan-review
```

Then write the completion marker, but only when the verdict is **Approve** or **Approve with changes** and all required changes have been applied to the plan. Do not write it on **Request changes** — write it only after the plan author revises the plan and a clean re-review completes.

<!-- HOOK_TEST_FIXTURE: record-completion — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/plan-review/SKILL.md) to verify it matches require-plan-review.sh's completion-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh write plan-review
```
