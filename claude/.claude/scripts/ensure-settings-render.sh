#!/bin/bash
set -uo pipefail

# Repairs the settings.json of the profile this shell resolves
# ($CLAUDE_CONFIG_DIR, else $HOME/.claude) by running render-settings.sh on
# every new shell. render-settings.sh skips the write when the render changes
# nothing, so an up-to-date profile costs one render pass and no file rewrite.
# Deliberately no -e: this script's only contract is to repair-or-warn and
# exit 0, so a failing render is handled inline rather than aborting the
# invoking shell's startup.

render_script="$HOME/.claude/scripts/render-settings.sh"

if [[ ! -x "$render_script" ]]; then
  # Nothing to repair with -- most likely a mid-install or incomplete
  # checkout that install.sh itself will finish; stay silent rather than
  # warn about a state install.sh is already responsible for.
  exit 0
fi

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# -ef compares the directories themselves, so a trailing slash or a symlink
# that spells the default profile differently is still the default profile.
is_default_profile=false
if [[ "$config_dir" -ef "$HOME/.claude" ]]; then
  is_default_profile=true
fi

# A shell that inherited CLAUDE_CONFIG_DIR for a profile this repo never
# stowed into has nothing to render; stay silent for it.
if [[ "$is_default_profile" == "false" && ! -f "$config_dir/settings.base.json" ]]; then
  exit 0
fi

# Portable timeout wrapper, shared with install.sh's render-settings-invoke
# marker block -- see _capped-for-lib.sh for the probe order, -k escalation,
# and D-state/no-binary caveats. 5s cap matches _lib_capped_for's own default
# per-call cap.
. "$(dirname "${BASH_SOURCE[0]}")/_capped-for-lib.sh"

# render-settings.sh resolves the same profile from the inherited CLAUDE_CONFIG_DIR.
render_status=0
_capped_for 5 "$render_script" || render_status=$?
if [[ "$render_status" -eq 0 ]]; then
  exit 0
fi

# A cap-kill leaves no error output from the render, so the hint must not
# point at one.
if _lib_status_consistent_with_cap_kill "$render_status"; then
  failure_detail="most likely did not finish within its 5s cap (exit $render_status)"
else
  failure_detail="failed -- see the error above"
fi

if [[ "$is_default_profile" == "true" ]]; then
  repo_dir=""
  if [[ -r "$HOME/.claude-config-source" ]]; then
    repo_dir="$(cat -- "$HOME/.claude-config-source" 2>/dev/null)" || repo_dir=""
  fi
  if [[ -n "$repo_dir" ]]; then
    printf 'ensure-settings-render.sh: render of %s/settings.json %s; if settings.base.json or the stow links are missing, run: cd %q && ./install.sh\n' "$config_dir" "$failure_detail" "$repo_dir" >&2
  else
    printf 'ensure-settings-render.sh: render of %s/settings.json %s; if settings.base.json or the stow links are missing, re-run install.sh from your claude-config checkout.\n' "$config_dir" "$failure_detail" >&2
  fi
else
  printf 'ensure-settings-render.sh: render of %s/settings.json %s; to re-render this profile, run: CLAUDE_CONFIG_DIR=%q ~/.claude/scripts/render-settings.sh\n' "$config_dir" "$failure_detail" "$config_dir" >&2
fi

exit 0
