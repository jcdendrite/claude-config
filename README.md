# claude-config

Portable [Claude Code](https://claude.ai/claude-code) global configuration: custom skills, PreToolUse hooks that gate `git commit` and PR-comment flows, and a custom statusline. Runs on any Unix-like system (Linux, macOS, WSL). Managed with [GNU Stow](https://www.gnu.org/software/stow/).

## Requirements

- **Operating system:** Linux, macOS, or WSL2. Native Windows (PowerShell / cmd.exe) is not supported — every hook is a bash script and `install.sh` uses GNU `stow` with symlinks. If you're on Windows, install inside [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) instead.
- **Shell:** `bash`. Hooks and `install.sh` use `#!/bin/bash`.
- **Tools:** `stow`, `git`, `gh`, `jq`, `sha256sum`, and the `claude` CLI. `install.sh` verifies they exist and exits early if any are missing.
- **Optional:** `pytest` for running the hook test suite (`pytest claude/.claude/hooks/tests/`).

**macOS:** `sha256sum` ships in GNU `coreutils`. Install with `brew install coreutils`, then add the gnubin directory to PATH so the unprefixed name resolves: `export PATH="$(brew --prefix coreutils)/libexec/gnubin:$PATH"`.

## Setup

```bash
git clone git@github.com:jcdendrite/claude-config.git ~/claude-config
cd ~/claude-config
./install.sh
```

This symlinks `claude/.claude/` into `$HOME/.claude/`.

## What this installs

### Hooks

- **`require-code-review.sh`** — blocks `git commit` (including chained forms like `git add . && git commit`) until `/code-review` has run on the current staged state. Verified via per-session sha256 marker in `~/.claude/review-markers/<repo-hash>.<session-id>`, which auto-invalidates the moment the staging area changes. Per-session keying prevents two parallel sessions in the same worktree from overwriting each other's markers when staging different diffs.
- **`deny-private-project-refs.sh`** — blocks `git commit`, `gh pr create`, and `gh pr edit` when the staged diff, commit message, or PR title/body/body-source-file contains either (a) tracker-ID tokens (`[A-Z]{2,}-\d+`) outside an OSS-prefix allowlist (`CVE-`, `RFC-`, `GH-`, and similar — see the script for the full list), or (b) a literal substring match against entries in the user's opt-in `~/.claude/private-projects.md` blocklist. Enforces the mechanical categories of the repo-root `CLAUDE.md` redaction rule; structural fingerprints still require review discipline. See [Private-project redaction](#private-project-redaction) below.
- **`require-respond-pr.sh`** — blocks PR comment reads and posts (`gh api .../pulls|issues/N/{comments,reviews}`, `gh pr comment`, `gh pr review`) and redirects to `/respond-pr`, so all three comment types get fetched and replies carry the `[Claude Code]` attribution prefix. Honors a per-session bypass marker at `~/.claude/.respond-pr-active.d/<session_id>` that the skill sets on entry and removes on exit; the hook refreshes the marker's mtime on each bypass so long skill runs don't hit the 60-minute staleness cutoff. Per-session keying (rather than a singleton path) keeps parallel Claude sessions from thrashing on cleanup or leaking bypass to unrelated sessions.
- **`capture-session-id.sh`** (SessionStart) — at session start, writes the session's `session_id` to `~/.claude/sessions/<claude-pid>` so skills running as Bash tool calls (which don't see the hook payload) can look up their own session id via the bash tool's `$PPID`. Used by both `/respond-pr` and `/code-review` to compute per-session marker filenames.
- **`ask-review-permissions.sh`** — asks before `Edit`/`Write`/`MultiEdit` to `.claude/settings*.json`, nudging toward `/review-permissions` when the edit touches `permissions.allow`.
- **`require-worktree-for-git-writes.sh`** — opt-in per repo. When active, denies non-read-only git operations unless the session runs in a linked git worktree. Prevents concurrent Claude Code sessions from racing on the same working tree. See [Worktree enforcement](#worktree-enforcement) below for opt-in instructions.

### Skills (slash commands)

- **`/code-review`** — principal engineer code review checklist with ripple-effect triage and domain-specific audits (backend, frontend, security, infrastructure, data).
- **`/plan-review`** — review implementation plans before presenting, with domain-specific reviewer roles.
- **`/review-permissions`** — security audit of `permissions.allow` rules with a 21-item checklist.
- **`/respond-pr`** — fetch and address PR review comments, with `[Claude Code]` attribution on all replies.
- **`/branch-creation`** — naming conventions (`<TICKET-ID>/<topic-slug>` for ticketed projects, `<topic-slug>` alone otherwise), anti-patterns to reject (tracker `<user>/` defaults), and branching from a fresh default-branch tip.
- **`/git-feature-branch-sync`** — decision framework for keeping a feature branch current with the default branch: when to rebase-and-force-push vs merge-in, and how to force-push safely (`--force-with-lease` vs `--force-if-includes`).
- **`/git-state-safety`** — safely inspecting other branches when the working tree is in a fragile state (mid-merge, mid-rebase, mid-cherry-pick), avoiding the silently-corrupted-index failure mode where a diagnostic `git checkout <ref> -- <path>` overwrites a partially-resolved merge, and recovering from bad merges that were already committed.
- **`/test-conventions`**, **`/test-evaluation`** — test authoring and audit guidance.
- **`/config-environments`** — designing configuration that differs across environments (dev, staging, production): env var naming, credential isolation, secrets provisioning, and the anti-patterns that reintroduce tight coupling.
- **`/sql-query-conventions`** — read-path conventions for SQL and PostgREST/Supabase queries: pagination, limits, N+1 avoidance, batch-size ceilings, explicit column selection.
- **`/ai-instruction-and-memory-files`** — how AI coding agents load instruction files (CLAUDE.md, AGENTS.md, Cursor rules, Lovable knowledge) and Claude Code auto-memory: precedence, duplication rules, length targets, import patterns.
- **`/read-docx-comments`** — extract comments from `.docx` files with anchored text context.

### Reviewer subagents

Eight stack-agnostic reviewer personas in `claude/.claude/agents/`, spawned by `/plan-review` and `/code-review` based on the **Item ownership** tables in those skills. Each runs in its own context with read-only tools (`Read`, `Grep`, `Glob`, `Bash`).

- **`ciso-reviewer`** — threat modeling, auth boundaries, privilege escalation, data exposure, defense in depth.
- **`staff-backend-engineer`** — API contracts, error handling, idempotency, retry semantics, service boundaries; AND application data-store schema design (relational + NoSQL): partition keys, GSI/LSI, document shape, single-table vs multi-table, index coverage for app queries.
- **`staff-frontend-engineer`** — components, state, data fetching, cache consistency, routing, forms, accessibility, Web Vitals, i18n, client-side analytics emission.
- **`staff-data-engineer`** — operational data infrastructure across all stores: migration pipeline impact, DDL execution shape, CDC / change-stream config, ETL/ELT pipelines, warehouse ingestion transport, schema-drift detection, catalog / lineage tracking.
- **`staff-analytics-engineer`** — warehouse-side modeling (fact/dim, SCD, partitioning, materialization), transformation correctness, source-schema review for ELT-readiness from a data-contract consumer perspective.
- **`staff-platform-engineer`** — CI/CD, IaC, shell, deployment ordering, secret provisioning; observability coverage, alerting, SLO impact, runbook linkage, load, cost; deploy-window ordering and lock-budget on migrations.
- **`staff-product-engineer`** — spec-to-user-problem fidelity, critical spec reading, telemetry semantics, adjacent-regression, backward compat, accessibility-as-spec-fidelity.
- **`staff-sdet`** — testability of the design, pyramid shape, edge cases, mock design, fixture realism, security-invariant coverage, production code that lacks tests.

Schema-change diffs nominally route three ways — `staff-backend-engineer` (designs), `staff-data-engineer` (operational / pipeline impact, DDL shape), `staff-analytics-engineer` (ELT-readiness). Trigger discipline in the skill bodies prevents three-persona fire on trivial additive changes.

### Other

- **`CLAUDE.md`** — baseline engineering instructions (judgment heuristics, working style, safety rules).
- **`settings.json`** — global settings wiring up the hooks and statusline. Session-only overrides (model, effortLevel) are intentionally not tracked — use the `ANTHROPIC_MODEL` and `CLAUDE_CODE_EFFORT_LEVEL` env vars, or `/effort max` mid-session.
- **`statusline-command.sh`** — status bar showing model, context usage, session cost, working directory, and git branch.

## Worktree enforcement

Concurrent Claude Code sessions that share a working tree can race: one session's `git reset --hard`, `git stash`, or `git checkout` silently wipes another session's uncommitted edits. See [Claude Code issue #34327](https://github.com/anthropics/claude-code/issues/34327) for examples of this failure mode in the wild.

`require-worktree-for-git-writes.sh` mitigates by denying non-read-only git operations (`commit`, `push`, `rebase`, `reset`, `merge`, `checkout`, etc.) unless the session runs inside a linked git worktree. Read-only commands (`status`, `log`, `diff`, `fetch`, `show`, `blame`, etc.) are always allowed. The hook is opt-in per repo via a committed sentinel file.

### Activating enforcement on a repo

The sentinel coexists with any existing `.claude/` content — `mkdir -p` is a no-op if the directory is already there, and the sentinel is an inert marker file alongside whatever project-level plans, `settings.local.json`, or untracked worktree dirs the repo already holds.

```bash
cd path/to/your/repo

mkdir -p .claude
cat > .claude/worktree-required <<'EOF'
# Claude Code worktree enforcement marker.
# Presence of this file activates ~/.claude/hooks/require-worktree-for-git-writes.sh.
# See https://github.com/jcdendrite/claude-config for details.
EOF

grep -qxF '.claude/worktrees/' .gitignore 2>/dev/null || echo '.claude/worktrees/' >> .gitignore

git add .claude/worktree-required .gitignore
git commit -m "Activate Claude Code worktree enforcement"
```

### Working inside a worktree

A [git worktree](https://git-scm.com/docs/git-worktree) is a linked working directory on a separate branch that shares the repo's `.git` object storage with the main clone. `git worktree add <path> -b <branch>` creates one; multiple worktrees of the same repo can have different branches checked out simultaneously, which is what lets concurrent Claude Code sessions stay isolated.

With enforcement active, start sessions for non-trivial work in a worktree instead of the main tree:

```bash
git worktree add .claude/worktrees/my-feature -b my-feature
cd .claude/worktrees/my-feature
# work happens here; git commit/push/etc. pass through the hook cleanly
```

Agents spawned with `isolation: worktree` create their own worktrees under `.claude/worktrees/` automatically.

To opt out, delete `.claude/worktree-required`.

## Private-project redaction

This repo is public, so any project codename, organization name, or tracker-ID that lands in a commit or PR description ships to the world. The repo-root [`CLAUDE.md`](./CLAUDE.md) "Redact private-project-identifying content" rule defines what to keep out; `deny-private-project-refs.sh` is the mechanical enforcement.

Two scans run, in order:

1. **Tracker-ID scan (always on, no setup).** Matches `[A-Z]{2,}-\d+` tokens not on the OSS allowlist. The allowlist also reserves two placeholder prefixes — `PROJ-` and `TICKET-` — so skill examples and commit messages can use a realistic-looking tracker shape (`PROJ-<digits>`, `TICKET-<digits>`) without obfuscating the digits to defeat the scan.
2. **Private-projects blocklist (opt-in).** Reads `~/.claude/private-projects.md` at hook runtime and blocks commits/PRs whose content contains any non-comment, non-blank line from the file as a case-insensitive whole-word match.

### Opt-in: enable the blocklist

```bash
# Create the file with a header pointing at this section for usage
# rules (the hook ignores `#` lines, so the header doesn't affect
# matching):
cat > ~/.claude/private-projects.md <<'EOF'
# Project names blocked from commits / PR titles / PR bodies in
# claude-config (and forks). Match semantics + what to put in this
# file: see README.md "Private-project redaction" in the
# claude-config repo.

EOF

# Append your project names, one per line:
echo "Acme Corp" >> ~/.claude/private-projects.md
echo "Project Bluebird" >> ~/.claude/private-projects.md
```

### File format

- One project name per line.
- Lines starting with `#` are comments; ignored.
- Blank lines ignored.
- Leading and trailing whitespace stripped.
- Names can contain spaces.
- Match is case-insensitive whole-word literal. No regex. No globs.

### What to put in the file (and what NOT to)

The match is **case-insensitive whole-word**, which is narrower than substring match — `AcmeCorp` matches `AcmeCorp`, `acmecorp`, `ACMECORP` (any casing as a standalone word), but NOT `AcmeCorpService` (concatenated — `S` is a word character so the boundary fails), and NOT `acme` inside `acmebrand` (substring within a word).

**Worked example.** Suppose your private project is `AcmeCorporation` with tracker prefix `ACME`:

✅ **Add `AcmeCorporation`** — catches the project name as a standalone word in commits, PR bodies, or added diff lines. Case variants (`acmecorporation`, `ACMECORPORATION`) match too — you don't need separate entries.

❌ **Don't add `ACME` alone** — the tracker-ID regex already catches `ACME-<digits>` patterns automatically; bare `ACME` adds nothing the regex doesn't already cover, while introducing a small false-positive surface for legitimate standalone uses of the word.

❌ **Avoid very short or common-word codenames as bare entries.** Whole-word matching shrinks the false-positive surface compared to substring match, but a 3-letter codename like `ART` would still match commits mentioning the word `art` or `ART` standalone (`ART department review`, `the art of war`). If your codename is a common standalone word, use a multi-word form (`ART pipeline` instead of `ART` alone) — the longer phrase is more selective — or rely on reviewer discipline instead of mechanical match.

**Rule of thumb:**

- **Tracker prefixes** (`[A-Z]{2,}` + dash + digits): trust the tracker-ID regex; don't blocklist the bare prefix.
- **Distinctive project names** (full names, codenames ≥ 5 chars and not common English words): blocklist them. Whole-word + case-insensitive handles casing variants automatically.
- **Concatenated identifiers** (`AcmeCorpService`, `acmecorp_client`, `acme-corp-api`): NOT caught by whole-word match against `AcmeCorp`. If a project name commonly appears concatenated AND the concatenated form is sensitive to leak, add the concatenated form as its own entry.

### Why user-local, not committed

A committed list of private-project names in this public repo would itself be the leak — it would hardcode in cleartext the exact strings the rule prevents from shipping. The file lives at `~/.claude/private-projects.md` directly, **not** inside `claude-config/claude/.claude/` (which `stow` symlinks into `$HOME/`). Creating it in the wrong place risks accidental commit; the repo-root `.gitignore` has a belt-and-suspenders entry for `claude/.claude/private-projects.md` as a safety net.

### Privacy of the deny message

When the blocklist scan blocks a commit or PR, the deny message **does not name which entry matched**. Echoing a name the user explicitly flagged as sensitive would re-expose it in terminal output, screenshots, CI logs, and Claude's own conversation context — exactly the surfaces the gate exists to protect. The tracker-ID scan does name matched tokens because they're mechanical patterns, not user-flagged secrets.

### For fork contributors

Forks of `claude-config` inherit the same hook (the scoping check passes for any `claude-config` substring in the origin URL). A fork user can drop their own `~/.claude/private-projects.md` and contribute back without their project names ever ending up in a PR they open against the upstream.

## Tests

Pytest suite for the hooks (covering allow, deny, and ask paths):

```bash
pytest claude/.claude/hooks/tests/
```

CI runs this on every PR and main push via `.github/workflows/hooks.yml`.

## Designing reviewer personas

Three operations on the persona roster, with different decision criteria. **Bias against spawn.** Persona count grows linearly; co-ownership cross-references grow combinatorially.

**Extend** — add review angles to an existing persona. Cheap. Default move.
- *When*: the new angles align with the persona's existing mental model and the persona has room (file under context budget, ownership lines uncluttered).
- *Example*: adding NoSQL document-shape and partition-key design to `staff-backend-engineer`. Backend already thinks about access patterns and query shape — partition keys are adjacent.

**Split** — carve a slice out of an existing persona into a new one.
- *When*: the persona has two genuinely distinct mental models crammed in, OR file is exceeding context budget, OR co-ownership lines are tangled because one persona owns too much.
- *Cost*: ownership lines redrawn, persona files reshaped, dispatcher wiring updated, co-ownership clauses across other personas may shift.
- *Example*: carving warehouse modeling out of `staff-data-engineer` into `staff-analytics-engineer`. OLTP migration-safety reasoning and dimensional modeling are different muscle memory.

**Spawn from scratch** — create a persona for a domain none of the existing ones cover.
- *When*: the gap is chronic (diffs in this category consistently go un-reviewed), the new domain has its own canonical body of failure modes, AND extending an existing persona would dilute that persona's mental model.
- *Cost*: full new persona file, dispatcher entry, co-ownership lines woven into adjacent personas. Higher than split because there's no pre-existing scope to inherit.

Decision tree, in order:
1. **Can an existing persona's scope absorb this without diluting?** If yes → extend.
2. **Is this a slice of an existing persona that has grown two distinct mental models?** If yes → split.
3. **Is this a chronic gap that no existing persona covers, with its own canonical failure-mode body?** If yes → spawn.

Splits and spawns must come with explicit ownership-line updates in adjacent personas — every co-ownership line touching the changed persona is a candidate edit.

### Roles intentionally not in the roster

For an AI-driven code-review system, the right criterion for adding a persona is **distinct review heuristics that an AI reviewer can act on from a diff** — not industry-headcount-mimicry. Some industry-recognized roles are deliberately absent because the part of their work that survives translation to AI review is already covered, or because their distinctive value depends on signals an AI reviewer doesn't have:

- **Database Reliability Engineer (DBRE)** — distinctive value in human teams is *live-system observability*: production metrics in real time, replication-lag trends, query plan stability post-migration, buffer-cache impact, page-size effects at scale. None of that is available to an AI reviewer working from a diff. The static heuristics that survive — DDL execution shape, lock-cost, deploy-window ordering — are already covered by `staff-data-engineer` (DDL form authority) and `staff-platform-engineer` (deploy-window, lock-budget). A separate DBRE persona would just rename a slice of those without adding a paradigm an AI can apply.
- **Data platform engineer** — warehouse infrastructure ownership, orchestration tooling operation (Airflow / Dagster runtime), catalog tooling operation. Currently absorbed into `staff-data-engineer` (pipeline transport / observability) and `staff-platform-engineer` (orchestration as ops surface). Splitting it out would help only at scales where the warehouse infra is its own engineering domain with reviews distinct from app-side data engineering.
- **Data steward / governance** — PII tagging policy, downstream-consumer contracts, data-contract enforcement. Largely a non-engineering function in most orgs (legal / compliance / data-governance). `staff-data-engineer` flags PII-shaped column candidates conditionally rather than asserting governance policy; the policy itself stays human-owned.

If a project's review pattern consistently surfaces gaps in these areas — and the AI can act on signals visible in the diff — the right response is to **spawn** (per the decision tree above) with explicit ownership-line updates in adjacent personas. Don't overload an existing persona just because the work is "data-shaped."

## Machine-specific overrides

Machine-local Claude Code permissions belong in `~/.claude/settings.local.json` (not tracked).
