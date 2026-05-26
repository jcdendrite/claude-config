# Handoff Nudge Hook

## What the hook does

`nudge-handoff-near-context-cap.sh` is a `UserPromptSubmit` hook that reads the latest assistant usage block from the session transcript on every user turn, sums the four token fields (`cache_read_input_tokens`, `cache_creation_input_tokens`, `input_tokens`, `output_tokens`), and emits a one-shot `hookSpecificOutput.additionalContext` JSON payload when the total exceeds 120 000 tokens (approximately 60% of the 200k model context window). The injected reminder tells the agent to suggest `/handoff` if the current task is not near completion — `/handoff` captures state in a `/tmp` brief and resumes in a fresh session, which is cheaper per turn than waiting for auto-compaction. The nudge fires once per session; a marker file under `~/.claude/.handoff-nudge-fired.d/<session_id>` prevents repeated injections.

## How to disable

Touch the kill-switch file to suppress nudges globally:

```bash
touch ~/.claude/.handoff-nudge-disabled
```

Remove the file to re-enable:

```bash
rm ~/.claude/.handoff-nudge-disabled
```

The hook checks for this file before reading the transcript. It is useful when running `claude -p` pipelines or automated test harnesses where the nudge would produce noise.

## Log location

The hook appends one line per significant event to `~/.claude/.handoff-nudge.log`. Three line types appear:

| Line prefix | Meaning |
|---|---|
| `skip session=<id> est=<n>` | Token estimate was below 120 000; no nudge emitted |
| `nudged session=<id> est=<n>` | Threshold crossed for the first time this session; nudge emitted |
| `schema-drift session=<id>` | Usage block was found but all four token fields were 0 or null, suggesting the transcript schema changed; see [Known limitations](#known-limitations) |

The log is append-only and not rotated automatically. Trim it periodically if disk space is a concern: `> ~/.claude/.handoff-nudge.log`.

## How to read `handoff-ratio` output

`transcript-analysis.py handoff-ratio` reports how often sessions use `/handoff` versus running until auto-compaction fires, bucketed by ISO week:

```
Week       Handoffs  Compactions  Ratio
2026-W21          3            7   30.0%
2026-W22          5            4   55.6%
```

- **Handoffs** — sessions where a `Skill` tool use with `input.skill == "handoff"` was found on the main thread.
- **Compactions** — sessions where a compaction marker record was present in the transcript.
- **Ratio** — `handoffs / (handoffs + compactions)` as a percentage; higher is better.

A schema-drift error count from `~/.claude/.handoff-nudge.log` is printed as a diagnostic footer when the log is present. Schema-drift lines indicate the usage-block field paths in the hook may need updating.

Run with:

```bash
python3 ~/.claude/scripts/transcript-analysis.py handoff-ratio --since 2026-01-01
```

## Known limitations

- **One-shot per session.** The nudge fires at most once per session. If the task continues well past 60% without completing, no further reminders are emitted. This is intentional to avoid repeated interruptions.
- **`claude -p` may leak stale markers.** One-shot invocations via `claude -p` do not fire `SessionEnd`, so the marker files written by the nudge hook are not cleaned up automatically. They are harmless — they only affect the session whose `session_id` they carry — but they accumulate at one-per-`claude -p`-call rate. The `cleanup-handoff-nudge-marker.sh` `SessionEnd` hook handles cleanup for interactive sessions. Run `rm -f ~/.claude/.handoff-nudge-fired.d/<session-id>` to remove a specific marker manually, or `rm -f ~/.claude/.handoff-nudge-fired.d/*` to clear all.
- **Threshold is a rough estimate.** The 120 000-token threshold is based on a 200k context window (Anthropic claude-sonnet-4-x and claude-opus-4-x). Smaller context models will have a higher effective percentage. The threshold is a constant in the hook script; adjust it locally if you use smaller-context models.
