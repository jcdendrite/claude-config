#!/bin/bash
# hook-class: informational
set -uo pipefail
# PostToolUse: when the handoff skill loads, activate its active-bypass
# marker immediately.
# This closes the bootstrap window where nudge-handoff-near-context-cap.sh's
# hard block could otherwise fire on the very batch that loads /handoff,
# before the skill's own `marker.sh activate handoff` step has had a chance
# to run.
# See docs/handoff-nudge.md's "Recovering from a hard block" section for the
# marker's read side.

INPUT=$(cat)
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" = "Skill" ] || exit 0

# Single jq pass for all three fields, mirroring
# nudge-handoff-near-context-cap.sh's own multi-field extraction.
SKILL_NAME=""
AGENT_TYPE=""
SESSION_ID=""
{
  IFS= read -r SKILL_NAME
  IFS= read -r AGENT_TYPE
  IFS= read -r SESSION_ID
} < <(
  printf '%s\n' "$INPUT" \
    | jq -r '(.tool_input.skill // ""),(.agent_type // ""),(.session_id // "")' 2>/dev/null
) 2>/dev/null || true

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# SESSION_ID feeds the drift-signal path below as a path component ("../"
# would escape its marker directory); fail the same way an empty id already
# does.
[ -n "$SESSION_ID" ] || exit 0
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# An unresolvable config dir leaves nowhere to log drift or find marker.sh.
CONFIG_DIR=$(_lib_config_dir) || exit 0
DRIFT_DIR="$CONFIG_DIR/.activate-handoff-bypass-drift.d"
# .activate-handoff-bypass-drift.d/ and .activate-handoff-bypass.log
# accumulate one entry per session with no eviction path, accepted at current
# single-operator scale -- same posture as log-routing-read.sh's
# .plan-review-pending-read.d/ (see that file's header).

# Subagent guard: marker.sh resolves the session by walking process
# ancestors to the Claude main process, so a subagent-issued Skill(handoff)
# call would disarm the *parent* session's hard block rather than its own.
# The nudge hook's own subagent gate means the block never fires in a
# subagent anyway, so there is nothing here to suppress.
[ -z "$AGENT_TYPE" ] || exit 0

if [ -z "$SKILL_NAME" ]; then
  # Schema drift here means a well-formed Skill tool_input carrying no
  # recognizable skill-name field at all.
  # This distinguishes "the field was renamed upstream" from "this call is
  # for a different skill" -- both would otherwise produce the same silent
  # exit 0.
  # Observability only: a per-session-deduped log line that nothing alerts
  # on, mirroring nudge-handoff-near-context-cap.sh's own DRIFT_MARKER.
  DRIFT_MARKER="$DRIFT_DIR/$SESSION_ID"
  if [ ! -f "$DRIFT_MARKER" ]; then
    mkdir -p "$DRIFT_DIR" 2>/dev/null || true
    printf 'schema-drift session=%s\n' "$SESSION_ID" \
      >> "$CONFIG_DIR/.activate-handoff-bypass.log" 2>/dev/null || true
    touch "$DRIFT_MARKER" 2>/dev/null || true
  fi
  exit 0
fi

# Match the final component after the last ':' or '/' -- stow-source
# copies render directory-qualified (.claude/worktrees/<branch>/claude:name)
# and plugin skills render as plugin:name, per claude/.claude/CLAUDE.md.
LABEL="${SKILL_NAME##*:}"
LABEL="${LABEL##*/}"
[ "$LABEL" = "handoff" ] || exit 0

# _lib_capped_for (claude/.claude/hooks/_lib.sh) sends SIGTERM only, with no
# -k/--kill-after force-kill, and falls back to fully uncapped execution when
# neither timeout nor gtimeout is on PATH. PostToolUse runs synchronously, so
# a wedged marker.sh call under that gap blocks the whole turn with no retry
# path.
if ! _lib_capped_for 2 "$CONFIG_DIR/scripts/marker.sh" activate handoff >/dev/null 2>&1; then
  # Observability only, same posture as the schema-drift signal above: a
  # per-session-deduped log line under a distinct tag distinguishes a
  # marker.sh failure or cap-timeout from every other silent exit-0 path.
  # Never turns into a retry or a deny -- the hook still always exits 0.
  FAILURE_MARKER="$DRIFT_DIR/$SESSION_ID-activation-failed"
  if [ ! -f "$FAILURE_MARKER" ]; then
    mkdir -p "$DRIFT_DIR" 2>/dev/null || true
    printf 'activation-failed session=%s\n' "$SESSION_ID" \
      >> "$CONFIG_DIR/.activate-handoff-bypass.log" 2>/dev/null || true
    touch "$FAILURE_MARKER" 2>/dev/null || true
  fi
fi

exit 0
