#!/bin/bash
# hook-class: informational
# PostToolUse redactor on Bash|Read|WebFetch|Grep|Task results: replaces credential-value shapes (GitHub token prefix, full PEM private-key block) with [REDACTED-CREDENTIAL] before the model's next turn reads them -- the value-shape backstop for credentials that reach context through a path deny-credential-bash-reads.sh/deny-credential-file-reads.sh don't cover (WebFetch body, Grep match, subagent output).
# Only recognizes vendor-fixed value shapes (GitHub token prefix, PEM header/footer); .netrc, .git-credentials, AWS/Docker/Kube credential values have no fixed shape and rely on the path gates instead. Fails open on any parse/extraction failure -- an informational hook has no deny primitive.

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

# Full PEM block first: Oniguruma takes the first alternative that matches at each position, not the longest, so ordering it before the header-only PEM alternative is what makes gsub prefer redacting the whole key body when a complete block is present.
CREDENTIAL_VALUE_PATTERN="${_LIB_PEM_PRIVATE_KEY_BLOCK_REGEX}|${_LIB_CREDENTIAL_VALUE_REGEX}"
# Optional user additions: ~/.claude/credential-value-patterns.md, one `<label>: <regex>` line per pattern (same grammar as deny-pii-in-commits.sh's pii-patterns.md, minus `exclude:`).
CREDENTIAL_VALUE_PATTERNS_FILE="${HOME}/.claude/credential-value-patterns.md"
if [ -f "$CREDENTIAL_VALUE_PATTERNS_FILE" ] && [ -r "$CREDENTIAL_VALUE_PATTERNS_FILE" ]; then
  addition_lineno=0
  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    addition_lineno=$((addition_lineno + 1))
    # Strip CR (CRLF), then leading/trailing whitespace.
    line=${raw_line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    case "$line" in '#'*) continue ;; esac
    case "$line" in
      *:*) ;;
      *) continue ;;
    esac

    addition_value="${line#*:}"
    addition_value="${addition_value#"${addition_value%%[![:space:]]*}"}"
    [ -n "$addition_value" ] || continue

    # Skip (don't apply) a pattern that fails to compile under jq's regex engine -- one bad addition would otherwise break the single combined gsub call below for the whole invocation, including the built-in redaction.
    if ! jq -n --arg pattern "$addition_value" '"" | test($pattern)' >/dev/null 2>&1; then
      printf 'redact-credential-values.sh: skipping unparseable pattern at %s line %d (jq could not compile it as a regex) — built-in credential redaction is unaffected, but this addition is not being applied.\n' \
        "$CREDENTIAL_VALUE_PATTERNS_FILE" "$addition_lineno" >&2
      continue
    fi
    CREDENTIAL_VALUE_PATTERN="${CREDENTIAL_VALUE_PATTERN}|${addition_value}"
  done < "$CREDENTIAL_VALUE_PATTERNS_FILE"
fi

# shellcheck disable=SC2016 # single-quoted on purpose: $pattern is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
REDACTED_TOOL_RESPONSE=$(printf '%s' "$TOOL_RESPONSE_RAW" | _lib_jq -c --arg pattern "$CREDENTIAL_VALUE_PATTERN" \
  'walk(if type == "string" then gsub($pattern; "[REDACTED-CREDENTIAL]") else . end)' 2>/dev/null) || exit 0
[ -n "$REDACTED_TOOL_RESPONSE" ] || exit 0

printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","updatedToolOutput":%s}}\n' "$REDACTED_TOOL_RESPONSE"
