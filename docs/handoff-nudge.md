# Handoff Nudge Hook

## What the hook does

`nudge-handoff-near-context-cap.sh` is registered on both `PostToolBatch` and `Stop`. `PostToolBatch` fires once per resolved tool-call batch, before the next model call — this is what lets the hook observe context growth *during* an autonomous tool-call stretch, not only at the human-interaction boundaries `UserPromptSubmit`/`Stop` sit at. The `Stop` registration closes the remaining gap: a turn with no tool calls at all has nothing for `PostToolBatch` to resolve, so a session that crosses the threshold on a toolless final turn would otherwise go unwarned.

It reads the latest assistant usage block from the session transcript, sums the four token fields (`cache_read_input_tokens`, `cache_creation_input_tokens`, `input_tokens`, `output_tokens`), resolves the record's `.message.model` field to a context window, and emits a `hookSpecificOutput.additionalContext` JSON payload when the total crosses 40% of that window, capped at an absolute-token ceiling (`HANDOFF_NUDGE_ABS_CAP`, default 150000) so a 1M-window session isn't left to accumulate 5x the dollar-equivalent prefix of a 200k-window session before the nudge fires.

At `PostToolBatch`'s fire rate (far higher than `Stop`/`UserPromptSubmit`'s), the transcript read is incremental: a per-session state file (`<config-dir>/.handoff-nudge-fired.d/<session_id>-scan`, `<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) caches the byte offset, estimate, and model from the last scan, so a fire with nothing new to read reuses the cached estimate rather than re-scanning the whole transcript. The first fire in a session, or one following a rotated/truncated transcript, falls back to a bounded scan of the transcript's last 200 lines to bootstrap the cache.

| Window | Threshold (40%) | Effective threshold | Models |
|---|---|---|---|
| 200k | 80000 | 80000 | Haiku 4.5, Sonnet 4.5, Opus 4.5, Opus 4.1 |
| 1M (default) | 400000 | 150000 (`HANDOFF_NUDGE_ABS_CAP`, default) | Sonnet 5, Opus 5, Opus 4.8/4.7/4.6, Sonnet 4.6, Fable 5, Mythos 5, and any unrecognized model ID |

**Why this cap.** Billing is linear per token — there's no per-token cost penalty for a longer prefix, so the cap isn't grounded in pricing shape. It's grounded in cost per unit of delivered work instead: `transcript-analysis.py pr-cost --record` builds a ledger of shipped PRs against each PR's mean context depth, and bucketing those rows by `mean_context_at_turn` (100–150k, 150–200k, 200–250k, 250–300k, 300k+) shows both $/PR and $/1k output tokens rising monotonically past the 100–150k bucket — the cheapest bucket with a trustworthy sample (n=23 PRs) in a 145-PR run of this ledger against this repo's own corpus. 150000 tokens is the upper edge of that bucket: sessions that cross it roll into the next, more expensive 150–200k bucket. 150000 also clears the 135000-token floor a GH-556 regression test pins — confirming the hook's per-model thresholds never re-fire at the flat 120000-token constant GH-556 replaced — so the 200k arm's own 40%-of-window threshold is unaffected; note the margin above that floor shrank from 225000 tokens to 15000, so the next retune of either constant should re-verify `150000 > <current GH-556 floor>` rather than assume the old headroom still holds. `HANDOFF_NUDGE_ABS_CAP` overrides the cap for a consumer whose session-shape differs from the corpus this value was read off. Re-run `pr-cost --record` and re-bucket by `mean_context_at_turn` against current data before changing this cap again. See `## Known limitations` for the dismissal-risk this move trades off.

The injected reminder tells the agent to suggest `/handoff` if the current task is not near completion — `/handoff` captures state in a `/tmp` brief and resumes in a fresh session; per-turn cost rises with carried context, but a fresh session pays a one-time rebuild cost first, so handoff pays off over several turns rather than immediately. The nudge re-arms at escalating token bands rather than firing once per session: a marker file under `<config-dir>/.handoff-nudge-fired.d/<session_id>` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) holds the estimate that triggered the most recent fire, and the hook fires again once the current estimate reaches that value plus `HANDOFF_NUDGE_REARM_SPACING` (default 80000 — see "Why this spacing" below).

**Why this spacing.** `transcript-analysis.py rearm-backtest --this-repo`, run against 127 sessions in this repo's own corpus, found 40000 and 80000 statistically indistinguishable under realistic (lagged) operator compliance on both dollar delta (-261.81 vs -259.76) and mean-context delta (-33,565 vs -32,732), while 120000 measured worse on both axes and was dropped. 80000 was chosen over the tied 40000 because it gives roughly 2.6x the sample's median 30,624-token operator-response lag as separation between nudges: a spacing barely larger than that lag risks a second nudge arriving just as, or before, the operator has acted on the first — the dismissal-as-noise risk `## Known limitations` already names for over-frequent firing. `HANDOFF_NUDGE_REARM_SPACING` overrides the spacing for a consumer whose session-shape differs from the corpus this value was read off, mirroring `HANDOFF_NUDGE_ABS_CAP` above.

Past a session's first fire, every re-arm the operator lets pass without acting on it increments a per-session ignored-re-arm counter (`<config-dir>/.handoff-nudge-fired.d/<session_id>-ignored`, one byte appended per ignored re-arm). The count is recorded on every `nudged` log line (`ignored=`, see "Log location" below) but does not itself gate anything — see "Why this block point" for what does. Once the session's estimate reaches `HANDOFF_NUDGE_BLOCK_AT`, the *next* re-arm stops being advisory: instead of the `additionalContext` JSON, the hook exits 2 with a reason on stderr — `PostToolBatch`'s own native "stop the agentic loop before the next model call" contract — forcing the session to address the context growth rather than continuing to defer it.

**Why this block point.** `HANDOFF_NUDGE_BLOCK_AT` (default 470000) is an absolute token position: the hard block fires once a session's estimate reaches it, independent of how many re-arms preceded it and of `HANDOFF_NUDGE_ABS_CAP`/`HANDOFF_NUDGE_REARM_SPACING`. A single observed session reached 485000 tokens after two dismissed advisory nudges, each dismissal reasonable in isolation, before manual intervention. That session is the sole documented justification for having a hard block at all. 470000 stays under that session's 485000 tokens.

A corpus scan of 426 fired sessions across 6 accounts is right-censored — it shows where sessions happened to stop, not where they should have been stopped — and has no data at the depth 470000 implies (past a session's fifth crossing under today's shipped 150000 cap and 80000 spacing), so it neither supports nor rules out 470000 specifically. The grounding session above is the actual justification for this value.

The hard block fires only on `PostToolBatch`. The same hook is also registered on `Stop`, where `exit 2` *forces the conversation to continue* rather than stopping it, so a `Stop`-registered re-arm falls through to the advisory path instead. See "Known limitations" for the resulting coverage gap on `Stop`.

`HANDOFF_NUDGE_BLOCK_AT` overrides the point for a consumer whose tolerance for dismissal differs from this default, mirroring `HANDOFF_NUDGE_ABS_CAP`/`HANDOFF_NUDGE_REARM_SPACING` above. The stderr message on a hard block names it directly.

Revisit this default via an extended `rearm-backtest` once escalation-fire data exists — concretely, once at least two contributors report a hard block on a session they consider legitimate, or after enough elapsed time that fresh corpus data materially changes the picture. The `ignored=`/`skills=` fields recorded on every `nudged` log line (see "Log location" below) exist for exactly that future analysis.

**Recovering from a hard block.** This is sticky within a session — the estimate only grows, so once it has reached `HANDOFF_NUDGE_BLOCK_AT` every later re-arm in that same session hard-blocks too, with no in-session reset. Three routes out:

- **Kill-switch** (`touch "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge-disabled"`) — suppresses every future fire, including the hard block, the same as it already suppresses the advisory nudge.
- **`/handoff`** — the intended way out. It captures state in a `/tmp` file and resumes in a fresh session, whose own estimate starts back below `HANDOFF_NUDGE_BLOCK_AT`.
- **The `/handoff` active-bypass marker** (`<config-dir>/.handoff-active.d/<session_id>`) — live only while `/handoff` itself is running, it keeps a qualifying re-arm advisory instead of blocking, so the block can't truncate `/handoff`'s own multi-turn write.

`/handoff` collects this session's in-flight background dispatches before writing the file (see the `handoff` skill's own "Before writing: collect in-flight background dispatches" section), so the ordinary exit from a block can take longer than a single turn.

## How to disable

Touch the kill-switch file to suppress nudges globally:

```bash
touch "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge-disabled"
```

Remove the file to re-enable:

```bash
rm "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge-disabled"
```

The hook checks for this file before reading the transcript. It is useful when running `claude -p` pipelines or automated test harnesses where the nudge would produce noise.

## Querying the current estimate (`--check`)

The nudge is a one-shot push, which makes it a poor thing to reason about as a query — a session at the plan→implementation boundary wants to know where it stands, not whether an injection happened to fire. `--check` answers that directly:

```bash
"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/nudge-handoff-near-context-cap.sh" --check
```

It writes nothing — no fired marker, no log line — and exits 0 on every path, emitting one JSON object:

```json
{"status":"ok","session_id":"…","estimate":98430,"threshold":150000,
 "over_threshold":false,"model":"claude-opus-5","context_window":1000000,
 "model_recognized":true,"already_fired":false,"nudge_disabled":false}
```

`estimate` is the same figure the fire path computes — the sum of the four token fields on the *latest recorded assistant turn*, not a live running count, so it lags by at most one assistant step. `over_threshold` is the field to act on; it uses the same `>=` comparison the fire path does, so the two never disagree about the same session. `already_fired` reports whether the marker file exists — i.e. whether the nudge has fired at least once this session — and `nudge_disabled` reports the kill-switch — **`--check` does not honour the kill-switch**, because that switch suppresses *notifying*, not *measuring*, and a session that explicitly asks for a number should get one. `model_recognized` is `false` only when the model ID matched no arm of the table above and fell to the 1M default, in which case the threshold may not match the running model. `already_fired=true` does not mean no further nudge this session — the marker re-arms at escalating bands, so a session can be both `already_fired=true` and still due for another fire once its estimate advances past the next band.

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

The hook appends one line per significant event to `<config-dir>/.handoff-nudge.log`. Two line types appear:

| Line prefix | Meaning |
|---|---|
| `nudged session=<id> est=<n> model=<id> window=<n> event=<PostToolBatch\|Stop> ignored=<n> skills=<label,label\|-> [action=block]` | Threshold crossed — first fire or a later re-arm, advisory or hard block alike; nudge emitted. `ignored=` is the ignored-re-arm count at fire time (0 on a first fire). `skills=` names the active-bypass skill markers live at fire time, comma-joined, or `-` when none are live. `action=block` is present only on a hard-block fire; an advisory fire carries no `action` field |
| `schema-drift session=<id> event=<PostToolBatch\|Stop>` | Usage block was found but all four token fields were 0 or null, suggesting the transcript schema changed; see [Known limitations](#known-limitations) |

The log is append-only and not rotated automatically. Trim it periodically if disk space is a concern: `> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge.log"`.

## How to read `spend-over-threshold` output

`transcript-analysis.py spend-over-threshold` reports what share of a session's dollar spend was earned at or above the handoff nudge's own fire threshold, bucketed by ISO week:

```
Week       Sessions       AboveUSD       TotalUSD   Share
2026-W21          3          42.10          97.35   43.3%
2026-W22          5          61.80         120.02   51.5%
```

- **Sessions** — sessions in scope for the week that have a resolvable fire threshold and nonzero priced spend (see exclusions below).
- **AboveUSD** — summed dollars from main-thread turns whose `context_at_turn` was at or above that session's own effective fire threshold (`_hook_effective_fire_threshold`, from its first main-thread turn's model — the same threshold the real hook computes).
- **TotalUSD** — summed dollars from every priced main-thread turn in scope.
- **Share** — `AboveUSD / TotalUSD` as a percentage; this is the fraction of spend that happens once a session is already past the point where the hook would nudge it.

A session with no main-thread turn carrying a usage block (no fire threshold to be above or below) or with zero total priced dollars (every turn unpriced) is excluded from the report entirely, not shown with an undefined or zero share. A schema-drift error count from `<config-dir>/.handoff-nudge.log` is printed as a diagnostic footer when the log is present. Schema-drift lines indicate the usage-block field paths in the hook may need updating.

Run with:

```bash
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" spend-over-threshold --since 2026-01-01
```

## Known limitations

- **Re-arms at a fixed spacing, not a schedule that adapts to dismissal.** The nudge fires again once the estimate advances `HANDOFF_NUDGE_REARM_SPACING` tokens (default 80000) past the last fire — see "Why this spacing" above. A session that dismisses every re-arm still eventually reaches the same log-volume profile as an unbounded one-shot session would have, just bounded by the spacing rather than eliminated: the tail is no longer *unwarned*, but it is not capped at one warning either.
- **Lowering the threshold increases how often a session sees the nudge at all, not just when.** Session-share (the fraction of *sessions*, not dollars, whose peak crosses the threshold) rose on every measured account when the absolute cap first replaced the flat 40%-of-window threshold: from 1.25x (40.0%→50.0% on one account) to as much as ~4x (7.6%→30.8% on another) — see `absolute-token-handoff-threshold.md` for that measurement. That's a real dismissal-as-noise risk if engineers start tuning the nudge out. Weighed against that: the coverage gain was the entire point of that earlier change (most spend previously happened in sessions that never reached 60% at all), and the re-arm spacing above still bounds repetition within any single session to one nudge per band. A two-tier (informational-then-hard) nudge would reduce this risk; not implemented. The 1.25x–4x figures are from that earlier transition. The 360000→150000 drop measured 1.69x (53.1%→89.7%); see [`case-studies/handoff-threshold-impact.md`](case-studies/handoff-threshold-impact.md) for the full measurement and the corresponding hard-block-frequency rise.
- **Markers persist until the next fire, not until session teardown.** The marker file holds the estimate that triggered the most recent fire (not a zero-byte touch), and every re-arm overwrites it with the new triggering estimate. The marker directory's steady-state file count is O(all-sessions-with-tool-calls), since the `-scan` state file writes on every fire-path invocation, not only on a fire. The hook sweeps entries older than 30 days from its own marker directory on every invocation that reaches the fire path (not only when it actually fires) — interactive session or `claude -p` one-shot alike — so accumulation is bounded by the interval between invocations across all sessions, not by any per-session cleanup. Because a re-arm overwrite also re-stamps the file's mtime, a long-running session that keeps re-arming near the 30-day mark keeps pushing its own marker's sweep eligibility forward from its *last* fire rather than its first. Run `rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge-fired.d/<session-id>"*` to remove every marker for a specific session manually, or `rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge-fired.d/"*` to clear all.
- **`PostToolBatch` has thin official documentation.** The hooks reference (code.claude.com/docs/en/hooks) has no dedicated `PostToolBatch` section — no worked input/output JSON example — so the field set and block-output shape this hook relies on were confirmed by capturing a real payload and testing the block contract live, not from the docs alone. If a future harness version changes either, this hook's plan-mode/subagent gates or its escalation-ladder block could silently degrade; watch `<config-dir>/.handoff-nudge.log` for an unexplained drop in `nudged` lines, or the absence of hard blocks despite `HANDOFF_NUDGE_BLOCK_AT` being reached, as the signal.
- **The hard block is unreachable on a 200k-window model.** `HANDOFF_NUDGE_BLOCK_AT`'s default (470000) sits above any 200k context window, so a session on such a model can never reach it — the advisory nudge re-arms indefinitely instead. A future retune of `HANDOFF_NUDGE_BLOCK_AT` toward a 200k-reachable value should be a deliberate choice, not an accident.
- **Worst-case per-fire latency sums across up to eight or nine independently-capped external calls.** Each `_lib_capped_for 2` call caps its own subprocess at 2s, but a single fire can chain that many (input parse, transcript size, usage scan, offset fast/slow-path checks, ignored-counter read, active-bypass marker enumeration, output build), for a worst case around 14-20s in one fire, not 2s. That ceiling is reachable on every tool-call batch under a uniformly slow filesystem, since `PostToolBatch` fires far more often than `Stop`/`UserPromptSubmit`.
- **A missing `timeout`/`gtimeout` binary removes `_lib_capped_for`'s own 2s cap, falling back to the harness's own hook timeout.** Per the hooks reference (code.claude.com/docs/en/hooks, fetched 2026-08-17; re-verify by 2026-11-17), `command`-type hooks default to a 600-second timeout unless the firing event lowers it, and `PostToolBatch` is not one of the two events (`UserPromptSubmit`, `MessageDisplay`) that do — so an uncapped external call on such a machine could stall a batch for up to 600s, not indefinitely, but far past this hook's normal latency. `install.sh` only warns about a missing `timeout`/`gtimeout` at onboarding; it doesn't block.
- **A scan timeout on the incremental-read slow (mid-write) path makes no forward progress.** On a timeout, the byte offset falls back to its pre-attempt value rather than partial progress, so the next fire re-attempts the identical scan over a slice that has since grown further. This can compound on a persistently slow filesystem; no data is corrupted (the fire just reuses its cached estimate), but the latency cost repeats and grows instead of resolving. No log signal currently distinguishes this from an ordinary cache-reuse fire; revisit if it recurs in practice.
- **`--check`'s PID-reuse guard has second resolution.** The stored and live start times come from `ps -o lstart=`, which reports whole seconds, so a process exiting and its PID being reused within the same second with a byte-identical start time would go undetected. The consequence is a stale number reported once, not a privilege or correctness boundary.
- **`--check`'s stop rule keys on the process name `claude`.** The walk halts at the first ancestor whose `ps -o comm=` reports `claude`, matching the bare name GNU `ps` emits, the absolute path BSD `ps` emits, and the leading-hyphen form either emits when `argv[0]` carries one. A consumer whose CLI process reports some other name — a wrapper script, a version-manager shim, or an interpreter such as `node` — gets no early stop, so a nested session under one of those can still resolve a parent session's entry; the failure direction everywhere else is a refusal, never a wrong number.
- **`--check` reports the threshold as computed in its own environment.** That is authoritative when `HANDOFF_NUDGE_ABS_CAP` is unset (both paths fall back to the same literal), but may diverge from the fire path's if an override is exported somewhere only one of the two environments sees.
- **Model→window table is hardcoded and dated.** Source: https://platform.claude.com/docs/en/about-claude/models/overview, fetched 2026-08-03; re-verify by 2026-11-03. An unlisted model ID silently takes the 1M-window default, whose effective threshold is the absolute cap (150000 by default) rather than 400000. This default is not self-detecting: a future smaller-window model that mis-resolves to the 1M default may still never accumulate enough tokens to cross the cap if its real window sits below it, so it never fires and never appears in the log at all. The dated source comment in the hook, checked manually, is the actual staleness control — check `model=`/`window=` on `nudged` log lines against reality for any model that does fire.
- **A session whose context growth happens entirely on toolless turns gets no hard-block escalation.** The hard block fires only on `PostToolBatch` (see "Why this block point" above for why). `Stop`'s own registration exists to cover exactly the toolless-turn case "What the hook does" describes — a turn with no tool calls for `PostToolBatch` to resolve — so a session that only ever crosses re-arm bands on such turns keeps getting advisory nudges indefinitely, with no hard block regardless of `HANDOFF_NUDGE_BLOCK_AT`.
- **Sessions nudged only via `UserPromptSubmit` — an event the current `Stop`+`PostToolBatch` registration no longer includes — go entirely un-nudged.** As of 2026-08-25 this affected 4 of 129 sessions (3.1%). Re-derive that figure before trusting it, rather than treating it as a one-time result: group `nudged` lines in `<config-dir>/.handoff-nudge.log` by `session=` in the slice preceding the log's first `event=PostToolBatch` line, and count sessions whose event set is exactly `{UserPromptSubmit}` — the log carries no timestamp field, so a coverage regression here produces no signal of its own, and is detectable only by re-running this grouping or by cross-referencing `transcript-analysis.py` for sessions that crossed the nudge threshold with no corresponding log line.
- **The `/handoff` active-bypass marker's suppression window is bounded only by PID liveness, not by the skill's own progress.** A `/handoff` run that halts between its "Before writing: activate the handoff bypass marker" and "After writing: deactivate the handoff bypass marker" steps leaves the hard block suppressed for the remainder of that session, and `marker.sh clear-stale` cannot evict it since the owning process is still alive. Run `~/.claude/scripts/marker.sh deactivate handoff` manually to clear it. The advisory nudge still fires throughout this window, so the session is not left unwarned, only unblocked.
