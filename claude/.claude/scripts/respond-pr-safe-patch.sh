#!/usr/bin/env bash
# Fetch a PR review comment and PATCH its body only if it starts with the
# Claude Code marker prefix — the ownership check and the PATCH happen in
# one call so a caller can't skip the check.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/respond-pr-safe-patch.sh <owner/repo> <comment-id>

Reads the replacement comment body from stdin -- stdin must be non-empty.
Fetches the target comment's current body from
repos/<owner/repo>/pulls/comments/<comment-id>; PATCHes it with the stdin
body only if the current body starts with "**[Claude Code]**".
Exits 1 with no PATCH attempted if it does not, or if the fetch fails.
EOF
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

REPO="$1"
COMMENT_ID="$2"

# Reject a shape that could carry a `..` path segment into the gh api URL,
# redirecting the PATCH to a different repo or comment than the caller
# intended (gh's HTTP client normalizes `.`/`..` segments per RFC 3986).
if [[ ! "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  echo "respond-pr-safe-patch.sh: '$REPO' is not a valid owner/repo — no PATCH attempted." >&2
  exit 2
fi
if [[ ! "$COMMENT_ID" =~ ^[0-9]+$ ]]; then
  echo "respond-pr-safe-patch.sh: '$COMMENT_ID' is not a valid numeric comment id — no PATCH attempted." >&2
  exit 2
fi

COMMENT_PATH="repos/$REPO/pulls/comments/$COMMENT_ID"

# Read stdin fully before the GET, so a slow/interactive stdin can't leave
# the GET half-done.
BODY=$(cat)

if [[ -z "${BODY//[[:space:]]/}" ]]; then
  echo "respond-pr-safe-patch.sh: stdin was empty or whitespace-only — no PATCH attempted." >&2
  exit 2
fi

# No revision pinning between this GET and the PATCH below -- a concurrent
# edit to this same comment in that window is overwritten unconditionally.
if ! CURRENT_BODY=$(gh api "$COMMENT_PATH" --jq '.body'); then
  echo "respond-pr-safe-patch.sh: could not fetch comment $COMMENT_ID from $REPO — no PATCH attempted." >&2
  exit 1
fi

case "$CURRENT_BODY" in
  '**[Claude Code]**'*)
    # -f (raw string), not -F (typed): -F applies gh's own type coercion
    # (true/false/null/integer conversion, {owner}/{repo}/{branch}
    # placeholder substitution, and a leading @ read as a filename) --
    # unsafe for an arbitrary PR comment body.
    gh api "$COMMENT_PATH" -X PATCH -f body="$BODY"
    ;;
  *)
    echo "respond-pr-safe-patch.sh: comment $COMMENT_ID in $REPO does not start with '**[Claude Code]**' — not Claude-authored; reply via the /replies form instead. No PATCH attempted." >&2
    exit 1
    ;;
esac
