#!/bin/bash
# hook-class: informational
set -uo pipefail
# PostToolUse: when plan-review is active and ROUTING.md is Read, write a
# routing-read marker so require-routing-read.sh can verify consultation
# happened before any Agent spawn.

INPUT=$(cat)
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" = "Read" ] || exit 0

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty')
case "$FILE_PATH" in
    */skills/plan-review/ROUTING.md) ;;
    *) exit 0 ;;
esac

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
[ -n "$SESSION_ID" ] || exit 0

# SESSION_ID feeds ACTIVE_MARKER (below) and the routing-read marker path
# (further below) as a path component ("../" would escape their marker
# directories); fail the same way an empty id already does.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# Only write the marker when a plan-review session is active. Without this
# guard a Read of ROUTING.md outside plan-review (e.g., an editing session)
# would falsely authorize a later plan-review Agent spawn.
# An unresolvable config dir leaves nothing to check or write, so this
# exits the same as an unusable SESSION_ID above.
CONFIG_DIR=$(_lib_config_dir) || exit 0

ACTIVE_MARKER="$CONFIG_DIR/.plan-review-active.d/$SESSION_ID"
[ -f "$ACTIVE_MARKER" ] || exit 0

mkdir -p "$CONFIG_DIR/.plan-review-routing-read.d" && \
    touch "$CONFIG_DIR/.plan-review-routing-read.d/$SESSION_ID"

exit 0
