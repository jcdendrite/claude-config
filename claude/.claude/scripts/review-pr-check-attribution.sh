#!/usr/bin/env bash
# Mechanical attribution check over /review-pr's findings-body file, run
# before the deliver step posts it. The synthesize-and-record step's "start
# with **[Claude Code]**, end with the attribution trailer" instruction is
# prose the model composes the body under, not a tool-call argument, so no
# PreToolUse hook ever sees it -- this closes that gap with mechanical
# checks, not a model re-read. Structurally parallel to
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
only — no checkout, no checks run." Every mode also requires balanced
<details>/</details> tags outside single-backtick code spans (a span must sit
on one line). Exits 1 on any missing piece, naming which one and the file's
own path on stderr
(never the file's own content, which may be arbitrarily long PR-review
prose). Exits 2 on a usage error, an invalid mode, an unreadable file, or a
details check that did not complete.
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

# Unbalanced <details> tags swallow every later finding into a collapsed
# block on GitHub. Single-backtick code spans are skipped, so a finding that
# quotes the tag in single backticks does not trip the check.
# A tag inside a fenced code block, a double-backtick span, or a span wrapped
# across lines still counts, so an unbalanced quote there refuses the body.
# A fenced quote that exactly offsets a real unbalanced tag is an accepted miss.
# A tag split across lines never counts, since the scan matches one line at a time.
# So an unbalanced split tag is missed, and a balanced pair with one split tag is refused.
# A self-closing `<details/>` never counts, though an HTML5 parser opens a block for it.
# A line with thousands of `<details ` openers and no later `>` scans in
# quadratic time, so a caller's time cap can kill the check first, which
# fails closed.
# awk exits 3 for a closing tag with no opener and 4 for an opener never
# closed.
DETAILS_STATUS=0
awk '
  {
    remaining = tolower($0)
    gsub(/`[^`]*`/, "", remaining)
    while (match(remaining, /<\/?details([ \t][^>]*)?>/)) {
      tag = substr(remaining, RSTART, RLENGTH)
      remaining = substr(remaining, RSTART + RLENGTH)
      if (substr(tag, 2, 1) == "/") {
        if (depth == 0) { stray_closer = 1 } else { depth-- }
      } else {
        depth++
      }
    }
  }
  END { exit (stray_closer ? 3 : (depth != 0 ? 4 : 0)) }
' < "$FINDINGS_BODY_PATH" || DETAILS_STATUS=$?
case "$DETAILS_STATUS" in
  0) ;;
  3)
    echo "review-pr-check-attribution.sh: findings-body file $FINDINGS_BODY_PATH has a </details> with no opener -- the tags are out of balance. A tag quoted in a code fence or a double-backtick span counts too; quote it in single backticks on one line instead. Balance the tags, then re-run this check before posting." >&2
    exit 1
    ;;
  4)
    echo "review-pr-check-attribution.sh: findings-body file $FINDINGS_BODY_PATH has a <details> never closed -- an unclosed block collapses every later finding on GitHub. A tag quoted in a code fence or a double-backtick span counts too; quote it in single backticks on one line instead. Balance the tags, then re-run this check before posting." >&2
    exit 1
    ;;
  *)
    echo "review-pr-check-attribution.sh: details check of $FINDINGS_BODY_PATH did not complete (exit $DETAILS_STATUS) -- the tags could not be verified as balanced. Re-run this check before posting." >&2
    exit 2
    ;;
esac

if [[ "$MODE" == "diff-only" ]]; then
  DISCLOSURE_LINE='Reviewed from the PR diff only — no checkout, no checks run.'
  if ! grep -qFx -- "$DISCLOSURE_LINE" "$FINDINGS_BODY_PATH"; then
    echo "review-pr-check-attribution.sh: findings-body file $FINDINGS_BODY_PATH is missing the diff-only disclosure line '$DISCLOSURE_LINE' -- a reduced-coverage review must say so. Add it as its own line, then re-run this check before posting." >&2
    exit 1
  fi
fi

exit 0
