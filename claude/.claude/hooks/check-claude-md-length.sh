#!/bin/bash
# hook-class: gate
# Gate: block git commit when a staged CLAUDE.md or AGENTS.md grows past its limit.
#
# Policy: deny when the staged file is over its limit AND longer than the
# previously committed version. This allows reducing an already-over-limit
# file commit by commit without blocking the work, while still catching new
# bloat.
#
# Fail posture: fail-closed — parse errors deny the commit. This gate
# enforces a style rule, not a security boundary, but consistent fail-closed
# posture across all gate hooks prevents a whole class of silent-allow
# regressions.
#
# Default limit is 200 lines, matching the Anthropic-documented threshold for
# CLAUDE.md/AGENTS.md files (Claude Code — memory: "Longer files consume more
# context and reduce adherence"). No per-file overrides exist today; the case
# structure is kept so future exceptions can slot in without touching the
# surrounding logic.
#
# The "if" field in settings.json is unreliable — the internal grep is the
# actual gate. See require-code-review.sh for the same pattern and rationale.

set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by CLAUDE.md length gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by CLAUDE.md length gate: could not parse tool-input JSON."

# Only gate Bash tool calls.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Only gate git commit commands.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$REPO_ROOT" ] && exit 0

# Per-file limit override. Listed paths are repo-root-relative.
limit_for() {
  case "$1" in
    *)
      echo 200 ;;
  esac
}

FAIL=0
MESSAGES=""
while IFS= read -r f; do
  new=$(timeout 5 git show ":$f" 2>/dev/null | awk 'END{print NR}')
  old=$(timeout 5 git show "HEAD:$f" 2>/dev/null | awk 'END{print NR}')
  limit=$(limit_for "$f")
  if [ "$new" -gt "$limit" ] && [ "$new" -gt "$old" ]; then
    MESSAGES="${MESSAGES}  $f: $new lines (was $old, limit $limit)\n"
    FAIL=1
  fi
# Matches CLAUDE.md and AGENTS.md at the repo root, inside any .claude/ directory,
# or at any depth inside a .claude/ directory. Does NOT match files in arbitrary
# subdirectories (e.g. foo/CLAUDE.md) — only root-level and .claude/-scoped files.
done < <(git diff --cached --name-only 2>/dev/null | grep -E '^(CLAUDE\.md|AGENTS\.md|(.*/)?\.claude/(CLAUDE|AGENTS)\.md)$')

if [ "$FAIL" -eq 1 ]; then
  REASON=$(printf 'CLAUDE.md/AGENTS.md length gate: one or more files grew past the 200-line limit. Reduce to the limit or fewer lines before committing:\n%b' "$MESSAGES")
  emit_deny "$REASON"
fi
