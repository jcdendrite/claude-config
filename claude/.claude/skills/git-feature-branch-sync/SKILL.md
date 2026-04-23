---
name: git-feature-branch-sync
description: >
  Decision framework for keeping a feature branch up to date with the
  repo's default branch: when to rebase-and-force-push vs merge-in,
  how to force-push safely (`--force-with-lease` / `--force-if-includes`
  / plain `--force`), and when the "never force-push" instinct is
  correct vs overcautious. This is the global framework — per-project
  policy (squash-merge vs merge-commit, rebase-after-review rules,
  branch-protection specifics) lives in that repo's CLAUDE.md or a
  project-scoped skill.
  TRIGGER when: deciding how to integrate the default branch into a
  feature branch, whether/how to force-push a feature branch, or
  picking between `--force-with-lease` and `--force-if-includes`.
  DO NOT TRIGGER when: routine work on a clean feature branch with no
  integration question, operations on the default / release / other
  shared branches (those are always protected — never force-push them),
  or mid-merge / mid-rebase / mid-cherry-pick state (use
  `git-state-safety` instead).
user-invocable: false
---

# Feature-Branch Sync: Rebase, Merge, and Force-Push

Examples below use `main` / `origin/main` as the default-branch
placeholder. If the repo's default is `master`, `trunk`, `develop`, or
something else, substitute accordingly — check with
`git symbolic-ref refs/remotes/origin/HEAD`.

Pre-creation concerns (branch naming, starting from a fresh default
tip) live in the `branch-creation` skill — this one covers the
lifecycle after the branch exists.

## The decision

Keeping a feature branch current with the default branch has two shapes:

- **Rebase** — `git fetch origin && git rebase origin/main`. Replays your
  commits on top of the current default-branch tip. Linear history.
  Rewrites commit SHAs, so the next push must be a force-push.
- **Merge** — `git merge --no-ff origin/main`. Creates a merge commit on
  the feature branch. Preserves SHAs. No force-push needed. (`--no-ff`
  matters: a plain `git merge origin/main` will fast-forward if the
  feature branch is strictly behind, which is not what "merge main in"
  implies.) `git pull --rebase` is equivalent to `fetch + rebase`;
  prefer the explicit two-step form so the fetch result is auditable.

**This skill's default is rebase on personal feature branches** — a
reasonable and common choice, but not universal (many shops default to
merge even on personal branches). Switch to merge when any
*merge-required condition* below applies. Always defer to per-project
policy when it contradicts this default.

## Merge-required conditions

Any one of these → merge-main, do not rebase:

- **Another active committer** on the branch. Rebasing rewrites history
  they have based work on; they will need a recovery dance.
- **Another branch is stacked on this one.** Rebase invalidates the
  child's base. For stacked-PR workflows (Graphite, spr, stgit, or
  hand-rolled), use `git rebase --update-refs` to update every
  ancestor branch ref in one pass — or set `rebase.updateRefs=true` to
  make this default. Then force-push each stacked branch separately,
  leaf-to-root; a lease failure mid-stack leaves the stack half-rewritten.
- **Review is already in flight** in a culture that anchors comments to
  specific commit SHAs. Rebasing detaches comments from their anchors.
  Some teams forbid rebase post-review; prefer "rework" commits.
- **The repo's per-project policy mandates merge-only.** Example: the
  repo's CLAUDE.md says "squash-merge PRs and use merge-main to stay
  current" — defer to that. Check the project's CLAUDE.md or
  project-scoped skill before recommending rebase.

Otherwise, rebase. For long-lived branches that rebase repeatedly, set
`rerere.enabled=true` — `rerere` caches conflict resolutions and replays
them on re-conflict, which is the highest-ROI configuration for a
weeks-old feature branch that eats main conflicts every few days.

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
that gap by checking your local reflog against the remote-tracking ref.
Use both. Setting `push.useForceIfIncludes=true` makes the flag apply
automatically whenever `--force-with-lease` is used.

**Critical gotcha for automation and fresh clones:**
`--force-with-lease` without an explicit expected value requires a
*remote-tracking ref* (`refs/remotes/origin/<branch>`) to compute the
lease against. In a shallow clone, `--single-branch` clone, or first
push on a branch, that ref is missing or stale, and `--force-with-lease`
silently degrades to `--force` semantics. In CI or any automated
context, use the explicit form, which fails loudly if the ref is absent:

```
git fetch origin <branch>
git push --force-with-lease="<branch>:$(git rev-parse origin/<branch>)" \
         --force-if-includes origin <branch>
```

`--force-if-includes` also depends on the local branch's reflog, which
is empty on fresh/shallow clones — ephemeral CI runners hit this
routinely. If reflogs are disabled (`core.logAllRefUpdates=false`),
`--force-if-includes` silently no-ops.

## Never-force-push targets

Refuse to force-push to any of these regardless of flavor:

- **The remote's default branch** — check
  `git symbolic-ref refs/remotes/origin/HEAD` rather than assuming.
  `main` is common but not universal (`master`, `trunk`, `develop`, or
  anything else is possible); `init.defaultBranch` is the local default,
  not authoritative.
- `release/*`, `hotfix/*`, `staging`, `production`, `qa`, `uat`, or any
  other shared / environment-tracking branch
- **Tags** (`refs/tags/*`) — never force-push. Tags are the
  supply-chain anchor for releases and SBOMs; signed tags especially
  must never be rewritten.
- Any branch the user has not explicitly said is personal

For those branches, rebasing the branch itself is also off the table —
they keep merge history. Bringing the default branch into them (when it
even makes sense) is a merge, not a rebase.

**Do not rely on server-side branch protection** to catch mistakes. On
GitHub Free private repos the branch-protection API is inaccessible; on
other repos the rules may simply not be configured. Treat the list above
as the last line of defense, not the only one.

**Protected-branch rules may further constrain strategy.** Configurations
that change the decision tree:
- **Required linear history** — rebase-merge or squash-merge only; a
  merge commit from "merge-main-in" will be rejected at PR merge.
- **Required signed commits** — see the signed-commits check in pre-flight.
- **Required status checks** — force-pushing to a feature branch
  invalidates in-flight check runs, re-enters the PR into pending, and
  can knock it out of an auto-merge queue.
- **Actor-specific rules (GitHub rulesets, GitLab push rules)** — bot
  or app tokens may be excluded from force-push even when humans are
  allowed. If an automated agent sees a 403 from `--force-with-lease`
  but humans can push, this is likely the cause.

Query the relevant protection API before acting on non-personal
branches (GitHub: `GET /repos/:owner/:repo/rulesets/rules/branches/<branch>`).
If the API is unreachable or the rules are ambiguous in an automated
context, fail closed — merge instead.

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
dissolves; on a merge-commit repo, it's at its strongest. The project's
CLAUDE.md should state the merge strategy.

## Pre-flight checklist

Before running `git push --force-with-lease` on a feature branch:

1. Is this branch personal (only you committing)? If no → merge instead.
   Verify with `git log --format='%ae %ce' origin/<branch> | sort -u` if
   uncertain — include committer (`%ce`), not just author (`%ae`), so
   rebases and cherry-picks don't hide a second contributor. On a
   shallow clone, `git fetch --unshallow` first; the verification is
   worthless against truncated history. Don't rely solely on a "yes
   it's mine" claim.
2. Has anyone stacked a branch on this one? If yes → coordinate or merge.
3. Is PR review in flight where comments anchor to SHAs? If yes → rework
   commits, not rebase.
4. Is the branch in the never-force-push list above (default branch,
   environment branches, tags, shared branches)? → stop. Force-push is
   forbidden regardless of flavor.
5. Does the repo require signed commits? Rebase drops signatures and
   recreates commits. Either (a) ensure `commit.gpgsign=true` **and the
   signing key is available non-interactively** (critical on CI — a
   headless runner without a provisioned key silently produces unsigned
   commits that then fail `required_signatures`), or (b) skip rebase
   and merge instead. Check `git config commit.gpgsign`, verify the key
   is accessible, and confirm the repo's branch-protection
   `required_signatures` rule.
6. Running in CI / automation without a live human? Any ambiguous
   "personal branch?" or "rebase safe?" answer → **fail closed, merge
   instead**. Do not rebase when there's no one to escalate to.
7. Using `--force-with-lease --force-if-includes` (with explicit
   `<branch>:<expected-sha>` if on a fresh or shallow clone)? → proceed.

## Recovery from a bad force-push

`--force-with-lease --force-if-includes` will catch most of these. If
one slips through and you clobber a teammate's commits:

1. Find the pre-force SHA. Your local `git reflog` has it if you
   force-pushed; any teammate's `git reflog show origin/<branch>` in a
   recently-fetched clone also has it. If no local reflog survives,
   `git fsck --lost-found` may surface the dangling commits.
2. **Pin it immediately**, before doing anything else. Reflog expiry and
   `git gc` can reap unreachable commits; `gc.reflogExpireUnreachable`
   defaults to 90 days but teams sometimes tighten it. Create a backup
   ref: `git branch backup/<branch>-<date> <recovered-sha>`.
3. **`git fetch origin` before restoring.** Someone may have pushed
   legitimate reconciliation work on top of the clobbered tip. If so,
   merge those commits onto the recovered SHA before pushing — do not
   overwrite reconciliation work.
4. Restore: `git push --force-with-lease origin <recovered-sha>:<branch>`
5. Coordinate. The displaced teammate may have local work that now
   needs rebasing onto the restored tip.

For recovery from a bad *merge* (not force-push) that already landed,
see `git-state-safety`.

## After a legitimate force-push: communicate

Even a correct force-push invalidates commit SHAs, which breaks PR
review-comment anchors, CI artifact links, external references, and
bookmarks. After a rebase + force-push on a branch that has review
activity, post a PR comment with:

- The pre- and post-force SHAs, so reviewers can locate their prior
  comments in their own checkouts.
- A `git range-diff <old-sha>..<old-head> <new-sha>..<new-head>`
  summary that shows which commits moved and what changed commit-by-
  commit (not just the combined diff).

This costs one comment and saves reviewers from guessing whether their
prior feedback is addressed. Skip it only on branches with no active
reviewers.

## Rule of thumb

On a personal branch with no stacked dependents and no in-flight review
anchoring: `git fetch origin && git rebase origin/main && git push
--force-with-lease --force-if-includes`. Anywhere else: merge, or stop
and ask.
