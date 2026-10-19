#!/bin/bash
# hook-class: gate
# tier-threat-model: cooperative
# PreToolUse: while review_orchestrator_routing is on, deny a main-session
# Skill(code-review) call so the round runs through the review-orchestrator
# agent. See docs/design-decisions/review-orchestrator-routing-gate.md for
# the design this hook implements.
#
# Allow rules, checked in this order; the hook denies otherwise:
#   1. The tool is not Skill.
#   2. agent_id is non-empty (every subagent call, the agent's own included).
#   3. review_orchestrator_routing is not enabled (any non-zero
#      _config_enabled exit).
#   4. The skill's final name component is not exactly `code-review`,
#      compared case-sensitively. An empty or unreadable name allows.
#   5. The args, with leading ASCII whitespace stripped, are `unrouted` or
#      begin with `unrouted` followed by ASCII whitespace.
#
# Failure direction:
#   - Deny on payload failure (malformed JSON, empty stdin, non-object
#     tool_input, missing _lib.sh) -- TestGateHookBehavior requires this
#     uniformly of every gate.
#   - Allow when the key read fails (unresolvable config dir, unreadable
#     schema, missing row): only an observed exit 0 routes.
#   - Allow when the skill read or the args read fails after the parse
#     succeeded.
#
# The settings entry carries no `if`, so the tool-name check is the only
# filter.
# A key-off Skill call pays the shared parse plus one config read.
# The two field reads run only on a key-on main-session call.
#
# Known gaps, stated rather than left implicit -- required by
# `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` §
# "9. Review checklist":
#   - A typed /code-review expands without a Skill call, so it runs in the
#     main thread by design. /review-pr is for reviewing a pull request.
#   - A plugin skill whose final name component is `code-review` also routes.
#   - A /ready-for-review cumulative pass or /review-pr pass that drops its
#     `unrouted` word is denied like a routine round. Only the deny text's
#     inline exception keeps that pass out of the agent, which reviews the
#     staged diff instead.
#   - A fork or nested session whose payload lacks agent_id reads as main
#     (the hooks-reference note in enforce-marker-script-shape.sh).
#   - If the harness stops sending tool_input.args as a string, every call
#     denies, including the `unrouted` retry. Setting the key to false
#     recovers on the next Skill call.
#   - A session that passes the quoted spelling "unrouted" is denied by
#     design and recovers the same way.
#   - A non-ASCII separator after `unrouted`, such as U+3000, is denied by
#     design, and a retry with an ASCII space allows.
#   - A main session run as `claude --agent review-orchestrator` carries
#     agent_type alone, so this gate denies it with text that points at
#     itself. Recovery is args "unrouted" or setting the key to false.
#   - The name match on tool_input.skill is exact and case-sensitive, and a
#     name that misses routes nothing, with no signal.
#   - Whether the harness resolves skill names case-insensitively, or trims
#     whitespace before resolving them, is unverified.

set -uo pipefail

DENY_GATE_LABEL="review-orchestrator routing"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source -- see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
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
  emit_deny "could not source _lib.sh; run ./install.sh to pick up hook files this update added (stow does not relink a new file into an existing directory until it is re-run)."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

[ "$TOOL_NAME" = "Skill" ] || exit 0

# AGENT_ID, never AGENT_TYPE: only a subagent's call carries agent_id.
[ -z "$AGENT_ID" ] || exit 0

# Only exit 0 routes; every other exit leaves the round inline.
_config_enabled review_orchestrator_routing || exit 0

SKILL=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.skill // empty | strings' 2>/dev/null) || exit 0
[ "$(_lib_skill_basename "$SKILL")" = "code-review" ] || exit 0

# One value, so a non-string args field reads as empty.
ARGS=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.args // empty | strings' 2>/dev/null) || exit 0
_lib_args_lead_word_is "$ARGS" unrouted && exit 0

emit_deny "review_orchestrator_routing is on for this account, so this /code-review round runs through the review-orchestrator agent, not in this session. Load the subagent-delegation skill and follow \`subagent-delegation/SKILL.md\` § \"Exception: gate/review loops stay orchestrator-driven\". Run the round here instead only for that section's HALT fallback, a /ready-for-review cumulative pass, a /review-pr pass, or an inline round the user asks for: re-invoke the code-review skill with args \"unrouted\", or args \"unrouted <path>\" when the round has a diff-file path. Pass the word unrouted without the quotation marks, and keep the path after it when there is one. If you are a subagent, report this denial to your dispatcher rather than attempting to resolve it yourself."
exit 0
