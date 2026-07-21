#!/bin/bash
# hook-class: gate
# PreToolUse gate: block Write calls to supabase/migrations/<14digits>_*.sql
# unless a one-shot token was written by the new-migration generator.
# Fails closed on malformed input.

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

# shellcheck source=./_lib.sh
if ! . "${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by validate-migration-filename: could not source _lib.sh."
  exit 0
fi

# shellcheck source=../lib/token-path.sh
if ! . "${CLAUDE_PLUGIN_ROOT}/lib/token-path.sh" 2>/dev/null; then
  emit_deny "Blocked by validate-migration-filename: could not source token-path.sh."
  exit 0
fi

# Guard against an empty MIGRATION_TOKEN_DIR (e.g. HOME unset when token-path.sh
# sourced): without this, the token check below expands to [ -f "/<basename>" ]
# and could allow against an unlikely root-path collision. Fail closed.
[ -n "${MIGRATION_TOKEN_DIR}" ] || { emit_deny "Blocked by validate-migration-filename: token directory path is not configured."; exit 0; }

_lib_parse_tool_input_or_deny "Blocked by validate-migration-filename: could not parse tool-input JSON."

# Defense-in-depth: this hook is wired for Write, but verify internally.
case "$TOOL_NAME" in
  Write) ;;
  *) exit 0 ;;
esac

# Extract the file_path from the Write call.
FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty')

# Not a path-targeting Write (or unusual Write shape) — allow.
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")

# Allow if the path is not a Supabase migration with a 14-digit UTC prefix.
# The match requires: supabase/migrations/ in the path AND a 14-digit prefix
# followed by underscore. Files outside this path or with a different prefix
# length are not Claude-authored migration candidates — allow them.
if ! printf '%s\n' "$FILE_PATH" | grep -qE 'supabase/migrations/[0-9]{14}_[^/]+\.sql$'; then
  exit 0
fi

# Token check: the new-migration generator writes a token keyed by the
# full basename. Existence of the token proves the filename came from the
# generator (hence from date -u, hence UTC).
if [ -f "${MIGRATION_TOKEN_DIR}/${BASENAME}" ]; then
  exit 0
fi

# No token — deny with the generator hint.
GENERATOR_PATH=$(printf '%s\n' "${CLAUDE_PLUGIN_ROOT}/scripts/new-migration")
emit_deny "Migration filename is not authorized for writing.
Filename: ${BASENAME}

To author a migration with a UTC timestamp, use the generator:
  \$(${GENERATOR_PATH} \"<your-slug>\")
Then write the file at the path it prints."
exit 0
