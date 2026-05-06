#!/usr/bin/env bash
# cleanup-merged-branches.sh — discover and clean up merged branches.
#
# Queries gh pr list for every local branch to find branches whose PRs
# have merged, then removes each one: worktree (if any), local branch,
# remote tracking ref, remote branch (if not auto-pruned), and finally
# fast-forwards the default branch.
#
# No user-controlled branch-name argument means no argument-injection
# attack surface against the destructive git ops. The exact-string
# permissions.allow entries in settings.json admit only the two
# invocation shapes below — no PreToolUse hook is needed because no
# wildcard is in play.
#
# Usage:
#   cleanup-merged-branches.sh
#   cleanup-merged-branches.sh --dry-run
#
# Exit codes:
#   0  success (including no-op)
#   1  gh missing or unauthenticated
#   2  bad arguments

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $(basename "$0") [--dry-run]" >&2
}

DRY_RUN=0
case "$#:${1:-}" in
  "0:"|"1:--dry-run") ;;
  *) usage; exit 2 ;;
esac
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

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

# ---------------------------------------------------------------------------
# Default branch resolution
# ---------------------------------------------------------------------------

DEFAULT_BRANCH=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)
if [ -z "$DEFAULT_BRANCH" ]; then
  # origin/HEAD unset — try to populate it once
  git remote set-head origin --auto &>/dev/null || true
  DEFAULT_BRANCH=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)
fi
# Strip the "origin/" prefix if present
DEFAULT_BRANCH="${DEFAULT_BRANCH#origin/}"
if [ -z "$DEFAULT_BRANCH" ]; then
  echo "WARNING: could not resolve default branch from origin/HEAD; falling back to 'main'." >&2
  DEFAULT_BRANCH="main"
fi

# ---------------------------------------------------------------------------
# Enumerate candidate branches
# ---------------------------------------------------------------------------

CURRENT_HEAD=$(git rev-parse --abbrev-ref HEAD)

# Collect candidates: all local branches except default and current HEAD.
mapfile -t ALL_BRANCHES < <(
  git for-each-ref --format='%(refname:short)' refs/heads
)

# Query gh for each candidate branch and collect merged ones.
declare -a MERGED_BRANCHES=()
declare -A MERGED_PR_INFO=()   # branch -> "PR #N, merged DATE"
declare -a SKIPPED_BRANCHES=() # checked-out candidates

for BRANCH in "${ALL_BRANCHES[@]}"; do
  # Skip the default branch
  [ "$BRANCH" = "$DEFAULT_BRANCH" ] && continue
  # Skip the currently checked-out branch (will recheck per-branch below)
  [ "$BRANCH" = "$CURRENT_HEAD" ] && continue

  # Query for a merged PR targeting this branch name
  PR_JSON=$(gh pr list \
    --head "$BRANCH" \
    --state merged \
    --limit 1 \
    --json number,headRefName,state,mergedAt \
    2>/dev/null || true)

  # A non-empty array with at least one entry means this branch was merged.
  # Parse number, mergedAt, and count in a single python3 invocation.
  PR_PARSED=$(echo "$PR_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if data:
    r = data[0]
    print(r['number'])
    print((r.get('mergedAt') or '')[:10])
else:
    print()
    print()
" 2>/dev/null || printf '\n\n')
  PR_NUMBER=$(echo "$PR_PARSED" | sed -n '1p')
  PR_MERGED_AT=$(echo "$PR_PARSED" | sed -n '2p')
  if [ -n "$PR_NUMBER" ]; then
    MERGED_BRANCHES+=("$BRANCH")
    MERGED_PR_INFO["$BRANCH"]="PR #${PR_NUMBER}, merged ${PR_MERGED_AT}"
  fi
done

# ---------------------------------------------------------------------------
# Dry-run: print candidates and exit
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  if [ "${#MERGED_BRANCHES[@]}" -eq 0 ] && [ "${#SKIPPED_BRANCHES[@]}" -eq 0 ]; then
    if [ -t 1 ]; then
      echo "nothing to clean"
    fi
    exit 0
  fi

  if [ "${#MERGED_BRANCHES[@]}" -gt 0 ]; then
    echo "Would clean up:"
    for BRANCH in "${MERGED_BRANCHES[@]}"; do
      echo "  ${BRANCH} (${MERGED_PR_INFO[$BRANCH]})"
    done
  fi

  # Re-check for currently checked-out candidates (those skipped during enumeration)
  for BRANCH in "${ALL_BRANCHES[@]}"; do
    [ "$BRANCH" = "$DEFAULT_BRANCH" ] && continue
    [ "$BRANCH" = "$CURRENT_HEAD" ] || continue
    # This branch is currently checked out — query to see if it was merged
    PR_JSON=$(gh pr list \
      --head "$BRANCH" \
      --state merged \
      --limit 1 \
      --json number,headRefName,state,mergedAt \
      2>/dev/null || true)
    PR_COUNT=$(echo "$PR_JSON" | python3 -c "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo 0)
    if [ "$PR_COUNT" -gt 0 ]; then
      echo "Skipped: ${BRANCH} (currently checked out)"
    fi
  done
  exit 0
fi

# ---------------------------------------------------------------------------
# No candidates
# ---------------------------------------------------------------------------

if [ "${#MERGED_BRANCHES[@]}" -eq 0 ]; then
  if [ -t 1 ]; then
    echo "nothing to clean"
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Per-branch cleanup
# ---------------------------------------------------------------------------

echo "Cleaned up:"

for BRANCH in "${MERGED_BRANCHES[@]}"; do
  # Re-check HEAD immediately before destructive ops (race guard)
  CURRENT_HEAD_NOW=$(git rev-parse --abbrev-ref HEAD)
  if [ "$BRANCH" = "$CURRENT_HEAD_NOW" ]; then
    SKIPPED_BRANCHES+=("$BRANCH")
    continue
  fi

  echo "  ${BRANCH}:"

  # Step 1: Remove worktree (if present)
  # Use --porcelain to get the exact path; do NOT construct from branch name
  # (slashes in branch names would break path interpolation).
  WORKTREE_PATH=""
  while IFS= read -r line; do
    if [[ "$line" == "worktree "* ]]; then
      CANDIDATE_PATH="${line#worktree }"
    elif [[ "$line" == "branch refs/heads/${BRANCH}" ]]; then
      WORKTREE_PATH="$CANDIDATE_PATH"
    fi
  done < <(git worktree list --porcelain)

  if [ -n "$WORKTREE_PATH" ] && [ "$WORKTREE_PATH" != "$REPO_ROOT" ]; then
    git worktree remove "$WORKTREE_PATH" --force 2>/dev/null || true
    echo "    worktree:       removed: ${WORKTREE_PATH}"
  else
    echo "    worktree:       not found"
  fi

  # Step 2: Fetch and prune — detect auto-deleted remote branches
  FETCH_OUTPUT=$(git fetch --prune 2>&1 || true)
  REMOTE_AUTO_PRUNED=0
  if echo "$FETCH_OUTPUT" | grep -qF "[deleted]" && \
     echo "$FETCH_OUTPUT" | grep -qF "origin/${BRANCH}"; then
    REMOTE_AUTO_PRUNED=1
  fi

  # Step 3: Delete local branch (force-delete handles squash merges)
  if git branch -D "$BRANCH" 2>/dev/null; then
    echo "    local branch:   deleted"
  else
    echo "    local branch:   not found"
  fi

  # Step 4: Delete remote branch (if not auto-pruned)
  if [ "$REMOTE_AUTO_PRUNED" -eq 1 ]; then
    echo "    remote branch:  auto-pruned"
  else
    # Check whether the remote ref still exists before attempting deletion
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

# Fetch any remaining changes to update origin/<DEFAULT_BRANCH>
git fetch origin "$DEFAULT_BRANCH" &>/dev/null || true

LOCAL_DEFAULT_SHA=$(git rev-parse "$DEFAULT_BRANCH" 2>/dev/null || true)
REMOTE_DEFAULT_SHA=$(git rev-parse "origin/${DEFAULT_BRANCH}" 2>/dev/null || true)

if [ -z "$LOCAL_DEFAULT_SHA" ] || [ -z "$REMOTE_DEFAULT_SHA" ]; then
  : # Can't compare — skip ff
elif [ "$LOCAL_DEFAULT_SHA" = "$REMOTE_DEFAULT_SHA" ]; then
  echo "Default branch: already current"
else
  COMMIT_COUNT=$(git rev-list --count "${DEFAULT_BRANCH}..origin/${DEFAULT_BRANCH}" 2>/dev/null || echo 0)
  if git merge --ff-only "origin/${DEFAULT_BRANCH}" -q 2>/dev/null; then
    echo "Default branch: fast-forwarded ${COMMIT_COUNT} commit(s)"
  else
    echo "Default branch: could not fast-forward (manual pull needed)"
  fi
fi

# ---------------------------------------------------------------------------
# Skipped branches
# ---------------------------------------------------------------------------

for BRANCH in "${SKIPPED_BRANCHES[@]}"; do
  echo "Skipped: ${BRANCH} (currently checked out)"
done

# Also check branches that were checked-out at enumeration time
for BRANCH in "${ALL_BRANCHES[@]}"; do
  [ "$BRANCH" = "$DEFAULT_BRANCH" ] && continue
  [ "$BRANCH" = "$CURRENT_HEAD" ] || continue
  # Check if this checked-out branch also had a merged PR
  PR_JSON=$(gh pr list \
    --head "$BRANCH" \
    --state merged \
    --limit 1 \
    --json number,headRefName,state,mergedAt \
    2>/dev/null || true)
  PR_COUNT=$(echo "$PR_JSON" | python3 -c "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo 0)
  if [ "$PR_COUNT" -gt 0 ]; then
    echo "Skipped: ${BRANCH} (currently checked out)"
  fi
done
