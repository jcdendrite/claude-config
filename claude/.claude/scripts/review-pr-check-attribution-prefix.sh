#!/usr/bin/env bash
# Mechanical attribution-prefix check over /review-pr's findings-body file,
# run before Step 9 posts it. SKILL.md Step 7's "start the body with
# **[Claude Code]**" instruction is prose the model composes the body
# under, not a tool-call argument, so no PreToolUse hook ever sees it --
# this closes that gap with a literal string comparison, not a model re-read.
# Structurally parallel to review-pr-scan-findings-body.sh's own credential
# scan: same usage/argv shape, same exit-code contract.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-check-attribution-prefix.sh <findings-body-file>

Exits 0 if the file's first line starts with "**[Claude Code]**". Exits 1 if
it does not, naming the file's own path on stderr (never the mismatched
line's own text, which may be arbitrarily long PR-review prose). Exits 2 on
a usage error or an unreadable file.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

FINDINGS_BODY_PATH="$1"

if [[ ! -r "$FINDINGS_BODY_PATH" ]]; then
  echo "review-pr-check-attribution-prefix.sh: cannot read $FINDINGS_BODY_PATH." >&2
  exit 2
fi

FIRST_LINE=$(head -n 1 -- "$FINDINGS_BODY_PATH")
# Quoted literal, not bare -- unquoted, [Claude Code] is a glob character
# class, same as respond-pr-safe-patch.sh's own prefix check.
if [[ "$FIRST_LINE" != '**[Claude Code]**'* ]]; then
  echo "review-pr-check-attribution-prefix.sh: findings-body file $FINDINGS_BODY_PATH does not start with '**[Claude Code]**' -- every posted review must be attributed to disclose AI authorship to readers of a public PR. Add the prefix, then re-run this check before posting." >&2
  exit 1
fi

exit 0
