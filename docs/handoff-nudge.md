# Handoff Nudge Hook

## What the hook does

`nudge-handoff-near-context-cap.sh` is registered on both `UserPromptSubmit` and `Stop`. It reads the latest assistant usage block from the session transcript on every trigger, sums the four token fields (`cache_read_input_tokens`, `cache_creation_input_tokens`, `input_tokens`, `output_tokens`), resolves the record's `.message.model` field to a context window, and emits a one-shot `hookSpecificOutput.additionalContext` JSON payload when the total crosses 40% of that window, capped at an absolute-token ceiling (`HANDOFF_NUDGE_ABS_CAP`, default 360000) so a 1M-window session isn't left to accumulate 5x the dollar-equivalent prefix of a 200k-window session before the nudge fires. The `Stop` registration closes the gap where a session crosses the threshold on its final assistant turn, with no further user prompt to trigger the `UserPromptSubmit` check:

| Window | Threshold (40%) | Effective threshold | Models |
|---|---|---|---|
| 200k | 80000 | 80000 | Haiku 4.5, Sonnet 4.5, Opus 4.5, Opus 4.1 |
| 1M (default) | 400000 | 360000 (`HANDOFF_NUDGE_ABS_CAP`, default) | Sonnet 5, Opus 5, Opus 4.8/4.7/4.6, Sonnet 4.6, Fable 5, Mythos 5, and any unrecognized model ID |

**Why this cap.** The threshold was originally a flat 40% of the model's context window, chosen from `transcript-analysis.py context-distribution --since 8d` run against four account config directories on 2026-08-07. Percent-of-window is a capacity measure, not a cost measure: cache-read cost is linear in absolute tokens, so the same 40% rule fired at 80000 tokens on a 200k-window model but 400000 on a 1M-window model — a 5x difference in the prefix size the nudge exists to bound. `context-distribution --since 30d`'s absolute-token bucketing (added to answer this question in the hook's own unit) was run across every account config directory accessible in this environment on 2026-08-08. 360000 tokens is the grounded cap: the tightest measured account showed 48.4% session-share at that value, every other measured account came in well under it, and both clear the 50% session-share ceiling the shipped 40%-of-window threshold itself already established as acceptable. 360000 also sits comfortably above both GH-556 regression floors (80000 and 135000), so the 200k arm's own 40%-of-window threshold is unaffected. `HANDOFF_NUDGE_ABS_CAP` overrides the cap for a consumer whose session-shape differs from the curve this value was read off. Re-run `context-distribution --since 30d` against current account data before changing this cap again — it reports session-share alongside dollar-share for exactly this reason. See `## Known limitations` for the dismissal-risk this move trades off.

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

- **One-shot per session — the unwarned tail is now materially worse.** The nudge fires at most once per session. If the task continues well past 40% without completing, no further reminders are emitted. This is intentional to avoid repeated interruptions. Firing at the absolute cap instead of the old flat 400000-token point spends that single shot earlier on a 1M-window session, so a session that keeps running afterward now runs further past the fire point, unwarned, before hitting auto-compact than it did before this cap. Re-arming at escalating bands remains deferred pending frequency evidence.
- **Lowering the threshold increases how often a session sees the nudge at all, not just when.** The dollar-share figures in "Why this cap" above measure spend coverage, not nudge frequency — the frequency metric is session-share (fraction of *sessions*, not dollars, whose peak crosses the threshold), and that also rose on every account: from 1.25x (40.0%→50.0% on one account) to as much as ~4x (7.6%→30.8% on another). That's a real dismissal-as-noise risk if engineers start tuning the nudge out. Weighed against that: the coverage gain was the entire point of the change (most spend previously happened in sessions that never reached 60% at all), and the one-shot-per-session limit above still bounds repetition within any single session. A two-tier nudge (an earlier informational fire plus a later hard one) was considered and deferred as a separate implementation surface — revisit if dismissal turns out to be material in practice.
- **Markers persist until the next fire, not until session teardown.** The hook sweeps entries older than 30 days from its own marker directory each time it fires — interactive session or `claude -p` one-shot alike — so accumulation is bounded by the interval between fires across all sessions, not by any per-session cleanup. Run `rm -f ~/.claude/.handoff-nudge-fired.d/<session-id>` to remove a specific marker manually, or `rm -f ~/.claude/.handoff-nudge-fired.d/*` to clear all.
- **Model→window table is hardcoded and dated.** Source: https://platform.claude.com/docs/en/about-claude/models/overview, fetched 2026-08-03; re-verify by 2026-11-03. An unlisted model ID silently takes the 1M-window default, whose effective threshold is the absolute cap (360000 by default) rather than 400000. This default is not self-detecting: a future smaller-window model that mis-resolves to the 1M default may still never accumulate enough tokens to cross the cap if its real window sits below it, so it never fires and never appears in the log at all. The dated source comment in the hook, checked manually, is the actual staleness control — check `model=`/`window=` on `nudged` log lines against reality for any model that does fire.
