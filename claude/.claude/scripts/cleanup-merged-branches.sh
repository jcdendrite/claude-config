#!/usr/bin/env bash
# cleanup-merged-branches.sh — discover and clean up merged branches.
#
# Uses two signals to detect merged branches:
#   Tier A — gh pr list confirms a merged PR for this branch name, and the
#             branch's current tip matches that merged PR's headRefOid.
#   Tier B — the branch tip is reachable from origin/<default> but no
#             merged PR was found for this name (branch renamed before
#             merge, worktree-prefixed name, etc.).
#   Tier C — not reachable, no merged PR; never touched.
#
# Before either tier is considered, classify_branch() checks for an open PR
# on the branch name and, for a same-named merged PR, that the branch's
# current tip actually belongs to that merge — a name can be reused (old PR
# merged, new PR opened on the same head branch), and neither reachability
# nor a same-named merged PR by itself proves the current tip was part of
# it. A branch with an open PR, or a merged-by-name match whose tip isn't
# part of that merge, is skipped rather than deleted; a `gh` lookup failure
# also skips (fails closed) rather than treating an error as "no PR found".
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
# whose owning session has already exited. So still prefer to run this
# only once other Claude Code sessions are idle.
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
    # No procfs (e.g. macOS): `lsof -d cwd` reports each process's cwd.
    # -F pn emits a `p<pid>` line, an `fcwd` line, then an `n<path>` line per
    # process; the case below picks p and n lines and ignores any other.
    pid=""
    while IFS= read -r line; do
      case "$line" in
        p*) pid="${line#p}" ;;
        n*)
          [ "$pid" = "$$" ] && continue
          cwd="${line#n}"
          if [ -n "$cwd" ]; then
            PROCESS_CWDS+=("$cwd")
          fi
          ;;
      esac
    done < <(lsof -d cwd -F pn 2>/dev/null)
  fi
  # A scan that finds zero process working directories has not worked — at
  # minimum the invoking shell should appear. Leave PROCESS_CWD_SCAN as
  # "unavailable" in that case so worktree_in_use reports "could not
  # determine" and worktrees are skipped conservatively, rather than
  # treated as idle and deleted on the strength of an empty snapshot.
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

# ---------------------------------------------------------------------------
# Branch classification
#
# Single source of truth for whether a branch is eligible for cleanup, used
# by the detection loop, the dry-run preview, and the checked-out-branch
# message so all three agree and none re-issues its own `gh` call.
# ---------------------------------------------------------------------------

# classify_branch <branch>
#
# Prints a verdict to stdout and always returns 0 — a return-nonzero here
# would abort the whole sweep under `set -e`, which is why every lookup
# below is captured rather than allowed to propagate. Pure: makes exactly
# one `gh pr list` call and one read-only git reachability check, with no
# destructive side effects, so it is safe to call from message-only sites.
#
# Verdicts:
#   tier-a:<pr>:<merged-date>  confirmed merged; branch tip matches the
#                              merged PR's headRefOid
#   tier-b:[<stale-pr>]       reachable from origin/<default>; no PR
#                              matched this name — <stale-pr> is set when a
#                              same-named merged PR exists but this tip isn't
#                              part of that merge, empty otherwise
#   skip-open-pr:<pr>         an open PR exists for this head branch name —
#                              never delete
#   skip-stale-name:<pr>      a merged PR shares this name, but the current
#                              tip is not part of that merge (reused name)
#   skip-error                the `gh` lookup failed; fail closed
#   none                      no signal either way (Tier C — untouched)
#
# Guard 1 (open-PR check) and Guard 2 (Tier-A tip verification) both live
# here: an open PR always wins over a same-named merged PR, and a
# merged-by-name match only qualifies for Tier A if the current tip is
# part of that merge — otherwise it is a reused branch name.
classify_branch() {
  local branch="$1" pr_json tip classification stale_pr rest pr_number merged_date

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
    print(f\"stale:{merged_rows[0]['number']}\")
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
      printf 'tier-a:%s:%s\n' "$pr_number" "$merged_date"
      return 0
      ;;
    stale:*)
      stale_pr="${classification#stale:}"
      ;;
  esac

  if git merge-base --is-ancestor "$branch" \
       "refs/remotes/origin/${DEFAULT_BRANCH}" 2>/dev/null; then
    # Reachability wins over a same-named-but-different-tip merged PR: carry
    # stale_pr (if set) so callers can report that a merged PR by this name
    # exists, just not the one that produced this tip.
    printf 'tier-b:%s\n' "${stale_pr:-}"
    return 0
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
print_skip_reason_lines() {
  local _i
  for _i in "${!SKIP_REASON_BRANCHES[@]}"; do
    echo "Skipped: ${SKIP_REASON_BRANCHES[$_i]} (${SKIP_REASON_MESSAGES[$_i]})"
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
      _merged_date="${_rest#*:}"
      MERGED_BRANCHES+=("$BRANCH")
      MERGED_PR_INFO_VALUES+=("PR #${_pr_number}, merged ${_merged_date}")
      TIER_VALUES+=("A")
      ;;
    tier-b:*)
      _stale_pr="${VERDICT#tier-b:}"
      MERGED_BRANCHES+=("$BRANCH")
      if [ -n "$_stale_pr" ]; then
        MERGED_PR_INFO_VALUES+=("reachable from origin/${DEFAULT_BRANCH}; a merged PR #${_stale_pr} shares this name but this tip isn't part of that merge")
      else
        MERGED_PR_INFO_VALUES+=("reachable from origin/${DEFAULT_BRANCH}; no merged PR for this name")
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

  if [ "${#_DRY_TIER_A[@]}" -eq 0 ] && [ "${#_DRY_TIER_B[@]}" -eq 0 ] \
     && [ "${#SKIPPED_BRANCHES[@]}" -eq 0 ] && [ "${#SKIP_REASON_BRANCHES[@]}" -eq 0 ]; then
    if [ -t 1 ]; then
      echo "nothing to clean"
    fi
    exit 0
  fi

  _dry_print_branch_with_lock() {
    local _branch="$1"
    local _locked=0
    local _matched=0
    local _cand_path=""
    local _wt_path=""
    while IFS= read -r _line; do
      if [[ "$_line" == "worktree "* ]]; then
        _cand_path="${_line#worktree }"
        _matched=0
      elif [[ "$_line" == "branch refs/heads/${_branch}" ]]; then
        _matched=1
        _wt_path="$_cand_path"
      elif [ "$_matched" -eq 1 ] && [[ "$_line" == "locked"* ]]; then
        _locked=1
      elif [ -z "$_line" ]; then
        _matched=0
      fi
    done < <(git worktree list --porcelain)
    local _tag=""
    if [ -n "$_wt_path" ] && [ "$_wt_path" != "$REPO_ROOT" ]; then
      local _in_use=0
      worktree_in_use "$_wt_path" || _in_use=$?
      if [ "$_in_use" -eq 0 ]; then
        _tag=" [worktree in use — would skip]"
      elif [ "$_in_use" -eq 2 ]; then
        _tag=" [worktree idle state unverifiable — would skip]"
      elif [ "$_locked" -eq 1 ]; then
        _tag=" [locked — will unlock and remove]"
      fi
    fi
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
    echo "Probable merges (would prompt; --yes to auto-confirm):"
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
    if [ "$ASSUME_YES" -eq 1 ]; then
      TO_DELETE+=("$BRANCH")
    elif [ -t 0 ]; then
      printf "delete '%s' (%s)? [y/N]: " \
        "$BRANCH" "${MERGED_PR_INFO_VALUES[$_mb_i]}"
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
  printf 'Skipped %d probable-merge branch(es) (no TTY for prompt; rerun with --yes or from a terminal): %s\n' \
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
    WORKTREE_IN_USE=0
    worktree_in_use "$WORKTREE_PATH" || WORKTREE_IN_USE=$?
    if [ "$WORKTREE_IN_USE" -eq 0 ]; then
      echo "    worktree:       skipped (in use by a live process)"
      SKIPPED_IN_USE+=("$BRANCH")
      SKIPPED_IN_USE_REASON_VALUES+=("worktree in use by a live process")
      continue
    elif [ "$WORKTREE_IN_USE" -eq 2 ]; then
      echo "    worktree:       skipped (cannot verify it is idle)"
      SKIPPED_IN_USE+=("$BRANCH")
      SKIPPED_IN_USE_REASON_VALUES+=("worktree idle state unverifiable")
      continue
    fi
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

for _su_i in "${!SKIPPED_IN_USE[@]}"; do
  echo "Skipped (${SKIPPED_IN_USE_REASON_VALUES[$_su_i]}): ${SKIPPED_IN_USE[$_su_i]}"
done

if [ "${#SKIPPED_NEEDS_PROMPT[@]}" -gt 0 ]; then
  printf 'Skipped %d probable-merge branch(es) (no TTY for prompt; rerun with --yes or from a terminal): %s\n' \
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
