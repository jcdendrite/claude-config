#!/bin/bash
# SessionStart bootstrap for the SKILL.md structural validator.
#
# Provisions a per-plugin Python venv with pyyaml at
# ${CLAUDE_PLUGIN_DATA}/venv, then reuses it across sessions. Re-runs only
# when ${CLAUDE_PLUGIN_ROOT}/requirements.txt differs from the copy cached
# alongside the venv.
#
# Failure modes are all non-fatal — the structural validator is best-effort,
# and require-skill-review.sh already falls back to system python3 when the
# venv is missing. The hook never blocks session start: it exits 0 on every
# path and writes a single graceful line to stderr when something prevents
# provisioning. The loud distro-specific banner that `python3 -m venv` emits
# on systems missing ensurepip (Debian/Ubuntu without `python3-venv`) is
# suppressed so it does not appear at the top of every new session.
#
# Exit codes: always 0. Diagnostics go to stderr.

set -u

# Cache hit: requirements.txt matches the cached copy — venv is already
# provisioned and current. Nothing to do.
if diff -q "${CLAUDE_PLUGIN_ROOT}/requirements.txt" "${CLAUDE_PLUGIN_DATA}/requirements.txt" >/dev/null 2>&1; then
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo 'skill-management: python3 not found; SKILL.md structural validator will be unavailable until python3 is installed' >&2
  exit 0
fi

mkdir -p "${CLAUDE_PLUGIN_DATA}"
cp "${CLAUDE_PLUGIN_ROOT}/requirements.txt" "${CLAUDE_PLUGIN_DATA}/requirements.txt"

# Provision venv + install pyyaml, then probe that yaml is importable. The
# import probe is required because `python3 -m venv` can succeed (creating
# venv/bin/python) while pip install fails (network down, yanked release).
# require-skill-review.sh elects venv/bin/python as VALIDATOR_PYTHON whenever
# the file is executable, so a venv that lacks pyyaml would silently break
# the structural validator at commit time.
#
# stderr is suppressed across the whole block so the distro-specific
# ensurepip banner does not surface at session start; we emit a single
# graceful line on failure instead. To debug, run
#   python3 -m venv "${CLAUDE_PLUGIN_DATA}/venv"
# manually to see the original error.
if (
  cd "${CLAUDE_PLUGIN_DATA}" \
    && python3 -m venv venv \
    && venv/bin/pip install -r requirements.txt \
    && venv/bin/python -c 'import yaml'
) >/dev/null 2>&1; then
  exit 0
fi

# Provisioning failed. Remove the cached requirements.txt so the next
# SessionStart retries (e.g. after the user installs python3-venv), and
# remove the partial venv so require-skill-review.sh does not elect a
# broken interpreter as VALIDATOR_PYTHON in the meantime.
rm -rf "${CLAUDE_PLUGIN_DATA}/venv"
rm -f "${CLAUDE_PLUGIN_DATA}/requirements.txt"
echo 'skill-management: failed to provision Python venv for the SKILL.md structural validator; install Python venv support for your distro (e.g. python3-venv on Debian/Ubuntu) to enable it' >&2
exit 0
