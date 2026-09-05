#!/usr/bin/env bash
# Resolves a /handoff or /brief continuity file's post-consume /tmp
# destination, for a session other than the one that did the consuming.
# resume-context.sh's move (directly, or via
# consume-durable-continuity-file-on-read.sh's --consume-only delegation)
# reports the destination only into the consuming session's own transcript;
# this script instead reads the durable, best-effort index
# resume-context.sh appends to (_lib_resume_context_index_file), so a
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
# - stdout: zero or more <stamp>\t<dest>\t<src> rows, one per line, in the
#   index's own append order (oldest first, newest last) -- filtered to
#   destinations that still exist, are regular files, are not symlinks,
#   and are owned by $EUID. This filter is a truthfulness control: age-based
#   tmp cleanup can reap a destination while the index's own mtime keeps
#   refreshing. It is also an integrity control on output that may feed
#   straight into `claude --append-system-prompt-file`: an unowned or
#   symlinked destination is never printed.
# - stderr: the reload hint for the newest printed row on success. On
#   failure, one of three distinct diagnoses:
#     - no index exists yet
#     - the index exists but no row's source matched the given substring
#     - rows matched the substring but every one of their destinations has
#       already been cleaned up (unrecoverable)
# - Exit 0 iff at least one row was printed; 1 otherwise.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

SLUG="${1:-}"

NO_INDEX_MSG='find-consumed-continuity-file.sh: no index found (nothing has been consumed yet)'

INDEX=$(_lib_resume_context_index_file) || {
  printf '%s\n' "$NO_INDEX_MSG" >&2
  exit 1
}

if [ -L "$INDEX" ] || [ ! -f "$INDEX" ]; then
  printf '%s\n' "$NO_INDEX_MSG" >&2
  exit 1
fi

MATCHED=0
PRINTED=0
LAST_PRINTED_DEST=""
while IFS=$'\t' read -r stamp dest src; do
  [ -n "$stamp" ] || continue
  if [ -n "$SLUG" ]; then
    case "$src" in
      *"$SLUG"*) ;;
      *) continue ;;
    esac
  fi
  MATCHED=$((MATCHED + 1))
  if [ -f "$dest" ] && [ ! -L "$dest" ] && [ -O "$dest" ]; then
    printf '%s\t%s\t%s\n' "$stamp" "$dest" "$src"
    PRINTED=$((PRINTED + 1))
    LAST_PRINTED_DEST="$dest"
  fi
done < "$INDEX"

if [ "$PRINTED" -gt 0 ]; then
  _lib_print_recovery_hint "$LAST_PRINTED_DEST"
  exit 0
fi

if [ "$MATCHED" -eq 0 ]; then
  if [ -n "$SLUG" ]; then
    printf 'find-consumed-continuity-file.sh: no row matched %s\n' "$SLUG" >&2
  else
    printf 'find-consumed-continuity-file.sh: index has no rows\n' >&2
  fi
else
  printf 'find-consumed-continuity-file.sh: matched %d row(s), every destination has already been cleaned up — unrecoverable\n' "$MATCHED" >&2
fi
exit 1
