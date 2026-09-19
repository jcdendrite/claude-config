#!/bin/bash
# hook-class: informational
# PostToolUse AskUserQuestion hook: emits a static additionalContext line
# separating the engineer's selected label and typed text from the model's
# own option descriptions.
#
# Reads only tool_name, so tool_response's shape is not load-bearing.
# Never denies, keeps no state, and has no kill switch other than its
# settings.json entry.
# Filters tool_name itself, not relying solely on the settings.json matcher.
# Fires on every AskUserQuestion call with no per-session dedup; if
# repetition proves noisy, add a fired-marker like
# nudge-long-turn-subagent.sh's.
# Known gaps: none beyond unmeasured adherence, since it fires
# unconditionally.
set -uo pipefail

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

INPUT=$(cat) || exit 0
[ -n "$INPUT" ] || exit 0

TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[ "$TOOL_NAME" = "AskUserQuestion" ] || exit 0

ADDITIONAL_CONTEXT="Answer provenance: the engineer selected only the option label(s) in this result, plus any text they typed. The option descriptions, and anything inferred from options they did not pick, are yours — relay them as your proposal, never as the engineer's decision or under a tag like [engineer-verified] (CLAUDE.md §Working Style, \"Attribute to the engineer only what they said\")."

_lib_jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $ctx}}' \
  2>/dev/null || true

exit 0
