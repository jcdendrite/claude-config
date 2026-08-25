#!/bin/bash
# hook-class: batch-gate
# PostToolBatch hook: nudges when a subagent dispatch's own turn count
# crosses a measured outlier threshold. See docs/design-decisions.md §32 for
# the threshold's measurement basis and rationale.
#
# Adapted from nudge-handoff-near-context-cap.sh, with three differences:
# Inverted polarity -- fires only when AGENT_TYPE identifies a subagent
# dispatch, the opposite of that hook's main-session-only gate.
# Incremental counting -- turn count is tracked per dispatch via a small
# state file (offset + running total) instead of a full-transcript `jq -s`
# pass on every fire, reusing the mid-write-safety offset helper
# (_lib_advance_offset_past_complete_lines, shared via _lib.sh).
# Sampled cadence -- the incremental scan and threshold check run only on
# every LONG_TURN_NUDGE_SAMPLE_CADENCE'th fire (docs/design-decisions.md
# §32). Every other fire only appends one byte to a counter file.
#
# Never blocks: exits 0 on every path. It never suppresses the tool call it
# observes. Output is purely advisory JSON on stdout. It fires at most once
# per dispatch -- a fired marker suppresses repeat nudges for the rest of
# that session.
#
# Fail-open everywhere: any unexpected error (missing jq, missing _lib.sh,
# malformed input) exits 0 with no stdout.

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

CONFIG_DIR=$(_lib_config_dir) || CONFIG_DIR=""

# Default 340; see docs/design-decisions.md §32 for the corpus basis.
# LONG_TURN_NUDGE_THRESHOLD overrides it; a malformed value (empty, zero,
# non-digit, zero-padded, or 9+ digits) falls back to the default rather
# than degrading toward 0/unset/negative, which would fire on every dispatch.
resolve_threshold() {
  case "$LONG_TURN_NUDGE_THRESHOLD" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) THRESHOLD=340 ;;
    *) THRESHOLD=$LONG_TURN_NUDGE_THRESHOLD ;;
  esac
}

# Default 10; see docs/design-decisions.md §32 for the corpus basis.
# LONG_TURN_NUDGE_SAMPLE_CADENCE overrides it; same malformed-value guard as
# resolve_threshold above (a degraded cadence toward 0 would divide by zero).
resolve_sample_cadence() {
  case "$LONG_TURN_NUDGE_SAMPLE_CADENCE" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) SAMPLE_CADENCE=10 ;;
    *) SAMPLE_CADENCE=$LONG_TURN_NUDGE_SAMPLE_CADENCE ;;
  esac
}

# _scan_turn_count_cached TRANSCRIPT SCAN_STATE_FILE
# Sets TURN_COUNT to the dispatch's running total of assistant turns (a
# transcript record carrying a usage block -- the same shape
# nudge-handoff-near-context-cap.sh's own usage-block filter selects on).
# - Scans only the slice appended since SCAN_STATE_FILE's stored offset, via
#   _lib_advance_offset_past_complete_lines, so a fire that catches the
#   transcript mid-write undercounts by at most the partial trailing line.
# - The first scan for a dispatch (no stored offset yet) reads the whole
#   transcript-so-far once -- a one-time cost, not a per-fire one.
# - A read failure (missing wc/tail/jq, unreadable file) returns 1 and
#   leaves TURN_COUNT at the last-known total.
# - A jq failure on the appended slice (timeout, missing binary) leaves the
#   stored offset unchanged, so the same bytes are rescanned next fire
#   instead of being counted as advanced-past-but-uncounted.
_scan_turn_count_cached() {
  local transcript_path="$1" scan_state_file="$2"
  local stored_offset="" stored_total=""
  if [ -f "$scan_state_file" ]; then
    { IFS= read -r stored_offset; IFS= read -r stored_total; } < "$scan_state_file" 2>/dev/null || true
  fi
  case "$stored_offset" in ''|*[!0-9]*) stored_offset=0 ;; esac
  case "$stored_total" in ''|*[!0-9]*) stored_total=0 ;; esac
  TURN_COUNT="$stored_total"

  local current_size
  current_size=$(_lib_capped_for 2 wc -c < "$transcript_path" 2>/dev/null | tr -d '[:space:]')
  case "$current_size" in ''|*[!0-9]*) return 1 ;; esac

  local scan_from="$stored_offset"
  if [ "$scan_from" -gt "$current_size" ] 2>/dev/null; then
    scan_from=0
    stored_total=0
  fi

  local new_turns=0 jq_ok=1
  if [ "$current_size" -gt "$scan_from" ] 2>/dev/null; then
    new_turns=$(_lib_capped_for 2 tail -c +$((scan_from + 1)) "$transcript_path" 2>/dev/null \
      | _lib_capped_for 2 jq -s '[.[] | select(.message? and .message.usage)] | length' 2>/dev/null)
    case "$new_turns" in ''|*[!0-9]*) new_turns=0; jq_ok=0 ;; esac
  fi

  local new_offset="$scan_from"
  if [ "$jq_ok" -eq 1 ]; then
    new_offset=$(_lib_advance_offset_past_complete_lines "$transcript_path" "$scan_from" "$current_size")
    case "$new_offset" in ''|*[!0-9]*) new_offset="$scan_from" ;; esac
  fi

  TURN_COUNT=$(( stored_total + new_turns ))
  mkdir -p "$(dirname "$scan_state_file")" 2>/dev/null || true
  printf '%s\n%s\n' "$new_offset" "$TURN_COUNT" > "$scan_state_file" 2>/dev/null || true
  return 0
}

INPUT=$(cat 2>/dev/null)

SESSION_ID=""
AGENT_TYPE=""
TRANSCRIPT_PATH=""
HOOK_EVENT=""
{
  IFS= read -r SESSION_ID
  IFS= read -r AGENT_TYPE
  IFS= read -r TRANSCRIPT_PATH
  IFS= read -r HOOK_EVENT
} < <(
  printf '%s\n' "$INPUT" \
    | _lib_capped_for 2 jq -r '(.session_id // ""),(.agent_type // ""),(.transcript_path // ""),(.hook_event_name // "")' \
    2>/dev/null
) 2>/dev/null || true
[ -z "$SESSION_ID" ] && exit 0

# Only registered under PostToolBatch; an unrecognized value degrades to that
# label rather than an unlabeled misfire, mirroring
# nudge-handoff-near-context-cap.sh's own fallback treatment of this same
# untrusted jq-extracted field.
case "$HOOK_EVENT" in
  PostToolBatch) ;;
  *) HOOK_EVENT="PostToolBatch" ;;
esac

# SESSION_ID feeds every marker/state-file path below as a path component
# ("../" would escape MARKER_DIR); fail the same way an empty id already does.
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# No config dir means nowhere to write the counter, scan-state, or fired
# marker, so fail open exactly as an unusable SESSION_ID already does.
[ -n "$CONFIG_DIR" ] || exit 0

# Subagent gate: only nudge inside a subagent dispatch's own execution, never
# in the main session -- the inverse of nudge-handoff-near-context-cap.sh's
# `[ -n "$AGENT_TYPE" ] && exit 0` main-session-only gate.
if [ -z "$AGENT_TYPE" ]; then
  exit 0
fi

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

MARKER_DIR="$CONFIG_DIR/.long-turn-nudge-fired.d"
NUDGE_LOG="$CONFIG_DIR/.long-turn-nudge.log"
mkdir -p "$MARKER_DIR" 2>/dev/null || true

# Already fired: this dispatch already got its one nudge, so every later
# fire is pure counter upkeep with no scan or re-emit.
FIRED_MARKER="${MARKER_DIR}/${SESSION_ID}"
if [ -f "$FIRED_MARKER" ]; then
  exit 0
fi

# Sampled cadence: append one byte per fire (O_APPEND is atomic for a write
# this small, same technique nudge-handoff-near-context-cap.sh's own
# IGNORED_MARKER uses) and only run the scan/threshold check on every
# SAMPLE_CADENCE'th fire. Every other fire exits here having done only this
# cheap append -- no transcript read at all.
resolve_sample_cadence
INVOCATION_MARKER="${MARKER_DIR}/${SESSION_ID}-invocations"
printf '.' >> "$INVOCATION_MARKER" 2>/dev/null || true
INVOCATION_COUNT=$(_lib_capped_for 2 wc -c < "$INVOCATION_MARKER" 2>/dev/null | tr -d '[:space:]')
case "$INVOCATION_COUNT" in ''|*[!0-9]*) exit 0 ;; esac
[ $(( INVOCATION_COUNT % SAMPLE_CADENCE )) -eq 0 ] || exit 0

# Sole cleanup mechanism for MARKER_DIR -- no other hook or script evicts
# entries here. Runs only on this sampled fire, not every fire, so the
# sweep's own directory listing doesn't undercut the cheap-fire framing
# above.
find "$MARKER_DIR" -maxdepth 1 -mtime +30 -delete 2>/dev/null || true

SCAN_STATE_FILE="${MARKER_DIR}/${SESSION_ID}-scan"
_scan_turn_count_cached "$TRANSCRIPT_PATH" "$SCAN_STATE_FILE" || exit 0

resolve_threshold
if [ "$TURN_COUNT" -lt "$THRESHOLD" ] 2>/dev/null; then
  exit 0
fi

# Fire: build the nudge JSON first and only write the marker/log if it
# actually produced output, so a jq failure never burns this dispatch's one
# shot silently.
# shellcheck disable=SC2016 # single-quoted on purpose: $turns/$threshold are jq --argjson bindings, not shell variables; double-quoting would expand them in the shell before jq sees them. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
OUTPUT=$(_lib_capped_for 2 jq -n --arg hookEventName "$HOOK_EVENT" --argjson threshold "$THRESHOLD" --argjson turns "$TURN_COUNT" '{
  hookSpecificOutput: {
    hookEventName: $hookEventName,
    additionalContext: ("This subagent dispatch has reached " + ($turns|tostring) + " turns, past this repo'\''s measured outlier threshold (" + ($threshold|tostring) + " turns; see docs/design-decisions.md for the corpus basis). If the task is not close to done, consider stopping now and reporting back to the parent/orchestrator instead of continuing indefinitely -- a runaway dispatch is far cheaper to catch here than after many more turns. If genuinely close to done, ignore this and finish.")
  }
}' 2>/dev/null) && [ -n "$OUTPUT" ] && {
  printf 'nudged session=%s turns=%s threshold=%s event=%s\n' \
    "$SESSION_ID" "$TURN_COUNT" "$THRESHOLD" "$HOOK_EVENT" >> "$NUDGE_LOG" 2>/dev/null || true
  touch "$FIRED_MARKER" 2>/dev/null || true
  printf '%s' "$OUTPUT"
}

exit 0
