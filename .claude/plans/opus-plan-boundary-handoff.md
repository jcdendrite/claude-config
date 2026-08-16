# Opus-anchored plan boundary: continue, switch, or hand off

## Context

Decide — reproducibly — which of three actions is cheapest at the
plan→implementation boundary when the planning session is anchored to Opus:
continue on Opus, switch the session to Sonnet in place, or hand off to a
fresh Sonnet session. Today neither `plan-it` Step 7 nor the handoff nudge
conditions on model at all: both are purely context-size functions, and the
nudge's threshold table is keyed to context *window*, so Opus 5 and Sonnet 5
land in the identical 360,000-token bucket despite a 2.5x price difference on
every token class.

Why now: this decision is currently made ad hoc per session, against a prior
measurement (`handoff-boundary-decision-rule.md`) that never conditioned on
parent model and whose headline figures are not reproducible. No
model-conditioned measurement exists to decide it from.

Intended outcome: a committed subcommand that answers this from the live
corpus on demand, a recorded verdict, and — when the verdict meets the
pre-committed criterion in M7 — a model-conditional branch in the two skills
that make this decision.

## Approach

Add a `plan-boundary` subcommand to `transcript-analysis.py` that finds each
session's plan boundary, then **re-prices that session's actual post-boundary
turn sequence under all three arms**, holding the observed work fixed and
varying only the price schedule and the context-rebuild penalty. Report the
per-arm dollar delta alongside the *work-inflation breakeven* — how much extra
work a Sonnet arm could absorb before its advantage disappears — so the one
assumption that cannot be verified from transcripts is expressed as a
sensitivity rather than buried in a verdict.

Counterfactual re-pricing rather than cohort comparison is the load-bearing
choice. A cohort comparison ("sessions that handed off" vs "sessions that
continued") is what the prior attempt reached for, and it is confounded by
selection: the engineer chose each arm. `design-decisions.md` §22 states the
governing constraint directly — the existing main/sidechain cost split is "an
observed association across an uncontrolled mix of session shapes, not a
causal per-byte price." Re-pricing one observed turn sequence under three
schedules has no selection term at all. It is also the only design the corpus
supports: the feasibility probe found **no** plan-boundary session where main-
thread work stops at the boundary, so the handoff arm is close to
unobservable and must be simulated regardless.

### What each arm means operationally

Stated up front because it determines what M7 can even say:

- **Arm A — continue on Opus.** Today's default behavior.
- **Arm B — switch in place.** The engineer runs `/model sonnet` mid-session.
  This is *not* satisfied by the `opusplan` alias: `opusplan` switches on the
  plan-mode toggle, and since PR #647 the agent no longer enters harness plan
  mode, so on the prescribed `--model opus` + `/plan-it` path `opusplan` never
  fires. Arm B is therefore a procedural instruction, not a settings change.
- **Arm C — hand off.** Write the handoff artifact, end the session, start a
  fresh Sonnet-anchored one.

### Arm B pricing, stated exactly

The switch forces a full cache miss (the model-keyed cache invalidation
recorded below as G2). At the boundary+1 turn the observed
`cache_read_input_tokens` reflects reading *Opus's* warm prefix, which does
not exist for Sonnet — so that component is **discarded and replaced**, never
scaled and kept:

- Boundary+1 turn: charge a Sonnet cache-write over the boundary context, plus
  Sonnet input/output on that turn's own new tokens. Do **not** also charge a
  scaled cache-read.
- Every later turn: carry the observed read/write split forward, priced at
  Sonnet rates.

"Scaled by 0.4 plus a cache-write" is the wrong reading and would double-bill
the same tokens as both a read and a write.

### Assumption ledger

**Root problem.** Opus-anchored sessions continue into implementation at 2.5x
Sonnet token prices for a median of 129 further main-thread turns, and no
reproducible measurement exists to say whether continuing, switching, or
handing off is cheapest.

**Givens** (fixed, beyond this plan's reach):

- **G1 — Opus 5 costs exactly 2.5x Sonnet 5 on every token class.** Vendor-set;
  Anthropic owns pricing. [verified: `platform.claude.com/docs/en/about-claude/pricing`,
  and independently against this repo's own `_MODEL_BASE_INPUT_RATES`
  (`transcript-analysis.py:4867`): opus-5 5.00, sonnet-5 2.00, with every
  other class derived from one shared multiplier table]
- **G2 — The prompt cache is model-keyed; switching models mid-session
  recomputes the entire prefix.** Harness/API behavior, not configurable:
  *"each model has its own cache. Switching models recomputes the entire
  request even when the content is identical."*
  [verified: `code.claude.com/docs/en/prompt-caching`]
- **G3 — Harness plan mode escalates subagent dispatches to Opus with no
  instruction-layer mitigation.** PR #647 closed only the agent-initiated
  entry path; human `Shift+Tab` / `/plan` entry still triggers it, and an
  explicit `model: sonnet` dispatch param gives no protection (12/12 escalated
  anyway). Harness-owned; no model-routing change reaches it.
  [verified: `docs/case-studies/plan-mode-model-resolution.md` lines 54, 56]
- **G4 — The transcript corpus is rolling and self-deleting.** Claude Code
  owns retention. This repo already mitigates for trend data via
  `cost-ledger`; this plan does not extend that ledger, and instead ships a
  re-runnable subcommand so the figure can be regenerated rather than stored.

**Mechanisms:**

- **M1 — New `plan-boundary` subcommand.** *anchors: root.* Nothing existing
  reports plan-boundary → model-continuity; `_price_turn` is internal and no
  subcommand surfaces per-turn cost.
  *Over-powered-primitive check — three lighter options, all rejected:*
  (i) extend `cost` with a boundary filter — `cost` aggregates by
  class/model/thread with no per-session boundary concept and no counterfactual
  re-pricing, so the filter would carry the entire new mechanism anyway;
  (ii) invoke `rearm-backtest` with different arguments — its boundary is a
  token-threshold crossing (hook fire points), not a plan boundary, and it
  simulates *nudge spacing*, not *model substitution*;
  (iii) another uncommitted one-off script — this is precisely the defect
  PR #605 self-reported and PR #630 was built to avoid.
  **M1 carries its own copy of the boundary-detection check**, matching the
  codebase's documented convention (see "Deliberate duplication" below).
- **M2 — Extend `_extract_rearm_session_turns` with per-turn model and record
  position, as new top-level dict keys holding parallel lists.**
  *anchors: M1.* `main_thread_turns` keeps its 3-tuple shape and
  `_simulate_rearm_spacing`'s `Sequence[tuple[int, int, float]]` hint is
  unchanged — three sites positionally unpack that tuple
  (`_ramp_curve_from_corpus:9226`, `_simulate_rearm_spacing:9395`,
  `_rearm_backtest_report:9555`) and widening it breaks all three. The
  parallel-list shape has precedent in the same function: `main_thread_priced`
  (9134/9166). M1's boundary walk consumes the returned `data["deduped"]`
  rather than re-calling `_dedup_turns_by_request_id`, which is what makes the
  single-decode-per-session claim true.
- **M3 — Parse `message.diagnostics.cache_miss_reason` for arm-B ground
  truth.** *anchors: M1.* This is **new parsing work, not reuse** — the field
  exists in transcripts but `transcript-analysis.py` does not read it today
  (zero references to `cache_miss_reason`, `model_changed`, or `diagnostics`
  anywhere in the repo, reproducible via `grep -rn cache_miss_reason
  claude/.claude/scripts/` against the tree predating this plan's own M3).
  Every `model_changed` occurrence sits at exactly
  `record.message.diagnostics.cache_miss_reason`, on a `type: "assistant"`
  record carrying a `requestId` — 456 of 456 observed, no other path and no
  non-assistant variant. It is therefore inside
  `_dedup_turns_by_request_id`'s assistant-run merge, and
  `_merge_assistant_run`'s existing convention of taking non-content fields
  from `run[0]` yields the correct value with no extra collapsing logic.
  Dedup is nonetheless load-bearing, not incidental: the same value repeats
  once per content block, and the observed spread is 1–7 records per
  `requestId` (23 requestIds at 1, 64 at 2, 67 at 3, 13 at 4, 9 at 5–7), so
  summing raw records inflates the switch cost roughly 2.6x on average.
- **M4 — Reuse `_ramp_curve_from_corpus` to re-derive the ramp; never cite
  3.55x (see A4).** *anchors: A4.* Arm C recovers dollars via that curve's own
  convention — bucket rate is total per-turn dollars over `output_tokens/1000`,
  multiplied back as `(output_tokens/1000) * rate`. Do **not** scale observed
  dollars by a ramp factor: the observed figure already embeds both the model-
  price gap and the context-growth gap, so scaling double-counts.
- **M5 — Inherit the redaction contract.** *anchors: root.* Every sibling
  subcommand ships `redact` on by default, routes session identity through
  `_redact_session_id` / `_assign_root_scoped_redact_label`, and refuses
  `--no-redact` when more than one root is in scope. `plan-boundary` matches
  those defaults. Boundary records are consumed for **type and position only**
  — never the `plan` text, never `planFilePath`. Shipped output is
  aggregate-only.
- **M6 — Report work-inflation breakeven, not a bare verdict.**
  *anchors: A1.* Converts the unverifiable assumption into a reported
  sensitivity.
- **M7 — Model-conditional branch in `plan-it` Step 7 and `handoff`'s warrant
  section, gated on the criterion below.** *anchors: root.*
- **M8 — Tests.** *anchors: M1–M6.*
- **M9 — Document the subcommand** in `docs/transcript-analysis.md`, and record
  the verdict — figures only, never a pasted output block — in
  `docs/cost-levers-considered.md`. *anchors: root.*

**Assumptions:**

- **A1 [unverified] — Sonnet completes the same post-boundary work in the same
  turns and tokens as Opus.** Load-bearing for arms B and C; if Sonnet needs
  more turns, both are understated. Not settleable from transcripts (a
  capability claim, and the corpus has no matched-task pairs). Mitigated, not
  resolved, by M6. Everything downstream of the arm-B/arm-C dollar figures
  inherits this flag.
- **A2 [unverified] — The context-rebuild ramp transfers across model
  families.** PR #605 asserted the curve "reproduces independently within both
  the Opus and Sonnet families," but that assertion is itself pre-dedup-fix
  (A4).
- **A3 [preliminary feasibility check, pre-implementation] — The corpus
  supports the measurement.** 88 Opus-anchored `plan-review` boundaries,
  median 129 post-boundary main-thread turns, splitting 48 with a
  post-boundary model switch / 30 pure-Opus / 8 and 2 in mixed cells
  including a third model family. Separately 23 `ExitPlanMode` boundaries, 14
  of them cleanly `opus → sonnet`. No cell contained a session with zero
  post-boundary main-thread turns. These counts predate the shipped
  `plan-boundary` subcommand and use a different per-signal-type breakdown
  than its unified boundary detection; they justified building the tool, not
  the tool's own findings. Superseded by the subcommand's own reproducible,
  reconciled output — re-run `plan-boundary` for current figures, and see
  `docs/cost-levers-considered.md`'s recorded verdict for this measurement's
  actual result.
- **A4 [verified: `.claude/plans/handoff-boundary-decision-rule.md` line 49,
  commit `07f28e0`] — The 3.55x ramp figure is unreproducible and predates a
  known pricing bug.** The plan calls its own table "a point-in-time
  measurement, not a reproducible report," and PR #622 later fixed a ~2.1x
  inflation from pricing per JSONL record rather than per `requestId`. The fix
  was never back-applied. Direction of distortion unknown.
- **A5 [engineer-verified] — Cost is the criterion.** Implementation-quality
  difference between Opus and Sonnet is out of scope and is not claimed either
  way by any output of this plan.
- **A6 [engineer-verified] — All three arms are in scope**, including the
  in-place switch the original hypothesis did not name.
- **A7 [unverified] — Corpus denominator needs reconciliation.** The probe
  walked 3,362 `.jsonl` files under the active account's project root while
  `cost` reports ~495 scanned transcripts for the same account; the likely
  cause is that subagent sidechain transcripts are separate files, which
  `iter_sessions` (456, 517) distinguishes and the probe may not have. This
  gates A3's counts, not merely derived rates.

**Scope of the corpus figures.** A3's and A7's numbers are corpus-wide across
the account's whole transcript root, not scoped to this repo's own sessions.
They are aggregate volume and turn-count statistics carrying no project
identifier, which is what makes them publishable under the repo's redaction
rules; M9 transcribes them at that same aggregate grain.

### Deliberate duplication — why M1 does not extract the shared check

`transcript-analysis.py:8192` and `:8452` state: *"The judgment-span state
machine is intentionally duplicated from cmd_audit_routing — tests
cross-validate the two copies to guard against drift,"* backed by
`test_cross_validation_with_audit_routing` at two sites. That is a named
exception to the single-source-of-truth rule, and collapsing the copies would
make those tests vacuous — one implementation cannot cross-validate itself.
The three existing sites also read `plan_mode_active` as a per-turn boolean
for classification, whereas M1 needs the boundary's turn *index*: a different
output shape, so "call the same helper from a fourth site" understates the
redesign. M1 adds a fourth copy, consistent with the established convention,
and leaves the three shipped subcommands untouched.

## Critical files

**Create:** none.

**Modify:**

- `claude/.claude/scripts/transcript-analysis.py` — `cmd_plan_boundary` +
  `_plan_boundary_report`, its `add_parser` registration, the M2 parallel-list
  extension, and M3's `cache_miss_reason` parsing.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — M8.
- `docs/transcript-analysis.md`, `docs/cost-levers-considered.md` — M9.
- `claude/.claude/skills/plan-it/SKILL.md` (Step 7) and
  `claude/.claude/skills/handoff/SKILL.md` (warrant section) — M7, gated.

**Reuse rather than reimplement** (all confirmed present):

| Helper | Line | Use |
|---|---|---|
| `_ramp_curve_from_corpus` | 9179 | Re-derive the ramp; built to avoid citing 3.55x |
| `_extract_rearm_session_turns` | 9106 | Single dedup+price pass per session (extended per M2) |
| `_simulate_rearm_spacing` | 9349 | Existing counterfactual-repricing shape for arm C |
| `_price_turn` / `_model_rates` | 5122 / 4947 | Per-turn dollars by class; per-model rate lookup |
| `_dedup_turns_by_request_id` | 4993 | The PR #622 fix — must be in the path |
| `_context_at_turn` / `_cache_write_split` | 4976 / 4961 | Context depth, 5m/1h write split |
| `_redact_session_id` / `_assign_root_scoped_redact_label` | — | M5's redaction contract |
| `iter_sessions` | 456 | Corpus walk, multi-root scope, sidechain-file distinction |

## M7 decision criterion — pre-committed

M7 ships only when **all five** hold; otherwise M9 records a null result and
the skills are untouched:

1. One arm's mean dollar advantage over the next-best arm exceeds the M6
   work-inflation breakeven at a **20% Sonnet turn-count excess**. The 20% is
   a stipulated stress margin, not a measured quantity — no data exists on
   Sonnet-vs-Opus turn-count variance for matched tasks (that is A1, which is
   unverifiable here by construction). It is chosen as a round, conservative
   floor; a future reader should not mistake it for a derived figure. Record
   the advantage at 0% excess alongside it, so the margin's effect is visible
   rather than baked in.
2. The direction is unchanged under **both** the pooled and the per-family
   ramp curve. If the corpus is too thin to split by family, this condition
   fails and M7 does not ship (A2 unresolved is a blocking state, not a
   footnote).
3. The direction is unchanged after the A7 denominator reconciliation.
4. The arm-B simulation reproduces observed cost on the real-switch sessions
   to within **±10% of mean per-session dollars** (Verification 7 — the
   ground-truth cross-check, not Verification 5's breakeven pin). The
   tolerance is fixed here rather than at implementation time specifically so
   it cannot be chosen after seeing the data to make the gate open.
5. Every cell the direction is read from holds **at least 20 sessions**, and
   the direction survives a bootstrap resample. A3's smallest cells are 8 and
   2 sessions; a point-estimate mean over those would flip on a handful of
   session swaps, so cells below the floor are reported but not gated on.

A bare mean advantage is explicitly *not* the bar — this repo's own precedent
rejected a lever whose 45.5% session-share showed it "merely permissible, not
better" (`docs/cost-levers-considered.md`, the
`handoff-boundary-decision-rule.md` section's `HANDOFF_NUDGE_ABS_CAP`
retuning row).

**Candidate M7 prose, per outcome** (so the product surface is reviewable now,
not authored at implementation time with the thinnest context):

- **Arm A wins** → no skill change; M9 records that the current
  context-only rule is correct as written.
- **Arm B wins** → Step 7 gains a third action ahead of the existing nudge
  check: when the session is Opus-anchored and a plan was just approved,
  switch to Sonnet in place, then apply today's continue-vs-handoff logic
  unchanged.
- **Arm C wins** → today's text, tightened: the nudge threshold stops being
  the floor for Opus-anchored sessions specifically. This branch is the one
  candidate that cannot be fully drafted now — the replacement condition is a
  number M6 produces (a lower context threshold, or an unconditional
  post-approval handoff). Whichever it is must be stated as an explicit rule
  at ship time; "tightened" is a direction, not shippable prose.

In every branch the added text must be **inert for sessions not anchored to
Opus** — the shared default is `sonnet`, so the common case must read and
behave exactly as today.

## Verification

1. **Unit tests (M8)** over synthetic JSONL fixtures, following the file's
   existing conventions: boundary detection for each signal (`ExitPlanMode`,
   `plan-review` invocation); no boundary; boundary as the final turn (the
   divide-by-zero guard); **boundary inside a sidechain, which must be
   ignored**; **a record with no `usage` block near the boundary**, which
   desyncs boundary position from `main_thread_turns` (the failure
   `test_synthetic_no_usage_record_does_not_desync_boundaries_from_main_thread_turns`
   guards this in the sibling code); **multiple boundaries in one session**,
   with the tie-break rule stated; **more than one model switch**;
   `cache_creation` present but zero; a **missing or malformed
   `cache_miss_reason`** (M3's one new parsed field, including a
   `previous_message_not_found` variant that carries no
   `cache_missed_input_tokens` at all); and an unpriced model, asserting it
   lands in `unpriced_turns` rather than pricing at zero.
2. **Redaction control tests (M5)**, in the shape siblings already use — a
   security control with no test is indistinguishable from an absent one:
   assert `plan-boundary` redacts by default, that `--no-redact` is refused
   when more than one root is in scope, and — as a negative assertion — that
   a fixture record carrying `plan` text and `planFilePath` produces output
   containing neither. Also assert that **no invocation across the
   subcommand's whole argument surface emits a per-session breakdown** — the
   aggregate-only claim needs the same untested-control-equals-absent-control
   treatment as the other two, or a convenience flag added later while
   iterating on Verification 7 ships permanently.
3. **Arm-B arithmetic pin**: assert the boundary+1 turn charges a Sonnet
   cache-write and **no** cache-read, and that later turns carry the observed
   split at Sonnet rates. Expected values derive from
   `_model_rates("claude-sonnet-5")` per token class — never a hardcoded
   `0.4`, which would restate G1 on both sides and pass even if the price
   table drifted. Assert at component level, not session total: one mispriced
   turn in ~129 will not surface in an aggregate.
4. **Arm-C arithmetic pin**: assert the `(output_tokens/1000) * rate`
   multiply-back convention against a synthetic fixture, guarding against
   scaling observed dollars by a ramp factor.
5. **Breakeven pin (M6)**: hand-computed fixture asserting breakeven turns
   against a known arm-A/arm-B delta. The breakeven is the mitigation for A1;
   an untested formula there puts the confidently-wrong-number risk on the
   mitigation itself.
6. **Reconcile A7 before treating A3 as settled**: run the subcommand and
   `cost` over the same single root, account for the file-count difference,
   and **re-report every A3 figure** under the reconciled denominator — the
   split counts (48/30/8/2, 23/14) *and* the median-129-post-boundary-turns
   statistic, since excluding sidechain files can change session membership
   and move the median too.
7. **Cross-check arm B against ground truth**, excluding those sessions from
   the ramp-curve corpus used to price them — otherwise the simulation is
   partly derived from the same sessions it is validated against, and a broken
   formula passes by reproducing corpus averages. If the holdout is not
   feasible, report the leakage explicitly rather than silently. **Dev-time
   only, enforced structurally:** this computation lives in a throwaway script
   outside `cmd_plan_boundary` (never a flag on it, since M5 forbids the
   shipped subcommand from emitting per-session figures), and its numbers are
   not transcribed into M9.
8. **Resolve A2**: run `_ramp_curve_from_corpus` per model family and report
   whether the relative curves agree; feed the result into M7 criterion 2.
9. Full suite and lint from the worktree: `../../../.venv/bin/pytest
   claude/.claude/`, `../../../.venv/bin/ruff check claude/.claude/`, and
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.

## Out of scope

- **Implementation-quality comparison between Opus and Sonnet** (A5).
- **Extracting the triplicated boundary-detection check** — deliberate,
  documented, and cross-validated by tests; see "Deliberate duplication."
- **Retuning `HANDOFF_NUDGE_ABS_CAP`** — measured and dropped in
  `handoff-boundary-decision-rule.md`.
- **Flipping the `settings.json` model default** — landed via PR #647, and
  `cost-levers-considered.md` records the cost-lever rationale for it as
  *refuted*, not merely stale.
- **Automating the decision in the nudge hook.** The prior plan rejected this
  because a hook cannot see remaining work. For a successor: a hook *can* see
  the model, and A3's median of 129 remaining turns weakens that objection.
  Not pursued here — it would turn a measurement into a hook change.
- **`docs/auto-mode.md` staleness.** Its plan-mode subsection still reads at
  the pre-PR-#647 state and lacks the corrected turn-count and
  escalation-rate figures — 129 of 131 matched dispatches resolved to Opus,
  and all 12 dispatches carrying an explicit `model: sonnet` param still
  escalated (see G3's source, `docs/case-studies/plan-mode-model-resolution.md`
  lines 54, 56). Real, but a different change — raising it to the reviewer
  rather than bundling it (scope Axis 1, bucket 3).

**Accepted outcome.** A null result — no arm clearing the M7 criterion, so
M1–M6, M8, M9 ship and the skills stay unchanged — is an acceptable end state
for this plan, not a failure of it. The reusable subcommand is the durable
deliverable; the verdict is the perishable one.
