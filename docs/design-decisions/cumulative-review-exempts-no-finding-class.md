# Cumulative `/code-review` passes exempt no finding class from blocking

*2026-10-18.*

A study of past `/code-review` findings set out to measure whether some finding classes could be resolved without a fix, so the cumulative pass that `/ready-for-review` runs would come back clean more often on large diffs. It stopped at its first stage, before measuring any candidate rule's miss rate. A read-only rounds measure then ran over the same review history; it was not a miss-rate measurement. The engineer chose to stop at the first stage and to adopt no rule. The reasoning is in the plan's "Phase 1 outcome" block.

The review history was frozen on 2026-10-07. The candidate rules are defined in `.claude/plans/clean-pass-criteria.md` § "Candidate rules (item 6 ships the scope, the fail-closed defaults, the exclusions, the definition and the picked rules word for word)". The heading's "item 6" is the plan's name for the keep rule it would have shipped, which this decision did not ship.
`code-review/SKILL.md` § "Finding disposition" is unchanged. So is what counts as clean, defined in `code-review/SKILL.md` § "Step — Record review completion".

The study's figures are not published. A miss rate would rest on agent judgment over local review records, which no command can re-run. Those records can also span more than one account's sessions, so the wider-corpus bar applies to every figure. `docs/private-project-redaction.md` § "Publishing a tooling measurement" bars such figures without per-figure authorization. The study's working files stayed local.

Reducing review rounds is a separate question, piloted off by default in [architect-authored-fix-plan-pilot.md](architect-authored-fix-plan-pilot.md).

The plan's "Revisit trigger" says when to re-measure. A re-measure takes a fresh snapshot of the review history.

## Sources

- `.claude/plans/clean-pass-criteria.md` § "Phases": the study design and its "Phase 1 outcome" block.
