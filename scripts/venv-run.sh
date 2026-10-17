#!/usr/bin/env bash
# Run a tool from the main checkout's contributor .venv, from the main checkout or any linked worktree.
# Usage: scripts/venv-run.sh <tool> [args...]
# Exit 2: usage error. Exit 127: the main checkout, its .venv, or the tool was not found.
# On bash 5.x an exec-time failure (e.g. a stale console-script shebang) also exits 127 but is reported by bash itself, with its own `scripts/venv-run.sh: line N:` prefix instead of this script's `venv-run.sh:` prefix.
# Stale-venv recovery, in the main checkout: `rm -rf .venv`, then `./install-dev.sh`.
set -euo pipefail

fail() {
  local exit_code=$1
  shift
  echo "venv-run.sh: $*" >&2
  exit "$exit_code"
}

if [[ $# -eq 0 || -z "$1" ]]; then
  fail 2 "usage: scripts/venv-run.sh <tool> [args...]"
fi

# A bare name only: a '/' could reach an executable outside .venv/bin.
if [[ "$1" == */* ]]; then
  fail 2 "the tool must be a bare name, not a path"
fi

script_dir=$(dirname -- "${BASH_SOURCE[0]}")
if ! common_dir=$(git -C "$script_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
  printf -v rerun_command 'git -C %q rev-parse --git-common-dir' "$script_dir"
  fail 127 "git failed for the git common dir of $script_dir; rerun to see why: $rerun_command"
fi

main=$(dirname -- "$common_dir")
if [[ ! -d "$main/.venv" ]]; then
  fail 127 "no .venv in the main checkout; run ./install-dev.sh in $main"
fi

tool=$1
shift
if [[ ! -f "$main/.venv/bin/$tool" || ! -x "$main/.venv/bin/$tool" ]]; then
  fail 127 "$tool is not an executable file in $main/.venv/bin; if it is a contributor tool, run ./install-dev.sh in $main (it installs the requirements-dev.txt checked out in $main, which in a linked worktree is not this branch's); for a stale or broken .venv, see the recovery line in the header of scripts/venv-run.sh"
fi

# Exec the unresolved path: Python finds its venv from the path it was invoked by.
exec "$main/.venv/bin/$tool" "$@"
