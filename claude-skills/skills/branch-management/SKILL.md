---
name: branch-management
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

Skip the `<type>/` prefix by default — it only earns its keep with
branch-prefix-keyed automation or ticket-free branch scanning; add it
via the project's own CLAUDE.md if either applies (see
REFERENCES.md).

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
only half the step — the session must also enter it, as the very next
call, before any other command touches the new path:

    git worktree add .claude/worktrees/<branch> -b <branch>

then `EnterWorktree{path: "<absolute path to the worktree>"}`.

Call `EnterWorktree` with an absolute path before any other command,
including read-only ones — it resolves relative paths against cwd (not
repo root) and has no idempotent "already there" case, so a prior `cd`
silently leaves the session unanchored, with none of `EnterWorktree`'s
other effects (subagent inheritance, `ExitWorktree` tracking).

Entering the worktree resets the session's cd-anchor to it; until
then, a stray `cd` out (including one through a symlink that resolves
inside the main checkout, which fails silently with no warning) snaps
back to the main checkout.

This matters beyond the current shell: a dispatched subagent starts in
the parent's working directory at dispatch time, so an unanchored
parent hands its children the main checkout. Naming a working
directory in the agent's prompt does not change where its commands
run.

Hold the anchor still for the life of any dispatch, read-only ones
included — the isolation check re-evaluates it for the whole dispatch,
not just at launch. Re-anchoring mid-dispatch denies the subagent's
Write/Edit calls outright, not redirected. Its Bash calls are denied
too, a bare `pwd` included, for the rest of the run. Finish anchoring
before the dispatch rather than during it; `isolation: worktree` is
not a substitute, since it changes what the agent reads — a checkout
at a committed ref, without the parent's uncommitted work
(`claude/.claude/CLAUDE.md` § "Agent Briefing").

The anchor is session-scoped. A resumed session starts unanchored, so
re-enter the worktree before running *any* other command or
dispatching subagents — a read-only lookup counts too, since it's the
`cd` that breaks the anchor described above.

## Crossing to a different branch mid-session

Worktree enforcement, where armed, denies `checkout`/`switch`/`stash`
against the main tree. It's opt-in, and it doesn't stop those same
commands inside a worktree you're already anchored in. Apply the
discipline either way: when the next task is on another branch and the
current tree holds uncommitted work, add a worktree instead of
switching in place. A linked worktree leaves the main checkout
untouched. Stashed work is easily left unpopped. `refs/stash` is
shared across worktrees, so an earlier stash still pops from inside
the new one.

When another session may be running against this repo, use a worktree
even when the destination branch matches current HEAD. State branch
and PR facts from the remote (`git fetch`, then `origin/<branch>` or
`gh pr view`), not from a local worktree snapshot — a sibling's
commit-then-cleanup can be captured mid-window.

## Plan files go on the implementation branch

If this branch is for work that has an associated plan file
(`.claude/plans/<name>.md`), commit the plan to this feature branch.
Don't open a standalone plan-only branch that can merge independently
of the implementation — plan and code ship as one PR. Reviewing the
plan as a PR diff on the feature branch is fine; merging it
separately from the implementation it plans isn't.
