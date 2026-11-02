#!/usr/bin/env bash
# install-dev.sh — contributor dev-environment setup (not for end users)
# Creates .venv from the python<X.Y> that .github/workflows/tests.yml pins for CI, and installs requirements-dev.txt (pytest, pytest-xdist, ruff, pyyaml, shellcheck-py).
# Rebuilds a .venv on another Python minor, and fails if it must build one and python<X.Y> is not on PATH.
# Then offers to install the pyright-lsp plugin at local scope from an interactive terminal.
# Run from the main worktree root; the .venv lives there only, and every worktree shares it.
# Run one install-dev.sh at a time, because it removes and rebuilds the shared .venv.
# The pin is read from the tests.yml of the checkout it runs in.
set -euo pipefail

install_dev_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# Step 1 — Anchor CWD: requirements-dev.txt proves we are at the repo root
# and is required for pip install. Refuse on a symlinked .venv to avoid
# silently blowing away a worktree symlink.
if [ ! -f requirements-dev.txt ]; then
  echo "ERROR: requirements-dev.txt not found. Run install-dev.sh from the repo root." >&2
  exit 1
fi
if [ -L .venv ]; then
  echo "ERROR: .venv is a symlink — refusing to remove it. Check your worktree setup." >&2
  exit 1
fi

# Step 2 — Require the private-projects.md redaction opt-in before setup
# continues, so a contributor doesn't discover tier-2 of the redaction hook
# was silently disabled for them only after a leak.
# Resolution mirrors deny-private-project-refs.sh's own union exactly: the
# $HOME/.claude copy is the floor, overridden only when $CLAUDE_CONFIG_DIR
# is set, absolute, and its own copy exists — never CLAUDE_CONFIG_DIR alone.
_private_projects_file="$HOME/.claude/private-projects.md"
case "${CLAUDE_CONFIG_DIR:-}" in
  /*)
    _config_dir="${CLAUDE_CONFIG_DIR%/}"
    [ -f "$_config_dir/private-projects.md" ] && _private_projects_file="$_config_dir/private-projects.md"
    ;;
esac
# -f && -r (not bare -e): excludes a stray directory or dangling symlink at
# this path, and matches the hook's own [ -r ] bar so a permission-denied
# file can't pass this gate while the hook silently can't read it.
if [ ! -f "$_private_projects_file" ] || [ ! -r "$_private_projects_file" ]; then
  echo "ERROR: $_private_projects_file not found or not readable." >&2
  echo "  Contributor setup requires opting into the private-project redaction" >&2
  echo "  blocklist first (a comment-only file is enough if you have nothing to" >&2
  echo "  list yet) — see docs/private-project-redaction.md 'Opt-in: enable the" >&2
  echo "  blocklist'." >&2
  exit 1
fi

# Step 3 — Read the interpreter minor CI pins; the interpreter itself is resolved in Step 5, only when .venv must be built.
# shellcheck source=claude/.claude/scripts/_ci-python-pin-lib.sh
. "$install_dev_dir/claude/.claude/scripts/_ci-python-pin-lib.sh"
if ! ci_python_pin="$(read_ci_python_pin .github/workflows/tests.yml)"; then
  exit 1
fi
pinned_python="python${ci_python_pin}"

# Prints the .venv's Python X.Y, or nothing when it has no executable python.
venv_python_minor() {
  [ -x .venv/bin/python ] || return 0
  .venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true
}

# Step 4 — Health probe: canonical check used by both the detect step and
# the final verification. Both sites call this function — one definition.
# A .venv on a Python minor other than the CI pin is unhealthy.
# Running pip, a console script, makes a stale shebang from a moved checkout unhealthy too.
check_venv_healthy() {
  [ "$(venv_python_minor)" = "$ci_python_pin" ] \
    && .venv/bin/python -c "import yaml, pytest, xdist" 2>/dev/null \
    && .venv/bin/pip --version >/dev/null 2>&1 \
    && .venv/bin/ruff --version >/dev/null 2>&1 \
    && .venv/bin/shellcheck --version >/dev/null 2>&1
}

# Step 5 — Create .venv if absent, or remove and recreate it if the health
# probe fails (partial install, deps missing, or a Python minor other than the
# CI pin). A healthy .venv needs no pinned interpreter, so none is required.
if [ -d .venv ] && check_venv_healthy; then
  venv_needs_build=false
else
  venv_needs_build=true
fi

if [ "$venv_needs_build" = true ]; then
  existing_venv_minor="$(venv_python_minor)"

  # Refuse rather than fall back to another python3, because a venv on another minor is what this script prevents.
  if ! pinned_python_path="$(command -v "$pinned_python")"; then
    if [ "$existing_venv_minor" = "$ci_python_pin" ]; then
      echo "ERROR: .venv failed its health probe, and rebuilding it needs $pinned_python, which is not on PATH." >&2
      echo "  Put $pinned_python on PATH, then re-run ./install-dev.sh." >&2
    else
      echo "ERROR: $pinned_python not found on PATH." >&2
      echo "  .github/workflows/tests.yml pins Python $ci_python_pin for CI, and .venv must run the same minor." >&2
      echo "  Install Python $ci_python_pin yourself, then re-run ./install-dev.sh." >&2
    fi
    exit 1
  fi

  # A version-manager shim can be on PATH yet fail to start.
  # Report that apart from a missing ensurepip, and show the interpreter's own error.
  if ! pinned_python_startup_error="$("$pinned_python_path" -c "pass" 2>&1)"; then
    echo "ERROR: $pinned_python is on PATH but failed to start:" >&2
    printf '  %s\n' "$pinned_python_startup_error" >&2
    exit 1
  fi

  # Guard ensurepip BEFORE creating venv. On Debian/Ubuntu without
  # python3-venv, `python -m venv` exits 0 but produces no pip, so the pip
  # install step fails with a confusing error. Catch this up front with
  # actionable guidance. Use `if !` so set -e does not swallow the exit path.
  if ! "$pinned_python_path" -c "import ensurepip" 2>/dev/null; then
    # _INSTALL_DEV_IS_DEBIAN: test-only override for Debian detection. On a
    # real system, apt-get on PATH or /etc/debian_version determines the branch.
    # Tests set this to "true" or "false" to exercise both branches in isolation.
    _is_debian="${_INSTALL_DEV_IS_DEBIAN:-auto}"
    if [ "${_is_debian}" = "auto" ]; then
      if command -v apt-get >/dev/null 2>&1 || [ -f /etc/debian_version ]; then
        _is_debian="true"
      else
        _is_debian="false"
      fi
    fi
    echo "ERROR: $pinned_python lacks ensurepip — venv creation will produce no pip." >&2
    if [ "${_is_debian}" = "true" ]; then
      echo "  Fix: sudo apt install python${ci_python_pin}-venv" >&2
      echo "  Then re-run ./install-dev.sh" >&2
    else
      echo "  Fix: install venv support for $pinned_python, meaning a build that ships its ensurepip module" >&2
      echo "  (python.org and Homebrew builds do), then re-run ./install-dev.sh" >&2
    fi
    exit 1
  fi

  if [ -d .venv ]; then
    if [ -n "$existing_venv_minor" ] && [ "$existing_venv_minor" != "$ci_python_pin" ]; then
      echo "Existing .venv runs Python $existing_venv_minor but CI pins $ci_python_pin — recreating with $pinned_python..."
    else
      echo "Existing .venv is incomplete or unhealthy — recreating..."
    fi
    rm -rf .venv
  fi

  echo "Creating .venv with $pinned_python..."
  "$pinned_python_path" -m venv .venv
fi

# Sync pins on every run — pip is a no-op when packages are up to date, so
# this also handles the case where requirements-dev.txt has been updated since
# the venv was last created.
echo "Syncing requirements-dev.txt..."
.venv/bin/pip install --quiet -r requirements-dev.txt

# Step 6 — Final verify. Same health probe as the detect step — consistent
# success criterion. Print pinned versions so contributors can confirm pins.
if check_venv_healthy; then
  pytest_ver="$(.venv/bin/python -c "import pytest; print(pytest.__version__)")"
  ruff_ver="$(.venv/bin/ruff --version | awk '{print $2}')"
  yaml_ver="$(.venv/bin/python -c "import yaml; print(yaml.__version__)")"
  shellcheck_ver="$(.venv/bin/shellcheck --version | awk '/^version:/ {print $2}')"
  echo "Done. .venv is ready (pytest=${pytest_ver}, ruff=${ruff_ver}, pyyaml=${yaml_ver}, shellcheck=${shellcheck_ver})"
  echo "  Run tests:  scripts/venv-run.sh pytest claude/.claude/ claude-skills/ plugins/ evals/"
  echo "  Run lint:   scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/"
  echo "  Lint shell: scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck"
else
  echo "ERROR: .venv verification failed after install. Check output above." >&2
  exit 1
fi

# Step 7 — Offer the pyright-lsp plugin at local scope (docs/design-decisions/pyright-lsp-installed-per-contributor.md).
# Every plugin check below is non-fatal, because a skipped plugin step must not fail the venv setup.
# None of them touches check_venv_healthy.
pyright_lsp_plugin="pyright-lsp@claude-plugins-official"
pyright_lsp_marketplace="${pyright_lsp_plugin##*@}"

# shellcheck source=claude/.claude/scripts/_plugin-install-lib.sh
. "$install_dev_dir/claude/.claude/scripts/_plugin-install-lib.sh"

echo ""
echo "=== pyright-lsp plugin (local scope) ==="
if ! command -v claude >/dev/null 2>&1; then
  echo "  ! claude CLI not found — skipping $pyright_lsp_plugin. Install the claude CLI, then re-run ./install-dev.sh."
elif ! pyright_lsp_registered_marketplaces="$(claude plugin marketplace list --json 2>/dev/null | jq -r '.[].name')"; then
  echo "[install-dev] warning: could not read registered marketplaces via 'claude plugin marketplace list --json' — skipping $pyright_lsp_plugin" >&2
# Never adds the marketplace: registration is profile-wide (docs/design-decisions/pyright-lsp-installed-per-contributor.md).
elif ! grep -qFx -- "$pyright_lsp_marketplace" <<< "$pyright_lsp_registered_marketplaces"; then
  echo "  ! $pyright_lsp_marketplace is not registered for this profile — skipping $pyright_lsp_plugin."
  echo "    Register it with: claude plugin marketplace add anthropics/claude-plugins-official"
  echo "    That registers the marketplace for every project on this profile. Re-run ./install-dev.sh afterward."
else
  if ! pyright_lsp_installed_tsv="$(claude plugin list --json 2>/dev/null | jq -r '.[] | select(.scope == "local") | "\(.id)\t\(.projectPath)"')"; then
    echo "[install-dev] warning: could not read installed local-scope plugins via 'claude plugin list --json' — proceeding as if none are installed" >&2
    pyright_lsp_installed_tsv=""
  fi
  if _plugin_installed_at_path "$pyright_lsp_plugin" "$pyright_lsp_installed_tsv" "$(pwd -P)"; then
    echo "  ✓ $pyright_lsp_plugin (already installed)"
  # Only an interactive run may install the plugin, because it is not a declared dependency (docs/design-decisions/pyright-lsp-installed-per-contributor.md).
  elif [ ! -t 0 ]; then
    echo "  ! not an interactive terminal — skipping $pyright_lsp_plugin."
    printf '    Install it with: cd %q && claude plugin install %s --scope local\n' "$(pwd -P)" "$pyright_lsp_plugin"
    echo "    Or re-run ./install-dev.sh from a terminal to be prompted."
  elif _confirm_plugin_install "$pyright_lsp_plugin" local; then
    echo "  → installing $pyright_lsp_plugin (local scope)"
    # A plugin from a GitHub-sourced marketplace can hang this line on a
    # stalled network path, with no timeout guard.
    # Ctrl-C and re-run is safe.
    if ! claude plugin install "$pyright_lsp_plugin" --scope local; then
      echo "[install-dev] warning: failed to install $pyright_lsp_plugin at local scope" >&2
    fi
  else
    echo "  ✓ leaving $pyright_lsp_plugin uninstalled"
  fi
fi

# Checks for the binary but never installs it, because pipx is not a declared dependency.
# pipx also installs outside .venv.
if pyright_langserver_path="$(command -v pyright-langserver 2>/dev/null)"; then
  echo "  ✓ pyright-langserver ($pyright_langserver_path)"
else
  echo "  ! pyright-langserver not found on PATH — pyright-lsp needs it there to launch."
  echo "    Install with: pipx install pyright"
  echo "    See README.md 'Tests' for alternatives."
fi
