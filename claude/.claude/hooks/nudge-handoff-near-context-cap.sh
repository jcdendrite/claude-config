#!/bin/bash
# hook-class: informational
# UserPromptSubmit hook: injects a one-shot context-window nudge when the
# estimated token count crosses 60% of the model's context window, prompting
# the agent to suggest /handoff if the current task is not near completion.
#
# Nudge is one-shot per session: a marker file under
# ~/.claude/.handoff-nudge-fired.d/<session_id> is written on first fire and
# prevents repeated nudges in the same session.
#
# Kill-switch: touching ~/.claude/.handoff-nudge-disabled suppresses all
# nudges globally (useful when running automated pipelines).
#
# Fail-open everywhere: any unexpected error exits 0 with no stdout so the
# hook never blocks a user prompt.
#
# Log file: ~/.claude/.handoff-nudge.log records two event types:
#   nudged  session=<id> est=<n>  — threshold crossed, nudge emitted
#   schema-drift session=<id>     — usage block present but all token fields 0/null
#
# strict mode omitted deliberately: this hook must never block prompts (exit 0
# on all paths); strict mode could cause unexpected early exits from the || true
# guards that protect against unwritable dirs and missing executables.
#
# Known limitations:
#   - claude -p one-shot runs do not fire SessionEnd, so nudge-fired markers from
#     those sessions accumulate without being cleaned up. Files are zero-byte.

INPUT=$(cat 2>/dev/null)

# Extract all four fields in a single jq pass to avoid four separate subshell spawns.
# Sequential reads from the jq output: each field on its own line handles
# empty values and paths with spaces correctly. Pre-initialize to "" so a
# failed read (e.g. jq unavailable or INPUT invalid) leaves empty strings
# rather than unbound variables. The || true preserves fail-open semantics.
SESSION_ID=""
AGENT_TYPE=""
PERMISSION_MODE=""
TRANSCRIPT_PATH=""
{
  IFS= read -r SESSION_ID
  IFS= read -r AGENT_TYPE
  IFS= read -r PERMISSION_MODE
  IFS= read -r TRANSCRIPT_PATH
} < <(
  printf '%s\n' "$INPUT" \
    | jq -r '(.session_id // ""),(.agent_type // ""),(.permission_mode // ""),(.transcript_path // "")' \
    2>/dev/null
) 2>/dev/null || true
[ -z "$SESSION_ID" ] && exit 0

# Kill-switch: suppress nudge for automated pipelines or user opt-out.
if [ -f "$HOME/.claude/.handoff-nudge-disabled" ]; then
  exit 0
fi

# Subagent gate: only nudge in the main session, not in subagents.
if [ -n "$AGENT_TYPE" ]; then
  exit 0
fi

# Plan-mode gate: nudging in plan mode would interrupt planning flow.
if [ "$PERMISSION_MODE" = "plan" ]; then
  exit 0
fi

# Transcript read: get the latest assistant usage block from the last 200 lines.
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

USAGE_BLOCK=$(timeout 2 tail -n 200 "$TRANSCRIPT_PATH" 2>/dev/null \
  | jq -s 'map(select(.message? and .message.usage)) | last // empty' 2>/dev/null)
if [ -z "$USAGE_BLOCK" ]; then
  exit 0
fi

# Sum the four token fields; missing or null fields default to 0.
ESTIMATE=$(printf '%s\n' "$USAGE_BLOCK" \
  | jq -r '
      (.message.usage.cache_read_input_tokens // 0)
    + (.message.usage.cache_creation_input_tokens // 0)
    + (.message.usage.input_tokens // 0)
    + (.message.usage.output_tokens // 0)
  ' 2>/dev/null)
if [ -z "$ESTIMATE" ]; then
  exit 0
fi

# Ensure the log parent directory exists before any log write.
mkdir -p "$HOME/.claude" 2>/dev/null || true
NUDGE_LOG="$HOME/.claude/.handoff-nudge.log"
MARKER_DIR="$HOME/.claude/.handoff-nudge-fired.d"

# Schema-drift detection: usage block present but all four fields are 0 or null.
# This indicates the transcript schema changed and the field paths are stale.
if [ "$ESTIMATE" -eq 0 ] 2>/dev/null; then
  DRIFT_MARKER="${MARKER_DIR}/${SESSION_ID}-drift"
  if [ ! -f "$DRIFT_MARKER" ]; then
    printf 'schema-drift session=%s\n' "$SESSION_ID" >> "$NUDGE_LOG" 2>/dev/null || true
    mkdir -p "$MARKER_DIR" 2>/dev/null || true
    touch "$DRIFT_MARKER" 2>/dev/null || true
  fi
  exit 0
fi

# Threshold: 120000 tokens ≈ 60% of a 200k context window.
# Source: Anthropic models documentation — claude.ai/docs/models-overview lists
# claude-sonnet-4-x and claude-opus-4-x at 200k context; 120k = 60% of 200k.
THRESHOLD=120000

if [ "$ESTIMATE" -lt "$THRESHOLD" ] 2>/dev/null; then
  exit 0
fi

# Already fired: suppress repeated nudges without logging (already recorded).
FIRED_MARKER="${MARKER_DIR}/${SESSION_ID}"
if [ -f "$FIRED_MARKER" ]; then
  exit 0
fi

# Fire: emit nudge, write marker, and log the event.
mkdir -p "$MARKER_DIR" 2>/dev/null || true
# Evict stale markers from one-shot runs that skipped SessionEnd cleanup.
find "$MARKER_DIR" -maxdepth 1 -mtime +30 -delete 2>/dev/null || true
printf 'nudged session=%s est=%s\n' "$SESSION_ID" "$ESTIMATE" >> "$NUDGE_LOG" 2>/dev/null || true
touch "$FIRED_MARKER" 2>/dev/null || true

jq -n '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: "Context is near 60% of the model window. If the current task is not close to done, suggest running /handoff to the user — it captures state in a /tmp file and resumes in a fresh session, which is ~25% cheaper per turn than waiting for auto-compaction. If the task is nearly complete, ignore this and finish."
  }
}' 2>/dev/null || true

exit 0
