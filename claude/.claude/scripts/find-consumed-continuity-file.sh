#!/usr/bin/env bash
# Resolves a /handoff or /brief continuity file's post-consume /tmp
# destination, for a session other than the one that did the consuming.
# resume-context.sh's move (directly, or via
# consume-durable-continuity-file-on-read.sh's --consume-only delegation)
# reports the destination only into the consuming session's own transcript;
# this script instead reads the durable, best-effort index
# resume-context.sh appends to (_lib_resume_context_index_dir), so a
# different session can look the destination up instead of grepping every
# resume-context.* file in /tmp by content.
#
# Usage:
#   find-consumed-continuity-file.sh [slug-substring]
#
# With no argument, prints every still-live row. With a slug substring,
# matches it against the row's source-path field only -- never the
# timestamp or destination -- so a numeric slug can't accidentally match a
# timestamp.
#
# Contract:
# - The index directory holds one day-file per UTC day
#   (consumed.<UTC YYYY-MM-DD>.tsv). Day-files are read in glob order,
#   which is chronological since the names are fixed-width ASCII dates --
#   oldest file's rows first, across all day-files, and write order within
#   a single day-file.
# - stdout: zero or more <stamp>\t<dest>\t<src> rows, one per line, in that
#   order, filtered to destinations that:
#     - still exist
#     - are regular files
#     - are not symlinks
#     - are owned by $EUID
#   This filter is a truthfulness control: age-based tmp cleanup can reap a
#   destination independently of the index's own 30-day day-file sweep. It
#   is also an integrity control: an unowned or symlinked destination is
#   never printed to output that may feed straight into
#   `claude --append-system-prompt-file`. A row's $src field is sanitized
#   (control bytes stripped) before printing, since it is printed to a
#   different session's terminal below -- stripped, not rejected, so a
#   poisoned row still surfaces rather than vanishing from the output.
# - stderr: the reload hint for the newest printed row on success. On
#   failure, one of three distinct diagnoses:
#     - no index found (no day-files at all)
#     - a day-file exists but no row's source matched the given substring
#     - rows matched the substring but every one of their destinations has
#       already been cleaned up (unrecoverable)
# - Exit 0 iff at least one row was printed; 1 otherwise.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

SLUG="${1:-}"
SLUG_DISPLAY=$(_lib_sanitize_for_terminal "$SLUG")

NO_INDEX_MSG='find-consumed-continuity-file.sh: no index found (nothing has been consumed yet)'

DIR=$(_lib_resume_context_index_dir) || {
  printf '%s\n' "$NO_INDEX_MSG" >&2
  exit 1
}

FILES_FOUND=0
MATCHED=0
PRINTED=0
LAST_PRINTED_DEST=""
# nullglob: with no day-files (no index yet, or nothing survived
# retention), the glob below must drop out of the argument list rather
# than be iterated as a literal unexpanded pattern string. The `[ -f "$f" ]
# && [ ! -L "$f" ] || continue` guard on the next line already skips that
# literal-string case even without nullglob, so nullglob here is a second,
# redundant safeguard against it.
shopt -s nullglob
for f in "$DIR"/consumed.*.tsv; do
  [ -f "$f" ] && [ ! -L "$f" ] || continue
  FILES_FOUND=$((FILES_FOUND + 1))
  while IFS=$'\t' read -r stamp dest src; do
    [ -n "$stamp" ] || continue
    if [ -n "$SLUG" ]; then
      case "$src" in
        *"$SLUG"*) ;;
        *) continue ;;
      esac
    fi
    MATCHED=$((MATCHED + 1))
    # $src is printed to a different session's terminal below; sanitize it
    # here so a raw OSC/CSI escape from a crafted path never reaches
    # rendered output. Matching above intentionally used the raw $src, not
    # this sanitized copy, so stripping can't affect which rows match.
    # A poisoned slug is written raw into the index row: resume-context.sh's
    # not-found hint pre-fills the *stripped* slug, which won't necessarily
    # substring-match this row's raw src, so that handoff isn't recoverable
    # via the suggested lookup command.
    src=$(_lib_sanitize_for_terminal "$src")
    if [ -f "$dest" ] && [ ! -L "$dest" ] && [ -O "$dest" ]; then
      printf '%s\t%s\t%s\n' "$stamp" "$dest" "$src"
      PRINTED=$((PRINTED + 1))
      LAST_PRINTED_DEST="$dest"
    fi
  # A concurrent consume's retention sweep can unlink this day-file between
  # the glob above and this redirect opening it -- `|| continue` (not a
  # bare failure under set -e) so that race moves on to the next day-file
  # instead of aborting the whole script. Loop-body state (MATCHED,
  # PRINTED, LAST_PRINTED_DEST) survives across files because a redirect,
  # unlike a pipe, introduces no subshell.
  done < "$f" || continue
done

if [ "$PRINTED" -gt 0 ]; then
  _lib_print_recovery_hint "$LAST_PRINTED_DEST"
  exit 0
fi

if [ "$FILES_FOUND" -eq 0 ]; then
  printf '%s\n' "$NO_INDEX_MSG" >&2
  exit 1
fi

if [ "$MATCHED" -eq 0 ]; then
  if [ -n "$SLUG" ]; then
    printf 'find-consumed-continuity-file.sh: no row matched %s\n' "$SLUG_DISPLAY" >&2
  else
    printf 'find-consumed-continuity-file.sh: index has no rows\n' >&2
  fi
else
  printf 'find-consumed-continuity-file.sh: matched %d row(s), every destination has already been cleaned up — unrecoverable\n' "$MATCHED" >&2
fi
exit 1
