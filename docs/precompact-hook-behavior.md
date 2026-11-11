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

## Assumption 1 — block reason visible to Claude? **Refuted.**

Manual `/compact`, `PreCompact` exiting 2 with a stderr marker string suffixed by the debug hook's own PID (e.g. `SPIKE BLOCK MARKER PID12345: this string should only reach Claude if PreCompact block-reason is model-visible`). Probed directly afterward: the marker *was* visible to the model, but only because Claude Code echoes the hook's blocking stderr into the terminal output that becomes `local-command-stdout` in the transcript — a debug/terminal surface, not a context-injection path. Confirmed via `/tmp/spike-2-block-manual.log`.

## Assumption 2 — auto-compaction-block safety? **Partially confirmed.**

`SPIKE_MODE=block`, `claude --autocompact 100000`, `trigger=auto`. `PreCompact` fired and was blocked (exit 2) **7 times over ~46 minutes** (`/tmp/spike-3-block-auto.log`, 02:29–03:15 UTC) — the harness re-attempts compaction on essentially every subsequent prompt once past the configured window, and gets refused every time. No crash, no hard error, session kept accepting prompts throughout; context reached 118K+ tokens and climbing by the end of the observation window.

**Not tested:** whether this holds all the way to the model's real ~1M context ceiling — that would require pushing far more filler than was practical here. Treat as "safe up to ~120K tokens / 7 blocked cycles," not "safe unconditionally."

**Open, unresolved:** in this same run, the first two `PreCompact` fires (same `prompt_id`, 11 seconds apart) did not produce anything until a third fire (new `prompt_id`) 8 minutes later. No explanation was established — could be a retry after a transient failure, could be something else. Flagged here rather than guessed at.

## Assumption 3 — `additionalContext` support on exit 0? **Refuted, categorically.**

`SPIKE_MODE=context`, both events returning:
```json
{"hookSpecificOutput": {"hookEventName": "PreCompact", "additionalContext": "SPIKE-CONTEXT-MARKER: ..."}}
```
Verified directly against the raw transcript, not just self-report: `grep -o '"additionalContext":"[^"]*SPIKE[^"]*"'` against the manual-`/compact` `additionalContext` probe session's own transcript (session `8d22abf4`, repeated below as the repeat-fire test) returns **zero matches** — the injected shape never appears in the transcript at all, not even filtered out downstream. The harness doesn't record it because it never gets past schema validation to be recorded.

Every attempt — the manual-`/compact` probe, the equivalent test against auto-triggered compaction, the subagent runs below, and a same-session repeat-fire test — failed the harness's own JSON schema validation with the identical error:

> Hook JSON output validation failed — (root): Invalid input... Expected schema: `hookSpecificOutput` accepts shapes for exactly five event types — `PreToolUse`, `UserPromptSubmit`, `PostToolUse`, `PostToolBatch`, `Stop`/`SubagentStop`.

`PreCompact` and `PostCompact` are absent from that list entirely. This is a hard, harness-level constraint, not an incidental payload mistake — no output shape from either event can inject `additionalContext` in this build. Confirmed independently three ways: the schema error itself; a probe asking whether the marker was visible (no, not via any legitimate channel); and a direct grep of the model's own transcript for the literal `"additionalContext":"SPIKE...` JSON shape (zero real matches — the only hits were the model's own investigation quoting the string back).

Where the marker *did* become visible each time was as literal text inside the validation-failure diagnostic itself, delivered via the same terminal-echo (`local-command-stdout`) channel as assumption 1's finding — not the intended delivery path. The repeat-fire test confirmed this is stable, not incidental: two `/compact` runs in the same session produced byte-for-byte identical failures, same schema dump, same marker-via-error-diagnostic-only visibility.

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

## What survives compaction — survival probe findings

Empirical results from seven scratch sessions that measured what a compaction keeps and what the transcript looks like when a `SessionStart` `compact` hook fires. The state-block script (`claude/.claude/scripts/compaction-context.py`) reads those transcript records, so its record-shape rules rest on this section.

- **Build:** `claude --version` **2.1.322**. Probe date 2026-11-11 (UTC). Re-verify by 2027-02-11, the three-month interval `docs/handoff-nudge.md` uses for vendor behavior. Builds from the 2.1.295 floor (`README.md`) up to 2.1.322 are unverified. Nothing raises this date when it lapses. The maintainer re-verifies by re-running the sessions below on the then-current build and updating this section.
- **Scope:** shapes, counts and booleans only. This section holds no transcript text, session id or home-rooted path. Sentinels are synthetic strings planted for the probe.

### Method

- Scratch git repository outside this repo, on a branch named `probe/scratch`, with a placeholder plan file and one scratch project skill with numbered steps, longer than the per-skill re-injection cap (`docs/handoff-nudge.md`).
- No `PreCompact` hook was registered, so every summary measured is the default one.
- Debug hooks beside the stowed ones:
  - `PostCompact` appended the stdin `compact_summary` to a log.
  - `SessionStart` with matcher `compact` logged its stdin and the transcript's line count. It emitted a 9,000-character `additionalContext` of 18 numbered segments mixing non-ASCII text, newlines, quotes and backticks, ending in a tail sentinel.
  - `UserPromptSubmit` emitted a short `additionalContext` only for a prompt holding HOOK-SENTINEL.
- Seven sessions:
  - M1 and M2 each compacted twice by a bare `/compact`. M1 also moved with `EnterWorktree` into a scratch worktree on a second branch with its own plan file.
  - LM compacted twice by bare `/compact` after a synthetic load of 16 prompts and large tool outputs sent through `claude -p`.
  - LA, LA2 and LA3 each compacted once, automatically, after the same kind of load. LA and LA2 compacted before most steps had run. LA3 ran every step but the HOOK-SENTINEL invocation.
  - INT interrupted one turn during a tool run and did not compact.
- Planted texts: EARLY-SENTINEL (first prompt); MID-SENTINEL (last line of a pasted paragraph of about 1,500 characters); ANSWER-SENTINEL and SELECTED-SENTINEL (a free-text `AskUserQuestion` answer and a selected option's label); BANG-SENTINEL (a `!` command's output); TAG-SENTINEL (a typed prompt opening with a tag-shaped token); REFERENT-ONE and REFERENT-TWO (two options that a typed "Yes for #1" chose between); HOOK-SENTINEL (a slash invocation's arguments); BG-SENTINEL (a background script's output).
- The probe's local logs spell the two referent sentinels with a digit suffix instead of the words ONE and TWO.
- A text counts as kept word for word when it appears in `compact_summary` as an exact substring once each side's whitespace runs collapse to one space. A mixed result across a sentinel's occurrences counts as lost.
- The scratch skill loaded through the `Skill` tool, a bare slash command and a slash command with arguments.

### Hook payloads and the transcript at hook time

- `SessionStart` `compact` fired nine times (M1 2, M2 2, LM 2, LA 1, LA2 1, LA3 1). Its payload has seven keys on both triggers: `cwd`, `hook_event_name`, `model`, `prompt_id`, `session_id`, `source`, `transcript_path`. `source` is `"compact"` and the payload carries no trigger. `PostCompact` carries `trigger`.
- In all nine firings `transcript_path` was a regular file that ended one line before that compaction's boundary record. The hook therefore reads every pre-compaction record and none of the compaction in progress: no boundary, summary or `/compact` record.
- M1's two payload paths named the worktree's project directory. Each was a regular file when its hook fired, and both were gone when these findings were written. A consumer must treat a missing path as "no transcript".
- Every main-thread record carried one `cwd` and `gitBranch` pair naming one checkout. The pair stayed the same across every compaction. In M1 it switched once, to the worktree's pair, after `EnterWorktree`.
- Around a manual compaction the records ran: boundary, `isCompactSummary` record, `isMeta` caveat, `/compact` record and its `<local-command-stdout>` record, then attachments, then the `SessionStart` hooks' result records. Around an automatic compaction: boundary, three attachments, summary, then attachments, with no command record.
- An automatic boundary carries `compactMetadata.preservedMessages` (`anchorUuid`, `uuids`). Its `uuids` named 6 records, the last user turn included. A manual boundary's named 3 to 5 and no user prompt. The automatic summary record also carries `summarizedTurnPromptUuids` and `turnOrigin`. No preserved record is written again after the boundary. Whether the model sees the preserved records again is not determinable from the files.

### Hook output

- The 9,000-character debug block (10,410 UTF-8 bytes) arrived with all 18 segments intact after all nine compactions, beside a second `SessionStart` `compact` hook's about 2,000-character block. No cap was observed below 9,000 characters.
- Earlier blocks are stored only in `attachment` records, request-blob records and later summaries.
- `UserPromptSubmit` fired on slash prompts. The HOOK-SENTINEL invocation's `additionalContext` was an `attachment` record written after the skill-load record.

### What the default summary keeps

- Lost scored prompts (EARLY, MID, load prompts):
  - M1's second summary omitted MID-SENTINEL.
  - M2's second summary held no planted sentinel except the debug block's tail sentinel.
  - Both LM summaries omitted 15 of 16 load prompts and represented the other by its sentinel only.
  - LA3's summary represented EARLY-SENTINEL and all four of its load prompts by sentinel only.
- Kept: LA and LA2, which compacted before most steps had run, kept EARLY-SENTINEL and their load prompts word for word.
- Answers and referents, over the seven summaries from sessions that sent them (two each from M1, M2 and LM, plus LA3's):
  - REFERENT-ONE and REFERENT-TWO appeared in five and were absent from M1's and M2's second summaries.
  - SELECTED-SENTINEL appeared in six and was absent from M2's second.
  - ANSWER-SENTINEL's answer text was kept word for word in four, represented by its sentinel only in two, and omitted from one.
  - REFERENT and SELECTED were scored by sentinel presence only, not word for word.
- Skill bodies: after each compaction the harness wrote an `invoked_skills` `attachment` record whose skill content was cut at 20,000 characters (the on-disk body is 32,532 characters) and whose path names no file on disk. It wrote no skill-load user record.

### Record shapes

- **Skill loads.** Every skill-load record was an `isMeta` user record with list-of-text content and a `promptId`, whose text opens "Base directory for this skill" and names the skill's directory, on all three invocation paths.
  - Slash path: the load record is always the next record and carries no `sourceToolUseID`. A bare slash invocation carries no `<command-args>` tag.
  - `Skill`-tool path: one `tool_result` record (`toolUseResult` keys `commandName`, `success`) sits between the call and the load, and the load carries a `sourceToolUseID`.
  - In LM, a `Skill`-tool call on a skill already loaded wrote no skill-load record. It wrote an `isMeta` string record carrying `sourceToolUseID` that does not open with the skill-load prefix.
- **Origins.** `origin.kind` was `"human"` on typed, slash and pasted records. Each task notification's `origin` held `kind` `"task-notification"`, `producer` and `runId`. BG-SENTINEL reached no record in any session.
- **No `origin`, by `entrypoint`.**
  - `cli`: the compact summary, the interrupt marker, skill-load and other `isMeta` records, built-in slash commands, records opening with `bash-input`, `bash-stdout` or `local-command-stdout` tags, and tool results.
  - `sdk-cli`: prompts sent through `claude -p`, and tool results.
  - Every `cli` prompt, typed, slash or pasted, carried `origin.kind` `"human"`.
  - The `sdk-cli` rows come from `claude -p` resumes of sessions whose harness records were written under `cli`. They cannot show what a headless-only session writes.
  - The state-block script admits a no-`origin` record only when `entrypoint` is `sdk-cli` and `promptSource` and `turnOrigin` are both `"sdk"`, the discriminators every recorded `claude -p` prompt carried (see "Loaded (`claude -p`) prompts" below). A plain-text `sdk-cli` record without both is excluded.
  - Census of main-thread user records across the seven transcripts, by shape:
    - `turnOrigin` `"human"` with no `promptSource`: the `!` stdout record (4 of 4) and the compaction summary (3 of 9, the other 6 carry neither field). So `turnOrigin` is a per-turn stamp, not a mark of an engineer prompt.
    - Neither field, on `cli`: the `!` input record (4), command record (7), local-command stdout (7), local-command caveat (7), interrupt marker (5), skill-load (19), other `isMeta` (1), tool result (29).
    - Both fields, on a harness-written shape: the task notification (4 of 4), with `promptSource` `"system"` and `turnOrigin` `"task_notification"`.
    - Typed `cli` prompts: 50 carry both, with `promptSource` `"typed"`. 14 carry `turnOrigin` only, and their shape was not checked.
    - `sdk-cli` prompts: 34 of 34 carry `promptSource` `"sdk"` and `turnOrigin` `"sdk"`. The 15 `sdk-cli` tool results carry neither.
  - The rule requires both fields, and `promptSource` `"sdk"` is the one a `cli` record never carried in this census.
  - Shapes the probe did not exercise: a scheduled or looped prompt; a prompt queued mid-turn; a resumed interactive session; hook feedback such as a `Stop` hook's block reason; an interrupt in a `claude -p` session; a `claude -p "/skill args"` invocation and its pairing with a skill-load record; a summary or skill-load record written in a headless session.
- **`AskUserQuestion` answers** (free-text and selected): a user record with one `tool_result` block, `toolUseResult` keys `annotations`, `answers` and `questions`, and no `origin`. Issue #1446 tracks restoring them.
- **Interrupt markers.** Five markers in two variants (four mid-stream, INT's during a tool run) were user records with list text content, a `promptId`, no `origin`, `entrypoint` `cli` and a top-level `interruptedMessageId` key. The tool-run variant's records ran: assistant `tool_use`, user `tool_result` carrying `toolUseResult`, an `attachment`, then the marker.
- **`!` records** opened with `<bash-input>` and `<bash-stdout>` tags and carried no `origin`.
- **Tag-opening typed prompt:** `origin.kind` `"human"`, text opening with a tag-shaped token.
- **Pasted prompt.** A multi-line paste is stored with `origin.kind` `"human"` and text that opens, after leading whitespace, with a `pasted_content` tag carrying an `id` attribute, closed by a tag with the same attribute. The MID record is one string of 1,572 characters including two leading newlines (1,569 stripped), recounted in all four pasted prompts. The closing tag is `</pasted_content id="...">`, repeating the attribute.
- **Loaded (`claude -p`) prompts:** `entrypoint` `sdk-cli`, no `origin`, `promptSource` `"sdk"`, `turnOrigin` `"sdk"`, plain text.
- `UserPromptSubmit` fire counts were not reconciled to prompt record counts.

### State-block script run on real records

`compaction-context.py state --transcript` was run over 62 cut copies of the seven transcripts. Sources of the cuts:
- Each of the nine compactions, cut at the line count the `SessionStart` hook logged (one line before that compaction's boundary).
- Each excluded record, cut just after it: 8 answer records, 8 `!` records, 5 interrupt markers (INT's tool-run marker included), 4 tag-opening prompts, 4 task notifications, and LM's `Skill`-tool call on an already-loaded skill.
- 6 bare slash invocations (cut just after the skill-load record) and 8 slash invocations with arguments (cut so the invocation is the newest prompt).
- 4 pasted MID prompts and 3 `claude -p` load prompts, each cut just after it.
- M1 on either side of `EnterWorktree`.

Every cut was copied with `head -n`, and none was rewritten. Results:

- **Pass:** in every cut the quoted prompts equal an independently built list of the last five admitted prompts, oldest first, and the newest prompt is the expected one.
  - The expected newest is the last admitted prompt before the compaction, or before the excluded record.
  - For a bare slash invocation it is the prompt before the invocation, with no `/NAME` line.
  - For a slash invocation with arguments it is the `/NAME <args>` text.
  - `claude -p` prompts are quoted.
- **Pass, absent from every output:**
  - ANSWER-SENTINEL, SELECTED-SENTINEL, BANG-SENTINEL, TAG-SENTINEL and BG-SENTINEL;
  - the interrupt markers' text, the tool-run variant's included;
  - a span of each task notification's envelope text;
  - the text of LM's already-loaded `Skill`-tool record (two testable spans only, because the record is short);
  - spans of the scratch skill's SKILL.md body (1,625 windows), of the skill-load records and of the `invoked_skills` attachment;
  - spans of the 15 `sdk-cli` tool-result records;
  - the `/compact` record, the `isCompactSummary` records' text and the debug block's tail sentinel.
  - HOOK-SENTINEL appears only in the two slash-with-arguments cuts that carry it.
- **Pass, pasted prompt:** the cut keeps the first 750 characters (the opening `pasted_content` wrapper with its `id`) and the last 750 (MID-SENTINEL and the closing tag), with an omitted count of 72, which is 1,572 minus 1,500.
- **Pass, skills:** the scratch skill appears exactly once, with a path under which a SKILL.md exists, in every cut taken at or after its first load, and in no earlier cut. All three invocation paths are covered. The skill-load first line uses a colon and a space after the prefix.
- **Pass, plan line:** it matches the script's constant word for word in all 62 cuts. In M1 it names the first plan file in the cut before the move and the second in the cut taken at the first main-thread record with the worktree's `cwd`, which is the assistant `tool_use` record.
- **Rules each Absent item pins:**
  - No `origin` on `cli`: answers, `!` records, interrupt markers, the already-loaded record, skill-load records, `/compact` and `isCompactSummary`.
  - `origin.kind` `"task-notification"`: task notifications.
  - Tag-opening exclusion, with `origin.kind` `"human"`: the tag-opening prompt.
  - Tool-result exclusion: the `sdk-cli` tool results.
- **Not exercised:** the answer, `!`-record and interrupt-key exclusions are not exercised on real no-`origin` `cli` records, because the no-`origin` exclusion drops each such record first. The script's unit tests cover them with `sdk-cli` copies.
- **Invocation:** `python3 -I compaction-context.py` exits 1 with `ModuleNotFoundError` for `transcript_analysis`, because `-I` removes the script's directory from `sys.path`. `transcript-analysis.py` imports its package the same way. Callers must run it without `-I`.
- **Admission and plan line:** the quoted `claude -p` load prompts pass the three-field no-`origin` rule, because the recorded records carry `entrypoint` `sdk-cli` with `promptSource` and `turnOrigin` both `"sdk"`. Every plan line names an absolute path that exists on disk, and the scratch checkout root passes the path allow-list.
- **Script attested:** git blob SHA beginning `9c62b1165a23` for the script alone (`git hash-object` prefix, shortened to stay under the repository's long-hex redaction detector). The `transcript_analysis` package was read from the working tree.
