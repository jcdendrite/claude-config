#!/usr/bin/env bash
# hook-class: turn-gate
# Sends a `code-writer` subagent back once when its return lacks evidence of
# the self-review code-writer.md mandates: a Read of each reviewer file its
# ledger names or its changed paths require, a ledger line per reviewer, and a
# diff re-read after its last edit. The second return of the cycle goes through.
#
# Dispatch surfaces:
# - PreToolUse matcher SubagentHandback.
# - SubagentStop matcher code-writer.
# - No matcher narrows the PreToolUse arm to one agent type, so the script checks agent_type itself.
#
# Fail posture: fail-open. Every failure allows, and only the once-per-cycle send-back blocks.
#
# set -e deliberately omitted: an aborting command's own exit status could be 2, which blocks the writer's return.
#
# Design record: docs/design-decisions/code-writer-self-review-send-back.md.
# The evaluator is evaluate-code-writer-self-review.py in this directory.
#
# Any agent's Bash tool can write the transcript, the marker directory and the log, so this is a workflow check for a cooperative writer, not a security boundary.
#
# Known gaps:
# - `docs/design-decisions/code-writer-self-review-send-back.md` § "Send it back once"
# - `docs/design-decisions/code-writer-self-review-send-back.md` § "Known degradations and unverified paths"
# - `docs/design-decisions/code-writer-self-review-send-back.md` § "Log verdicts"

set -uo pipefail

# Prefilter above the _lib.sh bootstrap: another subagent's handback costs one
# substring match. The exact agent_type check follows the jq pass below.
INPUT=$(cat 2>/dev/null)
case "$INPUT" in
  *'"code-writer"'*) ;;
  *) exit 0 ;;
esac

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

CONFIG_DIR=$(_lib_config_dir) || exit 0
LOG_FILE="$CONFIG_DIR/.code-writer-self-review.log"
MARKER_DIR="$CONFIG_DIR/.code-writer-self-review.d"
EVALUATOR="${0%/*}/evaluate-code-writer-self-review.py"

log_verdict() {
  local event="$1" verdict="$2" agent="$3"
  printf '%s %s %s agent=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$event" "$verdict" "$agent" \
    2>/dev/null >> "$LOG_FILE" || true
}

# Unit Separator joins the fields because it is not in the agent_id character set.
# The trailing "end" field keeps $(...) from stripping a trailing newline off agent_id.
FIELDS=$(printf '%s' "$INPUT" | _lib_jq -r '[.hook_event_name // "", .agent_type // "", .agent_id // "", "end"] | join("\u001f")')
JQ_STATUS=$?
if [ "$JQ_STATUS" -ne 0 ]; then
  log_verdict "-" "allowed-jq-failed" "-"
  exit 0
fi
# Parameter expansion, not read: read stops at the first newline and would truncate an agent_id that holds one.
FIELD_SEPARATOR=$'\x1f'
HOOK_EVENT=${FIELDS%%"$FIELD_SEPARATOR"*}
FIELDS_AFTER_EVENT=${FIELDS#*"$FIELD_SEPARATOR"}
AGENT_TYPE=${FIELDS_AFTER_EVENT%%"$FIELD_SEPARATOR"*}
FIELDS_AFTER_TYPE=${FIELDS_AFTER_EVENT#*"$FIELD_SEPARATOR"}
AGENT_ID=${FIELDS_AFTER_TYPE%"$FIELD_SEPARATOR"end}

[ "$AGENT_TYPE" = "code-writer" ] || exit 0

case "$HOOK_EVENT" in
  PreToolUse|SubagentStop) ;;
  *) HOOK_EVENT="-" ;;
esac

if ! _lib_valid_session_id_component "$AGENT_ID"; then
  log_verdict "$HOOK_EVENT" "allowed-invalid-agent-id" "-"
  exit 0
fi

EVALUATOR_OUTPUT=$(printf '%s' "$INPUT" | _lib_capped python3 "$EVALUATOR" "$AGENT_ID" "$MARKER_DIR" "$LOG_FILE" 2>/dev/null)
EVALUATOR_STATUS=$?
if [ "$EVALUATOR_STATUS" -ne 0 ]; then
  log_verdict "$HOOK_EVENT" "allowed-evaluator-exit=$EVALUATOR_STATUS" "$AGENT_ID"
  exit 0
fi

[ -n "$EVALUATOR_OUTPUT" ] && printf '%s' "$EVALUATOR_OUTPUT"

exit 0
