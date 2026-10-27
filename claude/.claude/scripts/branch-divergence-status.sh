#!/usr/bin/env bash
# Reports the current branch's divergence from its base in plain text to stdout -- always,
# even when fully in sync. The base is origin/<stack base> when
# `git config branch.<branch>.gh-merge-base` is set. With no key set, it is the base of an
# open PR for this branch when that differs from the default branch, else origin/<default branch>.
# Shares default-branch resolution, bounded
# fetch, behind-count and the `git merge-tree --write-tree` trial merge with
# check-branch-divergence.sh's SessionStart hook, which differs in never reading the key, in never
# reading a PR's base, and in fetching and measuring through `origin/<default branch>`.
# git-feature-branch-sync/SKILL.md's "Detecting divergence" section holds the canonical recipe.
# With a key set it makes no gh call. With no key set it makes at most one `gh pr view` call, capped at
# 10 seconds plus up to 2 seconds of kill grace, and writes no key and no config.
# The fetch writes `refs/remotes/origin/<base>` and FETCH_HEAD.
# The shared shape check (_lib_stack_base_name_shape), not prose, keeps remote text out of the commands the agent composes from a printed key or PR base.
# The default branch name comes from origin/HEAD unchecked.
# A merged or closed PR, or a reply it cannot parse, measures the default branch with no notice.
# A failed or timed-out `gh`, or no PR, measures the default branch and prints one stderr line.
# A PR base the shape check rejects measures the default branch and prints one stderr line that does not echo the base.
# The shape check rejects a leading `-`, a character outside [A-Za-z0-9._/-], an `origin/` prefix and `HEAD`.
#
# Stdout starts with `Default branch: <name>` for the default branch and `Base branch: <name>`
# for a stack base or an open PR's other base.
#
# Exit 0: report printed, regardless of divergence state -- divergence is reported in the
# text, never signaled via exit code.
# Exit 1 is a genuine failure, a different class than "divergence exists." Each of these exits 1:
# - The working directory is not inside a git repository.
# - The base could not be resolved.
# - Neither `timeout` nor `gtimeout` is available to bound the fetch.
# - The bounded fetch failed.
# - The stack base is unusable.
# - An open PR's base could not be fetched.
# - _lib_configured_stack_base returned a status other than 0 and 1, printing its own line.
#   A git read failure is one such stop, and it can fire with no key set.
# - The trial merge could not complete (_lib_trial_merge_verdict returned non-zero).
# A stack base's fetch failure reads the same for a timeout and a branch deleted on origin.
# The stop line tells the engineer to retry, then to run `git ls-remote` to tell a base missing from origin apart from any other failure.
# An open PR's base that fails to fetch prints a stop line naming the base and the `git fetch` to run by hand.

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
BASE_LABEL="Default branch"
PR_BASE_IN_USE=0
STACK_BASE_INFO=$(_lib_configured_stack_base "$SCRIPT_NAME" "$PWD") || STACK_STATUS=$?
if [[ "$STACK_STATUS" -eq 0 ]]; then
  STACK_BRANCH="${STACK_BASE_INFO%% *}"
  BASE_BRANCH="${STACK_BASE_INFO#* }"
  BASE_LABEL="Base branch"
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

# This guard must stay before the gh read: _lib_capped_for runs gh uncapped when neither binary exists, and the read's 2>/dev/null discards its note.
if [[ -z "$TIMEOUT_CMD" ]]; then
  echo "$SCRIPT_NAME: neither 'timeout' nor 'gtimeout' is available to bound the fetch" >&2
  exit 1
fi

# With no key set, an open PR's base other than the default branch is the base measured.
# The read is the only gh call, and it writes no key and no config.
# A gh failure, no PR, a merged or closed PR and a malformed reply all leave the default branch.
# A gh failure, no PR, or a base the shape check rejects each print one notice; a merged or closed PR and an unparseable reply print none.
# The call has no -R, so a fork origin whose PR targets an upstream branch reads that base from the upstream repository; pr-diff-against-base.sh reads the PR the same way.
# 10 seconds is the cap review-pr-acquire.sh, review-pr-checkout.sh, review-pr-diff.sh and review-pr-post.sh each set for their own `gh pr view` calls.
# Its vendor-grounded source is the GitHub documentation on the 10-second GraphQL request termination, which _review-pr-lib.sh cites above REVIEW_PR_STACK_LOOKUP_TIMEOUT_SECONDS.
GH_PR_VIEW_TIMEOUT_SECONDS=10
if [[ -z "$STACK_BRANCH" ]]; then
  PR_BASE=""
  if PR_REPLY=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view --json state,baseRefName --jq '.state, .baseRefName' 2>/dev/null); then
    PR_BASE=$(_lib_open_pr_base "$PR_REPLY") || PR_BASE=""
  else
    echo "$SCRIPT_NAME: gh pr view failed or found no PR; will measure against origin/$BASE_BRANCH" >&2
  fi
  # The comparison is byte-exact, so on a case-insensitive filesystem a PR base differing from the default branch only by case would fetch into a colliding remote-tracking ref.
  # That needs a retarget to a case-variant branch that exists on origin and is not guarded.
  # The same collision applies to a base named like `head`, whose fetch destination could resolve to the origin/HEAD symref, because _lib_stack_base_name_shape rejects only the exact name HEAD.
  if [[ -n "$PR_BASE" && "$PR_BASE" != "$BASE_BRANCH" ]]; then
    PR_BASE_SHAPE_STATUS=0
    # A base git rejects as a ref name (for example `a..b` or `x.lock`) passes this check and fails the fetch below with exit 1.
    # GitHub cannot hold such names.
    _lib_stack_base_name_shape "$PR_BASE" || PR_BASE_SHAPE_STATUS=$?
    if [[ "$PR_BASE_SHAPE_STATUS" -eq 0 ]]; then
      BASE_BRANCH="$PR_BASE"
      BASE_LABEL="Base branch"
      PR_BASE_IN_USE=1
    else
      # The notice lists the name rules and does not echo the base, because the base is remote text.
      echo "$SCRIPT_NAME: the open PR's base is not a usable branch name (it starts with '-', is HEAD, starts with 'origin/', or holds a character outside A-Z, a-z, 0-9, '.', '_', '/' and '-'); will measure against origin/$BASE_BRANCH" >&2
    fi
  fi
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
  elif [[ "$PR_BASE_IN_USE" -eq 1 ]]; then
    echo "$SCRIPT_NAME: fetch of the open PR's base origin/$BASE_BRANCH failed or timed out (network, or the base is gone from origin); retry, or run \"git fetch origin +refs/heads/$BASE_BRANCH:refs/remotes/origin/$BASE_BRANCH\" by hand to see git's own error" >&2
  else
    echo "$SCRIPT_NAME: fetch of origin/$BASE_BRANCH failed or timed out" >&2
  fi
  exit 1
fi

BEHIND=$(git rev-list --count "HEAD..refs/remotes/origin/$BASE_BRANCH")

if [[ "$BASE_LABEL" == "Base branch" ]]; then
  echo "Base branch: $BASE_BRANCH"
else
  echo "Default branch: $BASE_BRANCH"
fi
echo "Behind: $BEHIND commit(s)"

if [[ "$BEHIND" == "0" ]]; then
  echo "In sync with origin/$BASE_BRANCH."
  exit 0
fi

if verdict=$(_lib_trial_merge_verdict "refs/remotes/origin/$BASE_BRANCH"); then
  echo "$verdict"
else
  echo "$SCRIPT_NAME: trial merge of origin/$BASE_BRANCH into HEAD could not complete" >&2
  exit 1
fi
