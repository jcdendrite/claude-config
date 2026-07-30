# Idle open-PR worktree cleanup script

## Context

Add a script that reclaims disk space by removing git worktrees for open PRs
that are ready for review but not currently being worked on — without
touching the branch, the remote, or the PR itself — so this stops being an
ad hoc manual procedure.

This mirrors a manual pass done live on 2026-07-29: across two private
project repos, `git worktree remove` on 8 idle-but-open-PR worktrees
reclaimed ~3.68G, more than any other single technique in that session.
`cleanup-merged-branches.sh` already exists for a
different case — branches with **merged** PRs — but that tool's core
`classify_branch()` treats "open PR" as a permanent skip signal, so it has no
path for this. Doing the equivalent by hand each time means re-deriving the
`gh pr list` shape, the staleness read, and the live-process check from
scratch, and re-verifying by memory whether it's actually safe.

## Approach

**New sibling script, `cleanup-idle-open-pr-worktrees.sh`, sharing a small extracted
library with `cleanup-merged-branches.sh` — not a new flag on the existing
script.**

The two tools' action models don't overlap enough to share one script body:
`cleanup-merged-branches.sh`'s entire structure — `classify_branch`'s
tier-a/tier-b/skip-* verdicts, the interactive Tier-B prompt, branch deletion,
remote `push --delete`, default-branch fast-forward — is built around
*merge* status and ends in deleting the branch. The new tool operates only on
*open* PRs, never deletes a branch or touches the remote, and its staleness
signal (`updatedAt` recency) has no merge-status equivalent to piggyback on.
Bolting it on as a mode flag would mode-gate nearly every section of a
working, tested 775-line script for a `gh` query shape (bulk per-repo vs.
per-branch) and an output shape (informational skip list vs. delete summary)
that don't fit the existing scaffolding.

What *is* identical and stable between the two tools is the live-process /
worktree-in-use detection (`collect_process_cwds` + `worktree_in_use`,
~65 lines) and the branch→worktree-path porcelain lookup. Per this repo's
own single-source-of-truth rule, that block gets extracted into a sourced
library, `claude/.claude/scripts/_worktree-lib.sh`, following the precedent
already set by `claude/.claude/hooks/_lib.sh` (hooks already share code this
way; scripts currently don't, but there's no reason the convention shouldn't
extend there for a second consumer of the same logic).

**Idle signal:** one `gh pr list --state open --limit 100 --json headRefName,number,isDraft,updatedAt` call per repo (run from `$REPO_ROOT`, letting
`gh` infer the repo from the git remote — same implicit-repo convention
`cleanup-merged-branches.sh` already uses for its `--head` calls, so the
multi-account `gh` gotcha described in the Assumption ledger isn't a new
failure mode). `--limit 100` matches the existing script's own constant for the same
reason it does there: `gh`'s default page size is 30, and the whole point of
switching to a bulk per-repo call is handling repos with many open-PR
worktrees at once — an unpaginated call would silently truncate past 30 and
quietly defeat that. Bulk per-repo is a deliberate departure from
`cleanup-merged-branches.sh`'s per-branch `gh pr list --head` calls: a repo
with a dozen open-PR worktrees would otherwise cost a dozen API calls for
information one call already returns. Match key: `headRefName == branch`.

**`updatedAt` is a conservative proxy, not a true owner-activity signal** —
GitHub bumps it on any PR metadata change (a bot comment, a CI status
transition, a label, someone else's review request), not only on a push by
the branch owner. That means the idle check can read "still active" when
only CI touched the PR, which under-deletes (fails safe) rather than
over-deletes — worth a code comment at the point of use so a future reader
doesn't assume it's a tighter signal than it is.

Classification per branch, in order:
1. No open PR at all → not this tool's concern, skip silently (candidate for
   `cleanup-merged-branches.sh` instead). This bucket also catches a
   **closed-but-unmerged** PR (excluded by the `--state open` filter): that
   worktree is an orphan neither script cleans today (not idle-open-PR's
   concern by design; not `cleanup-merged-branches.sh`'s concern since
   nothing confirms it merged) — a known, deliberate gap, not an
   oversight. A dedicated test case (see Critical files) asserts the
   worktree is left untouched, so the boundary is documented as accepted
   rather than accidentally silent.
2. `isDraft: true` → skip, always. A draft is explicitly WIP by GitHub's own
   definition, not "ready for review."
3. `updatedAt` newer than `--idle-hours` (default 4, see ledger) → skip,
   reported as "still active." Comparison is against wall-clock `now`, so
   both sides of the mechanism need to be nailed down, not left to whatever
   the implementer reaches for first:
   - **Parsing `updatedAt` to an epoch integer needs a GNU/BSD `date`
     branch** — this is a *new* portability mechanism in this script
     suite, not a mirror of an existing one: this repo's own `mktemp`
     usage (`resume-context.sh`) deliberately uses the bare-template form
     specifically to avoid needing a GNU/BSD branch at all, per its own
     comment that the template form is documented identically by both GNU
     coreutils and BSD/macOS `mktemp(1)`. Before adding a two-path `date`
     branch, check whether an equivalent branch-free parse exists for
     ISO8601 input; if none does, implement GNU `date -u -d "$updated_at"
     +%s` / BSD `date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$updated_at" +%s`
     (detect via `date --version` support or a `uname` check) as a
     genuinely new conditional — state that plainly rather than citing
     false precedent.
   - **The BSD branch must not ship untested.** This repo's dev/CI is
     Linux-only, so every idle-hours fixture would otherwise exercise only
     the GNU path, leaving the BSD form as dead code from CI's perspective
     until a macOS stow consumer becomes its first real exerciser. Add a
     test that shims `date` on `PATH` to force the "no GNU support"
     detection outcome and asserts the BSD-form invocation runs and
     correctly parses a known ISO8601 timestamp.
   - **The staleness comparison itself — not `--idle-hours` input
     validation — is the actual `set -e` + `(( expr ))` zero-exit-status
     hazard.** A PR updated less than an hour ago (a routine case, already
     in the test matrix) legitimately produces an elapsed-hours value of
     `0`; if the epoch-delta-vs-threshold comparison is written as a bare
     arithmetic assignment or standalone `(( ))` statement rather than as
     the condition of an `if`/`[[`/`(( ))`-as-test, that `0` result trips
     `set -e` and aborts the entire run — silently dropping every
     remaining candidate, worse than the "under-deletes, fails safe"
     behavior this plan designs toward everywhere else. Write the
     comparison as `if (( now_epoch - updated_epoch < idle_seconds )); then`
     (or equivalent) — never as a bare assignment.
   - **`--idle-hours=N` needs explicit validation before arithmetic** — a
     `"${IDLE_HOURS:?idle-hours requires a value}"` guard plus a numeric
     regex check (`[[ "$IDLE_HOURS" =~ ^[0-9]+$ ]]`), rejecting non-numeric
     or negative input with a usage error. This repo's own
     `shell-script-conventions.md` flags the `set -e` + `(( expr ))`
     zero-exit-status trap for exactly this class of unguarded arithmetic.
4. Otherwise → candidate. Cross-check via the shared lib that no live
   process's cwd is inside the worktree (or a subdirectory of it); skip with
   a clear reason if so.
   **Tie-break for multiple rows sharing one `headRefName`** (e.g. two PRs
   from the same head against different base branches): match the row with
   the lowest PR `number` (the original PR opened against that head);
   draft/idle/in-use classification still applies to whichever row wins the
   match, not a merge of both rows' fields. Covered by a dedicated
   multi-row test case (see Critical files).
5. `git worktree remove <path>` on the survivors, **capturing success/failure
   per candidate rather than calling it bare** — under `set -euo pipefail`
   (which this script mirrors from `cleanup-merged-branches.sh`), an
   unguarded `git worktree remove` that exits non-zero on one candidate
   would abort the entire run, silently dropping every remaining candidate
   rather than skipping just that one. Mirror `cleanup-merged-branches.sh`'s
   own `if WORKTREE_REMOVE_OUTPUT=$(git worktree remove "$path" 2>&1); then
   ... else ... continue; fi` shape exactly — this is a design requirement,
   not just a test-coverage note, since the failure mode without it is a
   silent partial run. No `--force` — a dirty working tree (uncommitted or
   untracked changes) makes git itself refuse the removal (`git-worktree(1)`:
   "Only clean worktrees... can be removed"), so a script-level pre-check for
   *that* would be redundant; the requirement above is about not letting one
   such refusal kill the whole batch. The branch ref lives in the main
   repo's `refs/`, not inside the worktree directory, so removal never
   touches branch history — a worktree with committed-but-unpushed commits
   is exactly as safe to remove as one that's fully pushed; the commits stay
   reachable via the branch ref either way and come back with
   `git worktree add <path> <branch>`.

No interactive per-branch prompt (unlike `cleanup-merged-branches.sh`'s
Tier B). That prompt exists there because Tier B is genuinely uncertain about
merge status — a real judgment call. Here, once a worktree passes the
idle + non-draft + not-live-in-use + clean-tree checks, there's no
comparable ambiguity to prompt about, and the cost of a wrong call is a
`git worktree add` + reinstall, not lost work. `--dry-run` (same convention
as the existing script) covers the "review before acting" step instead —
list candidates and skip reasons, take no action, exit.

Alternatives considered and set aside:
- **Reuse `classify_branch()` / add a third tier to it.** Rejected: that
  function's whole contract is a merge-status verdict; forcing an
  orthogonal "idle open PR" verdict through it, with none of Tier A/B's
  consequences (no branch delete, no remote delete), muddies a
  single-responsibility function that 1700+ lines of existing tests already
  pin down.
- **Duplicate the ~65-line live-detection block into the new script instead
  of extracting a shared lib.** Rejected: CLAUDE.md's single-source-of-truth
  rule calls duplication a defect absent a named exception, and this repo
  already has the `_lib.sh` sourcing precedent in `claude/.claude/hooks/` —
  extracting isn't introducing a new pattern, just extending an existing one
  to a second consumer.
- **Per-branch `gh pr list --head` (mirroring the existing script) instead
  of bulk per-repo.** Rejected on efficiency: idle-worktree sweeps are
  exactly the case where a repo has many open-PR worktrees at once (the
  motivating case had 7 in one repo) — one bulk call beats N per-branch
  calls with no loss of information, since `gh pr list --state open` already
  returns every field the classifier needs.

### Assumption ledger

Root problem: idle open-PR worktrees accumulate multi-GB `node_modules` /
build-artifact footprints with no scripted way to identify and remove them
safely, forcing a from-scratch manual `gh` + process-cwd audit each time.

| # | Assumption | Tag |
|---|---|---|
| 1 | `git worktree remove` refuses on uncommitted/untracked changes, not on unpushed commits, and never touches the branch ref | `[verified: man git-worktree — "Only clean worktrees (no untracked files and no modification in tracked files) can be removed"]` anchors: root |
| 2 | Shared lib extraction (vs. duplicating or folding into existing script) is the right shape | `[verified: this repo's own CLAUDE.md single-source-of-truth rule + existing `_lib.sh` precedent in claude/.claude/hooks/]` anchors: root |
| 3 | `gh pr list --state open` with no `--repo` flag correctly infers the repo from cwd's git remote, matching `cleanup-merged-branches.sh`'s existing `--head` calls | `[verified: same implicit-repo behavior already relied on by cleanup-merged-branches.sh, confirmed working in the 2026-07-29 live session]` anchors: row 3 is inherited from existing script, not newly asserted |
| 4 | 4-hour default idle threshold — long enough that an active same-day iteration loop (push, wait for CI/feedback, push again) isn't misread as idle; short enough to be useful without requiring a multi-day wait | `[unverified]` — no authoritative source for "how long counts as idle" exists; this is a workflow judgment call, exposed as `--idle-hours=N` so the operator can tune it rather than being silently baked in. anchors: root |
| 5 | A draft PR is never a cleanup candidate regardless of idle time | `[verified: GitHub's own draft-PR semantics — a draft is explicitly not-yet-ready-for-review by definition]` anchors: root |
| 6 | A multi-account `gh`-org gotcha (wrong active account → every PR lookup fails closed, reads as "no PR" rather than an auth error) applies identically to this new script, since it uses the same implicit-repo `gh` call shape as the existing script | `[verified: reproduced directly in the 2026-07-29 live session — a `gh auth switch` to the correct account was needed before lookups worked]` anchors: row 3 |

## Critical files

- **`claude/.claude/scripts/_worktree-lib.sh`** (new) — extracted
  `collect_process_cwds`, `worktree_in_use`, and the branch→worktree-path
  porcelain parser (currently duplicated between `_dry_print_branch_with_lock`
  and the main removal loop in `cleanup-merged-branches.sh` — extracting it
  for the new script is also a chance to de-duplicate that second copy in
  the old one, in-file scope per CLAUDE.md Axis 2). Also extract the
  `progress`/`clear_progress` helpers (~9 lines, identically duplicated
  between the two scripts per this plan's own "mirror... progress helpers"
  language below) — the SSOT argument made for the 65-line block applies
  unaddressed to this smaller one; leaving it out is a silent inconsistency
  a future reader would have to notice and fix separately. Sourced via
  `. "$(dirname "${BASH_SOURCE[0]}")/_worktree-lib.sh"` — resolves correctly
  under the `.local/bin` wrapper pattern (see below), since the wrapper
  `exec`s the absolute stowed script path rather than a relative one.
- **`claude/.claude/scripts/cleanup-merged-branches.sh`** (modify) — replace
  its inline copies of the live-detection functions and the duplicated
  porcelain-parsing block with calls into `_worktree-lib.sh`. Behavior
  unchanged; existing test suite must still pass unmodified as the
  regression check. **Rollback:** the extracted functions are pure /
  side-effect-free, so if a behavior regression in this script surfaces
  post-merge (in real use, not caught by the existing suite), revert the
  extraction commit — re-inlining is mechanical.
- **`claude/.claude/scripts/cleanup-idle-open-pr-worktrees.sh`** (new) — the tool
  itself: arg parsing (`--dry-run`, `--idle-hours=N`, default 4), bulk `gh pr
  list` call, classification per branch, dry-run printer, real-run removal
  loop, end-of-run summary (candidates removed / skipped-active /
  skipped-draft / skipped-in-use / skipped-no-pr). Reuse `_worktree-lib.sh`
  and mirror `cleanup-merged-branches.sh`'s `set -euo pipefail`, prerequisite
  checks (`gh` installed + authenticated), `--dry-run` conventions, and
  progress helpers.
- **`claude/.local/bin/cleanup-idle-open-pr-worktrees`** (new) — thin wrapper
  mirroring `claude/.local/bin/cleanup-merged-branches`
  (`exec "$HOME/.claude/scripts/cleanup-idle-open-pr-worktrees.sh" "$@"`).
- **`claude/.claude/settings.json`** (modify) — add exact-string
  `permissions.allow` entries for the new script, mirroring the existing
  four `cleanup-merged-branches` entries: `Bash(~/.claude/scripts/cleanup-idle-open-pr-worktrees.sh)`, `Bash(~/.claude/scripts/cleanup-idle-open-pr-worktrees.sh --dry-run)`, `Bash(cleanup-idle-open-pr-worktrees)`, `Bash(cleanup-idle-open-pr-worktrees --dry-run)`. Per this repo's no-globs rule, `--idle-hours=N` invocations aren't pre-authorized — that's an accepted tradeoff (interactive approval on first use of a non-default threshold) rather than a glob rule. The two bare-name entries are justified by the same rationale already recorded for `cleanup-merged-branches` (installed to `~/.local/bin/` by `install.sh`; script calls only absolute paths and execs no untrusted input) — add a parallel subsection to `claude/.claude/skills/review-permissions/REFERENCES.md` under "Decisions on global allow list entries" so a future `/review-permissions` run doesn't have to re-derive it.
- **`docs/scripts.md`** (modify) — add an entry for the new script,
  matching the `cleanup-merged-branches.sh` entry's shape (description +
  usage block): what it does, the classification order, the safety
  properties (no branch/remote touch, live-process check, fail-closed on
  `gh` errors), and the `--dry-run`/`--idle-hours=N` flags.
- **`CHANGELOG.md`** (modify) — add an entry under `[Unreleased]` /
  `### Added` for the new script, mirroring how prior new-script additions
  (e.g. the original `cleanup-merged-branches` wrapper) were recorded.
- **`claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py`** (new) —
  mirror `test_cleanup_merged_branches.py`'s structure: real git repos in
  `tmp_path`, `gh` replaced by a PATH shim — but the shim here replies to
  `--state open` (bulk, no `--head` argument) rather than per-branch, since
  that's the new script's actual query shape. The gh-shim generator is
  legitimately duplicated per file (DAMP exception: the two shims model
  incompatible response shapes — single-PR keyed lookup vs. bulk array — a
  shared generalized shim would need its own branching logic to bridge
  them). The pure git-repo scaffolding helpers (`_init_repo`, `_commit`,
  `_make_repo_with_remote`, `_make_feature_branch`, worktree creation, dead-
  PID simulation) have no shape-specific dependency on either script's `gh`
  query and should move to a shared `conftest.py` instead of being
  copy-pasted — duplicating them has no DAMP-readability payoff, only the
  risk of a future git-version fix landing in one file and not the other.
  The bulk shim must validate the exact args the script passes (`--state
  open --limit 100 --json headRefName,number,isDraft,updatedAt`) and
  reject/fail loudly on any other shape, rather than permissively
  returning canned fixture data regardless of args — a generous shim would
  hide both an argument-construction regression (wrong `--limit`, dropped
  `--state open`) and a classifier bug that reads a field it never
  requested (e.g. stray `mergedAt`/`headRefOid` fields copy-pasted from the
  per-branch shim).

  Cases to cover:
  - Draft PR skipped regardless of `updatedAt`.
  - Idle-hours boundary tested on **both sides**, with fixtures generated
    clock-relative at test-execution time (`(now - timedelta(hours=X)).isoformat()`), never a hardcoded literal — unlike
    `test_cleanup_merged_branches.py`'s `mergedAt` literals (fine there,
    since `classify_branch` never compares them to wall-clock time; wrong
    here, since this script's core logic *is* a delta against "now" and a
    fixed literal drifts stale as the suite ages): exactly-at-threshold,
    one-second-under (still active, skipped), one-second-over (idle,
    removed).
  - **Idle-hours boundary under non-UTC `TZ`** — run at least the boundary
    case with a non-UTC `TZ` env var set in the test subprocess, asserting
    the boundary is unaffected. Catches a forgotten `-u`/`-j -u` on one of
    the GNU/BSD `date` branches silently shifting the idle boundary by the
    host's UTC offset — undetectable if the suite only ever runs in one TZ.
  - **BSD `date` branch exercised** — shim `date` on `PATH` to force the
    "no GNU support" detection outcome for at least one boundary case,
    asserting the BSD-form invocation (`date -j -u -f
    '%Y-%m-%dT%H:%M:%SZ' ...`) runs and parses correctly. Otherwise this
    branch ships as dead code from CI's perspective (this repo's dev/CI is
    Linux-only) until a macOS stow consumer becomes its first exerciser.
  - Stale open PR removed; branch and PR untouched after removal (assert
    both still exist post-run) — **and** the worktree is successfully
    recreatable via `git worktree add <fresh-path> <branch>` afterward.
    This is the actual rollback path the design's safety argument depends
    on (branch ref untouched by removal); asserting the branch still
    exists in `refs/heads` is necessary but not sufficient; `git worktree
    add` succeeding proves the ref is intact and re-checkoutable, not just
    present.
  - **Closed-but-unmerged PR** — a branch with a PR in `state: closed`
    (never merged) produces no row in the `--state open` response; assert
    the worktree is left untouched. Documents the deliberate orphan gap
    (see Approach, classification step 1) as accepted rather than
    accidental.
  - **Multiple rows sharing one `headRefName`** — two PR rows in the bulk
    response point at the same branch (e.g. against different base
    branches); assert the tie-break rule from Approach step 4 (lowest PR
    `number` wins) is applied deterministically.
  - **Slashed branch name** through the new script's own worktree-path
    lookup — same shape as `test_cleanup_merged_branches.py`'s
    `TestSlashedBranchName`, exercised at this script's own call site (not
    only inside `test_worktree_lib.py`), since a second consumer of the
    lib is exactly when a path-construction bug at the call site (as
    opposed to inside the lib) tends to reappear.
  - Worktree in live use skipped.
  - **Multi-row bulk response in one run** — a single `gh pr list` reply
    containing a mix of draft + recently-active + stale + in-use branches;
    assert each lands in its correct bucket and the end-of-run summary
    counts match. This is the scenario a per-branch-only test suite would
    never exercise, and it's exactly where an off-by-one over the JSON array
    or a wrong-key lookup would hide.
  - **Open PR with no matching local worktree** — `gh pr list` returns a
    branch that was never checked out; assert the script skips it rather
    than erroring on a missing worktree-path lookup.
  - **A dirty candidate mid-batch does not abort the run** — **at least
    two** idle candidates in one run, with the dirty one deliberately *not*
    last (branch names chosen to pin enumeration order, e.g. `aaa-`/`zzz-`
    prefixes, the way `test_cleanup_merged_branches.py`'s
    `TestTierBPromptEOFDoesNotAbortPendingTierADeletes` pins its own
    ordering — note its docstring warning that renaming the branches would
    silently invert what the test proves). A single-candidate version
    (mirroring `TestLockedWorktreeRemoveFailsCleanly`'s shape alone) proves
    the failing candidate survives, but not that a *later* candidate in the
    same run still gets processed — the actual `set -e`-abort risk this
    test exists to catch requires ≥2 candidates with the failure on a
    non-last item.
  - End-of-run summary counters (removed / skipped-active / skipped-draft /
    skipped-in-use / skipped-no-pr) asserted directly, not just inferred
    from side effects.
  - Dry-run takes no action.
  - **No-upstream-remote, non-zero-exit `gh` failure, and malformed-JSON-
    on-zero-exit `gh` response are three separate cases**, not one
    conflated "fails closed" case: a git-config precondition (no upstream)
    vs. an API/auth precondition (non-zero exit) vs. a parse-layer
    precondition (0 exit, unparseable body) are distinct failure surfaces
    that could legitimately want different behavior (immediate usage error
    vs. per-branch fail-closed skip), and the malformed-JSON path in
    particular guards against a fail-*open* bug — a malformed body on a 0
    exit misread as an empty list (rather than an error) would be wrongly
    classified as "no open PR" and treated as an idle-removal candidate
    instead of skipped. Mirror `test_cleanup_merged_branches.py`'s explicit
    `"malformed"` vs `"error"` sentinel distinction for this.
  - `--idle-hours` override respected (non-default value changes the
    boundary).
- **`claude/.claude/scripts/tests/conftest.py`** (new) — the shared
  git-repo-scaffolding fixtures described above, imported by both
  `test_cleanup_merged_branches.py` (refactored to use it, in-file scope per
  CLAUDE.md Axis 2) and the new test file.
- **`claude/.claude/scripts/tests/test_worktree_lib.py`** (new) — a direct,
  no-subprocess unit test: source `_worktree-lib.sh` standalone (not through
  either consumer script) and assert `collect_process_cwds` /
  `worktree_in_use` behave correctly in isolation against a synthetic path.
  Both scripts' end-to-end suites exercise the library only incidentally
  through a full subprocess + git-repo path; a defect isolated to the
  sourcing mechanism itself (`BASH_SOURCE` resolution, a missing/unreadable
  lib file, `set -e` silently swallowing a failed source) would only be
  caught today if it happened to manifest identically through both
  consumers' full test paths. This pins the library's own contract
  independently of either caller.
- **`claude/.claude/scripts/tests/test_cleanup_merged_branches.py`**
  (regression only, no intended behavior change) — must still pass after the
  lib extraction; add no new cases here unless the extraction reveals a gap.

## Verification

- **Sequencing:** run `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_cleanup_merged_branches.py -q` immediately after the `_worktree-lib.sh`
  extraction step (Critical Files, `cleanup-merged-branches.sh` edit) —
  before writing the new script or its tests. This is a bisectable
  checkpoint: if the extraction changed behavior, it fails here against a
  small, isolated diff rather than being diagnosed later against a much
  larger combined change once the new script also exists. Diff the
  collected node-id list (`pytest ... --collect-only -q`) before and after
  the `conftest.py` extraction, not just the pass/fail exit code — a
  reorganization can silently drop or shadow a test (duplicated class/
  function name, import collision) while pytest still exits 0.
- `../../../.venv/bin/pytest claude/.claude/scripts/tests/ -q` — full suite
  (all test files, including the new ones), from a worktree per this repo's
  contributor setup.
- `../../../.venv/bin/ruff check claude/.claude/` — repo lint gate (Python
  test files).
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
  repo shell lint gate, covers the two new/modified `.sh` files.
- Manual dry-run against a real repo with a mix of draft, recently-updated,
  and genuinely-idle open-PR worktrees (e.g. re-run against the private
  project repos from the motivating session, post-merge, once new PRs have
  accumulated) to sanity-check the `updatedAt` threshold reads naturally,
  before trusting the real (non-dry-run) path again.

## Out of scope

- Fixing `cleanup-merged-branches.sh`'s own exposure to the gh multi-account
  gotcha (row 6) — it has the same failure mode today and isn't part of this
  ask; noted so a future pass doesn't have to rediscover it.
- Automating either script via cron/hook. Both remain manually-invoked; nothing here changes that.
