#!/bin/bash
# hook-class: informational
# SessionStart hook: surface feature-branch divergence from origin/<default>
# to the resuming Claude session. Advisory-only; never blocks, never acts.
#
# Audience: Claude (the agent), not humans. Output is a JSON payload with
# hookSpecificOutput.additionalContext — the harness injects it into the
# agent's conversation context. Mirrors session-marker-dashboard.sh's
# envelope shape.
#
# Matcher: "startup" only (not startup|clear|compact). Divergence is
# on-disk state, not session-scoped; re-emitting on /compact or /clear
# would be noise. The agent re-checks at any time by invoking
# /ready-for-review (Checkpoint 2) or via a fresh session.
#
# Detection primitive (same recipe used at all three checkpoints — see
# git-feature-branch-sync/SKILL.md §"Detecting divergence"):
#   git rev-list --count HEAD..origin/<default>   → behind count
#   git merge-tree --write-tree origin/<default> HEAD  → trial merge
# git merge-tree --write-tree requires git ≥ 2.38; the trial-merge line
# is omitted in the stale-ref fallback (no behind-by-N check on stale
# data — already-known divergence is enough signal).
#
# Known gap on older git: when git < 2.38 the `--write-tree` flag is
# rejected, the captured stdout is empty, and the conflict-files awk
# returns nothing, so the advisory falsely reports "Trial merge: CLEAN".
# The behind-count + advisory still surface (the false negative is only
# on the trial-merge line). Acceptable because /ready-for-review and
# /respond-pr run the same recipe and reach the same gap, and the hook
# is advisory-only — pushes / replies are gated at the later checkpoints
# anyway. Not worth a runtime version probe.
#
# Quiet-on-success: emits nothing when behind = 0 (or when no skip
# condition can be evaluated). Mirrors session-marker-dashboard.sh.
#
# Resolution: never invoked from this hook. The agent reads the advisory
# and decides whether to invoke /git-feature-branch-sync, which owns the
# rebase/merge decision framework and pre-flight checklist.
#
# Exit 0 always — this hook must not block session startup.

set -uo pipefail

# --- skip-silent gates --------------------------------------------------
# Any of: not in a repo / detached HEAD / on default branch / no origin
# remote / origin/HEAD unresolvable → exit 0 silent.

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

CURRENT_BRANCH=$(git symbolic-ref -q --short HEAD 2>/dev/null) || exit 0
[ -z "$CURRENT_BRANCH" ] && exit 0

git remote get-url origin >/dev/null 2>&1 || exit 0

DEFAULT_REF=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null) || exit 0
DEFAULT_BRANCH=${DEFAULT_REF#refs/remotes/origin/}
[ -z "$DEFAULT_BRANCH" ] && exit 0

[ "$CURRENT_BRANCH" = "$DEFAULT_BRANCH" ] && exit 0

# --- portable timeout wrapper -------------------------------------------
# Linux ships GNU `timeout`; macOS without Homebrew coreutils ships
# neither. Detect at runtime; if neither is present, skip the fetch and
# enter the stale-ref fallback.

TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout"
fi

# --- fetch (bounded, prompt-suppressed) ---------------------------------
# GIT_TERMINAL_PROMPT=0 and SSH_ASKPASS=/GIT_ASKPASS= prevent any
# credential or passphrase prompt from blocking the hook on machines
# without ssh-agent. Any failure (timeout, network, auth) falls through
# to the stale-ref fallback.

FETCH_OK=0
if [ -n "$TIMEOUT_CMD" ]; then
  if GIT_TERMINAL_PROMPT=0 SSH_ASKPASS= GIT_ASKPASS= \
      "$TIMEOUT_CMD" 2 git fetch --no-tags --quiet origin "$DEFAULT_BRANCH" \
      >/dev/null 2>&1; then
    FETCH_OK=1
  fi
fi

# --- behind-count + advisory --------------------------------------------

REMOTE_REF="refs/remotes/origin/$DEFAULT_BRANCH"
git rev-parse --verify --quiet "$REMOTE_REF" >/dev/null || exit 0

BEHIND=$(git rev-list --count "HEAD..$REMOTE_REF" 2>/dev/null)
[ -z "$BEHIND" ] && exit 0
[ "$BEHIND" = "0" ] && exit 0

if [ "$FETCH_OK" = "1" ]; then
  TRIAL_OUTPUT=$(git merge-tree --write-tree "origin/$DEFAULT_BRANCH" HEAD 2>/dev/null)
  CONFLICT_FILES=$(printf '%s\n' "$TRIAL_OUTPUT" \
    | awk '/^[0-9]+ /{print $NF}' \
    | sort -u \
    | paste -sd', ' -)
  if [ -n "$CONFLICT_FILES" ]; then
    TRIAL_LINE="Trial merge: CONFLICT in: $CONFLICT_FILES"
  else
    TRIAL_LINE="Trial merge: CLEAN"
  fi
  HEAD_LINE="Branch \`$CURRENT_BRANCH\` is $BEHIND commits behind \`origin/$DEFAULT_BRANCH\`"
  ADDITIONAL_CONTEXT=$(printf '%s\n%s\n%s' \
    "$HEAD_LINE" \
    "$TRIAL_LINE" \
    "Acknowledge this advisory in your first response to the user and offer to invoke \`/git-feature-branch-sync\` to resolve before proceeding with substantial work; the user may defer.")
else
  HEAD_LINE="Branch \`$CURRENT_BRANCH\` is $BEHIND commits behind \`origin/$DEFAULT_BRANCH\` (stale ref — fetch failed or timed out)"
  ADDITIONAL_CONTEXT=$(printf '%s\n%s' \
    "$HEAD_LINE" \
    "Acknowledge this advisory in your first response to the user and offer to invoke \`/git-feature-branch-sync\` to resolve before proceeding with substantial work; the user may defer.")
fi

jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' || true
exit 0
