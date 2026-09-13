# `review-loop-cost-audit`'s freeze-partition fix: what post-merge calibration actually established

*Part of the [claude-config case studies](../case-studies.md).*

PR #987 fixed `review-loop-cost-audit`'s freeze-partition and verdict-rubric logic, correcting two false-positive "Stuck loop" verdicts. Both were traced to a timezone mismatch between the audit's commit clock and the corpus's UTC round timestamps. The same fix also corrected three further defects:

- An unhandled still-active branch.
- An unhandled vacuous partition.
- A rubric that required skew and dispatches-per-round as co-signals when neither tracks the freeze partition.

That fix's own commit message noted the calibration was one-sided: "the freeze partition's two corrected cases are both true negatives... its positive cases carry no calibration."

## What the post-merge audit found

A sweep ranked this repo's branches by review-round dollar total and selected three for deep audit against the fixed skill:

- The two branches carrying the most review rounds in the corpus.
- The branch carrying the highest share of its review-round spend falling outside dated round windows.

The three audits are not three converging null results. Two produced no measurement at all:

- One branch was still active — the corpus's dated round table never captured its final two commits, so Step (d2)'s liveness guard correctly withheld a verdict rather than scoring live work as thrash.
- One branch's code-freeze instant fell at or after the newest round of every round type it carried, so Step (e)'s partition was vacuous — zero rounds fell after the freeze.

Both confirm the guards fire correctly. Neither carries information about thrash prevalence, since neither branch had a post-freeze round to classify.

The third branch is the only one that produced a real measurement: one post-freeze `plan-review` round, reviewing a genuinely new plan authored after the freeze. The round-by-round content showed monotonic convergence to an "Approve with changes" verdict rather than re-surfaced findings. The review was split across two sessions by a deliberate context-pressure `/handoff`, not a crash or stale lock. The narrative read settled the question: this is legitimate follow-on work, not thrash — a true negative.

So the honest count from this pass is one true negative and two abstentions, not three confirmations. The sample of *measurable* high-cost branches is n=1.

## A rubric gap the audit surfaced

The third branch's verdict didn't fit the skill as written at audit time. Step 4's Inconclusive list was closed to four specific triggers, none of which applied:

- There was a real freeze.
- There was a real post-freeze round.
- There was a narrative read that settled the question rather than leaving it unclear.

The only non-thrash verdict, "Legitimate large-diff work," was defined by commit spread across the branch's whole history — also not this branch's shape, since it had zero post-freeze commits. The auditor's honest fallback was Inconclusive, but nothing in the rubric text actually produced that label; it was the auditor's judgment that nothing else fit.

This is now fixed: the skill has a fifth verdict, **Legitimate follow-on work**, for exactly this shape — a post-freeze round reviewing genuinely new work rather than re-litigating the frozen code, confirmed by the narrative read.

## A near-miss that turned out to be systematic

One audit hit a `ready-for-review` denial triggered by a reviewer's own `grep` search pattern, which happened to contain the literal text of a gated command. At the time this read as a lucky catch by the rubric's discrimination logic. It isn't luck: `require-ready-for-review.sh` documents that a command merely mentioning a gated command in free text is denied by design, fail-closed. Any branch whose sessions grep or discuss gated commands can produce this same denial shape. The skill's Step 3(g) and Step 4 now require confirming that a denial was a genuine invocation attempt, not an incidental textual mention, before counting it toward Gate-denial churn.

## Named residual

This calibration pass remains one-sided. It confirms Step (d2)'s liveness guard and Step (e)'s vacuous-partition case are real, reachable code paths, and it produced one genuine true negative for the new **Legitimate follow-on work** verdict. It does not confirm that **Stuck loop**, **Plan-grinding**, or **Gate-denial churn** fire correctly when actually warranted — no branch in this pass turned out to be a true positive for any of the three.

**Revisit trigger.** Re-run this calibration against the next branch the sweep ranks highly that has both a real freeze (not still-active, not vacuous) and post-freeze rounds — re-derivable via `review-loop-cost-audit`'s own sweep-then-deep-audit procedure. A confirmed true positive for any of Stuck loop, Plan-grinding, or Gate-denial churn closes the corresponding half of this residual.

## Sources

- `claude/.claude/scripts/transcript-analysis.py` — `review-round-cost`, `buckets`, and `subagent-mix`, the subcommands producing the corpus ranking and round tables this audit read.
- `claude-skills/skills/review-loop-cost-audit/SKILL.md` — the audited skill, including this study's own follow-up edit (the `Legitimate follow-on work` verdict and the genuine-invocation-attempt clause).
- PR #987 — the freeze-partition and verdict-rubric fix this study calibrates.
