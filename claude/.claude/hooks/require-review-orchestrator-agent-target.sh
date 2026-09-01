#!/bin/bash
# hook-class: gate
# Gate: restricts review-orchestrator's Agent tool calls to dispatch targets
# in _LIB_REVIEW_ONLY_AGENTS ∪ {code-writer}, closing the direct one-hop
# nested-dispatch gap require-review-orchestrator-bash.sh's own Bash
# restriction leaves open (which agent may be dispatched).
# This is not a guarantee that every allowed target's OWN Bash restriction is
# airtight — see docs/design-decisions.md §39 for the residual it does not
# close.
# tool_name for sub-agent spawning is "Agent" (verified from session
# transcripts — see require-routing-read.sh, which gates the same tool).
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
  # False positive: shellcheck can't model this stub-then-override
  # redefinition (resolves correctly at call time); disabling rather than
  # restructuring preserves bootstrap coverage for a failed _lib.sh source.
  # shellcheck disable=SC2218
  emit_deny "Blocked by review-orchestrator agent-target gate: could not source _lib.sh — hook cannot evaluate the dispatch-target restriction safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by review-orchestrator agent-target gate: could not parse tool-input JSON. Refusing to evaluate the dispatch-target restriction under malformed input."

[ "$TOOL_NAME" = "Agent" ] || exit 0

# .agent_type identifies the CALLER (the dispatching subagent), distinct
# from .tool_input.subagent_type below, which names the DISPATCH TARGET.
# Read fail-closed — an unchecked read would leave AGENT_TYPE empty on a jq
# failure and fall straight through to allow, the one fail-OPEN path in a
# hook that denies on every other read failure.
if ! AGENT_TYPE=$(printf '%s\n' "$INPUT" | _lib_jq -r '.agent_type // empty' 2>/dev/null); then
  emit_deny "Blocked by review-orchestrator agent-target gate: could not read .agent_type from the tool payload — refusing to evaluate the dispatch-target restriction under an unreadable trust-boundary field."
  exit 0
fi

# Fast common-path exit: every caller other than review-orchestrator itself
# (the main session, code-writer, general-purpose, any reviewer persona)
# passes through unconditionally regardless of dispatch target.
[ "$AGENT_TYPE" = "review-orchestrator" ] || exit 0

if ! TARGET=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.subagent_type // empty' 2>/dev/null); then
  emit_deny "Blocked by review-orchestrator agent-target gate: could not read .tool_input.subagent_type from the tool payload — refusing to evaluate an unreadable dispatch target."
  exit 0
fi

if [ -n "$TARGET" ] && { _lib_is_review_only_agent "$TARGET" || [ "$TARGET" = "code-writer" ]; }; then
  exit 0
fi

emit_deny "Blocked by review-orchestrator agent-target gate: dispatch target '$TARGET' is not in the closed allowlist (_LIB_REVIEW_ONLY_AGENTS's reviewer personas plus code-writer). review-orchestrator may only nest-dispatch code-writer for a fix, or a reviewer persona a skill's own routing table calls for — never general-purpose, claude, or any other type, since those carry the full tool set and could mutate the tree or release the gate on review-orchestrator's behalf."
