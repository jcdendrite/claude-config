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
  # Defined before _lib.sh is sourced so a failed source can still deny,
  # which means _lib_jq may not exist yet. Prefer it when it does, for its
  # timeout backstop.
  if declare -F _lib_jq >/dev/null 2>&1; then
    reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  else
    reason_json=$(printf '%s' "$reason" | jq -Rs . 2>/dev/null)
  fi
  if [ -z "$reason_json" ]; then
    # jq is absent, failed, or was killed by the timeout backstop. Exit 2 is
    # the harness's blocking path for PreToolUse and carries the reason on
    # stderr, so it needs no JSON encoding. Emitting a half-built payload on
    # exit 0 instead would parse as no-decision and let the tool run.
    #
    # The fixed prefix is load-bearing: every gate parses its input with jq
    # before any command filtering, so a missing jq denies every tool call
    # with the parse-failure reason below — which names the wrong cause.
    # Without this line the session has no in-agent route to a fix.
    printf 'Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. In an interactive session, install jq (and GNU coreutils timeout) using the ! shell escape, which runs outside the tool-call path these hooks gate; in a headless or non-interactive run, ensure jq is installed in the execution environment beforehand. Underlying gate reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
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
