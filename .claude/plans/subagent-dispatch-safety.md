# Subagent dispatch safety: stop-and-report on structural blockers, and don't batch dispatch with worktree anchoring

## Context

Fix two subagent-dispatch failure modes observed in a live session and drafted (but not sent) as Anthropic feedback, since both are claude-config-owned behaviors this repo's own instructions can correct rather than harness bugs to report externally.

**Bug 1 — overspawn on a structural blocker.** A dispatched `general-purpose` subagent hit a hard, non-transient blocker (every Bash call denied by the worktree-isolation guard because of a worktree-anchor mismatch). Instead of reporting that failure, it dispatched a child subagent to route around the block, which itself fanned out into 6 more subagents (one per account root), all hitting the identical blocker. Net cost: ~454k subagent tokens and 34+ tool calls, zero data gathered.

**Bug 2 — dispatching concurrently with worktree anchoring.** In one turn, a session called `Skill(branch-management)` and `Agent(general-purpose, no isolation, unrelated read-only work)` in the same parallel tool-call batch, before the worktree existed. The worktree was created and `EnterWorktree` fired while the already-dispatched agent was still running. Every subsequent Bash call in that agent — including a bare `pwd` — was denied permanently for the rest of its run, with the harness message "this session is isolated in the worktree \<path\>, but this command's working directory resolved to the shared checkout." `branch-management/SKILL.md` already documents an anchor-mismatch hazard, but scoped to Write/Edit only, and the hazard text lives inside the skill body — invisible until after the `Skill` call returns, i.e. after the concurrent dispatch already happened.

Intended outcome: a dispatched agent that hits a hard structural denial stops and reports instead of spawning children to retry it, and the CLAUDE.md guidance that already tells a session to anchor before dispatching PR-bound work is generalized so it also covers an unrelated agent dispatched in the same batch as a worktree-anchoring step — because the anchor-state check invalidates *any* concurrently-running agent's Bash calls, not just ones related to the new worktree.

## Approach

Both bugs are fixed with prose only: two new bullets in `claude/.claude/CLAUDE.md`'s **Agent Briefing** section, and one scope correction to a paragraph already in `claude/.claude/skills/branch-management/SKILL.md`. Bug 1 gets a rule that a denial rooted in state the child inherits is a stop, not a routing problem — with an explicit carve-out so it does not contradict the Safety section's existing "delegate a harness-blocked review skill to a `general-purpose` child" instruction. Bug 2 gets a rule that the worktree anchor must not move while any agent is running, stated in CLAUDE.md because that is the only surface read *before* a tool-call batch is composed; `branch-management`'s existing "hold the anchor still" paragraph is corrected from Write/Edit-only to cover Bash, which is what actually failed.

**Root:** A dispatched agent that hits a denial rooted in inherited session state has no repo-owned instruction telling it to stop, and the dispatching session has no instruction telling it not to invalidate a running agent by re-anchoring mid-flight — so both failures burn subagent tokens without producing data.

**Givens** (conditions this design treats as fixed, each beyond its reach):

- **G1** — The worktree-isolation Bash guard is harness-native; no script or hook in this repo implements or can intercept it. *Reason: another party (the harness vendor) owns it.* `[verified: docs/worktree-bash-guard.md:3-9]`
- **G2** — The guard's refusals do not reproduce on demand; a bisection that reproduced five triggers 2+ times each was followed by a live re-run finding zero refusals across seven shapes, with no identified config difference. *Reason: unexplained vendor-side behavior this repo could not isolate.* `[verified: docs/worktree-bash-guard.md:116-130]`
- **G3** — `general-purpose` is a harness built-in with no repo-owned agent file, so CLAUDE.md is the only repo surface that shapes its behavior. *Reason: the vendor owns the agent definition.* `[verified: directory listing of claude/.claude/agents/ — dispatching session's Step 3 evidence]`
- **G4** — `claude/.claude/CLAUDE.md` carries a hook-enforced 200-line cap; `check-claude-md-length.sh` denies `git commit` when a staged CLAUDE.md is both over the limit and longer than `HEAD`'s copy. *Reason: the threshold is vendor-documented and the gate is already committed policy, not this plan's to relitigate.* `[verified: claude/.claude/hooks/check-claude-md-length.sh:15-19, 76-81, 89]`

**Rows:**

1. `[mechanism]` Fix both bugs in `claude/.claude/CLAUDE.md`, not in an agent file or a skill body — G3 leaves no per-agent file for the agent that overspawned, and a skill body is not loaded by a subagent that never invokes it. — *anchors: root*
2. `[mechanism]` Prose, not a hook. G1 rules out patching the guard; for Bug 2 the trigger is a same-batch concurrency relationship between two tool calls, which a `PreToolUse` hook sees only one call at a time; for Bug 1 the discriminator is "is this denial inherited or a capability gap," a judgment call this repo already handles in CLAUDE.md prose. Two lighter primitives than a hook were checked and neither exists to reach for: there is no repo-owned agent file for `general-purpose` (G3), and no existing fan-out/recursion-depth mechanism to extend `[verified: dispatching session's Step 3 grep of claude/.claude/hooks/*.sh and claude/.claude/agents/*.md]`. — *anchors: root*
3. `[mechanism]` Bug 1 becomes a **new** Agent Briefing bullet rather than an extension of the existing "A prescribed dispatch is an authorized dispatch" bullet (`:77`), whose closing clause already governs self-originated fan-out. That bullet's subject is *authorization* ("may I dispatch?"); the new rule's subject is *what a denial means* ("can a child clear this?"). The agent that overspawned had already read `:77` and framed its fan-out as necessary rather than unprescribed, so folding the rule there puts it behind the wrong question. — *anchors: row1*
4. `[assumption]` A subagent starts in its dispatcher's working directory and inherits its permission mode, so a call denied for anchor mismatch or by a permission rule is denied identically in each child. `[verified: claude/.claude/skills/branch-management/SKILL.md:83-87; claude/.claude/skills/subagent-delegation/SKILL.md:60-64]` — these are this repo's own documented statements of harness behavior, corroborated by the incident's six children hitting one blocker, not a fresh harness experiment. — *anchors: row3*
5. `[mechanism]` The Bug 1 bullet is scoped by a discriminator — *does the child hold a capability you lack?* — not by an example list of denial sources. A list naming "a hook gate" would directly contradict Safety's marker bullet (`:129`), which instructs delegating a harness-blocked review skill to a `general-purpose` child precisely because that child carries the `Skill` tool. The bullet cross-references that bullet rather than restating it. — *anchors: row3*
6. `[assumption]` Bug 1's fix neither absorbs nor cross-references the Shipping "Stopping is still correct when the work is genuinely blocked" bullet (`:176`). That bullet carves an exception out of the autonomous-shipping directive above it and its three examples are all end-of-work conditions for a session about to ship; Bug 1 fires mid-work in an agent with nothing to ship. The single-source rule bars restating knowledge, not reaching a shared conclusion from a new premise — the premise here (a denial is inherited, so retrying through a child reproduces it) has no existing home. `[verified: claude/.claude/CLAUDE.md:171-176]` — *anchors: row3*
7. `[mechanism]` Bug 2's rule lives in CLAUDE.md, not only in `branch-management`. CLAUDE.md loads at session start; a skill body is visible only after the `Skill` call returns, which in the observed failure is after the batch containing the dispatch was already composed. — *anchors: root*
8. `[assumption]` CLAUDE.md `:87`'s clause "Anchor the parent session in that worktree before dispatching" stays as written despite partial overlap with the new Bug 2 bullet. It sits inside a step-by-step procedure for PR-bound work that must read end-to-end, which is the single-source rule's named exception (2), instructional prose that must stand alone. Its companion fact — a `Working directory:` prompt line does not override a child's cwd — is not restated by the new bullet. `[verified: claude/.claude/CLAUDE.md:7, 87]` — *anchors: row7*
9. `[mechanism]` `branch-management/SKILL.md:89-93` is corrected in place rather than extended with new guidance. Its scope claim ("write-capable dispatch," "Write/Edit calls") is factually narrow — a bare `pwd` was denied in the incident — and correcting a wrong scope is not new content. Adding the same-batch rule there too would create a second copy that drifts and would not reach the failing reader anyway (row 7). — *anchors: row7*
10. `[assumption]` The plan deliberately does **not** claim that launch-time pinning (`isolation: worktree` or an explicit cwd) exempts *Bash* calls from an anchor change, only Write/Edit as the existing text already asserts. `[unverified]` — the incident's agent carried neither pin, so it is silent on the pinned case, and G2 blocks resolving it by experiment. The prescribed wording below keeps the exemption attached to the Write/Edit clause where it was already asserted.
11. `[assumption]` `claude/.claude/CLAUDE.md` is 176 lines as read this session, leaving 24 lines of headroom under G4's cap. The two bullets below are 10 and 8 wrapped lines, landing at ~194. — *anchors: row1*
12. `[mechanism]` No test is added. Both fixes are model-behavior instructions with no static artifact shape to assert — unlike a naming or structural convention, there is nothing a test could read the repo and check. — *anchors: root*

**No open decision is left to the user.** Every call above is settled on evidence read this session.

### Prescribed text

**Insert into `claude/.claude/CLAUDE.md` §Agent Briefing.** Hard-wrap at ~72 columns to match the section's neighboring bullets (`:80-96`). Place both after the `isolation: "worktree"` bullet (`:87`) and before the "Script-first for multi-step Bash recipes" bullet (`:88`), so the anchor-related bullets sit together.

Bug 1:

```
- **Dispatching cannot clear a denial your child inherits.** A subagent
  starts in its dispatcher's working directory and permission mode, so
  a call denied over a worktree-anchor mismatch or a permission rule is
  denied identically in every child spawned to retry it, and re-running
  it with a varied argument varies the wrong thing. Report the denial
  verbatim to whoever dispatched you, name what you could not reach,
  and stop — only the dispatching session can change the state that
  caused it. Dispatch past a denial only when the child holds a
  capability you lack; Safety's marker bullet names the one documented
  case.
```

Bug 2:

```
- **Never move the worktree anchor while a dispatched agent is
  running.** The isolation check re-evaluates the session's anchor for
  the life of a dispatch, so an `EnterWorktree` firing mid-run denies
  every remaining Bash call in that agent — a bare `pwd` included — no
  matter how unrelated its work is to the new worktree. Pairing an
  agent dispatch with a `Skill(branch-management)` call in one parallel
  tool-call batch is the common shape: the skill's anchoring step lands
  while the agent is already running. Finish anchoring, then dispatch.
```

**Replace `claude/.claude/skills/branch-management/SKILL.md:89-93`** (the paragraph beginning "Hold the anchor still") with:

```
Hold the anchor still for the life of any dispatch, read-only ones
included — the isolation check re-evaluates it for the whole dispatch,
not just at launch. Re-anchoring mid-dispatch denies the subagent's
Write/Edit calls outright (not redirected) unless pinned at launch via
`isolation: worktree` or an explicit cwd. Its Bash calls are denied
too, a bare `pwd` included, for the rest of the run.
```

**Prose constraints on any deviation from the above.** Both files are durable contributor-facing docs under CLAUDE.md §Code Comments, Documentation, and Prose. Do not write the incident into either file — no token counts, no "surfaced during a live session," no "6 subagents," no "used to be scoped to Write/Edit." That rationale belongs in the commit message and PR body, and `.claude/rules/skill-and-agent-self-review.md` bars trigger-identity framing in skill bodies specifically. Each bullet states its non-obvious facts as separate sentences rather than one semicolon chain.

## Critical files

| Path | Change |
|---|---|
| `claude/.claude/CLAUDE.md` | Two new §Agent Briefing bullets, inserted between `:87` and `:88`, verbatim as prescribed above. Nothing else in the file changes — `:77`, `:87`, `:129`, and `:176` stay as written (rows 3, 6, 8). |
| `claude/.claude/skills/branch-management/SKILL.md` | Replace the paragraph at `:89-93` with the six-line version above. `+1` line on a 126-line file. |

**Reuse rather than restate.** Three existing sites already hold the mechanism and are cross-referenced instead of duplicated: Safety's marker bullet (`CLAUDE.md:129`) for the one legitimate dispatch-past-a-denial case, `branch-management`'s "This matters beyond the current shell" paragraph (`:83-87`) for subagent cwd inheritance, and `docs/worktree-bash-guard.md` for the guard itself. Add no new pointer to `docs/worktree-bash-guard.md` from either edited file — CLAUDE.md `:88-96` already carries one, and a second costs a line against G4's budget for no new information.

**Dispatch split: none.** One `code-writer` dispatch covers both files (`model: sonnet`), per `subagent-delegation`'s approved-plan default. Splitting would force the same anchor-inheritance background into two prompts, which the `plan-it` grammar names as a reason not to split. The dispatch prompt must say: apply the prescribed text verbatim; report any wording concern rather than revising, because the exact wording is the deliverable. The parent keeps everything in Verification below — `code-writer` cannot run review skills and is denied marker writes.

## Verification

Run in this order, all from the worktree root.

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped test command. `claude/.claude/CLAUDE.md` is a mapped path in its rule table (`GLOBAL_CLAUDE_MD`, `select-tests.py:115`), so it selects the implicated suites itself; do not widen to the full suite by hand.
2. `wc -l claude/.claude/CLAUDE.md` — must be ≤ 200. `check-claude-md-length.sh` denies `git commit` above that (G4). Expected ~194 from 176. If it lands above 196, tighten the bullets' line wrapping rather than cutting a fact.
3. `/skill-review` on the `branch-management/SKILL.md` diff — **hook-enforced**; `require-skill-review.sh` blocks `git commit` until the behavioral-equivalence marker is written (`.claude/rules/review-pipeline-dispatch.md`). Per `.claude/rules/skill-and-agent-self-review.md`, run a fresh allow/deny fixture pair, since the edit rewords a scope clause.
4. `/ai-instruction-and-memory-files` on the `CLAUDE.md` diff. Its specific job here: confirm the new Bug 1 bullet and Safety's marker bullet (`:129`) read as consistent rather than contradictory — that reconciliation is the single highest-risk aspect of this change.
5. `/code-review` on the full staged diff, then `/ready-for-review` before pushing.

**Do not add a behavioral reproduction step.** No acceptance criterion should require triggering the worktree Bash guard or staging a mid-dispatch re-anchor: G2 establishes the guard's refusals are not reliably reproducible, so such a step cannot reliably pass and a non-refusal would be misread as a fix. The change's correctness rests on the prose being accurate and internally consistent, which steps 3–5 check.

## Out of scope

- **A hook or any mechanical guard for either bug** — row 2. Whether a `PreToolUse` hook can even distinguish a subagent-originated `Agent` call from a parent-originated one is unknown to this plan and was not investigated; if that question is ever answered affirmatively, a fan-out-depth guard becomes a separate proposal, not a follow-on to this one.
- **Editing `CLAUDE.md:87`** (the `isolation: "worktree"` bullet) — row 8. Its "anchor before dispatching" clause overlaps the new Bug 2 bullet but is protected by the stand-alone-instruction exception, and rewriting it widens the diff on a high-blast-radius file for no factual gain.
- **Editing `CLAUDE.md:176`** (Shipping's "Stopping is still correct") — row 6. It states a different premise in a section whose local coherence depends on it.
- **Editing `subagent-delegation/SKILL.md:60-64`** ("No permission cost.") — it states a true and distinct fact (inheritance means no *extra permission cost*), and a `general-purpose` child never loads that skill, so a copy of the Bug 1 rule there would not reach the reader who needs it.
- **Copying either rule into `code-writer.md` or the `staff-*` agent files** — they all load CLAUDE.md at startup, so a per-agent copy is pure duplication.
- **A new test** — row 12. Flagged explicitly because this repo's habit is to land enforcement alongside a new convention; here there is no static artifact for a test to read.
- **An Anthropic bug report** — the dispatching session's framing already scoped both bugs as claude-config-owned behaviors, and `docs/worktree-bash-guard.md:146-150` independently places harness bug reports outside this repo's fix work.
