# Restore Tier B interactive prompting in cleanup-merged-branches

## Context

**Goal: stop `cleanup-merged-branches.sh` from auto-deleting Tier B branches (reachable from `origin/main`, no merged PR), and prompt for each one instead.**

PR #457 (merged 2026-07-21) made both Tier A and Tier B branches auto-delete without prompting. Its stated safety argument was that #459's open-PR guard "has already ruled out an open PR by the time either tier verdict is returned," so reachability is safe to trust. That argument has a hole: the open-PR guard only fires for branches that **have a PR**. A branch with *no PR at all* — active in-progress work that hasn't opened a PR yet — is never protected by it. Such a branch is "trivially reachable (freshly branched, not yet diverged)" precisely the way #457's own commit message warns about, so it classifies Tier B and is auto-deleted, its worktree removed. The only remaining protection is the live-process-cwd guard, which skips a worktree only if a process is *currently* `cd`'d inside at scan time — far too fragile to be the sole guard for uncommitted work.

A real dry-run surfaced two such branches (feature branches with active worktrees, no PR) queued for deletion. The intended outcome: Tier A (gh-confirmed merged PR whose tip matches) keeps auto-deleting — the friction-free common case — while Tier B prompts interactively, and skips with a warning when there is no TTY to prompt on. This is the pre-#457 behavior, minus the `--yes` auto-confirm flag (the user does not want that escape hatch reintroduced).

## Approach

Reverse the UX portion of #457's script hunk while keeping #459's open-PR guard and Guard 2 (Tier-A tip verification) fully intact — those are orthogonal and correct. Concretely, restore the tier-aware confirmation pass and dry-run split, but do **not** restore the `--yes` flag: without it, the non-TTY path always skips Tier B with a warning rather than auto-confirming.

Classification logic in `classify_branch()` is unchanged — it already emits distinct `tier-a:` / `tier-b:` verdicts. The change is purely in how the detection loop, dry-run block, and cleanup pass *act* on a `tier-b:` verdict:

- **Tier A** → `TO_DELETE` directly (unchanged behavior).
- **Tier B, TTY stdin** → `printf … [y/N]` prompt; add to `TO_DELETE` only on `y`/`Y`.
- **Tier B, non-TTY stdin** → add to `SKIPPED_NEEDS_PROMPT`, report a warning, never delete.

**Why not a smarter heuristic (e.g. hard-skip Tier B branches that have a worktree)?** Considered and set aside per the user's explicit choice: "prompt all Tier B." A worktree-presence discriminator would protect the exact case that bit us, but it adds classification complexity the user did not ask for and prompting already covers the risk. Keep the fix minimal and reversible.

**Why not keep `--yes` for the non-TTY case?** #457 removed `--yes`, and the user has a standing preference against it (never auto-confirm). Non-TTY skip-with-warning is the safe reading of "always prompt" — it never deletes without an explicit human `y`.

### Assumption ledger

Root problem: Tier B auto-delete (from #457) destroys active, PR-less work because the open-PR guard it relied on does not cover branches with no PR.

- **Restore per-branch `[y/N]` prompt for Tier B** — justification anchored to `root`: the prompt is the mechanism that puts a human in the loop before deleting a branch that only *looks* merged via reachability. Lighter primitives weighed: (1) a global "delete N Tier B branches? [y/N]" one-shot prompt — rejected, it can't distinguish the one active-work branch from genuinely-merged ones in the same batch; (2) print-and-exit with no deletion of Tier B at all — rejected, it removes the legitimate direct-merge-to-main cleanup use case #457 was serving. Per-branch prompt is the minimum that both protects active work and preserves legitimate cleanup.
- **Non-TTY → skip-with-warning (no `--yes`)** — anchored to `root`: with no terminal there is no safe way to confirm, so fail toward preservation. `[engineer-verified]` the user declined `--yes`.
- **`read -r _REPLY || _REPLY=""` guards EOF under `set -e`** — anchored to `root`: a bare `read` in the loop is not `set -e`-exempt, so an EOF must resolve to "declined/kept," never to a script-fatal abort (which would also drop pending Tier A deletes). `[verified: git show d992092 restores a *bare* `read -r _REPLY` with no guard — proof the problem exists; the `|| _REPLY=""` is an intentional override of the verbatim restore, NOT part of it. `set -e` semantics per shell-script-conventions.md]`.
- **`classify_branch()` verdicts already carry the tier distinction** `[verified: script lines 311–344 emit `tier-a:`/`tier-b:`]` — no classifier change needed; only the consumers change.
- **#459's open-PR guard and Guard 2 are independent of the prompting change** `[verified: guards live in `classify_branch`, before any tier verdict; the prompt lives in the post-classification cleanup pass]`.

## Critical files

- **`claude/.claude/scripts/cleanup-merged-branches.sh`** (modify) — reverse #457's UX hunk:
  - Header comment block (lines ~22–30, ~51–53): restore "Tier A deletes without prompting; Tier B prompts interactively; non-TTY skips Tier B with a warning" description.
  - Re-add `declare -a TIER_VALUES=()` and push `"A"`/`"B"` in the detection loop (reuse the existing `tier-a:`/`tier-b:` case arms — no new parsing).
  - Dry-run block: split back into a Tier A heading ("Would clean up (confirmed merged):") and a Tier B heading ("Probable merges (would prompt):") — drop the "--yes to auto-confirm" wording.
  - Confirmation pass: replace the unconditional `TO_DELETE` fill with the tier-aware pass (Tier A → delete; Tier B → prompt on TTY, `SKIPPED_NEEDS_PROMPT` on non-TTY). Re-add the `SKIPPED_NEEDS_PROMPT` reporting at both the early-exit and end-of-run sites. Do **not** re-add `ASSUME_YES` / `--yes`.
  - **EOF-safety on the prompt (platform review, B9):** the restored `read -r _REPLY` is a bare statement in a `for` loop, so it is NOT exempt from `set -e`. On EOF (Ctrl-D, or a pty test that closes its master before sending a line) `read` returns nonzero and `set -e` aborts the whole script mid-loop — which, because Tier A and Tier B share one confirmation loop, would also drop every not-yet-appended Tier A branch, violating "Tier A always deletes." Write it `read -r _REPLY || _REPLY=""` so EOF is treated as declining (branch kept), never as a script-fatal error and never as delete.
  - **Strip the `--yes` wording from the skip-report strings (platform review, B11/dead-reference):** #457's reverse diff restores two `printf` strings reading `"...(no TTY for prompt; rerun with --yes or from a terminal): %s\n"` at the early-exit and end-of-run sites. `--yes` is a removed flag (`usage()` exits 2 on it), so recover these strings but drop "; rerun with --yes or from a terminal" — e.g. `"Skipped %d probable-merge branch(es) (no TTY for prompt): %s\n"`. This is the same fix already applied to the dry-run heading; apply it to all three sites, not just the heading.
- **`claude/.claude/scripts/tests/test_cleanup_merged_branches.py`** (modify) — rewrite `TestTierBReachableNoMergedPR` **and two Tier-B tests in other classes the first draft missed** (SDET review, B8):
  - `TestTierBReachableNoMergedPR`:
    - TTY case → assert a `[y/N]` prompt appears; drive `y` (deletes) and `N` (survives) by writing to the pty master. **The current pty block does NOT write to the master** — #457 stripped the `os.write(master_fd, b"y\n")` line when it collapsed the y/n tests into one no-prompt test. Recover the `os.write(master_fd, b"y\n")` / `b"n\n"` lines from `git show d992092`'s reverse diff (same commit already cited below); without the write the test hangs to the 30s timeout instead of failing fast.
    - **Two-Tier-B per-branch TTY test** (SDET re-review, B8) → a single TTY run with two Tier B branches, writing `y\n` then `n\n` to the pty master in sequence; assert branch A is deleted, branch B survives, and that **two separate `[y/N]` prompts** appear. This is the only test that exercises the plan's load-bearing design claim (ledger row: per-branch prompt chosen over a global one-shot because a batch can mix one active-work branch with genuinely-merged ones) — without it, an implementation that prompts once and reuses the answer for the whole batch passes every other test. Reuse the existing `pty.openpty()` harness.
    - Non-TTY case → assert the branch **survives** and the skip/warning line is printed; assert the warning does **not** contain `--yes` (platform review, dead-reference guard).
    - `test_tier_a_and_tier_b_both_deleted_together` → rename (Tier B no longer deletes under non-TTY) to e.g. `test_tier_a_deletes_tier_b_skipped_non_tty`; assert Tier A deleted, Tier B survives with a skip warning.
    - `test_open_pr_survives_alongside_tier_b_auto_delete_same_run` → rework/rename: under non-TTY the Tier B branch now survives (skipped, not deleted); preserve the real assertion (open-PR branch survives), reframe Tier B as skipped.
    - `test_dry_run_lists_confirmed_and_reachable_together` → update to the two-heading output ("Would clean up (confirmed merged):" + "Probable merges (would prompt):").
  - **`TestTierBWithStaleMergedRowReportsBothSignals.test_tier_b_reachable_with_stale_merged_row_still_cleaned`** (~line 1551) → currently asserts the branch is deleted (`ref_check.returncode != 0`); it runs non-TTY, so invert to survives (`returncode == 0`) and assert the skip warning.
  - **`TestCheckedOutTierBBranchReportsSkip.test_checked_out_tier_b_branch_reports_reason_and_survives`** (~line 1481) → **no change needed** (SDET re-review corrected the prior round's framing). A checked-out branch is *structurally excluded* from `MERGED_BRANCHES`/`TO_DELETE`: the classification loop `continue`s on `$_B = $CURRENT_HEAD`, and `checked_out_skip_line()` reports it via a disjoint side-channel that never enters the new tier-aware confirmation pass. The test already asserts survival and the `"Skipped: … (currently checked out)"` message, so restoring Tier-B prompting cannot regress it. Leave it untouched; do not add the companion assertion the prior draft floated — there is no precedence ambiguity to resolve.
  - **Add an EOF test with pinned branch ordering** (platform + SDET re-review): a pty run that closes the master without sending a line → script exits cleanly (Tier B kept, Tier A still deleted), not a `set -e` abort. **The branch names must sort the Tier B branch *before* the Tier A branch in `git for-each-ref`'s default (alphabetical) order** — e.g. `aaa-tier-b` and `zzz-tier-a`, not the file's usual `tier-a-branch`/`tier-b-branch` convention. Rationale: the EOF-safety bug drops *not-yet-appended* Tier A branches when `set -e` aborts mid-loop; if Tier A sorts first it is already in `TO_DELETE` before the loop reaches the EOF-triggering Tier B branch, so the test would pass even with the guard absent — failing to exercise the very regression it guards. With Tier B visited first, assert the later Tier A branch is still deleted despite the EOF. Include at least two Tier B branches so the test also documents that every post-EOF Tier B `read` resolves to "keep" (the platform reviewer's multi-branch note), not just the first.
  - **Thread `stdin` through `_run_script()` and default it to `subprocess.DEVNULL`** (SDET re-review, B10) — `_run_script()` (~line 223, `def _run_script(repo, env, args=None)`) has no `stdin` parameter and never passes one to `subprocess.run`, so the plan's earlier "pin `stdin=DEVNULL` on every call" is not literally executable — `_run_script(..., stdin=subprocess.DEVNULL)` raises `TypeError`. Fix at the helper, not per-call-site: add a `stdin=subprocess.DEVNULL` parameter and forward it to `subprocess.run`. Default-safe for *all* callers (no existing `_run_script()` caller needs a live TTY — the TTY/pty tests bypass the helper entirely via direct `subprocess.Popen`), which is more robust than a per-call opt-in list a future test author can forget and thereby reintroduce the TTY-vs-non-TTY flake this guards against.
  - **Keep** the `--yes`-is-a-removed-flag regression guard (line ~607) unchanged — `--yes` stays gone.
- **`docs/scripts.md`** (modify, line ~39) — rewrite the "Both signals auto-delete without prompting" sentence to: Tier A auto-deletes; Tier B prompts (non-TTY skips with a warning).
- **`CHANGELOG.md`** (modify) — one entry noting Tier B prompting restored and why (#457 auto-delete could remove active PR-less work).

**Reuse opportunities:** the `pty.openpty()` prompt-driving harness already exists in the test file (`test_reachable_no_pr_deletes_with_tty_stdin` currently uses it) — reuse it for the y/N cases. The `SKIPPED_NEEDS_PROMPT` array, its early-exit report, and its end-of-run report are recoverable verbatim from #457's reverse diff (`git show d992092`).

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_cleanup_merged_branches.py` from the worktree — all Tier A, Tier B, Tier C, open-PR-guard, stale-name, and worktree-in-use cases pass.
- `../../../.venv/bin/ruff check claude/.claude/` and `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` clean.
- Manual: `cleanup-merged-branches.sh --dry-run` shows Tier A under "confirmed merged" and any Tier B under "would prompt"; a plain run in a terminal prompts per Tier B branch; a plain run with stdin from `/dev/null` skips Tier B with a warning and deletes only Tier A.

## Out of scope

- The worktree-presence discriminator (hard-skip Tier B branches with a worktree) — explicitly declined by the user; noted here only so a future reader knows it was considered.
- Any change to Guard 1 (open-PR) or Guard 2 (Tier-A tip match) — correct as-is.
