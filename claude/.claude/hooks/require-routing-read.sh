#!/bin/bash
# hook-class: gate
set -uo pipefail
# PreToolUse: during an active plan-review session, require that ROUTING.md
# was Read (tracked by log-routing-read.sh) before any Agent spawn.
#
# tool_name for sub-agent spawning is "Agent" (verified from session transcripts).
# This hook deliberately does NOT cover Bash matchers — sub-agent spawning is
# via the dedicated Agent tool, not shell.

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by routing-read gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by routing-read gate: could not parse tool-input JSON."

[ "$TOOL_NAME" = "Agent" ] || exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
[ -n "$SESSION_ID" ] || exit 0

# Only enforce during an active plan-review session.
ACTIVE_MARKER="$HOME/.claude/.plan-review-active.d/$SESSION_ID"
[ -f "$ACTIVE_MARKER" ] || exit 0

# Allow if a fresh (< 60 min) routing-read marker exists.
ROUTING_MARKER="$HOME/.claude/.plan-review-routing-read.d/$SESSION_ID"
if [ -f "$ROUTING_MARKER" ] && [ -n "$(find "$ROUTING_MARKER" -mmin -60 2>/dev/null)" ]; then
    exit 0
fi

emit_deny "Agent spawn blocked by plan-review routing gate: Read ~/.claude/skills/plan-review/ROUTING.md before spawning any specialist agent. All spawn criteria (always-spawn rules, item ownership, reconciliation logic) live exclusively in ROUTING.md."
