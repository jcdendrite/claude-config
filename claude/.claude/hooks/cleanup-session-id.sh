#!/bin/bash
# SessionEnd hook: delete the session_id ↔ claude-PID lookup file that
# capture-session-id.sh wrote for this session. It is the destructor
# capture-session-id.sh never had — without it, ~/.claude/sessions/ gains
# one bare-PID file per session start, resume, and one-shot run and grows
# without bound.
#
# Content-match guard, not a blind `rm`:
#   /clear ends one session and starts another in the *same* claude
#   process — same claude-PID, new session_id. Both this SessionEnd hook
#   (old id) and the SessionStart capture (new id) touch
#   sessions/<claude-pid>, and Claude Code does not order them. The file
#   is deleted only when its content still equals the *ending* session's
#   session_id:
#     - SessionEnd first  -> file holds the old id -> match -> deleted,
#       then SessionStart rewrites it for the successor session.
#     - SessionStart first -> file already holds the new id -> no match
#       -> file kept (it now belongs to the live successor session).
#
# PID resolution mirrors capture-session-id.sh: this hook runs through a
# transient `sh` shim, so `ps -o ppid= -p $PPID` walks up one level to
# the claude process — the same PID the lookup file is keyed by.
#
# Fail-open: SessionEnd cannot block, and a missed cleanup is harmless —
# the file lingers (~40 bytes) and self-heals when capture-session-id.sh
# overwrites the PID on reuse. Every path exits 0; the normal no-match
# path stays silent (a no-match is expected, e.g. the /clear ordering),
# with one diagnostic only on PID-resolution failure for parity with the
# sibling hook. `claude -p` one-shot invocations do not fire SessionEnd,
# so each one leaks one bare-PID file at one-shot rate (self-heals on
# PID reuse).

INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

CLAUDE_PID=$(ps -o ppid= -p "$PPID" 2>/dev/null | tr -d ' ')
if [ -z "$CLAUDE_PID" ]; then
  echo "[cleanup-session-id] could not resolve claude PID via 'ps -o ppid= -p $PPID'; lookup file not cleaned up" >&2
  exit 0
fi

LOOKUP_FILE="$HOME/.claude/sessions/$CLAUDE_PID"
if [ -f "$LOOKUP_FILE" ]; then
  CURRENT=$(tr -d '[:space:]' < "$LOOKUP_FILE" 2>/dev/null)
  if [ "$CURRENT" = "$SESSION_ID" ]; then
    rm -f "$LOOKUP_FILE" 2>/dev/null
  fi
fi

exit 0
