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
#   ~/.claude/code-review-markers/<repo-hash>.<session_id> with the sha256 hash of
#   `git diff --cached` when the review is clean. The marker lives under
#   $HOME (not inside the repo) so it never pollutes `git status` or risks
#   being accidentally committed.
# - This hook recomputes `git diff --cached | sha256sum` at commit time and
#   looks for any marker under this repo-hash holding that value. Match =
#   the staged state was reviewed, allow the commit. Mismatch/missing = deny
#   and redirect Claude to run /code-review.
# - The <session_id> in the filename is a WRITE-side key only: it prevents
#   two parallel sessions in the same worktree from overwriting each other's
#   markers when they stage different diffs. The read globs across it,
#   because the stored hash — not the filename — is what proves the review
#   covered this diff. Reading the session key as an authorization predicate
#   would deny a resumed session (new session_id) a review it already
#   completed against the identical staged state.
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
CURRENT_HASH=$(git diff --cached | sha256sum | awk '{print $1}')

# Allow when any marker under this repo-hash holds the currently staged
# diff's hash. The stored hash is the authorization — it proves a review
# covered exactly this diff — so the question is "has this diff been
# reviewed?", not "did this session review it?". An empty CURRENT_HASH
# (sha256sum unavailable) never matches, so a hashing failure denies.
if _lib_marker_value_present "$HOME/.claude/code-review-markers" "$CURRENT_HASH" "$REPO_HASH."; then
  exit 0
fi

# No marker, or marker hash does not match the current staged state.
# Build the reason as a bash variable so the conditional marker-chain
# note can be interpolated; jq -Rs handles JSON-encoding safely
# regardless of what characters appear in the appended note.
emit_deny "Commit blocked by code-review gate: the currently staged changes have not been reviewed, or the staged state has changed since the last review. Run the /code-review skill now on the currently staged diff. When the review is clean (no blockers), the skill will record the review in ~/.claude/code-review-markers/ and this commit will be allowed through on retry. Do not ask the user for permission — run the skill, address any findings, and retry the commit."
