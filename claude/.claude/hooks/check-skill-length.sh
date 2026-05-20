#!/bin/bash
# Gate: block git commit when a staged SKILL.md grows past its per-skill ceiling.
#
# Policy: deny when the staged file is over its limit AND longer than the
# previously committed version. This allows reducing an already-over-limit
# file commit by commit without blocking the work, while still catching new
# bloat.
#
# Default limit is 200 lines. Structural-dispatcher skills (code-review,
# plan-review) carry item-ownership / routing tables that legitimately run
# longer and are capped at Anthropic's documented 500-line ceiling instead.
# Plugin-scoped skills (plugins/*/skills/) currently have no override path
# and all fall to the 200-line default — extend limit_for() if a plugin
# skill earns the same exception.
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

# Per-skill limit override. Listed paths are repo-root-relative.
limit_for() {
  case "$1" in
    claude/.claude/skills/code-review/SKILL.md|claude/.claude/skills/plan-review/SKILL.md)
      echo 500 ;;
    *)
      echo 200 ;;
  esac
}

FAIL=0
MESSAGES=""
while IFS= read -r f; do
  new=$(git show ":$f" 2>/dev/null | awk 'END{print NR}')
  old=$(git show "HEAD:$f" 2>/dev/null | awk 'END{print NR}')
  limit=$(limit_for "$f")
  if [ "$new" -gt "$limit" ] && [ "$new" -gt "$old" ]; then
    MESSAGES="${MESSAGES}  $f: $new lines (was $old, limit $limit)\n"
    FAIL=1
  fi
# Path prefixes are repo-root-relative for this repo's layout.
# Covers both stowed skills (claude/.claude/skills/) and project-scoped plugins (plugins/*/skills/).
# In other repos this grep matches nothing and the hook exits 0 silently.
done < <(git diff --cached --name-only | grep -E '(claude/.claude/skills/|plugins/[^/]+/skills/).+/SKILL\.md')

if [ "$FAIL" -eq 1 ]; then
  REASON=$(printf 'Skill length gate: one or more SKILL.md files grew past their per-skill limit. Reduce to the limit or fewer lines before committing:\n%b' "$MESSAGES")
  REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
fi
