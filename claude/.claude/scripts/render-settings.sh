#!/bin/bash
set -euo pipefail

# Renders $CLAUDE_CONFIG_DIR/settings.json (or $HOME/.claude/settings.json
# when unset) by merging settings.base.json with an optional overlay.
# Overlay path: settings.overlay.json in the same directory by default, or
# the path given as $1. No overlay present means settings.json is base only.
#
# Overlay top-level keys are restricted to {enabled, autoMode}; any other
# key is rejected outright, not merged -- see docs/auto-mode.md for the
# full contract.
#
# theme and tui survive from the prior settings.json even though neither
# is in base or overlay, since those two are written directly into the
# live file by Claude Code's /theme and /tui commands.
#
# Replaces settings.json via mktemp+mv (never cp/redirect) so a symlinked
# target is replaced, not written through.
#
# Usage: render-settings.sh [overlay-path]

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
base_file="$config_dir/settings.base.json"
overlay_file="${1:-$config_dir/settings.overlay.json}"
target="$config_dir/settings.json"

if [[ ! -f "$base_file" ]]; then
  echo "render-settings.sh: $base_file not found -- cannot render settings.json without it" >&2
  exit 1
fi

if ! jq -e 'type == "object"' -- "$base_file" >/dev/null 2>&1; then
  echo "render-settings.sh: $base_file is not valid JSON or not a JSON object -- refusing to render" >&2
  exit 1
fi

if [[ -e "$overlay_file" ]]; then
  if [[ ! -r "$overlay_file" ]]; then
    echo "render-settings.sh: $overlay_file exists but is not readable -- refusing to render" >&2
    exit 1
  fi

  # settings.overlay.json is the sanctioned home for per-account autoMode
  # trust declarations (docs/auto-mode.md); tighten its mode so other local
  # accounts can't read it even if directory-level hardening hasn't run yet.
  chmod 600 -- "$overlay_file" 2>/dev/null || echo "render-settings.sh: warning: could not chmod 600 $overlay_file" >&2

  if ! jq -e 'type == "object"' -- "$overlay_file" >/dev/null 2>&1; then
    echo "render-settings.sh: $overlay_file is not valid JSON or not a JSON object -- refusing to render" >&2
    exit 1
  fi

  # Overlay keys outside {enabled, autoMode} are rejected, not merged.
  if ! jq -e '(keys - ["enabled", "autoMode"]) == []' -- "$overlay_file" >/dev/null 2>&1; then
    bad_keys="$(jq -r '(keys - ["enabled", "autoMode"]) | join(", ")' -- "$overlay_file")"
    echo "render-settings.sh: $overlay_file has top-level keys outside {enabled, autoMode}: $bad_keys -- refusing to render" >&2
    exit 1
  fi

  # enabled must be a literal JSON boolean: jq's == is type-strict, so a
  # string or number there would silently fail to match the strip check below.
  if ! jq -e '(has("enabled") | not) or (.enabled | type == "boolean")' -- "$overlay_file" >/dev/null 2>&1; then
    bad_enabled="$(jq -c '.enabled' -- "$overlay_file")"
    echo "render-settings.sh: $overlay_file has enabled=$bad_enabled, which is not a JSON boolean -- refusing to render" >&2
    exit 1
  fi

  # Deep-merges overlay onto base (jq `*`); arrays replace wholesale, they
  # do not concatenate. If the overlay sets enabled:false, autoMode is
  # stripped from the merged result even when the overlay also defines its
  # own autoMode key.
  if ! merged_json="$(jq --slurpfile overlay "$overlay_file" '
    . as $base
    | ($base * $overlay[0]) as $merged
    | if ($overlay[0].enabled == false) then ($merged | del(.autoMode)) else $merged end
  ' -- "$base_file" 2>/dev/null)"; then
    echo "render-settings.sh: merge of $overlay_file onto $base_file failed" >&2
    exit 1
  fi
else
  merged_json="$(jq '.' -- "$base_file")"
fi

# Claude Code's /theme and /tui commands write straight into the live
# settings.json rather than into base or overlay, so a render must carry
# forward whatever the current target holds for those two keys or it
# destroys the user's most recent in-app choice. A missing or unparseable
# $target means nothing to preserve, not a render failure: $target here is
# this script's own prior output, not user-supplied input to validate.
if [[ -f "$target" ]]; then
  prev_theme_tui="$(jq -c '{theme, tui} | with_entries(select(.value != null))' -- "$target" 2>/dev/null || echo '{}')"
  merged_json="$(jq --argjson prev "$prev_theme_tui" '. * $prev' <<<"$merged_json")"
fi

# mktemp in $target's own directory so the final mv is a same-filesystem
# rename, and so mv-onto-target replaces a symlink instead of writing
# through it (see header comment).
tmp_target="$(mktemp "$target.XXXXXX")"
trap 'rm -f "$tmp_target"' EXIT
printf '%s\n' "$merged_json" > "$tmp_target"

# Re-parses the temp file's own written content, not the $merged_json shell
# variable, so a bad render never goes live. There is nothing to restore on
# failure since settings.json is fully regenerable.
if ! jq -e 'type == "object"' -- "$tmp_target" >/dev/null 2>&1; then
  echo "render-settings.sh: rendered output at $tmp_target failed re-validation -- refusing to replace $target" >&2
  exit 1
fi

mv -- "$tmp_target" "$target"
