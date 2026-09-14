# Full-suite pytest drift traced to two stale pre-`select-tests.py` plans; no handoff-validation mechanism added

*2026-09-04. Formerly `docs/design-decisions.md` §53.*

A transcript-corpus audit of this repository's own project directories, described in full in the plan, confirmed the full-suite-run pattern was recurring, not a one-off, after the rule shipped (`7200d727`/`bf215df9`, 2026-08-25).

A systemic fix was drafted and rejected. The design added a `check-handoff.py` soft check plus a `handoff/SKILL.md` §3 clause requiring a handoff's named verification command be re-derived from the project's current documentation rather than copied from its plan. `/plan-review` found a foundation-level defect: CLAUDE.md's second full-suite exception ("a plan's Verification step... genuinely calls for a whole-repo claim") is a condition on intent, not a machine-readable predicate. Any mechanism checking "does this command match the default" would therefore silently override a legitimate whole-repo Verification claim on some future plan — trading the observed defect for a different one. This forecloses the whole family of "just check the command against the default" fixes, not only the one drafted here.

The two stale plan files are not fixed by this decision. That correction was handed directly to the two branches' own sessions rather than made from this unrelated branch, since editing another branch's plan file re-arms `require-plan-review.sh` there until re-reviewed. The full ledger, evidence, and rejected design live in `.claude/plans/select-tests-handoff-drift.md`, kept on this branch as the durable record rather than restated here.

**Revisit** if any of:

- A future inherited full-suite run traces to a plan file that postdates `select-tests.py` (2026-08-25) — that would mean the drift is live and recurring rather than two aging plans working through their own backlog, and would reopen the systemic-fix question.
- Either of the two branches above merges without its Verification section corrected — the stale command becomes a merged, harder-to-notice instruction rather than a live one two sessions were told about directly.
- `review-pipeline-orchestrator-subagent.md`'s proposed (not yet built) Bash-mutation-restriction hook ships with its command allowlist still omitting `select-tests.py` — that would convert this from a stale instruction into an enforced one.

## Sources

- `.claude/plans/select-tests-handoff-drift.md` — full assumption ledger, per-session provenance classification, and the rejected mechanism's design and `/plan-review` rejection.
- `CLAUDE.md`'s Commands section — the `select-tests.py` rule and its two named exceptions.
- `.claude/plans/prevent-runaway-subagent-cost.md` and `.claude/plans/review-pipeline-orchestrator-subagent.md` (each on its own branch) — the two stale Verification sections this entry traces the drift to.
- `.claude/plans/select-tests-fallback-audit.md` (merged, GH-765) — the prior related audit that fixed five other propagation surfaces without flagging `handoff/SKILL.md`'s silence as one of them.
