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
- **`deny-private-project-refs.sh`** — blocks `git commit`, `gh pr create`, and `gh pr edit` when the staged diff, commit message, or PR title/body/body-source-file contains either (a) tracker-ID tokens (`[A-Z]{2,}-\d+`) outside an OSS-prefix allowlist (`CVE-`, `RFC-`, `GH-`, and similar — see the script for the full list), or (b) a literal substring match against entries in the user's opt-in `~/.claude/private-projects.md` blocklist. Enforces the mechanical categories of the repo-root `CLAUDE.md` redaction rule; structural fingerprints still require review discipline. See [Private-project redaction](#private-project-redaction) below.
- **`require-respond-pr.sh`** — blocks PR comment reads and posts (`gh api .../pulls|issues/N/{comments,reviews}`, `gh pr comment`, `gh pr review`) and redirects to `/respond-pr`, so all three comment types get fetched and replies carry the `[Claude Code]` attribution prefix. Honors a 60-minute bypass marker at `~/.claude/.respond-pr-active` that the skill sets on entry and removes on exit.
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

1. **Tracker-ID scan (always on, no setup).** Matches `[A-Z]{2,}-\d+` tokens not on the OSS allowlist.
2. **Private-projects blocklist (opt-in).** Reads `~/.claude/private-projects.md` at hook runtime and blocks commits/PRs whose content contains any non-comment, non-blank line from the file as a case-insensitive literal substring.

### Opt-in: enable the blocklist

```bash
touch ~/.claude/private-projects.md
echo "Acme Corp" >> ~/.claude/private-projects.md
echo "Project Bluebird" >> ~/.claude/private-projects.md

# Same source of truth for Claude's context — so it self-scrubs while drafting:
echo "@private-projects.md" >> ~/.claude/CLAUDE.md
```

### File format

- One project name per line.
- Lines starting with `#` are comments; ignored.
- Blank lines ignored.
- Leading and trailing whitespace stripped.
- Names can contain spaces.
- Match is case-insensitive literal substring. No regex. No globs.

### What to put in the file (and what NOT to)

The two scans cover different shapes — understanding the split keeps you from adding entries that backfire:

- **Tracker-ID scan (always on):** automatically catches `[A-Z]{2,}-\d+` patterns. So any `ACME-<n>` tracker reference (where `<n>` is digits) is *already* blocked — you don't need a blocklist entry for them.
- **Blocklist scan (this file):** catches case-insensitive literal substrings of the entries you list.

**Worked example.** Suppose your private project is `AcmeCorporation` with tracker prefix `ACME`:

✅ **Add `AcmeCorporation`** — catches the bare project name in commits like "Refactor AcmeCorporation auth flow", which the tracker-ID regex doesn't cover. Case-insensitive match handles `acmecorporation`, `ACMECORPORATION`, etc., so you don't need variants.

❌ **Don't add `ACME`** — the tracker-ID regex already catches `ACME-<digits>`. Adding bare `ACME` to the blocklist would false-positive on the substring `acme` inside English words (`acme of clarity`, `Acme Anvil` references in documentation, etc.). For shorter prefixes like `DAY` the false-positive surface is much worse (`today`, `holiday`, `daylight`).

**Rule of thumb:**

- **Tracker prefixes** (`[A-Z]{2,}` + dash + digits): trust the tracker-ID regex; don't blocklist the bare prefix.
- **Distinctive project names** (full names, codenames): blocklist them.
- **Generic-word codenames** (`Pulse`, `Atlas`, `Echo`): blocklist with caution — same false-positive risk as bare prefixes. If the name is a common English word, reviewer discipline may be a better tier than mechanical match.

**Multi-word forms** reduce false-positive risk. If your project is referred to as "AC platform" internally and `AC` alone would false-positive everywhere, blocklist `AC platform` instead — the multi-word phrase is much more selective.

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

## Machine-specific overrides

Machine-local Claude Code permissions belong in `~/.claude/settings.local.json` (not tracked).
