#!/usr/bin/env bash
# Backs the /resume-context skill: loads a pending /handoff or /brief
# continuity file into the current session, when the file matches the worktree
# named in its own text, instead of launching a new process the
# way resume-context.sh does.
#
# Usage:
#   resume-context-same-session.sh [<slug> | <file name> | <absolute path>]
#
# Output contract: every stdout line is `<KEY>: <value>`, and every printed path
# passes _lib_passes_path_char_allowlist. File content is never printed.
#   LOAD: <dest>                  the consumed file's temp path, to `Read` in full
#   SOURCE: <file>                the pending file that was consumed
#   AGE: <bucket>                 `<1d`, `1-7d` or `>7d`, of the pending file before the consume
#   SESSION_TARGET: <path|none|unknown>
#                                 this session's linked worktree root; `none`
#                                 for a main checkout or a directory outside any
#                                 repository; `unknown` when git could not say
#   FILE_TARGET: <path|none|unknown>
#                                 the --cwd of the file's own resume command; `none`
#                                 when the file holds no --cwd text; `unknown` when the
#                                 script could not resolve one directory from the
#                                 file, which never loads
#   SHELL_COMMAND: <command>      the shell `resume-context` command to run instead
#   CANDIDATE: <file name> | <age bucket> | target <path|none|unknown> | <shell command>
#   MORE: <n>                     pending files beyond the 5 listed candidates
#   SKIPPED_UNPRINTABLE: <n>      pending names dropped for failing the allowlist
#   STOP_REASON: <code>: <sentence>
# Exit 0 once the verdict is printed: LOAD, SHELL_COMMAND, CANDIDATE or STOP_REASON.
# A non-zero exit means the script stopped before finishing its verdict.
# The pending set, selection, parse cost, consume and residuals are in docs/scripts.md.
#
# Env overrides:
#   RESUME_CONTEXT_CONSUMER  command to run instead of the sibling resume-context.sh.
#                            Tests only, mirroring RESUME_CONTEXT_LAUNCHER there.
set -euo pipefail

SCRIPT_DIR=$(dirname "$0")
# shellcheck source=../hooks/_lib.sh
. "$SCRIPT_DIR/../hooks/_lib.sh"
# shellcheck source=_worktree-lib.sh
. "$SCRIPT_DIR/_worktree-lib.sh"

CONSUMER="${RESUME_CONTEXT_CONSUMER:-$SCRIPT_DIR/resume-context.sh}"
CANDIDATE_CAP=5
FRESH_DAYS_BOUND=7   # `find -mtime -7` is true for a file under 7 days old

PENDING=""           # newline-joined allowlisted paths
PENDING_COUNT=0
SKIPPED_UNPRINTABLE=0
SESSION_STATE="unknown"
SESSION_ROOT=""
TARGET_CACHE=""      # newline-joined "<path> <target>" rows, one per parsed file
FILE_TARGET_RESULT=""
TARGET_MATCH_LIST="" # newline-joined pending paths whose usable target equals the linked root
OTHER_LIST=""        # newline-joined pending paths that are not target matches

stop() {
  printf 'STOP_REASON: %s: %s\n' "$1" "$2"
  exit 0
}

find_hint() {
  printf '%s/scripts/find-consumed-continuity-file.sh %s' "$CONFIG_DIR" "$1"
}

# stop_nothing_pending: reports any dropped names, then stops because no listable pending file remains.
stop_nothing_pending() {
  if [ "$SKIPPED_UNPRINTABLE" -gt 0 ]; then
    printf 'SKIPPED_UNPRINTABLE: %s\n' "$SKIPPED_UNPRINTABLE"
    stop nothing-pending "no pending handoff or brief has a name inside the printable allowlist; a consumed one can be found with $(find_hint "<slug>")"
  fi
  stop nothing-pending "no pending handoff or brief exists; a consumed one can be found with $(find_hint "<slug>")"
}

# collect_pending: fills PENDING, PENDING_COUNT and SKIPPED_UNPRINTABLE.
collect_pending() {
  local path
  for path in "$CONFIG_DIR"/handoffs/*-handoff.md "$CONFIG_DIR"/briefs/*-task.md; do
    if [ ! -f "$path" ] || [ -L "$path" ]; then
      continue
    fi
    if _lib_passes_path_char_allowlist "$path"; then
      PENDING="$PENDING$path"$'\n'
      PENDING_COUNT=$((PENDING_COUNT + 1))
    else
      SKIPPED_UNPRINTABLE=$((SKIPPED_UNPRINTABLE + 1))
    fi
  done
}

# existing_files LIST: prints the newline-joined paths of LIST that are still regular files.
existing_files() {
  local path
  while IFS= read -r path; do
    if [ -n "$path" ] && [ -f "$path" ]; then
      printf '%s\n' "$path"
    fi
  done <<< "$1"
}

# sort_newest_first LIST: prints the newline-joined allowlisted paths, newest first.
# `ls` only orders.
# Its output is re-filtered to files that still exist.
# The unsorted LIST stands in when none survive.
# A path that vanishes mid-listing makes `ls` exit non-zero but still list the rest.
# A QUOTING_STYLE in the environment makes `ls` print quoted names that match no file.
sort_newest_first() {
  local list previous_ifs="$IFS" sorted
  list=$(existing_files "$1")
  [ -n "$list" ] || return 0
  set -f
  IFS=$'\n'
  # shellcheck disable=SC2086 # splitting on newlines is intended; globbing is off and allowlisted paths hold no glob characters.
  set -- $list
  IFS="$previous_ifs"
  set +f
  [ "$#" -gt 0 ] || return 0
  sorted=$(existing_files "$(ls -t -- "$@" 2>/dev/null)")
  if [ -n "$sorted" ]; then
    printf '%s\n' "$sorted"
  else
    existing_files "$list"
  fi
}

# age_bucket FILE: `<1d`, `1-7d` or `>7d`, from find's whole-day mtime test.
# Both tests are positive, so a failing `find` reads as the oldest bucket.
age_bucket() {
  if [ -n "$(find "$1" -maxdepth 0 -mtime -1 2>/dev/null)" ]; then
    printf '<1d'
  elif [ -n "$(find "$1" -maxdepth 0 -mtime -"$FRESH_DAYS_BOUND" 2>/dev/null)" ]; then
    printf '1-7d'
  else
    printf '>7d'
  fi
}

# is_fresh FILE: true when FILE is under FRESH_DAYS_BOUND days old. A failing `find` reads as not fresh.
is_fresh() {
  [ -n "$(find "$1" -maxdepth 0 -mtime -"$FRESH_DAYS_BOUND" 2>/dev/null)" ]
}

# canonical_or_raw PATH: the symlink-resolved path when it still passes the allowlist.
canonical_or_raw() {
  local canon
  canon=$(worktree_canon_path "$1")
  if _lib_passes_path_char_allowlist "$canon"; then
    printf '%s' "$canon"
  else
    printf '%s' "$1"
  fi
}

# resolve_session_state: sets SESSION_STATE and SESSION_ROOT from the Bash $PWD.
resolve_session_state() {
  local root canon
  SESSION_STATE="unknown"
  SESSION_ROOT=""
  if root=$(_lib_linked_worktree_root "$PWD"); then
    if [ -z "$root" ]; then
      SESSION_STATE="not-linked"
    else
      canon=$(worktree_canon_path "$root")
      if _lib_passes_path_char_allowlist "$canon"; then
        SESSION_STATE="linked"
        SESSION_ROOT="$canon"
      fi
    fi
  fi
}

print_session_target() {
  case "$SESSION_STATE" in
    linked) printf 'SESSION_TARGET: %s\n' "$SESSION_ROOT" ;;
    not-linked) printf 'SESSION_TARGET: none\n' ;;
    *) printf 'SESSION_TARGET: unknown\n' ;;
  esac
}

# parse_file_target FILE: parses FILE, sets FILE_TARGET_RESULT to its --cwd (canonicalized),
# to "" when it holds no --cwd text, or to $_LIB_RESUME_CWD_UNUSABLE, and records the row.
# It sets a global rather than printing, because a command substitution would lose the cache.
parse_file_target() {
  local file="$1" raw
  raw=$(_lib_continuity_file_resume_cwd "$file")
  case "$raw" in
    "" | "$_LIB_RESUME_CWD_UNUSABLE") FILE_TARGET_RESULT="$raw" ;;
    *) FILE_TARGET_RESULT=$(canonical_or_raw "$raw") ;;
  esac
  TARGET_CACHE="$TARGET_CACHE$file $FILE_TARGET_RESULT"$'\n'
}

# resolve_file_target FILE: parse_file_target, except that a file already parsed this run
# reuses its cached row.
resolve_file_target() {
  local file="$1" row
  while IFS= read -r row; do
    case "$row" in
      "$file "*)
        FILE_TARGET_RESULT="${row#"$file "}"
        return 0
        ;;
    esac
  done <<< "$TARGET_CACHE"
  parse_file_target "$file"
}

# has_usable_target TARGET: true when TARGET is a path rather than empty or unusable.
has_usable_target() {
  [ -n "$1" ] && [ "$1" != "$_LIB_RESUME_CWD_UNUSABLE" ]
}

shell_command() {
  if has_usable_target "$2"; then
    printf 'resume-context --cwd %s %s' "$2" "$1"
  else
    printf 'resume-context %s' "$1"
  fi
}

print_file_target() {
  if [ -n "$1" ]; then
    printf 'FILE_TARGET: %s\n' "$1"
  else
    printf 'FILE_TARGET: none\n'
  fi
}

# print_candidate FILE TARGET
print_candidate() {
  printf 'CANDIDATE: %s | %s | target %s | %s\n' \
    "${1##*/}" "$(age_bucket "$1")" "${2:-none}" "$(shell_command "$1" "$2")"
}

# is_owner_only_file PATH: true when the caller owns PATH and its mode is exactly 0600.
# `ls -l` is the portable mode source; a trailing ACL or SELinux marker is ignored.
is_owner_only_file() {
  [ -O "$1" ] || return 1
  case "$(ls -ld -- "$1")" in
    "-rw-------"*) return 0 ;;
    *) return 1 ;;
  esac
}

# is_usable_destination DEST TMPDIR_ROOT: true when DEST is what the consumer is
# specified to print -- a printable, owner-only regular file directly under TMPDIR_ROOT.
is_usable_destination() {
  local dest="$1" tmpdir_root="$2" dest_dir
  dest_dir="${dest%/*}"
  [ -n "$dest" ] \
    && _lib_passes_path_char_allowlist "$dest" \
    && [ "${dest_dir%/}" = "${tmpdir_root%/}" ] \
    && [ -f "$dest" ] && [ ! -L "$dest" ] \
    && is_owner_only_file "$dest"
}

# load_file FILE TARGET: consumes FILE and prints the LOAD verdict, or stops.
load_file() {
  local file="$1" target="$2" bucket tmpdir_root dest consume_status=0 shell_hint
  shell_hint="for ${file##*/}, run: $(shell_command "$file" "$target")"
  bucket=$(age_bucket "$file")
  tmpdir_root=$(_lib_resume_context_tmpdir_root)
  case "$tmpdir_root" in
    /*) ;;
    *) tmpdir_root="" ;;
  esac
  if ! _lib_passes_path_char_allowlist "$tmpdir_root"; then
    stop tmpdir-unprintable "the temp-dir root is not an absolute path of printable characters, so nothing was moved; $shell_hint"
  fi
  # A rewrite of the pending path between the parse and this move loads bytes the verdict never saw.
  # On a temp root (RESUME_CONTEXT_TMPDIR or TMPDIR) that another uid can write without the sticky bit, that uid can swap the destination before the model's Read.
  # Every resume-context.sh move has the same residual, and it is accepted.
  # The consume has no time cap, as in the shell resume-context, so a hung config-dir filesystem holds the call until the Bash tool's timeout and can leave the file pending or moved.
  dest=$(RESUME_CONTEXT_EXACT_SOURCE=1 "$CONSUMER" --consume-only "$file") || consume_status=$?
  if [ "$consume_status" -eq 0 ] && is_usable_destination "$dest" "$tmpdir_root"; then
    printf 'LOAD: %s\n' "$dest"
    printf 'SOURCE: %s\n' "$file"
    printf 'AGE: %s\n' "$bucket"
    print_session_target
    print_file_target "$target"
    # A failed stderr write must not turn a printed verdict into a non-zero exit.
    _lib_print_recovery_hint "$dest" || true
    exit 0
  fi
  if [ -e "$file" ]; then
    stop consume-failed "the consume did not complete and the file is still pending; $shell_hint"
  fi
  stop consumed-elsewhere-or-partial "the file is no longer pending but no usable copy was reported; look it up with $(find_hint "${file##*/}")"
}

# act_on_file FILE: applies the verdict to one chosen pending file.
# resolve_session_state must have run first.
act_on_file() {
  local file="$1" target verdict
  resolve_file_target "$file"
  target="$FILE_TARGET_RESULT"
  verdict=$(_lib_resume_context_verdict "$SESSION_STATE" "$SESSION_ROOT" "$target")
  if [ "$verdict" = "load" ]; then
    load_file "$file" "$target"
  fi
  print_session_target
  print_file_target "$target"
  printf 'SHELL_COMMAND: %s\n' "$(shell_command "$file" "$target")"
  exit 0
}

# list_candidates: prints up to CANDIDATE_CAP pending files, target matches first.
# TARGET_MATCH_LIST and OTHER_LIST must be set. Only the listed files are parsed beyond that.
list_candidates() {
  local ordered shown=0 unlisted=0 file_line
  ordered="$(sort_newest_first "$TARGET_MATCH_LIST")"$'\n'"$(sort_newest_first "$OTHER_LIST")"
  print_session_target
  while IFS= read -r file_line; do
    [ -n "$file_line" ] || continue
    if [ "$shown" -ge "$CANDIDATE_CAP" ]; then
      unlisted=$((unlisted + 1))
      continue
    fi
    resolve_file_target "$file_line"
    print_candidate "$file_line" "$FILE_TARGET_RESULT"
    shown=$((shown + 1))
  done <<< "$ordered"
  if [ "$shown" -eq 0 ]; then
    stop_nothing_pending
  fi
  if [ "$unlisted" -gt 0 ]; then
    printf 'MORE: %s\n' "$unlisted"
  fi
  if [ "$SKIPPED_UNPRINTABLE" -gt 0 ]; then
    printf 'SKIPPED_UNPRINTABLE: %s\n' "$SKIPPED_UNPRINTABLE"
  fi
  exit 0
}

# resume_without_argument: loads a unique fresh target match, else lists candidates.
# Only a linked session classifies every pending file; elsewhere nothing can match.
resume_without_argument() {
  local file fresh_matches="" fresh_count=0
  resolve_session_state
  TARGET_MATCH_LIST=""
  OTHER_LIST="$PENDING"
  if [ "$SESSION_STATE" = "linked" ]; then
    OTHER_LIST=""
    while IFS= read -r file; do
      [ -n "$file" ] || continue
      # Not resolve_file_target: its per-file cache scan would make this loop quadratic.
      parse_file_target "$file"
      if has_usable_target "$FILE_TARGET_RESULT" && [ "$FILE_TARGET_RESULT" = "$SESSION_ROOT" ]; then
        TARGET_MATCH_LIST="$TARGET_MATCH_LIST$file"$'\n'
        if is_fresh "$file"; then
          fresh_matches="$file"
          fresh_count=$((fresh_count + 1))
        fi
      else
        OTHER_LIST="$OTHER_LIST$file"$'\n'
      fi
    done <<< "$PENDING"
  fi
  if [ "$fresh_count" -eq 1 ]; then
    act_on_file "$fresh_matches"
  fi
  list_candidates
}

# resume_with_argument ARG: loads the one pending file ARG names exactly.
resume_with_argument() {
  local arg="$1" file name matches="" match_count=0
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    name="${file##*/}"
    if [ "$file" = "$arg" ] || [ "$name" = "$arg" ] \
      || [ "$name" = "$arg-handoff.md" ] || [ "$name" = "$arg-task.md" ]; then
      matches="$matches$file"$'\n'
      match_count=$((match_count + 1))
    fi
  done <<< "$PENDING"
  if [ "$match_count" -eq 0 ]; then
    stop not-pending "no pending handoff or brief matches that argument; accepted forms are a slug, a file name, or the full path of a pending file; if it was already consumed, look it up with $(find_hint "$arg")"
  fi
  if [ "$match_count" -gt 1 ]; then
    resolve_session_state
    print_session_target
    while IFS= read -r file; do
      [ -n "$file" ] || continue
      resolve_file_target "$file"
      print_candidate "$file" "$FILE_TARGET_RESULT"
    done <<< "$matches"
    exit 0
  fi
  resolve_session_state
  act_on_file "${matches%$'\n'}"
}

CONFIG_DIR=$(_lib_config_dir) || stop config-dir-unresolved "the config dir could not be resolved (CLAUDE_CONFIG_DIR must be absolute); run the shell resume-context command instead"
if ! _lib_passes_path_char_allowlist "$CONFIG_DIR"; then
  stop config-dir-unprintable "the config dir has characters outside the printable allowlist; run the shell resume-context command instead"
fi

if [ "$#" -gt 1 ]; then
  stop argument-not-allowed "accepted forms are one slug, one file name, or one absolute path; the slash form takes no --cwd and no second argument"
fi
if [ "$#" -eq 1 ]; then
  case "$1" in
    -*) stop argument-not-allowed "an argument cannot start with a dash; accepted forms are one slug, one file name, or one absolute path, and the slash form takes no --cwd" ;;
  esac
  if ! _lib_passes_path_char_allowlist "$1"; then
    stop argument-not-allowed "an argument must be non-empty and use only letters, digits and the characters . _ / @ + -; accepted forms are a slug, a file name, or an absolute path"
  fi
fi

collect_pending

if [ "$PENDING_COUNT" -eq 0 ]; then
  stop_nothing_pending
fi

if [ "$#" -eq 1 ]; then
  resume_with_argument "$1"
fi
resume_without_argument
