#!/bin/bash
# SessionStart hook: surface active gate-bypass marker state to the resuming
# Claude session.
#
# Audience: Claude (the agent), not humans. The harness injects plain-text
# stdout from SessionStart hooks directly into the agent's conversation context
# at session start. This lets a new session — after compaction or restart —
# see which review-skill gates are already bypassed without having to rediscover
# the on-disk state itself.
#
# Emits output only when at least one active marker is present or stale.
# All-absent (normal fresh-session state) produces no output, keeping routine
# session starts noise-free.
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

PLAN_REVIEW_STATUS=$(marker_status "$HOME/.claude/.plan-review-active.d/$SESSION_ID")
READY_FOR_REVIEW_STATUS=$(marker_status "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID")
RESPOND_PR_STATUS=$(marker_status "$HOME/.claude/.respond-pr-active.d/$SESSION_ID")

if [ "$PLAN_REVIEW_STATUS" = "absent" ] \
   && [ "$READY_FOR_REVIEW_STATUS" = "absent" ] \
   && [ "$RESPOND_PR_STATUS" = "absent" ]; then
  exit 0
fi

printf "Active review-skill gate markers detected for this session. Each line below shows one skill's bypass state — \"present\" means the gate is bypassed (skill is mid-run or was interrupted); \"stale\" (>60 min old) means the bypass has expired and the gate is back in force.\n"
printf "  plan-review-active: %s\n" "$PLAN_REVIEW_STATUS"
printf "  ready-for-review-active: %s\n" "$READY_FOR_REVIEW_STATUS"
printf "  respond-pr-active: %s\n" "$RESPOND_PR_STATUS"
