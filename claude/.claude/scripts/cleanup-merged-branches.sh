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
# interactively; when stdin is not a TTY, Tier B branches are skipped with
# a warning.
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
#   cleanup-merged-branches.sh --all-projects --dry-run
#   cleanup-merged-branches.sh --all-projects
#
# --all-projects sweeps every git repo found under the roots listed in
# ~/.claude/cleanup-merged-branches-roots (one absolute path per line) instead
# of just the current repo — see the "--all-projects: root discovery" section
# below.
#
# Exit codes:
#   0  success (including no-op)
#   1  gh missing or unauthenticated; with --all-projects, also a missing or
#      unreadable roots config file, or set post-hoc if any repo's cleanup
#      crashed outright — a handled per-branch failure (worktree remove or
#      remote delete printed as "manual step needed") does not trip this;
#      check each repo's own output for those
#   2  bad arguments

set -euo pipefail

# Progress helpers, live-worktree detection (collect_process_cwds /
# worktree_in_use), and the branch -> worktree path/lock lookup
# (resolve_worktree_for_branch) are shared with cleanup-idle-open-pr-worktrees.sh.
# shellcheck source=_worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_worktree-lib.sh"

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $(basename "$0") [--dry-run] [--all-projects]" >&2
}

DRY_RUN=0
ALL_PROJECTS=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)      DRY_RUN=1 ;;
    --all-projects) ALL_PROJECTS=1 ;;
    *)              usage; exit 2 ;;
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
  # Linear membership scan, not an associative array: `declare -A` is a
  # bash-4+ construct that fails outright on macOS's frozen system bash 3.2
  # (test_no_bash4_constructs.py guards against it repo-wide). Fine at the
  # scale a machine's own repo count reaches.
  local root git_dir repo_root already_seen _existing_repo
  for root in "${CONFIGURED_ROOTS[@]+"${CONFIGURED_ROOTS[@]}"}"; do
    if [ ! -d "$root" ]; then
      echo "WARNING: configured root is not a directory, skipping: ${root}" >&2
      continue
    fi
    while IFS= read -r -d '' git_dir; do
      repo_root=$(cd "$(dirname "$git_dir")" && pwd -P)
      already_seen=0
      for _existing_repo in "${DISCOVERED_REPOS[@]+"${DISCOVERED_REPOS[@]}"}"; do
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
    resolve_worktree_for_branch "$_branch"
    local _wt_path="$WORKTREE_PATH"
    local _locked="$WORKTREE_LOCKED"
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

  resolve_worktree_for_branch "$BRANCH"

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
    echo "== ${_sweep_repo} =="
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
