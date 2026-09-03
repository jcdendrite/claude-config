#!/bin/bash
# hook-class: gate
# PreToolUse: deny a reviewer-persona Agent/Task spawn when this branch is
# entering its third distinct reviewed state without a recent
# `plan-architect MODE=consult`. "Entry to round 3" is the measured,
# discontinuous jump in the case study this gate exists to interrupt
# (docs/case-studies/opus-frontload-review-rounds.md lines 158-269). See
# docs/design-decisions.md §41 for the full design rationale.
#
# Failure direction:
#   - Deny on payload failure (malformed JSON, empty stdin, non-object
#     tool_input, missing _lib.sh, missing jq) -- TestGateHookBehavior
#     requires this uniformly of every gate.
#   - Allow on state failure (unusable session id, unresolvable config dir,
#     unresolvable repo root, detached HEAD) -- this is a cost-and-quality
#     interruption, not a security control, and an over-firing deny on
#     unresolvable git state would block legitimate reviewer fan-out.
#
# Known gaps, stated rather than left implicit (repo hook-review
# convention):
#   - Reviewer-persona-only scope: a code-writer/general-purpose/Explore/Plan
#     dispatch is never gated.
#   - Once-per-branch, non-rearming: after one consult, this gate goes
#     permanently silent for the branch, even at round 5, 7, 9.
#   - Zero-start residual: a branch already in flight when this gate ships
#     starts its round count from zero, since transcript retention is a
#     rolling 30 days and this state file is written forward only.

set -uo pipefail

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment).
  # shellcheck disable=SC2218
  emit_deny "Blocked by architect-consult gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by architect-consult gate: could not parse tool-input JSON."

# Self-filter on tool name -- registered on the union Agent|Task: the
# harness's confirmed dispatch tool name is "Agent", but a future
# Task-dispatched reviewer spawn is covered too.
case "$TOOL_NAME" in
  Agent | Task) ;;
  *) exit 0 ;;
esac

# Only a reviewer-persona spawn can trip this gate -- a code-writer,
# general-purpose, Explore, or Plan dispatch is never gated.
SUBAGENT_TYPE=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null)
_lib_is_reviewer_persona "$SUBAGENT_TYPE" || exit 0

# Machine-wide kill switch, checked before any git call.
_lib_round_consult_gate_disabled && exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
# A live /plan-review or /ready-for-review fan-out must not consume a
# round-counting slot -- without this, 115 plan-driven branches in the
# measured window (docs/case-studies/opus-frontload-review-rounds.md line
# 36) would drop the effective trigger to code-review round 2.
# An empty or unusable SESSION_ID makes both checks report "not live" (the
# function's own arity/validity guard), which correctly falls through to
# the ordinary round-count logic below rather than granting a bypass.
if [ -n "$SESSION_ID" ]; then
  _lib_active_bypass_marker_live ".plan-review-active.d" "$SESSION_ID" && exit 0
  _lib_active_bypass_marker_live ".ready-for-review-active.d" "$SESSION_ID" && exit 0
fi

# An unresolvable config dir leaves "how many rounds so far" undecidable --
# allow, per this gate's allow-on-state-failure posture (see header).
CONFIG_DIR=$(_lib_config_dir) || exit 0

CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

REPO_ROOT=$(_lib_capped git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || exit 0

STATE_KEY=$(_lib_reviewer_round_state_key "$REPO_ROOT") || exit 0

STATE_FILE="$CONFIG_DIR/.reviewer-round-state.d/$STATE_KEY"

# Below the cap (fewer than _LIB_REVIEWER_ROUND_STATE_CAP distinct reviewed
# states recorded so far): allow without paying for the current state's own
# hash. See _lib.sh's _LIB_REVIEWER_ROUND_STATE_CAP for the cap's citation.
if [ ! -f "$STATE_FILE" ] || [ "$(wc -l < "$STATE_FILE" | tr -d ' ')" -lt "$_LIB_REVIEWER_ROUND_STATE_CAP" ]; then
  exit 0
fi

# At the cap. Only now is it worth paying for `git diff --cached | sha256sum`
# plus `rev-parse HEAD`.
CURRENT_STATE=$(_lib_reviewer_round_state_value "$REPO_ROOT") || exit 0

# The current state is already one of the recorded rounds (a repeated
# reviewer fan-out against the same commit/diff, or a whole parallel batch)
# -- allow.
grep -qFx -e "$CURRENT_STATE" -- "$STATE_FILE" 2>/dev/null && exit 0

# A genuinely new, third distinct state. Allow if the latch already shows a
# consult ran recently on this branch -- otherwise deny.
LATCH_FILE="$CONFIG_DIR/.architect-consult-latch.d/$STATE_KEY"
[ -f "$LATCH_FILE" ] && exit 0

emit_deny "Blocked by architect-consult gate: this branch is entering its third distinct reviewed state without a recent architect consult. Dispatch \`plan-architect MODE=consult\` first (unspecialized -- 'is the foundation wrong?'), then retry this reviewer spawn once it returns. If dispatching that consult is genuinely not workable in this session, report this block to the engineer rather than resolving it unilaterally -- do not attempt to disable this gate yourself, since that is a persistent, machine-wide behavioral change no agent should self-authorize. If you are a subagent, report this denial to your dispatcher rather than attempting to resolve it yourself."
