#!/usr/bin/env bash
# Gate: block Edit, Write, and MultiEdit to files in the main working tree of
# a repo that has opted into worktree discipline (.claude/worktree-required
# committed at the repo root). Companion to require-worktree-for-git-writes.sh,
# which blocks git write ops from the main tree.
#
# Defensive: prevent GIT_DIR / GIT_WORK_TREE env overrides from making the
# main tree impersonate a linked worktree via rev-parse output.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

INPUT=$(cat)

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' \
    "$reason_json"
}

# Fail-closed on malformed input: capture jq's exit code (not cat's) to detect
# a broken jq binary or non-JSON harness output.
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
JQ_EXIT=$?
if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "Blocked by worktree-enforcement hook (file-writes): could not parse tool-input JSON. Refusing to evaluate worktree discipline under malformed input."
  exit 0
fi

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Only applies to file-writing tools; other tools pass through immediately.
case "$TOOL_NAME" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

# Nothing to check without a path.
[ -z "$FILE_PATH" ] && exit 0

# Walk up from the file's parent directory to find an existing ancestor.
# Write may target a file that does not exist yet; git -C on a missing dir
# returns nothing, so we must find an existing ancestor.
lookup_dir="$(dirname "$FILE_PATH")"
while [ -n "$lookup_dir" ] && [ "$lookup_dir" != "/" ] && [ ! -d "$lookup_dir" ]; do
  lookup_dir="$(dirname "$lookup_dir")"
done
[ ! -d "$lookup_dir" ] && exit 0

# Find the repo root from the existing ancestor directory.
REPO_ROOT=$(git -C "$lookup_dir" rev-parse --show-toplevel 2>/dev/null)
[ -z "$REPO_ROOT" ] && exit 0

# Per-repo opt-in: only enforce if the sentinel is committed.
[ ! -f "$REPO_ROOT/.claude/worktree-required" ] && exit 0

# Detect main tree vs linked worktree using the same git-dir comparison as
# require-worktree-for-git-writes.sh: in a linked worktree, --absolute-git-dir
# and --git-common-dir differ; in the main tree they resolve to the same path.
GIT_DIR_ABS=$(git -C "$lookup_dir" rev-parse --absolute-git-dir 2>/dev/null)
GIT_COMMON_DIR=$(git -C "$lookup_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)

if [ -z "$GIT_DIR_ABS" ] || [ -z "$GIT_COMMON_DIR" ]; then
  emit_deny "Blocked by worktree-enforcement hook (file-writes): could not determine git state for '$FILE_PATH'. This repo has opted into worktree discipline (.claude/worktree-required is committed). Run $TOOL_NAME from inside a linked worktree — cd into an existing worktree under .claude/worktrees/, or spawn an agent with isolation: worktree."
  exit 0
fi

# Already in a linked worktree: allow.
[ "$GIT_DIR_ABS" != "$GIT_COMMON_DIR" ] && exit 0

# In the main working tree: deny.
REL_PATH="${FILE_PATH#"$REPO_ROOT"/}"
emit_deny "Blocked by worktree-enforcement hook (file-writes): $TOOL_NAME targets '$FILE_PATH' which is in the main working tree of a repo that has opted into worktree discipline (.claude/worktree-required is committed). Write the file at its worktree path instead — e.g. .claude/worktrees/<branch>/$REL_PATH — or spawn an agent with isolation: worktree."
exit 0
