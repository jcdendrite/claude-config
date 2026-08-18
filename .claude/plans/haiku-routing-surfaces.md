# Haiku routing surfaces: pilot Explore on Haiku, gated on measured recall

## Context

**Goal: determine empirically whether `Explore` runs acceptably on Haiku in
this harness, by piloting it behind a recall gate whose pass condition is fixed
before any number is seen.** The pilot's product is a decision, and that
decision closes a follow-up this repo has carried open since
`pin-explore-to-sonnet.md`, whose **Out of scope** section deferred it verbatim:
*"Finding 'safe and appropriate uses for Haiku' … it is a separate design
question (which agents, if any, should run Haiku, and under what task shape
does that not degrade output)."*

### What the corpus shows

All figures are a **single-root** run — the declared-roots union is suppressed
via `TRANSCRIPT_CONFIG_DIRS_FILE`, so the scan covers one account and
`subagent_type` labels are unredacted. Re-derive:

```bash
TRANSCRIPT_CONFIG_DIRS_FILE=/dev/null python3 \
  claude/.claude/scripts/transcript-analysis.py subagent-mix \
  --this-repo --since 8d --reprice-as claude-haiku-4-5-20251001
```

Without that override the command unions every declared root
(`transcript_analysis/scope.py:530`) and redacts every agent-type label
(`transcript-analysis.py:2287-2291`). No multi-root figure is used here.

**The window is 8 days, not 30, and that choice is load-bearing.** `Explore`'s
repo-owned Sonnet pin landed 2026-08-09 (`d9793dd`). A 30-day window covers ~21
days when `Explore` was still the built-in inheriting the parent model (capped
at Opus per v2.1.198), so any pin-adherence read over 30 days measures a period
when there was no pin to adhere to. Every figure below starts after the pin.

`Explore`, 8 days post-pin, this repo:

| Column | Value |
| --- | --- |
| Runs | **11** |
| Observed models | **sonnet(6), opus(5)** |
| Actual | **$26.15** |
| Repriced at Haiku 4.5 | **$7.04** |
| Delta | **$19.11** |

**Two separable savings live inside that delta, and this plan only claims one.**
Haiku is exactly half Sonnet's base input rate (`pricing.py:34-45`: Sonnet 5
$2.00, Haiku 4.5 $1.00, Opus 5 $5.00), so an all-Sonnet arm is 2 x the Haiku
figure:

| Arm | ~Cost / 8d | Saving vs. today |
| --- | --- | --- |
| Today (6 Sonnet / 5 Opus) — measured | $26.15 | — |
| Sonnet pin fully honored — **estimated** | **~$14.08** | ~$12.07 |
| Haiku pin honored — measured | $7.04 | $19.11 |

The **Haiku-specific increment is ~$7 / 8 days**; the larger ~$12 comes from the
5 Opus dispatches, which A4 attributes to the plan-mode override and which this
plan does not touch. Anyone reading this as a $19 Haiku case is reading it
wrong.

**These dollar figures rest on 11 dispatches and carry no confidence interval**
— deliberately weaker rigor than Step 3 demands of the recall gate, and the
asymmetry is stated rather than hidden. They are sized to answer "is this worth
doing at all," not to support a precise return claim. The 8-day window cannot be
widened without re-crossing the pin date, so a larger sample requires waiting,
not rescanning.

**Why do it anyway.** Not for the ~$7. For three things: it closes the open
follow-up with evidence instead of assumption; it settles A5 — whether a
`model:` pin is honored at all outside plan mode — which no committed
measurement answers today; and it de-risks the real prize. `general-purpose`
runs **207 dispatches / $371.98 actual / $183.48 at Haiku ($188.50 delta) over
the same 8 days** and owns the whole-file discovery lane. If Haiku's locate
recall holds on `Explore`, routing locate-shaped `general-purpose` work becomes
an evidence-backed follow-on rather than a guess.

**The rule this replaces predates its own evidence.** CLAUDE.md's *"Haiku:
narrow, deterministic skills only. Never for code authoring or judgment"* landed
2026-05-02 (`16530ce`); the check-runner incidents ran through May and the
retirement measurement landed 2026-06-23. It also names a routing target no
mechanism implements — skills execute in whatever context invokes them.

## Approach

**Pin `Explore` to `model: haiku` in its repo-owned agent file, remove the two
standing instructions that would override that pin at dispatch time, and gate
the whole change on a stratified recall eval.** Verification runs first; no file
is edited until the gate passes.

Anthropic's sub-agents documentation names this exact override: *"A user or
project subagent named `Explore` overrides the built-in and keeps its own
`model` field, so define one with `model: haiku` to keep exploration on a
lower-cost model."* Anthropic also shipped `Explore` on Haiku by default until
v2.1.198 — *"The built-in Explore agent now inherits the main session's model
(capped at opus) instead of running on haiku."* The motivating defect
(`anthropics/claude-code#45357`) was prompt-size under a ~200-tool MCP surface
(*"Prompt is too long"*), **not reasoning quality**.

**The pin does not work without removing two blockers first.** Dispatch-time
`model` params outrank frontmatter (`docs/auto-mode.md:170-173`), and two
stow-shipped files instruct passing `model: sonnet` on `Explore` by name:
`CLAUDE.md:85,88` and `subagent-delegation/SKILL.md:131-133` (*"pass it even on
`Explore`, whose `Explore.md` pin is a request, not a guarantee"*). Any
compliant session would silently restore Sonnet. These edits are **functional
prerequisites**, not stale-assertion cleanup.

**Where the design strains the safety bar.** The bar is "output must be
parent-verifiable." `Explore` clears it for *precision* — a fabricated path dies
to one `grep` — but **not for recall**: an omitted site is silent. Recall is
therefore the gate's primary term, and the oracle is stratified so the gate
cannot be passed on the easy half alone.

**Why not the agents holding the money.** `code-writer` ($742.42 / 8d, the
largest line) and the `staff-*`/`ciso` reviewers are code authoring and rubric
judgment — excluded by the safety bar, not deferred for capacity.

**Why re-pin rather than build a new agent.** `subagent-delegation` Step 2
already assigns locate-style search to `Explore`; a new agent would duplicate a
charter that exists.

### Assumption ledger

**Root problem:** this harness has never measured whether any of its work runs
acceptably on Haiku, and the one agent whose charter is structurally verifiable
is governed by a rule written seven weeks before the only evidence bearing on
it.

**Givens** (fixed, beyond this design's reach):

- **G1 — Subagent model resolution follows a fixed four-step vendor order.**
  *Vendor-owned; no repo-side expression.*
- **G2 — Harness plan mode forces dispatches to Opus regardless of any pin.**
  *Vendor-owned, gated on `permissionMode`; `docs/auto-mode.md` records that no
  instruction-layer mitigation is known.*
- **G3 — Haiku 4.5 is $1.00/MTok base input vs Sonnet 5's $2.00 and Opus 5's
  $5.00.** *Vendor pricing.*
- **G4 — `subagent-mix` cannot label agent types under a multi-root union.**
  *Owned by a redaction rule this plan will not weaken; the
  `TRANSCRIPT_CONFIG_DIRS_FILE` override is the documented single-root path.*

**Mechanisms:**

- **M1 — `model: haiku` in `claude/.claude/agents/Explore.md`.** `anchors:
  root`. Lighter primitives rejected: (a) `CLAUDE_CODE_SUBAGENT_MODEL` — sits at
  resolution step 1 and forces *every* subagent, including excluded reviewers;
  `docs/auto-mode.md` advises against it by name. (b) dispatch-time `model:
  haiku` param — requires every caller to remember it and contradicts the
  standing rule this plan is already amending. (c) `--agents` CLI JSON — an eval
  mechanism, not shipped config.
- **M2 — Stratified recall eval under `evals/`.** `anchors: row A3`. Lighter
  primitives rejected: `run_skill_evals.py` measures skill *triggering*, not
  retrieval, and its `--model` choices exclude Haiku; `subagent-mix` prices
  dispatches and cannot score correctness.
- **M3 — A non-plan-mode matrix row in the existing
  `measure_subagent_model_resolution.py`.** `anchors: row A5`. Lighter primitive
  rejected: an ad hoc scratch dispatch produces no committed artifact and
  re-creates the ambiguity A5 records.

**Assumptions:**

| # | Assumption | Tag |
| --- | --- | --- |
| A1 | `Explore`'s 8-day figures (11 runs, $26.15 → $7.04) are single-root, post-pin, and correctly attributed | `[verified: TRANSCRIPT_CONFIG_DIRS_FILE-overridden subagent-mix --since 8d, run this session; pin date from git log --follow on Explore.md]` |
| A2 | The ~$14.08 honored-Sonnet arm is an estimate, not a measurement | `[unverified]` — derived from the 2:1 Sonnet:Haiku rate ratio, not from a repriced Sonnet run. Step 1 replaces it. |
| A3 | **Haiku's locate recall is adequate for `Explore`'s charter** | `[unverified]` — the load-bearing claim; Step 3 measures it. Nothing downstream may assume it. |
| A4 | `Explore`'s pin is not honored in plan mode, and that explains the residual Opus dispatches | `[verified: docs/cost-levers-considered.md:106-109 (92/95) for the plan-mode override]`; the attribution of this window's 5 Opus runs to plan mode specifically is **`[unverified]`** — consistent with A4 but not confirmed per-dispatch. |
| A5 | A `model:` pin is honored *outside* plan mode | `[unverified]` — `measure_subagent_model_resolution.py` rows 6–7 exist but only under plan mode, and their one live run was confounded. Step 2 closes this. |
| A6 | Anthropic ran `Explore` on Haiku until v2.1.198, moving off for prompt-size not reasoning quality | `[verified: .claude/plans/pin-explore-to-sonnet.md:33-44]` |
| A7 | Pilot `Explore` specifically; safety bar is "output must be parent-verifiable" | `[engineer-verified]` — chosen this session. |
| A8 | **Superseding `pin-explore-to-sonnet.md`'s A10** (Sonnet ceiling is an acceptable trade) is deliberate | `[engineer-verified]` — the engineer, this session, after the contradiction was surfaced: try Explore as the candidate, since what matters is quality of work and cost. |
| A9 | The vendor's MCP prompt-size ceiling does not bind this harness | `[engineer-verified]` — the engineer's judgment this session that the ~200-tool failure condition does not apply to their context-light harness. Residual: this ships to all stow users, whose surfaces differ — disclosed in Step 6, not treated as a blocker. |
| A10 | Adding a Haiku pin requires editing the roster test, not just the agent file | `[verified: test_agent_roster.py:73-76 (NON_REVIEWER_MODELS), :352 (test_model_pinned_to_expected_value), :368 (test_expected_model_map_is_complete)]` |
| A11 | Recall, not precision, is the binding risk | `[unverified]` — reasoned from failure shapes; Step 3 measures both so it is not assumed. |
| A12 | The verbose-output-absorption Haiku lever stays closed | `[verified: docs/cost-levers-considered.md:48]` — a locate agent absorbs no command output. |

## Verification — runs first, gates the edits

**Nothing in "Critical files" is edited until Steps 1–3 pass.**

**Step 1 — Complete the cost picture.** Re-run the single-root command with
`--reprice-as claude-sonnet-5` to replace A2's estimate with a measured
honored-Sonnet arm. Publish only single-root, post-pin figures, and state the
`TRANSCRIPT_CONFIG_DIRS_FILE` override and the window's start rationale
alongside any number that ships.

**Step 2 — Settle A5 and A9 cheaply, before eval spend.** Add a non-plan-mode
row to `measure_subagent_model_resolution.py`'s `RUN_MATRIX`, reusing the
existing `DISPATCH_EXPLORE_HAIKU` constants, and read the result from the
dispatch's own `subagents/*.meta.json` sidecar the way
`parse_subagent_dispatches` already does — **not** from `subagent-mix`'s
`Observed` column, which aggregates every dispatch in the window and cannot
isolate one trial. Three outcomes: `haiku` → proceed; `sonnet`/`opus` → the pin
is not honored and the plan stops; `"Prompt is too long"` → A9 is falsified on
this machine and the plan stops.

**Step 3 — Measure recall (the gate).** Two fixture strata, scored and reported
separately, never pooled:

- **Stratum A — grep-derivable.** Answer key from `git grep`. At least 8 cases,
  of which **at least 5 have ≥5 ground-truth sites** — recall is only exercised
  when a model can return a subset.
- **Stratum B — semantic.** Open-scope questions ("where is X handled") with a
  hand-curated answer key, because no mechanical oracle exists for them. At
  least 5 cases, of which **at least 3 have ≥5 ground-truth sites** — a
  single-site semantic case degenerates recall to a coin flip and re-creates the
  defect the Stratum A floor exists to prevent. A gate passing only on Stratum A
  would measure the half where literal search already suffices, which is where
  the two models least diverge.

**Stratum B scoring rules, fixed here so two implementers converge:**
- **Site granularity** is the enclosing *function or top-level block*, keyed by
  `path:symbol` — not a line number, which no two curators would agree on.
- **Answer-key validation:** each Stratum B key is built by one curator and
  independently confirmed by a second pass that must find zero additional sites
  before the case is admitted. An under-curated key inflates recall for both
  models identically, so the gate cannot detect it — validation is the only
  defense.
- **Precision on Stratum B** counts a returned `path:symbol` as a false positive
  only if the symbol does not exist, or exists and contains nothing related to
  the question. Judgment calls are resolved against the case's key, and a case
  needing more than one such call is rejected as under-specified rather than
  scored.
- **Aggregation** is a macro-average over cases (each case weighted equally),
  not a micro-average over sites, so a single site-rich case cannot dominate.
  Reported spread is min/max across the K samples per case.

Run **K = 5 samples per case per model**, Haiku and Sonnet on identical cases.
Report per-case recall and precision with observed spread, not just means.

**Verification spend.** This is ≥13 cases x 2 models x K=5 = **≥130 live
dispatches**, one-time. At this window's observed per-dispatch rates that is
roughly **$150–200** — an order of magnitude above the ~$7/8d it evaluates, and
justified by the decision and the `general-purpose` follow-on rather than by
Explore's own return. **Reuse `measure_subagent_model_resolution.py`'s existing
`--max-budget-usd` per-run cap (`PER_RUN_BUDGET_CAP_USD`) rather than running
uncapped.** If the realized spend exceeds $300, stop and report rather than
continuing.

**Adoption gate, fixed here before any number is seen:**
1. Haiku's mean recall within **5 points** of Sonnet's **in each stratum
   separately**.
2. **No individual case** shows a Haiku-vs-Sonnet recall gap exceeding **15
   points** — this stops a per-case cliff hiding inside a passing mean.
3. Precision **≥ 0.98** with a reported binomial confidence interval; if the
   interval's lower bound falls below 0.90, N is too small and more samples are
   required before the gate can be called either way.

Miss any term and the pin does not ship; the plan converts to the refuse branch,
which is a complete result rather than a failure.

**Step 4 — Suite and lint.** Runs *after* the Critical-files edits, unlike Steps
1–3 — it lints the edited tree. From this worktree:
`../../../.venv/bin/pytest claude/.claude/ evals/` and
`../../../.venv/bin/ruff check claude/.claude/ evals/`. `evals/` is absent from
`.github/workflows/tests.yml`, preserving `evals/README.md`'s never-CI posture.

**Step 5 — Confirm the live pin post-merge.** Re-run the single-root
`subagent-mix --since 7d` and confirm `Explore`'s `Observed` column reads
`haiku` outside plan mode. **If it does not, revert the commit** — the pin is
not holding in ordinary sessions and the change is delivering the quality
tradeoff without the saving.

**Step 6 — Disclose what is not monitored.** Two distinct residuals, both
recorded in the CHANGELOG entry:

- **Prompt-size overflow (A9).** Detection for a stow user whose MCP surface
  exceeds Haiku's prompt budget is **user-report-only**; there is no
  cross-consumer telemetry and `subagent-mix` sees only this machine. Include a
  tool-count figure for this machine **as an explicitly dated snapshot** that
  will drift as plugins change — a bare integer only, never the names of
  individual tools or MCP servers.
- **Recall drift (A3).** Step 3's gate is a one-time pre-merge measurement.
  Nothing re-runs it as the codebase, the query mix, or the Haiku model itself
  changes, and `evals/` is never-CI by design. There is no cadence and no alert;
  a later regression surfaces only if someone re-runs the eval deliberately.

All edits land as **one atomic commit**, so rollback is a single `git revert` —
which also removes the `evals/` additions, so re-running the measurement after a
revert means restoring those files first.

## Critical files

### Ship branch — only if Step 3's gate passes

- `claude/.claude/agents/Explore.md` — `model: sonnet` → `model: haiku`.
  `effort: low` is already correct and does not move.
- `claude/.claude/CLAUDE.md` — **functional prerequisite.** Amend Model & Effort
  Routing so the blanket "pass an explicit `model: sonnet` on every dispatch" no
  longer covers `Explore`, and correct line 88's "`Explore` is pinned to
  Sonnet". Rewrite the Haiku bullet to state the measured rule and stop naming
  "skills" as a routing target. **Draft the exact replacement sentences and get
  them agreed before this edit lands** — "state the measured rule" is precisely
  the kind of open instruction that regenerates an over-broad rule, and this
  file is re-read every session by every stow consumer.
- `claude/.claude/skills/subagent-delegation/SKILL.md:131-133` — **functional
  prerequisite.** Remove "pass it even on `Explore`", which would otherwise
  override the new pin on every debug-probe dispatch.
- `claude/.claude/hooks/tests/test_agent_roster.py` — `NON_REVIEWER_MODELS`
  entry for `Explore.md`. **Reuse** the existing map and its
  `test_expected_model_map_is_complete` guard.
- `docs/auto-mode.md` — the requested-model table's `Explore → Sonnet` row.
- `CHANGELOG.md` — entry, including Step 6's disclosure.

### Refuse branch — if the gate fails

- `docs/cost-levers-considered.md` — new section recording the lever, the
  measured reason, and the fixture set that produced it. **Reuse** the existing
  Lever/Verdict/Measured-reason table shape.
- `docs/design-decisions.md` — new dated §28 cross-referencing §10
  (check-runner) as the prior Haiku data point.

Both branches keep the `evals/` additions; a measurement that produced a refusal
is still worth keeping runnable.

### Create — both branches

- `evals/measure_explore_recall.py` — the stratified scorer. **Reuse**
  `measure_subagent_model_resolution.py`'s subprocess-launch shape, `--agents`
  override pattern, and `RunResult`/`observation_to_dict` output.
- `evals/test_measure_explore_recall.py` — unit tests for scoring arithmetic and
  case construction, mirroring `test_measure_subagent_model_resolution.py`.
  **Must cover the gate-composition function explicitly** — the three-term AND
  (per-stratum mean, per-case 15-point floor, precision-CI), including the
  "uncallable below 0.90" branch. That function decides ship vs. refuse; an
  `or`-for-`and` slip or an uncallable branch defaulting to pass would
  mis-decide the whole plan with nothing defending against it. The eval is not a
  CI gate; its scorer is still testable.
- `evals/fixtures/explore-recall-cases.json` — the two strata.

## Out of scope

- **Moving any reviewer, `code-writer`, or `general-purpose` dispatch to Haiku.**
  Excluded by A7's safety bar. `general-purpose`'s $188.50 / 8d delta is the
  real prize and is named as the follow-on, not bundled.
- **Fixing the residual Opus dispatches on `Explore`** (~$12 / 8d, larger than
  this plan's own Haiku increment). A plan-mode/`permissionMode` question
  already addressed structurally by keeping agent-initiated planning out of plan
  mode; reopening it needs its own evidence.
- **Reopening verbose-output absorption.** Closed at
  `docs/cost-levers-considered.md:48`.
- **Extending `run_skill_evals.py`'s `--model` choices to Haiku.** Its subject is
  skill-triggering behavior of models this harness actually runs on.
