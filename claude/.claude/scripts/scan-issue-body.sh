#!/usr/bin/env bash
# Pre-POST scan for identifying-shape content in a GitHub issue/comment body —
# run before `gh api -X POST ... -F body=@<file>` and gate the POST on this
# script's exit status; catches shapes `deny-private-project-refs.sh` doesn't
# (that hook only matches tracker-ID tokens and a blocklist that fails open
# when unconfigured).
#
# Detects six classes of content that can identify a specific machine,
# person, or private project even without naming it directly:
#   1. RFC1918/IPv4 literal
#   2. .ssh/ or id_<algorithm> SSH key path
#   3. /Users/<name>/ or /home/<name>/ home-rooted path
#   4. Long hex identifier (32+ contiguous hex chars, or a UUID)
#   5. Internal-only hostname (a label ending in a non-public-suffix TLD)
#   6. Slack-channel shape (#[a-z0-9_-]+, excluding all-digit runs so a plain
#      GitHub issue reference like #421 doesn't false-positive; a markdown
#      anchor link like #skill-architecture-notes shares the same shape as a
#      real channel name and is deliberately still blocked)
#
# Usage: scan-issue-body.sh <file>
#
# Exit codes: 0 = clean. 1 = a detector matched, OR the file could not be
# read, OR a usage error — every failure path exits non-zero explicitly
# rather than passing a sub-command's exit status through, since a
# match-only code (e.g. bare grep, which returns 1 on *no* match) has the
# wrong polarity for this contract.

set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: scan-issue-body.sh <file>" >&2
  exit 1
fi

BODY_FILE="$1"

if [[ ! -r "$BODY_FILE" ]]; then
  echo "scan-issue-body: cannot read '$BODY_FILE' — failing closed." >&2
  exit 1
fi

# label:pattern pairs, one per detection class (POSIX extended regex, grep -E).
# Checked independently so a match reports which class fired, rather than
# collapsing all six into one alternation.
DETECTORS=(
  "RFC1918/IPv4 literal:([0-9]{1,3}\.){3}[0-9]{1,3}"
  "SSH key path:(\.ssh/|id_(rsa|dsa|ecdsa|ed25519))"
  "home-rooted path:(/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+)"
  "long hex identifier:([0-9a-fA-F]{32,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
  "internal hostname:[A-Za-z0-9.-]+\.(internal|corp|local|lan|intranet|private)([^A-Za-z0-9_-]|$)"
  "Slack-channel shape:#[a-z0-9_-]*[a-z_-][a-z0-9_-]*"
)

MATCHED=0
for entry in "${DETECTORS[@]}"; do
  label="${entry%%:*}"
  pattern="${entry#*:}"
  # Capture grep's exit code explicitly rather than branching on it directly
  # (grep 0=match, 1=no match, >=2=error) — a bare `if grep; then` conflates
  # 1 and >=2 into a single "not matched" branch, silently treating a grep
  # error (e.g. the file became unreadable between the check above and this
  # loop) as a clean scan instead of failing closed.
  rc=0
  grep -Eq -- "$pattern" "$BODY_FILE" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    echo "scan-issue-body: matched detector '$label' in '$BODY_FILE'." >&2
    MATCHED=1
  elif [[ "$rc" -ge 2 ]]; then
    echo "scan-issue-body: detector '$label' failed to scan '$BODY_FILE' (grep exit $rc) — failing closed." >&2
    exit 1
  fi
done

if [[ "$MATCHED" -eq 1 ]]; then
  exit 1
fi

exit 0
