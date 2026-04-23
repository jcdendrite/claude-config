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
- **`require-respond-pr.sh`** — blocks PR comment reads and posts (`gh api .../pulls|issues/N/{comments,reviews}`, `gh pr comment`, `gh pr review`) and redirects to `/respond-pr`, so all three comment types get fetched and replies carry the `[Claude Code]` attribution prefix. Honors a 60-minute bypass marker at `~/.claude/.respond-pr-active` that the skill sets on entry and removes on exit.
- **`ask-review-permissions.sh`** — asks before `Edit`/`Write`/`MultiEdit` to `.claude/settings*.json`, nudging toward `/review-permissions` when the edit touches `permissions.allow`.
- **`require-worktree-for-git-writes.sh`** — opt-in per repo. When active, denies non-read-only git operations unless the session runs in a linked git worktree. Prevents concurrent Claude Code sessions from racing on the same working tree. See [Worktree enforcement](#worktree-enforcement) below for opt-in instructions.

### Skills (slash commands)

- **`/code-review`** — principal engineer code review checklist with ripple-effect triage and domain-specific audits (backend, frontend, security, infrastructure, data).
- **`/respond-pr`** — fetch and address PR review comments, with `[Claude Code]` attribution on all replies.
- **`/review-permissions`** — security audit of `permissions.allow` rules with a 21-item checklist.
- **`/plan-review`** — review implementation plans before presenting, with domain-specific reviewer roles.
- **`/test-conventions`**, **`/test-evaluation`** — test authoring and audit guidance.
- **`/read-docx-comments`** — extract comments from `.docx` files with anchored text context.

### Other

- **`CLAUDE.md`** — baseline engineering instructions (judgment heuristics, working style, safety rules).
- **`settings.json`** — global settings wiring up the hooks and statusline. Session-only overrides (model, effortLevel) are intentionally not tracked — use the `ANTHROPIC_MODEL` and `CLAUDE_CODE_EFFORT_LEVEL` env vars, or `/effort max` mid-session.
- **`statusline-command.sh`** — status bar showing model, context usage, session cost, working directory, and git branch.

## Worktree enforcement

Concurrent Claude Code sessions that share a working tree can race: one session's `git reset --hard`, `git stash`, or `git checkout` silently wipes another session's uncommitted edits. See [Claude Code issue #34327](https://github.com/anthropics/claude-code/issues/34327) for examples of this failure mode in the wild.

`require-worktree-for-git-writes.sh` mitigates by denying non-read-only git operations (`commit`, `push`, `rebase`, `reset`, `merge`, `checkout`, etc.) unless the session runs inside a linked git worktree. Read-only commands (`status`, `log`, `diff`, `fetch`, `show`, `blame`, etc.) are always allowed. The hook is opt-in per repo via a committed sentinel file.

### Activating enforcement on a repo

```bash
cd path/to/your/repo

mkdir -p .claude
cat > .claude/worktree-required <<'EOF'
# Claude Code worktree enforcement marker.
# Presence of this file activates ~/.claude/hooks/require-worktree-for-git-writes.sh.
# See https://github.com/jcdendrite/claude-config for details.
EOF

echo '.claude/worktrees/' >> .gitignore

git add .claude/worktree-required .gitignore
git commit -m "Activate Claude Code worktree enforcement"
```

### Working inside a worktree

With enforcement active, start sessions for non-trivial work in a worktree instead of the main tree:

```bash
git worktree add .claude/worktrees/my-feature -b my-feature
cd .claude/worktrees/my-feature
# work happens here; git commit/push/etc. pass through the hook cleanly
```

Agents spawned with `isolation: worktree` create their own worktrees under `.claude/worktrees/` automatically.

To opt out, delete `.claude/worktree-required`.

## Tests

Pytest suite for the hooks (covering allow, deny, and ask paths):

```bash
pytest claude/.claude/hooks/tests/
```

CI runs this on every PR and main push via `.github/workflows/hooks.yml`.

## Machine-specific overrides

Machine-local Claude Code permissions belong in `~/.claude/settings.local.json` (not tracked).
