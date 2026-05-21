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
#   of `git diff --cached -- 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md'` when the review
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
SKILL_DIFF=$(git diff --cached --name-only -- 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md')
if [ -z "$SKILL_DIFF" ]; then
  exit 0
fi

# Empty staged diff: amend-message-only, --allow-empty, or nothing to commit.
# No new content to review; let git decide whether the commit is valid.
if [ -z "$(git diff --cached 2>/dev/null)" ]; then
  exit 0
fi

# Structural validation phase: run validate_skill_structure.py on every staged
# SKILL.md before the behavioral-review marker check. Structural violations
# (invalid YAML, over-length descriptions) are cheap and deterministic; catching
# them here prevents malformed files from reaching the behavioral-equivalence
# audit and gives a precise error message pointing at the offending file.
#
# The validator must see the staged blob content, not the working-tree file —
# otherwise a stage-broken-then-fix-locally-without-restage flow would let
# malformed YAML slip past the gate. Materialize each staged blob via
# `git show :<path>` into a tmp tree that mirrors the original repo path, so
# the validator's error messages reference recognizable paths after the tmp
# prefix is stripped below.
STAGED_SKILL_PATHS=()
while IFS= read -r STAGED_PATH; do
  [ -n "$STAGED_PATH" ] && STAGED_SKILL_PATHS+=("$STAGED_PATH")
done <<< "$SKILL_DIFF"

if [ "${#STAGED_SKILL_PATHS[@]}" -gt 0 ]; then
  STAGED_BLOB_DIR=$(mktemp -d)
  trap 'rm -rf "$STAGED_BLOB_DIR"' EXIT

  STAGED_BLOB_PATHS=()
  for STAGED_PATH in "${STAGED_SKILL_PATHS[@]}"; do
    BLOB_PATH="$STAGED_BLOB_DIR/$STAGED_PATH"
    mkdir -p "$(dirname "$BLOB_PATH")"
    if git show ":$STAGED_PATH" > "$BLOB_PATH" 2>/dev/null; then
      STAGED_BLOB_PATHS+=("$BLOB_PATH")
    fi
    # If git show fails (unmerged path, index corruption), skip — the
    # marker-check phase below will deny via its own hash-mismatch path.
  done

  if [ "${#STAGED_BLOB_PATHS[@]}" -gt 0 ]; then
    VALIDATOR_SCRIPT="$(dirname "$0")/../scripts/validate_skill_structure.py"
    # Prefer the plugin's persistent-venv python (provisioned by the
    # SessionStart hook against ${CLAUDE_PLUGIN_DATA}/venv) so the validator
    # finds pyyaml without the consumer running a manual pip install. Fall
    # back to system python3 — covers the contributor pytest path, which
    # imports the validator directly without going through plugin hooks, and
    # the brief window before the first SessionStart provisions the venv.
    VALIDATOR_PYTHON="python3"
    if [ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -x "${CLAUDE_PLUGIN_DATA}/venv/bin/python" ]; then
      VALIDATOR_PYTHON="${CLAUDE_PLUGIN_DATA}/venv/bin/python"
    fi
    VALIDATOR_STDERR=$("$VALIDATOR_PYTHON" "$VALIDATOR_SCRIPT" "${STAGED_BLOB_PATHS[@]}" 2>&1)
    VALIDATOR_EXIT=$?
    if [ "$VALIDATOR_EXIT" -ne 0 ]; then
      # Strip the tmp-dir prefix so the user sees the original repo-relative
      # SKILL.md path in the deny reason rather than /tmp/tmp.XXXX/...
      VALIDATOR_STDERR=${VALIDATOR_STDERR//"$STAGED_BLOB_DIR/"/}
      REASON="Commit blocked by skill-management structural validator: ${VALIDATOR_STDERR}"
      REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
      printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
      exit 0
    fi
  fi
fi

# Honor in-chain marker writes. When the same Bash call chains
# `marker.sh write skill-review` before `git commit`, the on-disk marker
# does not exist yet at PreToolUse time (the chain has not run), so the
# usual marker check below would deny. The in-chain marker.sh invocation
# is the same evidence the on-disk marker would later provide -- marker.sh
# is the only sanctioned writer in either case. The structural validator
# above still fires, so malformed SKILL.md files cannot slip through this
# bypass.
if _lib_chains_marker_write_before_commit "$COMMAND" skill-review; then
  exit 0
fi

REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
CURRENT_HASH=$(git diff --cached -- 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' | sha256sum | awk '{print $1}')

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
REASON="Commit blocked by skill-review gate: Staged skill changes have not been audited by /skill-review. Run /skill-review on the staged SKILL.md diff; the skill must produce an explicit behavioral-equivalence table for any removed or shortened lines before committing."
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
