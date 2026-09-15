#!/bin/bash
set -uo pipefail

# Repairs $HOME/.claude/settings.json on every new shell by running
# render-settings.sh.
# Deliberately no -e: this script's only contract is to repair-or-warn and
# exit 0, so a failing render is handled inline rather than aborting the
# invoking shell's startup.

# Per-shell render cost measured negligible on a representative dev machine.
# The staleness check below still skips the full render when neither
# settings.base.json nor settings.overlay.json changed since the last
# successful render.

render_script="$HOME/.claude/scripts/render-settings.sh"

if [ ! -x "$render_script" ]; then
  # Nothing to repair with -- most likely a mid-install or incomplete
  # checkout that install.sh itself will finish; stay silent rather than
  # warn about a state install.sh is already responsible for.
  exit 0
fi

# Portable timeout wrapper:
# - Probes timeout(1) first, then gtimeout(1) -- Homebrew coreutils' g-prefixed name.
# - Same probe-then-fallback shape as _lib_capped_for in claude/.claude/hooks/_lib.sh.
# - Reimplemented here rather than sourced, since this script is not a hook and does not source _lib.sh.
# - Falls back to running uncapped when neither binary is on PATH, matching this script's own warn-and-continue contract rather than hard-failing shell startup.
# - 5s cap matches _lib_capped_for's own default per-call cap.
# - On a machine with neither timeout nor gtimeout on PATH (stock macOS included), this cap is a no-op and render_script runs fully uncapped.
_capped_for() {
  local seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$seconds" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$seconds" "$@"
  else
    "$@"
  fi
}

# sha256sum, not mtime: a same-second edit would be invisible to
# second-granularity mtime comparison, silently keeping settings.json stale.
# Probes gsha256sum too, same probe-then-fallback shape as _capped_for's
# timeout/gtimeout probe above, since a default (non-`--with-default-names`)
# Homebrew coreutils install links only the g-prefixed name.
hash_cmd=""
if command -v sha256sum >/dev/null 2>&1; then
  hash_cmd="sha256sum"
elif command -v gsha256sum >/dev/null 2>&1; then
  hash_cmd="gsha256sum"
fi

# Empty $hash_cmd would otherwise expand to `"" -- "$1"`, attempting to
# execute the file path itself as a command. Guarded here too, not just at
# the two call sites below, so a future call site that omits its own
# [ -n "$hash_cmd" ] check can't reintroduce the bug.
_content_hash() {
  [ -n "$hash_cmd" ] || return 0
  _capped_for 5 "$hash_cmd" -- "$1" 2>/dev/null | awk '{print $1}'
}

_hash_or_absent() {
  if [ -f "$1" ]; then _content_hash "$1"; else printf 'absent'; fi
}

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
base_file="$config_dir/settings.base.json"
overlay_file="$config_dir/settings.overlay.json"
target="$config_dir/settings.json"
cache_file="$config_dir/.render-settings-last-inputs"

# Hashed once here, before the skip-decision check below, and reused for the
# post-render cache write -- a base_file/overlay_file edit landing in the gap
# between the render finishing and the cache write can't otherwise get
# recorded as though it were true when the render actually ran.
base_hash="absent"
overlay_hash="absent"
if [ -n "$hash_cmd" ]; then
  base_hash="$(_hash_or_absent "$base_file")"
  overlay_hash="$(_hash_or_absent "$overlay_file")"
fi

# A base/overlay edit landing between this capture and render_script's own
# read of those files is not sticky: the next invocation re-reads fresh and
# will mismatch, except when the file is edited then reverted to
# byte-identical content before that next invocation runs -- the same risk
# class as this file's own already-accepted non-atomic-cache-write tradeoff.

# The cache key includes $target's own hash (not just base/overlay) so a
# corrupted target forces a re-render instead of matching stale state.
# Corruption shapes this catches:
# - disk-full truncation
# - a stray manual edit
# - invalid JSON
# $target is re-hashed fresh on every call -- before a render (to decide
# whether to skip) and after one (to cache the content a successful render
# actually left behind) -- since render-settings.sh is what writes $target.
_current_inputs() {
  printf '%s %s %s' "$base_hash" "$overlay_hash" "$(_hash_or_absent "$target")"
}

# Skipped entirely when neither sha256sum nor gsha256sum is on PATH, which
# disables the staleness optimization below (every shell still renders)
# rather than risk a wrong cache match.
if [ -n "$hash_cmd" ] && [ ! -L "$target" ] \
   && [ -r "$cache_file" ] && [ "$(cat -- "$cache_file" 2>/dev/null)" = "$(_current_inputs)" ]; then
  # $target is not a symlink someone wrote through since the last render,
  # and its content (plus base/overlay) still matches the last successful
  # render -- skip. A symlink at $target always falls through to a render
  # regardless of hash match, since write-through must still be repaired.
  exit 0
fi

if _capped_for 5 "$render_script"; then
  [ -n "$hash_cmd" ] && printf '%s\n' "$(_current_inputs)" > "$cache_file" 2>/dev/null
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
