#!/bin/bash
# hook-class: informational
# PostToolUse redactor, scoped to Bash|Read|WebFetch|Grep|Task: scans the
# tool's own result for credential-*value* shapes (a GitHub token prefix, a
# full PEM private-key block — header through footer, not just the header
# line) and replaces each match with [REDACTED-CREDENTIAL] before the
# model's next turn consumes it. This is
# the value-shape backstop the path-based gates (deny-credential-bash-
# reads.sh, deny-credential-file-reads.sh) cannot provide on their own: a
# credential can enter context through a path neither gate anticipates — a
# WebFetch response body, a Grep match inside an unexpected file, or
# subagent-returned text — and path enumeration can never be exhaustive.
#
# Scope is narrower than the path gates' credential-path coverage: this
# hook only recognizes value shapes with a vendor-fixed format (a GitHub
# token prefix, a PEM header/footer) — it does NOT recognize a .netrc
# plaintext password, a .git-credentials URL, an AWS INI
# aws_secret_access_key value, a Docker config.json auth blob, or a
# Kubernetes config bearer token/cert, none of which have a fixed,
# vendor-documented shape to match against without either an unacceptable
# false-positive rate or unverified/invented pattern heuristics. This is
# not a general backstop for every credential family the path gates
# enumerate — it is the value-shape layer for the subset of credentials
# that have one. The path gates (case-folded; see their own headers) are
# what actually stops those other credential families from entering
# context in the first place.
#
# tool_response shape: Anthropic's hooks docs confirm two of the five
# matcher-scoped tool shapes directly — Bash emits
# {"stdout":"...","stderr":"...","exit_code":N}; Read emits
# {"file_path":"...","file_contents":"..."}. WebFetch, Grep, and Task carry
# no documented shape at all. Rather than guess three more per-tool field
# names — a wrong guess would silently miss whichever tool it targeted,
# undermining the reason this hook exists — the redaction below is
# shape-agnostic: jq's `walk` recurses through tool_response regardless of
# its structure and rewrites only the string leaves it finds, via the
# shared credential-value regex. This works identically across the two
# confirmed shapes and the three undocumented ones, since it never assumes
# a specific key exists, and it re-emits the walked value with its original
# shape/keys intact — satisfying the requirement that updatedToolOutput be
# shaped like the original tool_response.
#
# Size cap: reuses the promoted _LIB_SIZE_THRESHOLD_BYTES (5 MB, shared with
# deny-data-file-reads.sh). A tool_response over the cap is left completely
# unmodified — this hook emits nothing for it, which the harness reads as
# "no change" — rather than regex-scanned, avoiding unbounded per-fire
# latency on a hook that runs on every Bash/Read/WebFetch/Grep/Task call. A
# credential inside a truncated-past-cap output is not redacted; documented,
# not closed, consistent with this repo's tripwire-not-airtight posture for
# these hooks.
#
# Additional value patterns: an optional
# ~/.claude/credential-value-patterns.md, one `<label>: <regex>` line per
# pattern (same grammar as deny-pii-in-commits.sh's pii-patterns.md, minus
# its `exclude:` directive — there is no diff-scan concept here to exclude
# a path from). A line this hook cannot parse (no `:`, empty label or
# value) is skipped rather than denied: unlike a gate hook, this
# informational hook has no deny primitive to fall back on. Each addition
# is also validated for jq-regex compilability before being folded into
# the combined pattern, and skipped with a stderr diagnostic (naming the
# file and line number) if it doesn't compile — this is what keeps one
# malformed user regex from invalidating the single combined gsub call for
# the whole invocation, which would otherwise silently disable the
# built-in GitHub-token/PEM-key redaction too, with no trace beyond the
# generic fail-open exit.
#
# Internal-leak constraint: this hook must not leak the value it redacts
# while processing it. No `set -x`, no intermediate temp file holding
# unredacted content — stdin is piped straight through jq to stdout with no
# disk-persisted intermediate. Reproducing the triggering exposure inside
# the very hook meant to prevent it would be worse than not having the hook
# at all. Out of this hook's control: the harness's own PostToolUse
# invocation still hands this hook's stdin the pre-redaction tool_response,
# so any harness-level session log that separately records raw hook stdin
# captures the unredacted value regardless of what this hook does.
#
# Fails OPEN on any parse or extraction failure — an informational
# PostToolUse hook has no deny primitive, so passing the original
# tool_response through unmodified (by emitting nothing) is the only
# failure posture available. No permissionDecision is ever emitted; success
# emits hookSpecificOutput.updatedToolOutput only.

set -uo pipefail

# Fail-open on malformed stdin: a crashed hook that exits non-zero would
# break the triggering tool call, which is worse than an unredacted (but
# otherwise unaffected) result.
INPUT=$(cat) || exit 0
[ -n "$INPUT" ] || exit 0

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# Single _lib_jq call extracts both fields, delimited by ASCII Unit
# Separator (0x1f) — mirrors _lib_parse_tool_input_or_deny's technique
# rather than spending two separate timeout-wrapped jq subprocess spawns
# on the highest-invocation-count new hook (Bash|Read|WebFetch|Grep|Task,
# with Grep typically the busiest tool in an agentic session). `// null`
# then an explicit `tojson`/"" branch (not `// empty`) is deliberate: an
# `empty` generator inside a string interpolation collapses the WHOLE
# interpolated string to zero output, which would silently drop TOOL_NAME
# too when tool_response is null — this hook needs TOOL_NAME regardless of
# whether tool_response is present, to decide whether to act at all.
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

# --- Additional value patterns from credential-value-patterns.md ---------
# _LIB_PEM_PRIVATE_KEY_BLOCK_REGEX first: Oniguruma (jq's regex engine)
# tries alternatives left-to-right and takes the first that matches at each
# position, not the longest overall — putting the full-block alternative
# ahead of _LIB_CREDENTIAL_VALUE_REGEX's header-only PEM alternative is
# what makes gsub prefer redacting the whole key body when a complete
# block is present, falling back to the header-only match only when it
# isn't (e.g. output truncated mid-key).
CREDENTIAL_VALUE_PATTERN="${_LIB_PEM_PRIVATE_KEY_BLOCK_REGEX}|${_LIB_CREDENTIAL_VALUE_REGEX}"
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

    # Validate compilability under jq's own regex engine (Oniguruma) before
    # folding this addition into the combined pattern. Without this check,
    # one malformed user regex would invalidate the single jq gsub call
    # below for the WHOLE invocation — silently disabling the built-in
    # GitHub-token/PEM-key redaction too, with the only trace being the
    # generic fail-open exit at the end of this script. Validating and
    # skipping per-line here means a bad addition loses only itself, not
    # the built-in coverage, and reports specifically which line and why.
    if ! jq -n --arg pattern "$addition_value" '"" | test($pattern)' >/dev/null 2>&1; then
      printf 'redact-credential-values.sh: skipping unparseable pattern at %s line %d (jq could not compile it as a regex) — built-in credential redaction is unaffected, but this addition is not being applied.\n' \
        "$CREDENTIAL_VALUE_PATTERNS_FILE" "$addition_lineno" >&2
      continue
    fi
    CREDENTIAL_VALUE_PATTERN="${CREDENTIAL_VALUE_PATTERN}|${addition_value}"
  done < "$CREDENTIAL_VALUE_PATTERNS_FILE"
fi

# shellcheck disable=SC2016 # single-quoted on purpose: $pattern below is a
# jq variable bound via --arg, not a shell variable, so it must NOT expand
# in the shell before jq ever sees it. Double-quoting would substitute the
# raw regex text into the jq program source instead of passing it through
# jq's own --arg binding, defeating the point of --arg.
REDACTED_TOOL_RESPONSE=$(printf '%s' "$TOOL_RESPONSE_RAW" | _lib_jq -c --arg pattern "$CREDENTIAL_VALUE_PATTERN" \
  'walk(if type == "string" then gsub($pattern; "[REDACTED-CREDENTIAL]") else . end)' 2>/dev/null) || exit 0
[ -n "$REDACTED_TOOL_RESPONSE" ] || exit 0

printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","updatedToolOutput":%s}}\n' "$REDACTED_TOOL_RESPONSE"
