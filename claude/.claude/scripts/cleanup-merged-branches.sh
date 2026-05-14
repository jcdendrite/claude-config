#!/usr/bin/env bash
# cleanup-merged-branches.sh — discover and clean up merged branches.
#
# Uses two signals to detect merged branches:
#   Tier A — gh pr list confirms a merged PR for this branch name.
#   Tier B — the branch tip is reachable from origin/<default> but no
#             merged PR was found for this name (branch renamed before
#             merge, worktree-prefixed name, etc.).
#   Tier C — not reachable, no merged PR; never touched.
#
# Tier A branches are deleted without prompting. Tier B branches prompt
# interactively; --yes auto-confirms them. When stdin is not a TTY and
# --yes is not set, Tier B branches are skipped with a warning.
#
# No user-controlled branch-name argument means no argument-injection
# attack surface against the destructive git ops. The exact-string
# permissions.allow entries in settings.json admit only the enumerated
# invocation shapes — no PreToolUse hook is needed because no
# wildcard is in play.
#
# Usage:
#   cleanup-merged-branches.sh
#   cleanup-merged-branches.sh --dry-run
#   cleanup-merged-branches.sh --yes
#   cleanup-merged-branches.sh --dry-run --yes
#
# Exit codes:
#   0  success (including no-op)
#   1  gh missing or unauthenticated
#   2  bad arguments

set -euo pipefail

# ---------------------------------------------------------------------------
# Progress helpers (stderr-only, no-op when stderr is not a TTY)
# ---------------------------------------------------------------------------

progress() {
  [ -t 2 ] || return 0
  local i=$1 n=$2 label=$3
  printf '\r  [%d/%d] %-60.60s' "$i" "$n" "$label" >&2
}

clear_progress() {
  [ -t 2 ] || return 0
  printf '\r%-80s\r' '' >&2
}

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $(basename "$0") [--dry-run] [--yes]" >&2
}

DRY_RUN=0
ASSUME_YES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes)     ASSUME_YES=1 ;;
    *)         usage; exit 2 ;;
  esac
  shift
done

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
  git remote set-head origin --auto &>/dev/null || true
  DEFAULT_BRANCH=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)
fi
DEFAULT_BRANCH="${DEFAULT_BRANCH#origin/}"
if [ -z "$DEFAULT_BRANCH" ]; then
  echo "WARNING: could not resolve default branch from origin/HEAD; falling back to 'main'." >&2
  DEFAULT_BRANCH="main"
fi

# ---------------------------------------------------------------------------
# Enumerate candidate branches
# ---------------------------------------------------------------------------

CURRENT_HEAD=$(git rev-parse --abbrev-ref HEAD)

mapfile -t ALL_BRANCHES < <(
  git for-each-ref --format='%(refname:short)' refs/heads
)

declare -a MERGED_BRANCHES=()
declare -A MERGED_PR_INFO=()
declare -A TIER=()
declare -a SKIPPED_BRANCHES=()

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

  PR_JSON=$(gh pr list \
    --head "$BRANCH" \
    --state merged \
    --limit 1 \
    --json number,mergedAt \
    2>/dev/null || true)

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
    TIER["$BRANCH"]="A"
    continue
  fi

  if git merge-base --is-ancestor "$BRANCH" \
       "refs/remotes/origin/${DEFAULT_BRANCH}" 2>/dev/null; then
    MERGED_BRANCHES+=("$BRANCH")
    MERGED_PR_INFO["$BRANCH"]="reachable from origin/${DEFAULT_BRANCH}; no merged PR for this name"
    TIER["$BRANCH"]="B"
  fi
done
clear_progress

# ---------------------------------------------------------------------------
# Dry-run: print candidates and exit
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  declare -a _DRY_TIER_A=()
  declare -a _DRY_TIER_B=()
  for BRANCH in "${MERGED_BRANCHES[@]}"; do
    if [ "${TIER[$BRANCH]}" = "A" ]; then
      _DRY_TIER_A+=("$BRANCH")
    else
      _DRY_TIER_B+=("$BRANCH")
    fi
  done

  if [ "${#_DRY_TIER_A[@]}" -eq 0 ] && [ "${#_DRY_TIER_B[@]}" -eq 0 ] && [ "${#SKIPPED_BRANCHES[@]}" -eq 0 ]; then
    if [ -t 1 ]; then
      echo "nothing to clean"
    fi
    exit 0
  fi

  _dry_print_branch_with_lock() {
    local _branch="$1"
    local _locked=0
    local _matched=0
    while IFS= read -r _line; do
      if [[ "$_line" == "worktree "* ]]; then
        _matched=0
      elif [[ "$_line" == "branch refs/heads/${_branch}" ]]; then
        _matched=1
      elif [ "$_matched" -eq 1 ] && [[ "$_line" == "locked"* ]]; then
        _locked=1
      elif [ -z "$_line" ]; then
        _matched=0
      fi
    done < <(git worktree list --porcelain)
    if [ "$_locked" -eq 1 ]; then
      echo "  ${_branch} (${MERGED_PR_INFO[$_branch]}) [locked — will unlock and remove]"
    else
      echo "  ${_branch} (${MERGED_PR_INFO[$_branch]})"
    fi
  }

  if [ "${#_DRY_TIER_A[@]}" -gt 0 ]; then
    echo "Would clean up (confirmed merged):"
    for BRANCH in "${_DRY_TIER_A[@]}"; do
      _dry_print_branch_with_lock "$BRANCH"
    done
  fi

  if [ "${#_DRY_TIER_B[@]}" -gt 0 ]; then
    echo "Probable merges (would prompt; --yes to auto-confirm):"
    for BRANCH in "${_DRY_TIER_B[@]}"; do
      _dry_print_branch_with_lock "$BRANCH"
    done
  fi

  for BRANCH in "${ALL_BRANCHES[@]}"; do
    [ "$BRANCH" = "$DEFAULT_BRANCH" ] && continue
    [ "$BRANCH" = "$CURRENT_HEAD" ] || continue
    PR_JSON=$(gh pr list \
      --head "$BRANCH" \
      --state merged \
      --limit 1 \
      --json number,mergedAt \
      2>/dev/null || true)
    PR_COUNT=$(echo "$PR_JSON" | python3 -c "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo 0)
    REACHABLE=0
    git merge-base --is-ancestor "$BRANCH" \
      "refs/remotes/origin/${DEFAULT_BRANCH}" 2>/dev/null && REACHABLE=1 || true
    if [ "$PR_COUNT" -gt 0 ] || [ "$REACHABLE" -eq 1 ]; then
      echo "Skipped: ${BRANCH} (currently checked out)"
    fi
  done
  exit 0
fi

# ---------------------------------------------------------------------------
# Pre-cleanup confirmation pass
# ---------------------------------------------------------------------------

declare -a TO_DELETE=()
declare -a SKIPPED_NEEDS_PROMPT=()

for BRANCH in "${MERGED_BRANCHES[@]}"; do
  if [ "${TIER[$BRANCH]}" = "A" ]; then
    TO_DELETE+=("$BRANCH")
  elif [ "${TIER[$BRANCH]}" = "B" ]; then
    if [ "$ASSUME_YES" -eq 1 ]; then
      TO_DELETE+=("$BRANCH")
    elif [ -t 0 ]; then
      printf "delete '%s' (reachable from origin/%s; no merged PR for this name)? [y/N]: " \
        "$BRANCH" "$DEFAULT_BRANCH"
      read -r _REPLY
      if [[ "$_REPLY" == "y" || "$_REPLY" == "Y" ]]; then
        TO_DELETE+=("$BRANCH")
      fi
    else
      SKIPPED_NEEDS_PROMPT+=("$BRANCH")
    fi
  fi
done

if [ "${#TO_DELETE[@]}" -eq 0 ] && [ "${#SKIPPED_NEEDS_PROMPT[@]}" -eq 0 ]; then
  if [ -t 1 ]; then
    echo "nothing to clean"
  fi
  exit 0
fi

if [ "${#TO_DELETE[@]}" -eq 0 ] && [ "${#SKIPPED_NEEDS_PROMPT[@]}" -gt 0 ]; then
  printf 'Skipped %d probable-merge branch(es) (no TTY for prompt; rerun with --yes or from a terminal): %s\n' \
    "${#SKIPPED_NEEDS_PROMPT[@]}" "${SKIPPED_NEEDS_PROMPT[*]}"
  exit 0
fi

# ---------------------------------------------------------------------------
# Per-branch cleanup
# ---------------------------------------------------------------------------

declare -a SKIPPED_LIVE_LOCK=()

echo "Cleaned up:"

for BRANCH in "${TO_DELETE[@]}"; do
  CURRENT_HEAD_NOW=$(git rev-parse --abbrev-ref HEAD)
  if [ "$BRANCH" = "$CURRENT_HEAD_NOW" ]; then
    SKIPPED_BRANCHES+=("$BRANCH")
    continue
  fi

  echo "  ${BRANCH}:"

  # Use --porcelain to get the exact path; do NOT construct from branch name
  # (slashes in branch names would break path interpolation).
  # The `locked` line appears after `branch` in a porcelain record, so we
  # use a deferred-commit pattern: finalize path + lock state at each record
  # boundary rather than at the moment the branch line is matched.
  WORKTREE_PATH=""
  WORKTREE_LOCKED=0
  WORKTREE_LOCK_PID=""
  _CANDIDATE_PATH=""
  _CANDIDATE_LOCKED=0
  _CANDIDATE_LOCK_PID=""
  _CANDIDATE_MATCHED=0
  _commit_wt_candidate() {
    if [ "$_CANDIDATE_MATCHED" -eq 1 ]; then
      WORKTREE_PATH="$_CANDIDATE_PATH"
      WORKTREE_LOCKED="$_CANDIDATE_LOCKED"
      WORKTREE_LOCK_PID="$_CANDIDATE_LOCK_PID"
    fi
  }
  while IFS= read -r line; do
    if [[ "$line" == "worktree "* ]]; then
      _commit_wt_candidate
      _CANDIDATE_PATH="${line#worktree }"
      _CANDIDATE_LOCKED=0
      _CANDIDATE_LOCK_PID=""
      _CANDIDATE_MATCHED=0
    elif [[ "$line" == "branch refs/heads/${BRANCH}" ]]; then
      _CANDIDATE_MATCHED=1
    elif [[ "$line" == "locked"* ]]; then
      _CANDIDATE_LOCKED=1
      if [[ "$line" =~ pid[[:space:]]+([0-9]+) ]]; then
        _CANDIDATE_LOCK_PID="${BASH_REMATCH[1]}"
      fi
    fi
  done < <(git worktree list --porcelain)
  _commit_wt_candidate

  if [ -n "$WORKTREE_PATH" ] && [ "$WORKTREE_PATH" != "$REPO_ROOT" ]; then
    if [ "$WORKTREE_LOCKED" -eq 1 ]; then
      WORKTREE_LOCK_ALIVE=0
      if [ -n "$WORKTREE_LOCK_PID" ]; then
        kill -0 "$WORKTREE_LOCK_PID" 2>/dev/null && WORKTREE_LOCK_ALIVE=1
      else
        WORKTREE_LOCK_ALIVE=1
      fi

      if [ "$WORKTREE_LOCK_ALIVE" -eq 1 ]; then
        echo "    worktree:       skipped (locked by live pid ${WORKTREE_LOCK_PID:-unknown})"
        SKIPPED_LIVE_LOCK+=("$BRANCH")
        continue
      fi

      if git worktree unlock "$WORKTREE_PATH" 2>/dev/null; then
        echo "    worktree:       unlocked stale lock (pid ${WORKTREE_LOCK_PID} dead)"
      fi
    fi
    WORKTREE_REMOVE_OUTPUT=
    if WORKTREE_REMOVE_OUTPUT=$(git worktree remove "$WORKTREE_PATH" 2>&1); then
      echo "    worktree:       removed: ${WORKTREE_PATH}"
    else
      if [ "$WORKTREE_LOCKED" -eq 1 ]; then
        git worktree lock "$WORKTREE_PATH" 2>/dev/null || true
      fi
      echo "    worktree:       remove failed (manual step needed)"
      if [ -n "$WORKTREE_REMOVE_OUTPUT" ]; then
        printf '%s\n' "$WORKTREE_REMOVE_OUTPUT" | sed 's/^/                    /'
      fi
      continue
    fi
  else
    echo "    worktree:       not found"
  fi

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

for BRANCH in "${SKIPPED_LIVE_LOCK[@]+"${SKIPPED_LIVE_LOCK[@]}"}"; do
  echo "Skipped (live agent lock): ${BRANCH}"
done

if [ "${#SKIPPED_NEEDS_PROMPT[@]}" -gt 0 ]; then
  printf 'Skipped %d probable-merge branch(es) (no TTY for prompt; rerun with --yes or from a terminal): %s\n' \
    "${#SKIPPED_NEEDS_PROMPT[@]}" "${SKIPPED_NEEDS_PROMPT[*]}"
fi

# ---------------------------------------------------------------------------
# Skipped branches
# ---------------------------------------------------------------------------

for BRANCH in "${SKIPPED_BRANCHES[@]}"; do
  echo "Skipped: ${BRANCH} (currently checked out)"
done

for BRANCH in "${ALL_BRANCHES[@]}"; do
  [ "$BRANCH" = "$DEFAULT_BRANCH" ] && continue
  [ "$BRANCH" = "$CURRENT_HEAD" ] || continue
  PR_JSON=$(gh pr list \
    --head "$BRANCH" \
    --state merged \
    --limit 1 \
    --json number,mergedAt \
    2>/dev/null || true)
  PR_COUNT=$(echo "$PR_JSON" | python3 -c "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo 0)
  REACHABLE=0
  git merge-base --is-ancestor "$BRANCH" \
    "refs/remotes/origin/${DEFAULT_BRANCH}" 2>/dev/null && REACHABLE=1 || true
  if [ "$PR_COUNT" -gt 0 ] || [ "$REACHABLE" -eq 1 ]; then
    echo "Skipped: ${BRANCH} (currently checked out)"
  fi
done
