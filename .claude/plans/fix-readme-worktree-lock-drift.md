# Fix stale "git worktree lock" mechanism description (README + hook comment)

## Context

PR #683 ("Atomic worktree lock acquisition") replaced the worktree-collision
guard's locking mechanism — from calling the `git worktree lock` subcommand
(non-atomic, confirmed racy) to a direct `O_EXCL` create against the
worktree's own `<git-dir>/locked` file — and updated `_lib.sh`'s own comment
and `docs/hooks.md`'s bullet to match, but missed two other prose sites that
still describe the old mechanism. The goal is to bring every prose
description of the collision guard's locking mechanism back in sync with
what `_lib_worktree_collision_guard` (`claude/.claude/hooks/_lib.sh:939`)
actually does, without touching the guard's logic itself.

## Approach

Correct the two drifted sites to describe the current O_EXCL-write mechanism, matching the phrasing `_lib.sh:938-940`'s own (already-updated) comment uses — the authoritative source for the mechanism's wording; `docs/hooks.md:20` names *that* the guard denies a second session but not *how*, so it's confirmatory context, not the phrasing source. No behavior changes — comment/doc text only.

**Root problem:** two prose sites describe the worktree-collision guard's locking mechanism as `git worktree lock`, which PR #683 replaced with a direct `O_EXCL` file write. `[engineer-verified]`

**Givens:**
- The guard's actual mechanism (`_lib_worktree_collision_guard`) is out of scope to change — this plan only corrects descriptions of it. Changing the mechanism was PR #683's concern, not this one's. `[engineer-verified]`

| # | Site | Justification | anchors |
|---|------|----------------|---------|
| 1 | `README.md:258` — rewrite "tracked via `git worktree lock`" | Named by the user as the reported drift; confirmed stale against `_lib.sh:939`'s own comment. `[verified: claude/.claude/hooks/_lib.sh:939]` | root |
| 2 | `claude/.claude/hooks/require-worktree-for-git-writes.sh:83-86` — rewrite the "Known gaps" bullet | Same drift class as row 1: describes "a `git worktree lock` failure caused by ... an old git without worktree-lock support" — a gap that no longer applies since the guard doesn't call that subcommand. `_lib.sh:954-956`'s Known-gaps list already carries the corrected version of this same gap (non-contention write failure misdiagnosed as a transient race); this row aligns the duplicate copy. Found via `git grep` sweep for `git worktree lock` outside plan/test files (see Verification). `[verified: claude/.claude/hooks/_lib.sh:954-956]` | root |

**Sweep performed:** `git grep -n "git worktree lock\b"` across `*.sh`/`*.md`/`*.py`, excluding `git worktree unlock` matches. Remaining hits are not drift:
- `.claude/plans/*.md` — historical planning docs recording what was decided and why; preserved record, not a live behavior description (CLAUDE.md Axis 3).
- `claude/.claude/hooks/tests/*.py` and `claude/.claude/scripts/tests/test_worktree_lib.py` — describe fabricating a `locked` file's on-disk *format*, which PR #683 deliberately preserved byte-for-byte so `git worktree lock`/`unlock`/porcelain still interoperate; accurate as written.
- `claude/.claude/scripts/cleanup-merged-branches.sh:753` — a genuine, unrelated call to the real `git worktree lock` subcommand (restoring a lock after a failed `git worktree remove`), not a description of the collision guard.

## Critical files

- `README.md` (line 258) — reword the "Worktrees only isolate…" paragraph in the "Worktree enforcement" section to describe the O_EXCL write against `<git-dir>/locked`, matching `claude/.claude/hooks/_lib.sh:938-940`'s phrasing.
- `claude/.claude/hooks/require-worktree-for-git-writes.sh` (lines 83-86) — reword the stale "Known gaps" bullet to match the corrected gap description already in `claude/.claude/hooks/_lib.sh:954-956`.

## Verification

- Re-run the sweep grep after editing to confirm no remaining non-preserved-record site describes the guard as using `git worktree lock`:
  `git grep -n "git worktree lock" -- '*.sh' '*.md' '*.py'` and manually confirm every remaining hit falls into one of the three excluded categories above.
- No test suite changes needed — this is a comment/doc-only change with no behavioral delta; run `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py` as a smoke check that the file still parses/loads correctly (shell comments can't break Python tests, but this confirms nothing else was accidentally touched).

## Out of scope

- Any other pre-existing drift not related to the `git worktree lock` → O_EXCL-write terminology change (per the user's framing, only this drift class is in scope).
- Deduplicating the "Known gaps" list between `_lib.sh` and `require-worktree-for-git-writes.sh` — the duplication predates this change and isn't part of the reported drift.
