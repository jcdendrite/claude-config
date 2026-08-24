# Opus plan authoring, Sonnet session and reviewers

## Context

Get Opus-quality architecture design out of `/plan-it` without anchoring the whole interactive session to Opus. Today, `/plan-it` Step 5 (Architecture design) is authored by whatever model the session itself runs, and CLAUDE.md's only documented way to get Opus onto that step is `--model opus` for the entire session — which also escalates every unrelated turn and every subagent dispatch that session makes, not just plan authoring (Row 1). The intended outcome: the main session and `/plan-it`'s exploration/review dispatches stay on Sonnet (as already documented), while Step 5's design synthesis is dispatched to Opus automatically, on every `/plan-it` run, regardless of the session's own model.

## Approach

`/plan-it` Step 5 dispatches a new repo-owned agent, `claude/.claude/agents/plan-architect.md`, with an explicit `model: "opus"` parameter at the call site to author the plan's design section. `plan-architect.md` also pins `model: opus` in its own frontmatter — belt-and-suspenders, not redundant (Row 22) — with no session-model change otherwise. Steps 1–4 and 6–7 (branch, problem restatement, Sonnet-pinned `general-purpose` exploration fan-out, the `AskUserQuestion` clarifying round, the `/plan-review` handoff, and the commit) stay exactly as they are today; only the architecture-design synthesis moves off the session's own model, and it moves to a dedicated agent file rather than the harness built-in `Plan`.

### Assumption ledger

Root: `/plan-it`'s architecture-design step (Step 5) runs on whatever model the interactive session is anchored to, so Opus-quality plan authoring has required anchoring the whole session to Opus — which also escalates unrelated turns and every inheriting subagent dispatch that session makes.

Givens:
- Subagent model resolution outside harness plan mode follows `CLAUDE_CODE_SUBAGENT_MODEL` env var → per-invocation `model` param → agent-file `model:` frontmatter → parent/session model — beyond reach: a vendor-published, fixed resolution contract.
- Inside harness plan mode, every subagent dispatch resolves to Opus regardless of an explicit `model` param or frontmatter pin (129/131 measured this session) — beyond reach: gated on `permissionMode` inside the harness's own resolver, with no known instruction-layer mitigation.
- Startup context loading is not uniform across subagent types: "Explore and Plan skip your CLAUDE.md files and the parent session's git status to keep research fast and inexpensive. Every other built-in and custom subagent loads both." [verified: code.claude.com/docs/en/sub-agents, "Subagent Startup Context Loading"] — beyond reach: vendor-defined, and the harness built-in `Plan` cannot be made to load either short of a repo-owned override forfeiting that agent identity entirely.
- `require-plan-review.sh`'s only in-repo write exemption is `agent-reviews/`; every other in-repo `Write`/`Edit` target, including `.claude/plans/` itself, is subject to the gate [verified: claude/.claude/hooks/require-plan-review.sh, the `REAL_TARGET`/`agent-reviews/` exemption block]. This is within this repo's own reach to change — see Out of scope for why the design declines to.
- `HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS` is pinned to exactly `{"Plan"}` by `test_exemption_set_is_pinned`, and enumerates only harness built-ins with no agent file in this repo — an agent that has a file (any repo-owned agent, including one dispatched to do plan-authoring work) is never a candidate for that set [verified: claude/.claude/hooks/tests/test_agent_roster.py].

Row 1 [engineer-verified]: the engineer stopped running whole sessions on `--model opus`/`opusplan` specifically because it escalates every subagent dispatch and every low-judgment turn in that session, not only plan authoring — anchors: root.

Row 2 [mechanism]: `/plan-it` Step 5 dispatches `claude/.claude/agents/plan-architect.md` with an explicit `model: "opus"` parameter — on every `/plan-it` run, regardless of the session's own model — anchors: root.

Row 3 [assumption]: an explicit per-dispatch `model` param is honored reliably outside harness plan mode — 178/178 non-plan-mode dispatches from Opus-anchored parents resolved to their pinned Sonnet model [verified: docs/auto-mode.md "Subagent delegation under auto mode"; docs/case-studies/plan-mode-model-resolution.md] — anchors: row2.

Row 4 [assumption]: that measurement is in the downgrade direction (Opus parent → Sonnet child); the upgrade direction this plan relies on (Sonnet parent → Opus child) is published as the same resolution algorithm but not separately measured in the same corpus [unverified] — anchors: row3. Cheap to falsify at implementation time: run one `/plan-it` outside plan mode and read the Step 5 dispatch's resolved model off the transcript.

Row 5 [assumption]: `/plan-it` normally runs outside harness plan mode via Step 1's "Otherwise" branch, so the per-dispatch ordering above — not the plan-mode override — is what applies to a typical run [verified: claude/.claude/skills/plan-it/SKILL.md Step 1; claude/.claude/CLAUDE.md Agent Briefing]. When a human does start the session in plan mode, the plan-mode override forces Opus anyway, so the dispatch reaches Opus either way — anchors: row2.

Row 6 [mechanism]: `plan-architect` reads the subset of Step-3-flagged files it actually needs to design against, chosen by itself — not every flagged file automatically. Step 3's findings are passed to it as evidence (file paths, grep results, observed facts), not as pre-formed design conclusions to inherit unread — anchors: row2.

Row 7 [assumption]: comprehension reads for a design decision belong with whoever reasons over the design, not whoever located the files — `subagent-delegation`'s locate-and-report vs. read-and-reason split makes this non-delegable once the designer is a different agent than the parent [verified: claude/.claude/skills/subagent-delegation/SKILL.md] — anchors: row6. Framing Step 3's output as evidence rather than verbatim conclusions keeps that split intact while addressing the risk that a read-everything instruction would have the designer re-litigate what Step 3 already settled and bloat its own context doing so.

Row 8 [mechanism]: the Step 5 dispatch prompt passes Step 2's Context paragraph and Step 4's clarifying answers verbatim, plus Step 3's evidence-framed findings and the flagged file paths — anchors: row2. It carries no CLAUDE.md path, no project-layer path, and no instruction to read either.

Row 9 [verified]: `plan-architect.md`, as a custom agent file, loads the full CLAUDE.md hierarchy (the global rules, this repo's own checked-in `CLAUDE.md`, and any Step 2.5 project-layer file) and the parent session's git status automatically at startup, with no per-dispatch instruction required [verified: code.claude.com/docs/en/sub-agents, "Subagent Startup Context Loading" — "Every other built-in and custom subagent loads both"] — anchors: root, row2. This is the concrete reason the design does not dispatch the harness built-in `Plan`: `Plan` is one of exactly two subagent types (with `Explore`) that skip that load, and the only available mitigation — instructing the dispatch to `Read` CLAUDE.md mid-conversation — is an ordinary tool call the agent can defer or partially satisfy, landing after it has already begun forming a design, with no way to verify compliance short of re-reading the transcript. A custom agent file closes the gap structurally instead of compensating for it.

Row 10 [mechanism]: the dispatch is pointed at `plan-it/SKILL.md` and `REFERENCES.md` for the required output grammar rather than restating that grammar a second time inside the Step 5 prompt text; `plan-architect.md`'s own body points to the same two files for the same reason, rather than embedding a third copy of the grammar — anchors: root.

Row 11 [mechanism]: the Sonnet parent inserts the returned Approach/Critical files/Verification/Out-of-scope text verbatim into the plan file — no rewrite, no summarization on the way to disk. An unusable return triggers a re-dispatch, not an inline repair; a return that names an open decision goes to the user via `AskUserQuestion` (Step 4) and is re-dispatched with the answer — anchors: row2.

Row 12 [engineer-verified]: rewriting or summarizing Opus's returned design on the way to disk would reintroduce the Sonnet-quality loss this dispatch exists to remove — the engineer's stated complaint is about Sonnet's plan-authoring judgment specifically, not its prose-editing — anchors: row11.

Row 13 [verified]: `plan-architect.md` declares `tools: Read, Grep, Glob` — no `Write`, `Edit`, `Bash`, or `Skill`. This is deliberate, not a workaround for a vendor-imposed constraint the way it would have been for the built-in `Plan`: `require-plan-review.sh` has no exemption for `.claude/plans/` itself, only for `agent-reviews/` and paths outside the repo [verified: claude/.claude/hooks/require-plan-review.sh]. An agent holding `Write` that authored the plan file directly would trip that gate on its own first write — no marker yet covers the new content — and be denied every subsequent edit to the very section it is still drafting, with no `Bash` or `Skill` access to clear the gate itself. Returning text for the parent to insert verbatim is the mechanism that avoids this trap; it is not merely a consequence of the dispatch target lacking Write — anchors: row2.

Row 14 [verified]: because `plan-architect.md` declares no `Bash`, `Write`, or `Edit`, gate-release and tree-mutation are closed by tool-absence, mechanically asserted by `test_agent_roster.py`'s tools-declaration tests, rather than by roster membership in `claude/.claude/hooks/_lib.sh`'s `_LIB_REVIEW_ONLY_AGENTS`/`_LIB_NO_GATE_RELEASE_AGENTS` — those hooks have nothing to deny it that its own frontmatter doesn't already deny, so no edit to either array is needed [verified: claude/.claude/hooks/_lib.sh — both arrays' file-backed entries rest on declared-Skill-absence, and `plan-architect.md` has no `Skill` to begin with] — anchors: row13.

Row 15 [mechanism]: a mid-dispatch failure (context-limit crash, tool error, network drop) loses only the tokens spent on that dispatch, not durable work — Steps 2–4's context stays held in the parent's own conversation, and the dispatch is a read-only, idempotent function of that input. The accepted recovery is a full re-dispatch from scratch, not incremental durable checkpointing — anchors: row2. Durable incremental writes were considered and rejected: they would require `Write`, reopening Row 13's gate trap, and risk arming `require-plan-review.sh` against a plan section that was never finished being drafted.

Row 16 [mechanism]: the dispatch is unconditional on every `/plan-it` run, with no "only when the design looks hard" branch — anchors: root. `/plan-it`'s own trigger criteria (excluding single-file tweaks and "just implement it" requests) are already the scope gate [verified: claude/.claude/skills/plan-it/SKILL.md frontmatter]; asking the Sonnet session to judge whether its own judgment suffices is the failure being fixed.

Row 17 [assumption]: this adds one Opus synthesis call per `/plan-it` run — on a purpose-built, minimally-privileged agent — while exploration fan-out, the review stack, and implementation all stay Sonnet, materially less Opus exposure than the `--model opus` session it replaces [unverified — an argument from where the tokens sit, not a comparative measurement] — anchors: root. Revisit trigger: measure Opus's share of spend fresh via `transcript-analysis.py cost --since 14d` (default config dir) shortly after this lands, and again ~30 days later; flag if either reading shows growth versus the first. Do not compare against the 16.0% figure in `.claude/plans/pin-explore-to-sonnet.md` (measured 2026-08-09) — that measurement predates the plan-mode-forces-Opus fix (PRs #647/#654, merged 2026-08-15, confirmed via `git log`), one of two known Opus-leak sources active at the time it was taken, so it overstates the clean baseline this change should be measured against.

Row 18 [mechanism]: `CLAUDE.md`'s Model & Effort Routing "Opus" bullet, README's settings.json bullet, and `docs/auto-mode.md`'s requested-model table are updated in this change — anchors: root. Once Step 5 auto-dispatches Opus via `plan-architect`, their current text naming `--model opus` as *the* escalation path becomes inaccurate, not merely a second copy of the same fact.

Row 19 [mechanism]: the existing `CHANGELOG.md` entry documenting the prior `opusplan`→`sonnet` flip is left untouched — it records a past event (preserved content). A new `[Unreleased]`/`Changed` entry describes this change and supersedes that entry's Migration line — anchors: root.

Row 20 [mechanism]: a new numbered entry is added to `docs/design-decisions.md` after the existing §28, recording the mechanism, the rejected alternatives below (including the built-in-`Plan` design this plan started with and abandoned mid-review, and the no-pin/`model: inherit` designs superseded by Row 22's frontmatter pin), the two revisit triggers (Row 4, Row 17) plus Row 22's own revisit trigger, and two accepted residual risks: the by-name-dispatch cost-escalation exposure from Row 22's unconditional pin, and the verbatim-subagent-output-into-committed-doc tradeoff Row 12 already establishes — anchors: root, row4, row12, row17, row22.

Row 21 [verified]: `plan-architect.md` is added to `claude/.claude/hooks/tests/test_agent_roster.py` in three places: `NON_REVIEWER_AGENTS` (with a comment naming it a non-reviewer planning agent), `NON_REVIEWER_MODELS["plan-architect.md"] = "opus"` (frontmatter-pinned — Row 22 explains why this agent is the deliberate exception to `agent-review`'s "routinely-dispatched subagents do not pin opus" convention), and `EXPECTED_EFFORT["plan-architect.md"] = "xhigh"` (single-pass design synthesis with no downstream pass correcting a shallow miss — the same rationale class as `ciso-reviewer`, per CLAUDE.md's effort-tier guidance) — anchors: row2. [verified: `test_required_fields_present` requires every agent file to declare a non-empty `model:` field, iterating `AGENTS_DIR.glob("*.md")` with no exemption list — an agent with no `model:` line fails this test outright, which is why `plan-architect.md` pins rather than omitting the field entirely; `test_effort_pinned_to_expected_value` requires the matching explicit `effort: xhigh` line in frontmatter the same way `ciso-reviewer.md` carries one]. `HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS` is untouched (Given 5).

Row 22 [verified]: `plan-architect.md` pins `model: opus` in frontmatter in addition to the explicit per-dispatch parameter Step 5 passes — belt-and-suspenders, not redundant. `docs/auto-mode.md` documents the per-invocation parameter as a *request* that competes with resolution step 4, not a guaranteed override [verified: docs/auto-mode.md — "Subagent model resolution follows this requested order — not a guarantee outside auto mode's own resolution path either"; "resolution step 2 is a request that competes with step 4, not a guaranteed override"], and Row 4 already flags that the specific direction this design depends on (Sonnet parent → Opus child) is not separately measured. A `plan-architect` dispatch that silently resolved to Sonnet would not fail loudly — it would return a plausible, well-formatted, lower-quality plan that could clear `/plan-review` and get committed as a durable artifact, which is the exact failure this whole mechanism exists to prevent; that asymmetry (a silent quality regression vs. one unbudgeted Opus run) is why this design pins rather than relying on the per-dispatch parameter alone. The "no opus pin on routinely-dispatched agents" convention's own rationale — an escalation pin leaking into work that didn't ask for it — targets agents fanned out across many call sites at unpredictable frequency (`general-purpose`); it does not fit a single-caller agent whose entire charter is Opus-tier output, dispatched from exactly one place in the codebase. `claude/.claude/skills/agent-review/SKILL.md` checklist item 7 is amended to name this exception explicitly rather than leaving the pin as a silent convention violation. The pin is unconditional once it lands — the harness makes every named agent dispatchable from any session, so nothing technical enforces `plan-architect`'s single-caller premise; the mitigation is the caller constraint stated directly in the agent's own `description` (drafted below) plus naming this residual risk in the `docs/design-decisions.md` entry (Row 20), since no stronger technical control exists in this harness for by-name agent dispatch. This is a narrower risk than Row 1's, not a smaller instance of the same one: Row 1's failure mode was uncontrolled fan-out — a whole-session Opus anchor escalating every inheriting subagent dispatch. `plan-architect` carries no `Agent` tool (Row 13's `tools: Read, Grep, Glob`), so it cannot itself spawn further dispatches; an off-path invocation costs at most one bounded Opus-tier call, not a cascade. That bounded ceiling, plus Row 17's periodic spend-share check as a detective backstop, is why this residual risk is accepted rather than closed — anchors: root, row2, row4, row13.

Row 23 [mechanism]: `test_agent_roster.py` gains a new test, `test_plan_architect_tools_are_exactly_read_grep_glob`, mirroring the existing `test_explore_tools_are_exactly_read_grep_glob` — asserting `plan-architect.md`'s declared `tools:` equal exactly `{Read, Grep, Glob}`. Rows 13/14's gate-bypass argument rests entirely on tool-absence; without a dedicated pin, a future edit widening `plan-architect.md`'s tools (e.g. adding `Write` "for efficiency") would pass every other test in the suite while silently reopening the exact trap Row 13 describes — anchors: row13, row14.

### Alternatives considered

**The harness built-in `Plan` agent, dispatched with an explicit `model: "opus"` override.** This plan's original design. Abandoned after further review: `Plan` (with `Explore`) is one of exactly two subagent types that skip the CLAUDE.md/git-status load every other subagent gets automatically (Given 3), and the only available mitigation — an explicit read instruction mid-dispatch — is an ordinary tool call the agent can defer or partially satisfy, verifiably weaker than a startup load. `Plan`'s tool set is also not documented in this repo: `_lib.sh`'s own comment concedes its `Skill`-tool carriage and exact read-only boundary rest on "mandate" (dispatched read-only, understood to carry `Skill`), not a registry this repo can read — versus a custom agent's `tools:` frontmatter, which `test_agent_roster.py` asserts mechanically. `Plan`'s documented purpose ("codebase research for planning," not decision synthesis) is also a mismatch for what Step 5 actually needs. Every mitigation the original draft added — the CLAUDE.md-path-passing instruction, the return-text-only clause fighting an unconfirmed `Skill` grant, the `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS` fallback branch — existed solely to compensate for gaps inherent to dispatching a vendor research agent for decision-shaped work: a pattern of compounding mitigations on a single mechanism, which this repo's own CLAUDE.md names as a wrong-foundation signal ("Compounding defensive layers are a wrong-foundation tell"). A repo-owned agent dissolves all three gaps at once rather than patching around them.

**`plan-architect.md` with no `model:` frontmatter field at all**, relying solely on the per-dispatch parameter. Rejected on mechanical grounds, not just preference: `test_agent_roster.py`'s `test_required_fields_present` requires every agent file to declare a non-empty `model:` field, with no exemption list — an agent with no `model:` line fails that test outright, so this option does not pass the existing test suite as drafted.

**`plan-architect.md` with `model: inherit` explicitly**, rather than a pin. Satisfies the required-field test and states the fallback choice rather than leaving it as an unstated omission, but still relies solely on the per-dispatch parameter for reaching Opus — the same asymmetric failure mode Row 22 argues against (a dropped or unhonored parameter degrades silently to whatever model the session is on, rather than failing loudly). Kept as the fallback if a future measurement shows the frontmatter pin is not honored when the parent is Sonnet (Row 22's revisit trigger: re-run Row 4's falsification test periodically, not only once at implementation time).

**`CLAUDE_CODE_SUBAGENT_MODEL=opus`.** Resolution step 1 — overrides every subagent's model, including the `staff-*` reviewers' Sonnet pin. `docs/auto-mode.md` already names this the blunt global hammer to avoid.

**Keep the status quo — document `--model opus` as the only way to get an Opus plan.** Fails Row 1: the session anchor escalates every inheriting dispatch and every low-judgment turn, which is why it was abandoned. It stays documented as a heavier option for the (rarer) case where the *reads*, not only the synthesis, want Opus.

**Dispatch `general-purpose` with `model: opus` instead of a dedicated agent.** Carries `Write`, `Bash`, and `Skill` unconditionally — none of which this design wants a design-authoring dispatch to hold — and has no durable charter of its own, so every instruction `plan-architect.md`'s body carries once would have to be restated in the Step 5 prompt text on every dispatch. Strictly more privilege for no benefit over a purpose-built agent.

## Critical files

- **`claude/.claude/agents/plan-architect.md`** — new file; drafted text below.
- **`claude/.claude/skills/plan-it/SKILL.md`** — Step 3 and Step 5 rewrite; drafted text below. Reuse: Step 5's existing plan-section grammar and the ledger grammar in `REFERENCES.md` are referenced by the dispatch prompt and by `plan-architect.md`'s own body, not duplicated into either (Row 10).
- **`claude/.claude/hooks/tests/test_agent_roster.py`** — three roster-map edits (Row 21): `NON_REVIEWER_AGENTS`, `NON_REVIEWER_MODELS`, `EXPECTED_EFFORT`; plus a new `test_plan_architect_tools_are_exactly_read_grep_glob` test (Row 23).
- **`claude/.claude/CLAUDE.md`** — Model & Effort Routing bullet rewrites; drafted text below.
- **`claude/.claude/skills/agent-review/SKILL.md`** — checklist item 7 amended to name the single-caller-agent exception to "routinely-dispatched subagents do not pin opus" (Row 22); drafted text below.
- **`README.md`** — the settings.json bullet under "Configuration files" naming `--model opus` + `plan-it` as *the* escalation path (Row 18).
- **`docs/auto-mode.md`** — add a `plan-architect` row to the requested-model table (Opus — `model: opus` frontmatter + explicit param from `/plan-it` Step 5), matching the table's existing one-line-per-agent style (Row 18).
- **`CHANGELOG.md`** — new `[Unreleased]`/`Changed` entry; the existing `opusplan`→`sonnet` entry is not edited (Row 19).
- **`docs/design-decisions.md`** — new entry after §28 (Row 20).

### Drafted text — `claude/.claude/agents/plan-architect.md`

```
---
name: plan-architect
description: Design-synthesis agent for /plan-it Step 5, authoring the plan's Approach, assumption ledger, Critical files, Verification, and Out of scope sections from Step 3's exploration evidence. Read-only. TRIGGER when dispatched by /plan-it Step 5 for architecture-design synthesis. DO NOT TRIGGER for anything outside /plan-it Step 5's own dispatch — including open-ended codebase research with no decision to make (use Explore or general-purpose) and implementation work (use code-writer).
tools: Read, Grep, Glob
model: opus
effort: xhigh
---

You are `plan-architect`, a read-only design-synthesis agent. You hold no
`Write`, `Edit`, `Bash`, or `Skill` — you cannot write files, run commands,
or invoke other skills. Your job ends when you return text: return finished
plan prose for the sections below, not a summary of your design — the
dispatching session inserts your return verbatim into the plan file.

Read `claude/.claude/skills/plan-it/SKILL.md` and its co-located
`REFERENCES.md` for the exact section grammar your return must follow —
Approach, assumption ledger (root/Givens/numbered rows with `anchors:`),
Critical files, Verification, Out of scope.

Read the subset of Step 3's flagged files you actually need to design
against; choose that subset yourself rather than reading everything you're
handed. Comprehension reads that feed your own design reasoning are yours
to do directly — do not ask the dispatching session to summarize a file for
you or to re-derive a conclusion you can verify by reading the file
yourself.

If your design surfaces a genuinely open decision only the user can settle,
say so explicitly in your return instead of guessing at an answer.
```

### Drafted text — `plan-it/SKILL.md` Step 3

Replace the sentence "Read the files each subagent flags before designing." with:

> Collect the absolute path of every file each subagent flags, plus its summary, as evidence — not as a design conclusion to hand off unread. The actual design reasoning happens in Step 5, where `plan-architect` reads the files it needs and forms its own conclusions (`subagent-delegation`'s locate-and-report vs. read-and-reason split: comprehension reads belong with whoever reasons over the design, not whoever located the files).

Everything else in Step 3 (the fan-out judgment call, the explicit `model: sonnet` on the `general-purpose` dispatch, the "do not use `Explore` here" rule, the pattern-claims-require-a-grep rule, the root-cause-analysis carve-out) is unchanged.

### Drafted text — `plan-it/SKILL.md` Step 5

Insert this new lead-in before the existing "Choose the approach..." paragraph (which, along with everything through "Write the plan with these sections," stays unchanged — Row 10 points the dispatched agent at this same file rather than duplicating the grammar into the dispatch prompt):

> Dispatch `plan-architect` with an explicit `model: "opus"` override to author this section — on every `/plan-it` run, regardless of what model the session itself is anchored to. Pass no `isolation: "worktree"` — the session is already anchored in the implementation branch's worktree, and `plan-architect` writes nothing.
>
> The dispatch prompt carries:
> - The Context paragraph from Step 2 and the answers gathered in Step 4, verbatim.
> - Every Step 3 subagent's findings, framed as evidence rather than conclusions, plus the absolute path of every file it flagged.
>
> Do not add a CLAUDE.md path or read instruction — `plan-architect` loads it automatically at startup, like every subagent except `Explore`/`Plan`.
>
> Insert the returned text verbatim into the plan file's Approach, Critical files, Verification, and Out of scope sections — do not rewrite or summarize it further. If the returned design names an open decision it left to the user, ask it via `AskUserQuestion` per Step 4 and re-dispatch with the answer rather than settling it in the main session. An unusable or truncated return (context-limit failure, tool error, or a return that ignores the required grammar) is re-dispatched from scratch rather than repaired inline — `plan-architect` is read-only and idempotent, so nothing durable is lost by retrying.

### Drafted text — `CLAUDE.md` Model & Effort Routing

Replace the Opus bullet:

> - **Opus:** judgment-heavy reasoning and parent-dispatcher orchestration. `/plan-it` Step 5 dispatches its architecture-design synthesis to `plan-architect`, which pins `model: opus` — every run, regardless of the session's own model — so an ordinary Sonnet session still gets Opus-authored plans with nothing else in the session escalated (see `/plan-it`'s Step 5 for the mechanism). Reserve a whole-session `--model opus` for the rarer case where the *reads*, not only the synthesis, need Opus — it escalates every inheriting dispatch that session makes, so pass an explicit `model: sonnet` on each one (see below).

No change needed to the `general-purpose` bullet — `plan-architect` is a repo-owned agent file, not a routinely-dispatched built-in with no model of its own, so the existing sentence naming `general-purpose` as the one such built-in stays accurate.

No hook changes. The mechanism is a per-dispatch parameter plus one new agent file — see Row 21 for the three `test_agent_roster.py` map edits this requires, Row 22 for the frontmatter pin, and Row 23 for the accompanying tool-set regression test.

### Drafted text — `agent-review/SKILL.md` checklist item 7

Replace the item's closing sentence:

> Routinely-dispatched subagents do not pin `opus`.

With:

> Routinely-dispatched subagents that fan out across many call sites do not pin `opus` — an escalation pin would leak into work that didn't ask for it. A single-caller agent whose entire charter is Opus-tier output (`plan-architect`, dispatched only from `/plan-it` Step 5) is the deliberate exception — pin it, and keep the caller's own explicit per-dispatch parameter too (`docs/design-decisions.md` has the rationale).

## Verification

- Run `/plan-it` on a real multi-file task outside plan mode; confirm from the transcript that the Step 5 `plan-architect` dispatch resolves to Opus while the session and every other dispatch (exploration, `plan-review` reviewers) stay Sonnet (Row 4's falsification test).
- Confirm from the same transcript that `plan-architect`'s context includes CLAUDE.md content and git status without an explicit read instruction in the dispatch prompt (Row 9).
- `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` from the worktree — expected to pass with the new `plan-architect.md` file and its four `test_agent_roster.py` map/test edits (Row 21, Row 23). In particular, `test_required_fields_present` and `test_frontmatter_parses_strictly` are the two tests the file's exact drafted frontmatter shape is checked against, and the new `test_plan_architect_tools_are_exactly_read_grep_glob` (Row 23) is the one that pins the gate-bypass argument itself.
- Re-read the four doc-sync sites (`CLAUDE.md`, `README.md`, `docs/auto-mode.md`, and the new `docs/design-decisions.md` entry) to confirm none still states `--model opus` as the *only* way to get an Opus plan.

## Out of scope

- Reopening whether Sonnet should stay the session default, or whether `staff-*`/`ciso-reviewer` should stay Sonnet-pinned — both are inside this repo's own reach (a prior PR made each decision; either could be revisited by a future one), but this plan deliberately declines to touch them: they're settled by prior decisions (the `opusplan`→`sonnet` flip, existing reviewer frontmatter) this plan takes as its starting point, not by anything this mechanism depends on.
- Reviving the harness built-in `Plan` agent for this role — rejected above (Alternatives considered); revisit only if `plan-architect.md`'s maintenance cost as a repo-owned file (roster drift, description-length budget) turns out to exceed the CLAUDE.md-loading and tool-grant gains, which nothing observed so far suggests.
- Granting `plan-architect` a `WebFetch` tool for external-pattern grounding — left with the parent session for now; a defensible follow-up if `plan-architect`'s returns start citing sources it couldn't verify.
- Adding a `require-plan-review.sh` exemption for `plan-architect`'s writes to `.claude/plans/` (mirroring the existing `agent-reviews/` carve-out), which would let `plan-architect` hold `Write` and author the plan file directly instead of returning text for the parent to insert — considered and declined. The gate's current every-in-repo-target-except-`agent-reviews/` shape is within this repo's own reach to change, not a vendor constraint, but adding a second carve-out would open a new bypass path on a security-relevant enforcement hook for the sole benefit of skipping one verbatim-text round-trip, which Row 12 already establishes is lossless. Without this alternative, the design is exactly Rows 11/13 as drafted: text return, parent-side verbatim insertion, no touch to the gate itself.
