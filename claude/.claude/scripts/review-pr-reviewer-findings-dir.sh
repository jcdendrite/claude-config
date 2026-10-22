#!/usr/bin/env bash
# Creates this session's reviewer findings directory and prints its path, so
# /review-pr's session hands each spawned reviewer a findings_path under a
# location both write gates admit, instead of a relative agent-reviews/ path
# that the main-tree write gate denies. Zero arguments -- an exact-match
# settings.json entry can allowlist it. The path is derived by
# _lib_review_pr_reviewer_findings_dir, which review-pr-finish.sh also calls
# to remove the directory.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-reviewer-findings-dir.sh

Creates /tmp/review-pr-reviewer-findings-$SESSION_ID (mkdir -p) and prints
that path on stdout. A re-run prints the same path. The root is a literal
/tmp, never $TMPDIR. review-pr-finish.sh removes the directory. Takes no
argument; exits 2 with this text on any. Every other failure exits 1 with a
stderr message naming the failing step and nothing on stdout.
EOF
}

if [[ $# -ne 0 ]]; then
  usage
  exit 2
fi

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

SESSION_ID=$("$(dirname "$0")/marker.sh" resolve-session-id) || {
  echo "review-pr-reviewer-findings-dir.sh: could not resolve this session's id." >&2
  exit 1
}

# marker.sh resolve-session-id already validated the id, so this branch is
# defense in depth behind the identical session-id check there.
FINDINGS_DIR=$(_lib_review_pr_reviewer_findings_dir "$SESSION_ID") || {
  echo "review-pr-reviewer-findings-dir.sh: session id '$SESSION_ID' is not a valid path component." >&2
  exit 1
}

if ! mkdir_error=$(mkdir -p -- "$FINDINGS_DIR" 2>&1); then
  echo "review-pr-reviewer-findings-dir.sh: could not create $FINDINGS_DIR: $mkdir_error" >&2
  exit 1
fi

printf '%s\n' "$FINDINGS_DIR"
