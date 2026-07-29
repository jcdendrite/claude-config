#!/bin/bash
# hook-class: gate
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
  emit_deny "Blocked by settings session-keys gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by settings session-keys gate: could not parse tool-input JSON."

# Only gate Bash tool calls.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

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
# These 6 jq calls parse settings.json content, not hook input — they are
# fail-open by design (a settings.json that can't be parsed doesn't block
# the commit; the commit's own schema validation handles that).
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

emit_deny "settings.json has session-scoped keys changed — commit these only if intentional. The staged settings.json differs from main on model, effortLevel, or skipAutoPermissionPrompt. These keys are typically ephemeral session state — model/effortLevel set via /config, and skipAutoPermissionPrompt written automatically by Claude Code — and should not be committed unless you are intentionally shipping a config change. Unstage the file (git restore --staged claude/.claude/settings.json) to allow the commit, or proceed only if this is a deliberate update."
