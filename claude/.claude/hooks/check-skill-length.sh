#!/bin/bash
# Gate: block git commit when a staged SKILL.md grows past the 200-line ceiling.
#
# Policy: deny when the staged file is over 200 AND longer than the previously
# committed version. This allows reducing an already-over-limit file commit by
# commit without blocking the work, while still catching new bloat.
#
# The "if" field in settings.json is unreliable — the internal grep is the
# actual gate. See require-code-review.sh for the same pattern and rationale.

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# Only gate git commit commands.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$REPO_ROOT" ] && exit 0

FAIL=0
MESSAGES=""
while IFS= read -r f; do
  new=$(git show ":$f" 2>/dev/null | awk 'END{print NR}')
  old=$(git show "HEAD:$f" 2>/dev/null | awk 'END{print NR}')
  if [ "$new" -gt 200 ] && [ "$new" -gt "$old" ]; then
    MESSAGES="${MESSAGES}  $f: $new lines (was $old, limit 200)\n"
    FAIL=1
  fi
# Path prefix is repo-root-relative for this repo's layout (claude/.claude/skills/).
# In other repos this grep matches nothing and the hook exits 0 silently.
done < <(git diff --cached --name-only | grep -E 'claude/.claude/skills/.+/SKILL\.md')

if [ "$FAIL" -eq 1 ]; then
  REASON=$(printf 'Skill length gate: one or more SKILL.md files grew past the 200-line limit. Reduce to 200 or fewer lines before committing:\n%b' "$MESSAGES")
  REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
fi
