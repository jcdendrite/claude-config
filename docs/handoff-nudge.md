# Handoff Nudge Hook

## What the hook does

`nudge-handoff-near-context-cap.sh` is registered on both `UserPromptSubmit` and `Stop`. It reads the latest assistant usage block from the session transcript on every trigger, sums the four token fields (`cache_read_input_tokens`, `cache_creation_input_tokens`, `input_tokens`, `output_tokens`), resolves the record's `.message.model` field to a context window, and emits a one-shot `hookSpecificOutput.additionalContext` JSON payload when the total crosses 60% of that window. The `Stop` registration closes the gap where a session crosses the threshold on its final assistant turn, with no further user prompt to trigger the `UserPromptSubmit` check:

| Window | Threshold (60%) | Models |
|---|---|---|
| 200k | 120 000 | Haiku 4.5, Sonnet 4.5, Opus 4.5, Opus 4.1 |
| 1M (default) | 600 000 | Sonnet 5, Opus 5, Opus 4.8/4.7/4.6, Sonnet 4.6, Fable 5, Mythos 5, and any unrecognized model ID |

The injected reminder tells the agent to suggest `/handoff` if the current task is not near completion — `/handoff` captures state in a `/tmp` brief and resumes in a fresh session; per-turn cost rises with carried context, but a fresh session pays a one-time rebuild cost first, so handoff pays off over several turns rather than immediately. The nudge fires once per session; a marker file under `~/.claude/.handoff-nudge-fired.d/<session_id>` prevents repeated injections.

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

The hook appends one line per significant event to `~/.claude/.handoff-nudge.log`. Two line types appear:

| Line prefix | Meaning |
|---|---|
| `nudged session=<id> est=<n> model=<id> window=<n> event=<UserPromptSubmit\|Stop>` | Threshold crossed for the first time this session; nudge emitted |
| `schema-drift session=<id> event=<UserPromptSubmit\|Stop>` | Usage block was found but all four token fields were 0 or null, suggesting the transcript schema changed; see [Known limitations](#known-limitations) |

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
- **Model→window table is hardcoded and dated.** Source: https://platform.claude.com/docs/en/about-claude/models/overview, fetched 2026-08-03; re-verify by 2026-11-03. An unlisted model ID silently takes the 1M-window default (threshold 600 000). This default is not self-detecting: a future smaller-window model that mis-resolves to the 1M default may never accumulate enough tokens to cross its threshold, so it never fires and never appears in the log at all. The dated source comment in the hook, checked manually, is the actual staleness control — check `model=`/`window=` on `nudged` log lines against reality for any model that does fire.
