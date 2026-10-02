#!/bin/bash
# hook-class: informational
# SessionStart hook: surface active gate-bypass marker state, and a
# review-narrative ledger summary, to the resuming Claude session.
#
# Audience: Claude (the agent) for the marker and ledger summaries. Output is a
# JSON payload with hookSpecificOutput.additionalContext — the harness injects
# it into the agent's conversation context. The retired-opt-out notice is for
# the engineer, so it goes out as a top-level systemMessage instead.
#
# Registered with matcher "startup|clear|compact|resume" so it fires on fresh
# starts, /compact, /clear, AND a genuine session resume (--resume/--continue),
# restoring marker and ledger knowledge in each resumed context. "fork" is
# deliberately excluded: a fork is expected to inherit its parent's
# conversation, which already holds the marker and ledger state this hook
# would restate.
#
# Emits hookSpecificOutput only when at least one active marker is present
# or stale, or the review-narrative ledger has content to summarize.
# Emits systemMessage only when the retired-opt-out notice applies.
# All-absent (normal fresh-session state) produces no output, keeping
# routine session starts noise-free.
#
# Active markers are session-scoped (keyed by session_id) so they can be
# checked here regardless of which git repo the session opens in. Completion
# markers are repo-scoped and checked lazily by the PreToolUse hooks.
#
# Ledger summary: the file comes from _lib_review_ledger_path, the resolver
# review-ledger.sh appends through, so the summary covers the branch's rows
# when the ledger is branch-scoped and only this session's when it is
# session-scoped. The repo comes from the payload's `.cwd` field, not process
# cwd -- a linked-worktree session's ambient cwd is not guaranteed to match the
# payload's declared cwd (see set-session-title-from-branch.sh, which resolves
# the same way for the same reason). The resolver's exit 1 means the ledger's
# location is unknown, so the summary prints nothing rather than reporting no
# rows.
#
# A ~/.claude/.review-narrative-ledger-disabled file has no effect on what the
# ledger records; while it exists the hook shows the engineer a systemMessage
# saying so. Marker-status reporting is always on.
#
# Exit 0 always — this hook must not block session startup.

INPUT=$(cat 2>/dev/null)

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty' 2>/dev/null)
if [ -z "$SESSION_ID" ]; then
  exit 0
fi

# SESSION_ID feeds each marker_status path below as a path component ("../"
# would probe a caller-chosen path's existence/mtime); fail the same way an
# empty id already does.
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
PAYLOAD_CWD=$(printf '%s\n' "$INPUT" | _lib_jq -r '.cwd // empty' 2>/dev/null)
REPO_ROOT=""
if [ -n "$PAYLOAD_CWD" ]; then
  REPO_ROOT=$(_lib_capped git -C "$PAYLOAD_CWD" rev-parse --show-toplevel 2>/dev/null)
fi
# The resolver makes several git calls, each bounded by _lib_capped.
if [ -n "$REPO_ROOT" ] && LEDGER_LOCATION=$(_lib_review_ledger_path "$CONFIG_DIR" "$REPO_ROOT" "$SESSION_ID" 2>/dev/null); then
  LEDGER_SCOPE=${LEDGER_LOCATION%% *}
  LEDGER_FILE=${LEDGER_LOCATION#* }
  if [ -s "$LEDGER_FILE" ]; then
    # -R/fromjson? parses one raw line at a time, so a torn or undecodable line
    # is skipped instead of hiding the whole summary; a non-object line is
    # skipped too, since it cannot carry a disposition.
    # shellcheck disable=SC2016 # single-quoted on purpose: $rows/$addressed/
    # $deferred/$settled/$scope_label are jq's own bindings, meant to expand
    # inside jq, not bash.
    LEDGER_SUMMARY=$(_lib_jq -R -n -r --arg scope "$LEDGER_SCOPE" '
        [inputs | fromjson? | select(type == "object")] as $rows
      | ($rows | map(select(.disposition=="ADDRESS")) | length) as $addressed
      | ($rows | map(select(.disposition=="DEFER")) | length) as $deferred
      | ($rows | map(select(.disposition=="SETTLED")) | length) as $settled
      | (if $scope == "branch" then "on this branch" else "this session" end) as $scope_label
      | ([$rows[].event_time // empty | select(type == "string" and . != "") | .[0:10]] | sort) as $dates
      | (if ($dates | length) == 0 then ""
         elif $dates[0] == $dates[-1] then " (\($dates[0]))"
         else " (\($dates[0]) to \($dates[-1]))" end) as $span
      | if ($addressed + $deferred + $settled) > 0 then
          "\($addressed + $deferred + $settled) findings recorded \($scope_label)\($span): \($addressed) addressed, \($deferred) deferred, \($settled) settled — run `review-ledger.sh show` for detail"
        else empty end
      ' "$LEDGER_FILE" 2>/dev/null)
  fi
fi

RETIRED_OPT_OUT_NOTICE=""
if [ -f "$CONFIG_DIR/.review-narrative-ledger-disabled" ]; then
  RETIRED_OPT_OUT_NOTICE="$CONFIG_DIR/.review-narrative-ledger-disabled is no longer honored. The review ledger always records, so engineer quotes logged at review stops now reach PR bodies. There is no replacement opt-out. Deleting that file silences this notice."
fi

NEWLINE=$'\n'
ADDITIONAL_CONTEXT=""
for context_part in "$MARKER_BLOCK" "$LEDGER_SUMMARY"; do
  if [ -n "$context_part" ]; then
    ADDITIONAL_CONTEXT="${ADDITIONAL_CONTEXT:+$ADDITIONAL_CONTEXT$NEWLINE}$context_part"
  fi
done

if [ -z "$ADDITIONAL_CONTEXT" ] && [ -z "$RETIRED_OPT_OUT_NOTICE" ]; then
  exit 0
fi

# Each channel is emitted only when it has content.
# shellcheck disable=SC2016 # single-quoted on purpose: $ctx and $notice are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them.
_lib_jq -n --arg ctx "$ADDITIONAL_CONTEXT" --arg notice "$RETIRED_OPT_OUT_NOTICE" '
    (if $ctx != "" then {hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}} else {} end)
  + (if $notice != "" then {systemMessage: $notice} else {} end)' || true
exit 0
