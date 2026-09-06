#!/bin/bash
# hook-class: informational
# SessionEnd hook: records that this Claude Code process shut down
# gracefully, so post-crash-sessions.py can tell a deliberate clean exit
# apart from a crash once the process is dead. See
# .claude/plans/detect-clean-exit-vs-crash.md for the full design.
#
# Record shape: writes <config-dir>/session-end-records/<claude-pid> as
# {"sessionId": "<id>", "reason": <SessionEnd reason, or null if absent>}.
# No timestamp field -- the file's own mtime is the record time, matching
# how post-crash-sessions.py's other on-disk sources date themselves.
# `<claude-pid>` is resolved the same way capture-session-id.sh resolves its
# own PID (_lib_hook_claude_pid): this hook's own $PPID, optionally
# overridden by $CLAUDE_PID when Claude Code exports it and it names the
# same process or its immediate parent.
#
# Known gaps:
#   `claude -p` (headless) skips SessionEnd entirely, so a headless run that
#   exits cleanly never writes a record.
#   Whether SessionEnd fires on a hard kill (SIGKILL, OOM, reboot) is
#   undocumented upstream. It is verified only by the plan's manual
#   verification steps, not by any automated check that reruns on every
#   Claude Code version upgrade.
#
# Failure mode: every failure path exits 0, with a one-line diagnostic to
# stderr only.
#
# Per-fire cost: includes a conditional `ps` shellout (inside
# _lib_hook_claude_pid, when $CLAUDE_PID is set and numeric) plus jq/find,
# but the exact call count is visible by reading the script below.
# SessionEnd's default execution budget is asserted to be short (~1.5s) by
# this design but is not cited from any Anthropic documentation this repo
# could locate; treat the per-fire cost above as a reason for caution, not a
# proven-safe margin.
#
# Self-sweep: after a successful write, deletes any file in its own records
# directory older than 30 days -- this repo's established idiom for
# hook-owned state directories (docs/error-mode-nudge.md). Swept after the
# write, not before, so a sweep failure can never cost the record just
# written.

INPUT=$(cat 2>/dev/null)
if [ -z "$INPUT" ]; then
  echo "[record-session-end] empty stdin; no SessionEnd record written" >&2
  exit 0
fi

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
REASON=$(printf '%s\n' "$INPUT" | jq -r '.reason // empty' 2>/dev/null)
if [ -z "$SESSION_ID" ]; then
  echo "[record-session-end] no session_id in payload; no SessionEnd record written" >&2
  exit 0
fi

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  echo "[record-session-end] could not source _lib.sh; no SessionEnd record written" >&2
  exit 0
fi
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  echo "[record-session-end] session_id is not a valid path component; no SessionEnd record written" >&2
  exit 0
fi

CONFIG_DIR=$(_lib_config_dir) || {
  echo "[record-session-end] could not resolve config dir; no SessionEnd record written" >&2
  exit 0
}

CLAUDE_PID=$(_lib_hook_claude_pid)
if [ -z "$CLAUDE_PID" ]; then
  echo "[record-session-end] could not resolve claude PID from \$PPID ($PPID) or \$CLAUDE_PID; no SessionEnd record written" >&2
  exit 0
fi

RECORDS_DIR="$CONFIG_DIR/session-end-records"
if ! mkdir -p "$RECORDS_DIR" 2>/dev/null; then
  echo "[record-session-end] could not create $RECORDS_DIR; no SessionEnd record written" >&2
  exit 0
fi

RECORD_JSON=$(jq -n --arg sid "$SESSION_ID" --arg reason "$REASON" \
  '{sessionId: $sid, reason: (if $reason == "" then null else $reason end)}' 2>/dev/null)
if [ -z "$RECORD_JSON" ]; then
  echo "[record-session-end] could not build record JSON; no SessionEnd record written" >&2
  exit 0
fi

if ! printf '%s\n' "$RECORD_JSON" > "$RECORDS_DIR/$CLAUDE_PID" 2>/dev/null; then
  echo "[record-session-end] could not write record file $RECORDS_DIR/$CLAUDE_PID" >&2
  exit 0
fi

find "$RECORDS_DIR" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true

exit 0
