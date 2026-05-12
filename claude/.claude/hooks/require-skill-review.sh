#!/bin/bash
# Gate: require /skill-review before git commit when SKILL.md files are staged,
# verified via marker file.
#
# WARNING: Do NOT remove the internal git commit check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands (e.g., git reset, date).
# The internal grep is the actual gate. The "if" field is a hint only.
#
# How it works:
# - The /skill-review skill writes
#   ~/.claude/skill-review-markers/<repo-hash>.<session_id> with the sha256 hash
#   of `git diff --cached -- 'claude/.claude/skills/**/SKILL.md'` when the review
#   is clean. The marker lives under $HOME (not inside the repo) so it never
#   pollutes `git status` or risks being accidentally committed.
# - This hook reads session_id from its JSON payload, recomputes the same
#   path-scoped diff hash at commit time, and compares against THIS session's
#   marker. Match = the staged skill changes were reviewed by this session,
#   allow the commit. Mismatch/missing = deny and redirect Claude to run
#   /skill-review.
# - Per-session keying (vs. a singleton path keyed only by repo-hash)
#   prevents two parallel sessions in the same worktree from overwriting
#   each other's markers when they stage different diffs. Each session
#   writes its own marker; the gate checks the calling session's marker
#   specifically.
# - The marker is scoped to SKILL.md diffs only (not the full staged diff),
#   so re-staging non-SKILL.md files after a clean skill-review does not
#   invalidate the marker.

. "$(dirname "$0")/_lib.sh"

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

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

# Early exit: if no SKILL.md files are staged, this hook is a no-op.
# Commits that don't touch any skill file are not gated by skill-review.
SKILL_DIFF=$(git diff --cached --name-only -- 'claude/.claude/skills/**/SKILL.md')
if [ -z "$SKILL_DIFF" ]; then
  exit 0
fi

# Empty staged diff: amend-message-only, --allow-empty, or nothing to commit.
# No new content to review; let git decide whether the commit is valid.
if [ -z "$(git diff --cached 2>/dev/null)" ]; then
  exit 0
fi

REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
CURRENT_HASH=$(git diff --cached -- 'claude/.claude/skills/**/SKILL.md' | sha256sum | awk '{print $1}')

# Empty session_id (older Claude Code versions or payload-schema drift) can't
# key a per-session marker — fall through to deny.
if [ -n "$SESSION_ID" ]; then
  MARKER="$HOME/.claude/skill-review-markers/$REPO_HASH.$SESSION_ID"
  if [ -f "$MARKER" ]; then
    MARKER_HASH=$(tr -d '[:space:]' < "$MARKER")
    if [ "$MARKER_HASH" = "$CURRENT_HASH" ]; then
      # Marker hash matches currently staged skill diff — review is current, allow.
      exit 0
    fi
  fi
fi

# No marker, or marker hash does not match the current staged skill state.
# Build the reason as a bash variable so the conditional marker-chain
# note can be interpolated; jq -Rs handles JSON-encoding safely
# regardless of what characters appear in the appended note.
REASON="Commit blocked by skill-review gate: Staged skill changes have not been audited by /skill-review. Run /skill-review on the staged SKILL.md diff; the skill must produce an explicit behavioral-equivalence table for any removed or shortened lines before committing. If the skill cannot run, see: https://github.com/jcdendrite/claude-config/blob/main/docs/hooks.md#gate-deadlock-recovery"
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
