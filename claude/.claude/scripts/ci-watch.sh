#!/usr/bin/env bash
# ci-watch.sh — launch a background CI-status watch for one PR and report a
# single machine-parseable terminal result line.
#
# Invoked by ready-for-review/SKILL.md's "CI watch (out-of-band)" section via
# `Bash` `run_in_background`, once the PR number is known. Never run in the
# foreground for a real PR: `gh pr checks --watch` blocks until every check
# reaches a terminal state, which can take hours.
#
# Output contract (stdout):
#   LAUNCH_SHA: <oid>          -- printed once, at start, before the watch
#   CI_RESULT: none            -- zero checks were ever configured on this PR
#   CI_RESULT: error <reason>  -- couldn't determine CI status
#   CI_RESULT: checks <json>   -- structured snapshot; `bucket` per check is
#                                  the pass/fail source of truth
#
# Two gh pr checks calls are required: --watch and --json cannot combine, and
# --watch's exit code doesn't distinguish failure/zero-checks/transient-error
# (cli/cli's pkg/cmd/pr/checks/checks.go).
#
# Usage: ci-watch.sh <pr-number>

set -euo pipefail

if [[ "$#" -ne 1 ]] || [[ -z "$1" ]]; then
  echo "Usage: $(basename "$0") <pr-number>" >&2
  echo "CI_RESULT: error missing or invalid pr-number argument"
  exit 2
fi
PR_NUMBER="$1"
if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Usage: $(basename "$0") <pr-number>" >&2
  echo "CI_RESULT: error pr-number must be numeric, got: ${PR_NUMBER}"
  exit 2
fi

if ! command -v gh &>/dev/null; then
  echo "CI_RESULT: error gh not installed"
  exit 1
fi

STDERR_FILE=$(mktemp -t ci-watch-stderr.XXXXXX) || {  # GNU mktemp requires the XXXXXX suffix; a bare prefix is BSD-only.
  echo "CI_RESULT: error could not create temp file for stderr capture"
  exit 1
}
trap 'rm -f "$STDERR_FILE"' EXIT

# Recorded before the watch starts so a mid-watch push is detectable as
# "superseded," not silently diagnosed against.
if ! LAUNCH_SHA=$(gh pr view "$PR_NUMBER" --json headRefOid --jq .headRefOid 2>"$STDERR_FILE"); then
  REASON=$(tr '\n' ' ' < "$STDERR_FILE")
  echo "CI_RESULT: error could not resolve PR #${PR_NUMBER}'s head SHA: ${REASON:-unknown gh failure}"
  exit 1
fi
echo "LAUNCH_SHA: ${LAUNCH_SHA}"

# Parses only the literal zero-checks text — --watch's exit code/output
# (2>&1, since gh writes it to stderr) can't otherwise distinguish a
# transient failure from a late-registering "pending" check.
WATCH_OUTPUT=$(gh pr checks "$PR_NUMBER" --watch 2>&1) || true

if printf '%s' "$WATCH_OUTPUT" | grep -q 'no checks reported'; then
  echo "CI_RESULT: none"
  exit 0
fi

if ! SNAPSHOT_JSON=$(gh pr checks "$PR_NUMBER" \
      --json name,bucket,description,link,workflow 2>"$STDERR_FILE"); then
  REASON=$(tr '\n' ' ' < "$STDERR_FILE")
  echo "CI_RESULT: error gh pr checks --json failed after --watch resolved: ${REASON:-unknown gh failure}"
  exit 1
fi

echo "CI_RESULT: checks ${SNAPSHOT_JSON}"
exit 0
