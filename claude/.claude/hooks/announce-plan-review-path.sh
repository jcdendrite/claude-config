#!/bin/bash
# hook-class: informational
# PostToolUse Write hook: announces the absolute plan path /plan-review's
# Step 1 declared at <config-dir>/.plan-review-active.d/<session-id>.reviewed-plan-path,
# on systemMessage (engineer) and hookSpecificOutput.additionalContext
# (model).
# This gives the engineer a deterministic copy of the Step-1 path.
# The Output-format closing line is still stated by the model.
#
# Fail posture: fail-silent, never blocks.
# Every unmatched, malformed, or rejected input exits 0 with no output, since PostToolUse cannot deny.
#
# The Write's file_path must equal exactly
# <config-dir>/.plan-review-active.d/<payload session_id>.reviewed-plan-path.
# A missing or invalid session_id, a nested segment, or a `..` traversal never matches.
#
# The declared path must be absolute, at most 4096 characters, free of NUL,
# and match ^[A-Za-z0-9._/@+-]+$ in the C locale, or nothing is emitted.
# This closes the shell-quoting, terminal-escape, and newline-based
# structural bypass a tool-supplied path would otherwise open.
# It is a structural filter only, not general semantic-content filtering.
#
# Paired glob site: the sibling path built below must match the literal
# path claude-skills/skills/plan-review/SKILL.md Step 1 declares its
# sibling at.
#
# Known gaps:
#   - Plan-path characters outside the allowlist (non-ASCII, ~, #, spaces, parentheses) silently suppress the announcement.
#   - A declaration write from a _LIB_NO_GATE_RELEASE_AGENTS persona is denied by enforce-marker-script-shape.sh and announces nothing.
#   - An interrupted review re-run in the same session can fail the Write over the leftover sibling file and skip the announcement.
#   - The announcement lands at Step 1 rather than beside the verdict.
#   - A same-character-set English directive spelled as an absolute path still passes the allowlist.
#   - Such a directive is echoed verbatim into additionalContext, the same accepted residual as announce-resume-command.sh.
#   - A Write whose file_path carries trailing newlines or NUL still matches, because command substitution strips them.
#   - Such a Write announces only the same declared value, and no reader consumes that file.
#   - The announced path is model-declared and never checked for existence or `..` segments.
#   - `local LC_ALL=C` keeps `[A-Za-z]` ASCII-only when bash's `globasciiranges` is off.
#   - `globasciiranges` is on by default in bash >= 5.0 and off in bash < 5.0, including macOS's bash 3.2.
#   - CI's bash 5.x observes the scoping only via `bash +O globasciiranges` plus a collating locale.
#   - _lib_jq's timeout cap is absent when neither timeout nor gtimeout is on PATH; see _lib.sh's header.
#   - Adding a _config_value/_config_enabled call would end the fail-silent posture.
#   - They do unwrapped filesystem I/O with no timeout backstop, per _config.sh's header.
#
# Defense-in-depth: filters tool_name and file_path itself; does not rely
# solely on the settings.json matcher condition.
set -uo pipefail

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi
CONFIG_DIR=$(_lib_config_dir) || exit 0

# Returns 0 iff $1 is an absolute path of at most 4096 characters whose
# every character is in the allowlist. The locale is scoped to this function
# so bracket ranges are ASCII-only and the C locale never reaches the jq calls.
_plan_path_is_announceable() {
  local LC_ALL=C
  local candidate="$1"
  [ "${#candidate}" -le 4096 ] || return 1
  case "$candidate" in
    /*) ;;
    # A non-absolute value means Step 1's resolution failed; announcing it
    # would mislead rather than inform.
    *) return 1 ;;
  esac
  # An embedded newline falls outside the class, so it is rejected here.
  case "$candidate" in
    *[!A-Za-z0-9._/@+-]*) return 1 ;;
  esac
  return 0
}

INPUT=$(cat) || exit 0

# Every Write pays for sourcing _lib.sh and the jq parses below; accepted as
# below current scale, including the repeated parse of the same payload.
TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null) || exit 0
case "$TOOL_NAME" in
  Write) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
# Cheap prefilter so an unrelated Write skips the remaining jq parses.
case "$FILE_PATH" in
  "$CONFIG_DIR"/.plan-review-active.d/*.reviewed-plan-path) ;;
  *) exit 0 ;;
esac

SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty' 2>/dev/null) || exit 0
_lib_valid_session_id_component "$SESSION_ID" || exit 0
[ "$FILE_PATH" = "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.reviewed-plan-path" ] || exit 0

# A NUL is dropped by bash's $(), which would leave the announced value
# differing from the written bytes, so jq blanks such content instead.
# The length test runs first so explode never materializes an oversized string.
# This guard is a latency backstop, since the bash-side cap already rejects the same values.
# $() also strips every trailing newline.
PLAN_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '
  (.tool_input.content // "")
  | if (type == "string") and ((length > 4096) or (explode | any(. == 0))) then "" else . end' 2>/dev/null) || exit 0

_plan_path_is_announceable "$PLAN_PATH" || exit 0

# shellcheck disable=SC2016 # single-quoted on purpose: $path is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
_lib_jq -n --arg path "$PLAN_PATH" \
  '{
    systemMessage: ("Plan declared for review: " + $path),
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: ("The session wrote this absolute path to the plan-path declaration file for /plan-review: " + $path + ". State the plan path you are reviewing verbatim in the Output format closing line.")
    }
  }' \
  2>/dev/null || true

exit 0
