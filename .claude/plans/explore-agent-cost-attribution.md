# Price Explore's actual cost during its Opus-inheritance exposure window

## Context

**Goal:** give `transcript-analysis.py` a reviewed way to compute the actual
dollar/token cost of a specific subagent type's dispatches over a closed
historical date range across every declared Claude Code account, with an
optional counterfactual re-pricing at a different model — so the cost
increase caused by Claude Code v2.1.198's Explore-model-inheritance default
can be answered with a number, not an estimate.

**Why now.** `.claude/plans/pin-explore-to-sonnet.md` (merged 2026-08-09)
already fixed the underlying issue — `claude/.claude/agents/Explore.md` now
pins `model: sonnet` — and already measured, on a throwaway script over a
single account's 14-day window, that 40 of ~60 observed `Explore` sidechains
ran on Opus. That plan's own Step 2 (PR #607, merged) built a *counting*
instrument (`subagent-mix`'s model-mix table: Runs/Declared/Requested/
Observed per agent type) but explicitly stopped short of dollars. No merged
work — including PR #616's cost-lever register and PR #617's cost-ledger —
computes an actual dollar figure for what a specific agent type cost, still
less a counterfactual against what it would have cost under a different
model. That gap is what this plan closes.

**Intended outcome:** `subagent-mix` gains a closed-date-range filter and a
per-agent-type dollar column (plus, when requested, a counterfactual $ and
delta at an alternate model), reusing the exact-model-ID pricing machinery
`cost` already has. Running it for `Explore`, `2026-07-01`–`2026-08-08`,
repriced at Haiku, across every declared account, is what actually answers
"how much did this change cost." That invocation happens after merge and its
output is reported to the user directly — no dollar figure is committed to
this public repo, matching `pin-explore-to-sonnet.md`'s own established rule.

## Approach

### Root problem and givens

**Root problem:** no reviewed tool joins a subagent dispatch's own priced
token usage to its agent type over an arbitrary historical window across
every declared account — the data and the pricing function both already
exist independently, but nothing sums them together.

| # | Given | Why it is fixed |
|---|---|---|
| G1 | Claude Code v2.1.198 (July 1, 2026) changed the built-in `Explore` agent's default model from hard-coded Haiku to inherit-the-session-model-capped-at-Opus. | `[verified: raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md, and GitHub issue anthropics/claude-code#72940 quoting the same release-notes line — both fetched this session]` |
| G2 | This repo's `claude/.claude/agents/Explore.md` (commit `d9793dd`, PR #596, merged 2026-08-09) pins `model: sonnet` and is stowed to `~/.claude/agents/`, i.e. applies to every project on this machine, not just this repo. This closes the exposure window at 2026-08-09. | `[verified: git log -- claude/.claude/agents/Explore.md; file content; this session]` |
| G3 | `_price_turn(model: str, usage: dict)` (transcript-analysis.py:4805) prices by the literal model-ID string passed in against `_MODEL_BASE_INPUT_RATES`, independent of which model the usage dict actually came from — calling it with a different model ID than the turn's real one is an already-correct counterfactual, not a new pricing path. | `[verified: transcript-analysis.py:4759-4805, read this session]` |
| G4 | `cmd_subagent_mix`'s model-mix table (PR #607, merged) already opens each matched dispatch's own paired sidechain `.jsonl` via `_observed_model_bucket(paired_jsonl)`, and already keys its rows by the dispatching `Task`/`Agent` tool_use's `input.subagent_type` — the table's established identity for "agent type," not `meta.json`'s separate `agentType` field. | `[verified: transcript-analysis.py diff for PR #607, read this session]` |
| G5 | `subagent-mix --since Nd` (PR #607) is relative-days-back only; no closed absolute date range exists on this subcommand today. Four unrelated subcommands (`user-input`, `review-trace`, `judgment-pair`, `handoff-ratio`) each independently inline an ISO-date `--since`/`--until` pair (`_iso_date` type, UTC-anchored via `T00:00:00Z`, inclusive end via `day_start + 86400`). | `[verified: exploration agent grep of all four call sites, this session]` |
| G6 | Every `subagent-mix` invocation already scans every declared account by default (unions `declared_transcript_roots()` plus explicit `--config-dir`) — "across all projects" needs no new flag. | `[verified: exploration agent, and prior tooling-capability agent, this session]` |
| G7 | This is a public repo; no dollar figure this plan produces may be committed to a tracked file. | `[engineer-verified — repo CLAUDE.md's redaction rules, plus pin-explore-to-sonnet.md's own precedent of the same rule]` |

### Mechanism 1 — where the pricing/join happens

*(anchors: root, row G4)* Extend `cmd_subagent_mix`'s existing per-dispatch
loop, which already opens each matched dispatch's paired `.jsonl` file for
`_observed_model_bucket`. Fold that single file-read into a richer helper —
`_dispatch_usage_summary(jsonl_path, since_ts: float | None, until_ts: float
| None, reprice_as: str | None)` — that returns, from one pass: the observed
model bucket (unchanged behavior, computed over **every** assistant record in
the file, exactly as today), the actual priced dollars (`_price_turn(real_model,
usage)` summed only over assistant records whose own `timestamp` falls in
`[since_ts, until_ts)`), and — only when `reprice_as` is given — the
counterfactual dollars (`_price_turn(reprice_as, usage)` on that same
in-window subset of usage dicts). Accumulate into the existing
`model_mix[stype_label]` row alongside `runs`/`dangling`/`requested`/
`observed`.

**Why the window must be applied per sidechain record, not per dispatch.**
A dispatch is currently included/excluded as a whole by the *dispatching*
`Task`/`Agent` tool_use record's own timestamp (the existing `--since Nd`
filter, PR #607, transcript-analysis.py:3020-3023) — that filter governs
`runs`/`dangling`/`requested`/`observed` and is left untouched. But a
dispatch's own sidechain can run long enough to straddle a window edge: if
`--since-date`/`--until-date` filtering were applied only at that same
outer, dispatch-start level, a dispatch starting one second before
`--until-date` would have **100% of its sidechain dollars** — including any
incurred after the cutoff — attributed to the "in-window" total. That
directly breaks the plan's own purpose (an accurate Explore-vs-Haiku dollar
figure for a specific historical window), so `_dispatch_usage_summary`
filters at the assistant-record level, inside the file it already opens, for
the dollar columns specifically. The existing `runs`/`declared`/`requested`/
`observed` columns keep their current, dispatch-level `--since Nd` semantics
unchanged — only the new dollar columns gain per-record window filtering.
*(Finding from `ciso-reviewer`-adjacent plan-review round: independently
raised by `staff-sdet`, as a missing boundary-straddling test case, and
`staff-backend-engineer`, as an unstated design gap between row A2's
timestamp-availability claim and Mechanism 1's original file-level summation
— converging on the same root cause from different angles.)

**Lighter primitives found; two heavier ones rejected:**
- Rejected: restructure `cmd_cost`'s flattened multi-file scan
  (`_read_session_file_partitioned` via `_resolve_project_scope(...,
  include_subagents=True)`) to carry per-record dispatch identity. `cmd_cost`
  is the highest-traffic subcommand in this file; every other flag on it
  depends on its current flattened-stream shape. Reworking that architecture
  for a narrower need risks regressing everything else `cost` does.
- Rejected: a new `agent-type-cost` subcommand. `pin-explore-to-sonnet.md`'s
  own Step 2 already rejected the structurally identical move ("a third
  subcommand would create a third definition of a run") when choosing to
  extend `cmd_subagents`/`cmd_subagent_mix` instead of adding new ones. The
  same reasoning applies here: `subagent-mix` already defines "a dispatch"
  and "an agent type" for this exact table: reuse it.

### Mechanism 2 — closed date range

*(anchors: row G5)* Add `--since-date YYYY-MM-DD` / `--until-date YYYY-MM-DD`
to `subagent-mix` as new, additive flags (`_iso_date`-typed, same UTC/
inclusive-end convention as the four existing subcommands), left independent
of the already-shipped relative `--since Nd`.

**Lighter primitives found; two heavier ones rejected:**
- Rejected: redefine `--since` to accept either an `Nd` suffix or an ISO
  date. One flag silently parsing two shapes is surprising, has no
  precedent anywhere else in this file, and breaks PR #607's just-merged
  contract for existing `--since Nd` callers.
- Rejected: a wrapper shell script that pre/post-filters `subagent-mix`
  stdout by a date grep. The file already has an in-process, tested
  `_iso_date` pattern with proper UTC/boundary handling; re-deriving that
  outside Python duplicates it with weaker error handling.

### Mechanism 3 — counterfactual model selection

*(anchors: root)* `--reprice-as MODEL_ID`, validated against
`_MODEL_BASE_INPUT_RATES`'s existing key set (the canonical model-ID
discriminator already in this file — no new enum introduced). Unknown values
error listing the valid keys.

**Lighter primitives found; two heavier ones rejected:**
- Rejected: an unvalidated free-text `--reprice-as`. A typo (e.g. `"haiku"`
  instead of the exact model ID) would silently produce `0` counterfactual
  dollars via `_price_turn`'s unpriced-token path rather than a clear error —
  wrong-looking output on the single most common mistake. (The discriminator
  itself — the model-ID string set — is not new: it reuses
  `_MODEL_BASE_INPUT_RATES`'s existing keys. The validate-and-list-choices
  logic that checks a CLI value against those keys is new code; no existing
  call site in this file does the same check today.)
- Rejected: a boolean `--as-haiku` flag scoped only to this plan's specific
  question. `_price_turn`'s exact-model-ID pricing already generalizes to any
  model in the rate table at no extra cost; narrowing it to one hardcoded
  target throws that away for no benefit.

### Assumption ledger — remaining rows

| # | Item | Status |
|---|---|---|
| A1 | `_observed_model_bucket` has no other caller besides `cmd_subagent_mix` today, so folding it into a richer function is a safe rename, not a breaking API change. | `[verified: plan-review round 1, staff-backend-engineer — grep confirms exactly two hits in the whole codebase: the def at transcript-analysis.py:3259 and the sole call site at :3049; no test references it by name. No wrapper needed — the fold can change the return shape directly.]` |
| A2 | A dispatch's own sidechain assistant records carry a `timestamp` field, usable for filtering the new dollar columns at the per-record level (no timestamp field exists on `meta.json` itself, and the existing outer dispatch-inclusion filter reads the *parent* record's timestamp, not the sidechain's own). | `[verified: plan-review round 1, staff-backend-engineer — confirmed `_index_subagent_dispatches` and the pre-fold `_observed_model_bucket` never read a sidechain timestamp; per-record `timestamp` presence follows the same shape the four existing ISO-date subcommands already filter on]` — see Mechanism 1's per-record filtering note, added after round 1 found the original file-level-summation design didn't actually use this field. |
| A3 | Redacting `agentType` labels under multi-root scope for the new dollar column reuses the *existing* `subagent_type_redact_map` PR #607 already built for this same table — no new redaction map needed. | `[verified: transcript-analysis.py diff for PR #607, this session; independently re-confirmed by plan-review round 1, ciso-reviewer, against live code (3031-3038, 3044/3097-3098)]` |
| A4 | Printing an exact-cent dollar total under multi-root redaction is a defense-in-depth erosion, not a standalone exploitable path: `_DO_NOT_PUBLISH_BANNER` (unconditional on `multi_root`) is the primary control for this data class, and per-run label instability is secondary — an exact dollar figure is a stronger cross-run correlation key than the existing integer spawn count, but only *within* an already-"do not publish" report. | `[verified: plan-review round 1, ciso-reviewer — Low severity, no blocking control required; documented as a residual risk per Critical Files below rather than a new precision-coarsening mechanism, to stay consistent with `cost`'s own existing exact-cent convention elsewhere in this file]` |

## Critical files

- `claude/.claude/scripts/transcript-analysis.py`
  - Fold `_observed_model_bucket`'s single-pass file read into a richer
    helper, `_dispatch_usage_summary(jsonl_path, since_ts, until_ts,
    reprice_as)`, returning `(observed_bucket, actual_dollars,
    dollars_by_class, counterfactual_dollars | None)`, reusing `_price_turn`
    — **reuse, don't re-derive**, the pricing math `cost` already relies on.
    `observed_bucket` stays computed over every record in the file
    (unchanged from today); `actual_dollars`/`counterfactual_dollars` sum
    only assistant records whose own `timestamp` falls in `[since_ts,
    until_ts)` (Mechanism 1's per-record filtering — see above; this is the
    round-1 plan-review fix, not the original design). No other caller of
    `_observed_model_bucket` exists (round-1 `staff-backend-engineer` grep,
    ledger row A1) — the return-shape change needs no compatibility wrapper,
    only its sole call site (transcript-analysis.py:3049-3055) rewritten to
    unpack the new tuple, **preserving the existing `None`-when-dangling
    semantics on the tuple's first element** (round-1 `staff-sdet` finding —
    a partial rewrite that drops this would silently break the `dangling`
    bucket without a compile-time signal).
  - `cmd_subagent_mix`: wire `--since-date`/`--until-date` (new, additive
    ISO-date flags feeding `_dispatch_usage_summary`'s window — see
    Mechanism 2; independent of the existing, unchanged `--since Nd`) and
    `--reprice-as` (validate against `_MODEL_BASE_INPUT_RATES`'s keys — this
    validate-and-list-choices check is new code, not reused from an existing
    call site, though the key set itself is not new); add Actual $ (and
    Counterfactual $ / Delta when `--reprice-as` given) to the model-mix
    table's printed columns.
  - `main()`: argparse wiring for the new flags on the `subagent-mix`
    subparser, mirroring the existing `--since`/`--config-dir` entries'
    help text style.
  - Reuse as-is: `_resolve_cost_roots`, `_redaction_ordinals`,
    `_assign_root_scoped_redact_label` + the existing
    `subagent_type_redact_map`, `_DO_NOT_PUBLISH_BANNER`, `_iso_date`,
    `_parse_ts`, `_MODEL_BASE_INPUT_RATES`.
  - Add a one-line comment near `_assign_root_scoped_redact_label`'s
    existing "stable only within one run" docstring note, documenting the
    residual cross-run correlation risk an exact-cent dollar figure carries
    relative to the integer spawn counts that comment already covers
    (ledger row A4, `ciso-reviewer` round 1 — Low severity, no blocking
    control, kept consistent with `cost`'s own existing exact-cent
    precision elsewhere in this file rather than a new coarsening rule).
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - New cases built on the existing `_write_subagent_dispatch(...,
    agent_type=..., requested_model=...)` fixture: dollar totals against a
    hand-computed usage dict; `--reprice-as` delta arithmetic; an invalid
    `--reprice-as` value; `--since-date`/`--until-date` inclusive/exclusive
    boundary cases; multi-root redaction of the new column (mirroring PR
    #607's own multi-root test pattern for this table).
  - A dispatch whose sidechain has zero priced (synthetic-only) assistant
    records, reusing the existing
    `test_synthetic_only_sidechain_lands_in_other_not_a_pin_violation`
    fixture shape, asserting the new dollar cell renders `$0.00` rather than
    crashing or printing `None` (round-1 `staff-sdet` finding).
  - A dispatch whose sidechain records straddle `--until-date` (some before,
    some after the cutoff), asserting only the in-window records' usage is
    priced into the dollar total — the direct regression test for
    Mechanism 1's per-record filtering fix (round-1 `staff-sdet` +
    `staff-backend-engineer` convergent finding).
  - `--reprice-as` set to the dispatch's own real model, asserting
    Delta == `$0.00` exactly — catches accidental divergence between the
    actual-dollars and counterfactual-dollars code paths on identical input
    (round-1 `staff-sdet` finding).
- `docs/transcript-analysis.md` — document the new flags and columns under
  the existing `## subagent-mix` section, including the per-record (not
  per-dispatch) semantics of `--since-date`/`--until-date` for the dollar
  columns specifically, versus `--since Nd`'s existing dispatch-level scope
  for every other column in this table.
- `claude/.claude/skills/transcript-analysis/SKILL.md` — matching
  quick-reference row update (PR #617's own precedent: doc and skill updated
  together).

**No file gets a hardcoded dollar figure.** This plan ships the capability;
the actual Explore-vs-Haiku number is produced by running the tool after
merge and reported in conversation (G7).

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py -q`
- `../../../.venv/bin/pytest claude/.claude/ -q` (full suite)
- `../../../.venv/bin/ruff check claude/.claude/scripts/transcript-analysis.py claude/.claude/scripts/tests/test_transcript_analysis.py`
- `scripts/list-shell-files.sh | xargs -0 shellcheck` (unaffected by this
  change; part of the standard check suite regardless)
- End-to-end, after merge (not committed anywhere):
  `python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --since-date 2026-07-01 --until-date 2026-08-08 --reprice-as claude-haiku-4-5-20251001`
  run with default scope (every declared account, per G6) — `Explore`'s row
  shows Actual $ vs Counterfactual $ @ Haiku; the delta is the answer to the
  user's original question. `--until-date 2026-08-08` (the day before the
  `Explore.md` merge) is used deliberately to avoid a same-day mixed
  pre/post-override boundary; day-granularity filtering can't otherwise
  distinguish dispatches before vs. after the exact merge time on 2026-08-09.
- Manually spot-check one dispatch's dollar total against a hand computation
  from its raw usage records, confirming the counterfactual path applies the
  same per-token-class math as the actual-dollars path.

## Out of scope

- Extending `cmd_cost` itself with an agent-type breakdown (Mechanism 1's
  rejected heavier alternative) — not needed once `subagent-mix` carries
  dollars.
- A durable ledger entry for this figure — `cost-trend-ledger.md`'s own
  Phase 2 (per-merged-PR cost attribution) already owns that, as a separate,
  already-scoped follow-up.
- Special-casing the September 1, 2026 Sonnet base-rate change
  (`pin-explore-to-sonnet.md` G3) — the recommended verification window
  (Jul 1–Aug 8) sits entirely before that repricing, so it doesn't confound
  this specific measurement, but `--reprice-as` itself does not detect or
  warn about a rate change mid-window for other callers' date ranges.
