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

# Garbage-collect stale bare-PID files from ~/.claude/sessions/.
#
# Throttled to once per 24 hours via ~/.claude/.sessions-gc-stamp so the
# sweep does not run on every SubagentStart in an agentic session. The
# stamp lives outside sessions/ to keep that directory purely bare-PID
# files plus Claude Code's <pid>.json sidecars.
#
# Fail-open: every find/kill/rm non-zero exit is swallowed. The sweep is
# best-effort tidiness — a failure must never propagate to the hook's
# top-level exit and must never block session startup.
_gc_stamp="$HOME/.claude/.sessions-gc-stamp"
_sessions_dir="$HOME/.claude/sessions"

# Check whether a fresh stamp exists (mtime under 24 hours old).
# `find FILE -mtime -1` exits 0 and prints the path when mtime < 24h.
_stamp_fresh=$(find "$_gc_stamp" -mtime -1 2>/dev/null)
if [ -z "$_stamp_fresh" ]; then
  # Stamp is absent or stale — run the sweep, then refresh the stamp.
  (
    # Precompute sets for the age-based checks. The directory is flat so
    # no -maxdepth is needed. Both find calls are best-effort; failure
    # produces an empty variable, which means those entries are simply
    # skipped rather than deleted — safe and fail-open.
    _old_files=$(find "$_sessions_dir" -type f -mtime +30 2>/dev/null || true)
    _fresh_files=$(find "$_sessions_dir" -type f -mmin -5 2>/dev/null || true)

    for _session_file in "$_sessions_dir"/*; do
      # Guard against empty-directory no-match (nullglob not set).
      [ -e "$_session_file" ] || continue

      _b=${_session_file##*/}

      # Only bare-PID filenames pass (excludes <pid>.json sidecars and
      # anything else Claude Code may write). This is the single gate
      # every deletion must pass.
      [[ "$_b" =~ ^[0-9]+$ ]] || continue

      # Never touch the lookup file just written by this run.
      [ "$_b" = "$CLAUDE_PID" ] && continue

      # Skip files younger than 5 minutes (floor against clock skew and
      # a just-written sibling file whose mtime we can't perfectly order).
      # Match whole newline-bounded paths: an unbounded substring match
      # would let PID 123 match a list containing PID 1234.
      case $'\n'"$_fresh_files"$'\n' in
        *$'\n'"$_session_file"$'\n'*) continue ;;
      esac

      # Delete: superseded prior incarnation of this session (Leak A).
      # `$(<file)` reads without a subprocess; a vanished file yields an
      # empty string, and the subshell's 2>/dev/null swallows the notice.
      _file_content=$(<"$_session_file")
      if [ "$_file_content" = "$SESSION_ID" ]; then
        rm -f "$_session_file" 2>/dev/null || true
        continue
      fi

      # Delete: mapped process is gone (Leak B, common case).
      if ! kill -0 "$_b" 2>/dev/null; then
        rm -f "$_session_file" 2>/dev/null || true
        continue
      fi

      # Delete: mtime older than 30 days covers PID-reuse where kill -0
      # passes for an unrelated live process (Leak B, ceiling). Match
      # whole newline-bounded paths so PID 123 cannot match PID 1234.
      case $'\n'"$_old_files"$'\n' in
        *$'\n'"$_session_file"$'\n'*) rm -f "$_session_file" 2>/dev/null || true ;;
      esac
    done
  ) 2>/dev/null || true

  # Refresh the throttle stamp regardless of sweep outcome. A failed
  # write is harmless — the sweep is idempotent and simply re-runs next
  # session — but emit a diagnostic per this hook's stderr convention.
  touch "$_gc_stamp" 2>/dev/null || \
    echo "[capture-session-id] could not write GC throttle stamp $_gc_stamp; sweep will re-run next session" >&2
fi

exit 0
