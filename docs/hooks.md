# Hook reference

Full descriptions for every hook in `claude/.claude/hooks/`. For the gate summary (which hook blocks which command), see the Hook → Gates → Cleared-by table in the [README](../README.md#workflow).

## Gate hooks

- **`require-code-review.sh`** — blocks `git commit` (including chained forms like `git add . && git commit`) until `/code-review` has run on the current staged state. Verified via per-session sha256 marker in `~/.claude/review-markers/<repo-hash>.<session-id>`, which auto-invalidates the moment the staging area changes. Per-session keying prevents two parallel sessions in the same worktree from overwriting each other's markers when staging different diffs.
- **`require-skill-review.sh`** — blocks `git commit` only when staged changes include a `SKILL.md`. Requires `/skill-review` to have produced a behavioral-equivalence audit for any removed or shortened lines. Marker is keyed to the SKILL.md-scoped diff (not the full staged diff), so re-staging non-skill files after a clean review does not invalidate the marker.
- **`deny-private-project-refs.sh`** — blocks `git commit`, `gh pr create`, `gh pr edit`, and mutating `gh api` calls when the staged diff, commit message, or PR title/body/body-source-file contains either (a) tracker-ID tokens (`[A-Z]{2,}-\d+`) outside an OSS-prefix allowlist (`CVE-`, `RFC-`, `GH-`, and similar — see the script for the full list), or (b) a literal substring match against entries in the user's opt-in `~/.claude/private-projects.md` blocklist. Enforces the mechanical categories of the repo-root `CLAUDE.md` redaction rule; structural fingerprints still require review discipline. See [`docs/private-project-redaction.md`](private-project-redaction.md).
- **`deny-pii-in-commits.sh`** — opt-in. Blocks `git commit` (every repo, including chained forms) when the added lines of the staged diff, the commit message, or a referenced `-F`/`--file` message file contain personally-identifying or protected health information. Dormant unless `~/.claude/pii-patterns.md` exists as a readable regular file. Scans built-in generic patterns (US SSN, Luhn-checked credit-card numbers) plus every user `<label>: <regex>` line in that file; `exclude: <glob>` lines drop synthetic-fixture paths from the scan. When `-a`/`--all`, a `--` pathspec, or a bare pathspec argument is present, also scans `git diff HEAD` (working-tree content committed outside the index). Robust against `--no-verify`. The deny message names the matched pattern by label only — never the matched value or the user regex. See [`docs/security-hardening.md`](security-hardening.md).
- **`deny-data-file-reads.sh`** — opt-in. PreToolUse `Read` gate that refuses data-shaped files before their content enters Claude's context. Dormant unless `~/.claude/data-file-read-guard.md` exists as a readable regular file. Once armed, denies `Read` of a data-file extension (`.csv`, `.parquet`, `.sqlite`, `.xlsx`, and similar), a path under a `Downloads/` directory, a file over 5 MB, or any path matching a glob line in the config file. No bypass valve. See [`docs/security-hardening.md`](security-hardening.md).
- **`deny-escaped-backticks-in-pr-body.sh`** — blocks `gh pr create` and `gh pr edit` when the PR body (inline `--body` or any body-source file passed via `--body-file`/`-F`/`--template`/`-T`) contains literal backslash-backtick sequences (`\``). Those sequences appear when a `<<'EOF'` single-quoted heredoc writer unnecessarily escapes a backtick — the backslash survives into the rendered GitHub body and breaks code-span formatting. Fails closed when a body-source file is unreadable or a pseudo-file path (`-`, `/dev/stdin`, `/dev/fd/*`).
- **`require-stow-reminder.sh`** — scoped to the `claude-config` repo. Blocks `gh pr create` and `gh pr edit` (when the edit changes the body) if the PR adds a new immediate child of `claude/.claude/` (file or directory) and neither the inline command, a referenced `--body-file`/`--template`, nor any `--fill`-sourced commit message mentions `install.sh` or `stow` (case-insensitive). Reason: GNU Stow links each top-level child of `claude/.claude/` individually, so a brand-new child only appears in `~/.claude/` after re-running `install.sh` — `git pull` alone won't materialize the symlink. The reminder lands in the PR body so the post-merge stow step doesn't get forgotten at merge time.
- **`require-respond-pr.sh`** — blocks PR comment reads and posts (`gh api .../pulls|issues/N/{comments,reviews}`, `gh pr comment`, `gh pr review`) and redirects to `/respond-pr`, so all three comment types get fetched and replies carry the `[Claude Code]` attribution prefix. Honors a per-session bypass marker at `~/.claude/.respond-pr-active.d/<session_id>` that the skill sets on entry and removes on exit; the hook checks `kill -0` against the stored PID on each gate hit; a dead PID evicts the orphaned marker automatically. Per-session keying (rather than a singleton path) keeps parallel Claude sessions from thrashing on cleanup or leaking bypass to unrelated sessions.
- **`require-ready-for-review.sh`** — gates `git push` and `gh pr ready` on branches with an open PR. Requires `/ready-for-review` to have run and passed since the last commit. Verified via a per-session marker keyed by HEAD SHA — a new commit invalidates the marker automatically and forces a re-run. An active-skill bypass marker (`~/.claude/.ready-for-review-active.d/<session_id>`) prevents the skill's own iteration pushes (fix → push → loop) from self-denying.
- **`require-worktree-for-git-writes.sh`** — opt-in per repo. When active, denies non-read-only git operations unless the session runs in a linked git worktree. Prevents concurrent Claude Code sessions from racing on the same working tree. See [Worktree enforcement](../README.md#worktree-enforcement).
- **`require-worktree-for-file-writes.sh`** — opt-in per repo (same `.claude/worktree-required` sentinel). When active, denies `Edit`/`Write`/`MultiEdit` on paths in the main working tree; all file edits must land in a linked worktree.
- **`guard-settings-model-effort.sh`** — blocks `git commit` when `claude/.claude/settings.json` has staged changes to `model` or `effortLevel`. Per-session overrides should not be committed; use `ANTHROPIC_MODEL`/`CLAUDE_CODE_EFFORT_LEVEL` env vars or `/effort max` mid-session instead.
- **`check-skill-length.sh`** — blocks `git commit` when a staged `SKILL.md` exceeds 200 lines and grew vs HEAD. Nudges toward splitting a growing skill or moving reference material to a co-located `REFERENCES.md`.
- **`require-memory-skill.sh`** — PreToolUse `Edit|Write|MultiEdit` gate on `~/.claude/projects/*/memory/MEMORY.md` and new memory topic files under that path. Requires an active `/ai-instruction-and-memory-files` per-session marker before allowing writes, ensuring index format and frontmatter rules are applied.
- **`require-routing-read.sh`** — PreToolUse `Agent` gate active during a running `/plan-review` session. Denies subagent spawn until `~/.claude/skills/plan-review/ROUTING.md` has been read (tracked by `log-routing-read.sh`), ensuring reviewer routing consults the full ownership table before any specialist is spawned.

## Utility hooks

- **`capture-session-id.sh`** (SessionStart, SubagentStart) — at session and subagent start, writes the session's `session_id` to `~/.claude/sessions/<claude-pid>` so skills running as Bash tool calls (which don't see the hook payload) can look up their own session id via the bash tool's `$PPID`. Used by both `/respond-pr` and `/code-review` to compute per-session marker filenames. The hook also self-prunes superseded and orphaned bare-PID files from `~/.claude/sessions/`, throttled to once per 24 hours via `~/.claude/.sessions-gc-stamp`.
- **`ask-review-permissions.sh`** — asks before `Edit`/`Write`/`MultiEdit` to `.claude/settings*.json`, nudging toward `/review-permissions` when the edit touches `permissions.allow`.
- **`session-marker-dashboard.sh`** (SessionStart) — at session start, emits a summary of any active bypass markers (`/respond-pr`, `/ready-for-review`) into the resumed session's context so stale bypasses are visible.
- **`enforce-marker-script-shape.sh`** — PreToolUse `Bash` gate that denies any `marker.sh` invocation not exactly matching the allowlisted shape pattern (`~/.claude/scripts/marker.sh <subcommand> <skill>`). Blocks chains (`&&`, `;`), redirects, env-var prefixes, and extra arguments. Defense-in-depth against prompt-injection escalation through the `marker.sh` allow rules.
- **`deny-env-reads.sh`** — PreToolUse `Read` gate that allows `.env.example`, `.env.template`, and `.env.sample` (including symlink-resolved targets) while denying all other `.env`/`.env.*` paths. Extends the static `permissions.deny` entries documented in [`docs/auto-mode.md`](auto-mode.md) to cover `.env.*` variants not listed there.
- **`log-routing-read.sh`** — PostToolUse `Read` companion to `require-routing-read.sh`. Writes a per-session routing-read marker when `~/.claude/skills/plan-review/ROUTING.md` is read during an active `/plan-review` session. The only PostToolUse hook in this repo.

## Gate deadlock recovery

Active-bypass markers from sessions that crash before cleanup evict themselves automatically: the hooks check `kill -0` against the stored PID and evict dead entries on the next gate hit. The remaining case is a *live session* whose review skill cannot execute — harness-blocked, failing to load, or unable to produce a completion marker due to a tool error.

For three gates, the gating condition is removable via Bash without destroying work:

**`require-plan-review.sh`** blocks Write/Edit when an uncommitted or modified plan file exists in `.claude/plans/` with no completion marker for this session. Committed, unmodified plan files are treated as historical and do not arm the gate.

```bash
# Option 1: remove the plan file to clear the gate. Copy the content first
# if you need to preserve it. If multiple uncommitted plan files exist, all
# must be removed or committed to clear the gate.
rm .claude/plans/<slug>.md

# Option 2: commit the plan file. A committed, unmodified plan is treated
# as historical and does not arm the gate.
git add .claude/plans/<slug>.md && git commit -m "plan: <topic>"
```

**`require-code-review.sh`** blocks `git commit` when staged changes have not been reviewed in this session:

```bash
# Unstage the files. Changes remain in your working tree; re-stage and
# run /code-review once the skill is available.
git reset HEAD -- <files>
```

Alternatively, commit directly from a terminal outside Claude Code — Claude Code PreToolUse hooks only fire for `Bash` tool calls within a session.

**`require-skill-review.sh`** blocks `git commit` when a staged `SKILL.md` has not been audited in this session:

```bash
git reset HEAD -- <path-to-SKILL.md>
```

Same terminal-outside-Claude-Code fallback applies.

### Gates with no clean escape

Three skill gates have no safe condition-removal path — the gate's intent is to force the skill:

- **`require-ready-for-review.sh`**: escape paths (close the PR, force-push, hard-reset) are destructive. If the skill cannot run in the current session, spawn a subagent.
- **`require-respond-pr.sh`**: the gate ensures the skill fetches all review-comment types before any `gh` PR comment operation. There is no condition to remove short of closing the PR.
- **`require-memory-skill.sh`**: the gate is path-based (fires on writes to memory files). Writing elsewhere defeats the user's intent. Run the skill in a subagent.

### Orphaned active-bypass markers

If a skill's active-bypass gate refuses to release after the skill has finished, use the manual sweep utility:

```bash
~/.claude/scripts/marker.sh clear-stale          # evict dead-PID entries
~/.claude/scripts/marker.sh clear-stale --dry-run # report without removing
```

`clear-stale` operates only on active-bypass marker directories (`.*-active.d/`); it does not touch `~/.claude/sessions/`, which is GC'd automatically by `capture-session-id.sh`.
