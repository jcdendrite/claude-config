#!/usr/bin/env bash
# Prints the cumulative PR-vs-default-branch diff /ready-for-review's step 3
# reviews to stdout: git diff <merge-base of origin/<base-branch> and HEAD>...HEAD.
# Usage: pr-diff-against-base.sh
set -euo pipefail

# Resolves the repo's own default branch, which is not always main.
resolve_default_branch() {
  local origin_head candidate
  if origin_head=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null); then
    printf '%s\n' "${origin_head#*/}"
    return 0
  fi
  # Reached only when origin/HEAD is unset, so the name below is a guess, not a lookup.
  for candidate in main master develop; do
    if git rev-parse --verify "origin/$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! BASE_REF=$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null); then
  # gh exits nonzero for "no PR open yet" and for auth/network failure alike.
  if ! BASE_REF=$(resolve_default_branch); then
    printf 'pr-diff-against-base.sh: gh pr view failed and no default branch resolved from origin\n' >&2
    exit 1
  fi
  printf 'pr-diff-against-base.sh: gh pr view failed; defaulting base to %s\n' "$BASE_REF" >&2
fi

if ! MERGE_BASE=$(git merge-base "origin/$BASE_REF" HEAD 2>/dev/null); then
  printf 'pr-diff-against-base.sh: could not resolve merge-base against origin/%s\n' "$BASE_REF" >&2
  exit 1
fi

git diff "$MERGE_BASE...HEAD"
