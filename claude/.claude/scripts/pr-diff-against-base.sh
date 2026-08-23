#!/usr/bin/env bash
# Prints the cumulative PR-vs-default-branch diff /ready-for-review's step 3
# reviews to stdout: git diff <merge-base of origin/<base-branch> and HEAD>...HEAD.
# Usage: pr-diff-against-base.sh
set -euo pipefail

if ! BASE_REF=$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null); then
  # Folds "no PR open yet" together with a transient gh failure (auth, network,
  # rate limit) -- both fall back to main. A failure that isn't actually "no PR
  # yet" is silent otherwise, so flag the fallback on stderr for visibility.
  BASE_REF=main
  printf 'pr-diff-against-base.sh: gh pr view failed; defaulting base to %s\n' "$BASE_REF" >&2
fi

if ! MERGE_BASE=$(git merge-base "origin/$BASE_REF" HEAD 2>/dev/null); then
  printf 'pr-diff-against-base.sh: could not resolve merge-base against origin/%s\n' "$BASE_REF" >&2
  exit 1
fi

git diff "$MERGE_BASE...HEAD"
