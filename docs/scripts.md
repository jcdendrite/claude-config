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

- **`transcript-analysis.py`** — analysis toolkit for Claude Code transcripts (`~/.claude/projects/*/*.jsonl`). Eleven subcommands: `buckets` (assistant turns by branch × model family), `fail-seq` (ordered test-failure-count sequence per branch — convergence vs thrashing), `struggle` (correction/frustration phrase frequency split by model), `duration` (active vs idle-gap time per branch), `subagents` (`isSidechain` vs main-thread turn split), `subagent-mix` (per-branch subagent/Task spawn types and review-skill spawn counts), `skill-pair` (leader→follower skill-invocation pairing rate, binned by ISO week), `commit-gate` (review-skill vs git-commit ordering per ISO week, with `--no-verify` detection), `pr-link` (map branches to GitHub PRs and count per-author comments), `review-trace` (per-session timeline of review-skill invocations, hook denials, and reviewer-agent spawns), `audit-routing` (per-turn Opus token-class breakdown with `--redact` for public reporting). All subcommands are local-only reads except `pr-link`, which calls `gh`. Invoked directly from the shell (no `~/.local/bin/` wrapper); the `/transcript-analysis` skill documents which subcommand answers which question.

  ```bash
  python3 ~/.claude/scripts/transcript-analysis.py buckets
  python3 ~/.claude/scripts/transcript-analysis.py fail-seq --branches <branch>
  python3 ~/.claude/scripts/transcript-analysis.py review-trace --deny-only
  python3 ~/.claude/scripts/transcript-analysis.py pr-link --repo owner/repo --branches <branch>
  ```

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
