#!/bin/bash
# hook-class: informational
# SessionStart hook: surface active gate-bypass marker state, and a
# review-narrative ledger summary, to the resuming Claude session.
#
# Audience: Claude (the agent), not humans. Output is a JSON payload with
# hookSpecificOutput.additionalContext — the harness injects it into the
# agent's conversation context. Registered with matcher
# "startup|clear|compact|resume" so it fires on fresh starts, /compact,
# /clear, AND a genuine session resume (--resume/--continue), restoring
# marker and ledger knowledge in each resumed context. "fork" is deliberately
# excluded: a forked session gets a fresh session-id with no ledger entries
# of its own yet, so firing on fork would always no-op.
#
# Emits hookSpecificOutput only when at least one active marker is present
# or stale, or the review-narrative ledger has content to summarize.
# All-absent (normal fresh-session state) produces no output, keeping
# routine session starts noise-free.
#
# Active markers are session-scoped (keyed by session_id) so they can be
# checked here regardless of which git repo the session opens in. Completion
# markers are repo-scoped and checked lazily by the PreToolUse hooks.
#
# Ledger summary: keyed the same way review-ledger.sh keys writes (repo-hash
# + session-id), resolved from the payload's `.cwd` field, not process cwd —
# a linked-worktree session's ambient cwd is not guaranteed to match the
# payload's declared cwd (see set-session-title-from-branch.sh, which resolves
# the same way for the same reason). Gated on
# ~/.claude/.review-narrative-ledger-disabled, the same kill switch
# review-ledger.sh's own append path checks; the marker-status reporting
# above stays always-on, ungated.
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

MARKER_BLOCK=""
if [ "$PLAN_REVIEW_STATUS" != "absent" ] \
   || [ "$READY_FOR_REVIEW_STATUS" != "absent" ] \
   || [ "$RESPOND_PR_STATUS" != "absent" ]; then
  MARKER_BLOCK=$(printf 'Active review-skill gate markers detected for this session. Each line below shows one skill'"'"'s bypass state — "present" means the gate is bypassed (skill is mid-run or was interrupted); "stale" (>60 min old) means the bypass has expired and the gate is back in force.\n  plan-review-active: %s\n  ready-for-review-active: %s\n  respond-pr-active: %s' \
    "$PLAN_REVIEW_STATUS" "$READY_FOR_REVIEW_STATUS" "$RESPOND_PR_STATUS")
fi

LEDGER_SUMMARY=""
if [ ! -f "$CONFIG_DIR/.review-narrative-ledger-disabled" ]; then
  PAYLOAD_CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
  REPO_ROOT=""
  if [ -n "$PAYLOAD_CWD" ]; then
    REPO_ROOT=$(git -C "$PAYLOAD_CWD" rev-parse --show-toplevel 2>/dev/null)
  fi
  if [ -n "$REPO_ROOT" ]; then
    REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
    LEDGER_FILE="$CONFIG_DIR/review-narrative-ledger/$REPO_HASH.$SESSION_ID.jsonl"
    if [ -s "$LEDGER_FILE" ]; then
      # shellcheck disable=SC2016 # single-quoted on purpose: $addressed/
      # $deferred are jq's own `as` bindings, meant to expand inside jq, not bash.
      LEDGER_SUMMARY=$(_lib_jq -rs '
          (map(select(.disposition=="ADDRESS")) | length) as $addressed
        | (map(select(.disposition=="DEFER")) | length) as $deferred
        | "\($addressed + $deferred) findings recorded this session: \($addressed) addressed, \($deferred) deferred — see review-narrative-ledger for detail"
        ' "$LEDGER_FILE" 2>/dev/null)
    fi
  fi
fi

if [ -z "$MARKER_BLOCK" ] && [ -z "$LEDGER_SUMMARY" ]; then
  exit 0
fi

if [ -n "$MARKER_BLOCK" ] && [ -n "$LEDGER_SUMMARY" ]; then
  ADDITIONAL_CONTEXT=$(printf '%s\n%s' "$MARKER_BLOCK" "$LEDGER_SUMMARY")
elif [ -n "$MARKER_BLOCK" ]; then
  ADDITIONAL_CONTEXT="$MARKER_BLOCK"
else
  ADDITIONAL_CONTEXT="$LEDGER_SUMMARY"
fi

jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' || true
exit 0
