# Plan-mode subagent model resolution: measured, and why the pin holds

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** Subagent dispatches carrying a `model: sonnet` frontmatter pin (`Explore`, `staff-*`, `ciso-reviewer`) resolve to Opus almost every time the parent session is in harness plan mode, and almost never otherwise. The first explanation reached for was "plan mode breaks the pin" (H1). A second, simpler explanation surfaced on review: this repo's session default is `opusplan` — Opus while planning, Sonnet during execution (`claude/.claude/scripts/claude-auto.sh`) — so in the existing corpus, "plan mode active" and "parent's own effective model is Opus" were almost perfectly correlated. Under that confound, a rival hypothesis fits the same data equally well: dispatches simply inherit whatever the parent currently is, full stop, and no pin was ever the operative factor (H2).

**Question.** Does a `model:` pin genuinely override the parent's own model for these dispatches, or were the "reliable outside plan mode" numbers an artifact of the parent almost always being Sonnet there?

**Short answer.** H1. The pin is genuinely honored independent of the parent's own model; the mechanism that breaks it is keyed specifically to `permissionMode == "plan"`, not to which model the parent happens to be running.

## The falsification test

H1 and H2 make different predictions for one bucket the original measurement never isolated: non-plan-mode dispatches where the parent was itself anchored to Opus (`--model opus`, or an Opus-anchored auto-mode session). H1 predicts these still resolve to Sonnet (the pin holds regardless of parent). H2 predicts they resolve to Opus (dispatches just inherit the parent).

**Method.** Scanned the personal-account transcript corpus for `staff-*`/`ciso-reviewer` dispatches where `permissionMode != "plan"` at dispatch time AND the parent's own dispatching turn (`message.model` on the assistant turn that invoked the `Task`/`Agent` tool) read an Opus model ID. 1,658 total `staff-*`/`ciso-reviewer` dispatches were scanned to find this bucket.

**Result.** 178 dispatches matched — 22 distinct sessions across 8 projects, all direct (`spawnDepth: 1`) dispatches from the top-level interactive session. **178/178 (100%) resolved to `claude-sonnet-5`**, matching the pin, not the Opus-anchored parent. This falsifies H2: if dispatches simply inherited the parent's current model, an Opus-anchored non-plan-mode parent should have produced Opus-resolved subagents, and it produced none.

**Mirror counter-example.** One plan-mode `ciso-reviewer` dispatch had a parent turn running on `claude-sonnet-5` — not Opus — yet still resolved to `claude-opus-5`. Plan mode forced Opus even when the parent itself was on Sonnet. Together with the 178/178 result, this confirms the override is gated on `permissionMode`, not on the parent's own model in either direction.

Scripts and raw per-dispatch output for this pass live outside the repo (session scratchpad, not committed) — the reproducible claim is the classification behavior above, not a re-runnable corpus snapshot; the corpus itself grows every session the way `edit-format`'s and `targeted-read-discipline`'s case studies note for their own point-in-time counts.

## What this doesn't resolve

The falsification test confirms *that* the pin is honored independent of parent model and *that* the break is plan-mode-specific. It does not confirm *why* — no primary-source doc names a plan-mode-specific override for a same-named user/project agent's `model:` field. Claude Code's own sub-agents doc ([code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents)) describes a distinct built-in `Plan` subagent used specifically for plan-mode research, whose model "inherits from the main conversation" by design, with no per-repo override documented for it — but that page also states, unscoped, that a same-named override of `Explore` "keeps its own `model` field," with no plan-mode carve-out. The docs are consistent with plan mode routing research work through `Plan`'s inheritance-only path regardless of which agent name was requested; they don't say so directly, and this case study doesn't claim more than the falsification test actually measured.

## Rejected mitigations

Two candidate fixes were checked and rejected, not left untested:

- **Move plan-mode discovery to run after `ExitPlanMode`.** Not available: `ExitPlanMode`'s own tool description states it can only be invoked once the plan file is already fully written ("Only use this tool ... when you have finished writing your plan to the plan file"), so plan-mode discovery can't be deferred to a post-approval step without abandoning "explore before presenting a plan" as a workflow.
- **`CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1`.** Real and documented ("To remove only the built-in `Explore` and `Plan` subagents... Claude reads and explores files directly instead of delegating to them") but removes subagent delegation for research entirely rather than fixing its model — the parent's own already-Opus turns would do that work inline, a cost regression relative to even a mis-tiered subagent.

No instruction-layer mitigation is known as of this writing. The two real levers are revisiting the `opusplan` session default (see `docs/cost-levers-considered.md`'s `pin-explore-to-sonnet.md` register entry) or keeping agent-initiated planning out of harness plan mode entirely — both are follow-up decisions, not made here.
