#!/bin/bash
# PreToolUse hook: block Write/Edit when a plan file exists in .claude/plans/
# without a current plan-review marker for this session.
#
# Globally applied (no opt-in), consistent with require-code-review.sh,
# require-ready-for-review.sh, and require-respond-pr.sh.
# Projects without a .claude/plans/ directory or with no plan files pass
# through silently — the plan-existence check is the built-in filter.
#
# Two-marker pattern:
# - Active marker (~/.claude/.plan-review-active.d/<session_id>):
#   content = Claude session PID. Written by /plan-review at step 0;
#   removed at the deactivation step. Bypasses the gate so the skill's own
#   Write/Edit calls during review don't self-deny. The hook checks PID
#   liveness (kill -0) on each gate hit; dead PIDs are evicted automatically,
#   which handles orphaned markers from sessions that errored before cleanup.
# - Completion marker (~/.claude/plan-review-markers/<repo-hash>.<session_id>):
#   written by /plan-review when the review is clean. Existence-checked;
#   allows Write/Edit until the next session.
# The markers are keyed per-session (not singletons) to prevent parallel
# sessions from overwriting each other's markers.
#
# Defense-in-depth: the hook filters its own input by tool name; do not
# rely solely on the settings.json matcher condition.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (plan exists but no review marker for this session)

. "$(dirname "$0")/_lib.sh"

INPUT=$(cat)
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')

# Only gate Write, Edit, and MultiEdit tool calls.
case "$TOOL_NAME" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

# Not in a git repo — can't check for plan files or key the marker.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# Check whether any plan file exists under .claude/plans/ in the project.
PLANS_DIR="$REPO_ROOT/.claude/plans"
if [ ! -d "$PLANS_DIR" ]; then
  exit 0
fi

PLAN_COUNT=$(find "$PLANS_DIR" -maxdepth 1 -name "*.md" -o -name "*.txt" 2>/dev/null | wc -l)
if [ "$PLAN_COUNT" -eq 0 ]; then
  exit 0
fi

# Plans exist — check for a current plan-review marker for this session.
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')

# Without session_id, we cannot key a per-session marker — block.
if [ -n "$SESSION_ID" ]; then
  # Active-marker bypass: the /plan-review skill is currently running.
  ACTIVE_MARKER="$HOME/.claude/.plan-review-active.d/$SESSION_ID"
  if [ -f "$ACTIVE_MARKER" ]; then
    STORED_PID=$(cat "$ACTIVE_MARKER" 2>/dev/null | tr -d '[:space:]')
    if [[ "$STORED_PID" =~ ^[0-9]+$ ]] && kill -0 "$STORED_PID" 2>/dev/null; then
      exit 0
    fi
    rm -f "$ACTIVE_MARKER" 2>/dev/null
  fi

  # Completion-marker check.
  REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
  MARKER="$HOME/.claude/plan-review-markers/$REPO_HASH.$SESSION_ID"
  if [ -f "$MARKER" ]; then
    exit 0
  fi
fi

# Scope the deny to writes inside this repo. Writes targeting user-home
# directories (~/.claude/plans/), /tmp, or other repos are outside the gate's
# intent — the gate guards this repo's code, not all files on disk.
TARGET_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty')
if [ -n "$TARGET_PATH" ]; then
  REAL_REPO=$(realpath -m "$REPO_ROOT")
  REAL_TARGET=$(realpath -m "$TARGET_PATH")
  if [[ "$REAL_TARGET" != "$REAL_REPO/"* ]]; then
    exit 0
  fi
fi

REASON="Write/Edit blocked by plan-review gate: a plan file exists in .claude/plans/ but no plan-review marker was found for this session. Next step depends on whether a plan covers this change:

  - If a plan covers this change → run /plan-review against it. The skill records the review in ~/.claude/plan-review-markers/ and this write will be allowed through on retry.

  - If no plan covers this change yet → run /plan-it first. It authors the plan and hands off to /plan-review at the end.

The model judges which case applies from conversation context. Plans live wherever you put them — typically .claude/plans/, but also /tmp/<slug>.md, handoff docs, or external design doc URLs. The hook does not try to detect plan-change correlation."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
