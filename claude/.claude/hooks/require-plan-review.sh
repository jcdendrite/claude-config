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
#   presence-only, fresh mtime <60 min. Written by /plan-review at step 0;
#   removed at the deactivation step. Bypasses the gate so the skill's own
#   Write/Edit calls during review don't self-deny. mtime refreshed on each
#   bypass to handle long reviews.
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
  if [ -f "$ACTIVE_MARKER" ] && [ -n "$(find "$ACTIVE_MARKER" -mmin -60 2>/dev/null)" ]; then
    touch "$ACTIVE_MARKER" 2>/dev/null
    exit 0
  fi

  # Completion-marker check.
  REPO_HASH=$(printf '%s' "$REPO_ROOT" | sha256sum | awk '{print $1}')
  MARKER="$HOME/.claude/plan-review-markers/$REPO_HASH.$SESSION_ID"
  if [ -f "$MARKER" ]; then
    exit 0
  fi
fi

REASON="Write/Edit blocked by plan-review gate: a plan file exists in .claude/plans/ but no plan-review marker was found for this session. Run the /plan-review skill now. When the review is clean, the skill will record the review in ~/.claude/plan-review-markers/ and this write will be allowed through on retry."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
