#!/usr/bin/env bash
# hook-class: gate
# Gate: block Edit, Write, and MultiEdit to files in the main working tree of
# a repo where worktree discipline is active. Three activation markers:
#   - <repo>/.claude/worktree-required  (committed repo sentinel — opt-out has no effect)
#   - ~/.claude/worktree-required       (machine-level personal default)
#   - <repo>/.claude/worktree-optout    (per-repo opt-out of machine default only)
# Companion to require-worktree-for-git-writes.sh, which blocks git write ops.
# Also denies a write into a linked worktree already claimed by a different
# live session via _lib_worktree_collision_guard (_lib.sh) — see that
# function's header for the full design and known gaps.
#
# Known gaps: a write that freshly acquires the worktree lock reports that
# via additionalContext, decided by a plain `[ -e ... ]` pre-check on the
# lock file just before the collision-guard call. This pre-check is wired
# identically to require-worktree-for-git-writes.sh's own pre-check at its
# one call site here. See that file's Known gaps for the full
# TOCTOU/uncapped-stat analysis.
#
# Known exclusion: paths under $HOME/.claude/ are always exempt —
# they are Claude Code harness/skill infrastructure, never project work.
# Assumption: no project repo will be placed directly under $HOME/.claude/
# (a checkout at $HOME/.claude/my-project/ would be fully exempt). This is
# acceptable against the threat model (concurrent-session races), not an
# adversarial-relocation scenario.
# Under stow directory-fold, $HOME/.claude/ is a symlink to the package's
# .claude/ directory; stow-managed files (e.g., ~/.claude/settings.json →
# repo's claude/.claude/settings.json) also satisfy the raw-string prefix
# match and are exempt. realpath cannot be used to detect this: it would
# resolve $HOME/.claude/plans/new.md to the repo root path, which does not
# match the home prefix, re-introducing the original bug. This is
# acceptable: contribution sessions must use the worktree path
# (claude/.claude/...); any write arriving via the $HOME/.claude/ prefix
# is infrastructure activity, not contribution work.
#
# Defensive: prevent GIT_DIR / GIT_WORK_TREE env overrides from making the
# main tree impersonate a linked worktree via rev-parse output.
# HOME is not unset: its value comes from the OS user session (set by the
# system for the running user, not from tool_input JSON), so it is trusted
# at the same level as other shell-environment configuration — unlike git
# env vars which git itself reads and which a committed config can pre-set.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by worktree-enforcement hook (file-writes): could not parse tool-input JSON. Refusing to evaluate worktree discipline under malformed input."

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Only applies to file-writing tools; other tools pass through immediately.
case "$TOOL_NAME" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

# Nothing to check without a path.
[ -z "$FILE_PATH" ] && exit 0

# Claude Code's own infrastructure paths — plans, todos, memory, shell
# snapshots, settings — are written by the harness and skills as normal
# operation, never as project feature work. Exempt them even when
# ~/.claude resolves (via stow directory-folding) into a repo that has
# opted into worktree discipline.
# $HOME is normalized first: a trailing slash would make the prefix a
# double-slash that never matches the harness's single-slash file_path,
# and an empty $HOME must not collapse the pattern to bare /.claude/.
# Paths containing '..' are not exempted: the case glob matches on the
# literal string, so $HOME/.claude/../other/file would satisfy the prefix
# pattern without actually resolving inside $HOME/.claude/. Rejecting any
# path that contains '/..' closes that traversal vector before the match.
# This is a union, not a swap, with the resolved-config-dir exemption below:
# the literal $HOME/.claude match is mechanism-bound to stow-fold detection
# (realpath can't be used — see the header comment) and stays even when
# CLAUDE_CONFIG_DIR relocates the harness infrastructure elsewhere.
home_norm="${HOME%/}"
case "$FILE_PATH" in
  */../*|*/..)
    ;; # traversal present — do not exempt either arm; fall through to repo-walk
  *)
    if [ -n "$home_norm" ]; then
      case "$FILE_PATH" in
        "$home_norm"/.claude/*)
          exit 0 ;;
      esac
    fi
    # Harness-infrastructure exemption at the resolved config dir, which
    # differs from $HOME/.claude only when CLAUDE_CONFIG_DIR is set — same
    # rationale as the literal match above, true regardless of which
    # directory currently holds that infrastructure. A failed resolution
    # grants no exemption here, same fail-safe direction as the $HOME arm's
    # own empty-$HOME guard above.
    config_dir=$(_lib_config_dir) || config_dir=""
    if [ -n "$config_dir" ]; then
      case "$FILE_PATH" in
        "$config_dir"/*)
          exit 0 ;;
      esac
    fi
    ;;
esac

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

# Three-marker gate: repo sentinel, machine sentinel, per-repo opt-out.
_lib_worktree_enforcement_active "$REPO_ROOT" || exit 0

# Detect main tree vs linked worktree using the same git-dir comparison as
# require-worktree-for-git-writes.sh: in a linked worktree, --absolute-git-dir
# and --git-common-dir differ; in the main tree they resolve to the same path.
GIT_DIR_ABS=$(git -C "$lookup_dir" rev-parse --absolute-git-dir 2>/dev/null)
GIT_COMMON_DIR=$(git -C "$lookup_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)

# Deliberately no _lib_stray_marker_hint here: this is a git-state parse
# failure (rev-parse produced no output after REPO_ROOT already resolved),
# not the "enforcement active, blocking on main tree" case the hint targets.
# Untestable deterministically without simulating a race between the two
# rev-parse calls above, so no regression test pins this boundary.
if [ -z "$GIT_DIR_ABS" ] || [ -z "$GIT_COMMON_DIR" ]; then
  emit_deny "Blocked by worktree-enforcement hook (file-writes): could not determine git state for '$FILE_PATH'. This is a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout. Run $TOOL_NAME from inside a linked worktree — cd into an existing worktree under .claude/worktrees/, or spawn an agent with isolation: worktree."
  exit 0
fi

# Already in a linked worktree: check for a same-worktree collision with
# another live session before allowing. See require-worktree-for-git-writes.sh
# and _lib_worktree_collision_guard (_lib.sh) for the full design.
if [ "$GIT_DIR_ABS" != "$GIT_COMMON_DIR" ]; then
  # See _lib_worktree_lock_absent (_lib.sh) for the pre-check contract.
  WAS_UNLOCKED=false
  _lib_worktree_lock_absent "$GIT_DIR_ABS" && WAS_UNLOCKED=true
  COLLISION_REASON=$(_lib_worktree_collision_guard "$lookup_dir" "$GIT_COMMON_DIR") || {
    emit_deny "Blocked by worktree-enforcement hook (file-writes): $TOOL_NAME targets '$FILE_PATH' — $COLLISION_REASON. This is a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required)."
    exit 0
  }
  if $WAS_UNLOCKED; then
    _lib_emit_allow_with_context "$TOOL_NAME on '$FILE_PATH' re-acquired the worktree lock for $lookup_dir, since worktree discipline allows only one live session per linked worktree at a time. If this was unintended, run 'git worktree unlock $lookup_dir' again."
  fi
  exit 0
fi

# In the main working tree: deny.
REL_PATH="${FILE_PATH#"$REPO_ROOT"/}"
emit_deny "Blocked by worktree-enforcement hook (file-writes): $TOOL_NAME targets '$FILE_PATH' which is in the main working tree of a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout. Write the file at its worktree path instead — e.g. .claude/worktrees/<branch>/$REL_PATH — or spawn an agent with isolation: worktree.$(_lib_stray_marker_hint "$REPO_ROOT")"
exit 0
