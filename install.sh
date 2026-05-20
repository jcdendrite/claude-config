#!/bin/bash
set -e

echo "=== claude-config Setup ==="

missing=()
for cmd in stow git gh jq sha256sum claude python3; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing dependencies: ${missing[*]}"
  echo "Install them via your system package manager, then re-run."
  exit 1
fi

# pyyaml is imported by the skill-management plugin's structural validator,
# which the pre-commit hook invokes via python3 and which the test suite
# imports directly. Without it, `pytest claude/.claude/` fails to collect.
if ! python3 -c "import yaml" >/dev/null 2>&1; then
  echo ""
  echo "=== Installing pyyaml (needed by tests + skill-management plugin) ==="
  if ! python3 -m pip install --user pyyaml; then
    echo ""
    echo "pyyaml install failed. Some distros mark the system Python as"
    echo "externally-managed (PEP 668). Install pyyaml another way and re-run:"
    echo "  python3 -m pip install --user --break-system-packages pyyaml   # if you trust system pip"
    echo "  pipx install pyyaml                                            # isolated install"
    echo "  python3 -m venv .venv && .venv/bin/pip install pyyaml          # repo-local venv"
    exit 1
  fi
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"
mkdir -p "$HOME/.local/bin"
stow -v --adopt -t "$HOME" claude

SETTINGS_FILE="$HOME/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
  echo ""
  echo "=== Registering marketplaces ==="
  existing_marketplaces="$(claude plugin marketplace list --json 2>/dev/null | jq -r '.[].name')"

  # This repo is itself a marketplace. A directory source needs an absolute
  # path, which is machine-specific and cannot live in the stowed settings.json
  # — so register it here from this checkout's location.
  if echo "$existing_marketplaces" | grep -qFx "claude-config"; then
    echo "  ✓ claude-config (already registered)"
  else
    echo "  → adding claude-config ($REPO_DIR)"
    claude plugin marketplace add "$REPO_DIR" --scope user
  fi

  while IFS=$'\t' read -r name source_type repo; do
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
  done < <(jq -r '.extraKnownMarketplaces // {} | to_entries[] |
    "\(.key)\t\(.value.source.source)\t\(.value.source.repo // "")"
  ' "$SETTINGS_FILE")

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
echo "  pytest claude/.claude/"
