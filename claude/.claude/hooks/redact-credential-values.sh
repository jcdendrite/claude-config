#!/bin/bash
# hook-class: informational
# PostToolUse redactor on Bash|Read|WebFetch|Grep|Task results: replaces credential-value shapes (GitHub token prefix, full PEM private-key block) with [REDACTED-CREDENTIAL] before the model's next turn reads them -- the value-shape backstop for credentials that reach context through a path deny-credential-bash-reads.sh/deny-credential-file-reads.sh don't cover (WebFetch body, Grep match, subagent output).
# That parenthetical names the motivating channels, not the full matcher -- Bash and Read are also covered above, Read backstopped further by deny-data-file-reads.sh's size cap.
# Only recognizes vendor-fixed value shapes (GitHub token prefix, AWS access key ID, PEM header/footer); .netrc, .git-credentials, Docker/Kube credential values have no fixed shape and rely on the path gates instead. Fails open on any parse/extraction failure -- an informational hook has no deny primitive.
# Also fails open (skips redaction, returns the original content) when tool_response exceeds _LIB_SIZE_THRESHOLD_BYTES (5 MB): this is the one channel-specific residual, since WebFetch/Grep/Task -- the channels this hook exists to backstop -- have no other size cap the way Read does via deny-data-file-reads.sh.

set -uo pipefail

# Fail-open on malformed stdin: exiting non-zero would break the triggering tool call.
INPUT=$(cat) || exit 0
[ -n "$INPUT" ] || exit 0

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# One _lib_jq call extracts both fields (0x1f-delimited) to avoid a second jq spawn on this hook's every-Bash/Read/WebFetch/Grep/Task invocation rate.
# `// null` + explicit tojson/"" branch, not `// empty`: an `empty` generator inside string interpolation would zero out the whole output, dropping TOOL_NAME along with a null tool_response.
JQ_OUT=$(printf '%s\n' "$INPUT" | _lib_jq -r \
  '(.tool_name // "") + "" + ((.tool_response // null) | if . == null then "" else tojson end)' \
  2>/dev/null) || exit 0
TOOL_NAME="${JQ_OUT%%$'\x1f'*}"
TOOL_RESPONSE_RAW="${JQ_OUT#*$'\x1f'}"

case "$TOOL_NAME" in
  Bash|Read|WebFetch|Grep|Task) ;;
  *) exit 0 ;;
esac

# A literal JSON null or an absent tool_response both collapsed to "" above.
[ -n "$TOOL_RESPONSE_RAW" ] || exit 0

RESPONSE_SIZE=$(printf '%s' "$TOOL_RESPONSE_RAW" | wc -c | tr -d '[:space:]')
if [ -n "$RESPONSE_SIZE" ] && [ "$RESPONSE_SIZE" -gt "$_LIB_SIZE_THRESHOLD_BYTES" ] 2>/dev/null; then
  exit 0
fi

# Pattern assembly (built-in credential shapes + optional user additions)
# and the redaction walk itself live in _lib_redact_credential_shaped_strings
# (_lib.sh) — shared with track-permission-prompts.sh, the second caller
# that crossed this repo's own DRY threshold for extracting it.
REDACTED_TOOL_RESPONSE=$(_lib_redact_credential_shaped_strings "$TOOL_RESPONSE_RAW")
[ -n "$REDACTED_TOOL_RESPONSE" ] || exit 0

printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","updatedToolOutput":%s}}\n' "$REDACTED_TOOL_RESPONSE"
