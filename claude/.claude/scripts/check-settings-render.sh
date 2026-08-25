#!/bin/bash
set -uo pipefail

# Warns when settings.json needs render-settings.sh to run before Claude
# Code can read it -- see README's "One-time migration for
# settings.base.json" note for the full failure mode this guards against.
# Runs independent of settings.json's own hook pipeline, since a hook
# registered inside a missing settings.json cannot itself warn about it.
# Deliberately no -e: this script's only contract is to warn and exit 0, so
# a failing command below is handled inline rather than aborting early.

# Same config-dir resolution convention as render-settings.sh.
config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
settings_file="$config_dir/settings.json"

# A present, non-symlink settings.json means render-settings.sh has already
# produced it -- nothing to warn about.
if [ -e "$settings_file" ] && [ ! -L "$settings_file" ]; then
  exit 0
fi

# A still-symlinked settings.json (dangling or resolving) means
# render-settings.sh has never run for this $HOME. A plain-missing,
# non-symlink settings.json (e.g. a fresh machine mid-install) reaches the
# same warning below.
repo_dir=""
if [ -r "$HOME/.claude-config-source" ]; then
  repo_dir="$(cat -- "$HOME/.claude-config-source" 2>/dev/null)" || repo_dir=""
fi

if [ -n "$repo_dir" ]; then
  printf 'check-settings-render.sh: %s needs a render -- cd %s && ./install.sh to fix.\n' "$settings_file" "$repo_dir" >&2
else
  printf 'check-settings-render.sh: %s needs a render -- re-run install.sh from your claude-config checkout to fix.\n' "$settings_file" >&2
fi

exit 0
