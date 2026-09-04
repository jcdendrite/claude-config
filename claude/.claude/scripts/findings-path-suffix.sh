#!/usr/bin/env bash
# Prepare this round's reviewer findings destination and print its <epoch>-<slug> suffix.
# Exit 0: suffix printed on stdout as the last line; earlier lines (e.g. a
#         stderr warning) are discardable.
# Exit 0 also covers a failed ignore-list update -- it warns on stderr but
#         does not change the exit code (best-effort, not the enforcement point).
# Exit 1: not a git repository, or HEAD does not resolve -- no stdout.
#
# The info/exclude append is duplicate-tolerant, not idempotent -- see docs/design-decisions.md §12.
set -euo pipefail

SCRIPT_NAME="findings-path-suffix.sh"
# Sibling branches sharing a prefix beyond this length can produce identical slugs --
# acceptable because the slug is a human-readable hint, not an identifier.
SLUG_MAX_LENGTH=20

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "$SCRIPT_NAME: not inside a git repository" >&2
  exit 1
fi

if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "$SCRIPT_NAME: HEAD does not resolve -- no commit yet" >&2
  exit 1
fi

EXCLUDE_FILE=$(git rev-parse --git-path info/exclude)
grep -qxF "agent-reviews/" "$EXCLUDE_FILE" 2>/dev/null || {
  # A pre-existing entry with no trailing newline would otherwise merge with the
  # appended line into one broken, unmatchable pattern.
  if [[ -s "$EXCLUDE_FILE" && -n "$(tail -c1 "$EXCLUDE_FILE" 2>/dev/null)" ]]; then
    printf '\n' >> "$EXCLUDE_FILE" 2>/dev/null
  fi
  echo "agent-reviews/" >> "$EXCLUDE_FILE" 2>/dev/null
} || echo "$SCRIPT_NAME: warning: could not update $EXCLUDE_FILE" >&2

EPOCH=$(date +%s)
# Detached HEAD collapses to the literal string "HEAD" and is deliberately not
# special-cased.
# Filtering to `[A-Za-z0-9-]` before `cut -c` truncation means no multibyte
# UTF-8 sequence is ever split.
SLUG=$(git rev-parse --abbrev-ref HEAD | tr '/' '-' | tr -cd 'A-Za-z0-9-' | cut -c1-"$SLUG_MAX_LENGTH")
echo "${EPOCH}-${SLUG}"
