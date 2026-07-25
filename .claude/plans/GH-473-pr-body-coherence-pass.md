# GH-473 — Reader-coherence pass for `/sync-pr-description`

## Context

Give `/sync-pr-description` a holistic read of the PR body, so defects that
match none of its five patterns stop surviving a sync.

`claude/.claude/skills/sync-pr-description/SKILL.md` flags five specific
shapes: per-commit narratives, reviewer-action items Claude can answer
itself, content-claim staleness, `TBD`/`pending` markers, and files in the
diff missing from the body. All five are pattern matches. A body defect that
resembles none of them passes the sync untouched, and no other station in
the pipeline reads the rendered body — `/code-review` reviews the diff.

The observed escape: a PR body retained a section whose heading announced a
step the author had skipped, while the section's own body stated the step had
in fact been completed. The heading asserted the opposite of its content, and
the template it came from directed deleting such a section outright once the
condition no longer held — so emptying it and leaving the heading was itself
the defect. `/sync-pr-description` ran on that PR later in the same session
and correctly left it alone — it matches no listed pattern. A human reading
the rendered description caught it.

**Evidence base, stated plainly.** A scoped `error-mode-analysis` pass over
40 recent merged PRs in this repo (with full `pulls/*/comments`,
`pulls/*/reviews`, and `issues/*/comments` history) plus 15 cross-repo
sessions where the skill demonstrably fired found **zero** further instances
of this defect class. Detection-layer bucket: **human-unique**. Trend
classification: **one-off**, present in one sub-window only. Per that
skill's Step 7, a one-off is reported but must not drive machinery. This
plan is deliberately sized to a narrow, targeted change for that reason.

"One-off" here describes observed frequency, not reproducibility. The
issue argues the pattern is mechanically reproducible — any PR whose body
is generated once under one set of facts and then outlives them will
recreate it — and nothing in this sweep contradicts that. The label
justifies not building enforcement machinery today; it is not grounds to
decline coverage if a second instance surfaces.

## Approach

Add a named **reader-coherence pass** that runs before the existing pattern
checks, mirroring the shape three sibling skills in this repo already use.

### Why a pre-checklist pass rather than a sixth bullet

The issue proposes a sixth entry in the "Flag and fix" list. A sixth bullet
is a sixth pattern, and this defect class is *defined by matching no
pattern* — it would catch the observed heading-negates-body shape and miss
the next one. Structural-sibling audit of the repo's other authored-artifact
verifiers found the general form already established:

| Skill | Holistic pass | Location |
|---|---|---|
| `code-review` | Step 1 — Implementation-fitness gate | `code-review/SKILL.md:31-45` |
| `plan-review` | Step 4 — Design-fitness gate | `plan-review/SKILL.md:61-80` |
| `respond-pr` | Step 4 — Holistic triage | `respond-pr/SKILL.md:25-31` |
| `sync-pr-description` | *(none)* | — |

Three of four same-genre skills place the holistic read *before* the
itemized checks, each justified by an explicit one-line statement of what
the checklist structurally cannot surface (`code-review/SKILL.md:33`: *"the
checklist won't surface 'the whole implementation is the wrong shape'"*;
`respond-pr/SKILL.md:31`: *"If none apply after a careful read, say so — but
the read is required"*). `sync-pr-description` is the outlier. The fix is to
apply the established primitive, in the established voice, not to invent
one.

`handoff` is the fourth checklist-only skill (`handoff/SKILL.md:107-120`)
but is a weaker parallel — it verifies a self-authored continuity file, not
a document read cold by an external reviewer — and the data pass found no
instance of this defect there. Out of scope; see below.

### Resolving the conflict with the existing "don't restructure" line

`sync-pr-description/SKILL.md:42-44` currently reads *"Keep the project's
template structure intact — refresh content inside existing sections, don't
restructure."* The correct remedy for the observed case is to **delete** a
section — exactly what that line forbids. Without a carve-out the new pass
would identify the defect and then be blocked from fixing it. The carve-out
is one sentence: a section the template's own instructions say to delete
once a condition holds is meant to be removed, not emptied or annotated.

### Assumption ledger

```
Root: /sync-pr-description's five checks are all pattern matches, so a PR-body
defect matching no pattern survives the sync, and no other pipeline station
reads the rendered body.

Row 1 [mechanism]: pre-checklist reader-coherence pass in
sync-pr-description/SKILL.md — anchors: root — a holistic read is the only
control that catches a defect class defined by matching no pattern.
Row 2 [mechanism]: one-sentence carve-out to the "don't restructure" line —
anchors: row1 — without it row1 can name the defect but not remedy it.
Row 3 [assumption]: three sibling artifact-verifier skills already place a
holistic pass before their itemized checks
[verified: code-review/SKILL.md:31-45, plan-review/SKILL.md:61-80,
respond-pr/SKILL.md:25-31] — anchors: row1
Row 4 [assumption]: no test in claude/.claude/skills/tests/test_skills.py
asserts on sync-pr-description/SKILL.md's body content; the two wiring tests
(:443-449) pin caller-side strings in ready-for-review/handoff only
[verified: test_skills.py] — anchors: row1
Row 5 [assumption]: body is 55 lines against check-skill-length.sh's 200-line
default (no 500-line override for this skill)
[verified: claude/.claude/hooks/check-skill-length.sh:52-59] — anchors: row1
Row 6 [assumption]: docs/skills.md:11 enumerates this skill's checks by name
and goes stale on a new check; README.md:141 and docs/skills.md:42 are
generic and do not [verified: grep of docs surfaces] — anchors: row1
Row 7 [assumption]: this defect class is a one-off by observed frequency,
not by reproducibility — zero further instances across 40 public-repo PRs
with full comment history and 15 cross-repo sessions where the skill fired
[verified: error-mode-analysis data pass, this session] — anchors: root
Row 8 [assumption]: no enforcing test ships with this change; the repo's
add-a-test-with-a-new-convention precedent covers cross-file wiring, and an
assert on prose inside one file has no cross-file coupling to break
[engineer-verified] — anchors: row1
Row 9 [assumption]: fix shape is the pre-checklist holistic pass rather than
the sixth bullet the ticket's acceptance criteria prescribe
[engineer-verified] — anchors: row1
Row 10 [assumption]: sync-pr-description/SKILL.md:7 and docs/skills.md:11 both
name "/ready-for-review step 4" but the sync dispatch is step 5; step 4 is
"Skill-procedural-fidelity review" [verified: grep of `^## [0-9]` headings in
ready-for-review/SKILL.md — step 5 is "Sync PR description"] — anchors: none
(incidental; both lines are rewritten by edit 0 and the `docs/skills.md`
edit regardless)
```

### Lighter primitives considered and rejected

Row 1 adds a new section to a skill body, so it owes the over-powered-primitive
check three lighter alternatives:

1. **Sixth bullet in "Flag and fix"** (the ticket's prescription) — rejected:
   another lexical pattern against a class defined by matching no pattern, and
   it grows the very list the issue diagnoses as structurally insufficient.
2. **Extend the existing "Content-claim verification" bullet** to cover the
   body's internal consistency — rejected: that bullet's reference point is
   file content at HEAD; folding a body-versus-itself check into it conflates
   two reference points and buries the gate inside a bullet, losing the
   before-the-checklist ordering the sibling precedent depends on.
3. **Do nothing; rely on `/ready-for-review`** — rejected: step 5 dispatches
   to this same skill (`ready-for-review/SKILL.md:118-124`), and step 3's
   `/code-review` reviews the diff, not the body. No station reads the body.

## Critical files

**`claude/.claude/skills/sync-pr-description/SKILL.md`** — four edits, ~27
lines added net.

0. Frontmatter `description` (`:3-8`) enumerates the checks by name — *"flag
   per-commit narratives, stale content claims, TBD markers, and files
   missing from the body"* — and goes stale for the same reason
   `docs/skills.md:11` does. Lead the clause with the new pass. No cap
   applies: the skill is `name-only` in `skillOverrides`
   (`claude/.claude/settings.json:45`), so it is excluded from
   `_model_invokable_skills()` and therefore from both the 1536-char
   description cap and the 8000-char listing budget
   (`test_skills.py:164, 171-172, 634-659`).

   Verbatim replacement for `:3-8`:

   ```yaml
   description: >
     Verify and sync the current branch's open PR description against branch
     state — read the body end to end for self-consistency, then flag
     per-commit narratives, stale content claims, TBD markers, and files
     missing from the body; preserve coordination steps; apply the fix with
     gh pr edit. Dispatched from /ready-for-review step 5 and the /handoff
     pre-write checklist; also run standalone.
   ```

   The `step 4` → `step 5` correction inside that block is an incidental
   edit, not part of the coherence pass — see **Incidental edits** below.

1. Insert the reader-coherence pass between the existing lead sentence
   (*"The PR description is for the reviewer, not for posterity."*, `:16-17`)
   and the branch-state comparison (`:19-20`). Ordering becomes:
   reviewer-framing → holistic read of the body alone → branch-state compare
   → pattern checks. No renumbering — the body has no numbered steps. The
   existing sentence pair at `:16-17` splits; `Compare the body against
   branch state:` becomes `Then compare the body against branch state:`.

   Verbatim text to insert:

   ```markdown
   **Reader-coherence pass.** Before the pattern checks below, read the
   body end to end as the reviewer will and answer: **does this document
   make sense on its own?** Judge the body against itself — comparison
   against branch state and file content comes after. Those checks are
   pattern matches; none of them surfaces "this section contradicts
   itself" or "this text should not be here at all."

   Markers, illustrative rather than exhaustive:

   - A heading whose own body negates it — a "why I skipped X" section
     whose text says X was not skipped.
   - Two sections that contradict each other — a "no breaking changes"
     claim up top against a breaking change in the deploy notes.
   - Leftover template instruction text: placeholder prompts the
     template's own directions said to remove once a condition holds.
   - Any span a reader arriving cold would stop on and ask "what is this?"

   If nothing fires after a careful read, say so — naming the sections
   you read end to end. A bare negative cannot distinguish a coherent
   body from a skipped pass.
   ```

   The markers are illustrations, not the definition — writing them as a
   closed list would reproduce the pattern-matching failure at smaller
   scale, so the label says so on the page rather than leaving a future
   editor to infer it. The cross-section marker is named explicitly
   because it is the most common real PR-body incoherence and none of the
   other three reaches it. The "judge the body against itself" sentence
   fixes this pass's reference point, distinguishing it from the
   Content-claim-verification bullet, whose reference point is file
   content at HEAD. The text names no platform-specific template file,
   per the repo rule that stowed skill bodies stay platform-agnostic.
   The closing line makes the negative result carry an artifact — the
   sections actually read — because a bare "nothing fired" is
   indistinguishable from a pass that never ran, and no test asserts on
   this prose (see Row 8).

2. Append the deletion carve-out to `:42-44`, which becomes:

   ```markdown
   Propose an updated body and apply with `gh pr edit <n> --body`. Keep
   the project's template structure intact — refresh content inside
   existing sections, don't restructure. Exception: a section the
   template's own instructions say to delete once a condition holds is
   meant to be removed, not emptied or annotated — give any action item
   inside it a disposition first (see "Coordination-step preservation"
   below).
   ```

   The trailing cross-reference matters: a template-deletable section can
   carry a live coordination step the deletion would otherwise take with
   it. Without it the exception reads as unqualified delete-on-sight and
   can silently drop that item — the exact failure the file's own
   Coordination-step preservation paragraph exists to prevent, and one the
   "not emptied or annotated" clause forecloses leaving a stub for. That
   paragraph's "survive" disposition therefore has to name the section a
   surviving item moves to; a survivor whose container is deleted and
   whose destination is unnamed is the same silent loss by another route.
   The pointer uses the same idiom the two adjacent bullets already use
   rather than restating the rule.

3. Give the "survive" disposition a destination, in the
   **Coordination-step preservation** paragraph. `For each, give it an
   explicit disposition: survive into the new body, answer-and-strip`
   becomes:

   ```markdown
   For each, give it an explicit disposition: survive into the new body
   — and when its own section is being deleted under the exception
   above, name the remaining section it moves to, since a survivor with
   no home is the silent loss in disguise — answer-and-strip
   ```

   The clause lands here, not in the exception, because this paragraph is
   where the three dispositions are defined; putting the destination rule
   beside the disposition it qualifies keeps one authoritative home for
   the enumeration rather than splitting it across two sections.

**`docs/skills.md:11`** — the bullet enumerates checks by name
(*"content-claim verification, coordination-step preservation, backtick
hygiene"*). Add the reader-coherence pass at the front, since it runs first.
Correct `step 4` → `step 5` in the same line — see **Incidental edits**.

## Incidental edits

`sync-pr-description/SKILL.md:7`, `docs/skills.md:11`, and the docstring of
`test_skills.py::test_ready_for_review_invokes_sync_pr_description` all say the
skill is *"Dispatched from `/ready-for-review` step 4."* `ready-for-review/SKILL.md`
numbers the sync dispatch **step 5**; step 4 is *"Skill-procedural-fidelity
review,"* added in commit `c623ea5` (#469), which shifted sync down by one. No
test pins the step number — the assertion at `test_skills.py:445` matches only
the invocation-verb substring — so nothing catches the drift. The first two
lines are already being rewritten by edit 0 and the `docs/skills.md` edit; the
docstring is the third
arm of the same drift, corrected per CLAUDE.md's audit-structural-siblings rule
rather than left for a future contributor to trip over. Carry this section into
the PR description.

`.claude/plans/engagement-lessons-fixes.md:156` carries the same stale number
but is a historical plan file narrating a past PR — preserved record, left
untouched.

**Reuse:** no new helpers, commands, or tooling. The pass reads the body
already fetched by the existing precondition (`gh pr view --json
number,body,title`, `:13`); nothing new is invoked.

**Not touched:** `README.md:141`, `docs/skills.md:10/30/42` (generic enough
to survive), `claude/.claude/settings.json` (`skillOverrides` entry
unchanged), any hook, any test assertion. The one test-file edit is a
docstring correction in `test_skills.py` (see **Incidental edits**) — no
assertion changes, so ledger Row 4 is unaffected.

## Verification

- `../../../.venv/bin/pytest claude/.claude/skills/ claude/.claude/hooks/`
  from the worktree — expect no change in outcomes. Specifically
  `TestNameOnlySkillContracts` (`test_skills.py:318-372`) and the two wiring
  assertions (`:443-449`) still pass; the latter two pin caller-side strings
  in `ready-for-review`/`handoff`, which this change does not touch.
- `check-skill-length.sh` at commit time: body goes 55 → ~73 lines against a
  200-line default. Well clear.
- `/skill-review` (hook-enforced for any `SKILL.md` change) —
  behavioral-equivalence audit of the diff. This is the substantive gate:
  the added prose must not contradict the skill's own economy, and the
  carve-out must not read as a licence to restructure freely.
- `/code-review` per the repo pipeline, then `/ready-for-review`, which
  dispatches this very skill against this PR's own body — a live exercise of
  the new pass on a real body.
- **Synthetic-marker exercise.** This repo has no
  `.github/pull_request_template.md`, so the leftover-template-instruction
  marker cannot be reproduced against a live PR here — that subtype exists
  only in repos shipping a template with deletable instruction blocks. Close
  the gap without a committed test: hand-write a throwaway PR-body fixture in
  the scratchpad carrying all three markers (a heading its own body negates, a
  leftover `_Delete this section if..._` instruction block, and one span a
  cold reader would stop on), and run the pass against it manually. Exercising
  all three markers, rather than the two this repo's own PRs can produce, is
  the acceptance condition. Not a new test file — the "no enforcing test"
  decision stands.

  Two limits of this exercise, accepted rather than overlooked: the fixture is
  written by the same agent that wrote the pass, so it demonstrates the pass
  fires on markers designed to trigger it, not that it generalizes; and the
  fixture is discarded, so nothing catches a future edit that quietly narrows
  or drops the pass. Both are consequences of the no-enforcing-test decision,
  not gaps a committed assert on prose would close.

## Out of scope

- **No enforcing test.** Existing coverage (file-exists and no-disable-flag
  contracts, the commit-time length hook, unaffected caller-side wiring
  assertions) already applies. A new assert on a prose section inside one
  file would be a pure change detector with no cross-file coupling to guard.
- **`handoff`'s checklist-only shape.** The second sibling lacking a
  holistic pass. Different genre (self-authored continuity file, not a
  document read cold by an external reviewer), and the data pass found no
  instance of this defect there. If it warrants one, it warrants its own
  issue.
- **Adding a PR template to this repo.** Would make the template subtype
  testable here, but it is a separate decision with its own reviewer-workflow
  consequences.
- **Any hook, gate, or marker plumbing.** A one-off human-unique finding does
  not justify enforcement machinery; adding it would be the compounding-layers
  tell this repo's own CLAUDE.md warns against.
