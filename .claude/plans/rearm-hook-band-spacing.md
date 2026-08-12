# Re-arm the handoff-nudge hook at escalating bands

## Context

Phase 3 of `.claude/plans/token-cost-reduction.md`: the handoff-nudge hook
(`claude/.claude/hooks/nudge-handoff-near-context-cap.sh`) currently fires its
context-cap reminder **once per session** — a marker file's mere existence
suppresses every later check, so a session that keeps running past the
threshold gets no further warning. `docs/handoff-nudge.md`'s own "Known
limitations" section already names the consequence: "the unwarned tail is now
materially worse" since the absolute-cap change (PR referenced there) moved
the single shot earlier, leaving more runway unwarned afterward. Evidence:
PR #609 ran to 551K tokens after its one fire; four other sessions in the
current log ran 76K-161K past threshold with no further nudge. The fix is to
replace the one-shot suppression with re-arming at a fixed token-spacing past
each fire, so a session that keeps growing keeps getting reminded. `ABS_CAP`
(the fire point, `360000`) is not touched — this phase changes only what
happens *after* the first fire.

Why now: Phase 2 (PR #630, merged `398b7fe`) built the backtest tool this
phase needed and was explicit that nothing ships before it runs against the
real corpus. This session ran it for the first time:

```
python3 ~/.claude/scripts/transcript-analysis.py rearm-backtest --this-repo
```

```
REARM BACKTEST SOURCES (this repo (33 project dirs); 1 root)
Sessions in scope: 127 (19 unpriced turns / 0 tokens excluded)
Operator-response-lag sample: 39 joined 'nudged' log line(s), median lag 30,624 tokens past the fire point

   Spacing        Model              $   DeltaUSD      C_bar    DeltaCbar
-------------------------------------------------------------------------
  baseline       actual       2,542.81         --    274,334           --
    40,000      perfect       2,213.13    -329.68    232,859      -41,475
    40,000    realistic       2,281.00    -261.81    240,769      -33,565
    80,000      perfect       2,211.88    -330.93    233,723      -40,611
    80,000    realistic       2,283.05    -259.76    241,602      -32,732
   120,000      perfect       2,222.88    -319.93    235,210      -39,124
   120,000    realistic       2,288.85    -253.96    242,645      -31,690
```
`[verified: rearm-backtest --this-repo, run this session against the real corpus]`

## Approach

**Spacing: 80,000 tokens.** Under realistic (lagged) compliance, 40,000 and
80,000 are statistically indistinguishable on both dollar delta (-261.81 vs
-259.76) and mean-context delta (-33,565 vs -32,732) — a difference smaller
than the noise in a 127-session sample. 120,000 is measurably worse on both
axes and is dropped. Between the two near-tied candidates, 80,000 is chosen
over 40,000 on the one dimension the backtest doesn't model: the measured
median operator-response lag is 30,624 tokens past a fire. A 40,000 spacing
is barely larger than that lag, so under realistic behavior the second nudge
would frequently arrive just as (or before) the operator has acted on the
first one — reproducing the dismissal-as-noise risk `docs/handoff-nudge.md`'s
"Known limitations" already flags for over-frequent firing. 80,000 gives
~2.6x the median lag as separation while costing nothing measurable in the
backtest. `[verified: rearm-backtest output above; docs/handoff-nudge.md
"Known limitations" dismissal-risk bullet]`

**Record shape: overwrite one file with the triggering estimate, not a
directory of per-band files.** The existing `FIRED_MARKER` path
(`$MARKER_DIR/$SESSION_ID`) already exists and is already swept by the
hook's own 30-day mtime sweep (`nudge-handoff-near-context-cap.sh:420`) and
covered by `_lib_valid_session_id_component`'s traversal guard. Re-arming
only needs that same file to hold a value instead of being a zero-byte
touch: `LAST_FIRED_AT` (the `ESTIMATE` that triggered the most recent fire).
A later check fires again once `ESTIMATE >= LAST_FIRED_AT + REARM_SPACING`,
and overwrites the file with the new `ESTIMATE`. This is the plan's own
prescribed shape (`token-cost-reduction.md:327`) — one file per band would
multiply per-session files and turn an integer compare into an enumeration
for no benefit, since the comparison only ever needs the most recent value.

**Read with shell builtins, not a `jq` subprocess.** A single-line integer
read is `IFS= read -r LAST_FIRED_AT < "$FIRED_MARKER"` — no subprocess, no
latency-budget interaction with the existing `timeout 2` wrappers around the
hook's `jq` calls. A corrupt or non-numeric value is treated as "no prior
fire" (empty `LAST_FIRED_AT`), which forces a fire: this mirrors the hook's
existing write-failure posture (fail toward firing, never toward silently
suppressing) rather than its read-failure posture elsewhere (fail toward
`cannot-resolve`/exit-0), because a stuck-suppressed nudge is the one failure
mode this hook's own header comment rules out ("Nudge is one-shot per
session" was a feature, wrongly staying suppressed forever is not).

**Spacing is overridable, mirroring `HANDOFF_NUDGE_ABS_CAP`.**
`docs/handoff-nudge.md`'s existing rationale for `ABS_CAP` being overridable
— "for a consumer whose session-shape differs from the curve this value was
read off" — applies identically to a value read off this session's own
account corpus. `HANDOFF_NUDGE_REARM_SPACING` reuses `compute_threshold`'s
exact validation `case` pattern (empty / non-digit / zero-padded / 9+-digit
falls back to the default) so a malformed override can't degrade spacing
toward 0 (which would fire on every turn) or wrap negative.

**No `decision` key, ever.** The hook's `hookSpecificOutput` object carries
only `hookEventName` and `additionalContext` today. Re-arming must not tempt
adding a blocking final band on repeated dismissal — the hook is
`# hook-class: informational` and registered on `Stop`; a `decision` key on
a `Stop` path can block. This plan does not add one; called out explicitly
because the plan's own drafting note (`token-cost-reduction.md:327`) flags it
as the likely regression this class of change invites.

**Alternatives considered and set aside:**
- *One marker file per band crossed* — rejected above (record-shape
  rationale); no reader ever needs more than the latest value.
- *Track a band index (0, 1, 2, ...) instead of the raw estimate* — rejected:
  an index requires the reader to also know the original fire's estimate to
  reconstruct the next threshold, so it's strictly more state for no benefit
  over storing the estimate directly.
- *Widen `--check`'s JSON contract to report the next re-arm point* —
  deferred; nothing consumes `--check` today for re-arm status, and
  `docs/handoff-nudge.md`'s JSON contract section is itself a change surface
  this phase doesn't need to touch. See Out of scope.

### Assumption ledger

**Root problem:** the one-shot marker leaves the entire post-first-fire tail
of a long session unwarned, which is the defect
`docs/handoff-nudge.md:103` already names and which PR #609 (551K tokens
after one fire) demonstrates concretely.

**Givens:**
- `ABS_CAP` stays at `360000` — fixed by `token-cost-reduction.md`'s Phase 3
  instruction, itself resting on PR #605's U-shape analysis; this plan does
  not reopen it. `[engineer-verified]` (parent plan's own given, restated)

**Mechanisms:**
- *Overwrite the existing per-session marker file with the triggering
  estimate; compare `ESTIMATE >= LAST_FIRED_AT + REARM_SPACING` on each
  later check* — `anchors: root`. Lighter primitives considered and
  rejected: (a) leave the marker as a zero-byte touch and re-derive spacing
  from the log file instead — rejected, the log is append-only across every
  session ever, so extracting "this session's last fire" needs a filtered
  scan on every check where the marker file is an O(1) point read; (b) a
  second marker directory keyed by band number — rejected under Record
  shape above, multiplies files for a value obtainable as an arithmetic
  compare on one file.

## Critical files

- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — the only
  behavioral change:
  - Header comment (lines 3, 12-14): update the "one-shot per session"
    description to describe re-arming at `REARM_SPACING`-token bands past
    the first fire.
  - `compute_threshold()` (lines 129-136): add a sibling
    `resolve_rearm_spacing()` using the identical validation `case` as the
    existing `ABS_CAP` arm (lines 131-134), default `80000`, override var
    `HANDOFF_NUDGE_REARM_SPACING`. **Reuse the existing pattern verbatim**
    rather than writing a new one — same failure modes (empty, non-digit,
    zero-padded, oversized) apply.
  - Fire-path already-fired gate (lines 401-405 today): replace the bare
    existence check with a read-and-compare. **The marker must be written
    with a trailing newline** — `read` returns non-zero on EOF without a
    delimiter even though it populates the variable, so a no-newline write
    (the naive `printf '%s'`) makes every subsequent `read ... || LAST_FIRED_AT=""`
    unconditionally discard the value it just read, permanently defeating
    re-arming (verified empirically this session: `printf '%s' "123" > f;
    IFS= read -r X < f 2>/dev/null || X="EMPTY"` yields `X=EMPTY`). The
    `LAST_FIRED_AT` validation reuses the **full 4-arm** `case` pattern from
    `ABS_CAP` (line 132), not a 2-arm subset — a marker holding a leading-zero
    or 9+-digit value hits the identical octal-misparse / wrap-negative
    hazard the 4-arm pattern exists to guard against, and a marker's content
    is less trusted than an env-var override (a stale write from a future
    format change, not just operator input):
    ```
    LAST_FIRED_AT=""
    if [ -f "$FIRED_MARKER" ]; then
      IFS= read -r LAST_FIRED_AT < "$FIRED_MARKER" 2>/dev/null || LAST_FIRED_AT=""
    fi
    # A corrupt/unreadable/pre-change-format marker reads as "no prior fire"
    # and forces a fire — mirrors the hook's write-failure posture (fail
    # toward firing, never toward staying silently suppressed).
    case "$LAST_FIRED_AT" in ''|*[!0-9]*|0[0-9]*|?????????*) LAST_FIRED_AT="" ;; esac
    if [ -n "$LAST_FIRED_AT" ] && [ "$ESTIMATE" -lt "$(( LAST_FIRED_AT + REARM_SPACING ))" ] 2>/dev/null; then
      exit 0
    fi
    ```
  - Fire block (lines 407-425 today): change
    `touch "$FIRED_MARKER" 2>/dev/null || true` to
    `printf '%s\n' "$ESTIMATE" > "$FIRED_MARKER" 2>/dev/null || true` — note
    the trailing `\n` (see above); same place in the `&&`-chain (only runs
    once `OUTPUT` is confirmed non-empty, preserving the existing "don't
    burn the shot on a jq failure" precedent the surrounding comment already
    documents).
  - Header lines 45-47 ("`claude -p one-shot runs do not fire SessionEnd...
    Files are zero-byte`"): update — markers now hold the triggering
    `ESTIMATE` from first fire onward, not zero bytes; the accumulation
    behavior itself (uncleaned leak from one-shot runs) is unchanged.
  - **No change** to `run_check_mode`'s `already_fired` (lines 284-285) —
    stays a bare existence check (`[ -f ".../$session_id" ]`), which remains
    correct: the file exists from the first fire onward regardless of its
    content. Its *meaning* does weaken (see docs update below), but the
    boolean's computation doesn't change. **No change** to the 30-day mtime
    sweep mechanism itself (line 420) — but its *effective window* does
    change: every re-arm overwrite re-stamps the file's mtime, so a
    long-running session that keeps re-arming near the 30-day mark keeps
    pushing its own marker's sweep eligibility forward from the *last* fire
    rather than the first. Document this in `docs/handoff-nudge.md` (below);
    no code change needed.
  - Line numbers above are re-verified against this branch's own base
    (`398b7fe`, current HEAD) this session, not carried from the parent
    plan's citations, which the parent plan itself flags as stale against an
    earlier base.

- `docs/handoff-nudge.md`:
  - "What the hook does" section: update the one-shot description
    (currently ends "...prevents repeated injections") to state the
    escalating-band behavior and the `80000` default spacing.
  - "Known limitations" section: replace the "One-shot per session — the
    unwarned tail is now materially worse... Re-arming at escalating bands
    remains deferred pending frequency evidence" bullet — it describes the
    now-fixed defect as both current and deferred. Replace with a bullet
    describing the shipped behavior and its own limitation (a session that
    dismisses every re-arm still eventually reaches the same log-volume
    profile as today, bounded by spacing rather than eliminated).
  - Add a "Why this spacing" note (mirroring the existing "Why this cap"
    subsection's citation style) pointing at the `rearm-backtest`
    invocation and the table in Context above.
  - "Markers persist until the next fire, not until session teardown"
    bullet: update to note the marker now holds the triggering `ESTIMATE`
    (not zero bytes) and that its 30-day sweep eligibility resets on every
    re-arm overwrite, not just the first fire — a long-running,
    repeatedly-re-arming session keeps its own marker fresh from the sweep's
    perspective for as long as it keeps re-arming.
  - `--check`'s JSON contract section: add a note that `already_fired=true`
    no longer implies "no further nudge this session" — a session can be
    both `already_fired=true` and still due for another re-arm.

- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` (1596
  lines; reuse existing fixtures — `_marker_path`, `HOOK_EVENT_NAMES`,
  `SESSION_ID`, the `LARGE_THRESHOLD`/`SMALL_THRESHOLD` constants — rather
  than re-deriving them; `_drift_marker_path` is unrelated schema-drift
  bookkeeping, not reused here):
  - New: second fire suppressed when `ESTIMATE < LAST_FIRED_AT + SPACING`
    (marker file pre-populated **through a real hook invocation**, not a
    hand-seeded fixture — i.e. drive "fire, then check-again-below-band" as
    two `_run_hook` calls rather than writing the marker's content directly
    with a test-chosen trailing-newline convention that could accidentally
    mask the write/read newline mismatch called out above).
  - New: second fire allowed once `ESTIMATE >= LAST_FIRED_AT + SPACING`,
    also driven through two real hook invocations; assert the marker file's
    content is overwritten to the new `ESTIMATE` (not left at the old value,
    not empty, not touched-to-zero-byte) and read it back with the same
    newline-sensitive method the hook itself uses.
  - New: explicit N-1/N boundary pair at the rearm threshold —
    `(LAST_FIRED_AT + SPACING - 1, suppressed)` /
    `(LAST_FIRED_AT + SPACING, fires)` — matching this file's existing
    adjacent-pair convention for every other threshold it tests.
  - New: corrupt/non-numeric pre-existing marker content (e.g. leftover
    zero-byte file from a session run against the pre-change hook) is
    treated as no-prior-fire and fires immediately once past `THRESHOLD`.
    Also cover a marker holding a leading-zero value and a 9+-digit value —
    both must fall back to no-prior-fire via the 4-arm guard, not misparse.
  - **Update, not new:** the existing `test_already_fired_is_silent`
    (currently ~line 533-542) pre-populates the marker via
    `_marker_path(tmp_path).touch()` (zero-byte). Under the new corrupt-marker
    rule this scenario now fires instead of staying silent — rewrite the
    fixture to pre-populate a numeric `LAST_FIRED_AT` within-band value
    (asserting suppression) and add a sibling test asserting the old
    zero-byte-touch scenario now fires. This is a required update, not a
    regression to paper over.
  - New: `HANDOFF_NUDGE_REARM_SPACING` override — valid override changes the
    re-arm point; malformed override (empty/non-digit/zero-padded/oversized,
    **plus the literal string `"0"`**, which the shared 4-arm case pattern
    does not reject on its own — `"0"` is one character and `0[0-9]*`
    requires a second digit — so the fallback-to-default behavior for a
    literal zero override must be asserted explicitly, not assumed) falls
    back to `80000`. Opportunistically add the same `"0"` case to the
    existing `HANDOFF_NUDGE_ABS_CAP` malformed-value parametrize list
    (currently untested there too, same file, same pattern, one-line add).
  - New: three-fire sequence in one session (fire, suppress, re-fire past
    second band) exercising the overwrite happening twice.
  - Unchanged/regression: existing `already_fired` (`--check` mode)
    assertions still pass unmodified — content-bearing marker still reads as
    "exists" for that boolean (the boolean's *meaning* weakens post-rearm,
    documented above, but its computation is untouched).

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`
  from the worktree (per this repo's three-levels-deep `.venv` convention).
- `../../../.venv/bin/shellcheck` (or the repo's tracked-file wrapper) against
  the modified hook.
- Manual: touch a fake transcript past `THRESHOLD`, run the hook twice with
  `ESTIMATE` values `THRESHOLD` and `THRESHOLD + 80000 - 1`, confirm the
  second call is silent; run a third time at `THRESHOLD + 80000`, confirm it
  fires and the marker file's content changed.
- `python3 ~/.claude/scripts/transcript-analysis.py rearm-backtest --this-repo`
  is not re-run post-implementation — it models hypothetical spacings against
  historical transcripts, not this hook's runtime behavior; nothing here
  changes its inputs.

## Out of scope

- Retuning `ABS_CAP` — fixed at `360000` by the parent plan's explicit
  instruction and PR #605's evidence; not reopened here.
- Widening `--check`'s JSON contract to expose the next re-arm point or last
  band — no current consumer needs it; adding it now is speculative surface
  the parent plan's Axis 4 (change size) argues against. A prose note that
  `already_fired`'s meaning weakened is added to the docs (see Critical
  files); the JSON shape itself is untouched.
- `--check`'s known limitations (PID-reuse second-resolution, `claude`
  process-name stop rule, model-table staleness) — pre-existing, unrelated
  to re-arming.
- `claude/.claude/hooks/nudge-error-mode-analysis.sh` — shares the identical
  one-shot marker-existence shape (its own `FIRED_MARKER` + bare `touch`,
  lines ~115-119, 178) but is a distinct hook for a distinct nudge domain
  (friction-signal accumulation, not context growth). The parent plan's
  Phase 3 instruction names only `nudge-handoff-near-context-cap.sh`, and no
  evidence in this session's backtest or the transcript log establishes the
  same "unwarned tail" defect for the friction-nudge's usage pattern.
  Left untouched; a future plan should backtest it independently before
  applying the same fix, rather than this phase assuming the fix transfers.
- The hook's broader fail-open / informational-class contract (exit 0 on
  every path) — this phase's changes preserve it (see Approach), but nothing
  about the contract itself is being redesigned; changing it would be a
  separate, unscoped decision.
- Phase 5b's `opusplan`-routing investigation and Phase 6's cost-ledger
  disposition — separate phases of the parent plan, tracked independently.
