---
name: code-review
description: "Principal-engineer review before presenting code. Also the gate on `git commit`. TRIGGER when: code is about to be presented, a commit is pending, or the user asks for a review. skill-review, agent-review, and ai-instruction-and-memory-files run from inside this review, never instead of it; plan-review gates separately and also fires. DO NOT TRIGGER when: a fresh code-review-markers/ entry already covers the staged diff."
allowed-tools: Read, Grep, Glob, Bash
---

Review the code that was just written or modified. Act as a principal engineer reviewing a junior engineer's work. Be thorough but not pedantic.

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

## Step 0.5 — Load project-specific layer

If a project-specific layer exists for this skill, load it now. Glob for `.claude/skills/code-review-*/SKILL.md` from the repo root (resolved via `git rev-parse --show-toplevel`); if exactly one matches, read it with the Read tool and merge its checklist into the items below. If multiple match, list them and stop — that's a config error in the project, not something this review resolves. If none match, proceed without a layer.

## Step 0.6 — Pre-judgment table check

Before forming any ripple judgment in Step 1 or the checklist, enumerate every row in the Change-type table (under *Ripple effect triage*) that matches the diff. Hold the list in working memory through the rest of the review — each row is an anchor the spawn decision must address. This step exists because the table is the institutional memory; the parent's first-pass judgment is not. Form judgment *against* the table, not in lieu of it. For compound diffs that match multiple rows, narrow the diff scope per spawn — pass each specialist only the file(s) within their lane, not the full diff, so their findings stay in-scope.

<!-- SCOPE_RULE:code-review-staged-diff-only start -->
When the diff under review is the currently-staged diff and `HEAD` is not the default branch, every spawned row's responsibility is bounded to that diff. The default branch is resolved via the same mechanism `require-ready-for-review.sh` uses: `git symbolic-ref --quiet refs/remotes/origin/HEAD`, falling back to checking `main`/`master`/`develop` as candidates only when that ref is unset. A spawn's exhaustive-enumeration duty covers only the boundary. <!-- SCOPE_RULE:code-review-causal-reach start -->A defect outside the boundary that the change causes, activates, or newly reaches stays in scope for that spawn's flagging duty.<!-- SCOPE_RULE:code-review-causal-reach end --> The boundary is passed as file paths with added/modified line ranges, not a ref expression, because not every reviewer carries `Bash`. This narrows only speculative, undirected search for unrelated defects — it never suppresses disclosure of a defect a spawn already noticed during the full-file read required elsewhere in this skill. Two exceptions stay in scope regardless of the boundary:

- A matched Change-type row's own directed causal-reach instruction (e.g. "verify all consumers," "trace all auth paths").
- Checklist item 14's duty to always flag a pre-existing issue you notice, which is unconditional and unaffected by this rule.

Every other context enumerates and is responsible for the full diff, unnarrowed:

- The cumulative PR-vs-base pass.
- A presentation-path review.
- An ad-hoc review.
- A commit to the default branch.

These are illustrative examples of contexts that typically fail the test above, not a separate trigger-based rule. The discriminator is always whether the diff actually under review is exactly `git diff --cached` on a `HEAD` that is not the default branch, not which of these triggers fired.

The row <!-- SCOPE_EXEMPT_ROW start -->Adds or modifies a comment or durable-doc prose beyond a hygiene tweak (code comments, docstrings, `REFERENCES.md`, doc files, README sections, skill/agent bodies)<!-- SCOPE_EXEMPT_ROW end --> is additionally match-narrowed on top of this: it does not spawn at all when the boundary carries no comment/durable-doc prose.
<!-- SCOPE_RULE:code-review-staged-diff-only end -->

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

## Step 1.5 — Judgment-activation pass

Evaluate the diff against CLAUDE.md §Engineering Judgment, §Working Style, and §Code Comments, Documentation, and Prose — being loaded is not the same as being applied. The tripwires below fire on **diff surface**, not on internal reasoning, so they catch judgment-class defects even when the author's reasoning reads as coherent:

- **Unverified external-state claim** — the diff or its commit message asserts state the author cannot observe (which env vars or secrets exist, CI/config contents, whether a migration was already applied, git blame or authorship, that a referenced file exists on `main`) without tool output or an explicit "unverified" flag. Default to flag — the model cannot observe external state by reading source files.
- **Out-of-scope file edits** — files changed that the stated task did not require, especially copy, comment, or cosmetic edits on unrelated files. Distinct from item 14 (don't *fix* unchanged code): this is having *edited* what was out of scope.
- **Preserved-record edits** — edits to already-applied migrations, changelog or incident records, or anchor fixtures.
- **Non-durable comment** — a new or modified comment that narrates PR/incident history, references "this diff," or re-litigates a rejected alternative at length, rather than stating a durable fact about the code. This check always runs inline, regardless of whether `comment-discipline-reviewer` (checklist item 12a) is spawned for exhaustive enumeration.

## Base checklist

Evaluate the code against each item. Only flag items where there is a concrete issue — do not flag items just to show you checked them.

**Read every changed file fully, including generated ones.** Generated files (Supabase types, OpenAPI clients, GraphQL codegen) need the same scrutiny — runners like Vitest+esbuild strip types without validating, so npm warnings or build noise can leak into the file head undetected. Check `head -5` of generated files even when tests are green.

### Correctness

1. **API misuse** — Are libraries, frameworks, and language APIs used as designed? Flag any reliance on accidental or undocumented behavior (passing invalid arguments that happen to work, using internal methods, relying on side effects of unrelated calls).

2. **Error handling changes** — Are there catch blocks, fallback defaults, or error handlers that hide failures the caller would want to know about? Empty catches, catch-and-return-null, and catch-and-log-only are all suspects. If this change modifies error/fallback behavior, trace the error paths — most production incidents come from changed catch blocks and silent fallbacks, not happy-path logic. If the change touches error response shape, code namespace, or call-site mapping, invoke the `error-handling` skill for the standard.

3. **Race conditions** — Is shared mutable state accessed concurrently without synchronization? Check module-level variables, singletons, caches, and lazy-init patterns.

4. **Silent defaults for unexpected values** — Does the code silently substitute a default for an unexpected value (unknown enum variant, unrecognized config key)? In infrastructure and test code, prefer throwing over guessing.

5. **Feature flag coverage** — If the change adds or modifies feature flags or conditional rollout logic, are all flag states tested? Check for stale flags that are always-on/always-off and should be removed; verify the default-off state doesn't break existing behavior.

### Hygiene

6. **Dead exports** — Are there exported types, functions, or constants not imported by any other file? Check with grep before flagging.

7. **Unnecessary wrappers** — Are there functions that simply delegate to another function without adding logic, type narrowing, or meaningful naming? The same principle applies at module level: if a file's entire post-refactor content is re-export statements from a single other module (and it no longer holds any unique declarations), suggest deleting the shim and updating importers directly. This is not a breaking change if all callers are in the same repo.

8. **Inline business logic where a library method exists** — Is there hand-rolled logic (regex parsing, string manipulation, date math, data structure ops) where the project's existing dependencies already provide a tested function for the same thing?

9. **Repeated in-house logic that should be extracted** — Is the same non-trivial logic block (≥~5 lines, or any block with semantic identity beyond formatting) repeated in three or more sites in this codebase, where a single shared helper would carry the same behavior? The default threshold is three instances, but it's a default, not a floor or ceiling: extraction can be right below it for a single domain concept, and wrong above it when the repetition is coincidental rather than semantic. Suggest the extraction site (`_shared/<helper>.ts`, `lib/<helper>.ts`, or the codebase's established shared-utility location) and name what the helper should encapsulate, not just "DRY this up." Carve-out: production code is DRY; **test code is DAMP** — repeated arrange/assert blocks in tests often aid readability over abstraction, so do not flag test-file duplication unless the repeated block is large enough to obscure the test's intent.

9a. **Repeated domain discriminants without a shared type** — Is the same string literal representing a domain concept (tier, role, status, event type) used as a discriminator at 3 or more sites without a shared named type? Flag and suggest the language's idiomatic named-type construct (TypeScript string-literal union, Java/Kotlin/C# enum, Python `Literal[...]` or `Enum`, Rust enum, Go typed-string constant set) declared at the canonical module for that domain, with call sites replaced by the named type.

9b. **Unnamed semantic bounds in slicing/truncation** — Does the code pass a bare numeric literal as a length, prefix, or offset bound when slicing, truncating, or windowing a string or collection in non-test code? If the number represents a semantic bound (prefix length for redaction, maximum field width, ID segment length), it should be a named constant expressing the intent, not a bare number. Suggest a named constant at the shared-utility level if the bound is used across files, or at the top of the file in the language's idiomatic constant declaration if local.

9c. **Ungrounded numeric literal in network/timeout/retry context** — Does the diff introduce a bare numeric literal as a timeout, retry count, interval, or network parameter without a citation of the vendor or protocol documentation that specifies the value? Flag and require a primary-source citation or a named constant with a link in a comment.

9d. **Lint or type-check suppression without rationale** — Run a literal text search of the diff for suppression tokens (`eslint-disable`, `@ts-ignore`, `@ts-expect-error`, `# noqa`, `# type: ignore`, `//nolint`, `# pylint: disable`, `istanbul ignore`); do not rely on noticing directives during read-through. For each hit the diff adds: is there a one-line comment naming the alternative considered and why it does not apply? Flag — presence of a reason, not its quality, is the check.

9e. **New third-party dependency without provenance research** — Does the diff add a new runtime or dev dependency (any package.json, requirements.txt, go.mod, Cargo.toml addition) without evidence of vulnerability-history and maintenance-health research? Flag and require the source-of-choice rationale to be named (in the PR description, a comment, or a commit message note).

9f. **Brittle structural test assertion** — Does the diff add a test that asserts on specific field values in runtime/structured output (log lines, JSON, XML, generated config) by applying regex or string-matching to raw text rather than (a) parsing with a library and asserting on the resulting object, or (b) calling the production parse/validate function? The regex re-implements logic the code owns: the test passes when production parsing is broken and breaks on format changes. Does not apply when the assertion only checks the output's format envelope (e.g., that the output is valid JSON or matches a known line format) rather than asserting on specific field values — that is a legitimate format-contract test. See `test-conventions` §9.

9g. **Source-scanning test assertion** — Does the diff add a test that reads a source, DDL, or generated file as a string and regex-matches or substring-matches against its literal text rather than executing the code and asserting on observable behavior? Reading source proves the literal text exists, not that it runs, is reachable, or behaves correctly; the assertion passes on dead code and fails on cosmetic renames. See `test-conventions` §9.

9h. **Construct-substitution suppression** — Does the diff silence a warning via a language construct rather than a directive — `void` applied to a promise in an async/dispatch context, an empty catch, a broad type assertion, an unused-variable rename that hides a real value? Check whether the construct introduces its own defect class or is itself flagged by standard analyzers under stricter reliability configurations — either way it hides the signal without handling the condition, equivalent to a disable directive minus the rationale comment. Prefer explicit handling: an explicit catch, or an intentional, named fire-and-forget wrapper. For casts reaching mock state in test files, `test-conventions` §8 names the prescriptive fix (the framework's typed mock accessor) — prefer that over a rationale-commented cast.

### Clarity

10. **Undocumented limitations** — Does the code make assumptions or have known constraints invisible to future readers (only handling the first element, assuming single-tenant, ignoring edge cases by design)?

11. **Misleading names** — Do function or variable names promise more or less than they deliver? A `validateUser` that only checks one field, an `allItems` that holds a filtered subset.

12. **Stripped WHY comments** — In modified files, were comments documenting a non-obvious constraint, subtle invariant, bug workaround, or surprising behavior deleted? Stripping these regresses documentation that the original author judged worth keeping. A preference for minimal comments governs whether to *add* one; it does not authorize bulk removal during unrelated edits. Apply the same WHY test to existing comments: keep if it meets the standard, remove only if it restates WHAT the code does. Check `git diff` for deleted comment lines in changed hunks.

12a. **Comment/prose discipline on added or modified text** — Does a new or modified comment or durable-doc paragraph violate CLAUDE.md §Code Comments, Documentation, and Prose?
   - Comment verbosity — a multi-paragraph rationale where one line suffices
   - Multi-fact comment structure — several independent facts chained into one run-on instead of split or listed
   - Prose at the wrong altitude for its reader
   - PR-defined terminology
   - "Used to be X" framing
   - Durable-doc content that fails the survives-the-PR-being-merged self-test

   Distinct from item 12, which covers comment *deletion*. Step 1.5's "Non-durable comment" tripwire runs this check inline on every review; `comment-discipline-reviewer` (Ripple effect triage) provides exhaustive enumeration when spawned.

### Security

13. **Test adequacy for security controls** — For code enforcing security invariants (access control, input validation, privilege boundaries), are there tests for both allow and deny paths? This overrides the general "add tests" exclusion — untested security controls are indistinguishable from absent ones. Check: for each boundary, is the unauthorized caller rejected AND the authorized caller accepted?

### Scope discipline

14. **Pre-existing issues in unchanged code** — If you notice issues in code NOT written or modified in this change, flag them in a separate "Pre-existing issues" section. Do NOT fix them — informational only, out of scope.

14a. **Config-file change without stated intent** — Does the diff change a project config file (build config, tooling config, dependency manifest, lockfiles — `package-lock.json` and equivalents, compiler/analyzer options, infrastructure-as-code such as Terraform or Kubernetes manifests) without its intent being evident — an inline comment where the format allows comments, or an explicit mention in the PR description or commit message? A config diff reads as structural; a reviewer cannot distinguish intentional cleanup from accidental deletion without stated rationale. Flag when neither exists. When the changed file also matches an Infrastructure-domain item (15–19) and the finding content overlaps (the same permission/trigger/idempotency delta), report once under the more specific item and note the missing rationale there — reserve 14a for config changes no domain item absorbs.

## Domain: Infrastructure

15. **Concurrency and parallelism scoping** — Do concurrency groups, mutex locks, or job dependencies match their intended scope? A workflow-level concurrency group affects all jobs, including no-op or unrelated ones — check that cancel-in-progress won't kill an important job due to an unrelated trigger.

16. **Secret exposure** — Are secrets used in contexts that could log them? Check for secrets in `run:` commands that echo or pipe output, in `env:` blocks visible to steps that don't need them, and in artifact uploads. Ensure secrets are not passed as command-line arguments (visible in process lists).

17. **Permissions least privilege** — Are workflow permissions, IAM roles, or service accounts scoped to what's actually needed? Flag `contents: write` when only `read` is required, `admin` when `write` suffices, or wildcard permissions.

18. **Idempotency** — Is the workflow/script safe to re-run? Check for unconditional creates (without "if not exists"), non-atomic operations that leave partial state on failure, and missing cleanup on retry.

19. **Trigger-condition alignment** — Do trigger filters (branch, path, actor, event type) match the job's purpose? A job intended only for bot commits but triggered on all pushes is a mismatch even if individual steps have `if` guards.

## Domain: Data

20. **Migration reversibility** — Destructive operations (`DROP COLUMN`, type narrowing, table drops) need a backup or reversal path.

21. **Index coverage** — New `WHERE`/`JOIN`/`ORDER BY` columns and foreign keys need supporting indexes, especially on growing tables.

22. **Lock safety** — Flag long-locking DDL on large tables — `ALTER` with defaults, `CREATE INDEX` without `CONCURRENTLY`, in-migration backfills.

23. **RLS and access control on new tables** — New tables exposed via auto-generated APIs (PostgREST, Hasura, generated resolvers) need row-security enabled with policies.

## Domain: Frontend

24. **Accessibility** — Do interactive elements have accessible names (aria-label, visible label, alt text)? Are click handlers on non-button elements keyboard-accessible? Check for missing focus management in modals and drawers.

25. **Render performance** — Are there new inline object/array/function literals in JSX props that would cause child re-renders on every parent render? Check for missing `key` props on list items and expensive computations not wrapped in `useMemo`/`useCallback` where the component re-renders frequently.

26. **Bundle impact** — Does the change add a large new dependency where a smaller alternative or existing utility exists? Flag full-library imports (e.g., all of lodash) when only one function is used.

27. **State-dependent rendering coverage** — Does the change modify which UI state a component enters (conditional branches, state machines, context-driven rendering)? If so, check whether tests verify the affected states render correctly. For each new or changed condition, is there a test that the component renders the expected output for each branch?

## Domain: Backend

28. **Auth boundary coverage** — Every new endpoint or RPC needs both authentication (who) and authorization (can they) — and the check must not be bypassable by hitting the endpoint outside the UI flow.

29. **Input validation at system boundaries** — Validate/sanitize user input before SQL, shell, file paths, or outbound API calls — framework parameterization counts, string concatenation does not.

30. **Error response leakage** — Error responses must not expose internal details (stack traces, internal IDs, DB error text, file paths) — log server-side, return generic to the client. For the canonical error envelope shape and code-namespace rules, invoke the `error-handling` skill.

31. **Dependency upgrades** — Does the change upgrade a runtime or build dependency? Read the changelog for breaking changes. Check for peer dependency conflicts and, for frontend deps, bundle size regression.

32. **Third-party API integration** — Does the change add or modify a third-party API call? Verify retry/timeout behavior, credential scoping (least-privilege keys), and failure modes (API down, unexpected data).

33. **Sensitive data in logs** — Does the change add or modify logging? Verify logs do not include tokens, credentials, PII, or full API responses from auth/OAuth endpoints. Extract only the fields needed for debugging.

34. **Performance-sensitive code paths** — Does the change modify a hot path (queries in loops, N+1, cache read/write, large list ops)? Verify with representative data volumes, not just test fixtures. For read-path SELECT query conventions (explicit limits, N+1 from SELECT iteration, explicit column selection), invoke the `sql-query-conventions` skill.

34a. **Untyped error raised where the file uses typed-error dispatch** — If the file defines or imports typed error variants and dispatches on them by type (catch-arm narrowing, `instanceof` / `isinstance` / `errors.As`, sum-type pattern match), any untyped/generic error raised for a parallel failure path will bypass the typed arm and fall to the generic handler. Flag any generic-error raise, throw, or error-return in the same file that shares the logical shape of an existing typed variant. The fix is a typed variant — not widening the dispatch.

## Domain: Claude Code config

For `.claude/skills/**/SKILL.md` review, invoke `skill-review`. For `claude/.claude/agents/*.md` and `plugins/*/agents/*.md` review, invoke `agent-review`. Each owns frontmatter contract, trigger design, voice, length, behavior test, cross-reference vs duplication, and behavioral-equivalence audit on compressions for its file type — do not assert behavioral equivalence on prose compressions yourself; that audit belongs to the dispatched skill.

For `CLAUDE.md`, `AGENTS.md`, and `<config-dir>/projects/*/memory/` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) review, invoke `ai-instruction-and-memory-files`. It owns placement (which surface), altitude, duplication, length cap, and behavior test for instruction and memory files.

35. **Permission scope** — Do `permissions.allow` rules in settings.json follow least-privilege? Flag blanket allows (`"Bash"`) where scoped (`"Bash(git:*)"`) would suffice. If permissions.allow rules were added or modified, invoke `/review-permissions` for deep security analysis.

For hook reviews (`claude/.claude/hooks/*.sh`, hook entries in `settings.json`), invoke the `claude-hook-review` skill.

When changed files are inside a plugin directory (identified by `.claude-plugin/plugin.json` present in the plugin's tree), or when `.claude-plugin/marketplace.json` is modified, invoke `plugin-semver` for version-bump discipline (hook-enforced at commit time by `require-plugin-version-bump.sh`, which ships with the `plugin-semver` plugin).

## Domain: Lovable config

Apply when changed files match `.lovable/**`. Invoke the `lovable-cloud-knowledge` skill for the review checklist (perspective, specificity, scope split between project/workspace knowledge, char budget, sync status).

## Exclusions — do NOT flag these

- Issues that a linter, typechecker, or compiler would catch (imports, type errors, formatting)
- Stylistic nitpicks in unchanged code (naming conventions, whitespace, comment style)
- Generic improvement suggestions ("add tests," "add docs," "improve error messages") not tied to a specific finding from the checklist above, **except** for security controls (see item 13)
- Domain checklist items for domains where no files were changed

## Output format

Start with a one-line summary of which domains were detected (e.g., "Domains: Infrastructure, Backend").

Follow with a mandatory **Spawn decisions:** line. For every Change-type table row that matched the diff (per Step 0.6's enumeration), list it with a one-line verdict:

- `spawned: <agent> for <specific question>` — the question must be a concrete sentence, not the row's title.
- `skipped: <row> — <reason>` — name the row by its left-column shorthand and give a one-sentence rationale.

When no Change-type rows match the diff, write: *"Spawn decisions: no Change-type rows matched the diff."* This makes the absence affirmative, not silent. Empty rationale is the under-spawn failure mode this format closes; visible rationale is reviewable.

The **Spawn decisions:** line also states whether the staged-diff responsibility boundary applied to this invocation, with a short tag naming the specific reason it did or didn't — e.g., `scope: staged-diff (narrowed)` when it applied, or `scope: cumulative diff (unnarrowed — RFR cumulative pass)` / `scope: cumulative diff (unnarrowed — default-branch guard)` when it didn't — not a restatement of the whole rule.

**Project-layer scope:** the mandatory Spawn decisions format is base-skill-only. Project-layer skills (`code-review-*/SKILL.md`, loaded at Step 0.5) compose by extending checklists — they do not override the output format. A project layer that wants to add fields should do so via an additive section, not by replacing the base format.

For each finding, state:

1. **Which checklist item** (by number and name)
2. **File and line**
3. **What the issue is** (one sentence)
4. **Why it matters** (one sentence)
5. **Suggested fix** (concrete, not "consider improving")

If no issues are found, say: "No issues found" — do not pad with praise or generic observations.

## Ripple effect triage

Spawn every Change-type table row that matches the diff (per Step 0.6's enumeration). To skip a row, write the skip rationale into the *Spawn decisions:* output (one sentence per skipped row). High-stakes rows — data migration, row-security policy changes, auth model changes, billing or payment, access-control restrictions, re-pointed reads on shared data — are near-mandatory: skip only when the change is declared dev-only or internal-only with no production-reachable surface, and name the surface in the output. The escape-hatch criteria below remain available but become reasons to skip *named in the output*, not reasons to never enumerate the row.

This is the last gate before ship — a specialist miss here ships as a regression. Lean conservative; misses are not recoverable downstream the way they are at plan-review. The criteria below anchor when a small diff still fires and when a matched row can be skipped with rationale:

- **Contract blast radius — not LOC.** A small diff that re-points a shared internal contract (a helper read by many callsites, a config consumed by many flows, a rename that propagates to many policies/functions) fires the relevant Change-type rows even when the line count is small. Count *consumers of the contract*, not changed lines. A 10-line helper rename consumed by 30+ row-security policies is a high-stakes data-model change, not a hygiene refactor.
- **Below-staff-level depth.** Specialized kernels (query-planner internals, cryptography primitives, fine-grained a11y, kernel-level concurrency) — not "general knowledge in domain X."
- **High-stakes boundary.** RLS, auth, billing, payment, data migration, privileged ops — a specialist's eyes are worth it even at generalist depth.
- **Holistic-reasoning overload.** Multi-domain ripple you can't hold in working memory at once.
- **Convergence-as-design-tell** from a prior round (see Reconciliation).
- **Explicit user request.**

Always spawn `ciso-reviewer` when the change touches auth/authz, secrets, tokens, data exposure (including prose that identifies a private project, organization, codename, internal product or tool, person, hostname, internal URL, email address, tracker ID, or filesystem path embedding a project name — a documentation-only diff is not exempt), sensitive-data logging, third-party data sharing, or infra permissions — high-stakes-boundary case is non-optional, **unless** the change is declared dev-only or internal-only (no privilege boundary crossed) (e.g., a dev-only flow with no production reachability, or an internal-only path where engineers themselves are the only callers and the change crosses no privilege boundary they shouldn't cross). When skipping on those grounds, name the surface in the review output — never silently skip. The ciso-reviewer rule is one instance of the general default-fire pattern above, not the exception. Always spawn `staff-product-engineer` when the change alters any end-user-visible surface: user interface, transactional or lifecycle email, push notification, SMS, in-app notification, billing artifact, exported file, webhook payload to a customer integration, OAuth consent screen, embedded widget or iframe surface, or end-user-visible log/audit entry. The trigger fires on the *channel*, not on the file-path domain — a backend-only change that ships a new email body still alters user-facing behavior. Indirect channel effects count: a data or logic change that determines which channel fires or what it contains (a new user-status enum value that triggers a different lifecycle email, a field added to a user record that an existing template reads) alters user-facing behavior even when the diff contains no channel template file.

Spawn per question (not per file-path domain) — "change touches `.github/`" isn't enough; the question needs a specific shape. When you spawn: spawn on the CODE, not on this review's output (each subagent reads the diff fresh); pick the specialist that serves the question (table is reference, not roster); pass diff scope, specific question, AND — for re-review — prior findings + what's been applied. Reviewers without prior context re-discover; that's wasted spawn.

The Change type column keys on what the change *does* for an operator or consumer, not on file types — a markdown-only diff can cross a runtime-config or CI/CD boundary if it restructures the taxonomy operators use to provision secrets, identify deploy targets, or reason about config layering.

| Change type | Spawn / invoke |
|-------------|----------------|
| Restricts DB access (RLS revocation, GRANT removal, trigger tightening) | `ciso-reviewer` + `staff-backend-engineer` — trace restrictions against caller code and check for privilege escalation |
| Re-points a read to a different data source while both stay populated | `staff-data-engineer` + `staff-backend-engineer` + `ciso-reviewer` (when the read feeds an access-control decision) — verify every write path to the prior source also writes to the new one |
| Changes API response shape | `staff-product-engineer` + `staff-backend-engineer` — verify all consumers handle new shape |
| Adds/modifies security controls | `staff-sdet` + `ciso-reviewer` — verify test pyramid, coverage, and threat model |
| Adds or modifies test code | `staff-sdet` — verify test-pyramid placement, behavioral-vs-structural fidelity, fixture realism, and mock design; invoke `test-conventions` for the authoring standard |
| Changes auth model (JWT, roles, permissions) | `ciso-reviewer` + `staff-backend-engineer` — trace all auth paths including token refresh, session expiry, and error fallbacks |
| Modifies shared utilities (helpers, hooks, contexts) | `staff-backend-engineer` + `staff-frontend-engineer` — verify all call sites and check for behavioral assumptions |
| Changes data model (columns, types, defaults, migrations) | Route by change type: new nullable column, index, or view → `staff-backend-engineer` only; new table → `staff-backend-engineer` + `staff-analytics-engineer`; rename, drop, type change, NOT NULL constraint added, partition key, or RLS policy → `staff-backend-engineer` + `staff-data-engineer` + `staff-analytics-engineer`. Add `staff-product-engineer` if user-visible. |
| Adds or changes warehouse models / dbt transformations / semantic-layer files | `staff-analytics-engineer` (modeling, transformation correctness, materialization, test coverage) |
| Adds or changes CDC / change-stream / ETL/ELT pipeline / warehouse ingestion connector | `staff-data-engineer` (transport, schema-drift, observability) + `staff-platform-engineer` (operational footprint) |
| Modifies CI/CD pipelines or deploy config | `staff-platform-engineer` + `staff-backend-engineer` — verify pipelines and environment consistency |
| Adds or modifies a comment or durable-doc prose beyond a hygiene tweak (code comments, docstrings, `REFERENCES.md`, doc files, README sections, skill/agent bodies) | `comment-discipline-reviewer` — enumerate every site violating CLAUDE.md §Code Comments, Documentation, and Prose (comment verbosity, multi-fact comment structure, wrong-altitude prose, PR-defined terminology, "used to be X" framing, durable-doc self-test failures); Step 1.5's inline "Non-durable comment" tripwire still runs unconditionally regardless of this spawn |
| Adds or modifies a skill, agent, instruction-file rule, or hook | **Invoke** (via the Skill tool — these are skills, not agents; do not spawn them) `skill-review`, `agent-review`, `ai-instruction-and-memory-files`, or `claude-hook-review`, per the dispatcher (Domain: Claude Code config). **Separately, spawn** a `staff-*` agent only when the edit demonstrably changes the *output* or *decision* that lane's specialist would review — not merely because the lane's name appears in the edited rule (e.g., a rule change that alters when `staff-backend-engineer` is consulted → spawn `staff-backend-engineer`; a typo or formatting fix in the same rule → do not spawn). Apply that test to every lane the edited artifact governs, not only lanes whose own files appear in the diff: an agent file left untouched while the skill governing its behavior changed is still in scope. For substantive routing-table edits specifically, defer to the *Reshapes reviewer ownership* row below, which has the precise pre/post-edit-union spawn rule. |
| Changes runtime config (env vars, secrets, feature flags) | `staff-platform-engineer` + `ciso-reviewer` — verify config is consistent across environments, check for leaked secrets |
| Reshapes reviewer ownership (substantive edits to plan-review/code-review skill routing tables, or scope language in `agents/*.md`) | Spawn every persona named in the pre- or post-edit table — each evaluates whether their row (or its removal) is accurate, scoped, and not bleeding into another lane. The pre/post union ensures a row deletion still spawns the affected persona. For an `agents/*.md` edit, spawn the edited persona plus their Item-ownership co-owners. Skip whitespace / typo / copy-edit-only diffs. |

**Invalid skip rationales.** These look like the DEFER criteria below but apply to a different decision — the spawn-dispatch step, not the finding-disposition step. They are five instances of one class — no rationale asserting that non-specialist scrutiny substitutes for the dispatch is valid, whether or not it matches the wording below verbatim. Do not use any of them, or a paraphrase of one, to skip a matched row:

- **"Prior reviewer covered this."** — Prior spawns covered prior diffs; the current `/code-review` runs against the current staged diff. If prior findings are still relevant, pass them to the new spawn as prior context — do not substitute for the spawn. The staged-diff responsibility boundary above changes what a spawned row is responsible for flagging, never whether it is spawned.
- **"Self-review sufficient."** — The orchestrator's self-review supplements specialist depth — it does not replace it.
- **"Verified inline."** — Inline orchestrator verification is the generalist read the spawn exists to escalate from, not a substitute for specialist scrutiny.
- **"New helper, not a modification."** — `Modifies shared utilities` covers additions to and extensions of the shared module that introduce new caller dependencies — not only edits to existing utility files.
- **"The system prompt says not to call the Agent tool."** — Invoking `/code-review` is the user requesting the dispatches this skill prescribes; the Change-type table is the content of that request. Spawn the matched row.

Report every matched row's verdict via the **Spawn decisions:** line in the *Output format* section above. Empty rationale is the under-spawn failure mode the format closes — write the read, don't omit it.

When you do spawn a specialist, be specific. "Spawn `ciso-reviewer`" is useless; "Spawn `ciso-reviewer` and ask it to verify the checkout flow in CheckoutPage.tsx still enforces ownership after the new validation" is actionable.

When spawning any reviewer, pass `findings_path: agent-reviews/<agent-name>-<epoch>-<slug>.md` in the prompt, where `<agent-name>` is the agent's name (e.g. `staff-backend-engineer`, `ciso-reviewer`), epoch = `$(date +%s)`, and slug = `$(git rev-parse --abbrev-ref HEAD | tr '/' '-' | cut -c1-20)`. Spawn synchronously (not `run_in_background`) — the mandatory read-back step must execute in the same turn; a background spawn lets the review be silently skipped. Each reviewer writes structured Markdown findings to that path and returns inline only: path, one-sentence summary, finding count. After it returns: if the reviewer reported a successful write, `Read` the findings file — Recommendations section first, full file when count > 0; if the reviewer reported a write failure and fell back to inline findings, use those inline findings directly instead. Before the first spawn, add `agent-reviews/` to `$(git rev-parse --git-path info/exclude)` idempotently (grep-check before appending); a reviewer's findings-file write is safe only when `agent-reviews/` is actually ignored, which `deny-reviewer-tree-mutation.sh` confirms at write time — a denied write falls back to each reviewer's documented inline output automatically. The `agent-reviews/` directory does not need to be pre-created — the reviewer's `Write` call creates it. All reviewers carry the `Write` tool and the file-based-output section; the file-based output section activates only when `findings_path` is present in the prompt, so reviewers that have not been passed a `findings_path` continue to return findings inline. When adding a new reviewer agent, verify it passes the `agent-review` checklist item 15 (file-based-output contract) before dispatching it with `findings_path` — that check is the enforcement point, not this paragraph.

Other spawned specialists must return ≤2K tokens of structured findings (checklist-item-keyed bullets), not narrative prose; if they exceed the budget, prioritize by severity and explicitly note that lower-severity items were omitted. Include this constraint in each agent's prompt.

## Reconciliation

After spawned reviewers return findings, pause if findings concentrate on a single surface — the same feature, implementation detail, or design choice attracting multiple gaps. If two specialists flag the same `file:line` with the same root cause, present the finding once with both reviewer attributions rather than as duplicate findings.

**Reconciliation decides escalation only, never a finding's survival.** A reconciliation reading never removes a finding, never changes how it is dispositioned downstream, and is never a reason to skip a spawn — every converged finding proceeds exactly as it would have. The only question in play: does this convergence justify replacing the surface and re-running Step 1?

Two readings:

- **Implementation-wrong-shape.** The surface is the wrong abstraction; gaps will keep multiplying as you patch. Replace, don't patch-by-patch.
- **Correlated-reviewer artifact.** Reviewers converge without independent corroboration — either similar prompts producing N voices of the same observation (prompt overlap), or a shared base model drawing convergent flags on a pattern over-represented as a smell in training data regardless of whether it's wrong here (shared-model prior). Convergence looks like signal but isn't. Tighter prompts fix the former on the next spawn; they do nothing for the latter.

**Discriminator:** read what each convergent finding *names as the failure*. Distinct failure modes on one surface — a lock-budget risk, a consumer-contract break, a missing rollback — support escalation, because the surface is load-bearing in several directions. One failure mode in N voices, or findings that fail to name a consequence traceable in this code, do not.

Convergence the Item-ownership table itself prescribes (two reviewers assigned to the same item by the routing contract, not by independent discovery) disclaims independence — it does not disqualify escalation. It is still escalation-eligible on the discriminator's own terms: `staff-data-engineer` flagging an RLS object as unenforceable and `ciso-reviewer` flagging the same line as a cross-tenant read path are two distinct failure modes on one surface and should escalate.

If implementation-wrong-shape, replace the surface and re-run Step 1. If correlated-reviewer artifact, present the finding once with both reviewer attributions; when the cause is prompt overlap, note it so the next spawn uses tighter prompts.

## Finding disposition

After Reconciliation, before producing the recommendation, walk every reviewer-spawned finding and tag it ADDRESS or DEFER. ADDRESS is the default and needs no rationale; DEFER requires a named criterion from the closed list below.

Default to ADDRESS for in-diff, tested code (see `docs/design-decisions.md` §16 for rationale).

Disposition turns on complexity/risk/test coverage, never fix size — a small fix in already-touched, already-tested code is still ADDRESS.

Format: inline `ADDRESS:` / `DEFER (<criterion>):` tags when there are ≤2 findings; a four-column table — Finding | Source | Disposition | Rationale — when there are 3+. The orchestrator's recommendation that follows can still propose grouping fixes into this PR vs follow-ups, but every finding has been seen, tagged, and either addressed or justified against the closed list.

Follow the disposition with a mandatory **Fix route:** line, mirroring **Spawn decisions:** above: `code-writer` when every ADDRESS row is dispatched as one round per `subagent-delegation`'s "Implementation work → `code-writer`" rule, or `inline — <reason>` naming which of that rule's two carve-outs applies (a fix that isn't code, or a finding still being re-decided). DEFER rows carry no route — they produce no fix. As with Spawn decisions, an empty rationale is the failure mode this line closes.

**DEFER criteria (closed list).** A finding may be tagged DEFER only when it matches one of:

1. **Orthogonal scope** — addresses code or a concern truly unrelated to this change and not covered by tests already running in this PR's verification. Not orthogonal if this change touches the code the finding lands in, or if this change is what activates the finding (a new caller reaching a latent bug, a migration that flips a table a bug reads) — those are ADDRESS: scope is set by the bug, not by where the symptom first surfaced.
2. **Coordinated multi-PR effort, with design-tell test passed** — the fix requires changes across repos, services, or sequenced migrations that exceed this PR's coordination boundary. Before tagging DEFER here, run the design-tell test: if the coordination requirement signals the current PR's design is wrong-shape, re-run Step 1 instead of deferring. If it's pre-existing structural debt, DEFER is valid only when a ticket is filed for the remediation and the issue is surfaced to the human in the recommendation.
3. **Gold-plating beyond declared user surface** — Step 1's implementation-fitness gate established the user surface and threat model; the finding adds a layer beyond it.
4. **Contract pinned at another layer** — test coverage suggested for a contract already enforced by another layer's test or invariant.
5. **Edge case below current scale** — failure mode that doesn't fire at the system's operating scale; mark the boundary with an in-code comment so the future re-evaluation has a hook.

**Invalid DEFER rationales.** These look like dispositions but aren't — do not use them:

- **"Informational only" / "FYI"** — reviewer severity labels are triage signals, not dispositions. The orchestrator runs its own test against the five criteria above. A reviewer's "FYI: X is stale" produces orchestrator disposition ADDRESS unless one of the criteria genuinely applies.
- **"No correctness impact"** — stale comments, misleading names, and outdated docs cost future readers (often future agent sessions priming on the comment to build context). Mechanical fixes are ADDRESS.
- **"General skill guidance, not PR scope"** — narrowing the PR boundary to dismiss a finding is scope-shrinking, not disposition. If the guidance has a durable home (header comment, migration note, runbook line in the file), ADDRESS it there.
- **"Pre-existing gap"** — pre-existing is a fact about the gap's age, not a disposition. A pre-existing gap that closes a correctness or security-invariant hole is ADDRESS; pre-existing alone is not a criterion.
- **"Small" / "quick" / "cosmetic" / "non-blocking" / "advisory"** — implementation size and merge-blocking severity are not disposition axes. A reviewer's advisory or a one-line cosmetic fix in already-touched, already-tested code is ADDRESS; "it doesn't block merge" is a severity label, not a DEFER criterion.
<!-- DISPOSITION_RULE:code-review-defer-invariant start -->
- **"Enforcement invariant weakened, but disclosed"** — a finding that the diff opens a path around an enforcement invariant (a gate, hook, permission check, required-approval, or marker guarantee some mechanism currently makes unbypassable) is never DEFER-eligible, regardless of which criterion above seems to match. Disposition is ADDRESS (fix the hole) or a blocking stop-and-ask to the human — never persisted to the `## Deferred review findings` block. Approval of a diff or PR does not function as informed consent for an invariant-break buried in the body.
<!-- DISPOSITION_RULE:code-review-defer-invariant end -->

If you find yourself tagging 3+ findings DEFER in a single review, re-read the criteria — the default is ADDRESS, and a heavy DEFER list is usually a sign the criteria are being stretched.

## Review-narrative ledger

Immediately once dispositions are finalized — before DEFER persistence below — call `~/.claude/scripts/review-ledger.sh append code-review --finding "<summary>" --disposition ADDRESS|DEFER --rationale "<one line>" [--source "<file:line>"]` once per finding, on every invocation, regardless of whether the disposition step used inline `ADDRESS:`/`DEFER (<criterion>):` tags (≤2 findings) or the four-column table (3+). Unlike the marker write at the end of this skill, this runs whether or not the review is clean — a mid-loop review that is not yet clean is exactly the case this ledger exists to preserve across a compaction or session resume. A finding rejected by the script for an over-length `--finding`/`--rationale` should be summarized more tightly and retried, not skipped.

## DEFER persistence

When the disposition step produces ≥1 DEFER finding, persist the DEFER rows (and only the DEFER rows) to the PR description as a `## Deferred review findings` section. ADDRESS findings are not persisted — they are visible in the diff the reviewer is reading.

Row format: `Finding | Source | DEFER criterion | Rationale` (four columns, same shape as the disposition table filtered to non-ADDRESS rows).

**If a PR is already open** (`gh pr view --json number,body` succeeds): update the PR description idempotently with `gh pr edit --body`. Delimit the section with HTML comment markers so it can be mechanically replaced on re-runs:

Opening delimiter: `<!-- code-review:deferred:start -->`
Closing delimiter: `<!-- code-review:deferred:end -->`

Append the delimited block if absent; replace the existing delimited block if present (idempotent across repeated `/code-review` runs as fix commits collapse DEFERs into ADDRESSes or introduce new ones). Preserve all PR description content outside the delimiters.

**If no PR is open yet** (pre-`/ready-for-review` path): return the rendered `## Deferred review findings` block as part of the orchestrator output, wrapped in the same HTML comment delimiters (`<!-- code-review:deferred:start -->` / `<!-- code-review:deferred:end -->`). `/ready-for-review` step 5 includes it verbatim in the body it authors, before step 6 creates the PR from that body; the delimiters let a subsequent `/code-review` run locate and replace the block idempotently.

**If zero DEFERs**: do not add the section. If a prior run wrote the delimited block and the latest run produces zero DEFERs, remove the delimited block from the PR description.

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
| **6–9. Hygiene** (dead exports, unnecessary wrappers, inline business logic, repeated in-house logic) | judgment (any reviewer) | — |
| **9a. Repeated domain discriminants without a shared type** | judgment (any reviewer) | — |
| **9b. Unnamed semantic bounds in slicing/truncation** | judgment (any reviewer) | — |
| **9c. Ungrounded numeric literal in network/timeout/retry context** | `staff-backend-engineer` (timeout/retry in server paths), `staff-platform-engineer` (CI/infra timeouts) | — |
| **9d. Lint or type-check suppression without rationale** | judgment (any reviewer) | — |
| **9e. New third-party dependency without provenance research** | `staff-backend-engineer` (runtime deps) | `staff-platform-engineer` (build/CI deps), `staff-frontend-engineer` (frontend/client-side deps) |
| **9f. Brittle structural test assertion** | `staff-sdet` | `staff-backend-engineer` (backend tests asserting on structured API response output) |
| **9g. Source-scanning test assertion** | `staff-sdet` | — |
| **9h. Construct-substitution suppression** | judgment (any reviewer) | `staff-sdet` (suppression constructs inside test files or test-double setup; see `test-conventions` §8) |
| **10. Undocumented limitations** | `staff-product-engineer` (user-visible limitations) | judgment (others) |
| **11. Misleading names** | `staff-product-engineer` (API / copy facing) | `staff-frontend-engineer` (component / hook), `staff-backend-engineer` (server) |
| **12. Stripped WHY comments** | judgment (any reviewer) | — |
| **12a. Comment/prose discipline on added or modified text** | `comment-discipline-reviewer` (when spawned) | judgment (any reviewer, via Step 1.5's inline tripwire) |
| **13. Test adequacy for security controls** | `ciso-reviewer` (designated writer) | `staff-sdet` (second-reader) |
| **14. Pre-existing issues in unchanged code** | judgment (any reviewer) | — |
| **14a. Config-file change without stated intent** | judgment (any reviewer) | `staff-platform-engineer` (build/CI/IaC configs) |
| **15–19. Infrastructure** (concurrency scoping, secret exposure, least-privilege, idempotency, trigger alignment) | `staff-platform-engineer` | `ciso-reviewer` (16 secret exposure, 17 least-privilege) |
| **20. Migration reversibility** | `staff-data-engineer` (rollback safety, pipeline impact) | `staff-backend-engineer` |
| **21. Index coverage** | `staff-backend-engineer` (app-query coverage) | `staff-data-engineer` (DDL risk and bloat) |
| **22. Lock safety** | `staff-data-engineer` (DDL execution shape, pipeline impact) | `staff-platform-engineer` (deploy-window, lock-budget) |
| **23. RLS / access control on new tables** | `staff-data-engineer` (enforceability) | `ciso-reviewer` (threat framing) |
| **24. Accessibility** | `staff-frontend-engineer` (technical a11y) | `staff-product-engineer` (a11y as spec fidelity) |
| **25. Render performance** | `staff-frontend-engineer` | — |
| **26. Bundle impact** | `staff-frontend-engineer` | `staff-platform-engineer` (build tooling) |
| **27. State-dependent rendering coverage** | `staff-frontend-engineer` (branch implementation) | `staff-sdet` (test coverage), `staff-product-engineer` (right branches for spec) |
| **28. Auth boundary coverage** | `staff-backend-engineer` | `ciso-reviewer` |
| **29. Input validation at boundaries** | `staff-backend-engineer` | `ciso-reviewer` |
| **30. Error response leakage** | `staff-backend-engineer` | `ciso-reviewer` |
| **31. Dependency upgrades** | `staff-backend-engineer` (runtime deps) | `staff-platform-engineer` (CI / build deps), `staff-frontend-engineer` (frontend/client-side deps) |
| **32. Third-party API integration** | `staff-backend-engineer` | `ciso-reviewer` (credential scoping) |
| **33. Sensitive data in logs** | `staff-backend-engineer` | `ciso-reviewer` |
| **34. Performance-sensitive paths** | `staff-backend-engineer` (app-level query patterns) | `staff-data-engineer` (DDL / index / read-path) |
| **34a. Untyped error raised where the file uses typed-error dispatch** | `staff-backend-engineer` (server-side throw paths), `staff-frontend-engineer` (client-side throw paths) | — |
| **35. Permission scope** | `ciso-reviewer` | — |
| **36. Hook correctness** — reviewed via `claude-hook-review` skill | `staff-platform-engineer` | `ciso-reviewer` |

## Step — Record review completion

If the review is **clean** (no blockers, no unresolved critical findings, and you reviewed the currently staged changes), record it by running this command exactly once:

<!-- HOOK_TEST_FIXTURE: marker-write — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/code-review/SKILL.md) to verify it matches require-code-review.sh's marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh write code-review
```

This writes the hash of the currently staged diff into `<config-dir>/code-review-markers/<repo-hash>.<session-id>`. The pre-commit hook recomputes the staged-diff hash and allows the commit when any marker under this repo-hash holds that value — the stored hash is the authorization, so a review still covering the staged state counts even from another session. The session id in the filename only prevents two parallel sessions in the same worktree from overwriting each other's markers. Re-staging any change invalidates the marker automatically.

Run the command standalone, or chained only as `marker.sh write code-review && git commit …` — the invocation-shape gate denies every other chain tail. If it fails (empty `SESSION_ID`, etc.), `marker.sh` could not resolve this session's id — abort and report; do not proceed without the marker, since `git commit` will be blocked by the gate.

**Authoring the commit message.** A single-line message goes inline with `-m`. For a multi-line message, create the file with `mktemp "${TMPDIR:-/tmp}/commit-msg.XXXXXX"`, populate it with the **`Write` tool**, then pass that path to `git commit -F <path>` as literal text — a `$VAR` is opaque to the gates, which resolve the argument statically and fail closed on it.

Never author the message any of these ways:

- **A shell heredoc.** An unquoted `<<EOF` parses embedded backticks and `$(...)` as shell substitution before either gate ever sees the content — use the `Write` tool instead.
- **`-F` pointed at `-`, `/dev/stdin`, `/dev/fd/*`, or any other pseudo-file.** Both the PII gate and the redaction gate read the `-F` file before git runs; neither can read the stdin of a process that hasn't started, so a pseudo-file source is denied outright rather than scanned.
- **`-m "$(cat …)"` command substitution.** The gates scan only the literal command string, so the real message would reach the commit unscanned.

Keep the file out of `$HOME` and out of any UUID-shaped or 32-plus-hex-character path — e.g. not the session scratchpad. The redaction gate scans the commit command string itself. Its home-rooted-path and long-hex-identifier detectors both match on the `-F` argument.

**Do NOT write the marker if:**

- The review found blockers or unresolved critical findings
- You reviewed a different state than what is currently staged
- The user asked you to present findings without committing
- You are not in a git repository

If you skip it, say so explicitly so the user knows the commit gate will block until issues are fixed and the review is re-run on the final staged state.

**This marker must be written only by this skill.** Never compute and write it manually — that satisfies the hook's shell check but forges the gate. If the hook blocks a commit and you cannot invoke this skill, spawn a subagent that can; do not hand-write the marker path.
