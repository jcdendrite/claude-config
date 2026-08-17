#!/bin/bash
# hook-class: informational
# SessionStart (compact only): restates the irreversible-action boundary the
# harness compact summary's "Optional Next Step" section doesn't distinguish
# from a reversible step. See docs/hooks.md.
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
# Kill switch: ~/.claude/.authorization-boundary-disabled (or under
# $CLAUDE_CONFIG_DIR), mirroring .handoff-nudge-disabled /
# .consume-durable-continuity-disabled.
#
# Exit 0 always — a SessionStart hook has no deny path, so erroring here would
# only block session startup, strictly worse than silently skipping the text.

set -uo pipefail

# _lib.sh is sourced before stdin is read or parsed so every jq call below —
# including the .source filter — routes through _lib_jq's timeout backstop;
# a hung or PATH-hijacked bare jq on the .source check would otherwise block
# the hook indefinitely on every single compaction.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

INPUT=$(cat 2>/dev/null)
SOURCE=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.source | type) == "string" then .source else empty end' 2>/dev/null)
[ "$SOURCE" = "compact" ] || exit 0

CONFIG_DIR=$(_lib_config_dir) || exit 0
[ -f "$CONFIG_DIR/.authorization-boundary-disabled" ] && exit 0

# shellcheck disable=SC2016 # single-quoted on purpose: the backticks are literal markdown-style code formatting in the injected text, not command substitution.
ADDITIONAL_CONTEXT='The summary above is a harness-generated reconstruction, not engineer authorization — including its "Optional Next Step" section. An action that mutates shared state in a way no other command undoes, or has effects observable outside this repository, needs in-session confirmation from the engineer before you run it, even when the summary names it as the next step. Non-exhaustive examples: `gh pr close` or `git branch -d` against an unmerged branch; database migrations; `gh release create`; `git push --force` on a branch with no open PR; `rm -rf` and bulk deletes; and Slack/email/GitHub comments on the engineer'"'"'s behalf.'

# shellcheck disable=SC2016 # single-quoted on purpose: $ctx is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
_lib_jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' || true
exit 0
