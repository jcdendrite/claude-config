#!/bin/bash
# PreToolUse hook: block git commit when claude/.claude/settings.json has
# model or effortLevel changes staged relative to main.
#
# Purpose: model/effortLevel overrides in settings.json are typically session-
# scoped ephemeral changes (e.g., /config model opus). Accidentally committing
# them modifies the shipped config for all users. This hook catches that class
# of accidental commit and surfaces it before git runs.
#
# Defense-in-depth: the hook filters its own input by tool name AND checks
# whether settings.json is actually staged — do not rely solely on the
# settings.json `if` condition in settings.json.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (model or effortLevel changed in staged settings.json)

INPUT=$(cat)
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')

# Only gate Bash tool calls.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# Only gate commands that contain a git commit invocation.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  exit 0
fi

# Only proceed if inside a git repo.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

SETTINGS_REPO_PATH="claude/.claude/settings.json"

# Check whether settings.json is staged at all.
if ! git diff --cached --name-only 2>/dev/null | grep -qF "$SETTINGS_REPO_PATH"; then
  exit 0
fi

# Diff the staged version against main. If main doesn't have the file yet
# (unlikely but possible on a brand-new branch), diff against /dev/null.
STAGED_CONTENT=$(git show :"$SETTINGS_REPO_PATH" 2>/dev/null)
if ! git show "main:$SETTINGS_REPO_PATH" >/dev/null 2>&1; then
  MAIN_CONTENT=""
else
  MAIN_CONTENT=$(git show "main:$SETTINGS_REPO_PATH" 2>/dev/null)
fi

# Extract model and effortLevel from both versions using jq.
STAGED_MODEL=$(printf '%s\n' "$STAGED_CONTENT" | jq -r '.model // ""' 2>/dev/null)
STAGED_EFFORT=$(printf '%s\n' "$STAGED_CONTENT" | jq -r '.effortLevel // ""' 2>/dev/null)
MAIN_MODEL=$(printf '%s\n' "$MAIN_CONTENT" | jq -r '.model // ""' 2>/dev/null)
MAIN_EFFORT=$(printf '%s\n' "$MAIN_CONTENT" | jq -r '.effortLevel // ""' 2>/dev/null)

CHANGED=0
if [ "$STAGED_MODEL" != "$MAIN_MODEL" ]; then
  CHANGED=1
fi
if [ "$STAGED_EFFORT" != "$MAIN_EFFORT" ]; then
  CHANGED=1
fi

if [ "$CHANGED" -eq 0 ]; then
  exit 0
fi

REASON="settings.json has model/effortLevel changes — commit these only if intentional. The staged settings.json differs from main on model or effortLevel. These fields are typically set per-session via /config and should not be committed unless you are intentionally shipping a routing change. Unstage the file (git restore --staged claude/.claude/settings.json) to allow the commit, or proceed only if this is a deliberate routing update."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
