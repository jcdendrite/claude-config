#!/bin/bash
# hook-class: gate
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
# - This hook recomputes the same path-scoped diff hash at commit time and
#   looks for any marker under this repo-hash holding that value. Match =
#   the staged skill changes were reviewed, allow the commit. Mismatch/missing
#   = deny and redirect Claude to run /skill-review.
# - The <session_id> in the filename is a WRITE-side key only: it prevents
#   two parallel sessions in the same worktree from overwriting each other's
#   markers when they stage different diffs. The read globs across it,
#   because the stored hash — not the filename — is what proves the review
#   covered this diff. Reading the session key as an authorization predicate
#   would deny a resumed session (new session_id) a review it already
#   completed against the identical staged state.
# - The marker is scoped to SKILL.md diffs only (not the full staged diff),
#   so re-staging non-SKILL.md files after a clean skill-review does not
#   invalidate the marker.
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
  emit_deny "Blocked by skill-review gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by skill-review gate: could not parse tool-input JSON."

# Filter by tool name in the hook itself rather than relying on the
# settings.json matcher — the "if" field is a hint only (see the warning
# above), and a non-Bash payload yields an empty COMMAND that would otherwise
# reach the git-commit grep and pass by accident rather than by intent.
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

# Resolve the repo from the payload's cwd rather than this hook process's
# ambient cwd, and thread that one root through every git call below. The
# marker's path (repo hash) and its value (staged SKILL.md diff hash) must
# describe the same tree: resolving the root one way and hashing the diff
# another lets a session whose shell drifted to a different working tree of
# the same repo satisfy the gate with a review of a tree nobody reviewed.
CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  # Not in a git repo — let git surface the error itself
  exit 0
fi

# Early exit: if no SKILL.md files are staged, this hook is a no-op.
# Commits that don't touch any skill file are not gated by skill-review.
SKILL_DIFF=$(git -C "$REPO_ROOT" diff --cached --name-only -- 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md')
if [ -z "$SKILL_DIFF" ]; then
  exit 0
fi

# Empty staged diff: amend-message-only, --allow-empty, or nothing to commit.
# No new content to review; let git decide whether the commit is valid.
if [ -z "$(git -C "$REPO_ROOT" diff --cached 2>/dev/null)" ]; then
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

if [ "${#STAGED_SKILL_PATHS[@]}" -gt 0 ]; then
  STAGED_BLOB_DIR=$(mktemp -d)
  trap 'rm -rf "$STAGED_BLOB_DIR"' EXIT

  STAGED_BLOB_PATHS=()
  for STAGED_PATH in "${STAGED_SKILL_PATHS[@]}"; do
    BLOB_PATH="$STAGED_BLOB_DIR/$STAGED_PATH"
    mkdir -p "$(dirname "$BLOB_PATH")"
    if git -C "$REPO_ROOT" show ":$STAGED_PATH" > "$BLOB_PATH" 2>/dev/null; then
      STAGED_BLOB_PATHS+=("$BLOB_PATH")
    fi
    # If git show fails (unmerged path, index corruption), skip — the
    # marker-check phase below will deny via its own hash-mismatch path.
  done

  if [ "${#STAGED_BLOB_PATHS[@]}" -gt 0 ]; then
    VALIDATOR_STDERR=$("$VALIDATOR_PYTHON" "$VALIDATOR_SCRIPT" "${STAGED_BLOB_PATHS[@]}" 2>&1)
    VALIDATOR_EXIT=$?
    if [ "$VALIDATOR_EXIT" -ne 0 ]; then
      # Strip the tmp-dir prefix so the user sees the original repo-relative
      # SKILL.md path in the deny reason rather than /tmp/tmp.XXXX/...
      VALIDATOR_STDERR=${VALIDATOR_STDERR//"$STAGED_BLOB_DIR/"/}
      emit_deny "Commit blocked by skill-management structural validator: ${VALIDATOR_STDERR}"
      exit 0
    fi
  fi
fi

# Corpus budget warning: check aggregate description+when_to_use chars across
# all model-invokable skills in this repo against the Claude Code listing budget.
# Non-blocking — exits 0 regardless; hard enforcement is in pytest/CI.
# Uses the same scoped pathspecs as SKILL_DIFF above (no bare *SKILL.md glob,
# which would sweep in vendored or fixture files).
CORPUS_PATHS=()
while IFS= read -r CORPUS_PATH; do
  [ -n "$CORPUS_PATH" ] && CORPUS_PATHS+=("$CORPUS_PATH")
done < <(git -C "$REPO_ROOT" ls-files 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' 2>/dev/null)

# Overlay staged blobs: replace corpus path with staged blob path for any
# staged SKILL.md (so the warning reflects the post-commit state of staged files).
if [ "${#CORPUS_PATHS[@]}" -gt 0 ]; then
  OVERLAY_PATHS=()
  for CORPUS_PATH in "${CORPUS_PATHS[@]}"; do
    # Check if this path has a staged version (already materialized in STAGED_BLOB_DIR).
    BLOB_CANDIDATE="${STAGED_BLOB_DIR:-}/$CORPUS_PATH"
    if [ -n "${STAGED_BLOB_DIR:-}" ] && [ -f "$BLOB_CANDIDATE" ]; then
      OVERLAY_PATHS+=("$BLOB_CANDIDATE")
    else
      OVERLAY_PATHS+=("$CORPUS_PATH")
    fi
  done

  CORPUS_STDERR=$(timeout 10s "$VALIDATOR_PYTHON" "$VALIDATOR_SCRIPT" --corpus "${OVERLAY_PATHS[@]}" 2>&1 || true)
  if [ -n "$CORPUS_STDERR" ]; then
    printf 'skill-management: corpus budget warning: %s\n' "$CORPUS_STDERR" >&2
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
CURRENT_HASH=$(git -C "$REPO_ROOT" diff --cached -- 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' | sha256sum | awk '{print $1}')

# Allow when any marker under this repo-hash holds the currently staged skill
# diff's hash. The stored hash is the authorization — it proves a review
# covered exactly this diff — so the question is "has this diff been
# reviewed?", not "did this session review it?". An empty CURRENT_HASH
# (sha256sum unavailable) never matches, so a hashing failure denies.
if _lib_marker_value_present "$HOME/.claude/skill-review-markers" "$CURRENT_HASH" "$REPO_HASH."; then
  exit 0
fi

# No marker, or marker hash does not match the current staged skill state.
# Build the reason as a bash variable so the conditional marker-chain
# note can be interpolated; jq -Rs handles JSON-encoding safely
# regardless of what characters appear in the appended note.
emit_deny "Commit blocked by skill-review gate: Staged skill changes have not been audited by /skill-review. Run /skill-review on the staged SKILL.md diff; the skill must produce an explicit behavioral-equivalence table for any removed or shortened lines before committing."
