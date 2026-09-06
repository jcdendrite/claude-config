#!/bin/bash
# hook-class: informational
set -uo pipefail
# PostToolUse: when ROUTING.md is Read, record a pending-read timestamp so a
# later `marker.sh activate` can backfill routing-read credit for a Read
# that lands just before activation (the ordering race this closes). When
# plan-review is already active, also write the existing routing-read
# marker directly so require-routing-read.sh sees it immediately.

INPUT=$(cat)

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty')
[ "$TOOL_NAME" = "Read" ] || exit 0

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty')
case "$FILE_PATH" in
    */skills/plan-review/ROUTING.md) ;;
    *) exit 0 ;;
esac

SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty')
[ -n "$SESSION_ID" ] || exit 0

# SESSION_ID feeds both marker paths below as a path component ("../" would
# escape their marker directories); fail the same way an empty id already
# does.
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# An unresolvable config dir leaves nothing to check or write, so this
# exits the same as an unusable SESSION_ID above.
CONFIG_DIR=$(_lib_config_dir) || exit 0

# Unconditional: no active-marker precondition, since this Read may land
# before `marker.sh activate` ever runs. See marker.sh's activate backfill.
# Accumulates one file per session that ever reads this, with no eviction
# path (clear-stale's PID-liveness sweep doesn't apply -- these files carry
# no PID). Accepted at current single-operator scale; revisit if this ever
# needs bounding.
mkdir -p "$CONFIG_DIR/.plan-review-pending-read.d" && \
    touch "$CONFIG_DIR/.plan-review-pending-read.d/$SESSION_ID"

# Only write the existing routing-read marker directly when a plan-review
# session is already active. Without this guard a Read of ROUTING.md outside
# plan-review (e.g., an editing session) would falsely authorize a later
# plan-review Agent spawn.
ACTIVE_MARKER="$CONFIG_DIR/.plan-review-active.d/$SESSION_ID"
if [ -f "$ACTIVE_MARKER" ]; then
  mkdir -p "$CONFIG_DIR/.plan-review-routing-read.d" && \
      touch "$CONFIG_DIR/.plan-review-routing-read.d/$SESSION_ID"
fi

exit 0
