# Restore commit-with-implementation flow for plan files

## Context

**Goal:** Remove `.claude/plans/` from this repo's root `.gitignore` so plan
files get committed alongside their implementation, restoring the lifecycle
the `require-plan-review.sh` gate was designed for in PR #257.

**Problem.** A recurring gate false-arm — a fresh session opens, finds a
stale plan file from a prior session in `.claude/plans/`, and refuses any
`Write`/`Edit` until `/plan-review` runs against an unrelated plan. Recent
examples include `~/.claude/plans/we-need-to-figure-swirling-rain.md`
arming the gate in an unrelated session. The user clears sessions after
`/handoff`, so any in-session cleanup step never runs.

**Why now.** Two PRs are load-bearing here:

- **PR #257 (May 17)** rewrote the gate's signal: *"a plan file only counts as
  active work if it is untracked or modified vs HEAD. A file that is tracked
  and identical to HEAD is treated as historical (its review shipped with the
  PR that committed it)."* That commit message also names the **prescribed
  flow**: *"/plan-it → /plan-review → commit-with-implementation does not hit
  this path."*
- **PR #24 (April)** earlier added `.claude/plans/` to the root `.gitignore`
  as part of a sweeping "everything in `.claude/` is per-machine or per-session
  and shouldn't be tracked" categorization. That bucketing was wrong for plans
  — a plan belongs to a PR, not to a machine or a session — but the gitignore
  line was never reverted to match PR #257's design.

In this repo today, plans are *forever untracked*, so PR #257's "tracked +
clean = historical" branch never engages. The gate stays armed indefinitely
on any plan file that's ever been authored.

**Intended outcome.** Plan files live with the PR that produced them:
authored on the implementation branch, modified-and-`/plan-review`ed during
iteration, committed with the implementation, and treated as historical by
the gate on every future session.

## Approach

Single, precise revert of the misclassification:

1. **Remove** the line `.claude/plans/` from the repo-root `.gitignore`
   (currently at `.gitignore:8`).
2. **Update the comment** on `.gitignore:5-7` so it no longer claims plans
   are per-machine/per-session. Settings.local.json and worktrees stay
   gitignored under that rationale; plans separate.
3. **Keep** `claude/.claude/plans/` on `.gitignore:15` — this is the
   stow-package guard added in PR #257, preventing `stow --adopt` from
   re-tracking harness scratch plans into the stow package. Load-bearing.

No skill changes are needed: `branch-creation/SKILL.md:74-81` already
prescribes "commit the plan to this feature branch," `plan-it/SKILL.md:17-21`
already routes plans to `.claude/plans/<topic-slug>.md` on the feature
branch, `plan-review/SKILL.md` B17 already flags orphaned plan-only PRs,
and `docs/hooks.md:50-52` already documents commit-the-plan as a recovery
path. The current state already expects commit; only the gitignore conflicts.

No hook changes are needed: PR #257's existing logic (`require-plan-review.sh:58-65`)
already does the right thing once plans become tracked-and-clean.

**Rationale for not doing the alternatives.** Two were considered:

- *Branch-scoped gate + cleanup script extension.* Would also work, but adds
  a new arming heuristic and a new cleanup-script responsibility. The simpler
  fix is to align the gitignore with PR #257's already-implemented design.
- *Time-based mtime cutoff.* Heuristic; mis-fires on long-paused branches.
  Worse fit than git-status as the lifecycle signal.

## Out of scope

- **`~/.claude/plans/` scratch accumulation** (368 harness-provided plan
  paths). That directory is outside any repo and does not arm the gate.
  Pure disk hygiene; separable change if the user wants it later.
- **Other projects' `.gitignore` files.** This change affects only
  `claude-config`. Other repos the user works in each set their own
  gitignore; the stowed skills describe the intent, but per-project
  gitignore alignment is a per-project edit, not this PR's scope.
- **Reviewing/relocating already-leaked plan files.** No leaked plans
  exist in this repo's `.claude/plans/` currently (verified empty).
  `~/.claude/plans/` accumulation is the prior bullet, not this one.

## Critical files

- `.gitignore` (repo root) — remove the `.claude/plans/` line,
  revise the comment above it. ~3-line diff.

No other files require changes. Reuse:

- **`require-plan-review.sh:58-65`** — the per-file tracked-and-clean
  check; no edits needed, just stops mis-firing once plans are committable.
- **`branch-creation/SKILL.md:74-81`** — "Plan files go on the
  implementation branch" guidance; already correct.
- **`docs/hooks.md:50-52`** — already documents the commit-the-plan
  recovery path.

## Verification

Run from the `claude-config` repo after the change is applied (in a linked
worktree per repo policy):

1. **Tests pass unchanged.** Existing hook tests already exercise the
   tracked-and-clean / untracked / modified-vs-HEAD branches:
   ```
   ../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_plan_review.py
   ```

2. **End-to-end: a committed plan does not arm the gate.**
   1. From a fresh worktree, write `.claude/plans/verify-fix.md` with
      any content.
   2. Confirm `git status` shows the file as `Untracked` (not `Ignored`).
   3. `git add .claude/plans/verify-fix.md && git commit -m "plan: verify-fix"`.
   4. Start a fresh Claude Code session in the worktree. Without running
      `/plan-review`, attempt an `Edit` against any other file.
   5. **Expected:** the edit is allowed. The gate sees the plan file as
      tracked-and-clean (PR #257 logic) and does not arm.

3. **End-to-end: a modified committed plan re-arms the gate.**
   1. With the plan from step 2 still committed, modify it (any edit).
   2. In the same session, attempt an `Edit` against another file.
   3. **Expected:** the gate denies with the existing message, naming
      `/plan-review` as the next step.
   4. Run `/plan-review` against the modified plan. Marker is written.
   5. Retry the edit. **Expected:** allowed.

4. **Regression check: untracked plan still arms.** Delete the committed
   plan and write a fresh untracked one. Attempt an edit. **Expected:**
   gate denies (unchanged from current behavior).

5. **Stow-package guard still works.** Confirm `claude/.claude/plans/` is
   still listed in `.gitignore` (line 15 should be intact). Try
   `git check-ignore claude/.claude/plans/anything.md`; **expected:** path
   matches ignore rule.
