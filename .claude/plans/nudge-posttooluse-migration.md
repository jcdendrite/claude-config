# Move the handoff nudge off Stop/UserPromptSubmit onto PostToolBatch

## Context

The handoff-nudge hook (`nudge-handoff-near-context-cap.sh`) can only ever
observe context growth at `Stop`/`UserPromptSubmit` — the two points where
control yields back to a human. Diagnosis from
`~/.claude/handoffs/context-cost-nudge-event-source-handoff.md` (§6,
[engineer-confirmed] as the root cause): during a long unyielded autonomous
stretch — verified there as one real session with 790 API calls where the
first yield came at call 769, context climbing 59k→554k with no hook event
possible in between — no threshold value can fire an event that never
occurs. The fix has to move the check to an event that fires *during* tool
work, not just at its edges.

This plan covers exactly three items, prioritized as: (1) move the nudge's
evaluation to an event that fires during autonomous tool-call stretches, (2)
make repeated dismissal carry consequences instead of staying advisory
forever, (3) replace `handoff-ratio`'s success metric, which currently
clears a bar that's met once in 27,667 turn transitions and so measures
almost nothing.

## Approach

**Concluded design:** register the hook on `PostToolBatch` (fires once per
resolved tool-call batch, immediately before the next model call) instead of
`Stop`/`UserPromptSubmit`, keep a cheap `Stop` registration alongside it as a
safety net for toolless final turns, replace the per-fire 200-line transcript
scan with an incremental byte-offset read so the ~20x fire-rate increase
doesn't multiply cost, add a per-session ignored-re-arm counter that
escalates the nudge from advisory to a hard block via `PostToolBatch`'s
native `exit 2` loop-stop once a session has ignored enough of them, and
replace `handoff-ratio`'s handoff-vs-compaction formula with a
dollars-spent-above-threshold metric built on machinery
(`_hook_effective_fire_threshold`, `_extract_rearm_session_turns`) that
already exists in `transcript-analysis.py` for the `rearm-backtest`
subcommand.

**Why `PostToolBatch` over the engineer-confirmed `PostToolUse`.** The prior
session's handoff recorded "I like the idea of PostToolUse-gated nudges"
[engineer-confirmed] and assumed `PostToolUse`'s only cost was its ~28k/1,300x
fire-rate multiplier, to be solved with "a cheaper check or a sampling rule."
Verifying against the official hooks reference
(code.claude.com/docs/en/hooks, fetched this session) surfaced a event the
prior session didn't know about: `PostToolBatch` — "After a full batch of
parallel tool calls resolves, before the next model call" — which, unlike
`PostToolUse`, natively supports blocking (`exit 2` "stops the agentic loop
before the next model call," vs. `PostToolUse`'s exit 2 which only "shows
stderr to Claude; the tool already ran"). I checked whether `PostToolBatch`
would also cut the fire-rate problem by counting this repo's own transcript
corpus directly (1,421 session files, 239,944 records under
`~/.claude/projects/-Users-jared-MyCode-claude-config`): 25,711 individual
`tool_use` blocks vs. 25,709 tool-bearing assistant turns — parallel tool-call batching is close to
nonexistent in this workflow, so `PostToolBatch` and `PostToolUse` fire at
statistically the same rate here. `PostToolBatch`'s advantage is entirely its
native blocking, not frequency — which is exactly what item 2 (binding)
needs, without bolting a second `PreToolUse` gate onto a different event the
way an all-`PostToolUse` design would require. This was surfaced to the
engineer as a re-scope of their stated preference, per CLAUDE.md's
"prescribed approach is a hypothesis" guidance, and they chose `PostToolBatch`
after seeing the tradeoff (thin docs, unprecedented event in this repo, vs.
one mechanism instead of two).

**Root problem:** the nudge can't observe or act on context growth during
autonomous tool-call stretches, only at human-interaction boundaries.

**Givens** (conditions this plan treats as fixed, each with a reason):
- The 360,000-token absolute cap and 80,000-token re-arm spacing stay as-is
  — [engineer-verified: "Threshold *value* itself is secondary/out of scope
  for redesign" from the scoping brief]. Retuning them is a `rearm-backtest`
  exercise, not a design change this plan makes.
- `PostToolBatch`'s existence and its documented firing/blocking semantics
  are taken from Anthropic's own hooks reference, not derivable from this
  repo — another party (Anthropic) owns the contract.
- The four token-usage fields summed for `ESTIMATE` (`cache_read_input_tokens`,
  `cache_creation_input_tokens`, `input_tokens`, `output_tokens`) and the
  model→window resolution table are unchanged — out of this plan's scope by
  the engineer's own prior scoping (§6 "Deferred by explicit decision" in the
  handoff), not something this plan could fix without touching the
  unattributed-context-growth investigation explicitly deferred there.

**Per-mechanism assumption ledger:**

| # | Mechanism | Justification | Tag |
|---|---|---|---|
| 1 | Register hook on `PostToolBatch` | Only event in Anthropic's hook contract that both fires during autonomous tool-call stretches and supports native blocking; anchors: root | [verified: code.claude.com/docs/en/hooks, fetched this session — "After a full batch of parallel tool calls resolves, before the next model call"; blocking table row "PostToolBatch \| Yes \| Stops the agentic loop before the next model call"] |
| 2 | Keep `Stop` registration alongside `PostToolBatch` | `PostToolBatch` only fires when a turn contains at least one tool call; a toolless final turn (plain-text answer, no tool use) would otherwise go unobserved, reproducing the exact gap `docs/handoff-nudge.md:5` names for adding `Stop` originally; anchors: row1 | [verified: PostToolBatch's own firing description names "tool calls resolve" as the trigger condition — a turn with zero tool calls has nothing to resolve] |
| 3 | Drop `UserPromptSubmit` registration | Its coverage (check state as of the prior assistant turn, right before a new user message is processed) is now redundant: `Stop` already checked that same state at the end of the prior turn, and `PostToolBatch` checks far more granularly during any tool-bearing turn; anchors: row1 | [assumed — not backtested pre-cutover; flagged as a residual risk in Verification] |
| 4 | Incremental byte-offset read replaces the fixed `tail -n 200 \| jq -s` scan | At `PostToolBatch` frequency (~20x `Stop`/`UserPromptSubmit`), the fixed-window scan's measured ~0.3s/call cost multiplies proportionally; reading only bytes appended since the last check keeps marginal per-fire cost near-zero without skipping any fire (unlike a sampling/debounce rule, chosen against by the engineer for that reason); anchors: row1 | [engineer-verified: "Incremental read via cached byte offset" chosen explicitly over "Time/count-based debounce" this session] |
| 5 | Escalation counter (advisory → hard block after N ignored re-arms) | Directly answers "make the nudge binding" (item 2) using `PostToolBatch`'s native block instead of inventing a second gate; a first-crossing hard block was rejected as too large a UX regression from today's fully-advisory nudge; anchors: row1, item 2 | [engineer-verified: "Escalation ladder" chosen explicitly over "Block on first threshold crossing" and "defer binding" this session] |
| 6 | `handoff-ratio` replaced (not augmented) by a dollars-above-threshold metric, reusing `_hook_effective_fire_threshold`/`_extract_rearm_session_turns` | The existing formula's bar (both a handoff AND compaction event in the same session) clears in 1 of 27,667 turn transitions per the handoff's diagnosis — not a meaningful secondary signal worth carrying forward alongside a real one; anchors: item 3 | [engineer-verified: "Fully replace" chosen explicitly over "keep both" this session; formula source: `cmd_handoff_ratio`, `transcript-analysis.py:8806-8898`] |

**Two lighter primitives considered and set aside** (over-powered-primitive
check, since `PostToolBatch` blocking is a materially more invasive
mechanism than anything in this repo's hooks today):
1. *A companion `PreToolUse` gate, keyed on tool-call count since last
   `Stop`.* This is the mechanism the all-`PostToolUse` design would have
   needed for binding. Rejected: it requires threading escalation state
   between two separately-registered hook scripts on two different events,
   doubling the surface that has to agree on session state, where
   `PostToolBatch`'s own native block does the same job on one event with
   one script.
2. *`Stop`-hook blocking* (the existing `advance-past-commit-stall.sh`
   pattern: block with a reason that forces the agent to keep working).
   Rejected: `Stop`'s block semantics force *continuation*, which is the
   opposite of what an over-threshold session needs — the nudge wants to
   *interrupt* an autonomous stretch, not extend it, so a mechanism whose
   entire contract is "make the agent do more before it's allowed to stop"
   is the wrong shape regardless of frequency.

**Design tension left open, flagged for implementation-time verification.**
The official docs page has no dedicated `PostToolBatch` section — no worked
input/output JSON example, and one summarized fetch pass returned an
unconfirmed, inference-flagged claim that `PostToolBatch` "does not receive
`permission_mode`" (not a verbatim-quoted fact — the source note it cited was
a generic "not all events receive this field" disclaimer, not a
`PostToolBatch`-specific statement). This is unresolved and load-bearing: the
hook's plan-mode gate (`permission_mode == "plan"` → exit 0) and the
`decision`/`reason` vs. `hookSpecificOutput.permissionDecision` block-output
shape both depend on fields the docs don't conclusively confirm for this
event. **Critical files** below opens with a smoke-test step to resolve this
against the real payload before writing the real hook logic — do not port
the existing field-read/output-emit code by assumption. The smoke-test
sub-step's outcome must also decide the plan-mode/subagent fail-safe
direction, not just the field-presence question: if `permission_mode` or
`agent_type` turn out absent from `PostToolBatch` payloads, treat that as
"plan mode" / "subagent" respectively (i.e. don't fire) rather than falling
through to "not plan mode" / "not a subagent" — silence on an unverified
field is the safer failure than nudging (or worse, hard-blocking) inside
plan mode or a subagent.

**Unresolved cross-version risk, verification required before merge.**
`settings.json` is a single git-tracked file shared by every stow consumer
on `git pull`, with no version gate. Neither the hooks reference nor the
CLI changelog (both fetched this session) states how an older Claude Code
CLI treats an unrecognized top-level hook-event key — silently ignored, or
a hard parse failure that could break *other*, unrelated hooks for that
contributor. One data point found, not conclusive either way: the
changelog's v2.1.232 entry describes Desktop's config overlay as "validated
at boot against Desktop's own schema; unknown or invalid keys fail boot" —
a different config surface (Desktop overlay, not `.claude/settings.json`
hooks), but evidence that "unknown keys are silently tolerated" is not a
safe universal assumption across this CLI's config surfaces. **Before
merge**: confirm on the oldest Claude Code CLI version reasonably available
(a contributor's own older pinned install, or `npm view
@anthropic-ai/claude-code versions` cross-referenced with what any
contributor is actually running) that a `PostToolBatch` key in `settings.json`
does not break settings-loading for that CLI, and state the result in the PR
description. This plan does not get to pre-decide that skipping the check is
an acceptable risk: if no older CLI is available to test against by the time
`/ready-for-review` runs, the PR description must say so explicitly (not
silently omit the check) so the engineer makes that call at merge time with
the gap named, rather than the plan asserting "bounded and safe" on their
behalf. "Required landing order" above is exactly the silent-failure shape
this plan's own diagnosis warns about — an untested compatibility assumption
here would be the same class of failure, so "surely it fails loud" is not
load-bearing without the actual test.

## Critical files

1. **`claude/.claude/hooks/nudge-handoff-near-context-cap.sh`** (primary
   change)
   - **Required landing order**: this file's `PostToolBatch`-handling code
     must land in the same commit as (or strictly before) settings.json's
     `PostToolBatch` registration below — never after. If the registration
     lands first, the script's existing event-name fallback (`case
     "$HOOK_EVENT" in UserPromptSubmit|Stop) ;; *) HOOK_EVENT="UserPromptSubmit"
     ;; esac`, `:357-360`) silently relabels every `PostToolBatch` fire as
     `UserPromptSubmit` and runs the *old* full 200-line scan at
     `PostToolBatch`'s ~20x rate — reproducing, silently and mislabeled in
     the log, the exact cost blowup this plan exists to prevent. Land both
     files in one PR/commit to make the ordering question moot.
   - **New hook-class**: the file's header currently declares `# hook-class:
     informational` ("fires PostToolUse/SessionStart/etc. and never
     denies," per this repo's `claude-hook-review` conventions) — the
     escalation ladder below contradicts that class by design. Change the
     header to a new class, e.g. `# hook-class: batch-gate`, documenting its
     emission contract (`PostToolBatch`'s native block, shape pinned by the
     smoke-test sub-step below) as distinct from `gate` (`PreToolUse` deny)
     and `turn-gate` (`Stop` block-to-force-continuation). `claude/.claude/hooks/tests/test_hook_alignment.py`'s
     `GATE_HOOKS`-driven auto-parametrization does not cover this new class
     (mirrors the existing `turn-gate` exclusion) — hand-written jq-absent
     and dependency-missing cases must assert silent-allow (exit 0, no
     block), not a hard stop, matching every other class's fail-open posture
     under those conditions.
   - **Smoke-test sub-step first**: temporarily register a throwaway script
     under `PostToolBatch` in this worktree's `claude/.claude/settings.json`
     that dumps its raw stdin to a scratch file and exits 0; trigger a couple
     of real tool-call batches in a live session; inspect the captured
     payload to confirm the exact field set (`session_id`, `transcript_path`,
     `permission_mode`, `agent_type` presence, `cwd`, `tool_use_id`) and, if
     documentable, the actual block-output contract (`decision`/`reason` vs.
     something else) by testing a hook that returns each candidate shape and
     observing whether the loop actually halts. The live session used for
     this must have its CWD inside this worktree — a project-level
     `.claude/settings.json` only merges for sessions rooted there. Revert
     the throwaway registration before the real implementation lands.
   - New: `HOOK_EVENT` case arm accepts `PostToolBatch` alongside `Stop`
     (drop `UserPromptSubmit` per row 3 above) — mirrors the existing
     `case "$HOOK_EVENT" in UserPromptSubmit|Stop) ;; ...)` allowlist at
     `:357-360`.
   - **Move `MARKER_DIR="$CONFIG_DIR/.handoff-nudge-fired.d"` and its
     `mkdir -p` (currently `:395`) to before the `read_latest_usage()` call
     (currently `:390`)** — the incremental-read helper below needs
     `MARKER_DIR` to locate its `-scan` state file, but the call site that
     needs it currently runs before the variable is assigned.
   - New: incremental-read helper replacing the unconditional
     `_lib_capped_for 2 tail -n 200 "$transcript_path" | jq -s ...` in
     `read_latest_usage()` (`:158-181`). Persist a 3-line state file per
     session (`${MARKER_DIR}/${SESSION_ID}-scan`: byte offset, last-known
     estimate, last-known model) alongside the existing `FIRED_MARKER`/
     `DRIFT_MARKER` convention (`:395,400`) — reuse `MARKER_DIR`, don't
     invent a second state directory. On a fire: get the transcript's
     current size via `wc -c < "$transcript_path"` (not `stat -c%s`/`stat
     -f%z` — this file's own BSD/GNU portability, and this session runs on
     Darwin); read only bytes appended since the stored offset via
     `_lib_capped_for 2 tail -c +$((offset+1)) "$transcript_path" | jq -s
     ...`, matching every other `tail`/`jq` call in this file's existing
     `_lib_capped_for` convention rather than an unwrapped call. If a fresh
     assistant-usage record is found in that slice, update the cached
     estimate/model/offset; if not, reuse the cached estimate without
     re-scanning. First fire in a session (no `-scan` file yet) falls back
     to today's full 200-line scan to bootstrap, then writes the state file.
     A stored offset larger than the current file size (rotated/truncated
     transcript — should not happen in practice, but the malformed-value
     guards elsewhere in this file already treat "should not happen" inputs
     as real cases) resets to a fresh bootstrap scan rather than erroring.
     Only advance the stored offset to the position *after the last
     complete newline actually read*, not to raw EOF — a trailing
     partially-written line must be left for the next fire to pick up whole,
     mirroring how `jq -s` already silently drops unparseable input via
     `2>/dev/null` today.
   - **Incidental fix** (Axis 1 bucket 2 — small, non-cosmetic, same file
     already in scope): the fire-path field-extraction `jq -r` at `:349` is
     unwrapped, unlike every other `jq`/`tail` call in this file. Wrap it in
     `_lib_capped_for` for consistency while this file is open for the
     `PostToolBatch` changes above.
   - **Cleanup-cadence footprint**: unlike `FIRED_MARKER`/`DRIFT_MARKER`
     (written only inside the rare "fire" block), the new `-scan` file
     writes on every `PostToolBatch` invocation for every session that
     makes any tool call — a structural shift in `MARKER_DIR`'s steady-state
     file count from O(sessions-that-fired) to O(all-sessions-with-tool-calls).
     The existing 30-day sweep (`find "$MARKER_DIR" -maxdepth 1 -mtime +30
     -delete`, `:450`) only runs inside that same rare fire block, so it
     would no longer bound `-scan` file accumulation the way it bounds
     `FIRED_MARKER`/`DRIFT_MARKER` today. Move the sweep to run
     unconditionally near the top of the fire path (after `MARKER_DIR` is
     assigned per the reordering above), not only inside the emit-on-fire
     block, so it bounds all four marker-file types the same way.
   - New: escalation counter. On each fire past the first (i.e., every
     re-arm), increment `${MARKER_DIR}/${SESSION_ID}-ignored`. When that
     count reaches a new `HANDOFF_NUDGE_BLOCK_AFTER` (default: a small
     constant — no corpus-backtested value exists yet for this new
     mechanism; ship conservative and revisit via an extended
     `rearm-backtest` once real fire data exists, mirroring how the existing
     360000/80000 constants were themselves tuned after initial ship), emit
     the blocking output instead of the advisory one — exact JSON shape
     pinned by the smoke-test sub-step above. Malformed-value guard for the
     new env var override follows the exact `case` pattern already used for
     `HANDOFF_NUDGE_ABS_CAP`/`HANDOFF_NUDGE_REARM_SPACING` (`:134-137,147-150`).
   - Unchanged: subagent gate (`AGENT_TYPE` non-empty → exit, `:376-378`),
     kill-switch (`:371-373`), schema-drift detection (`:399-407`),
     `--check` mode (unaffected — it's invoked by hand, never
     hook-registered, per the file's own header comment `:31-33`).

2. **`claude/.claude/settings.json`**
   - Remove the `UserPromptSubmit` entry for `nudge-handoff-near-context-cap.sh`
     (`:143-149`, leaving the sibling nudge hooks in that block untouched).
   - Keep the `Stop` entry (`:174-181`) as-is.
   - Add a new top-level `PostToolBatch` key (does not exist today — confirmed
     via `jq '.hooks|keys'`, current keys are `Notification`, `PostToolUse`,
     `PreToolUse`, `SessionStart`, `Stop`, `SubagentStart`,
     `UserPromptSubmit`) with no `matcher` field, matching the format of
     other no-matcher-support events already in this file (e.g. the
     `UserPromptSubmit` block itself has no `matcher` key).

3. **`claude/.claude/skills/handoff/SKILL.md`** (small addition)
   - The existing "After writing: record the conversion signal" step
     (`:149-164`) already appends a `handoff session=<id>` log line on
     conversion, using its own locally-computed `$CONFIG_DIR`/`$SESSION_ID`
     (`:157-158`) — this recipe has no `$MARKER_DIR` variable (that name is
     internal to the hook script's own bash context, a separate process).
     Add one line to also remove the literal path
     `$CONFIG_DIR/.handoff-nudge-fired.d/$SESSION_ID-ignored` at the same
     point, so a successful handoff resets the escalation ladder for that
     session rather than leaving a stale ignored-count that would immediately
     re-trigger a block on the *next* session's re-arm if the session id
     were ever reused (defense-in-depth; session ids aren't reused in
     practice, but the reset is a one-line no-regret addition at an
     existing touchpoint). Per `.claude/rules/skill-and-agent-self-review.md`,
     this edit requires `/skill-review` before commit.

4. **`claude/.claude/scripts/transcript-analysis.py`**
   - Rename `cmd_handoff_ratio` (`:8806-8898`) and its CLI registration
     (`:11038-11052`) from `handoff-ratio` to `spend-over-threshold` — the
     existing name would be actively misleading once the formula no longer
     measures a ratio of handoffs to anything. This is a breaking CLI rename;
     call it out explicitly in the PR description.
   - New formula, built from existing per-turn machinery rather than new
     pricing logic: for each session, call `_extract_rearm_session_turns`
     (`:9905-9975`, already computes `(context_at_turn, output_tokens,
     actual_dollars)` per main-thread turn plus `session_threshold` via
     `_hook_effective_fire_threshold`, `:9850-9859`) and sum `actual_dollars`
     for turns where `context_at_turn >= session_threshold`, against the
     session's total `actual_dollars`. A session where `session_threshold
     is None` (no main-thread turn carries a usage block) is excluded from
     the report entirely — it has no threshold to be above or below, same
     as `cmd_handoff_ratio`'s own `if not (session_has_handoff or
     session_has_compaction): continue` precedent for skipping
     uninformative sessions. A session with `total_dollars == 0` (every
     turn unpriced) is also excluded rather than reported as an undefined
     0/0 share. Bucket by ISO week exactly like today's table (reuse the
     `_parse_ts`/`isocalendar()` pattern at `:8868-8869`), report `share =
     above_threshold_dollars / total_dollars` per week and as a total row.
     Keep `_print_nudge_log_diagnostic()`
     (`:8898`) — the schema-drift footer is still useful and orthogonal to
     the ratio formula being replaced.
   - Reuse, don't reimplement: `_price_turn` (`:5124`), `_HANDOFF_NUDGE_ABS_CAP`
     (`:9797`), `_HANDOFF_NUDGE_PCT_THRESHOLD` (`:9805`) are all already
     shared with `rearm-backtest` — the new metric adds no new pricing
     constants.

5. **`docs/handoff-nudge.md`**
   - `## What the hook does` (`:3`): update to describe `PostToolBatch` +
     `Stop` registration instead of `UserPromptSubmit` + `Stop`, and add the
     escalation-ladder behavior (new "Why this block-after count" subsection,
     mirroring the existing "Why this cap"/"Why this spacing" prose style —
     ground it once a real default is chosen, per the ledger row 5 above).
     Add a "Recovering from a hard block" note alongside it: the existing
     kill-switch (`touch ~/.claude/.handoff-nudge-disabled`) suppresses
     future fires including the block, and removing
     `~/.claude/.handoff-nudge-fired.d/<session-id>-ignored` resets that
     session's escalation count without disabling the nudge globally.
   - `## How to read handoff-ratio output` (`:82-102`): rename section and
     update to describe the new `spend-over-threshold` output shape.
   - `## Known limitations` (`:104`): add the `PostToolBatch` documentation
     thinness as a named limitation (mirrors the file's existing practice of
     naming its own soft spots, e.g. "Model→window table is hardcoded and
     dated").

## Verification

- **Existing suite must stay green**: `../../../.venv/bin/pytest
  claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` from
  this worktree.
- **`claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`**:
  add `PostToolBatch` to `HOOK_EVENT_NAMES` (`:42`) and to the payload
  builder (`_base_payload`, `:185-194`); confirm the marker-cleanup test
  fixture is generalized to every file matching `${MARKER_DIR}/${SESSION_ID}*`
  rather than enumerating the two current filenames — the new `-scan`/
  `-ignored` files need the same teardown the existing `FIRED_MARKER`/
  `DRIFT_MARKER` pair gets, or stale state leaks across test cases. Add
  cases for:
  - **Incremental-read correctness**: a transcript grown (not rewritten)
    between two hook invocations — assert the stored offset after the
    second fire equals the transcript's byte length at fire time minus any
    trailing partial line, and assert `ESTIMATE` is unchanged when the
    appended delta contains no new usage block.
  - **Incremental-read edge cases**: (a) a fire mid-write where the
    trailing appended bytes are an incomplete JSON line — assert the
    offset stops before it and the next fire picks up the completed line
    whole, not duplicated or dropped; (b) a stored `-scan` offset larger
    than the transcript's current size (simulating rotation/truncation) —
    assert it falls back to a fresh bootstrap scan rather than erroring;
    (c) no `-scan` file present — assert the bootstrap path runs and writes
    a correctly-shaped 3-line state file.
  - **Unrecognized-event fallback**: a payload with a `HOOK_EVENT` value
    that isn't `Stop`/`PostToolBatch` (simulating the exact landing-order
    failure mode named above) falls back to `UserPromptSubmit` and takes the
    full 200-line-scan path — this pins the described bug class as a test,
    not just avoids triggering it via commit discipline.
  - **Unconditional sweep**: invoking the hook with no threshold crossing
    (a fire that doesn't emit the nudge) still runs the marker-directory
    30-day sweep and removes stale marker files — asserts the "Cleanup-cadence
    footprint" behavior change (sweep moved out of the emit-on-fire block)
    actually took effect, not just that test teardown was generalized.
  - **Escalation counter concurrency**: two overlapping increments against
    the same session's `-ignored` file (simulating near-simultaneous
    `PostToolBatch` fires) — assert no lost update. If the harness in fact
    guarantees serialized `PostToolBatch` dispatch per session (verify this
    against the hooks reference or empirically before writing the test),
    document that guarantee inline and this case may be a no-op assertion
    rather than a real race test; if no such guarantee exists, the
    increment itself must become atomic (temp-file-then-rename, mirroring
    this file's existing marker-write idiom but closing the read-modify-write
    gap) before the test can pass honestly.
  - **Escalation counter ladder**: increments across re-arms; resets on a
    `-ignored` marker removal; blocking output emitted once
    `HANDOFF_NUDGE_BLOCK_AFTER` is reached, using whatever JSON shape the
    smoke-test sub-step confirms.
  - **Gate correctness under `PostToolBatch`**: subagent gate and plan-mode
    gate still correctly filter under `PostToolBatch`-shaped payloads,
    including the fail-safe direction (absent field ⇒ treat as
    plan-mode/subagent) if the smoke-test finds either field absent.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**: replace
  `TestHandoffRatio` (`:11471-11526`) with tests against
  `cmd_spend_over_threshold` covering: a session entirely under threshold
  (0% share), a session entirely over (100% share), a mixed session, a
  session where `session_threshold is None` (no main-thread turn carries a
  usage block, per `_extract_rearm_session_turns`'s documented behavior —
  assert the formula's chosen handling, e.g. excluded from the report,
  rather than whatever `context_at_turn >= None` happens to evaluate to), a
  session where `total_dollars == 0` (every turn unpriced — assert a
  deliberate share output, not a `ZeroDivisionError`), and the existing
  shared header-consistency row (`:15828`) updated to the new function/CLI
  name.
- **Manual end-to-end check**: after the real hook lands, run a live session
  that crosses the 360,000-token threshold via ordinary tool use (no
  `Stop`/`UserPromptSubmit` in between) and confirm the nudge fires from
  `PostToolBatch` alone — this is the actual regression the diagnosis found,
  so a real crossing is the only check that closes the loop the unit tests
  can't (they exercise the logic against synthetic transcripts, not a real
  agentic loop's actual hook dispatch).
- **Residual risk from dropping `UserPromptSubmit`** (ledger row 3,
  `[assumed]`): watch `~/.claude/.handoff-nudge.log` for a session-share drop
  in `nudged` lines after this ships, compared to the pre-change baseline —
  if toolless-turn-heavy sessions (e.g. planning-only or discussion-only
  sessions) turn out to be a meaningful share of nudge-worthy sessions, this
  assumption was wrong and `UserPromptSubmit` should be restored alongside
  `PostToolBatch` rather than dropped.

## Out of scope

- Retuning the 360,000-token cap or 80,000-token re-arm spacing values
  themselves — `rearm-backtest` already exists for that and this plan
  doesn't touch it.
- The three items explicitly deferred in the handoff's §6 ("Deferred by
  explicit decision"): unattributed context growth, the ~52k session-start
  floor, subagent call volume — none of this plan's changes bear on them.
- A two-tier informational-then-hard nudge design was already considered and
  deferred in `docs/handoff-nudge.md`'s own "Known limitations" before this
  plan existed; the escalation ladder here is that deferred idea, scoped now
  because item 2 specifically asked for binding.
