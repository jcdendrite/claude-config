#!/bin/bash
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
# is fresh (<60 min old), the hook allows through so the skill's own
# Write/Edit calls during the memory-write session are not re-blocked.
# mtime is refreshed on each bypass to handle long sessions that span multiple
# memory writes without hitting the staleness cutoff.
#
# Fail-open conditions (exit 0 without denying):
#   - session_id absent from input — cannot key a per-session marker
#   - unbound variable encountered (set -u) — exits non-zero but falls through
#     to allow, since no JSON is emitted on an unbound-variable exit
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

# Fail open if we can't key the per-session marker.
if [ -z "$SESSION_ID" ]; then
  exit 0
fi

# Active-bypass: fresh marker for THIS session means the skill is active —
# allow through and refresh the marker's mtime so long sessions don't time out.
ACTIVE_MARKER="$HOME/.claude/.memory-skill-active.d/$SESSION_ID"
if [ -f "$ACTIVE_MARKER" ] && [ -n "$(find "$ACTIVE_MARKER" -mmin -60 2>/dev/null)" ]; then
  touch "$ACTIVE_MARKER" 2>/dev/null
  exit 0
fi

REASON="Memory write blocked by ai-instruction-and-memory-files gate. You are writing to $FILE_PATH, which is part of Claude Code's auto-memory file system (MEMORY.md index or a new topic file). Invoke the ai-instruction-and-memory-files skill via the Skill tool first — it covers MEMORY.md index format, topic-file frontmatter, length budgets, and the type classification (user / feedback / project / reference). The skill's Step 0 activates a bypass marker so all memory writes in the session pass through after the skill is loaded."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
