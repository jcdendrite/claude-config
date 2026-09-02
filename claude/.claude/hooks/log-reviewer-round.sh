#!/bin/bash
# hook-class: informational
# PostToolUse: recorder half of the round-3-triggered architect-consult
# gate (require-architect-consult.sh) -- see
# .claude/plans/round3-review-consult-trigger.md for the full design
# rationale. Registers on PostToolUse matcher Agent|Task, mirroring the
# gate's own PreToolUse matcher. It self-filters internally, per this
# repo's hook defense-in-depth convention. There is no existing
# same-surface PostToolUse hook to mirror.
#
# Two independent write paths, both keyed on the gate's own
# "<repo-hash>.<branch-hash>" state key (_lib_reviewer_round_state_key):
#   - A reviewer-persona dispatch appends "<head-sha> <staged-diff-sha256>"
#     to <config-dir>/.reviewer-round-state.d/<key>, capped at
#     _LIB_REVIEWER_ROUND_STATE_CAP distinct lines, and skipped once a latch
#     already exists for this branch -- further tracking has zero marginal
#     value once the gate has gone permanently silent.
#   - A `plan-architect` dispatch whose prompt's first line is not
#     `MODE=plan-sections` writes a content-free, presence-only latch to
#     <config-dir>/.architect-consult-latch.d/<key>: the fail-safe direction
#     treats any non-plan-sections first line, including an absent one, as
#     a consult.
#
# exits 0 on every path -- an informational hook never denies, and every
# failure here (no jq, no _lib.sh, unresolvable session id/config
# dir/repo root, detached HEAD) is a reason to stay quiet rather than
# interfere with the tool call already in flight.
#
# Known gaps, same list as the gate's own header:
#   - Reviewer-persona-only scope: a code-writer/general-purpose/Explore/Plan
#     dispatch is never recorded.
#   - Once-per-branch, non-rearming: recording stops for good once the
#     latch exists (see the short-circuit above).
#   - Zero-start residual: a branch already in flight when this hook ships
#     starts its round count from zero.

set -uo pipefail

INPUT=$(cat 2>/dev/null)
[ -n "$INPUT" ] || exit 0

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
case "$TOOL_NAME" in
  Agent | Task) ;;
  *) exit 0 ;;
esac

SUBAGENT_TYPE=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null)

# _resolve_round_context: sets CONFIG_DIR, REPO_ROOT, STATE_KEY as a side
# effect. Returns 1 (leaving at least one unset) on any resolution
# failure -- every caller below already treats that as "stay quiet",
# per this hook's exit-0-on-every-path contract.
_resolve_round_context() {
  CONFIG_DIR=$(_lib_config_dir) || return 1
  local cwd
  cwd=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
  [ -z "$cwd" ] && cwd="$PWD"
  REPO_ROOT=$(_lib_capped git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)
  [ -n "$REPO_ROOT" ] || return 1
  STATE_KEY=$(_lib_reviewer_round_state_key "$REPO_ROOT") || return 1
  return 0
}

_record_reviewer_round() {
  local session_id
  session_id=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
  # A live /plan-review or /ready-for-review fan-out must not consume a
  # round-counting slot. An empty or unusable session_id makes both checks
  # report "not live" (the function's own arity/validity guard), which
  # correctly falls through to the ordinary recording logic.
  if [ -n "$session_id" ]; then
    _lib_active_bypass_marker_live ".plan-review-active.d" "$session_id" && return 0
    _lib_active_bypass_marker_live ".ready-for-review-active.d" "$session_id" && return 0
  fi

  _resolve_round_context || return 0

  local latch_file="$CONFIG_DIR/.architect-consult-latch.d/$STATE_KEY"
  # Once a consult has run for this branch, the gate never re-arms, so
  # further round tracking has zero marginal value -- short-circuit instead
  # of growing the state file needlessly.
  [ -f "$latch_file" ] && return 0

  local state_value
  state_value=$(_lib_reviewer_round_state_value "$REPO_ROOT") || return 0

  local state_dir="$CONFIG_DIR/.reviewer-round-state.d"
  local state_file="$state_dir/$STATE_KEY"
  mkdir -p "$state_dir" 2>/dev/null || return 0

  # Never grows past the cap. This pre-check runs outside the lock
  # _lib_append_line_locked holds below, so two concurrent dispatches
  # racing at two DIFFERENT new states (not the same-state fan-out the lock
  # exists for) could both read "under cap" and both append -- an accepted,
  # narrow residual: cross-state concurrency at the exact cap boundary is
  # not the scenario this lock targets (same-state fan-out is).
  if [ -f "$state_file" ] && ! grep -qFx -e "$state_value" -- "$state_file" 2>/dev/null; then
    local existing_count
    existing_count=$(wc -l < "$state_file" | tr -d ' ')
    [ "$existing_count" -ge "$_LIB_REVIEWER_ROUND_STATE_CAP" ] && return 0
  fi

  _lib_append_line_locked "$state_file" "$state_file.lock" "$state_value"

  # 30-day sweep, only on this write-transition branch -- matching
  # nudge-worktree-anchor.sh's and review-ledger.sh's own sweep placement.
  # The lock file shares the state file's own name plus ".lock", so this
  # one sweep covers both.
  find "$state_dir" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null
  return 0
}

_maybe_write_consult_latch() {
  local prompt first_line
  prompt=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.prompt // empty' 2>/dev/null)
  first_line="${prompt%%$'\n'*}"
  # Fail-safe direction: any first line other than the literal
  # MODE=plan-sections -- including an absent one -- is a consult
  # (plan-architect.md: "A dispatch carrying neither line is a consult").
  [ "$first_line" = "MODE=plan-sections" ] && return 0

  _resolve_round_context || return 0

  local latch_dir="$CONFIG_DIR/.architect-consult-latch.d"
  mkdir -p "$latch_dir" 2>/dev/null || return 0
  touch -- "$latch_dir/$STATE_KEY" 2>/dev/null

  find "$latch_dir" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null
  return 0
}

if _lib_is_reviewer_persona "$SUBAGENT_TYPE"; then
  _record_reviewer_round
elif [ "$SUBAGENT_TYPE" = "plan-architect" ]; then
  _maybe_write_consult_latch
fi

exit 0
