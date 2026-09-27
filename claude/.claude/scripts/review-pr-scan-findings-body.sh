#!/usr/bin/env bash
# Mechanical secret scan over /review-pr's findings-body file, run before
# the deliver step posts it. SKILL.md's synthesize-and-record step's own
# "scrub any secret value" instruction is prose the model composes the body
# under, not a tool-call argument, so deny-private-project-refs.sh (which
# fires on tool calls) never sees it -- this closes that gap with a grep,
# not a model re-read.
# Not a general-purpose scanner: reuses _LIB_CREDENTIAL_VALUE_REGEX
# (_lib.sh), the same credential-shape check deny-pii-in-commits.sh and
# redact-credential-values.sh already use, so a hit here is a shape those
# surfaces would also flag.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-scan-findings-body.sh <findings-body-file>

Exits 0 if the file contains no credential-shaped string. Exits 1 if it
does, naming the match's line number on stderr -- never the matched value.
Exits 2 on a usage error or an unreadable file.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

FINDINGS_BODY_PATH="$1"

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

if [[ ! -r "$FINDINGS_BODY_PATH" ]]; then
  echo "review-pr-scan-findings-body.sh: cannot read $FINDINGS_BODY_PATH." >&2
  exit 2
fi

# grep below follows a symlink at this path, not O_NOFOLLOW: marker.sh's
# `write review-pr` arm runs _lib_sha256_no_follow against this same path
# after this scan and refuses the whole write on a symlinked target, so
# that downstream hash check is what actually closes the symlink-follow gap.

# -n prints the line number, not the match itself -- the deny message below
# names where the hit is, so the finding can be located and scrubbed
# without the credential value ever reaching this script's own stdout/stderr.
if MATCH_LINE=$(grep -nE "$_LIB_CREDENTIAL_VALUE_REGEX" -- "$FINDINGS_BODY_PATH" | cut -d: -f1 | head -1) && [[ -n "$MATCH_LINE" ]]; then
  echo "review-pr-scan-findings-body.sh: line $MATCH_LINE of $FINDINGS_BODY_PATH matches a credential shape (GitHub token prefix, AWS access key ID, or PEM private-key header). Scrub it to location-and-type only, then re-run this scan before posting." >&2
  exit 1
fi

exit 0
