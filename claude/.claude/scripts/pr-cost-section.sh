#!/usr/bin/env bash
# Print the PR-description Cost section body for the current branch, gated
# by the <config-dir>/pr-cost-disclosure sentinel.
# Exit 0: sentinel enabled and HEAD resolves to a branch -- the complete cost
#         block (delimiters, heading, transcript-analysis.py cost --summary
#         report, reproducibility trailer) is printed to stdout for verbatim
#         embedding in a PR body.
# Exit 1: sentinel disabled, unreadable, or malformed -- no stdout.
# Exit 2: sentinel enabled but HEAD is detached -- no stdout.
# Exit 3: sentinel enabled and HEAD resolves to a branch, but the downstream
#         transcript-analysis.py cost call itself failed -- no stdout.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

# _config_value's exit code 2 (config dir unresolvable) is folded into this
# script's own exit 1 (sentinel disabled, unreadable, or malformed) by the
# `|| exit 1` below -- pr_cost_disclosure's own content-matches trim/fold
# lives once, in _config.sh.
mode=$(_config_value pr_cost_disclosure) || exit 1
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
# The trailer names the stow install path as a literal; "$0" would publish a
# worktree-local or account-scoped absolute path into a public PR body.
# The blank line before the trailer is load-bearing: GFM ends a table at an
# empty line, so an adjacent paragraph parses as one more table row.
# shellcheck disable=SC2016 # single-quoted so the trailer's backticks stay a literal markdown code span instead of undergoing command substitution.
printf '%s\n%s\n\n%s\n\n%s\n%s\n' \
  '<!-- pr-cost:start -->' \
  '## Cost (list-price estimate)' \
  "$cost_output" \
  'Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`' \
  '<!-- pr-cost:end -->'
