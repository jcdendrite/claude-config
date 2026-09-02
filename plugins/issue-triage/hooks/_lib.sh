#!/bin/bash
# Trimmed shared helper library for the issue-triage plugin's hook.
# Source this file (do NOT invoke it). Contains only the helpers
# deny-gh-mutation-during-triage.sh needs: tool-input parsing, deny-message
# encoding, config-dir resolution, and the session-scoped active-marker
# liveness check. No git helpers, no worktree-enforcement helpers — this
# hook is a Bash-command gate, not a marker-write gate.
# Copied and trimmed from claude/.claude/hooks/_lib.sh; see
# plugins/plugin-semver/hooks/_lib.sh for the precedent of this pattern.

# Backstop against a hung jq (~5s, not a per-fire latency budget).
# Cites guard-settings-session-keys.sh's _lib_capped 5s precedent.
# Probes timeout(1) then gtimeout(1) (Homebrew coreutils' g-prefixed name),
# falling back to bare jq only when neither is on PATH (stock macOS with no
# coreutils installed).
# Security implication: on a machine with neither binary, a stalled or
# replaced jq binary can hold a gate hook open indefinitely. The harness's
# own hook timeout (if any) then governs — not this wrapper.
_lib_jq() {
  _lib_capped_for 5 jq "$@"
}

# Same backstop and same probe-then-fallback as _lib_jq, for any other
# command that reads the filesystem and can stall on it. Callers MUST check
# the exit status: a bare `timeout 5 cmd` is not just uncapped when
# timeout(1) is missing, it is "command not found" (127), which silently
# yields empty output on stock macOS.
_lib_capped() {
  _lib_capped_for 5 "$@"
}

# _lib_capped_for SECONDS CMD [ARGS...]
# Shared probe-then-run logic behind _lib_capped and _lib_jq: probes
# timeout(1), then gtimeout(1), and runs CMD uncapped only when neither is
# on PATH. Callers MUST check the exit status.
_lib_capped_for() {
  local seconds="${1:?_lib_capped_for requires a seconds argument}"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$seconds" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$seconds" "$@"
  else
    "$@"
  fi
}

# Prints the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set
# (must be absolute), else $HOME/.claude. Returns 1 with no stdout when
# CLAUDE_CONFIG_DIR is relative, or when CLAUDE_CONFIG_DIR is unset/empty
# and $HOME is also unset/empty.
# Call-site contract (load-bearing): bare interpolation,
# "$(_lib_config_dir)/whatever", is unsafe — under `set -e`, a failing
# *nested* command substitution does not abort the script, so a resolver
# failure silently collapses to "/whatever" instead of being caught. Every
# call site must capture and check the exit status first.
_lib_config_dir() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    case "$CLAUDE_CONFIG_DIR" in
      /*) ;;
      *) return 1 ;;
    esac
    printf '%s\n' "${CLAUDE_CONFIG_DIR%/}"
    return 0
  fi
  local home_norm="${HOME%/}"
  [ -n "$home_norm" ] || return 1
  printf '%s\n' "$home_norm/.claude"
}

# Canonical jq-encode-or-hard-block body for a gate hook's deny path.
# Deliberately NOT named `emit_deny`: sourcing this file must not silently
# satisfy the "CALLER MUST define emit_deny" contract below on its own. Each
# gate hook defines a minimal bootstrap `emit_deny` before sourcing this
# file, then re-points `emit_deny` at this function immediately after a
# successful source — see require-respond-pr.sh in this repo's stowed
# hooks for the canonical pattern this mirrors.
_lib_emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  if [ -z "$reason_json" ]; then
    printf 'Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. Underlying gate reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

# Reads stdin into INPUT (global), extracts TOOL_NAME and COMMAND (globals)
# via a single _lib_jq call using ASCII Unit Separator (0x1f) as delimiter.
# Three deny paths protect against silent-allow: jq non-zero exit, empty
# INPUT, empty TOOL_NAME.
# Contract: if this function returns, parse succeeded AND TOOL_NAME is set.
# CALLER MUST define emit_deny before sourcing this file.
_lib_parse_tool_input_or_deny() {
  local deny_msg="${1:-Blocked: could not parse tool-input JSON.}"
  INPUT=$(cat)  # command substitution strips trailing newlines — safe for JSON payloads
  if [ -z "$INPUT" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  local jq_out
  jq_out=$(printf '%s\n' "$INPUT" | _lib_jq -r '"\(.tool_name // "")\(.tool_input.command // "")"' 2>/dev/null)
  local jq_exit=$?
  if [ "$jq_exit" -ne 0 ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  TOOL_NAME="${jq_out%%$'\x1f'*}"
  # shellcheck disable=SC2034 # set for hook scripts that source this file and reference $COMMAND
  COMMAND="${jq_out#*$'\x1f'}"
  if [ -z "$TOOL_NAME" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  case "$TOOL_NAME" in
    *$'\n'*) emit_deny "$deny_msg"; exit 0 ;;
  esac
}

# _lib_valid_session_id_component SESSION_ID
# Returns 0 (true) iff SESSION_ID is safe to use as a single filesystem path
# component. Harness session ids are UUIDs, so this conservative allow-list
# (letters, digits, underscore, hyphen) has ample room without ever needing
# '.' or '/'. Empty input is rejected.
_lib_valid_session_id_component() {
  local session_id="$1"
  [[ "$session_id" =~ ^[A-Za-z0-9_-]+$ ]]
}

# _lib_active_bypass_marker_live MARKER_DIR_NAME SESSION_ID
# Returns 0 (true) iff <config-dir>/MARKER_DIR_NAME/SESSION_ID holds the PID
# of a live process — that is, the skill which writes this marker is running
# right now, in this session. Returns 1 in every other case, and evicts the
# marker as an orphan when it exists but its stored PID is dead or
# unreadable.
# Usage: if _lib_active_bypass_marker_live ".issue-triage-active.d" "$SESSION_ID"; then ...; fi
_lib_active_bypass_marker_live() {
  [ "$#" -eq 2 ] || return 1
  local marker_dir_name="$1" session_id="$2"
  _lib_valid_session_id_component "$session_id" || return 1
  local config_dir
  config_dir=$(_lib_config_dir) || return 1
  local marker="$config_dir/$marker_dir_name/$session_id"
  [ -f "$marker" ] || return 1
  local stored_pid
  stored_pid=$(cat "$marker" 2>/dev/null | tr -d '[:space:]') || true
  if [[ "$stored_pid" =~ ^[0-9]+$ ]] && kill -0 "$stored_pid" 2>/dev/null; then
    return 0
  fi
  rm -f "$marker" 2>/dev/null
  return 1
}
