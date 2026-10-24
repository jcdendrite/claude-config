#!/usr/bin/env bash
# Reports the current branch's divergence from its base in plain text to stdout -- always,
# even when fully in sync. The base is origin/<default branch>, or origin/<stack base> when
# `git config branch.<branch>.gh-merge-base` is set. Uses the same detection primitives as
# check-branch-divergence.sh's SessionStart hook (default-branch resolution, bounded fetch,
# behind-count, `git merge-tree --write-tree` trial merge), but that hook never reads the key
# and fetches and measures through `origin/<default branch>` -- see
# git-feature-branch-sync/SKILL.md's "Detecting divergence" section for the canonical recipe.
# Requires git >= 2.38 for `git merge-tree --write-tree`.
# Makes no gh call: it never reads an open PR's base.
#
# Stdout starts with `Default branch: <name>` for the default branch and `Base branch: <name>`
# for a stack base.
#
# Exit 0: report printed, regardless of divergence state -- divergence is reported in the
# text, never signaled via exit code.
# Exit 1 is a genuine failure, a different class than "divergence exists." Each of these exits 1:
# - The base could not be resolved.
# - The bounded fetch failed.
# - The stack base is unusable.
# - _lib_configured_stack_base returned a status other than 0 and 1, printing its own line.
#   A git read failure is one such stop, and it can fire with no key set.
# A stack base's fetch failure reads the same for a timeout and a branch deleted on origin.
# The stop line tells the engineer to retry, then to run `git ls-remote` to tell a base missing from origin apart from any other failure.

set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

SCRIPT_NAME="branch-divergence-status.sh"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "$SCRIPT_NAME: not inside a git repository" >&2
  exit 1
fi

# The origin/HEAD pointer only, with no conventional-name fallback: a guessed
# name here would select the branch this fetches and reports divergence
# against. A dangling origin/HEAD whose target is still live on the remote
# is not recovered here either — this script fails outright rather than
# self-healing via its own fetch below, the same tradeoff
# check-branch-divergence.sh's header documents for its hook call site. This
# is the higher-stakes of the two call sites, since it backs
# /ready-for-review and /respond-pr's precheck.
#
# With a stack base configured, the default branch is not consulted at all.
# Empty STACK_BRANCH marks the no-key path.
STACK_BRANCH=""
STACK_STATUS=0
STACK_BASE_INFO=$(_lib_configured_stack_base "$SCRIPT_NAME" "$PWD") || STACK_STATUS=$?
if [[ "$STACK_STATUS" -eq 0 ]]; then
  STACK_BRANCH="${STACK_BASE_INFO%% *}"
  BASE_BRANCH="${STACK_BASE_INFO#* }"
elif [[ "$STACK_STATUS" -ne 1 ]]; then
  # Status 2 is the helper's stop, with its line already printed.
  exit 1
elif ! BASE_BRANCH=$(_lib_default_branch_from_origin_head "$PWD"); then
  echo "$SCRIPT_NAME: could not resolve origin/HEAD -- no 'origin' remote, or its default branch is unset (try 'git remote set-head origin -a')" >&2
  exit 1
fi

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
# The named destination keeps a narrowed remote.origin.fetch from leaving the
# ref missing or stale, and the leading + lets a force-pushed base update.
# The verify confirms the ref resolves to a commit before it is measured.
if ! GIT_TERMINAL_PROMPT=0 SSH_ASKPASS='' GIT_ASKPASS='' \
    "$TIMEOUT_CMD" 2 git fetch --no-tags --quiet origin "+refs/heads/$BASE_BRANCH:refs/remotes/origin/$BASE_BRANCH" \
    >/dev/null 2>&1 \
  || ! git rev-parse --verify --quiet "refs/remotes/origin/$BASE_BRANCH^{commit}" >/dev/null 2>&1; then
  if [[ -n "$STACK_BRANCH" ]]; then
    _lib_print_unusable_stack_base "$SCRIPT_NAME" "$STACK_BRANCH" "$BASE_BRANCH" \
      "fetch failed (network, or the stack base is gone from origin); retry the command before changing the key; if it fails again, run \"git ls-remote origin refs/heads/$BASE_BRANCH\": if it prints nothing, the base is not on origin and the fix that follows applies; otherwise run \"git fetch origin +refs/heads/$BASE_BRANCH:refs/remotes/origin/$BASE_BRANCH\" by hand to see git's own error, then retry"
  else
    echo "$SCRIPT_NAME: fetch of origin/$BASE_BRANCH failed or timed out" >&2
  fi
  exit 1
fi

BEHIND=$(git rev-list --count "HEAD..refs/remotes/origin/$BASE_BRANCH")

if [[ -n "$STACK_BRANCH" ]]; then
  echo "Base branch: $BASE_BRANCH"
else
  echo "Default branch: $BASE_BRANCH"
fi
echo "Behind: $BEHIND commit(s)"

if [[ "$BEHIND" == "0" ]]; then
  echo "In sync with origin/$BASE_BRANCH."
  exit 0
fi

# Non-zero exit on conflict is expected here, not a script failure -- capture stdout
# regardless of exit status instead of letting `set -e` abort on a real conflict result.
TRIAL_OUTPUT=$(git merge-tree --write-tree "refs/remotes/origin/$BASE_BRANCH" HEAD 2>/dev/null) || true
CONFLICT_FILES=$(printf '%s\n' "$TRIAL_OUTPUT" \
  | awk '/^[0-9]+ /{print $NF}' \
  | sort -u \
  | paste -sd', ' -)

if [[ -n "$CONFLICT_FILES" ]]; then
  echo "Trial merge: CONFLICT in: $CONFLICT_FILES"
else
  echo "Trial merge: CLEAN"
fi
