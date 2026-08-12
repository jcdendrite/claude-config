# Backtest the handoff-nudge re-arm band spacing

## Context

**Goal: replace the projected saving from re-arming `nudge-handoff-near-context-cap.sh`'s
one-shot nudge with a measured backtest, before Phase 3 ships a spacing value on
guesswork.** This is Phase 2 of `.claude/plans/token-cost-reduction.md`. Phase 1 (the
`requestId` dedupe fix) shipped and merged as PR #622, correcting all downstream dollar
figures ~2.1x — this plan runs after that fix specifically because its own output would
otherwise be inflated by the same factor and would drive Phase 3's spacing choice to the
wrong value.

The parent plan's own assumption ledger flags the spacing value as `[unverified]` twice
over: "A threshold in the 120-180K range materially lowers `C_bar`... Phase 2 backtests it
against the recorded corpus and picks the value; nothing ships before that," and "Handoff
re-establishment cost is lower than the context it avoids... Phase 2 must model it
explicitly, since a too-low threshold inverts the trade." Both are this plan's job.

**Scope, per the parent plan:** only re-arm band *spacing* past the fixed `360000` `ABS_CAP`
is backtested. The fire threshold itself is not retuned (PR #605's U-shape analysis already
settled that — see parent plan Phase 3). Session-split threshold backtesting and model
routing are both explicitly out of scope for this phase; neither is replay-testable here
and the report must say so rather than silently omit them.

## Approach

**New CLI subcommand `rearm-backtest` in `claude/.claude/scripts/transcript-analysis.py`**,
following the file's existing `cmd_X(args)` → `_X_report(args, today, roots)` split (used by
`cost`, `context-distribution`, `cost-trend`, `handoff-ratio`) so the wall clock is read
exactly once at the CLI boundary and every report function stays testable without a live
clock. `cmd_rearm_backtest` resolves roots via `_resolve_scan_roots`/`_resolve_cost_roots`
and scope via `_resolve_project_scope`, exactly like every sibling subcommand.

**Alternative considered and rejected: a standalone new module.** The backtest needs
`_dedup_turns_by_request_id`, `_price_turn`, `_context_at_turn`, `_context_window_for_model`,
`_is_fresh_user_prompt`, `_resolve_scan_roots`, `_resolve_cost_roots`, `_resolve_project_scope`,
`_add_project_scope_args`, `_print_resolved_scope`, and `config_dir`/`declared_transcript_roots`
— twelve helpers, all but the last two (`config_dir`, `declared_transcript_roots`)
underscore-prefixed private functions of `transcript-analysis.py`. Importing them
across a module boundary means either making ten functions public (churn well beyond this
phase's scope) or reaching into another module's "private" internals, which is worse than
living in the same module. Every existing report (`cost`, `context-distribution`, `cost-trend`,
`handoff-ratio`, `edit-format`, `subagents`) already lives in this one file as a subcommand —
that is the established pattern here, not an accident to route around.

### Re-establishment ramp cost: re-derived from the corpus, not PR #605's table

The parent plan says to charge "PR #605's measured 3.55x rebuild ramp." That table's own
source document says otherwise: `.claude/plans/handoff-boundary-decision-rule.md` states
"These figures come from one-off analysis scripts run against local transcripts, not from a
subcommand this repo ships... the scripts are not committed... treat the table as a
point-in-time measurement, not a reproducible report." Citing a number its own source calls
non-reproducible violates CLAUDE.md's Ground-Every-Choice rule for quantitative claims.
**Engineer-confirmed this session:** re-derive the ramp curve from the corpus instead.

`_ramp_curve_from_corpus(sessions) -> tuple[dict[str, dict[str, float]], int]` computes $/1k-output-tokens
bucketed by turn-index-since-session-start, over main-thread turns only, reusing
`_price_turn`/`_dedup_turns_by_request_id` exactly as `cost` does. Bucket edges mirror PR
#605's bands (`0-5`, `5-10`, `20-40`, `40-80`, `80-150`, `150-300`, `300+`) for
comparability with the prior point-in-time figure, but the multipliers themselves come from
this run's corpus every time the subcommand runs — the backtest can never go stale the way
the PR #605 table already has, and a reviewer can re-run it to check the cited multiplier
against the number the report just printed. The returned `int` is the corpus's own total
priced output-token count, letting the report print an explicit warning when it's zero
(every turn in scope unpriced) instead of silently emitting a $0.00 ramp for every spacing —
a distinction each bucket's own rate/mean_context can't make on its own, since both default
to 0.0 in that case too.

### Detection-sampling realism folds into the compliance split, not a third axis

The parent plan requires two figures per spacing: a perfect-compliance ceiling and a
compliance-realistic figure, because the operator-compliance assumption is
`[unverified]` and contradicted by logged evidence. Real detection lag (`nudge-handoff-
near-context-cap.sh` samples only at `UserPromptSubmit`/`Stop`, not every API call — defect
2, which Phase 3 deliberately does not fix) is a second reason a naive backtest would
overstate Phase 3's benefit: assuming every band crossing is caught at the very next API
call ignores a real, persistent blind spot. Rather than a third modeled axis, both lags fold
into the same two-figure split the parent plan already asks for:

- **Perfect-compliance ceiling** — split each session at the first *hook-observable
  boundary* at or after a band crossing. `_hook_observable_boundaries(records)` identifies
  these boundaries by reusing `_is_fresh_user_prompt` (:102-126, already filters
  `isSidechain`/`isMeta`/`isCompactSummary`/tool-result-bearing records — not reimplemented)
  to mark the record before each genuine user-authored message, **plus two boundaries
  `_is_fresh_user_prompt` alone can't supply: session start, and session end.** The
  session-end boundary matters concretely: the hook's own header comment states it is
  "registered on both events so a session that crosses the threshold on its final turn, with
  no further user prompt, still gets warned" (`nudge-handoff-near-context-cap.sh:6-9`) — a
  boundary set with no session-end entry would make a last-turn crossing invisible to the
  simulation, silently understating Phase 3's benefit on exactly the long-single-shot
  sessions (PR #609-shaped) that motivate this whole plan. This is "perfect" only in the
  sense that the operator acts on the nudge the instant it's technically visible.
- **Compliance-realistic** — the same boundary detection, plus an empirically-derived
  operator-response lag: `_operator_response_lag_from_log` joins `nudged` lines in
  `.handoff-nudge.log` to each logged session's own recorded trace (peak context reached
  after the nudge fired) to measure how far past the fire point sessions in *this* corpus
  actually kept running, refreshed from the full current log (324 lines as of this writing)
  rather than reusing the four data points the parent plan cites from an earlier read of a
  shorter log. Sessions whose `nudged` line can't be joined to a session in the current
  scope (log entry from a since-deleted transcript, or from an account/root outside the
  resolved scope) are excluded and the excluded count is reported, not silently dropped.

### Core replay

`_simulate_rearm_spacing(main_thread_turns, boundaries, spacing, ramp_curve, threshold)`
walks a session's boundaries in order. Each time an unfired band is crossed — first fire at
`threshold`, then every `spacing` tokens past it — it splits the session there: dollars
before the split are the actual recorded dollars (context growth up to that point is
identical to what really happened, nothing counterfactual); dollars after the split are
re-priced by mapping each post-split turn's distance from the split to a "turns since a
fresh session start" position and applying `_ramp_curve_from_corpus`'s rate at that position
to the turn's *actual recorded output-token volume* (work stays constant; only the
context-depth-driven rate changes). If the remainder itself crosses another band before the
session ends, the split repeats on the remainder — this is what re-arming is for, and is
where a one-shot baseline (today's actual behavior) diverges from a re-armed spacing.

`_HANDOFF_NUDGE_ABS_CAP = 360_000` is a new dedicated module constant (the existing
`360_000` in `transcript-analysis.py` today lives only inside
`_CONTEXT_DISTRIBUTION_THRESHOLD_ABS`, a candidate-sweep tuple, not its own symbol) with a
comment cross-referencing the hook's `ABS_CAP`, following the same cross-language
duplication-with-comment precedent `_context_window_for_model`'s docstring already uses
("mirrors the bash hook's `CONTEXT_WINDOW` case statement exactly") — there is no mechanism
to literally share a constant between a bash hook and a Python script, so a named,
commented, independently-tested duplicate is the established pattern, not a new one. Not
exposed as a CLI flag: the parent plan's Phase 3 explicitly keeps `ABS_CAP` fixed, and a flag
would invite a future run to quietly retune it.

**Output:** one row per candidate spacing (default `40000,80000,120000`, matching the parent
plan's example set) plus an unmodified baseline row (today's real recorded one-shot totals,
i.e. spacing = never-re-arm) — for each: predicted total $, predicted `C_bar`, delta vs
baseline, under both the perfect-compliance and compliance-realistic models. The report
prints an explicit line stating model-routing and session-split-threshold retuning are out
of scope for these figures, so a reader can't mistake "spacing-only" for "everything."

### Assumption ledger

**Root problem:** the parent plan's Phase 3 spacing value is currently a guess
(`[unverified]` twice over in its own ledger); shipping it unbacktested risks a spacing that
either leaves the unwarned tail nearly as long as today's one-shot behavior (too wide) or
triggers so many handoffs that the 3.55x-order rebuild ramp dominates the very savings it's
meant to produce (too narrow).

**Givens** (fixed, outside this phase's reach):
- Model routing is not replay-testable from session JSONL alone — routing depends on
  `opusplan` behavior Phase 5b is still investigating, a separate, not-yet-answered question
  this phase cannot resolve; no amount of replay logic dissolves the dependency because the
  data this plan can see doesn't carry the routing decision. `[verified: .claude/plans/token-cost-reduction.md Phase 5b]`

`ABS_CAP=360000` and the hook's `UserPromptSubmit`/`Stop`-only sampling are **not** givens
here, despite both being fixed inputs to this phase's model — both live in editable repo
artifacts (`nudge-handoff-near-context-cap.sh`, `claude/.claude/settings.json`'s hook
registration) that a sibling phase of this same parent plan touches, and the parent plan's
own text calls the sampling scope "a deliberate decline, not a platform limit." Both are
recorded in **Out of scope** below with their reasons instead.

**Mechanisms:**
- *Re-derive the ramp curve from the corpus* (`anchors: root`) — the lighter alternative
  (hardcode PR #605's 3.55x) was rejected because that figure's own source document calls it
  non-reproducible; re-deriving from the same corpus the backtest already replays keeps the
  whole computation self-consistent and re-runnable. `[engineer-verified this session]`
- *Fold detection lag into the compliance split rather than a third axis* (`anchors: root`) —
  a simpler per-call-fidelity model was considered and rejected: it would silently overstate
  Phase 3's benefit by ignoring defect 2, which the parent plan's own Verification item 8
  requires the backtest not to assume away.
- *New subcommand in `transcript-analysis.py`, not a standalone module* (`anchors: root`) — a
  standalone module was considered and rejected because it needs twelve helpers, ten of them
  private to that file; matches the file's own established one-module-many-subcommands
  pattern (`cost`, `context-distribution`, `cost-trend`, `handoff-ratio`, `edit-format` all
  live there already).

**Assumptions:**
- `_hook_observable_boundaries`'s proxy (reusing `_is_fresh_user_prompt` for user-message
  detection, plus session-start and session-end) accurately approximates real
  `UserPromptSubmit`/`Stop` firing points. `[unverified]` — not independently checked against
  real hook invocation traces in this session; reusing an already-tested predicate for the
  user-message half narrows the unverified surface to the two added boundaries (start/end),
  which the new test-class's session-end case (Critical files table) covers directly.
- The current `.handoff-nudge.log` (324 lines as of this writing) has enough joinable
  `nudged` entries to produce a compliance-realistic figure that isn't dominated by a small
  sample. `[unverified]` — Verification item 4 checks the join doesn't silently fail, not
  that the resulting sample size is large enough to trust.
- PR #605's bucket edges (`0-5`, `5-10`, `20-40`, `40-80`, `80-150`, `150-300`, `300+` turns)
  remain a sensible bucketing for this corpus's own re-derived curve. `[unverified]` — reused
  for comparability with the cited prior figures rather than re-derived from this corpus's
  own distribution; if the corpus's session-length distribution has shifted materially since
  PR #605, these edges may bucket too coarsely or too finely.

## Critical files

| Path | Change |
| --- | --- |
| `claude/.claude/scripts/transcript-analysis.py` | Add `cmd_rearm_backtest`, `_rearm_backtest_report(args, today, roots)`, `_hook_observable_boundaries`, `_ramp_curve_from_corpus`, `_parse_nudge_log_entries` (reuse `_print_nudge_log_diagnostic`'s bounded-read pattern at :7798-7811 rather than a fresh read implementation), `_operator_response_lag_from_log`, `_simulate_rearm_spacing`, and the `_HANDOFF_NUDGE_ABS_CAP` constant. Wire the subcommand in `build_parser()` alongside the other `sub.add_parser(...)` blocks (~9006-9205), using `_add_project_scope_args` for the shared `--projects`/`--this-repo` flags plus `--since`, `--branches`, `--config-dir` (append), `--no-redact`, `--spacings` (comma-separated ints, default `40000,80000,120000`). Reuses `_dedup_turns_by_request_id` (:4964), `_price_turn` (:5093), `_context_at_turn` (:4947, called inside `_price_turn`), `_context_window_for_model` (:4903), `_is_fresh_user_prompt` (:102) — `_hook_observable_boundaries` builds on this rather than reimplementing user-message detection, adding only the session-start and session-end boundaries `_is_fresh_user_prompt` alone doesn't supply — `_resolve_scan_roots` (:2575), `_resolve_cost_roots` (:5232), `_resolve_project_scope` (:2612), `_print_resolved_scope` (:2725) — none of these are reimplemented. |
| `claude/.claude/scripts/tests/test_transcript_analysis.py` | New test class covering: **`_hook_observable_boundaries`** — tool-call-only stretches (no boundary mid-stretch) vs genuine multi-turn conversations (one boundary per turn) vs a session whose last record is mid-band-crossing with no further user message (boundary must still surface at session end — the case the hook's own dual `UserPromptSubmit`/`Stop` registration exists to cover); **`_ramp_curve_from_corpus`** — bucket edges match PR #605's bands, sane rates on a synthetic corpus with known context growth, and an explicit empty-bucket case (a corpus with no turns in some band) asserting a defined fallback rather than a division-by-zero or NaN propagating into `_simulate_rearm_spacing`; **`_simulate_rearm_spacing`** — at least one hand-built session with a fully hand-computed expected dollar total (not only a bounds check against baseline/naive-reprice, which multiple wrong implementations could satisfy), plus an explicit case exercising 2+ sequential re-arms within one session (the remainder crossing a second band after the first simulated split) since that compounding is the feature's core value over a one-shot baseline; **`_parse_nudge_log_entries`** — all three line shapes (`nudged`/`schema-drift`/`handoff`) plus malformed lines; **`_operator_response_lag_from_log`** — exact-match join, no-match (session excluded from the compliance-realistic figure, counted), and ambiguous-match (two turns in the same session near the same `est` value — the `nudged` log line carries no timestamp, only `session=`/`est=`/`model=`/`window=`/`event=`, so the join key is `session_id` plus a first-crossing rule — the trace's first value `>= est`, matching the real hook's own once-only fire semantics — that needs its own coverage, including a case where a mid-session compaction dip's value is numerically closer to `est` than the true first-crossing turn, guarding against a nearest-value join silently landing on the wrong turn). New `_rearm_backtest_args(...)` fixture helper matching whatever flags the subparser adds, following `_cost_args`/`_context_distribution_args`'s existing pattern (:5275, :5300). Fixtures compose `fake_projects` + `_write_jsonl` + a new composite record builder — `_priced` (:5234) hardcodes `content=[]`, so a session exercising both `_hook_observable_boundaries` (needs real `tool_use`/`tool_result`/user-message content shapes) and `_simulate_rearm_spacing` (needs known usage/pricing) needs a helper that produces both in one record; name it explicitly during implementation (e.g. a `content=` parameter added to `_priced`, or a thin wrapper composing `_priced`'s usage with `_asst`'s content) rather than discovering the gap mid-test-writing. |
| `docs/transcript-analysis.md` | Add a `## rearm-backtest` section with example output, matching the documented pattern every other subcommand follows — including the two most recently added (`## cost-trend` :657, `## handoff-ratio` :705). |

**Not modified:** `nudge-handoff-near-context-cap.sh` (Phase 3's file, not this phase's),
`docs/handoff-nudge.md` (Phase 3), `.claude/plans/token-cost-reduction.md` (this is a
sub-plan; the parent plan is not rewritten by this phase — Phase 3 records the spacing this
backtest recommends, once it ships).

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` + `../../../.venv/bin/ruff check claude/.claude/`
   + `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` (repo standard;
   the shellcheck leg is a no-op for this phase since no shell file changes, but stays part
   of the standard suite).
2. Cross-check, encoded as a pytest against a shared fixture corpus (not a one-time manual
   run): the baseline row's total dollars must equal `_cost_report`'s own total for the same
   fixture scope — an independent, already-verified code path computing the same real
   (non-counterfactual) dollars over the same corpus should agree, and this stays a
   regression check across future refactors of `_price_turn`/`_dedup_turns_by_request_id`
   call sites rather than a fact someone has to remember to re-check by hand.
3. `_ramp_curve_from_corpus`'s printed multipliers are inspected by hand against PR #605's
   cited 3.55x/1.00x figures for the `0-5`/`40-80` bands — not asserted equal (the corpus has
   grown since that one-off measurement and is expected to differ), but reported alongside
   the old figure in the PR body so a reviewer can judge whether the drift is sane.
4. The report's excluded-session count (from `_operator_response_lag_from_log`'s log-to-
   session join) is sanity-checked against `.handoff-nudge.log`'s total `nudged` line count —
   a near-100% exclusion rate would mean the join logic is broken, not that compliance data
   is simply sparse.
5. Confirm the report explicitly states, in its own printed output, that model routing and
   the `360000` fire threshold are held fixed — this phase does not backtest either.

## Out of scope

- **Session-split threshold retuning.** `ABS_CAP` is editable (it lives in
  `nudge-handoff-near-context-cap.sh`, which Phase 3 of the parent plan touches) but this
  phase deliberately does not vary it — settled by PR #605's U-shape analysis, and the
  parent plan does not reopen it. `_HANDOFF_NUDGE_ABS_CAP` is a fixed module constant, not a
  CLI flag, specifically so a future run can't quietly retune it through this tool.
- **Widening the hook's sampling to `PostToolUse`.** Also editable (the hook registration
  lives in `claude/.claude/settings.json`), and the parent plan's own text calls this scope
  "a deliberate decline, not a platform limit" (matcherless `PostToolUse` would fire 3-10x+
  per turn against today's 2, mostly redundant). This phase models the persisting mid-stretch
  blind spot (`_hook_observable_boundaries`) rather than proposing to close it.
- **Model-routing backtesting.** Not replay-testable from session JSONL alone (routing
  depends on `opusplan` behavior the parent plan's Phase 5b is still investigating); the
  report states this explicitly rather than omitting the dimension silently.
- **Shipping the recommended spacing.** This phase produces the number; Phase 3 changes
  `nudge-handoff-near-context-cap.sh` to use it.
