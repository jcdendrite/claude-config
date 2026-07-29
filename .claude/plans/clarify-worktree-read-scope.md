# Clarify: worktree/repo scope constrains writes, not reads

## Context

Agents sometimes decline to independently verify a cross-repo claim — reasoning
that a sibling repo or directory is "not in this worktree" or "not this repo"
and treating that as if it meant "not accessible to me" — when the target was
actually sitting reachable on the same filesystem the whole time, and a plain
`ls`/`find`/`grep`/`Read` would have settled the question. A transcript
surfaced this pattern clearly: an agent refused to re-verify a plan's
cross-repo survey, citing that the cited repos were "not present in this
worktree," until the user pointed out the repos were sitting in a sibling
directory on the same machine. Once corrected, a same-session re-check both
confirmed most of the original claim and surfaced a real regression the
original survey had missed — evidence that the reflexive deferral was
actively costing verification quality, not just being overly cautious.

Before treating this as an instruction-level bug, I searched this machine's
Claude Code transcript history (grep across `~/.claude/projects/*/*.jsonl`,
~860 files) for the same reasoning shape elsewhere. It recurs: 3 distinct
sessions across 3 different project pairings show an agent asserting
something is unverifiable/inaccessible ("I don't have that repo's config in
front of me," "outside this worktree's scope") when the target was in fact a
locally reachable sibling directory or fetchable via a tool already available
to it. One of the three went uncaught — a reviewer subagent's deferral was
accepted by its parent session without challenge. None of the three occurred
immediately after a hook-injected system-reminder; the trigger is situational
(cross-repo verification requested informally, mid-task) rather than tied to
any specific hook or to `plan-it`'s assumption-ledger citation format
specifically — only one of the three cases involved a plan citation at all.

Intended outcome: a standing, always-loaded instruction that closes the gap —
before declaring a claim unverifiable because its source isn't in the current
worktree/repo, check the filesystem for it.

## Approach

**Root problem:** agents conflate "not present in this git worktree/repo" (a
fact about write/git scope) with "not accessible on this filesystem" (a
claim about read scope), and default to deferral without attempting a check.

**Assumption ledger:**

- Row 1 [root]: the two scopes are genuinely different — worktree/repo
  boundaries are about where git operations and file writes land, never
  about what a `Read`/`Bash`/`Grep` call can reach.
- Row 2 [verified: grep of `claude/.claude/CLAUDE.md`, `branch-management/SKILL.md`,
  `nudge-worktree-anchor.sh`, `require-worktree-for-file-writes.sh`,
  `require-worktree-for-git-writes.sh`]: no existing instruction or hook in
  this repo says or implies that reads are scoped to the current
  worktree/repo. The four existing CLAUDE.md "Agent Briefing" worktree
  bullets and `branch-management`'s "Anchor the session" section are all
  about git-write/dispatch-cwd correctness; the two write-gating hooks
  (`require-worktree-for-*-writes.sh`) only gate `Edit`/`Write`/`MultiEdit`
  and git mutations, never `Read`. So the failure is not caused by any
  instruction telling agents to behave this way — it's an unaddressed gap
  (nothing tells agents *not* to over-generalize this way, and the
  structurally adjacent write-scope bullets exist in the exact section
  where a read-scope counterpart is missing).
- Row 3 [verified: transcript grep, ~860 files, 3 hits across 3 distinct
  projects, 0 tied to a hook-injected reminder, 1 of 3 tied to a
  plan-citation]: recurring, not a one-off, and not reducible to `plan-it`'s
  citation format — a `plan-it`-only fix would leave the 2/3 of cases that
  never touched a plan file unaddressed. anchors: root
- Row 4 [mechanism]: add one instruction bullet to `claude/.claude/CLAUDE.md`
  under "Agent Briefing" (the section that already holds the four
  git-write-scope worktree bullets) stating that worktree/repo scope
  constrains writes, not reads, and that a reachability check (`ls`/
  `find`/`grep`/`Read`) must be attempted before a cross-repo or
  cross-directory claim is treated as unverifiable. anchors: root
  - Lighter-primitive check: a mechanically-enforced hook was considered
    and rejected — a hook pattern-matches tool calls and deny conditions;
    it cannot semantically detect "the agent decided a claim is
    unverifiable" in free-text reasoning, and none of the three recorded
    occurrences fired any hook at all, so a hook can't reach the failure
    mode regardless of design effort.
  - A new standalone skill was considered and rejected — skills are for
    repeatable procedures a session actively triggers; this is a standing
    judgment default that should apply unconditionally, not something an
    agent has to remember to invoke by name.
  - Editing `nudge-worktree-anchor.sh`'s message was considered and
    rejected — that hook only fires when the session is anchored to the
    main tree with a linked worktree present; per Row 3, none of the three
    observed failures had that condition true, so the edit would not reach
    the actual trigger.
- Row 5 [assumption, unverified]: a single CLAUDE.md bullet is legible
  enough to change behavior in an ad hoc, no-plan-involved situation (2 of
  3 recorded cases), since CLAUDE.md loads every session regardless of
  whether a plan or `plan-it` is in play. This can't be verified
  mechanically — there's no hook to test prose-driven judgment against —
  so it stays a documented, load-bearing assumption rather than something
  this plan proves out. anchors: row4

**Alternative not pursued:** doing nothing and relying on user vigilance —
rejected because Row 3 shows the pattern already went uncaught once, and a
reviewer subagent (dispatched precisely to catch this class of gap) is the
one place a human is least likely to be watching in real time.

## Critical files

- `claude/.claude/CLAUDE.md` — add one bullet to the existing "Agent
  Briefing" section (after the four current worktree-scope bullets),
  verbatim:

  > **Worktree/repo scope constrains writes, not reads.** A worktree
  > (or a repo boundary generally) only fixes where git operations and
  > file writes land — it says nothing about what you may read. Before
  > treating a claim as unverifiable because its source is "not in this
  > worktree" or "not this repo," check the filesystem for it (`ls`,
  > `find`, `grep`, `Read`) — a sibling checkout or a nearby directory is
  > often reachable even when it isn't part of the current git tree.
  > "Not open in my current context" is not evidence of inaccessibility;
  > only defer to a human once an actual check has come up empty.

  No other section or file needs a change: `verify-sources` covers a
  different failure domain (external documentation claims, not filesystem
  reachability) and would be diluted by folding this in; `plan-it`'s
  assumption-ledger citation format is not the root cause per Row 3, so
  editing it would duplicate the fix without covering the cases that
  matter more (the 2/3 with no plan involved).

## Verification

This is a prose-only instruction change with no executable behavior to run
under a test suite. Verification is:

1. Read the new bullet in isolation (no other conversation context) and
   confirm it's self-contained per this repo's own documentation rule — no
   reference to this investigation, this PR, or any project name.
2. Diff it against the four existing worktree bullets in the same section
   to confirm it doesn't restate any of them (single source of truth) and
   fills the one gap they leave (read scope vs. their write/dispatch scope).
3. `/code-review` on the staged change, which dispatches
   `ai-instruction-and-memory-files` for the `CLAUDE.md` edit.

## Out of scope

- Editing `nudge-worktree-anchor.sh` or the two write-gating hooks — ruled
  out in the assumption ledger (Row 4); they don't fire in the observed
  failure cases.
- Editing `plan-it`'s assumption-ledger citation guidance — ruled out (Row
  3); the pattern isn't citation-format-specific.
- Editing `verify-sources` — different failure domain (external-doc claims,
  not filesystem reachability).
- Retroactively fixing the one historical session where a reviewer
  subagent's deferral went uncaught — that session is closed; nothing to
  change there beyond the standing instruction going forward.
