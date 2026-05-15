#!/bin/bash
# PreToolUse guard for the check-runner subagent.
# Scope: wired via subagent frontmatter hooks (not settings.json), so fires
# only while check-runner is active. settings.json PreToolUse hooks also fire
# concurrently; this guard is not a replacement for them.
# Posture: fail-closed — _lib.sh absent → deny; malformed JSON → deny.
# Purpose: deny any Bash fragment invoking `git` with a subcommand not on the
# 32-entry read-only allowlist. check-runner has no legitimate reason to invoke
# git mutations; denies in all repos regardless of worktree discipline.
# Known gaps: non-git mutation vectors (rm, mv, stray redirects) are not
# separately gated — maxTurns + prose constraints bound those paths.
set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' \
    "$reason_json"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "check-runner-bash-guard: could not source _lib.sh — refusing to evaluate git discipline under degraded state."
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
JQ_EXIT=$?

if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "check-runner-bash-guard: could not parse tool-input JSON — refusing to evaluate git discipline under malformed input."
  exit 0
fi

# Fast-path: no `git` word in command → nothing to enforce.
if ! [[ "$COMMAND" =~ (^|[^[:alnum:]])git([^[:alnum:]]|$) ]]; then
  exit 0
fi

readonly ALLOWED_SUBCMDS=(
  blame branch cat-file check-attr check-ignore check-mailmap check-ref-format
  count-objects describe diff fetch for-each-ref fsck help log ls-files
  ls-remote ls-tree name-rev reflog remote rev-list rev-parse shortlog show
  status tag var verify-commit verify-tag version worktree
)
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

FRAGMENTS=$(_lib_split_fragments "$COMMAND")

while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue
  _lib_fragment_invokes_git "$fragment" || continue

  subcmd=$(_lib_extract_git_subcmd "$fragment")
  if [ -z "$subcmd" ]; then
    emit_deny "check-runner-bash-guard: could not determine the git subcommand in '$fragment'. check-runner must not invoke git mutations — return the verdict now."
    exit 0
  fi

  if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
    emit_deny "check-runner-bash-guard: 'git $subcmd' is not on the read-only allowlist. check-runner must not invoke git mutations — return the verdict now with whatever results you have so far."
    exit 0
  fi
done <<< "$FRAGMENTS"

exit 0
