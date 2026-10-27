# A `comment-discipline-reviewer` angle catches dense, hard-to-follow paragraphs

*2026-10-15.*

**Superseded in part by [Prose that restates the code is deleted, not corrected](prose-restating-code-is-deleted.md) (2026-10-26):** instruction files are no longer exempt from Prose at the wrong altitude, so the paragraph below on the Change-type table's widening no longer describes a one-angle exemption. The angle-count trigger in "Revisiting the angle count" fired on that date, and the reviewer moved to `effort: high`. Every other section stands.

Every existing `comment-discipline-reviewer` angle judges a sentence or a
site, never how a paragraph's sentences are arranged. `Paragraph readability`,
a new angle in `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles", closes that gap. The angle body states its own
predicate, fix, and carve-outs directly. This entry doesn't restate them.

The angle's charter widens to three `CLAUDE.md` §Prose and Output Format
bullets — "Lead with the answer", "Shape follows content", and "One idea
per sentence" — applied to the paragraph as a whole rather than to a single
sentence. Citing those three, and not the full section, keeps the charter
matched to what the angle actually judges. "Active voice, plain verbs, no
noun stacks" stays `tighten-prose`'s rewrite territory, not this reviewer's.

Multi-fact comment structure's tightly-coupled-pair carve-out (see that
angle's "Not a violation" list) is bounded rather than narrowed, to the
pair alone. The bound keeps the carve-out inside `CLAUDE.md`'s "Split
multi-fact comments" bullet, which names "a cause and its effect, or a
claim and its hedge" as the sharable pair and nothing wider.

Paragraph readability's first shape (see that angle's body) starts its
parallel-rule threshold at three. The tightly coupled pair Multi-fact
comment structure exempts is two, so the paragraph shape has to start one
past it. A threshold of two would make the two angles overlap on an
ordinary two-sentence case.

The Change-type table's comment/durable-doc-prose row widens to
instruction files (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/*.md`). The
agent's Scope section (added by this change) exempts instruction files
from one angle only, Prose at the wrong altitude; see that section for
the exemption and its citation. Running both reviewers on the same file
therefore double-dispatches only that one angle.

## Re-running the fixture's hash check after a rewording

`claude/.claude/hooks/tests/fixtures/paragraph-readability.json` pins the
angle's exact wording via a SHA-256 hash of its text block
(`claude/.claude/hooks/tests/test_design_decision_files.py`'s assertion 9).
Rewording the angle changes that hash on purpose, so the check fails until
someone updates it. That failure is the trigger for re-running the
measurement, not a license to update the hash without re-running it. After
any edit to the angle's wording:

1. Recompute `angle_text_sha256` from the edited block (the test's failure
   message prints the recomputed value).
2. Re-run the three fixture cells at K=5 — old body on `must_flag`, new
   body on `must_flag`, new body on `must_pass` — and confirm the
   fixture-cell bars still hold: old body on `must_flag` ≤ 1/5, new body
   on `must_flag` ≥ 4/5, new body on `must_pass` ≤ 1/5.
3. Write the measured rates, date, and model into the fixture's `note`
   field.

An editor can still update the hash without re-running the measurement.
That gap is the same honor system `.claude/rules/skill-and-agent-self-review.md`
already relies on for every fixture, now with a trigger attached to it.

## Committing the fixture despite the scratchpad rule

`paragraph-readability.json`'s `must_flag` and `must_pass` are a neutral
synthetic topic whose only defect is paragraph arrangement.
`.claude/rules/skill-and-agent-self-review.md` § "When editing a skill or agent, run the skill on its own diff" states the scratchpad rule's reason: probing a leak or an evasion. That reason does not apply here,
so this decision commits the fixture as an exception. The plan's
assumption ledger in `.claude/plans/prose-density-reviewer-gaps.md`
designed it to be exactly that regression guard.

## Revisiting the angle count

`comment-discipline-reviewer`'s How-to-work step 2 asks the reviewer to
check each fix against every other angle before reporting it, so each new
angle raises that per-finding cost. Revisit `effort: medium` or splitting
the reviewer into more than one agent the next time a new angle needs a
carve-out cross-reference to an existing angle. Paragraph readability
needed exactly that cross-reference, to Multi-fact comment structure.

This operationalizes rather than supersedes
[prose-rules-promoted-to-global-claude-md.md](prose-rules-promoted-to-global-claude-md.md),
which recorded that `comment-discipline-reviewer` enforced no §Prose rule
at the time §Prose and Output Format was promoted into `CLAUDE.md`. This
decision is what first gives the reviewer a §Prose rule to enforce.

## Measured outcome: shipped with caveats

The angle shipped with three measured caveats still open rather than
resolved before merge. `claude/.claude/hooks/tests/fixtures/paragraph-readability.json`'s
`note` field holds the measured rates, the date, and the model. This
section states what those rates left unmet, comparing the pre-angle
reviewer (`comment-discipline-reviewer` without Paragraph readability)
against the reviewer with this angle added.

Three items were not achieved:

- The pre-angle reviewer's fixture cell missed its bar. The fixture's
  `note` field holds the measured rate.
- One complaint site caught in at least 2 of 3 runs is a known
  residual. A dense paragraph of mixed rules that already leads with
  its claim draws sentence splits, not a lead plus a list. Its rules
  are mixed in kind. They also sit interleaved with a definition and
  an examples line, which falls outside the first shape's
  three-or-more parallel, consecutive-rule case. Reaching it would
  need a density rule. `.claude/plans/prose-density-reviewer-gaps.md`'s
  Alternatives list already declines a density mechanism, for the same
  reason.
- One complaint site caught in at least 2 of 3 runs gives no
  evidence for the new angle. Both the pre-angle reviewer and the
  reviewer with this angle added caught it in every run, so the site
  doesn't separate them.

## Sources

- `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" — the angle's predicate, fix, and carve-outs.
- `claude-skills/skills/code-review/SKILL.md`'s item 12a and Change-type
  table — the index bullet and the widened row.
- `claude/.claude/hooks/tests/test_design_decision_files.py` — assertion 9,
  the fixture's structural checks.
- `claude/.claude/hooks/tests/fixtures/paragraph-readability.json` — the
  committed regression fixture.
- [prose-rules-promoted-to-global-claude-md.md](prose-rules-promoted-to-global-claude-md.md) —
  the §Prose and Output Format promotion this decision extends; not
  superseded.
- [comment-discipline-reviewer-deferred-to-cumulative-pass.md](comment-discipline-reviewer-deferred-to-cumulative-pass.md) —
  the deferral this plan's row widening leaves unchanged.
- `.claude/rules/skill-and-agent-self-review.md` — the scratchpad rule this
  decision's fixture is an exception to.
- `.claude/plans/prose-density-reviewer-gaps.md` — full assumption ledger,
  mechanism-by-mechanism reasoning, and verification steps.
