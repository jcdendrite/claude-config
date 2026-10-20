#!/usr/bin/env bash
# Prints the fixed findings-body path for /review-pr's synthesize-and-record
# step, so SKILL.md's Write-tool call gets the path from a script rather
# than transcribing $CONFIG_DIR/$SESSION_ID by hand. Zero arguments -- an
# exact-match settings.json entry can allowlist it (see marker.sh's own
# write review-pr arm, which derives this identical path independently via
# _review_pr_findings_body_fixed_path).
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-findings-path.sh

Prints $CONFIG_DIR/.review-pr-active.d/$SESSION_ID.body on stdout and
returns 0. Exits 2, printing nothing, when this session has no provenance
file -- the acquire script (review-pr-acquire.sh) has not run, so there is nothing to
record findings against.
EOF
}

if [[ $# -ne 0 ]]; then
  usage
  exit 2
fi

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-findings-path.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty)." >&2
  exit 2
}

SESSION_ID=$("$(dirname "$0")/marker.sh" resolve-session-id) || {
  echo "review-pr-findings-path.sh: could not resolve this session's id." >&2
  exit 2
}

PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)
if [[ ! -f "$PROVENANCE" ]]; then
  echo "review-pr-findings-path.sh: no provenance file for this session at $PROVENANCE -- run review-pr-acquire.sh first." >&2
  exit 2
fi

printf '%s\n' "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" body)"
