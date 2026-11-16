# Claims in comments and durable docs are checked for truth by one angle and one tripwire

*2026-11-05.*

`claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" carries the `False or unsourced claim` angle, and `claude-skills/skills/code-review/SKILL.md` § "Step 1.5 — Judgment-activation pass" carries the `Stale claim in unchanged prose` tripwire. Each states its own predicate and carve-outs. This record keeps only why the design has this shape and how its wording was measured. The plan that scoped the change is `.claude/plans/prose-claim-truth-check.md`.

## Why changed claims and stale claims take different routes

The reviewer judges only added or modified lines, so it cannot see an unchanged passage that a code change makes false. The tripwire covers that arm inline in every round because the orchestrator already holds the changed code. The tripwire's search key is a literal name the diff itself changes. A diff that changes no such name searches nothing.

The angle for changed claims shares the reviewer's existing deferral to `ready-for-review`'s cumulative pass, recorded in `comment-discipline-reviewer-deferred-to-cumulative-pass.md`. The tripwire cannot share that deferral. Step 1.5 has no per-context switch, and a finding first caught in the cumulative pass reopens `ready-for-review`'s step 3 and costs another cumulative round. That cost is an inference from the mechanism, not a measurement.

## Why an unsettleable claim draws a finding only when it is an unsourced number or cause and effect

A "could not verify" finding has no edit to ADDRESS and fits no DEFER criterion, so it would recur at every cumulative pass. `claude-skills/skills/code-review/SKILL.md` § "Finding disposition" defines ADDRESS and DEFER. An unsourced number or cause-and-effect claim is a defect on its face, wherever its subject lives, because it breaks the "Quantitative or causal claims in prose" bullet without the reviewer reading anything. The angle's rule for unsettleable claims and their fixes is in `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles", in the `False or unsourced claim` block. That any other unsettleable claim draws no finding follows Restated canonical rule's "No home located, no finding".

## Why a false claim's fix deletes or narrows it

A rewritten claim is modified prose, which the reviewer judges again in the next round. So the fix is narrowed or deleted, as `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" states in the `False or unsourced claim` block.

The Restating angle already owns the test for what to cut, so the False angle's fix defers to it. A second False-angle clause saying what to keep or what the reader needs would restate the Restating angle's test.

The replay comparison that chose this wording is in `.claude/plans/comment-rule-false-angle-fix.md` § "Context".

## Why the reviewer checks no external-state claim for truth

The reviewer holds `Read`, `Grep`, `Glob` and `Write`, with no `Bash` or network tool, by the closed-form design in `reviewer-persona-roster-operations.md`. Step 1.5's "Unverified external-state claim" tripwire already asks inline for evidence behind such a claim in the diff or its commit message.

## Why the reviewer stays one agent at `effort: high`

The reviewer stays one agent because the angle's boundary with Prose restating the code is a precedence clause, not a judgment that needs its own context. A passage both angles could flag draws one finding, not two with opposite fixes. The precedence cell in `.claude/plans/prose-claim-truth-check.md` § "Verification" measures that a comment both false and restating draws the Prose restating the code finding, not a `False or unsourced claim` finding. The precedence clause is like the restating angle's precedence over the altitude angle in `prose-restating-code-is-deleted.md`. That file also says the judgment at the boundary between angles is what needs `effort: high`.

## Why a mechanical reference checker is deferred

The maintainer deferred a mechanical checker for file, flag and symbol references in prose. It is not built here. GH-1397 is the tracker, and it lists the checker only as one candidate mechanism. It would catch a vanished name, not a changed behavior.

## How the wording was measured

The fixture cells and the scratch trees' contents live in `.claude/plans/prose-claim-truth-check.md` § "Verification". Each cell guards one rule of the angle or the tripwire and runs K=5 times against the bars from `paragraph-readability-review-angle.md`: a must-flag cell flags its site in at least 4 of 5 runs, and a must-pass cell flags it in at most 1 of 5.

A cell that misses by one run gets five more runs and is scored over all ten, against at least 8 for a must-flag cell or at most 2 for a must-pass cell. Those two thresholds are this record's own rule. The eight must-flag bars are five angle cells, one precedence cell and two tripwire cells. At an assumed 90% hit rate per cell, P(at least 4 of 5) = 0.919 and 0.919^8 ≈ 0.51, so all eight clear one K=5 pass together about half the time. The K=10 re-run raises that chance. The 90% rate is an assumption, not a measurement.

The tripwire cells apply the bullet alone, outside a real `/code-review` run, so they do not measure it in the orchestrator's full context. The cells show that the wording works on synthetic cases, not a catch rate. The outcome test is re-running the count of fix commits that correct a false, stale or overstated prose claim, over pull requests merged after 2026-11-05. The baseline count is in `.claude/plans/prose-claim-truth-check.md` § "Context".

### Measured rates

Measured 2026-11-05 on Sonnet 5.5, one fresh general-purpose subagent per run, K=5 per cell. No cell needed the K=10 re-run.

**Angle.** Every cell met its bar:
- Must-flag, each 5 of 5: a false default, a false count, the quantified claim found by search, an unsourced number, and an unsourced causal claim.
- Precedence cell (a comment that restates the code and is also false): Prose restating the code with a deletion fix 5 of 5, and `False or unsourced claim` on that site 0 of 5.
- Must-pass, each 0 of 5: a true default, a settleable cause and effect, a number with a named source, an instruction carrying a number, an unsettleable claim that is neither a number nor a cause and effect, a record's account of past events, and quoted text.
- No Prose restating the code finding landed on a usage-doc cell.

**Tripwire.** Every cell with a bar met it:
- Must-flag, each 5 of 5: a vanished flag name and a redefined default.
- Must-pass, each 0 of 5: the changelog skip and a still-true statement. The other must-pass sites were not exercised, as the second caveat below states.
- A diff changing no name drew no finding and made no search in 5 of 5 runs.

**Unmeasured and caveats:**
- The runs over the diff that changes no name read the bullet with `Read` only. Each transcript holds two `Read` calls and no `Grep`, `Glob` or `Bash` call, counting `"type":"tool_use"` entries. A raw count of `"name":"Grep"` strings over-counts, because the tool schemas in a transcript's header carry those names too.
- The tripwire's must-pass cells met their bar partly by omission, because the runs examined only a few files and never examined the plan, dated-record and ordinary-word sites. Only the changelog skip was exercised, in 5 of 5 runs.
- Every run of the angle used `Bash` grep in place of `Grep` and `Glob`, so the search-dependent cells were measured under that substitution, not under the tools the cells specify.

### 2026-11-15 re-run for the false-claim fix rule

Measured on Sonnet 5.5, one fresh general-purpose subagent per run, on the reviewer with the 2026-11-15 wording. Each cell ran K=5, except the pointer cell, which ran 7 times, and the acceptance cell, re-run at K=10. The subagents had no `Grep` or `Glob`, so each must-flag run searched with read-only `Bash` and wrote a `LISTING:` header line naming it.

**Cells.**
- Must-flag, each 5 of 5: a false default, a false count, a quantified claim found by search, an unsourced number and an unsourced causal claim.
- Precedence cell: Prose restating the code with a deletion fix 5 of 5, and a `False or unsourced claim` finding on that site 0 of 5.
- Must-pass, each 0 of 5: the seven items of the 2026-11-05 run.
- Pointer cell, a false docstring on a function whose behavior lives in another function: the claim was deleted in 7 of 7 runs and no run rewrote it. The docstring is also contradicted by the function's own body, so the cell does not isolate the pointer's routing.
- Negative cell, a rationale naming an external system: a Prose restating the code finding 0 of 5, and any finding 1 of 5, an Unsourced one.
- Limit-note cell, a docstring on a gate function saying what the gate does not catch, scored by each run's first stated fix on the reviewer at `13ccd953` and on the reviewer with the 2026-11-15 wording:
  - A true note, which names a previous commit that the function's signature rules out, was deleted in 4 of 5 runs on the reviewer with the 2026-11-15 wording and 2 of 5 on the reviewer at `13ccd953`.
  - A partly false note was deleted in 2 or 3 of 5 runs on the reviewer with the 2026-11-15 wording and 1 to 3 of 5 on the reviewer at `13ccd953`, depending on how a run that names both a rewrite and a deletion is scored.
  - There is no bar. The two reviewers differ in both the False bullet and the restating pointer, so the cause is not known.

**Unmet bar.** On the acceptance cell, a true docstring on a function that only calls another function, the docstring was deleted in 5 of 10 runs against a bar of at least 8 of 10 after the K=10 re-run. Runs that left it alone reasoned that the function's name, signature and body do not show the criterion. That is the bar `claude/.claude/CLAUDE.md` § "Durable text" sets for what is obvious, which the Restating angle cites.

**Replay.** The runs replayed 61 units through the reviewer with the 2026-11-15 wording. A unit is a past review finding or a claim from this repository. The study recorded for each unit what the right fix would have been, and two classifiers read that record. Each figure below lists one count per run, over three runs.
- Real findings where the reviewer kept the claim: 8, 8 and 9 of 28. The study scored a finding as real by its own criterion, which this repository does not hold, and `.claude/plans/comment-rule-false-angle-fix.md` § "Context" states the count of 28. Kept the claim means a no-finding or keep-correct verdict, and a keep-correct verdict lets the claim stay with a possible reword. Across the three runs, 24 of those 25 verdicts were keep-correct.
- Deleted among the 30 claims the study verified: 0 of 30 in every run. Of those 30, 24 were verified true, 5 partly false and 1 unverifiable.
- Units deleted outright that classifier A labels as needing a shorter local version: 4, 5 and 4.
- Units deleted outright that classifier B labels as having a recorded fix that keeps the claim: 2, 2 and 1.

The units and claims come from this repository's own tracked files and review findings, and the study's inputs are local and unpublished. The figures come from `.claude/plans/comment-rule-false-angle-fix.md` § "Context", except the classifier B counts, which are in `.claude/plans/comment-rule-false-angle-fix.md` § "Assumption ledger", row 36. Three runs make these figures directional.

**Caveats:**
- The bars at K=5 were scored as the 2026-11-05 run scored them, except for two cells. The precedence cell is scored by fix kind with one finding counted per site, and the negative cell by Prose restating the code findings only. Both cells also meet the original bars: restating with deletion at least 4 of 5, a `False or unsourced claim` finding at most 1 of 5, and any finding at most 1 of 5.
- The cells cannot show a change in fix kind. The quantified-claim cell's fix (a claim that only one script calls another) named every caller in each run, which no rule separates from a narrowing. The replay is the only evidence of a shift. The limit-note cell also scores fix kind and shows none for a partly false note.
- Nothing here measures the goal of fewer review rounds. A replay that re-reviews the fixed text and counts findings would.
- The record makes no claim about whether the reviewer leaves a limit note alone. The limit-note cell has one true note, whose limit the function's signature can express, and runs split on whether that makes it restating. A true note of a limit that no parameter or branch shows was not measured.

## The guard's limit

No committed fixture or test guards the wording, so re-running the cells after any edit to the angle's block or the tripwire bullet is an honor-system rule. A hash pin would trigger a re-measure without forcing one, and a committed fixture would need a new format holding a scratch tree, a diff and per-site expectations, plus a new test.

## Sources

- `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles"
- `claude-skills/skills/code-review/SKILL.md` § "Step 1.5 — Judgment-activation pass"
- `claude-skills/skills/code-review/SKILL.md` § "Finding disposition"
- `comment-discipline-reviewer-deferred-to-cumulative-pass.md`
- `prose-restating-code-is-deleted.md`
- `paragraph-readability-review-angle.md`, for K and the bars
- `reviewer-persona-roster-operations.md`
- `.claude/plans/prose-claim-truth-check.md`
- `.claude/plans/comment-rule-false-angle-fix.md`
