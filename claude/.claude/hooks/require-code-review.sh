#!/bin/bash
# hook-class: gate
# Gate: require /code-review before git commit, verified via marker file.
#
# WARNING: Do NOT remove the internal git commit check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands (e.g., git reset, date).
# The internal grep is the actual gate. The "if" field is a hint only.
#
# How it works:
# - The /code-review skill writes
#   ~/.claude/review-markers/<repo-hash>.<session_id> with the sha256 hash of
#   `git diff --cached` when the review is clean. The marker lives under
#   $HOME (not inside the repo) so it never pollutes `git status` or risks
#   being accidentally committed.
# - This hook reads session_id from its JSON payload, recomputes
#   `git diff --cached | sha256sum` at commit time, and compares against
#   THIS session's marker. Match = the staged state was reviewed by this
#   session, allow the commit. Mismatch/missing = deny and redirect Claude
#   to run /code-review.
# - Per-session keying (vs. a singleton path keyed only by repo-hash)
#   prevents two parallel sessions in the same worktree from overwriting
#   each other's markers when they stage different diffs. Each session
#   writes its own marker; the gate checks the calling session's marker
#   specifically.
# - The marker auto-invalidates as soon as the staging area changes, so
#   re-staging after review correctly forces a re-review.

set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by code-review gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by code-review gate: could not parse tool-input JSON."

# Only gate Bash tool calls — exit 0 (no opinion) for everything else.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Only gate git commit commands — exit 0 (no opinion) for everything else.
# Match `git commit` at the start of the command OR after a shell separator
# (`&&`, `||`, `;`, `|`, `&`), so chained forms like `git add . && git commit`
# are also caught. The trailing `(\s|$)` ensures we don't match `git commit-tree`
# or other `git commit`-prefixed subcommands.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  # Not in a git repo — let git surface the error itself
  exit 0
fi

# Empty staged diff: amend-message-only, --allow-empty, or nothing to commit.
# No new content to review; let git decide whether the commit is valid.
if [ -z "$(git diff --cached 2>/dev/null)" ]; then
  exit 0
fi

# Honor in-chain marker writes. When the same Bash call chains
# `marker.sh write code-review` before `git commit`, the on-disk marker
# does not exist yet at PreToolUse time (the chain has not run), so the
# usual marker check below would deny. The in-chain marker.sh invocation
# is the same evidence the on-disk marker would later provide -- marker.sh
# is the only sanctioned writer in either case.
if _lib_chains_marker_write_before_commit "$COMMAND" code-review; then
  exit 0
fi

REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
CURRENT_HASH=$(git diff --cached | sha256sum | awk '{print $1}')

# Empty session_id (older Claude Code versions or payload-schema drift) can't
# key a per-session marker — fall through to deny.
if [ -n "$SESSION_ID" ]; then
  MARKER="$HOME/.claude/review-markers/$REPO_HASH.$SESSION_ID"
  if [ -f "$MARKER" ]; then
    MARKER_HASH=$(tr -d '[:space:]' < "$MARKER")
    if [ "$MARKER_HASH" = "$CURRENT_HASH" ]; then
      # Marker hash matches currently staged diff — review is current, allow.
      exit 0
    fi
  fi
fi

# No marker, or marker hash does not match the current staged state.
# Build the reason as a bash variable so the conditional marker-chain
# note can be interpolated; jq -Rs handles JSON-encoding safely
# regardless of what characters appear in the appended note.
emit_deny "Commit blocked by code-review gate: the currently staged changes have not been reviewed, or the staged state has changed since the last review. Run the /code-review skill now on the currently staged diff. When the review is clean (no blockers), the skill will record the review in ~/.claude/review-markers/ and this commit will be allowed through on retry. Do not ask the user for permission — run the skill, address any findings, and retry the commit."
