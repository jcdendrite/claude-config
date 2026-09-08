#!/bin/bash
# hook-class: informational
# SessionStart hook: bootstrap a session_id ↔ claude-PID mapping at session
# start so downstream skills running as Bash tool calls can learn their
# own session_id.
#
# Why: the require-respond-pr.sh gate's bypass marker is keyed by session_id
# (the documented Claude Code hook payload field). The marker writer is the
# /respond-pr skill, which runs as Bash tool calls. Bash tool calls don't
# see session_id in env or on stdin — only hooks do. This hook captures
# session_id once at session start and exposes it at a path the skill can
# look up.
#
# Lookup contract on the skill side:
#   The Bash tool's $PPID is the claude main process PID. The skill reads
#   sessions/$PPID under the resolved config directory (_lib_config_dir:
#   $CLAUDE_CONFIG_DIR if set, else ~/.claude) to learn its session_id.
#
# Deriving claude_pid from inside this hook:
#   This hook's own $PPID is the claude process. Claude Code also exports
#   $CLAUDE_PID into hook environments; it is accepted only when it equals
#   $PPID or $PPID's immediate parent (a shim between claude and this hook),
#   so an unrelated live process can't be named. An invalid or out-of-bound
#   $CLAUDE_PID falls back to $PPID rather than aborting the write.
#
# This hook is also registered on SubagentStart, not just SessionStart, so
# it runs once per subagent launch in addition to once per session.
#
# Failure mode: every step exits 0 (a SessionStart hook that fails-closed
# would block session startup, which is worse than a delayed Step 0
# failure in the skill). Each failure path emits a one-line diagnostic to
# stderr — visible in the user's terminal, not added to Claude's context
# (which is stdout). When the lookup file isn't written, the /respond-pr
# skill's Step 0 fails loudly with a clear message; the stderr trail here
# is the upstream signal explaining why. If both the $PPID and $CLAUDE_PID
# candidates are unusable, no lookup file is written at all.
#
# No self-sweep, unlike other retired-destructor replacements: this file is
# rewritten at every SessionStart and SubagentStart under the resolved PID,
# so a session alive past any time-based watermark would still need its own
# entry — an mtime sweep can't distinguish "stale" from "long-lived but
# live." Growth is bounded only by PID reuse, not a time watermark.

INPUT=$(cat 2>/dev/null)
if [ -z "$INPUT" ]; then
  echo "[capture-session-id] empty stdin; lookup file not written; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  echo "[capture-session-id] could not source _lib.sh; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty' 2>/dev/null)
if [ -z "$SESSION_ID" ]; then
  echo "[capture-session-id] no session_id in payload; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

# SESSION_ID feeds the active.d rewrite loop below as a path component ("../"
# would escape the resolved config dir's .*-active.d/); fail the same way an
# empty id already does rather than sanitizing further.
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  echo "[capture-session-id] session_id is not a valid path component; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

CONFIG_DIR=$(_lib_config_dir) || {
  echo "[capture-session-id] could not resolve config dir; respond-pr skill will fail at Step 0" >&2
  exit 0
}

CLAUDE_PID=$(_lib_hook_claude_pid)
if [ -z "$CLAUDE_PID" ]; then
  echo "[capture-session-id] could not resolve claude PID from \$PPID ($PPID) or \$CLAUDE_PID; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

# Pinned TZ/locale: lstart's rendering is ambient-locale-sensitive, and the
# writer (this sh shim) and reader (marker.sh, run from the user's profile
# shell) can differ; pinning both to the same values makes them comparable.
# Correctness never depends on BSD/GNU rendering the same bytes — writer and
# reader always run on the same host, each comparing against its own output.
CLAUDE_PID_START=$(TZ=UTC LC_ALL=C ps -o lstart= -p "$CLAUDE_PID" 2>/dev/null)
if [ -z "$CLAUDE_PID_START" ]; then
  echo "[capture-session-id] could not resolve start time for claude PID $CLAUDE_PID; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

if ! mkdir -p "$CONFIG_DIR/sessions" 2>/dev/null; then
  echo "[capture-session-id] could not create $CONFIG_DIR/sessions; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

if ! printf '%s\n%s\n' "$SESSION_ID" "$CLAUDE_PID_START" > "$CONFIG_DIR/sessions/$CLAUDE_PID" 2>/dev/null; then
  echo "[capture-session-id] could not write lookup file $CONFIG_DIR/sessions/$CLAUDE_PID; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

# Update any active.d entries for this session to the current PID. This
# handles --continue: the session resumes under a new PID, but any active
# markers written before the restart still carry the old PID. Hooks use
# kill -0 <stored-pid> for liveness, so stale PIDs would cause them to
# evict live markers. Rewriting here keeps bypass working after restart.
for _active_dir in "$CONFIG_DIR"/.*-active.d; do
  [ -d "$_active_dir" ] || continue
  _entry="$_active_dir/$SESSION_ID"
  if [ -f "$_entry" ]; then
    printf '%s\n' "$CLAUDE_PID" > "$_entry" 2>/dev/null || true
  fi
done

exit 0
