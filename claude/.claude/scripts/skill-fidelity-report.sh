#!/usr/bin/env bash
# Print skill-invocation and review-trace reports for the current branch,
# scoped to this repo. Used by /ready-for-review's fidelity-check step.
set -euo pipefail

BRANCH=$(git rev-parse --abbrev-ref HEAD)
SCRIPT_DIR=$(dirname "$0")

# Both calls must run regardless of whether the first one fails -- guarded by
# `if !`, which is exempt from set -e's abort-on-failure per the Bash manual.
# Either report failing exits 1, in addition to the stderr note below.
SKILL_INVOCATION_OK=1
REVIEW_TRACE_OK=1

echo "=== skill-invocation ==="
if ! "$SCRIPT_DIR/transcript-analysis.py" skill-invocation --branches "$BRANCH" --include-subagents; then
  SKILL_INVOCATION_OK=0
  echo "skill-fidelity-report.sh: skill-invocation report failed" >&2
fi

echo "=== review-trace ==="
if ! "$SCRIPT_DIR/transcript-analysis.py" review-trace --this-repo --branches "$BRANCH"; then
  REVIEW_TRACE_OK=0
  echo "skill-fidelity-report.sh: review-trace report failed" >&2
fi

if [[ "$SKILL_INVOCATION_OK" -eq 0 || "$REVIEW_TRACE_OK" -eq 0 ]]; then
  exit 1
fi

exit 0
