#!/bin/bash
# Reports the current branch's divergence from origin/<default branch> in plain text to
# stdout -- always, even when fully in sync. Detection primitive matches
# check-branch-divergence.sh's SessionStart hook (default-branch resolution, bounded fetch,
# behind-count, `git merge-tree --write-tree` trial merge) -- see
# git-feature-branch-sync/SKILL.md's "Detecting divergence" section for the canonical recipe.
# Requires git >= 2.38 for `git merge-tree --write-tree`.
#
# Exit 0: report printed, regardless of divergence state -- divergence is reported in the
# text, never signaled via exit code. Exit 1: the default branch could not be resolved or the
# bounded fetch failed -- a genuine failure, a different class than "divergence exists."

set -euo pipefail

SCRIPT_NAME="branch-divergence-status.sh"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "$SCRIPT_NAME: not inside a git repository" >&2
  exit 1
fi

if ! DEFAULT_REF=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null); then
  echo "$SCRIPT_NAME: could not resolve origin/HEAD -- no 'origin' remote, or its default branch is unset (try 'git remote set-head origin -a')" >&2
  exit 1
fi
ORIGIN_HEAD_PREFIX="refs/remotes/origin/"
DEFAULT_BRANCH=${DEFAULT_REF#"$ORIGIN_HEAD_PREFIX"}

# Portable timeout wrapper: same detection as check-branch-divergence.sh's hook script.
TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout"
fi

if [[ -z "$TIMEOUT_CMD" ]]; then
  echo "$SCRIPT_NAME: neither 'timeout' nor 'gtimeout' is available to bound the fetch" >&2
  exit 1
fi

# GIT_TERMINAL_PROMPT=0 and SSH_ASKPASS='' GIT_ASKPASS='' prevent any credential or
# passphrase prompt from blocking on a machine without ssh-agent. The 2-second bound
# mirrors check-branch-divergence.sh's own value -- change both together.
if ! GIT_TERMINAL_PROMPT=0 SSH_ASKPASS='' GIT_ASKPASS='' \
    "$TIMEOUT_CMD" 2 git fetch --no-tags --quiet origin "$DEFAULT_BRANCH" \
    >/dev/null 2>&1; then
  echo "$SCRIPT_NAME: fetch of origin/$DEFAULT_BRANCH failed or timed out" >&2
  exit 1
fi

BEHIND=$(git rev-list --count "HEAD..origin/$DEFAULT_BRANCH")

echo "Default branch: $DEFAULT_BRANCH"
echo "Behind: $BEHIND commit(s)"

if [[ "$BEHIND" == "0" ]]; then
  echo "In sync with origin/$DEFAULT_BRANCH."
  exit 0
fi

# Non-zero exit on conflict is expected here, not a script failure -- capture stdout
# regardless of exit status instead of letting `set -e` abort on a real conflict result.
TRIAL_OUTPUT=$(git merge-tree --write-tree "origin/$DEFAULT_BRANCH" HEAD 2>/dev/null) || true
CONFLICT_FILES=$(printf '%s\n' "$TRIAL_OUTPUT" \
  | awk '/^[0-9]+ /{print $NF}' \
  | sort -u \
  | paste -sd', ' -)

if [[ -n "$CONFLICT_FILES" ]]; then
  echo "Trial merge: CONFLICT in: $CONFLICT_FILES"
else
  echo "Trial merge: CLEAN"
fi
