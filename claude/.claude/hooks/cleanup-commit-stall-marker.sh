#!/bin/bash
# hook-class: informational
# SessionEnd hook: remove the per-session state file written by
# advance-past-commit-stall.sh when the session ends.
#
# Deletes the state file so a stale entry does not suppress a future
# session's first legitimate fire (recycled session ids are theoretically
# possible). Also sweeps entries older than 30 days from the whole state
# dir on every run — `claude -p` one-shot invocations never fire
# SessionEnd, so their state files would otherwise accumulate without
# bound; this destructor is the only place that runs often enough to catch
# them, since the fired hook itself only touches its own session's file.
#
# Fail-open: SessionEnd hooks must never block. Every path exits 0.

INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

# SESSION_ID feeds the rm -f target below as a path component ("../" would
# escape STATE_DIR); fail the same way an empty id already does.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
_lib_valid_session_id_component "$SESSION_ID" || exit 0

STATE_DIR="$HOME/.claude/.commit-stall-block.d"

rm -f "${STATE_DIR}/${SESSION_ID}" 2>/dev/null || true

# Sweep confined to STATE_DIR itself (not recursive into anything a symlink
# might point at) and only when it is an actual directory — a symlinked or
# absent STATE_DIR is left alone rather than followed or created.
if [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ]; then
  find "$STATE_DIR" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true
fi

exit 0
