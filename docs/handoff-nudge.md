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

## Querying the current estimate (`--check`)

The nudge is a one-shot push, which makes it a poor thing to reason about as a query — a session at the plan→implementation boundary wants to know where it stands, not whether an injection happened to fire. `--check` answers that directly:

```bash
~/.claude/hooks/nudge-handoff-near-context-cap.sh --check
```

It writes nothing — no fired marker, no log line — and exits 0 on every path, emitting one JSON object:

```json
{"status":"ok","session_id":"…","estimate":310184,"threshold":360000,
 "over_threshold":false,"model":"claude-opus-5","context_window":1000000,
 "model_recognized":true,"already_fired":false,"nudge_disabled":false}
```

`estimate` is the same figure the fire path computes — the sum of the four token fields on the *latest recorded assistant turn*, not a live running count, so it lags by at most one assistant step. `over_threshold` is the field to act on; it uses the same `>=` comparison the fire path does, so the two never disagree about the same session. `already_fired` reports whether the one-shot nudge is spent, and `nudge_disabled` reports the kill-switch — **`--check` does not honour the kill-switch**, because that switch suppresses *notifying*, not *measuring*, and a session that explicitly asks for a number should get one. `model_recognized` is `false` only when the model ID matched no arm of the table above and fell to the 1M default, in which case the threshold may not match the running model.

The harness supplies `session_id` and `transcript_path` on stdin to hooks only, so a manual run resolves both itself: it walks up to six process ancestors looking for the `sessions/<pid>` entry `capture-session-id.sh` writes, stopping at the first ancestor that is itself `claude` rather than climbing past it, checks that entry's stored start time against the live process to catch PID reuse, then finds the transcript by globbing `$CONFIG_DIR/projects/*/<session-id>.jsonl`. Stopping at `claude` is what keeps a session whose own entry is missing from inheriting a parent session's number; an entry found at a hop *below* that process still resolves, since it belongs to the asking session's own subtree. Keying on the session id rather than deriving a project slug from the cwd is deliberate: a session that moved between worktrees has its transcript under the worktree's slug, not the one its cwd would produce.

Refusing is a first-class outcome — a confident number for the wrong session is worse than no number, because a caller presents it as measured fact:

| `status` | `reason` | Meaning |
|---|---|---|
| `cannot-resolve` | `config-dir-unresolved` | `CLAUDE_CONFIG_DIR` is set but relative, so it resolves differently per invocation cwd — or it is unset and `$HOME` is empty too |
| `cannot-resolve` | `session-id-unresolved` | No `sessions/<pid>` entry anywhere in the ancestor walk, and no `claude` ancestor reached within the hop cap either |
| `cannot-resolve` | `session-id-missing-at-claude` | The walk reached the owning `claude` process and it has no `sessions/<pid>` entry — typically a nested Claude Code session, or one whose `SessionStart` capture did not run |
| `cannot-resolve` | `session-id-stale-pid` | The entry's stored start time disagrees with the live process — the PID was reused |
| `cannot-resolve` | `session-id-malformed` | The entry's first line is not a safe path component |
| `cannot-resolve` | `transcript-not-found` | No transcript matched the session id |
| `cannot-resolve` | `transcript-ambiguous` | More than one project directory holds a transcript for it |
| `cannot-resolve` | `usage-block-missing` | The transcript has no assistant record carrying a usage block |
| `cannot-resolve` | `jq-unavailable` | `jq` is missing, failed, or timed out, so no payload could be encoded |
| `schema-drift` | — | A usage block was found but all four token fields were 0 or null |

`plan-it` Step 7 and the `handoff` skill's warrant check both call this and report the result.

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
- **`--check`'s PID-reuse guard has second resolution.** The stored and live start times come from `ps -o lstart=`, which reports whole seconds, so a process exiting and its PID being reused within the same second with a byte-identical start time would go undetected. The consequence is a stale number reported once, not a privilege or correctness boundary.
- **`--check`'s stop rule keys on the process name `claude`.** The walk halts at the first ancestor whose `ps -o comm=` reports `claude`, matching the bare name GNU `ps` emits, the absolute path BSD `ps` emits, and the leading-hyphen form either emits when `argv[0]` carries one. A consumer whose CLI process reports some other name — a wrapper script, a version-manager shim, or an interpreter such as `node` — gets no early stop, so a nested session under one of those can still resolve a parent session's entry as it did before. The behaviour is unchanged for those installs rather than newly broken, and the failure direction everywhere else is a refusal, never a wrong number.
- **`--check` reports the threshold as computed in its own environment.** That is authoritative when `HANDOFF_NUDGE_ABS_CAP` is unset (both paths fall back to the same literal), but may diverge from the fire path's if an override is exported somewhere only one of the two environments sees.
- **Model→window table is hardcoded and dated.** Source: https://platform.claude.com/docs/en/about-claude/models/overview, fetched 2026-08-03; re-verify by 2026-11-03. An unlisted model ID silently takes the 1M-window default, whose effective threshold is the absolute cap (360000 by default) rather than 400000. This default is not self-detecting: a future smaller-window model that mis-resolves to the 1M default may still never accumulate enough tokens to cross the cap if its real window sits below it, so it never fires and never appears in the log at all. The dated source comment in the hook, checked manually, is the actual staleness control — check `model=`/`window=` on `nudged` log lines against reality for any model that does fire.
