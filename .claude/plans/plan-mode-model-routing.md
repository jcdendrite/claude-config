# Correct subagent model-routing documentation for plan mode

## Context

This repo's documentation (`docs/auto-mode.md`, `claude/.claude/CLAUDE.md`,
`subagent-delegation/SKILL.md`, `Explore.md`, `cost-levers-considered.md`)
asserts or implies that Sonnet-pinned or Sonnet-declared subagent dispatches
are reliably honored — in particular that `Explore`'s repo-owned `model:
sonnet` override is "not a request the platform can decline." A root-cause
investigation this session measured the opposite: during harness **plan
mode**, subagent dispatches resolve to Opus regardless of frontmatter pin or
an explicit `model` param on the `Agent` dispatch, while outside plan mode
resolution is ~100% reliable. Primary-source verification (`verify-sources`,
this session) found Anthropic's own sub-agents doc *does* mention plan mode
as a variable, via a distinct built-in `Plan` subagent whose model
"inherits from the main conversation" by design, with no per-repo override
documented — unlike `Explore`, whose same-named-override "keeps its own
`model` field" claim carries no plan-mode carve-out. The docs corroborate
that a plan-mode-specific, non-overridable model-inheritance path exists;
they do not explain why an explicitly-dispatched `Explore` exhibits it too.
That specific mechanism remains corroborated only by this session's own
measurement, not by the primary source — see G1's citation below. The goal
is to correct
every falsified claim with a citation to the measurement that falsifies it,
fix one confirmed independent inconsistency, and record — rather than paper
over — that no working mitigation exists for the plan-mode case today.

This is documentation-and-one-small-consistency-fix, not a behavior change:
the investigation's one candidate behavior change (moving `plan-it`'s
discovery fan-out out of plan mode) is confirmed unavailable by the harness's
own `ExitPlanMode` contract (see Approach), so it is documented as a dead end
rather than implemented.

## Approach

**Root problem:** five files assert or imply plan-mode subagent dispatches
honor a Sonnet pin/param; measurement (this session) shows 0% honored for
`Explore`'s override and 0/70 for explicit per-dispatch params, both in plan
mode specifically, while non-plan-mode dispatches are ~100% reliable by the
same measurement. anchors: root

**Givens:**

| # | Given | Reason |
|---|---|---|
| G1 | Plan mode forcing Opus onto subagent dispatches is platform behavior this repo's instruction layer cannot override | Confirmed by direct measurement of the dispatch/resolution mechanism, which lives in the harness, not in repo-owned files — [verified: root-cause investigation this session, personal-account corpus, Explore n=127 (92 opus in plan mode / 95 plan-mode dispatches = 97%, 0/32 opus outside plan mode), staff-*/ciso-reviewer n=1,619 (340/341 = 99.7% opus in plan mode, ~2/1,231 outside), 500 plan-mode dispatches overall (489 opus, including 70 carrying an explicit `model: sonnet` param — 0/70 honored)]. Partially corroborated by primary source — [verified: `code.claude.com/docs/en/sub-agents`, read this session — "Plan — A research agent used during plan mode to gather context before presenting a plan... Model: inherits from the main conversation... When you're in plan mode and Claude needs to understand your codebase, it delegates research to the Plan subagent"; the same page states, unscoped, "A user or project subagent named `Explore` overrides the built-in and keeps its own `model` field"]. The primary source confirms a plan-mode-specific, non-overridable model-inheritance path exists (the `Plan` agent's designed behavior); it does not confirm why `Explore`'s own, separately-documented override loses effect under that same path — that connection is [unverified] beyond this session's own dispatch-count measurement. **[verified, added 2026-08-12]** The `opusplan` default (Opus while planning, Sonnet during execution) confounds the "outside plan mode" contrast with parent model, not just plan-mode status. A falsification test resolved it: 178 non-plan-mode `staff-*`/`ciso-reviewer` dispatches from Opus-anchored parents, 178/178 still resolved to Sonnet, matching the pin — see `docs/case-studies/plan-mode-model-resolution.md`. The pin is confirmed honored independent of parent model; the override is specifically gated on `permissionMode == "plan"`. |
| G2 | `ExitPlanMode` cannot be invoked before a plan file is fully written, so plan-mode discovery cannot be moved to a post-`ExitPlanMode` step without abandoning "explore before presenting a plan" as a workflow | [verified: `ExitPlanMode` tool description, this session — "Only use this tool ... when you have finished writing your plan to the plan file and are ready for user approval"; "This tool does NOT take the plan content as a parameter - it will read the plan from the file you wrote"] |

**Mechanisms:**

- **M1 — Correct `docs/auto-mode.md`'s "Subagent delegation under auto mode" section** (lines ~138-185). Add the plan-mode fact as the actual driver of the leak; correct three specific claims that measurement falsifies (see Critical files for exact line-level changes); do not weaken the section's existing auto-mode content, which measurement corroborates (near-zero leak in literal auto mode). anchors: row G1
- **M2 — Correct `claude/.claude/CLAUDE.md`'s Model Routing section** (~line 78). Scope the "Explore needs no explicit model" and "helps at any honor rate above zero" claims to non-plan-mode dispatch, where they are true; state the plan-mode exception plainly. anchors: row G1
- **M3 — Correct `subagent-delegation/SKILL.md`** (~line 130-131). Replace "no model needed" with a short correction that cross-references `CLAUDE.md`'s corrected Model Routing section for the measurement and the plan-mode exception, rather than restating it — the file is already 172/200 lines, and the two locations don't meet the skill-review duplication test (CLAUDE.md is already always-loaded in the same session where this line fires, so there's no distinct-load-path justification for a second full copy). State plainly here: outside plan mode the pin holds and no param is needed; inside plan mode, see CLAUDE.md — neither the pin nor an explicit param currently has any effect, and this file's readers are not the audience for the "no known mitigation" note in full. Explicitly do not recommend adding `model: sonnet` here as if it were a fix — measurement shows that specific substitution is a no-op. anchors: row G1
- **M4 — Correct `Explore.md`** (~lines 11-18). Narrow the claim that the override "replaces the built-in's `model` field explicitly" to scope it correctly: true for `description`/`tools` (confirmed this session — the custom description renders in this session's agent registry), false for `model` specifically in plan mode. This is a scoping addition to the existing sentence, not a deletion — the claim stays correct for non-plan-mode dispatch and needs a qualifier, not removal. anchors: row G1
- **M8 — Correct `claude/.claude/skills/agent-review/SKILL.md` item 7** ("model field discipline"). It states "`Explore`'s pin... is the one exception that is enforced, since it's a repo-owned override rather than a per-dispatch request" — the same claim M4 falsifies, surfaced during `/plan-review`'s own dispatch of `agent-review` against this plan, not present in the original file scan. Scope it the same way as M4. anchors: row G1
- **M5 — Amend (not rewrite) `cost-levers-considered.md`'s `pin-explore-to-sonnet.md` register entry** (~line 79). Its recorded rationale for dropping the opusplan-default flip claims the Explore override "needs none of" the complications that blocked the flip — false for the ~75% of Explore dispatches happening in plan mode. Per this repo's Axis 3 discipline on preserved content, add a dated follow-up note below the existing row rather than editing the original "Measured reason" cell in place — the original reasoning is a record of what was believed at decision time, not a live description to correct silently. anchors: row G1
- **M6 — Fix `plan-it/SKILL.md:35` and `plan-review/SKILL.md:42`** to name an explicit `model: sonnet` on their `general-purpose` dispatches, matching `CLAUDE.md`'s "always dispatch `general-purpose` with an explicit `model`" rule. This is a no-op for the majority case (dispatched from plan mode, where the param is measured 0/70 honored) but a real fix for the two documented non-plan-mode paths: `plan-it`'s own "if the harness-provided plan path write fails" recovery flow, and any `/plan-review` invocation against an already-written plan file outside plan mode. Framed as instruction-file consistency with a partial-coverage caveat stated explicitly, not as a cost lever. anchors: row G1
- **No heavier mechanism was considered or adopted.** The one candidate behavior change (restructuring `plan-it` to run discovery after `ExitPlanMode`) is ruled out by G2, not implemented via a heavier substitute (e.g., a hook attempting to detect and redirect plan-mode discovery) — a hook can't reliably distinguish legitimate plan-mode exploration from the fan-out at issue, and would be exactly this repo's own "compounding defensive layers" tell if it tried to compensate for a platform behavior the instruction layer has no lever over. This is recorded as a dead end (M7 below), not routed around.
- **M7 — Document the dead end explicitly**, in `docs/auto-mode.md`'s corrected section (part of M1): plan mode's Opus-forcing has no known instruction-layer mitigation as of this plan; the only real levers are revisiting the `opusplan` default (out of scope, see below) or accepting the cost as intrinsic to plan mode's explore-before-committing value. One control was checked and rejected, not left untested: `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1` — [verified: `code.claude.com/docs/en/sub-agents`, this session — "To remove only the built-in `Explore` and `Plan` subagents, set `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1`. Claude reads and explores files directly instead of delegating to them."] — removes subagent delegation for research entirely rather than fixing its model; the parent's own (already-Opus, by design) turns would then do that work inline, which is a cost regression relative to even a mis-tiered subagent, not a mitigation. anchors: row G2

**Revision, 2026-08-12 — M2/M3/M6 shape changed after PR review:** the engineer flagged the mode-conditional caveat text M2/M3/M6 originally shipped ("honored outside plan mode, not honored during it") as itself a wrong-foundation tell, independent of the duplication problem M1-M8 already fixed — the two branches of that conditional prescribe the same action (pass `model: sonnet` either way) keyed on a fact the reader can't observe (a session can't detect its own permission mode). Dispatched to Opus for judgment per this repo's Model Routing rule; verdict: replace the mode-conditional framing everywhere with one unconditional rule — "a `model:` pin or dispatch param is a request, never a guarantee; pass one anyway" — which is shorter, requires no unobservable predicate, and holds under either resolution hypothesis (see the new G1 caveat above). `CLAUDE.md`, `docs/auto-mode.md`, `plan-it/SKILL.md`, `plan-review/SKILL.md`, and `subagent-delegation/SKILL.md` were re-edited to this shape; M2/M3/M6's original mechanism text above describes the first-round implementation, not the current file content — read the files themselves as the source of truth for what shipped. `Explore.md` and `agent-review/SKILL.md` (M4/M8) needed no further change — both already deferred to `CLAUDE.md` rather than restating a mode caveat.

**Alternatives considered and set aside:**

- *Add `model: sonnet` restatements to `code-review`/`ready-for-review`/`plan-review`'s reviewer-dispatch instructions* (the original candidate from earlier in this investigation) — set aside because those run outside plan mode, where resolution is already ~100% reliable; the fix would be dead prose justified by a claim the data doesn't support.
- *A hook that warns or blocks large `general-purpose` fan-outs during plan mode* — set aside per M7 above: no reliable signal distinguishes intended discovery from anything else a plan-mode session does, and building one to route around a platform behavior is the over-powered-primitive/compounding-layers pattern this repo's own CLAUDE.md warns against.

## Critical files

- `docs/auto-mode.md` — lines ~138-185, section "## Subagent delegation under auto mode". Add a "plan mode" subsection with the measured numbers (G1); correct the "nontrivial share of sampled cases" (~145-148), "not a request the platform can decline" (~155-158), and the `staff-*`/`ciso-reviewer` table row ("resolved unreliably", ~166) to state the mode-conditional reality; add the M7 dead-end note.
- `claude/.claude/CLAUDE.md` — Model Routing section, ~line 78. Scope "needs no explicit `model` passed" and "helps at any honor rate above zero" to non-plan-mode dispatch; state the plan-mode exception with the measured honor rate (0/70).
- `claude/.claude/skills/subagent-delegation/SKILL.md` — ~line 130-131 ("(no `model` needed — pinned via `Explore.md`)"). Correct per M3 — cross-reference `CLAUDE.md`, don't restate.
- `claude/.claude/agents/Explore.md` — ~lines 11-18. Narrow the "replaces the built-in's `model` field explicitly" claim per M4 (additive scoping clause, not a deletion).
- `claude/.claude/skills/agent-review/SKILL.md` — item 7 ("model field discipline", ~40 lines into the checklist section). Same correction as M4, per M8.
- `docs/cost-levers-considered.md` — ~line 79, `pin-explore-to-sonnet.md` entry. Add a dated amendment note per M5; do not edit the original cell.
- `claude/.claude/skills/plan-it/SKILL.md` — line 35. Add explicit `model: sonnet` to the `general-purpose` dispatch instruction, with the partial-coverage caveat from M6.
- `claude/.claude/skills/plan-review/SKILL.md` — line 42. Same fix as above.

No new files. No test suite changes — this is prose-only plus a two-line instruction addition with no executable behavior to unit test.

## Verification

- Re-read each corrected file after editing to confirm every changed claim carries an inline citation to the measurement that grounds it (dispatch counts, honor rate, sample size) — per this repo's "Ground every choice" rule on quantitative claims in docs.
- Grep `docs/`, `claude/.claude/CLAUDE.md`, and the touched SKILL.md files for the specific falsified phrases ("not a request the platform can decline", "nontrivial share of sampled cases", "no model needed", "helps at any honor rate above zero" applied unscoped) to confirm none survive unscoped.
- Confirm `docs/auto-mode.md`'s existing auto-mode content (the parts measurement corroborates) is unchanged — diff review, not a rewrite.
- `skill-review` on `subagent-delegation/SKILL.md`, `plan-it/SKILL.md`, `plan-review/SKILL.md` per this repo's own self-review rule for skill edits (`.claude/rules/skill-and-agent-self-review.md`).
- `agent-review` on `Explore.md` per the same rule.
- Standard `/code-review` pass before commit.

## Out of scope

- **Reopening the `opusplan` repo-wide session default.** Its prior rejection (`cost-levers-considered.md`) is partly falsified by this session's measurement (see M5), but flipping it is a bigger, more disruptive decision — it collides with `guard-settings-session-keys.sh`'s hard block on Claude-Code-authored commits touching that key, and affects every session's cost profile, not just plan-mode dispatches. Name it as a follow-up in the PR description; do not decide it here.
- **Testing whether `CLAUDE_CODE_SUBAGENT_MODEL` behaves differently from plan mode's Opus-forcing.** Flagged as an untested gap by the root-cause investigation. A measurement task, not a documentation fix — out of scope for this plan.
- **The separate "Opus code-read delegation discipline" cost driver** (parent sessions doing Opus code-reads inline instead of dispatching at all, measured this session at ~70-72% dispatchable/Sonnet-tier-repriceable). Related in spirit — both are delegation-correctness issues touching some of the same files — but is a distinct mechanism (whether to dispatch at all vs. what model a dispatch resolves to) requiring its own trigger-condition analysis of `subagent-delegation` SKILL.md's dispatch conditions. Not folded into this plan's fixes to avoid conflating two different problems in one diff; a natural follow-up plan given the file overlap.
