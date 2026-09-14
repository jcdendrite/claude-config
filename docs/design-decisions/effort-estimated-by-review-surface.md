# Effort estimated by review surface, not implementation time

*2026-05-29. Formerly `docs/design-decisions.md` §14.*

For a coding agent, implementation time is near-zero — a 500-line change and a one-line fix both run in minutes. The real costs that gate a change are reviewer time (file count, domain complexity, risk concentration) and testing surface area. Anchoring effort estimates on implementation time miscalibrates triage and treats low-implementation-cost changes as low-risk even when they touch shared surfaces.

The rule is encoded in two planning skills: `plan-it` instructs that effort sections, if present, describe review surface (file count, domain spread, risk concentration) and never hours or days; `plan-review` enforces it at checklist item B15, flagging any effort section citing hours/days and rewriting it in review-surface terms. The rule is deliberately scoped to plan documents — where effort estimates are formally consequential — rather than all prose: a general always-loaded instruction would be a heavier mechanism than the documented occurrence warrants (the [effort estimation case study](../case-studies/effort-estimation-review-surface.md) puts the rate at roughly 6–8 instances across 50K assistant text blocks, with the dominant corpus signal being the agent flagging the pattern as a defect, not using it).

## Sources

- `claude-skills/skills/plan-it/SKILL.md` — the governing rule (Step 5, effort section guidance)
- `claude-skills/skills/plan-review/SKILL.md` — enforcement (checklist item B15)
- `claude-skills/skills/plan-it/REFERENCES.md` — cross-template research confirming no canonical PR planning template uses hour/day estimates at single-PR scope
