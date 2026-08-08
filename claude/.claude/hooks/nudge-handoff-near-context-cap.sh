#!/bin/bash
# hook-class: informational
# UserPromptSubmit and Stop hook: injects a one-shot context-window nudge
# when the estimated token count crosses 40% of the model's context window,
# prompting the agent to suggest /handoff if the current task is not near
# completion. Registered on both events so a session that crosses the
# threshold on its final turn, with no further user prompt, still gets
# warned.
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
#   nudged  session=<id> est=<n> model=<id> window=<n> event=<UserPromptSubmit|Stop>  — threshold crossed, nudge emitted
#   schema-drift session=<id> event=<UserPromptSubmit|Stop>     — usage block present but all token fields 0/null
#
# strict mode omitted deliberately: this hook must never block prompts (exit 0
# on all paths); strict mode could cause unexpected early exits from the || true
# guards that protect against unwritable dirs and missing executables.
#
# Known limitations:
#   - claude -p one-shot runs do not fire SessionEnd, so nudge-fired markers from
#     those sessions accumulate without being cleaned up. Files are zero-byte.
#   - Model→window resolution below is a hardcoded, dated table — see docs/handoff-nudge.md.
#   - An unrecognized model ID defaults to the 1M window, which can silently miss
#     firing for a future smaller-window model with no log signal at all.

INPUT=$(cat 2>/dev/null)

# Extract all five fields in a single jq pass to avoid five separate subshell spawns.
# Sequential reads from the jq output: each field on its own line handles
# empty values and paths with spaces correctly. Pre-initialize to "" so a
# failed read (e.g. jq unavailable or INPUT invalid) leaves empty strings
# rather than unbound variables. The || true preserves fail-open semantics.
SESSION_ID=""
AGENT_TYPE=""
PERMISSION_MODE=""
TRANSCRIPT_PATH=""
HOOK_EVENT=""
{
  IFS= read -r SESSION_ID
  IFS= read -r AGENT_TYPE
  IFS= read -r PERMISSION_MODE
  IFS= read -r TRANSCRIPT_PATH
  IFS= read -r HOOK_EVENT
} < <(
  printf '%s\n' "$INPUT" \
    | jq -r '(.session_id // ""),(.agent_type // ""),(.permission_mode // ""),(.transcript_path // ""),(.hook_event_name // "")' \
    2>/dev/null
) 2>/dev/null || true
[ -z "$SESSION_ID" ] && exit 0

# Constrain to the two registered events; default to UserPromptSubmit for an
# empty, missing, or unrecognized value (matches SESSION_ID/MODEL's own
# allowlist treatment of this same untrusted jq-extracted input).
case "$HOOK_EVENT" in
  UserPromptSubmit|Stop) ;;
  *) HOOK_EVENT="UserPromptSubmit" ;;
esac

# SESSION_ID feeds DRIFT_MARKER and FIRED_MARKER below as a path component
# ("../" would escape MARKER_DIR); fail the same way an empty id already does.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# An unresolvable config dir leaves no kill-switch/log/marker location to
# check or write, so this hook fails open the same way an unusable
# SESSION_ID already does.
CONFIG_DIR=$(_lib_config_dir) || exit 0

# Kill-switch: suppress nudge for automated pipelines or user opt-out.
if [ -f "$CONFIG_DIR/.handoff-nudge-disabled" ]; then
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

# Sum the four token fields (missing/null default to 0) and read the resolved
# model ID in the same jq pass. ESTIMATE is read first: a corrupted or
# multi-line MODEL value can only truncate MODEL, never desync ESTIMATE.
ESTIMATE=""
MODEL=""
{
  IFS= read -r ESTIMATE
  IFS= read -r MODEL
} < <(
  printf '%s\n' "$USAGE_BLOCK" \
    | jq -r '
        ((.message.usage.cache_read_input_tokens // 0)
       + (.message.usage.cache_creation_input_tokens // 0)
       + (.message.usage.input_tokens // 0)
       + (.message.usage.output_tokens // 0)),
        (.message.model // "" | tostring | gsub("[^a-zA-Z0-9._-]"; ""))
      ' 2>/dev/null
) 2>/dev/null || true
if [ -z "$ESTIMATE" ]; then
  exit 0
fi

# Ensure the log parent directory exists before any log write.
mkdir -p "$CONFIG_DIR" 2>/dev/null || true
NUDGE_LOG="$CONFIG_DIR/.handoff-nudge.log"
MARKER_DIR="$CONFIG_DIR/.handoff-nudge-fired.d"

# Schema-drift detection: usage block present but all four fields are 0 or null.
# This indicates the transcript schema changed and the field paths are stale.
if [ "$ESTIMATE" -eq 0 ] 2>/dev/null; then
  DRIFT_MARKER="${MARKER_DIR}/${SESSION_ID}-drift"
  if [ ! -f "$DRIFT_MARKER" ]; then
    printf 'schema-drift session=%s event=%s\n' "$SESSION_ID" "$HOOK_EVENT" >> "$NUDGE_LOG" 2>/dev/null || true
    mkdir -p "$MARKER_DIR" 2>/dev/null || true
    touch "$DRIFT_MARKER" 2>/dev/null || true
  fi
  exit 0
fi

# Context window in tokens per model ID; THRESHOLD is 40% of it.
# Source: https://platform.claude.com/docs/en/about-claude/models/overview,
# fetched 2026-08-03; re-verify by 2026-11-03.
# Verified 200k: Haiku 4.5, Sonnet 4.5, Opus 4.5, Opus 4.1. Verified 1M:
# Fable 5, Mythos 5, Opus 5, Opus 4.8/4.7/4.6, Sonnet 5, Sonnet 4.6.
# An unlisted ID takes the 1M default; see docs/handoff-nudge.md for why.
# Each arm requires an exact match or a trailing "-" (dated-snapshot suffix),
# not a bare trailing "*", so a longer numeral (claude-opus-4-10) can't
# collide with a shorter one (claude-opus-4-1) by string prefix alone.
case "$MODEL" in
  claude-haiku-4-5|claude-haiku-4-5-*| \
  claude-sonnet-4-5|claude-sonnet-4-5-*| \
  claude-opus-4-5|claude-opus-4-5-*| \
  claude-opus-4-1|claude-opus-4-1-*)
    CONTEXT_WINDOW=200000 ;;
  *)
    CONTEXT_WINDOW=1000000 ;;
esac
THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))

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
printf 'nudged session=%s est=%s model=%s window=%s event=%s\n' \
  "$SESSION_ID" "$ESTIMATE" "$MODEL" "$CONTEXT_WINDOW" "$HOOK_EVENT" >> "$NUDGE_LOG" 2>/dev/null || true
touch "$FIRED_MARKER" 2>/dev/null || true

jq -n --arg hookEventName "$HOOK_EVENT" '{
  hookSpecificOutput: {
    hookEventName: $hookEventName,
    additionalContext: "Context is past 40% of this model'\''s context window. If the current task is not close to done, suggest running /handoff to the user — it captures state in a /tmp file and resumes in a fresh session. Per-turn cost rises with carried context, but a fresh session pays a one-time rebuild cost first, so handoff pays off over the next several turns rather than immediately. If the task is nearly complete, ignore this and finish."
  }
}' 2>/dev/null || true

exit 0
