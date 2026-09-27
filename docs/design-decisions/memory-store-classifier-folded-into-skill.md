# `memory-store-classifier` agent deleted; classification folded into its calling skill

*2026-09-03.*

`memory-store-classifier` was added as a dedicated, Opus-pinned, read-only classification agent for `/memory-store-audit` Step 2 during that skill's original design (`.claude/plans/memory-audit-nudge.md`). During review of PR #817, a reviewer flagged the dedicated agent as an anti-pattern, and `plan-architect`'s consult confirmed it on three independently sufficient grounds:

1. **The agent's read-only tool cap was never load-bearing for the skill's human-in-the-loop guarantee.** The dispatching session holds `Bash`/`Write` unconditionally and performs every consequential act itself.
2. **The classification output does not survive as a standalone verdict.** Later skill steps re-read each non-`keep` file's full content anyway to draft the migration text, the issue body, and the compression-diff table.
3. **A dedicated agent's `description` is a permanent global cost.** It is paid by every session on every machine that installs this repo. It only amortizes for a call site every `/plan-it` run reaches — e.g. `plan-architect`'s [§30](plan-it-step5-pinned-opus-dispatch.md)/[§37](plan-architect-consult-mode.md) frequency-bound case. A periodic-maintenance workflow gated by a rare re-arm band doesn't qualify.

The agent file, its four `test_agent_roster.py` registrations, and the skill's classifier-dispatch text were deleted (`.claude/plans/memory-audit-nudge.md`, Step 5); classification now runs inline in `/memory-store-audit` Step 2's own session. This entry answers the same question [§30](plan-it-step5-pinned-opus-dispatch.md)/[§37](plan-architect-consult-mode.md) asked: is the agent's restricted privilege set load-bearing for the caller's guarantee, and does its output stand alone? Both answer no here, so the agent is folded away instead of kept.
