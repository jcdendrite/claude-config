# Script reference

Full descriptions for utility scripts in `claude/.claude/scripts/` (stowed to `~/.claude/scripts/`). User-facing scripts are installed as wrappers under `~/.local/bin/` — see [README PATH setup](../README.md#requirements).

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

- **`transcript-analysis.py`** — analysis toolkit for Claude Code transcripts (`~/.claude/projects/*/*.jsonl`); local-only reads except `pr-link`, which calls `gh`. Invoked directly: `python3 ~/.claude/scripts/transcript-analysis.py <subcommand>`. See [`docs/transcript-analysis.md`](transcript-analysis.md) for per-subcommand reference; the `/transcript-analysis` skill documents which subcommand answers which question.

- **`marker.sh`** — write and remove review markers on behalf of workflow skills. `/code-review`, `/skill-review`, `/plan-review`, `/ready-for-review`, `/respond-pr`, and `/ai-instruction-and-memory-files` write or activate markers via `~/.claude/scripts/marker.sh`. The 12 valid invocation shapes are allowlisted in `settings.json` for silent auto-approval; shape validation is enforced by `enforce-marker-script-shape.sh` (see [`docs/hooks.md`](hooks.md)).

- **`cleanup-merged-branches.sh`** — discovers local branches that are safe to delete and cleans them up: removes the worktree, force-deletes the local branch, prunes the remote tracking ref, deletes the remote branch if not auto-deleted, and fast-forwards the default branch. Two signals are used: `gh pr list --head <branch> --state merged` (confirmed merged PR for this exact branch name), and `git merge-base --is-ancestor` to catch branches whose commits are reachable from `origin/<default>` even when the PR shipped under a different head name (renamed branch, worktree-prefixed name, etc.). Branches confirmed via gh are deleted without prompting; branches detected only via reachability prompt for confirmation. `--yes` skips the prompt and auto-confirms reachability-only branches — required when invoking from a non-interactive shell (including the Claude Code Bash tool, which does not allocate a TTY) — without it, probable-merge branches are skipped with a warning. Auto-approved by the paired `permissions.allow` entries. Before removing a worktree, the script skips any that a live process is working inside — it checks process working directories via `/proc` (or `lsof` where there is no `/proc`) — so a session active in a worktree is not deleted out from under it. That check sees a process's cwd only: it does not catch a worktree held open indirectly (e.g. a container bind-mounted in, whose owning session has already exited) or by another user's process, so still prefer to run cleanup when other Claude Code sessions are idle.

  ```bash
  cleanup-merged-branches          # run cleanup (prompts for reachability-only branches)
  cleanup-merged-branches --yes    # run cleanup, auto-confirm all candidates
  cleanup-merged-branches --dry-run  # preview without acting
  ```

- **`update-claude-config-plugins.sh`** — checks which `@claude-config` marketplace plugins installed in the current project are behind the marketplace's latest version, and interactively offers to update each one. Refreshes the marketplace first (`claude plugin marketplace update`) so the diff is against the live catalog. Scoped to `@claude-config` plugins only: they carry real semver in `plugin.json` (enforced by the `plugin-semver` plugin), making version comparison clean. Run from the consumer repo's root; project-scope entries from other repos are excluded.

  ```bash
  update-claude-config-plugins             # interactive update
  update-claude-config-plugins --yes       # update all outdated plugins without prompting
  update-claude-config-plugins --dry-run   # preview outdated plugins without updating
  ```

- **`resume-context.sh`** — resumes a `/handoff` or `/brief` continuity file: moves it from `~/.claude/handoffs/` or `~/.claude/briefs/` to a fresh `mktemp`-created path under `${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}` (permissions re-asserted to `600` via an explicit `chmod` after the move — a same-filesystem `mv` inherits the source file's permissions via `rename(2)`, not `mktemp`'s own mode, so the `chmod` is what actually enforces owner-only), then launches a new interactive `claude` session with the moved file loaded via `--append-system-prompt-file`. Refuses to move a symlink source: `mv` preserves a symlink's identity on a same-filesystem rename, and `chmod` dereferences symlinks by default, so chmodding a moved symlink would silently narrow permissions on whatever arbitrary file it points to. `--consume-only` performs the move only, without launching anything — used by `consume-durable-continuity-file-on-read.sh` (see [`docs/hooks.md`](hooks.md)) to consume a continuity file read directly in the same session (e.g. after `/clear`), without going through this script's launch path. Two env-var seams exist: `RESUME_CONTEXT_LAUNCHER` (command to exec instead of `claude`) is used by tests to avoid the real `claude` binary, and doubles as a production seam for fronting `claude` with a wrapper — e.g. `RESUME_CONTEXT_LAUNCHER=claude-auto` resumes in auto mode; `RESUME_CONTEXT_TMPDIR` (temp-dir root override) is tests-only, never touching the real shared `/tmp` otherwise. No kill-switch of its own — the kill-switch lives in the consuming hook, since this script only ever runs when explicitly invoked.

  ```bash
  resume-context ~/.claude/handoffs/<slug>-handoff.md   # move + launch
  resume-context --consume-only <path>                  # move only, no launch
  ```
