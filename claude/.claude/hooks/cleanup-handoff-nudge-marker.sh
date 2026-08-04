#!/bin/bash
# hook-class: informational
# SessionEnd hook: remove the per-session handoff-nudge marker files written
# by nudge-handoff-near-context-cap.sh when the session ends.
#
# Deletes both the fired marker and the schema-drift marker (if present) so
# that stale markers do not suppress nudges in a future session with the same
# session_id (recycled ids are theoretically possible).
#
# Fail-open: SessionEnd hooks must never block. Every path exits 0.

INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

# SESSION_ID feeds the rm -f targets below as a path component ("../" would
# escape MARKER_DIR); fail the same way an empty id already does.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# An unresolvable config dir leaves no marker location to clean up, so this
# hook fails open the same way an unusable SESSION_ID already does.
CONFIG_DIR=$(_lib_config_dir) || exit 0
MARKER_DIR="$CONFIG_DIR/.handoff-nudge-fired.d"

rm -f "${MARKER_DIR}/${SESSION_ID}" 2>/dev/null || true
rm -f "${MARKER_DIR}/${SESSION_ID}-drift" 2>/dev/null || true

exit 0
