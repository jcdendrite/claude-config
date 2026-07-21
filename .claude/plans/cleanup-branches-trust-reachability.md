# Plan: treat "merged directly without a PR" as a confident cleanup signal

**Prerequisite (resolved):** this plan was blocked on an open-PR guard —
without it, auto-deleting Tier B widened the reused-name closure risk (see
**Update after PR #459** below). PR #459 landed that guard; this plan now
implements on top of it.

## Context

`cleanup-merged-branches.sh` is stowed globally and runs in every repo. Some
repos merge straight to `main` without ever opening a PR (common in private
repos). In those repos the script *already detects* the merged branches — via
its reachability check (`git merge-base --is-ancestor <branch> origin/main`) —
but it classifies them as a **weak "probable merge" signal ("Tier B")** that
must be individually confirmed at an interactive prompt (or auto-confirmed with
`--yes`). The observed friction:

```
delete 'discovery-audit-log-split' (reachable from origin/main; no merged PR for this name)? [y/N]: n
...
nothing to clean
```

The prompt framing ("no merged PR for this name") reads as a warning, so the
branches get declined reflexively and never cleaned up. The intended outcome:
a branch whose commits are all in `origin/main` should be cleaned up
confidently — no prompt, no `--yes`, no per-repo configuration.

**Why no per-repo opt-in** (marker file / git-config / CLI flag were all
considered and rejected by the user as over-engineered): the reachability rule
is *already* a universally safe merge signal (proof below). It needs no
per-repo gating — it needs to be trusted.

**Deferred (user's call):** direct merges that are **not** reachable from
`main` — local squash- or rebase-merges, whose commits land under rewritten
SHAs. Detecting those needs patch-id / `git cherry` matching with its own
false-positive profile; it is a separate feature, out of scope here.

## Safety analysis (answers "do we ever delete un-merged work?")

**No.** A branch is flagged only when `git merge-base --is-ancestor <branch>
origin/main` is true, i.e. **every commit reachable from the branch is already
reachable from `origin/main`.** The contrapositive is the safety guarantee:

- Work committed on a branch but **not yet merged** (open PR, or just local
  commits) has at least one commit absent from `origin/main` → `is-ancestor`
  is **false** → the branch is **never flagged**. This holds regardless of
  whether the repo requires PRs / disallows auto-merge — un-merged commits are
  simply not ancestors of `origin/main`.
- The check runs against **`origin/main`** (freshly `git fetch`ed at scan
  start), not local `main`. So a branch merged **locally but not yet pushed**
  is also not flagged until the merge actually reaches the remote.
- A stale/failed fetch only makes `origin/main` *older*, which flags *fewer*
  branches (false negatives), never more — the unsafe direction is impossible.

**Sole residual false-positive (superseded — see Update below):** a branch
with **zero commits of its own** — a never-committed placeholder branch, or a
long-lived branch that is *momentarily* fully caught-up to `main`.
Topologically these are indistinguishable from a merged branch (both: tip is
an ancestor of `origin/main`), so reachability alone cannot distinguish them.
This analysis originally dismissed the cost as "bounded and non-destructive —
at worst an empty branch pointer is removed." That dismissal was wrong: a
branch in this exact shape can carry an **open, actively-reviewed PR** — the
production incident behind PR #459 confirmed the general version of this gap
in practice (there, via Tier A's name-reuse path, but the same reachability
blind spot applies to Tier B). Deleting such a branch doesn't just remove a
recreatable pointer; it silently closes a live PR. The current-branch skip and
live-worktree guard don't cover this case (an open PR can exist on a branch
neither checked out nor in a live worktree). See **Update after PR #459**
below for the actual guard that closes this gap.

## Update after PR #459

PR #459 (merged, see `.claude/plans/cleanup-branches-open-pr-guard.md`) adds a
`classify_branch()` open-PR guard that runs *before* either tier is
considered, for both Tier A and Tier B. This is the missing piece the
paragraph above identifies: reachability was never sufficient on its own to
prove "no active review is happening" — it only proves "these commits exist
in `origin/main`'s history." With the open-PR guard in place, unconditional
Tier-B deletion becomes safe: by the time a branch reaches a `tier-b` verdict,
`classify_branch()` has already confirmed no open PR references that head
name. This plan's implementation (below) targets `classify_branch()`'s
`tier-a`/`tier-b` verdicts directly, rather than the pre-#459 inline
detection loop the rest of this document was originally written against.

## Approach

Collapse the two-tier *confidence* model into one **confident** path while
keeping the two-tier *labelling* for output provenance:

1. **Both signals auto-delete.** A branch flagged by either `gh pr list`
   (confirmed merged PR) **or** reachability (`is-ancestor`) goes straight to
   the cleanup path. Reachability stops being prompt-gated.
2. **Remove the interactive confirmation entirely** — the `read -r`/`[y/N]`
   prompt, the `SKIPPED_NEEDS_PROMPT` array, and the non-TTY "skipped with
   warning" branch. None have a purpose once reachability is trusted.
3. **Remove the `--yes` flag.** It existed only to auto-confirm the reachability
   prompt; with the prompt gone it is functionless. (Aligns with the standing
   "never `--yes`" preference.) This churns the flag out of the arg parser,
   `permissions.allow`, tests, and docs.
4. **Keep tier labels for messaging only** so the output still shows *why* each
   branch was deleted:
   - confirmed PR → `PR #N, merged YYYY-MM-DD`
   - reachability → e.g. `merged directly (reachable from origin/main, no PR)`

Rationale for keeping the labels: transparency. The user should see the
provenance of each deletion in the "Cleaned up:" list and in `--dry-run`,
even though both now act without prompting.

### Alternatives considered

- **Per-repo opt-in (marker file / git-config / `--reachable-is-merged` flag):**
  rejected by the user as over-engineered; the safety proof shows the rule is
  universally safe, so gating adds config surface for no safety gain.
- **Keep prompting but reword the message:** does not remove the reported
  friction (still one confirmation per branch, every run).
- **Keep `--yes` as an accepted no-op:** avoids a few test edits but leaves a
  confusing "auto-confirm" flag with nothing to confirm. Removal is cleaner;
  this is the one reversible decision — fall back to no-op only if you'd rather
  not touch the `permissions.allow` entries.

## Critical files

- **`claude/.claude/scripts/cleanup-merged-branches.sh`** — the change lands here:
  - **Header comment:** rewrite the Tier A/B/C block and the `--yes`/usage lines
    to describe one confident path (PR-confirmed *and* reachability) with the
    safety rationale; drop the "prompt interactively / `--yes` auto-confirms /
    non-TTY skip" description.
  - **Arg parser:** remove the `--yes` case and `ASSUME_YES`. Also update the
    `usage()` function's output string from `[--dry-run] [--yes]` to
    `[--dry-run]` — otherwise `cleanup-merged-branches --yes` (now an invalid
    arg → `exit 2`) prints a usage line that still advertises `--yes`.
  - **Confirmation pass** (`TO_DELETE` / `SKIPPED_NEEDS_PROMPT` loop): route both
    tier A and tier B into `TO_DELETE`; delete the prompt, the `ASSUME_YES`
    branch, and the `SKIPPED_NEEDS_PROMPT` accumulation + its two "no TTY"
    summary blocks.
  - **`--dry-run` section:** merge the "Probable merges (would prompt; `--yes`…)"
    heading into the single "Would clean up" list, with the reachability label.
  - **Reuse, do not reimplement:** the enumeration/`is-ancestor` scan,
    `collect_process_cwds` / `worktree_in_use` live-worktree guard, the
    porcelain worktree-path resolution, and the fast-forward block are all
    unchanged — only the *routing* of tier-B branches changes.
- **`.claude/settings.json`** (repo-root, non-stowed) — remove the two `--yes`
  `permissions.allow` entries (`cleanup-merged-branches --yes` and the
  `~/.claude/scripts/...--yes` form). Leave the base + `--dry-run` entries in
  the stowed `claude/.claude/settings.json` as-is.
- **`claude/.claude/scripts/tests/test_cleanup_merged_branches.py`** — update the
  behavior-changed tests (mechanical):
  - Rewrite/replace `test_reachable_but_no_merged_pr_prompts`,
    `test_reachable_but_no_merged_pr_skipped_on_n`,
    `test_reachable_no_pr_non_tty_no_yes_skips`,
    `test_reachable_no_pr_with_yes_flag_deletes` → assert a reachable branch is
    auto-deleted by a **plain** `cleanup-merged-branches` invocation (TTY and
    non-TTY), no prompt emitted, no `--yes`.
  - `test_dry_run_separates_confirmed_and_probable` /
    `test_dry_run_with_yes_flag_no_prompt` → assert the reachability branch
    appears in the unified "would clean up" list with its label; drop the
    "probable"/"would prompt" assertions and the `--yes` variant.
  - Drop `--yes` from the invocations in the locked-worktree and
    `test_unmerged_branch_not_touched` tests; use the plain form.
  - `test_duplicate_flags_are_idempotent` / `test_invalid_args_exit_2` → drop
    `--yes` from the valid-dup set; `--yes` is now an invalid arg (exit 2) — add
    a case asserting that, so the removal is enforced.
  - **Preserve** `test_unmerged_branch_not_touched` (Tier C stays untouched) —
    it is the regression guard for the safety property; keep it, only swap the
    invocation.
- **`docs/scripts.md`** — rewrite the `cleanup-merged-branches.sh` paragraph and
  the usage block (lines ~39–44): one confident path, remove the `--yes` line and
  the "prompts for reachability-only branches" comment; keep the worktree-guard
  paragraph unchanged.
- **`CHANGELOG.md`** — add a new entry per repo convention (do not edit prior
  entries — preserved record).

## Verification

- **Unit tests:** `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_cleanup_merged_branches.py`
  from a worktree (or `.venv/bin/pytest …` from the main tree). All updated
  tests green; the new "reachable auto-deletes without `--yes`", "no prompt
  emitted", and "`--yes` → exit 2" cases pass.
- **Lint:** `../../../.venv/bin/ruff check claude/.claude/scripts/tests/` (test
  file) — script itself is bash; sanity-check with `bash -n
  claude/.claude/scripts/cleanup-merged-branches.sh`.
- **Manual smoke (safe, in a scratch clone):**
  - `--dry-run` in a repo with a reachable-no-PR branch → it now lists under
    "would clean up" (not "probable"), no prompt text.
  - Plain run → the reachable branch is deleted with a reason label; no
    `[y/N]` appears; a branch with un-pushed commits is **not** touched
    (regression check of the safety property).
  - `cleanup-merged-branches --yes` → exits 2 (flag removed).

## Coordination note

A separate pending plan (`.claude/plans/https-github-com-jcdendrite-claude-confi-melodic-flurry.md`)
rewrites this same script's array handling. The current script already reflects
value-parallel indexed arrays, so that work appears landed — but confirm before
implementing; if it is still in flight, the two changes touch overlapping lines
and need a merge-order decision.

## Out of scope

- Non-reachable direct merges (local squash / rebase-merge) — deferred by
  decision; needs patch-id/`git cherry` detection.
- Any per-repo opt-in mechanism (marker file, git-config, flag) — rejected as
  over-engineered.
