#!/bin/bash
# hook-class: informational
# PostToolUse Edit|Write|MultiEdit hook: when the tool call just wrote a
# /handoff or /brief continuity file (<config-dir>/handoffs/*-handoff.md,
# <config-dir>/briefs/*-task.md), independently compute and announce its
# resume-context command on both hook output channels -- systemMessage for
# the engineer, hookSpecificOutput.additionalContext for the model -- so
# display no longer depends on the authoring model recalling
# handoff/SKILL.md's §7 or brief/SKILL.md's §7.5.
#
# --cwd is included only when the payload's .cwd sits inside a linked
# worktree (git rev-parse --absolute-git-dir differs from
# --path-format=absolute --git-common-dir), matching what those skill
# sections themselves prescribe; a main-tree or unresolvable .cwd emits the
# bare command instead. The git -C calls that decide this run only after
# the path glob below has already matched a continuity file, and each is
# wrapped in _lib_capped (5s): unlike require-worktree-for-file-writes.sh's
# identical comparison, this hook buys no correctness benefit from blocking
# (the write already succeeded; --cwd is a cosmetic annotation), so a hang
# here would trade the gate hook's justified risk for an unjustified one.
#
# Fail posture: fail-silent, never blocks. Every failure path (missing
# _lib.sh, unparseable stdin, a non-matching tool or path, a failed
# allowlist check, a capped or erroring git call) falls through to exit 0
# with no output; PostToolUse cannot deny, so there is nothing to fail
# closed against.
#
# Both interpolated values (the written file's path, and the linked
# worktree's root when present) must match ^[A-Za-z0-9._/@+-]+$ under
# LC_ALL=C or nothing is emitted at all -- closes the shell-quoting,
# terminal-escape, and additionalContext semantic-injection exposures a
# tool-supplied path would otherwise open (set-session-title-from-branch.sh:
# 18-23, :151-155).
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
# bare `resume-context` name requires ~/.local/bin on PATH (install.sh
# manages this for bash/zsh; README documents the manual fish step).
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

ALLOWLIST_RE='^[A-Za-z0-9._/@+-]+$'
# -z (null-data): treats the whole value as one line, so ^/$ anchor its
# start/end rather than each embedded line's -- a bash `case` glob matches
# across embedded newlines, and the bracket class above excludes \n, so a
# plain (non -z) grep -q would wrongly pass on a line that happens to fully
# match even though the value as a whole carries other text.
printf '%s' "$FILE_PATH" | LC_ALL=C grep -Eqz "$ALLOWLIST_RE" || exit 0

WORKTREE_ROOT=""
PAYLOAD_CWD=$(printf '%s\n' "$INPUT" | _lib_jq -r '.cwd // empty' 2>/dev/null) || exit 0
if [ -n "$PAYLOAD_CWD" ] && _lib_capped git -C "$PAYLOAD_CWD" rev-parse --git-dir >/dev/null 2>&1; then
  GIT_DIR_ABS=$(_lib_capped git -C "$PAYLOAD_CWD" rev-parse --absolute-git-dir 2>/dev/null)
  GIT_COMMON_DIR=$(_lib_capped git -C "$PAYLOAD_CWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -n "$GIT_DIR_ABS" ] && [ -n "$GIT_COMMON_DIR" ] && [ "$GIT_DIR_ABS" != "$GIT_COMMON_DIR" ]; then
    CANDIDATE_ROOT=$(_lib_capped git -C "$PAYLOAD_CWD" rev-parse --show-toplevel 2>/dev/null)
    if [ -n "$CANDIDATE_ROOT" ] && printf '%s' "$CANDIDATE_ROOT" | LC_ALL=C grep -Eqz "$ALLOWLIST_RE"; then
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
