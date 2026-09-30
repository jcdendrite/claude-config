#!/bin/bash
# hook-class: informational
# PostToolUse Bash hook: shows the approved plan's absolute path in the
# engineer's terminal after `marker.sh write plan-review` records an approval.
# It relays the `plan-review marker covers: <absolute path>` lines that
# marker.sh prints, as a systemMessage only.
# It emits no additionalContext because the Bash tool result already carries
# marker.sh's stdout to the model.
#
# Trigger: tool_name is Bash and .tool_input.command is a single line that
# starts, after optional spaces or tabs, with the `~`-or-absolute
# `.../.claude/scripts/marker.sh` path and contains a `write plan-review` op.
# The rest of the chain is not re-validated here.
# It depends on enforce-marker-script-shape.sh for the shape of any single-line
# command that starts with the marker.sh path, and it filters tool_name and the
# command itself rather than relying solely on the settings.json matcher.
#
# Paired literal: the `plan-review marker covers: ` prefix below is copied from
# PLAN_REVIEW_COVERED_PATH_PREFIX in marker.sh.
#
# Fail posture: fail-silent, never blocks. Every path exits 0, and a failed
# jq call or a missing _lib.sh produces no output.
#
# Message forms:
#   - Normal: `plan-review marker recorded for: <path>[, <path>...]`.
#   - A withheld path: one line naming no paths.
#   - Payload drift: a line saying the Bash result carried no
#     tool_response.stdout string. It does not claim a marker was recorded.
# The messages claim only that a marker was recorded, never an approval verdict.
#
# Each relayed path must be non-empty, start with `/`, and consist only of bytes
# in [A-Za-z0-9._/@+-] (_lib_passes_path_char_allowlist in _lib.sh). It must
# contain no `..` segment and end in `.md`, with exactly one segment, containing
# no `/`, after `/.claude/plans/`.
#
# This group runs in parallel with the redaction group that also matches
# Bash. There is no ordering between them, and the two emit different fields.
#
# Known gaps:
#   - An approval whose active plan set is empty (the plan is committed and
#     unmodified, or lives outside .claude/plans/) announces nothing.
#   - An approval recorded in harness plan mode announces nothing.
#   - A delegated review shows the line only in the subagent's window.
#   - A withheld path is never shown, and any withheld path suppresses the
#     whole list.
#   - An active plan whose name does not end in `.md` (for example `.txt`,
#     which marker.sh enumerates) is withheld, so it suppresses the whole list.
#   - A hand-run `write plan-review` announces as an approval.
#   - A command whose marker.sh path contains a space or other character
#     outside the trigger's path class announces nothing.
#   - A repo path containing a space or other character outside the path
#     allowlist yields the withheld line.
#   - A Bash call whose raw payload contains both marker.sh and plan-review
#     passes the raw-stdin prefilter.
#   - The raw payload includes the command, cwd, transcript path, and output.
#   - A payload that passes the prefilter pays the _lib.sh sourcing cost before
#     the trigger rejects it.
#   - Unverified: whether the harness fires PostToolUse when the Bash command
#     exits non-zero.
#   - marker.sh prints paths only after a successful write, so a failed write
#     never announces a path either way.
#   - If the harness does fire on failure with a non-object tool_response, the
#     drift line may also appear on a failed write.
#
# Timeout: the settings.json registration sets no `timeout`, matching sibling
# informational hooks, so a hung jq is bounded only by the harness default when
# neither `timeout` nor `gtimeout` is on PATH.
set -uo pipefail

INPUT=$(cat) || exit 0

# Raw-stdin prefilter: settles ordinary Bash calls before _lib.sh is sourced and jq is spawned.
case "$INPUT" in
  *marker.sh*plan-review*) ;;
  *) exit 0 ;;
esac

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[ "$TOOL_NAME" = "Bash" ] || exit 0

COMMAND=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
# The shape gate never shape-checks a command with a newline, so none is trusted.
case "$COMMAND" in
  *$'\n'*) exit 0 ;;
esac

# Unquoted in [[ =~ ]] on purpose: a quoted pattern is a literal match on bash 3.2.
MARKER_PATH_RE='^[[:blank:]]*(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]'
WRITE_PLAN_REVIEW_RE='marker\.sh[[:space:]]+write[[:space:]]+plan-review([[:space:]]|&|$)'
[[ $COMMAND =~ $MARKER_PATH_RE ]] || exit 0
[[ $COMMAND =~ $WRITE_PLAN_REVIEW_RE ]] || exit 0

emit_system_message() {
  # shellcheck disable=SC2016 # single-quoted on purpose: $msg is a jq --arg binding, not a shell variable.
  _lib_jq -n --arg msg "$1" '{systemMessage: $msg}' 2>/dev/null || true
}

STDOUT_TYPE=$(printf '%s\n' "$INPUT" \
  | _lib_jq -r '(.tool_response | if type == "object" then (.stdout | type) else "none" end)' 2>/dev/null) || exit 0
if [ "$STDOUT_TYPE" != "string" ]; then
  emit_system_message "announce-approved-plan-path.sh: this Bash result carried no tool_response.stdout string, so the approved plan's path cannot be shown."
  exit 0
fi

TOOL_STDOUT=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_response.stdout' 2>/dev/null) || exit 0

PLAN_REVIEW_COVERED_PATH_PREFIX='plan-review marker covers: '
PREFIXED_LINE_COUNT=0
PATH_WITHHELD=false
PATHS_JOINED=""
# Here-string, not a pipe, so the loop runs in the current shell and keeps its state.
# String concatenation, not an array: an empty "${arr[@]}" aborts under `set -u` on bash 3.2.
while IFS= read -r OUTPUT_LINE; do
  case "$OUTPUT_LINE" in
    "$PLAN_REVIEW_COVERED_PATH_PREFIX"*) ;;
    *) continue ;;
  esac
  PREFIXED_LINE_COUNT=$((PREFIXED_LINE_COUNT + 1))
  PLAN_PATH="${OUTPUT_LINE#"$PLAN_REVIEW_COVERED_PATH_PREFIX"}"
  if ! _lib_passes_path_char_allowlist "$PLAN_PATH"; then
    PATH_WITHHELD=true
    continue
  fi
  # Bash `*` crosses `/`, so the single-segment rule needs its own arm ahead of the accept arm.
  case "$PLAN_PATH" in
    */../* | */..) PATH_WITHHELD=true; continue ;;
    /*/.claude/plans/*/*) PATH_WITHHELD=true; continue ;;
    /*/.claude/plans/*.md) ;;
    *) PATH_WITHHELD=true; continue ;;
  esac
  if [ -z "$PATHS_JOINED" ]; then
    PATHS_JOINED="$PLAN_PATH"
  else
    PATHS_JOINED="$PATHS_JOINED, $PLAN_PATH"
  fi
done <<< "$TOOL_STDOUT"

[ "$PREFIXED_LINE_COUNT" -gt 0 ] || exit 0

if $PATH_WITHHELD; then
  emit_system_message 'plan-review marker recorded; a path is not shown (unexpected characters or path shape).'
else
  emit_system_message "plan-review marker recorded for: $PATHS_JOINED"
fi

exit 0
