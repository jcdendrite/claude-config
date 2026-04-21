---
name: git-feature-branch-sync
description: >
  Decision framework for keeping a feature branch up to date with main:
  when to rebase-and-force-push vs merge-main-in, how to force-push
  safely (`--force-with-lease` / `--force-if-includes` / plain `--force`),
  and when the "never force-push" instinct is correct vs overcautious.
  This is the global framework — per-client policy (squash-merge vs
  merge-commit, rebase-after-review rules, branch-protection specifics)
  lives in that repo's CLAUDE.md or a client-scoped skill.
  TRIGGER when: deciding how to integrate `main` into a feature branch,
  whether/how to force-push a feature branch, or picking between
  `--force-with-lease` and `--force-if-includes`.
  DO NOT TRIGGER when: routine work on a clean feature branch with no
  integration question, operations on `main` / `release/*` / protected
  branches (those are always protected — never force-push them), or
  mid-merge / mid-rebase / mid-cherry-pick state (use `git-state-safety`
  instead).
user-invocable: false
---

# Feature-Branch Sync: Rebase, Merge, and Force-Push

## The decision

Keeping a feature branch current with `main` has two shapes:

- **Rebase** — `git fetch origin && git rebase origin/main`. Replays your
  commits on top of current `main`. Linear history. Rewrites commit SHAs,
  so the next push must be a force-push.
- **Merge** — `git merge origin/main`. Creates a merge commit on the
  feature branch. Preserves SHAs. No force-push needed.

**Default: rebase on personal feature branches.** Switch to merge when any
*merge-required condition* below applies. Always defer to per-client
policy if it contradicts this default.

## Merge-required conditions

Any one of these → merge-main, do not rebase:

- **Another active committer** on the branch. Rebasing rewrites history
  they have based work on; they will need a recovery dance.
- **Another branch is stacked on this one.** Rebase invalidates the
  child's base. If the stack is intentional and you must rebase, use
  `git rebase --update-refs` and coordinate.
- **Review is already in flight** in a culture that anchors comments to
  specific commit SHAs. Rebasing detaches comments from their anchors.
  Some teams forbid rebase post-review; prefer "rework" commits.
- **Per-client policy says merge-only.** Check the repo's CLAUDE.md /
  skills before recommending rebase.

Otherwise, rebase.

## Force-push flavors

A rebased feature branch has commits whose SHAs no longer match the
remote's. The push must be a force-push. The flavor matters:

| Flag | Safety check | When to use |
|---|---|---|
| `--force` | None. Unconditionally overwrites the remote ref. | Never on its own. |
| `--force-with-lease` | Fails if the remote ref moved since your last fetch. | Default for rebased feature branches. |
| `--force-if-includes` | Additionally fails if the local branch doesn't contain the remote's tip. | Git ≥ 2.30. Pair with `--force-with-lease`. |

Recommended command after rebasing a personal branch:

```
git push --force-with-lease --force-if-includes
```

`--force-with-lease` alone has a known gap: a stray `git fetch` updates
the remote-tracking ref, which makes the lease look held even though you
haven't integrated the remote's new commits. `--force-if-includes` closes
that gap. Use both.

## Never-force-push targets

Refuse to force-push to any of these regardless of flavor:

- `main`, `master`, `trunk`, `develop`, or any default branch
- `release/*`, `hotfix/*`, or any other protected / shared branch
- Any branch the user has not explicitly said is personal

For those branches, rebasing the branch itself is also off the table —
they keep merge history. Bringing `main` into them (when it even makes
sense) is a merge, not a rebase.

## Interaction with PR merge strategy

The feature branch's internal history is either discarded or preserved
at PR-merge time, depending on the repo's merge strategy:

- **Squash-merge** — feature history collapses into one commit on `main`.
  Rebase vs merge-main becomes purely about *your* dev experience during
  the PR (reviewer diff cleanliness, conflict ergonomics). `main`'s
  history is unaffected either way.
- **Rebase-merge** — feature commits replay onto `main`. Internal
  history survives. Keeping the branch rebased keeps `main` clean;
  merge-main leaves merge commits in the replayed series.
- **Merge-commit** — feature history plus a merge commit land on `main`.
  Merge-main noise becomes permanent `main` history.

**Before recommending rebase-only on history-cleanliness grounds, check
the PR merge strategy.** On a squash-merge repo, the argument mostly
dissolves; on a merge-commit repo, it's at its strongest. Per-client
CLAUDE.md should state the policy.

## Pre-flight checklist

Before running `git push --force-with-lease` on a feature branch:

1. Is this branch personal (only you committing)? If no → merge instead.
2. Has anyone stacked a branch on this one? If yes → coordinate or merge.
3. Is PR review in flight where comments anchor to SHAs? If yes → rework
   commits, not rebase.
4. Is the branch one of `main` / `master` / `develop` / `release/*` /
   any protected branch? → stop. Force-push is forbidden here.
5. Using `--force-with-lease --force-if-includes`? → proceed.

## Recovery from a bad force-push

`--force-with-lease --force-if-includes` will catch most of these. If
one slips through and you clobber a teammate's commits:

1. Find the pre-force SHA. Your local `git reflog` has it if you were
   the one who force-pushed. Otherwise ask the displaced teammate —
   their local reflog or branch still points at it.
2. Restore: `git push --force-with-lease origin <recovered-sha>:<branch>`
3. Coordinate. The teammate may have local work that now needs rebasing
   onto the restored tip.

For recovery from a bad *merge* (not force-push) that already landed,
see `git-state-safety`.

## Rule of thumb

On a personal branch with no stacked dependents and no in-flight review
anchoring: `git fetch origin && git rebase origin/main && git push
--force-with-lease --force-if-includes`. Anywhere else: merge, or stop
and ask.
