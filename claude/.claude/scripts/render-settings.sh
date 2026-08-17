#!/bin/bash
set -euo pipefail

# Renders $CLAUDE_CONFIG_DIR/settings.json (or $HOME/.claude/settings.json
# when CLAUDE_CONFIG_DIR is unset) by merging settings.base.json -- content
# claude-config owns and ships tracked -- with an optional per-profile
# overlay: settings.overlay.json in the same directory by default, or the
# path given as $1. No overlay present means settings.json is base only.
#
# Overlay keys may only add new top-level keys -- overriding a base-defined
# key silently succeeds (jq `*` lets overlay win) rather than being
# rejected; see docs/auto-mode.md for the full contract.
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

  # Deep-merges overlay onto base (jq `*`, arrays replace wholesale), then
  # strips autoMode when overlay sets enabled:false -- even if the overlay
  # defines its own autoMode key.
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

# mktemp in $target's own directory so the final mv is a same-filesystem
# rename, and so mv-onto-target replaces a symlink instead of writing
# through it (see header comment).
tmp_target="$(mktemp "$target.XXXXXX")"
trap 'rm -f "$tmp_target"' EXIT
printf '%s\n' "$merged_json" > "$tmp_target"

# Re-parse the temp file's own written content -- not the $merged_json
# shell variable -- before the final mv: a bad render must never go live,
# and there is nothing to restore since settings.json is fully regenerable.
if ! jq -e 'type == "object"' -- "$tmp_target" >/dev/null 2>&1; then
  echo "render-settings.sh: rendered output at $tmp_target failed re-validation -- refusing to replace $target" >&2
  exit 1
fi

mv -- "$tmp_target" "$target"
