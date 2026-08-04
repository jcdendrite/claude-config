#!/bin/bash
# hook-class: informational
# SessionStart hook: surface active gate-bypass marker state to the resuming
# Claude session.
#
# Audience: Claude (the agent), not humans. Output is a JSON payload with
# hookSpecificOutput.additionalContext — the harness injects it into the
# agent's conversation context. Registered with matcher "startup|clear|compact"
# so it fires on fresh starts AND after /compact and /clear, restoring marker
# knowledge in each resumed context.
#
# Emits hookSpecificOutput only when at least one active marker is present or
# stale. All-absent (normal fresh-session state) produces no output, keeping
# routine session starts noise-free.
#
# Active markers are session-scoped (keyed by session_id) so they can be
# checked here regardless of which git repo the session opens in. Completion
# markers are repo-scoped and checked lazily by the PreToolUse hooks.
#
# Exit 0 always — this hook must not block session startup.

INPUT=$(cat 2>/dev/null)
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
if [ -z "$SESSION_ID" ]; then
  exit 0
fi

# SESSION_ID feeds each marker_status path below as a path component ("../"
# would probe a caller-chosen path's existence/mtime); fail the same way an
# empty id already does.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  exit 0
fi

marker_status() {
  local marker="$1"
  if [ ! -f "$marker" ]; then
    printf "absent"
    return
  fi
  local now_s mtime_s age_min
  now_s=$(date +%s 2>/dev/null)
  mtime_s=$(date -r "$marker" +%s 2>/dev/null)  # -r <file> works on GNU date (Linux) and BSD date (macOS)
  if [ -z "$mtime_s" ] || [ -z "$now_s" ]; then
    printf "present (mtime unknown)"
    return
  fi
  age_min=$(( (now_s - mtime_s) / 60 ))
  if [ "$age_min" -ge 60 ]; then
    printf "stale (%dm ago)" "$age_min"
  else
    printf "present (%dm ago)" "$age_min"
  fi
}

# An unresolvable config dir leaves no marker to report on, and this hook
# must not block session startup, so it exits the same as an unusable
# SESSION_ID above.
CONFIG_DIR=$(_lib_config_dir) || exit 0

PLAN_REVIEW_STATUS=$(marker_status "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID")
READY_FOR_REVIEW_STATUS=$(marker_status "$CONFIG_DIR/.ready-for-review-active.d/$SESSION_ID")
RESPOND_PR_STATUS=$(marker_status "$CONFIG_DIR/.respond-pr-active.d/$SESSION_ID")

if [ "$PLAN_REVIEW_STATUS" = "absent" ] \
   && [ "$READY_FOR_REVIEW_STATUS" = "absent" ] \
   && [ "$RESPOND_PR_STATUS" = "absent" ]; then
  exit 0
fi

ADDITIONAL_CONTEXT=$(printf 'Active review-skill gate markers detected for this session. Each line below shows one skill'"'"'s bypass state — "present" means the gate is bypassed (skill is mid-run or was interrupted); "stale" (>60 min old) means the bypass has expired and the gate is back in force.\n  plan-review-active: %s\n  ready-for-review-active: %s\n  respond-pr-active: %s' \
  "$PLAN_REVIEW_STATUS" "$READY_FOR_REVIEW_STATUS" "$RESPOND_PR_STATUS")
jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' || true
exit 0
