# Add a premise check to /plan-review's design-fitness gate

## Context

**Goal:** make `/plan-review` fire on a plan whose design is well-built but whose
stated problem accepts a constraint the plan could have dissolved — the "does this
solve the right problem" question the gate does not currently ask.

`/plan-review` Step 4 is already the design-fitness gate, and it carries five
foundation-correctness tripwires. Every one reads **design shape**: mechanism
weight, layer count, self-reference, step ordering, rule breadth. None reads
**problem framing**. Two lines make the gap explicit rather than incidental:

- `plan-review/SKILL.md:64` — Step 4's Q2 asks "Is the design appropriately sized
  for the user pain it solves on that surface?" The phrasing presupposes the pain
  is correctly characterized; it tests sizing, not framing.
- `plan-review/SKILL.md:81` — "Question implementation choices, not feature scope
  — the ticket itself isn't reviewed here, that goes back to the author." This
  actively forecloses premise review.

`plan-it/SKILL.md:61` has an author-side counterpart ("Question the ticket's
prescribed approach"), but it fires only on the wrong-foundation tell, and
`plan-review` has no reviewer-side mirror. `plan-review/ROUTING.md:43` advertises
`staff-product-engineer` as owning "whether the plan solves the user problem," but
ROUTING's spawn trigger for that agent is end-user-visible **channels**
(`ROUTING.md:28`) — an internal-tooling plan never gets that reviewer, so the
ownership claim is unreachable for exactly the plan class where the premise most
often goes unchallenged.

**What prompted it:** a plan in this repo passed `/plan-review` on its
implementation merits while treating "the file under this length gate carries a
machine-generated block" as immutable. The premise was attacked only after the
engineer challenged it directly, post-review.

**Intended outcome:** Step 4 gains a sixth tripwire whose fire condition is a test
on plan text, `plan-it` produces the text it reads, and the pair is verified
against a corpus the rule can actually evaluate.

## Approach

Add one tripwire — **Unjustified given** — to Step 4, and make `plan-it`'s
assumption ledger require the givens it reads.

A "given" is a condition the design treats as fixed rather than as something it
could change: an input format, an upstream producer's output, an existing file's
contents, a prior decision.

**The fire condition is reach, and reach includes dependence.** The predicate is
two-pronged: could the plan change the condition, *or* could it remove the design's
dependence on the condition? Either one puts the given inside reach. The second
prong is load-bearing — without it, naming any third-party owner immunizes a given,
and the motivating case (a file owned by another repo) would pass. Owning one
artifact in a causal chain does not put the chain out of reach.

**Reach is not the Critical-files list.** An honest author lists only the files
they intend to touch, which systematically under-represents what the plan *could*
edit. Reach is what the plan could change within the system it operates on. Without
this clause the predicate is gameable with no bad faith required.

**A deliberate scope choice is not a given.** A condition inside reach that the
plan chooses not to change belongs in **Out of scope** with its reason, not in the
givens list. Both the author-side bullet and the reviewer-side tripwire must say
so: otherwise authors list every fixed-for-now condition as a given, none can carry
a beyond-reach reason, and Step 4 fires on all of them — the systematic
false-positive class, relocated from reviewer to author.

**"The engineer already decided it" is explicitly not a qualifying reason.**
Admitting it would make the tripwire self-disabling: the motivating plan's
Out-of-scope section already read "private, out of this repo's scope," so a
sincerity test would clear it on exactly the case that motivated the rule. Engineer
decisions belong on their own `[engineer-verified]` ledger row, which already
carries the "escalate, don't override" contract.

**A missing givens line is an author-side gap, not a foundation concern.** All 44
ledgered plans in `.claude/plans/` predate this grammar (verified below), so
treating a missing givens line as a Step 3 "not ready to implement" return would
halt review on every one of them. It is a normal B8 finding instead: ask the author
to add it. The tripwire itself fires only on plans carrying a ledger; ledger-less
plans (harness plan-mode, other agents') are untouched.

**Why a tripwire and not prose in Q2.** `plan-review/REFERENCES.md:73` records the
design reason the tripwires exist: they "anchor Step 4 to observable surface
features … so they fire even when the AI's internal reasoning is coherent." Q2
already contains "the user pain it solves" and did not fire on the motivating plan.

**Why paired with `plan-it`.** A tripwire reading for a justification the authoring
skill never asks for has nothing to parse.

**Alternatives set aside:**

- *Amend the `SKILL.md:81` disclaimer only* (~3 lines). Rejected — this reproduces
  the un-anchored prose that already failed.
- *Broaden `staff-product-engineer`'s spawn trigger to all plans.* Rejected as an
  over-powered primitive: that agent's lane is user-facing surfaces, and Step 4
  runs **before** any spawn, so a spawn-side fix arrives after the station that
  needs it.
- *A new checklist item (B18) in the Base checklist.* Rejected because Step 5 is
  the wrong station by the skill's own logic — `plan-review/REFERENCES.md:72`
  states "gap-finding on a wrong foundation elaborates the wrong foundation."
- *Mirror the tripwire into `/code-review` Step 1.5.* Rejected: `code-review` reads
  a diff, and a wrong-premise finding there means discarding a finished
  implementation. Named rather than silently scoped out, per CLAUDE.md
  §Engineering Judgment "Audit structural siblings."

### Assumption ledger

Root: `/plan-review` Step 4 tests design shape and design sizing but never tests
problem framing, so a well-built design for a mis-framed problem passes.

Givens: `SKILL.md` has no include/import mechanism, so the rule text is authored in
each skill independently rather than shared — beyond reach: a Claude Code
frontmatter limitation, restated in repo-root `CLAUDE.md` ("No shared partials
across skills"), and nothing in this plan's reach removes the dependence on it.

```
Row 1 [mechanism]: sixth Step 4 tripwire, "Unjustified given" — anchors: root —
  two-pronged reach predicate (change the condition / remove dependence on it) is
  the only formulation that is observable AND fires on the motivating case; the
  alternatives (fires-on-absence, fires-on-weak-reason, change-only reach) each
  fail, per Approach.
Row 2 [mechanism]: plan-it ledger root line names its givens — anchors: row1 —
  without it the tripwire has no text to parse.
Row 3 [mechanism]: pytest parity assertion over tripwire names — anchors: row1 —
  this change is what makes 6-vs-5-vs-5 drift possible across three sites.
Row 4 [assumption]: plan-review/REFERENCES.md mandates a mapping-table row and a
  provenance paragraph per tripwire [verified: REFERENCES.md:12-14 "Keep one row
  per Step 4 tripwire" and :79 "Keep a paragraph here per tripwire"] — anchors: row1
Row 5 [assumption]: length headroom exists — plan-it/SKILL.md 79 lines (limit 200),
  plan-review/SKILL.md 262, ROUTING.md 106 (limit 500 each)
  [verified: wc -l and check-skill-length.sh:61-68 this session] — anchors: root
Row 6 [assumption]: no existing test asserts Step 4's tripwire count or the ledger
  grammar. Two cross-file consistency tests touch these skills —
  test_reconciliation_block_consistency.py and test_skills.py:1864-1884
  (skip-rationale parity) — and both read ROUTING.md sections this change does not
  touch [verified: grepped skills/tests/ and hooks/tests/, and read
  test_skills.py:1864-1884 directly, this session] — anchors: row3
Row 7 [assumption]: the existing plan corpus is 111 plans, 44 carrying an
  "Assumption ledger" and 0 carrying a Givens line [verified: grep -c over
  .claude/plans/*.md this session] — anchors: row2. This is why a missing givens
  line is a B8 finding rather than a Step 3 return, and why Verification item 4
  must back-fill its corpus rather than sample it as-is.
Row 8 [assumption]: REFERENCES.md files are not subject to check-skill-length.sh
  (its staged-file glob covers **/SKILL.md and plan-review/ROUTING.md only), so
  Verification item 2 correctly scopes the length check to the SKILL.md files
  [verified: check-skill-length.sh this session] — anchors: row5
Row 9 [assumption]: paired scope across both skills rather than plan-review alone
  [engineer-verified: selected via AskUserQuestion this session] — anchors: root.
  Scope is six files: Critical files 2 and 6 were Out of scope in an earlier round
  and moved in on reviewer findings, so the expansion was escalated rather than
  resolved here, per the tag's escalate-don't-override contract.
  [engineer-verified: six-file scope re-confirmed via AskUserQuestion this session]
```

Not givens — in-reach conditions this plan deliberately declines to change,
recorded in **Out of scope** with their reasons: Step 4's five existing tripwires,
and `code-review/SKILL.md:44`'s parallel disclaimer.

## Critical files

### 1. `claude/.claude/skills/plan-review/SKILL.md`

Insert as the sixth bullet in Step 4's tripwire list, after "Overcorrection that
negates a named allowance" (currently line 77). **Drafted text:**

> - **Unjustified given.** The ledger's root line names a condition the design
>   treats as fixed that the plan could change — or whose dependence the plan could
>   remove — within the system it operates on. Reach is what the plan could edit,
>   not only what its Critical-files list enumerates; a third party owning one
>   artifact does not put the chain out of reach, and "the engineer already decided
>   it" never qualifies (`[engineer-verified]` tags that). A condition inside reach
>   the plan deliberately declines to change is not a given — it belongs in **Out
>   of scope** with its reason. Required: name the given, name what puts it inside
>   reach, and state what the design becomes without it. Fires only on plans
>   carrying a ledger; a missing givens line is an author-side gap (B8), not a
>   foundation concern.

Replace line 81. **Drafted text:**

> Question implementation choices and the conditions the design accepts as fixed. A
> condition that defines *what* the plan delivers is feature scope and goes back to
> the author; a condition that constrains *how* it delivers is in bounds — whether
> or not the plan lists the file that would change it.

**Reuse:** Step 4's existing stop-and-surface mechanism (line 71) is used as-is.

### 2. `claude/.claude/skills/plan-review/ROUTING.md`

One clause on line 43 (`staff-product-engineer`'s Focus cell) — the surgical fix
for the live misrouting the Context section cites, per B16's solo-scale default.
**Drafted text:** replace "Whether the plan solves the user problem" with "Whether
the plan solves the user-facing problem it claims to (Step 4's ledger-gated
"Unjustified given" tripwire, pre-spawn, catches accepted-but-changeable design
conditions on plans this row never reaches)". Revised during the `/code-review`
round after `staff-product-engineer` found the original phrasing invented
unanchored terminology ("the general premise check") and its aside's placement
risked reading as ceding this persona's own channel-triggered ownership.

### 3. `claude/.claude/skills/plan-review/REFERENCES.md`

Three edits, the first two mandated by the file's own text:

- One row in the mapping table (lines 16-22), anchored to CLAUDE.md §Engineering
  Judgment — "A locally-valid patch can signal a wrong foundation," whose "check
  whether a change one level up … dissolves the need for it" is exactly this check
  applied to an accepted constraint rather than a code patch. The tripwire is an
  existing global principle reaching a station that does not apply it.
- One provenance paragraph in "Foundation-tripwire rules — surfacing incident"
  (lines 69-79), carrying the **two worked fixtures verbatim** (Verification item
  3), each with its one-line reach rationale, plus the sentence the bullet drops
  for length: *a design that only holds because of a given the plan could have
  dissolved solves the problem that given created, not the one stated.* The
  fixtures live here, not in a worktree path, because
  `.claude/worktrees/claude-md-length-generated-block-exclusion/` is pruned when
  that branch lands.
- Normalize "self-referential finding" → "self-referential findings" at line 71 so
  tripwire names match SKILL.md's bullets exactly — required for Critical file 6's
  assertion to be an exact-match parse.

**Redaction:** describe the motivating case by failure mode only — "a plan verified
a hook's filter logic and test coverage without asking whether the file under the
gate needed to carry machine-generated content at all." No downstream-repo identity,
no borrowed marker string, per repo-root `CLAUDE.md` §"When a skill is surfaced by
real-world work, abstract first."

### 4. `claude/.claude/skills/plan-it/SKILL.md`

Replace the ledger's "One root problem/threat line" bullet (line 52). **Drafted
text:**

> - **One root problem/threat line** stating what the plan solves, followed by the
>   **givens** it accepts — conditions the design treats as fixed that lie beyond
>   its own reach. Each carries a one-sentence reason: another party owns it, a
>   vendor or protocol imposes it, or dissolving the design's dependence on it
>   needs a decision outside this plan. "The engineer decided it" is not such a
>   reason — tag that `[engineer-verified]` on its own row. A condition the plan
>   *could* change but deliberately won't is not a given — record it in **Out of
>   scope** with its reason. A given with no qualifying reason is an untested
>   premise, and `plan-review` Step 4 fires on it.

Keep it to the bullet; add no new ledger section.

### 5. `claude/.claude/skills/plan-it/REFERENCES.md`

Add a `Givens:` line to the grammar block (lines 102-109) and to the worked example
(lines 117-132). Not self-mandated by that file — the reason is staleness: once
`SKILL.md` requires givens, a grammar block and example that omit them teach the
wrong shape. **Reuse:** extend the existing example; do not author a second one.

### 6. `claude/.claude/skills/tests/test_skills.py`

New test asserting the invariant `REFERENCES.md` states in prose but nothing
enforces: the set of bolded tripwire names in `plan-review/SKILL.md`'s Step 4 block
equals the set of first-column names in `REFERENCES.md`'s mapping table, and each
name appears in the "Foundation-tripwire rules" section. **Must fail loudly on
extraction failure** — assert the extracted set is non-empty and has cardinality 6,
and raise when the bounding `^## ` heading is not found, so a heading rename or
bold-syntax change cannot yield two empty sets comparing equal.
`test_reconciliation_block_consistency.py` documents this exact trap and is the
working precedent for the section extraction. **Home:** `test_skills.py` already
carries a cross-file skill-content parity test at lines 1864-1884.

## Verification

1. **Suite and lint**, from the worktree:
   ```
   ../../../.venv/bin/pytest claude/.claude/
   ../../../.venv/bin/ruff check claude/.claude/
   ```
   The new parity test must pass; no other count should change. If anything else
   fails, reproduce on the merge-base before treating it as in scope.

2. **Length gate** — `check-skill-length.sh` on `git commit`: `plan-it/SKILL.md`
   ≤200, `plan-review/SKILL.md` and `ROUTING.md` ≤500. `REFERENCES.md` uncapped
   (Row 8).

3. **Discriminating fixture pair — vary only dissolvability.** Two fixtures, frozen
   into `REFERENCES.md`. Both carry an explicit Critical-files list (the predicate
   reads it, so it must be present), and both name an **equally concrete owner** —
   otherwise a grader can produce both verdicts by grading vagueness rather than
   reach, which is the round-1 failure re-entering on a new axis.
   - **Expect fire.** Root: "a length gate denies commits on a file carrying
     machine-generated content." Given: "the file carries a generated block —
     beyond reach: the downstream repo's index generator owns it." Critical files:
     the gate script and its test. Fires on the second prong: the gate script is in
     the plan's reach and defines what the count includes, so the plan can remove
     the design's dependence on that file's contents even though it cannot change
     them.
   - **Expect no fire.** Same root. Given: "the gate receives only the staged blob
     — beyond reach: the Claude Code harness defines the `PreToolUse` hook
     payload." Same Critical files. Neither prong reaches: nothing the plan can
     edit changes the payload or removes the dependence on it.

   **Grading.** Run each in a `general-purpose` subagent (`model: sonnet`) given the
   revised Step 4 text and the fixture but **not** the expected verdict.
   Fire-fixture passes only if the output names that given specifically. No-fire
   fixture passes only if Step 4 does not stop and that given is not named as a
   foundation concern — stated explicitly, since "names the given" is not a
   coherent grade for the negative half. **≥3 independent runs per fixture,
   majority verdict**; a split is a wording defect, not a pass.

   **Expected failure mode.** Prong 2 ("whose dependence the plan could remove") is
   close to universally satisfiable, and the discriminating weight rests on "within
   the system it operates on," which the bullet leaves undefined. The no-fire
   fixture is the one at risk: a plan whose Critical files include the gate script
   could arguably remove the dependence on the harness payload by having the script
   read staged content itself. If the no-fire fixture fires, the pair discriminates
   nothing. Have the narrowing clause drafted **before** the runs, not after — bound
   "within the system it operates on" to artifacts the plan's own repository
   contains — so a fire on the negative is a one-line correction rather than a
   re-design.

4. **False-positive rate on a corpus the rule can evaluate.** The 44 ledgered plans
   in `.claude/plans/` carry no givens line (Row 7), so sampling them as-is fires
   nothing and the check would pass unconditionally. Instead: take 5 of the 44,
   mechanically back-fill a `Givens:` line reflecting each plan's actual accepted
   conditions **without altering its design**, then run the revised Step 4 against
   each in a fresh-context subagent. **The back-fill runs in its own separate
   subagent, given only `plan-it`'s new ledger bullet and the plan — never the Step
   4 tripwire text.** A back-filler that has seen the predicate phrases reasons that
   land cleanly on one side, which suppresses exactly the ambiguous-reason cases
   that generate real false positives; the measurement would then be of phrasing,
   not of corpus realism. **Rejection criterion: any fire the engineer
   disagrees with ⇒ revise the tripwire wording before merge.** A tolerated
   false-halt rate is wrong for a gate that stops specialist spawning.

5. **Author→review round trip.** Run `/plan-it` on one small synthetic task,
   confirm the emitted ledger carries a parseable `Givens:` line with a qualifying
   reason per given and routes in-reach scope choices to Out of scope, then run
   Step 4 against it. This is the flow the pairing depends on and no other item
   covers it.

6. **Skill self-review** — per `.claude/rules/skill-and-agent-self-review.md`,
   invoke `/skill-review` against both changed `SKILL.md` files (hook-enforced by
   `require-skill-review.sh` on `git commit`), then `/code-review`.

## Out of scope

- **Rewriting Step 4's five existing tripwires.** In reach, deliberately declined:
  each has a recorded surfacing incident at `plan-review/REFERENCES.md:69-79` and
  none is implicated here.
- **Mirroring the tripwire, or the line-81 wording, into `/code-review`.** In
  reach, deliberately declined: `code-review/SKILL.md:44` carries the identical
  "not feature scope" sentence, so the two siblings diverge after this change. A
  wrong-premise finding at diff time means discarding a finished implementation.

### What this does not close

Named so the change is not read as solving more of the complaint than it does:

- **Undisclosed givens.** The tripwire reads the ledger's givens line, so it
  catches *disclosed* givens with non-qualifying reasons. A condition the author
  never noticed is never listed and never fires — which is what actually happened
  in the motivating case. Verification item 3's fire fixture rewrites that plan to
  *disclose* its given. The rule converts an invisible framing failure into a
  disclosure-discipline failure; that is progress, not closure.
- **Ledger-less plans.** Harness plan-mode plans and other agents' plans carry no
  ledger, so the tripwire never fires on them. The originating complaint was about
  `/plan-review`, which runs on all of them.
- **Symptom-vs-cause plans with no accepted given at all.** A plan that
  misdiagnoses the cause without treating anything as fixed passes untouched.
- **A root line that faithfully restates a mis-framed ticket.** The amended line 81
  keeps feature scope off-limits by design — the engineer's stated outcome is still
  not reviewable.
- **Frequency-of-one problems.** Nothing here asks whether a problem observed once
  warrants a mechanism; the motivating plan would still clear that bar.
- **Prong 2's boundary is a soft ceiling, not a crisp one.** Verification item 3
  was run against the shipped rule: the fire fixture passed correctly (a peer
  repository's script is in reach), but the no-fire fixture also fired, twice
  across two rounds of narrowing. The first round misapplied "a third party
  owning it doesn't count" identically to both fixtures; the tripwire text now
  distinguishes a peer repository's own artifact from the platform a mechanism
  runs on top of, and a second grading round no longer made that specific
  mistake — but found a different angle instead (arguing the gate script's own
  input-handling, not the harness's contract, was the real dependence). "Could
  the plan remove its dependence on this condition" asks a grader to invent
  alternative designs, which has no natural stopping point in prose alone.
  Accepted rather than chased further: the tripwire's failure mode is
  fail-safe — an over-fire stops specialist spawning and asks a human to
  confirm or dismiss a "Foundation concern," it does not silently pass a real
  gap — and the mechanism prong 2 exists for (the actual motivating case, where
  the plan's own Critical files could dissolve the dependence with no
  third-party question in play at all) fires correctly.
