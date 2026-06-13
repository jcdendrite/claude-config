#!/bin/bash
# hook-class: informational
# PostToolUse consumer: removes the one-shot migration token after a successful
# Write to a supabase/migrations/<14digits>_*.sql path. PostToolUse fires only
# after a tool call succeeds, so no success-field gating is needed.
# This hook never blocks — it always exits 0.
set -euo pipefail

# Read stdin directly. PostToolUse does not need a deny response, so we
# do not use _lib_parse_tool_input_or_deny (a PreToolUse gating function).
# Fail-open on malformed input: an orphaned token is harmless; a crashed
# consume hook that exits non-zero would break the Write tool call.
INPUT=$(cat) || exit 0

# Extract tool name. If jq is missing or INPUT is malformed, exit 0 (fail-open).
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0

# Defense-in-depth: this hook is wired for Write, but verify internally.
[ "$TOOL_NAME" = "Write" ] || exit 0

# Source the single canonical token-dir definition.
# shellcheck source=../lib/token-path.sh
if ! . "${CLAUDE_PLUGIN_ROOT}/lib/token-path.sh" 2>/dev/null; then
  exit 0
fi

# Extract the file_path written by the successful Write call.
FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0

# Not a path-targeting Write — nothing to consume.
[ -n "$FILE_PATH" ] || exit 0

BASENAME=$(basename "$FILE_PATH")

# Only consume tokens for Supabase migrations with a 14-digit UTC prefix.
if ! printf '%s\n' "$FILE_PATH" | grep -qE 'supabase/migrations/[0-9]{14}_[^/]+\.sql$' 2>/dev/null; then
  exit 0
fi

# Guard against empty MIGRATION_TOKEN_DIR (e.g. if token-path.sh sourced but
# produced no output). Without this check rm -f expands to rm -f /<basename>,
# which is an unintended root-path deletion attempt.
[ -n "${MIGRATION_TOKEN_DIR}" ] || exit 0

# Remove the token. -f makes this idempotent: silent if already absent.
rm -f "${MIGRATION_TOKEN_DIR}/${BASENAME}"

exit 0
