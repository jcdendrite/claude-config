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
# Failure mode: every step exits 0 silently. If session_id or claude_pid
# can't be determined, the lookup file isn't written; the skill will then
# fail loudly when it tries to read it. A SessionStart hook must never
# block session startup, even on internal error.

INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

CLAUDE_PID=$(ps -o ppid= -p "$PPID" 2>/dev/null | tr -d ' ')
[ -z "$CLAUDE_PID" ] && exit 0

mkdir -p "$HOME/.claude/sessions" 2>/dev/null || exit 0
printf '%s\n' "$SESSION_ID" > "$HOME/.claude/sessions/$CLAUDE_PID" 2>/dev/null

exit 0
