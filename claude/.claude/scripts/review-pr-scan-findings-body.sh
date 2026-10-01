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
Exits 2 on a usage error, an unreadable file, or a scan that did not
complete (grep exited with a status other than 0 or 1).
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
# -a scans a body holding a NUL byte as text (grep otherwise reports only
# that a binary file matches, with no line number), and LC_ALL=C keeps
# invalid UTF-8 from aborting the scan. The verdict is grep's own exit status
# (0 hit, 1 clean, else the scan failed), captured with no pipe after grep so
# no other command's status can stand in for it. The `N:` prefix is split off
# in the shell for the same reason.
scan_status=0
GREP_HIT=$(LC_ALL=C grep -a -m 1 -nE "$_LIB_CREDENTIAL_VALUE_REGEX" -- "$FINDINGS_BODY_PATH") || scan_status=$?
MATCH_LINE=${GREP_HIT%%:*}

case "$scan_status" in
  0)
    echo "review-pr-scan-findings-body.sh: line $MATCH_LINE of $FINDINGS_BODY_PATH matches a credential shape (GitHub token prefix, AWS access key ID, or PEM private-key header). Scrub it to location-and-type only, then re-run this scan before posting." >&2
    exit 1
    ;;
  1)
    exit 0
    ;;
  *)
    echo "review-pr-scan-findings-body.sh: scan of $FINDINGS_BODY_PATH did not complete (exit $scan_status); refusing." >&2
    exit 2
    ;;
esac
