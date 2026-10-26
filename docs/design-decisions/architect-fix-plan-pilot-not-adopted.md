# The architect-authored fix-plan pilot is not adopted

*2026-10-25.*

The pilot in [A default-off pilot has `plan-architect` author each `/code-review` round's fix plan](architect-authored-fix-plan-pilot.md) is not adopted. The `architect_fix_plan_pilot` key stays `false` by default, its code stays dormant behind that key until a separate decision removes it, and any account that enabled it sets it back to `false`.

The decision rests on absence of benefit and a failed guardrail, not on proof of harm.

## What was assessed

The assessment ran once, over existing transcripts, and is exploratory. It compared this repository's `/code-review` rounds with the key on against rounds from a comparison set outside this repository, read privately by the engineer, with the key off. The read was private and used no command that refuses a wider corpus, so this record publishes verdicts and method limits and no measurement. A verdict that involves the comparison set says only which way that comparison pointed. The assessment was not randomized: the engineer chose which work ran where, so the two sets are not matched on workload, calendar time or session model.

Outcomes and guardrails were fixed before grading, except for the three choices disclosed under "What the original pilot lacked":

- **Primary outcomes:** code-review rounds per PR and human asks per PR.
- **Cost:** reported, and used only as a tiebreak if the guardrails tied.
- **Guardrail 1:** format stops stay at or under a fixed share of pilot dispatches.
- **Guardrail 2:** an audit of DEFER dispositions, with the set label hidden from the graders, shows the pilot no worse than the comparison set, within a fixed one-sided non-inferiority margin.
- **Guardrail 3:** no DEFER or KEEP on an enforcement-invariant finding goes through without stopping for the human.

Each threshold's value is withheld, because a value paired with its verdict bounds the figure it was tested against.

Any failed guardrail means the pilot is not adopted, and an inconclusive one counts as failed.

## What was found

- **Rounds and asks per PR:** no detectable difference. The comparison cannot support adoption.
- **Guardrail 1:** passed, including when every dispatch with no recorded outcome was counted as a stop.
- **Guardrail 2:** passed, but the audit is weak. The graders saw finding text and no code, their error calls on DEFERs did not separate the two sets, and they disagreed with each other. It is a pass, not an inconclusive result under the rule above. It supports no reading beyond that verdict. The graders did flag some DEFER dispositions as errors, which is the failure class named under "What this record does not claim". The graders were model sessions run inside the engineer's own sessions.
- **Guardrail 3:** failed. A DEFER of an enforcement-invariant finding went through without a stop, and a keyword screen of the rest was too narrow to say there were no others. The enforcement-invariant rule (`DISPOSITION_RULE:code-review-defer-invariant` in `claude-skills/skills/code-review/SKILL.md`) is read here with "the diff" meaning the whole PR against its base, not the round's own change, and that reading is what makes the finding a hit.
- **Cost:** the pilot's architect consults added cost. That is gross cost, and it excludes the dispatching session's own turns. No saving elsewhere was found. The two-set comparison cannot attribute any cost difference to the pilot.

## Why not adopt, and why not keep running it

- No measured benefit offsets the pilot's gross consult cost and a failed safety check.
- Keeping the key on to gather more data buys little. The comparison is confounded by consult routing: the engineer can direct a session to consult the architect with or without the key, so the sets differ in more than who writes the dispositions.
- At Claude Code's default transcript retention window (`cleanupPeriodDays`, 30 days, per `docs/pr-cost.md`), the sessions behind the comparison age out, so it cannot be repeated later on the same data.

## What the original pilot lacked

The original record listed four conditions before enabling:

- A baseline.
- A pre-registered adopt-or-revert rule with a stop-rate threshold.
- A reader for the `fix-plan:` marks.
- A deny/allow fixture pair.

None existed when the key went on, so this assessment had to build its reader, its audit and its rule after the fact. The engineer made three choices after the numbers were visible:

- How an untagged dispatch counts as a stop.
- The floor on how many PRs the audit needs.
- Which reading of "the diff" the third guardrail uses.

Each could move a result, so each is disclosed here.

The lesson for any later pilot is to write the baseline, the metric, the margin and the stopping rule before enabling it.

## What this record does not claim

- That Sonnet dispositions are as good as the architect's. The audit and the outcome counts cannot show that, and the comparison did not hold session model fixed.
- That the architect should not review dispositions. A narrower design, where a reviewer challenges DEFER dispositions an orchestrator already wrote, targets the failure class the Guardrail 2 graders flagged (DEFER dispositions they judged wrong) and is expected to cost less. It needs its own record, with its metric and rule fixed first.
- That "the diff" in `DISPOSITION_RULE:code-review-defer-invariant` has one settled reading when the key is off. The rule's own text does not say, so that reading needs its own decision on the rule, not on this record.

## Known limits

- The two sets are not matched on diff size. The assessment removed Opus-orchestrated rounds from both sets.

## Sources

- The pilot's rule: `claude-skills/skills/code-review/SKILL.md` § "Finding disposition" (the `DISPOSITION_RULE:code-review-fix-plan-pilot` region).
- The DEFER criteria the audit graded against: the same section, in its "DEFER criteria (closed list)" and "Invalid DEFER rationales" paragraphs.
- Cost-recording rules, including the close-out window a merged PR must clear before capture: `docs/pr-cost.md`.
- Publication bar for tooling measurements: `docs/private-project-redaction.md` § "Publishing a tooling measurement".
