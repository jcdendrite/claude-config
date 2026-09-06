#!/bin/bash
# hook-class: informational
# UserPromptSubmit hook: injects a one-shot nudge to run /error-mode-analysis
# when the current session's friction signals (hook denials + failed test
# runs + user-correction phrases, composited by
# transcript-analysis.py's friction-count subcommand) reach FRICTION_THRESHOLD.
#
# Session-intrinsic trigger, modeled on nudge-handoff-near-context-cap.sh:
# keys off a number computed from the current transcript, not off any
# external "is the engagement done" judgment.
#
# Paths below resolve under _lib_config_dir (the active Claude Code config
# directory: $CLAUDE_CONFIG_DIR if set, else ~/.claude).
#
# Nudge is one-shot per session: a marker file under
# .error-mode-nudge-fired.d/<session_id> is written on first fire and
# prevents repeated nudges — and repeated friction-count spawns — in the
# same session. The marker check runs before python3 is ever invoked.
#
# A second per-session state file under
# .error-mode-nudge-checkpoint.d/<session_id> persists a byte offset and
# running per-signal totals across every prompt in the session
# (written whether or not the nudge fires), so friction-count only rescans
# newly appended transcript lines instead of the whole file each prompt.
# The checkpoint directory gets a 30-day eviction sweep on every qualifying
# invocation (not gated on the nudge firing, since checkpoints are written
# on every such call); the marker directory's sweep stays tied to the fire
# path, since markers are themselves only ever written there.
# Known limitation: friction-count's denial dedup (`seen_denial_ids`) is
# local to each call and isn't persisted in the checkpoint, so a duplicate
# same-shape denial record straddling a checkpoint-read boundary can still
# be double-counted — accepted as low-likelihood (the duplicate would need
# to land exactly at a checkpoint read boundary) and low-impact (it can
# only inflate the composite toward the nudge threshold, never corrupt
# state).
#
# Opt-in: dormant by default. Touching .error-mode-nudge-enabled arms the
# hook; without that file, every invocation exits 0 before reading the
# transcript. See CONTRIBUTING.md for how to enable it.
#
# Fail-open everywhere: any unexpected error (missing python3, python3 older
# than 3.11, non-integer friction-count output, a friction-count timeout, an
# unresolvable config dir) exits 0 with no stdout so the hook never blocks a
# user prompt.
#
# Log file: .error-mode-nudge.log records one event type:
#   nudged session=<id> friction=<n>  — threshold crossed, nudge emitted
#
# strict mode omitted deliberately: this hook must never block prompts (exit 0
# on all paths); strict mode could cause unexpected early exits from the || true
# guards that protect against unwritable dirs and missing executables.

INPUT=$(cat 2>/dev/null)

# 1. Source _lib.sh and resolve the active config directory before any
# ~/.claude-rooted path is built. Fail-open per this hook's own contract
# (see header): an unresolvable config dir just leaves the nudge dormant.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
CONFIG_DIR=$(_lib_config_dir) || exit 0

# Extract all four fields in a single jq pass to avoid four separate subshell
# spawns. Sequential reads from the jq output: each field on its own line
# handles empty values and paths with spaces correctly. Pre-initialize to ""
# so a failed read (e.g. jq unavailable or INPUT invalid) leaves empty
# strings rather than unbound variables. The || true preserves fail-open
# semantics.
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
    | _lib_jq -r '(.session_id // ""),(.agent_type // ""),(.permission_mode // ""),(.transcript_path // "")' \
    2>/dev/null
) 2>/dev/null || true

# 2. Opt-in gate: dormant unless the contributor has explicitly armed the
# hook. Absent this file, every invocation exits here before doing any
# transcript work.
if [ ! -f "$CONFIG_DIR/.error-mode-nudge-enabled" ]; then
  exit 0
fi

# 3. Subagent gate: only nudge in the main session, not in subagents.
if [ -n "$AGENT_TYPE" ]; then
  exit 0
fi

# 4. Plan-mode gate: nudging in plan mode would interrupt planning flow.
if [ "$PERMISSION_MODE" = "plan" ]; then
  exit 0
fi

# 5. Require a session id and a readable transcript file. SESSION_ID feeds
# FIRED_MARKER and CHECKPOINT_FILE below as a path component ("../" would
# escape their marker directories), so an id that is not a safe single path
# component is rejected the same way an empty one already is.
if [ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  exit 0
fi

# 6. Already fired: suppress repeated nudges without spawning python3 at all —
# this is the whole-session hot-path fix (the marker check gates the spawn,
# not just the emitted output).
MARKER_DIR="$CONFIG_DIR/.error-mode-nudge-fired.d"
FIRED_MARKER="${MARKER_DIR}/${SESSION_ID}"
if [ -f "$FIRED_MARKER" ]; then
  exit 0
fi

# 7. python3 preflight (bash-side fail-open). transcript-analysis.py does
# `from datetime import UTC` at module top, which raises ImportError on
# python3 < 3.11 before any subcommand runs — a script-side guard is
# impossible, so the version compare is delegated to python3 itself rather
# than hand-parsing a `python3 --version` string.
if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  exit 0
fi

# 8. Run friction-count with a bounded wall-clock timeout. 10s is grounded on
# measurement: the largest real transcript found under ~/.claude/projects on
# the implementing machine (6.4 MB, 2656 lines) completed in ~0.18s; a 10x
# synthetic (64 MB, 26560 lines) completed in ~1.3s — 10s leaves ample
# headroom over both realistic transcript sizes and slower disks/CI.
#
# --checkpoint turns the per-fire cost into O(lines appended since the last
# prompt) instead of a full-transcript reparse every prompt: the checkpoint
# dir is created (and persists) here regardless of whether the nudge fires,
# since it must exist before the first call for the incremental scan to have
# somewhere to write. Its 30-day eviction sweep runs right here too, on every
# qualifying invocation — unlike the fired-marker dir (evicted only in the
# fire block below), checkpoint files are written on every call that reaches
# this point, so gating eviction on the rare fire event would let them
# accumulate unboundedly for sessions that never cross FRICTION_THRESHOLD.
CHECKPOINT_DIR="$CONFIG_DIR/.error-mode-nudge-checkpoint.d"
CHECKPOINT_FILE="${CHECKPOINT_DIR}/${SESSION_ID}"
mkdir -p "$CHECKPOINT_DIR" 2>/dev/null || true
find "$CHECKPOINT_DIR" -maxdepth 1 -mtime +30 -delete 2>/dev/null || true
FRICTION_COUNT=$(_lib_capped_for 10 python3 "$CONFIG_DIR/scripts/transcript-analysis.py" \
  friction-count --transcript "$TRANSCRIPT_PATH" --checkpoint "$CHECKPOINT_FILE" 2>/dev/null)
if ! [[ "$FRICTION_COUNT" =~ ^[0-9]+$ ]]; then
  exit 0
fi

# Threshold: 99th percentile of the composite friction-signal distribution,
# backtested against 654 real historical sessions under ~/.claude/projects on
# the implementing machine (p99 ≈ 11.47, p99.5 ≈ 12.74), rounded up to sit
# just above p99 so ordinary TDD churn (a few red test runs, a couple of
# corrections) does not trip it. See the PR description for the full
# per-signal distribution.
FRICTION_THRESHOLD=12

if ! [ "$FRICTION_COUNT" -ge "$FRICTION_THRESHOLD" ] 2>/dev/null; then
  exit 0
fi

# 9. Fire: emit nudge, write marker, and log the event.
NUDGE_LOG="$CONFIG_DIR/.error-mode-nudge.log"
mkdir -p "$MARKER_DIR" 2>/dev/null || true
# Evict stale markers from one-shot runs that skipped SessionEnd cleanup.
# Gated on fire (unlike the checkpoint dir's sweep above) because markers
# are themselves only ever written here.
find "$MARKER_DIR" -maxdepth 1 -mtime +30 -delete 2>/dev/null || true
printf 'nudged session=%s friction=%s\n' "$SESSION_ID" "$FRICTION_COUNT" >> "$NUDGE_LOG" 2>/dev/null || true
touch "$FIRED_MARKER" 2>/dev/null || true

_lib_jq -n '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: "This session has accumulated a number of mechanical friction signals (hook denials, failed test runs, user corrections). If the current body of work is close to delivered, suggest running /error-mode-analysis to the user — it buckets what went wrong by which pipeline layer caught it, so the lessons carry forward. If the session is still mid-task, ignore this and continue."
  }
}' 2>/dev/null || true

exit 0
