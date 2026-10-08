# A default-off pilot has `plan-architect` author each `/code-review` round's fix plan

*2026-10-08.*

`/code-review` today has the orchestrating session decide each round's
dispositions, and consults `plan-architect` only on three narrow triggers:
a heavier mechanism, a contradiction with an earlier fix, and the round cap.
A new per-account config key, `architect_fix_plan_pilot`, default `false`,
pilots a different split. With the key on, a round's remaining
reviewer-spawned findings go, numbered, to one `plan-architect`
`MODE=consult` dispatch. Its numbered rows become the round's disposition
when the return passes a mechanical shape check, and every other return stops
for the human.

`code-review/SKILL.md`'s `DISPOSITION_RULE:code-review-fix-plan-pilot`
region is canonical for the rule. This entry doesn't restate it and states
only why the pilot has this shape.

## Why default-off

[§42](code-review-fix-route-plan-architect.md) rejected an unconditional
`plan-architect` consult on every ADDRESS finding on three grounds, and the
pilot engages each:

- **Volume.** A key-on round with a non-empty payload costs one Opus
  dispatch, so the ceiling scales with rounds, not with mechanism-proposing
  fixes. Default-off keeps that volume out of every account that has not
  opted in. §42's published figures are cited by reference, not recomputed
  here.
- **Relocating the scope argument.** Without the key, nothing moves. With it,
  the contestable judgment (is this a gold-plating DEFER, is this a design
  question) sits in the architect's rows instead of the orchestrator's, and
  the barred-row stop keeps the enforcement-invariant rule and a loaded
  project layer binding on those rows.
- **A third genre inside `MODE=consult`.** The pilot adds a row grammar to a
  consult's request, which the next section addresses directly.

Whether an Opus-authored per-round fix plan makes review loops converge with
fewer disposition errors is untested. The pilot exists so the design can be
reviewed before the measurement does.

## Why `MODE=consult`, and the departure from the round-cap precedent

`MODE=consult` is reused and no new `plan-architect` mode is added;
`plan-architect.md` is untouched, because its description already admits a
skill-prescribed consult. `MODE=consult` asks for conversational prose that
commits to a recommendation. It does not bar numbered rows and a foundation
line inside that prose, so a prose-wrapped table is the expected return.

This departs from
[round3-consult-verdict-routing.md](round3-consult-verdict-routing.md), which
rejected a new return grammar because a second grammar must be maintained in
lockstep with `code-review`'s reading rule, and kept the trichotomy "a rule
for the reading session to apply, not a contract imposed on the consulted
agent". Here the dispatch prompt imposes a line-level grammar (one row per
payload number, one `foundation:` line). The lockstep objection does not
apply, because the grammar and its reader sit in the same pinned region of
one file, so they cannot drift apart without a test failing.

**A hedged KEEP row stays valid, deliberately.** The contradiction route
treats a failed, empty, hedged, or partial consult as a blocking stop. The
pilot reads only the rows and the single `foundation:` line, so prose around
a row, including a hedge, is relayed verbatim and decides nothing. A KEEP row
with a hedge in the surrounding text is therefore a valid row. The pilot
diverges here on purpose: numbered rows turn row-to-finding matching into a
number comparison, where judging whether prose is hedged would be a fresh
judgment each round. A finding the architect cannot commit to gets no row,
which stops the round.

## Why every return that is not a valid table stops

A valid table is three mechanical checks: the row numbers match the payload
numbers, each row carries a valid verdict word and that verdict's required
parts, and exactly one `foundation:` line reads `foundation: sound`. Every
other return, a failed dispatch included, is a blocking stop-and-ask before
any fix is dispatched. The one judgment left is the barred-row check, which
is the same classification the default procedure makes for the same finding.

Falling back to the default procedure on a malformed return was rejected: it
leaves unknown, for any given PR, whether the architect ran consistently.
The engineer's words: "The default is a complete fallback does NOT
constitute a good reason. That just adds instability to the experiment. Now I
don't know for any given PR running the experiment when the architect ran
consistently."

There is no automatic re-dispatch, so there is no retry count to ground; no
primary source supplies one. Each stop names the condition the return
failed, which leaves the evidence for any later retry decision in the
transcript. The cost is that a malformed return interrupts the human. The
tag on the round's `Fix route:` line records the round's final outcome:
`dispatched`, `fell-back` or `stopped`.

## Placement

The engineer chose a `DISPOSITION_RULE` region in the base file, gated at
runtime by the config key.

- **A runtime auxiliary file** was rejected as heavier. It would need a
  `_skill_auxiliary_files.py` entry, a `check-skill-length.sh` regex edit
  shipped to every consumer, and either a read-gate hook or a waiver.
- **The project layer** was rejected by the engineer, quoted verbatim: "The
  project layer is a terrible idea for the record. It is biased by the line
  cap."
- **Respecting a loaded project layer's rules is not placement.** The
  dispatch carries the layer's path when Step 0.5 loaded one, and the
  barred-row item names the layer as a source beside the enforcement-invariant
  rule. Without that, the control arm's orchestrator applies the layer's
  rules and the pilot's architect never sees them, so the evaluation would
  measure the architect plus a loosened rule. The pilot stays in the base
  file.

## Cost to every stow consumer

- **Bytes.** The region adds 3,429 bytes and 8 lines to
  `claude-skills/skills/code-review/SKILL.md`, which `wc -c` measured at
  78,118 bytes before and 81,547 after (`awk 'END{print NR}'`: 488 lines
  before, 496 after). Every stow consumer loads them on every `/code-review`.
- **The loaded-layer rule.** The clauses carrying it measure, with `wc -c` on
  the clause strings: `and the project layer's path when Step 0.5 loaded one`
  is 53 bytes, `or a loaded project layer` is 25, and the logging clause
  `and a loaded project layer` is 26.
- **One `config-get.sh` call per round with a reviewer-spawned finding**,
  which may prompt for permission, because neither `config-get.sh` nor
  `review-ledger.sh` has a `permissions.allow` entry. `/code-review` already
  calls `review-ledger.sh` every round, so the call matches the current
  permission posture. Only an observed exit 0 enables the pilot, so every
  other outcome, a call with no exit code included, reads as off.
- **A per-account key.** An opted-in account runs the pilot in every repo it
  reviews, while the evaluation counts this repository only.

## Ledger and observability

The ledger schema is unchanged. The merged schema rejects `--decided-by` on
ADDRESS and allows only `carry` on DEFER, so every table-decided row's
`--rationale` starts with `fix-plan:`, the only per-row marker available
without a script change every consumer would receive. A KEEP row is logged
`SETTLED --decided-by plan-architect`, as the contradiction route logs a
consult's keep.

## Schema scaffold: the explicit `false` row

The key has no legacy file, like `test_selection_tracking`. The config
scaffold therefore backfills an explicit `architect_fix_plan_pilot = false`
row into every `<config-dir>/claude-config.toml`. An explicit row shadows the
schema default, so flipping the default to `true` later needs a row
migration; changing the `default` column in `config-keys.psv` alone would
not reach any scaffolded account.

## Known gaps

- **The round-3 latch.** `log-reviewer-round.sh` writes the once-per-branch
  latch for any `plan-architect` dispatch whose first line is not
  `MODE=plan-sections`, reading only that first line. Any pilot dispatch that
  returns writes it, whatever the return said, so a return with no
  foundation verdict can latch the branch. Whether a dispatch that ends in a
  tool error writes it depends on whether `PostToolUse` fires on an Agent
  call that errors, which is unverified. The latch cannot carry narrower
  content without reversing the round-3 decision. The engineer accepted the
  interaction. The stop makes it visible and does not prevent it.
- **A resume between the dispatch's return and the ledger append** can
  re-dispatch.
- **The round-cap consult's "stop escalating" verdict** has no pilot
  counterpart beyond per-finding gold-plating DEFER rows. The round-cap
  consult itself folds in through the latch: the first pilot dispatch on a
  branch leaves the round-cap section unreachable on that branch, and every
  later round's foundation line asks its re-plan question again.
- **Overlap with `ready-for-review`'s Cap consult.** At most one extra consult
  per `ready-for-review` run overlaps with the pilot.
- **Measurement contamination.** Key-on dispatches write the round-3 latch
  and count as consults in `review-trace`, so measurements of either must
  exclude key-on accounts.

## Measurement and enabling

The key stays off on every account until all three exist:

- A baseline from #1211 steps 1-3.
- A pre-registered adopt/revert rule with a stop-rate threshold. It names the
  behavior evidence required before enabling: the scope of any Layer-2 run or
  cited transcript.
- The evaluation's reader for the `fix-plan:` prefix and outcome tags, run on
  a synthetic fixture before any account enables the key, because ledger
  rows and transcripts roll over at 30 days.

**Enable, check, disable.** Hand-edit `architect_fix_plan_pilot = true` in
`<config-dir>/claude-config.toml`. Check with
`claude/.claude/scripts/config-get.sh architect_fix_plan_pilot` (the installed
copy is in the config dir's `scripts/`); exit 0 means on. Disable by setting
`false`. The key applies per account.

**The hold is open-ended.** The engineer accepted no removal backstop if the
key is never enabled. The round-2 pilot's record fixed its gates before its
sentinel existed and also carried a stopping rule; this pilot fixes gates
before enabling but ships no stopping rule.

**Revert contract.** Delete the region, its pin constant and test class, its
anchor-set entry and its wiring test. Set the config-schema audit's Call site
for this key to none. Keep the key row inert at default `false`, because
deleting a schema row makes every scaffolded `claude-config.toml` warn on
every read, and keep the hand-typed schema pin, the only independent guard of
that default. Keep this record so the schema's docs-anchor never dangles.
Set the key `false` on any account that enabled it.

**Adopt.** A follow-up supersedes the
[§42](code-review-fix-route-plan-architect.md) and
[§59](code-reviews-fix-route-unifies-42s.md) routes. A default-off pilot
overturns neither, so no supersession edit is made now.

**Post-merge key-off check.** The first `/code-review` in this repository
after merge whose round has a reviewer-spawned finding must show one
`config-get.sh architect_fix_plan_pilot` call that reads off, the default
procedure, and no `fix-plan:` tag.

## Sources

- #1213 (the pilot), #1187 (the review-only role this would supersede on
  adopt), #1211 (the baseline).
- `.claude/plans/architect-authored-fix-plan.md` — the plan, with the
  assumption ledger behind each claim above.
- [round3-consult-verdict-routing.md](round3-consult-verdict-routing.md) —
  the precedent departed from.
- [round2-consult-trigger-pilot-revert.md](round2-consult-trigger-pilot-revert.md)
  — the default-off pilot precedent, including keeping its key row on revert.
