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

**2026-08-14 follow-up:** `plan-mode-workflow-discipline.md` made the second
lever the fix — an advisory bullet in `claude/.claude/CLAUDE.md`'s Agent
Briefing plus `"EnterPlanMode"` in `permissions.deny` keep agent-initiated
planning out of harness plan mode entirely, since the override this case
study measured is keyed to `permissionMode`, not to any model setting. That
plan also flipped the `opusplan` session default to `sonnet`, but as a
coherence consequence of closing plan mode to the agent, not as an attempt
to fix this escalation directly — no model setting reaches a
`permissionMode`-gated override. See `docs/cost-levers-considered.md` for
how that flip differs from the `pin-explore-to-sonnet.md` register entry's
cost-lever framing of the same setting.

## 2026-08-14 follow-up: independent re-derivation, and a headless-measurement limitation

A committed, re-runnable harness (`evals/measure_subagent_model_resolution.py`) was built to settle this by controlled experiment rather than corpus inference. Its own decisive cells — a Sonnet-pinned `staff-backend-engineer` dispatched under `claude -p --permission-mode plan`, both from a Sonnet parent and an Opus parent, two repetitions each — resolved to the declared pin every time, no exceptions; `python evals/measure_subagent_model_resolution.py --run 2` (or `--run 4`) re-runs the same live cell, though — being a real API call — not guaranteed to land on an identical outcome each time. Taken alone this would read as H1 falsified. It is not: the harness measures a condition that turns out not to represent the mechanism this case study is about.

**Provenance note:** only the harness result above is a committed, re-runnable claim; the three findings below are separate, uncommitted, manually-run analyses against the same corpus (scripts and raw output live outside the repo, per the disclosure above) — the reproducible claim is each paragraph's classification methodology, not a re-runnable script.

**The original finding independently reconfirmed, at much higher confidence.** The `permissionMode` field is re-stamped throughout a session, and a reader that takes only a session's *first* stamp — the pattern already present in this repo's own `cmd_commit_gate` (`transcript-analysis.py`) — misattributes every dispatch after a mode change. A corrected re-scan (carrying the most recent preceding stamp forward, verified against the raw stamp-transition sequence of a sampled session) searched the same personal-account corpus for the decisive bucket directly: `staff-*`/`ciso-reviewer` dispatches with a Sonnet-declared pin, a Sonnet-family parent turn, and `permissionMode == "plan"` at dispatch time. **131 dispatches matched, not 1 — 129 resolved to Opus, 2 mixed**, spanning three repositories and Claude Code v2.1.219 through v2.1.227 with no version-dependent split. Substitution to the built-in `Plan` agent was checked and ruled out directly: `meta.json`'s `agentType` (harness-written, not repo-owned) and each dispatch's own tool usage matched the requested specialist, never `Plan`'s wider read-only set.

**An explicit `model` param provides no protection.** Twelve of the 131 dispatches already carried an explicit `model: sonnet` param before any repo instruction told dispatchers to do so universally — **12/12 still resolved to Opus.** One was verified directly against its raw transcript: a real `/plan-review` re-review dispatch in this repo, Claude Code v2.1.227, parent turn genuinely on `claude-sonnet-5`, explicit param present, plan mode active, Opus resolved anyway.

**Headless `--permission-mode plan` does not reproduce the escalation, at all — checked with a separate, uncommitted probe.** Beyond the committed harness's own synthetic single-dispatch cells, the actual mechanism — `/plan-review`'s own specialist fan-out, which this repo's `require-plan-review.sh` hook forces to run before `ExitPlanMode` and therefore executes while `permissionMode` is still `"plan"` — was replicated headlessly against a real migration-and-RLS-shaped plan, once with dispatchers passing an explicit `model` param and once explicitly told not to. **13 specialist dispatches across both runs, 13/13 resolved to the declared pin, 0 Opus.** This is a genuine, previously unverified gap this case study's own validity-threats list first flagged and did not yet resolve: `-p --permission-mode plan` is not equivalent to interactive Shift+Tab plan mode for this specific behavior. Any future measurement of this mechanism needs a genuine interactive session; a headless harness, however controlled, will read as a clean negative regardless of what interactive sessions actually do.

**Where this leaves the fix.** PR #631 documented this escalation with no known instruction-layer mitigation; PR #647 closed it the only way available — keeping agent-initiated planning out of harness plan mode entirely, since the override is gated on `permissionMode` and no model-routing change reaches it. The findings above corroborate that call rather than reopen it: the escalation is confirmed at more than two orders of magnitude larger sample than originally recorded, and the one candidate model-routing mitigation this repo had already started leaning on (an explicit `model` param on every dispatch) is now confirmed, not merely suspected, to do nothing against it.
