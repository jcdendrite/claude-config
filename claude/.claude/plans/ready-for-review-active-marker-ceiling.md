# Salvage Q4 from rfr-hook-hardening — active-marker 90-min hard ceiling

## Context

The handoff at `/tmp/rfr-q3-probe-handoff.md` covered two concerns
about `require-ready-for-review.sh`:

- **Q3 (session-id PPID-walk stability):** OBSOLETE. Solved on main
  via a different mechanism — `marker.sh:_resolve_session_id`
  (`claude/.claude/scripts/marker.sh:27-44`, commit `05a3c7e`) walks
  the process ancestor chain until it finds a session file, so any
  PID drift across compaction or shim depth is absorbed by the walk.
  No `refresh-session-id.sh` hook is needed; the empirical-probe
  procedure is moot.
- **Q4 (active-marker hard ceiling):** STILL OPEN. The rationale
  (cross-skill iteration pushes can keep a stuck marker alive
  indefinitely by refreshing its mtime without explicit user opt-in)
  is unchanged. The current `require-ready-for-review.sh:118-125`
  active-marker bypass uses sliding `mtime <60min` + `touch` on each
  bypass — no absolute lifetime cap.

The `rfr-hook-hardening` worktree carries 4 staged files (one of
which is the broad Q3+Q4 plan). Main is at `9d3b9e59` — 8 commits
ahead of the branch tip (`5e233a0`). The staged `SKILL.md` edits
target the *old* `activate-gate` recipe shape
(`SESSION_ID=$(cat ...) && ... && touch`) which no longer exists —
main's recipe is `~/.claude/scripts/marker.sh activate
ready-for-review`. The staged `require-ready-for-review.sh` and
test additions are still mostly valid (line numbers shifted by +2
from the `_marker_lib_repo_hash` extraction in commit `f120447`,
but the bypass block content is unchanged); the staged plan and
SKILL.md changes are not.

**Goal of this PR:** ship only the 90-min hard-ceiling fix for the
ready-for-review active marker. Discard the broad Q3+Q4 plan, the
SKILL.md prose additions (Q1/Q2 rejection + "no wrap-up commit"
note), and any work that assumed the pre-marker.sh skill recipe
shape.

**Scope:** ready-for-review only. The other active-marker hooks
(`require-respond-pr.sh`, `require-plan-review.sh`,
`require-memory-skill.sh`) have the same shape and may benefit from
the same hardening, but the user scoped this salvage to Q4 alone.
Note in the PR description that the pattern can be extended later;
do not change those hooks here.

## Approach

Two-line change at the write side, ~7-line check at the read side,
plus tests that pin the design choice.

### Write side — `marker.sh activate ready-for-review`

`claude/.claude/scripts/marker.sh:114-118` currently does
`touch "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"`.
Change to: `date +%s > "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"`.
File still has fresh mtime (write updates mtime); content is now a
parseable Unix epoch readable by the hook.

Plain `>` (not write-temp + atomic-rename) is sufficient here:
activate is a one-shot setup at skill step 0 with no concurrent
reader, the payload is a single 10-digit integer, and the hook
reads only after activate has exited. Atomic-rename was relevant to
the abandoned Q3 refresh-hook (every-turn writes with concurrent
skill reads); not relevant for activate.

Leave the other `activate` cases (`plan-review`, `respond-pr`,
`memory-skill`) untouched — keeps scope to Q4. Their content stays
empty; their hooks don't read content; behavior unchanged.

### Read side — `require-ready-for-review.sh:120-127`

Line numbers verified against `main@9d3b9e59`. Current bypass block:
```bash
if [ -n "$SESSION_ID" ]; then
  ACTIVE_MARKER="$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"
  if [ -f "$ACTIVE_MARKER" ] && [ -n "$(find "$ACTIVE_MARKER" -mmin -60 2>/dev/null)" ]; then
    touch "$ACTIVE_MARKER" 2>/dev/null
    exit 0
  fi
fi
```

Add a third check: read content as `CREATED_TS`, require it to be a
positive integer, require `now - CREATED_TS < 5400` (90 min). All
three pass → bypass + mtime touch. Any one fails → fall through to
the completion-marker check.

The staged shape in `rfr-hook-hardening` (`baecijcj8.txt:30-37`) is
the right form, including the `[[ "$CREATED_TS" =~ ^[0-9]+$ ]]`
numeric guard and the non-negative delta check. Salvage as-is.

### Empty-content fallback (rollout backward compat)

A marker file written by an older `marker.sh` (empty content) hits
the regex check, fails, falls through to completion-marker. Failing
**closed** is the integrity-first choice: users with stale pre-PR
markers re-run `/ready-for-review` once. Recoverable, not a
regression. Do not auto-migrate.

### Why 90 min

Genuine `/ready-for-review` runs take 5–15 min in observed practice,
bounded by step 3 (subagent review). 90 min is ~6× headroom.
Hung-gate / cross-skill-refresh paths get a hard cap; healthy runs
unaffected.

### What to drop from the staged worktree

- `claude/.claude/skills/ready-for-review/SKILL.md` — discard all
  staged changes. The `activate-gate` recipe in main already calls
  `marker.sh activate ready-for-review`; the staged diff edits the
  pre-marker.sh recipe form. The "No fast-path for small diffs" and
  "No wrap-up commit" prose are out of scope per user (Q4 only).
- `claude/.claude/plans/rfr-hook-hardening.md` (staged) — replace
  with the focused plan from this file once a feature branch is
  created.

## Critical files

To modify:

- `claude/.claude/scripts/marker.sh` — change line 117 from
  `touch "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"` to
  `date +%s > "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"`.
  No `umask` change; this tracks the existing file-mode posture
  (CISO file-mode tightening was deferred in the original plan).

- `claude/.claude/hooks/require-ready-for-review.sh` — replace the
  bypass block at lines 120-127 with the ceiling-checked variant
  (form already staged in `rfr-hook-hardening`). Update the
  header-comment "Two-marker pattern" block to describe content =
  activation timestamp + 90-min hard ceiling + fail-closed on
  empty/non-numeric. The staged comment update
  (`baecijcj8.txt:9-20`) is the right text.

- `claude/.claude/hooks/tests/test_require_ready_for_review.py`:
  - Update existing `test_active_marker_present_allows` (line ~226)
    and the cross-session test (line ~262): `.touch()` → `.write_text(str(int(time.time())))`.
  - Update existing `test_active_marker_mtime_refreshed_on_bypass`
    (line 275): write timestamp at activation; preserve its
    touch-refresh assertion.
  - Add `test_active_marker_within_ceiling_allows`: `created_ts =
    now - 4000` (~67 min), bypass allowed.
  - Add `test_active_marker_hard_ceiling_blocks_after_90min`:
    `created_ts = now - 5500` (just past 90 min), mtime fresh,
    bypass denied. Pins the design choice.
  - Add `test_active_marker_empty_content_denies_bypass`:
    `marker.write_text("")`, mtime fresh, bypass denied. Pins
    rollout backward-compat behavior.
  - Update `test_skill_activate_command_creates_active_marker`
    (line 439): after asserting the marker exists, also assert
    `marker.read_text().strip().isdigit()`. Pins
    SKILL-recipe ↔ marker.sh ↔ hook alignment.
  - Staged versions of these tests in `baecijcj8.txt:67-128, 130-145,
    150-154` are essentially correct — copy them over.

- `claude/.claude/plans/<branch-slug>.md` — copy this plan file
  here (per project convention; plans ship with their PR). Branch
  slug from `branch-creation` skill.

### Reuse opportunities

- `marker.sh:_resolve_session_id` (the ancestor-walk) — already
  resolves SESSION_ID for the activate path. No new helper needed.
- `tests/conftest.py` `isolated_home` fixture, `bash_input` helper,
  `extract_skill_command` for the hook-alignment test — all in
  place.

## Worktree-state actions before coding

The `rfr-hook-hardening` worktree is on `5e233a0` with 4 files
staged and zero commits past main (`git rev-list --left-right
--count 5e233a0...main` reported `0	6` at session start; main has
since advanced to `9d3b9e59`, putting the branch 8 commits behind
with still no original commits). The branch is a parking spot, not
a history. The `cleanup-merged-branches.sh` script (commit
`9d3b9e5`) cannot help — it discovers *merged* branches via
`gh pr list` and `rfr-hook-hardening` has no PR. Use plain git:

1. From the main repo (cwd = `/home/jared/MyCode/claude-config`,
   not the worktree, since you're about to remove the worktree):
   ```
   git worktree remove .claude/worktrees/rfr-hook-hardening --force
   git branch -D rfr-hook-hardening
   ```
   `--force` on `worktree remove` discards the staged + unstaged
   changes; `-D` on `branch` allows deletion of an unmerged branch.
   Both are intentional — there's nothing in the worktree worth
   preserving (the salvaged content is captured in *this* plan
   file).
2. Create a new feature branch via the `branch-creation` skill,
   off a fresh `origin/main` tip. Suggested slug:
   `ready-for-review-active-marker-ceiling`.
3. Move this plan file to
   `claude/.claude/plans/ready-for-review-active-marker-ceiling.md`
   (or whatever slug `branch-creation` produced) so it ships in the
   same PR (per plan-review B17).
4. Apply the three targeted edits below: `marker.sh`,
   `require-ready-for-review.sh`, `test_require_ready_for_review.py`.

If, before discarding, the implementer wants the staged-diff text
as a reference, the worktree's `git diff --cached` output for this
session is preserved at
`/home/jared/.claude/projects/-home-jared-MyCode-claude-config/1b1eebec-e42c-480e-9ce0-a9ed06c4d096/tool-results/baecijcj8.txt`.

This avoids a `git stash` + `git rebase` + `git stash pop` dance
on a branch that has nothing worth preserving as a base.

## Verification

1. `pytest claude/.claude/hooks/tests/test_require_ready_for_review.py`
   — all existing tests pass; the three new tests pass; the
   activate-gate hook-alignment test asserts timestamp content.
2. `pytest claude/.claude/` — full suite green (catches any
   knock-on from the marker.sh `activate ready-for-review` shape
   change).
3. `ruff check claude/.claude/`.
4. **Manual smoke (positive path).** On a feature branch with an
   open PR in any repo:
   - Run `/ready-for-review` end-to-end.
   - `cat ~/.claude/.ready-for-review-active.d/<sid>` mid-run —
     content is a Unix epoch.
   - Iteration push during the run is allowed (mtime touched,
     content unchanged).
   - At step 7, marker is removed.
5. **Manual smoke (hard ceiling).** Activate the gate, then:
   - `echo "$(($(date +%s) - 5500))" > ~/.claude/.ready-for-review-active.d/<sid>`
     and `touch ~/.claude/.ready-for-review-active.d/<sid>`
   - Attempt `git push` — hook denies with the standard reason
     (the ceiling failure falls through to the completion-marker
     check, which finds no matching marker).
6. **Manual smoke (empty-content backward compat).**
   - `: > ~/.claude/.ready-for-review-active.d/<sid>; touch <same path>`
   - Attempt `git push` — hook denies (empty content fails the
     numeric guard).

## Out of scope

- Same hard-ceiling treatment for `require-respond-pr.sh`,
  `require-plan-review.sh`, `require-memory-skill.sh`. Same shape
  applies; deferred to a follow-up PR if/when the user wants it.
- Q3 work in any form (`refresh-session-id.sh`, the empirical
  probe, `claude/.claude/hooks/REFERENCES.md`). Solved differently
  on main.
- The Q1/Q2 rejection prose and "no wrap-up commit" SKILL.md note.
  User scoped to Q4 only.
- File-mode tightening on `~/.claude/sessions/` and marker dirs.
  CISO Low; deferred in original plan; still deferred.
