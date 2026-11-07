#!/usr/bin/env bash
# _worktree-lib.sh — shared helpers for worktree scripts.
#
# Sourced by cleanup-merged-branches.sh, cleanup-idle-open-pr-worktrees.sh,
# worktree-removal-status.sh, worktree-ensure.sh, and
# resume-context-same-session.sh.
# Not executable on its own; source it, do not invoke it directly.
#
# Provides:
#   progress / clear_progress            — stderr-only progress line helpers
#   collect_process_cwds / worktree_in_use — live-process detection
#   resolve_worktree_for_branch          — branch -> worktree path/lock lookup
#   worktree_lock_is_live                — is a lock's recorded pid alive, or unknown
#   main_worktree_path                   — the main worktree's path
#   worktree_protected_role              — whether a worktree is the main or the runner's own, never removed
#   worktree_is_clean                    — the cleanliness check `git worktree remove` runs
#   worktree_has_populated_submodules    — the submodule refusal `git worktree remove` raises
#   worktree_removal_verdict             — what a real cleanup run does with a branch's worktree
#   collect_all_worktrees                — full `worktree list --porcelain` scan
#   worktree_canon_path                  — symlink-resolved path, raw on failure
#   worktree_matches_filter              — branch/path match against FILTER_ARGS
#   worktree_ensure_name_check           — branch-name verdict for worktree-ensure.sh
#   worktree_ensure_classify             — which state the branch's worktree is in
#   worktree_ensure_ref_check            — existing local branches that block a create
#   worktree_ensure_target_check         — path conditions that block a create
#   worktree_ensure_describe_record      — one-line description of an ALL_WT_* record
#
# FILTER_ARGS is a caller-assigned array (may be empty) that worktree_matches_filter reads.
# Assign it before calling -- an unassigned array aborts under `set -u`.
#
# Every function here is side-effect-free with respect to the caller's script
# state, aside from the documented globals each one reads or populates.
# collect_process_cwds is the one exception: its lsof branch installs an EXIT trap.
# That trap replaces any EXIT trap the caller already set, and the reset does not restore it.
# A behavior regression traced back to this file can therefore be fixed by
# editing this file alone — neither consumer script needs a parallel change.

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
# Path canonicalization and filter matching
# ---------------------------------------------------------------------------

# worktree_canon_path <path> — prints <path> with symlinks resolved.
# Falls back to the raw input when <path> can't be cd'd into (e.g. a prunable
# worktree whose directory is gone, or a branch name).
# A comparison against a raw fallback therefore never matches rather than erroring.
# An inherited CDPATH is cleared so it cannot resolve a relative name (and
# print the hit) against an unrelated directory.
# An empty input or a lone `-` (cd's OLDPWD shorthand) falls back to raw.
# `--` stops an input like `-P` from being read as a cd option instead of a path.
worktree_canon_path() {
  local p="$1"
  case "$p" in
    '' | -) printf '%s' "$p"; return 0 ;;
  esac
  (CDPATH='' cd -- "$p" 2>/dev/null && pwd -P) || printf '%s' "$p"
}

# worktree_matches_filter <branch> <canonical-path> — does the worktree with
# this branch and canonical path survive the FILTER_ARGS filter?
#   0 = matches (always, when FILTER_ARGS is empty)   1 = no match
# Each filter argument matches by exact branch name or by canonicalized path,
# so a relative path or a symlinked component still matches the canonical
# form git reports. A branch-name argument is compared raw, since
# worktree_canon_path only ever resolves an actual path.
worktree_matches_filter() {
  local branch="$1" canon_path="$2" _f
  [ "${#FILTER_ARGS[@]}" -eq 0 ] && return 0
  for _f in "${FILTER_ARGS[@]}"; do
    if [ "$_f" = "$branch" ] || [ "$(worktree_canon_path "$_f")" = "$canon_path" ]; then
      return 0
    fi
  done
  return 1
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
# Returns 2 unless that snapshot succeeded, so a caller that never ran
# collect_process_cwds cannot read a busy worktree as idle.
worktree_in_use() {
  [ "$PROCESS_CWD_SCAN" = "ok" ] || return 2
  local target="$1" resolved cwd
  # Canonicalize so symlinked path components match the kernel-canonical
  # cwd strings reported by /proc and lsof.
  resolved=$(worktree_canon_path "$target")
  for cwd in "${PROCESS_CWDS[@]}"; do
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

# worktree_lock_is_live <lock-pid>
#
# Returns 0 when the lock holder must be treated as alive, 1 when this user's
# `kill -0` cannot signal the pid: it is dead, or it belongs to another user.
# An empty pid (a lock with no `pid N` in its reason) returns 0, so a lock of
# unknown ownership fails closed.
worktree_lock_is_live() {
  local lock_pid="${1:-}"
  [ -z "$lock_pid" ] && return 0
  kill -0 "$lock_pid" 2>/dev/null
}

# main_worktree_path
#
# Prints the main worktree's path: the first record of `git worktree list
# --porcelain`, the same whichever worktree the caller runs from. Returns 1
# when the listing fails or its first line is not a `worktree` record.
main_worktree_path() {
  local listing first_line
  listing=$(git worktree list --porcelain 2>/dev/null) || return 1
  first_line="${listing%%$'\n'*}"
  [[ "$first_line" == "worktree "* ]] || return 1
  printf '%s\n' "${first_line#worktree }"
}

# worktree_protected_role <worktree-path> <main-worktree-path> <current-worktree-path>
#
# Prints `main` when <worktree-path> is the main worktree, else
# `current-worktree` when it is the worktree the caller runs in, else nothing.
# A removal run never removes either one.
# The current worktree is compared by canonical path, since `--show-toplevel`
# and git's porcelain can spell one path differently.
# The main-worktree path is compared raw, and `main` outranks `current-worktree`.
# The caller guards against an empty <worktree-path>, which matches an empty main or current path and prints a protected role.
worktree_protected_role() {
  local worktree_path="$1" main_worktree="$2" current_worktree="$3"
  if [ "$worktree_path" = "$main_worktree" ]; then
    printf 'main'
  elif [ "$(worktree_canon_path "$worktree_path")" = "$(worktree_canon_path "$current_worktree")" ]; then
    printf 'current-worktree'
  fi
}

# worktree_is_clean <path>
#
# Runs the cleanliness check `git worktree remove` runs before it removes a
# worktree. Returns 0 when clean, 1 when dirty, 2 when the check cannot run: the
# worktree has no `.git`, or `git status` fails. A path whose directory is gone
# returns 0, because `git worktree remove` removes such a worktree.
# Sets WORKTREE_STATUS_PORCELAIN to the status lines (empty unless dirty) and
# WORKTREE_PROBE_REASON to a one-line reason on a 2, empty otherwise.
# The `.git` check comes first because `git -C <path> status` in a directory
# without one walks up to the enclosing repo and reports that repo's state.
# A `.git` file whose back-link names another worktree's admin dir passes this
# check, though `git worktree remove` refuses that worktree as invalid.
# shellcheck disable=SC2034  # both globals are read by the sourcing script
worktree_is_clean() {
  local path="$1"
  WORKTREE_STATUS_PORCELAIN=""
  WORKTREE_PROBE_REASON=""
  [ -d "$path" ] || return 0
  if [ ! -e "$path/.git" ]; then
    WORKTREE_PROBE_REASON="$path has no .git"
    return 2
  fi
  if ! WORKTREE_STATUS_PORCELAIN=$(git --no-optional-locks -C "$path" status --porcelain --ignore-submodules=none 2>/dev/null); then
    WORKTREE_STATUS_PORCELAIN=""
    WORKTREE_PROBE_REASON="'git status' failed in $path"
    return 2
  fi
  [ -z "$WORKTREE_STATUS_PORCELAIN" ]
}

# worktree_has_populated_submodules <path>
#
# Returns 0 when <path> has a populated submodule or its own git dir holds a
# `modules` directory, 1 when it has neither, and 2
# when the check cannot run: the worktree has no `.git`, or `git rev-parse` or
# `git submodule status` fails. A path whose directory is gone returns 1.
# Sets WORKTREE_PROBE_REASON to a one-line reason on a 2, empty otherwise.
# `git worktree remove` refuses a worktree with a populated submodule, and one
# whose own git dir still holds a `modules` directory even if every submodule
# is now uninitialized. It removes a worktree whose submodules are only
# uninitialized, which `git submodule status` marks with a leading `-`.
# A gitlink inactive in config whose directory has its own `.git` reads as
# uninitialized here but counts as populated to git, so it returns 1 and the
# real remove still refuses it.
# The `modules` directory is read from the worktree's own git dir, never the
# main checkout's, which would make every worktree look populated whenever the
# main checkout has an initialized submodule.
# shellcheck disable=SC2034  # WORKTREE_PROBE_REASON is read by the sourcing script
worktree_has_populated_submodules() {
  local path="$1" submodule_status submodule_line worktree_git_dir
  WORKTREE_PROBE_REASON=""
  [ -d "$path" ] || return 1
  if [ ! -e "$path/.git" ]; then
    WORKTREE_PROBE_REASON="$path has no .git"
    return 2
  fi
  if ! worktree_git_dir=$(git -C "$path" rev-parse --absolute-git-dir 2>/dev/null); then
    WORKTREE_PROBE_REASON="'git rev-parse' failed in $path"
    return 2
  fi
  [ -d "$worktree_git_dir/modules" ] && return 0
  if ! submodule_status=$(git -C "$path" submodule status 2>/dev/null); then
    WORKTREE_PROBE_REASON="'git submodule status' failed in $path"
    return 2
  fi
  while IFS= read -r submodule_line; do
    case "$submodule_line" in
      '' | -*) ;;
      *) return 0 ;;
    esac
  done <<< "$submodule_status"
  return 1
}

# worktree_removal_verdict <branch> <main-worktree-path> <current-worktree-path>
#
# Resolves <branch>'s worktree with resolve_worktree_for_branch and sets
# WORKTREE_REMOVAL_VERDICT to what a real cleanup run does with it.
# WORKTREE_PATH, WORKTREE_LOCKED and WORKTREE_LOCK_PID stay populated for the
# caller. Always returns 0.
# The verdict is a global because a `$(...)` capture would discard those three.
# Run collect_process_cwds first, or the live-process check reads as idle-unverifiable.
# <current-worktree-path> is the checkout the caller runs in. First match wins:
#   none                      no worktree is checked out to the branch
#   main                      it is the main worktree, which is never removed
#   current-worktree          it is the worktree the caller runs in, never removed
#   in-use                    a live process is working inside it
#   idle-unverifiable         the live-process scan could not run
#   live-lock                 locked by a pid that is alive
#   pidless-lock              locked with no pid recorded, treated as live
#   submodules                `git worktree remove` refuses populated submodules
#   dirty                     `git worktree remove` refuses uncommitted changes
#   status-unverifiable       the cleanliness check could not run
#   submodules-unverifiable   the submodule check could not run
#   unlock-remove             locked by a dead pid, unlocked then removed
#   remove                    removed
# The last six are the tokens for which a real run attempts `git worktree
# remove`. A refusal git raises for a reason these probes do not model still
# fails that attempt. The probes run only after the lock checks, populated
# submodules first, the order `git worktree remove` checks them in.
# shellcheck disable=SC2034  # WORKTREE_REMOVAL_VERDICT is read by the sourcing script
worktree_removal_verdict() {
  local branch="$1" main_worktree="$2" current_worktree="$3" in_use_rc=0 clean_rc=0 submodules_rc=0 protected_role
  resolve_worktree_for_branch "$branch"
  WORKTREE_REMOVAL_VERDICT="none"
  [ -n "$WORKTREE_PATH" ] || return 0
  protected_role=$(worktree_protected_role "$WORKTREE_PATH" "$main_worktree" "$current_worktree")
  if [ -n "$protected_role" ]; then
    WORKTREE_REMOVAL_VERDICT="$protected_role"
    return 0
  fi
  worktree_in_use "$WORKTREE_PATH" || in_use_rc=$?
  if [ "$in_use_rc" -eq 0 ]; then
    WORKTREE_REMOVAL_VERDICT="in-use"
    return 0
  elif [ "$in_use_rc" -eq 2 ]; then
    WORKTREE_REMOVAL_VERDICT="idle-unverifiable"
    return 0
  fi
  if [ "$WORKTREE_LOCKED" -eq 1 ] && worktree_lock_is_live "$WORKTREE_LOCK_PID"; then
    if [ -n "$WORKTREE_LOCK_PID" ]; then
      WORKTREE_REMOVAL_VERDICT="live-lock"
    else
      WORKTREE_REMOVAL_VERDICT="pidless-lock"
    fi
    return 0
  fi
  worktree_has_populated_submodules "$WORKTREE_PATH" || submodules_rc=$?
  if [ "$submodules_rc" -eq 0 ]; then
    WORKTREE_REMOVAL_VERDICT="submodules"
    return 0
  fi
  worktree_is_clean "$WORKTREE_PATH" || clean_rc=$?
  if [ "$clean_rc" -eq 1 ]; then
    WORKTREE_REMOVAL_VERDICT="dirty"
  elif [ "$clean_rc" -eq 2 ]; then
    WORKTREE_REMOVAL_VERDICT="status-unverifiable"
  elif [ "$submodules_rc" -eq 2 ]; then
    WORKTREE_REMOVAL_VERDICT="submodules-unverifiable"
  elif [ "$WORKTREE_LOCKED" -eq 1 ]; then
    WORKTREE_REMOVAL_VERDICT="unlock-remove"
  else
    WORKTREE_REMOVAL_VERDICT="remove"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Full worktree-list scan (every record, main worktree included)
# ---------------------------------------------------------------------------

# _commit_all_worktree_record — internal to collect_all_worktrees. Finalizes
# the just-scanned porcelain record into the ALL_WT_* arrays.
_commit_all_worktree_record() {
  if [ "$_AWT_HAVE_RECORD" -eq 1 ]; then
    ALL_WT_PATHS+=("$_AWT_PATH")
    ALL_WT_BRANCHES+=("$_AWT_BRANCH")
    ALL_WT_LOCKED+=("$_AWT_LOCKED")
    ALL_WT_LOCK_PIDS+=("$_AWT_LOCK_PID")
    ALL_WT_LOCK_REASONS+=("$_AWT_LOCK_REASON")
    ALL_WT_PRUNABLE_REASONS+=("$_AWT_PRUNABLE_REASON")
  fi
}

# collect_all_worktrees
#
# One forward scan of `git worktree list --porcelain`, populating six
# parallel arrays — ALL_WT_PATHS, ALL_WT_BRANCHES, ALL_WT_LOCKED,
# ALL_WT_LOCK_PIDS, ALL_WT_LOCK_REASONS, ALL_WT_PRUNABLE_REASONS — one entry
# per worktree record, main worktree included. Unlike
# resolve_worktree_for_branch, this does not key off one target branch, so
# every record is committed rather than only a matching one. ALL_WT_LOCK_REASONS
# holds the porcelain `locked <reason>` text verbatim, empty when the record
# is `locked` with no reason. Git may quote it per `core.quotePath`; this
# does not attempt to unescape it. ALL_WT_PRUNABLE_REASONS holds the
# porcelain `prunable <reason>` text, empty when the record carries no
# `prunable` line, for a worktree whose directory is gone. ALL_WT_LOCK_PIDS
# mirrors resolve_worktree_for_branch's WORKTREE_LOCK_PID field for a
# forward-looking consumer that needs process-liveness checks on a lock
# holder, even though worktree-removal-status.sh itself doesn't currently
# read it, only surfacing the full lock-reason text.
#
# resolve_worktree_for_branch and collect_all_worktrees are two independent
# parsers of the same porcelain grammar, an accepted duplication rather than
# an oversight — consider unifying them if a third consumer needs this
# grammar, rather than adding a third copy.
collect_all_worktrees() {
  # shellcheck disable=SC2034
  ALL_WT_PATHS=()
  # shellcheck disable=SC2034
  ALL_WT_BRANCHES=()
  # shellcheck disable=SC2034
  ALL_WT_LOCKED=()
  # shellcheck disable=SC2034
  ALL_WT_LOCK_PIDS=()
  # shellcheck disable=SC2034
  ALL_WT_LOCK_REASONS=()
  # shellcheck disable=SC2034
  ALL_WT_PRUNABLE_REASONS=()
  _AWT_PATH="" _AWT_BRANCH="" _AWT_LOCKED=0 _AWT_LOCK_PID="" _AWT_LOCK_REASON="" _AWT_PRUNABLE_REASON=""
  _AWT_HAVE_RECORD=0
  local line
  while IFS= read -r line; do
    if [[ "$line" == "worktree "* ]]; then
      _commit_all_worktree_record
      _AWT_PATH="${line#worktree }"
      _AWT_BRANCH=""
      _AWT_LOCKED=0
      _AWT_LOCK_PID=""
      _AWT_LOCK_REASON=""
      _AWT_PRUNABLE_REASON=""
      _AWT_HAVE_RECORD=1
    elif [[ "$line" == "branch refs/heads/"* ]]; then
      _AWT_BRANCH="${line#branch refs/heads/}"
    elif [[ "$line" == "locked"* ]]; then
      _AWT_LOCKED=1
      _AWT_LOCK_REASON="${line#locked}"
      _AWT_LOCK_REASON="${_AWT_LOCK_REASON# }"
      if [[ "$line" =~ pid[[:space:]]+([0-9]+) ]]; then
        _AWT_LOCK_PID="${BASH_REMATCH[1]}"
      fi
    elif [[ "$line" == "prunable"* ]]; then
      _AWT_PRUNABLE_REASON="${line#prunable}"
      _AWT_PRUNABLE_REASON="${_AWT_PRUNABLE_REASON# }"
    fi
  done < <(git worktree list --porcelain)
  _commit_all_worktree_record
}

# ---------------------------------------------------------------------------
# worktree-ensure.sh decision functions
#
# Pure bash over the ALL_WT_* arrays, stdin text and the filesystem, so the
# input matrix is testable without running the script's git writes.
# ---------------------------------------------------------------------------

# worktree_ensure_name_check <name> — prints one verdict, always returning 0:
#   ok                 the name is in the ASCII set and git accepts it
#   bad-name           the name is outside the set, or git rejects it (status 128)
#   git-failed <n>     git could not run; <n> is its exit status
# The bracket expression lists every character instead of using ranges, so a
# locale cannot widen it. It excludes `@{`, so `@{-N}` never reaches git. The
# pattern sits in a variable because bash matches a quoted right-hand side of
# =~ as a literal string.
worktree_ensure_name_check() {
  local name="$1" name_pattern git_status=0
  name_pattern='^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-]+$'
  if ! [[ "$name" =~ $name_pattern ]]; then
    printf 'bad-name'
    return 0
  fi
  git check-ref-format --branch "$name" >/dev/null 2>&1 || git_status=$?
  case "$git_status" in
    0) printf 'ok' ;;
    128) printf 'bad-name' ;;
    *) printf 'git-failed %s' "$git_status" ;;
  esac
}

# worktree_ensure_classify <branch> <canon-main> <canon-cwd> — reads
# ALL_WT_PATHS, ALL_WT_BRANCHES and ALL_WT_LOCK_REASONS (main checkout first).
# Prints `absent`, or `<state> <record index>` for the record holding <branch>:
#   in-main          the main checkout holds it
#   add-unfinished   its lock reason is exactly `initializing` (a git add in flight or killed)
#   missing          its path is not a directory holding a `.git` that resolves
#   outside          its canonical path is not under <canon-main>/.claude/worktrees/
#   current          its canonical path is <canon-cwd>
#   existing         none of the above
# Checks run in that order, so `current` never names a path EnterWorktree refuses.
worktree_ensure_classify() {
  local branch="$1" canon_main="$2" canon_cwd="$3"
  local worktrees_prefix="${canon_main}/.claude/worktrees/"
  local record_count="${#ALL_WT_PATHS[@]}" index=0 record_path canon_path
  while [ "$index" -lt "$record_count" ]; do
    if [ "${ALL_WT_BRANCHES[$index]}" = "$branch" ]; then
      record_path="${ALL_WT_PATHS[$index]}"
      if [ "$index" -eq 0 ]; then
        printf 'in-main %s' "$index"
      elif [ "${ALL_WT_LOCK_REASONS[$index]}" = "initializing" ]; then
        printf 'add-unfinished %s' "$index"
      elif [ ! -d "$record_path" ] || [ ! -e "$record_path/.git" ]; then
        printf 'missing %s' "$index"
      else
        canon_path=$(worktree_canon_path "$record_path")
        if [[ "$canon_path" != "$worktrees_prefix"* ]]; then
          printf 'outside %s' "$index"
        elif [ "$canon_path" = "$canon_cwd" ]; then
          printf 'current %s' "$index"
        else
          printf 'existing %s' "$index"
        fi
      fi
      return 0
    fi
    index=$((index + 1))
  done
  printf 'absent'
}

# worktree_ensure_ref_check <branch> — reads `<sha> <refname>` lines on stdin
# (refs/heads/ listing), skipping empty lines. Prints nothing, or one of:
#   branch-without-worktree <sha>     <branch> already exists
#   ref-conflict <existing branch>    an existing branch is a path prefix of <branch>, or lies beneath it
worktree_ensure_ref_check() {
  local branch="$1" sha refname existing
  while read -r sha refname || [ -n "$sha" ]; do
    [ -n "$sha" ] || continue
    [[ "$refname" == refs/heads/* ]] || continue
    existing="${refname#refs/heads/}"
    if [ "$existing" = "$branch" ]; then
      printf 'branch-without-worktree %s' "$sha"
      return 0
    fi
    if [[ "$branch" == "$existing"/* ]] || [[ "$existing" == "$branch"/* ]]; then
      printf 'ref-conflict %s' "$existing"
      return 0
    fi
  done
  return 0
}

# worktree_ensure_describe_record <index> — prints one line describing the
# ALL_WT_* record at <index>: its path, branch or detached, lock, a directory
# problem, and git's prunable reason with the prune remedy.
worktree_ensure_describe_record() {
  local index="$1" record_path details line
  record_path="${ALL_WT_PATHS[$index]}"
  if [ -n "${ALL_WT_BRANCHES[$index]}" ]; then
    details="branch ${ALL_WT_BRANCHES[$index]}"
  else
    details="detached"
  fi
  if [ "${ALL_WT_LOCKED[$index]}" = "1" ]; then
    details="$details, locked"
  fi
  if [ ! -d "$record_path" ]; then
    details="$details, directory missing"
  elif [ ! -e "$record_path/.git" ]; then
    details="$details, no .git"
  fi
  line="$record_path ($details)"
  if [ -n "${ALL_WT_PRUNABLE_REASONS[$index]}" ]; then
    line="$line; git marks it prunable: ${ALL_WT_PRUNABLE_REASONS[$index]}. \`git worktree prune\` removes this record but not its directory or branch; a run for that branch then reports \`branch-without-worktree\`"
  fi
  printf '%s' "$line"
}

# worktree_ensure_target_check <canon-main> <target> — walks each component
# from <canon-main>/.claude down to <target>, reading the ALL_WT_* arrays.
# The first hit prints `<token> <message>` and stops the walk; no hit and an
# empty or absent <target> print nothing. Per component, in order:
#   a symlink (dangling included)                       symlinked-path
#   a linked record's canonical path equals it          path-in-use
#   it exists and is not a directory                    path-in-use
#   it holds a `.git` entry (any type)                  path-in-use (stray)
# Then a <target> directory holding any entry, dotfiles included, or one that cannot be
# listed, prints target-not-empty.
worktree_ensure_target_check() {
  local canon_main="$1" target="$2"
  local relative="${target#"$canon_main"/}"
  local record_count="${#ALL_WT_PATHS[@]}" index component_path part stray_message target_entries
  local -a path_parts
  local -a record_canon_paths
  record_canon_paths=()
  # Canonicalize every linked record once, up front, rather than once per component.
  index=1
  while [ "$index" -lt "$record_count" ]; do
    record_canon_paths[index]=$(worktree_canon_path "${ALL_WT_PATHS[$index]}")
    index=$((index + 1))
  done
  IFS=/ read -r -a path_parts <<< "$relative"
  component_path="$canon_main"
  for part in "${path_parts[@]}"; do
    component_path="$component_path/$part"
    if [ -L "$component_path" ]; then
      printf 'symlinked-path %s is a symlink, and no worktree is created under a symlinked path' "$component_path"
      return 0
    fi
    index=1
    while [ "$index" -lt "$record_count" ]; do
      if [ "${record_canon_paths[$index]}" = "$component_path" ]; then
        printf 'path-in-use %s is claimed by a registered worktree: %s' \
          "$component_path" "$(worktree_ensure_describe_record "$index")"
        return 0
      fi
      index=$((index + 1))
    done
    if [ -e "$component_path" ] && [ ! -d "$component_path" ]; then
      printf 'path-in-use %s exists and is not a directory' "$component_path"
      return 0
    fi
    if [ -e "$component_path/.git" ] || [ -L "$component_path/.git" ]; then
      stray_message="$component_path holds a .git entry, but no registered worktree claims this directory; resolving it is the engineer's call"
      printf 'path-in-use %s' "$stray_message"
      return 0
    fi
  done
  if [ -d "$target" ]; then
    if ! target_entries=$(find "$target" -mindepth 1 -maxdepth 1 -print); then
      printf 'target-not-empty %s exists but cannot be listed, so it cannot be shown empty' "$target"
    elif [ -n "$target_entries" ]; then
      printf 'target-not-empty %s already exists and holds files' "$target"
    fi
  fi
  return 0
}
