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

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"
mkdir -p "$HOME/.local/bin"
# Both target directories are created before stow runs so stow links their
# contents entry by entry. Without this, stow tree-folds a target that does
# not yet exist into a single symlink pointing back into this checkout — which
# would put every file Claude Code writes at runtime inside the git clone.
mkdir -p "$HOME/.claude"
stow -v --adopt -t "$HOME" claude

# Harden ~/.claude and ~/.claude.json against other local accounts. $HOME is
# commonly 755 (set once at account creation, not by umask), and under the
# widespread umask 0002 anything created beneath it lands group-writable —
# ~/.claude at 775. Claude Code narrows only a few paths of its own
# (.credentials.json, projects/, sessions/, ide/, daemon/), leaving
# file-history/, plans/, shell-snapshots/ and the rest at that default.
# chmod 700 on ~/.claude is a single choke point: clearing the search bit
# blocks path resolution into every subdirectory at once, so no per-directory
# recipe is needed and directories added by future releases are covered too.
#
# ~/.claude.json needs its own chmod because it sits at $HOME level, outside
# ~/.claude/ — it indexes every project directory ever opened. Current Claude
# Code releases create it 0600 and preserve its mode across their
# temp-file-plus-rename rewrites, so this is a one-time repair of files left
# at 664 by older releases, and the repair holds.
#
# chmod is skipped when ~/.claude is a symlink: chmod dereferences, so a
# tree-folded ~/.claude left by an earlier install would narrow this
# checkout's own directory rather than a private one. Failures only warn, so
# hardening never blocks the plugin registration below.
#
# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: continuity-hardening — start
if [ -L "$HOME/.claude" ]; then
  echo "[install] warning: ~/.claude is a symlink to $(readlink "$HOME/.claude") — skipping chmod 700 so the link target is not narrowed. Claude Code is storing its state inside that path; replace the symlink with a real directory to get owner-only permissions." >&2
elif [ -d "$HOME/.claude" ]; then
  chmod 700 "$HOME/.claude" || echo "[install] warning: could not chmod 700 ~/.claude" >&2
fi
if [ -f "$HOME/.claude.json" ]; then
  chmod 600 "$HOME/.claude.json" || echo "[install] warning: could not chmod 600 ~/.claude.json" >&2
fi
# INSTALL_TEST_FIXTURE: continuity-hardening — end

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
  elif ! grep -Evq '^[[:space:]]*(#|$)' "$file" 2>/dev/null; then
    echo ""
    echo "WARNING: ~/.claude/private-projects.md exists but contains no usable entries"
    echo "         (only comments or blank lines). Either populate it or delete it —"
    echo "         an empty file is the confusing state."
  fi
}

if ! command -v timeout >/dev/null 2>&1; then
  # shellcheck disable=SC2016 # single-quoted for literal display text — the
  # backtick-quoted tokens are markdown-style formatting, not command
  # substitution; there is no shell expansion intended in either message.
  printf '[install] warning: GNU coreutils `timeout` not in PATH; guard hooks will run jq without timeout protection.\n' >&2
  # shellcheck disable=SC2016 # single-quoted for literal display text — the
  # backtick-quoted tokens are markdown-style formatting, not command
  # substitution; there is no shell expansion intended in this message.
  printf '[install] hint: install via `brew install coreutils` (macOS) or `apt install coreutils` (debian). On macOS, coreutils installs `gtimeout` by default — either re-run with `--with-default-names` (older brew) or symlink `gtimeout` to `timeout` in PATH.\n' >&2
fi

check_private_projects_file

if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo ""
  echo "NOTE: this python3 lacks ensurepip, so 'python3 -m venv' produces a venv with no pip."
  echo "      On Debian/Ubuntu, install it first: sudo apt install python3.12-venv"
fi

echo ""
echo "Done. Optional (contributors): run the hook test suite:"
echo "  ./install-dev.sh   # creates .venv from requirements-dev.txt"
echo "  .venv/bin/pytest claude/.claude/"
