#!/bin/bash
# hook-class: gate
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

set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  # Defined before _lib.sh is sourced so a failed source can still deny,
  # which means _lib_jq may not exist yet. Prefer it when it does, for its
  # timeout backstop.
  if declare -F _lib_jq >/dev/null 2>&1; then
    reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  else
    reason_json=$(printf '%s' "$reason" | jq -Rs . 2>/dev/null)
  fi
  if [ -z "$reason_json" ]; then
    # jq is absent, failed, or was killed by the timeout backstop. Exit 2 is
    # the harness's blocking path for PreToolUse and carries the reason on
    # stderr, so it needs no JSON encoding. Emitting a half-built payload on
    # exit 0 instead would parse as no-decision and let the tool run.
    #
    # The fixed prefix is load-bearing: every gate parses its input with jq
    # before any command filtering, so a missing jq denies every tool call
    # with the parse-failure reason below — which names the wrong cause.
    # Without this line the session has no in-agent route to a fix.
    printf 'Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. In an interactive session, install jq (and GNU coreutils timeout) using the ! shell escape, which runs outside the tool-call path these hooks gate; in a headless or non-interactive run, ensure jq is installed in the execution environment beforehand. Underlying gate reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by skill length gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by skill length gate: could not parse tool-input JSON."

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
  new=$(timeout 5 git show ":$f" 2>/dev/null | awk 'END{print NR}')
  old=$(timeout 5 git show "HEAD:$f" 2>/dev/null | awk 'END{print NR}')
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
  emit_deny "$REASON"
fi
