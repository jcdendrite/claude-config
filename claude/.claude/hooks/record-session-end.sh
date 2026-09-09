#!/bin/bash
# hook-class: informational
set -uo pipefail
# SessionEnd hook: records that this Claude Code process shut down
# gracefully, so post-crash-sessions.py can tell a deliberate clean exit
# apart from a crash once the process is dead. See
# .claude/plans/detect-clean-exit-vs-crash.md for the full design.
#
# Record shape: writes <config-dir>/session-end-records/<claude-pid> as
# {"sessionId": "<id>", "reason": <SessionEnd reason, or null if absent>}.
# No timestamp field -- the file's own mtime is the record time.
# `<claude-pid>` resolution's two-way fallback (own $PPID, optionally
# overridden by $CLAUDE_PID) is documented in full at _lib_hook_claude_pid's
# own header in _lib.sh.
#
# Known gaps:
#   `claude -p` (headless) skips SessionEnd entirely, so a headless run that
#   exits cleanly never writes a record.
#   Whether SessionEnd fires on a hard kill (SIGKILL, OOM, reboot) is
#   undocumented upstream and verified only by manual testing (see
#   .claude/plans/detect-clean-exit-vs-crash.md).
#
# Failure mode: every failure path exits 0, with a one-line diagnostic to
# stderr only.
#
# Per-fire cost: a conditional `ps` shellout (inside _lib_hook_claude_pid,
# when $CLAUDE_PID is set and numeric) plus jq and find. See the script
# below for the exact call sequence.
#
# SessionEnd hooks share a 1.5s default execution budget across every
# SessionEnd hook registered for the session (confirmed:
# code.claude.com/docs/en/hooks, "Common fields"/SessionEnd section). A
# per-hook `timeout` field raises that budget, up to a 60s ceiling.
# settings.json sets `"timeout": 10` on this hook's registration for that
# reason. The self-sweep below is capped at 2s. The per-fire cost above
# is comfortably sub-second, so 10 gives several times headroom over the
# realistic ~2-3s worst case without approaching the 60s ceiling.
#
# Self-sweep: after a successful write, deletes any file in its own records
# directory older than 30 days. That's this repo's established idiom for
# hook-owned state directories (docs/error-mode-nudge.md). It runs
# synchronously after the write, so a sweep failure can't cost the record
# just written.

INPUT=$(cat 2>/dev/null)
if [ -z "$INPUT" ]; then
  echo "[record-session-end] empty stdin; no SessionEnd record written" >&2
  exit 0
fi

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  echo "[record-session-end] could not source _lib.sh; no SessionEnd record written" >&2
  exit 0
fi

SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty' 2>/dev/null)
REASON=$(printf '%s\n' "$INPUT" | _lib_jq -r '.reason // empty' 2>/dev/null)
if [ -z "$SESSION_ID" ]; then
  echo "[record-session-end] no session_id in payload; no SessionEnd record written" >&2
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

# shellcheck disable=SC2016 # single-quoted on purpose: $sid/$reason are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them.
RECORD_JSON=$(_lib_jq -n --arg sid "$SESSION_ID" --arg reason "$REASON" \
  '{sessionId: $sid, reason: (if $reason == "" then null else $reason end)}' 2>/dev/null)
if [ -z "$RECORD_JSON" ]; then
  echo "[record-session-end] could not build record JSON; no SessionEnd record written" >&2
  exit 0
fi

if ! printf '%s\n' "$RECORD_JSON" > "$RECORDS_DIR/$CLAUDE_PID" 2>/dev/null; then
  echo "[record-session-end] could not write record file $RECORDS_DIR/$CLAUDE_PID" >&2
  exit 0
fi

_lib_capped_for 2 find "$RECORDS_DIR" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true

exit 0
