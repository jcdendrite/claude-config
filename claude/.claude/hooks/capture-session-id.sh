#!/bin/bash
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
#   ~/.claude/sessions/$PPID to learn its session_id.
#
# Deriving claude_pid from inside this hook:
#   This hook is invoked through a transient `sh` shim, so its $PPID is
#   that shim, not claude. claude is the shim's parent. POSIX
#   `ps -o ppid= -p $PPID` walks up one level. Works on Linux, macOS, WSL.
#
# Failure mode: every step exits 0 (a SessionStart hook that fails-closed
# would block session startup, which is worse than a delayed Step 0
# failure in the skill). Each failure path emits a one-line diagnostic to
# stderr — visible in the user's terminal, not added to Claude's context
# (which is stdout). When the lookup file isn't written, the /respond-pr
# skill's Step 0 fails loudly with a clear message; the stderr trail here
# is the upstream signal explaining why.

INPUT=$(cat 2>/dev/null)
if [ -z "$INPUT" ]; then
  echo "[capture-session-id] empty stdin; lookup file not written; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
if [ -z "$SESSION_ID" ]; then
  echo "[capture-session-id] no session_id in payload; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

CLAUDE_PID=$(ps -o ppid= -p "$PPID" 2>/dev/null | tr -d ' ')
if [ -z "$CLAUDE_PID" ]; then
  echo "[capture-session-id] could not resolve claude PID via 'ps -o ppid= -p $PPID'; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

if ! mkdir -p "$HOME/.claude/sessions" 2>/dev/null; then
  echo "[capture-session-id] could not create $HOME/.claude/sessions; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

if ! printf '%s\n' "$SESSION_ID" > "$HOME/.claude/sessions/$CLAUDE_PID" 2>/dev/null; then
  echo "[capture-session-id] could not write lookup file $HOME/.claude/sessions/$CLAUDE_PID; respond-pr skill will fail at Step 0" >&2
  exit 0
fi

# Update any active.d entries for this session to the current PID. This
# handles --continue: the session resumes under a new PID, but any active
# markers written before the restart still carry the old PID. Hooks use
# kill -0 <stored-pid> for liveness, so stale PIDs would cause them to
# evict live markers. Rewriting here keeps bypass working after restart.
for _active_dir in "$HOME/.claude"/.*-active.d; do
  [ -d "$_active_dir" ] || continue
  _entry="$_active_dir/$SESSION_ID"
  if [ -f "$_entry" ]; then
    printf '%s\n' "$CLAUDE_PID" > "$_entry" 2>/dev/null || true
  fi
done

exit 0
