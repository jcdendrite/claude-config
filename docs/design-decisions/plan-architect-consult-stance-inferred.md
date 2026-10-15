# `plan-architect` chooses its consult stance by its own judgment, with no second literal

*2026-10-14.*

`plan-architect` chooses its consult stance, review or design, by its own judgment of the dispatch. It reports the stance on the return's last line, and no second literal sets it. `claude/.claude/agents/plan-architect.md` § "MODE=consult" holds the default rule for the choice.

**Why it differs from MODE.** [`plan-architect-consult-mode.md`](plan-architect-consult-mode.md) explains why `MODE=` stays an explicit literal: a misread mode would land consult prose in a committed plan file. A misread stance cannot do that. Both stances return conversational prose, and the only path from a consult to a plan goes through a separate plan-sections re-dispatch.

**Why the agent chooses, not the caller.** There are two reasons:

- **Less complexity.** There is no second literal, and no existing `MODE=consult` dispatch site changes.
- **More objectivity.** A session asked to choose a stance may be less likely to pick an adversarial review of its own proposal. The agent has no stake in that proposal.

**The residual.** The caller still writes the prompt. A caller that leaves its proposal out gets an independent design, not a softer review. A caller that frames its proposal as settled context still meets the body's rule to treat a handed-over approach as a hypothesis, which applies in both stances.

**Why a misread stays safe.** A caller-named verdict leads the return, so a misread stance still answers in the caller's terms. The closing stance line makes a misread visible to the caller and the human without a caller-side literal.

**Rejected.** A `CONSULT=` literal. It changes every `MODE=consult` caller and puts the choice back with the session whose objectivity the second reason above distrusts.

## Sources

- [`plan-architect-consult-mode.md`](plan-architect-consult-mode.md) — why `MODE=` stays an explicit literal, and why a subagent sees only its dispatch prompt plus its startup load.
- `.claude/plans/plan-architect-consult-persona.md` — the assumption ledger and mechanisms behind this decision.
