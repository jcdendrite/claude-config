#!/usr/bin/env bash
# worktree-ensure.sh — finds the worktree for <branch>, or creates one under the
# main checkout from the freshly fetched default-branch tip, then prints the one
# next step. Callers: the branch-management skill and the worktree gate's deny hint.
#
# Usage:
#   ~/.claude/scripts/worktree-ensure.sh '<branch>'
#
# <branch> may use only ASCII letters, digits, '.', '_', '/' and '-', and must be
# a name `git check-ref-format --branch` accepts.
#
# Checks and writes, in order. Every refusal that needs no write runs first.
#   1. Unsets GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR and GIT_INDEX_FILE, and exports LC_ALL=C.
#      `add-unfinished` recognizes only an add made under LC_ALL=C, because an add under a
#      translated locale may write a lock reason other than the `initializing` it matches.
#   2. Name check, then the main checkout (the first `git worktree list` record).
#   3. Lookup of the branch's worktree: usable -> prints `existing` or `current` and
#      stops, with no network call. Otherwise refuses `in-main`, `add-unfinished`,
#      `missing` or `outside`.
#   4. Refuses a local branch of that name or a path-prefix conflict with one, then any
#      path condition at <main>/.claude/worktrees/<branch> that would make git fail.
#   5. Fetch of that one ref. It follows no tags and recurses into no submodules.
#      It writes refs/remotes/origin/<default>, FETCH_HEAD and objects in the shared repository.
#      Git's automatic maintenance may then run.
#   6. Add: `worktree add --no-track -b` writes the new branch, its administrative entry,
#      and the target directory with any missing parents.
#   7. Re-reads the worktree list. That state decides success, not git's exit status,
#      because a failing post-checkout hook makes git exit non-zero after the checkout.
# Neither write touches the main checkout's HEAD, index or tracked files.
#
# Stdout on exit 0 is exactly two lines, written once after every git call:
#   created <path>
#   next: EnterWorktree{path: "<path>"}
# or the same with `existing`, or
#   current <path>
#   next: none; the working directory is this worktree, and EnterWorktree refuses a target equal to the working directory
# <path> is the record's path as `git worktree list --porcelain` prints it.
# `current` means the canonical working directory equals the worktree root;
# a subdirectory gets `existing`. Git's own output goes to stderr.
#
# On exit 1 or 2, stdout is empty and the last stderr line is
#   worktree-ensure.sh: <token>: <message>
# Tokens: usage, bad-name, no-repo, list-failed, in-main, add-unfinished, missing, outside,
#   branch-without-worktree, ref-conflict, symlinked-path, path-in-use, target-not-empty,
#   no-origin-head, fetch-failed, add-failed.
#
# Exit codes:
#   0  the worktree is ready
#   1  a state or failure to report to the engineer
#   2  the `usage` or `bad-name` token
#   2  with no `worktree-ensure.sh:` line: a shell syntax error in the call
#   2  with no `worktree-ensure.sh:` line: a script file bash cannot parse
#      Statements before a parse error may already have run.
#   127 with no `worktree-ensure.sh:` line: the script is missing
#   A run the caller's timeout killed ends with no such line and may leave the new branch,
#   a worktree record locked `initializing`, or a complete worktree.
#
# Assumes a standard clone, where git's first `worktree list` record is the main checkout.
# Other layouts are unchecked, and a create there can exit 0 with the worktree inside or
# beside the git directory.
#
# Trust: the default tip's hooks run as they would on `git pull`, under LC_ALL=C. ssh
# prompts are not suppressed, so the caller's timeout is the backstop. Hooks and
# text-matched permission rules judge only the outer call, so they cannot see the inner git calls.

set -euo pipefail

SCRIPT_NAME="worktree-ensure.sh"

# die <token> <message> [exit status] — the only writer of the last stderr line.
die() {
  printf '%s: %s: %s\n' "$SCRIPT_NAME" "$1" "$2" >&2
  exit "${3:-1}"
}

unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
export LC_ALL=C
CANON_CWD=$(pwd -P) || die no-repo "the working directory cannot be resolved"

# shellcheck source=../hooks/_lib.sh
if ! . "$(dirname "$0")/../hooks/_lib.sh"; then
  die no-repo "could not source hooks/_lib.sh; re-run ./install.sh"
fi
# shellcheck source=_worktree-lib.sh
if ! . "$(dirname "$0")/_worktree-lib.sh"; then
  die no-repo "could not source _worktree-lib.sh; re-run ./install.sh"
fi

if [ "$#" -ne 1 ]; then
  die usage "expected exactly one argument: worktree-ensure.sh '<branch>'" 2
fi
BRANCH="$1"

if ! NAME_VERDICT=$(worktree_ensure_name_check "$BRANCH"); then
  die no-repo "the name check could not run"
fi
case "$NAME_VERDICT" in
  ok) ;;
  bad-name)
    die bad-name "the branch name is not usable: use only ASCII letters, digits, '.', '_', '/' and '-', in a name git accepts" 2
    ;;
  *)
    die no-repo "git could not run (${NAME_VERDICT#git-failed } is its exit status)"
    ;;
esac

# collect_or_die <token> <message> — refreshes the ALL_WT_* arrays; an empty list means the listing failed.
collect_or_die() {
  collect_all_worktrees
  if [ "${#ALL_WT_PATHS[@]}" -eq 0 ]; then
    die "$1" "$2"
  fi
}

# lookup_branch — sets LOOKUP_STATE (absent, in-main, add-unfinished, missing, outside, current,
# existing) and LOOKUP_INDEX (the record's index, empty for absent).
lookup_branch() {
  local verdict
  if ! verdict=$(worktree_ensure_classify "$BRANCH" "$CANON_MAIN" "$CANON_CWD"); then
    die list-failed "the worktree lookup could not run"
  fi
  LOOKUP_STATE="${verdict%% *}"
  LOOKUP_INDEX=""
  if [ "$verdict" != "$LOOKUP_STATE" ]; then
    LOOKUP_INDEX="${verdict#* }"
  fi
}

# refuse_for_lookup_state — dies with the token that names LOOKUP_STATE.
refuse_for_lookup_state() {
  local description
  description=$(worktree_ensure_describe_record "$LOOKUP_INDEX")
  case "$LOOKUP_STATE" in
    in-main)
      die in-main "'$BRANCH' is checked out in the main checkout; ask the engineer to switch the main checkout to another branch, then rerun"
      ;;
    add-unfinished)
      die add-unfinished "a worktree add for '$BRANCH' is still running or was killed, so the tree may be incomplete: $description"
      ;;
    missing)
      die missing "the worktree registered for '$BRANCH' is not usable: $description"
      ;;
    outside)
      die outside "the worktree for '$BRANCH' is outside $CANON_MAIN/.claude/worktrees/, which EnterWorktree refuses: $description"
      ;;
    *)
      die list-failed "unexpected lookup state '$LOOKUP_STATE'"
      ;;
  esac
}

# emit_ready <created|existing|current> — the only writer of stdout.
emit_ready() {
  local state="$1" ready_path="${ALL_WT_PATHS[$LOOKUP_INDEX]}"
  if [ "$state" = "current" ]; then
    printf 'current %s\n' "$ready_path"
    printf 'next: none; the working directory is this worktree, and EnterWorktree refuses a target equal to the working directory\n'
  else
    printf '%s %s\n' "$state" "$ready_path"
    printf 'next: EnterWorktree{path: "%s"}\n' "$ready_path"
  fi
}

collect_or_die no-repo "the working directory is outside any git repository, or 'git worktree list' failed"
if ! CANON_MAIN=$(worktree_canon_path "${ALL_WT_PATHS[0]}"); then
  die no-repo "the main checkout's path cannot be resolved"
fi

lookup_branch
case "$LOOKUP_STATE" in
  absent) ;;
  existing | current)
    emit_ready "$LOOKUP_STATE"
    exit 0
    ;;
  *) refuse_for_lookup_state ;;
esac

if ! REF_LISTING=$(git -C "$CANON_MAIN" for-each-ref --format='%(objectname) %(refname)' refs/heads/); then
  die list-failed "listing the local branches failed"
fi
REF_VERDICT=$(worktree_ensure_ref_check "$BRANCH" <<< "$REF_LISTING")
case "$REF_VERDICT" in
  branch-without-worktree*)
    die branch-without-worktree "branch '$BRANCH' already exists at ${REF_VERDICT#branch-without-worktree } but no worktree holds it. Possible causes: an earlier create that failed or was killed after git made the branch, a create still in flight that has not registered its worktree yet, or a worktree in the middle of a rebase or bisect, which shows a detached HEAD. 'git worktree list' tells these apart. Attaching or deleting the branch is the engineer's call, with the commands in docs/scripts.md of the claude-config checkout"
    ;;
  ref-conflict*)
    die ref-conflict "existing branch '${REF_VERDICT#ref-conflict }' is a path prefix of '$BRANCH' or lies beneath it, so git cannot hold both"
    ;;
esac

TARGET="$CANON_MAIN/.claude/worktrees/$BRANCH"
TARGET_VERDICT=$(worktree_ensure_target_check "$CANON_MAIN" "$TARGET")
if [ -n "$TARGET_VERDICT" ]; then
  die "${TARGET_VERDICT%% *}" "${TARGET_VERDICT#* }"
fi

if ! DEFAULT_BRANCH=$(_lib_default_branch_from_origin_head "$CANON_MAIN"); then
  die no-origin-head "the remote's default branch cannot be read from the local clone; ask the engineer"
fi

# The refspec names its own destination, so a narrowed remote.origin.fetch cannot leave the ref stale.
if ! GIT_TERMINAL_PROMPT=0 git -C "$CANON_MAIN" fetch --quiet --no-tags --no-recurse-submodules origin \
  "+refs/heads/$DEFAULT_BRANCH:refs/remotes/origin/$DEFAULT_BRANCH" >&2; then
  die fetch-failed "fetching '$DEFAULT_BRANCH' from origin failed. The cause could be the network, authentication, a concurrent fetch holding the ref lock, or a stale refs/remotes/origin/$DEFAULT_BRANCH.lock left by a killed fetch, which blocks the fetch once the remote's '$DEFAULT_BRANCH' has advanced"
fi
if ! START_SHA=$(git -C "$CANON_MAIN" rev-parse --verify --quiet "refs/remotes/origin/$DEFAULT_BRANCH^{commit}"); then
  die fetch-failed "fetch reported success but \`refs/remotes/origin/$DEFAULT_BRANCH\` is absent; \`git worktree add\` did not run"
fi

# Git creates the branch before it checks the path, so every path refusal ran first.
# The add's status is not trusted: the re-read worktree list decides the outcome.
ADD_STATUS=0
git -C "$CANON_MAIN" worktree add --no-track -b "$BRANCH" "$TARGET" "$START_SHA" >&2 || ADD_STATUS=$?

collect_or_die list-failed "'git worktree list' failed after the add, so the add may have succeeded"
lookup_branch
case "$LOOKUP_STATE" in
  existing)
    emit_ready created
    ;;
  current)
    emit_ready current
    ;;
  absent)
    die add-failed "git worktree add exited $ADD_STATUS and no worktree holds '$BRANCH'. Git creates the branch before it checks the path, so '$BRANCH' may remain at $START_SHA. A concurrent run for this branch may own it instead, and 'git worktree list' after a moment shows which. A rerun then writes nothing and reports branch-without-worktree or the winner's state"
    ;;
  *)
    refuse_for_lookup_state
    ;;
esac
