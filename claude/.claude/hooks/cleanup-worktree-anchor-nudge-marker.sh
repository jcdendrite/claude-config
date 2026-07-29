#!/bin/bash
# hook-class: informational
# SessionEnd hook: remove the per-session worktree-anchor-nudge state file
# written by nudge-worktree-anchor.sh when the session ends.
#
# Deletes the state file so that a stale entry does not suppress a nudge in a
# future session with the same session_id (recycled ids are theoretically
# possible).
#
# Fail-open: SessionEnd hooks must never block. Every path exits 0.

INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

# SESSION_ID feeds the rm -f target below as a path component ("../" would
# escape STATE_DIR); fail the same way an empty id already does.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
_lib_valid_session_id_component "$SESSION_ID" || exit 0

STATE_DIR="$HOME/.claude/.worktree-anchor-nudge.d"

rm -f "${STATE_DIR}/${SESSION_ID}" 2>/dev/null || true

exit 0
