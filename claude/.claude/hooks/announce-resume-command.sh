#!/usr/bin/env bash
# hook-class: informational
# PostToolUse Edit|Write|MultiEdit hook: announces the resume-context
# command for a just-written /handoff or /brief continuity file
# (<config-dir>/handoffs/*-handoff.md, <config-dir>/briefs/*-task.md), on
# systemMessage (engineer) and hookSpecificOutput.additionalContext
# (model).
# This removes the dependency on the authoring model recalling
# handoff/SKILL.md §7 or brief/SKILL.md §7.5.
#
# Includes a session-derived --cwd only when the payload's .cwd sits inside a
# linked worktree (git rev-parse --absolute-git-dir differs from
# --path-format=absolute --git-common-dir).
# On the shell route, a main-tree `.cwd` whose file names no usable target, or an unresolvable `.cwd`, gets the bare command.
# The git -C calls run only after the path glob has matched, each capped
# at 5s via _lib_capped.
# It announces only /resume-context <file name> when all of these hold:
#   - _lib_resume_context_verdict returns load: the written file's own resume
#     command (_lib_continuity_file_resume_cwd) names this session's linked
#     worktree, or names no --cwd in a main checkout.
#   - The file name is one the slash script accepts: a regular file directly in
#     handoffs/ or briefs/, not a symlink, whose name starts with neither a dash
#     nor a dot.
#   - The name does not start with an @. The slash script accepts one, but the client may treat a leading @ in a slash argument as a file mention.
#     That client behavior is assumed, not observed, and the hook withholds the slash form as a precaution.
# A brief's `load` announce offers a new session before `/clear`, because a brief is work for another session.
# In every other case it announces only the shell command, with one of four reasons:
#   - the session's worktree could not be determined;
#   - a file the slash script refuses by name or cannot list, or whose name starts with an @,
#     given only when the verdict would otherwise be `load`, so an unknown-session reason or a
#     mismatch reason takes precedence over it;
#   - a linked session whose file does not name this worktree as an absolute --cwd;
#   - a session outside a linked worktree whose file names a target directory
#     (or whose resume target could not be resolved to one directory, which
#     includes a file carrying --cwd text in prose only). That message says
#     "this directory" rather than "this worktree".
# In the last two cases, a usable file target (absolute, allowlisted, and not
# this worktree) becomes the --cwd of the advertised shell command, and the
# reason names it. Without a usable target, the command keeps this worktree's
# --cwd (linked) or has none (outside a linked worktree).
# `systemMessage` puts the announced command alone on its second line in single backticks, not in a fenced block.
# The phone client prefixes every `systemMessage` line, fence lines included (observed once; Claude Code does not document it).
# `additionalContext` is one line telling the model to relay that one route, its command fenced, and run nothing.
# A file-sourced --cwd is the file's own text, not a measurement by this hook,
# and additionalContext labels it so and carries the reason.
# When the --cwd is file-sourced, the command equals the one
# resume-context-same-session.sh prints as SHELL_COMMAND for the same file,
# except that the script spells the target by its canonical path (symlinks and
# `.` and `..` components resolved).
# A session outside a linked worktree is also what a removed or git-refused
# worktree looks like (see _lib_linked_worktree_root), so such a session
# announces /resume-context for a file that names no --cwd.
#
# Fail posture: fail-silent, never blocks. Each of the following falls
# through to exit 0 with no output:
#   - missing _lib.sh
#   - unparseable stdin
#   - a non-matching tool or path
#   - a failed allowlist check
# A capped or erroring git call (_lib_linked_worktree_root exits 1) still
# announces the bare command, without the slash form and with a "worktree
# could not be determined" reason.
# PostToolUse cannot deny, so there is nothing to fail closed against.
#
# Each interpolated value (the written file's path, the linked worktree's
# root when present, and the file's own --cwd target when usable) must be
# non-empty and consist only of bytes in [A-Za-z0-9._/@+-], checked by
# _lib_passes_path_char_allowlist in _lib.sh.
# A failing FILE_PATH emits nothing.
# A failing worktree root drops --cwd and the slash form.
# A failing file target is treated as a target that could not be resolved.
# This closes the shell-quoting, terminal-escape, and newline-based
# structural case-glob bypass a tool-supplied path would otherwise open
# (set-session-title-from-branch.sh: 18-23, :151-155).
# It is a structural filter only, not general semantic-content filtering.
# $(...) strips NUL bytes and trailing newlines before the gate, so an
# announced path can differ from the real one.
# No byte outside the allowlist reaches the output either way.
#
# Paired glob sites: the continuity-path case glob below is copied verbatim
# from the `case "$FILE_PATH" in` arm of consume-durable-continuity-file-on-read.sh.
# _lib_collect_pending_continuity_files in _lib.sh defines the same two globs as the pending set; a glob change reaches it too.
# The suffixes are spelled again in resume-context-same-session.sh's argument matching; a suffix change reaches it too.
# test_continuity_glob_parity.py pins the announce and consume patterns to each other and the helper's pending set to a subset of both.
#
# Known gaps:
#   - A session whose .cwd has drifted out of the worktree where the work
#     lives, writing a file with no usable --cwd, announces a command without
#     that worktree's --cwd, matching a resume launched from the wrong directory.
#   - A config dir or FILE_PATH containing a space produces no announcement at all.
#   - A worktree path containing a space instead drops both --cwd and the
#     /resume-context form, falling back to the bare `resume-context $FILE_PATH`
#     form. The continuity file's own §7/§7.5 text still carries the correct
#     command either way.
#   - Resolving the bare `resume-context` name requires ~/.local/bin on PATH.
#     install.sh manages this for bash/zsh; README documents the manual fish step.
#   - A same-character-set English directive -- a handoff/brief filename, or the
#     file's own --cwd directory, spelled as an instruction using only letters,
#     digits, and the allowed punctuation -- still passes the allowlist and is
#     echoed verbatim into additionalContext. This is a known, accepted residual,
#     not a bug.
#   - A file-sourced --cwd may name any directory made of allowlisted bytes, and the
#     engineer copies the command with it without this hook having checked the directory.
#   - The file's own --cwd is compared raw, with no canonicalization (symlinks and
#     `.` and `..` components), so a non-canonical spelling of this worktree gets the
#     shell-only announce (its reason names that spelling) while /resume-context loads it.
#   - A session whose `.cwd` drifted out of its worktree gets the shell route for a file naming
#     that worktree, because the hook compares the payload `.cwd`, not where the work lives.
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

# Glob copy: see "Paired glob sites" in the header.
case "$FILE_PATH" in
  "$CONFIG_DIR"/handoffs/*-handoff.md | "$CONFIG_DIR"/briefs/*-task.md) ;;
  *) exit 0 ;;
esac

_lib_passes_path_char_allowlist "$FILE_PATH" || exit 0

PAYLOAD_CWD=$(printf '%s\n' "$INPUT" | _lib_jq -r '.cwd // empty' 2>/dev/null) || exit 0
SESSION_STATE="unknown"
WORKTREE_ROOT=""
if WORKTREE_ROOT=$(_lib_linked_worktree_root "$PAYLOAD_CWD"); then
  if [ -n "$WORKTREE_ROOT" ]; then
    SESSION_STATE="linked"
  else
    SESSION_STATE="not-linked"
  fi
else
  WORKTREE_ROOT=""
fi

if [ -n "$WORKTREE_ROOT" ]; then
  RESUME_COMMAND="resume-context --cwd $WORKTREE_ROOT $FILE_PATH"
else
  RESUME_COMMAND="resume-context $FILE_PATH"
fi

FILE_NAME="${FILE_PATH##*/}"
# The slash script refuses a leading-dash argument and lists only _lib_is_pending_continuity_file paths.
# This hook also refuses a leading `@`; the header says why.
SLASH_NAME_ACCEPTED=false
case "$FILE_NAME" in
  -* | @*) ;;
  *)
    if _lib_is_pending_continuity_file "$CONFIG_DIR" "$FILE_PATH"; then
      SLASH_NAME_ACCEPTED=true
    fi
    ;;
esac
FILE_TARGET=$(_lib_continuity_file_resume_cwd "$FILE_PATH")
# The helper already allowlists a usable target. This re-check keeps a helper regression from reaching the command line.
if [ -n "$FILE_TARGET" ] && [ "$FILE_TARGET" != "$_LIB_RESUME_CWD_UNUSABLE" ] \
  && ! _lib_passes_path_char_allowlist "$FILE_TARGET"; then
  FILE_TARGET="$_LIB_RESUME_CWD_UNUSABLE"
fi
FILE_TARGET_USABLE=false
if [ -n "$FILE_TARGET" ] && [ "$FILE_TARGET" != "$_LIB_RESUME_CWD_UNUSABLE" ]; then
  FILE_TARGET_USABLE=true
fi
VERDICT=$(_lib_resume_context_verdict "$SESSION_STATE" "$WORKTREE_ROOT" "$FILE_TARGET")
SCOPE_NAME="this worktree"
[ "$SESSION_STATE" = "linked" ] || SCOPE_NAME="this directory"
PROVENANCE="The command was computed from the resolved config dir and the working directory of this session"

if [ "$VERDICT" = "load" ] && [ "$SLASH_NAME_ACCEPTED" = true ]; then
  CMD="/resume-context $FILE_NAME"
  if [ "$SESSION_STATE" = "linked" ]; then
    LOAD_REASON="the file's resume command targets this worktree (\`$WORKTREE_ROOT\`)"
  else
    LOAD_REASON="the file's resume command names no directory, and this session is not in a linked worktree"
  fi
  PROVENANCE="The command names the written file by its file name, and the route compares the working directory of this session with the file's own resume command"
  # _lib_is_pending_continuity_file already fixed the suffix, so the directory alone picks the wording.
  case "$FILE_PATH" in
    "$CONFIG_DIR"/briefs/*)
      LEAD="Resume in a session in $SCOPE_NAME, because $LOAD_REASON. Start a new claude session there, or run /clear in this one once its work is done, then:"
      ROUTE="in a session in $SCOPE_NAME: the engineer starts a new claude session there, or runs /clear in this one once its work is done, then runs \`$CMD\`, because $LOAD_REASON."
      ;;
    *)
      LEAD="Resume in this session, because $LOAD_REASON. Run /clear, then:"
      ROUTE="in this session: the engineer runs /clear, then \`$CMD\`, because $LOAD_REASON."
      ;;
  esac
else
  CMD="$RESUME_COMMAND"
  if [ "$SESSION_STATE" = "unknown" ]; then
    REASON="/resume-context cannot confirm this file belongs to this worktree, because this session's worktree could not be determined."
  elif [ "$VERDICT" = "load" ]; then
    REASON="/resume-context cannot load this file by name, because the slash form accepts only a regular file directly in handoffs/ or briefs/ whose name does not start with a dash or a dot, and this hook does not announce it for a name starting with an @."
  else
    if [ "$FILE_TARGET_USABLE" = true ]; then
      CMD="resume-context --cwd $FILE_TARGET $FILE_PATH"
      if [ "$SESSION_STATE" = "linked" ]; then
        MISMATCH_REASON="its resume command targets \`$FILE_TARGET\`, not this worktree (\`$WORKTREE_ROOT\`)"
      else
        MISMATCH_REASON="its resume command targets \`$FILE_TARGET\`, and this session is not in a linked worktree"
      fi
      PROVENANCE="The command was computed from the resolved config dir, except that the --cwd directory comes from the written file's own resume command (it passed the path-character allowlist; this hook did not measure it)"
    else
      if [ "$SESSION_STATE" = "linked" ]; then
        MISMATCH_REASON="its resume command does not name this worktree (\`$WORKTREE_ROOT\`) as an absolute --cwd"
      else
        MISMATCH_REASON="its resume target could not be resolved to one directory, and this session is not in a linked worktree"
      fi
    fi
    REASON="/resume-context cannot confirm this file belongs to $SCOPE_NAME, because $MISMATCH_REASON."
  fi
  LEAD="$REASON Resume outside this session instead, in a new terminal on the machine that holds this file:"
  ROUTE="outside this session: the engineer runs \`$CMD\` in a new terminal on the machine that holds this file. $REASON"
fi
SYSTEM_MESSAGE="$LEAD"$'\n'"\`$CMD\`"

# shellcheck disable=SC2016 # single-quoted on purpose: $system_message and $route are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them.
_lib_jq -n --arg system_message "$SYSTEM_MESSAGE" --arg route "$ROUTE" --arg provenance "$PROVENANCE" \
  '{
    systemMessage: $system_message,
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: ("The resume route for the continuity file just written is " + $route + " " + $provenance + " -- use the command verbatim instead of reconstructing it. Relay this one route and its reason to the engineer with the command alone in its own fenced code block, name no other resume route, and run nothing yourself.")
    }
  }' \
  2>/dev/null || true

exit 0
