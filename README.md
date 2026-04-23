# claude-config

Portable [Claude Code](https://claude.ai/claude-code) global configuration: custom skills, PreToolUse hooks that gate `git commit` and PR-comment flows, and a custom statusline. Runs on any Unix-like system (Linux, macOS, WSL). Managed with [GNU Stow](https://www.gnu.org/software/stow/).

## Prerequisites

`stow`, `git`, `gh`, `jq`, `sha256sum`. `install.sh` verifies they exist and exits early if any are missing.

**macOS:** `sha256sum` ships in GNU `coreutils`. Install with `brew install coreutils`, then add the gnubin directory to PATH so the unprefixed name resolves: `export PATH="$(brew --prefix coreutils)/libexec/gnubin:$PATH"`.

## Setup

```bash
git clone git@github.com:jcdendrite/claude-config.git ~/claude-config
cd ~/claude-config
./install.sh
```

This symlinks `claude/.claude/` into `$HOME/.claude/`.

## What this installs

### Hooks (PreToolUse gates)

- **`require-code-review.sh`** — blocks `git commit` (including chained forms like `git add . && git commit`) until `/code-review` has run on the current staged state. Verified via sha256 marker in `~/.claude/review-markers/<repo-hash>`, which auto-invalidates the moment the staging area changes.
- **`deny-client-refs.sh`** — blocks `git commit` when the staged diff or commit message contains tracker-ID tokens (`[A-Z]{2,}-\d+`) outside an OSS-prefix allowlist (`CVE-`, `RFC-`, `GH-`, and similar — see the script for the full list). Enforces the tracker-ID category of the repo-root `CLAUDE.md` redaction rule; other categories (client names, internal tool names, structural fingerprints) still require review discipline.
- **`require-respond-pr.sh`** — blocks PR comment reads and posts (`gh api .../pulls|issues/N/{comments,reviews}`, `gh pr comment`, `gh pr review`) and redirects to `/respond-pr`, so all three comment types get fetched and replies carry the `[Claude Code]` attribution prefix. Honors a 60-minute bypass marker at `~/.claude/.respond-pr-active` that the skill sets on entry and removes on exit.
- **`ask-review-permissions.sh`** — asks before `Edit`/`Write`/`MultiEdit` to `.claude/settings*.json`, nudging toward `/review-permissions` when the edit touches `permissions.allow`.

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

### Other

- **`CLAUDE.md`** — baseline engineering instructions (judgment heuristics, working style, safety rules).
- **`settings.json`** — global settings wiring up the hooks and statusline. Session-only overrides (model, effortLevel) are intentionally not tracked — use the `ANTHROPIC_MODEL` and `CLAUDE_CODE_EFFORT_LEVEL` env vars, or `/effort max` mid-session.
- **`statusline-command.sh`** — status bar showing model, context usage, session cost, working directory, and git branch.

## Tests

Pytest suite for the hooks (37 cases covering allow, deny, and ask paths):

```bash
pytest claude/.claude/hooks/tests/
```

CI runs this on every PR and main push via `.github/workflows/hooks.yml`.

## Machine-specific overrides

Machine-local Claude Code permissions belong in `~/.claude/settings.local.json` (not tracked).
