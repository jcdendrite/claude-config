# Plan: Stop `cleanup-merged-branches.sh` from deleting branches with open/unmerged work

## Context

On 2026-07-19, an open PR in a private project repo (referred to below as **PR #14**) was closed and its branch deleted — not by a human, and not by an agent "deciding" to close it. The user ran a routine disk-cleanup task (*"run through all directories … and run cleanup-merged-branches"*). The agent faithfully ran `~/.claude/scripts/cleanup-merged-branches.sh` across ~37 repos; a latent bug in that script destroyed the branch.

**Root cause — Tier A matches a merged PR by branch *name* only, with no verification that the current branch tip belongs to that merge.** The script's `classify` logic:

- **Tier A** (`cleanup-merged-branches.sh:229-256`): `gh pr list --head "$BRANCH" --state merged --limit 1` → if *any* merged PR ever used that head-branch name, the branch is "confirmed merged" and deleted (worktree + local + `git push origin --delete`).
- **Tier B** (`:258-263`): else if `git merge-base --is-ancestor "$BRANCH" origin/<default>` — reachability — clean up.

A single head-branch name had been used by **PR #7 (merged 2026-06-16)**, then *reused* for **PR #14 (open; tip NOT reachable from `origin/main`)**. Tier A saw merged #7, classified the branch "merged," and deleted it. Deleting the head branch of open PR #14 is what GitHub logged as `closed` + `head_ref_deleted`, attributed to the machine's `gh` token owner. Tier B would have been *safe* here (reachability was false) — Tier A short-circuits before Tier B and never checks reachability or tip identity.

The existing plan `~/.claude/plans/cleanup-merged-branches-needs-to-account-toasty-dream.md` contains a "Safety analysis: do we ever delete un-merged work? **No.**" proof — but it only reasons about the **reachability** path. It never analyzed Tier A, which is exactly the hole.

**Blast radius (verified across every repo in the affected org):** the run deleted 19 remote branches; **PR #14 was the only casualty.** The other 18 branches all belonged to already-merged PRs (harmless post-merge cleanup). No other open/unmerged PR was affected.

**Recovery status:** already complete — the user restored the branch and reopened PR #14 (`reopened` 2026-07-21T05:49:11Z; currently OPEN). This plan is prevention only.

**Intended outcome:** `cleanup-merged-branches.sh` must never delete a branch that has an open PR, and must never treat a name-reused branch as merged when its current tip was not part of the referenced merge — while still cleaning genuinely-merged branches (including squash/rebase merges whose tips aren't reachable).

## Approach

Two guards, added inside the branch-classification logic. **Land this fix on its own, before** the pending toasty-dream Tier-B change (that plan removes the Tier-B prompt/`--yes` and makes Tier B auto-delete — which *widens* this risk surface, so the open-PR guard must exist first).

**Guard 1 — Open-PR guard (both tiers).** Before classifying a branch as merged, check for an open PR on that head branch. If one exists, never delete; record it as skipped with the PR number. This directly encodes the safety property ("don't close my open PRs") and by itself would have prevented this incident (#14 was open).

**Guard 2 — Tier-A tip verification (root-cause fix).** A Tier-A merged-PR-by-name match qualifies for deletion **only if** the branch's current tip actually corresponds to that merge — i.e. `git merge-base --is-ancestor "$BRANCH" origin/<default>` **OR** the current tip (full 40-char `git rev-parse "$BRANCH"`) equals a merged PR's recorded `headRefOid` (compare full SHAs, never abbreviated). This is the precise discriminator:
- Squash/rebase-merged branch (legitimate cleanup): tip not reachable, but tip **==** the merged PR's `headRefOid` → still deleted. ✅
- Name reused with new commits (the incident): tip not reachable **and** tip **≠** old merged PR's `headRefOid` → skipped as a stale name match. ✅
- Stale *local* branch (behind the pushed tip): tip ≠ `headRefOid` → skipped. Conservative and acceptable — document as intended, not a bug.

If neither condition holds, the branch is skipped with reason "merged PR #N by name only; current tip not part of that merge — likely a reused branch name."

**Fail-closed on ambiguous data (critical).** The existing lookups use `gh … 2>/dev/null || true`, which collapses a *failed* `gh` call (secondary rate-limit during a multi-repo sweep, transient network, token expiry) into an empty result indistinguishable from a genuine "no PR." That re-arms the exact incident one layer down: an errored open-PR check reads as "no open PR" → the branch becomes deletable. The helper must **fail closed** — on any non-zero `gh` exit, skip the branch (`skip-error`), never delete. Distinguish the exit code from an empty-but-successful `[]` by capturing `gh` output *before* parsing, not piping straight into `python3` (which loses `gh`'s exit code):
```bash
if ! PR_JSON=$(gh pr list --head "$BRANCH" --state all --limit 100 \
      --json number,state,mergedAt,headRefOid 2>/dev/null); then
  printf 'skip-error\n'; return 0        # gh failed → fail closed, do not delete
fi
# $PR_JSON now trusted; "[]" (exit 0) is the real "no PR" case → eligible for Tier B
```

**Scan ALL PRs for a reused name (not `--limit 1`).** A reused branch name returns **multiple** PRs from `--head … --state all`; the current code's `--limit 1` is precisely what let merged #7 mask open #14. Set `--limit 100` and inspect every returned row: skip if **any** row is `OPEN`; for the tip check, match against **any** `MERGED` row's `headRefOid`. `gh` returns `state` **uppercase** (`OPEN`/`CLOSED`/`MERGED`) — compare uppercase; treat merged as `state=="MERGED"` (equivalently `mergedAt != null`, since a closed-unmerged reused PR has `mergedAt=null`).

**Single classification helper (avoid sibling drift, one gh call).** The merged-PR-by-name check is currently duplicated across three sites — the detection loop (`:229`), the dry-run preview (`:349`), and the checked-out messaging block (`:601`). Extract one `classify_branch <branch>` helper returning a single verdict via **stdout string** (`tier-a` / `tier-b` / `skip-open-pr:<n>` / `skip-stale-name:<n>` / `skip-error` / `none`), doing the single `gh pr list … --state all` call above. Required properties:
- **Always `return 0`** (verdict communicated only on stdout). A `return`-nonzero helper under `set -e` would abort the entire sweep on the first skip — the reason the current script wraps everything in `|| true`. Callers use plain `verdict=$(classify_branch "$B")`.
- **Pure** — no git-destructive side effects — so the messaging-only sites can call it safely.
- **Classify once, store the verdict.** Run the helper once per branch in the detection loop and store its verdict (extend the existing `MERGED_BRANCHES`/`TIER_VALUES` array pattern at `:276-282`); dry-run preview and real deletion both read the **stored** verdict. Do not re-invoke per site — re-invoking doubles `gh` calls and opens a TOCTOU gap where a PR opened/merged between calls makes dry-run and real-run disagree. The checked-out block (`:597`, for `$CURRENT_HEAD`, which the detection loop skips) still calls the helper once, but uses the verdict **only to gate its message**, never to delete.

*Rationale for not choosing the heavier options:* blocking remote deletion entirely (considered) over-corrects — remote cleanup is the disk/hygiene value the user wants, and the two guards make it safe. A per-repo config/marker (considered) is rejected for the same reason the toasty-dream plan rejected it: the safety invariant is universal, not per-repo.

## Critical files

- **`MyCode/claude-config/claude/.claude/scripts/cleanup-merged-branches.sh`** — the fix. This is the canonical source; `~/.claude/scripts/cleanup-merged-branches.sh` resolves into it via stow (symlink), so editing the repo file is immediately live — no re-stow step.
  - Add `classify_branch()` helper (open-PR guard + Tier-A tip check + single `gh pr list --state all` call).
  - Route the detection loop (`:224-268`), dry-run preview (`:333-360`), and checked-out block (`:597-615`) through it.
  - Add a `SKIPPED_OPEN_PR` / `SKIPPED_STALE_NAME` reporting path so a skipped branch prints *why* (e.g. `Skipped: reused-branch-name (open PR #14)`).
- **Reuse:** the existing `git merge-base --is-ancestor "$BRANCH" origin/<default>` reachability check (`:258`) is exactly the Tier-A tip condition — call the same check, don't reimplement.
- **`MyCode/claude-config/claude/.claude/scripts/tests/test_cleanup_merged_branches.py`** — the existing pytest harness (40+ cases) with a `fake_gh` PATH shim keyed on `--head` (shim at ~lines 87–131). Extend **this** suite in Python — do **not** add a bats/shell file (that would fork the suite and duplicate fixtures). The load-bearing change is a **fixture migration**: the shim currently emits only `number, headRefName, state, mergedAt` for a single `--state merged` record and models no open PR. It must be extended to (a) emit `headRefOid`, (b) handle `--state all` (returning multi-row arrays), and (c) model open-state PRs — otherwise Guard 2's tip check cannot be exercised and would be untested. This shim edit touches the code path of every existing case, so "run the suite, all pass" understates it — treat it as a migration and re-green existing assertions (e.g. the `"PR #110"` string checks).
- **`~/.claude/plans/cleanup-merged-branches-needs-to-account-toasty-dream.md`** — add a one-line prerequisite note: "Blocked on the open-PR guard (see this fix) — auto-deleting Tier B without it widens the reused-name closure risk." (Documentation-only touch, per the "land first" decision.)

Implementation happens in the **`MyCode/claude-config`** repo (not the private project repo that is the current session cwd) — create the branch there per `branch-creation`.

## Verification

Extend the existing hermetic pytest harness (`test_cleanup_merged_branches.py`) — temp git repos + the `fake_gh` shim returning canned JSON. **Do not** stand up a live GitHub repo: the guard invariant is a pure function of four injectable inputs (open-PR present?, `mergedAt`, `headRefOid`, reachability), and a real repo cannot reliably *control* the `headRefOid`/state values the guard keys on (it can only observe them) — it would be slower, flaky, and network/auth-bound. First migrate the shim (see Critical files), then add these cases. Cases that assert "not deleted" must run against the **real deletion path** (non-dry-run), because `classify_branch` routes the delete loop, the dry-run preview, and the checked-out block separately — a guard correct in preview but wrong in the delete loop passes every dry-run assertion while still deleting the branch.

1. **Incident shape — both PRs, real run (the regression):** shim returns `[{state:OPEN, #14}, {state:MERGED, #7, headRefOid:OLD}]` for one head name; branch tip = new SHA ≠ OLD. Real (non-dry-run) execution → assert `git rev-parse --verify refs/heads/X` **still succeeds** and the remote ref survives; verdict `skip-open-pr:14`. Proves Guard 1 wins over a simultaneous Tier-A match and that multi-row arrays are scanned (no `--limit 1` masking).
2. **Fail-closed on gh error:** shim exits non-zero for one branch → assert that branch is **skipped, not deleted** (verdict `skip-error`), even when it is reachable from `origin/main` (i.e. fail-closed overrides Tier B).
2b. **Fail-closed on malformed JSON:** shim exits **zero** but writes non-JSON to stdout (a truncated body or a stray banner mixed into stdout — a real `gh` behavior) → same assertion as case 2. This is a second, independent fail-closed path: the parser's `JSONDecodeError` branch rather than the exit-code branch. Without it, a future edit that swallows the decode error and substitutes an empty PR list would bypass the open-PR guard exactly as the original incident did, while passing every other case here.
3. **Stale-name, no open PR:** merged PR by name (`headRefOid:OLD`), no open PR, tip ≠ OLD and not reachable → **not** cleaned; verdict `skip-stale-name`.
4. **Regression — squash-merge still cleaned:** merged PR, tip not reachable but tip == `headRefOid`, no open PR → cleaned (Tier A).
5. **Regression — Tier B still cleaned:** reachable from `origin/main`, no PR → cleaned.
6. **Regression — plain Tier A still cleaned:** unique name, one merged PR, tip matches, no open PR → cleaned.
7. **Closed-unmerged-only:** branch whose sole PR is closed-unmerged (`mergedAt=null`), not reachable → left untouched (Tier C), and does **not** emit a false stale-name skip.
8. **Checked-out incident branch:** incident-shape branch is `$CURRENT_HEAD` → the checked-out site reports the open-PR skip reason (Guard 1 honored at the third call site), never deletes.
9. Re-green the full existing suite after the shim migration (all 40+ cases).

## Out of scope

- The toasty-dream Tier-B auto-delete / `--yes` removal (separate plan; this one is its prerequisite).
- Any change to how `gh`/git credentials are scoped on the machine, or broader "agents must not run destructive git" policy — a real discussion, but a different change; note it to the user separately rather than bundling.
- Recovery of PR #14 — already done by the user.
