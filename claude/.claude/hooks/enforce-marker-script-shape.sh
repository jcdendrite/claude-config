#!/bin/bash
# Gate: enforce strict invocation shape for ~/.claude/scripts/marker.sh.
#
# Fail posture: fail-closed — if jq cannot parse the input, deny.
#
# WARNING: Do NOT remove the internal marker.sh check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands. The internal grep is the actual
# gate. The "if" field is a hint only.
#
# Any Bash command containing "marker.sh" must be one of the 10 valid
# invocation shapes exactly. No chains, no env-var prefixes, no redirects,
# no bash wrappers, no extra args. This prevents prompt-injection attacks
# that chain marker.sh invocations with malicious commands and rely on the
# settings.json allowlist to approve the first segment.
set -uo pipefail

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
JQ_EXIT=$?

if [ "$JQ_EXIT" -ne 0 ]; then
  REASON_JSON='"Blocked: could not parse tool-input JSON."'
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
  exit 0
fi

# Fast-exit: no opinion on commands that don't involve marker.sh
if ! printf '%s' "$COMMAND" | grep -qF 'marker.sh'; then
  exit 0
fi

# Strip leading/trailing whitespace
TRIMMED=$(printf '%s' "$COMMAND" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')

# Reject path traversal sequences before the allowlist check. The VALID_PATTERN
# character class permits '.' and '/', which together admit '../' segments.
# An explicit '..' pre-check closes that gap cleanly.
if printf '%s' "$TRIMMED" | grep -qF '..'; then
  TRUNCATED=$(printf '%s' "$TRIMMED" | cut -c1-80)
  REASON="marker.sh invocation denied (path traversal '..' detected). Command (truncated): $TRUNCATED"
  REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
  exit 0
fi

# Strict allowlist. Tilde form (~/.claude/scripts/marker.sh) and absolute
# path form (/home/<user>/.claude/scripts/marker.sh) are both accepted.
# No bash wrapper, no env-var prefix, no chain operator, no redirect, no
# extra args after the skill name.
VALID_PATTERN='^(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+(write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)|(activate|deactivate)[[:space:]]+(plan-review|ready-for-review|respond-pr))[[:space:]]*$'

if printf '%s' "$TRIMMED" | grep -qE "$VALID_PATTERN"; then
  exit 0
fi

# Deny. Truncate to 80 chars to avoid echoing attacker-controlled bytes verbatim.
TRUNCATED=$(printf '%s' "$TRIMMED" | cut -c1-80)
REASON="marker.sh invocation denied. Command (truncated): $TRUNCATED

Valid shapes:
  ~/.claude/scripts/marker.sh write code-review
  ~/.claude/scripts/marker.sh write skill-review
  ~/.claude/scripts/marker.sh write plan-review
  ~/.claude/scripts/marker.sh write ready-for-review
  ~/.claude/scripts/marker.sh activate plan-review
  ~/.claude/scripts/marker.sh activate ready-for-review
  ~/.claude/scripts/marker.sh activate respond-pr
  ~/.claude/scripts/marker.sh deactivate plan-review
  ~/.claude/scripts/marker.sh deactivate ready-for-review
  ~/.claude/scripts/marker.sh deactivate respond-pr

No chains (&&, ||, ;), env-var prefixes, bash wrappers, redirects, or extra args."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
