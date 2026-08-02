#!/bin/bash
# _worktree-lib.sh — shared helpers for worktree-cleanup scripts.
#
# Sourced by cleanup-merged-branches.sh and cleanup-idle-open-pr-worktrees.sh.
# Not executable on its own; source it, do not invoke it directly.
#
# Provides:
#   progress / clear_progress            — stderr-only progress line helpers
#   collect_process_cwds / worktree_in_use — live-process detection
#   resolve_worktree_for_branch          — branch -> worktree path/lock lookup
#
# Every function here is pure / side-effect-free with respect to the caller's
# script state (aside from the documented globals each one populates), so a
# behavior regression traced back to this file can be fixed by editing this
# file alone — neither consumer script needs a parallel change.

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
# Live-worktree detection
#
# A worktree must not be removed while a live process is working inside it:
# that process would be left with a deleted working directory. Detection is
# OS-level (process working directories), not tied to any project's tooling.
# ---------------------------------------------------------------------------

# Snapshot of every readable process working directory. Populated once by
# collect_process_cwds; PROCESS_CWD_SCAN records whether the scan succeeded.
declare -a PROCESS_CWDS=()
PROCESS_CWD_SCAN="unknown"

collect_process_cwds() {
  PROCESS_CWDS=()
  PROCESS_CWD_SCAN="unavailable"
  local proc_cwd pid cwd line
  if readlink /proc/self/cwd >/dev/null 2>&1; then
    # Linux: each /proc/<pid>/cwd is a symlink to that process's cwd.
    for proc_cwd in /proc/[0-9]*/cwd; do
      pid="${proc_cwd#/proc/}"; pid="${pid%/cwd}"
      [ "$pid" = "$$" ] && continue
      cwd=$(readlink "$proc_cwd" 2>/dev/null) || continue
      if [ -n "$cwd" ]; then
        PROCESS_CWDS+=("$cwd")
      fi
    done
  elif command -v lsof >/dev/null 2>&1; then
    # No procfs (e.g. macOS): -F pn emits a `p<pid>` line then an `n<path>` line per open-cwd-fd process.
    local lsof_tmp lsof_pid self_cwd
    self_cwd=$(pwd -P)
    lsof_tmp=$(mktemp -t worktree-lib-lsof.XXXXXX) || return 0  # GNU mktemp requires the XXXXXX suffix; a bare prefix is BSD-only.
    trap 'rm -f "$lsof_tmp"' EXIT
    lsof -d cwd -F pn >"$lsof_tmp" 2>/dev/null &
    lsof_pid=$!  # backgrounded via a temp file rather than process substitution, so $! reliably captures the PID.
    wait "$lsof_pid" 2>/dev/null || true  # `|| true`: don't let a shimmed/failing lsof abort the caller under set -e.
    pid=""
    while IFS= read -r line; do
      case "$line" in
        p*) pid="${line#p}" ;;
        n*)
          [ "$pid" = "$$" ] && continue
          [ "$pid" = "$lsof_pid" ] && continue
          cwd="${line#n}"
          # lsof's own forked helper briefly shares its cwd and exits before `wait` returns; kill -0, scoped to self_cwd matches only, drops it without risking another user's live process.
          if [ "$cwd" = "$self_cwd" ] && ! kill -0 "$pid" 2>/dev/null; then
            continue
          fi
          if [ -n "$cwd" ]; then
            PROCESS_CWDS+=("$cwd")
          fi
          ;;
      esac
    done < "$lsof_tmp"
    rm -f "$lsof_tmp"
    trap - EXIT  # explicit reset: this repo composes a single EXIT trap per script, not one per library function.
  fi
  # Zero cwds means the scan failed (self would always appear), not that nothing's running — stays "unavailable" so worktree_in_use reports "could not determine," not false-idle.
  if [ "${#PROCESS_CWDS[@]}" -gt 0 ]; then
    PROCESS_CWD_SCAN="ok"
  fi
  return 0
}

# worktree_in_use <path> — is any live process working inside <path>?
#   0 = in use   1 = idle   2 = could not determine
# Matches against the collect_process_cwds snapshot, so the OS is scanned
# once per run rather than once per branch.
worktree_in_use() {
  [ "$PROCESS_CWD_SCAN" = "unavailable" ] && return 2
  local target="$1" resolved cwd
  # Canonicalize so symlinked path components match the kernel-canonical
  # cwd strings reported by /proc and lsof.
  resolved=$(cd "$target" 2>/dev/null && pwd -P) || resolved="$target"
  for cwd in "${PROCESS_CWDS[@]+"${PROCESS_CWDS[@]}"}"; do
    if [ "$cwd" = "$resolved" ] || [[ "$cwd" == "$resolved"/* ]]; then
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# Branch -> worktree path/lock lookup
# ---------------------------------------------------------------------------

# _commit_worktree_candidate — internal to resolve_worktree_for_branch.
# Finalizes the just-scanned porcelain record into WORKTREE_PATH /
# WORKTREE_LOCKED / WORKTREE_LOCK_PID if it matched the target branch.
# Defined at file scope (not nested) since bash functions are not truly
# block-scoped; resolve_worktree_for_branch's _WT_CANDIDATE_* variables are
# script-global by the same convention the pre-extraction script used.
_commit_worktree_candidate() {
  if [ "$_WT_CANDIDATE_MATCHED" -eq 1 ]; then
    WORKTREE_PATH="$_WT_CANDIDATE_PATH"
    WORKTREE_LOCKED="$_WT_CANDIDATE_LOCKED"
    WORKTREE_LOCK_PID="$_WT_CANDIDATE_LOCK_PID"
  fi
}

# resolve_worktree_for_branch <branch>
#
# Populates WORKTREE_PATH, WORKTREE_LOCKED, and WORKTREE_LOCK_PID for the
# worktree checked out to <branch>, or leaves WORKTREE_PATH empty if no
# worktree exists for it. Uses --porcelain to get the exact path — never
# construct a path from the branch name, since slashes in branch names
# would break path interpolation.
#
# The `locked` line appears after `branch` in a porcelain record, so this
# uses a deferred-commit pattern: finalize path + lock state at each record
# boundary (the next `worktree` line, or end of input) rather than at the
# moment the `branch` line is matched.
resolve_worktree_for_branch() {
  local branch="$1" line
  # These three are this function's return value, read by the sourcing
  # script rather than within this file — shellcheck can't see that
  # cross-file usage, hence the disables.
  # shellcheck disable=SC2034
  WORKTREE_PATH=""
  # shellcheck disable=SC2034
  WORKTREE_LOCKED=0
  # shellcheck disable=SC2034
  WORKTREE_LOCK_PID=""
  _WT_CANDIDATE_PATH=""
  _WT_CANDIDATE_LOCKED=0
  _WT_CANDIDATE_LOCK_PID=""
  _WT_CANDIDATE_MATCHED=0
  while IFS= read -r line; do
    if [[ "$line" == "worktree "* ]]; then
      _commit_worktree_candidate
      _WT_CANDIDATE_PATH="${line#worktree }"
      _WT_CANDIDATE_LOCKED=0
      _WT_CANDIDATE_LOCK_PID=""
      _WT_CANDIDATE_MATCHED=0
    elif [[ "$line" == "branch refs/heads/${branch}" ]]; then
      _WT_CANDIDATE_MATCHED=1
    elif [[ "$line" == "locked"* ]]; then
      _WT_CANDIDATE_LOCKED=1
      if [[ "$line" =~ pid[[:space:]]+([0-9]+) ]]; then
        _WT_CANDIDATE_LOCK_PID="${BASH_REMATCH[1]}"
      fi
    fi
  done < <(git worktree list --porcelain)
  _commit_worktree_candidate
}
