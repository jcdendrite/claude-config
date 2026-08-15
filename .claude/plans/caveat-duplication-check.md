# Detect rationale restated across sites

## Context

**Goal:** make this repo's review pipeline catch "the same explanation,
measurement, or justification written to two or more sites instead of one
canonical home plus pointers" before a human PR reviewer has to.

An error-mode analysis of this repo's 2026-08-02 → 2026-08-14 session corpus
found 6 instances of the agent defaulting to a heavier, multi-site fix and
being redirected to a lighter, single-source one — 5 caught live in
conversation, 1 caught only at PR review. In that one, PR #631, the reviewer
wrote: *"Wrong solution overall. You added the plan mode caveat all over the
place. This is compounding engineering."*

**What that evidence does and does not support.** Only PR #631 produced a
shipped diff, so it is the only instance confirmed to match the specific
shape this check targets — duplicated prose, as distinct from duplicated
logic or an unrelated over-powered-primitive choice. The other 5 were
conversational corrections with nothing left to inspect; they establish that
the *family* of defect recurs, not that this check would have fired on any of
them. The design below is justified on the one confirmed case plus the
architecture already in place, not on an unexamined count of 6.

Why now: the rule this violates is already loaded in every session —
`CLAUDE.md` §Engineering Judgment carries both "Single source of truth" and
"Compounding defensive layers are a wrong-foundation tell." Availability is
not the gap; **activation** is. Nothing in the automated pipeline converts
either rule into an observable a reviewer checks against diff or plan text.

Intended outcome: four one-line tripwires, one appended to each of the four
enumerations the pipeline already runs at author and reviewer stations, plus
one decision record and one presence test guarding them. No new agent, no new
numbered checklist item.

## Approach

Each of the four stations gets **one line naming an observable** — "the same
rationale appears at 2+ sites" — appended to a tripwire list that already
exists there. None of the four restates the CLAUDE.md rule; each points at
it. The rationale for the whole change lives in exactly one new place,
`docs/design-decisions.md` §25.

That shape is forced by an on-record rejection. `docs/design-decisions.md`
§13 (2026-05-22) already considered and rejected the obvious form of this
change:

> A `/code-review` checklist item was considered and rejected: it would be a
> second copy of an always-loaded rule on a surface that can drift from it —
> the exact failure the rule names.

§13's objection is to a checklist item that **restates the principle**. It
does not reach a tripwire that **names an observable**. That distinction is
this repo's own established answer to "rule loaded but not applied," settled
in `.claude/plans/activate-engineering-judgment-at-review-time.md:18`:

> A tripwire is the detection *procedure* for a principle, not a restatement
> of it — different knowledge, different home, not a DRY violation.

So this plan does not revive the rejected item. It applies the pattern that
replaced it — the same pattern that produced `code-review`'s existing Step
1.5 tripwires (`Unverified external-state claim`, `Out-of-scope file edits`,
`Preserved-record edits`, `Non-durable comment`).

**The discriminator is reader reachability.** The firing test is: *can the
reader at this site follow a pointer to the canonical home?* If yes,
duplication is a defect. If each site's reader loads independently and cannot
reach the other, the duplication is load-bearing and the tripwire must stay
silent.

That test is the operational form of `skill-review` §6's three-condition rule,
and it classifies both known cases correctly:

- **Fires on PR #631.** The canonical home was `docs/auto-mode.md`, reachable
  from every site that restated it — `CLAUDE.md` is always loaded, and a doc
  file is one Read away. Nothing forced those sites to carry the measurement
  inline.

  *Count, derived from `git show 2001121c` this session:* 8 files touched.
  `docs/auto-mode.md` is the canonical home and `docs/cost-levers-considered.md`
  a separate follow-up note. Of the remaining 6, **five restate the measurement
  digits inline** (`CLAUDE.md`, `agents/Explore.md`,
  `skills/agent-review/SKILL.md`, `skills/plan-it/SKILL.md`,
  `skills/plan-review/SKILL.md` — each carrying some combination of the
  `0/70`, `340/341`, `92/95`, `0/32` figures) and one
  (`skills/subagent-delegation/SKILL.md`) only points at `CLAUDE.md` without
  restating them. The inclusion criterion is *restates the measurement*, not
  *mentions plan mode* — the pointing site is the near-miss that makes the
  criterion load-bearing rather than cosmetic.
- **Stays silent on the `## Reconciliation` block**, duplicated near-verbatim
  between `code-review/SKILL.md:269-284` and `plan-review/ROUTING.md:53-68`
  and pinned by `claude/.claude/hooks/tests/test_reconciliation_block_consistency.py`.
  A session running `/code-review` never loads `ROUTING.md` and vice versa, so
  neither reader can follow a pointer to the other. This is sanctioned,
  tested, deliberate duplication of *explanatory* prose.

A rationale-vs-instruction split was drafted first and rejected: it fires on
the `## Reconciliation` block, which is duplicated rationale by design. Any
formulation keying on *what kind of text* is duplicated hits that case;
keying on *whether the reader can reach the canonical home* does not. What
remains true is that rationale is the usual thing over-copied — in PR #631
every file legitimately needed the *instruction* ("pass `model: sonnet`") and
only the *justification* was over-copied, which is what the corrective commit
`962779fb` moved to one home. So rationale-vs-instruction is useful for
deciding **what to move**, never for deciding **whether to fire**.

**Threshold is 2, not 3.** Taken from CLAUDE.md's own wording — "Before
writing something a second time, pick the canonical home" — rather than
inherited from checklist item 9's code-duplication count of 3.

**Alternatives set aside.** A dedicated fresh-context reviewer agent modeled
on `comment-discipline-reviewer`: rejected as the heavier primitive, and the
precedent argues against it on its own terms — that agent shipped for comment
verbosity and the defect recurred within 48 hours on PR #645 (error-mode
report row #10), so a dedicated agent is not demonstrated to be what makes a
check stick. Adding an angle to `comment-discipline-reviewer` instead:
rejected because its charter is explicitly scoped to CLAUDE.md §Code
Comments, Documentation, and Prose, while this rule lives in §Engineering
Judgment. A new numbered Hygiene item (9i): rejected per §13 above.

### Assumption ledger

**Root problem:** `CLAUDE.md`'s single-source-of-truth and compounding-layers
rules are loaded in every session but not converted into anything the review
pipeline observes, so multi-site rationale duplication reaches human review.

**Givens:**

| # | Given | Reason |
|---|---|---|
| G1 | "Same rationale" is not a computable predicate; the check can only ever be a model judgment against an observable | A capability boundary of the runtime, not an artifact any repo owns — semantic equivalence between two prose passages is not decidable by string comparison, so no hook this repo could write would decide it. Hooks can gate on the *presence* of text, never on whether two passages carry the same reasoning. [verified: `code-review/SKILL.md:49-54` — every existing Step 1.5 tripwire is a model-judgment check, none is hook-backed] |
| G2 | The engineer wants coverage at all four stations, having explicitly weighed and accepted the breadth | [engineer-verified] — answered this session, with the standing instruction to remain wary of overengineering the decision itself. |

**Mechanisms:**

- **M1 — `code-review` Step 1.5: one tripwire bullet.** The reviewer-side
  code-stage station. Extends an existing four-bullet list; adds no numbered
  item and therefore no Item-ownership row. `anchors: root`
- **M2 — `code-writer` self-review step 6: one bullet.** The author-side
  code-stage station. Step 6 is documented as "the same set the code-review
  skill's Judgment-activation pass checks, applied here before handoff"
  (`code-writer.md:92-94`), so the two lists are a deliberate pair; adding to
  one without the other breaks a documented symmetry. `anchors: root`
- **M3 — `plan-review` Step 4: one foundation tripwire.** The reviewer-side
  plan-stage station. Extends the existing six-tripwire list (`Over-powered
  primitive`, `Compounding layers`, `Self-referential findings`, `Misordered
  observe-then-mutate steps`, `Overcorrection`, `Unjustified given`).
  `anchors: root`
- **M4 — `plan-it` Step 5: one sentence.** The author-side plan-stage
  station, appended to the existing per-mechanism over-powered-primitive
  paragraph. `plan-review`'s own `Over-powered primitive` tripwire states its
  threshold is "the same threshold `plan-it` Step 5 sets for the author, so a
  one-alternative plan fails here rather than passing review while violating
  the authoring rule" (`plan-review/SKILL.md:82`) — the repo's convention is
  that a plan-stage reviewer tripwire has an author-side counterpart.
  `anchors: root`
- **M5 — `docs/design-decisions.md` §25 (new).** The single canonical home
  for this change's rationale, recording the revisit of §13 on new evidence.
  §13 itself is left byte-for-byte intact: it is a dated decision record and
  therefore preserved content under CLAUDE.md §Working Style Axis 3.
  `anchors: root`
- **M6 — a presence-only pytest pinning the four tripwires.** Nothing else
  prevents a future length-trimming pass from silently deleting a one-line
  addition; `code-review/SKILL.md` sits at 405 of its 500-line cap, so that
  pressure is real rather than hypothetical. `anchors: root`

**Why four stations is not itself the defect it catches.** The
over-powered-primitive check applies to this plan reflexively, so it is
answered explicitly. Two lighter primitives were enumerated and both fail:

1. *One station only (`code-review`)*. Fails because the two stages observe
   different populations, evidenced in PR #631 itself. At plan stage:
   `.claude/plans/plan-mode-model-routing.md`'s mechanisms M1/M2/M4/M8 each
   independently instruct adding the claim *with its measurement*, and none is
   designated the canonical home — observable in the plan text before any code
   was written. At code stage: commit `962779fb` records that "the first fix
   round's own edits introduced fresh duplication," which no plan named. One
   station misses one of those two populations.
2. *Reviewer stations only, no author stations*. Fails against the repo's
   documented author/reviewer pairing at both stages (`code-writer.md:92-94`;
   `plan-review/SKILL.md:82`), which exists so an authoring rule and its
   review gate cannot drift.

The repo's own canonical test for justified duplication agrees. `skill-review`
§6 and `agent-review` §6 permit duplicating content across skills and agents
when **all three** conditions hold; all three hold here:

1. *Content is critical* — each site is a review gate.
2. *Different load paths* — `code-review` and `plan-review` bodies load when
   their skill is invoked; `code-writer`'s body loads at dispatch; `plan-it`'s
   loads at plan authoring. Four distinct paths.
3. *One path could silently fail* — a `code-writer` dispatch can return
   without the parent ever invoking `/code-review`; a session that never runs
   `plan-it` still reaches `code-review`.

The load-bearing constraint that keeps four sites from becoming the defect:
**each site is one line naming an observable and defers to CLAUDE.md for the
rule.** If any of the four grows into an explanation of single-source-of-truth,
the change has become what it catches. This is checkable — see Verification.

**Assumptions:**

- No *existing* pytest test pins `code-review/SKILL.md`'s checklist
  numbering, item count, or Item-ownership table; none pins `plan-it` Step 5
  or `code-writer`'s step-6 list content — which is why M6 adds one, rather
  than a reason no test is needed. [verified: subagent sweep of
  `claude/.claude/hooks/tests/` this session — `test_agent_roster.py` pins
  only `code-writer.md` frontmatter (`model`, `effort`, `Write` tool);
  `test_reconciliation_block_consistency.py` pins only the `## Reconciliation`
  block; `test_require_code_review.py` pins only the `HOOK_TEST_FIXTURE`
  marker-write block at `code-review/SKILL.md:387`]
- Length caps are not at risk. `check-skill-length.sh:10-15` sets 500 for
  `code-review` (currently 405) and `plan-review` (272); `plan-it` (92) falls
  to the 200 default. [verified: `check-skill-length.sh:10-15` and `wc -l`,
  this session]
- Step 1.5 tripwires carry no Item-ownership row, so M1 adds no routing-table
  edit and does not trigger the `Reshapes reviewer ownership` spawn rule.
  [verified: `code-review/SKILL.md:340-381` — the table's primary key is the
  numbered checklist item; no Step 1.5 tripwire appears in it]
- The `Adds or modifies a skill, agent, instruction-file rule, or hook`
  Change-type row will match this diff, requiring `skill-review` (3 SKILL.md
  files) and `agent-review` (`code-writer.md`) at `/code-review` time.
  `skill-review` is additionally hook-enforced on commit. [verified:
  `.claude/rules/review-pipeline-dispatch.md`, loaded this session]

## Critical files

All paths relative to the worktree
`.claude/worktrees/caveat-duplication-check/`.

**1. `claude/.claude/skills/code-review/SKILL.md`** — modify.
Append a fifth bullet to the Step 1.5 tripwire list (after the
`Non-durable comment` bullet, currently line 54). Draft text:

> - **Rationale restated across sites** — the diff writes the same
>   explanation, measurement, or justification at two or more sites (a site
>   is a file, or a distinct section within one), or restates one that
>   already has a canonical home elsewhere in the repo, rather than stating
>   it once and pointing at it. Fire only when a reader at each site could
>   follow a pointer to that home instead. Prose duplicated because each
>   site's reader loads independently and cannot reach the other is the
>   stand-alone exception CLAUDE.md §Engineering Judgment names — do not
>   flag it; a duplicate carrying its own consistency test is the clearest
>   case. Scope the check to the diff — sites within it, plus a canonical
>   home the diff itself cites. Do not run a repo-wide duplication search.

*Reuse:* matches the existing bullet shape exactly — bolded observable, em
dash, one-sentence condition. No new numbered item, no Item-ownership row.
"Site" is defined once here and used verbatim by the other three drafts;
they must not re-derive it.

**2. `claude/.claude/agents/code-writer.md`** — modify.
Append a seventh bullet to the self-review step 6 list (after the
comment bullet ending line 102). Draft text:

> - A rationale, measurement, or justification restated at two or more sites
>   in your own diff where a reader at each site could have followed a
>   pointer to one canonical home instead. Not a finding when each site's
>   reader loads independently and cannot reach the other.

*Reuse:* the six existing bullets are noun-phrase fragments naming an
observable; this matches without introducing a new section.

**M1 and M2 must land in the same commit.** `code-writer.md:92-94` states
step 6 checks "the same set the code-review skill's Judgment-activation pass
checks." Landing either edit without the other makes that sentence false.

**3. `claude/.claude/skills/plan-review/SKILL.md`** — modify.
Append a seventh foundation tripwire to the Step 4 list (after
`Unjustified given`, line 87). Draft text:

> - **Rationale restated across sites.** Two or more of the plan's mechanisms
>   write the same explanation, measurement, or justification to different
>   sites. Required: name the canonical home and reduce the other mechanisms
>   to pointers, or state per site why its reader cannot reach that home and
>   must carry the rationale standalone.

*Reuse:* matches the existing tripwire shape — bolded name, observable
condition, `Required:` clause.

**4. `claude/.claude/skills/plan-it/SKILL.md`** — modify.
Append one sentence to the end of the `**Per mechanism:**` bullet in the
assumption ledger (line 53). Draft text:

> The same check applies to breadth, not only power: if two or more
> mechanisms would write the same explanation, measurement, or justification
> to different sites, name the canonical home in one and reduce the rest to
> pointers before the ledger is final — or record, per site, why its reader
> cannot reach that home.

*Reuse:* extends the paragraph that already holds the over-powered-primitive
check rather than opening a new sub-section.

**5. `docs/design-decisions.md`** — modify.
Add `## 25. Single-source-of-truth detection added as tripwires, not a
checklist item (2026-08-15)` after §24 (ends line 279). §13 is not edited.
Five things this section must record, each of which a reviewer flagged as a
gap if omitted:

1. What §13 rejected, and why that reasoning does not reach a tripwire.
2. **Why SSOT survives that scrutiny despite sitting closer to the line than
   its sibling tripwires.** The three shipped Step 1.5 tripwires translate an
   *abstract* CLAUDE.md instruction into a differently-shaped diff-surface
   test; CLAUDE.md already phrases SSOT as an observable, so the translation
   gap is narrower here. State the reason it still holds — the tripwire's job
   is detecting the diff-surface pattern (same text, 2+ reachable sites),
   which is a different act from the rule's prose — rather than treating
   "tripwire ≠ restatement" as automatically dispositive.
3. **The evidence ratio, stated honestly: 1 of 6 instances is confirmed to
   match this specific shape** (PR #631, the only one with a shipped diff);
   the other 5 were conversational corrections whose shape — duplicated
   prose vs. duplicated logic vs. an unrelated over-powered-primitive
   pattern — is not verifiable from the repo. Do not cite "6 instances" as
   undifferentiated recurrence evidence for this check.
4. The reachability discriminator, and why §4's no-shared-partials policy is
   not in tension with it.
5. **A revisit trigger.** Because "same rationale" is a model judgment (G1),
   false positives have no automatic detector and every stow user pays each
   one on every future review. Record a concrete criterion: if two false
   positives are reported, narrow the tripwire or drop it.

*Reuse:* follows the existing dated-section format with a `### Sources`
subsection where sources are cited.

**6. `claude/.claude/hooks/tests/test_rationale_tripwire_presence.py`** — create.
A presence-only test asserting each of the four station files contains a
stable anchor substring for the tripwire. Nothing else pins these four
additions, and `code-review/SKILL.md` sits at 405 of its 500-line cap, so a
future length-trimming pass is a live deletion vector.

*Reuse:* mirror `test_reconciliation_block_consistency.py`'s second test
(`test_reconciliation_block_contains_collapsing_rule_and_discriminator`) —
presence-only, not byte-equality. Byte-equality is wrong here: the four texts
are deliberately worded per-file, so there is no cross-file string to pin.

**7. `.claude/plans/caveat-duplication-check.md`** — this file, committed to
the branch per `branch-management`.

## Verification

1. **Self-test — the change must not fire its own tripwire.** Run
   `/code-review` on the staged diff. The new tripwire must *not* fire on
   M1–M6: the rationale lives once in `docs/design-decisions.md` §25 and the
   four station edits name observables without restating it. Weak evidence by
   construction — it is self-graded, and the Approach section was written to
   satisfy it — so treat a pass as a sanity check, not validation. Check 8 is
   the primary test. A *failure* here is still decisive: reduce the design,
   do not argue past it.
2. **Line-budget check.** `git diff --stat` — each of the four station edits
   should be ≤8 added lines. A station edit that grew into a paragraph is the
   failure mode check 1 exists to catch, visible here earlier.
3. **Test suite:** `../../../.venv/bin/pytest claude/.claude/` from the
   worktree (the contributor `.venv` lives at the main worktree root only).
   Expect no failures, including M6's new presence test. Confirm M6 actually
   fails when it should: delete one station's tripwire line locally, re-run,
   see it go red, restore. A pinning test that passes against a missing
   anchor is worse than no test.
4. **Lint:** `../../../.venv/bin/ruff check claude/.claude/` — no Python
   changed, run as a clean-tree check.
5. **Length gate:** confirm `check-skill-length.sh` passes at commit —
   `code-review` ≤500, `plan-review` ≤500, `plan-it` ≤200.
6. **Per-file-type review dispatch** (`.claude/rules/review-pipeline-dispatch.md`):
   `skill-review` on the three SKILL.md files (hook-enforced at commit) and
   `agent-review` on `code-writer.md`.
7. **Behavioral spot-check on the known-positive case.** Re-read PR #631's
   first-round commit `2001121c` (the five restating sites derived in
   Approach) against the new `code-review` tripwire text and confirm it names
   that diff as a hit; then re-read the corrective commit `962779fb` and
   confirm the tripwire does *not* fire on it. A tripwire that cannot
   separate those two commits does not encode the defect.
8. **Held-out negative case — the sanctioned-duplication stress test.** Apply
   the drafted tripwire text to the `## Reconciliation` block, duplicated
   near-verbatim between `code-review/SKILL.md:269-284` and
   `plan-review/ROUTING.md:53-68` and pinned by
   `test_reconciliation_block_consistency.py`. It must **not** fire: the two
   readers load independently and neither can reach the other. This case is
   held out in the sense that matters — the wording was drafted against
   #631, not against this — so unlike checks 1 and 7 it can fail on wording
   the author did not tune for it. An earlier draft of the discriminator
   (rationale-vs-instruction) failed exactly here, which is why this check
   exists rather than being assumed.

## Out of scope

- **PR #631** — merged and closed. Not re-opened, edited, or reverted.
- **The 5 live-correction instances** — conversational corrections, never
  shipped code. Nothing to change retroactively.
- **A general-purpose duplication detector.** Scoped strictly to the
  restated-rationale shape. Logic duplication is checklist item 9's job;
  comment verbosity is item 12a's and `comment-discipline-reviewer`'s.
- **Editing `docs/design-decisions.md` §13.** A dated decision record, and
  preserved content under CLAUDE.md §Working Style Axis 3. §25 records the
  revisit alongside it.
- **A hook enforcing the check mechanically.** Declined on G1's grounds — a
  hook cannot decide the predicate it would gate.
- **Changing the no-shared-partials policy** (root `CLAUDE.md`;
  `docs/design-decisions.md` §4 — see Approach). Inside this plan's reach —
  this repo owns both artifacts — and deliberately declined: the policy is
  correct as written, and the check is designed to coexist with it via the
  reachability discriminator rather than by narrowing it. If that
  discriminator turns out not to hold, the correct response is to drop the
  check, not to weaken §4.
- **Editing `CLAUDE.md` §Engineering Judgment.** The principles are already
  complete and correctly worded; the gap this plan closes is activation, not
  statement.
