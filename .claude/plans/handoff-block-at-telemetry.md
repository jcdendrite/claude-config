# Handoff-nudge: absolute block threshold + per-fire telemetry

## Context

Replace the count-based `HANDOFF_NUDGE_BLOCK_AFTER` re-arm counter with an
absolute `HANDOFF_NUDGE_BLOCK_AT` token threshold, and add per-fire
handoff-nudge telemetry, in one combined plan — per the engineer's explicit
instruction this session to combine the two related redesigns rather than
plan them separately. `HANDOFF_NUDGE_BLOCK_AFTER` counts *ignored re-arms*
(a session-relative count that drifts with re-arm spacing), while the
intended behavior is a hard stop at a fixed *absolute* token position
regardless of how many re-arms preceded it — PR #769 (merged) restored the
current count-based hard-block point after the 150k `HANDOFF_NUDGE_ABS_CAP`
retune, but only as an interim fix; this plan replaces the mechanism
itself. Per-fire telemetry (recording, on each nudge fire, which
handoff-relevant skill markers were active) rides along in the same plan
because both touch the same hook script and log format.

Intended outcome: `nudge-handoff-near-context-cap.sh` blocks at a fixed
absolute token count (`HANDOFF_NUDGE_BLOCK_AT`) instead of after N ignored
re-arms, and `.handoff-nudge.log` gains per-fire telemetry fields — the
existing ignored-re-arm counter is repurposed as one of those fields
rather than retired (see Approach for the reasoning).

## Approach

Replace the ignored-re-arm counter's gating role with a single absolute-token comparison — the hook hard-blocks when the session's estimate reaches `HANDOFF_NUDGE_BLOCK_AT` (default `470000`), regardless of how many re-arms preceded it — and keep the counter alive as a per-fire telemetry field on `.handoff-nudge.log` alongside a new record of which active-bypass skill markers were live when the nudge fired. `470000` is the block point the shipped configuration already produces (`THRESHOLD 150000 + BLOCK_AFTER 4 × REARM_SPACING 80000`, stated at `docs/handoff-nudge.md:24`), so behavior at shipped defaults on a 1M-window model is unchanged; what changes is that the block point stops being a derived quantity that moves whenever `HANDOFF_NUDGE_ABS_CAP` or `HANDOFF_NUDGE_REARM_SPACING` is retuned.

**Why pure-absolute over hybrid.** Both hybrid shapes were evaluated and both reintroduce the exact defect this plan removes. An **OR-hybrid** (block when `ESTIMATE >= BLOCK_AT` *or* `IGNORED_COUNT >= BLOCK_AFTER`) blocks at the earlier of the two arms; at shipped defaults the arms coincide at 470000, but lowering `HANDOFF_NUDGE_REARM_SPACING` to 20000 moves the count arm to 230000, so the effective block point is once again a function of the spacing constant — which is precisely what PR #769 had to hand-restore. An **AND-hybrid** (block only when both hold) fails in the opposite direction: a session that reaches 470000 in fewer than four re-arms — a large single-batch jump, or a raised spacing — never blocks at all, which contradicts the stated intent of a hard stop at a fixed absolute position. Neither shape leaves the block point independent of the other two constants, so neither is a hybrid of two mechanisms so much as a reattachment of the coupling. Pure-absolute is the recommendation.

**Why the counter is repurposed, not retired.** `docs/handoff-nudge.md:34` directs a future reader to revisit the block default "via an extended `rearm-backtest` once escalation-fire data exists," and names the `action=block` log field as existing for exactly that analysis. The ignored-re-arm counter is the only per-session record of dismissal behavior in the system; retiring it would delete the signal that the doc's own follow-up depends on, right as the plan is otherwise adding telemetry. It is already computed on every fire (`nudge-handoff-near-context-cap.sh:604-608`) and today surfaces only in the hard-block stderr string, so logging it on every `nudged` line costs one `printf` argument. It stops gating; it starts being recorded.

**One consequence of the counter losing its gating role must be followed through.** `claude/.claude/scripts/handoff-record-conversion.sh:23` removes the `-ignored` file as the documented ordinary exit from a hard block (`docs/handoff-nudge.md:36`). Under an absolute block point there is no such exit — the estimate only grows — so that `rm -f` stops meaning anything, and leaving it in place would make the new `ignored=` field non-monotonic within a session (a later log line showing a lower count than an earlier one) for no remaining benefit. The line is dropped. The block's surviving exits are the intended one (run `/handoff`, resume in a fresh session), the global kill-switch, and — pre-existing and unchanged — a live `/handoff` active-bypass marker, which already keeps a qualifying re-arm advisory. No new escape hatch is invented; a post-conversion grace band was considered and rejected as a compounding layer that would re-couple the block point to `REARM_SPACING`.

**One behavior is restated rather than newly added.** Today a session's first-ever crossing can never hard-block: `IGNORED_COUNT` is 0 on a first fire and the minimum accepted `BLOCK_AFTER` is 1. That property is structural in the count-based design and disappears under an absolute comparison, where a session whose first observed crossing is already past `BLOCK_AT` would hard-block with no advisory warning at all. The block condition therefore carries an explicit "this session has fired before" precondition, reusing `LAST_FIRED_AT` which is already resolved a few lines above (`nudge-handoff-near-context-cap.sh:580-593`). This preserves current behavior rather than adding a defensive layer, and it is what keeps a degraded-toward-small `BLOCK_AT` from blocking a session's very first fire.

**Telemetry shape.** `.handoff-nudge.log` stays a space-delimited `key=value` line format. `_parse_nudge_log_entries` (`claude/.claude/scripts/transcript-analysis.py:9587-9610`) tokenizes on whitespace, requires a fixed key subset, and ignores unknown keys, so appending fields is forward-compatible with every already-written line. Two fields are added to `nudged` lines, always emitted (never conditionally omitted) because the log is append-only across eras and `docs/case-studies/handoff-threshold-impact.md` already performs era attribution on it — an absent field must mean "written before this change," not "nothing was active":

- `ignored=<n>` — the repurposed counter, `0` on a first fire.
- `skills=<comma-separated-labels>` or `skills=-` — the active-bypass markers live at fire time.

`action=block` stays the last field on a hard-block line so the existing `endswith("action=block")` assertion keeps its meaning; the two new fields go between `event=` and `action=`.

**Marker enumeration uses the lightest available primitive.** All five active-bypass markers share one layout (`<config-dir>/.{skill}-active.d/<session_id>`, bare-PID content — `claude/.claude/scripts/marker.sh:351-397`), and `marker.sh:435-437` already establishes globbing `"$CONFIG_DIR"/.*-active.d` with a `[ -d ]` guard as the way to enumerate them without a hardcoded list. The hook reuses that idiom rather than introducing a shared skill registry sourced by both files: the glob needs no second file, auto-covers a marker directory added later, and degrades to "no skills recorded" rather than to a wrong answer if globbing is disabled. A hardcoded array mirroring `marker.sh`'s `activate` arms was the other lighter option and loses on drift — two lists to keep in sync where the glob has none. The per-directory liveness check calls `_lib_active_bypass_marker_live` (`claude/.claude/hooks/_lib.sh:832-865`) rather than reimplementing the `^[0-9]+$` + `kill -0` pair. The label is derived from the directory name (`.memory-skill-active.d` → `memory-skill`), so it reports the marker's name and not the owning skill's — `memory-skill` rather than `ai-instruction-and-memory-files` — which is correct and should not be "fixed."

**Cost placement.** The live-marker probe runs only on a real fire, after the re-arm-spacing gate, never on the common suppressed path, so the per-tool-call-batch hot path is untouched. The single computed live-marker set serves both the log field and the block condition's existing `/handoff`-marker suppression, so `.handoff-active.d` is read once per fire rather than twice. `/plan-review`'s hook-checklist pass found this probe is nonetheless the one dependency on the fire path not wrapped in `_lib_capped_for` — every other external call in this hook is capped, and up to five marker directories × `_lib_active_bypass_marker_live` (a subshell plus `cat`/`tr`/`kill -0` each) is 10-15 uncapped subprocess spawns in the worst case. Wrap the enumeration loop in `_lib_capped_for` to match the file's own established discipline rather than leaving it as the one uncapped exception.

**Migration notice for the renamed env var.** `HANDOFF_NUDGE_BLOCK_AFTER` wasn't an obscure internal knob — the hard-block stderr text this plan replaces (`handoff-hard-block.md`, merged) specifically publicized it as *the* override for a workflow that finds the default too aggressive. A consumer who raised it to avoid hard-blocking silently gets no benefit from that export once this ships, and the new stderr message names `HANDOFF_NUDGE_BLOCK_AT` with no hint that a still-exported `HANDOFF_NUDGE_BLOCK_AFTER` is the reason nothing changed. A PR body reaches this repo's reviewers, not a downstream consumer's shell profile. Add a one-time stderr notice — gated the same way the file's existing `DRIFT_MARKER`/`-drift` mechanism dedups a once-per-session warning — firing when `HANDOFF_NUDGE_BLOCK_AFTER` is still set in the environment. This is proportionate: one conditional and one marker file, matching an idiom already in this hook, not a new migration mechanism.

### Assumption ledger

**Root:** the hard block's trip point is expressed as a count of ignored re-arms, making its actual token position a derived value that silently moves whenever `HANDOFF_NUDGE_ABS_CAP` or `HANDOFF_NUDGE_REARM_SPACING` is retuned; and no per-fire record exists of what a session was doing when a nudge fired, so the dismissal data the block's own default is supposed to be revisited against does not exist.

**Givens** (fixed conditions beyond this design's reach):

- **G1.** `PostToolBatch`'s `exit 2` is the only loop-stop primitive available, and the same `exit 2` on `Stop` forces continuation instead — harness-owned contract, so the block stays `PostToolBatch`-only and `Stop` keeps falling through to advisory.
- **G2.** `PostToolBatch` has no dedicated section in the vendor hooks reference, so its field set and block contract remain confirmed by live capture rather than documentation — vendor-owned documentation gap.
- **G3.** `ESTIMATE` is the four-field sum of the latest *recorded* assistant turn and can lag by up to one assistant step — the transcript schema and write cadence are harness-owned.
- **G4.** A 200k-window model's effective threshold is 80000 and its window is smaller than any plausible absolute block point, so the hard block is unreachable there under both the current and the proposed design — model context-window sizes are vendor-owned. Not a regression; it is a property to name in the docs, not to fix here.
- **G5.** The sole empirical justification for having a hard block at all remains one observed 485000-token session, with the corroborating 426-session corpus scan right-censored and too thin at the relevant depth (`docs/handoff-nudge.md:24-32`). Better grounding requires the dismissal data this plan produces, so it cannot be settled inside this plan.

**Rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | The block-point redesign and the telemetry addition ship as one plan rather than two, because both edit the same hook and the same log-line format. | `[engineer-verified]` |
| 2 | Telemetry extends `.handoff-nudge.log`'s existing lines with new fields; no separate telemetry file. | `[engineer-verified]` |
| 3 | The hybrid `BLOCK_AT`/`BLOCK_AFTER` design is evaluated on its merits rather than assumed away; conclusion above is to reject both OR- and AND-hybrid shapes, with reasons. | `[engineer-verified]` |
| 4 | The ignored-re-arm counter is not assumed away; conclusion above is to repurpose it as an always-emitted telemetry field. | `[engineer-verified]` |
| 5 | The `test_doc_counts.py` behavioral-derivation rework is the architect's call; conclusion below is to keep the behavioral shape and retarget it. | `[engineer-verified]` |
| 6 | The shipped block point today is 470000 tokens (`150000 + 4 × 80000`), so `HANDOFF_NUDGE_BLOCK_AT=470000` is behavior-preserving at shipped defaults on a 1M-window model. | `[verified: docs/handoff-nudge.md:24; nudge-handoff-near-context-cap.sh:149,162,176]` |
| 7 | `_parse_nudge_log_entries` requires a fixed key subset and ignores unknown `key=value` tokens, so appended fields do not break parsing of new or historical lines. | `[verified: claude/.claude/scripts/transcript-analysis.py:9587-9610]` |
| 8 | `_lib_active_bypass_marker_live` evicts a dead-PID marker as a side effect of returning false; using it for telemetry means the nudge hook evicts other skills' orphaned markers on a fire. Accepted — `marker.sh status`, a reporting-only consumer, already does exactly this across all five directories. | `[verified: claude/.claude/hooks/_lib.sh:863; claude/.claude/scripts/marker.sh:198-210,517-522]` |
| 9 | All five active-bypass markers share the `<config-dir>/.{skill}-active.d/<session_id>` layout with bare-PID content, so one uniform probe covers them; `.planmode-path` is a sibling named `<session_id>.planmode-path` and cannot collide with an exact-`$SESSION_ID` filename match. | `[verified: claude/.claude/scripts/marker.sh:351-397,443-445]` |
| 10 | Globbing `"$CONFIG_DIR"/.*-active.d` with a `[ -d ]` guard is the established in-repo enumeration idiom and needs no `nullglob` handling. | `[verified: claude/.claude/scripts/marker.sh:435-437]` |
| 11 | A session's first-ever fire can never hard-block under the current design (`IGNORED_COUNT` is 0 there and `BLOCK_AFTER` cannot resolve below 1), so the explicit `LAST_FIRED_AT`-non-empty precondition preserves behavior rather than adding a new guard. | `[verified: nudge-handoff-near-context-cap.sh:596-610]` |
| 12 | `handoff-record-conversion.sh:23` is the only site in the repository that removes the `-ignored` marker; nothing else depends on that reset. | `[verified: repo-wide grep for `-ignored`]` |
| 13 | `_count_handoff_nudge_abs_cap_default` reads its number from the advisory JSON on **stdout** (`threshold \((\d+) tokens\)`), not from the hard-block stderr, so rewording the stderr message cannot break it. | `[verified: claude/.claude/hooks/tests/test_doc_counts.py:241]` |
| 14 | The malformed-value `case` guard's `?????????*` arm rejects values of 10 or more digits; `470000` is six digits and clears it. | `[verified: nudge-handoff-near-context-cap.sh:148-151 idiom]` |
| 15 | A resumed or long-plan-mode-gated session could observe its first crossing already past `BLOCK_AT`. Not verified against harness session-id behavior on resume; row 11's precondition makes the outcome identical either way, so nothing downstream depends on resolving it. | `[unverified]` |

**Mechanism justifications:**

- Absolute `HANDOFF_NUDGE_BLOCK_AT` comparison replacing `IGNORED_COUNT >= BLOCK_AFTER` — `anchors: root`. Directly dissolves the derived-position coupling; no lighter primitive exists, since the comparison being replaced is already a single integer test.
- `resolve_block_at()` as a self-contained `case`-guarded reader — `anchors: row6, row14`. Mirrors `compute_threshold`/`resolve_rearm_spacing`/`resolve_block_after` exactly; adopting the existing shape is the lightest option available.
- `LAST_FIRED_AT`-non-empty precondition on the block — `anchors: row11`. Reuses a variable already resolved eight lines above; no new state, no new file.
- Directory-glob enumeration of active-bypass markers — `anchors: root, row9, row10`. The heavier alternative (a shared skill registry consumed by both `marker.sh` and the hook) is rejected in favor of two lighter in-system primitives: the `clear-stale` glob idiom (chosen — zero lists to keep in sync), and a hardcoded array mirroring `marker.sh`'s `activate` arms (rejected — introduces a second list that drifts silently).
- `_lib_active_bypass_marker_live` reused for liveness — `anchors: row8`. Reimplementing the PID check inline would duplicate the eviction and validation semantics; the accepted cost is the eviction side effect, which has direct precedent.
- Always-emitted `ignored=`/`skills=` fields with a `-` sentinel — `anchors: row7`. An omitted field would be indistinguishable from a pre-change log line in an append-only log that is already read across eras.
- Dropping `handoff-record-conversion.sh`'s `-ignored` removal — `anchors: row4, row12`. Required for the new field to carry a monotonic, non-conditional meaning; the removal's only documented purpose disappears with the count-based block.

## Critical files

Two sequential dispatches. Phase 1's emitted log-line format is Phase 2's input; the file sets do not overlap, and each phase leaves the tree green on its own.

### Phase 1 — hook, its tests, and the doc it is count-checked against

- **`claude/.claude/hooks/nudge-handoff-near-context-cap.sh`** — the core change.
  - Replace `resolve_block_after()` (lines 174-179, comment 167-173) with `resolve_block_at()`, default `470000`, carrying the identical `''|0|*[!0-9]*|0[0-9]*|?????????*` malformed-value arm. **Reuse:** copy the guard shape from `compute_threshold` (146-153) / `resolve_rearm_spacing` (160-165) verbatim rather than inventing a variant.
  - Replace the block condition at 610-611: `[ "$ESTIMATE" -ge "$BLOCK_AT" ]` **and** `[ -n "$LAST_FIRED_AT" ]` **and** `[ "$HOOK_EVENT" = "PostToolBatch" ]` **and** the `/handoff`-marker suppression. Keep `IGNORED_COUNT`'s computation at 604-608 — it now feeds only the log and the stderr message.
  - Add one live-marker enumeration, placed after the re-arm gate (593) and before the block condition, producing a sorted comma-joined label list and reusing `_lib_active_bypass_marker_live` per directory, the whole loop wrapped in `_lib_capped_for` per the Approach's Cost-placement note. Skip any label not matching `[A-Za-z0-9_-]` via the file's existing `case`-glob validation idiom, so a hand-created directory name with a space cannot corrupt the log's tokenization. Derive the `/handoff` liveness for the block condition from this same result rather than making a second call.
  - Both log `printf`s (620-621, 639-640) gain ` ignored=%s skills=%s`, inserted before `action=block` on the block line so it stays last.
  - Rewrite the hard-block stderr (623-624) to name the absolute point. Proposed text, with the `HANDOFF_NUDGE_BLOCK_AT=%s)` parenthetical load-bearing for `test_doc_counts.py`'s regex: `Context (%s tokens) is past this session's handoff-nudge hard-block point (HANDOFF_NUDGE_BLOCK_AT=%s), after %s ignored re-arms. Blocking rather than advising: run /handoff now — it captures state in a /tmp file and resumes in a fresh session.` Arguments `ESTIMATE`, `BLOCK_AT`, `IGNORED_COUNT`. Do not reintroduce the "genuinely almost done" / "too aggressive for your workflow" phrasing — three existing tests assert its absence (1178, 1217, and `TestHandoffActiveMarkerSuppressesBlock`'s `test_dead_pid_marker_does_not_suppress_and_is_evicted` at 2142, which also asserts `"HANDOFF_NUDGE_BLOCK_AFTER=" in result.stderr` and needs the same reword).
  - Add the one-time `HANDOFF_NUDGE_BLOCK_AFTER`-still-set stderr notice described in Approach, gated on a per-session marker following the `DRIFT_MARKER` dedup pattern already in this file.
  - Header comment block (lines 9-18, 30-32): update the escalation description and the log-line format summary. Keep each fact to one sentence per `claude/.claude/rules/shell-script-conventions.md`. The line-552 sweep comment still mentions `-ignored` and remains accurate.
- **`claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`**
  - Module docstring (12-17) and constants (66-73): `DEFAULT_BLOCK_AFTER = 4` → `DEFAULT_BLOCK_AT = 470_000`; `REARM_MECHANICS_BLOCK_AFTER = "5"` → a `REARM_MECHANICS_BLOCK_AT` high enough that mechanics tests never reach it (seven digits, e.g. `"9000000"`, keeps it under the ten-digit guard). Consider a collection-time assert that `DEFAULT_BLOCK_AT > LARGE_THRESHOLD`, mirroring the existing collection-time assert at line 97. Update the constant reference **and** the `extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER}` dict key literal together at all seven call sites — `/plan-review`'s `staff-sdet` pass found the constant-only rename leaves the key stale, which passes silently (each test's estimate tops out well under `BLOCK_AT`) while making the pin a dead no-op and its docstring's claim false: `test_second_fire_allowed_after_rearm_spacing` (739), `test_rearm_boundary_at_last_fired_plus_spacing` (766), `test_three_fire_sequence_rearms_twice` (834), `test_incremental_read_stops_offset_before_incomplete_trailing_line` (882), `test_escalation_counter_concurrent_rearms_no_lost_update` (1131), `test_rearm_spacing_override_changes_rearm_point` (1915), `test_rearm_spacing_malformed_override_positive_control_fires_at_default` (1967).
  - Rewrite `test_escalation_ladder_blocks_once_block_after_ignored_rearms_reached` (1178) as an estimate-driven equivalent; its `action=block` log assertions survive unchanged given the field ordering above.
  - **Delete and invert** `test_escalation_ladder_resets_when_ignored_marker_removed` (1217). Its premise is exactly what this change retires. Replace with a test that removing `-ignored` does **not** lift the block once the estimate is past `BLOCK_AT` — that inversion is the plan's headline behavior change and needs a regression test of its own.
  - Retarget `test_hard_block_only_fires_on_post_tool_batch` (1256) and both `test_block_after_malformed_override_*` tests (1295, 1311) to the new variable. The positive control becomes stronger: drive to just under 470000 (advisory), then to 470000 (block), pinning the exact default instead of "blocks eventually." Carry the malformed-value parametrize list over unchanged.
  - `test_atomic_append_no_lost_writes_under_concurrency` (1109) and `test_escalation_counter_concurrent_rearms_no_lost_update` (1131) both survive — the append idiom and the counter file persist. The first's docstring points at "the hard-block's own source comment"; that comment moves to the telemetry site, so update the pointer.
  - `TestHandoffActiveMarkerSuppressesBlock` (~2078-2230): `BLOCK_AFTER = "2"` and `_drive_to_block_point` retarget to `BLOCK_AT`; structure and the `marker.sh activate/deactivate` fixture round-trip survive.
  - New: a first-ever-crossing-already-past-`BLOCK_AT` test asserting advisory, not block (row 11).
  - New telemetry tests: `ignored=` present on every `nudged` line and equal to the `-ignored` file size; `skills=` naming a live marker's label; `skills=` omitting (and evicting) a dead-PID marker; `skills=-` when none are active; a marker directory whose name yields a non-conforming label is skipped rather than emitted; **two simultaneously-live markers produce a `skills=` value with a stable sort order and comma delimiter** — the plan's own "sorted comma-joined label list" claim (Approach, Marker enumeration) is otherwise untested, since every other case here uses at most one marker. **Reuse:** `_handoff_active_marker_path` (338) and `_ignored_marker_path` (331) already exist; add sibling helpers in the same shape rather than inlining paths.
  - New producer/consumer contract test: run the real hook to fire a `nudged` block line, capture the emitted log line, and feed it through `_parse_nudge_log_entries` (Phase 2), asserting `ignored`/`skills` come back correctly typed. Phase 1's hook-level assertions and Phase 2's parser-level assertions otherwise each use hand-written literal strings for the other side's format, so a field-ordering or delimiter drift between them could pass both suites while breaking the real pipeline.
- **`claude/.claude/hooks/tests/test_doc_counts.py`** — rework `_count_handoff_nudge_block_after_default` (251-322) into `_count_handoff_nudge_block_at_default`. **Judgment call, taken:** keep the behavioral-derivation approach — a source scan of the fallback literal still would not prove the runtime path uses it — but replace the up-to-20-invocation re-arm loop with two invocations: one advisory fire at the threshold to satisfy the prior-fire precondition, then one fire at an estimate far above any plausible block point (six digits, under the 1M window), reading the value out of the `HANDOFF_NUDGE_BLOCK_AT=(\d+)\)` stderr. This is exact rather than accurate-to-one-increment, and strictly cheaper. Update the registry entry (435-445) to the new function and the new doc pattern. **Watch:** `_assert_exactly_one_match` requires each registered pattern to match exactly once in its file, so the doc must not leave two `470000` occurrences that a loose pattern would both catch.
- **`docs/handoff-nudge.md`** — all of this file lands in Phase 1 so `test_doc_counts.py` stays green at the phase boundary.
  - Line 22: the counter paragraph becomes a description of a recorded, non-gating count.
  - Line 24 ("Why this block-after count"): retitle and rewrite around the absolute point. The re-derivation caveat ("needs re-derivation whenever `HANDOFF_NUDGE_ABS_CAP` or `HANDOFF_NUDGE_REARM_SPACING` changes") is **deleted, not restated** — its disappearance is the change's actual win. Keep the 485000-token grounding session and the right-censored corpus scan (G5); those are preserved records.
  - Line 34: rewrite the override sentence around `HANDOFF_NUDGE_BLOCK_AT` and retarget the "revisit once escalation-fire data exists" pointer at the new `ignored=`/`skills=` fields.
  - Line 36 ("Recovering from a hard block"): the `-ignored` removal route is gone; state the three surviving exits.
  - Lines 93-98 ("Log location"): add `ignored=` and `skills=` to the `nudged` row, including the `-` sentinel.
  - Known limitations (128-141): add G4's 200k-window unreachability as its own bullet; check line 131's capped-call enumeration against the new call count before editing it — the new `cat` calls inside `_lib_active_bypass_marker_live` are uncapped and so do not change that bullet's figure, but the claim should be re-derived rather than assumed.
  - Do **not** write "used to be `HANDOFF_NUDGE_BLOCK_AFTER`" framing anywhere in this file. The migration rationale belongs in the PR description, per CLAUDE.md §Code Comments, Documentation, and Prose — the one-time stderr notice itself (Approach, Critical files hook bullet) is the durable, in-product mechanism; the doc need not restate it.
- Verification for this phase: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, plus `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.

### Phase 2 — downstream consumers and the remaining prose

- **`claude/.claude/scripts/handoff-record-conversion.sh`** — drop line 23's `rm -f`. The `_lib_valid_session_id_component` guard at line 18 **stays**, but its comment currently justifies itself by the `rm -f` path alone; reword it to the surviving reason (a session id carrying whitespace would corrupt the log line's `key=value` tokenization). Update the file header (lines 2-3), which advertises the reset.
- **`claude/.claude/scripts/tests/test_handoff_record_conversion.py`** — the marker-removal assertions (73-81) invert to "the `-ignored` file is left alone"; the log-line assertions (81, 116) and the traversal-canary test (89-102) survive, the latter now covering the log-line path rather than the `rm` path.
- **`claude/.claude/scripts/transcript-analysis.py`** — capture `ignored` (as `int`) and `skills` on `nudged` entries in `_parse_nudge_log_entries` (9604-9610), in the same optional-key style `action` already uses, so the emitted telemetry is actually readable by the one tool that reads this log. Update `_operator_response_lag_from_log`'s docstring (9631-9634), which enumerates the log's fields as "session=/est=/model=/window=/event= only." Check the `_NUDGE_LOG_LINE_KINDS` comment (~9330) for the same staleness. No behavior change to `rearm-backtest` or `spend-over-threshold`.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`** — extend the `_parse_nudge_log_entries` cases (14930-14970) with lines carrying the new fields, and a line lacking them (the historical-era case). These tests already hardcode literal log lines, so the format duplication is the file's established pattern, not new.
- **`docs/hooks.md:33`** — the bullet states the block fires "Past `HANDOFF_NUDGE_BLOCK_AFTER` ignored re-arms"; restate around the absolute point and mention the added log fields.
- **`README.md`** — line 478 and the Threshold reference at 490-494 describe only `HANDOFF_NUDGE_ABS_CAP` and do not name the block mechanism, so check whether either needs a touch; if the block point is worth naming in the Threshold reference, add it as a sibling bullet to the 150000 one.
- **`claude/.claude/skills/handoff/SKILL.md`** — line 180 describes what `handoff-record-conversion.sh` does ("Append the session id to … own log"); confirm it does not also promise the escalation reset, and adjust if it does. The two `HOOK_TEST_FIXTURE` blocks (29, 194) are anchor comments the hook-alignment suite re-reads — treat as preserved content unless the marker recipe itself changes, which it does not.
- Verification for this phase: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, plus `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` and `.venv/bin/ruff check claude/.claude/`.

## Verification

Run the repo's own scoped selector, not the full suite (per `CLAUDE.md`'s Commands section — the full suite is CI's job and does not scale across parallel agents):

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

`select-tests.py` maps `docs/**` as a blanket domain (`DOCS_DIR`, `select-tests.py:90-97`) precisely because `test_doc_counts.py` reads `docs/handoff-nudge.md`, so the doc edits pull the doc-count test in automatically; no hand-widening is needed or warranted.

Run the selector at the end of each phase, not only at the end. Phase 1 is the phase that can leave the tree red on its own (the hook, its tests, and the doc that is count-checked against it move together); Phase 2's checks confirm the consumer and prose updates did not break the log parser.

Behavioral claims the tests above must actually pin, stated so a reviewer can check them off:

1. At shipped defaults on a 1M-window model, the first hard block occurs at an estimate of 470000 — the same point the current configuration produces.
2. A session's first-ever crossing never hard-blocks, even when its estimate already exceeds `BLOCK_AT`.
3. Removing the `-ignored` marker does not lift a block once the estimate is past `BLOCK_AT`.
4. `Stop` still falls through to advisory under a condition that hard-blocks on `PostToolBatch`.
5. A live `/handoff` active-bypass marker still keeps a would-block re-arm advisory, and `marker.sh deactivate handoff` restores the block.
6. Every `nudged` line carries `ignored=` and `skills=`, with `skills=-` when no marker is live, and `action=block` remains the final field on a block line.
7. A dead-PID marker is absent from `skills=` and is evicted.
8. A malformed `HANDOFF_NUDGE_BLOCK_AT` falls back to 470000 rather than degrading toward zero, across the existing malformed-value parametrize list.
9. Every existing fail-open test still passes: missing `jq`, `wc`, `tail`, `find`, or `mkdir` must still exit 0 and never hard-block.
10. Two simultaneously-live markers produce a `skills=` value with a deterministic sort order and comma delimiter, not just "at least one label present."
11. A real hook-emitted `nudged` line, fed through `_parse_nudge_log_entries`, returns `ignored`/`skills` correctly typed — not just a hand-written fixture line on each side.
12. A still-exported `HANDOFF_NUDGE_BLOCK_AFTER` produces the one-time stderr migration notice, once per session, not on every fire.

## Out of scope

- **Retuning the block point's value.** 470000 is carried across because it is what ships today and because the sole grounding session (485000 tokens) still sits above it. Re-grounding it needs the dismissal data this plan produces; `docs/handoff-nudge.md`'s existing "revisit once escalation-fire data exists" pointer covers the follow-up.
- **Adding a `block_at` / `over_block_at` field to `--check`.** Genuinely useful for `plan-it` Step 7 and the `handoff` warrant check, but it widens the JSON contract, its reason-vocabulary table, and its test surface for a convenience, not a correctness gap — a session past the advisory threshold already gets `over_threshold: true`.
- **Adding a timestamp field to `.handoff-nudge.log`.** The log's lack of a timestamp forces `_operator_response_lag_from_log` into a fragile first-crossing join against transcripts, and `docs/case-studies/handoff-threshold-impact.md` has to reconstruct era attribution from session first-record timestamps. Both are real costs, and this plan could change the format — it deliberately does not, because a timestamp changes what every existing consumer means by a line and deserves its own plan.
- **Teaching `rearm-backtest` to consume `ignored=` / `skills=`.** This plan's job is to emit and parse the data; building analysis on top of it is the follow-up the doc already anticipates.
- **Reworking `_lib_active_bypass_marker_live`'s liveness weakness.** Its own comment (`_lib.sh:821-829`) documents that a session outlives any one skill invocation, so a bypass can outlast what it was scoped to. Pre-existing, affects five gate hooks, and bounding the marker's age is a change with blast radius well beyond this hook.
- **Making the hard block reachable on 200k-window models.** Unreachable under both the current and the proposed design (G4); making it reachable means a window-relative block point, which reintroduces exactly the derived-position coupling this plan removes.
- **`docs/case-studies/handoff-threshold-impact.md` and every file under `.claude/plans/`.** Dated measurement records and historical planning documents — preserved content under `CLAUDE.md`'s scope-discipline Axis 3, read-only here even though they mention `action=block` and `HANDOFF_NUDGE_BLOCK_AFTER`.
- **A new mid-session escape from the hard block.** A post-conversion grace band was considered and rejected: it would re-couple the block point to `REARM_SPACING` and is a compounding layer over a mechanism whose whole point is that it does not move. The three surviving exits are documented instead.
