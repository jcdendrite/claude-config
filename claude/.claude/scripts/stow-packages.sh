#!/usr/bin/env bash
set -euo pipefail

# Prints this repo's stow packages, one tab-separated row per package:
# <repo-relative package directory> TAB <stow target, relative to $HOME>.
# install.sh's stow loop and any downstream consumer installing this repo's
# content into a non-$HOME config directory both read this list rather than
# hardcoding it — see docs/scripts.md's entry for the two consumers.

# pwd -P (not pwd) canonicalizes away any symlink, matching install.sh's own
# REPO_DIR resolution.
# readlink -f on $0 resolves through the stowed symlink first, since this
# script is normally invoked as ~/.claude/scripts/stow-packages.sh rather
# than a direct checkout path the way install.sh always is.
resolved="$(readlink -f -- "$0" 2>/dev/null || printf '%s' "$0")"
REPO_DIR="$(cd -- "$(dirname -- "$resolved")/../../.." && pwd -P)"

# package directory, stow target (relative to $HOME) — one row per package.
packages=(
  $'claude\t.'
)

for package in "${packages[@]}"; do
  package_dir="${package%%$'\t'*}"
  if [ ! -d "$REPO_DIR/$package_dir" ]; then
    echo "[stow-packages] error: resolved REPO_DIR ($REPO_DIR) has no $package_dir directory — self-location must have resolved incorrectly; refusing to list a package that doesn't exist." >&2
    exit 1
  fi
  printf '%s\n' "$package"
done
