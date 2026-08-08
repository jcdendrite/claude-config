# Re-unit the handoff nudge: threshold in absolute tokens, not percent of window

> Target filename on the implementation branch: `.claude/plans/absolute-token-handoff-threshold.md`.
> The current path is the harness-provided plan location, which resolves through the stow
> symlink into the **gitignored** `claude/.claude/plans/`. Committed plans live in the
> repo-root `.claude/plans/`; the file moves there when the branch is created, and only then
> does it become public and subject to the redaction rules below.

## Context

**Goal: bound the per-call prefix cost the handoff nudge exists to control, by parameterizing
its threshold in absolute tokens instead of percent-of-context-window. Directional expectation:
cost per merged PR falls from baseline ~$82 toward ~$70 (a ~15% reduction — see *Expected
effect* for why this is a target to aim at, not a pass/fail gate, and A10 for the unverified
number it rests on).**

PR #579 moved the handoff-nudge threshold from 60% to 40% of the model's context window,
grounding the number in a `context-distribution` query built for the purpose. The number was
picked correctly. The **unit** was not.

`nudge-handoff-near-context-cap.sh:163` computes `THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))`.
Percent-of-window is a *capacity* measure — it answers "how close is this session to running
out of room." Cache-read cost is linear in *absolute* tokens. The two coincide only when the
window is fixed. It is not: the hook resolves `CONTEXT_WINDOW` to either 200,000 or 1,000,000
(`:155-162`), so the identical rule fires at 80,000 tokens on one model and 400,000 on another
— a 5x difference in the dollar quantity the hook exists to control.

Every measured fire is on the large-window side. The hook's own log records 18 fires since
#579 added window logging: **all on 1M-window models (`claude-sonnet-5`, `claude-sonnet-4-6`),
minimum 433,055 tokens, mean 655,477, maximum 879,994.** At Sonnet's post-September cache-read
rate ($0.30/MTok), a 655,477-token prefix is **~$0.20 charged on every subsequent API call**
for the rest of that session. The nudge is not firing early; it fires after the cost it exists
to prevent is already locked in.

Corroborated by the repo's own cost report, which buckets on an *absolute* 200,000-token
boundary (`transcript-analysis.py:2895`): **61.1% of dollars sit above it** across all
projects, **58.6%** scoped to this repo. That boundary is 20% of a 1M window — half the
current nudge threshold. Most spend occurs in a region the nudge cannot reach on the models
actually in use. (Units differ between these two figures — see A2/A2b.)

**Why the prior investigation missed it.** `context-distribution`, the subcommand built to
ground the threshold, has the same unit. Its docstring frames each turn's context "as a
fraction of that turn's own model's context window" (`:3494-3503`), and its buckets are fixed
percentages at `:2919`. A query that can only report percentages cannot reveal that
percentages are the wrong variable. The 60%→40% move re-tuned the number *inside* the error.

**Metric reframed to $/merged-PR** (engineer decision, A9). The prior plan targeted a ~64.8%
blended volume cut against an 8-day dollar total. A denominator that moves with throughput
makes a genuine efficiency win read as a miss. This plan is measured per merged PR and states
plainly that its lever does not reach 65% alone.

**Disclosure boundary for this public repo.** This plan publishes the engineer's own OSS-repo
cost ($3,787.74 over 8 days scoped to this repo) and his own public PR throughput (46 merged),
yielding ~$82/PR. Both are his own figures, and the PR count is already derivable from this
repo's public history. **Withheld, as in the merged predecessor:** per-account totals, the
mapping of any account to a real engagement, and any per-session or per-project breakdown.
Note this denominator is narrower than the predecessor's four-account aggregate — recorded here
as a deliberate decision so the boundary is explicit rather than drifting by precedent. Publishing
both figures together lets a reader subtract and infer that roughly 65% of the predecessor's
8-day spend falls outside this named public repo — business-volume shape, not an engagement
identity, and no more than the predecessor's own text already disclosed ("four Claude accounts,"
mapping withheld). Accepted deliberately, not overlooked.

## Approach

Two steps, strictly ordered: extend the measurement to the correct unit, **then** pick a number
from the resulting curve and make the synchronized edit. Step 2 does not begin until step 1 has
run and produced output.

### Root problem and givens

**Root problem:** the nudge's trigger is expressed in units of context *capacity*, while the
cost it bounds is linear in *absolute* tokens; on 1M-window models the two diverge 5x and the
nudge fires far past the point of cost control.

| # | Given | Why it is fixed |
|---|---|---|
| G1 | Cache read is 0.1x base input, charged on the full prefix every call | Vendor pricing mechanic. `[verified: transcript-analysis.py:2858-2859 _PRICING_SOURCE_URL / _PRICING_FETCH_DATE (2026-08-02), re-verify by the file's own `_DEFAULT_REVERIFY_BY`]` |
| G2 | The 1M context window carries no per-token premium | Vendor pricing. This is *why* percent-of-window carries no price signal at large windows: capacity got 5x cheaper to occupy; cost per token did not move. G2 is the mechanism that makes the current parameterization wrong. `[verified: same source as G1]` — if a long-context premium exists above 200k that this repo's rate table does not model, the ≥200k region is super-linear and the ~15% figure and the chosen cap both move; re-check against `_MODEL_BASE_INPUT_RATES` at implementation time, not just at planning time. |
| G3 | Sonnet 5 base rates rise 2026-09-01 ($2→$3 in, $10→$15 out) | Vendor-set. Confounds any before/after window spanning the date — **applies to this plan's own success metric (Verification 6), not only to the `cost-trend` rejection below.** |
| G4 | A hook cannot mutate the model of the session it is observing | Harness contract — the hook runs inside a session already bound to a model, so it must adapt its threshold to whatever window it finds. **Narrowed:** which model sessions *default* to is set by this repo (`claude/.claude/settings.json:62`, currently `"opusplan"`) and is therefore within reach; that is not a given and is recorded in *Out of scope*. (The `opusplan` value selects a cost/capability *tier*, not a context window directly — the 1M window follows from which Sonnet generation the tier resolves to.) |

### Mechanisms

**M1 — Add absolute-token bucketing to `context-distribution`, in the hook's own unit**
*(anchors: root)*.

The measurement must express the answer before the answer can be picked — and must express it
in the **same unit the hook compares against**, or the number does not transfer.

**Critical unit correction.** The hook's `ESTIMATE` (`nudge-handoff-near-context-cap.sh:117-121`)
sums `cache_read_input_tokens + cache_creation_input_tokens + input_tokens + **output_tokens**`.
The script's `context_at_turn` (`transcript-analysis.py:2988`) sums
`input_tokens + cache_read + ephemeral_1h + ephemeral_5m` — **`output_tokens` is absent**. The
delta is one turn's output. A cap read off `context_at_turn` would sit systematically below the
quantity the hook tests, so the hook would fire earlier than the curve indicates. The new
absolute peak must therefore be accumulated with the hook's four-field sum, as a **second
per-session accumulator** — not derived from `peak_pct × window`, because on a mixed-model
session the argmax turn for percentage is not the argmax for absolute tokens (450k on a 1M
model is 45%; 90k on a 200k model is also 45% but five times cheaper).

**Also report session-share per absolute bucket, alongside dollar-share.** `docs/handoff-nudge.md:68`
names session-share as *the* frequency metric and records the 60%→40% move raising it up to ~4x.
Lowering the 1M trigger strictly increases fire frequency for every stow consumer; without
session-share, step 2 could pick a cap the prior standard would have rejected.

**Aggregate-only.** Absolute buckets are additional rows/columns on the existing aggregate
table — no per-session listing, no session IDs, cwd paths, branch names, or transcript content.
The merged predecessor stated this constraint for its own additions; it is inherited here
explicitly because step 2's "pick a number off the curve" framing invites eyeballing the tail
per-session, which is exactly what must not be added.

**Testability.** The bucket arithmetic currently lives inline in the report loop
(`:3566-3601`), so every existing test drives it through stdout column-splitting
(`test_transcript_analysis.py:2328` already documents that fragility). Extract the bucket
computation into a pure function over `(peaks, dollars)` returning rows, unit-test the
arithmetic there, and keep one formatting test. Without this, the new cases inherit the
coupling and the suite gets more brittle, not less.

**M2 — Change the hook's threshold to `min(percentage × window, absolute_cap)`** *(anchors: root)*.

Over-powered-primitive check — three lighter alternatives, each rejected with cause:

1. *A single absolute number, no percentage arm.* **Fails:** a cap tuned for a 1M window
   (~180k) is 90% of a 200k window — on small-window models it fires so late the session may
   auto-compact first, losing the capacity-safety role the hook also serves.
2. *Keep percent-of-window, lower the number further* (40%→18%). **Fails:** 18% of 200k is
   36,000 tokens, firing on nearly every small-window session; `docs/handoff-nudge.md:12`
   rejected 30% on exactly this over-trigger reasoning. One number cannot serve both windows —
   that restatement *is* the bug.
3. *A per-model-ID threshold table*, reusing the existing `case` statement. **Fails:** heavier
   in practice — a hand-maintained number per model ID, re-derived at every model launch,
   restating one absolute value across arms. `min(pct, cap)` derives what the table would
   duplicate.

**Implementation shape:** bash arithmetic supports the C ternary, so this is one expression, not
a branch: `THRESHOLD=$(( PCT_THRESHOLD < ABS_CAP ? PCT_THRESHOLD : ABS_CAP ))`. Verified
shellcheck-clean under this repo's `.shellcheckrc` and valid on stock macOS `/bin/bash` 3.2,
which is what `#!/bin/bash` resolves to for a stow consumer. Keep the source line matchable by
`test_doc_counts.py`'s ground-truth regex (see Critical files, step 2) — e.g. retain
`PCT_THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))` as its own assignment rather than folding the
multiplication into the ternary expression.

**Consumer override.** Add one line reading `HANDOFF_NUDGE_ABS_CAP` from the environment,
defaulting to the value step 1 grounds. The cap is derived from this author's own session-shape
curve (see *Expected effect* — Parity bound); a dissenting stow consumer's only control today is
the hook's existing all-or-nothing kill switch, and a one-line override costs nothing to add
while it is already being edited.

**Validate the override before use.** `HANDOFF_NUDGE_ABS_CAP` is consumer-supplied and feeds
directly into the ternary's arithmetic — this hook's own fail-open contract means a malformed
value must fall back to the default, not silently degrade `THRESHOLD` to `0`/empty. Bash treats
an unset-variable-shaped value (e.g. `abc`) as `0` in arithmetic context, and a zero-padded value
(e.g. `080000`) as an invalid octal literal that leaves `THRESHOLD` unset — either way the
downstream `[ "$ESTIMATE" -lt "$THRESHOLD" ]` compares false and the hook **fires on every
session**, the opposite of "override ignored." Guard with a non-negative-integer check before the
arithmetic line, mirroring this file's existing treatment of untrusted input
(`_lib_valid_session_id_component` for `SESSION_ID`): `case "$HANDOFF_NUDGE_ABS_CAP" in
''|*[!0-9]*) ABS_CAP=<step-1-grounded-default>;; *) ABS_CAP=$HANDOFF_NUDGE_ABS_CAP;; esac`.

**M3 — Make the injected nudge string state the computed threshold instead of asserting a
literal** *(anchors: row A3)*. The string at `:186` hardcodes "past 40% of this model's context
window," which becomes false whenever the cap binds. Pass the computed `THRESHOLD` into `jq`
via `--argjson` so the text is true by construction.

**Correction to an earlier draft of this plan:** this does **not** remove an entry from the
`DocCountFact` registry — that fact's occurrences (`test_doc_counts.py:266-297`) are three in
`docs/handoff-nudge.md` and three in `README.md`; the hook is the fact's *ground-truth source*,
never an occurrence, and the `:186` literal has never been test-enforced at all. M3's real value
is removing a hand-synced literal that **no test would catch drifting** — and it therefore
requires a *new* positive test (see Verification 5), because after M3 nothing verifies the
`--argjson` wiring.

**Ordering hazard.** `jq -n … 2>/dev/null || true` (`:183-188`) swallows every failure, and both
the `nudged` log line (`:179-180`) and the one-shot marker (`:181`) are written *before* it. A
jq failure would burn the session's single shot, log success, and emit nothing — silently and
unfalsifiably. The hook deliberately omits `set -e` (`:24-26`), so reordering the writes alone
does not fix this — `|| true` still swallows a failure regardless of statement order. Capture
jq's output and test it explicitly before writing the marker/log:
`OUTPUT=$(jq -n … 2>/dev/null) && [ -n "$OUTPUT" ] && { <write log+marker>; printf '%s' "$OUTPUT"; }`.
The capture-then-test form makes the log/marker write wait on this `jq` call's completion, where
it previously didn't block on it; the file's earlier `tail | jq -s` call is wrapped in `timeout 2`
for exactly this reason, and this one currently isn't — wrap it the same way while the line is
already being touched.

### Assumption ledger

| Row | Assumption | Tag |
|---|---|---|
| A1 | The nudge fires at mean 655,477 tokens on 1M-window models | `[verified: ~/.claude/.handoff-nudge.log, 18 post-#579 fires carrying the window field; min 433,055 / max 879,994; all claude-sonnet-5 or claude-sonnet-4-6 — re-derived this session]`. n=18 is a thin baseline; noted wherever the mean is quoted. |
| A2 | 61.1% of dollars occur at ≥200k `context_at_turn` (58.6% scoped to this repo) | `[verified: transcript-analysis.py cost --since 8d, and again with --this-repo — re-derived this session]` |
| A2b | A1 and A2 are in **different units** — A1 includes `output_tokens`, A2 does not | `[verified: nudge-handoff-near-context-cap.sh:117-121 vs transcript-analysis.py:2988, both read this session]`. They are not directly commensurable; the "half the current threshold" comparison in Context is directional, not arithmetic. **Scoped correction:** M1 eliminates the divergence only *within* `context-distribution`'s new absolute-peak accumulator (which is built in the hook's unit specifically so step 2 can read a transferable number); it does not touch `cost`/`cost-trend`'s existing `context_at_turn` bucket, which keeps the output-token-free unit — realigning that is Out of scope. |
| A3 | The nudge text at `:186` asserts the threshold as literal fact to the model | `[verified: nudge-handoff-near-context-cap.sh:186, read this session]` |
| A4 | `context-distribution` reports only percentage buckets | `[verified: transcript-analysis.py:2919, docstring frame :3494-3503]` |
| A5 | ~$82/merged-PR (46 PRs, $3,787.74, 8 days, this repo) | `[verified: gh pr list --state merged --limit 200 --json number,mergedAt filtered on mergedAt, paired with cost --since 8d --this-repo so numerator and denominator share a scope — re-derived this session]`. **`--limit 200` is load-bearing: `gh pr list` defaults to 30, which would truncate 46 and inflate $/PR by ~50%.** |
| A6 | The ~65% blended target is unreachable by this lever alone | `[derived, conditional on A10]` — the ceiling arithmetic in *Expected effect* is straightforward given the ≥200k dollar-share figures (A2, solid), but its magnitude depends on A10's conversion rate, which is `[unverified]`. The *direction* of this conclusion (this lever alone cannot reach 65%) is robust to A10's exact value — even a much higher conversion rate leaves the ≥200k bucket only partially recovered — but the specific ~15%/~25–40% figures should be read as bounded estimates, not measurements. |
| A7 | `min(pct, cap)` leaves 200k-model behavior unchanged | `[verified: 200000 × 40 / 100 = 80,000, below every candidate cap]`. Holds **only while the cap exceeds 80,000** — see A8. |
| A8 | The correct absolute cap is readable off an absolute-bucketed distribution | `[unverified]` — the query does not exist yet. **Two stop conditions:** (a) if the curve is too flat to justify a value, step 1 ships alone as a measurement-only change; (b) if the curve argues for a cap **below 80,000**, the 200k arm moves too, `SMALL_THRESHOLD` is no longer unchanged, and that is a different and larger change requiring re-planning rather than a number swap. **A second band matters below 135,000** (not just below 80,000): a cap in 80,001–135,000 doesn't move the 200k arm but does invert `test_old_120k_constant_no_longer_fires_on_1m_models`'s GH-556 regression pin — see the step-2 test-file bullet in Critical files for the required extension. |
| A9 | ~65% remains the aspirational goal, tracked as $/merged-PR | `[engineer-verified]` — confirmed this session, including the metric reframe. A6 and A9 coexist as *goal vs. this-plan's-contribution*: the goal is unchanged, this plan's contribution to it is bounded and stated. **Flagged, not resolved here:** *Expected effect* states no combination of config-side changes reaches 65% at all — a scope claim stronger than "this plan alone doesn't." Whether that stronger claim also holds, or whether some future config-side lever could still close the gap, is the engineer's call, not a conclusion this plan resolves unilaterally against an `[engineer-verified]` row. |
| A10 | Roughly a quarter of nudge fires convert to a handoff | `[unverified]` — **no tooling produces this number.** `cmd_handoff_ratio` (`:3689`) measures handoffs vs *compactions* per ISO week; nothing joins the nudge log to handoff events. Treated as a rough prior, not evidence. Verification 6 adds the instrument that would ground it. The Goal's ≤$70/~15% figure is downstream of this row — read it as directional until Verification 6 runs. |

### Expected effect

**Dollar side.** The ≥200k region is 58.6–61.1% of spend, but this lever recovers only the
*marginal prefix above the new cap* — a session handing off at 180k still pays for the first
180k. Expected saving is `P(convert) × Δprefix × remaining-calls`. With conversion at roughly a
quarter (A10, unverified), the credible claim is a **~15% reduction in $/merged-PR**, hence the
≤$70 target. Independent estimates put the ceiling across *all* config-side levers at ~25–40%
blended. **This does not reach 65% alone, and no combination of config-side changes does** — the
remainder lives in the second term of `prefix × calls`, session and PR count, which no config
edit touches.

**Interruption side — the cost being traded.** Benefit accrues on the ~25% of fires that
convert; the interruption is paid on **100%** of them, and the fire count itself rises because
the trigger drops. That asymmetry is the real risk, and it is why step 1 must report
session-share and step 2's gate must carry an over-trigger ceiling.

**Parity bound.** The change is confined to 1M-window sessions. A consumer whose model mix is
mostly 200k gets zero behavior change and zero benefit (A7), while a 1M-mix consumer receives a
cap tuned to this author's session-shape curve. The hook's only consumer control is its
all-or-nothing kill switch; a dissenting consumer can disable it wholesale but cannot retune it.

**Known regression, shipped knowingly.** The nudge is one-shot (`:166-173`). Lowering the fire
point spends that single shot earlier and **widens the unwarned tail**: a session that reaches
879,994 tokens would go from ~480k of post-nudge silence to ~700k *at a candidate cap of
~180,000* — the exact figure depends on step 1's grounded value. Re-arming at escalating bands
is the direct fix; it is deferred (see *Out of scope*) not because it is a large second surface
— M3's `--argjson` wiring and the per-session marker path already make a second band a small
addition — but because **it needs its own frequency evidence**, which this plan does not yet
have. Verification 6's conversion instrument is what would ground that evidence.
`docs/handoff-nudge.md:67` must be updated to say the one-shot limitation is now materially
worse, rather than leaving that discovery to a future reader.

## Critical files

### Step 1 — measurement (ships first, independently mergeable and revertible)

- `claude/.claude/scripts/transcript-analysis.py` — absolute-token buckets in
  `cmd_context_distribution` (`:3476`). **The dual-accumulator logic (M1's "Critical unit
  correction") must itself be extracted as a pure function over a turn sequence** — not just the
  downstream bucketing — and unit-tested with a mixed-window session where the argmax turn for
  absolute tokens differs from the argmax turn for percentage. The existing loop
  (`:3566-3601`) tracks only `peak_pct`; the new peak needs the hook's four-field sum
  (`input + cache_read + cache_creation + output`), which means either reading `usage` directly
  in the loop or extending `_price_turn`'s (`:2970-2988`) return shape — the plan defers that
  choice to implementation but the seam must be pure-function-testable, not stdout-only, same as
  the bucketing itself. Note `_cache_write_split` (`:2951-2963`) already establishes the
  equivalence between the hook's flat `cache_creation_input_tokens` field and the script's nested
  `cache_creation.ephemeral_*` split — the two sums agree because of that documented invariant,
  not by coincidence.
  Add an absolute-bucket constant tuple beside `_CONTEXT_DISTRIBUTION_THRESHOLD_PCTS` (`:2919`).
  **Reuse:** `_context_window_for_model` (`:2922`) and the existing record traversal — no new
  parsing pass. **Do not unify with `_CONTEXT_BUCKET_THRESHOLD`** (`:2895`, compared in
  `_context_bucket` at `:2967`, consumed by `cost`/`cost-trend` at `:3313`/`:3663`/`:3429`): that
  is a fixed *reporting* bucket edge, whereas this is a *candidate-threshold sweep* — the
  absolute sibling of the percentage tuple. Different questions; state this in a comment so a
  future reader does not merge them.
  **Also update, all now stale under M1:** the mirror comment at `:2899-2903` ("a threshold
  percentage computed here means the same absolute token count the hook would compute" — no
  longer true once the hook's threshold is `min(pct, cap)`), the constant comment at
  `:2917-2918` ("crossing-count/dollar-share table" — becomes crossing-count/dollar-share
  *and session-share*), and the docstring at `:3488-3503` (documents only the percentage
  framing). State clearly, in the output itself, which field set belongs to the percentage rows
  and which to the absolute rows — the two share a report and must not be presented as one
  undifferentiated table.
- `claude/.claude/scripts/transcript-analysis.py:4891-4894` — argparse help hardcodes
  `"(30/40/50/60%)"`; ships stale otherwise.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — new cases, and
  `_extract_context_distribution_row` (`:2328-2343`) is used by 5 call sites across 4 test
  functions (`:3796,3804,3820,3839,3878`) and matches rows positionally on
  `tokens[0] == f"{pct}%"`. Decide and state the output shape (second table vs added columns);
  either way the helper must be section-scoped or it will silently read the wrong table. Also
  cover: unknown model ID still contributing to an absolute bucket (the absolute path doesn't
  need `_context_window_for_model` at all — worth pinning as its own invariant), the empty-scope
  zero-division path already guarded by `_pct_of` (`:3006`) extended to the new rows, and a test
  that the four existing percentage rows are byte-identical before/after this change. **Also
  cover:** the session-share computation itself, on the extracted pure function directly — a
  synthetic multi-session fixture with a known session-share fraction, asserting the returned
  value (not just that a column renders) — because Verification 2's 50% over-trigger ceiling is
  only as trustworthy as this arithmetic. **Also cover:** a fixture with one turn's
  `cache_creation` expressed via the hook's flat `cache_creation_input_tokens` field and one via
  the script's nested `ephemeral_*` split, asserting both feed the new absolute-peak accumulator
  to the identical total — pinning the equivalence M1 leans on (`_cache_write_split`) rather than
  only citing it.
- `docs/handoff-nudge.md:12` — one prose touch, so the "re-run the same command" instruction
  still describes a command that exists even if step 2 never lands (A8 stop-path).

### Step 2 — the synchronized edit (**one commit**; not piecewise-revertible)

`_count_handoff_nudge_threshold_percentage` raises the moment the literal's shape changes, so
hook + docs + README + both test files must land and revert together.

- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — `:4`, `:31-33` (the unlisted-model
  default is documented as "may never fire," which stops being true once a cap binds), `:145`,
  `:163` (ternary per M2, plus the `HANDOFF_NUDGE_ABS_CAP` override), `:179-188` (jq
  capture-then-test + `--argjson` per M3).
- `docs/handoff-nudge.md:5,7-10,12,67,68,70` — prose, the per-model threshold table, the
  unlisted-model limitation bullet (`:70`, not just `:68`), the "Why 40%" derivation rewritten
  as a "why this cap" derivation citing step 1, and the one-shot regression note. **Reformat the
  threshold table's numbers from space-separated thousands (`80 000`, `400 000`) to plain digits
  (`80000`, `400000`)** — this is what dissolves the `DocCountFact` parser problem below rather
  than patching around it.
- `README.md` — `:411` carries **two** registered patterns in one paragraph (`suggests
  \`/handoff\` at ~N% context usage`, and `every turn beyond N% is waste`) plus `:423`
  (`~N%: suggested threshold`) — three occurrences across two lines, not "both mentions." All
  three need rewording that no longer names a percentage.
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` — `:5` docstring,
  `LARGE_THRESHOLD`/`SMALL_THRESHOLD` (`:41-42`, plus the new `EFFECTIVE_SMALL_THRESHOLD` per the
  collision-probe fix below), the collision probe (`:533-546`) plus its positive control,
  **plus two new cases the collision probe doesn't cover:** (a) `HANDOFF_NUDGE_ABS_CAP` —
  override set → effective threshold reflects it; unset → default; malformed (non-numeric,
  zero-padded, empty) → falls back to the default rather than degrading `THRESHOLD` toward
  `0`/unset (see M2's validation addition above — this is the test that would have caught the
  fire-on-every-session failure mode); (b) the **true no-match default arm** — an unlisted model
  ID with no colliding `case`-statement prefix, a different path from the collision probe's
  mismatched-prefix arm, asserting its effective threshold now reflects the cap rather than the
  old "may never fire" assumption `:31-33`'s doc update states. And
  **every test in this file that hand-computes `window * 40 // 100` or hardcodes the
  400,000/80,000/135,000 constants** — not only `test_fires_at_exactly_threshold_for_model`
  (`:455-462`), but its direct sibling `test_silent_one_below_threshold_for_model` (`:465-472`,
  same computation) and `test_old_120k_constant_no_longer_fires_on_1m_models` (`:474-481`, a
  named GH-556 regression pin that inverts if the cap lands at or below 135,000 — extend A8's
  stop condition (b) to cover this band, not only sub-80,000 caps). **Replace every one of these
  computed expressions with a literal expected-threshold table** (`model → effective_threshold`);
  mirroring the production formula in the test would make each one tautological on formula
  shape. Grep the file for `40 // 100`, `LARGE_THRESHOLD`, and `SMALL_THRESHOLD` before starting
  this step to confirm no further sibling was missed — do not rely on this plan's own enumeration
  as exhaustive.
- `claude/.claude/hooks/tests/test_doc_counts.py` — the registry now needs **two** facts
  (percentage and absolute cap), unblocked by the docs reformat above:
  `_count_handoff_nudge_threshold_percentage`'s ground-truth regex (`:181-192`) must also be
  updated for the `PCT_THRESHOLD=` line name change. At least four of the six current occurrences
  (`:266-297`) do **not** split cleanly on their own: `docs/handoff-nudge.md:7`'s `Threshold
  (40%)` is a column header over two *derived* values, and the three README occurrences above
  become false on 1M models and move to the cap fact once reworded to absolute tokens. Resolve
  the occurrence-to-fact mapping explicitly before editing, and prefer deriving the cap
  ground-truth **behaviorally** (fire the hook on a 1M model, read the effective threshold from
  its output) over regexing an `ABS_CAP=` literal, so the fact doesn't require yet another
  source-scanning regex on top of the percentage one.
- `claude/.claude/skills/handoff/SKILL.md` (or its implementation) — append
  `handoff session=<id>` to `~/.claude/.handoff-nudge.log` on completion, per Verification 6's
  conversion instrument. One line; no new file, no new log.

### The collision probe — re-derivation, concretely

`:533-546` writes `LARGE_THRESHOLD - 1` for an unlisted model ID and asserts silence, guarding the
`case`-statement prefix-collision logic. Under `min(pct × window, cap)` with
`LARGE_THRESHOLD := cap`, it **discriminates only for `80_001 ≤ cap ≤ 400_000`**:

- At `cap ≤ 80_000` both the correct and buggy arms collapse to `cap`; `cap-1` is silent either
  way and **the probe goes green on a broken glob** — silent loss of the GH-556 regression pin.
- At `cap ≥ 400_001` correct code fires at `cap-1` — **false red**.

Fix: stop parameterizing the probe on the cap. Use `SMALL_THRESHOLD` (80,000) as the estimate —
the buggy arm fires at exactly 80,000, the correct arm stays silent for any `cap > 80,000` — and
add a module-level assertion that the cap sits in the valid range, so an out-of-range value
fails loudly instead of silently defanging the probe. Keep `SMALL_THRESHOLD` meaning "40% of
200k" as today; introduce a distinct name for the *effective* 200k threshold under `min()`
(`EFFECTIVE_SMALL_THRESHOLD`, currently equal to `SMALL_THRESHOLD` per A7) rather than
overloading one symbol with two meanings. Also add a **positive control**: assert the same
colliding model ID *does* fire at the cap — the existing probe only proves "doesn't match the
200k arm," not "correctly falls to the 1M arm and respects the cap there."

## Verification

1. **Baseline first, with exact commands recorded.** `cost --since 8d` and
   `cost --since 8d --this-repo`, plus
   `gh pr list --state merged --limit 200 --json number,mergedAt` filtered on `mergedAt`.
   `cost --since` is now-relative and cannot be re-derived later, so capture at a fixed date.
   Also record the nudge log's line count, so post-cap fires are separable from pre-cap ones.
2. **Step 1 gates step 2.** Run the new absolute-bucketed `context-distribution` across the
   account config directories. Apply both A8 stop conditions. Step 2's pick rule carries a
   **numeric over-trigger ceiling: reject any cap whose session-share exceeds 50% on any
   account.** Do not simply "inherit `docs/handoff-nudge.md:12`'s rejection criterion" —
   `docs/handoff-nudge.md:68` records the 40% threshold that already shipped landing at 50.0%
   session-share on one account, which is inside the band `:12` used to reject 30%. Citing that
   criterion without a number would let step 2 both admit and reject the same value; 50% is the
   ceiling the current threshold itself already establishes as shipped-acceptable.
3. **Per step:** `../../../.venv/bin/pytest claude/.claude/`,
   `../../../.venv/bin/ruff check claude/.claude/`, and
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` from the worktree.
4. **Sync coverage matrix, replacing a red-test spot check.** Reverting a registered occurrence
   is red by construction, and reverting the ground truth reds all six at once — neither proves
   per-site sync. Instead, enumerate every site step 2 touches and mark it registry-covered or
   not. The hook's `:4`/`:145` comments, `docs/handoff-nudge.md:12`, `:67`, and `:70`, and the
   test docstring at `:5` are **not** registry-covered today; either register them or record why
   not.
5. **Positive behavioral test for M3, both directions.** Fire the hook against a 200k model and
   a 1M model and assert the emitted `additionalContext` contains that model's computed
   threshold — the only thing catching a `CONTEXT_WINDOW`-instead-of-`THRESHOLD` wiring slip, and
   nothing covers it today. Pair the success-path assertion (stdout non-empty when the cap binds)
   with a **deny-path test**: force a jq failure and assert the marker is absent and no `nudged`
   line is logged — the capture-then-test fix in M3 needs both directions covered, or a
   regression to the original swallow-and-succeed shape would pass every existing test.
6. **Effect measurement, on the declared metric, timed around G3.** Re-derive **$/merged-PR**
   over a matched-length window (`cost --since Nd --this-repo` with the same `gh pr list`
   command). G3's 2026-09-01 repricing confounds this measurement exactly as it confounds
   `cost-trend` — a ~50% base-rate jump can swamp a ~15% lever. Either complete the after-window
   entirely before 2026-09-01, or recompute both windows' dollars at fixed pre-September rates
   before comparing; do not compare a pre-September baseline to a post-September measurement at
   face value.
   `cost-trend` is **not** the primary signal — ISO-week bins only (`:3657-3658`), no
   `--since`/`--until` (`:4921-4926`), >3x week-over-week noise, and it carries the same
   repricing exposure.

   **Conversion instrument — use the lighter primitive.** Grounding A10 needs to know whether a
   nudge fire was followed by a `/handoff` in the same session. Joining `.handoff-nudge.log` to
   transcript content (the shape `cmd_handoff_ratio` at `:3689` already uses) is heavier than the
   question requires and opens a new session-keyed read surface. Instead, have the `handoff`
   skill (`claude/.claude/skills/handoff/`) append one line to the *same* log file the hook
   already writes — `handoff session=<id>` — on completion. Conversion becomes a within-one-file
   count over data the hook already emits: no transcript scan, no join, no persisted
   session-keyed intermediate. Report only the aggregate conversion rate; never a raw log line
   or `session=` value (a UUID) outside that one file.

## Out of scope

- **The repo-set default model** (`claude/.claude/settings.json`). This repo chooses the default
  for every stow consumer, and that default is why all 18 measured fires are on 1M-window
  models. It is within reach — but changing a model default to simplify a threshold calculation
  inverts the priority, so it is declined deliberately rather than treated as fixed (G4).
- **Model-routing truth-up — a second PR.** `CLAUDE.md:77` asserts "`Explore` is pinned to
  Haiku"; measurement (this session, via `agent-*.meta.json` dispatch records joined to their
  sidechain's own `assistant.message.model`, across this account's config directory — not
  this-repo-scoped) shows 78 of 111 `Explore` runs on Opus. Separately, `staff-*` agents carry
  `model: sonnet` frontmatter yet were measured running Opus end-to-end — cause unidentified,
  so it needs a bounded verification step before any fix is designed.
- **Static-prefix trimming.** Real but small: an always-on floor of ~15,700 tokens against a
  ~177,761-token per-call prefix (~8.8%) — the denominator is the predecessor plan's own figure,
  scoped to the largest account, not this repo. The one clean win — skill descriptions loading
  twice in this repo, ~1,950 tokens of pure duplication — deserves its own change.
- **Re-arming the nudge at escalating bands.** `docs/handoff-nudge.md:68` deferred a two-tier
  nudge pending evidence that dismissal is material; that condition now looks met, and this plan
  *worsens* the tail it addresses (see *Expected effect*). Still deferred, but not for the
  original reason: M3 already removes most of the implementation cost (per-session marker path,
  `--argjson`-driven string), so the true blocker is that re-arming needs its own frequency
  evidence, which this plan does not have until Verification 6's conversion instrument runs.
  The regression is written into `docs/handoff-nudge.md:67` rather than left implicit, and this
  is the natural first follow-up once that evidence exists.
- **A delegation-discipline pilot — closed, not deferred.** 71.2% of tool-result bytes already
  land in sidechains, and the effect is unmeasurable in the available window (ISO-week
  granularity, ~25 days of history, >3x noise floor, Sept 1 repricing inside any powered window).
- **Sonnet→Haiku routing — closed on prior evidence.** `docs/case-studies/check-runner.md`
  records a Haiku agent built for this purpose, six documented incidents, and its retirement;
  the "cheap model absorbs verbose output" argument is measured-dead there.
- **Realigning the `cost` report's fixed ≥200k bucket edge.** Once a cap lands, `≥200k` stops
  tracking the quantity being controlled and A2's headline metric decouples from the lever.
  Named here as explicit follow-up rather than left to drift.
- **Adding $/merged-PR to `transcript-analysis.py`.** Computable today by pairing
  `cost --this-repo` with `gh pr list`; automating it is a tooling change.
