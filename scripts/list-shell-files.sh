#!/usr/bin/env bash
# List every tracked shell script in the repo, NUL-separated.
#
# A `*.sh` glob is not sufficient: several tracked executables carry no
# extension and are identifiable only by their shebang. Missing one means it
# is silently never linted while CI stays green, so discovery is computed
# rather than hardcoded.
#
# The CI lint runs through claude/.claude/hooks/tests/test_shellcheck.py,
# which invokes this script, so the linted set is defined in exactly one
# place. That test also scans independently with a looser shebang pattern and
# asserts discovery returns everything that scan finds.
#
# Output is NUL-separated so paths containing whitespace survive; consume it
# with `xargs -0` or `read -r -d ''`.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# This loop body runs in a subshell under set -e. Every command in it must be
# exit-status-guarded (|| continue, or inside if/case) — an unguarded command
# that fails would abort the subshell mid-enumeration and silently truncate
# the file list, which is worse than the empty-list case: an empty list fails
# loudly (`xargs -0 shellcheck` exits non-zero on GNU xargs; `TestRepoIsClean` fails
# through `assert files`), but a truncated list just under-lints.
git ls-files -z | while IFS= read -r -d '' file; do
  case "$file" in
    *.sh)
      printf '%s\0' "$file"
      continue
      ;;
  esac

  # Extensionless candidates: read only the first line, and only classify
  # files that actually start with a shebang.
  [ -f "$file" ] || continue
  IFS= read -r first_line < "$file" || continue
  case "$first_line" in
    '#!'*) ;;
    *) continue ;;
  esac

  # Matches an interpreter path ending in sh/bash/dash/ksh, either directly
  # (`#!/bin/bash`) or as the argument to env (`#!/usr/bin/env bash`).
  # Anchoring on the trailing name rather than the full path keeps this
  # working regardless of where the interpreter lives.
  if printf '%s' "$first_line" |
    grep -qE '^#![[:space:]]*[^[:space:]]*/(env[[:space:]]+)?(ba|da|k)?sh([[:space:]]|$)'; then
    printf '%s\0' "$file"
  fi
done
