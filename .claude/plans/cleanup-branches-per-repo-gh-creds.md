# Load each repo's own environment during `cleanup-merged-branches --all-projects`

## Context

**Goal:** make `--all-projects` query every repo with that repo's own `gh`
credentials, and collapse the resulting wall of per-branch failure lines into
one line per distinct reason.

`cleanup-merged-branches.sh --all-projects` sweeps every git repo under the
roots listed in `~/.claude/cleanup-merged-branches-roots`. On a machine where
per-directory tooling (direnv) supplies a different `GH_TOKEN`/`GH_HOST` per
top-level container, every repo outside the invoking shell's container is
queried with the wrong credentials. `gh pr list` then fails, `classify_branch`
fails closed, and the operator sees a wall of
`gh lookup failed; skipping to fail closed` — one line per branch, with no
indication that credentials are the cause. Whole repos are silently skipped.

**Correcting two premises in the report.** The script *already* `cd`s into each
repo root (`cleanup-merged-branches.sh:782`), so adding a `cd` fixes nothing —
direnv is hooked to the shell's `PROMPT_COMMAND`, not to `cd`, so it never
fires inside a non-interactive script. And this does not require a redesign:
the sweep's per-repo subshell is already the correct seam.

### Root cause (reproduced, not inferred)

Loading container A's environment and then querying a repo in container B:

```
$ cd <container-b-repo> && gh pr list --head <branch> --state all --limit 1 --json number,state
GraphQL: Could not resolve to a Repository with the name '<owner>/<repo>'. (repository)
exit=1
```

`classify_branch:273-281` captures that nonzero exit and returns `skip-error`,
which the detection loop renders as `gh lookup failed; skipping to fail
closed`. Public repos in the sweep still succeed (any valid token reads them),
which is why the reported run cleaned one repo and skipped its neighbours —
the pattern that made this look intermittent rather than credential-scoped.

## Approach

Run direnv's own hook body inside each repo's subshell, immediately after the
existing `cd`. `direnv hook bash` — the primary source, read this session — is
in full:

```
_direnv_hook() {
  local previous_exit_status=$?;
  trap -- '' SIGINT;
  eval "$("/usr/local/bin/direnv" export bash)";
  trap - SIGINT;
  return $previous_exit_status;
};
if [[ ";${PROMPT_COMMAND[*]:-};" != *";_direnv_hook;"* ]]; then
```

Two facts follow verbatim: the hook is installed into `PROMPT_COMMAND` (an
interactive-shell prompt hook — nothing a script's `cd` can trigger), and its
entire mechanism is `eval "$(direnv export bash)"`. That one line is the fix.

Measured (direnv 2.37.1): `eval "$(direnv export bash)"` costs **49 ms**, and
switching between two containers correctly swapped both org identity and
`GH_HOST` (including an enterprise host). One call per repo is negligible.

The reporting half is solved by **aggregating the skip lines the script already
produces** — not by adding a credential probe. A first draft added a per-repo
`gh repo view` probe plus a new exit code; review established that
`gh repo view --json nameWithOwner` returns the *remote-derived* repo name,
identical under a correct and a misrouted token, so it could not detect what it
existed to detect, while it misclassified local-only and non-GitHub repos as
credential failures. Dropping it leaves the exit-code contract unchanged.

### Alternatives set aside

- **`direnv exec <dir> <cmd>`** — would require re-exec'ing the script with a
  new path-taking flag, adding its first user-controlled argument (the header
  cites argument-injection-freedom as a property). A prior in-repo plan
  measured `direnv exec` spawns at 0.33–0.45 s each. Verified this session:
  `direnv exec DIR` does **not** `cd` into `DIR`, so the `cd` is still needed.
- **Wrapping only the `gh` calls** — one direnv load per *branch* rather than
  per repo, and it leaves `git fetch`/`git push --delete` on the wrong
  environment for HTTPS remotes.
- **A per-repo credential probe** — see above; it cannot distinguish a
  misrouted token from a correct one, and turns "not a GitHub repo" into a
  reported credential failure.

### Assumption ledger

**Root problem:** a non-interactive sweep inherits one repo's credentials and
applies them to every repo, so `gh` fails everywhere else and fails closed with
one opaque line per branch.

**Given** (fixed condition outside this plan's reach): `gh` reads credentials
only from the process environment (`GH_TOKEN`, `GH_HOST`) or its own config —
there is no per-repo credential argument this script could pass it.
*(Vendor-imposed: `gh`'s own credential resolution.)*

Deliberately declined rather than given: reimplementing per-directory
credential selection inside this script is reachable — the provisioning repo is
a peer repo the same engineer owns — but declined. See **Out of scope**.

| # | Assumption | Tag |
|---|---|---|
| 1 | `direnv hook bash` installs into `PROMPT_COMMAND` and evals `direnv export bash`; a script's `cd` never fires it | `[verified: direnv hook bash output, quoted above]` |
| 2 | The wrong-container token yields `Could not resolve to a Repository` and `gh` exit 1 | `[verified: reproduced this session]` |
| 3 | `direnv export bash` emits only `export`/`unset` with ANSI-C quoting — nothing `test_no_bash4_constructs.py` forbids | `[verified: inspected output; independently confirmed in review]` |
| 4 | In a directory under **no** `.envrc`, with a container env loaded, `direnv export bash` exits **0** and emits `unset GH_TOKEN`/`unset GH_HOST` — it removes credentials rather than leaving them | `[verified: measured in review; contradicts this plan's own first draft]` |
| 5 | A non-`allow`ed `.envrc` exits **1** with an unset payload on stdout, so an exit-status guard discards it cleanly | `[verified: measured in review]` |
| 6 | `direnv export bash` inherits the sweep's TTY stdin; an `.envrc` that reads stdin hangs the sweep indefinitely with no output | `[verified: reproduced in review — 2-minute timeout, zero output]` |
| 7 | `direnv export bash` **stdout carries secret values verbatim**; its stderr carries variable *names* only | `[verified: measured in review]` |
| 8 | Exit code `3` is unused, but is no longer needed once the probe is dropped | `[verified: run_repo_cleanup uses only exit 0/1/2]` |
| 9 | The sibling `cleanup-idle-open-pr-worktrees.sh` has no multi-repo sweep, so it does not share this bug shape | `[verified: arg parser accepts only `--dry-run`/`--idle-hours=N`]` |
| 10 | `--all-projects` sits in `permissions.ask`, not `permissions.allow`, so no permission rule changes | `[verified: claude/.claude/settings.json]` |
| 11 | direnv is **not** a claude-config dependency — most stow consumers will not have it | `[unverified]` — drives the graceful-degradation requirement |
| 12 | A repo whose `gh` is unusable should report and continue, not fail the sweep | `[engineer-verified]` |

### Mechanisms

- **`eval "$(direnv export bash </dev/null 2>/dev/null)"`, guarded by
  `command -v direnv`** — *anchors: root, rows 1, 6, 7.* The lightest available
  primitive: literally direnv's own hook body. `</dev/null` is load-bearing
  (row 6); `2>/dev/null` suppresses per-repo chatter, and direnv's **stdout is
  never printed** (row 7).
- **`readonly` on the destructive control variables + a post-eval repo-root
  re-check** — *anchors: row 1.* `eval` of arbitrary `export` statements runs in
  the scope holding `DRY_RUN` and `REPO_ROOT`; see **Design 2**.
- **Aggregating existing skip reasons** — *anchors: root.* Reuses data
  `print_skip_reason_lines` already holds; no new `gh` call, no new exit code,
  no contract change.

## Design

### 1. `load_repo_environment()`

New function, called from `run_repo_cleanup` after `REPO_ROOT` resolves.
Applies to **both** the single-repo and sweep paths — one code path, not two.

```bash
# Apply the environment direnv's shell hook would apply on `cd`. That hook is
# installed into PROMPT_COMMAND, so a script's own `cd` never fires it and a
# sweep would query every repo with the invoking shell's credentials.
load_repo_environment() {
  command -v direnv >/dev/null 2>&1 || return 0
  local direnv_exports
  # </dev/null: an .envrc that reads stdin would otherwise consume the TTY the
  # sweep reattaches at :782 for the Tier B prompt, hanging with no output.
  # direnv's stdout carries secret values verbatim and is never printed.
  direnv_exports=$(direnv export bash </dev/null 2>/dev/null) || return 0
  eval "$direnv_exports"
}
```

The `eval` stays **bare** — not `|| return 0`. Under `set -e` a failed
assignment to a `readonly` variable must abort the repo rather than continue;
see below.

### 2. Guarding what `eval` can reach

`eval` executes `export <NAME>=<VALUE>` for arbitrary names in the scope that
holds this script's destructive control variables. Two are reachable and
generic enough to collide with an ordinary dev `.envrc`:

- `DRY_RUN` (`:83`, gates the early exit at `:477`) — an `.envrc` exporting
  `DRY_RUN=0` would convert `--all-projects --dry-run` into a real deletion run.
- `REPO_ROOT` (`:208`) — clobbering it makes the `[ "$WORKTREE_PATH" !=
  "$REPO_ROOT" ]` test at `:613` true for the *main* checkout, pointing
  `git worktree remove` (`:646`) at the operator's primary worktree.

Required: `readonly DRY_RUN ALL_PROJECTS` after arg parsing, and
`readonly REPO_ROOT` after `:208`. With a bare `eval` under `set -euo
pipefail`, a clobber attempt aborts the subshell and the original value
survives — fail-safe and loud.

Separately, an `.envrc` exporting `GIT_DIR`/`GIT_WORK_TREE`/`GIT_COMMON_DIR`
would repoint the destructive git operations at another repository.
`--show-toplevel` alone does not catch a `GIT_DIR`-only redirection:
reproduced in review — with only `GIT_DIR` set (no `GIT_WORK_TREE`), `git
rev-parse --show-toplevel` is unchanged, but `git branch -D` and equivalent
plumbing operate against the `GIT_DIR`-targeted repo's refs. A second vector
evades a `--show-toplevel` + `--absolute-git-dir` pair too: per `git`'s own
manual (`git help git`, `GIT_COMMON_DIR` — verified this session), "non-
worktree files that are normally in `$GIT_DIR` will be taken from this path
instead," and `refs/heads/*` (what `branch -D` mutates) are exactly such
non-worktree files — `--absolute-git-dir` reports the worktree-specific
`$GIT_DIR`, not the common dir, so it does not move under a
`GIT_COMMON_DIR`-only redirection either.

Capture all three — `git rev-parse --show-toplevel`, `--absolute-git-dir`,
and `--path-format=absolute --git-common-dir` — **before** the eval
(alongside `REPO_ROOT`), and immediately after the eval re-check all three
against their pre-eval values, aborting this repo if any has changed. Use a
distinct message so it's not confused with an unrelated `set -e` failure.
This check does not cover `GIT_INDEX_FILE`, which doesn't affect any of the
three; left unaddressed as lower-severity (it redirects the index, not which
repository's refs commands mutate).

### 3. Collapsing the skip lines

`print_skip_reason_lines` (`:369-374`) prints one line per skipped branch.
Group by identical reason message and print one line per distinct reason with
its count and branch names. The `gh lookup failed; skipping to fail closed`
message is byte-identical across branches, so it collapses to one line; open-PR
skips carry distinct PR numbers and stay per-branch, which is correct.

No new exit code and no change to fail-closed behavior: a repo whose `gh` is
unusable still deletes nothing and still exits 0 (row 12).

### 4. Prerequisite check

Keep `command -v gh` (`:98-102`) and `gh auth status` (`:104-107`) as-is for
the single-repo path. Under `--all-projects`, downgrade `gh auth status` to a
warning **only when direnv is present** (`command -v direnv`) — the rationale
("the invoking shell's credentials are not the operative ones for any swept
repo") holds only when per-repo credentials can actually differ. On a machine
with no direnv (row 11 — most stow consumers), the invoking shell's `gh auth`
state *is* what every repo gets, so an unauthenticated `gh` must still hard-exit
there: warning + exit 0 would read as "success, nothing to clean" instead of
the actionable `ERROR: gh is not authenticated` the operator needs.

Exit-code contract update (`:58-65`): document that under `--all-projects`
with direnv present, an unauthenticated invoking shell no longer produces
exit 1 by itself — each repo's own `gh auth` state governs that repo. Without
direnv, exit 1 for "unauthenticated" is unchanged.

## Critical files

- **`claude/.claude/scripts/cleanup-merged-branches.sh`** — add
  `load_repo_environment` and call it plus the root re-check at the top of
  `run_repo_cleanup` (`:202-208`); add the three `readonly` declarations;
  rewrite `print_skip_reason_lines` (`:369-374`) to aggregate; make
  `gh auth status` a warning under `--all-projects` (`:104-107`); update the
  header comment block (`:1-65`).
  - *Reuse:* the existing per-repo subshell (`:782`) is the seam — no re-exec,
    no new flag, so the script keeps its "no user-controlled argument"
    property and needs no `permissions` change (row 10). No new exit code is
    introduced, but `:58-65`'s description of exit 1 needs the direnv-
    conditional carve-out from **Design 4**.
  - *Constraint:* macOS bash 3.2 — no `declare -A`, `mapfile`, `readarray`,
    `sort -V` (`test_no_bash4_constructs.py`). Aggregation must use the
    linear-scan idiom already at `:171-185`, not an associative array.
- **`claude/.claude/scripts/tests/test_cleanup_merged_branches.py`** — the
  `fake_gh` fixture (`:149-169`) currently builds `{**os.environ, ...}`,
  inheriting `DIRENV_*` from a contributor's shell. Left as-is, `direnv export
  bash` run from `tmp_path` emits the *revert* half of that inherited diff,
  restoring a `PATH` without `tmp_path/gh_shim` — the script's next `gh` call
  would be the contributor's **real gh with their real token**, against real
  GitHub. Required, in one helper shared by every env-building site so no
  site can bypass it (`fake_gh` **and** the four tests that hand-roll their
  own shim dir/env at `:999`, `:1027`, `:1105`, `:1133` — those currently
  build `{**os.environ, "PATH": ...}` directly and must call the same helper):
  1. Scrub `DIRENV_*`, `GH_TOKEN`, `GH_HOST`, `GITHUB_TOKEN`,
     `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, `GH_CONFIG_DIR` from
     the env.
  2. Always write a default no-op `direnv` shim beside the `gh` shim;
     direnv-specific tests overwrite it.
  3. For the absent-direnv case, filter `PATH` entries containing an
     executable `direnv` — deterministic on machines with and without it.
  No env-var seam for the direnv binary path: that would give the script its
  first externally-supplied executable name.
  - The root-recheck guard (**Design 2**) and its test (case 9) cover
    `GIT_DIR`/`GIT_WORK_TREE` only, not `GIT_INDEX_FILE` — see Design 2's note.
- **`docs/scripts.md`** — the `**cleanup-merged-branches.sh**` bullet (~`:47`)
  is the canonical `--all-projects` prose. Add: each repo's environment is
  loaded via direnv when present, absent-direnv behavior is unchanged, skip
  reasons are aggregated, and — matching the header (`:58-65`) — under
  `--all-projects` with direnv present an unauthenticated invoking-shell `gh`
  no longer exits 1 by itself (each repo's own `gh auth` state governs); with
  no direnv, exit 1 for "unauthenticated" is unchanged.
- **`CHANGELOG.md`** — one entry.

Not touched: `_worktree-lib.sh`, `cleanup-idle-open-pr-worktrees.sh` (row 9 —
the structural-sibling audit comes back clean), any `settings.json` (row 10).

## Verification

`.venv/bin/pytest claude/.claude/scripts/tests/test_cleanup_merged_branches.py`,
plus `ruff`, `shellcheck`, and `test_no_bash4_constructs.py`.

The `gh` shim must key its canned rows on an env var the `direnv` shim exports
(e.g. `GH_TOKEN`) and **never** on cwd — keying on cwd would make case 1 pass
without direnv ever running.

1. **Per-repo environment is loaded.** Two repos under two roots, the direnv
   shim exporting a different identity for each. Fails on current code.
2. **No cross-repo leak.** Three repos where the middle exports nothing — it
   must not inherit the first repo's identity.
3. **Skip lines are aggregated.** A repo where every branch `gh`-fails yields
   one skip line, not N — asserted with every skipped branch name still
   present in that line, not just the count; sweep exits 0; sibling repo
   still cleaned. Add a single-skipped-branch case (count-of-1 phrasing) and a
   mixed case where `gh`-failure branches aggregate while a distinct-PR-number
   skip stays per-branch (**Design 3**'s claim, currently untested). Pin the
   aggregated line to the existing message text verbatim plus count and branch
   names — no added causal wording ("check your credentials" and similar):
   the text must stay accurate for non-credential `gh` failures too (rate
   limit, network, a non-GitHub or local-only remote under a swept root).
4. **Fail-closed is preserved.** In that same repo, the merged branch still
   exists afterward and zero destructive git commands were issued.
5. **Absent direnv** (PATH filtered): behavior identical to today.
6. **Non-allowed `.envrc`** (direnv exits 1): behavior identical to today.
7. **A stdin-reading `.envrc` does not hang the sweep** (row 6). Not
   assertable via `_run_script`'s default `stdin=subprocess.DEVNULL` — a
   stdin-reading shim hits EOF immediately regardless of whether `</dev/null`
   is present, passing on unfixed code. Use a pty (already the pattern in
   `TestTierBReachableNoMergedPR`) or a held-open pipe, with a `subprocess`
   timeout that fails the test rather than hanging CI.
8. **An `.envrc` exporting `DRY_RUN=0` does not delete under `--dry-run`.**
   Assert behaviorally (dry-run branch still exists, exit nonzero, no
   `git push --delete` issued) — bash's `readonly variable` diagnostic text
   and line number differ between bash 3.2 (macOS) and 5.x (CI); don't match it.
9. **Redirection guard — three sub-cases, one shared fixture.** Set up a
   second real repo (repo B, via the existing `_make_repo_with_remote`-style
   helper) with a local branch AND a remote ref sharing the exact name as a
   branch under test in repo A — an empty or differently-named repo B makes
   the assertions below pass identically on buggy and fixed code, so this
   fixture detail is required, not incidental. Redirect repo A's swept
   `.envrc` at repo B via each of:
   - `GIT_DIR` alone (no `GIT_WORK_TREE`) — the case `--show-toplevel` alone
     does not catch (reproduced in review: `--show-toplevel` stays unchanged
     while `git branch -D` operates against the redirected repo).
   - `GIT_WORK_TREE` (with or without `GIT_DIR`) — caught by the pre-existing
     `--show-toplevel` check.
   - `GIT_COMMON_DIR` alone — caught by neither of the above two checks;
     `git`'s own manual states non-worktree files (including `refs/heads/*`,
     what `branch -D` mutates) are taken from `GIT_COMMON_DIR` when set,
     independent of `GIT_DIR`/`GIT_WORK_TREE` (verified this session via
     `git help git`).

   Each sub-case asserts: the guard's abort message appears (if the three
   checks share one message, corroborate with which env var the case
   exported, since a regression dropping one of the three checks must not
   pass just because the shared message still appears on the other two); repo
   A's own branch is untouched; repo B's same-named **local branch** survives
   (`git branch --list`, the existing pattern from
   `test_dry_run_no_destructive_action`); and repo B's same-named **remote
   ref** survives (`git ls-remote` against repo B's bare remote) — the script
   also runs `git push origin --delete` (`:711` currently) and `git fetch
   --prune`, so a local-only check would miss a redirected remote delete.
10. **A repo under no `.envrc`, with a parent env loaded.** This exercises the
    script's handling of a scripted `unset` payload from the shim — it cannot
    pin real direnv's own behavior (row 4), only that this design handles that
    payload correctly.
11. **`--all-projects` with `gh auth status` failing, direnv present** — warns
    and the sweep proceeds (**Design 4**). **`--all-projects` with `gh auth
    status` failing, direnv absent** — still hard-exits 1, matching
    `test_gh_unauth_exits_nonzero`'s single-repo behavior. Neither path is
    exercised by any existing test.

Existing tests to reconcile: `test_gh_unauth_exits_nonzero` (`:612`) runs
single-repo (no `--all-projects`), so **Design 4**'s downgrade cannot affect
it — no change expected there; `test_normal_run_writes_nothing_to_stderr`
(`:1685`) — the load must stay silent on the success path;
`test_broken_repo_does_not_block_healthy_sibling` (`:2002`) — its broken repo
has no commits and no remote, so `git rev-parse --show-toplevel` still
succeeds there and the root re-check does not fire; the test's intent (an
unguarded `set -e` failure at `:229` does not abort the sweep) is preserved
because nothing in the new code runs before that line.

Manual, on the reporting machine — the only place the real multi-container
layout exists:

```bash
cleanup-merged-branches --all-projects --dry-run
```

Expect zero `gh lookup failed` lines where credentials were the cause, and any
remaining failure reported once per repo rather than once per branch.

## Out of scope

- **Reimplementing per-directory credential selection.** The script could read
  the provisioning repo's token files directly, removing the direnv dependency.
  Declined: it would hardcode one machine's token-file scheme into a script
  stowed to every consumer of this repo, and duplicate credential-selection
  logic that already has an owner.
- **Restoring credentials for a repo under no `.envrc`.** Per row 4, such a
  repo has direnv-sourced `GH_TOKEN`/`GH_HOST` *unset* rather than inherited
  from the parent container (a value exported directly from the user's shell
  profile, outside direnv's diff, is unaffected and survives). `gh` falls back
  to its own config. That matches what an interactive `cd` does — the contract
  this plan adopts throughout — so declining to special-case it keeps one
  behavior instead of two. Case 10 pins the behavior for every stow consumer,
  not only the reporting machine.
- **Parallelising the sweep.** The Tier B `[y/N]` prompts are serial by design.
- **Distinguishing a machine-wide `gh` outage from expected per-repo credential
  mismatch.** Under `--all-projects` with direnv present, a fully broken `gh`
  (revoked token, network down) and an ordinary per-repo mismatch both surface
  as aggregated `gh lookup failed` lines with exit 0 — row 12's decision
  ("a repo whose `gh` is unusable should report and continue, not fail the
  sweep") applies uniformly whether one repo or every repo hits it. Extending
  that same decision to the all-repos-failed case is consistent, not a new
  design choice; distinguishing the two would need either a repo-wide-failure
  counter or a bigger reporting mechanism, and the reported bug was about
  false skips, not about detecting a real widespread outage.
