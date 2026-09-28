#!/bin/bash
# _capped-for-lib.sh — portable timeout/gtimeout wrapper for render-settings.sh
# invocations.
#
# Sourced by ensure-settings-render.sh and install.sh's render-settings-invoke
# marker block. Not executable on its own; source it, do not invoke it
# directly.
#
# Probes timeout(1) first, then gtimeout(1) -- Homebrew coreutils'
# g-prefixed name.
# `-k 2` sends SIGKILL 2s after the SIGTERM deadline if the child hasn't
# exited yet, the same escalation _lib_capped_for uses in
# claude/.claude/hooks/_lib.sh.
# A child stuck in an uninterruptible (D-state) syscall -- e.g. a
# network-mounted $HOME stalled on I/O -- doesn't respond to either signal,
# so this wrapper only bounds a CPU-bound hang or a child that honors
# SIGTERM.
# Falls back to running the command fully uncapped, silently, when neither
# timeout nor gtimeout is on PATH (stock macOS included).
# Each caller's own contract governs what happens next on a genuine
# failure; this wrapper only ever affects whether a hang gets bounded.
_capped_for() {
  local seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 2 "$seconds" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout -k 2 "$seconds" "$@"
  else
    "$@"
  fi
}
