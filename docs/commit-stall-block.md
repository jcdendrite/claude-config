# Commit Stall Block Hook

## Activation

The hook is on by default for every stow consumer, in any repo the root guard accepts. Turn it off for the config dir the session resolves (one per account) with `commit_stall_block = false` (see [How to disable](#how-to-disable)). No per-repo exemption exists.

Under a relative or nonexistent `CLAUDE_CONFIG_DIR` the hook is silently inert and writes no log line.

## What the hook does

`advance-past-commit-stall.sh` is a `Stop` hook (matcher-less — `Stop` has no matcher support) that fires at the end of a turn. It emits `{"decision": "block", "reason": "..."}` — which forces the same turn to continue rather than end — only when every one of these holds:

- No kill switch is set (see [How to disable](#how-to-disable)).
- The session is the one the engineer is talking to, not a dispatched subagent (`agent_type` is empty).
- `session_id` and `prompt_id` are present and well-formed.
- `permission_mode` is not `plan`.
- `prompt_id` differs from the last one this hook fired on for this session — at most one forced continuation per user turn.
- The **final sentence** of the last assistant message (the last sentence of its last non-blank line) asks permission to commit, push, or open a PR (case-insensitive whole-word match: "want me to", "should I", "shall I", and similar, paired with commit/push/open a PR, where inflections such as "committing" and "pushed" count), and that sentence does not also carry an exclusion phrase that begins at a word boundary (`merge`, `--force`, `failing`, `blocked`, and similar).
- The repo comes from the payload's `.cwd` through `_lib_autonomous_shipping_root_for_cwd` in `_lib.sh`, which defines which roots are eligible. A `.cwd` containing a newline counts as absent, so the hook stays silent. A newline in `session_id`, `agent_type`, `permission_mode` or `prompt_id` also leaves the hook silent.
- Work is actually pending: a dirty tree, HEAD ahead of its configured upstream, or any failed upstream comparison. A failed comparison counts as pending when there is no upstream configured (the common state before a branch's first push), when the configured upstream's remote-tracking ref is gone (after a merge plus `fetch --prune`), and when the capped `git rev-list` call is killed.
- HEAD is on a non-default branch, per `_lib_head_detached_or_default` in `_lib.sh`. The default branch and a detached HEAD stay silent, because the forced continuation would ship from them. A failed HEAD read also stays silent.

The `reason` text tells the agent not to ask permission to continue at a natural stopping point, unless CLAUDE.md, the skill the agent is following, or a hold the engineer stated requires the question. When one does, the reason tells the agent to end its turn. Otherwise it tells the agent to continue with the task's next step, say the task is complete, or say what is blocked. It ends with the disable command. `claude/.claude/CLAUDE.md`'s Shipping bullet carries the push scope, the staging limit and the in-session hold, and Agent Core carries the rule that a fork or subagent returns its work.

The reason leaves two gaps:

- **No fork limit.** The hook skips a payload carrying `agent_type`. Whether a fork's Stop payload carries `agent_type` is unverified, and a fork whose payload lacks it receives the reason with only Agent Core's rule to stop it.
- **Neither direction is measured.** No test or eval shows that the reason moves a stalled agent forward, or that an agent with a stated hold, a force-push question or a CLAUDE.md-required confirmation still asks instead of continuing. Tests pin only the carve-out's wording.

## How to disable

**Mid-session:** press `Esc` to interrupt the current turn before the hook fires, or set `commit_stall_block = false` via the `!` shell escape — this kill switch needs no repo change and is honored except under the degraded-schema limitation below:

```bash
! printf 'commit_stall_block = false\n' >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/claude-config.toml"
```

Set it back to `true` to re-enable — see [`docs/config-file.md`](config-file.md) for the file's hand-edit contract:

```bash
! printf 'commit_stall_block = true\n' >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/claude-config.toml"
```

The `commit_stall_block` switch and `.commit-stall-block-disabled` silence only this Stop hook, for the config dir the session resolves (one per account). The legacy file counts only while `claude-config.toml` has no `commit_stall_block` row, so on a machine whose config file has the row, touching the file changes nothing. No setting turns off CLAUDE.md's Shipping rule, which keeps telling the agent not to pause.

## Fire predicate and the exclusion-window tradeoff

The fire and exclusion regexes both scan only the **final sentence** of the last assistant message, not the whole message. This is a deliberate, accepted tradeoff, not an oversight:

- If the exclusion regex scanned the whole message, "Fixed the failing test in the parser. Want me to commit this…?" — the modal shape after a routine bug-fix task — would never fire, because "failing" appears in an earlier sentence. That's the primary case this hook exists to catch; over-suppressing it defeats the fix.
- Scoped to the final sentence, an earlier-sentence failure signal can be missed instead — but the loop guard caps forced retries at one per `prompt_id`, so the worst case is one wasted retry, not a runaway loop.
- A line break ends a sentence. The window is the last sentence of the last non-blank line, so a message that is a heading, bullets, then a closing question is judged on the question alone.
- Only the last 2048 characters of the message are scanned (`ASK_WINDOW_CHARS` in the hook), because bash pattern removal over a long last line is expected to be slow, though that was not measured. A last sentence longer than that is judged on its final 2048 characters, so an exclusion word earlier in it is not seen.

## Log location

The hook appends one line per significant event to `<config-dir>/.commit-stall-block.log` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`):

| Line prefix | Meaning |
|---|---|
| `fired session=<id> prompt=<id>` | The hook blocked the turn from ending |
| `phrasing-drift session=<id> prompt=<id>` | The final sentence matched the permission-verb half of the fire pattern (`want me to`, `should I`, etc.) but not the object half (`commit`, `push`, `open a PR`) — the only signal that catches a future rewording of the stall without logging on nearly every non-matching turn |
| `schema-drift session=<id> field=prompt_id` | `prompt_id` was absent from the Stop payload |
| `root-denied session=<id> prompt=<id>` | The final sentence matched the fire pattern with no exclusion phrase, but `_lib_autonomous_shipping_root_for_cwd` rejected the payload's `.cwd`, so the hook stayed silent |

A decline at the HEAD check (default branch, detached HEAD, or unreadable HEAD) writes no log line. Every consumer with the hook on appends `phrasing-drift` lines, because the permission-verb half of the pattern also matches closers such as "let me know if". The log is append-only and not rotated automatically. Trim it periodically if disk space is a concern: `> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.commit-stall-block.log"`.

## Known limitations

- **Silent (non-question) stops are not caught.** The predicate requires a question construction in the final sentence. A session that simply states "Per your standing instruction, I haven't committed" and ends the turn without asking anything is not detected — the always-loaded `## Shipping` section in `CLAUDE.md` and the statement `restore-authorization-boundary-on-compact.sh` restates after compaction are the only defenses against that shape, and both are context, not a gate.
- **The `--dry-run`/default-branch bypass residuals in `require-ready-for-review.sh` are inherited, not fixed, by this hook.** `git push --dry-run && gh pr create` still bypasses the pre-handoff review gate entirely — pre-existing in code this hook doesn't touch (the identical shape already bypasses a second real `git push` chained the same way). See that hook's own header comment for the full residual list; this Stop hook doesn't gate a git operation itself, so it can't close that gap.
- **State files persist until the next fire, not until session teardown.** The hook sweeps entries older than 30 days from its own state dir each time it fires — interactive session or `claude -p` one-shot alike — so a state file from one session lingers until the next qualifying fire anywhere sweeps the directory, not indefinitely.
- **The git calls this hook makes (repo-root resolve, `git status --porcelain`, the upstream-ahead check with its HEAD fallback, and the HEAD and default-branch reads behind `_lib_head_detached_or_default`) are unbounded on stock macOS.** `_lib_capped`'s `timeout` wrapper is a no-op when GNU coreutils isn't installed, so a wedged git call (a stale NFS mount, a held index lock) hangs the hook rather than failing closed to a decision. This path runs for every stow consumer with `commit_stall_block` on, on turns whose final sentence matches the fire pattern. The Stop registration in `claude/.claude/settings.json` sets no `timeout`, so a wedged call holds the turn boundary for up to the harness's default hook timeout of 600 s (hooks reference, "Common fields" table, `timeout` row: https://code.claude.com/docs/en/hooks, fetched 2026-10-17). Every other hot-path hook in this repo shares the same limitation; `install.sh` warns about the missing `timeout`/`gtimeout` binary at onboarding time.
- **`commit_stall_block = false` is not honored while the schema is degraded.** When `config-keys.psv` is unreadable (`_config_enabled` exit 3) or lacks `commit_stall_block`'s row (exit 4), the key stays armed by design: the hook still fires with `commit_stall_block = false` in `claude-config.toml`, and the legacy `.commit-stall-block-disabled` file is not honored either, because its name comes from the schema. The exit-3 and exit-4 case tests in `test_advance_past_commit_stall.py` pin only the `commit_stall_block = false` half; no test pins the legacy file's half.
- **The fire predicate does not read what the question targets.** From a feature branch with work pending, a compound question such as "Want me to commit this and deploy it?" and one naming the default branch such as "Want me to commit this and push it to main?" both fire. The reason's only instruction routing such a question back is its carve-out, which defers to the Shipping bullet's scope sentence and to `claude/.claude/CLAUDE.md`'s Engineering Judgment bullet on confirming an action beyond the immediate change. Whether the agent follows it is not measured.
- **Two force-push spellings are not excluded.** The exclusion regex holds `--force` and `force-push` only, so a final sentence that spells a force push as `force push` or `-f` ("Want me to force push this branch?", "Want me to push this with -f?") still fires from a feature branch with work pending, and falls under the previous bullet's carve-out.
- **The predicate matches whole words, so a word in an unrelated sense still counts.**
  - "push back" and "push notification" fire ("Should I push back on the spec change?"). A hyphen is a word boundary, so hyphenated compounds such as "pre-commit" and "push-notification" fire too. For a fired question the reason's carve-out is the only routing back, and whether the agent follows it is not measured.
  - "Want me to commit the error handling changes?" is silenced through "error".
  - An exclusion phrase is bounded only at its start, so a longer word that begins with one still excludes ("merged", "errors"), and a word that merely contains one no longer does ("emergency", "unblocked", "parse_error"). A hyphen is a word boundary on this side too, so hyphenated compounds such as "pre-merge" and "non-failing" still exclude. An exclusion spelling such as "merging" is not matched.
  - A prefixed object does not match, so "Should I recommit this?" and "Want me to reopen the PR?" are not blocked. They may log `phrasing-drift`, because the verb half still matches.
  - An underscore joins a word, so `commit_hook` does not count and an underscore-wrapped question (`_Want me to commit this?_`) is not blocked. Such a question logs no `phrasing-drift` line either, because the verb half fails too. An underscore-wrapped single word such as `_commit_` or `_error_` does not match either: "Should I _commit_ this?" is not blocked and logs `phrasing-drift` because the verb half still matches, and "the _error_ path" no longer excludes.
  - A dot between the permission verb and the object ends the match, so "Want me to update README.md and commit?" is not blocked. It logs `phrasing-drift` because the verb half still matches.
  - Word boundaries follow the process locale, so a letter outside ASCII counts as part of a word under a UTF-8 locale.
  - Over-exclusion leaves the agent's own question standing for the human, so it fails toward asking.
- **The fire predicate reads neither whether the engineer asked for the change nor who owns `origin`.** From a feature branch with work pending, "Want me to commit these experiments?" after an analysis-only request, or "Should I open a PR?" in a clone of a repository the engineer does not own, fires and is force-continued once per prompt. The reason's carve-out is the only routing back.
- **Per-Stop cost of the no-fire path.** Median 76ms over 40 runs of `advance-past-commit-stall.sh` on a final sentence that matches neither the fire nor the permission-verb pattern (fresh empty config dir, load average about 10), against the <100ms per-invocation hook budget in [`config-file.md`](config-file.md). The machine was not idle, so the figure is not the idle-machine baseline the 40-run method assumes. Unreproduced: the measuring command is not recorded. The 76ms figure is a one-off measurement, not a baseline to compare later changes against. The hook spawns `jq` under `timeout`, which is two execs where GNU `timeout` or `gtimeout` is on PATH. A control run with a verb-only closing sentence appended the `phrasing-drift` line, confirming the measured path reaches the regex step. Not measured: the block-emitting path (root-guard resolve, HEAD read and up to three git calls, state write, second `jq` encode) and a populated `claude-config.toml`, so this does not show the hook as a whole meets the budget.
