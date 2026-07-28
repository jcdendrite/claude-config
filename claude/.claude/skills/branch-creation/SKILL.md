---
name: branch-creation
description: >
  Name a feature branch, start it clean, and anchor the session in its
  worktree.
  TRIGGER when: creating a new feature branch, picking a branch name,
  deciding whether to use a tracker-provided default branch name,
  entering or re-entering a branch's worktree, starting work in a
  resumed session.
  DO NOT TRIGGER when: routine work on an existing branch already
  anchored in its worktree, syncing a branch with the default (use
  `git-feature-branch-sync` instead), or force-pushing.
user-invocable: false
---

# Branch Creation

## Naming

Branch names follow `<TICKET-ID>/<topic-slug>` when the project has a
ticket system (Linear, Jira, GitHub Issues, etc.). When there's no
ticket system, use `<topic-slug>` alone.

- **Topic slug** — lowercase, hyphen-separated, ≤50 chars. Describes
  the change, not the motivation.
- **Ticket ID** — as the tracker emits it (e.g., `GH-1234`, `T-42`,
  `issue-99`).
- **Don't accept tracker defaults** like the `<user>/<ticket>-<slug>`
  form some tools emit (Linear's `gitBranchName` field is a common
  source). The user prefix signals branch ownership, not work type,
  and isn't standard practice.

Examples:

- With ticket: `GH-1234/checkout-redesign`, `T-42/verify-jwt-hardening`
- Without ticket: `verify-jwt-hardening`, `checkout-redesign`

### Why no `<type>/` prefix?

Industry guides often recommend `<type>/<ticket>-<topic>` — e.g.,
`feature/GH-1234-checkout`. That pattern adds signal when:

- The team uses branch-prefix-keyed automation (changelog generation,
  CI gating by branch type, auto-deploy paths).
- Branches are scanned without opening the associated ticket.

When neither applies — the ticket system already carries the work
type as a label, and no automation keys off the branch prefix — the
type prefix duplicates the tracker metadata without adding signal.

If a specific project needs prefix-keyed automation, add the type
prefix at that project's CLAUDE.md level. The global default stays
prefix-free.

## Branch from a fresh default

Create feature branches from the current tip of the default branch,
not from whatever happens to be checked out:

    git checkout main && git pull --ff-only
    git checkout -b <new-branch>

Branching off a stale or unrelated branch carries baggage (extra
commits, dirty state) that doesn't belong in the PR. Repos that route
branch creation through a worktree helper (`EnterWorktree`, custom
scripts) usually handle this automatically — verify once, then trust.

If the repo's default branch is `master`, `trunk`, `develop`, or
anything other than `main`, substitute accordingly. Check with
`git symbolic-ref refs/remotes/origin/HEAD`.

## Anchor the session in the worktree

When the branch lives in a linked worktree, creating the worktree is
only half the step — the session must also enter it:

    git worktree add .claude/worktrees/<branch> -b <branch>

then `EnterWorktree{path: ".claude/worktrees/<branch>"}`.

Creating a worktree does not move the session into it. A session's
working directory has an anchor that a stray `cd` is reset back to,
and that anchor defaults to the main checkout. Until the session
enters the worktree, an excursion that leaves the tree lands back on
the default branch — and an excursion through a symlink that resolves
*inside* the main checkout is kept with no notice at all, which is the
silent case. Entering the worktree moves the reset target to it, so
both kinds of excursion self-correct.

This matters beyond the current shell: a dispatched subagent starts in
the parent's working directory at dispatch time, so an unanchored
parent hands its children the main checkout. Naming a working
directory in the agent's prompt does not change where its commands
run.

The anchor is session-scoped. A resumed session starts unanchored, so
re-enter the worktree before running commands or dispatching
subagents.

## Plan files go on the implementation branch

If this branch is for work that has an associated plan file
(`.claude/plans/<name>.md`), commit the plan to this feature branch.
Don't open a standalone plan-only branch that can merge independently
of the implementation — plan and code ship as one PR. Reviewing the
plan as a PR diff on the feature branch is fine; merging it
separately from the implementation it plans isn't.
