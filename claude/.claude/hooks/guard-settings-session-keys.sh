#!/bin/bash
# PreToolUse hook: block git commit when claude/.claude/settings.json has
# session-scoped keys (model, effortLevel, skipAutoPermissionPrompt) staged
# relative to main.
#
# Purpose: these keys are typically session-scoped ephemeral changes —
# model/effortLevel from /config, and skipAutoPermissionPrompt written
# automatically by Claude Code when it persists the permission-prompt
# preference into the user settings file. Accidentally committing them
# modifies the shipped config for all users. This hook catches that class
# of accidental commit and surfaces it before git runs.
#
# Defense-in-depth: the hook filters its own input by tool name AND checks
# whether settings.json is actually staged — do not rely solely on the
# settings.json `if` condition in settings.json.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (a guarded key changed in staged settings.json)

set -uo pipefail

# Cap every git call at 5s so a pathologically slow git (huge repo, slow
# disk, stuck index lock) can't hang the commit this PreToolUse hook
# gates. The cap is per-call: worst case is 5s times the number of git
# calls below — bounded, but not instant. On timeout the call fails like
# any other git error and the hook falls through to its no-opinion exit.
git_capped() {
  timeout 5 git "$@"
}

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
REPO_ROOT=$(git_capped rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

SETTINGS_REPO_PATH="claude/.claude/settings.json"

# Check whether settings.json is staged at all.
if ! git_capped diff --cached --name-only 2>/dev/null | grep -qF "$SETTINGS_REPO_PATH"; then
  exit 0
fi

# Diff the staged version against main. If main doesn't have the file yet
# (unlikely but possible on a brand-new branch), diff against /dev/null.
STAGED_CONTENT=$(git_capped show :"$SETTINGS_REPO_PATH" 2>/dev/null)
if ! git_capped show "main:$SETTINGS_REPO_PATH" >/dev/null 2>&1; then
  MAIN_CONTENT=""
else
  MAIN_CONTENT=$(git_capped show "main:$SETTINGS_REPO_PATH" 2>/dev/null)
fi

# Extract the guarded keys from both versions using jq.
STAGED_MODEL=$(printf '%s\n' "$STAGED_CONTENT" | jq -r '.model // ""' 2>/dev/null)
STAGED_EFFORT=$(printf '%s\n' "$STAGED_CONTENT" | jq -r '.effortLevel // ""' 2>/dev/null)
STAGED_SKIP_AUTO_PROMPT=$(printf '%s\n' "$STAGED_CONTENT" | jq -r '.skipAutoPermissionPrompt // ""' 2>/dev/null)
MAIN_MODEL=$(printf '%s\n' "$MAIN_CONTENT" | jq -r '.model // ""' 2>/dev/null)
MAIN_EFFORT=$(printf '%s\n' "$MAIN_CONTENT" | jq -r '.effortLevel // ""' 2>/dev/null)
MAIN_SKIP_AUTO_PROMPT=$(printf '%s\n' "$MAIN_CONTENT" | jq -r '.skipAutoPermissionPrompt // ""' 2>/dev/null)

CHANGED=0
if [ "$STAGED_MODEL" != "$MAIN_MODEL" ]; then
  CHANGED=1
fi
if [ "$STAGED_EFFORT" != "$MAIN_EFFORT" ]; then
  CHANGED=1
fi
if [ "$STAGED_SKIP_AUTO_PROMPT" != "$MAIN_SKIP_AUTO_PROMPT" ]; then
  CHANGED=1
fi

if [ "$CHANGED" -eq 0 ]; then
  exit 0
fi

REASON="settings.json has session-scoped keys changed — commit these only if intentional. The staged settings.json differs from main on model, effortLevel, or skipAutoPermissionPrompt. These keys are typically ephemeral session state — model/effortLevel set via /config, and skipAutoPermissionPrompt written automatically by Claude Code — and should not be committed unless you are intentionally shipping a config change. Unstage the file (git restore --staged claude/.claude/settings.json) to allow the commit, or proceed only if this is a deliberate update."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
