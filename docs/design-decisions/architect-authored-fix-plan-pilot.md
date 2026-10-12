# A default-off pilot has `plan-architect` author each `/code-review` round's fix plan

*2026-10-08.*

By default, `/code-review` has the orchestrating session decide each round's
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

`MODE=consult` is reused, and no new mode is added. `plan-architect.md` is
untouched because its description already admits a skill-prescribed consult.
`MODE=consult` asks for conversational prose that
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
treats a hedged consult as a blocking stop. The pilot diverges on purpose:
numbered rows turn row-to-finding matching into a number comparison, where
judging whether prose is hedged would be a fresh judgment each round.
`DISPOSITION_RULE:code-review-fix-plan-pilot` in
`claude-skills/skills/code-review/SKILL.md` states what text outside the rows
does.

## Why every return that is not a valid table stops

`DISPOSITION_RULE:code-review-fix-plan-pilot` in
`claude-skills/skills/code-review/SKILL.md` defines the valid table and the
stop. This section records only why the stop has no fallback and no retry.

Falling back to the default procedure on a malformed return was rejected: it
leaves unknown, for any given PR, whether the architect ran consistently.
The engineer's words: "The default is a complete fallback does NOT
constitute a good reason. That just adds instability to the experiment. Now I
don't know for any given PR running the experiment when the architect ran
consistently."

There is no automatic re-dispatch, so there is no retry count to ground; no
primary source supplies one. Each stop names the condition the return
failed, which leaves the evidence for any later retry decision in the
transcript. The cost is that a malformed return interrupts the human.

## Which findings the fix shape replaces

`DISPOSITION_RULE:code-review-fix-plan-pilot` in
`claude-skills/skills/code-review/SKILL.md` defines which findings a row's fix
shape replaces. This section records only why.

An ADDRESS row's fix shape replaces the Suggested fix on every finding, a
barred one included. The table's shape check does not test whether a fix shape
closes the hole a finding states. The engineer objected to a carve-out that
kept the reviewer's Suggested fix on barred findings: "Why wouldn’t we have the
architect’s fix be subject to approval by the reviewer."

A consult of the Fable 5 model, dispatched by the session, read the next
review of the staged diff as the place approval of the fix falls. That is a
design reading, and it adds no new approval step.

Two of the engineer's proposals were not adopted. The reasons are that
consult's, not the engineer's.

- **A separate adversarial briefing for the consult.** A fresh dispatch is
  already a different context. An extra briefing line would make the pilot's
  consult prompt differ from a key-off round's.
- **Reviewer pre-approval of the architect's fix.** It adds a dispatch type, a
  return grammar and an undefined reject branch. It also has a Sonnet
  reviewer judge an Opus fix against its own proposal.

## Placement

The engineer chose a `DISPOSITION_RULE` region in the base file, gated at
runtime by the config key.

- **A runtime auxiliary file** was rejected as heavier. It would need a
  `_skill_auxiliary_files.py` entry, a `check-skill-length.sh` regex edit
  shipped to every consumer, and either a read-gate hook or a waiver.
- **The project layer** was rejected by the engineer, quoted verbatim: "The
  project layer is a terrible idea for the record. It is biased by the line
  cap."
- **A config-gated name-only skill** (the shape `pr-description` uses to gate
  `tighten-prose`) was not chosen. The base file would carry only a gate, but
  the behavior would cite about ten base-file rules from a new skill, five of
  them written as "above" or "below". It would also need a new skill
  directory, a `skillOverrides` entry with its name-only count pin, a
  retargeted whole-region pin and anchor, and another plan-review and
  skill-review round. Keeping the region costs every consumer 3,421 bytes more
  per `/code-review` and leaves 4 lines of headroom in `code-review/SKILL.md`.
  Only a revert ends either cost. The reasoning is from the architect consult
  on the fifth cumulative review, with the byte figure corrected to the
  measured size. It is not the engineer's.
- **Respecting a loaded project layer's rules is not placement.** The
  dispatch carries the layer's path when Step 0.5 loaded one, and the
  barred-row item names the layer as a source beside the enforcement-invariant
  rule. Without that, a key-off round's orchestrator applies the layer's
  rules and the pilot's architect never sees them, so a comparison of key-on
  against key-off rounds would measure the architect plus a loosened rule. The
  dispatch also carries the base file's DEFER criteria and invalid DEFER
  rationales, so the architect applies the same rulebook a key-off round's
  orchestrator applies from its own skill body. The pilot stays in the base
  file.

## Cost to every stow consumer

- **Load.** Every stow consumer loads the region on every `/code-review`.
  `_PINNED_FIX_PLAN_PILOT_CLAUSE` in
  `claude-skills/skills/tests/test_skills.py` holds its exact text.
- **Line gate.** The region's lines count against `check-skill-length.sh`'s
  500-line cap on `code-review/SKILL.md`, a file every consumer loads.
- **One `config-get.sh` call per round with a reviewer-spawned finding**,
  which may prompt for permission, because neither `config-get.sh` nor
  `review-ledger.sh` has a `permissions.allow` entry. `/code-review` already
  calls `review-ledger.sh` every round, so the call matches the current
  permission posture. `DISPOSITION_RULE:code-review-fix-plan-pilot` in
  `claude-skills/skills/code-review/SKILL.md` states which outcome enables
  the pilot.
- **A per-account key.** An opted-in account runs the pilot in every repo it
  reviews, while the key-on versus key-off comparison counts this repository
  only.

## Ledger and observability

The ledger schema is unchanged, because a script change would reach every
consumer. The merged schema rejects `--decided-by` on ADDRESS and allows only
`carry` on DEFER, so a per-row marker for table-decided rows has to ride in
`--rationale`. `DISPOSITION_RULE:code-review-fix-plan-pilot` in
`claude-skills/skills/code-review/SKILL.md` states that marker, how KEEP rows
are logged and the round's outcome tags.

## Schema scaffold: the explicit `false` row

Flipping this key's default to `true` later reaches no scaffolded account
without a row migration. `docs/config-file.md` § "Migration" is canonical for
the scaffold.

## Known gaps

- **The round-3 latch.** `log-reviewer-round.sh` writes the once-per-branch
  latch for any `plan-architect` dispatch whose first line is not
  `MODE=plan-sections`, reading only that first line. Any pilot dispatch that
  returns writes it, whatever the return said, so a return with no
  foundation verdict can latch the branch. Whether a dispatch that ends in a
  tool error writes it depends on whether `PostToolUse` fires on an Agent
  call that errors, which is unverified. The latch cannot carry narrower
  content without reversing the round-3 decision. Every `MODE=consult`
  dispatch writes the latch the same way, including consults that ask no
  foundation question. The engineer accepted the interaction. The stop makes
  it visible and does not prevent it.
- **A resume between the dispatch's return and the ledger append** can
  re-dispatch.
- **The round-cap consult's "stop escalating" verdict** has no pilot
  counterpart beyond per-finding gold-plating DEFER rows. The round-cap
  consult itself folds in through the latch: the first pilot dispatch on a
  branch leaves the round-cap section unreachable on that branch, and every
  later round's foundation line asks its re-plan question again.
- **Overlap with `ready-for-review`'s Cap consult.** At most one extra consult
  per `ready-for-review` run overlaps with the pilot.
- **A DEFER row's criterion is checked by name, not by its conditions.** The
  dispatch carries the closed list and the invalid DEFER rationales, but the
  shape check does not judge a row against them. A DEFER row that names a
  closed-list criterion with a barred rationale, such as "pre-existing gap",
  is a valid row and logs as DEFER.
- **A security-class finding is decided by criterion name too.** A DEFER or
  KEEP row on one is barred only where the enforcement-invariant rule or a
  loaded project layer bars it, which matches the default arm. The engineer
  kept this.
- **No untrusted-data rule or row delimiter.** Findings, diff and plan text
  reach the consult with no rule marking them as data, and a row is
  recognized by its line prefix alone. Findings and diff already reach every
  reviewer and `code-writer` the same way, and a delimiter the model picks
  would add a mechanism for no gain.
- **An ADDRESS row's files are not confined** before the `code-writer`
  dispatch. The heavier-mechanism rule's outside-the-diff limb is the only
  route away. The engineer kept this.
- **No quoting rule for architect-derived text** in the ledger append
  arguments. The ledger section's single-quote form for every value is the
  only guard. The engineer kept this.
- **A table-decided row can authorize a commit.** A finding logged DEFER or
  SETTLED counts as resolved for the commit gate
  (`code-review/SKILL.md` § "Step — Record review completion"), so a DEFER or
  KEEP row that passes the shape check can authorize a commit over its
  finding.
- **An ADDRESS row on an enforcement-invariant finding reaches `code-writer`
  with only a shape check.** Closure then rests on the next staged-diff
  review. That review exists: `subagent-delegation/SKILL.md` keeps the
  `/code-review` re-run with the parent after the `code-writer` fix, and
  `code-review/SKILL.md` § "Step — Record review completion" ties the commit
  marker to the staged-diff hash. It is conditional on the orchestrator
  matching a spawn row for a reviewer that covers the finding, and on a
  reviewer re-finding a still-open hole. Nothing pins or tests it.
- **The pilot offers KEEP more widely than the default arm does.** The default
  arm's keep reaches a finding whose fix would undo an earlier fix. The pilot
  marks any payload finding at an earlier round's site, including a block a
  live DEFER or SETTLED decision names. The deciding
  architect may have written that earlier fix, and no orchestrator-disagreement
  stop applies. A DEFER row, checked by criterion name only, could end the same
  finding, so narrowing KEEP would not close it.
- **A Rejected heavier-mechanism consult can end an enforcement-invariant
  finding with no fix and no stop.** The rule's Rejected bullet says only
  "No second dispatch and no plan edit", so the finding's disposition after
  it is unstated. The commit marker rule blocks the commit but asks no
  question and logs no `--enforcement-invariant` stop. The key-off arm has
  the same text. The fix is one general clause on that bullet, as a follow-up
  to the rule itself.
- **Measurement contamination.** Key-on dispatches write the round-3 latch
  and count as consults in `review-trace`, so measurements of either must
  exclude key-on accounts.

## Measurement and enabling

The key stays off on every account until all four exist:

- A baseline of review-loop convergence and disposition-error rates without
  the pilot (#1211).
- A pre-registered adopt/revert rule with a stop-rate threshold. It names the
  behavior evidence required before enabling: the scope of any Layer-2 run or
  cited transcript.
- A reader for the `fix-plan:` prefix and outcome tags, run on
  a synthetic fixture before any account enables the key, because ledger
  rows and transcripts roll over at 30 days.
- A deny/allow fixture pair, run against the region with its result recorded
  in the pre-registration follow-up: an enforcement-invariant finding with a
  DEFER or KEEP row yields a stop, and the same finding with an ADDRESS row in
  an otherwise valid table proceeds with the architect's fix shape. Where a
  project layer is loaded, a finding it bars from DEFER is run the same way.
  The region's barred-row stop is pinned as text only.

The engineer who enables the key runs the fixture pair. The allow-path fixture
is constructed so the architect's fix shape differs from the reviewer's
Suggested fix. Each recorded result cites every ADDRESS row's fix text from the
`code-writer` dispatch prompt, which is the evidence artifact. That record is
the sign-off.

The `fix-plan:` rationale prefix is advisory, unchecked free text unless the
pre-enable reader checks it. Without that check, an omitted prefix makes a
table-decided row read as an orchestrator-decided one.

**Enable, check, disable.** Hand-edit the key as
`docs/config-file.md` § "Writing a value" describes.
Check it as
`docs/config-file.md` § "Querying a value" describes.
The value must be bare `true`, and exit 0 means on. A stderr warning from the
check means the row was rejected and the key reads `false`. Disable by setting
`false`. The key applies per account, and the check reads the config dir of the
invoking shell, so run it for the account being enabled.

**The hold is open-ended.** The engineer accepted no removal backstop if the
key is never enabled. The round-2 pilot's record fixed its gates before its
sentinel existed and also carried a stopping rule; this pilot fixes gates
before enabling but ships no stopping rule.

**Revert contract.** Delete the region, its pin constants and test class, its
anchor-set entry. Keep
`test_every_config_get_key_named_in_a_skill_is_in_the_config_schema`, which
still covers the call sites that outlive the region. Set the config-schema
audit's Call site for this key to none. Keep the key row inert at default `false`, because
deleting a schema row makes every scaffolded `claude-config.toml` warn on
every read. Keep the per-key tests, which still hold for an inert row:

- The hand-typed schema pins in `test_config_py.py` (known set and defaults,
  per-account resolution, bool type, empty prompt description).
- `TestArchitectFixPlanPilotGate` in `test_config_get.py`, whose class
  docstring must no longer cite the deleted region.

`test_fresh_home_with_no_state_file_exits_1` in that class asserts the `false`
default independently of the hand-typed pins. Keep this record so the schema's
docs-anchor never dangles. Reverting the merge commit is not the rollback: it
deletes the schema row while accounts keep their scaffolded row, so every
config read warns. Set the key `false` on any account that enabled it.

**Adopt.** A follow-up supersedes the
[§42](code-review-fix-route-plan-architect.md) and
[§59](code-reviews-fix-route-unifies-42s.md) routes. A default-off pilot
overturns neither, so no supersession edit is made now.

**Key-off check.** A key-off round with a reviewer-spawned finding shows one
`config-get.sh architect_fix_plan_pilot` read that exits 1 and no `fix-plan:`
tag. The check passes only on an observed exit 1. A read that exits with any
other code, or yields none, also leaves no tag, so on any account only the
read's exit code in the transcript tells it from key-off.

## Sources

- #1213 (the pilot), #1187 (the review-only role this would supersede on
  adopt), #1211 (the baseline).
- `.claude/plans/architect-authored-fix-plan.md` — the plan, with the
  assumption ledger behind each claim above.
- [round3-consult-verdict-routing.md](round3-consult-verdict-routing.md) —
  the precedent departed from.
- [round2-consult-trigger-pilot-revert.md](round2-consult-trigger-pilot-revert.md)
  — the default-off pilot precedent, including keeping its key row on revert.
