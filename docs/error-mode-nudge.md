# Error-Mode Analysis Nudge Hook

## What the hook does

`nudge-error-mode-analysis.sh` is a `UserPromptSubmit` hook that runs `transcript-analysis.py friction-count` against the current session's transcript on every user turn and sums three signals — hook denials, failed test runs, and user-correction phrases — into a composite. When that composite reaches `FRICTION_THRESHOLD` (12, backtested against 654 historical sessions at the 99th percentile), the hook emits a one-shot `hookSpecificOutput.additionalContext` payload suggesting the agent offer to run `/error-mode-analysis` if the current body of work is close to delivered. The nudge fires once per session; a marker file under `<config-dir>/.error-mode-nudge-fired.d/<session_id>` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) prevents repeated injections, and that marker check runs before `transcript-analysis.py` is ever spawned. A second per-session checkpoint file under `<config-dir>/.error-mode-nudge-checkpoint.d/<session_id>` makes each fire an incremental scan of only the transcript bytes appended since the last prompt, rather than a full re-parse.

## How to enable

The hook is **opt-in** — dormant unless armed:

```bash
touch "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.error-mode-nudge-enabled"
```

Remove the file to disable it again:

```bash
rm "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.error-mode-nudge-enabled"
```

Without this file, every invocation exits before reading the transcript or spawning `transcript-analysis.py`. Unlike the sibling `nudge-handoff-near-context-cap.sh` hook (opt-out, on by default), this nudge is off by default for every contributor and must be explicitly turned on per machine.

## Log location

The hook appends one line per fire to `<config-dir>/.error-mode-nudge.log`:

```
nudged session=<id> friction=<n>
```

The log is append-only and not rotated automatically. Trim it periodically if disk space is a concern: `> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.error-mode-nudge.log"`.

## Known limitations

- **One-shot per session.** The nudge fires at most once per session, even if friction keeps accumulating afterward.
- **`claude -p` may leak stale markers and checkpoints.** `claude -p` skips `SessionEnd`, so marker/checkpoint files can leak — a 30-day mtime eviction sweep on qualifying hook invocations cleans them up automatically.
- **Denial dedup is not carried across checkpoint reads.** `friction-count`'s in-call denial dedup (`seen_denial_ids`) isn't persisted across checkpoint reads, so a denial straddling a checkpoint boundary can be double-counted — accepted since it can only inflate the composite, never corrupt state.
- **Threshold is a snapshot, not a live recalibration.** `FRICTION_THRESHOLD=12` was set from one backtest run on one machine's historical sessions; it does not self-adjust as usage patterns change.
