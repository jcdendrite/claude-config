#!/bin/bash
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

# Only write the marker when a plan-review session is active. Without this
# guard a Read of ROUTING.md outside plan-review (e.g., an editing session)
# would falsely authorize a later plan-review Agent spawn.
ACTIVE_MARKER="$HOME/.claude/.plan-review-active.d/$SESSION_ID"
[ -f "$ACTIVE_MARKER" ] || exit 0

mkdir -p "$HOME/.claude/.plan-review-routing-read.d" && \
    touch "$HOME/.claude/.plan-review-routing-read.d/$SESSION_ID"

exit 0
