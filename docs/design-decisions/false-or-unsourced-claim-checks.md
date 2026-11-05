# Claims in comments and durable docs are checked for truth by one angle and one tripwire

*2026-11-05.*

`claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" carries the `False or unsourced claim` angle, and `claude-skills/skills/code-review/SKILL.md` § "Step 1.5 — Judgment-activation pass" carries the `Stale claim in unchanged prose` tripwire. Each states its own predicate and carve-outs. This record keeps only why the design has this shape and how its wording was measured. The plan that scoped the change is `.claude/plans/prose-claim-truth-check.md`.

## Why changed claims and stale claims take different routes

The reviewer judges only added or modified lines, so it cannot see an unchanged passage that a code change makes false. The tripwire covers that arm inline in every round: the orchestrator already holds the changed code, and the tripwire's search key is a literal name the diff itself changes, so a diff that changes no such name searches nothing.

The angle for changed claims shares the reviewer's existing deferral to `ready-for-review`'s cumulative pass, recorded in `comment-discipline-reviewer-deferred-to-cumulative-pass.md`. The tripwire cannot share that deferral. Step 1.5 has no per-context switch, and a finding first caught in the cumulative pass reopens `ready-for-review`'s step 3 and costs another cumulative round. That cost is an inference from the mechanism, not a measurement.

## Why an unsettleable claim draws a finding only when it is an unsourced number or cause and effect

A "could not verify" finding has no edit to ADDRESS and fits no DEFER criterion, so it would recur at every cumulative pass. An unsourced number or cause-and-effect claim is a defect on its face, wherever its subject lives, because it breaks the "Quantitative or causal claims in prose" bullet without the reviewer reading anything. Its fix names the source, marks the claim unverified, or deletes it. Any other unsettleable claim draws no finding, which follows Restated canonical rule's "No home located, no finding".

## Why the reviewer checks no external-state claim for truth

The reviewer holds `Read`, `Grep`, `Glob` and `Write`, with no `Bash` or network tool, by the closed-form design in `reviewer-persona-roster-operations.md`. Step 1.5's "Unverified external-state claim" tripwire already asks inline for evidence behind such a claim in the diff or its commit message.

## Why the reviewer stays one agent at `effort: high`

The angle's boundary with Prose restating the code is a precedence clause, like the restating angle's precedence over the altitude angle in `prose-restating-code-is-deleted.md`. A passage that would be deleted even if true takes the restating angle's deletion fix. A feature doc's account of what the code does for its user or how to use it stays when true, so the new angle owns it.

## Why a mechanical reference checker is deferred

The maintainer deferred a mechanical checker for file, flag and symbol references in prose to a follow-up. It would catch a vanished name, not a changed behavior.

## How the wording was measured

The fixture cells and the scratch trees' contents live in `.claude/plans/prose-claim-truth-check.md` § "Verification". Each cell guards one rule of the angle or the tripwire and runs K=5 times against the bars from `paragraph-readability-review-angle.md`: a must-flag cell flags its site in at least 4 of 5 runs, and a must-pass cell flags it in at most 1 of 5.

A cell that misses by one run gets five more runs and is scored over all ten, against at least 8 for a must-flag cell or at most 2 for a must-pass cell. Those two thresholds are this record's own rule. With eight must-flag bars and an assumed 90% hit rate per cell, all eight clear one K=5 pass together about half the time, so the K=10 re-run raises that chance. The 90% rate is an assumption, not a measurement.

The tripwire cells apply the bullet alone, outside a real `/code-review` run, so they do not measure it in the orchestrator's full context. The cells show that the wording works on synthetic cases, not a catch rate. The outcome test is re-running a count of fix commits that correct a false, stale or overstated prose claim over pull requests merged after this lands.

### Measured rates

Measured 2026-11-05 on Sonnet 5.5, one fresh general-purpose subagent per run, K=5 per cell. The bars are must-flag at least 4 of 5 and must-pass at most 1 of 5. No cell needed the K=10 re-run.

**Angle, first wording.** Three cells missed:
- The restating-comment precedence cell: the comment was labeled `False or unsourced claim` in 5 of 5 runs and never as Prose restating the code. The comment is both false and restating.
- The two search-dependent cells ("hooks/ holds three hooks", "Only install.sh calls fetch.sh"): 1 of 5 each, because `Grep` and `Glob` were unavailable to most runs.

The angle's last bullet was reworded to give a restating passage the deletion fix whether true or false.

**Angle, reworded.** Every cell met its bar:
- Must-flag, each 5 of 5: a false default, a false count, the quantified claim found by search, an unsourced number, and an unsourced causal claim.
- Precedence: Prose restating the code with a deletion fix 5 of 5, and `False or unsourced claim` on that site 0 of 5.
- Must-pass, each 0 of 5: a true default, a settleable cause and effect, a number with a named source, an instruction carrying a number, an unsettleable claim that is neither a number nor a cause and effect, a record's account of past events, and quoted text.
- No Prose restating the code finding landed on a usage-doc cell.

**Tripwire.** Every cell with a bar met it:
- Must-flag, each 5 of 5: a vanished flag name and a redefined default.
- Must-pass, each 0 of 5: the changelog, plan and dated-record skip, an ordinary use of the word "limit", and a still-true statement.
- A diff changing no name drew no finding in any run.

**Unmeasured and caveats:**
- The tripwire was re-measured after its bullet was restructured into a lead plus sub-bullets, and every cell met its bar again: both must-flag cells 5 of 5, every must-pass cell 0 of 5, and the no-contract diff drew no finding and made no search in 5 of 5 runs.
- The no-contract runs read the bullet with `Read` only. Each transcript holds two `Read` calls and no `Grep`, `Glob` or `Bash` call, counting `"type":"tool_use"` entries. A raw count of `"name":"Grep"` strings over-counts, because the tool schemas in a transcript's header carry those names too.
- The three must-pass contract cells met their bar partly by omission, because the runs examined only a few files and the plan, dated-record and ordinary-word sites were never examined. Only the changelog skip was exercised, in 5 of 5 runs.
- Every run of the reworded angle used `Bash` grep in place of `Grep` and `Glob`, so the search-dependent cells were measured under that substitution, not under the tools the cells specify.
- The tripwire cells apply the bullet alone, so they do not measure it inside a full `/code-review` run.

## The guard's limit

No committed fixture or test guards the wording, so re-running the cells after any edit to the angle's block or the tripwire bullet is an honor-system rule. A hash pin would trigger a re-measure without forcing one, and a committed fixture would need a new format holding a scratch tree, a diff and per-site expectations, plus a new test.

## Sources

- `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles"
- `claude-skills/skills/code-review/SKILL.md` § "Step 1.5 — Judgment-activation pass"
- `comment-discipline-reviewer-deferred-to-cumulative-pass.md`
- `prose-restating-code-is-deleted.md`
- `paragraph-readability-review-angle.md`, for K and the bars
- `reviewer-persona-roster-operations.md`
- `.claude/plans/prose-claim-truth-check.md`
