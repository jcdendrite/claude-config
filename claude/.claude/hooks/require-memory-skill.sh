#!/bin/bash
# hook-class: gate
# set -uo pipefail but NOT -e: hooks inspect exit codes rather than aborting on them.
set -uo pipefail
# PreToolUse hook: block Write/Edit/MultiEdit to Claude Code auto-memory files
# without an active ai-instruction-and-memory-files skill session for this session.
#
# Matched tools: Write, Edit, MultiEdit (self-filters; do not rely solely on
# the settings.json matcher condition).
#
# Two path classes are gated:
#   a. MEMORY.md index — any write/edit to
#      ~/.claude/projects/*/memory/MEMORY.md
#   b. New topic files — Write to a path that does not yet exist under
#      ~/.claude/projects/*/memory/ (Edit always targets an existing file, so
#      only Write can create; MultiEdit never creates new files).
#   Edits to existing topic files (Write or Edit to an already-present path)
#   pass through — the index-format and frontmatter rules were already observed
#   when the file was first created.
#
# Active-bypass marker: ~/.claude/.memory-skill-active.d/<session_id> — written
# by the ai-instruction-and-memory-files skill at Step 0 via
# `marker.sh activate memory-skill`, removed at its final step via
# `marker.sh deactivate memory-skill`. While THIS session's marker exists AND
# its stored PID is alive (kill -0), the hook allows through so the skill's
# own Write/Edit calls during the memory-write session are not re-blocked.
# Orphaned markers (session errored before cleanup) are evicted automatically:
# dead PID → rm on next gate hit.
#
# Fail-closed on parse error — if the hook cannot parse its input it denies
# rather than allowing through.
#
# Bash-tool bypass: Bash writes (e.g. echo > file) bypass this gate because
# the hook only intercepts Write/Edit/MultiEdit tool calls.
#
# Defense-in-depth: the hook filters its own input by tool name; do not
# rely solely on the settings.json matcher condition.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (memory write without active skill session)

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by memory-skill gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by memory-skill gate: could not parse tool-input JSON."

# Only gate Write, Edit, and MultiEdit tool calls.
case "$TOOL_NAME" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty')
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

REAL_PATH=$(realpath -m "$FILE_PATH")
REAL_HOME=$(realpath -m "$HOME")

# Classify path — only proceed for memory files.
IS_CANDIDATE=0

# Class (a): MEMORY.md index — always gated regardless of tool or existence.
if [[ "$REAL_PATH" == "$REAL_HOME/.claude/projects/"*"/memory/MEMORY.md" ]]; then
  IS_CANDIDATE=1
fi

# Class (b): new topic file — Write only, file must not exist yet.
if [ "$IS_CANDIDATE" -eq 0 ] && \
   [ "$TOOL_NAME" = "Write" ] && \
   [[ "$REAL_PATH" == "$REAL_HOME/.claude/projects/"* ]] && \
   [[ "$REAL_PATH" == *"/memory/"* ]] && \
   [ ! -e "$FILE_PATH" ]; then
  IS_CANDIDATE=1
fi

if [ "$IS_CANDIDATE" -eq 0 ]; then
  exit 0
fi

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')

# Fail open if we can't key the per-session marker at all: an absent
# session_id (older Claude Code versions, payload-schema drift) leaves no
# marker path to build, so there is nothing to distinguish "skill active"
# from "skill not active" and the safer default is to allow.
if [ -z "$SESSION_ID" ]; then
  exit 0
fi

# Active-bypass: alive PID stored in the marker means the skill is active —
# allow through. A non-empty id that is not a safe single path component
# (e.g. containing "../") withholds the bypass and falls through to the deny
# below rather than being treated as absent — the point of validating it is
# to never build that path, not to grant the allow anyway.
if _lib_active_bypass_marker_live ".memory-skill-active.d" "$SESSION_ID"; then
  exit 0
fi

emit_deny "Memory write blocked by ai-instruction-and-memory-files gate. You are writing to $FILE_PATH, which is part of Claude Code's auto-memory file system (MEMORY.md index or a new topic file). Invoke the ai-instruction-and-memory-files skill via the Skill tool first — it covers MEMORY.md index format, topic-file frontmatter, length budgets, and the type classification (user / feedback / project / reference). The skill's Step 0 activates a bypass marker so all memory writes in the session pass through after the skill is loaded."
