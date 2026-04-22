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
  normal feature work without pending merges, writing new code
  unrelated to git state, or rebase-vs-merge-main and force-push-safety
  questions on a clean feature branch (use `git-feature-branch-sync`
  instead).
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
| **Inspect unmerged stages without touching the index** | `git ls-files -u` |
| Need to actually build / test / browse another ref's code | `git worktree add <path> <ref>` (creates a disposable checkout that does not touch the current tree) |

The worktree option is the escape hatch when you genuinely need working
files: create a temporary worktree for *reading/testing*, do the
investigation there, remove it with `git worktree remove <path>` (add
`--force` if the worktree is dirty). Note: the worktree is for reading;
do not run a test-merge inside it — that is a separate merge with
separate state, not a rehearsal of the one you are in.

## Stage numbering

Mid-merge, the index holds up to three versions of each conflicted path:

- **Stage 1** — common ancestor ("base")
- **Stage 2** — current branch ("ours")
- **Stage 3** — incoming branch ("theirs")

`git ls-files -u` prints `<mode> <sha> <stage>\t<path>` for every
conflicted entry. These stages are what `git checkout --ours <path>` and
`git checkout --theirs <path>` select from (see the safe-mutations
section below). Knowing the numbering is a prerequisite for reading
`git status -s` codes (`UU`, `AU`, `UA`, `DU`, `UD`, `AA`, `DD`) without
guessing.

## Unsafe mutations during fragile state

Never run these while merge/rebase/cherry-pick state exists, or while a
conflict is partially resolved:

- `git checkout <ref> -- <path>` — overwrites the index entry for
  `<path>`. Can silently cancel staged deletions, modifications, or
  unresolved markers from the merge.
- **`git restore --source=<ref> <path>` / `git restore --staged <path>`** —
  modern equivalents of the `checkout` hazard, and the form current
  agents will reach for by default. Same failure mode.
- **`git add <path>` / `git add -p` on a conflicted path** — collapses
  stages 1/2/3 into stage 0 using the current worktree content. If the
  worktree still has `<<<<<<<` markers, you will commit literal conflict
  markers. If it has a half-resolution, you lose the other stages
  irrecoverably.
- **`git update-index`** (any form) — direct index mutation, same or
  worse blast radius.
- **`git rm <path>` on a conflicted path** — resolves-by-deletion; easy
  to do accidentally when intending to delete an unrelated file.
- **`git clean`** — wipes untracked files, including merge leftovers
  (conflict backups, `*.orig`, notes you may need).
- **`git commit -a` / `git commit --all` during a merge** — pulls in
  unresolved worktree state wholesale.
- `git checkout <ref>` (branch switch) — blocked by git if conflicts are
  present, but succeeds and throws away the merge state if you force
  past it.
- `git reset` — always destructive to index/working tree state. Even
  `git reset HEAD <path>` can unstage a merge resolution.
- `git stash` — on modern Git, stash refuses mid-merge by default; with
  `-f` / `--all` or on older versions it resets `.git/MERGE_HEAD` and
  there is no supported `stash pop` path that restores the merge state.
  Treat as unrecoverable.
- `git switch` — same hazard as `git checkout <ref>`.

If you need to abandon and restart a merge, use `git merge --abort` (or
`git rebase --abort`, `git cherry-pick --abort`). Those are the only
supported ways to back out. `git merge --abort` requires `ORIG_HEAD` to
point at the pre-merge tip; if you have already run a `git reset` or
started a second merge, `--abort` may refuse or restore the wrong state.

## Safe per-path conflict resolution

These *are* safe mid-merge and often the right tool for picking one
side on a specific path:

- `git checkout --ours <path>` — resolve `<path>` using stage 2 (current
  branch's version). Stages the resolution.
- `git checkout --theirs <path>` — resolve `<path>` using stage 3
  (incoming branch's version). Stages the resolution.

These differ from `git checkout <ref> -- <path>` (unsafe, above)
because they operate on the conflict stages already in the index, not
by fetching from a ref. After running one, `<path>` moves from stages
1/2/3 to stage 0 (resolved).

**Cheap insurance:** `cp .git/index .git/index.bak` before any risky
operation. Restore with `cp .git/index.bak .git/index` if the
experiment goes sideways. Trivial cost, recoverable footgun.

## Recovery when a bad merge already landed

The worst case: the merge commit is already created and the merge tree
is wrong (e.g., files deleted on the other side are still present on
your branch).

**If the bad merge is local-only (not yet pushed):** `git reset --hard
ORIG_HEAD` — `git merge` writes `ORIG_HEAD` to the pre-merge tip, so
this is the single-command undo. Only works pre-push and only if you
have not run another merge / reset since. This is the highest-leverage
recovery there is; try it first.

**If the bad merge is already pushed** and the no-force-push rule binds,
the recipe below reverses the effect by restoring upstream content in a
follow-on commit.

This recipe applies **after** the bad merge is already committed —
`git status` shows a clean tree (including empty `git diff --cached`)
and the merge commit is in history. The fragile-index rules in the
sections above apply during the *active* merge; once the merge is
committed, standard index operations (`git rm`, `git checkout <ref> -- <path>`)
are safe again.

For the rest of this section, **`<upstream>`** means the ref whose state
the merge was intended to produce — typically `origin/<target-branch>`
at the time of the attempted merge.

### Recipe

1. **Confirm the damage:**
   - `git diff <upstream>...<your-branch> --name-status` shows what your
     branch has that upstream does not.
   - `git show <merge-commit> --name-status` should match expectations;
     if the merge commit only touches the conflict-resolved files and
     nothing else, that is the smoking gun.
2. **Partition the diverged files by how they diverged.** Piping a mixed
   list to `xargs git checkout` silently skips deletions; handle each
   class explicitly:

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
3. **Verify the diff is now clean:** `git diff <upstream>...<your-branch>
   --name-status` should list only the files your change actually
   touches.
4. **Commit the recovery** as its own commit on top — do not amend the
   merge commit and do not force-push. The bad merge stays in history
   but its effect is reversed by the recovery commit. Amending is worse
   than a follow-up because other branches / PRs / comments may already
   reference the merge commit SHA.
5. Pre-commit hooks may complain about pre-existing warnings in the
   restored files (the upstream content that you did not author). If so,
   request explicit engineer approval before using `--no-verify`;
   skipping hooks without approval is forbidden.

Force-push + coordination is a legitimate alternative on short-lived
single-author branches or release branches where the bad merge poisoned
a tag candidate. That is out of scope for this skill — see
`git-feature-branch-sync` for the never-force-push list and the
`--force-with-lease --force-if-includes` recipe.

### If GitHub's PR diff still shows restored files after the recovery commit

GitHub's Files-changed view renders `git diff <base>...<head>`
(three-dot) — the diff from the branch's *merge-base* to its tip, not
from the upstream's current tip to the branch's tip. Any file modified
on the branch relative to the merge-base stays in the PR diff even
after the content now matches current `<upstream>`.

The recovery commit restores content but cannot rewrite history. If
the clean PR diff matters (visual review scope, CI file-filter
triggers, reviewer trust), the only clean fix is to **open a fresh
branch off current upstream containing only the intended changes**,
then close the original PR and point at the new one. Further commits
on the corrupted branch do not reduce the set of files GitHub shows.

## Rule of thumb

If `git status` shows `MERGING`, `REBASING`, `CHERRY-PICKING`, or any
`UU` / `DU` / `UD` / `AU` / `UA` / `AA` / `DD` entries, treat the index
as read-only for inspection purposes. If you need to see something from
another ref, use the read-only operations in the table above.

**Detection without parsing `git status`:** in scripts, checking file
existence is more robust and locale-independent. Any of these means a
fragile state:

- `test -e "$(git rev-parse --git-path MERGE_HEAD)"` — active merge
- `test -d "$(git rev-parse --git-path rebase-merge)"` — active rebase (interactive / merge-based)
- `test -d "$(git rev-parse --git-path rebase-apply)"` — active rebase (apply-based)
- `test -e "$(git rev-parse --git-path CHERRY_PICK_HEAD)"` — active cherry-pick

**When in doubt, the reflog is your safety net.** `git reflog` logs
every ref movement for 90 days by default (`gc.reflogExpire`). An
accidental `reset --hard` or `checkout` is usually recoverable via
`git reset --hard HEAD@{1}` or similar, *if you catch it before the
reflog entry expires and before another `git gc` reaps the orphaned
commits.*
