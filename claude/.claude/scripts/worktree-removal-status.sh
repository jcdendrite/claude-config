#!/usr/bin/env bash
# worktree-removal-status.sh — reports, per linked worktree, whether
# `git worktree remove` (no --force) would succeed, and why when it wouldn't.
#
# Read-only: every working-tree check uses `git --no-optional-locks status
# --porcelain`, so a concurrent session in the same worktree is never
# blocked or has its index touched.
#
# Per `git worktree remove --help`, remove refuses a dirty, locked, or
# submodule-containing worktree without --force. A live process working
# inside a worktree downgrades the verdict to "do not remove" regardless of
# --force, since --force does not itself check for one. See
# docs/scripts.md's entry for this script for the full behavior writeup.
#
# Usage:
#   worktree-removal-status.sh                        # every linked worktree
#   worktree-removal-status.sh <branch-or-path> ...   # only matching worktrees
#
# Exit codes:
#   0  scan completed, including the "no linked worktrees" and
#      "no worktrees matched" cases
#   1  not inside a git repository, or the worktree scan itself failed
#
# This 0/1 contract assumes a non-bare main worktree; a bare-repository-as-
# main-worktree layout can make the internal `git rev-parse --show-toplevel`
# call exit with git's own code instead.

set -euo pipefail

SCRIPT_NAME="worktree-removal-status.sh"

# shellcheck source=_worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_worktree-lib.sh"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "${SCRIPT_NAME}: not inside a git repository" >&2
  exit 1
fi

collect_all_worktrees
if [ "${#ALL_WT_PATHS[@]}" -eq 0 ]; then
  echo "${SCRIPT_NAME}: 'git worktree list' returned no worktrees at all -- unexpected for a valid repo" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Canonicalize, main-worktree/current-worktree identification
#
# _canon falls back to the raw path when it can't be cd'd into (e.g. a
# prunable worktree whose directory is gone), so a comparison against it
# simply never matches rather than erroring.
# ---------------------------------------------------------------------------

_canon() {
  local p="$1"
  (cd "$p" 2>/dev/null && pwd -P) || printf '%s' "$p"
}

MAIN_WORKTREE_PATH=$(_canon "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")
CURRENT_WORKTREE_PATH=$(_canon "$(git rev-parse --show-toplevel)")

# Canonicalize every reported worktree path once, up front, so every
# identity comparison below (main-worktree exclusion, filter matching,
# current-worktree tagging) compares canonical forms on both sides rather
# than mixing raw porcelain paths with canonicalized ones.
declare -a ALL_WT_CANON_PATHS=()
for _i in "${!ALL_WT_PATHS[@]}"; do
  ALL_WT_CANON_PATHS+=("$(_canon "${ALL_WT_PATHS[$_i]}")")
done

# ---------------------------------------------------------------------------
# Optional filter (positional args) -- each argument matches a worktree by
# exact branch name or exact path, narrowing the report instead of covering
# every linked worktree. A path argument is canonicalized before comparison
# so a relative path or a symlinked component still matches the canonical
# form git reports. A branch-name argument is compared raw, since _canon's
# cd-into-it fallback only ever resolves an actual path.
# ---------------------------------------------------------------------------

FILTER_ARGS=()
[ "$#" -gt 0 ] && FILTER_ARGS=("$@")

_matches_filter() {
  local branch="$1" canon_path="$2" _f
  [ "${#FILTER_ARGS[@]}" -eq 0 ] && return 0
  for _f in "${FILTER_ARGS[@]}"; do
    if [ "$_f" = "$branch" ] || [ "$(_canon "$_f")" = "$canon_path" ]; then
      return 0
    fi
  done
  return 1
}

# Build the linked-worktree index list (main excluded) that survives the
# filter, and separately track which filter args matched nothing.
declare -a REPORT_INDICES=()
for _i in "${!ALL_WT_PATHS[@]}"; do
  [ "${ALL_WT_CANON_PATHS[$_i]}" = "$MAIN_WORKTREE_PATH" ] && continue
  if _matches_filter "${ALL_WT_BRANCHES[$_i]}" "${ALL_WT_CANON_PATHS[$_i]}"; then
    REPORT_INDICES+=("$_i")
  fi
done

declare -a UNMATCHED_FILTER_ARGS=()
if [ "${#FILTER_ARGS[@]}" -gt 0 ]; then
  for _f in "${FILTER_ARGS[@]}"; do
    _canon_f=$(_canon "$_f")
    _found=0
    for _i in "${!ALL_WT_PATHS[@]}"; do
      [ "${ALL_WT_CANON_PATHS[$_i]}" = "$MAIN_WORKTREE_PATH" ] && continue
      if [ "$_f" = "${ALL_WT_BRANCHES[$_i]}" ] || [ "$_canon_f" = "${ALL_WT_CANON_PATHS[$_i]}" ]; then
        _found=1
        break
      fi
    done
    [ "$_found" -eq 0 ] && UNMATCHED_FILTER_ARGS+=("$_f")
  done
fi

LINKED_WORKTREE_COUNT=$(( ${#ALL_WT_PATHS[@]} - 1 ))
if [ "$LINKED_WORKTREE_COUNT" -eq 0 ]; then
  echo "No linked worktrees found."
  exit 0
fi

# Snapshot live process working directories once; worktree_in_use queries it.
collect_process_cwds

# ---------------------------------------------------------------------------
# worktree_has_submodules <path> -- 0 = has submodules, 1 = no submodules,
# 2 = could not determine. `git submodule status` prints one line per
# initialized submodule and nothing when there are none, so a missing
# .gitmodules and an empty-but-clean submodule config both report "no".
# ---------------------------------------------------------------------------

worktree_has_submodules() {
  local path="$1" output
  [ -d "$path" ] || return 2
  output=$(git -C "$path" submodule status 2>/dev/null) || return 2
  [ -n "$output" ] && return 0
  return 1
}

# ---------------------------------------------------------------------------
# report_worktree <index> -- prints one "Label: value" block for the
# worktree at ALL_WT_* index <index>, and sets IS_REMOVABLE / IS_DIRTY /
# IS_LOCKED / IS_PRUNABLE / IS_IN_USE / IS_UNVERIFIABLE (each 0 or 1) for
# the caller's tally.
# ---------------------------------------------------------------------------

report_worktree() {
  local idx="$1"
  local path="${ALL_WT_PATHS[$idx]}" branch="${ALL_WT_BRANCHES[$idx]}"
  local locked="${ALL_WT_LOCKED[$idx]}" lock_reason="${ALL_WT_LOCK_REASONS[$idx]}"
  local prunable_reason="${ALL_WT_PRUNABLE_REASONS[$idx]}"
  local branch_label="$branch"
  [ -z "$branch_label" ] && branch_label="(detached)"
  local current_tag=""
  [ "${ALL_WT_CANON_PATHS[$idx]}" = "$CURRENT_WORKTREE_PATH" ] && current_tag=" (current)"

  IS_REMOVABLE=0
  IS_DIRTY=0
  IS_LOCKED=0
  IS_PRUNABLE=0
  IS_IN_USE=0
  IS_UNVERIFIABLE=0

  echo "Worktree: ${path}${current_tag}"
  echo "Branch: ${branch_label}"

  if [ -n "$prunable_reason" ]; then
    IS_PRUNABLE=1
    echo "Prunable: yes (${prunable_reason})"
    echo "Verdict: prunable -- run 'git worktree prune', not --force."
    echo
    return 0
  fi
  echo "Prunable: no"

  local in_use_rc=0
  worktree_in_use "$path" || in_use_rc=$?
  case "$in_use_rc" in
    0) echo "In use: yes (live process)"; IS_IN_USE=1 ;;
    1) echo "In use: no" ;;
    *) echo "In use: could not be determined"; IS_UNVERIFIABLE=1 ;;
  esac

  if [ "$locked" -eq 1 ]; then
    IS_LOCKED=1
    if [ -n "$lock_reason" ]; then
      echo "Locked: yes (${lock_reason})"
    else
      echo "Locked: yes"
    fi
  else
    echo "Locked: no"
  fi

  local status_output status_failed=0
  if ! status_output=$(git --no-optional-locks -C "$path" status --porcelain 2>&1); then
    status_failed=1
  fi
  if [ "$status_failed" -eq 1 ]; then
    echo "Status: could not be determined (${status_output})"
    IS_UNVERIFIABLE=1
  elif [ -z "$status_output" ]; then
    echo "Status: clean"
  else
    echo "Status: dirty"
    printf '%s\n' "$status_output" | sed 's/^/  /'
    IS_DIRTY=1
  fi

  local submodules_rc=0
  worktree_has_submodules "$path" || submodules_rc=$?
  local has_submodules=0
  case "$submodules_rc" in
    0) echo "Submodules: yes"; has_submodules=1 ;;
    1) echo "Submodules: no" ;;
    *) echo "Submodules: could not be determined"; IS_UNVERIFIABLE=1 ;;
  esac

  local -a reasons=()
  [ "$IS_DIRTY" -eq 1 ] && reasons+=("dirty working tree")
  [ "$IS_LOCKED" -eq 1 ] && reasons+=("locked")
  [ "$has_submodules" -eq 1 ] && reasons+=("has submodules")
  local reason_text="" _reason
  for _reason in "${reasons[@]+"${reasons[@]}"}"; do
    if [ -z "$reason_text" ]; then
      reason_text="$_reason"
    else
      reason_text="${reason_text}, ${_reason}"
    fi
  done

  if [ "$IS_IN_USE" -eq 1 ]; then
    echo "Verdict: do not remove -- a live process is working in this worktree (--force does not check for this)."
  elif [ "$IS_UNVERIFIABLE" -eq 1 ]; then
    echo "Verdict: could not be determined; inspect this worktree manually."
  elif [ -z "$reason_text" ]; then
    echo "Verdict: git worktree remove would succeed (no --force needed)."
    IS_REMOVABLE=1
  else
    # Per git-worktree(1), remove refuses an unclean worktree unless --force
    # is used once. A locked worktree needs --force specified twice.
    local force_clause="--force"
    [ "$IS_LOCKED" -eq 1 ] && force_clause="--force specified twice"
    echo "Verdict: git worktree remove would fail without ${force_clause} (${reason_text})."
  fi
  echo
  return 0
}

# ---------------------------------------------------------------------------
# Report every filtered worktree, tallying as we go
# ---------------------------------------------------------------------------

REMOVABLE_COUNT=0
DIRTY_COUNT=0
LOCKED_COUNT=0
PRUNABLE_COUNT=0
IN_USE_COUNT=0
UNVERIFIABLE_COUNT=0

for _i in "${REPORT_INDICES[@]+"${REPORT_INDICES[@]}"}"; do
  report_worktree "$_i"
  [ "$IS_REMOVABLE" -eq 1 ] && REMOVABLE_COUNT=$(( REMOVABLE_COUNT + 1 ))
  [ "$IS_DIRTY" -eq 1 ] && DIRTY_COUNT=$(( DIRTY_COUNT + 1 ))
  [ "$IS_LOCKED" -eq 1 ] && LOCKED_COUNT=$(( LOCKED_COUNT + 1 ))
  [ "$IS_PRUNABLE" -eq 1 ] && PRUNABLE_COUNT=$(( PRUNABLE_COUNT + 1 ))
  [ "$IS_IN_USE" -eq 1 ] && IN_USE_COUNT=$(( IN_USE_COUNT + 1 ))
  [ "$IS_UNVERIFIABLE" -eq 1 ] && UNVERIFIABLE_COUNT=$(( UNVERIFIABLE_COUNT + 1 ))
done

if [ "${#UNMATCHED_FILTER_ARGS[@]}" -gt 0 ]; then
  echo "No worktree found for: ${UNMATCHED_FILTER_ARGS[*]}"
fi

printf '%d worktrees: %d removable, %d dirty, %d locked, %d prunable, %d in-use, %d unverifiable\n' \
  "${#REPORT_INDICES[@]}" "$REMOVABLE_COUNT" "$DIRTY_COUNT" "$LOCKED_COUNT" \
  "$PRUNABLE_COUNT" "$IN_USE_COUNT" "$UNVERIFIABLE_COUNT"
