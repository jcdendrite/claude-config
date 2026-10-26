#!/usr/bin/env bash
# hook-class: turn-gate
# Sends a `code-writer` subagent back once when its return lacks evidence of
# the self-review code-writer.md mandates: a Read of each reviewer file its
# ledger names or its changed paths require, a ledger line per reviewer, and a
# diff re-read after its last edit. The second return of the cycle goes through.
#
# Workflow check for a cooperative writer that skips its self-review; not a security boundary.
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
# Known gaps:
# - A Write over an existing file counts all its comment lines as added.
# - A comment one Edit adds and a later Edit removes still counts.
# - The shell discards the evaluator's stdout on any nonzero exit status.
# - A nonzero evaluator exit after the marker claim leaves an orphaned marker.
# - After the envelope flush, the incomplete first return passes unsent-back.
# - The orphaned marker makes a later return of the same agent id pass flagged.
# - Trailing comments, unconventional test paths, prose in .rst or .txt, and comments in .css, .scss, .vue, .svelte or .html miss the path floor and fall to the catch-all.
# - Edits made through Bash (sed -i, heredocs) are not seen as edits.
# - Each newline-separated line of a Bash command is judged alone, so a line inside a multi-line quoted argument or heredoc body is judged as a command and a command inside `bash -c '...'` is not seen.
# - A Bash call that exits nonzero earns no re-read credit whatever it ran before the failure, so a `no diff re-read` verdict is not proof the writer never ran `git diff`.
# - A Bash cat of a reviewer file draws one send-back, as require-routing-read.sh does for its own file.
# - A reverted edit or an edit to an ignored path still draws the reads.
# - A writer that never returns after a send-back leaves one empty marker directory that nothing sweeps.
# - That stale marker suppresses the next send-back if the same agent id is resumed.
# - Reviewer-Read credit is a path-suffix match on a path the writer chooses, so a successful Read of any path ending in /agents/<name>.md or /skills/ai-instruction-and-memory-files/SKILL.md credits that reviewer.
# - The marker directory is creatable by the writer's own Bash tool, so a pre-created <marker_dir>/<agent-id> changes the first return's verdict.
# - An agent defined elsewhere that is also named `code-writer` inherits the send-back, with one extra round trip in both modes.
# - The send-back reason cites code-writer.md and its ledger format, which the other definition may lack.
# - Path floors are empty for CI files other than workflows (a GitLab CI file, composite-action files, docker-compose files, a Jenkinsfile, a Makefile), so those fall to the catch-all.
# - Path floors are also empty for extensionless shell scripts, SQL migrations and frontend files, which fall to the catch-all.
# - Comment detection misses a Python docstring whose opening """ is alone on its line.
# - Comment detection misses # comments in .tf, .tfvars, .hcl, .mk, .bats and .ex files.
# - When two SubagentHandback calls land in one batch, the loser's report passes unflagged (verdict allowed-claim-raced).

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

# Best-effort log append: <UTC time> <event> <verdict> agent=<agent_id>
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
