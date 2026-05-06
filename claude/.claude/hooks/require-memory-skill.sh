#!/bin/bash
# set -uo pipefail but NOT -e: hooks inspect exit codes rather than aborting on them.
set -uo pipefail
# PreToolUse hook: block Write/Edit/MultiEdit to Claude Code auto-memory files
# without a current ai-instruction-and-memory-files skill invocation for this turn.
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
# Per-turn debounce: the gate fires once per user turn, keyed by the uuid of
# the latest type=="user" entry in the transcript. uuid (not mtime) is used
# because turns can last seconds to minutes. After the first deny, the marker
# stores the current turn's uuid; subsequent writes in the same turn see a
# matching marker and pass through.
#
# Fail-open conditions (exit 0 without denying):
#   - session_id absent from input — cannot key a per-session marker
#   - transcript_path absent from input — cannot read turn uuid
#   - transcript has no type=="user" entries — no uuid to key on
#   - unbound variable encountered (set -u) — exits non-zero but falls through
#     to allow, since no JSON is emitted on an unbound-variable exit
#
# Bash-tool bypass: Bash writes (e.g. echo > file) bypass this gate because
# the hook only intercepts Write/Edit/MultiEdit tool calls. This is an
# acceptable limitation — realistic auto-memory writes use the Write tool.
#
# Defense-in-depth: the hook filters its own input by tool name; do not
# rely solely on the settings.json matcher condition.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (memory write without skill invocation this turn)

INPUT=$(cat)
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')

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
TRANSCRIPT_PATH=$(printf '%s\n' "$INPUT" | jq -r '.transcript_path // empty')

# Fail open if we can't track turns.
if [ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

LATEST_UUID=$(tail -n 200 "$TRANSCRIPT_PATH" 2>/dev/null | jq -r 'select(.type=="user") | .uuid' 2>/dev/null | tail -1)

# Fail open if there are no user messages in the transcript yet.
if [ -z "$LATEST_UUID" ]; then
  exit 0
fi

MARKER_DIR="$HOME/.claude/.memory-skill-fired.d"
MARKER="$MARKER_DIR/$SESSION_ID"

# Per-turn debounce: if the marker's stored uuid matches the current turn's
# latest user uuid, the skill already fired this turn — allow through.
if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$LATEST_UUID" ]; then
  exit 0
fi

# Record this turn's uuid and deny.
mkdir -p "$MARKER_DIR"
printf '%s' "$LATEST_UUID" > "$MARKER"

REASON="Memory write blocked by ai-instruction-and-memory-files gate. You are writing to $FILE_PATH, which is part of Claude Code's auto-memory file system (MEMORY.md index or a new topic file). Invoke the ai-instruction-and-memory-files skill via the Skill tool first — it covers MEMORY.md index format, topic-file frontmatter, length budgets, and the type classification (user / feedback / project / reference). After invoking the skill, retry this write; subsequent memory writes in this turn will pass through."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
