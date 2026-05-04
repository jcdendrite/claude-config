#!/bin/bash
# Gate: require /respond-pr when fetching or posting PR comments.
#
# Why: Claude habitually fetches only inline file comments
# (gh api .../pulls/N/comments) and misses top-level reviews and issue-level
# comments, which is the common PR response failure mode. The /respond-pr
# skill fetches all three AND enforces the [Claude Code] attribution prefix
# on replies.
#
# Bypass: the /respond-pr skill writes a marker at
# ~/.claude/.respond-pr-active.d/<session_id> at its start and removes it at
# the end. While THIS session's marker exists AND is fresh (<60 min old),
# this hook lets gh commands through so the skill itself doesn't recurse
# into its own gate. Per-session keying (vs. a singleton path) prevents two
# parallel respond-pr sessions from thrashing on cleanup, and prevents one
# session's marker from leaking bypass to unrelated parallel sessions —
# both of which the singleton design did not handle.
#
# The hook refreshes the marker's mtime on each bypass so a long-running
# skill invocation (large PR, many comments) doesn't hit the 60-min
# staleness cutoff mid-run. The cutoff still applies to genuinely orphaned
# markers from a session that errored before reaching the cleanup step.
# The gate also covers `repos/{o}/{r}/(pulls|issues)/comments/{id}` (no
# PR/issue-number segment) — the destructive PATCH endpoint that overwrites
# a comment in place; gating it forces any edit to flow through
# /respond-pr's verified-author guidance.

INPUT=$(cat)
TOOL=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')

# Only gate Bash tool use
if [ "$TOOL" != "Bash" ]; then
  exit 0
fi

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# Bypass: fresh marker for THIS session's session_id means we're inside the
# skill and should let its own gh commands through. Empty session_id (older
# Claude Code versions, payload-schema drift) falls through to the gate.
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
if [ -n "$SESSION_ID" ]; then
  MARKER="$HOME/.claude/.respond-pr-active.d/$SESSION_ID"
  if [ -f "$MARKER" ] && [ -n "$(find "$MARKER" -mmin -60 2>/dev/null)" ]; then
    touch "$MARKER" 2>/dev/null
    exit 0
  fi
fi

# Match PR comment read/write patterns. Three forms:
#   gh api .../pulls/N/comments       (inline review comments)
#   gh api .../pulls/N/reviews        (top-level review bodies)
#   gh api .../issues/N/comments      (issue-level, which GH uses for PR top-level threads)
#   gh pr comment ...                 (post a top-level comment)
#   gh pr review ...                  (post a review)
if printf '%s\n' "$COMMAND" | grep -qE 'gh\s+api\s+[^|&;]*(pulls|issues)/[0-9]+/(comments|reviews)'; then
  :
elif printf '%s\n' "$COMMAND" | grep -qE 'gh\s+api\s+[^|&;]*repos/[^/[:space:]]+/[^/[:space:]]+/(pulls|issues)/comments/[0-9]+'; then
  :
elif printf '%s\n' "$COMMAND" | grep -qE 'gh\s+pr\s+(comment|review)(\s|$)'; then
  :
else
  exit 0
fi

# Cross-repo bypass: if the command explicitly targets a repo that differs
# from the current git origin, it is research on an external repo (e.g.
# reading anthropics/claude-code issues while working in the user's project),
# not a PR response in the current repo. Two explicit forms are recognized:
#   gh api repos/OWNER/REPO/...
#   gh pr <cmd> ... -R OWNER/REPO      (also --repo OWNER/REPO, --repo=OWNER/REPO)
# Implicit commands (no repo specified) still gate — gh resolves those
# against the current repo. Caveats: (1) in-repo reads of an actual Issue
# (not a PR) still false-positive; accepted because the user does not track
# work in GitHub Issues. (2) the -R/--repo regex scans raw command text, so
# a flag-shaped substring inside a quoted body could spoof a cross-repo
# match and bypass the gate — unlikely for Claude-written commands, but
# worth knowing.
COMMAND_REPO=$(printf '%s\n' "$COMMAND" | sed -nE 's#.*repos/([^/]+/[^/]+)/(pulls|issues)/[0-9]+/(comments|reviews).*#\1#p;s#.*repos/([^/]+/[^/]+)/(pulls|issues)/comments/[0-9]+.*#\1#p' | head -1)
if [ -z "$COMMAND_REPO" ]; then
  COMMAND_REPO=$(printf '%s\n' "$COMMAND" | sed -nE 's#.*[[:space:]](-R|--repo)[[:space:]=]+([^[:space:]=]+/[^[:space:]]+).*#\2#p' | head -1)
fi

if [ -n "$COMMAND_REPO" ]; then
  CURRENT_URL=$(git config --get remote.origin.url 2>/dev/null)
  if [ -n "$CURRENT_URL" ]; then
    CURRENT_REPO=$(printf '%s\n' "$CURRENT_URL" | sed -nE 's#.*[:/]([^/:]+/[^/]+)$#\1#p' | sed 's#\.git$##')
    if [ -n "$CURRENT_REPO" ] && [ "$COMMAND_REPO" != "$CURRENT_REPO" ]; then
      exit 0
    fi
  fi
fi

echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"PR comment access blocked by respond-pr gate. Run the /respond-pr skill instead — it fetches inline file comments, top-level review bodies, AND issue-level comments (Claude habitually fetches only the first and misses real feedback), and it enforces the [Claude Code] attribution prefix on replies so comments posted through the GitHub token are clearly labeled as AI-generated. Do not ask the user for permission — run /respond-pr and let it handle this operation."}}'
