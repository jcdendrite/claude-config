# claude-config

Portable [Claude Code](https://claude.ai/claude-code) global configuration: custom skills, PreToolUse hooks that gate `git commit` and PR-comment flows, and a custom statusline. Runs on any Unix-like system (Linux, macOS, WSL). Managed with [GNU Stow](https://www.gnu.org/software/stow/).

Maintained by [Cordova Strategy](https://cordovastrategy.com).

## Philosophy

### Why this repo exists

Working across a variety of projects from very early stage to enterprise-level, I wanted to use a flexible but predictable setup for Claude Code to help me get through large chunks of work efficiently and cost-effectively: from security audits of vibe-coded codebases to building out standard features, adding testing infrastructure, and enforcing quality against industry-standard, best-practice checklists. I wanted to stop fixing the same errors that AI agents kept encountering and really focus my time on the nuanced technical challenges that need human judgment.

### How this repo is different from others

AI agents are powerful but probabilistic. They will frequently gaslight you, or more fairly put, they will confidently draw incorrect conclusions from prose (even top-class models). Simply stated, unsupervised AI is reliable for summarization but not synthesis. I wanted to add a layer of determinism on top of agents' inherently probabilistic judgment to enforce quality and prevent repetitive errors where I could. To achieve these goals, I added safeguards in this repo with *hooks* and *markers*.

I also wanted to encode industry-standard best practices in this repo. LLMs are trained on the corpus of the internet and are biased by the loudest and most common viewpoints. While the wisdom of the masses can often be directionally correct, it's best to defer to primary sources and scientific thinking, applying the rigor of research and adhering to evidence-based approaches. You'll see those perspectives represented in the content of the *skills* and *instructions* in this repo, with *references* to primary sources that converge on tried-and-true guidelines on how to design and write good software.

### How enforcement complements instructions

Claude Code without enforcement will claim code is done before tests pass, skip the review step when it judges a change "too small," write to the main worktree when a concurrent session is already staged there, and paste project codenames directly into commit messages and PR bodies. claude-config makes these mistakes structurally impossible rather than relying on prompt instructions.

A CLAUDE.md instruction says "you should run code-review before committing." A PreToolUse hook says "the commit is denied until code-review ran on this exact diff in this session." This distinction is the core design choice: enforce at the tool-call boundary, not at the prompt layer, because prompt-layer instructions are advisory — the model can disregard them on any change it judges simple enough not to need review.

claude-config is a **workflow-enforcement layer** — hooks that gate what Claude can do until explicit review steps are satisfied. It wires in the `anthropics/claude-plugins-official` marketplace but ships official plugins disabled by default; stow users can enable any of them via `enabledPlugins` in their settings. claude-config ships the enforcement harness; hand-rolled `~/.claude/` configs improvise the patterns claude-config systematizes: per-session marker keying, specialist reviewer routing, and three-tier redaction.

## Requirements

- **Operating system:** Linux, macOS, or WSL2. Native Windows (PowerShell / cmd.exe) is not supported — every hook is a bash script and `install.sh` uses GNU `stow` with symlinks. If you're on Windows, install inside [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) instead.
- **Shell:** `bash`. Hooks and `install.sh` use `#!/bin/bash`.
- **Tools:** `stow`, `git`, `gh`, `jq`, `sha256sum`, and the `claude` CLI. `install.sh` verifies they exist and exits early if any are missing.
- **Optional:** `pytest` for running the test suite (`pytest claude/.claude/`).

**macOS:** `sha256sum` ships in GNU `coreutils`. Install with `brew install coreutils`, then add the gnubin directory to PATH so the unprefixed name resolves: `export PATH="$(brew --prefix coreutils)/libexec/gnubin:$PATH"`.

**PATH setup for script wrappers:** The three user-facing scripts are installed as wrappers under `~/.local/bin/`. That directory needs to be on your PATH:

- **Linux / WSL2 (Ubuntu-based):** `~/.profile` auto-adds `~/.local/bin` if the directory exists. Re-login, or pick it up immediately: `source ~/.profile`
- **macOS (stock zsh):** not auto-added. Add once: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc`
- **fish (any platform):** `fish_add_path ~/.local/bin`

Verify: `command -v cleanup-merged-branches` should print the wrapper path.

**Existing users:** `git pull` does not materialize the new wrappers automatically — re-run `./install.sh` once after pulling. After re-stowing, run `git status` in the repo: if any file under `claude/.local/bin/` shows as modified, stow's `--adopt` flag adopted a same-named local file. Revert with `git checkout claude/.local/bin/<name>` and rename the conflicting local script.

## Setup

```bash
git clone git@github.com:jcdendrite/claude-config.git ~/claude-config
cd ~/claude-config
./install.sh
```

This symlinks `claude/.claude/` into `$HOME/.claude/`.

## What this installs

### Workflow

The skills form a sequential pipeline that covers the full contribution lifecycle. Hooks enforce the transitions so steps cannot be skipped.

```mermaid
flowchart LR
    A[/plan-it/] --> B[/plan-review/]
    B -->|"require-plan-review.sh\ngates Write/Edit while plan exists"| C([Write code])
    C --> D[/code-review/]
    D -->|"require-code-review.sh\ngates git commit"| E([git commit])
    E --> F[/ready-for-review/]
    F -->|"require-ready-for-review.sh\ngates git push"| G([git push / PR open])
    G --> H([Review comments arrive])
    H --> I[/respond-pr/]
    I -->|"require-respond-pr.sh\ngates gh api comment reads/posts"| J([PR replies posted])

    K([Session start]) -->|"capture-session-id.sh\nwrites session-id for marker lookup"| D
    K --> F
```

**Skills in the pipeline:**

- **`/plan-it`** — produce an implementation plan in `.claude/plans/<topic-slug>.md` through discovery, codebase exploration, clarifying questions, and architecture design; hands off to `/plan-review`.
- **`/plan-review`** — review an implementation plan against domain checklists before presenting it to a human; required before writing code when a plan file exists in `.claude/plans/`.
- **`/code-review`** — principal-engineer code review with ripple-effect triage and domain-specific audits; required before `git commit`.
- **`/skill-review`** — behavioral-equivalence audit for `SKILL.md` changes; required before `git commit` when staged changes include a SKILL.md. Produces an explicit table verifying that every removed or shortened line's behavior is preserved.
- **`/ready-for-review`** — pre-handoff gate that verifies tests/lint/typecheck and reviews the cumulative PR diff; required before `git push` on a branch with an open PR.
- **`/respond-pr`** — fetch all PR review comments (inline, top-level, review summaries) and post replies with `[Claude Code]` attribution; required before reading or posting PR comments via `gh api`.

**Hook transitions:**

| Hook | Gates | Cleared by |
|---|---|---|
| `require-plan-review.sh` | `Write`/`Edit` while a plan file exists in `.claude/plans/` | `/plan-review` per-session marker |
| `require-code-review.sh` | `git commit` | `/code-review` run against current staged state |
| `require-skill-review.sh` | `git commit` when staged changes include a `SKILL.md` | `/skill-review` behavioral-equivalence audit |
| `require-ready-for-review.sh` | `git push`, `gh pr ready` | `/ready-for-review` run since last commit |
| `require-respond-pr.sh` | `gh api` PR comment reads/posts | `/respond-pr` active bypass marker |
| `capture-session-id.sh` | — (SessionStart, no gate) | Writes session-id so marker filenames are per-session |

### Hooks

- **`require-code-review.sh`** — blocks `git commit` (including chained forms like `git add . && git commit`) until `/code-review` has run on the current staged state. Verified via per-session sha256 marker in `~/.claude/review-markers/<repo-hash>.<session-id>`, which auto-invalidates the moment the staging area changes. Per-session keying prevents two parallel sessions in the same worktree from overwriting each other's markers when staging different diffs.
- **`require-skill-review.sh`** — blocks `git commit` only when staged changes include a `SKILL.md`. Requires `/skill-review` to have produced a behavioral-equivalence audit for any removed or shortened lines. Marker is keyed to the SKILL.md-scoped diff (not the full staged diff), so re-staging non-skill files after a clean review does not invalidate the marker.
- **`deny-private-project-refs.sh`** — blocks `git commit`, `gh pr create`, and `gh pr edit` when the staged diff, commit message, or PR title/body/body-source-file contains either (a) tracker-ID tokens (`[A-Z]{2,}-\d+`) outside an OSS-prefix allowlist (`CVE-`, `RFC-`, `GH-`, and similar — see the script for the full list), or (b) a literal substring match against entries in the user's opt-in `~/.claude/private-projects.md` blocklist. Enforces the mechanical categories of the repo-root `CLAUDE.md` redaction rule; structural fingerprints still require review discipline. See [Private-project redaction](#private-project-redaction) below.
- **`require-stow-reminder.sh`** — scoped to the claude-config repo. Blocks `gh pr create` and `gh pr edit` (when the edit changes the body) if the PR adds a new immediate child of `claude/.claude/` (file or directory) and neither the inline command, a referenced `--body-file`/`--template`, nor any `--fill`-sourced commit message mentions `install.sh` or `stow` (case-insensitive). Reason: GNU Stow links each top-level child of `claude/.claude/` individually, so a brand-new child only appears in `~/.claude/` after re-running `install.sh` — `git pull` alone won't materialize the symlink. The reminder lands in the PR body so the post-merge stow step doesn't get forgotten at merge time.
- **`require-respond-pr.sh`** — blocks PR comment reads and posts (`gh api .../pulls|issues/N/{comments,reviews}`, `gh pr comment`, `gh pr review`) and redirects to `/respond-pr`, so all three comment types get fetched and replies carry the `[Claude Code]` attribution prefix. Honors a per-session bypass marker at `~/.claude/.respond-pr-active.d/<session_id>` that the skill sets on entry and removes on exit; the hook refreshes the marker's mtime on each bypass so long skill runs don't hit the 60-minute staleness cutoff. Per-session keying (rather than a singleton path) keeps parallel Claude sessions from thrashing on cleanup or leaking bypass to unrelated sessions.
- **`require-ready-for-review.sh`** — gates `git push` and `gh pr ready` on branches with an open PR. Requires `/ready-for-review` to have run and passed since the last commit. Verified via a per-session marker keyed by HEAD SHA — a new commit invalidates the marker automatically and forces a re-run. An active-skill bypass marker (`~/.claude/.ready-for-review-active.d/<session_id>`) prevents the skill's own iteration pushes (fix → push → loop) from self-denying.
- **`capture-session-id.sh`** (SessionStart) — at session start, writes the session's `session_id` to `~/.claude/sessions/<claude-pid>` so skills running as Bash tool calls (which don't see the hook payload) can look up their own session id via the bash tool's `$PPID`. Used by both `/respond-pr` and `/code-review` to compute per-session marker filenames.
- **`ask-review-permissions.sh`** — asks before `Edit`/`Write`/`MultiEdit` to `.claude/settings*.json`, nudging toward `/review-permissions` when the edit touches `permissions.allow`.
- **`require-worktree-for-git-writes.sh`** — opt-in per repo. When active, denies non-read-only git operations unless the session runs in a linked git worktree. Prevents concurrent Claude Code sessions from racing on the same working tree. See [Worktree enforcement](#worktree-enforcement) below for opt-in instructions.
- **`require-worktree-for-file-writes.sh`** — opt-in per repo (same `.claude/worktree-required` sentinel). When active, denies `Edit`/`Write`/`MultiEdit` on paths in the main working tree; all file edits must land in a linked worktree.
- **`guard-settings-model-effort.sh`** — blocks `git commit` when `claude/.claude/settings.json` has staged changes to `model` or `effortLevel`. Per-session overrides should not be committed; use `ANTHROPIC_MODEL`/`CLAUDE_CODE_EFFORT_LEVEL` env vars or `/effort max` mid-session instead.
- **`check-skill-length.sh`** — blocks `git commit` when a staged `SKILL.md` exceeds 200 lines and grew vs HEAD. Nudges toward splitting a growing skill or moving reference material to a co-located `REFERENCES.md`.
- **`session-marker-dashboard.sh`** (SessionStart) — at session start, emits a summary of any active bypass markers (`/respond-pr`, `/ready-for-review`) into the resumed session's context so stale bypasses are visible.

### Skills (slash commands)

- **`/plan-it`** — produce an implementation plan in `.claude/plans/<topic-slug>.md` through discovery, codebase exploration, clarifying questions, and architecture design; hands off to `/plan-review`.
- **`/plan-review`** — review implementation plans before presenting, with domain-specific reviewer roles.
- **`/code-review`** — principal engineer code review checklist with ripple-effect triage and domain-specific audits (backend, frontend, security, infrastructure, data).
- **`/skill-review`** — behavioral-equivalence audit for `SKILL.md` changes; required before `git commit` when staged changes include a SKILL.md. Produces an explicit table verifying every removed or shortened line's behavior is preserved.
- **`/ready-for-review`** — pre-handoff gate: verifies tests/lint/typecheck, runs `/code-review` against the cumulative PR diff (all commits vs default branch), and syncs the PR description; required before `git push` on a branch with an open PR.
- **`/review-permissions`** — security audit of `permissions.allow` rules with a 21-item checklist.
- **`/respond-pr`** — fetch and address PR review comments, with `[Claude Code]` attribution on all replies.
- **`/claude-hook-review`** — review playbook for `claude/.claude/hooks/*.sh` and `settings.json` hook entries: event/matcher selection, defense-in-depth filtering within the script body, exit-code contracts.
- **`/branch-creation`** — naming conventions (`<TICKET-ID>/<topic-slug>` for ticketed projects, `<topic-slug>` alone otherwise), anti-patterns to reject (tracker `<user>/` defaults), and branching from a fresh default-branch tip.
- **`/git-feature-branch-sync`** — decision framework for keeping a feature branch current with the default branch: when to rebase-and-force-push vs merge-in, and how to force-push safely (`--force-with-lease` vs `--force-if-includes`).
- **`/git-state-safety`** — safely inspecting other branches when the working tree is in a fragile state (mid-merge, mid-rebase, mid-cherry-pick), avoiding the silently-corrupted-index failure mode where a diagnostic `git checkout <ref> -- <path>` overwrites a partially-resolved merge, and recovering from bad merges that were already committed.
- **`/test-conventions`**, **`/test-evaluation`** — test authoring and audit guidance.
- **`/config-environments`** — designing configuration that differs across environments (dev, staging, production): env var naming, credential isolation, secrets provisioning, and the anti-patterns that reintroduce tight coupling.
- **`/sql-query-conventions`** — read-path conventions for SQL and PostgREST/Supabase queries: pagination, limits, N+1 avoidance, batch-size ceilings, explicit column selection.
- **`/ai-instruction-and-memory-files`** — how AI coding agents load instruction files (CLAUDE.md, AGENTS.md, Cursor rules, Lovable knowledge) and Claude Code auto-memory: precedence, duplication rules, length targets, import patterns.
- **`/verify-primary-sources`** — when web research informs a code or design decision, read the primary documentation directly rather than trusting agent summaries or secondary sources.
- **`/read-docx-comments`** — extract comments from `.docx` files with anchored text context.
- **`/handoff`** — write a structured cross-session handoff file at `/tmp/<slug>-handoff.md` capturing goal, status, next step, modified files, active markers, open questions, and the resume incantation (`Read /tmp/<slug>-handoff.md and continue.`). User-invoked only — implemented as a slash command at `claude/.claude/commands/handoff.md`, not a skill, to keep it out of the always-loaded skills catalog. Claude proactively suggests `/handoff` when context usage exceeds ~60% with an incomplete task; you decide whether to invoke.

Each skill lives in `claude/.claude/skills/<skill-name>/SKILL.md`. A skill directory may also contain a `REFERENCES.md` — canonical references (URLs, key quotes, framework notes) that informed the skill's rules. `REFERENCES.md` is not loaded during skill execution; consult it when editing a skill to verify a rule still holds or to add new guidance.

#### Skill architecture notes

- **`SKILL.md` is self-contained.** Bundled reference files (`REFERENCES.md`) are read on demand via Bash/Read tool calls, not loaded automatically at skill runtime.
- **Frontmatter has no inclusion fields.** There are no `includes:`, `import:`, or `extends:` frontmatter keys — skills do not support partial inclusion.
- **`@path` import syntax is for `CLAUDE.md` only.** The `@path/to/file` import pattern that works in `CLAUDE.md` files is not supported in `SKILL.md`.
- **Duplicate rule text across skills intentionally.** When two skills need the same rule, copy it into both — do not extract it into a `_shared/` partial or similar abstraction. Duplication is deliberate: it keeps each skill independently readable and avoids brittle cross-skill coupling. If you find yourself wanting a shared partial, that is a signal to reconsider whether the skills should be merged, not a signal to add an include mechanism.

#### Project-specific layers

`/plan-review` and `/code-review` auto-load a project-specific layer if one exists in the consuming repo — so a project can extend the base checklist without forking the public skill body.

- **Location:** `.claude/skills/code-review-<project>/SKILL.md` or `.claude/skills/plan-review-<project>/SKILL.md`, placed in the consuming repo. The `<project>` token is freeform; only the prefix (`code-review-` or `plan-review-`) is load-bearing.
- **Frontmatter:** match the shape of any skill in `claude/.claude/skills/` (`name`, `description`, `user-invocable`). The parent invokes the layer via the Skill tool — not via description-based auto-trigger, which doesn't fire from inside a running skill (design rationale in [`docs/design-decisions.md`](docs/design-decisions.md)).
- **Behavior:** glob runs from the repo root (`git rev-parse --show-toplevel`). Single match → invoked and merged into the base checklist. Multiple matches → review stops — that's a config error in the consuming project, not a review item the skill resolves. Zero matches → proceeds without a layer.

### Plugins (marketplace)

Skills that apply to one or a few private projects — not broadly to all sessions — live as marketplace plugins under `plugins/<name>/` rather than in `claude/.claude/skills/`. This keeps them out of the global skill catalog and lets them be installed only in the repos that need them.

This repo exposes a marketplace via `.claude-plugin/marketplace.json`. Each plugin lives under `plugins/<name>/` with a `.claude-plugin/plugin.json` manifest and skills under `plugins/<name>/skills/<name>/SKILL.md`.

**Current plugins:**

- **`lovable-knowledge`** — Lovable Project Knowledge vs Workspace Knowledge fields, the `.lovable/*.md` repo-mirror workflow, content scope split, precedence, and character limits. To install at project scope from a Lovable project repo: first register the marketplace (`claude plugin marketplace add <path-or-URL-to-claude-config>`), then `claude plugin install lovable-knowledge@claude-config --scope project`.

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
- **`settings.json`** — global settings wiring up the hooks, statusline, and a `permissions.deny` hard floor for `sudo` and secret-file reads (see [Auto mode](#auto-mode)). Configured with **opusplan** as the default model (cost-effective and [recommended by Anthropic](https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code)). Session-only overrides (model, effortLevel) are intentionally not tracked — use the `ANTHROPIC_MODEL` and `CLAUDE_CODE_EFFORT_LEVEL` env vars, or `/effort max` mid-session.
- **`statusline-command.sh`** — status bar showing model, context usage, session cost, working directory, and git branch.

### Scripts

Utility scripts in `claude/.claude/scripts/` (stowed to `~/.claude/scripts/`).

- **`analyze-context.py`** — inspect context window growth for a Claude Code session. Reads `~/.claude/projects/<project>/<session>.jsonl` and `~/.claude/usage-data/session-meta/` locally; no network calls, no writes.

  ```bash
  # Latest session in current project (run from project root)
  analyze-context

  # Heaviest sessions across all projects
  analyze-context --top
  analyze-context --top 20

  # Specific session
  analyze-context <session-id>
  ```

  The per-session view reports start/peak/end token counts, a growth curve
  (context window size per turn), and the ten turns with the largest single-step
  jumps — useful for identifying which tool results or subagent returns are
  expanding the context most.

  The `--top` view ranks sessions by direct token usage (input + output; cache
  reads excluded) from session metadata, and prints the session ID so you can
  drill in with the per-session view.

- **`token-analyzer.py`** — cross-session per-model token breakdown (Opus / Sonnet / Haiku) with cache-hit rates, plus a list of Opus sessions that likely could have run on Sonnet (no plan-mode, no edits, no sub-agent dispatch, no extended thinking, no judgment-skill invocations). Reads `~/.claude/projects/*/*.jsonl`; no network calls, no writes.

  ```bash
  token-analyzer             # all-time
  token-analyzer --since 7d  # include token activity from the last N days (e.g. 2d, 7d)
  ```

- **`marker.sh`** — write and remove review markers on behalf of workflow skills. `/code-review`, `/skill-review`, `/plan-review`, `/ready-for-review`, and `/respond-pr` write review markers via `~/.claude/scripts/marker.sh`. The 10 valid invocation shapes are allowlisted in `settings.json` for silent auto-approval. A companion `enforce-marker-script-shape.sh` hook denies any other invocation (chains, env-var prefixes, redirects) to prevent prompt-injection escalation via the allowlist.

- **`cleanup-merged-branches.sh`** — discovers all local branches whose PRs are merged (queried via `gh pr list --head <branch> --state merged`) and cleans them up: removes the worktree, force-deletes the local branch, prunes the remote tracking ref, deletes the remote branch if not auto-deleted, and fast-forwards the default branch. Auto-approved by the paired `permissions.allow` entries.

  ```bash
  cleanup-merged-branches          # run cleanup
  cleanup-merged-branches --dry-run  # preview without acting
  ```

## Context management

Claude Code compresses conversation history when the context window fills up. This config adds two layers to keep that process reliable.

### How it works

1. **Marker re-injection (automatic).** `session-marker-dashboard.sh` is registered with matcher `startup|clear|compact`, so it fires on session start, after `/clear`, and after compaction. It emits `hookSpecificOutput.additionalContext` with the current state of all active review-skill gate markers, restoring marker knowledge in the resumed context automatically. You don't need to do anything for this to work.

2. **`/handoff` slash command (user-invoked).** When the task will continue in a fresh session, run `/handoff` to write a structured resume file at `/tmp/<slug>-handoff.md`. The §1–§6 shape is defined inline in `claude/.claude/commands/handoff.md`. Claude proactively suggests `/handoff` at ~60% context usage because cleaner context produces a higher-quality resume file, and every turn beyond 60% is waste.

### When to use which

| Situation | Right action |
|---|---|
| Switching to an unrelated task | `/clear` (intent-driven, no percentage threshold) |
| Ending a session, will resume later | `/handoff` (produces resume file) |
| Context >83.5%: auto-compact fires | Happens automatically; marker state is restored by the hook |

### Threshold reference

- ~60%: suggested threshold for `/handoff` — [Anthropic best practices](https://code.claude.com/docs/en/best-practices) cite 60% as the point where context compression produces the highest-quality summary; the same logic applies to `/handoff` (less context noise → better resume file).
- ~83.5%: auto-compact trigger (community-reported; configurable via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`).
- Run `analyze-context` to inspect token usage for the current session.

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

Pytest suite covering hooks (allow, deny, and ask paths) and skill description contracts:

```bash
pytest claude/.claude/
```

CI runs this on every PR and main push via `.github/workflows/hooks.yml`.

## Threat model

The hook system protects against three failure modes: committing project identifiers to a public repo (the tracker-ID regex and private-projects blocklist run before every `git commit` and PR create/edit); claiming work is done before tests pass or code review ran (the require-code-review and require-ready-for-review hooks deny the commit or push until the review marker exists for the current staged state); and two concurrent Claude sessions racing on the same working tree (require-worktree-for-git-writes denies write-path git operations unless the session is inside a linked worktree).

The hook system does not protect against a skill or hook script itself being malicious — if an attacker can write to `claude/.claude/`, they can ship a hook that exfiltrates secrets before denying the command. It does not protect against an attacker with write access to `~/.claude/`: the marker directory, session files, and hook scripts all live there, and tampering with any of them can bypass or forge gate checks. It does not protect against a model that quotes sensitive tool output back to the user in chat — the hooks gate tool calls, not what the model says; if Claude reads a secret from an allowed path and repeats it in conversation, no hook fires.

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

## Auto mode

Auto mode replaces per-action permission prompts with a background classifier that evaluates each tool call before it runs, blocking anything irreversible, destructive, or targeted outside your environment. See the [engineering deep dive](https://www.anthropic.com/engineering/claude-code-auto-mode) and the [permission modes reference](https://code.claude.com/docs/en/permission-modes) for how the two-layer pipeline works.

### Requirements

- **Plan:** Max, Team, Enterprise, or Anthropic API. Not available on Pro, or on Bedrock, Vertex, or Foundry.
- **Model:** Claude Sonnet 4.6, Opus 4.6, or Opus 4.7 (Team/Enterprise/API); Opus 4.7 only on Max. Verify that any custom model alias in your `settings.json` ultimately resolves to one of these.
- **Claude Code:** v2.1.83 or later.

### Activating

Press **Shift+Tab** in the CLI to cycle through modes until `auto` appears, then accept the one-time opt-in prompt. To start directly in auto mode:

```bash
claude --permission-mode auto
```

To make it the default, add to `~/.claude/settings.json`:

```json
{
  "permissions": {
    "defaultMode": "auto"
  }
}
```

### Hard-floor deny rules

`settings.json` in this repo ships a `permissions.deny` list that runs *before* the classifier and cannot be overridden by any `autoMode.allow` entry. These close gaps the classifier's default block list doesn't cover:

| Rule | What it closes |
|---|---|
| `Bash(sudo *)`, `Bash(sudo)` | Privilege escalation — turns the `sudo` prohibition in `CLAUDE.md` into a hard block |
| `Read(**/.env)`, `Read(**/.env.local)`, `Read(**/.env.local.*)`, `Read(**/.env.production)`, `Read(**/.env.production.*)`, `Read(**/.env.development)`, `Read(**/.env.development.*)`, `Read(**/.env.staging)`, `Read(**/.env.staging.*)`, `Read(**/.env.test)`, `Read(**/.env.test.*)` | Local secret reads — hard floors on the well-known secret-bearing variants; the classifier won't flag in-working-directory reads as exfiltration |
| `Read(**/credentials.json)` | Cloud provider credential files (AWS CLI, GCP service accounts, etc.) |

The `deny-env-reads.sh` PreToolUse hook covers `.env.*` variants not listed above. It allows the three conventional non-secret template suffixes (`.env.example`, `.env.template`, `.env.sample`) while denying everything else, including symlinks whose resolved target's basename matches a denied pattern.

These rules apply in all permission modes, not only auto mode.

### What to put in `settings.local.json`

The classifier trusts only the working repo and its configured remotes by default. Add `autoMode.environment` to `~/.claude/settings.local.json` (gitignored) to declare which infrastructure is yours, reducing false positives on routine operations:

```json
{
  "autoMode": {
    "environment": [
      "$defaults",
      "Organization: <org name>. Primary use: <use case, e.g. software development, security consulting>.",
      "Source control: github.com — only repos this developer is a collaborator on. Do not push to other organizations.",
      "Trusted domains: <domains your work regularly reaches, e.g. supabase.com, vercel.com, api.example.com>",
      "Additional context: <regulated industry, multi-tenant infrastructure, compliance constraints if any>"
    ]
  }
}
```

`"$defaults"` splices in the built-in trust list at that position. Omit it only if you intend to replace the defaults entirely — doing so silently drops all built-in block rules including force-push and `curl | bash` protection. See the [danger note in the config reference](https://code.claude.com/docs/en/auto-mode-config#override-the-block-and-allow-rules).

Keep project names, internal hostnames, and private domain names in `settings.local.json`. Do not put them in the committed `settings.json`.

Start minimal and expand reactively: run `claude auto-mode config` to see your effective config, and check `/permissions → Recently denied` after the first few sessions to find legitimate operations the classifier is blocking.

### Broad allow rules drop in auto mode

When auto mode activates, Claude Code silently drops `permissions.allow` rules that grant arbitrary code execution:

- Blanket wildcards: `Bash(*)`, `PowerShell(*)`
- Wildcarded interpreters: `Bash(python3:*)`, `Bash(node:*)`, and similar
- Package-manager run commands

Check your `settings.local.json` for entries matching these patterns — those operations will route to the classifier instead of auto-approving. Narrow rules like `Bash(npm test)` carry over unchanged. Dropped rules are restored when you leave auto mode.

### Inspection and tuning

```bash
claude auto-mode defaults   # print built-in environment, allow, and soft_deny rules
claude auto-mode config     # print effective config with your settings applied
claude auto-mode critique   # get AI feedback on your custom rules
```

## Output preferences

To customize response tone, formatting, and communication style, create `~/.claude/output-preferences.md`. This file is user-local and never committed to this repo. It is loaded via an instruction in `claude/.claude/CLAUDE.md`'s "Output Preferences" section.

**Cap:** keep it under 50 lines — content beyond that competes with project context for the 200-line CLAUDE.md budget. Avoid duplicating rules already in the global CLAUDE.md — they apply regardless, and duplicate entries waste context budget.

**Template:**

```markdown
# Output preferences

- Tone: direct and calibrated — state things plainly; match certainty to evidence (no overclaiming, no hedging filler).
- Length: concise. Include the why when non-obvious; skip narration of internal process.
- Avoid emoji unless explicitly asked.
- Prefer plain prose over bullet lists when the answer is a single concept.
```
