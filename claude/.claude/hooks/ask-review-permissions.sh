#!/bin/bash
# hook-class: informational
# Gate: ask before editing .claude/settings*.json.
#
# Why: settings.json edits that touch permissions.allow are security-sensitive
# and deserve a /review-permissions pass. A precise "does this edit touch
# permissions.allow" heuristic is fuzzy — the hook only sees new content, not
# a diff, and edits can move keys around without the word "allow" appearing —
# so we use a broad match and ask the user in the moment. Settings.json edits
# are rare enough that the "ask" mode is tolerable.

INPUT=$(cat)

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  echo "[ask-review-permissions] could not source _lib.sh; settings.json edits will silently proceed without the ask decision" >&2
  exit 0
fi

TOOL=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null)

case "$TOOL" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Match .claude/settings*.json paths: settings.json, settings.local.json, etc.
if ! printf '%s\n' "$FILE_PATH" | grep -qE '\.claude/settings[^/]*\.json$'; then
  exit 0
fi

echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Edit to .claude/settings.json. If this changes permissions.allow, adds a bare tool name to permissions.deny, or changes permissions.defaultMode, run the /review-permissions skill first. Approve if unrelated (model, hooks, statusLine, enabledPlugins, etc)."}}'
