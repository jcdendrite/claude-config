#!/bin/bash
# hook-class: batch-gate
# PostToolBatch hook: nudges when a subagent dispatch's own turn count
# crosses a measured outlier threshold. See docs/design-decisions.md §41 for
# the threshold's measurement basis and rationale.
#
# Adapted from nudge-handoff-near-context-cap.sh, with three differences:
# - Inverted polarity: subagent-only, not main-session-only -- see the
#   AGENT_TYPE gate below.
# - Incremental per-dispatch turn counting instead of a full-transcript
#   scan -- see _scan_turn_count_cached below.
# - A sampled cadence instead of scanning every fire -- see the
#   invocation-counter section below.
#
# Never blocks: exits 0 on every path. It never suppresses the tool call it
# observes. Output is purely advisory JSON on stdout. It fires at most once
# per dispatch -- a fired marker suppresses repeat nudges for the rest of
# that session.
#
# Fail-open everywhere: any unexpected error (missing jq, missing _lib.sh,
# malformed input) exits 0 with no stdout.
#
# Known gaps (see docs/hooks.md's Known limitations entry for this hook for
# the full list):
# - A stalled or hung dispatch producing zero new turns is not detected
#   (docs/design-decisions.md §41,
#   .claude/plans/prevent-runaway-subagent-cost.md).
# - Registered on every subagent dispatch, unlike
#   nudge-handoff-near-context-cap.sh's main-session-only registration,
#   widening that hook's pre-existing timeout-absent exposure
#   (docs/handoff-nudge.md, docs/commit-stall-block.md) accordingly.
# - An oversized record's own line force-advances the scan offset past it,
#   undercounting that record's turn.
# - A jq timeout driven by content rather than backlog size retries the
#   identical window forever.
# - MAX_SCAN_WINDOW_BYTES/SAMPLE_CADENCE's rate isn't validated against
#   real transcript growth.
# - A losing fire during lock contention contributes nothing to that
#   fire's scan.
# - Undercounting from an outpaced scan rate compounds across a burst of
#   same-session fires.
# - A SIGKILL while holding the scan lock can orphan the lock directory.
# - A mkdir racing its own timeout's SIGTERM, or a trap-ordering race
#   around LOCK_DIR's assignment, can also orphan the lock directory.

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

CONFIG_DIR=$(_lib_config_dir) || CONFIG_DIR=""

# Default 340; see docs/design-decisions.md §41 for the corpus basis.
# LONG_TURN_NUDGE_THRESHOLD overrides it; a malformed value (empty, zero,
# non-digit, zero-padded, or 9+ digits) falls back to the default rather
# than degrading toward 0/unset/negative, which would fire on every dispatch.
resolve_threshold() {
  case "$LONG_TURN_NUDGE_THRESHOLD" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) THRESHOLD=340 ;;
    *) THRESHOLD=$LONG_TURN_NUDGE_THRESHOLD ;;
  esac
}

# Default 10; see docs/design-decisions.md §41 for the corpus basis.
# LONG_TURN_NUDGE_SAMPLE_CADENCE overrides it; same malformed-value guard as
# resolve_threshold above (a degraded cadence toward 0 would divide by zero).
resolve_sample_cadence() {
  case "$LONG_TURN_NUDGE_SAMPLE_CADENCE" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) SAMPLE_CADENCE=10 ;;
    *) SAMPLE_CADENCE=$LONG_TURN_NUDGE_SAMPLE_CADENCE ;;
  esac
}

# Bounds a single scan attempt's tail|jq -s window, so a retry after a
# timed-out jq -s call re-reads a fixed-size slice instead of one that grows
# with however much backlog piled up across the skipped sampling fires.
# Default 2000000; see docs/design-decisions.md §41 for the parsing-speed
# basis (not validated against catch-up rate -- see Known gaps above).
# LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES overrides it; same malformed-value
# guard as resolve_threshold above.
resolve_max_scan_window_bytes() {
  case "$LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) MAX_SCAN_WINDOW_BYTES=2000000 ;;
    *) MAX_SCAN_WINDOW_BYTES=$LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES ;;
  esac
}

# WINDOW_FILE/LOCK_DIR are script-scope (not local) so this EXIT trap can
# still reach them after an abnormal exit mid-scan, since a trap
# referencing a local sees it already unset.
WINDOW_FILE=""
LOCK_DIR=""
trap 'rm -f "$WINDOW_FILE"; rmdir "$LOCK_DIR" 2>/dev/null' EXIT

# _scan_turn_count_cached TRANSCRIPT SCAN_STATE_FILE
# Sets TURN_COUNT to the dispatch's running total of assistant turns -- a
# transcript record carrying a usage block. This is the same shape
# nudge-handoff-near-context-cap.sh's own usage-block filter selects on.
# - Scans only the slice appended since SCAN_STATE_FILE's stored offset,
#   capped at MAX_SCAN_WINDOW_BYTES per attempt, so a backlog accumulated
#   across skipped sampling fires is read incrementally across several
#   sampled fires rather than in one unbounded tail|jq -s call.
# - Reuses _lib_advance_offset_past_complete_lines against a temp-file copy
#   of the bounded window. Materializing to a temp file is required because
#   the helper's fast path checks the transcript's true last byte, not the
#   window's boundary. A fire that catches the transcript mid-write, or
#   whose window cuts off mid-line, undercounts by at most that trailing
#   incomplete line.
# - The first scan for a dispatch (no stored offset yet) is subject to the
#   same per-attempt window cap as every later scan, not a one-time
#   unbounded read.
# - A same-session fire holds an exclusive lock (SCAN_STATE_FILE.lock) for
#   its whole read-scan-write sequence, so two concurrent fires can never
#   interleave their reads and writes. The loser's non-blocking mkdir fails
#   immediately, contributing nothing to this fire. The next sampled fire
#   that acquires the lock picks up where the winner left off.
# - A read failure (missing wc/tail/jq, unreadable file) or a failed lock
#   acquisition returns 1 and leaves TURN_COUNT at the last-known total.
# - A jq failure on the windowed slice (timeout, missing binary) leaves the
#   stored offset unchanged, so the next sampled fire retries the same
#   bounded window rather than a larger one.
# - A missing mktemp is treated as a jq failure: the offset and total are
#   written back unchanged and the function still returns 0.
# - A full window with no trailing newline and more transcript beyond it
#   means one record's own line exceeds the window; the offset
#   force-advances past it rather than freezing.
# - SCAN_STATE_FILE's third line marks that state so the next scan treats
#   its leading bytes as a stale record's tail, resyncing at the next
#   newline instead of feeding the fragment to jq.
# - Only that one record's turn is undercounted; nothing after it is.
_scan_turn_count_cached() {
  local transcript_path="$1" scan_state_file="$2"
  local lock_dir="${scan_state_file}.lock"
  _lib_capped_for 2 mkdir "$lock_dir" 2>/dev/null || return 1
  LOCK_DIR="$lock_dir"

  local stored_offset="" stored_total="" stored_misaligned=""
  if [ -f "$scan_state_file" ]; then
    { IFS= read -r stored_offset; IFS= read -r stored_total; IFS= read -r stored_misaligned; } \
      < "$scan_state_file" 2>/dev/null || true
  fi
  case "$stored_offset" in ''|*[!0-9]*) stored_offset=0 ;; esac
  case "$stored_total" in ''|*[!0-9]*) stored_total=0 ;; esac
  case "$stored_misaligned" in 1) stored_misaligned=1 ;; *) stored_misaligned=0 ;; esac
  TURN_COUNT="$stored_total"

  local current_size
  current_size=$(_lib_capped_for 2 wc -c < "$transcript_path" 2>/dev/null | tr -d '[:space:]')
  case "$current_size" in
    ''|*[!0-9]*)
      rmdir "$lock_dir" 2>/dev/null
      LOCK_DIR=""
      return 1
      ;;
  esac

  local scan_from="$stored_offset" misaligned="$stored_misaligned"
  if [ "$scan_from" -gt "$current_size" ] 2>/dev/null; then
    scan_from=0
    stored_total=0
    misaligned=0
  fi

  local scan_window_end="$current_size"
  if [ $(( current_size - scan_from )) -gt "$MAX_SCAN_WINDOW_BYTES" ] 2>/dev/null; then
    scan_window_end=$(( scan_from + MAX_SCAN_WINDOW_BYTES ))
  fi

  local new_turns=0 jq_ok=1 new_offset="$scan_from" new_misaligned="$misaligned"
  if [ "$scan_window_end" -gt "$scan_from" ] 2>/dev/null; then
    # GNU mktemp requires the XXXXXX suffix to substitute a temp name.
    # BSD mktemp accepts the same template but appends its own suffix
    # regardless of the template's content.
    WINDOW_FILE=$(_lib_capped_for 2 mktemp -t long-turn-nudge-scan.XXXXXX 2>/dev/null) || WINDOW_FILE=""
    if [ -n "$WINDOW_FILE" ]; then
      _lib_capped_for 2 tail -c +$((scan_from + 1)) "$transcript_path" 2>/dev/null \
        | head -c "$(( scan_window_end - scan_from ))" > "$WINDOW_FILE" 2>/dev/null
      local window_size
      window_size=$(_lib_capped_for 2 wc -c < "$WINDOW_FILE" 2>/dev/null | tr -d '[:space:]')
      case "$window_size" in ''|*[!0-9]*) window_size=0 ;; esac
      local window_offset=0
      window_offset=$(_lib_advance_offset_past_complete_lines "$WINDOW_FILE" 0 "$window_size")
      case "$window_offset" in ''|*[!0-9]*) window_offset=0 ;; esac

      if [ "$window_offset" -eq 0 ] 2>/dev/null; then
        # No newline in a full window with more transcript remaining means
        # an oversized record's own line. The legitimate mid-write case is
        # distinguishable because it would instead hit a short read at
        # current_size. Force-advance past this window rather than
        # re-reading it forever.
        if [ "$window_size" -eq "$(( scan_window_end - scan_from ))" ] 2>/dev/null \
          && [ "$scan_window_end" -lt "$current_size" ] 2>/dev/null; then
          new_offset="$scan_window_end"
          new_misaligned=1
        fi
      elif [ "$misaligned" -eq 1 ] 2>/dev/null; then
        # This window's first line is the tail of a record already skipped
        # as oversized -- resync past it without counting it. The rest of
        # this window is picked up fresh on the next sampled fire.
        local first_line_bytes
        first_line_bytes=$(_lib_capped_for 2 head -1 "$WINDOW_FILE" 2>/dev/null | wc -c | tr -d '[:space:]')
        case "$first_line_bytes" in ''|*[!0-9]*) first_line_bytes=0 ;; esac
        if [ "$first_line_bytes" -gt 0 ] 2>/dev/null; then
          new_offset=$(( scan_from + first_line_bytes ))
          new_misaligned=0
        fi
      else
        new_turns=$(head -c "$window_offset" "$WINDOW_FILE" 2>/dev/null \
          | _lib_capped_for 2 jq -s '[.[] | select(.message? and .message.usage)] | length' 2>/dev/null)
        case "$new_turns" in ''|*[!0-9]*) new_turns=0; jq_ok=0 ;; esac
        if [ "$jq_ok" -eq 1 ]; then
          new_offset=$(( scan_from + window_offset ))
        fi
      fi
      rm -f "$WINDOW_FILE" 2>/dev/null
      WINDOW_FILE=""
    fi
  fi

  TURN_COUNT=$(( stored_total + new_turns ))
  _lib_capped_for 2 mkdir -p "$(dirname "$scan_state_file")" 2>/dev/null || true

  printf '%s\n%s\n%s\n' "$new_offset" "$TURN_COUNT" "$new_misaligned" > "$scan_state_file" 2>/dev/null || true
  rmdir "$lock_dir" 2>/dev/null
  LOCK_DIR=""
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

# Only registered under PostToolBatch. An unrecognized HOOK_EVENT value
# degrades to that label rather than surfacing as an unlabeled misfire -- the
# same fallback nudge-handoff-near-context-cap.sh uses for this same
# untrusted jq-extracted field.
case "$HOOK_EVENT" in
  PostToolBatch) ;;
  *) HOOK_EVENT="PostToolBatch" ;;
esac

# SESSION_ID feeds every marker/state-file path below as a path component.
# "../" would escape MARKER_DIR, so fail the same way an empty id already does.
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
_lib_capped_for 2 mkdir -p "$MARKER_DIR" 2>/dev/null || true

# Already fired: this dispatch already got its one nudge, so every later
# fire exits here immediately with no counter upkeep, scan, or re-emit.
# FIRED_MARKER is a directory (see the mkdir claim below), so -e, not -f.
FIRED_MARKER="${MARKER_DIR}/${SESSION_ID}"
if [ -e "$FIRED_MARKER" ]; then
  exit 0
fi

# O_APPEND is atomic for a write this small, the same technique
# nudge-handoff-near-context-cap.sh's own IGNORED_MARKER uses.
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
_lib_capped_for 2 find "$MARKER_DIR" -maxdepth 1 -mtime +30 -delete 2>/dev/null || true

resolve_max_scan_window_bytes
SCAN_STATE_FILE="${MARKER_DIR}/${SESSION_ID}-scan"
_scan_turn_count_cached "$TRANSCRIPT_PATH" "$SCAN_STATE_FILE" || exit 0

resolve_threshold
if [ "$TURN_COUNT" -lt "$THRESHOLD" ] 2>/dev/null; then
  exit 0
fi

# Build the nudge JSON before taking the claim: a jq failure never touches
# FIRED_MARKER, so a later fire still gets to retry -- no rollback needed.
# shellcheck disable=SC2016 # single-quoted on purpose: $turns/$threshold are jq --argjson bindings, not shell variables; double-quoting would expand them in the shell before jq sees them. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
OUTPUT=$(_lib_capped_for 2 jq -n --arg hookEventName "$HOOK_EVENT" --argjson threshold "$THRESHOLD" --argjson turns "$TURN_COUNT" '{
  hookSpecificOutput: {
    hookEventName: $hookEventName,
    additionalContext: ("This subagent dispatch has reached " + ($turns|tostring) + " turns, past this repo'\''s measured outlier threshold (" + ($threshold|tostring) + " turns; see docs/design-decisions.md for the corpus basis). If the task is not close to done, consider stopping now and reporting back to the parent/orchestrator instead of continuing indefinitely -- a runaway dispatch is far cheaper to catch here than after many more turns. If genuinely close to done, ignore this and finish.")
  }
}' 2>/dev/null) && [ -n "$OUTPUT" ] || exit 0

# Atomic claim: mkdir is atomic, so only one of two near-simultaneous fires
# that both built OUTPUT can claim FIRED_MARKER. The loser discards its
# already-built OUTPUT and exits without emitting a duplicate nudge.
_lib_capped_for 2 mkdir "$FIRED_MARKER" 2>/dev/null || exit 0

printf 'nudged session=%s turns=%s threshold=%s event=%s\n' \
  "$SESSION_ID" "$TURN_COUNT" "$THRESHOLD" "$HOOK_EVENT" >> "$NUDGE_LOG" 2>/dev/null || true
printf '%s' "$OUTPUT"

exit 0
