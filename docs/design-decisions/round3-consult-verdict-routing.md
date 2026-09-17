# Route the round-3 architect-consult gate's return by a closed three-verdict trichotomy

*2026-09-16.*

`require-architect-consult.sh` denies a reviewer-persona spawn once a
branch is at its round cap without a recent architect consult, and its
deny message prescribes dispatching `plan-architect MODE=consult` and
retrying. `code-review/SKILL.md`'s `### Round-cap architect consult`
section reads the consult's free-prose return as exactly one of three
verdicts, so the gate's one per-branch firing routes the loop instead of
resuming it unchanged. `code-review/SKILL.md` § "Round-cap architect consult" states each verdict's routing directly. This entry doesn't
restate it.

Two rejected alternatives, both because a more literal reading of the
trichotomy would have opened a hole:

**A new `plan-architect` mode or return grammar.** Rejected in favor of
routing the agent's existing free-prose return, because a second grammar
would need to be maintained in lockstep with `code-review/SKILL.md`'s
routing rule, doubling the surface for the same job. The trichotomy stays
a rule for the reading session to apply, not a contract imposed on the
consulted agent, so `plan-architect.md` itself is untouched.

**A blanket "stop reviewing" verdict, or a sixth DEFER criterion.**
Rejected because either would function as a sixth DEFER criterion granted
by a subagent's prose, which the enforcement-invariant DISPOSITION_RULE in
`code-review/SKILL.md`'s *Finding disposition* section forecloses
absolutely. The "stop escalating" verdict is scoped narrowly instead: it
ends the round and feeds the existing gold-plating criterion per finding,
never a standing exemption from the closed DEFER list.

## Spawn-obligation invariant

**No verdict discharges a matched Change-type row's spawn obligation.**
See `code-review/SKILL.md` § "Round-cap architect consult" for the
routing rule itself. Without it, "the architect consult covered it"
would function as non-specialist scrutiny silently substituting for a
near-mandatory `ciso-reviewer`/`staff-product-engineer` spawn.

## Sources

- [round3-plan-architect-consult-gate.md](round3-plan-architect-consult-gate.md) — the gate and latch this entry's routing rule answers; nothing in it is overturned.
- `claude-skills/skills/code-review/SKILL.md`'s `### Round-cap architect consult` section — the routing rule itself.
- `claude-skills/skills/subagent-delegation/SKILL.md`'s "Implementation work → `code-writer`" rule — what the third verdict's ADDRESS row routes through.
- `.claude/plans/round3-gate-executable-plan.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.
