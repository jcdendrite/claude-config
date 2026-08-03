#!/usr/bin/env bash
set -euo pipefail

# Registers this repo's plugin marketplace and installs its enabledPlugins
# for one Claude Code config profile ($CLAUDE_CONFIG_DIR, or $HOME/.claude by
# default) — safe to invoke once per profile on a machine running several.
# CLAUDE_CONFIG_DIR replaces the whole ~/.claude directory, not just $HOME
# (https://code.claude.com/docs/en/claude-directory) — settings.json lives
# directly at its root, with no nested .claude/ segment underneath it.
#
# Threat model: the resolved settings.json is trusted without an ownership
# check — a session with enough local access to set CLAUDE_CONFIG_DIR and
# author that file could already run `claude plugin install` directly, so
# this script grants no new capability over what's already reachable.

# pwd -P (not pwd) canonicalizes away any symlink, matching install.sh's own
# REPO_DIR resolution — plus a readlink -f on $0 first, since this script is
# normally invoked through its stowed symlink at
# ~/.claude/scripts/register-marketplace.sh rather than a direct checkout
# path the way install.sh always is.
resolved="$(readlink -f -- "$0" 2>/dev/null || printf '%s' "$0")"
REPO_DIR="$(cd -- "$(dirname -- "$resolved")/../../.." && pwd -P)"

if [ ! -f "$REPO_DIR/.claude-plugin/marketplace.json" ]; then
  echo "[register-marketplace] error: resolved REPO_DIR ($REPO_DIR) has no .claude-plugin/marketplace.json — self-location must have resolved incorrectly; refusing to register a wrong directory as the marketplace source." >&2
  exit 1
fi

SETTINGS_FILE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
if [ ! -f "$SETTINGS_FILE" ]; then
  echo "[register-marketplace] $SETTINGS_FILE not found — nothing to register for this profile."
  exit 0
fi

echo ""
echo "=== Registering marketplaces ==="
# INSTALL_TEST_FIXTURE: repo-relocation-marketplace — start
marketplace_list_json="$(claude plugin marketplace list --json 2>/dev/null)"
existing_marketplaces="$(echo "$marketplace_list_json" | jq -r '.[].name')"

# This repo is itself a marketplace. A directory source needs an absolute
# path, which is machine-specific and cannot live in the stowed settings.json
# — so register it here from this checkout's location. Compares the
# recorded .path (not just the name — .repo is github-source-only and is
# what the extraKnownMarketplaces loop below already uses) in canonicalized
# form against REPO_DIR (itself already canonicalized via pwd -P above), so
# a stale post-move registration is re-added instead of silently reported
# as "already registered".
claude_config_recorded_path="$(echo "$marketplace_list_json" | jq -r '.[] | select(.name == "claude-config") | .path // empty')"
claude_config_recorded_real=""
if [ -n "$claude_config_recorded_path" ]; then
  # BSD readlink -f (macOS) can print a partial path to stdout AND exit
  # non-zero for a dangling target, unlike GNU readlink — check the exit
  # status of the assignment itself rather than trusting `cmd1 || cmd2`,
  # which can concatenate BSD's partial stdout with the fallback echo.
  if ! claude_config_recorded_real="$(readlink -f -- "$claude_config_recorded_path" 2>/dev/null)"; then
    claude_config_recorded_real="$claude_config_recorded_path"
  fi
fi
if [ -n "$claude_config_recorded_path" ] && [ "$claude_config_recorded_real" = "$REPO_DIR" ]; then
  echo "  ✓ claude-config (already registered)"
else
  if [ -n "$claude_config_recorded_path" ]; then
    echo "  → re-registering claude-config: $claude_config_recorded_path -> $REPO_DIR"
    claude plugin marketplace remove claude-config
  else
    echo "  → adding claude-config ($REPO_DIR)"
  fi
  claude plugin marketplace add "$REPO_DIR" --scope user
fi
# INSTALL_TEST_FIXTURE: repo-relocation-marketplace — end

# Tracks every marketplace this run leaves actually registered — seeded from
# what was already there plus claude-config (unconditionally registered
# above, or the script would already have exited via set -e on a failed
# add) — so the enabledPlugins loop below can tell a plugin's marketplace
# apart from one skipped just above as non-portable, instead of attempting
# an install doomed to fail.
registered_marketplaces="$existing_marketplaces
claude-config"

while IFS=$'\t' read -r name source_type repo; do
  # claude-config is registered separately above as a directory source; skip
  # any extraKnownMarketplaces entry so a stray leftover doesn't trip the
  # non-github warning below.
  if [ "$name" = "claude-config" ]; then
    continue
  fi
  if [ "$source_type" != "github" ]; then
    echo "  ! $name: non-github source '$source_type' — skipping (only github sources are portable)"
    continue
  fi
  if echo "$existing_marketplaces" | grep -qFx "$name"; then
    echo "  ✓ $name (already registered)"
  else
    echo "  → adding $name ($repo)"
    claude plugin marketplace add "$repo" --scope user
  fi
  registered_marketplaces="$registered_marketplaces
$name"
done < <(jq -r '.extraKnownMarketplaces // {} | to_entries[] |
  "\(.key)\t\(.value.source.source)\t\(.value.source.repo // "")"
' -- "$SETTINGS_FILE")

echo ""
echo "=== Installing plugins from enabledPlugins ==="
existing_plugins="$(claude plugin list --json 2>/dev/null | jq -r '.[] | select(.scope == "user") | .id')"
while read -r plugin; do
  marketplace_name="${plugin##*@}"
  if ! echo "$registered_marketplaces" | grep -qFx "$marketplace_name"; then
    echo "  ! $plugin: marketplace '$marketplace_name' is not registered for this profile — skipping (either non-portable and skipped above, or never declared in extraKnownMarketplaces)"
    continue
  fi
  if echo "$existing_plugins" | grep -qFx "$plugin"; then
    echo "  ✓ $plugin (already installed)"
  else
    echo "  → installing $plugin"
    claude plugin install "$plugin" -s user
  fi
done < <(jq -r '.enabledPlugins // {} | to_entries[] | select(.value == true) | .key' -- "$SETTINGS_FILE")
