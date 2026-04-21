---
name: git-state-safety
description: >
  How to inspect other branches / commits safely when the working tree is
  in a fragile state: mid-merge, mid-rebase, mid-cherry-pick, or with
  staged changes from a partially-resolved conflict. Prevents the
  silently-corrupted-index failure mode where a diagnostic
  `git checkout <ref> -- <path>` overwrites the mid-merge index and the
  resulting commit is wrong.
  TRIGGER when: examining another ref's content while the current tree
  has merge/rebase/cherry-pick state, unresolved conflicts, or a
  partially-staged merge resolution. Also when recovering from a bad
  merge that was already committed.
  DO NOT TRIGGER when: plain read-only git operations on a clean tree,
  normal feature work without pending merges, or writing new code
  unrelated to git state.
user-invocable: false
---

# Git State Safety

## The failure mode this prevents

During a merge/rebase/cherry-pick, the index holds a carefully constructed
mid-state: files from both sides, deletion markers, unresolved paths.
**Any mutation of the index before commit silently corrupts that state.**
The resulting commit captures the corrupted index, not the intended merge.

Common way this goes wrong: you want to compare how a file looks on another
branch during conflict resolution, so you run
`git checkout origin/main -- src/some-file.ts`. That *overwrites* the
index entry for `src/some-file.ts`. If main had *deleted* some sibling
file, the deletion marker can also get reset. The failure is silent until
commit, when the captured tree is missing main's deletions.

## Safe read-only inspection

Use these during any fragile state. All are read-only — zero index /
working-tree effect:

| Need | Command |
|---|---|
| See a file's content on another ref | `git show <ref>:<path>` |
| Same, piped for scripting | `git cat-file -p <ref>:<path>` |
| Log for a path on another ref | `git log <ref> -- <path>` |
| Diff between two refs | `git diff <ref-a> <ref-b> -- <path>` |
| Who last touched a line | `git blame <ref> -- <path>` |
| Full tree inspection | `git ls-tree -r <ref>` |
| Need to actually build / test / browse another ref's code | `git worktree add <path> <ref>` (creates a disposable checkout that does not touch the current tree) |

The worktree option is the escape hatch when you genuinely need working
files: create a temporary worktree, do the investigation there, remove it.

## Unsafe mutations during fragile state

Never run these while merge/rebase/cherry-pick state exists, or while a
conflict is partially resolved:

- `git checkout <ref> -- <path>` — overwrites the index entry for
  `<path>`. Can silently cancel staged deletions, modifications, or
  unresolved markers from the merge.
- `git checkout <ref>` (branch switch) — blocked by git if conflicts are
  present, but succeeds and throws away the merge state if you force
  past it.
- `git reset` — always destructive to index/working tree state. Even
  `git reset HEAD <path>` can unstage a merge resolution.
- `git stash` — saves a *clean* state. During a merge, it may stash
  incomplete resolution state that does not restore cleanly on pop.
- `git switch` — same hazard as `git checkout <ref>`.

If you need to abandon and restart a merge, use `git merge --abort` (or
`git rebase --abort`, `git cherry-pick --abort`). Those are the only
supported ways to back out.

## Recovery when a bad merge already landed

The worst case: the merge commit is already created and pushed, the
no-force-push rule binds, and the merge tree is wrong (e.g., files
deleted on the other side are still present on your branch).

### Recipe

1. **Confirm the damage:**
   - `git diff <upstream>...<your-branch> --name-status` shows what your
     branch has that upstream does not. `A` entries are files your side
     kept that the other side deleted; `M` entries are files with
     divergent content.
   - `git show <merge-commit> --name-status` should match expectations;
     if the merge commit only touches the conflict-resolved files and
     nothing else, that is the smoking gun.
2. **Restore the upstream state for every diverged file that is not part
   of your actual change:**

   ```
   # Delete files that were deleted upstream
   git rm <file1> <file2> ...

   # Restore upstream content for files that diverged
   <file-list> | xargs git checkout <upstream> --
   ```
3. **Verify the diff is now clean:** `git diff <upstream>...<your-branch>
   --name-status` should list only the files your change actually
   touches.
4. **Commit the recovery** as its own commit on top — do not amend the
   merge commit and do not force-push. The bad merge stays in history
   but its effect is reversed by the recovery commit.
5. Pre-commit hooks may complain about pre-existing warnings in the
   restored files (the upstream content that you did not author). If so,
   request explicit engineer approval before using `--no-verify`;
   skipping hooks without approval is forbidden.

## Rule of thumb

If `git status` shows `MERGING`, `REBASING`, `CHERRY-PICKING`, or any
`UU` / `DU` / `UD` entries, treat the index as read-only for inspection
purposes. If you need to see something from another ref, use the
read-only operations in the table above.
