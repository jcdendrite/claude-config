#!/usr/bin/env bash
# hook-class: gate
# tier-threat-model: cooperative
# PreToolUse: deny a reviewer-persona Agent/Task spawn when this branch is
# entering a new reviewed state beyond the round-state threshold without a
# recent `plan-architect MODE=consult`. The threshold is resolved by _lib.sh's
# _lib_architect_consult_threshold. "Entry to round 3" -- the gate's first
# trigger by default -- is the measured, discontinuous jump in the case study
# this gate exists to interrupt (docs/case-studies/opus-frontload-review-rounds.md
# lines 158-269). See docs/design-decisions/round3-plan-architect-consult-gate.md
# for the full design rationale.
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
#   - Zero-start residual: a branch already in flight when this gate ships
#     starts its round count from zero, since transcript retention is a
#     rolling 30 days and this state file is written forward only.
#   - Consult-reset residuals:
#       - A state file the consult reset cannot delete can leave the gate
#         denying again (repair: docs/hooks.md § "Gate deadlock recovery").
#       - The 30-day latch sweep re-applies the first-firing threshold.
#       - Any `plan-architect` dispatch not led by `MODE=plan-sections`
#         restarts the count, whether or not it returns a verdict.

set -uo pipefail

DENY_GATE_LABEL="architect-consult"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment).
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh. For the recovery, see docs/hooks.md § \"Gate deadlock recovery\" in the claude-config repository; the user, not the agent, runs ./install.sh from the claude-config checkout in a separate terminal only if that runbook says so."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

# Self-filter on tool name -- registered on the union Agent|Task: the
# harness's confirmed dispatch tool name is "Agent", but a future
# Task-dispatched reviewer spawn is covered too.
case "$TOOL_NAME" in
  Agent | Task) ;;
  *) exit 0 ;;
esac

# Only a reviewer-persona spawn can trip this gate -- a code-writer,
# general-purpose, Explore, or Plan dispatch is never gated.
SUBAGENT_TYPE=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.subagent_type // empty' 2>/dev/null)
_lib_is_reviewer_persona "$SUBAGENT_TYPE" || exit 0

# Machine-wide kill switch, checked before any git call.
_lib_round_consult_gate_disabled && exit 0

# A live /plan-review fan-out must not consume a round-counting slot --
# without this, 115 plan-driven branches in the measured window
# (docs/case-studies/opus-frontload-review-rounds.md line 36) would drop the
# effective trigger to code-review round 2.
# An empty or unusable SESSION_ID makes the check report "not live" (the
# function's own arity/validity guard), which correctly falls through to
# the ordinary round-count logic below rather than granting a bypass.
if [ -n "$SESSION_ID" ]; then
  _lib_active_bypass_marker_live ".plan-review-active.d" "$SESSION_ID" && exit 0
fi

# An unresolvable config dir leaves "how many rounds so far" undecidable --
# allow, per this gate's allow-on-state-failure posture (see header).
CONFIG_DIR=$(_lib_config_dir) || exit 0

[ -z "$CWD" ] && CWD="$PWD"

REPO_ROOT=$(_lib_capped git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || exit 0

STATE_KEY=$(_lib_reviewer_round_state_key "$REPO_ROOT") || exit 0

STATE_FILE="$CONFIG_DIR/.reviewer-round-state.d/$STATE_KEY"

LATCH_FILE="$CONFIG_DIR/.architect-consult-latch.d/$STATE_KEY"

THRESHOLD=$(_lib_architect_consult_threshold "$CONFIG_DIR" "$STATE_KEY")
LATCH_PRESENT=0
[ -f "$LATCH_FILE" ] && LATCH_PRESENT=1

# Below the threshold (fewer than THRESHOLD distinct reviewed states recorded
# since the branch's last consult): allow without paying for the current
# state's own hash. See _lib.sh's _lib_architect_consult_threshold for the
# threshold's resolution.
if [ ! -f "$STATE_FILE" ] || [ "$(wc -l < "$STATE_FILE" | tr -d ' ')" -lt "$THRESHOLD" ]; then
  exit 0
fi

# At the threshold. Only now is it worth paying for `git diff --cached |
# sha256sum` plus `rev-parse HEAD`.
CURRENT_STATE=$(_lib_reviewer_round_state_value "$REPO_ROOT") || exit 0

# The current state is already one of the recorded rounds (a repeated
# reviewer fan-out against the same commit/diff, or a whole parallel batch)
# -- allow. grep's error status (2, unreadable or vanished file) defers to
# the latch, since a consult's reset deletes the state file and a latch
# shows one ran.
grep -qFx -e "$CURRENT_STATE" -- "$STATE_FILE" 2>/dev/null
GREP_STATUS=$?
[ "$GREP_STATUS" -eq 0 ] && exit 0
if [ "$GREP_STATUS" -gt 1 ]; then
  [ "$LATCH_PRESENT" -eq 1 ] && exit 0
fi

STATE_NOUN="states"
[ "$THRESHOLD" -eq 1 ] && STATE_NOUN="state"
if [ "$LATCH_PRESENT" -eq 1 ]; then
  RECORDED_CLAUSE="this branch has recorded $THRESHOLD distinct reviewed $STATE_NOUN since the branch's last architect consult"
else
  RECORDED_CLAUSE="this branch has already recorded $THRESHOLD distinct reviewed $STATE_NOUN and no architect consult is recorded for this branch"
fi
emit_deny "$RECORDED_CLAUSE, and the current one is new. Dispatch \`plan-architect MODE=consult\` first (unspecialized -- 'is the foundation wrong?'), then route its return by the three verdicts in code-review/SKILL.md's 'Round-cap architect consult' section before retrying this reviewer spawn. If you already dispatched that consult for this denial, report this block to the engineer instead of consulting again. If dispatching that consult is genuinely not workable in this session, report this block to the engineer rather than resolving it unilaterally -- do not attempt to disable this gate yourself, since that is a persistent, machine-wide behavioral change no agent should self-authorize. If you are a subagent, report this denial to your dispatcher rather than attempting to resolve it yourself."

exit 0
