#!/usr/bin/env bash
# Single cleanup call for /review-pr's deliver step, on every exit path --
# posted, declined, or aborted. review-pr has no activate/deactivate arms.
# Idempotent and safe to run with nothing in flight, so a retry or a defensive
# re-run never errors.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-finish.sh

Removes this session's provenance, findings-body, diff, and context-backstop
files, and its completion marker (keyed to the main tree's root, so any tree
of the repo resolves it). Then removes every review worktree this session's
review-pr-checkout.sh runs created, found by the session-scoped directory
name in `git worktree list` rather than through provenance. Logs each match,
and the match count with the .claude/worktrees directory it inspected (0
included), on stderr. The file cleanup runs first, so a slow or stalled sweep
never leaves those files behind. The sweep is session-wide: two /review-pr
runs in flight under one session id are not told apart, so the first finish
removes both. Each git or rm call in the sweep is capped
(_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS in _lib.sh). Zero arguments,
matching every other zero-argument review-pr script's exact-match
settings.json entry. Always exits 0, whether or not anything was in flight;
a failed removal is reported on stderr and a re-run retries it.
EOF
}

if [[ $# -ne 0 ]]; then
  usage
  exit 2
fi

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

# remove_session_worktrees MAIN_REPO_ROOT MATCHED_PATHS
# Removes each newline-separated path in MATCHED_PATHS, preferring
# `git worktree remove --force --force` (a single --force refuses a locked
# worktree) and falling back to `rm -rf`. The fallback only ever sees a path
# _lib_review_pr_select_session_worktrees matched, and `git worktree prune`
# runs afterwards only when the fallback was used, since a plain `rm -rf`
# leaves the worktree registered. A path that cannot be removed stays on disk
# and in `git worktree list`, so a later run finds it again.
# Discovery reads only `git worktree list`, so an unregistered leftover
# directory is never swept.
remove_session_worktrees() {
  local main_repo_root="$1" matched_paths="$2"
  local total path used_fallback=0
  total=$(printf '%s\n' "$matched_paths" | grep -c . || true)
  echo "review-pr-finish.sh: found $total review worktree(s) for this session under $main_repo_root/.claude/worktrees." >&2
  [[ "$total" -gt 0 ]] || return 0
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    if _lib_capped_for "$_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS" git -C "$main_repo_root" worktree remove --force --force -- "$path" >/dev/null 2>&1; then
      echo "review-pr-finish.sh: removed review worktree $path" >&2
    elif _lib_capped_for "$_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS" rm -rf -- "$path" >/dev/null 2>&1; then
      used_fallback=1
      echo "review-pr-finish.sh: git could not remove review worktree $path; deleted the directory instead. A git registration for it may remain (a locked worktree survives the prune that follows); a later run of this script retries it." >&2
    else
      echo "review-pr-finish.sh: could not remove review worktree $path; re-run this script, or remove the directory by hand." >&2
    fi
  done <<< "$matched_paths"
  if [[ "$used_fallback" -eq 1 ]]; then
    _lib_capped_for "$_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS" git -C "$main_repo_root" worktree prune >/dev/null 2>&1 \
      || echo "review-pr-finish.sh: git worktree prune failed after deleting a review worktree directory; run 'git worktree prune' by hand." >&2
  fi
}

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-finish.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) -- nothing to clean up without it." >&2
  exit 0
}

SESSION_ID=$("$(dirname "$0")/marker.sh" resolve-session-id) || {
  echo "review-pr-finish.sh: could not resolve this session's id -- nothing to clean up without it." >&2
  exit 0
}

# Best-effort: a failure here must not abort the artifact removal below. An
# `|| true` on each rm keeps a failing removal (rm prints its own error) from
# tripping `set -e`, since this script always exits 0.
if REPO_HASH=$(_lib_review_pr_marker_repo_hash); then
  rm -f -- "$CONFIG_DIR/review-pr-markers/$REPO_HASH.$SESSION_ID" || true
else
  echo "review-pr-finish.sh: could not compute the review-pr marker key (not inside a git repository, or hashing failed) -- skipping completion-marker cleanup." >&2
fi

rm -f -- "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)" \
  "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" body)" \
  "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" diff)" \
  "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" context.json)" || true

if ! MAIN_REPO_ROOT=$(_lib_main_repo_root); then
  echo "review-pr-finish.sh: could not resolve this repository's main tree root -- skipping worktree removal." >&2
elif ! WORKTREE_LIST=$(_lib_capped_for "$_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" worktree list --porcelain 2>/dev/null); then
  echo "review-pr-finish.sh: could not list this repository's worktrees -- skipping worktree removal." >&2
# resolve-session-id already validated SESSION_ID, so this branch is defense
# in depth against a change to that validation.
elif ! SESSION_WORKTREES=$(_lib_review_pr_select_session_worktrees "$WORKTREE_LIST" "$MAIN_REPO_ROOT" "$SESSION_ID"); then
  echo "review-pr-finish.sh: session id '$SESSION_ID' is not a valid path component -- skipping worktree removal." >&2
else
  remove_session_worktrees "$MAIN_REPO_ROOT" "$SESSION_WORKTREES"
fi

exit 0
