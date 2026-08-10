# Plan-boundary continue-vs-handoff decision rule

## Context

**Goal:** stop sessions from re-deriving "continue in this session or hand off?"
at the plan→implementation boundary, by stating the rule once, where the
decision is made.

**Scope note:** an earlier draft also retuned `HANDOFF_NUDGE_ABS_CAP`
360000 → 300000. That was dropped — see **Out of scope** for the arithmetic that
killed it. No hook, doc, or threshold value changes here; this is two skill-body
additions.

Sessions currently answer this question ad hoc, and they answer it
inconsistently. One recent session argued for handing off before implementation
on the reasoning that continuing "re-sends that entire accumulated history as
input tokens" every turn. That mechanism is real but mispriced — the re-send
bills as `cache_read` at a fraction of input rate, and the argument omitted the
cost a fresh session pays to rebuild. Measured against transcript data, that
advice is net-negative at the median plan boundary.

The intended outcome: one rule, stated where the decision is made, carrying
enough of the cost model that a session can apply judgment instead of guessing —
and deliberately carrying no threshold number of its own.

## Approach

### What the data says

Measured over 396 transcripts / 60 days of main-thread assistant turns, priced
with `transcript-analysis.py`'s own rate table. Cost is normalized by output
tokens — the model's actual production — so it measures cost per unit of work
rather than cost per turn:

| turn index | mean context | $/1k output | vs best |
|---|---|---|---|
| 0–5 | 60k | 0.207 | **3.55x** |
| 5–10 | 66k | 0.072 | 1.23x |
| 20–40 | 98k | 0.059 | 1.01x |
| 40–80 | 136k | 0.058 | **1.00x** |
| 80–150 | 199k | 0.076 | 1.31x |
| 150–300 | 301k | 0.112 | 1.93x |
| 300+ | 498k | 0.183 | 3.14x |

**Provenance and its limit.** These figures come from one-off analysis scripts
run against local transcripts, not from a subcommand this repo ships. They reuse
`transcript-analysis.py`'s own `_price_turn`, `_cache_write_split`, and
`iter_sessions`, so the pricing reconciles with `cost` — but the scripts are not
committed, so treat the table as a **point-in-time measurement, not a
reproducible report**. Only the session-share and dollar-share figures cited
under **Out of scope** are re-derivable today, via
`transcript-analysis.py context-distribution`. Anyone revisiting this decision
should re-measure rather than trust these numbers to have aged well.

The curve is U-shaped and reproduces independently within both the Opus and
Sonnet families, so it is not a model-mix artifact. Two consequences:

1. **A fresh session is not free.** It pays roughly a 3.55x premium over its
   first five turns while it rebuilds context. "Always hand off" pays that
   premium unconditionally.
2. **At the typical plan boundary, continuing wins.** Across 141 sessions
   carrying a `plan-review` boundary, median context at that boundary was 152k
   (sessions that continued) and 175k (sessions that handed off) — inside the
   1.00–1.31x band. Handing off there trades a 1.0–1.3x rate for a 3.55x ramp.

Handing off only pays once the carried prefix is expensive enough that the ramp
amortizes. **That point is not a single context number.** Restarting costs a
roughly fixed ramp; continuing costs a rate premium per turn. So the breakeven is
`remaining_turns ≈ ramp_excess / (rate − 1)` — a function of context *and* work
remaining. At 199k with 135 turns left, restarting wins; at 199k with 37 turns
left, continuing wins. Same context, opposite answers.

This is why the rule is guidance at a decision point rather than a threshold: the
judgment needs both variables, and only a session at the boundary has the second
one. It is also why this plan does not retune the nudge cap — see **Out of scope**.

### The two halves separate

Committing the plan and handing off have different economics and should not be
bundled into one rule:

- **Committing the plan** costs about one turn. It makes a reviewed, approved
  plan durable before a phase that rewrites the working tree, and it is already
  a precondition of `handoff` (§5 requires finished work be committed before the
  handoff file is written). Making it unconditional removes a decision at
  negligible cost.
- **Handing off** carries the measured ramp. It stays conditional.

### Mechanisms

**Root problem:** the continue-vs-handoff call is re-derived per session, with no
stated rule and no shared cost model, so sessions reach opposite conclusions from
the same facts.

**Givens** (conditions this plan treats as fixed and does not try to change):

- Cache-read is priced at a fraction of base input rate by the vendor; the
  relative cost of carrying a prefix is not ours to set. Out of reach: it is a
  platform boundary — no change to this repo alters it. Without it, the whole
  continue-vs-handoff tradeoff would be priced differently and the rule would
  need re-deriving from scratch.

| # | Assumption | Tag |
|---|---|---|
| 1 | Fresh-session ramp is ~3.55x for turns 0–5, converging to 1.0x by turn ~20–40 | `[verified: turn-index analysis over 287 sessions with >=60 priced main-thread turns, 60d]` |
| 2 | Median context at a `plan-review` boundary is 152k (continued) / 175k (handed off), inside the 1.00–1.31x band | `[verified: boundary analysis over 141 sessions carrying a plan-review boundary, 60d]` |
| 3 | Work remaining after a plan boundary is large and highly variable — median 135 main-thread turns, p25 62, p10 37 | `[verified: same 141-session boundary analysis]` — this variance is why the breakeven cannot collapse to one context number |
| 4 | Split rule: always commit the plan; handoff stays conditional. Placed in plan-it and handoff | `[engineer-verified]` — the engineer chose "plan-it Step 6 + handoff"; within plan-it it lands as a new Step 7, since Step 6 is the `/plan-review` handoff itself and this decision follows it. Placement intent unchanged; flagged rather than silently rewritten |
| 5 | The threshold retune is dropped; the nudge cap stays at its current value | `[verified: re-derivation of the 300000 figure against its own source buckets — the crossover falls inside the 300–400k bucket, ~350k midpoint, not at 300000]`. The engineer challenged the retune's grounding; the drop is this re-derivation's conclusion, not an engineer instruction — see **Out of scope** |
| 6 | The "fewer output tokens per turn at high context" component of the curve is partly task-mix, not purely context cost | `[unverified]` — the `cache_read` component is separately measurable and causal (per-turn cache_read cost rises 14x across the range), and is sufficient on its own to establish the direction |
| 7 | Nudge-to-handoff conversion data is already being collected, so a future cap decision has a live evidence path | `[verified: hook logs a nudged line per fire; handoff/SKILL.md L145–156 appends a handoff line to the same file; handoff-ratio reads it]` — only a report joining the two is absent |

**M1 — Decision rule in `plan-it`, as a new Step 7.** `anchors: root`. plan-it is
where the plan→implementation transition lives, so the rule loads exactly when
the decision is made and costs nothing in sessions that never plan. It lands as
a new Step 7 rather than inside Step 6 because Step 6 *is* the `/plan-review`
handoff, and this decision happens only after that review returns clean.
*Lighter primitives rejected:* (a) a global `CLAUDE.md` rule — loads in every
session on every stow user's machine, including the majority that never reach a
plan boundary; (b) leaving it to the nudge hook alone — the hook fires on a
token threshold and cannot see whether meaningful work remains, which is the
other half of the breakeven.

**M2 — Guard in `handoff`.** `anchors: root`. `handoff` is invocable outside a
plan boundary, so it needs its own brief "is a handoff warranted here" line.
Deliberately duplicated rather than cross-referenced: this repo forbids shared
partials across skills and prefers intentional duplication so each skill reads
standalone.

M1 and M2 are the whole change. No hook, threshold, doc, or test file is touched.

## Critical files

**Create**

- `.claude/plans/handoff-boundary-decision-rule.md` — this file, committed to the
  implementation branch per `branch-management`.

**Modify — M1/M2 (rule text)**

- `claude/.claude/skills/plan-it/SKILL.md` — append a new **Step 7** after the
  existing Step 6. Insert the M1 block from **Drafted text** verbatim.
- `claude/.claude/skills/handoff/SKILL.md` — insert the M2 block from **Drafted
  text** verbatim, after the `mkdir` recipe and before `## Verify the handoff
  file with Bash`.

Deliberately **not** changed: `plan-it`'s frontmatter `description`. Step 7 does
extend the body past "produces a plan and hands off to `/plan-review`," but the
description's job is trigger routing and Step 7 is reachable only from inside
plan-it's own flow — never by independent match. Editing it would spend
always-loaded context budget and force a `run_skill_evals.py` re-run for no
routing gain.

**Explicitly unchanged**

`claude/.claude/hooks/nudge-handoff-near-context-cap.sh`, `docs/handoff-nudge.md`,
`README.md`, `claude/.claude/scripts/transcript-analysis.py`, and every test file.
An earlier draft modified all of these to retune the cap; that is dropped. The
comment at `transcript-analysis.py:4049` naming the current cap as the live 1M
threshold stays accurate precisely because the cap does not move.

## Drafted text

Literal text to insert, so implementation is transcription rather than
re-authoring. Both blocks cite the hook by name and state no token number —
`docs/handoff-nudge.md` and the hook remain the single source of truth for the
threshold.

**M1 — new Step 7 in `claude/.claude/skills/plan-it/SKILL.md`**, appended after
the existing Step 6. A new step rather than an edit inside Step 6: this happens
*after* `/plan-review` returns clean, and Step 6 is the review handoff itself.

```markdown
## Step 7 — Commit the plan, then choose where implementation runs

Commit the reviewed plan to the implementation branch before implementation
begins — it makes an approved plan durable before a phase that rewrites the
working tree, and `handoff` §5 already requires it.

Then choose the session. **Continue in this one by default.** A fresh session is
not free: it re-pays for context this session already holds, and that rebuild
dominates its first several turns, so handing off early costs more than it
saves. Hand off when `nudge-handoff-near-context-cap.sh` has fired for this
session, when the engineer asked for one, when the session is ending regardless,
or when a `handoff` §2 reason applies on its own terms. Treat the nudge as a
floor rather than the only signal — it fires once, is globally disableable, and
per `docs/handoff-nudge.md` can stay silent on an unrecognized model or a
schema-drifted transcript.

Delegating implementation to `code-writer` is a separate axis, not a tiebreaker:
a subagent starts from a fresh context either way, so it neither argues for
handing off nor for staying.
```

**M2 — guard in `claude/.claude/skills/handoff/SKILL.md`**, inserted after the
`mkdir` recipe block and before `## Verify the handoff file with Bash`.

```markdown
## Before writing: is a handoff warranted?

A handoff resets context, and the fresh session re-pays for what this one
already holds — that rebuild dominates its first several turns. Below
`nudge-handoff-near-context-cap.sh`'s threshold, a handoff written *only* to shed
context usually costs more than continuing. A §2 reason that applies on its own
terms, an explicit engineer request, or a session ending anyway each warrant one
without a cost argument. Treat the nudge as a floor rather than the only signal
— it fires once, is globally disableable, and per `docs/handoff-nudge.md` can
stay silent on an unrecognized model or a schema-drifted transcript.
```

## Verification

The change is two prose insertions into skill bodies, so the verification surface
is correspondingly small — there is no behavioral code path to exercise.

1. `../../../.venv/bin/pytest claude/.claude/` — must stay green. Relevant here
   because the skill-body test suite reads several SKILL.md files as fixtures
   (`handoff`'s `mkdir` recipe is one), so an insertion at the wrong offset in
   `handoff/SKILL.md` can break a fixture read even though the text itself is
   inert.
2. `../../../.venv/bin/ruff check claude/.claude/` — no Python changes expected;
   run it to confirm that is actually true.
3. `git diff --stat` names exactly two files:
   `claude/.claude/skills/plan-it/SKILL.md` and
   `claude/.claude/skills/handoff/SKILL.md`. Any third file is scope creep from
   the dropped retune leaking back in.
4. `/skill-review` on both edits, per `.claude/rules/review-pipeline-dispatch.md`
   — hook-enforced on commit by `require-skill-review.sh`. `claude-hook-review`
   is **not** needed: no hook file is touched.
5. Confirm both inserted blocks state no token number, so `docs/handoff-nudge.md`
   and the hook remain the single source of truth for the threshold.

## Out of scope

- **Retuning `HANDOFF_NUDGE_ABS_CAP` (dropped from this plan, not merely
  deferred).** An earlier draft moved it 360000 → 300000. Three reasons it did
  not survive:
  1. **The number was a bucket edge read as a crossover.** The cost-per-work
     buckets are 250–300k at 1.5x and 300–400k at 2.1x. The point where
     continuing overtakes a fresh start falls *inside* the 300–400k bucket,
     whose midpoint is ~350k — which brackets the current value, not 300000.
     The data is consistent with the cap already being about right.
  2. **The supporting figures did not support it.** A 45.5% session-share shows
     300000 is *permissible* under the doc's ≤50% ceiling, not better. And
     "raises dollar-share coverage" is arithmetic — any lower threshold does
     that — so it is not evidence of benefit.
  3. **The mechanism cannot use the precision.** Per the breakeven in
     **Approach**, the call needs work-remaining as well as context, and a
     token-threshold hook sees only one of the two. Tuning it to a "measured
     crossover" claims a resolution the hook cannot act on.

  Against that, the move would worsen two limitations `docs/handoff-nudge.md`
  already documents: a longer unwarned tail (an earlier single shot leaves more
  post-fire runway) and a ~10pp rise in how often the nudge fires, against a
  dismissal-as-noise risk the doc names. Real costs, unproven benefit, on a value
  that has already been retuned once.

  What would justify revisiting: the nudge-to-handoff conversion report (row 7 —
  the data is already being logged, only the joining report is missing), or a
  measurement of how much work typically remains at fire time, which is the
  variable the threshold currently cannot see.
- **The 200k-window arm's `PCT_THRESHOLD`.** Inside reach — same hook, one
  arithmetic line — but untouched for the same reason as above, plus its
  threshold is capacity-bound rather than cost-bound and would need a different
  measurement entirely.
- **Automating the continue-vs-handoff call.** The rule is guidance at a decision
  point. A hook cannot see how much work remains, which is half the breakeven.
