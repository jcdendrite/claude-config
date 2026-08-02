# Commit Stall Block Hook

## Activation

```bash
touch ~/.claude/autonomous-shipping-required
```

`./install.sh` now offers this interactively on every run (see [`install.sh` machine-level opt-ins](../README.md#autonomous-shipping)) — the command above is the non-interactive/scripted alternative. A repo cannot grant this by committing anything; only this machine-level file can. To exempt one repo while the machine-level sentinel stays on: `mkdir -p .claude && touch .claude/autonomous-shipping-optout`.

## What the hook does

`advance-past-commit-stall.sh` is a `Stop` hook (matcher-less — `Stop` has no matcher support) that fires at the end of a turn. It emits `{"decision": "block", "reason": "..."}` — which forces the same turn to continue rather than end — only when every one of these holds:

- No kill switch is set (see [How to disable](#how-to-disable)).
- The session is the one the engineer is talking to, not a dispatched subagent (`agent_type` is empty).
- `session_id` and `prompt_id` are present and well-formed.
- `permission_mode` is not `plan`.
- `prompt_id` differs from the last one this hook fired on for this session — at most one forced continuation per user turn.
- The **final sentence** of the last assistant message asks permission to commit, push, or open a PR (case-insensitive phrasing match: "want me to", "should I", "shall I", and similar, paired with commit/push/open a PR), and that sentence does not also carry an exclusion phrase (`merge`, `--force`, `failing`, `blocked`, and similar).
- `_lib_autonomous_shipping_active` holds for the resolved repo — the machine-level sentinel above is set, and the repo carries no `.claude/autonomous-shipping-optout`.
- Work is actually pending: a dirty tree, HEAD ahead of its configured upstream, or (the common state before a branch's first push) no upstream configured at all.

The `reason` text names the next step explicitly: `/code-review` → commit (path-scoped staging, never stage-all) → `/ready-for-review` → PR-open, stopping before merge — merge stays human-only regardless of this setting.

## How to disable

**Mid-session:** press `Esc` to interrupt the current turn before the hook fires, or run `touch ~/.claude/.commit-stall-block-disabled` via the `!` shell escape — this kill switch is always effective regardless of the sentinel state and needs no repo change:

```bash
! touch ~/.claude/.commit-stall-block-disabled
```

Remove it to re-enable:

```bash
! rm ~/.claude/.commit-stall-block-disabled
```

**Per repo, while the machine-level sentinel stays on:**

```bash
mkdir -p .claude && touch .claude/autonomous-shipping-optout
```

**Everywhere:** remove the machine-level sentinel itself (`rm ~/.claude/autonomous-shipping-required`).

## Fire predicate and the exclusion-window tradeoff

The fire and exclusion regexes both scan only the **final sentence** of the last assistant message, not the whole message. This is a deliberate, accepted tradeoff, not an oversight:

- If the exclusion regex scanned the whole message, "Fixed the failing test in the parser. Want me to commit this, or do you want to review the diff first?" — the modal shape after a routine bug-fix task, and the case closest to the issue's own quoted examples — would never fire, because "failing" appears in an earlier sentence. That's the primary case this hook exists to catch; over-suppressing it defeats the fix.
- With the exclusion window scoped to the final sentence, a failure signal in an *earlier* sentence is missed: "The push failed with a non-fast-forward error. Want me to push again after rebasing?" fires when it arguably shouldn't. The cost is bounded, not open-ended: the loop guard fires at most once per `prompt_id`, so the agent retries the already-failing operation exactly once, the retry fails again for the same underlying reason, and the *next* `Stop` event for the same `prompt_id` is not re-blocked — it ends the turn normally, reporting the failure. One wasted tool-call cycle, not a runaway loop and not a silently swallowed error.

## Log location

The hook appends one line per significant event to `~/.claude/.commit-stall-block.log`:

| Line prefix | Meaning |
|---|---|
| `fired session=<id> prompt=<id>` | The hook blocked the turn from ending |
| `phrasing-drift session=<id> prompt=<id>` | The final sentence matched the permission-verb half of the fire pattern (`want me to`, `should I`, etc.) but not the object half (`commit`, `push`, `open a PR`) — the only signal that catches a future rewording of the stall without logging on nearly every non-matching turn |
| `schema-drift session=<id> field=prompt_id` | `prompt_id` was absent from the Stop payload |

The log is append-only and not rotated automatically. Trim it periodically if disk space is a concern: `> ~/.claude/.commit-stall-block.log`.

## Known limitations

- **Silent (non-question) stops are not caught.** The predicate requires a question construction in the final sentence. A session that simply states "Per your standing instruction, I haven't committed" and ends the turn without asking anything is not detected — the always-loaded `## Shipping` section in `CLAUDE.md` is the only defense against that shape, and it's prose, not a hook.
- **The `--dry-run`/default-branch bypass residuals in `require-ready-for-review.sh` are inherited, not fixed, by this hook.** `git push --dry-run && gh pr create` still bypasses the pre-handoff review gate entirely — pre-existing in code this hook doesn't touch (the identical shape already bypasses a second real `git push` chained the same way). See that hook's own header comment for the full residual list; this Stop hook doesn't gate a git operation itself, so it can't close that gap.
- **`claude -p` one-shot runs leak state files at one-shot rate.** One-shot invocations don't fire `SessionEnd`, so the per-session state file at `~/.claude/.commit-stall-block.d/<session_id>` isn't cleaned up automatically. Harmless — each file only affects the session whose `session_id` it carries — but `cleanup-commit-stall-marker.sh` (the `SessionEnd` destructor for interactive sessions) also sweeps entries older than 30 days on every run, so accumulation from one-shot runs is bounded even without manual cleanup.
- **The three git calls this hook makes (repo-root resolve, `git status --porcelain`, the upstream-ahead check) are unbounded on stock macOS.** `_lib_capped`'s `timeout` wrapper is a no-op when GNU coreutils isn't installed, so a wedged git call (a stale NFS mount, a held index lock) hangs the hook rather than failing closed to a decision. Every other hot-path hook in this repo shares the same limitation; `install.sh` warns about the missing `timeout`/`gtimeout` binary at onboarding time.
