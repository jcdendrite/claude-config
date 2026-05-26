#!/bin/bash
# Gate: block every shape of `gh pr merge` issued by the AI agent.
# Enforces the repo-root CLAUDE.md rule "AI agents: don't merge your own PRs":
# CI passing is necessary but not sufficient — the engineer merges directly.
#
# Dispatch: no `if`-pattern in settings.json — intentional. `Bash(gh pr merge *)`
# would miss the bare `gh pr merge` (no-args) form; self-filtering is the only
# shape that covers both. Fires on every Bash call; fast-exits for non-merge cmds.
#
# Fail posture: fail-closed on JSON parse error (jq non-zero exit → deny).
# Fail-open when jq is not installed (pre-flight guard → exit 0), on missing
# tool_name, or on missing/non-string command field (allow through —
# cannot confirm the call is a Bash merge attempt, so do not block).
#
# Known gaps (out of scope by design):
#   - `gh api repos/OWNER/REPO/pulls/N/merge` — the gh-api path to merge.
#     Different command shape; excluded by the implementation brief.
#   - `eval "gh pr merge..."`, `bash -c "gh pr merge..."` — subshell wrappers.
#     The hook inspects tool_input.command, not the expanded subshell content.
#     Claude Code agents operating in good faith do not use these forms.
#   - `/usr/bin/gh pr merge N` — full-path invocation. The regex matches
#     the literal token `gh`; resolving `gh` to its full binary path bypasses
#     the text match. Good-faith agents invoke `gh` directly.

set -uo pipefail

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
JQ_EXIT=$?

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "Blocked: could not parse tool-input JSON for gh-pr-merge gate."
  exit 0
fi

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command | if type == "string" then . else empty end' 2>/dev/null)
[ -z "$COMMAND" ] && exit 0

# Word-boundary match: (^|[[:space:]]) before `gh`; non-word char or EOL after `merge`.
# Matches: `gh pr merge 291 --squash`, `(cd /repo && gh pr merge 291)`,
#          `gh pr merge; echo done`, `git push && gh pr merge 291`, bare `gh pr merge`.
# Rejects: `echo "gh pr merge"` (gh preceded by `"`), `gh pr mergefoo` (word chars follow).
# Uses POSIX [[:space:]] (portable to BSD/macOS grep) instead of \s (GNU extension).
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])gh[[:space:]]+pr[[:space:]]+merge([^a-zA-Z0-9_]|$)'; then
  emit_deny "Blocked: '${COMMAND:0:200}' would merge a PR. Per the repo-root CLAUDE.md rule \"AI agents: don't merge your own PRs\", an AI agent that opens a PR does not also merge it — CI passing is necessary but not sufficient. Surface the merge intent to the engineer and wait for their explicit \"merge it\" instruction. (The engineer can run the command directly via the ! shell escape.)"
  exit 0
fi

exit 0
