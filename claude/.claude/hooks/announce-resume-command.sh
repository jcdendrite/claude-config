#!/bin/bash
# hook-class: informational
# PostToolUse Edit|Write|MultiEdit hook: announces the resume-context
# command for a just-written /handoff or /brief continuity file
# (<config-dir>/handoffs/*-handoff.md, <config-dir>/briefs/*-task.md), on
# systemMessage (engineer) and hookSpecificOutput.additionalContext
# (model).
# This removes the dependency on the authoring model recalling
# handoff/SKILL.md §7 or brief/SKILL.md §7.5.
#
# Includes --cwd only when the payload's .cwd sits inside a linked
# worktree (git rev-parse --absolute-git-dir differs from
# --path-format=absolute --git-common-dir).
# A main-tree or unresolvable .cwd emits the bare command instead,
# matching what those skill sections prescribe.
# The git -C calls run only after the path glob has matched, each capped
# at 5s via _lib_capped.
#
# Fail posture: fail-silent, never blocks. Each of the following falls
# through to exit 0 with no output:
#   - missing _lib.sh
#   - unparseable stdin
#   - a non-matching tool or path
#   - a failed allowlist check
#   - a capped or erroring git call
# PostToolUse cannot deny, so there is nothing to fail closed against.
#
# Both interpolated values (the written file's path, and the linked
# worktree's root when present) must be non-empty and consist only of bytes
# in [A-Za-z0-9._/@+-], checked by the bash `case` glob in _passes_allowlist.
# A failing FILE_PATH emits nothing; a failing worktree root drops only --cwd.
# This closes the shell-quoting, terminal-escape, and newline-based
# structural case-glob bypass a tool-supplied path would otherwise open
# (set-session-title-from-branch.sh: 18-23, :151-155).
# It is a structural filter only, not general semantic-content filtering.
# LC_ALL=C is set script-wide, after the continuity-path glob, and is
# exported to child processes only if the caller had already exported it.
# $(...) strips NUL bytes and trailing newlines before the gate, so an
# announced path can differ from the real one.
# No byte outside the allowlist reaches the output either way.
#
# Paired glob site: the continuity-path case glob below is copied verbatim
# from consume-durable-continuity-file-on-read.sh:120.
#
# Known gaps: a session whose .cwd has drifted out of the worktree where the
# work lives gets a command with no --cwd, matching the pre-existing
# behavior of a resume launched from the wrong directory. A config dir or
# FILE_PATH containing a space produces no announcement at all. A worktree
# path containing a space instead drops only --cwd, falling back to the
# bare `resume-context $FILE_PATH` form. The continuity file's own
# §7/§7.5 text still carries the correct command either way. Resolving the
# bare `resume-context` name requires ~/.local/bin on PATH. install.sh
# manages this for bash/zsh; README documents the manual fish step. A
# same-character-set English directive -- a handoff/brief filename spelled
# as an instruction using only letters, digits, and the allowed
# punctuation -- still passes the allowlist and is echoed verbatim into
# additionalContext. This is a known, accepted residual, not a bug.
#
# Defense-in-depth: filters tool_name and file_path itself; does not rely
# solely on the settings.json matcher condition.
set -uo pipefail

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi
CONFIG_DIR=$(_lib_config_dir) || exit 0

INPUT=$(cat) || exit 0

TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null) || exit 0
case "$TOOL_NAME" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$FILE_PATH" ] || exit 0

case "$FILE_PATH" in
  "$CONFIG_DIR"/handoffs/*-handoff.md | "$CONFIG_DIR"/briefs/*-task.md) ;;
  *) exit 0 ;;
esac

# Bracket ranges are byte ranges only in the C locale.
LC_ALL=C

# Succeeds only when $1 is non-empty and every byte is in [A-Za-z0-9._/@+-].
# Matched in bash rather than grep, because BSD grep's -z still anchors ^/$ at each embedded newline.
_passes_allowlist() {
  case "$1" in
    '' | *[!A-Za-z0-9._/@+-]*) return 1 ;;
  esac
}

_passes_allowlist "$FILE_PATH" || exit 0

WORKTREE_ROOT=""
PAYLOAD_CWD=$(printf '%s\n' "$INPUT" | _lib_jq -r '.cwd // empty' 2>/dev/null) || exit 0
if [ -n "$PAYLOAD_CWD" ] && _lib_capped git -C "$PAYLOAD_CWD" rev-parse --git-dir >/dev/null 2>&1; then
  GIT_DIR_ABS=$(_lib_capped git -C "$PAYLOAD_CWD" rev-parse --absolute-git-dir 2>/dev/null)
  GIT_COMMON_DIR=$(_lib_capped git -C "$PAYLOAD_CWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -n "$GIT_DIR_ABS" ] && [ -n "$GIT_COMMON_DIR" ] && [ "$GIT_DIR_ABS" != "$GIT_COMMON_DIR" ]; then
    CANDIDATE_ROOT=$(_lib_capped git -C "$PAYLOAD_CWD" rev-parse --show-toplevel 2>/dev/null)
    if _passes_allowlist "$CANDIDATE_ROOT"; then
      WORKTREE_ROOT="$CANDIDATE_ROOT"
    fi
  fi
fi

if [ -n "$WORKTREE_ROOT" ]; then
  RESUME_COMMAND="resume-context --cwd $WORKTREE_ROOT $FILE_PATH"
else
  RESUME_COMMAND="resume-context $FILE_PATH"
fi

# shellcheck disable=SC2016 # single-quoted on purpose: $cmd is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
_lib_jq -n --arg cmd "$RESUME_COMMAND" \
  '{
    systemMessage: ("Resume this continuity file with: " + $cmd),
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: ("The resume command for the continuity file just written is: " + $cmd + ". It was computed from the resolved config dir and the working directory of this session -- use it verbatim instead of reconstructing it.")
    }
  }' \
  2>/dev/null || true

exit 0
