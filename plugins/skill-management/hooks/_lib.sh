#!/bin/bash
# Trimmed shared helper library for skill-management plugin hooks.
# Source this file (do NOT invoke it). Contains only the helpers needed by
# require-skill-review.sh: _lib_jq, _lib_parse_tool_input_or_deny,
# _marker_lib_repo_hash, _lib_marker_value_present, and
# _lib_chains_marker_write_before_commit. No git helpers, no
# worktree-enforcement helpers.
#
# _marker_lib_repo_hash must stay byte-identical to the same function in the
# stowed claude/.claude/hooks/_lib.sh — marker.sh (the write side) always
# sources the stowed copy directly ($HOME/.claude/hooks/_lib.sh), never a
# plugin-bundled one, so a divergence here breaks the repo-hash used to key
# markers between the write side and this hook's read side.
# _lib_marker_value_present is duplicated from that same file for the same
# reason the others are: a plugin cannot source across the plugin boundary.

# Backstop against a hung jq (~5s, not a per-fire latency budget).
# Cites guard-settings-session-keys.sh's git_capped 5s precedent.
# Fallback to bare jq when timeout(1) is absent (BSD/macOS default).
# install.sh warns about missing timeout at onboarding time.
# Security implication: on BSD/macOS without coreutils, a stalled or
# replaced jq binary can hold a gate hook open indefinitely. The harness's
# own hook timeout (if any) then governs — not this wrapper.
_lib_jq() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 jq "$@"
  else
    jq "$@"
  fi
}

# Reads stdin into INPUT (global), extracts TOOL_NAME and COMMAND (globals)
# via a single _lib_jq call using ASCII Unit Separator (0x1f) as delimiter.
# The single call surfaces a structural-type error when .tool_input is non-object
# (jq non-zero exit).
#
# Three deny paths protect against silent-allow:
#   (a) jq non-zero exit (parse failure, timeout exit=124, missing jq binary)
#   (b) empty INPUT (stdin EOF, closed pipe, harness misbehavior)
#   (c) empty TOOL_NAME (valid JSON but PreToolUse contract not honored, e.g. "{}")
# Per Anthropic PreToolUse contract, every legitimate event has a non-empty
# .tool_name; absence indicates the call did not originate from a real tool
# invocation. Without (b)/(c), downstream gates that early-exit on
# `[ "$TOOL_NAME" = "Bash" ]` silently allow on zero-byte or "{}" inputs.
#
# Contract: if this function returns, parse succeeded AND TOOL_NAME is set.
# On failure, calls emit_deny (caller-defined) with the supplied message and
# exits 0 — caller never checks $?. Never call this with `|| true` or in
# a pipeline. CALLER MUST define emit_deny before sourcing _lib.sh so this
# helper can resolve it; the canonical pattern (define-emit_deny-then-source)
# is enforced by test_hook_alignment.py.
_lib_parse_tool_input_or_deny() {
  local deny_msg="${1:-Blocked: could not parse tool-input JSON.}"
  INPUT=$(cat)  # command substitution strips trailing newlines — safe for JSON payloads
  if [ -z "$INPUT" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  # Single jq call extracts both fields delimited by ASCII Unit Separator (0x1f)
  # rather than newlines, preventing a tool_name value containing an embedded
  # newline from corrupting COMMAND via head/tail line splitting. Unit Separator
  # cannot appear in a valid Claude Code tool name or shell command.
  # The .tool_input.command extraction additionally surfaces a structural-type
  # error when .tool_input is non-object (e.g. "Cannot index string with string
  # 'command'"), returning non-zero.
  local jq_out
  # WARNING: the format string below contains a literal 0x1f (ASCII Unit Separator)
  # byte between the two interpolated fields - invisible in editors and diff views.
  # Do not remove it. test_lib.py::test_valid_bash_payload_returns_ok will fail
  # immediately if the delimiter is absent, catching accidental deletion.
  jq_out=$(printf '%s\n' "$INPUT" | _lib_jq -r '"\(.tool_name // "")\(.tool_input.command // "")"' 2>/dev/null)
  local jq_exit=$?
  if [ "$jq_exit" -ne 0 ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  TOOL_NAME="${jq_out%%$'\x1f'*}"
  # shellcheck disable=SC2034 # set for hook scripts that source this file and reference $COMMAND
  COMMAND="${jq_out#*$'\x1f'}"
  # Embedded newline in TOOL_NAME means the payload violated the PreToolUse
  # contract; deny rather than allow with a corrupted TOOL_NAME value.
  if [ -z "$TOOL_NAME" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  case "$TOOL_NAME" in
    *$'\n'*) emit_deny "$deny_msg"; exit 0 ;;
  esac
}

# Compute the marker repo-hash for an absolute repo-toplevel path.
# Input must have no trailing newline -- printf '%s' omits one, so the SHA
# covers exactly the bytes of $1.
# Usage: hash=$(_marker_lib_repo_hash "$REPO_ROOT")
_marker_lib_repo_hash() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

# _lib_marker_value_present MARKERS_DIR EXPECTED_VALUE GLOB_PREFIX...
# Returns 0 (true) iff some file in MARKERS_DIR whose name begins with one of
# the supplied prefixes holds EXPECTED_VALUE as a whole line.
#
# Completion markers are content-addressed: the stored value is a hash of
# exactly the state that was reviewed. That content is the authorization, so
# the read asks "has this state been reviewed?" rather than "did this filename
# review it?" — the filename keys only namespace concurrent writers apart.
#
# One `grep` process regardless of marker count. Marker directories are
# unbounded and already hold thousands of entries, so a per-file `cat` loop
# would put a fork count proportional to review history on a hook that fires
# on every git commit.
#
# `-x` (whole-line) is load-bearing, not stylistic: with a bare `-F` a stored
# value that merely CONTAINS the expected hash — a truncated write, or a
# longer digest sharing this one as a prefix — would falsely release the gate.
#
# `nullglob` is equally load-bearing. Bash's default expands a zero-match
# pattern to the literal, unexpanded pattern string, which grep then tries to
# open as a real path and fails on (exit 2) — and "no marker exists yet for
# this prefix" is the single most common call, so the default behavior would
# misreport the common case as an error rather than as a clean not-found.
# The shopt state is saved and restored so callers that rely on the default
# glob behavior elsewhere are unaffected.
#
# Kept byte-identical to the same function in the stowed
# claude/.claude/hooks/_lib.sh; see the header note on why this file
# duplicates rather than sources.
_lib_marker_value_present() {
  local markers_dir="$1" expected_value="$2"
  shift 2
  [ -n "$markers_dir" ] || return 1
  [ -n "$expected_value" ] || return 1
  [ -d "$markers_dir" ] || return 1
  [ "$#" -gt 0 ] || return 1

  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local -a marker_files=()
  local prefix
  for prefix in "$@"; do
    [ -n "$prefix" ] || continue
    marker_files+=("$markers_dir/$prefix"*)
  done
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi

  [ "${#marker_files[@]}" -gt 0 ] || return 1
  # -e pins EXPECTED_VALUE as the pattern even if it begins with a dash; the
  # stderr redirect swallows the "Is a directory" noise a stray subdirectory
  # under MARKERS_DIR would otherwise produce.
  #
  # SCALE BOUNDARY: the matched files become one argv, so a single prefix
  # holding roughly 13k-30k markers (host-dependent; ARG_MAX bounded by the
  # stack rlimit) makes grep fail to exec with E2BIG. That is a nonzero exit,
  # which every call site reads as "no matching marker" — so it fails CLOSED,
  # denying rather than releasing. Completion markers are never pruned today
  # (marker.sh clear-stale only evicts active-bypass markers), so the ceiling
  # is reachable by unattended growth. Whoever adds marker retention should
  # remove this note; until then a wedged gate at that scale is a deny with no
  # diagnostic, and manual pruning of ~/.claude/*-markers/ is the workaround.
  # A flat glob (not `grep -r`) is deliberate: recursion would let a file
  # nested in a stray subdirectory authorize a gate.
  grep -qFx -e "$expected_value" -- "${marker_files[@]}" 2>/dev/null
}

# Decide whether a command chains `marker.sh write <skill>` before its first
# `git commit`. PreToolUse hooks fire once per Bash tool call before the chain
# runs, so an on-disk marker check denies naturally-typed forms like
# `marker.sh write code-review && git commit`. When the same Bash call will
# write the marker before invoking commit, the in-chain marker.sh invocation
# is the same evidence the on-disk marker would later provide — marker.sh is
# the only sanctioned writer in either case.
#
# **Anchored at command start, not fragment start.** A fragment-walking
# approach (split on `&&` / `;` / `|`, then scan each fragment) would treat
# heredoc-body lines and wrapper-command arguments as "fragments" — so
# `echo ~/.claude/scripts/marker.sh write code-review && git commit` or
# `cat <<EOF | bash\n...marker.sh write code-review\nEOF && git commit` would
# wedge the gate open without ever actually invoking marker.sh. The strict
# command-start anchor here mirrors enforce-marker-script-shape.sh's
# VALID_CHAINED_COMMIT_PATTERN: only the literal shape
# `marker.sh write <skill>{,&& marker.sh write <skill2>...} && git commit`
# is honored. Wrapper commands, env-var prefixes, `bash -c`, heredoc bodies,
# and pipes all fail the anchor.
#
# Usage: _lib_chains_marker_write_before_commit "$COMMAND" code-review
# Returns 0 (true) if the command matches the sanctioned chained shape AND
# the target skill appears among the chained writes.
_lib_chains_marker_write_before_commit() {
  local command="$1" skill="$2"
  # Step 1: command matches the sanctioned chained shape (mirrors
  # enforce-marker-script-shape.sh's VALID_CHAINED_COMMIT_PATTERN). One or
  # more marker.sh write fragments joined by `&&`, then git commit. Anchored
  # so wrapper commands cannot trick the gate.
  if ! printf '%s' "$command" | grep -qE \
    "^[[:space:]]*((~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)[[:space:]]*&&[[:space:]]*)+git[[:space:]]+commit([[:space:]].*)?$"; then
    return 1
  fi
  # Step 2: target skill is among the chained writes. A chain like
  # `marker.sh write skill-review && git commit` must not authorize a
  # code-review-gated commit.
  printf '%s' "$command" | grep -qE \
    "(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+${skill}([[:space:]]|$)"
}
