#!/bin/bash
# hook-class: gate
# PreToolUse gate: block Write calls to supabase/migrations/<14digits>_*.sql
# unless a one-shot token was written by the new-migration generator.
# Fails closed on malformed input.

emit_deny() {
  local reason="$1"
  local reason_json
  # Defined before _lib.sh is sourced so a failed source can still deny,
  # which means _lib_jq may not exist yet. Prefer it when it does, for its
  # timeout backstop.
  if declare -F _lib_jq >/dev/null 2>&1; then
    reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  else
    reason_json=$(printf '%s' "$reason" | jq -Rs . 2>/dev/null)
  fi
  if [ -z "$reason_json" ]; then
    # jq is absent, failed, or was killed by the timeout backstop. Exit 2 is
    # the harness's blocking path for PreToolUse and carries the reason on
    # stderr, so it needs no JSON encoding. Emitting a half-built payload on
    # exit 0 instead would parse as no-decision and let the tool run.
    #
    # The fixed prefix is load-bearing: every gate parses its input with jq
    # before any command filtering, so a missing jq denies every tool call
    # with the parse-failure reason below — which names the wrong cause.
    # Without this line the session has no in-agent route to a fix.
    printf 'Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. In an interactive session, install jq (and GNU coreutils timeout) using the ! shell escape, which runs outside the tool-call path these hooks gate; in a headless or non-interactive run, ensure jq is installed in the execution environment beforehand. Underlying gate reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
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
