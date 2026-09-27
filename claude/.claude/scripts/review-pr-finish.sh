#!/usr/bin/env bash
# Single cleanup call for /review-pr's deliver step, on every exit path --
# posted, declined, or aborted -- replacing prose spread across three
# SKILL.md locations. review-pr carries no activate/deactivate arms of its
# own, since the acquire step needs no bypass marker -- see
# require-respond-pr.sh's own header for why. Idempotent and safe to run
# with nothing in flight, so a retry or a defensive re-run never errors.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-finish.sh

Removes this session's provenance, findings-body, diff, and context-backstop
files, and its completion marker. In `checkout` mode (read from provenance
before it is removed), also removes the review worktree and its lock file --
acquiring the same lock review-pr-worktree-replace.py uses on that path
first, so this never races a concurrent review-pr-checkout.sh invocation's
own remove/prune/add sequence for the same PR. Touches no worktree in
`diff-only` mode, or when no provenance file exists. Zero arguments,
matching every other zero-argument review-pr script's exact-match
settings.json entry. Always exits 0, whether or not anything was in flight.
EOF
}

if [[ $# -ne 0 ]]; then
  usage
  exit 2
fi

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-finish.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) -- nothing to clean up without it." >&2
  exit 0
}

SESSION_ID=$("$(dirname "$0")/marker.sh" resolve-session-id) || {
  echo "review-pr-finish.sh: could not resolve this session's id -- nothing to clean up without it." >&2
  exit 0
}

PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)

PR_IDENTITY=""
MODE=""
if [[ -f "$PROVENANCE" ]]; then
  PROVENANCE_CONTENT=$(_lib_capped cat -- "$PROVENANCE" 2>/dev/null) || PROVENANCE_CONTENT=""
  PR_IDENTITY=$(printf '%s\n' "$PROVENANCE_CONTENT" | sed -n '1p')
  # Field 4, not field 3: the provenance file's own field order is PR
  # identity, headRefOid, PID, mode -- matching the completion marker's
  # field order (PR identity, headRefOid, body hash, mode) rather than an
  # independently-drifted order for the same shared field.
  MODE=$(printf '%s\n' "$PROVENANCE_CONTENT" | sed -n '4p')
fi

# Resolved and removed BEFORE any worktree removal below: REPO_HASH is
# keyed to whichever tree this process currently stands in (the review
# worktree's own path in `checkout` mode, matching write review-pr/
# review-pr-post.sh's own cwd-based resolution) -- and `git worktree
# remove` invalidates that resolution for any FURTHER git call made from
# this same cwd, since it deletes the very `.git` file the worktree's
# identity depends on. Best-effort: a failure here must not abort the
# artifact removal below.
if REPO_ROOT_FOR_MARKER=$(_lib_repo_root 2>/dev/null); then
  if REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT_FOR_MARKER"); then
    rm -f -- "$CONFIG_DIR/review-pr-markers/$REPO_HASH.$SESSION_ID"
  else
    echo "review-pr-finish.sh: could not compute the repo hash -- skipping completion-marker cleanup." >&2
  fi
else
  echo "review-pr-finish.sh: could not resolve the current repository root -- skipping completion-marker cleanup." >&2
fi

if [[ "$MODE" == "checkout" ]]; then
  if [[ -z "$PR_IDENTITY" ]]; then
    echo "review-pr-finish.sh: provenance mode is checkout but PR identity is empty -- skipping worktree removal." >&2
  elif ! PR_IDENTITY_FIELDS=$(_lib_parse_pr_identity "$PR_IDENTITY"); then
    echo "review-pr-finish.sh: provenance mode is checkout but PR identity '$PR_IDENTITY' could not be parsed -- skipping worktree removal." >&2
  else
    OWNER_REPO=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '1p')
    PR_NUMBER=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '2p')
    # _lib_main_repo_root resolves via --git-common-dir, the same shared
    # .git directory regardless of which worktree of this repo the process
    # is standing in -- unlike --show-toplevel (_lib_repo_root), which
    # returns the CURRENT worktree's own path. review-pr-checkout.sh derives
    # WORKTREE_DIR through the same helper, so reconstructing it here always
    # agrees, even when this script runs from inside the review worktree
    # being removed.
    if ! MAIN_REPO_ROOT=$(_lib_main_repo_root); then
      echo "review-pr-finish.sh: could not resolve this repository's main tree root -- skipping worktree removal." >&2
    else
      # Same cross-repo substitution check review-pr-checkout.sh/review-pr-diff.sh
      # apply to the identical PR-identity-derived input before any destructive
      # action -- a forged or stale provenance OWNER_REPO must not be able to
      # direct this script's own `git worktree remove --force`/`rm -rf` at a
      # colliding-but-unrelated worktree.
      ORIGIN_URL=$(_lib_capped git -C "$MAIN_REPO_ROOT" remote get-url origin 2>/dev/null) || ORIGIN_URL=""
      ORIGIN_OWNER_REPO=$(printf '%s\n' "$ORIGIN_URL" | sed -nE 's|.*[:/]([^/:]+/[^/]+)$|\1|p' | sed 's|\.git$||')
      if [[ -z "$ORIGIN_OWNER_REPO" || "$ORIGIN_OWNER_REPO" != "$OWNER_REPO" ]]; then
        echo "review-pr-finish.sh: provenance PR identity '$PR_IDENTITY' names repo '$OWNER_REPO', which does not match this repo's own origin remote ('$ORIGIN_OWNER_REPO'). Skipping worktree removal." >&2
      else
        WORKTREE_DIR=$(_lib_review_pr_worktree_dir "$MAIN_REPO_ROOT" "$OWNER_REPO" "$PR_NUMBER")
        if [[ -e "$WORKTREE_DIR" || -e "$WORKTREE_DIR.lock" ]]; then
          # WORKTREE_DIR is shared across every session that has ever
          # reviewed this PR, so review-pr-worktree-remove.py compares
          # SESSION_ID against the WORKTREE_DIR.owner sidecar
          # review-pr-checkout.sh writes at checkout time -- an owner
          # mismatch means a DIFFERENT, still-active session's checkout, so
          # this session's own (possibly stale) provenance must never delete
          # it. That comparison happens only after the removal script's own
          # lock is held (not here, unlocked) so it can never observe a
          # stale owner file left behind by a checkout that started, and
          # finished, entirely within an unlocked gap.
          #
          # Acquires the same lock file review-pr-worktree-replace.py uses on
          # this path, so this removal can never race that script's own
          # remove/prune/add sequence for the same PR. A sibling script, not
          # a call into review-pr-worktree-replace.py itself: that script's
          # contract is remove-THEN-add, which a cleanup call must never
          # trigger -- the two share only the lock-acquire and
          # remove-worktree primitives (_review_pr_worktree.py).
          WORKTREE_REMOVE_LOCK_WAIT_DEADLINE_SECONDS=120
          if REMOVE_OUTPUT=$(python3 "$(dirname "$0")/review-pr-worktree-remove.py" "$MAIN_REPO_ROOT" "$WORKTREE_DIR" "$SESSION_ID" "$WORKTREE_REMOVE_LOCK_WAIT_DEADLINE_SECONDS" 2>&1); then
            :
          else
            REMOVE_EXIT=$?
            if [[ "$REMOVE_EXIT" -eq 3 ]]; then
              echo "review-pr-finish.sh: $REMOVE_OUTPUT" >&2
            else
              echo "review-pr-finish.sh: could not remove review worktree $WORKTREE_DIR: $REMOVE_OUTPUT. Artifacts below are still removed; the worktree may need manual cleanup." >&2
            fi
          fi
        fi
      fi
    fi
  fi
fi

rm -f -- "$PROVENANCE" \
  "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" body)" \
  "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" diff)" \
  "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" context.json)"

exit 0
