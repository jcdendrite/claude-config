# Route the round-3 architect-consult gate's return by a closed three-verdict trichotomy

*2026-09-16.*

`require-architect-consult.sh` denies a reviewer-persona spawn once a
branch is at its round cap without a recent architect consult, and its
deny message prescribes dispatching `plan-architect MODE=consult` and
retrying. Before this entry, nothing told the dispatching session what the
consult's free-prose return meant or where it routed, so the gate spent
its one per-branch firing and the underlying review loop resumed
unchanged the moment the retry succeeded. `code-review/SKILL.md`'s new
`### Round-cap architect consult` section closes that gap by reading the
return as exactly one of three verdicts:

- **Foundation is wrong — re-plan.** The round ends: no further spawns, no
  fix dispatch. The branch's plan file is revised through `plan-it` Step
  5's revision re-dispatch, or `/plan-it` runs fresh when the branch has
  none. The rows enumerated that round spawn again once Step 1 re-runs
  against the replaced surface.
- **Foundation is fine, the review loop is the noise — stop escalating.**
  The round finishes on the rows already enumerated and no new round
  opens. The verdict is evidence a given finding meets the existing
  gold-plating-beyond-declared-user-surface DEFER criterion, applied per
  finding — never a blanket DEFER, and never a new criterion.
- **Foundation is fine, one concrete defect remains — fix it.** Only when
  the return names the defect's `file:line` and a concrete fix: an
  ordinary ADDRESS row, routed to `code-writer` like any other. A return
  that leaves the fix still to be designed does not qualify.

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

**No verdict discharges a matched Change-type row's spawn obligation.**
"The architect consult covered it" is non-specialist scrutiny
substituting for a dispatch, which the invalid-skip-rationale class in
`code-review/SKILL.md`'s *Ripple effect triage* section already forecloses
by its own stated principle, whether or not the consult's wording matches
it verbatim. A denied reviewer spawn is retried immediately under the
second and third verdicts. Under the first, the round ends and those
rows spawn again once Step 1 re-runs against the replaced surface.
Without this rule the new verdict-routing text would silently create
an escape hatch around a near-mandatory
`ciso-reviewer`/`staff-product-engineer` spawn.

## Sources

- [round3-plan-architect-consult-gate.md](round3-plan-architect-consult-gate.md) — the gate and latch this entry's routing rule answers; nothing in it is overturned.
- `claude-skills/skills/code-review/SKILL.md`'s `### Round-cap architect consult` section — the routing rule itself.
- `claude-skills/skills/subagent-delegation/SKILL.md`'s "Implementation work → `code-writer`" rule — what the third verdict's ADDRESS row routes through.
- `.claude/plans/round3-gate-executable-plan.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.
