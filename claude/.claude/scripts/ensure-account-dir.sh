#!/bin/bash
# Creates the active account's handoffs/ or briefs/ directory. Exactly one
# argument, "handoffs" or "briefs" -- anything else, including empty, exits
# non-zero without touching the filesystem.
# See claude-skills/skills/handoff/SKILL.md's and brief/SKILL.md's
# "write-target" fixture blocks for the callers this implements.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

# Under `set -e`, a failing nested command substitution doesn't abort the
# script. Bare "$(_lib_config_dir)/handoffs" would silently collapse to
# root-anchored "/handoffs" on a resolver failure; see _lib_config_dir's
# contract comment in _lib.sh.
config_dir=$(_lib_config_dir) || exit 1

if [[ $# -ne 1 ]]; then
  exit 1
fi

# Exact-match against a closed name set, each branch hardcoding its own
# literal mkdir target instead of interpolating "$1" -- no traversal-shaped
# or otherwise unexpected argument can reach mkdir.
case "$1" in
  handoffs)
    mkdir -p "$config_dir/handoffs"
    ;;
  briefs)
    mkdir -p "$config_dir/briefs"
    ;;
  *)
    exit 1
    ;;
esac
