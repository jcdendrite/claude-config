---
name: cleanup-merged-branch
description: >
  Cleans up after a PR is merged: removes the local worktree for the
  branch (if one exists), force-deletes the local branch, prunes the
  remote tracking ref, deletes the remote branch if not auto-deleted,
  and fast-forwards the default branch. Use this skill whenever the
  user says "merged, clean up", "delete the branch", "tidy up", or
  just "merged" at the end of a PR session — post-merge cleanup is
  almost always what they mean. Also trigger proactively after any
  successful PR merge where the user hasn't yet cleaned up.
user-invocable: true
argument-hint: "[branch-name]"
---

# Cleanup merged branch

This repo squash-merges PRs and enforces worktree discipline. Two
constraints shape every step:

- **Always use `git branch -D`** (force), not `-d`. Squash merges don't
  leave a merge commit that git can trace back to the branch, so `-d`
  refuses with "not fully merged" even when the branch is clearly gone.
- **CWD anchoring for write ops**: the worktree hook fires *before* the
  subshell runs, so `cd /worktree && git push` doesn't work — the hook
  reads the session's prior CWD, not the inline `cd`. Run `cd` as its
  own Bash call, then the git op in a follow-up call.

## Before you begin — anchor CWD

Run this as the very first Bash call, using the absolute repo root path
you know from context. CWD may point to a deleted worktree directory
left over from earlier in the session; any git command will fail with
`getcwd: cannot access parent directories` until you re-anchor:

```bash
cd <ABSOLUTE_REPO_ROOT>
```

If the path isn't obvious, check recent tool output — a `git worktree list`
result, a file path the model wrote, or the skill invocation directory.

## 0. Resolve branch name

Use the argument if provided. Otherwise detect the most recently merged PR:

```bash
gh pr list --state merged --limit 1 --json headRefName --jq '.[0].headRefName'
```

Confirm with the user if unsure which branch to clean up.

## 1. Remove worktree (if present)

`git worktree` is on the main-tree allowlist, so this runs without CWD
anchoring:

```bash
git worktree remove .claude/worktrees/<BRANCH> --force 2>/dev/null || true
```

`--force` handles the case where files are open or uncommitted changes
were left behind. The `|| true` swallows the error when the path doesn't
exist.

## 2. Fetch and prune

```bash
git fetch --prune
```

If the output shows `[deleted] (none) -> origin/<BRANCH>`, the remote
was auto-deleted on merge — skip step 4.

## 3. Delete local branch

```bash
git branch -D <BRANCH> 2>/dev/null || echo "Local branch not found"
```

## 4. Delete remote branch (if not auto-deleted)

Skip if step 2 showed the remote was already pruned.

`git push --delete` is blocked from the main tree — it must run from
inside a linked worktree. Run `git worktree list` and pick any path
that is not the main repo root. If none exists, create a temporary one:

```bash
git worktree add --detach .claude/worktrees/_cleanup-tmp
```

Then anchor and delete in two separate Bash calls:

```bash
# Call 1 — anchor CWD:
cd <worktree-path>

# Call 2 — delete remote:
git push origin --delete <BRANCH>
```

If you created `_cleanup-tmp`, remove it when done:

```bash
git worktree remove .claude/worktrees/_cleanup-tmp
```

## 5. Fast-forward default branch

Check whether local main lags behind origin/main:

```bash
git rev-list --count HEAD..origin/main
```

Zero means you're done. If nonzero, fast-forward — same CWD anchoring
pattern as step 4 (reuse the worktree if still open):

```bash
# Call 1 — anchor CWD (skip if already in a worktree):
cd <worktree-path>

# Call 2 — fast-forward:
git -C <REPO_ROOT> merge --ff-only origin/main
```

## 6. Summary

Report concisely:
- Worktree: removed / not found
- Local branch: deleted / not found
- Remote branch: already pruned / deleted now / manual step needed
- Default branch: already current / fast-forwarded to `<sha>` / manual pull needed
