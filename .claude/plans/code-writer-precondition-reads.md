# Plan: close code-writer's skill-loading reliability gap for test-authoring rules

## Context

**Goal:** make `code-writer` reliably pick up test-authoring conventions (specifically the "don't regex-parse SQL / runtime output in assertions" family) at the two points where it can act on them — *while writing* and *during its own self-review* — instead of leaving the catch to a parent `/code-review` round-trip.

**Why now.** The user instituted a routing rule (CLAUDE.md §Model Routing) sending delegated code-writing to `code-writer` (introduced 2026-05-19, #271), and suspected a regression: skills that guide code-writing might not reach `code-writer` because it can't invoke the `Skill` tool. Investigation refined this:

- **Not a Claude Code limitation, and not a recommendation against it.** Subagents *can* invoke skills if `Skill` is in their `tools:` allowlist (or `tools:` is omitted). All 10 agents in this repo declare explicit allowlists that exclude `Skill` — a deliberate least-privilege choice. `code-writer` instead **reads skill bodies by tilde path** (`code-writer.md:48` sql-query-conventions, `:53` test-conventions). Skills also don't *auto-trigger* inside a subagent, so granting the tool wouldn't reproduce main-session behavior anyway.
- **Empirical evidence (transcript-analysis, ~1,400 sessions).** The conditional Read on `code-writer.md:53` fires in only **39%** of test-touching `code-writer` dispatches in the current era (0% before the #375 pointer was wired). Regex-in-test-assertions roughly tripled when `code-writer` arrived (1.8% → 5.2%), receding to 3.1% after #375 but staying above baseline. The dominant offending shape is `expect(sql).toMatch(/.../)` — asserting against SQL/source text with regex.
- **The rule the user cares about** lives in a project-layer skill (`test-conventions-<project>/SKILL.md`) prohibiting regex to parse structured output (SQL, domain DSLs) and directing writers to the project's parser library and shared AST helpers instead. It loads only via global `test-conventions` Step 0 glob — i.e. only when something *reads* the `test-conventions` body. At 39% read reliability, it reaches the writer in a minority of test-writing runs.

**Intended outcome:** raise the write-time read reliability and add the missing self-review angle, so `code-writer` prevents the anti-pattern in its own context (cheaper than a parent review round-trip) and the constructive project-layer guidance (use the AST helpers) reaches the writer.

### How PR 386 changes the picture (and what it leaves open)

PR 386 ("guardrail for regex-parsing runtime output in test assertions") is the **complementary half** of this problem. It:
- Promotes the prohibition into **global `test-conventions` §9** (so it surfaces on *any* test-conventions read, across all projects).
- Adds **`code-review` items 9f** (runtime-output regex) **and 9g** (source-scanning — the `expect(sql).toMatch` shape), owned by `staff-sdet` in the ownership table.
- Mirrors into `test-evaluation` §4 and records grounding (Meszaros "Fragile Test"; SWE@Google "Don't Put Logic in Tests").

What 386 **fixes:** the *parent's* mandatory `/code-review` now catches both shapes reliably (it reads `code-review/SKILL.md` and dispatches `staff-sdet`, which itself reads `test-conventions` + any project layer). The global §9 rule reaches all projects, not only those with a project-layer skill.

What 386 **leaves open — the code-writer-specific paths:**
1. **Write-time prevention.** 386 relies on detect-at-review, not prevent-at-write. `code-writer`'s write-time read of `test-conventions` is still ~39% reliable, so §9 still misses the writer in most test-writing runs — and the *constructive* project-layer AST guidance only ever arrives via that read (9f/9g say "don't," not "here's how").
2. **Self-review.** `code-writer`'s self-review pass reads the `staff-*` files, **not** `code-review/SKILL.md`. 386 updates the `code-review` ownership table to name `staff-sdet` but **does not edit `staff-sdet.md`'s body**, so the new angle never reaches `code-writer`'s in-context self-review. That defeats the reason `code-writer` exists (catch review-class defects in its own context).

This plan targets exactly those two open paths. It is scoped to `code-writer`'s reliability (the fix target the user chose), not the main-thread surface — 386 already covers the parent/main path.

## Approach

Two coordinated edits to public, stowed agent files. Both reference `test-conventions` generically — the global skill body loads any project-specific layer via Step 0 glob at runtime.

**Edit 1 — `code-writer.md`: strengthen the write-time read from a buried conditional bullet to a prominent mandatory precondition.**
Today the test-conventions read is one clause inside a long baseline bullet (`:53`), conditioned on "when test code IS being written and the codebase already shows a test convention." Reframe it as a hard precondition: *before writing or modifying any test file, Read `~/.claude/skills/test-conventions/SKILL.md` first* (which runs its Step 0 glob and loads any project layer). Make it a precondition, not a judgment call, and surface it where the writer will hit it before writing — not folded into the implementation baseline.

**Keep two rules separate (do not overcorrect).** `code-writer.md:49–53` deliberately scopes the test-*shipping* rule ("ship a test with new behavior") to repos that already show a test convention. That conditioning is correct and must stay. The strengthened **read** precondition is a *different* rule: it fires whenever the writer is editing a test file at all, independent of the convention-exists check — reading global `test-conventions` is cheap and always valid, and the Step 0 glob simply finds no layer in a convention-less repo. Phrase the edit so the read becomes unconditional-on-test-writing while leaving the ship-a-test condition untouched.

*Rationale / lighter-primitive check.* Three mechanisms could raise reliability:
- **Prose gate (chosen).** Lightest; matches the existing Read-by-path design; no tool-surface or token-cost change. Evidence that prominence moves the needle: wiring the #375 pointer alone took the read rate 0% → 39%; a hard, prominently-placed precondition should push further. It is a reliability *improvement*, not a guarantee.
- **`skills:` preload frontmatter** — rejected: injects the full `test-conventions` body on *every* `code-writer` dispatch (token cost even when no test is written), and preloaded static content still wouldn't run Step 0's glob, so the project-layer AST guidance wouldn't load reliably anyway.
- **A PreToolUse hook blocking test-file writes until `test-conventions` was read** — rejected as the default: only deterministic option, but heaviest blast radius (fires for every stow user on every test write) and uncertain for subagent tool calls. Hold as the escalation if the prose gate's reliability proves insufficient on a follow-up transcript check.

**Edit 1b — structural sibling.** Per CLAUDE.md "audit structural siblings," `code-writer.md:48` (sql-query-conventions) has the *same* conditional-trigger weakness. Apply the same precondition strengthening to it in the same pass so the read-path SQL conventions are equally reliable. Same fix shape, two arms.

**Edit 2 — `staff-sdet.md`: add the brittle-assertion review angle** (regex/string-matching over runtime output *or* source text where a parser library / the production parse-validate function is correct), cross-referencing the relevant `test-conventions` §-anchor and `code-review` item-anchors that 386 introduces. This closes the self-review path: `code-writer` reads `staff-sdet.md` during self-review, so the angle then surfaces in its own context instead of only at the parent's `/code-review`. This is the propagation 386 omitted (it edited the `code-review` ownership table but not the reviewer body).

*Anchor verification.* PR 386 is still open; the §9 / 9f / 9g numbers above are from its current diff and could shift before merge. After 386 lands, read the merged `test-conventions/SKILL.md` and `code-review/SKILL.md` and cite the actual section/item numbers — do not hardcode `9f/9g` from this plan.

**Sequencing.** This plan depends on PR 386's anchors (§9 and the new code-review items) for Edit 2's citations. Land 386 first, then branch this work off the updated default tip. 386 touches neither `code-writer.md` nor `staff-sdet.md`, so there is no file conflict — only a citation dependency. On branch creation, move this plan file from the harness path into the repo's `.claude/plans/` so it ships in the implementation PR (per `branch-creation`'s "plan files go on the implementation branch" and plan-review B17).

## Critical files

- `claude/.claude/agents/code-writer.md` — strengthen `:53` (test-conventions) and `:48` (sql-query-conventions) from conditional bullets to mandatory write-time preconditions. **Reuse:** the existing tilde-path Read pattern and the Step 0-glob behavior already documented on `:53` — no new mechanism.
- `claude/.claude/agents/staff-sdet.md` — add one brittle-assertion review angle in the existing angle list (near the tautological/snapshot angles, `:30`/`:40`), citing `test-conventions` §9 and `code-review` 9f/9g by section number per the file's own citation convention (`:18`, `:76`). **Reuse:** the existing "cite by §N, and Read test-conventions first to ground the citation" convention already in `staff-sdet.md:18`.

## Verification

- **Agent review:** run `/agent-review` on both edited files (CLAUDE.md requires it for `claude/.claude/agents/*.md`; dispatcher-invoked, not hook-enforced). Address findings.
- **Behavioral spot-check:** re-read each edited agent body with the diff in mind — confirm the strengthened reads read as preconditions, not optional, and that `staff-sdet`'s new angle cites real §-anchors that exist post-386.
- **Suite:** `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/` (from main worktree root, or `../../../.venv/...` from a linked worktree).
- **Empirical follow-up (post-merge, deferred):** after these land and accrue usage, re-run the `transcript-analysis` mining (the same code-writer-reads-test-conventions ratio and the regex-in-tests era rate) to confirm the write-time read rate rises above 39% and the regex rate falls. If the read rate stays low, escalate to the PreToolUse-hook option.

## Out of scope

- Main-thread / parent-session regex authoring — PR 386 covers that path (global §9 + `/code-review` 9f/9g). This plan is deliberately scoped to `code-writer`'s two paths.
- Editing any project-layer `test-conventions-<project>` skill — those already state the rule and constructive guidance; the gap is delivery to the writer, not the rule's content.
- Adding `Skill` to any agent's `tools:` allowlist — rejected (over-powered; doesn't auto-trigger; invites nested agent spawning via skills like `code-review`).
- Changing the agent roster's least-privilege `tools:` convention.
