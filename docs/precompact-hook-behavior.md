# `PreCompact`/`PostCompact` hook behavior — Phase 0 spike findings

Empirical results from `.claude/plans/precompact-review-snapshot.md`'s Phase 0 spike. Tested against `claude --version` **2.1.233**. Spike hook script and harness are throwaway (scratchpad, never committed); only these findings persist.

## JSON input schema observed

Both events, captured verbatim from a debug hook dumping raw stdin (`session_id`, `transcript_path`, `cwd`, `prompt_id`, `hook_event_name`, `trigger`, `custom_instructions` — the last always `null` in every run observed):

```json
{
  "session_id": "...",
  "transcript_path": "...",
  "cwd": "...",
  "prompt_id": "...",
  "hook_event_name": "PreCompact",
  "trigger": "manual" | "auto",
  "custom_instructions": null
}
```

`PostCompact` carries the same fields, plus one the docs never mention: **`compact_summary`**, the full structured summary Claude Code generated during compaction, embedded verbatim in the hook's input. See "Design implication" below.

No token-count or context-percentage field appears in either event's input.

## Block reason visible to Claude? Refuted.

Manual `/compact`, `PreCompact` exiting 2 with a stderr marker string suffixed by the debug hook's own PID (e.g. `SPIKE BLOCK MARKER PID12345: this string should only reach Claude if PreCompact block-reason is model-visible`). The marker reached the model only via terminal echo into `local-command-stdout`, not as genuine context injection. Confirmed via `/tmp/spike-2-block-manual.log`.

## Auto-compaction block safety: partially confirmed.

`SPIKE_MODE=block`, `claude --autocompact 100000`, `trigger=auto`. Auto-triggered `PreCompact` blocks are re-attempted on every subsequent prompt and never crash the session — blocked (exit 2) 7 times over ~46 minutes (`/tmp/spike-3-block-auto.log`, 02:29–03:15 UTC); context reached 118K+ tokens and climbing by the end of the observation window.

**Not tested:** whether this holds all the way to the model's real ~1M context ceiling — that would require pushing far more filler than was practical here. Treat as "safe up to ~120K tokens / 7 blocked cycles," not "safe unconditionally."

**Open question:** in this same run, the first two `PreCompact` fires (same `prompt_id`, 11 seconds apart) did not produce anything until a third fire (new `prompt_id`) 8 minutes later; cause not established.

## `additionalContext` support on exit 0: refuted, categorically.

`SPIKE_MODE=context`, both events returning:
```json
{"hookSpecificOutput": {"hookEventName": "PreCompact", "additionalContext": "SPIKE-CONTEXT-MARKER: ..."}}
```
Verified directly against the raw transcript, not just self-report: `grep -o '"additionalContext":"[^"]*SPIKE[^"]*"'` against the manual-`/compact` `additionalContext` probe session's own transcript (session `8d22abf4`, repeated below as the repeat-fire test) returns **zero matches** — the injected shape never appears in the transcript at all, not even filtered out downstream. The harness doesn't record it because it never gets past schema validation to be recorded.

Every attempt — the manual-`/compact` probe, the equivalent test against auto-triggered compaction, the subagent runs below, and a same-session repeat-fire test — failed the harness's own JSON schema validation with the identical error:

> Hook JSON output validation failed — (root): Invalid input... Expected schema: `hookSpecificOutput` accepts shapes for exactly five event types — `PreToolUse`, `UserPromptSubmit`, `PostToolUse`, `PostToolBatch`, `Stop`/`SubagentStop`.

`PreCompact` and `PostCompact` are absent from that list entirely. This is a hard, harness-level constraint, not an incidental payload mistake — no output shape from either event can inject `additionalContext` in this build. Confirmed independently three ways: the schema error itself; a probe asking whether the marker was visible (no, not via any legitimate channel); and a direct grep of the model's own transcript for the literal `"additionalContext":"SPIKE...` JSON shape (zero real matches — the only hits were the model's own investigation quoting the string back).

Where the marker *did* become visible each time was as literal text inside the validation-failure diagnostic itself, delivered via the same terminal-echo channel as the block-reason finding above, reproduced identically across two separate `/compact` runs.

## Subagent context window — **Inconclusive.**

Four separate spike sessions in the `precompact-spike-subagent` scratch directory, verified directly against each session's own transcript under Claude Code's local project-history storage, not from pasted summaries:

| Session (`session_id`) | Subagent task | Tokens reported | Main-session compactions | Result |
| --- | --- | --- | --- | --- |
| `cc9b7b8c` | 10 essays, ≥3000 words each, one response (no `--autocompact` set — an instruction gap in this round) | 24,729 (`tool_uses=0`) | 0 | Undershot to ~3 essays |
| `df7fbf82` | Same task, reworded "must produce ALL TEN... do not stop early", still single-response | **24,729** — identical to the run above | 2 | Undershot again; also the session that surfaced the `compact_summary` field (see below) |
| `ecdb7d1d` | Single continuous essay, ≥40,000 words | 24,648 | 1 | Undershot; essay complete but far short of the target length |
| `f7212ab5` | 10 separate `Write`-tool calls, one essay per file, forcing multiple turns | 60,591 | 2 | All 10 files written; still no subagent-scoped log entry |

Two independent generations from differently-worded prompts (`cc9b7b8c`, `df7fbf82`) landing on the exact same 24,729-token figure is strong evidence for a fixed per-response output ceiling around ~24.6-24.7K tokens, not the model choosing to stop early — "don't stop early" wording had zero measurable effect on the second attempt. `tool_uses=0` on both confirms each was a single generation call, not a multi-turn loop. Forcing multiple turns via separate `Write` calls (`f7212ab5`) raised total usage to 60,591 but still stayed under the 100K floor.

`/tmp/spike-5-subagent-v2.log` (the `f7212ab5` run) shows only the orchestrator's own `session_id` across all entries (two full `PreCompact`→`PostCompact` cycles, both `trigger=auto`, firing while the subagent ran in the background) — never a second, subagent-scoped session_id. All four attempts stayed under the 100,000-token floor, so this cannot be read as "subagent hooks don't fire" — only as "not yet tested at a high enough number." Also structurally relevant: the orchestrating Claude has no visibility into a subagent's raw transcript regardless (barred from reading the async `output_file` directly; only the final result text and reported usage numbers come back), so any future subagent-side compaction question needs to be answered from the hook log or the subagent's own transcript directly, not by asking the orchestrator to introspect.

**Not resolved further** — deemed non-blocking for the ledger design (see below) and not worth the additional token spend to chase past 60,591.

## Unintentional hook failure/hang — **Not run.**

Deferred; no data collected.

## Design implication for the review-narrative ledger

`PostCompact`'s input carries the full `compact_summary` Claude Code just generated during compaction. A `PostCompact` hook can't feed anything back to Claude (per the `additionalContext` refutation above), but it can **archive** that summary to disk — a cheap, no-synthesis-required complement to an incremental review-narrative ledger, useful as a raw safety net for whatever the ledger didn't anticipate capturing.

Combined with the `additionalContext` refutation above, the next implementation phase no longer needs a `PreCompact` hook at all, and needs a `PostCompact` hook only for archival (not for reminding Claude of anything — it structurally cannot). The reminder-to-check-the-ledger piece has to be a static, always-loaded CLAUDE.md instruction, not an event-driven injection.
