#!/usr/bin/env bash
# Prints the cumulative PR-vs-default-branch diff /ready-for-review's step 3
# reviews to stdout: git diff <merge-base of origin/<base-branch> and HEAD>...HEAD.
# Usage: pr-diff-against-base.sh [--record]
# --record additionally records the diff as the subject
# `~/.claude/scripts/marker.sh write cumulative-review` later reads, at
# <config-dir>/cumulative-review-subject-markers/<repo-hash>.<session-id> --
# see docs/design-decisions.md §44 and §50.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

RECORD=0
if [ "$#" -gt 0 ] && [ "$1" = "--record" ]; then
  RECORD=1
  shift
fi

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

# Everything below is the --record path. It always runs after the diff has
# already reached stdout, so a CONFIG_DIR/REPO_HASH/mkdir failure here never
# costs the caller the diff it asked for, and never changes this script's
# exit code -- only stderr reports it.
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
        trap 'rm -f "$TMP_FILE"' EXIT
        # No trailing newline here (unlike the stdout printf above): marker.sh
        # judges emptiness on this file's canonicalized $(cat ...) text, which
        # strips trailing newlines the same way regardless of what's written.
        if printf '%s' "$DIFF_TEXT" > "$TMP_FILE" && mv "$TMP_FILE" "$SUBJECT_DIR/$SUBJECT_NAME"; then
          trap - EXIT
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

exit 0
