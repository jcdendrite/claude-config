# Plan: Author net-new, source-grounded path-scoped rules for CI/infra + SQL/DDL file types

## Context

**Goal:** author new user-scope path-scoped rules under `claude/.claude/rules/`
(stows to `~/.claude/rules/`) that encode opinionated, **primary-source-grounded**
conventions for high-gotcha file types — GitHub Actions workflows, Dockerfiles,
shell scripts, and raw SQL/DDL — so the conventions load deterministically
whenever a stow user opens a matching file in **any** repo.

**Why now / how we got here:** the original task (migrate file-type-conditional
content *out* of the global CLAUDE.md) was closed by two audits:
1. **No CLAUDE.md section migrates cleanly** — the one candidate would need an
   all-code glob (compounding complexity), so it stays always-loaded.
2. **No existing skill converts cleanly** — `sql-query-conventions`,
   `error-handling`, `config-environments` fail the narrow-glob test;
   `test-conventions` is already dispatch-wired (`docs/design-decisions.md §15`).

The value is **net-new** content for file types with a clean single glob,
cross-repo-generic relevance, and — the decisive filter — where Claude's
defaults are genuinely weak (so every line passes the behavior test). That
points at CI/infra config and raw SQL/DDL.

**Source discipline (user requirement):** rules must be **grounded**, not
recalled. For each rule, in order: (a) reuse a reputable pre-existing rule from
a Cursor rules marketplace / native Claude skill / plugin marketplace if one
exists and fits; (b) otherwise author from **primary sources verified via the
`/verify-sources` skill**, and cite the source. The drafts below have already
had one round of specialist claim-verification folded in (see "Round-2 review"
note); final wording still gets a `/verify-sources` pass at build.

**Intended outcome:** a set of grounded, `[PROPOSAL]`-marked rules the user
vets ("I draft, you edit"); a frontmatter-validity test; doc updates for the
new stowed rules directory. The global CLAUDE.md is **not** trimmed.

> **Round-2 review already run:** the drafts were verified by `ciso-reviewer`,
> `staff-data-engineer`, `staff-platform-engineer`, and `staff-analytics-engineer`
> during plan-review. Their corrections are folded in below (two SQL bullets were
> factually wrong for current Postgres; the shell glob had a bash-vs-POSIX
> hazard; the Actions timeout claim was backwards). Bullet counts are generous
> for vetting — expect to cut.

## Approach

One rule file per target under `claude/.claude/rules/`, each with **generic
cross-repo globs** (user-scope applies in every repo the user opens), a single
`##` heading + tight bullets, mirroring the existing project-rule shape. Content
is `[PROPOSAL]`-marked and grounded per the source-discipline rule above.

*Behavior-test discipline:* every bullet must change Claude's default; lines
restating what the model already does are cut.

### Source-grounding & citation (methodology, applied per rule)

1. **Search first** for an authoritative pre-existing rule (cursor.directory /
   awesome-cursorrules; `anthropics/claude-plugins-official`). Adapt + cite if
   one fits.
2. **Else author from primary sources via `/verify-sources`.** Named sources:
   - GitHub Actions → GitHub "Security hardening for GitHub Actions" docs.
   - Dockerfile → Docker "Best practices for building images" + BuildKit
     secret-mount docs.
   - Shell → Google Shell Style Guide + ShellCheck wiki.
   - SQL/DDL → the in-repo agent files + PostgreSQL docs for lock/transaction
     mechanics.
3. **Cite the source** per rule (comment line and/or PR description).

### Private-project redaction gate (hard requirement)

The user has a **private** Supabase/Lovable project outside this repo; this
repo is **public and stow-distributed**. That project may be consulted as a
private cross-check, but **no project-specific content** — schema shapes,
table/column/policy patterns, worktree/ticket names, Supabase/PostgREST
fingerprints — enters any rule file. Extract only generic, stack-agnostic
conventions; strip vendor/project tokens (applies equally to private-shaped
examples in the agent files). Sweep the diff before the commit gate.

### Rule 1 — `claude/.claude/rules/github-actions-workflows.md`

Glob `**/.github/workflows/*.{yml,yaml}`. Verify vs GitHub hardening docs at
build.
- **[PROPOSAL] Pin third-party actions to a full 40-char commit SHA, not a
  tag** — a moved tag runs attacker code with the job's token/secrets. (Doesn't
  cover transitive actions the pinned action itself calls.)
- **[PROPOSAL] Explicit least-privilege `permissions:`** — default
  `permissions: {}`, grant the minimum per job. An omitted block inherits the
  repo/org default: read-only on repos created after Feb 2023, but write-all on
  older repos or permissive org settings.
- **[PROPOSAL] `persist-credentials: false` on `actions/checkout`** — the
  default persists `GITHUB_TOKEN` in `.git/config` for any later step (or
  malicious dep) to read/exfiltrate.
- **[PROPOSAL] Never interpolate untrusted `${{ github.event.* }}` into `run:`**
  — route through `env:` and quote `"$VAR"` (shell injection).
- **[PROPOSAL] `pull_request_target` runs in the base-repo context with base
  secrets + write token** and checks out the base ref by default — never check
  out/execute PR-head code under it; use `pull_request` for untrusted PRs.
- **[PROPOSAL] Prefer OIDC (`id-token: write` + short-lived federated creds)**
  over stored long-lived cloud secrets.
- **[PROPOSAL] Bound each job with a tight `timeout-minutes`** — the implicit
  default is 360 (6h), so pick a real per-job budget; setting 360 adds nothing.
- **[PROPOSAL] `concurrency:` with `cancel-in-progress: true` for PR/feature
  validation only** — NOT deploy or push-to-default workflows (cancels mid-apply).
- **[PROPOSAL] Pin runners to a fixed image (`ubuntu-24.04`), not
  `ubuntu-latest`** (which mutates on GitHub's schedule and breaks builds with
  zero diff).

### Rule 2 — `claude/.claude/rules/dockerfile-conventions.md`

Glob `**/Dockerfile`, `**/Dockerfile.*`, `**/*.Dockerfile`, `**/*.dockerfile`.
Verify vs Docker best-practices docs at build.
- **[PROPOSAL] Pin base image by digest, not a floating tag** — and pair with a
  refresh mechanism, since a digest freezes out upstream security rebuilds
  (image goes stale silently).
- **[PROPOSAL] Run as a non-root `USER`.**
- **[PROPOSAL] Multi-stage build** — ship only runtime artifacts (keeps
  toolchains and build-time secrets/source out of the final image).
- **[PROPOSAL] No secrets in layers** — `ARG`/`ENV`/`COPY` of a credential
  persists in image history; use `RUN --mount=type=secret` (BuildKit).
- **[PROPOSAL] `COPY` explicit paths, not `COPY . .`** — the primary control
  against baking `.env`/keys/`.git` into a layer; `.dockerignore` is a
  bypass-prone backstop, not the boundary.
- **[PROPOSAL] Add a `.dockerignore`** (`.git`, `node_modules`, `.env*`, build
  output) as defense-in-depth.
- **[PROPOSAL] `apt-get update && apt-get install --no-install-recommends … &&
  rm -rf /var/lib/apt/lists/*` in ONE `RUN`** — a separate `update` layer
  caches and serves stale package indexes.

### Rule 3 — `claude/.claude/rules/shell-script-conventions.md`

Glob `**/*.sh`, `**/*.bash`. Verify vs Google Shell Style Guide / ShellCheck at
build.
- **[PROPOSAL] These are bash conventions.** The `**/*.sh` glob can't read the
  shebang; if the script is POSIX `#!/bin/sh` (dash), `pipefail`, `[[ … ]]`, and
  arrays are invalid — use portable forms instead. State the rule is
  bash-specific up top.
- **[PROPOSAL] `set -euo pipefail` at the top** — but know `set -e`'s footguns:
  it's suppressed for a whole function called in an `if`/`&&`/`||` condition,
  and `local x=$(cmd)` masks the failure (`local`'s exit status wins).
- **[PROPOSAL] Run `shellcheck` (CI or pre-commit)** — mechanically catches the
  quoting / `set -e` / portability issues below; highest-leverage single item.
- **[PROPOSAL] Quote every expansion** (`"$var"`, `"${arr[@]}"`, `"$(cmd)"`).
- **[PROPOSAL] `[[ … ]]` over `[ … ]`** (bash).
- **[PROPOSAL] `IFS= read -r` for line-reading loops** (bare `read` strips
  whitespace and mangles backslashes).
- **[PROPOSAL] `mktemp` for temp files** (with a template on BSD/macOS:
  `mktemp -t name`) + a SINGLE `trap … EXIT` handler (a second `trap` silently
  overwrites the first — compose cleanup).
- **[PROPOSAL] `local` for all function variables** (unset leaks/collisions).
- **[PROPOSAL] `"${VAR:?message}"` for required inputs.**

### Rule 4 — `claude/.claude/rules/sql-ddl-conventions.md` (user-requested)

Glob `**/*.sql`. **Non-overlapping** with `sql-query-conventions` (read-path
SELECT; explicitly excludes DDL). Primary sources: in-repo
`staff-data-engineer.md` + `staff-analytics-engineer.md` (public, stack-agnostic)
+ PostgreSQL docs. Postgres named as illustration, not required stack. **This
draft reflects round-2 data-engineer + analytics-engineer corrections** (bullets
on `NOT NULL` defaults and `ALTER TYPE` were rewritten — the originals were
false for current Postgres).

```markdown
---
paths:
  - "**/*.sql"
---

## Raw SQL & DDL conventions

Hand-written SQL: migrations, DDL, schema, functions. (Read-path SELECT /
pagination tuning lives in the `sql-query-conventions` skill.) Postgres idioms
below are illustrations — apply the equivalent for your engine.

### Migration safety
- **[PROPOSAL] Set a low `lock_timeout` before DDL.** A blocked ALTER/CREATE
  INDEX sits at the head of the lock queue and blocks every query behind it —
  fail fast and retry rather than stalling all traffic.
- **[PROPOSAL] `CREATE INDEX CONCURRENTLY` on non-trivial tables** (plain
  `CREATE INDEX` blocks writes for the whole build). It cannot run in a
  transaction, belongs in its own migration, and on failure leaves an INVALID
  index that must be explicitly `DROP`ped and rebuilt — not just re-run.
- **[PROPOSAL] Adding a `NOT NULL` column: a *volatile* default (e.g.
  `random()`) rewrites the table; a constant or non-volatile default (e.g.
  `now()`) uses the PG11+ fast path with no rewrite.** Promoting to `NOT NULL`
  still full-scans to validate — on large tables add nullable, backfill in
  batches, then set the constraint.
- **[PROPOSAL] Add FK/CHECK constraints as `NOT VALID`, then `VALIDATE
  CONSTRAINT` in a separate step.** Plain `ADD CONSTRAINT` takes ACCESS
  EXCLUSIVE and full-scans; `NOT VALID` skips the scan, and `VALIDATE` takes a
  weaker lock.
- **[PROPOSAL] `ALTER TYPE … ADD VALUE` runs in a transaction (PG12+), but the
  new value can't be used until that transaction commits** (pre-12 the statement
  was non-transactional).
- **[PROPOSAL] Destructive changes (`DROP`, type narrowing, rename) use
  expand/contract** — additive first, deploy, then contract — with a rollback
  path; old and new code must both run across the deploy window.

### Access control on new objects
- **[PROPOSAL] Create table/view + `ENABLE ROW LEVEL SECURITY` + at least one
  explicit policy + grants in the SAME migration.** A new table on an
  auto-generated API (PostgREST/Supabase) with no policy is a data-exposure
  default; RLS enabled with zero policies is fail-closed (denies all) — you need
  both the enable and the policy.

### Schema choices with downstream cost
- **[PROPOSAL] `timestamptz` over `timestamp`** (naive timestamps are
  timezone-ambiguous — break replication/CDC and cross-region ordering).
- **[PROPOSAL] Explicit `numeric(p,s)` for money/quantity**, never float.
- **[PROPOSAL] Include `created_at`/`updated_at` (+ soft-delete marker where
  relevant); update `updated_at` on every mutation** (trigger or app write —
  Postgres won't self-update it) so it's a trustworthy incremental watermark.
- **[PROPOSAL] Give every table a *stable* key; where the natural/composite key
  is mutable, add a surrogate.** (Not "every table needs a surrogate" — junction
  tables keyed by stable surrogate-FK composites, and append-only event tables
  keyed by a stable `event_id`, are fine.)
- **[PROPOSAL] Constrain fixed categorical columns to a bounded domain** — a
  reference/lookup table (often more ELT-friendly than a native `ENUM`, which is
  painful to alter) or `varchar` + `CHECK` — not free `TEXT` (drifts
  casing/synonyms, defeats dictionary compression, breaks accepted-values tests).
- **[PROPOSAL] Typed columns over wide `jsonb`** for fixed-shape data; reserve
  `jsonb` for genuinely dynamic shapes.
- **[PROPOSAL] Index the columns queries filter/join/order on, especially FKs on
  growing tables** — Postgres auto-indexes PK/unique but NOT FK-referencing
  columns; an unindexed FK forces scans and lock escalation on parent
  DELETE/UPDATE. (Source-side OLTP concern, not ELT.)
```

### Supporting changes

- **Frontmatter-validity test** — new
  `claude/.claude/skills/tests/test_rules_frontmatter.py`: discovers every
  `.claude/rules/*.md` under the repo-root project rules **and** the stowed
  `claude/.claude/rules/`, asserting parseable YAML frontmatter whose `paths` is
  a non-empty list of strings. Guards the global-rule failure mode: a malformed
  glob silently matches nothing. Reuses `parse_frontmatter` from
  `claude/.claude/skills/tests/test_skills.py`.
- **Doc accuracy** — `README.md:58`, `README.md:216` describe path-scoped rules
  as project-scoped `.claude/rules/` only; add the stowed user-scope
  `claude/.claude/rules/` (→ `~/.claude/rules/`). Repo-root `CLAUDE.md:22`: one
  sentence on the stowed sibling and its generic/cross-repo globs.
  `docs/design-decisions.md` gets no entry (PR #450 precedent).

*Alternatives considered:* language-style rules (cut — Claude writes idiomatic
code); converting existing skills (cut per §15 audit); migrating CLAUDE.md
content (cut per audit 1). Path-scoped rules are the native, lightest mechanism
for file-type-conditional context.

## Critical files

**Create:**
- `claude/.claude/rules/github-actions-workflows.md`
- `claude/.claude/rules/dockerfile-conventions.md`
- `claude/.claude/rules/shell-script-conventions.md`
- `claude/.claude/rules/sql-ddl-conventions.md`
- `claude/.claude/skills/tests/test_rules_frontmatter.py`

**Modify:**
- `README.md` — lines 58, 216 (one-line accuracy edits).
- `CLAUDE.md` (repo root) — line 22 (one sentence).

**Reuse / draw from (do not reimplement / do not copy verbatim):**
- `claude/.claude/agents/staff-data-engineer.md` + `staff-analytics-engineer.md`
  — public, abstracted DDL/schema conventions feeding Rule 4.
- Plan-review `D1–D5` domain checklist — migration-safety framing.
- `parse_frontmatter` in `test_skills.py` — for the new test.
- Existing rule-file shape in `.claude/rules/*.md` — structure only.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` from the worktree; new
   frontmatter test passes for all rules files.
2. Each new rule's `paths` frontmatter parses as YAML; globs well-formed (no
   unescaped `[`, per the docs' bracket caveat).
3. **Source citation present** for each rule (verify-sources fetch or reused
   marketplace rule named); no rule ships on recall alone.
4. **Redaction sweep** — grep the diff for private-project/Supabase tokens;
   nothing private lands in a public rule.
5. **Stow dry-run (optional):** `stow -n -v -t "$HOME" claude`.
6. **Rule-loads-when-expected (optional):** `InstructionsLoaded` hook while
   opening a `.github/workflows/x.yml`, a `Dockerfile`, a `foo.sh`, and a
   `foo.sql` in a scratch session.
7. `/code-review` on the staged diff (dispatches
   `ai-instruction-and-memory-files` for the rules-surface question). Commit,
   push, open PR — **do not merge** (repo rule: agents don't merge own PRs).

## Out of scope

- Migrating any CLAUDE.md section (audited; stays put).
- Converting existing skills (audited; §15 dispatch wiring stands;
  `test-conventions` pointer-rule declined).
- Language-style rules (fail the behavior test).
- Copying the user's private-project content into the public repo (redaction gate).
- Mechanical enforcement (linters/CI gates) of these conventions in user repos
  — the rules are advisory guidance for Claude; enforcement is a separate
  per-repo decision.
