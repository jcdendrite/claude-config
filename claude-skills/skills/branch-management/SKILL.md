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
scripts) usually handle this automatically — verify once, then trust. A
branch for a linked worktree skips the `checkout`/`pull` commands above,
because `worktree-ensure.sh` (below) fetches and starts from the default
tip itself.

If the repo's default branch is `master`, `trunk`, `develop`, or
anything other than `main`, substitute accordingly.
`git symbolic-ref --short refs/remotes/origin/HEAD` prints the name
after its `origin/` prefix.

## Anchor the session in the worktree

When the branch lives in a linked worktree, creating the worktree is
only half the step — the session must also enter it. One script finds or
creates the worktree and prints the step to take next. It works the same
from the main checkout or from a linked worktree:

1. Branch names use only ASCII letters, digits, `.`, `_`, `/` and `-`.
   Rewrite a name outside that set before composing the call; never
   quote around it.
2. Run `~/.claude/scripts/worktree-ensure.sh '<branch>'`, typing the
   branch name in place of `<branch>` inside the single quotes. Set the
   Bash tool's `timeout` to the maximum its description allows, because
   a kill mid-checkout leaves a state only the engineer can clear.
3. Find the last line that starts with `next:`. The line just before it
   starts with `created `, `existing ` or `current `. Other lines come
   from git and the programs it runs, such as hooks.
   - On `created` or `existing`, make the `EnterWorktree{path: …}` call
     on the `next:` line your very next call.
   - On `current`, skip `EnterWorktree`. The working directory already
     is that worktree, and `EnterWorktree` refuses a target equal to it.
4. On failure, read the last line that starts with
   `worktree-ensure.sh: `. Its second field is the token.
   - `usage`: call the script with exactly one argument.
   - `bad-name`: rewrite the name within the set in step 1.
   - Exit 2 with no such line: the script most likely never ran, usually
     because the name held a `'`. Rewrite the name only if it held a
     character outside the set.
   - After any exit 2, correct the call and rerun once. A second exit 2,
     or a no-line exit 2 from a call that already had one argument and
     a name within the set, goes to the engineer with the exit status
     and stderr.
   - `in-main`: ask the engineer to switch the main checkout to another
     branch, then rerun.
   - Exit 127 with no such line: the script is missing. Tell the
     engineer to re-run `./install.sh`.
   - A worktree-gate denial when the name is `git` or ends in `/git`:
     rename the branch, because the gate reads such a name as a git
     command. `EnterWorktree`'s create mode is not a substitute, because
     it takes no start point.
   - Any other failure, including a run the tool killed with no
     `worktree-ensure.sh:` line: report that line, or the exit status,
     to the engineer, and do not rerun.
     - Commands that write are the engineer's to run, wherever they are
       suggested. Examples include creating or attaching a worktree,
       deleting a branch, unlocking, removing or pruning a record,
       deleting a lock file, and moving or removing a directory.
     - Read-only commands such as `git worktree list` are fine.
     - A rerun the engineer asks for writes nothing while the failed
       run's branch or worktree remains, and reports that state.
   - Never fall back to a hand-typed `git worktree add`.

A `cd` before `EnterWorktree` silently leaves the session unanchored,
with none of `EnterWorktree`'s other effects (subagent inheritance,
`ExitWorktree` tracking).

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
re-enter the worktree with the steps above before running *any* other
command or dispatching subagents — a read-only lookup counts too, since it's the
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
