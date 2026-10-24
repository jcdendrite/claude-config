#!/usr/bin/env bash
# Prints the cumulative PR-vs-base diff /ready-for-review's step 3
# reviews to stdout: the git diff-tree patch from the merge-base of origin/<base-branch> and HEAD to HEAD.
# <base-branch> is the stack base in `git config branch.<branch>.gh-merge-base`
# when that key is set, else the base of the PR `gh pr view` returns for this branch (merged or closed included), else the default branch.
# With the key set, the script uses its value or exits 1 with one stderr line.
# Each of these stops exits 1:
# - The value is unusable.
# - No merge-base resolves against origin/<value>.
# - An OPEN PR targets another base.
# - _lib_configured_stack_base returns a status other than 0 and 1, printing
#   its own line. A git read failure is one such stop, and it can fire with no key set.
# On the key path, a `gh pr view` that exits non-zero adds a clause to the
# `using branch.<b>.gh-merge-base` line saying no open-PR base was checked.
# The script never fetches.
# Usage: pr-diff-against-base.sh [--staged] [--record] [--diff-file]
# --staged prints `git diff --cached` instead of the merge-base diff,
# skipping the gh pr view/merge-base resolution entirely.
# --staged cannot combine with --record: the cumulative-review subject
# must be the cumulative diff.
# Combined with --diff-file, --staged writes to its own
# <config-dir>/code-review-diff-markers/<repo-hash>.<session-id>, distinct
# from --diff-file's default directory below.
# --record additionally records the diff as the subject
# `~/.claude/scripts/marker.sh write cumulative-review` later reads, at
# <config-dir>/cumulative-review-subject-markers/<repo-hash>.<session-id> --
# see docs/design-decisions.md §44 and §50.
# --diff-file additionally writes the diff to
# <config-dir>/cumulative-review-diff-markers/<repo-hash>.<session-id> and
# announces the path on stderr as `DIFF_FILE: <path>`, for a reviewer with
# no Bash to Read directly. See docs/scripts.md for the current caller
# list. Both flags may be combined. `marker.sh deactivate ready-for-review`
# removes both artifacts.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

STAGED=0
RECORD=0
WRITE_DIFF_FILE=0
TMP_FILE=""
DIFF_TMP=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --staged)
      STAGED=1
      shift
      ;;
    --record)
      RECORD=1
      shift
      ;;
    --diff-file)
      WRITE_DIFF_FILE=1
      shift
      ;;
    *)
      printf 'pr-diff-against-base.sh: unknown argument %s (supports --staged, --record, --diff-file)\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [ "$STAGED" -eq 1 ] && [ "$RECORD" -eq 1 ]; then
  printf 'pr-diff-against-base.sh: --staged cannot be combined with --record (the cumulative-review subject must be the cumulative diff)\n' >&2
  exit 2
fi

if [ "$STAGED" -eq 1 ]; then
  DIFF_MARKER_DIR_NAME="code-review-diff-markers"
  if ! DIFF_TEXT=$(_lib_capped git diff --cached 2>/dev/null); then
    printf 'pr-diff-against-base.sh: --staged could not compute the staged diff\n' >&2
    exit 1
  fi
  if [ -z "$DIFF_TEXT" ]; then
    printf 'DIFF_EMPTY: no staged changes\n' >&2
    exit 0
  fi
else
  DIFF_MARKER_DIR_NAME="cumulative-review-diff-markers"
  # Empty STACK_BRANCH marks the no-key path: the base of the PR `gh pr view` returns for this branch (merged or closed included), else the default branch.
  STACK_BRANCH=""
  STACK_STATUS=0
  STACK_BASE_INFO=$(_lib_configured_stack_base "pr-diff-against-base.sh" "$PWD") || STACK_STATUS=$?
  if [ "$STACK_STATUS" -eq 0 ]; then
    STACK_BRANCH="${STACK_BASE_INFO%% *}"
    BASE_REF="${STACK_BASE_INFO#* }"
    # A merged or closed PR's base is ignored; only an OPEN PR can disagree.
    # A gh failure or a reply that is not two lines reads as no open PR.
    # Only a gh failure sets GH_PR_VIEW_FAILED, which the `using` line reports.
    PR_BASE=""
    GH_PR_VIEW_FAILED=0
    if PR_REPLY=$(gh pr view --json state,baseRefName --jq '.state, .baseRefName' 2>/dev/null); then
      PR_BASE=$(_lib_open_pr_base "$PR_REPLY") || PR_BASE=""
    else
      GH_PR_VIEW_FAILED=1
    fi
    if [ -n "$PR_BASE" ] && [ "$PR_BASE" != "$BASE_REF" ]; then
      # %q-quoted because a valid branch name can still hold shell metacharacters,
      # and this text offers a command to copy.
      printf -v PR_BASE_QUOTED '%q' "$PR_BASE"
      printf -v BASE_REF_QUOTED '%q' "$BASE_REF"
      _lib_print_unusable_stack_base "pr-diff-against-base.sh" "$STACK_BRANCH" "$BASE_REF" \
        "the open PR for this branch targets $PR_BASE_QUOTED, not $BASE_REF_QUOTED (to retarget the PR to the key's value instead, the engineer can run: gh pr edit --base $BASE_REF_QUOTED)"
      exit 1
    fi
  elif [ "$STACK_STATUS" -ne 1 ]; then
    # Status 2 is the helper's stop with its line already printed. This
    # script's own exit 2 means a usage error, so every stop exits 1.
    exit 1
  elif ! BASE_REF=$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null); then
    # gh exits nonzero for "no PR open yet" and for auth/network failure alike.
    if ! BASE_REF=$(_lib_default_branch_or_guess "$PWD"); then
      printf 'pr-diff-against-base.sh: gh pr view failed and no default branch resolved from origin\n' >&2
      exit 1
    fi
    printf 'pr-diff-against-base.sh: gh pr view failed; defaulting base to %s\n' "$BASE_REF" >&2
  fi

  if ! MERGE_BASE=$(git merge-base "refs/remotes/origin/$BASE_REF" HEAD 2>/dev/null); then
    if [ -n "$STACK_BRANCH" ]; then
      _lib_print_unusable_stack_base "pr-diff-against-base.sh" "$STACK_BRANCH" "$BASE_REF" \
        "no merge-base with origin/$BASE_REF (if it was never fetched, run ~/.claude/scripts/branch-divergence-status.sh, which fetches it, before changing the key; otherwise the histories are unrelated)"
    else
      printf 'pr-diff-against-base.sh: could not resolve merge-base against origin/%s\n' "$BASE_REF" >&2
    fi
    exit 1
  fi
  # After the merge-base resolves, so a merge-base stop prints one line.
  if [ -n "$STACK_BRANCH" ]; then
    GH_FAILED_CLAUSE=""
    if [ "$GH_PR_VIEW_FAILED" -eq 1 ]; then
      GH_FAILED_CLAUSE="; gh pr view failed or found no PR, so no open-PR base was checked"
    fi
    printf 'pr-diff-against-base.sh: using branch.%s.gh-merge-base = %s as the base%s\n' "$STACK_BRANCH" "$BASE_REF" "$GH_FAILED_CLAUSE" >&2
  fi

  # The cumulative arm uses plumbing `git diff-tree`, so porcelain-only diff config
  # (e.g. diff.relative, diff.submodule, diff.context) does not change the hashed text.
  # Observed on git 2.56.0, and pinned by the tests in test_pr_diff_against_base.py.
  # See docs/design-decisions/cumulative-review-marker-keyed-on-normalized-diff.md for the residual inputs.
  # GIT_DIFF_OPTS=-u3 fixes the context width at git's default of 3 lines: plumbing still reads GIT_DIFF_OPTS,
  # so only setting the variable itself pins it.
  # --ignore-submodules=none keeps a .gitmodules `ignore` setting from dropping a gitlink change.
  # --no-color and --no-ext-diff are defense in depth: plumbing emits neither color nor external-diff output.
  # The trailing `--` ends revision parsing, so git skips its revision-versus-filename ambiguity check.
  # Without it, a file named `HEAD` in the cwd makes git exit 128 ("ambiguous argument").
  if ! DIFF_TEXT=$(GIT_DIFF_OPTS=-u3 git diff-tree -r -p -M --no-color --no-ext-diff --ignore-submodules=none "$MERGE_BASE" HEAD -- 2>/dev/null); then
    printf 'pr-diff-against-base.sh: could not compute the diff against %s\n' "$MERGE_BASE" >&2
    exit 1
  fi
fi

# Command substitution strips trailing newlines; the printf below restores exactly one.
# Stdout matches git's own output apart from trailing newlines and NUL bytes, which command substitution drops.
printf '%s\n' "$DIFF_TEXT"

# Everything below writes optional artifacts (--record, --diff-file). Both
# always run after the diff has already reached stdout, so a
# CONFIG_DIR/REPO_HASH/mkdir failure here never costs the caller the diff it
# asked for, and never changes this script's exit code -- only stderr
# reports it.

# Single combined EXIT trap for both blocks below: a second `trap ... EXIT`
# call would silently overwrite this one, per
# claude/.claude/rules/shell-script-conventions.md.
# shellcheck disable=SC2329 # invoked indirectly via the trap registered below, which shellcheck's static analysis doesn't follow.
_cleanup_diff_temp_files() {
  # Captures and restores $? explicitly: under `set -e`, an EXIT trap whose
  # last command is a false `[ -n ... ]` test would otherwise overwrite the
  # script's real exit code with that test's own failure status.
  local exit_code=$?
  [ -n "$TMP_FILE" ] && rm -f "$TMP_FILE"
  [ -n "$DIFF_TMP" ] && rm -f "$DIFF_TMP"
  return "$exit_code"
}
trap _cleanup_diff_temp_files EXIT

if [ "$RECORD" -eq 1 ]; then
  if REPO_ROOT=$(_lib_repo_root) && CONFIG_DIR=$(_lib_config_dir) && SESSION_ID=$(_lib_resolve_session_id); then
    if REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT"); then
      SUBJECT_DIR="$CONFIG_DIR/cumulative-review-subject-markers"
      # Session-id-suffixed, matching every completion marker kind's own
      # <repo-hash>.<session-id> keying, so two sessions recording in the same
      # worktree don't overwrite or consume each other's subject.
      SUBJECT_NAME="$REPO_HASH.$SESSION_ID"
      if mkdir -p "$SUBJECT_DIR" 2>/dev/null; then
        if TMP_FILE=$(mktemp "$SUBJECT_DIR/.$SUBJECT_NAME.XXXXXX" 2>/dev/null); then
          # No trailing newline here (unlike the stdout printf above): marker.sh
          # judges emptiness on this file's canonicalized $(cat ...) text, which
          # strips trailing newlines the same way regardless of what's written.
          if printf '%s' "$DIFF_TEXT" > "$TMP_FILE" && mv "$TMP_FILE" "$SUBJECT_DIR/$SUBJECT_NAME"; then
            TMP_FILE=""
          else
            printf 'pr-diff-against-base.sh: --record could not write the subject file; subject not recorded.\n' >&2
          fi
        else
          printf 'pr-diff-against-base.sh: --record could not create a temp file in %s; subject not recorded.\n' "$SUBJECT_DIR" >&2
        fi
      else
        printf 'pr-diff-against-base.sh: --record could not create %s; subject not recorded.\n' "$SUBJECT_DIR" >&2
      fi
    else
      printf 'pr-diff-against-base.sh: --record could not compute the repo hash; subject not recorded.\n' >&2
    fi
  else
    printf 'pr-diff-against-base.sh: --record could not resolve the repo root, config directory, or session id; subject not recorded.\n' >&2
  fi
fi

if [ "$WRITE_DIFF_FILE" -eq 1 ]; then
  if REPO_ROOT=$(_lib_repo_root) && CONFIG_DIR=$(_lib_config_dir) && SESSION_ID=$(_lib_resolve_session_id); then
    if REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT"); then
      DIFF_DIR="$CONFIG_DIR/$DIFF_MARKER_DIR_NAME"
      DIFF_NAME="$REPO_HASH.$SESSION_ID"
      if mkdir -p "$DIFF_DIR" 2>/dev/null; then
        if DIFF_TMP=$(mktemp "$DIFF_DIR/.$DIFF_NAME.XXXXXX" 2>/dev/null); then
          # Unlike --record's subject, this file keeps stdout's trailing
          # newline: it has no hashing consumer to disagree with.
          # mktemp+mv is atomic for one writer at a time per session; a
          # concurrent --diff-file call within the same session is unsupported,
          # since ready-for-review step 4 calls this once per gate pass.
          if printf '%s\n' "$DIFF_TEXT" > "$DIFF_TMP" && mv "$DIFF_TMP" "$DIFF_DIR/$DIFF_NAME"; then
            DIFF_TMP=""
            # Printed only after the mv succeeds, so the line's presence is
            # proof the file exists.
            printf 'DIFF_FILE: %s\n' "$DIFF_DIR/$DIFF_NAME" >&2
          else
            printf 'pr-diff-against-base.sh: --diff-file could not write the diff file; diff file not written.\n' >&2
          fi
        else
          printf 'pr-diff-against-base.sh: --diff-file could not create a temp file in %s; diff file not written.\n' "$DIFF_DIR" >&2
        fi
      else
        printf 'pr-diff-against-base.sh: --diff-file could not create %s; diff file not written.\n' "$DIFF_DIR" >&2
      fi
    else
      printf 'pr-diff-against-base.sh: --diff-file could not compute the repo hash; diff file not written.\n' >&2
    fi
  else
    printf 'pr-diff-against-base.sh: --diff-file could not resolve the repo root, config directory, or session id; diff file not written.\n' >&2
  fi
fi

exit 0
