#!/bin/bash
set -e

echo "=== claude-config Setup ==="

missing=()
for cmd in stow git gh jq sha256sum claude; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing dependencies: ${missing[*]}"
  echo "Install them via your system package manager, then re-run."
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"
stow -v --adopt -t "$HOME" claude

SETTINGS_FILE="$HOME/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
  echo ""
  echo "=== Registering marketplaces from extraKnownMarketplaces ==="
  existing_marketplaces="$(claude plugin marketplace list --json 2>/dev/null | jq -r '.[].name')"
  while IFS=$'\t' read -r name repo; do
    if echo "$existing_marketplaces" | grep -qFx "$name"; then
      echo "  ✓ $name (already registered)"
    else
      echo "  → adding $name ($repo)"
      claude plugin marketplace add "$repo" --scope user
    fi
  done < <(jq -r '.extraKnownMarketplaces // {} | to_entries[] | "\(.key)\t\(.value.source.repo)"' "$SETTINGS_FILE")

  echo ""
  echo "=== Installing plugins from enabledPlugins ==="
  existing_plugins="$(claude plugin list --json 2>/dev/null | jq -r '.[] | select(.scope == "user") | .id')"
  while read -r plugin; do
    if echo "$existing_plugins" | grep -qFx "$plugin"; then
      echo "  ✓ $plugin (already installed)"
    else
      echo "  → installing $plugin"
      claude plugin install "$plugin" -s user
    fi
  done < <(jq -r '.enabledPlugins // {} | to_entries[] | select(.value == true) | .key' "$SETTINGS_FILE")
fi

check_private_projects_file() {
  local file="$HOME/.claude/private-projects.md"
  if [ ! -e "$file" ]; then
    echo ""
    echo "TIP: Create ~/.claude/private-projects.md and add \"@private-projects.md\""
    echo "     to ~/.claude/CLAUDE.md to enable redaction of project names you don't"
    echo "     want leaking in commits/PRs. See README section 'Private-project redaction'."
  elif [ -z "$(grep -Ev '^[[:space:]]*(#|$)' "$file" 2>/dev/null)" ]; then
    echo ""
    echo "WARNING: ~/.claude/private-projects.md exists but contains no usable entries"
    echo "         (only comments or blank lines). Either populate it or delete it —"
    echo "         an empty file is the confusing state."
  fi
}

check_private_projects_file

echo ""
echo "Done. Optional: run the hook test suite:"
echo "  pytest claude/.claude/hooks/tests/"
