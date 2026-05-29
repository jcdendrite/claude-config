---
name: git-feature-branch-sync
description: >
  Rebase vs merge a feature branch; force-push safely.
  TRIGGER when: syncing a feature branch with the default branch or
  deciding whether and how to force-push. DO NOT TRIGGER when: routine
  work on a clean branch, operating on shared/default branches (never
  force-push them), or mid-merge/rebase/cherry-pick state (use
  git-state-safety instead).
user-invocable: false
---

# Feature-Branch Sync: Rebase, Merge, and Force-Push

Examples below use `main` / `origin/main` as the default-branch
placeholder. Check with `git symbolic-ref refs/remotes/origin/HEAD`.

Pre-creation concerns (branch naming, starting from a fresh default
tip) live in the `branch-creation` skill.

## Detecting divergence

Canonical recipe — used at the SessionStart advisory hook
(`check-branch-divergence.sh`), the `/ready-for-review` pre-push gate,
and the `/respond-pr` precheck. Same primitive everywhere so detection
is uniform.

```
DEFAULT=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's#^refs/remotes/origin/##')
git fetch --no-tags --quiet origin "$DEFAULT"
BEHIND=$(git rev-list --count "HEAD..origin/$DEFAULT")
git merge-tree --write-tree "origin/$DEFAULT" HEAD   # trial merge; conflict files printed in trailer
```

`git merge-tree --write-tree` performs a trial merge without touching
the working tree or index; conflicting paths appear in the output's
trailer block. Requires git ≥ 2.38.

Behind-count = 0 → in sync, no action. Behind > 0 with a clean trial
merge → safe fast-forward or rebase candidate; run the [pre-flight
checklist](#pre-flight-checklist) before force-pushing. Behind > 0
with conflicts → resolve via rebase or merge per [The
decision](#the-decision) below.

## The decision

- **Rebase** — `git fetch origin && git rebase origin/main`. Replays commits on top of the current default-branch tip. Linear history. Rewrites SHAs → force-push required.
- **Merge** — `git merge --no-ff origin/main`. Creates a merge commit. Preserves SHAs. No force-push. (`--no-ff` matters: plain `git merge origin/main` fast-forwards if the feature branch is strictly behind.) `git pull --rebase` is equivalent to `fetch + rebase`; prefer the explicit two-step form.

**Default is rebase on personal feature branches** — common choice, but not universal. Switch to merge when any *merge-required condition* applies. Defer to per-project policy when it contradicts this default.

## Merge-required conditions

Any one of these → merge-main, do not rebase:

- **Another active committer** on the branch. Rebasing rewrites history they have based work on.
- **Another branch is stacked on this one.** Rebase invalidates the child's base. Use `git rebase --update-refs` (or `rebase.updateRefs=true`) to update every ancestor branch in one pass, then force-push each stacked branch separately leaf-to-root.
- **Review is in flight** in a culture that anchors comments to commit SHAs. Rebasing detaches comments. Prefer "rework" commits.
- **The repo's per-project policy mandates merge-only.** Check the project's CLAUDE.md before recommending rebase.

For long-lived branches, set `rerere.enabled=true` — `rerere` caches conflict resolutions and replays them on re-conflict.

## Force-push flavors

| Flag | Safety check | When to use |
|---|---|---|
| `--force` | None. Unconditionally overwrites. | Never on its own. |
| `--force-with-lease` | Fails if the remote ref moved since your last fetch. | Default for rebased feature branches. |
| `--force-if-includes` | Also fails if local branch doesn't contain the remote's tip. | Git ≥ 2.30. Pair with `--force-with-lease`. |

Recommended command after rebasing a personal branch:

```
git push --force-with-lease --force-if-includes
```

`--force-with-lease` alone has a gap: a stray `git fetch` updates the remote-tracking ref, making the lease look held even though you haven't integrated new commits. `--force-if-includes` closes that gap. Set `push.useForceIfIncludes=true` to apply it automatically.

**Critical gotcha for automation and fresh clones:** `--force-with-lease` without an explicit expected value requires a remote-tracking ref. In a shallow clone, `--single-branch` clone, or first push on a branch, that ref is missing and `--force-with-lease` silently degrades to `--force`. Use the explicit form:

```
git fetch origin <branch>
git push --force-with-lease="<branch>:$(git rev-parse origin/<branch>)" \
         --force-if-includes origin <branch>
```

`--force-if-includes` also depends on the local branch's reflog, which is empty on fresh/shallow clones. If reflogs are disabled (`core.logAllRefUpdates=false`), `--force-if-includes` silently no-ops.

## Never-force-push targets

Refuse to force-push to any of these regardless of flavor:

- **The remote's default branch** — check `git symbolic-ref refs/remotes/origin/HEAD`. `main` is common but not universal (`master`, `trunk`, `develop` are all possible).
- `release/*`, `hotfix/*`, `staging`, `production`, `qa`, `uat`, or any other shared / environment-tracking branch
- **Tags** (`refs/tags/*`) — supply-chain anchor for releases and SBOMs; never rewrite.
- Any branch the user has not explicitly said is personal

For these branches, rebasing the branch itself is also off the table — they keep merge history.

**Do not rely on server-side branch protection** — GitHub Free private repos may lack protection API access; rules may not be configured. Treat this list as the last line of defense.

**Protected-branch rules may further constrain strategy:**
- **Required linear history** — rebase-merge or squash-merge only; a merge-main commit will be rejected at PR merge.
- **Required signed commits** — rebase drops signatures. Ensure `commit.gpgsign=true` and the signing key is available non-interactively on CI, or merge instead.
- **Required status checks** — force-pushing invalidates in-flight check runs and can knock PRs out of auto-merge queues.
- **Actor-specific rules** — bot tokens may be excluded from force-push; a 403 from `--force-with-lease` when humans can push indicates this.

Query `GET /repos/:owner/:repo/rulesets/rules/branches/<branch>` before acting on non-personal branches. If unreachable or ambiguous in automation, fail closed — merge instead.

## Interaction with PR merge strategy

- **Squash-merge** — feature history collapses into one commit on `main`. Rebase vs merge-main affects only *your* dev experience.
- **Rebase-merge** — feature commits replay onto `main`. Keeping the branch rebased keeps `main` clean; merge-main leaves merge commits in the replayed series.
- **Merge-commit** — feature history plus a merge commit land on `main`. Merge-main noise becomes permanent `main` history.

Check the PR merge strategy before recommending rebase on history-cleanliness grounds.

## Pre-flight checklist

Before running `git push --force-with-lease` on a feature branch:

1. Is this branch personal? Verify with `git log --format='%ae %ce' origin/<branch> | sort -u` (include `%ce` for committer, not just `%ae` author — on a shallow clone, `git fetch --unshallow` first).
2. Has anyone stacked a branch on this one? If yes → coordinate or merge.
3. Is PR review in flight with SHA-anchored comments? If yes → rework commits, not rebase.
4. Is the branch in the never-force-push list? → stop.
5. Does the repo require signed commits? Ensure `commit.gpgsign=true` and the key is accessible non-interactively, or merge.
6. Running in CI without a live human? Any ambiguous answer → **fail closed, merge instead**.
7. Using `--force-with-lease --force-if-includes` (explicit `<branch>:<sha>` on shallow/fresh clones)? → proceed.

## Recovery from a bad force-push

If `--force-with-lease --force-if-includes` slips and you clobber commits:

1. Find the pre-force SHA via `git reflog` or a teammate's `git reflog show origin/<branch>`. If no reflog survives, try `git fsck --lost-found`.
2. **Pin it immediately** before `git gc` reaps it: `git branch backup/<branch>-<date> <recovered-sha>`.
3. `git fetch origin` before restoring — someone may have pushed reconciliation work on top.
4. Restore: `git push --force-with-lease origin <recovered-sha>:<branch>`.
5. Coordinate with the displaced teammate.

## After a legitimate force-push: communicate

Post a PR comment with the pre- and post-force SHAs and a `git range-diff <old-sha>..<old-head> <new-sha>..<new-head>` summary. Skip only on branches with no active reviewers.

## Rule of thumb

On a personal branch with no stacked dependents and no in-flight review anchoring: `git fetch origin && git rebase origin/main && git push --force-with-lease --force-if-includes`. Anywhere else: merge, or stop and ask.
