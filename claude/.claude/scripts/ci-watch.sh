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
# Input (environment):
#   CI_CHECKS_GH_TOKEN  -- optional; used only for the two `gh pr checks`
#                           calls, since fine-grained PATs 403 on the Checks
#                           API. Unset leaves behavior unchanged. See
#                           docs/scripts.md for provisioning guidance.
#
# Usage: ci-watch.sh <pr-number>

set -euo pipefail

# fd 3 preserves the script's real stderr, independent of the 2>&1 /
# 2>"$STDERR_FILE" redirects applied around each `gh pr checks` call below —
# without it, gh_with_checks_token's escalation notice would be captured
# into WATCH_OUTPUT or STDERR_FILE instead of reaching the terminal.
exec 3>&2

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

# GH_TOKEN alone covers both github.com and *.ghe.com subdomains (gh help
# environment, gh v2.97.0).
gh_with_checks_token() {
  if [[ -n "${CI_CHECKS_GH_TOKEN:-}" ]]; then
    echo "ci-watch: using CI_CHECKS_GH_TOKEN override for Checks API" >&3
    GH_TOKEN="$CI_CHECKS_GH_TOKEN" gh "$@" 3>&-
  else
    gh "$@" 3>&-
  fi
}

STDERR_FILE=$(mktemp -t ci-watch-stderr.XXXXXX) || {  # GNU mktemp requires the XXXXXX suffix; a bare prefix is BSD-only.
  echo "CI_RESULT: error could not create temp file for stderr capture"
  exit 1
}
trap 'rm -f "$STDERR_FILE"' EXIT

# Recorded before the watch starts so a mid-watch push is detectable as
# "superseded," not silently diagnosed against.
if ! LAUNCH_SHA=$(gh pr view "$PR_NUMBER" --json headRefOid --jq .headRefOid 2>"$STDERR_FILE" 3>&-); then
  REASON=$(tr '\n' ' ' < "$STDERR_FILE")
  echo "CI_RESULT: error could not resolve PR #${PR_NUMBER}'s head SHA: ${REASON:-unknown gh failure}"
  exit 1
fi
echo "LAUNCH_SHA: ${LAUNCH_SHA}"

# Parses only the literal zero-checks text — --watch's exit code/output
# (2>&1, since gh writes it to stderr) can't otherwise distinguish a
# transient failure from a late-registering "pending" check.
WATCH_OUTPUT=$(gh_with_checks_token pr checks "$PR_NUMBER" --watch 2>&1) || true

if printf '%s' "$WATCH_OUTPUT" | grep -q 'no checks reported'; then
  echo "CI_RESULT: none"
  exit 0
fi

if ! SNAPSHOT_JSON=$(gh_with_checks_token pr checks "$PR_NUMBER" \
      --json name,bucket,description,link,workflow 2>"$STDERR_FILE"); then
  REASON=$(tr '\n' ' ' < "$STDERR_FILE")
  HINT=""
  if [[ -z "${CI_CHECKS_GH_TOKEN:-}" ]]; then
    HINT=" (if this is a 403: fine-grained PATs cannot reach the Checks API at all, and no CI_CHECKS_GH_TOKEN override was set for this call; see docs/scripts.md for how to provision one)"
  fi
  echo "CI_RESULT: error gh pr checks --json failed after --watch resolved: ${REASON:-unknown gh failure}${HINT}"
  exit 1
fi

echo "CI_RESULT: checks ${SNAPSHOT_JSON}"
exit 0
