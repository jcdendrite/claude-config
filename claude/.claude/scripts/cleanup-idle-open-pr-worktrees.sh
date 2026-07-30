#!/usr/bin/env bash
# cleanup-idle-open-pr-worktrees.sh — reclaim disk space from git worktrees
# for open PRs that are ready for review but not currently being worked on.
#
# Unlike cleanup-merged-branches.sh, this never touches a branch, a remote,
# or a PR — it only removes the local worktree directory. The branch ref
# lives in the main repo's refs/, not inside the worktree, so removal never
# risks losing committed-but-unpushed work: the commits stay reachable via
# the branch ref and the worktree comes back with
# `git worktree add <path> <branch>`.
#
# One bulk `gh pr list --state open` call (per repo, run once) replaces a
# per-branch query: classify_branch() in cleanup-merged-branches.sh makes
# one `gh` call per candidate branch, which is fine for a merge-status check
# that runs occasionally, but an idle-worktree sweep is exactly the case
# where a repo has many open-PR worktrees at once — one bulk call returns
# every field the classifier below needs.
#
# Classification per local branch, in order:
#   1. No open PR at all for this branch name -> not this tool's concern,
#      skipped silently (a candidate for cleanup-merged-branches.sh instead,
#      or simply an orphaned worktree neither script claims — see
#      docs/scripts.md for the accepted gap around a closed-but-unmerged PR).
#   2. The matching PR is a draft -> skipped, always. A draft is explicitly
#      WIP by GitHub's own definition, not "ready for review".
#   3. The matching PR's `updatedAt` is newer than --idle-hours -> skipped,
#      reported as "still active". `updatedAt` is a conservative proxy for
#      owner activity, not a precise one: GitHub bumps it on any PR metadata
#      change (a bot comment, a CI status transition, a label, someone
#      else's review request), not only a push by the branch owner. That
#      means this check can read "still active" when only CI touched the
#      PR — under-deletes (fails safe) rather than over-deletes.
#   4. Otherwise -> candidate. A worktree that doesn't actually exist for
#      this branch (never checked out, or it's the worktree this script is
#      running from) is skipped silently rather than erroring. A worktree a
#      live process is working inside is skipped and reported.
#   5. Removed via `git worktree remove` (no --force — a dirty working tree
#      makes git itself refuse the removal), capturing success/failure per
#      candidate so one refusal doesn't abort the batch under `set -euo
#      pipefail`.
#
# Live-worktree check window: the process-cwd snapshot behind step 4's
# worktree_in_use check is taken once, up front (collect_process_cwds, before
# the classification loop starts), but the actual `git worktree remove` calls
# for every candidate run afterward, in their own loop. A process that starts
# working inside a candidate's worktree after the snapshot but before that
# candidate's removal is not seen as in-use. Accepted as a known limitation
# rather than re-checked immediately before each removal: this is a
# manually-invoked, single-operator tool, and the next paragraph's "cost of a
# wrong call" argument for skipping an interactive prompt applies here too.
#
# No interactive prompt (unlike cleanup-merged-branches.sh's Tier B): once a
# worktree passes every check above, there's no comparable judgment call to
# prompt about, and the cost of a wrong call is a `git worktree add` +
# reinstall, not lost work. `--dry-run` covers "review before acting".
#
# Usage:
#   cleanup-idle-open-pr-worktrees.sh
#   cleanup-idle-open-pr-worktrees.sh --dry-run
#   cleanup-idle-open-pr-worktrees.sh --idle-hours=8
#
# Exit codes:
#   0  success (including no-op)
#   1  gh missing/unauthenticated, no 'origin' remote, or the gh pr list
#      query failed (non-zero exit, or a zero exit with an unparseable body)
#   2  bad arguments

set -euo pipefail

# progress / clear_progress, collect_process_cwds / worktree_in_use, and
# resolve_worktree_for_branch are shared with cleanup-merged-branches.sh.
# shellcheck source=_worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_worktree-lib.sh"

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $(basename "$0") [--dry-run] [--idle-hours=N]" >&2
}

DRY_RUN=0
IDLE_HOURS=4
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)        DRY_RUN=1 ;;
    --idle-hours=*)   IDLE_HOURS="${1#--idle-hours=}" ;;
    *)                usage; exit 2 ;;
  esac
  shift
done

# Fail loudly at the point of use rather than letting an empty
# --idle-hours= silently reach arithmetic as an unset/blank value.
IDLE_HOURS="${IDLE_HOURS:?idle-hours requires a value}"
if ! [[ "$IDLE_HOURS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --idle-hours requires a non-negative integer, got '${IDLE_HOURS}'." >&2
  usage
  exit 2
fi

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

if ! command -v gh &>/dev/null; then
  echo "ERROR: 'gh' (GitHub CLI) is not installed or not in PATH." >&2
  echo "Install it from https://cli.github.com/ and re-run." >&2
  exit 1
fi

if ! gh auth status &>/dev/null; then
  echo "ERROR: gh is not authenticated. Run 'gh auth login' first." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Repo root — all git ops run from here
# ---------------------------------------------------------------------------

REPO_ROOT=$(git rev-parse --show-toplevel)

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "ERROR: no 'origin' remote configured; cannot determine which repo's open PRs to check." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# ISO8601 -> epoch parsing (GNU/BSD date)
#
# `updatedAt` from `gh pr list --json updatedAt` is always UTC with a
# trailing Z (GitHub's REST/GraphQL timestamp format), so both branches
# parse it as UTC explicitly (-u / -j -u) rather than relying on the host's
# local timezone default.
# ---------------------------------------------------------------------------

parse_iso8601_epoch() {
  local iso_timestamp="$1"
  if date --version >/dev/null 2>&1; then
    # GNU date (Linux, or macOS with coreutils installed): -d accepts
    # ISO8601 directly.
    date -u -d "$iso_timestamp" +%s
  else
    # BSD/macOS date: -j (don't set the system clock), -f (input format).
    date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$iso_timestamp" +%s
  fi
}

# ---------------------------------------------------------------------------
# Bulk open-PR lookup — one call for every branch, not one call per branch.
#
# --limit 100: gh's default page size is 30; without an explicit limit a
# repo with more than 30 open PRs would silently truncate and quietly
# under-report idle candidates.
# ---------------------------------------------------------------------------

if ! PR_JSON=$(cd "$REPO_ROOT" && gh pr list \
      --state open \
      --limit 100 \
      --json headRefName,number,isDraft,updatedAt \
      2>/dev/null); then
  echo "ERROR: 'gh pr list' failed; cannot determine open PRs for this repo." >&2
  exit 1
fi

# Fail closed on unparseable output: a malformed body on a 0 exit (a
# truncated response, a stray banner mixed into stdout) must never be read
# as "no open PRs" — that would treat every branch with an actual open PR
# as a removal candidate instead of skipping it.
#
# The python source below is a double-quoted shell string, so the shell
# expands it before python ever sees it: no backticks, no dollar signs, and
# no unescaped double quotes may appear in it, comments included.
if ! PR_LINES=$(printf '%s' "$PR_JSON" | python3 -c "
import json, sys

try:
    rows = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)

# Tie-break for multiple rows sharing one headRefName (e.g. two PRs from
# the same head against different base branches): keep the row with the
# lowest PR number — the original PR opened against that head. Every
# classification field (draft, updatedAt) comes from that one winning row,
# never merged across rows.
best = {}
for row in rows:
    name = row.get('headRefName')
    number = row.get('number')
    if name is None or number is None:
        continue
    if name not in best or number < best[name]['number']:
        best[name] = row

for name, row in best.items():
    is_draft = '1' if row.get('isDraft') else '0'
    updated_at = row.get('updatedAt') or ''
    print(f\"{name}\t{row['number']}\t{is_draft}\t{updated_at}\")
"); then
  echo "ERROR: 'gh pr list' returned output that could not be parsed as JSON." >&2
  exit 1
fi

declare -a PR_BRANCHES=()
declare -a PR_NUMBERS=()
declare -a PR_ISDRAFT=()
declare -a PR_UPDATED_AT=()
while IFS=$'\t' read -r _pr_branch _pr_number _pr_isdraft _pr_updated; do
  [ -z "$_pr_branch" ] && continue
  PR_BRANCHES+=("$_pr_branch")
  PR_NUMBERS+=("$_pr_number")
  PR_ISDRAFT+=("$_pr_isdraft")
  PR_UPDATED_AT+=("$_pr_updated")
done <<< "$PR_LINES"

# lookup_pr_for_branch <branch> — populates MATCHED_PR_NUMBER,
# MATCHED_PR_ISDRAFT, MATCHED_PR_UPDATED_AT; returns 1 if no open PR
# matches this branch name.
lookup_pr_for_branch() {
  local branch="$1" _i
  MATCHED_PR_NUMBER=""
  MATCHED_PR_ISDRAFT=""
  MATCHED_PR_UPDATED_AT=""
  for _i in "${!PR_BRANCHES[@]}"; do
    if [ "${PR_BRANCHES[$_i]}" = "$branch" ]; then
      MATCHED_PR_NUMBER="${PR_NUMBERS[$_i]}"
      MATCHED_PR_ISDRAFT="${PR_ISDRAFT[$_i]}"
      MATCHED_PR_UPDATED_AT="${PR_UPDATED_AT[$_i]}"
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

declare -a ALL_BRANCHES=()
while IFS= read -r _branch_line; do
  ALL_BRANCHES+=("$_branch_line")
done < <(git for-each-ref --format='%(refname:short)' refs/heads)

declare -a CANDIDATE_BRANCHES=()
declare -a CANDIDATE_PATHS=()
declare -a CANDIDATE_PR_NUMBERS=()
declare -a CANDIDATE_UPDATED_AT=()
declare -a SKIPPED_DRAFT_BRANCHES=()
declare -a SKIPPED_ACTIVE_BRANCHES=()
declare -a SKIPPED_IN_USE_BRANCHES=()
SKIPPED_NO_PR_COUNT=0

IDLE_SECONDS=$(( IDLE_HOURS * 3600 ))
NOW_EPOCH=$(date -u +%s)

# Snapshot live process working directories once; worktree_in_use queries it.
collect_process_cwds

for BRANCH in "${ALL_BRANCHES[@]}"; do
  if ! lookup_pr_for_branch "$BRANCH"; then
    SKIPPED_NO_PR_COUNT=$(( SKIPPED_NO_PR_COUNT + 1 ))
    continue
  fi

  if [ "$MATCHED_PR_ISDRAFT" = "1" ]; then
    SKIPPED_DRAFT_BRANCHES+=("$BRANCH")
    continue
  fi

  # A date that fails to parse (unexpected from gh's own contract, but not
  # impossible) must not abort classification for every remaining branch
  # under set -e — fail closed the same way an unguarded `git worktree
  # remove` or a bare `(( ))` staleness comparison would otherwise do.
  if ! UPDATED_EPOCH=$(parse_iso8601_epoch "$MATCHED_PR_UPDATED_AT" 2>/dev/null); then
    SKIPPED_ACTIVE_BRANCHES+=("$BRANCH")
    continue
  fi

  # Written as the condition of the `if` itself, never a bare arithmetic
  # statement: a PR updated less than an hour ago (a routine case) produces
  # an elapsed value of exactly 0, and `set -e` treats a standalone `(( 0 ))`
  # as a failing command — aborting the whole run right here.
  if (( NOW_EPOCH - UPDATED_EPOCH < IDLE_SECONDS )); then
    SKIPPED_ACTIVE_BRANCHES+=("$BRANCH")
    continue
  fi

  resolve_worktree_for_branch "$BRANCH"
  if [ -z "$WORKTREE_PATH" ] || [ "$WORKTREE_PATH" = "$REPO_ROOT" ]; then
    # No removable worktree for this branch — never checked out, or it's
    # the worktree this script is running from. Nothing to do.
    continue
  fi

  WORKTREE_IN_USE=0
  worktree_in_use "$WORKTREE_PATH" || WORKTREE_IN_USE=$?
  if [ "$WORKTREE_IN_USE" -eq 0 ] || [ "$WORKTREE_IN_USE" -eq 2 ]; then
    SKIPPED_IN_USE_BRANCHES+=("$BRANCH")
    continue
  fi

  CANDIDATE_BRANCHES+=("$BRANCH")
  CANDIDATE_PATHS+=("$WORKTREE_PATH")
  CANDIDATE_PR_NUMBERS+=("$MATCHED_PR_NUMBER")
  CANDIDATE_UPDATED_AT+=("$MATCHED_PR_UPDATED_AT")
done

# print_summary <label> <count> — <label> is "removed" for a real run or
# "would-remove" for --dry-run, so the summary line never claims a removal
# happened when nothing was actually touched.
print_summary() {
  printf 'Summary: %s=%d skipped-active=%d skipped-draft=%d skipped-in-use=%d skipped-no-pr=%d\n' \
    "$1" "$2" "${#SKIPPED_ACTIVE_BRANCHES[@]}" "${#SKIPPED_DRAFT_BRANCHES[@]}" \
    "${#SKIPPED_IN_USE_BRANCHES[@]}" "$SKIPPED_NO_PR_COUNT"
}

# ---------------------------------------------------------------------------
# Dry-run: print candidates and reasons, take no action
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  if [ "${#CANDIDATE_BRANCHES[@]}" -gt 0 ]; then
    echo "Would remove (idle open-PR worktree):"
    for _c_i in "${!CANDIDATE_BRANCHES[@]}"; do
      printf '  %s (PR #%s, updated %s): %s\n' \
        "${CANDIDATE_BRANCHES[$_c_i]}" "${CANDIDATE_PR_NUMBERS[$_c_i]}" \
        "${CANDIDATE_UPDATED_AT[$_c_i]}" "${CANDIDATE_PATHS[$_c_i]}"
    done
  fi
  for BRANCH in "${SKIPPED_DRAFT_BRANCHES[@]+"${SKIPPED_DRAFT_BRANCHES[@]}"}"; do
    echo "Skipped (draft): ${BRANCH}"
  done
  for BRANCH in "${SKIPPED_ACTIVE_BRANCHES[@]+"${SKIPPED_ACTIVE_BRANCHES[@]}"}"; do
    echo "Skipped (still active): ${BRANCH}"
  done
  for BRANCH in "${SKIPPED_IN_USE_BRANCHES[@]+"${SKIPPED_IN_USE_BRANCHES[@]}"}"; do
    echo "Skipped (worktree in use, or its idle state could not be verified): ${BRANCH}"
  done
  print_summary "would-remove" "${#CANDIDATE_BRANCHES[@]}"
  exit 0
fi

# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------

REMOVED_COUNT=0

if [ "${#CANDIDATE_BRANCHES[@]}" -gt 0 ]; then
  echo "Removed:"
  for _c_i in "${!CANDIDATE_BRANCHES[@]}"; do
    BRANCH="${CANDIDATE_BRANCHES[$_c_i]}"
    WORKTREE_TO_REMOVE="${CANDIDATE_PATHS[$_c_i]}"
    # Captured rather than called bare: an unguarded `git worktree remove`
    # that exits non-zero on one candidate (e.g. uncommitted/untracked
    # changes — git itself refuses removal in that case, no --force here)
    # would abort the entire run under set -euo pipefail, silently dropping
    # every remaining candidate.
    if WORKTREE_REMOVE_OUTPUT=$(git worktree remove "$WORKTREE_TO_REMOVE" 2>&1); then
      echo "  ${BRANCH}: removed ${WORKTREE_TO_REMOVE}"
      REMOVED_COUNT=$(( REMOVED_COUNT + 1 ))
    else
      echo "  ${BRANCH}: remove failed (manual step needed)"
      if [ -n "$WORKTREE_REMOVE_OUTPUT" ]; then
        printf '%s\n' "$WORKTREE_REMOVE_OUTPUT" | sed 's/^/    /'
      fi
    fi
  done
fi

for BRANCH in "${SKIPPED_DRAFT_BRANCHES[@]+"${SKIPPED_DRAFT_BRANCHES[@]}"}"; do
  echo "Skipped (draft): ${BRANCH}"
done

for BRANCH in "${SKIPPED_ACTIVE_BRANCHES[@]+"${SKIPPED_ACTIVE_BRANCHES[@]}"}"; do
  echo "Skipped (still active): ${BRANCH}"
done

for BRANCH in "${SKIPPED_IN_USE_BRANCHES[@]+"${SKIPPED_IN_USE_BRANCHES[@]}"}"; do
  echo "Skipped (worktree in use, or its idle state could not be verified): ${BRANCH}"
done

print_summary "removed" "$REMOVED_COUNT"
