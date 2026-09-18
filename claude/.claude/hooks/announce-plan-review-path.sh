#!/bin/bash
# hook-class: informational
# PostToolUse Write hook: announces the absolute plan path /plan-review's
# Step 1 just declared (<config-dir>/.plan-review-active.d/<session-id>.reviewed-plan-path),
# on systemMessage (engineer) and hookSpecificOutput.additionalContext
# (model).
# This removes the dependency on the reviewing model recalling that
# Step-1-resolved value all the way to the Output-format closing line.
#
# Fail posture: fail-silent, never blocks. Each of the following falls
# through to exit 0 with no output:
#   - missing _lib.sh
#   - unparseable stdin
#   - a non-matching tool or path
#   - non-absolute or otherwise malformed declared content
#   - a failed allowlist check
# PostToolUse cannot deny, so there is nothing to fail closed against.
#
# The declared path must match ^[A-Za-z0-9._/@+-]+$ under LC_ALL=C or
# nothing is emitted at all.
# This closes the shell-quoting, terminal-escape, and newline-based
# structural case-glob bypass a tool-supplied path would otherwise open
# (set-session-title-from-branch.sh: 18-23, :151-155).
# It is a structural filter only, not general semantic-content filtering.
#
# Paired glob site: the sibling-path glob below must match the literal
# path claude-skills/skills/plan-review/SKILL.md Step 1 declares its
# sibling at.
#
# Known gaps: _lib_jq's timeout/gtimeout cap runs uncapped when neither
# binary is on PATH -- a pre-existing _lib.sh gap, not one this hook
# introduces (_lib.sh's own header already documents it for every caller).
# This hook must not gain a _config_value/_config_enabled call without
# re-evaluating its fail-silent posture, since those calls perform
# unwrapped filesystem I/O with no timeout backstop per _config.sh's own
# header -- the hook currently avoids this class entirely by calling only
# _lib_config_dir (pure env-var branching, no stat/read).
# A same-character-set English directive -- an absolute path spelled as an
# instruction using only letters, digits, and the allowed punctuation --
# still passes the allowlist and is echoed verbatim into additionalContext.
# This mirrors announce-resume-command.sh's identical, accepted residual;
# it is not a bug.
#
# Defense-in-depth: filters tool_name and file_path itself; does not rely
# solely on the settings.json matcher condition.
set -uo pipefail

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi
CONFIG_DIR=$(_lib_config_dir) || exit 0

INPUT=$(cat) || exit 0

TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null) || exit 0
case "$TOOL_NAME" in
  Write) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
case "$FILE_PATH" in
  "$CONFIG_DIR"/.plan-review-active.d/*.reviewed-plan-path) ;;
  *) exit 0 ;;
esac

CONTENT=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.content // empty' 2>/dev/null) || exit 0
# Strips at most one trailing newline, so an embedded newline still fails
# the allowlist below rather than being normalized away.
PLAN_PATH="${CONTENT%$'\n'}"

case "$PLAN_PATH" in
  /*) ;;
  # A non-absolute value means Step 1's resolution failed; announcing it
  # would mislead rather than inform.
  *) exit 0 ;;
esac

ALLOWLIST_RE='^[A-Za-z0-9._/@+-]+$'
# -z (null-data) treats the whole value as one line, so ^/$ anchor its
# start/end rather than each embedded line's.
# Without it, grep -q would wrongly pass on a value containing an embedded
# newline, since a bash case glob -- and the bracket class above, which
# excludes \n -- both operate line-by-line.
printf '%s' "$PLAN_PATH" | LC_ALL=C grep -Eqz "$ALLOWLIST_RE" || exit 0

# shellcheck disable=SC2016 # single-quoted on purpose: $path is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
_lib_jq -n --arg path "$PLAN_PATH" \
  '{
    systemMessage: ("Plan under review: " + $path),
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: ("The absolute path of the plan file /plan-review is reviewing is: " + $path + ". It was recorded from this session'"'"'s own Step 1 resolution -- state it verbatim in the Output format closing line instead of re-deriving it.")
    }
  }' \
  2>/dev/null || true

exit 0
