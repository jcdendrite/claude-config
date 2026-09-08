# Fix ancestor-of-merged-head detection in cleanup-merged-branches.sh

## Context

`classify_branch()` in `claude/.claude/scripts/cleanup-merged-branches.sh`
fails to recognize a branch as safe to delete when its local tip is behind
the commit that GitHub actually merged for that PR — for example, extra
commits landed on the PR's head branch after the local checkout last
fetched it, or the merge was a squash/rebase that produces a merge commit
with no ancestry link back to the feature branch's own commits. Today the
function only confirms Tier A ("confirmed merged, delete without
prompting") when the local tip equals the merged PR's `headRefOid` exactly,
and falls back to Tier B only when the local tip is directly reachable from
`origin/<default>` — neither test passes when the tip is a strict, proper
ancestor of the actual merged head rather than equal to it or already
folded into the default branch's own history. The observed effect is a
`skip-stale-name` verdict ("a merged PR shares this name, but the current
tip is not part of that merge — likely a reused branch name"), which is
wrong: the branch was not renamed or reused, its local checkout is simply
behind what got merged.

This was confirmed against 5 real branches in a private repo (not named
here per this repo's redaction rules): each had a `MERGED` PR under the
same head branch name, a local tip that did not match that PR's
`headRefOid`, and a local tip that was *not* an ancestor of
`origin/<default>` either — so each fell through to `skip-stale-name`
despite being fully merged. For all 5, `git fetch origin
refs/pull/<PR#>/head` recovered the PR's exact head commit, and `git
merge-base --is-ancestor <local-tip> <fetched-head>` confirmed the local
tip was a strict ancestor of it, i.e. a true subset of what was merged —
verified live against a GitHub-hosted repo this session. All 5 source
branches still existed on the remote at verification time; whether
`refs/pull/<PR#>/head` remains fetchable after GitHub also deletes the
source branch was not tested this session (see the Approach's assumption
ledger, row 2).

Intended outcome: `classify_branch()` recognizes this shape and reports it
as merged (not stale-name), so the branch is offered for cleanup instead of
being silently mischaracterized as a probable name reuse.

## Approach

When a branch's tip matches no merged PR's `headRefOid` **and** is not reachable from `origin/<default>`, `classify_branch()` gains one more test before it gives up: for each merged PR row `gh` already returned, ask git whether the local tip is an ancestor of that row's `headRefOid`, fetching the PR's `refs/pull/<n>/head` once if those objects aren't local yet. A row that answers yes proves every commit on the local branch is contained in a commit GitHub says it merged, so the branch is classified **Tier A** (deleted without a prompt) carrying a new `pr-head-ancestor` basis marker that the `--dry-run` preview prints, distinguishing it from an exact tip match. Any failure along that path — fetch refused, ref absent, timeout, non-GitHub remote — leaves today's `skip-stale-name` verdict exactly as it is.

**Decision: Tier A, not Tier B.** Four grounds, in order of weight:

1. **It is the same evidence class Tier A already auto-deletes on, generalized.** Today's Tier A fires on `headRefOid == tip`. `git merge-base --is-ancestor X X` is true, so equality is a special case of the ancestry relation — the new rule is a strict superset of the rule the script already trusts enough to delete without asking, and it preserves the property that makes Tier A safe: nothing reachable from the local tip is absent from what GitHub merged. Routing a strictly-stronger-than-Tier-B proof to Tier B's prompt would make evidence strength and confirmation level disagree.

   This equivalence is about the *relation*, not the *proof mechanism*, and carries one caveat:
   - `headRefOid == tip` is a byte comparison immune to local repository configuration, while `--is-ancestor` honors `refs/replace/*` and `.git/info/grafts` by default, so an operator-configured graft or replace-ref could make it report containment that doesn't correspond to the real, unmodified commit graph.
   - This is narrow and self-inflicted: it requires the operator to have deliberately configured grafts or replace refs on their own machine, not something GitHub/`gh` data can trigger.

   Net effect: Tier A's move here removes Tier B's human-in-the-loop backstop for exactly this evidence class, in exchange for `--dry-run` disclosure instead.
2. **Tier B would make the fix a near no-op in this script's documented invocation path.** Tier B prompts only when stdin is a TTY; with a non-TTY stdin it is skipped with a warning (`cleanup-merged-branches.sh:666-681`). The documented destructive invocation runs through a Claude Code `permissions.ask` prompt (`docs/scripts.md:81`) — i.e. a Bash tool call, not a TTY. At Tier B the reported 5 branches would trade one skip message ("likely a reused branch name") for a different one ("no TTY for prompt") and still never be cleaned.
3. **The residual risk Tier A adds over Tier B is a branch pointer with zero unique commits.** The only shape where the ancestry test passes but the operator still wants the ref is a genuinely reused name whose new tip has no commits of its own beyond the old merged head's history. The commits are by construction all recoverable; only the name is lost. The open-PR guard (`classify_branch`'s Guard 1) already skips any reused name carrying an open PR, and the live-worktree guard already skips any branch someone is sitting in.
4. **The new failure mode can only produce false negatives.** A failed fetch means the ancestry test never runs; the branch falls back to today's verdict. No fetch outcome can manufacture a Tier A that the API record and the local object graph don't jointly support.

Against this sits CLAUDE.md's confirm-before-destructive rule. It is satisfied by disclosure rather than by a prompt: the widening is recorded in the PR body and CHANGELOG, `--dry-run` remains the preview surface, and the dry-run line for these branches names the basis instead of reading identically to an exact match. That last point is why the verdict grammar changes at all.

**Verdict grammar: extend the existing token's payload, don't add a token.** `tier-a:<pr>:<merged-date>` becomes `tier-a:<pr>:<merged-date>:<basis>`, `basis` ∈ `tip-match` | `pr-head-ancestor`. A new top-level `tier-a-ancestor:` token was the obvious alternative and is the more dangerous one: the glob `tier-a:*` does not match `tier-a-ancestor:...`, and neither of the two `case` statements that switch on a verdict (`cleanup-merged-branches.sh:496-506`, `:544-577`) has a `*)` default arm — a forgotten arm drops the branch from the sweep silently. Keeping the tier in the token means `checked_out_skip_line()` needs no edit at all and any future call site that only asks "is this Tier A" is correct by construction. Making the field mandatory rather than optional keeps the parse a flat three-way split instead of a conditional.

**Every merged row is scanned, in the order `gh` returned them.** The exact-match arm already scans every `MERGED` row (`:403`) and `TestMultipleMergedRowsScanFullHistory` locks that. Checking only `merged_rows[0]` in the sibling arm would plant the same bug shape in the other arm of one structure. First ancestry hit wins, API order preserved — same "first match wins" rule the exact-match arm uses; the PR number is informational once containment is proven.

**Alternatives weighed for the fetch itself** (it is the heaviest primitive here — a network round trip inside a per-branch classifier that two call sites, one of them message-only, invoke):

- *Local objects only, no network.* Kept — as the first pass — but it cannot be the whole answer: the bug's premise is that the merged head is a commit this checkout never fetched, which is exactly why `cat-file` will miss it. It does earn its place as a gate, so a genuinely reused name whose oids are already local costs zero network calls.
- *`gh api /repos/{o}/{r}/compare/{base}...{head}`.* Set aside: it needs owner/repo resolution and a second API call of comparable cost, and it requires the **local tip** to exist on the remote — which is precisely what a post-merge branch deletion removes. It fails in the case we are fixing.
- *`gh api /repos/{o}/{r}/pulls/{n}/commits`, then membership-test the tip.* Set aside: paginated with a 250-commit ceiling, needs owner/repo, and answers a weaker question (is the tip one of the PR's own commits) than `merge-base --is-ancestor` answers directly with git's own reachability algebra.
- *`git fetch origin <headRefOid>` (fetch by raw SHA).* Set aside: depends on `uploadpack.allowReachableSHA1InWant` server-side, which GitHub does not document as enabled. `refs/pull/<n>/head` is GitHub's own documented recipe for retrieving a PR head.
- *Shallow/`--depth` fetch.* Set aside outright: git already negotiates down to the missing delta, and a shallow fetch would write shallow boundaries into the operator's repo.

The chosen fetch is bounded four ways:
- It fires only in the fallthrough: tip matched no `headRefOid`, tip not reachable from `origin/<default>`, at least one merged row exists, and at least one row's object is missing locally.
- It is a **loop of one `git fetch` per missing row's refspec** rather than one batched multi-refspec call, bounding the new network cost to one round trip per missing row — typically 1-3, not one per branch. The paragraph below justifies why batching is unsound, not merely non-preferred.
- It runs under `claude/.claude/hooks/_lib.sh:27`'s capping wrapper (already sourced at `cleanup-merged-branches.sh:96`), called via `_lib_capped_for 15` rather than the bare `_lib_capped` (which defaults to 5s) — matching the file's own existing network-bound precedent at `_lib.sh:623-631` (`_lib_cumulative_diff_hash`'s comment: "15s, not the shared 5s `_lib_capped` default: this is the only `_lib_capped` call site that's network-bound"), rather than reusing the filesystem-stall-scoped 5s default or inventing a third timeout literal. On a host with neither `timeout` nor `gtimeout` on `PATH`, `_lib_capped_for` runs the fetch uncapped — the same operator-visible behavior the file's existing network call already accepts, not a new risk this plan introduces.
- Because the API already told us each row's `headRefOid`, the fetch's output is never read — no `FETCH_HEAD` parse, and therefore no risk of a failed fetch leaving a previous branch's `FETCH_HEAD` in place to be misread as this branch's merged head.

**Fetches run per-row, in a loop, not as one batched multi-refspec call — confirmed necessary, not just cautious.** This session reproduced the failure mode live: a single `git fetch` given two refspecs (one resolvable, one nonexistent) against a real bare repo exited 128 ("couldn't find remote ref") and transferred the resolvable refspec's object to neither the object database nor any ref — the whole invocation aborts before any transfer, not just the unresolvable ref (git 2.43.0; ledger row 11, `[engineer-verified]`). A batched fetch across all missing rows would therefore silently fail to retrieve a sibling row's resolvable object whenever any other row's ref had aged out — exactly the shape M3 exists to handle. Each row's fetch attempt is instead independent: guarded with `|| true` so a 128 exit doesn't propagate past its own statement, doesn't affect any other row's fetch, and doesn't trip the surrounding `set -e` sweep. After every row's fetch attempt has completed (successful or not), `merged_row_containing_tip`'s local-object scan (M4) runs once more, unconditionally — a cheap local check against whatever objects actually landed, regardless of which individual attempts succeeded. Only when that re-scan still finds no containing row does classification fall through to today's `skip-stale-name`/`none`.

### Assumption ledger

**Root:** a local branch whose tip is a strict ancestor of the commit GitHub merged is reported as a probable reused name and never cleaned up, though nothing on it is unmerged.

**Givens**
- **G1 — GitHub owns `refs/pull/<n>/head`: what it resolves to, whether it is advertised, and how long it outlives the source branch.** Vendor-controlled; no design here can make the ref exist.
- **G2 — `gh pr list --json number,state,mergedAt,headRefOid` is the merge record available without a second API call, in `gh`'s row shape.** Tool contract; changing it means adding an API call, which the perf constraint rules out.
- **G3 — The script's stdin is not a TTY on its documented invocation path.** The harness owns stdin for a Bash tool call; the script cannot make itself interactive.

**Rows**
1. `refs/pull/<PR#>/head` on GitHub is fetchable by an authenticated client and resolves to that PR's `headRefOid`, including for old merged PRs. `[engineer-verified]` — run live this session against a GitHub-hosted repo.
2. That ref survives deletion of the PR's source branch. `[unverified]` — the live check covered only the still-present case, and GitHub's documentation was not read this session. Load-bearing for *coverage* only: mechanism M5 fails back to today's verdict when the ref is absent.
3. `git merge-base --is-ancestor A B` exiting 0 means every commit reachable from A is reachable from B, so deleting A's ref discards no commit content. `[verified: git's own ancestry primitive; the script already routes its Tier B decision through the identical call at cleanup-merged-branches.sh:432]`
4. Nothing in this script reads `FETCH_HEAD`; its three existing fetches (`:533`, `:775`, `:807`) discard it. `[verified: grep for FETCH_HEAD across cleanup-merged-branches.sh returns no reads]`
5. No test asserts a raw `classify_branch` verdict string — every `tier-a`/`tier-b` hit in the test file is a branch *name*, and all assertions are on stdout. `[verified: grep for tier-a|tier-b|classify_branch over claude/.claude/scripts/tests/test_cleanup_merged_branches.py]`
6. No test pins the Tier-A dry-run reason text `PR #<n>, merged <date>`. `[verified: grep for "Would clean up|merged 20" returns three hits, all on the section header line]`
7. Non-TTY stdin skips Tier B rather than prompting. `[verified: cleanup-merged-branches.sh:666-681]`
8. The exact-match arm already scans every `MERGED` row, and a test locks it. `[verified: cleanup-merged-branches.sh:403; TestMultipleMergedRowsScanFullHistory, test_cleanup_merged_branches.py:1728]`
9. The embedded classifier is a double-quoted shell string: `$`, backticks, and unescaped `"` are consumed by the shell before Python runs — comments included. `[verified: cleanup-merged-branches.sh:378-382]` Consequence for M6: no `re` pattern with `^`/`$` anchors may be introduced there.
10. `_lib_capped` caps at 5s via `timeout`/`gtimeout` and runs the command uncapped when neither exists; callers must check its exit status. `_lib_capped_for <n>` wraps exactly one command per call, so M5's per-row loop applies the 15s cap independently to each row's fetch attempt, not once per branch — the aggregate worst case is `15s × missing-row-count`, bounded today only by `gh pr list --limit 100` (`cleanup-merged-branches.sh:354-356`), not by anything M5 itself adds. `[verified: claude/.claude/hooks/_lib.sh:27-48, sourced at cleanup-merged-branches.sh:96]`
11. A single `git fetch` invocation carrying several refspecs aborts the whole transfer if any one refspec cannot be resolved on the remote — no object lands for any refspec, including ones that would otherwise resolve on their own. `[engineer-verified]` — reproduced live this session: a two-refspec fetch (one resolvable ref, one nonexistent ref) against a real bare repo exited 128 with "couldn't find remote ref," and the resolvable ref's object was absent afterward (git 2.43.0). This is why M5 fetches per-refspec in an independent loop rather than as one batched call — a batched call would silently fail to retrieve a sibling row's resolvable object whenever any other row's ref had aged out, defeating M3's "scan every merged row" guarantee for exactly the network-required case this mechanism exists to handle.
12. A bare git repo advertises refs outside `refs/heads/`, so a fixture can create `refs/pull/<n>/head` in the test's bare remote and fetch it from the local clone. `[unverified]` — standard git behavior, but no existing test in `claude/.claude/scripts/tests/` creates a non-standard ref on a bare remote (grep for `refs/pull`/`update-ref` across that directory returns none), so the first new test proves it.
13. The Tier A vs Tier B call is delegated to this design. `[engineer-verified]`
14. All 5 observed branches had a local tip that was a strict ancestor of the merged PR's `headRefOid`, recoverable via `git fetch origin refs/pull/<PR#>/head`. `[engineer-verified]`

**Mechanisms**
- **M1 — Classify the ancestor case Tier A (`TIER_VALUES` entry `A`, auto-delete), not Tier B.** `anchors: row3, row7, row13` — the containment proof is the same evidence class Tier A already deletes on, and Tier B's non-TTY arm would skip rather than prompt.
- **M2 — Add a fourth `basis` field to the existing `tier-a:` verdict instead of a new verdict token.** `anchors: row5, row6` — no test depends on the verdict string or on the current reason text, and keeping the token stable means neither `case` statement can silently drop the new shape.
- **M3 — Scan every merged row for containment, in `gh`'s row order, first hit wins.** `anchors: row8` — the sibling arm of this same structure already scans all rows.
- **M4 — Try `git cat-file -e <oid>^{commit}` and the ancestry test against locally-present objects before any network call.** `anchors: row3` — separates "not an ancestor" from "object missing", so a genuinely reused name still costs zero fetches.
- **M5 — Per-row fetch loop, then an unconditional re-scan.** `anchors: row1, row2, row10, row11`
  - For each row whose object is missing, in row order: `_lib_capped_for 15 git fetch --quiet origin refs/pull/<n>/head 2>/dev/null || true`, independent of every other row's attempt.
  - After every row's attempt has completed, re-run the local scan (M4) once, unconditionally.
  - Only a re-scan that still finds no containing row falls through to today's `skip-stale-name`/`none`.

  Per-refspec looping (rather than one batched multi-refspec fetch) is required for correctness, not just caution: row 11 confirms a single fetch invocation aborts entirely, transferring nothing, when any one of its refspecs is unresolvable, which would silently defeat M3's guarantee for exactly the multi-merged-row case this mechanism exists to handle. It bounds the new network cost at one round trip per missing row (typically 1-3, not per branch), uses the file's own 15s network-call precedent rather than the filesystem-stall-scoped 5s default, and makes row 2 non-load-bearing: a row whose ref has aged out simply fails its own fetch attempt (`|| true`, matching the existing idiom at `cleanup-merged-branches.sh:533`) without affecting sibling rows or aborting the surrounding `set -e` sweep.

  **Traded cost:** the loop applies the 15s cap per row rather than once per branch (row 10), so the aggregate worst case is `15s × missing-row-count`, bounded today only by `gh pr list --limit 100` (`:354-356`) — accepted given the scope (personal-machine CLI, not a production service) and the expected row count of 1-3.
- **M6 — Validate each row's fields inside the Python before they can reach a git argv: `number` must be all digits (otherwise `sys.exit(1)`, which the caller already maps to `skip-error`), `headRefOid` must be 40 lowercase hex characters (otherwise the row is emitted with an empty oid and skipped by the ancestry scan), using character-class membership rather than a regex.** `anchors: row9` — preserves the header's no-argument-injection property now that API-supplied values reach git operands, and a regex here would have its `$` anchor eaten by the shell.
- **M7 — Extract the scan into a named helper (`merged_row_containing_tip`) rather than inlining two passes in `classify_branch`.** `anchors: root` — the local-scan/fetch/re-scan sequence needs a name to be readable, and `classify_branch` is already the longest function in the file.

### Concrete edit shape

`classify_branch()` currently emits `stale:<pr>` from its embedded classifier and derives `stale_pr` from it at `:428`. The stale line becomes a space-separated table of validated triples — `stale:<pr>,<oid>,<merged-date> <pr>,<oid>,<merged-date> …` — preserving `merged_rows[0]` as the first triple so `stale_pr="${stale_rows%%,*}"` reproduces today's value for the unchanged `tier-b:` and `skip-stale-name:` messages. The new block goes between the reachability check's closing `fi` (`:439`) and the `skip-stale-name` fallback (`:441`), guarded on a non-empty `tip` (`:371` documents that it can be empty) and a non-empty row table, and printing `tier-a:<pr>:<date>:pr-head-ancestor` on a hit. The ancestry test takes `"$tip"` — the SHA already captured at `:371` — rather than the branch name the reachability check uses, so classification and proof are about the same commit. The detection loop's `tier-a:*` arm (`:545-552`) parses the fourth field and selects the reason string: `tip-match` keeps `PR #<n>, merged <date>` byte-for-byte; `pr-head-ancestor` appends `; local tip is an ancestor of that PR's merged head`. Comment blocks to update in the same edit: the file header's tier model (`:4-10`, `:22-25`), the verdict list (`:330-342`), and the `classify_branch` docstring's "exactly one `gh pr list` call and one read-only git reachability check" claim (`:326-328`), which stops being true — including for the message-only `checked_out_skip_line()` caller.

## Critical files

All paths relative to the repo root.

- **`claude/.claude/scripts/cleanup-merged-branches.sh`** — the whole change. Edit sites: header comment `:4-10` and `:22-25`; `classify_branch` docstring and verdict list `:322-347`; the embedded classifier's `stale` emission `:406-407` plus its new field validation; the `stale:*` case arm `:427-429`; the new ancestor block inserted between `:439` and `:441`; the `tier-a:` printf `:424`; the detection loop's `tier-a:*` arm `:545-552`. New helper `merged_row_containing_tip` defined above `classify_branch`.
  **Reuse:** `_lib_capped_for 15` (`claude/.claude/hooks/_lib.sh:38`, already sourced at `:96`) for the fetch cap, matching `_lib_cumulative_diff_hash`'s own network-call precedent at `_lib.sh:629` rather than the bare `_lib_capped`'s filesystem-stall-scoped 5s default — do not hand-roll `timeout 15 git`, which is `command not found` (127) on a stock macOS without coreutils. The ancestry call copies the shape already at `:432` (`2>/dev/null` inside an `if`, since `set -e` is suppressed there and a 128 "no such object" is indistinguishable from a 1 "not an ancestor" — both are correctly read as "no proof"). Each per-row `git fetch` must carry `--quiet`, `2>/dev/null`, and `|| true` — copying the existing idiom already at `:533` — both to keep `TestClassifierEmitsNoShellDiagnostics`'s empty-stderr guarantee (fetch progress goes to stderr) and to stop one row's unresolvable ref from tripping `set -e` and aborting the sweep for every remaining branch. The post-fetch re-scan (M4, re-run per the Approach section) runs unconditionally, once, after every row's fetch attempt has completed — not gated on any individual attempt's exit status.
- **`claude/.claude/scripts/tests/test_cleanup_merged_branches.py`** — new fixture helper plus new test classes. The fixture must place the merged head **only** on the bare remote so the fetch is genuinely required: capture the branch tip `T1` after `_make_feature_branch`, then build a descendant in the bare repo with plumbing — `git -C <bare> rev-parse <T1>^{tree}`, `git -C <bare> commit-tree <tree> -p <T1> -m …` (set `user.email`/`user.name` on the bare, `_init_repo` only configures the local), `git -C <bare> update-ref refs/pull/<n>/head <T2>` — then `git -C <bare> branch -D <branch>` to model GitHub's post-merge source deletion. Cases to cover:
  1. tip strictly behind the merged head, `refs/pull/<n>/head` present → branch deleted, no prompt, exit 0 (the fix).
  2. same fixture with `--dry-run` → listed under `Would clean up (confirmed merged):` with the ancestor basis in the reason, and `likely a reused branch name` absent.
  3. `refs/pull/<n>/head` absent on the remote (fetch fails) → branch survives, `likely a reused branch name` still reported (fail-back).
  4. genuine reuse — tip is *not* an ancestor of a real, fetchable merged head → branch survives, stale-name message unchanged.
  5. two merged rows where the *first* row's `refs/pull/<n1>/head` does not exist on the remote at all (not just locally-missing objects — genuinely unresolvable, modeling an aged-out PR ref) and the *second* row's does and contains the tip → still deleted, proving the per-row fetch loop (M5) retrieves the second row's object independently of the first row's fetch failure, and the post-fetch re-scan (M4, re-run unconditionally) picks it up. This case is the direct test of M5's per-refspec looping design: ledger row 11 confirms (empirically, this session) that a single *batched* multi-refspec fetch aborts entirely and transfers neither row's object the moment one refspec is unresolvable, so this fixture would fail against a batched-fetch implementation and only passes against the per-row loop. It does not mirror `TestMultipleMergedRowsScanFullHistory`'s placeholder-OID pattern (`"a"*40` with no backing ref), since an unfetchable placeholder row would conflate "row is a deliberate non-match" with "row's ref is unresolvable" and not actually exercise the fetch-failure path.
  6. stderr stays empty on the ancestor path, extending `TestClassifierEmitsNoShellDiagnostics`'s guarantee to the arm that now shells out to `git fetch`.
  7. the merged PR's `headRefOid` commit is already present locally before the classifier runs (e.g., reachable via the existing `origin/<default>` fetch for an unrelated branch in the same sweep) → the ancestor hit still fires, asserting zero `git fetch` invocations for `refs/pull/*` on this path (observable via `GIT_TRACE_PACKET`/`GIT_TRACE` on the subprocess environment, or an access-log assertion on the bare remote if `_run_script` exposes one). This is the direct test of mechanism M4's "zero network calls when objects are already local" claim, which case 1 (deliberately fetch-only) does not exercise.
  8. `fake_gh` returns a `MERGED` row whose `number` field contains a non-digit character → `skip-error` verdict, not a crash and not a value reaching `git` argv.
  9. `fake_gh` returns a `MERGED` row whose `headRefOid` is not exactly 40 lowercase hex characters (wrong length or uppercase) → the row is treated as no-match and falls through, never reaching the `git merge-base` call with a malformed operand.
  10. two merged rows, both `refs/pull/<n>/head` genuinely unresolvable on the remote → the per-row loop attempts and fails both fetches independently, the re-scan finds no containing row, classification falls through to `skip-stale-name`/`none` cleanly — no crash, no stderr, and row 2's fetch attempt is not skipped or short-circuited by row 1's failure. This is the multi-row all-fail shape that only the per-row loop (not case 3's single-row version) can regress on.
  **Reuse:** `_make_repo_with_remote`, `_make_feature_branch`, `_rev_parse` (`:37`), the `fake_gh` fixture (`:289`) with an explicit `headRefOid`, and `_run_script` (`:331`, non-TTY by default). The `(repo, remote, branch_name)` calling convention follows `_make_tier_b_branch`'s (`:1049`) shape, but that helper's own git sequence (checkout, commit, `merge --ff-only`, push) does not exercise `commit-tree`/`update-ref`/a non-standard ref namespace — there is no existing helper to model the novel half of the new fixture on, only the working `git branch -D` against the bare remote precedent already established by `TestSquashMergeStillCleanedAfterTipGuard` (`:1774`) and `TestClassifierEmitsNoShellDiagnostics`.
- **`docs/scripts.md`** — the `cleanup-merged-branches.sh` bullet at `:81` states the two merge signals verbatim and separately enumerates what `--dry-run` still does on the network; both need the third signal and the conditional per-row PR-ref fetches. The Tier A bullet at `:83` needs the ancestor basis named.
- **`CHANGELOG.md`** — a `### Changed` entry under `## [Unreleased]`. This is a stowed script: it goes live for every consumer on `git pull` with no re-install, and it widens an unprompted-delete path, so the entry says so plainly and points at `--dry-run` as the preview.
- **No change:** `README.md` (its only mention is the wrapper-verification line at `:108`), `cleanup-idle-open-pr-worktrees.sh`, `claude/.claude/settings.json` (no new invocation shape — the same `--dry-run` and destructive entries still cover it).

**Dispatch split:** one `code-writer` dispatch for all four files. The verdict grammar's `basis` field naming (`tip-match` | `pr-head-ancestor`) is shared state across the script edit, the test assertions, and the docs prose; splitting the dispatch would force restating that naming in each prompt and risks two agents independently choosing different names for the same field.

## Verification

```bash
.venv/bin/pytest claude/.claude/scripts/tests/test_cleanup_merged_branches.py   # inner loop
.venv/bin/python3 claude/.claude/scripts/select-tests.py                        # scoped suite for this diff
.venv/bin/ruff check claude/.claude/scripts/tests/test_cleanup_merged_branches.py
```

`select-tests.py` maps a `claude/.claude/scripts/**.sh` change to the scripts, hooks, and skills test directories (`select-tests.py:458`), which is what pulls in `claude/.claude/hooks/tests/test_shellcheck.py` — the shell lint for the new bash. Run it rather than a hand-widened suite; its target-list collision bug is already fixed in `[Unreleased]`, so its printed scope is the executed scope.

Beyond the suite, one operator check no fixture can stand in for, since the suite's remote is a local bare repo rather than GitHub: run `cleanup-merged-branches --dry-run` in a repo holding one of the affected branches and confirm it now appears under `Would clean up (confirmed merged):` with the ancestor basis rather than under the reused-name skip line. Keep any evidence quoted into the PR body to placeholder branch and repo names — a plan and its PR ship to a public repo.

**Review surface:** one shell script (three comment blocks, one embedded-Python edit, one new helper, two case arms), one test file, two doc surfaces. Risk concentrates in a single place — the new per-row `git fetch` loop inside a function two call sites invoke, one of them message-only — and every one of its failure paths must land back on today's verdict.

## Out of scope

- **Running the ancestry check before the `origin/<default>` reachability check.** It would upgrade branches that are reachable *and* carry a same-named merged row from a Tier B prompt to an unprompted delete (`TestTierBWithStaleMergedRowReportsBothSignals`'s shape), widening blast radius past the reported bug and putting a network call where a local check already answers. Deliberately declined, not overlooked.
- **`skip-stale-name` for genuine reuse.** A tip that is an ancestor of no merged head keeps today's verdict and today's message.
- **A non-interactive Tier B flag** (`--yes`, `--assume-yes`, or similar). Tier B branches remaining unclean on a non-TTY run is a separate design question about reachability-only evidence; this plan does not touch that route.
- **Non-GitHub forges.** `refs/merge-requests/<n>/head` and equivalents are not attempted; on such a remote the fetch fails and behavior is identical to today's.
- **A per-branch basis line in the real run's `Cleaned up:` block.** The reason string is dry-run-only today (documented by `test_tier_b_reason_names_the_same_named_merged_pr`); `--dry-run` stays the audit surface.
- **`cleanup-idle-open-pr-worktrees.sh`**, which classifies open PRs by a bulk query and shares no code with `classify_branch`.
- **A recovery breadcrumb (e.g., logging the pre-delete SHA) for a wrongly-classified Tier-A deletion.** Today's exact-match Tier A has essentially no room for a false positive (SHA equality is a tautology); the new ancestor-based path depends on new code (a fetch, field validation, a multi-row scan) that, unlike today's check, could in principle misclassify. `cleanup-merged-branches.sh:782`'s plain `git branch -D` gives an operator no better recovery path than git's own reflog/GC-window forensics, for either path, today or after this change. This plan does not add one — deliberately deferred as a separate, pre-existing gap this change exposes to more branches rather than introduces, not a gap in this design.

**One thing to watch during implementation, not a gap in this design:** row 2 (does `refs/pull/<n>/head` outlive its source branch?) is unverified. It does not need to be resolved before implementing, because M5 degrades to today's behavior when the ref is missing — but if the operator dry-run above shows the affected branches still skipping, that row is the first thing to check, and `git ls-remote origin 'refs/pull/*/head'` on a repo with a deleted source branch settles it in one call.
