# Plan — fix the worktree-dispatch branch-naming flaw

## Context

While dispatching an implementation agent for a recent fix, the agent was
spawned with `isolation: "worktree"`. The harness created the worktree on a
harness-generated branch name (`worktree-agent-a24e94a4a5378c81f`) instead of a
meaningful slug. The `branch-creation` skill — which prescribes
`<TICKET-ID>/<topic-slug>` or `<topic-slug>` names — never ran, because the
branch is created by the harness, not by the agent.

A memory note (`feedback_isolation_worktree_branch_naming.md`) was written to
patch around this. The user rejected that: *"Something is fundamentally
flawed here."* The memory was both **misrouted** (a rule any contributor should
follow belongs in contributor instructions, not per-user auto-memory) and a
**band-aid** over a deeper workflow mismatch.

**Root cause.** `isolation: "worktree"` is an *ephemeral-isolation* primitive,
not a *feature-branch* primitive. Its job is to isolate an agent's filesystem
changes from the parent; the harness names the branch arbitrarily and (per the
Agent tool contract) auto-cleans the worktree if the agent makes no changes.
That is correct behavior for throwaway/parallel work. It is the **wrong
primitive** for PR-bound implementation work, which needs a meaningful,
persistent branch name.

The guidance conflates the two. `require-worktree-for-git-writes.sh` and
`require-worktree-for-file-writes.sh` deny messages both list "spawn an agent
with `isolation: worktree`" as a remediation; README's "Working inside a
worktree" section (line 287) says agents with `isolation: worktree` "create
their own worktrees automatically" with no caveat. A reader satisfying worktree
enforcement reaches for `isolation: "worktree"` and silently loses branch
naming. Evidence it recurs: the repo currently has **six** leftover
`worktree-agent-<hash>` local branches.

**The fix is guidance, not a hook.** A hook cannot synthesize a good branch
name — picking a slug requires knowing the work's topic, which only the parent
knows at dispatch time. This is a judgment call made when dispatching, so it
belongs in contributor instructions (CLAUDE.md). A hook that merely flagged
`worktree-agent-*` branches after the fact would be another reactive band-aid.

## Fix — three changes

### Change 1 — delete the misrouted memory

- Delete `~/.claude/projects/.../memory/feedback_isolation_worktree_branch_naming.md`.
- Remove its index line from `MEMORY.md` (line 9):
  `- [isolation:worktree auto-generates branch name](feedback_isolation_worktree_branch_naming.md) — …`

These are auto-memory files, not in the git repo — plain `rm` + `Edit`, no
worktree needed.

### Change 2 — Agent Briefing rule in the global CLAUDE.md

File: `claude/.claude/CLAUDE.md`, "## Agent Briefing" section (currently 3
bullets). Add one bullet:

> - `isolation: "worktree"` is an **ephemeral-isolation** primitive, not a feature-branch primitive. The harness creates the worktree on a harness-generated branch name (`worktree-agent-<hash>`), so the `branch-creation` skill never runs. Use it only for work that will NOT become a named PR branch — parallel exploration, reviewer/check-runner agents, throwaway spikes. For PR-bound implementation work, create the worktree yourself first: pick a slug per the `branch-creation` skill, run `git worktree add .claude/worktrees/<slug> -b <slug>` (allowed on the main tree even under worktree enforcement), then dispatch the agent **without** `isolation: "worktree"`, naming that worktree path as its working directory.

This is consistent with the section's existing first bullet (which says *not*
to name a working directory for `isolation: worktree` agents — a non-isolation
agent pointed at a pre-made worktree is the complementary case).

### Change 3 — disambiguate the README

File: `README.md`, "Working inside a worktree" section, line 287. Current:

> Agents spawned with `isolation: worktree` create their own worktrees under `.claude/worktrees/` automatically.

New:

> Agents spawned with `isolation: worktree` create their own worktrees under `.claude/worktrees/` automatically — on a harness-generated branch name (`worktree-agent-<hash>`). That auto-naming is fine for ephemeral, non-PR work (parallel exploration, reviewer agents). For PR-bound work that needs a meaningful branch name, create the worktree yourself with `git worktree add .claude/worktrees/<slug> -b <slug>` first, then dispatch the agent into that path.

## Deliberately not doing

- **No hook.** Branch naming needs the work topic; only the dispatching parent
  has it. Rejected above.
- **No change to the two worktree hook deny messages.** They list remediation
  *options*; choosing the right one is the parent's judgment, now documented in
  CLAUDE.md. Editing fail-closed security-hook prose for a guidance nuance is
  disproportionate scope.
- **No change to the `branch-creation` skill.** It is already correct; the
  problem was never its content, only that nothing routed to it.

## Files

- `~/.claude/projects/.../memory/feedback_isolation_worktree_branch_naming.md` — delete (Change 1)
- `~/.claude/projects/.../memory/MEMORY.md` — remove line 9 (Change 1)
- `claude/.claude/CLAUDE.md` — new Agent Briefing bullet (Change 2)
- `README.md` — disambiguate line 287 (Change 3)

Changes 2 and 3 are repo files under worktree enforcement — make them on a
linked worktree with a meaningful slug (e.g. `worktree-dispatch-branch-naming`),
which is itself the workflow this plan prescribes.

## Housekeeping (mention, do not bundle)

Six stale `worktree-agent-<hash>` local branches exist. After this PR, clean
them with `cleanup-merged-branches` (per existing practice) — not in this PR's
diff.

## Verification

1. `git diff` — confirm only the CLAUDE.md bullet and the README line changed.
2. The memory deletion is outside the repo; confirm `MEMORY.md` has 31 index
   lines (was 32) and the file is gone.
3. `/ai-instruction-and-memory-files` review on the CLAUDE.md edit (per repo
   rule for CLAUDE.md changes). No `/skill-review` — no SKILL.md is staged.
4. `/code-review` on the branch.
5. `pytest claude/.claude/` and `ruff check claude/.claude/` via the
   `check-runner` agent with the worktree path as working directory — no test
   asserts CLAUDE.md/README prose, so this is a regression check only; expect
   green.
