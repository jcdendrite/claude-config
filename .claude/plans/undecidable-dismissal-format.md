# GH-514 — stop mandating a literal heading for undecidable-skill dismissals

## Context

**Goal:** replace `skill-fidelity-reviewer`'s requirement that each
undecidable-skill dismissal appear under a literal `## Dismissed as
undecidable` heading with a semantic requirement (name the skill, name the
reason, keep it visibly grouped) that doesn't depend on the model
reproducing an exact string mid-reasoning — since three independent
attempts to make it do so have already failed identically.

GH-494 (PR #513, merged as `953e7fa`) taught the reviewer to dismiss skills
its diff-plus-plan evidence structurally cannot decide, rather than flagging
them as false "artifact absent" findings. That part works: across four real
dispatches during the same PR's own review, the reviewer never raised a
false `[SILENT-SKIP]` against an undecidable skill. But the same PR also
required each dismissal be recorded under one literal H2 string, and none of
the four dispatches produced it — the reviewer wrote correct, fully-reasoned
prose about the same dismissals under headings of its own choosing (`##
In-scope evaluation`, `## Answers`). Three structurally different prompt
fixes (a downstream reminder, moving the instruction inline with the
decision point, a worked example with explicit "a self-composed heading
doesn't count" language) all failed the same way. GH-514 asks for a design
reconsideration rather than a fourth wording pass.

**Intended outcome:** the agent's output contract stops asserting a
guarantee it cannot keep, without losing the underlying goal — that a
dismissal is recorded somewhere visible, with a reason, rather than
collapsing into silence.

## Approach

Drop the literal-heading mandate; keep everything else. Three coordinated
edits to `claude/.claude/agents/skill-fidelity-reviewer.md`, all inside the
existing "The comparison" step 2 and "Dismissed-as-undecidable output
structure" section — no other file needs a behavioral change.

### Why this option over the other two considered

The issue names three paths: a different mechanically-checkable output
shape, accepting prose-only records and updating `/ready-for-review` to read
for them differently, or accepting the gap outright.

**A unified per-skill ledger table** (every skill — finding, clean, or
dismissed — gets one row in the same table, so there's no special-cased
action for the undecidable subset) was the other real contender. Rejected
for now: it's a materially bigger rewrite of the output contract, and
without a fresh real dispatch to test it against, there's no way to confirm
it doesn't hit the same instruction-following wall in a new shape — CLAUDE.md's
"compounding defensive layers" guidance says stop hardening the *same*
mechanism, not necessarily invent an untested new one on the first pass. If
the chosen fix below turns out not to hold up under a future real dispatch,
this is the next thing to try.

**Accepting the gap with a doc-only fix** (issue's third option) was
rejected because it leaves the *false* claim in place — "this pointer line
is the only surface `/ready-for-review` reads" — which actively misleads a
future editor into thinking heading-fidelity is load-bearing when it isn't.
That claim needs fixing regardless of which path is chosen, so folding it
into a real requirement-relaxation costs nothing extra.

**Why dropping the requirement is sound, not just cheaper:** nothing in the
pipeline mechanically parses the heading. `/ready-for-review` step 4 does an
unconditional whole-file `Read` of the findings file every time (`Read the
findings file after it returns`, `ready-for-review/SKILL.md:107`) — not a
grep gated on the heading's presence, not a pointer-line-only parse. A
repo-wide search of `claude/.claude/scripts/` and `claude/.claude/hooks/`
for the dismissal heading text returns zero matches. The only thing that
currently references the literal string is `test_skills.py`, and those
assertions check that the *instruction text* exists in the agent's own
prompt — not that a real dispatch produced the heading; they would stay
green even if every real dispatch ignored the instruction, which is exactly
what happened. So the heading was never load-bearing for what the parent
actually reads; it was aspirational polish for grep-based scanning that the
model can't reliably deliver. The pointer line's `<M> dismissed as
undecidable` count is the part that already reliably closes the original
GH-494 "indistinguishable 0 issues" concern — it doesn't depend on the file
body at all, and none of the four real dispatches misreported it.

### Assumption ledger

**Root problem:** the agent's output contract requires an exact literal
string that the model has failed to produce across four independent real
dispatches and three distinct fix attempts, while asserting a false
justification for why that string matters.

**Mechanisms**

- *Relax step 2's dismissal-recording instruction from "exact heading" to
  "named, grouped, reason given"* — anchors: root. This is the instruction
  the model has already shown it can't follow as written; removing the
  string requirement removes the thing that fails.
- *Relax the Output-format section's mandate the same way, and correct the
  false "only surface" claim* — anchors: root. Two edits needed together:
  the mandate and its stated rationale both need to change, or the
  correction is undermined by the sentence right above it still asserting
  exclusivity.
- *Update `test_skills.py`'s pinned assertions to match the relaxed
  wording* — anchors: root. The existing assertions pin the now-removed
  exact-heading mandate; left as-is they'd fail against the new text, or
  worse, get satisfied by unrelated substrings.

Lighter primitives considered instead of a full output-contract rewrite
(the ledger-table alternative): rejected above, not because the primitive
itself is heavier — it's actually a simpler *shape* — but because it is an
unverified new mechanism being substituted for a verified-failing one,
which trades a known problem for an unknown one without a way to test the
trade before shipping it to every stow consumer on `git pull`.

| # | Assumption | Tag |
|---|---|---|
| 1 | `/ready-for-review` step 4 does an unconditional whole-file `Read` of the findings file, not a pointer-line-only parse. | `[verified: ready-for-review/SKILL.md:107]` |
| 2 | No script or hook mechanically parses `"Dismissed as undecidable"` or the pointer line's dismissal count. | `[verified: grep -rn "Dismissed as undecidable\|## Dismissed" claude/.claude/ --include="*.py" --include="*.sh"` returns only `test_skills.py:531,536`, both inside string-literal assertions against the agent's own prompt text]` |
| 3 | `test_skills.py`'s `TestSkillFidelityReviewerUndecidableDismissal` class asserts agent-body prompt text, not runtime/behavioral output. | `[verified: test_skills.py:487-488, self._body() calls _agent_body which reads the agent .md source]` |
| 4 | All four real dispatches during PR #513's review reached correct substantive conclusions on the undecidable skills and never emitted the mandated heading. | `[engineer-verified — PR #513 body, "Behavioral smoke test findings"]` |
| 5 | The pointer line's `<M> dismissed as undecidable` count was not itself reported as unreliable across those four dispatches — only the file-body heading was. | `[unverified — the four findings files were local to a since-removed worktree (branch `GH-494/fidelity-reviewer-undecidable-artifacts`) and are not recoverable; PR #513's own narrative doesn't separately flag the pointer-line count as wrong]` |
| 6 | No other agent file in `claude/.claude/agents/*.md` uses this same "exact literal heading, self-composed heading doesn't count" pattern. | `[verified: grep -rln "does not satisfy this requirement\|heading text exactly as written\|not a heading you compose\|verbatim" claude/.claude/agents/*.md → only skill-fidelity-reviewer.md]` |
| 7 | `/code-review`'s parallel specialist-findings consumption (`code-review/SKILL.md:262`) is likewise a `Read` of the actual file (conditional on finding count), never a heading-gated parse — so this file's contract isn't diverging from its sibling pipeline's pattern by relaxing the heading requirement. | `[verified: code-review/SKILL.md:262]` |

Row 5 is the one residual unverified assumption this plan rests on, and it's
asymmetric: if it's wrong (the pointer line's count was *also* unreliable on
some dispatch), that's a materially different and more serious problem than
this ticket — the mechanical signal, not just the human-readable one, would
be failing — and would need its own investigation rather than a heading
wording fix. Flagging it rather than silently assuming it away.

## Critical files

### 1. `claude/.claude/agents/skill-fidelity-reviewer.md`

**"The comparison" step 2** (`:53`) — replace the whole paragraph verbatim
with this text (one unwrapped line, matching this file's existing prose-
paragraph convention — every other paragraph in step 1/2 of this section is
a single long line, only the bulleted output-format lists below are hand-
wrapped):

> **The moment you reach an undecidable determination for a skill, record it
> before moving to the next skill** — name the skill and the one-line
> reason, grouped with any other dismissals under a heading that identifies
> them as declined coverage, when your prompt gives `findings_path` (a
> suggested shape appears in Output format; you are not required to
> reproduce it verbatim), otherwise in the inline count. This is a separate
> obligation from explaining *why* the skill is undecidable, not a
> restatement of it: a case that took real reasoning to resolve — an
> artifact that genuinely exists but sits structurally outside your
> evidence, like `pr-description`'s PR body — needs the name-and-reason
> record exactly as much as an easy no-artifact case does. Writing the
> prose explanation elsewhere in your reasoning does not substitute for it
> being identifiable as a dismissal. Do not flag it, and do not go looking
> for it on disk: `resume-context` moves a continuity file aside once
> consumed, so absence there is not evidence either way.

**"Dismissed-as-undecidable output structure"** (`:118-147`) — reuse
opportunity: keep the section header and the shared-protocol framing
sentence; replace the two bullets, the worked-example lead-in, and the
closing paragraph. Replace the entire section body (everything after the
framing sentence, through the end of the section) with:

````
- File-based output: group dismissals together, between the per-finding H2s and `## Recommendations`, under a heading that identifies them as declined coverage — `## Dismissed as undecidable` is a reasonable choice, but the exact wording is not required: nothing downstream parses it mechanically. What matters is that each dismissal names the skill and the reason, and that the group is identifiable as dismissals rather than scattered through unrelated prose. Not a finding — still required, since this is the parent's visible record that coverage was declined rather than clean.
- Pointer line: `Wrote findings to <path>. Found <N> issues, <M> dismissed as undecidable. <One-sentence summary>.` A dismissal is never counted in `<N>`. `/ready-for-review` reads the whole findings file after every dispatch, not just this line — but `<M>` is what a reader scanning only the pointer line sees, so keep it accurate regardless of file-body formatting.

Example:

```
## Dismissed as undecidable
- `pr-description` — its PR-body artifact is applied via `gh pr edit`, never
  enters a branch diff; not evaluable from diff-plus-plan evidence.
```

Any clearly-labeled grouping that names the skill and the reason satisfies this — prose explaining the same conclusion under a self-chosen heading is acceptable as long as the dismissal and its reason are identifiable there.
````

Every bullet and the closing paragraph above is written as one unwrapped
line deliberately — not a style choice, a correctness one. The *original*
file's hand-wrapped bullets are what caused the false "only surface" claim
(row for `test_pointer_line_claim_is_corrected` below) to silently wrap
across `:130-131`, and the retracted mandate sentence to wrap across
`:144-145` — both confirmed by direct check against the current file (see
Critical file 2). A test anchor that straddles a line break silently stops
matching. Keeping prose unwrapped removes the whole fragility class rather
than working around it bullet-by-bullet; do not re-wrap these lines for
cosmetic column width when implementing.

Each bulleted line above must stay on one source line where it carries a
test-pinned fragment (see Critical file 2) — matching the file's existing
convention (e.g. the current `"issues, <M> dismissed as undecidable"`
fragment already lives on one line at `:129`).

### 2. `claude/.claude/skills/tests/test_skills.py` — `TestSkillFidelityReviewerUndecidableDismissal` (`:466-548`)

Reuse opportunity: keep `_DISK_HUNT_PROHIBITION` and `_body()`. Four of the
existing six tests are untouched, one gets a docstring-only fix, and four
new/rewritten tests replace the one that pinned the removed mandate —
sized to cover both edit sites independently (a staff-sdet review of this
plan flagged that the original draft's test changes covered only the
Output-format edit site, leaving step 2 with zero coverage — a revert of
step 2 alone, while Output-format stayed relaxed, would silently reintroduce
step 2 pointing readers at "(exact structure below)" for a section that no
longer requires exact structure, which is exactly the kind of contradiction
this ticket exists to fix, and no test would catch it).

**Unchanged** (none assert the removed exact-heading text):
`test_declares_decidability_test`,
`test_prohibits_disk_hunt_for_dismissed_artifacts`,
`test_undecidable_examples_exclude_a_known_decidable_skill`,
`test_inline_output_lists_dismissals_before_verdict`,
`test_inline_output_reports_dismissed_separately`.

**Docstring-only fix**: `test_pointer_line_reports_dismissed_count`
(`:518-524`). Its assertion (`"issues, <M> dismissed as undecidable" in
self._body()`) is unchanged and still correct. Its docstring currently
repeats the false claim being retracted ("the only surface
`/ready-for-review` reads when `findings_path` is set") — reword to: `"""The
pointer line must report dismissals, not just carry the step-2 instruction
to record them. A single \`in body\` check on the bare phrase is satisfied
by the step-2 occurrence alone, so this pins the fuller pointer-line
fragment specifically."""` Per CLAUDE.md single-source-of-truth: this repo
edit is the one place the false claim is fully retracted, so no test
docstring should still assert it as true.

**Replace** `test_file_based_output_gains_dismissed_section` (`:526-536`)
with two tests, each independently mutation-verified (test-first against
the unmodified body, then green after the edit):

- `test_file_based_output_relaxes_heading_to_suggestion`: assert
  `"between the per-finding H2s and"` in `self._body()` (placement guidance
  survives) AND assert `"the exact wording is not required: nothing
  downstream parses it mechanically"` in `self._body()` (the relaxation
  language, unique to this section — distinct from step 2's own relaxation
  phrasing below, so this test cannot pass on a step-2-only edit).
- `test_dismissed_section_drops_literal_heading_mandate`: assert
  `"does not satisfy this"` **not in** `self._body()`. Confirmed by direct
  check against the current file
  (`python3 -c "print('does not satisfy this requirement' in open('claude/.claude/agents/skill-fidelity-reviewer.md').read())"`
  → `False` — the retracted phrase wraps across a line break in the source,
  so the naive full-phrase substring is already absent pre-edit and would
  make that assertion vacuous; `"does not satisfy this"` is confirmed
  present pre-edit as a single unbroken line-144 substring, so it is the
  fragment that actually discriminates the edit) AND assert `"prose
  explaining the same conclusion under a self-chosen heading"` in
  `self._body()` (the replacement acceptance language).

**New**, covering step 2 independently (closes the staff-sdet gap):

- `test_step_two_records_dismissal_without_exact_heading_mandate`: assert
  `"name the skill and the one-line reason, grouped with any other
  dismissals"` in `self._body()` (step 2's own relaxed instruction, a
  phrase that does not appear in the Output-format section, so this test
  fails independently of whether that section was also edited) AND assert
  `"(exact structure below)"` **not in** `self._body()` (the retracted
  step-2 phrase — confirmed single-occurrence in the pre-edit file per the
  grep above).

**New**, covering the false-claim correction:

- `test_pointer_line_claim_is_corrected`: assert `"the only surface
  \`/ready-for-review\` reads"` **not in** `self._body()`. Confirmed by
  direct check against the current file
  (`python3 -c "print('This pointer line is the only surface' in open('claude/.claude/agents/skill-fidelity-reviewer.md').read())"`
  → `False`, same line-wrap trap as above; `"the only surface
  \`/ready-for-review\` reads"` is confirmed present pre-edit on line 131
  unbroken, so it is the fragment that actually discriminates the edit) AND
  assert `"reads the whole findings file after every dispatch"` in
  `self._body()` (the corrected claim — must land on one source line in the
  rewritten text).

Net: 6 existing tests → 4 unchanged + 1 docstring-fixed + 4 new/replacement
(one two-for-one split, one net-new) = 9 tests in the class.

## Verification

Worktree is three levels deep, so the venv is at `../../../.venv`.

1. **Test-first** (`test-conventions` §2): write the updated/new assertions
   and run them red against the unmodified agent body, then edit the agent
   body, then green.
2. `../../../.venv/bin/pytest claude/.claude/` — full suite green.
3. `/agent-review`, per `.claude/rules/skill-and-agent-self-review.md` —
   confirm the edit doesn't silently drop the disk-hunt prohibition or the
   decidability test while relaxing the heading mandate.
4. `/code-review`, then `/ready-for-review`.

**No new behavioral smoke test required to merge.** The prior smoke-testing
effort (four real dispatches, three fix attempts) already established the
failure mode this plan responds to; there's no new model-following behavior
being asked for here to re-verify — the change removes an instruction
rather than adding a sharper one. This is a deliberate scope narrowing, not
an oversight: adding a fourth dispatch to re-prove the same already-settled
negative would spend real tokens on a question this plan doesn't change the
answer to.

**But if step 4 of this branch's own `/ready-for-review` run surfaces
`pr-description` (or another undecidable skill) in the invocation list —
which it will, on this branch, once `pr-description` has run at least
once — `Read` the resulting findings file before merging**, not merely as
an opportunistic aside. This is the one residual unverified item on the
assumption ledger (row 5: whether the pointer line's `<M>` count stays
accurate under the relaxed wording, not just the file-body prose). It costs
nothing beyond the `Read` step 4 already performs unconditionally, and it
is the only real-world check available for this specific residual risk.

## Out of scope

- **The unified per-skill ledger table.** Considered and set aside above;
  flag as a follow-up if the relaxed wording is later found insufficient.
- **Any change to `ready-for-review/SKILL.md` or `code-review/SKILL.md`.**
  Both already do an unconditional/conditional full-file `Read`; neither
  depends on the heading text, so neither needs to change.
- **`handoff/SKILL.md`, `brief/SKILL.md`, or any other skill file.** This
  ticket is scoped to the reviewer agent's own output contract.
