---
name: git-state-safety
description: >
  Safe inspection of other refs while mid-merge, mid-rebase, or
  mid-cherry-pick — avoids `git checkout <ref> -- <path>` which
  silently corrupts the index in these states. TRIGGER when: examining
  another ref while the tree has merge/rebase/cherry-pick state or
  unresolved conflicts, or recovering from a bad merge already committed.
  DO NOT TRIGGER when: working on a clean tree or for force-push safety
  questions (use git-feature-branch-sync instead).
user-invocable: false
---

# Git State Safety

## The failure mode this prevents

During a merge/rebase/cherry-pick, the index holds a carefully constructed
mid-state: files from both sides, deletion markers, unresolved paths.
**Any mutation of the index before commit silently corrupts that state.**

Common way this goes wrong: you want to compare a file on another branch
during conflict resolution, so you run
`git checkout origin/main -- src/some-file.ts`. That *overwrites* the
index entry for that path. If main had *deleted* a sibling file, the
deletion marker can also get reset. The failure is silent until commit.

## Safe read-only inspection

Use these during any fragile state. All are read-only — zero index / working-tree effect:

| Need | Command |
|---|---|
| See a file's content on another ref | `git show <ref>:<path>` |
| Same, piped for scripting | `git cat-file -p <ref>:<path>` |
| Log for a path on another ref | `git log <ref> -- <path>` |
| Diff between two refs | `git diff <ref-a> <ref-b> -- <path>` |
| Who last touched a line | `git blame <ref> -- <path>` |
| Full tree inspection | `git ls-tree -r <ref>` |
| Inspect unmerged stages without touching the index | `git ls-files -u` |
| Genuinely need working files from another ref | `git worktree add <path> <ref>` |

The worktree option is the escape hatch when you need working files: create a temporary worktree for reading/testing, investigate there, remove with `git worktree remove <path>`. The worktree is for reading — do not run a test-merge inside it.

## Stage numbering

Mid-merge, the index holds up to three versions of each conflicted path:

- **Stage 1** — common ancestor ("base")
- **Stage 2** — current branch ("ours")
- **Stage 3** — incoming branch ("theirs")

`git ls-files -u` prints `<mode> <sha> <stage>\t<path>` for every conflicted entry. These are what `git checkout --ours <path>` and `git checkout --theirs <path>` select from.

## Unsafe mutations during fragile state

Never run these while merge/rebase/cherry-pick state exists, or while a conflict is partially resolved:

- `git checkout <ref> -- <path>` — overwrites the index entry. Can silently cancel staged deletions, modifications, or unresolved markers.
- **`git restore --source=<ref> <path>` / `git restore --staged <path>`** — modern equivalents; same failure mode.
- **`git add <path>` / `git add -p` on a conflicted path** — collapses stages 1/2/3 into stage 0. If the worktree still has `<<<<<<<` markers, you commit literal conflict markers.
- **`git update-index`** (any form) — direct index mutation, same or worse blast radius.
- **`git rm <path>` on a conflicted path** — resolves-by-deletion; easy to do accidentally.
- **`git clean`** — wipes untracked files, including merge leftovers and `*.orig` notes.
- **`git commit -a` / `git commit --all` during a merge** — pulls in unresolved worktree state wholesale.
- `git checkout <ref>` (branch switch) — blocked by git if conflicts are present, but succeeds and throws away merge state if forced.
- `git reset` — always destructive to index/working tree state. Even `git reset HEAD <path>` can unstage a merge resolution.
- `git stash` — refuses mid-merge by default; with `-f` / `--all` or on older Git it resets `.git/MERGE_HEAD`. No supported `stash pop` path restores merge state. Treat as unrecoverable.
- `git switch` — same hazard as `git checkout <ref>`.

To abandon and restart a merge, use `git merge --abort` (or `git rebase --abort`, `git cherry-pick --abort`). Note: `git merge --abort` requires `ORIG_HEAD` to point at the pre-merge tip — if you have already run a `git reset` or started a second merge, `--abort` may refuse or restore the wrong state.

## Safe per-path conflict resolution

These *are* safe mid-merge:

- `git checkout --ours <path>` — resolve using stage 2 (current branch's version). Stages the resolution.
- `git checkout --theirs <path>` — resolve using stage 3 (incoming branch's version). Stages the resolution.

These differ from `git checkout <ref> -- <path>` (unsafe) because they operate on conflict stages already in the index.

**Cheap insurance:** `cp .git/index .git/index.bak` before any risky operation. Restore with `cp .git/index.bak .git/index`.

## Recovery when a bad merge already landed

**If the bad merge is local-only (not yet pushed):** `git reset --hard ORIG_HEAD` — `git merge` writes `ORIG_HEAD` to the pre-merge tip. Only works pre-push and only if no subsequent merge/reset has run.

**If the bad merge is already pushed** and no-force-push applies, reverse the effect with a follow-on commit. This assumes `git status` shows a clean tree (the merge is committed; fragile-index rules above no longer apply).

For the rest of this section, **`<upstream>`** = the ref whose state the merge was intended to produce.

### Recipe

1. **Confirm the damage:** `git diff <upstream>...<your-branch> --name-status`
2. **Partition and fix by diff filter:**

   ```
   # Files the branch kept that upstream DELETED — remove them
   git diff <upstream>...<your-branch> --diff-filter=A --name-only \
     | xargs git rm

   # Files that diverged in CONTENT — restore upstream content
   git diff <upstream>...<your-branch> --diff-filter=M --name-only \
     | xargs -I{} git checkout <upstream> -- {}

   # Files the branch DELETED that upstream kept — restore from upstream
   git diff <upstream>...<your-branch> --diff-filter=D --name-only \
     | xargs -I{} git checkout <upstream> -- {}
   ```
3. **Verify:** `git diff <upstream>...<your-branch> --name-status` should list only files your change actually touches.
4. **Commit the recovery** as its own commit — do not amend the merge commit and do not force-push. The bad merge stays in history but its effect is reversed.
5. Pre-commit hooks may complain about pre-existing warnings in restored files. Request explicit engineer approval before using `--no-verify`.

### If GitHub's PR diff still shows restored files

GitHub's Files-changed view renders `git diff <base>...<head>` (three-dot) — diff from the branch's *merge-base* to its tip. Files modified on the branch relative to the merge-base remain in the PR diff even after content now matches `<upstream>`. The only clean fix is a **fresh branch off current upstream** containing only the intended changes.

## Rule of thumb

If `git status` shows `MERGING`, `REBASING`, `CHERRY-PICKING`, or any `UU` / `DU` / `UD` / `AU` / `UA` / `AA` / `DD` entries, treat the index as read-only.

**Detection in scripts** (locale-independent):

- `test -e "$(git rev-parse --git-path MERGE_HEAD)"` — active merge
- `test -d "$(git rev-parse --git-path rebase-merge)"` — active interactive/merge rebase
- `test -d "$(git rev-parse --git-path rebase-apply)"` — active apply-based rebase
- `test -e "$(git rev-parse --git-path CHERRY_PICK_HEAD)"` — active cherry-pick

**The reflog is your safety net.** `git reflog` logs every ref movement for 90 days (`gc.reflogExpire`). An accidental `reset --hard` is usually recoverable via `git reset --hard HEAD@{1}` if caught before `git gc` reaps the orphaned commits.
