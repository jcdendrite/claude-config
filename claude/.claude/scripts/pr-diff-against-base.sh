#!/usr/bin/env bash
# Prints the cumulative PR-vs-default-branch diff /ready-for-review's step 3
# reviews to stdout: git diff <merge-base of origin/<base-branch> and HEAD>...HEAD.
# Usage: pr-diff-against-base.sh [--record] [--diff-file]
# --record additionally records the diff as the subject
# `~/.claude/scripts/marker.sh write cumulative-review` later reads, at
# <config-dir>/cumulative-review-subject-markers/<repo-hash>.<session-id> --
# see docs/design-decisions.md §44 and §50.
# --diff-file additionally writes the diff to
# <config-dir>/cumulative-review-diff-markers/<repo-hash>.<session-id> and
# announces the path on stderr as `DIFF_FILE: <path>`, for a reviewer with
# no Bash to Read directly -- ready-for-review/SKILL.md step 4 is the only
# caller. Both flags may be combined. `marker.sh deactivate ready-for-review`
# removes both artifacts.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

RECORD=0
WRITE_DIFF_FILE=0
TMP_FILE=""
DIFF_TMP=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --record)
      RECORD=1
      shift
      ;;
    --diff-file)
      WRITE_DIFF_FILE=1
      shift
      ;;
    *)
      printf 'pr-diff-against-base.sh: unknown argument %s (supports --record, --diff-file)\n' "$1" >&2
      exit 2
      ;;
  esac
done

if ! BASE_REF=$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null); then
  # gh exits nonzero for "no PR open yet" and for auth/network failure alike.
  if ! BASE_REF=$(_lib_default_branch_or_guess "$PWD"); then
    printf 'pr-diff-against-base.sh: gh pr view failed and no default branch resolved from origin\n' >&2
    exit 1
  fi
  printf 'pr-diff-against-base.sh: gh pr view failed; defaulting base to %s\n' "$BASE_REF" >&2
fi

if ! MERGE_BASE=$(git merge-base "origin/$BASE_REF" HEAD 2>/dev/null); then
  printf 'pr-diff-against-base.sh: could not resolve merge-base against origin/%s\n' "$BASE_REF" >&2
  exit 1
fi

# Command substitution strips trailing newlines; the printf below restores
# exactly one, so stdout matches git diff's own output byte for byte.
DIFF_TEXT=$(git diff "$MERGE_BASE...HEAD")
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
    REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
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
    printf 'pr-diff-against-base.sh: --record could not resolve the repo root, config directory, or session id; subject not recorded.\n' >&2
  fi
fi

if [ "$WRITE_DIFF_FILE" -eq 1 ]; then
  if REPO_ROOT=$(_lib_repo_root) && CONFIG_DIR=$(_lib_config_dir) && SESSION_ID=$(_lib_resolve_session_id); then
    REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
    DIFF_DIR="$CONFIG_DIR/cumulative-review-diff-markers"
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
    printf 'pr-diff-against-base.sh: --diff-file could not resolve the repo root, config directory, or session id; diff file not written.\n' >&2
  fi
fi

exit 0
