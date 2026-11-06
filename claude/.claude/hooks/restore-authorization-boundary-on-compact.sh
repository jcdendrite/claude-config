#!/usr/bin/env bash
# hook-class: informational
# SessionStart (compact only): restates the irreversible-action boundary the
# harness compact summary's "Optional Next Step" section doesn't distinguish
# from a reversible step. When autonomous shipping is active it appends the
# shipping statement to that same message. See docs/hooks.md.
#
# Advisory only: this emits additionalContext, not a gate — it cannot block a
# tool call, only lower the chance the agent misreads the summary's next step.
# The shapes it names are illustrative, not an exhaustive list — see
# handoff/SKILL.md §3.5 for the categorization rule this hook echoes a subset
# of.
# Deliberately excluded from `clear`: an emptied context asserts no next step,
# so there is no false authorization to correct.
#
# Matches "compact" on the SessionStart hook AND self-filters on
# .source == "compact" per repo CLAUDE.md's hook defense-in-depth rule.
#
# The message is the boundary text and the shipping statement in one
# additionalContext, so the statement sits beside the outside-repository clause
# it overrides.
#
# Kill switch: ~/.claude/.authorization-boundary-disabled (or under
# $CLAUDE_CONFIG_DIR), mirroring .handoff-nudge-disabled /
# .consume-durable-continuity-disabled. It silences only the boundary text.
# The statement follows autonomous_shipping and the repo optout instead.
#
# Exit 0 always — a SessionStart hook has no deny path, so erroring here would
# only block session startup, strictly worse than silently skipping the text.

set -uo pipefail

# _lib.sh is sourced before stdin is read or parsed so every jq call below —
# including the .source filter — routes through _lib_jq's timeout backstop;
# a hung or PATH-hijacked bare jq on the .source check would otherwise block
# the hook indefinitely on every single compaction.
if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

INPUT=$(cat 2>/dev/null)
SOURCE=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.source | type) == "string" then .source else empty end' 2>/dev/null)
[ "$SOURCE" = "compact" ] || exit 0

# Delegates to _config_enabled's authorization_boundary_restore schema row
# (presence-disables).
# Exit code 1 (disabled) or 2 (config dir unresolvable) skips the boundary text.
# Exit code 3 (config-keys.psv unreadable) or 4 (this key's row missing) keeps
# it, because a schema failure must not silence the restatement.
BOUNDARY_TEXT=''
_config_enabled authorization_boundary_restore
AUTHORIZATION_BOUNDARY_RESTORE_STATUS=$?
case "$AUTHORIZATION_BOUNDARY_RESTORE_STATUS" in
  0|3|4)
    # shellcheck disable=SC2016 # single-quoted on purpose: the backticks are literal markdown-style code formatting in the injected text, not command substitution.
    BOUNDARY_TEXT='The summary above is a harness-generated reconstruction, not engineer authorization — including its "Optional Next Step" section. An action that mutates shared state in a way no other command undoes, or has effects observable outside this repository, needs in-session confirmation from the engineer before you run it, even when the summary names it as the next step. Non-exhaustive examples: `gh pr close` or `git branch -d` against an unmerged branch; database migrations; `gh release create`; `git push --force` on a branch with no open PR; `rm -rf` and bulk deletes; and Slack/email/GitHub comments on the engineer'"'"'s behalf.'
    ;;
  *) ;;
esac

# The statement is resolved for the repo the payload's .cwd names, through
# _lib_autonomous_shipping_root_for_cwd, which fails closed when the payload
# names no usable directory.
# The sentinel fast path keeps a machine that never adopted shipping from
# spawning jq or git here.
SHIPPING_STATEMENT=''
if _lib_autonomous_shipping_sentinel_present; then
  PAYLOAD_CWD=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.cwd | type) == "string" then .cwd else empty end' 2>/dev/null)
  if REPO_ROOT=$(_lib_autonomous_shipping_root_for_cwd "$PAYLOAD_CWD"); then
    SHIPPING_STATEMENT=$(_lib_autonomous_shipping_statement "$REPO_ROOT") || true
  fi
fi

ADDITIONAL_CONTEXT="$BOUNDARY_TEXT"
if [ -n "$SHIPPING_STATEMENT" ]; then
  ADDITIONAL_CONTEXT="${ADDITIONAL_CONTEXT:+$ADDITIONAL_CONTEXT }$SHIPPING_STATEMENT"
fi
[ -n "$ADDITIONAL_CONTEXT" ] || exit 0

# shellcheck disable=SC2016 # single-quoted on purpose: $ctx is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
_lib_jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' || true
exit 0
