#!/bin/bash
# PreToolUse hook: block Write/Edit on implementation files when a plan file
# exists in .claude/plans/ without a current plan-review marker for this session.
#
# Opt-in: only active when CLAUDE_REQUIRE_PLAN_REVIEW=1 is set OR a
# .claude/require-plan-review sentinel file exists in the repo root.
# This prevents non-claude-config projects from being forced into the gate.
#
# Marker layout:
#   ~/.claude/plan-review-markers/<repo-hash>.<session_id>
# Written by the /plan-review skill when a clean review completes.
# The marker is keyed per-session (not a singleton) to prevent parallel
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

# Only gate Write and Edit tool calls.
if [ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ]; then
  exit 0
fi

# Check opt-in: env var or sentinel file.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)

OPT_IN=0
if [ "${CLAUDE_REQUIRE_PLAN_REVIEW:-}" = "1" ]; then
  OPT_IN=1
elif [ -n "$REPO_ROOT" ] && [ -f "$REPO_ROOT/.claude/require-plan-review" ]; then
  OPT_IN=1
fi

if [ "$OPT_IN" -eq 0 ]; then
  exit 0
fi

# Not in a git repo — can't check for plan files or key the marker.
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
  REPO_HASH=$(printf '%s' "$REPO_ROOT" | sha256sum | awk '{print $1}')
  MARKER="$HOME/.claude/plan-review-markers/$REPO_HASH.$SESSION_ID"
  if [ -f "$MARKER" ]; then
    exit 0
  fi
fi

REASON="Write/Edit blocked by plan-review gate: a plan file exists in .claude/plans/ but no plan-review marker was found for this session. Run the /plan-review skill now. When the review is clean, the skill will record the review in ~/.claude/plan-review-markers/ and this write will be allowed through on retry."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
