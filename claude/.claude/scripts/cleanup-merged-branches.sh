#!/usr/bin/env bash
# cleanup-merged-branches.sh — discover and clean up merged branches.
#
# Uses two signals to detect merged branches:
#   Tier A — gh pr list confirms a merged PR for this branch name, and
#             either the branch's current tip matches that merged PR's
#             headRefOid (tip-match), or the tip is a strict ancestor of
#             it (pr-head-ancestor) — fetching the PR's refs/pull/<n>/head
#             on demand when its objects aren't local yet.
#   Tier B — the branch tip is reachable from origin/<default> but no
#             merged PR was found for this name (branch renamed before
#             merge, worktree-prefixed name, etc.), or a same-named merged
#             PR's headRefOid is a strict ancestor of the tip
#             (pr-head-descendant) — real commits sit unmerged on top of
#             what GitHub actually merged.
#   Tier C — not reachable, no merged PR; never touched.
#
# Before either tier is considered, classify_branch() checks for an open PR
# on the branch name and, for a same-named merged PR, that the branch's
# current tip actually belongs to that merge — a name can be reused (old PR
# merged, new PR opened on the same head branch), and neither reachability
# nor a same-named merged PR by itself proves the current tip was part of
# it. A branch with an open PR is skipped rather than deleted. A
# merged-by-name match whose tip isn't part of that merge is either a Tier B
# pr-head-descendant prompt (tip strictly descends from the merge) or,
# absent that, a skip (reused name). A `gh` lookup failure also skips
# (fails closed) rather than treating an error as "no PR found". Skip
# lines are reported once per distinct reason, with a count and the
# branch names, rather than one line per branch.
#
# Tier A branches are deleted without prompting; --dry-run's preview names
# which basis (tip-match or pr-head-ancestor) qualified a Tier A branch.
# Tier B branches prompt interactively; when stdin is not a TTY, Tier B
# branches are skipped with a warning.
# A branch checked out in the main worktree is skipped whole: its worktree is
# never removed, and neither its local branch nor its remote branch is deleted.
#
# No user-controlled branch-name argument means no argument-injection
# attack surface against the destructive git ops. The exact-string
# permissions.allow entries in settings.json admit only the enumerated
# invocation shapes — no PreToolUse hook is needed because no
# wildcard is in play.
#
# Live-worktree guard:
# This script removes the worktree of every merged branch it cleans up.
# Before removing one it skips any worktree that a live process is
# working inside — it inspects process working directories (Linux
# /proc, otherwise lsof) and skips a worktree that is the cwd of a live
# process (a Claude Code session, a shell, a dev server). Removing such
# a worktree would leave that process serving a deleted directory.
# The check sees process working directories, and only those of the
# invoking user; it does not catch a process holding the worktree by an
# open file descriptor without cwd'ing in, nor one bind-mounted into it
# whose owning session has already exited. The live-process snapshot is
# also taken once, up front; the interactive per-branch confirmation
# prompts below hold it open for as long as the operator takes to answer,
# so a worktree first entered during that wait is not seen as in-use. So
# still prefer to run this only once other Claude Code sessions are idle.
#
# Usage:
#   cleanup-merged-branches.sh
#   cleanup-merged-branches.sh --dry-run
#   cleanup-merged-branches.sh --list-removable
#   cleanup-merged-branches.sh --all-projects --dry-run
#   cleanup-merged-branches.sh --all-projects
#
# --list-removable is a machine-readable preview that removes nothing. It runs
# the same `git fetch` and `git remote set-head` as --dry-run, and it cannot be
# combined with --dry-run. Stdout carries only one tab-separated line per
# worktree the real run would remove: <main worktree path> <branch> <worktree
# path>. Column 1 is the first record `git worktree list --porcelain` prints,
# whichever checkout the script runs from. In a `--separate-git-dir` repository
# that record can be the git dir rather than the checkout. Every other message
# goes to stderr.
# It lists only Tier A branches whose worktree is idle, not locked by a live
# or pid-less lock, clean, free of populated submodules, not the main worktree
# and not the worktree the script runs from. A worktree whose directory is gone
# is listed, because `git worktree remove` removes it. Tier B is left out
# because the real run removes it only after a `y` at a prompt.
# A listed worktree can be locked by a holder this user cannot signal: a dead
# pid, or another user's process. The real run unlocks it first, and a plain
# `git worktree remove` is refused until `git worktree unlock`.
# Every listed line is a worktree the real run would remove, but git stays the
# authority: a listed worktree can still fail to remove when git refuses for a
# reason the probes do not model. Two such states are known:
#   - a gitlink inactive in config whose directory has its own `.git`, which
#     `git submodule status` marks uninitialized and git counts as populated;
#   - a `.git` file whose back-link names another worktree's admin dir.
# A Tier A branch it could not evaluate is left out with one stderr line naming
# the branch and the reason, and the run exits 1 after printing every other
# line. The cases are:
#   - a failed `gh` lookup;
#   - an idle state that cannot be verified;
#   - a worktree without a `.git`, or a failed `git status`, `git rev-parse` or
#     `git submodule status`;
#   - a tab in a path.
# A failed up-front `git worktree list` exits 1 before any line, with a stderr
# line that names no branch.
# A nonzero exit means the list is partial: discard it.
# Exit 0 does not mean the list is complete, because a branch can be left out
# with no nonzero exit:
#   - a failed fetch of a PR's refs/pull/<n>/head, which leaves the branch
#     unclassified as Tier A;
#   - a failed per-branch `git worktree list` after the up-front listing
#     succeeded, which leaves the branch with no worktree to evaluate;
#   - under --all-projects, a configured root that is missing or holds no repos.
# A newline in a worktree path is unsupported: porcelain parsing is line-based.
# Under --all-projects each repo gets a banner on stderr, and any repo that
# exits nonzero makes the overall exit 1, with stdout still only list lines.
#
# --all-projects sweeps every git repo found under the roots listed in
# ~/.claude/cleanup-merged-branches-roots (one absolute path per line) instead
# of just the current repo — see the "--all-projects: root discovery" section
# below.
#
# Every invocation, single-repo included, loads that repo's own environment
# via `direnv export bash` (load_repo_environment) before any `gh` call runs
# — a script's own `cd` never fires direnv's shell hook, so without this a
# sweep would query every repo with the invoking shell's credentials, and a
# single-repo run could carry stale credentials from wherever the invoking
# shell last had its PROMPT_COMMAND hook fire (a non-interactive context,
# e.g. this script itself, never re-fires it). This resync can *remove*
# credentials the invoking shell had, not just add per-repo ones: direnv's
# own diff mechanism unsets whatever the shell's prior context added when the
# current directory's own `.envrc` doesn't re-supply it — matching what an
# interactive `cd` into that directory would do. Absent direnv, this is a
# no-op and every repo is queried with the invoking shell's environment,
# unchanged from before this behavior was added.
#
# Exit codes:
#   0  success (including no-op)
#   1  gh missing or unauthenticated; the bash floor check in `_config.sh`
#      failed; with --all-projects, also a missing or
#      unreadable roots config file, or set post-hoc if any repo's cleanup
#      crashed outright — a handled per-branch failure (worktree remove or
#      remote delete printed as "manual step needed") does not trip this;
#      check each repo's own output for those. Under --all-projects with
#      direnv installed, an unauthenticated invoking-shell `gh` no longer
#      causes exit 1 by itself — it's downgraded to a warning, since each
#      swept repo's own gh auth state is what actually governs that repo.
#      Without direnv, or in single-repo mode, unauthenticated `gh` still
#      hard-exits 1.
#   2  bad arguments, including --dry-run combined with --list-removable
#
# With --list-removable, exit 1 also means a Tier A branch could not be
# evaluated and is missing from the list; see above.

set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../hooks/_lib.sh"
# Progress helpers, live-worktree detection (collect_process_cwds /
# worktree_in_use), and the branch -> worktree path/lock lookup
# (resolve_worktree_for_branch) are shared with cleanup-idle-open-pr-worktrees.sh.
# shellcheck source=_worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_worktree-lib.sh"
# direnv_export_bash (load_repo_environment's own resolution call) is shared
# with ci-watch.sh.
# shellcheck source=_direnv-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_direnv-lib.sh"

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $(basename "$0") [--dry-run | --list-removable] [--all-projects]" >&2
}

DRY_RUN=0
DRY_RUN_FLAG_SEEN=0
LIST_REMOVABLE=0
ALL_PROJECTS=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)        DRY_RUN=1; DRY_RUN_FLAG_SEEN=1 ;;
    --list-removable) DRY_RUN=1; LIST_REMOVABLE=1 ;;
    --all-projects) ALL_PROJECTS=1 ;;
    *)              usage; exit 2 ;;
  esac
  shift
done
if [ "$DRY_RUN_FLAG_SEEN" -eq 1 ] && [ "$LIST_REMOVABLE" -eq 1 ]; then
  usage
  exit 2
fi
# readonly: load_repo_environment() below evals arbitrary `export` statements
# from an .envrc; a clobber attempt on DRY_RUN aborts under set -e instead of
# silently flipping --dry-run. LIST_REMOVABLE is readonly for the same
# reason. ALL_PROJECTS is readonly for symmetry — every read of it happens
# before load_repo_environment ever runs, so it has nothing left to protect
# by this point, but the shared declaration keeps every dispatch-controlling
# flag handled the same way.
readonly DRY_RUN LIST_REMOVABLE ALL_PROJECTS

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

if ! command -v gh &>/dev/null; then
  echo "ERROR: 'gh' (GitHub CLI) is not installed or not in PATH." >&2
  echo "Install it from https://cli.github.com/ and re-run." >&2
  exit 1
fi

if ! gh auth status &>/dev/null; then
  # Under --all-projects with direnv present, each repo loads its own gh
  # credentials below — the invoking shell's auth state isn't what any
  # swept repo actually uses, so a warning replaces the hard exit. Without
  # direnv (most stow consumers), the invoking shell's auth state is what
  # every repo gets, so it still must hard-exit here.
  if [ "$ALL_PROJECTS" -eq 1 ] && command -v direnv &>/dev/null; then
    echo "WARNING: gh is not authenticated for the invoking shell; each swept repo's own gh auth state governs under --all-projects." >&2
  else
    echo "ERROR: gh is not authenticated. Run 'gh auth login' first." >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# --all-projects: root discovery
#
# ROOTS_FILE is user-local config, not part of this repo — no template is
# shipped, matching ~/.claude/private-projects.md's opt-in-file precedent.
# CLEANUP_MERGED_BRANCHES_ROOTS_FILE is a test seam (same pattern as
# resume-context.sh's RESUME_CONTEXT_TMPDIR); production runs never set it.
# ---------------------------------------------------------------------------

ROOTS_FILE="${CLEANUP_MERGED_BRANCHES_ROOTS_FILE:-${HOME}/.claude/cleanup-merged-branches-roots}"

# Bounds a pathological root (e.g. a root accidentally pointed at $HOME) so
# discovery cannot walk the whole filesystem hunting for nested .git dirs.
MAX_REPO_DISCOVERY_DEPTH=5

if [ "$ALL_PROJECTS" -eq 1 ] && [ ! -r "$ROOTS_FILE" ]; then
  echo "ERROR: --all-projects roots config file not found or unreadable: ${ROOTS_FILE}" >&2
  echo "Create it with one absolute directory path per line — blank lines and '#' comments are ignored, and a leading '~' or '~/' expands to \$HOME. Example:" >&2
  echo "  ~/code" >&2
  echo "  /opt/repos" >&2
  exit 1
fi

# read_configured_roots — parse ROOTS_FILE into the global CONFIGURED_ROOTS
# array: one absolute directory per line, blank lines and `#`-comments
# skipped, CRLF stripped, leading/trailing whitespace trimmed, and a leading
# `~`/`~/` expanded to $HOME via literal prefix substitution (not full
# tilde-user expansion).
declare -a CONFIGURED_ROOTS=()
read_configured_roots() {
  local raw_line line
  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    line=${raw_line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    case "$line" in '#'*) continue ;; esac
    if [[ "$line" == "~" ]]; then
      line="$HOME"
    elif [[ "$line" == \~/* ]]; then
      line="${HOME}${line#\~}"
    fi
    CONFIGURED_ROOTS+=("$line")
  done < "$ROOTS_FILE"
}

# discover_repo_roots — populate the global DISCOVERED_REPOS array with the
# deduplicated, absolute repo root of every git repo found under
# CONFIGURED_ROOTS. `-type d -name .git -prune` stops descent the instant a
# repo root is found, so it never walks into a matched repo's own
# .claude/worktrees/<branch>/ subdirectories — those hold a `.git` *file* (a
# linked-worktree pointer), which `-type d` never matches, and pruning
# already happened one level up at the parent repo's own .git directory. A
# root line that is not a directory (typo, deleted since configured) is
# warned to stderr and skipped; two configured roots that both reach the
# same repo contribute it only once.
declare -a DISCOVERED_REPOS=()
discover_repo_roots() {
  # Linear membership scan; fine at the scale a machine's own repo count reaches.
  local root git_dir repo_root already_seen _existing_repo
  for root in "${CONFIGURED_ROOTS[@]}"; do
    if [ ! -d "$root" ]; then
      echo "WARNING: configured root is not a directory, skipping: ${root}" >&2
      continue
    fi
    while IFS= read -r -d '' git_dir; do
      repo_root=$(cd "$(dirname "$git_dir")" && pwd -P)
      already_seen=0
      for _existing_repo in "${DISCOVERED_REPOS[@]}"; do
        if [ "$_existing_repo" = "$repo_root" ]; then
          already_seen=1
          break
        fi
      done
      if [ "$already_seen" -eq 0 ]; then
        DISCOVERED_REPOS+=("$repo_root")
      fi
    done < <(find "$root" -maxdepth "$MAX_REPO_DISCOVERY_DEPTH" -type d -name .git -prune -print0 2>/dev/null)
  done
}

# load_repo_environment — apply the environment direnv's shell hook would
# apply on `cd`. That hook is installed into PROMPT_COMMAND, so a script's
# own `cd` never fires it and a sweep would query every repo with the
# invoking shell's credentials instead of each repo's own.
#
# The eval below is deliberately unguarded: DRY_RUN/LIST_REMOVABLE/
# ALL_PROJECTS are readonly by this point, so an .envrc attempting to clobber
# any of them aborts the whole script under set -e instead of silently taking
# effect (see the `readonly DRY_RUN LIST_REMOVABLE ALL_PROJECTS` comment above).
load_repo_environment() {
  local direnv_exports
  direnv_exports=$(direnv_export_bash)
  eval "$direnv_exports"
}

# ---------------------------------------------------------------------------
# run_repo_cleanup — single-repo cleanup body
#
# Everything from repo-root resolution through the end-of-run summary,
# scoped to one repo. Called directly for the default single-repo path, or
# once per repo in a backgrounded subshell under --all-projects (see the
# sweep loop below) — the subshell contains this function's internal `exit`
# calls so one repo's early exit doesn't end the whole sweep.
# ---------------------------------------------------------------------------
run_repo_cleanup() {

# ---------------------------------------------------------------------------
# Repo root — all git ops run from here
# ---------------------------------------------------------------------------

REPO_ROOT=$(git rev-parse --show-toplevel)
REPO_GIT_DIR=$(git rev-parse --absolute-git-dir)
REPO_GIT_COMMON_DIR=$(git rev-parse --path-format=absolute --git-common-dir)
# readonly: an .envrc's GIT_DIR/GIT_WORK_TREE/GIT_COMMON_DIR would otherwise
# repoint the git-root re-check below, and clobbering these directly would
# make the worktree-vs-main-checkout guard downstream compare against the
# wrong path.
readonly REPO_ROOT REPO_GIT_DIR REPO_GIT_COMMON_DIR

load_repo_environment

# eval above runs in this scope; re-verify none of the three moved. Per
# `git help git` (GIT_COMMON_DIR), refs/heads/* — what `git branch -D`
# mutates — is a non-worktree file taken from GIT_COMMON_DIR when set,
# independent of GIT_DIR/GIT_WORK_TREE, so --show-toplevel and
# --absolute-git-dir alone would miss a GIT_COMMON_DIR-only redirection.
if [ "$(git rev-parse --show-toplevel)" != "$REPO_ROOT" ] \
   || [ "$(git rev-parse --absolute-git-dir)" != "$REPO_GIT_DIR" ] \
   || [ "$(git rev-parse --path-format=absolute --git-common-dir)" != "$REPO_GIT_COMMON_DIR" ]; then
  echo "ERROR: this repo's environment changed git's repo root, git dir, or common dir (an .envrc exporting GIT_DIR, GIT_WORK_TREE, or GIT_COMMON_DIR?); aborting this repo untouched." >&2
  exit 1
fi

# REPO_ROOT is the checkout the script runs in, a linked worktree when run
# from one; the main worktree is what `git worktree remove` never removes.
# Outside list mode a failed listing falls back to REPO_ROOT, because
# resolve_worktree_for_branch then finds no worktree to act on anyway.
if ! MAIN_WORKTREE_PATH=$(main_worktree_path); then
  if [ "$LIST_REMOVABLE" -eq 1 ]; then
    echo "cleanup-merged-branches.sh: --list-removable: 'git worktree list --porcelain' failed; no worktree can be evaluated" >&2
    exit 1
  fi
  MAIN_WORKTREE_PATH="$REPO_ROOT"
fi

# List mode exits 1 after printing when any branch it could not evaluate
# was left out, so an incomplete list never reads as a complete one.
LIST_INDETERMINATE=0
# note_list_indeterminate <branch> <reason> — list mode only.
note_list_indeterminate() {
  echo "cleanup-merged-branches.sh: --list-removable: '$1' not evaluated: $2" >&2
  LIST_INDETERMINATE=1
}

# ---------------------------------------------------------------------------
# Default branch resolution
# ---------------------------------------------------------------------------

# No fallback branch name: an unresolved default would fail to exclude the
# repo's real default branch from the deletion candidates below.
if ! DEFAULT_BRANCH=$(_lib_default_branch_from_origin_head "$REPO_ROOT"); then
  # Known gap, accepted:
  # - Under --all-projects this repair call runs serially, so a hung/slow
  #   remote blocks the rest of the sweep; _lib_capped's 5s cap only applies
  #   when timeout/gtimeout is present.
  # - Runs only when origin/HEAD is already unresolvable, and the sweep
  #   stalls visibly on the printed "== <repo> ==" line rather than failing
  #   silently.
  _lib_capped git remote set-head origin --auto >/dev/null 2>&1 || true
  if ! DEFAULT_BRANCH=$(_lib_default_branch_from_origin_head "$REPO_ROOT"); then
    echo "cleanup-merged-branches.sh: could not resolve origin/HEAD, and 'git remote set-head origin --auto' did not repair it -- check that 'origin' exists and is reachable, then set the default branch manually with 'git remote set-head origin <branch>'" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Enumerate candidate branches
# ---------------------------------------------------------------------------

CURRENT_HEAD=$(git rev-parse --abbrev-ref HEAD)

# ---------------------------------------------------------------------------
# Branch classification
#
# Single source of truth for whether a branch is eligible for cleanup, used
# by the detection loop, the dry-run preview, and the checked-out-branch
# message so all three agree and none re-issues its own `gh` call.
# ---------------------------------------------------------------------------

# merged_row_by_tip_ancestry BASIS TIP ROWS
# ROWS is classify_branch's validated stale-row table: space-separated
# <pr>,<oid>,<merged-date> triples in gh's own row order, one per merged
# PR sharing this branch name. BASIS decides which side of the ancestry
# test TIP occupies:
#   pr-head-ancestor:   TIP is an ancestor of the row's oid (the local
#                        branch is behind what GitHub merged).
#   pr-head-descendant:  the row's oid is an ancestor of TIP (the local
#                        branch has extra commits on top of what GitHub
#                        merged).
# An unrecognized BASIS returns 1 without scanning. Scans every row, first
# hit wins. Tests only objects already present locally; classify_branch's
# own fetch loop is what makes a missing object visible to a second call
# of this scan.
# Prints the winning row's <pr>,<oid>,<merged-date> triple and returns 0
# on a hit; prints nothing and returns 1 when no row's oid satisfies the
# chosen ancestry test against TIP.
# Precondition: TIP must not already equal a row's oid, since
# `git merge-base --is-ancestor A B` is also true for A == B, and every
# caller only reaches this scan after classify_branch's own tip-match
# check (the `matched:` arm) has already ruled that case out.
merged_row_by_tip_ancestry() {
  local basis="$1" tip="$2" rows="$3" row pr oid rest merged_date
  local presumed_ancestor presumed_descendant
  local tip_is_presumed_ancestor
  case "$basis" in
    pr-head-ancestor)   tip_is_presumed_ancestor=1 ;;
    pr-head-descendant) tip_is_presumed_ancestor=0 ;;
    *) return 1 ;;
  esac
  for row in $rows; do
    pr="${row%%,*}"
    rest="${row#*,}"
    oid="${rest%%,*}"
    merged_date="${rest#*,}"
    [ -n "$oid" ] || continue
    if [ "$tip_is_presumed_ancestor" -eq 1 ]; then
      presumed_ancestor="$tip"
      presumed_descendant="$oid"
    else
      presumed_ancestor="$oid"
      presumed_descendant="$tip"
    fi
    if git merge-base --is-ancestor "$presumed_ancestor" "$presumed_descendant" 2>/dev/null; then
      printf '%s,%s,%s\n' "$pr" "$oid" "$merged_date"
      return 0
    fi
  done
  return 1
}

# classify_branch <branch>
#
# Prints a verdict to stdout and always returns 0 — a return-nonzero here
# would abort the whole sweep under `set -e`, which is why every lookup
# below is captured rather than allowed to propagate. No destructive side
# effects, so it is safe to call from message-only sites. Beyond the
# `gh pr list` call and the read-only git reachability check, it may also
# trigger network I/O: a tip that matches no headRefOid and isn't
# reachable from origin/<default> can trigger one `git fetch` per merged
# row whose object isn't local yet, to test ancestry against that PR's
# actual merged head (merged_row_by_tip_ancestry above).
#
# Verdicts:
#   tier-a:<pr>:<merged-date>:<basis>  confirmed merged; basis is
#                              tip-match (branch tip equals the merged
#                              PR's headRefOid) or pr-head-ancestor (tip
#                              is a strict ancestor of it)
#   tier-b:reachable:[<stale-pr>]
#                              reachable from origin/<default>; no PR
#                              matched this name — <stale-pr> is set when a
#                              same-named merged PR exists but this tip isn't
#                              part of that merge, empty otherwise
#   tier-b:pr-head-descendant:<pr>:<merged-date>:<ahead-count>
#                              a same-named merged PR's headRefOid is a
#                              strict ancestor of the tip: real commits
#                              sit unmerged on top of what was merged.
#                              <ahead-count> is the number of the tip's
#                              commits reachable from neither that head
#                              nor origin/<default> — the commits a `y`
#                              would discard. It is `?` if the count
#                              could not be computed.
#   skip-open-pr:<pr>         an open PR exists for this head branch name —
#                              never delete
#   skip-stale-name:<pr>      a merged PR shares this name, but the current
#                              tip is not part of that merge (reused name)
#   skip-error                the `gh` lookup failed; fail closed
#   none                      no signal either way (Tier C — untouched)
#
# The open-PR check and the Tier-A tip-verification check both live here:
# an open PR always wins over a same-named merged PR, and a
# merged-by-name match only qualifies for Tier A if the current tip
# matches or is an ancestor of that merge. Failing that, a descendant tip
# qualifies for the Tier-B pr-head-descendant prompt instead; otherwise
# it is a reused branch name.
classify_branch() {
  local branch="$1" pr_json tip classification stale_pr stale_rows rest
  local pr_number merged_date ancestor_row descendant_row ahead_count row oid

  # Fail closed on a `gh` error: capture output before parsing (rather than
  # piping straight into python3, which would lose gh's exit code) so a
  # transient rate-limit or auth failure never reads as "no PR found".
  # --limit 100: a generous cap — a single branch name being reused across
  # more than a handful of historical PRs is not a realistic case.
  if ! pr_json=$(gh pr list \
        --head "$branch" \
        --state all \
        --limit 100 \
        --json number,state,mergedAt,headRefOid \
        2>/dev/null); then
    printf 'skip-error\n'
    return 0
  fi

  # An empty tip (branch deleted by a concurrent run between enumeration
  # and this lookup) is safe rather than fail-closed: it can never equal a
  # headRefOid, which is always a 40-char SHA, so it cannot manufacture a
  # Tier-A match. The branch falls through to the reachability check and,
  # at worst, draws a Tier-B prompt for a ref that is already gone.
  tip=$(git rev-parse "$branch" 2>/dev/null || true)

  # Fail closed here too: gh's --json contract guarantees an array of
  # objects, but an unexpected shape must skip this branch, not abort the
  # whole sweep (set -e + pipefail would otherwise kill every remaining
  # branch on one bad record).
  #
  # The python source below is a double-quoted shell string, so the shell
  # expands it before python ever sees it: no backticks, no dollar signs,
  # and no unescaped double quotes may appear in it, comments included.
  # The shell would substitute them away — silently, in the case of a
  # comment — and could execute whatever they expanded to.
  if ! classification=$(printf '%s' "$pr_json" | python3 -c "
import json, sys

tip = sys.argv[1]
try:
    rows = json.load(sys.stdin)
except json.JSONDecodeError:
    # Malformed JSON on a 0 exit (truncated output, a stray banner mixed
    # into stdout) must fail closed, not read as \"no PR found\" — an
    # empty-list substitution here would bypass the open-PR guard exactly
    # like the original incident. This nonzero exit propagates out through
    # the pipeline to the caller, which turns it into a skip-error verdict.
    sys.exit(1)

open_pr = next((r for r in rows if r.get('state', '').upper() == 'OPEN'), None)
if open_pr is not None:
    print(f\"open:{open_pr['number']}\")
    sys.exit(0)

merged_rows = [r for r in rows if r.get('state', '').upper() == 'MERGED' and r.get('mergedAt')]
matched = next((r for r in merged_rows if r.get('headRefOid') == tip), None)
if matched is not None:
    print(f\"matched:{matched['number']}:{(matched.get('mergedAt') or '')[:10]}\")
elif merged_rows:
    # number must be all-digit and headRefOid must be exactly 40 lowercase
    # hex characters before either reaches a git fetch or merge-base
    # argument. This is character-class membership, not a regex, since
    # this source is a double-quoted shell string whose anchors would be
    # eaten by the shell before python runs.
    # A stray space or comma in row_date would misparse a downstream row
    # in the space-separated triple table.
    # It degrades to blank instead of sys.exit, matching row_oid's
    # treatment, since a bad date shouldn't fail the whole branch closed.
    DIGITS = '0123456789'
    HEX_LOWER_CHARS = '0123456789abcdef'
    DATE_CHARS = '0123456789-'
    triples = []
    for row in merged_rows:
        row_number = str(row['number'])
        if not row_number or any(c not in DIGITS for c in row_number):
            sys.exit(1)
        row_oid = row.get('headRefOid') or ''
        if len(row_oid) != 40 or any(c not in HEX_LOWER_CHARS for c in row_oid):
            row_oid = ''
        row_date = (row.get('mergedAt') or '')[:10]
        if any(c not in DATE_CHARS for c in row_date):
            row_date = ''
        triples.append(f\"{row_number},{row_oid},{row_date}\")
    print(f\"stale:{' '.join(triples)}\")
else:
    print('none')
" "$tip"); then
    printf 'skip-error\n'
    return 0
  fi

  case "$classification" in
    open:*)
      printf 'skip-open-pr:%s\n' "${classification#open:}"
      return 0
      ;;
    matched:*)
      rest="${classification#matched:}"
      pr_number="${rest%%:*}"
      merged_date="${rest#*:}"
      printf 'tier-a:%s:%s:tip-match\n' "$pr_number" "$merged_date"
      return 0
      ;;
    stale:*)
      stale_rows="${classification#stale:}"
      stale_pr="${stale_rows%%,*}"
      ;;
  esac

  if git merge-base --is-ancestor "$branch" \
       "refs/remotes/origin/${DEFAULT_BRANCH}" 2>/dev/null; then
    # Reachability wins over a same-named-but-different-tip merged PR: carry
    # stale_pr (if set) so callers can report that a merged PR by this name
    # exists, just not the one that produced this tip.
    printf 'tier-b:reachable:%s\n' "${stale_pr:-}"
    return 0
  fi

  if [ -n "$tip" ] && [ -n "${stale_rows:-}" ]; then
    if ancestor_row=$(merged_row_by_tip_ancestry pr-head-ancestor "$tip" "$stale_rows"); then
      pr_number="${ancestor_row%%,*}"
      rest="${ancestor_row#*,}"
      merged_date="${rest#*,}"
      printf 'tier-a:%s:%s:pr-head-ancestor\n' "$pr_number" "$merged_date"
      return 0
    fi

    # Local objects don't contain the merged head.
    # Fetch each missing row's PR ref independently rather than in one
    # batched multi-refspec call -- a single fetch spanning several
    # refspecs aborts the whole transfer the moment any one of them is
    # unresolvable on the remote, which would silently defeat the
    # multi-row scan for exactly the case this loop exists to handle.
    # `|| true` forces the fetch attempt to a 0 exit regardless of
    # outcome, so a missing or unresolvable ref is silently skipped
    # rather than surfaced as a shell error.
    for row in $stale_rows; do
      pr_number="${row%%,*}"
      rest="${row#*,}"
      oid="${rest%%,*}"
      [ -n "$oid" ] || continue
      git cat-file -e "${oid}^{commit}" 2>/dev/null && continue
      _lib_capped_for 15 git fetch --quiet origin "refs/pull/${pr_number}/head" 2>/dev/null || true
    done

    if ancestor_row=$(merged_row_by_tip_ancestry pr-head-ancestor "$tip" "$stale_rows"); then
      pr_number="${ancestor_row%%,*}"
      rest="${ancestor_row#*,}"
      merged_date="${rest#*,}"
      printf 'tier-a:%s:%s:pr-head-ancestor\n' "$pr_number" "$merged_date"
      return 0
    fi

    # Only reached after both forward-scan passes fail, so a row the
    # forward scan could confirm always wins over one only this scan
    # would match. No fetch is needed here: an ancestor head is already
    # in the tip's own local history.
    if descendant_row=$(merged_row_by_tip_ancestry pr-head-descendant "$tip" "$stale_rows"); then
      pr_number="${descendant_row%%,*}"
      rest="${descendant_row#*,}"
      oid="${rest%%,*}"
      merged_date="${rest#*,}"
      ahead_count=$(git rev-list --count "$tip" "^${oid}" "^refs/remotes/origin/${DEFAULT_BRANCH}" 2>/dev/null) || ahead_count='?'
      printf 'tier-b:pr-head-descendant:%s:%s:%s\n' "$pr_number" "$merged_date" "$ahead_count"
      return 0
    fi
  fi

  if [ -n "${stale_pr:-}" ]; then
    printf 'skip-stale-name:%s\n' "$stale_pr"
    return 0
  fi

  printf 'none\n'
  return 0
}

# print_skip_reason_lines — report every branch classify_branch skipped
# (open PR / stale name / gh error), and why, so a skip is never silent.
# Groups by identical reason text: a repo where every branch fails `gh` the
# same way reports one line with a count and the branch names, not one line
# per branch. Distinct-PR-number reasons (open PR, stale name) differ per
# branch and so stay one line each, unaffected by the grouping.
print_skip_reason_lines() {
  # Linear scan against parallel arrays; fine for the handful of distinct skip reasons.
  local -a _reason_messages=()
  local -a _reason_branch_lists=()
  local -a _reason_counts=()
  local _i _j _found _message
  for _i in "${!SKIP_REASON_BRANCHES[@]}"; do
    _message="${SKIP_REASON_MESSAGES[$_i]}"
    _found=-1
    for _j in "${!_reason_messages[@]}"; do
      if [ "${_reason_messages[$_j]}" = "$_message" ]; then
        _found="$_j"
        break
      fi
    done
    if [ "$_found" -eq -1 ]; then
      _reason_messages+=("$_message")
      _reason_branch_lists+=("${SKIP_REASON_BRANCHES[$_i]}")
      _reason_counts+=(1)
    else
      _reason_branch_lists[_found]="${_reason_branch_lists[_found]}, ${SKIP_REASON_BRANCHES[$_i]}"
      _reason_counts[_found]=$(( _reason_counts[_found] + 1 ))
    fi
  done
  for _i in "${!_reason_messages[@]}"; do
    printf 'Skipped %d branch(es) (%s): %s\n' \
      "${_reason_counts[$_i]}" "${_reason_messages[$_i]}" "${_reason_branch_lists[$_i]}"
  done
}

# checked_out_skip_line — report the currently checked-out branch if it
# would otherwise be a cleanup candidate. The detection loop below never
# classifies CURRENT_HEAD (it can't be deleted while checked out), so this
# is CURRENT_HEAD's only classify_branch call, used purely to gate this
# message — never to delete.
checked_out_skip_line() {
  [ "$CURRENT_HEAD" = "$DEFAULT_BRANCH" ] && return 0
  local verdict
  verdict=$(classify_branch "$CURRENT_HEAD")
  case "$verdict" in
    tier-a:*|tier-b:*)
      echo "Skipped: ${CURRENT_HEAD} (currently checked out)"
      ;;
    skip-open-pr:*)
      echo "Skipped: ${CURRENT_HEAD} (currently checked out; open PR #${verdict#skip-open-pr:})"
      ;;
    skip-stale-name:*|skip-error|none)
      ;;
  esac
}

ALL_BRANCHES=()
while IFS= read -r _branch_line; do
  ALL_BRANCHES+=("$_branch_line")
done < <(
  git for-each-ref --format='%(refname:short)' refs/heads
)

declare -a MERGED_BRANCHES=()
declare -a MERGED_PR_INFO_VALUES=()
declare -a TIER_VALUES=()
declare -a SKIPPED_BRANCHES=()
declare -a SKIP_REASON_BRANCHES=()
declare -a SKIP_REASON_MESSAGES=()

CANDIDATE_COUNT=0
for _B in "${ALL_BRANCHES[@]}"; do
  [ "$_B" = "$DEFAULT_BRANCH" ] && continue
  [ "$_B" = "$CURRENT_HEAD" ] && continue
  CANDIDATE_COUNT=$(( CANDIDATE_COUNT + 1 ))
done

if [ -t 2 ] && [ "$CANDIDATE_COUNT" -gt 0 ]; then
  printf 'Scanning %d branch(es) for merged PRs...\n' "$CANDIDATE_COUNT" >&2
fi

git fetch origin "$DEFAULT_BRANCH" --quiet 2>/dev/null || true

_PROGRESS_I=0
for BRANCH in "${ALL_BRANCHES[@]}"; do
  [ "$BRANCH" = "$DEFAULT_BRANCH" ] && continue
  [ "$BRANCH" = "$CURRENT_HEAD" ] && continue
  _PROGRESS_I=$(( _PROGRESS_I + 1 ))
  progress "$_PROGRESS_I" "$CANDIDATE_COUNT" "$BRANCH"

  VERDICT=$(classify_branch "$BRANCH")

  case "$VERDICT" in
    tier-a:*)
      _rest="${VERDICT#tier-a:}"
      _pr_number="${_rest%%:*}"
      _rest="${_rest#*:}"
      _merged_date="${_rest%%:*}"
      _basis="${_rest#*:}"
      MERGED_BRANCHES+=("$BRANCH")
      if [ "$_basis" = "pr-head-ancestor" ]; then
        MERGED_PR_INFO_VALUES+=("PR #${_pr_number}, merged ${_merged_date}; local tip is an ancestor of that PR's merged head")
      else
        MERGED_PR_INFO_VALUES+=("PR #${_pr_number}, merged ${_merged_date}")
      fi
      TIER_VALUES+=("A")
      ;;
    tier-b:*)
      _rest="${VERDICT#tier-b:}"
      _basis="${_rest%%:*}"
      _rest="${_rest#*:}"
      MERGED_BRANCHES+=("$BRANCH")
      if [ "$_basis" = "pr-head-descendant" ]; then
        _pr_number="${_rest%%:*}"
        _rest="${_rest#*:}"
        _merged_date="${_rest%%:*}"
        _ahead_count="${_rest#*:}"
        MERGED_PR_INFO_VALUES+=("PR #${_pr_number}, merged ${_merged_date}; local tip is ${_ahead_count} commit(s) ahead of that PR's merged head and origin/${DEFAULT_BRANCH}")
      else
        _stale_pr="$_rest"
        if [ -n "$_stale_pr" ]; then
          MERGED_PR_INFO_VALUES+=("reachable from origin/${DEFAULT_BRANCH}; a merged PR #${_stale_pr} shares this name but this tip isn't part of that merge")
        else
          MERGED_PR_INFO_VALUES+=("reachable from origin/${DEFAULT_BRANCH}; no merged PR for this name")
        fi
      fi
      TIER_VALUES+=("B")
      ;;
    skip-open-pr:*)
      SKIP_REASON_BRANCHES+=("$BRANCH")
      SKIP_REASON_MESSAGES+=("open PR #${VERDICT#skip-open-pr:}")
      ;;
    skip-stale-name:*)
      SKIP_REASON_BRANCHES+=("$BRANCH")
      SKIP_REASON_MESSAGES+=("merged PR #${VERDICT#skip-stale-name:} by name only; current tip not part of that merge — likely a reused branch name")
      ;;
    skip-error)
      SKIP_REASON_BRANCHES+=("$BRANCH")
      SKIP_REASON_MESSAGES+=("gh lookup failed; skipping to fail closed")
      if [ "$LIST_REMOVABLE" -eq 1 ]; then
        note_list_indeterminate "$BRANCH" "gh lookup failed"
      fi
      ;;
    none)
      ;;
  esac
done
clear_progress

# Snapshot live process working directories once; worktree_in_use queries it.
collect_process_cwds

# ---------------------------------------------------------------------------
# Dry-run: print candidates and exit
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  declare -a _DRY_TIER_A=()
  declare -a _DRY_TIER_B=()
  for _mb_i in "${!MERGED_BRANCHES[@]}"; do
    if [ "${TIER_VALUES[$_mb_i]}" = "A" ]; then
      _DRY_TIER_A+=("${MERGED_BRANCHES[$_mb_i]}")
    else
      _DRY_TIER_B+=("${MERGED_BRANCHES[$_mb_i]}")
    fi
  done

  # emit_removable_line — print "<main worktree>\t<branch>\t<worktree>" when
  # worktree_removal_verdict says the real run would remove this Tier A
  # branch's worktree. A worktree it cannot evaluate is reported on stderr and
  # makes the run exit 1.
  emit_removable_line() {
    local branch="$1"
    worktree_removal_verdict "$branch" "$MAIN_WORKTREE_PATH" "$REPO_ROOT"
    case "$WORKTREE_REMOVAL_VERDICT" in
      unlock-remove | remove) ;;
      idle-unverifiable)
        note_list_indeterminate "$branch" "cannot verify the worktree is idle"
        return 0
        ;;
      status-unverifiable)
        note_list_indeterminate "$branch" "cannot verify ${WORKTREE_PATH} is clean"
        return 0
        ;;
      submodules-unverifiable)
        note_list_indeterminate "$branch" "cannot verify ${WORKTREE_PATH} has no populated submodules"
        return 0
        ;;
      *) return 0 ;;
    esac
    # Refnames cannot contain a tab, so only the paths can break the format.
    case "$MAIN_WORKTREE_PATH$WORKTREE_PATH" in
      *$'\t'*)
        note_list_indeterminate "$branch" "a tab in a path would break the line format"
        return 0
        ;;
    esac
    # Porcelain parsing is line-based and REPO_ROOT is whole only when run from
    # the main checkout, so this misses a newline in a linked worktree's path.
    case "$MAIN_WORKTREE_PATH$REPO_ROOT" in
      *$'\n'*)
        note_list_indeterminate "$branch" "a newline in the repo path would break the line format"
        return 0
        ;;
    esac
    printf '%s\t%s\t%s\n' "$MAIN_WORKTREE_PATH" "$branch" "$WORKTREE_PATH"
  }

  if [ "$LIST_REMOVABLE" -eq 1 ]; then
    for BRANCH in "${_DRY_TIER_A[@]}"; do
      emit_removable_line "$BRANCH"
    done
    [ "$LIST_INDETERMINATE" -eq 0 ] || exit 1
    exit 0
  fi

  if [ "${#_DRY_TIER_A[@]}" -eq 0 ] && [ "${#_DRY_TIER_B[@]}" -eq 0 ] \
     && [ "${#SKIPPED_BRANCHES[@]}" -eq 0 ] && [ "${#SKIP_REASON_BRANCHES[@]}" -eq 0 ]; then
    if [ -t 1 ]; then
      echo "nothing to clean"
    fi
    exit 0
  fi

  _dry_print_branch_with_lock() {
    local _branch="$1"
    local _tag=""
    worktree_removal_verdict "$_branch" "$MAIN_WORKTREE_PATH" "$REPO_ROOT"
    case "$WORKTREE_REMOVAL_VERDICT" in
      main) _tag=" [checked out in the main worktree — would skip]" ;;
      current-worktree) _tag=" [the worktree this script is running from — would skip]" ;;
      in-use) _tag=" [worktree in use — would skip]" ;;
      idle-unverifiable) _tag=" [worktree idle state unverifiable — would skip]" ;;
      live-lock) _tag=" [locked by live pid ${WORKTREE_LOCK_PID} — would skip]" ;;
      pidless-lock) _tag=" [locked, no pid recorded — treated as live, would skip]" ;;
      dirty) _tag=" [worktree has uncommitted changes — remove would fail]" ;;
      submodules) _tag=" [worktree has populated submodules — remove would fail]" ;;
      status-unverifiable | submodules-unverifiable) _tag=" [worktree state unverifiable — remove may fail]" ;;
      unlock-remove) _tag=" [locked — will unlock and remove]" ;;
      none | remove) ;;
    esac
    _branch_info=""
    for _mb_i in "${!MERGED_BRANCHES[@]}"; do
      if [ "${MERGED_BRANCHES[$_mb_i]}" = "$_branch" ]; then
        _branch_info="${MERGED_PR_INFO_VALUES[$_mb_i]}"
        break
      fi
    done
    echo "  ${_branch} (${_branch_info})${_tag}"
  }

  if [ "${#_DRY_TIER_A[@]}" -gt 0 ]; then
    echo "Would clean up (confirmed merged):"
    for BRANCH in "${_DRY_TIER_A[@]}"; do
      _dry_print_branch_with_lock "$BRANCH"
    done
  fi

  if [ "${#_DRY_TIER_B[@]}" -gt 0 ]; then
    echo "Probable merges (would prompt):"
    for BRANCH in "${_DRY_TIER_B[@]}"; do
      _dry_print_branch_with_lock "$BRANCH"
    done
  fi

  print_skip_reason_lines
  checked_out_skip_line
  exit 0
fi

# ---------------------------------------------------------------------------
# Pre-cleanup confirmation pass
# ---------------------------------------------------------------------------

declare -a TO_DELETE=()
declare -a SKIPPED_NEEDS_PROMPT=()

for _mb_i in "${!MERGED_BRANCHES[@]}"; do
  BRANCH="${MERGED_BRANCHES[$_mb_i]}"
  _branch_tier="${TIER_VALUES[$_mb_i]}"
  if [ "$_branch_tier" = "A" ]; then
    TO_DELETE+=("$BRANCH")
  elif [ "$_branch_tier" = "B" ]; then
    if [ -t 0 ]; then
      printf "delete '%s' (%s)? [y/N]: " \
        "$BRANCH" "${MERGED_PR_INFO_VALUES[$_mb_i]}"
      # A closed stdin (EOF, e.g. an SSH session dropping mid-run) makes
      # this read fail; the fallback keeps the branch, treating EOF as a
      # decline. That is fail-safe (nothing is deleted) but is not
      # distinguished from an explicit "N" in the run summary. Acceptable
      # on a single-operator tool; revisit if EOF-vs-decline reporting
      # ever needs to be observable.
      read -r _REPLY || _REPLY=""
      if [[ "$_REPLY" == "y" || "$_REPLY" == "Y" ]]; then
        TO_DELETE+=("$BRANCH")
      fi
    else
      SKIPPED_NEEDS_PROMPT+=("$BRANCH")
    fi
  fi
done

if [ "${#TO_DELETE[@]}" -eq 0 ] && [ "${#SKIPPED_NEEDS_PROMPT[@]}" -eq 0 ]; then
  if [ "${#SKIP_REASON_BRANCHES[@]}" -eq 0 ]; then
    if [ -t 1 ]; then
      echo "nothing to clean"
    fi
  else
    print_skip_reason_lines
  fi
  exit 0
fi

if [ "${#TO_DELETE[@]}" -eq 0 ] && [ "${#SKIPPED_NEEDS_PROMPT[@]}" -gt 0 ]; then
  printf 'Skipped %d probable-merge branch(es) (no TTY for prompt): %s\n' \
    "${#SKIPPED_NEEDS_PROMPT[@]}" "${SKIPPED_NEEDS_PROMPT[*]}"
  print_skip_reason_lines
  exit 0
fi

# ---------------------------------------------------------------------------
# Per-branch cleanup
# ---------------------------------------------------------------------------

declare -a SKIPPED_LIVE_LOCK=()
declare -a SKIPPED_IN_USE=()
declare -a SKIPPED_IN_USE_REASON_VALUES=()

echo "Cleaned up:"

for BRANCH in "${TO_DELETE[@]}"; do
  CURRENT_HEAD_NOW=$(git rev-parse --abbrev-ref HEAD)
  if [ "$BRANCH" = "$CURRENT_HEAD_NOW" ]; then
    SKIPPED_BRANCHES+=("$BRANCH")
    continue
  fi

  echo "  ${BRANCH}:"

  # The real run pays for the verdict's probes twice by design: list mode and --dry-run need them, and git stays the authority.
  worktree_removal_verdict "$BRANCH" "$MAIN_WORKTREE_PATH" "$REPO_ROOT"

  case "$WORKTREE_REMOVAL_VERDICT" in
    none)
      echo "    worktree:       not found"
      ;;
    main)
      echo "    worktree:       skipped (checked out in the main worktree)"
      SKIPPED_IN_USE+=("$BRANCH")
      SKIPPED_IN_USE_REASON_VALUES+=("checked out in the main worktree")
      continue
      ;;
    current-worktree)
      echo "    worktree:       skipped (the worktree this script is running from)"
      SKIPPED_IN_USE+=("$BRANCH")
      SKIPPED_IN_USE_REASON_VALUES+=("the worktree this script is running from")
      continue
      ;;
    in-use)
      echo "    worktree:       skipped (in use by a live process)"
      SKIPPED_IN_USE+=("$BRANCH")
      SKIPPED_IN_USE_REASON_VALUES+=("worktree in use by a live process")
      continue
      ;;
    idle-unverifiable)
      echo "    worktree:       skipped (cannot verify it is idle)"
      SKIPPED_IN_USE+=("$BRANCH")
      SKIPPED_IN_USE_REASON_VALUES+=("worktree idle state unverifiable")
      continue
      ;;
    live-lock)
      echo "    worktree:       skipped (locked by live pid ${WORKTREE_LOCK_PID})"
      SKIPPED_LIVE_LOCK+=("$BRANCH")
      continue
      ;;
    pidless-lock)
      echo "    worktree:       skipped (locked, no pid recorded — treated as live)"
      SKIPPED_LIVE_LOCK+=("$BRANCH")
      continue
      ;;
    *)
      # dirty, submodules, the two unverifiable probes, unlock-remove and
      # remove: git decides, so a refusal is reported, not predicted.
      WORKTREE_UNLOCKED=0
      if [ "$WORKTREE_LOCKED" -eq 1 ]; then
        if git worktree unlock "$WORKTREE_PATH" 2>/dev/null; then
          WORKTREE_UNLOCKED=1
          echo "    worktree:       unlocked stale lock (pid ${WORKTREE_LOCK_PID} dead)"
        fi
      fi
      WORKTREE_REMOVE_OUTPUT=
      if WORKTREE_REMOVE_OUTPUT=$(git worktree remove "$WORKTREE_PATH" 2>&1); then
        echo "    worktree:       removed: ${WORKTREE_PATH}"
      else
        echo "    worktree:       remove failed (manual step needed)"
        if [ -n "$WORKTREE_REMOVE_OUTPUT" ]; then
          printf '%s\n' "$WORKTREE_REMOVE_OUTPUT" | sed 's/^/                    /'
        fi
        # --no-optional-locks: git's own remove refusal already names which
        # rule fired (dirty/locked/submodules); this adds what is dirty.
        # Without a `.git`, status would walk up and report an enclosing repo's files.
        WORKTREE_STATUS_OUTPUT=""
        if [ -e "$WORKTREE_PATH/.git" ]; then
          WORKTREE_STATUS_OUTPUT=$(git --no-optional-locks -C "$WORKTREE_PATH" status --porcelain --ignore-submodules=none 2>&1) || true
        fi
        if [ -n "$WORKTREE_STATUS_OUTPUT" ]; then
          printf '%s\n' "$WORKTREE_STATUS_OUTPUT" | sed 's/^/                    /'
        fi
        # The reason names the dead pid: a lock with no pid reads as live, so later runs would skip the worktree forever.
        if [ "$WORKTREE_UNLOCKED" -eq 1 ]; then
          if git worktree lock --reason "kept by cleanup-merged-branches (pid ${WORKTREE_LOCK_PID})" "$WORKTREE_PATH" 2>/dev/null; then
            echo "    worktree:       re-locked (pid ${WORKTREE_LOCK_PID})"
          else
            echo "    worktree:       re-lock failed (manual step needed)"
          fi
        fi
        continue
      fi
      ;;
  esac

  # After --prune, check the tracking ref directly; substring grep on fetch output
  # can't distinguish "origin/feat/foo" from "origin/feat/foo-v2".
  FETCH_OUTPUT=$(git fetch --prune 2>&1 || true)
  REMOTE_AUTO_PRUNED=0
  if echo "$FETCH_OUTPUT" | grep -qF "[deleted]" && \
     ! git rev-parse --verify "refs/remotes/origin/${BRANCH}" &>/dev/null; then
    REMOTE_AUTO_PRUNED=1
  fi

  if git branch -D "$BRANCH" 2>/dev/null; then
    echo "    local branch:   deleted"
  else
    echo "    local branch:   not found"
  fi

  if [ "$REMOTE_AUTO_PRUNED" -eq 1 ]; then
    echo "    remote branch:  auto-pruned"
  else
    if git ls-remote --heads origin "$BRANCH" 2>/dev/null | grep -q .; then
      if git push origin --delete "$BRANCH" 2>/dev/null; then
        echo "    remote branch:  deleted"
      else
        echo "    remote branch:  delete failed (manual step needed)"
      fi
    else
      echo "    remote branch:  not on remote"
    fi
  fi
done

# ---------------------------------------------------------------------------
# Fast-forward default branch (once, after all per-branch cleanup)
# ---------------------------------------------------------------------------

git fetch origin "$DEFAULT_BRANCH" &>/dev/null || true

LOCAL_DEFAULT_SHA=$(git rev-parse "$DEFAULT_BRANCH" 2>/dev/null || true)
REMOTE_DEFAULT_SHA=$(git rev-parse "origin/${DEFAULT_BRANCH}" 2>/dev/null || true)

if [ -z "$LOCAL_DEFAULT_SHA" ] || [ -z "$REMOTE_DEFAULT_SHA" ]; then
  : # Can't compare — skip ff
elif [ "$LOCAL_DEFAULT_SHA" = "$REMOTE_DEFAULT_SHA" ]; then
  echo "Default branch: already current"
else
  COMMIT_COUNT=$(git rev-list --count "${DEFAULT_BRANCH}..origin/${DEFAULT_BRANCH}" 2>/dev/null || echo 0)
  # When on the default branch, merge --ff-only updates both the ref and the
  # working tree. When on a feature branch, use a fetch refspec to update the
  # default branch ref without touching the currently checked-out branch.
  CURRENT_HEAD_FOR_FF=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
  if [ "$CURRENT_HEAD_FOR_FF" = "$DEFAULT_BRANCH" ]; then
    FF_CMD=(git merge --ff-only "origin/${DEFAULT_BRANCH}" -q)
  else
    FF_CMD=(git fetch origin "${DEFAULT_BRANCH}:${DEFAULT_BRANCH}" -q)
  fi
  if "${FF_CMD[@]}" 2>/dev/null; then
    echo "Default branch: fast-forwarded ${COMMIT_COUNT} commit(s)"
  else
    echo "Default branch: could not fast-forward (manual pull needed)"
  fi
fi

# ---------------------------------------------------------------------------
# End-of-run summary
# ---------------------------------------------------------------------------

for BRANCH in "${SKIPPED_LIVE_LOCK[@]}"; do
  echo "Skipped (live agent lock): ${BRANCH}"
done

for _su_i in "${!SKIPPED_IN_USE[@]}"; do
  echo "Skipped (${SKIPPED_IN_USE_REASON_VALUES[$_su_i]}): ${SKIPPED_IN_USE[$_su_i]}"
done

if [ "${#SKIPPED_NEEDS_PROMPT[@]}" -gt 0 ]; then
  printf 'Skipped %d probable-merge branch(es) (no TTY for prompt): %s\n' \
    "${#SKIPPED_NEEDS_PROMPT[@]}" "${SKIPPED_NEEDS_PROMPT[*]}"
fi

print_skip_reason_lines

# ---------------------------------------------------------------------------
# Skipped branches
# ---------------------------------------------------------------------------

for BRANCH in "${SKIPPED_BRANCHES[@]}"; do
  echo "Skipped: ${BRANCH} (currently checked out)"
done

checked_out_skip_line

}

# ---------------------------------------------------------------------------
# Dispatch: single repo (default), or a sweep across every --all-projects root
# ---------------------------------------------------------------------------

if [ "$ALL_PROJECTS" -eq 0 ]; then
  run_repo_cleanup
else
  read_configured_roots
  discover_repo_roots

  if [ "${#DISCOVERED_REPOS[@]}" -eq 0 ]; then
    echo "No git repos found under any configured root in ${ROOTS_FILE}." >&2
    exit 0
  fi

  SWEEP_HAD_FAILURE=0
  for _sweep_repo in "${DISCOVERED_REPOS[@]}"; do
    if [ "$LIST_REMOVABLE" -eq 1 ]; then
      echo "== ${_sweep_repo} ==" >&2
    else
      echo "== ${_sweep_repo} =="
    fi
    # A subshell nested directly inside `if !(...)` or a `||` list runs with
    # errexit silently disabled for its whole execution (a documented bash
    # quirk, not specific to this script) — a genuine unguarded failure
    # inside run_repo_cleanup would then pass as success instead of aborting
    # that repo's cleanup. Backgrounding the subshell and `wait`-ing on it
    # keeps errexit intact inside the subshell while still letting the sweep
    # survive its nonzero exit. `<&0` undoes bash's default of redirecting a
    # backgrounded job's stdin to /dev/null, which would otherwise silently
    # break run_repo_cleanup's TTY-gated Tier B [y/N] prompts under a sweep.
    _sweep_status=0
    ( cd "$_sweep_repo" && run_repo_cleanup ) <&0 &
    _sweep_pid=$!
    wait "$_sweep_pid" || _sweep_status=$?
    if [ "$_sweep_status" -ne 0 ]; then
      echo "WARNING: cleanup failed in ${_sweep_repo}; continuing sweep" >&2
      SWEEP_HAD_FAILURE=1
    fi
  done

  if [ "$SWEEP_HAD_FAILURE" -eq 1 ]; then
    exit 1
  fi
fi
