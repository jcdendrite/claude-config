#!/usr/bin/env bash
# Mechanical attribution check over /review-pr's findings-body file, run
# before the deliver step posts it. The synthesize-and-record step's "start
# with **[Claude Code]**, end with the attribution trailer" instruction is
# prose the model composes the body under, not a tool-call argument, so no
# PreToolUse hook ever sees it -- this closes that gap with literal string
# comparisons, not a model re-read. Structurally parallel to
# review-pr-scan-findings-body.sh's own credential scan: same usage/argv
# shape, same exit-code contract. The mode argument means the diff-only
# disclosure line cannot be opted out of by a model that never saw the
# prose stating it.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-check-attribution.sh <findings-body-file> <checkout|diff-only>

Exits 0 if the file's first line starts with "**[Claude Code]**", its last
non-blank line is exactly the attribution trailer "🤖 Generated with
[Claude Code](https://claude.com/claude-code)", and -- in diff-only mode
only -- the file also contains the literal line "Reviewed from the PR diff
only — no checkout, no checks run." Exits 1 on any missing piece, naming
which one and the file's own path on stderr (never the file's own content,
which may be arbitrarily long PR-review prose). Exits 2 on a usage error,
an invalid mode, or an unreadable file.
EOF
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

FINDINGS_BODY_PATH="$1"
MODE="$2"

case "$MODE" in
  checkout | diff-only) ;;
  *)
    usage
    exit 2
    ;;
esac

if [[ ! -r "$FINDINGS_BODY_PATH" ]]; then
  echo "review-pr-check-attribution.sh: cannot read $FINDINGS_BODY_PATH." >&2
  exit 2
fi

# Read with ordinary redirection below, not O_NOFOLLOW: marker.sh's `write
# review-pr` arm runs _lib_sha256_no_follow against this same path after
# this check and refuses the whole write on a symlinked target, so that
# downstream hash check is what actually closes the symlink-follow gap.

FIRST_LINE=$(head -n 1 -- "$FINDINGS_BODY_PATH")
# Quoted literal, not bare -- unquoted, [Claude Code] is a glob character
# class, same as respond-pr-safe-patch.sh's own prefix check.
if [[ "$FIRST_LINE" != '**[Claude Code]**'* ]]; then
  echo "review-pr-check-attribution.sh: findings-body file $FINDINGS_BODY_PATH does not start with '**[Claude Code]**' -- every posted review must be attributed to disclose AI authorship to readers of a public PR. Add the prefix, then re-run this check before posting." >&2
  exit 1
fi

# The last NON-BLANK line, not simply the last line -- trailing blank lines
# after the trailer must still pass. `|| [[ -n "$line" ]]` (not a bare
# `while read`) so a file with no trailing newline after its own last line
# still processes that line -- a plain `while read` silently drops it.
LAST_NON_BLANK_LINE=""
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ -n "${line//[[:space:]]/}" ]]; then
    LAST_NON_BLANK_LINE="$line"
  fi
done < "$FINDINGS_BODY_PATH"
ATTRIBUTION_TRAILER='🤖 Generated with [Claude Code](https://claude.com/claude-code)'
if [[ "$LAST_NON_BLANK_LINE" != "$ATTRIBUTION_TRAILER" ]]; then
  echo "review-pr-check-attribution.sh: findings-body file $FINDINGS_BODY_PATH's last non-blank line is not the attribution trailer '$ATTRIBUTION_TRAILER' -- every posted review must disclose it was AI-generated. Add the trailer as the body's final line, then re-run this check before posting." >&2
  exit 1
fi

if [[ "$MODE" == "diff-only" ]]; then
  DISCLOSURE_LINE='Reviewed from the PR diff only — no checkout, no checks run.'
  if ! grep -qFx -- "$DISCLOSURE_LINE" "$FINDINGS_BODY_PATH"; then
    echo "review-pr-check-attribution.sh: findings-body file $FINDINGS_BODY_PATH is missing the diff-only disclosure line '$DISCLOSURE_LINE' -- a reduced-coverage review must say so. Add it as its own line, then re-run this check before posting." >&2
    exit 1
  fi
fi

exit 0
