#!/bin/bash
# hook-class: gate
set -uo pipefail
# PreToolUse: during an active plan-review session, require that ROUTING.md
# was Read (tracked by log-routing-read.sh) before any Agent spawn.
#
# tool_name for sub-agent spawning is "Agent" (verified from session transcripts).
# This hook deliberately does NOT cover Bash matchers — sub-agent spawning is
# via the dedicated Agent tool, not shell.

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by routing-read gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

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
