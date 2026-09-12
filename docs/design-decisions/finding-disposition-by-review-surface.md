# Finding disposition calibrated by review surface, not coding time

*2026-06-14. Formerly `docs/design-decisions.md` §16.*

[§14](effort-estimated-by-review-surface.md) established that effort anchors on review surface, not implementation time, and scoped the rule to plan effort sections. The same miscalibration surfaced at the code-review finding-disposition step: the orchestrator deferred reviewer findings cheap to ADDRESS — small, in already-touched code, covered by tests already running — on effort/size/non-blocking grounds. A transcript audit found the pushback recurring across 10+ sessions in more than one repo; in several the "pre-existing/independent" label was wrong because the PR's own change touched or activated the finding.

The fix wires [§14](effort-estimated-by-review-surface.md)'s principle into disposition: the code-review skill's Finding-disposition section now states disposition calibrates on complexity, risk, and testing area — not implementation effort — reinforces the opportunistic-refactoring license for tech debt in already-touched, already-tested code, hardens "Orthogonal scope" against the touch/activates-it mislabel, and adds "small/quick/cosmetic/non-blocking/advisory" to the invalid-DEFER list.

## Sources

- `claude-skills/skills/code-review/SKILL.md` — Finding disposition (ADDRESS/DEFER) machinery
- [§14](effort-estimated-by-review-surface.md) — the parent principle this extends
