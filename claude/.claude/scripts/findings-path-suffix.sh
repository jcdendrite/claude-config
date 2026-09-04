#!/usr/bin/env bash
# Prepare this round's reviewer findings destination and print its <epoch>-<slug> suffix.
# Exit 0: the suffix is printed on stdout as the last line of output -- any earlier line
#         (e.g. a stderr warning, when a caller merges stdout and stderr) is discardable.
#         A failed ignore-list update warns on stderr and still exits 0 -- the update is
#         best-effort, not the enforcement point.
# Exit 1: not a git repository, or HEAD does not resolve -- no stdout.
#
# The info/exclude append is duplicate-tolerant, not idempotent: two concurrent
# invocations (e.g. from two linked worktrees) can race between the grep check and the
# append, leaving a benign duplicate "agent-reviews/" line.
set -euo pipefail

SCRIPT_NAME="findings-path-suffix.sh"
# Sibling branches sharing a prefix beyond this length can produce identical slugs --
# acceptable because the slug is a human-readable hint, not an identifier
# (.claude/plans/findings-path-script.md:44).
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
# special-cased, matching the pre-existing recipe's behavior
# (.claude/plans/findings-path-script.md:44).
RAW_SLUG=$(git rev-parse --abbrev-ref HEAD | tr '/' '-' | cut -c1-"$SLUG_MAX_LENGTH")
# `cut -c` truncates by byte position in this environment's locale, which can split a
# multibyte UTF-8 sequence at the boundary. iconv's //IGNORE drops any resulting
# incomplete trailing bytes -- it also exits non-zero whenever it drops something, so
# the sanitized stdout, not the exit status, is what this relies on.
if command -v iconv >/dev/null 2>&1; then
  SLUG=$(printf '%s' "$RAW_SLUG" | iconv -f UTF-8 -t UTF-8//IGNORE 2>/dev/null || true)
  SLUG="${SLUG:-$RAW_SLUG}"
else
  SLUG="$RAW_SLUG"
fi
echo "${EPOCH}-${SLUG}"
