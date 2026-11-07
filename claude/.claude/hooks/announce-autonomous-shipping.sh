#!/usr/bin/env bash
# hook-class: informational
# SessionStart (startup|resume|clear): states the resolved autonomous-shipping
# state as additionalContext, so the agent acts on it without recalling to run
# ~/.claude/scripts/autonomous-shipping-active.sh. See docs/hooks.md.
#
# The settings.json matcher is startup|resume|clear, and the script also
# filters on .source per repo CLAUDE.md's hook defense-in-depth rule.
# Compaction is covered by restore-authorization-boundary-on-compact.sh, which
# carries the same statement in its own message.
# A fork does not ship, so it gets no statement.
#
# The repo is resolved from the payload's .cwd by
# _lib_autonomous_shipping_root_for_cwd, which fails closed when the payload
# names no usable directory.
#
# Exit 0 always -- a SessionStart hook has no deny path, so erroring here would
# only block session startup, strictly worse than silently skipping the text.

set -uo pipefail

# _lib.sh is sourced before stdin is read so every jq call below routes through
# _lib_jq's timeout backstop.
if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

# Machine-sentinel fast path: a machine that never adopted autonomous shipping
# spawns no jq or git.
_lib_autonomous_shipping_sentinel_present || exit 0

INPUT=$(cat 2>/dev/null)

SOURCE=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.source | type) == "string" then .source else empty end' 2>/dev/null)
case "$SOURCE" in
  startup|resume|clear) ;;
  *) exit 0 ;;
esac

PAYLOAD_CWD=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.cwd | type) == "string" then .cwd else empty end' 2>/dev/null)
REPO_ROOT=$(_lib_autonomous_shipping_root_for_cwd "$PAYLOAD_CWD") || exit 0

ADDITIONAL_CONTEXT=$(_lib_autonomous_shipping_statement "$REPO_ROOT") || exit 0

# shellcheck disable=SC2016 # single-quoted on purpose: $ctx is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
_lib_jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' || true
exit 0
