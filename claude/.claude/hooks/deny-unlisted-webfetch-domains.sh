#!/bin/bash
# hook-class: gate
# Gate: WebFetch to a domain not on ~/.claude/webfetch-allowed-domains.md
# asks in default/acceptEdits/plan mode and denies in auto/bypassPermissions/
# dontAsk (a hook-returned ask isn't documented to hold there). File-absent
# means the same as file-empty — every domain unlisted, inverting
# _lib_config_lines's usual contract since this file grants reach. Host
# extraction shells to python3's urllib.parse, not a hand-rolled regex; an
# absent or hung python3 denies naming it.
#
# Known gaps (accepted, not chased further — rationale: docs/security-hardening.md):
#   - No dedicated write-gate protects the allowlist file itself: an agent
#     with ordinary Edit/Write access can append a domain and immediately
#     fetch it with no prompt.
#
# Fail-closed on unparseable hook input.

set -uo pipefail

# Bootstrap so a failed source of _lib.sh can still deny; re-pointed at
# _lib_emit_deny once sourced — see _lib.sh for the full contract.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # shellcheck disable=SC2218 # false positive: this stub-then-override redefinition resolves correctly at call time.
  emit_deny "Blocked by WebFetch domain gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

# Sibling to _lib_emit_deny for "ask" — kept local since this is the only
# consumer; falls back to deny (not allow) when jq can't encode the reason.
_webfetch_emit_ask() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  if [ -z "$reason_json" ]; then
    printf 'Hook gate could not encode its ask reason: jq is missing from PATH, failed, or timed out. Falling back to deny, the safe direction when a prompt cannot be encoded. In an interactive session, install jq (and GNU coreutils timeout) using the ! shell escape; in a headless or non-interactive run, ensure jq is installed in the execution environment beforehand. Underlying ask reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

_lib_parse_tool_input_or_deny "Blocked by WebFetch domain gate: could not parse tool-input JSON. Refusing to evaluate the request under malformed input."

# Defense-in-depth: only act on WebFetch calls (settings.json already matches WebFetch).
if [ "$TOOL_NAME" != "WebFetch" ]; then
  exit 0
fi

URL=$(_lib_jq -r '.tool_input.url // empty' <<< "$INPUT" 2>/dev/null)
PERMISSION_MODE=$(_lib_jq -r '.permission_mode // empty' <<< "$INPUT" 2>/dev/null)

if ! command -v python3 >/dev/null 2>&1; then
  emit_deny "Blocked by WebFetch domain gate: python3 is required to parse the requested URL's host and is not on PATH. Install python3, then retry."
  exit 0
fi

# urlsplit resolves a userinfo-prefixed authority (user@evil.com) to the
# real host, not the substring before the @.
HOST=$(_lib_capped python3 -c '
import sys
import urllib.parse
try:
    host = urllib.parse.urlsplit(sys.argv[1]).hostname
except Exception:
    host = None
print(host or "")
' "$URL" 2>/dev/null)
PY_EXIT=$?
if [ "$PY_EXIT" -ne 0 ]; then
  emit_deny "Blocked by WebFetch domain gate: python3 failed or timed out (exit $PY_EXIT) while parsing the requested URL's host — treated as fail-closed deny, since a hung interpreter and a malformed URL must not be indistinguishable from an allow. Install/repair python3, then retry."
  exit 0
fi

HOST=$(printf '%s' "$HOST" | tr '[:upper:]' '[:lower:]')
HOST="${HOST%.}"

if [ -z "$HOST" ]; then
  emit_deny "Blocked by WebFetch domain gate: could not determine a host from the requested URL (empty, schemeless, or a non-http scheme like about:/data:) — fail-closed rather than treating an unparseable URL as allowed."
  exit 0
fi

WEBFETCH_ALLOWLIST="${HOME}/.claude/webfetch-allowed-domains.md"
MATCHED=false
while IFS=$'\t' read -r _lineno entry; do
  entry_lower=$(printf '%s' "$entry" | tr '[:upper:]' '[:lower:]')
  entry_lower="${entry_lower%.}"
  case "$entry_lower" in
    '*.'*)
      suffix="${entry_lower#\*.}"
      case "$HOST" in
        *".$suffix") MATCHED=true ;;
      esac
      ;;
    *)
      [ "$HOST" = "$entry_lower" ] && MATCHED=true
      ;;
  esac
  $MATCHED && break
done < <(_lib_config_lines "$WEBFETCH_ALLOWLIST")

if $MATCHED; then
  exit 0
fi

case "$PERMISSION_MODE" in
  default|acceptEdits|plan)
    _webfetch_emit_ask "WebFetch domain gate: '$HOST' is not on the ~/.claude/webfetch-allowed-domains.md allowlist. Add it there (one domain per line, '*.example.com' matches subdomains only) to fetch this domain without prompting again, or approve this one-time fetch."
    exit 0
    ;;
  auto|bypassPermissions|dontAsk)
    emit_deny "Blocked by WebFetch domain gate: '$HOST' is not on the ~/.claude/webfetch-allowed-domains.md allowlist. A hook-returned ask prompt is not guaranteed under this session's permission mode ($PERMISSION_MODE), so this denies rather than assuming a prompt will appear. Add '$HOST' to ~/.claude/webfetch-allowed-domains.md if this fetch is legitimate, or ask the user to fetch it themselves."
    exit 0
    ;;
  *)
    emit_deny "Blocked by WebFetch domain gate: could not determine this session's permission mode, and '$HOST' is not on the ~/.claude/webfetch-allowed-domains.md allowlist — fail-closed rather than falling through to a prompt that might not appear. Add '$HOST' to the allowlist if this fetch is legitimate."
    exit 0
    ;;
esac
