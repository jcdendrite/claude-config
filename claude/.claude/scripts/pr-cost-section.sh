#!/usr/bin/env bash
# Print the PR-description Cost section body for the current branch, gated
# by the <config-dir>/pr-cost-disclosure sentinel.
# Exit 0: sentinel enabled and HEAD resolves to a branch -- the cost report
#         (transcript-analysis.py cost --summary) is printed to stdout.
# Exit 1: sentinel disabled, unreadable, or malformed -- no stdout.
# Exit 2: sentinel enabled but HEAD is detached -- no stdout.
# Exit 3: sentinel enabled and HEAD resolves to a branch, but the downstream
#         transcript-analysis.py cost call itself failed -- no stdout.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

config_dir=$(_lib_config_dir) || exit 1

sentinel_path="$config_dir/pr-cost-disclosure"
mode=$(cat "$sentinel_path" 2>/dev/null) || mode=""
# Trims space/tab only, not newline, so a blank line anywhere in the
# sentinel is preserved rather than collapsed -- a two-line file can never
# equal "dollars" after this trim, matching the exact-one-line contract.
mode="${mode#"${mode%%[![:blank:]]*}"}"
mode="${mode%"${mode##*[![:blank:]]}"}"
mode=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
[[ "$mode" == "dollars" ]] || exit 1

branch=$(git rev-parse --abbrev-ref HEAD)
[[ "$branch" != "HEAD" ]] || exit 2

# The 2>/dev/null below discards child stderr (request-ID-bearing NOTICE
# lines, format-drift WARNINGs) so it never reaches a public PR body. The
# format-drift WARNINGs are also surfaced on stdout as a PRICING INTEGRITY
# banner; the NOTICE lines (non-contiguous-merge decisions) have no stdout
# counterpart and stay a fully-discarded accepted residual. Accepted
# residual: an agent invoking transcript-analysis.py directly, bypassing
# this wrapper, can still capture that stderr -- no hook enforces the
# wrapper, since the caller is the same already-trusted agent drafting the
# PR body.
if ! cost_output=$("$(dirname "$0")/transcript-analysis.py" cost --this-repo --branches "$branch" --summary 2>/dev/null); then
  echo "pr-cost-section.sh: transcript-analysis.py cost call failed -- re-run" \
    "transcript-analysis.py cost --this-repo --branches $branch --summary" \
    "directly to see the diagnostic this redirect discarded" >&2
  exit 3
fi
printf '%s\n' "$cost_output"
