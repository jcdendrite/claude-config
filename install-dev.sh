#!/usr/bin/env bash
# install-dev.sh — contributor dev-environment setup (not for end users)
# Creates .venv and installs requirements-dev.txt (pytest, ruff, pyyaml, shellcheck-py).
# Run from the main worktree root; the .venv lives there only.
set -euo pipefail

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

# Step 2 — Guard ensurepip BEFORE creating venv. On Debian/Ubuntu without
# python3-venv, `python3 -m venv` exits 0 but produces no pip, so the pip
# install step fails with a confusing error. Catch this up front with
# actionable guidance. Use `if !` so set -e does not swallow the exit path.
if ! python3 -c "import ensurepip" 2>/dev/null; then
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
  if [ "${_is_debian}" = "true" ]; then
    py_ver="$(python3 --version 2>&1 | grep -oE '3\.[0-9]+' || true)"
    if [ -z "${py_ver}" ]; then
      echo "ERROR: could not parse python3 version from 'python3 --version'" >&2
      exit 1
    fi
    echo "ERROR: python3 lacks ensurepip — venv creation will produce no pip." >&2
    echo "  Fix: sudo apt install python${py_ver}-venv" >&2
    echo "  Then delete .venv (if it exists) and re-run ./install-dev.sh" >&2
  else
    echo "ERROR: python3 lacks ensurepip — venv creation will produce no pip." >&2
    echo "  Fix: install your platform's Python venv support" >&2
    echo "  (e.g. python3-venv on Debian/Ubuntu, or use python.org / Homebrew on macOS)" >&2
  fi
  exit 1
fi

# Step 3 — Health probe: canonical check used by both the detect step and
# the final verification. Both sites call this function — one definition.
check_venv_healthy() {
  [ -x .venv/bin/python ] \
    && .venv/bin/python -c "import yaml, pytest" 2>/dev/null \
    && .venv/bin/ruff --version >/dev/null 2>&1 \
    && .venv/bin/shellcheck --version >/dev/null 2>&1
}

# Step 4 — Heal or create venv. If .venv exists but the health probe fails
# (partial install, deps missing, or interpreter mismatch), remove and
# recreate. If absent, create fresh.
if [ -d .venv ] && ! check_venv_healthy; then
  echo "Existing .venv is incomplete or unhealthy — recreating..."
  rm -rf .venv
fi

if [ ! -d .venv ]; then
  echo "Creating .venv..."
  python3 -m venv .venv
fi

# Sync pins on every run — pip is a no-op when packages are up to date, so
# this also handles the case where requirements-dev.txt has been updated since
# the venv was last created.
echo "Syncing requirements-dev.txt..."
.venv/bin/pip install --quiet -r requirements-dev.txt

# Step 5 — Final verify. Same health probe as the detect step — consistent
# success criterion. Print pinned versions so contributors can confirm pins.
if check_venv_healthy; then
  pytest_ver="$(.venv/bin/python -c "import pytest; print(pytest.__version__)")"
  ruff_ver="$(.venv/bin/ruff --version | awk '{print $2}')"
  yaml_ver="$(.venv/bin/python -c "import yaml; print(yaml.__version__)")"
  shellcheck_ver="$(.venv/bin/shellcheck --version | awk '/^version:/ {print $2}')"
  echo "Done. .venv is ready (pytest=${pytest_ver}, ruff=${ruff_ver}, pyyaml=${yaml_ver}, shellcheck=${shellcheck_ver})"
  echo "  Run tests:  .venv/bin/pytest claude/.claude/"
  echo "  Run lint:   .venv/bin/ruff check claude/.claude/"
  echo "  Lint shell: scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck"
else
  echo "ERROR: .venv verification failed after install. Check output above." >&2
  exit 1
fi
