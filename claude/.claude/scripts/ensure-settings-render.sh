#!/bin/bash
set -uo pipefail

# Repairs $HOME/.claude/settings.json on every new shell by running
# render-settings.sh, rather than only warning that a render is needed --
# replaces the deleted check-settings-render.sh, which could only detect a
# dangling/missing settings.json and print a warning. render-settings.sh
# itself self-heals a dangling symlink or a plain-missing file via its own
# mktemp+mv sequence, so this wrapper's only job is to invoke it and, if
# that invocation fails, add the checkout-path hint the bare render error
# doesn't have.
# Runs independent of settings.json's own hook pipeline, since a hook
# registered inside a missing settings.json cannot itself repair it.
# Deliberately no -e: this script's only contract is to repair-or-warn and
# exit 0, so a failing render is handled inline rather than aborting the
# invoking shell's startup.

# Per-shell render cost: measured negligible, no staleness check added.
# Wall-clock varied 0.7-2.5s across repeated runs on a shared, heavily-loaded
# dev machine, but user+sys CPU time -- the actual cost this script adds to
# shell startup -- was consistently ~0.05-0.1s per invocation, dominated by
# render-settings.sh's ~28 jq subprocess spawns, so the wall-clock variance
# is scheduling contention rather than the script's own cost.

render_script="$HOME/.claude/scripts/render-settings.sh"

if [ ! -x "$render_script" ]; then
  # Nothing to repair with -- most likely a mid-install or incomplete
  # checkout that install.sh itself will finish; stay silent rather than
  # warn about a state install.sh is already responsible for.
  exit 0
fi

if "$render_script"; then
  exit 0
fi

repo_dir=""
if [ -r "$HOME/.claude-config-source" ]; then
  repo_dir="$(cat -- "$HOME/.claude-config-source" 2>/dev/null)" || repo_dir=""
fi

if [ -n "$repo_dir" ]; then
  printf 'ensure-settings-render.sh: settings.json render failed -- cd %s && ./install.sh to fix.\n' "$repo_dir" >&2
else
  printf 'ensure-settings-render.sh: settings.json render failed -- re-run install.sh from your claude-config checkout to fix.\n' >&2
fi

exit 0
