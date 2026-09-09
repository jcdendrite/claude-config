#!/usr/bin/env bash
set -uo pipefail

# Reports whether a config key is enabled for the current machine/account.
# Usage: config-get.sh <key>
#
# Exit 0 = enabled, exit 1 = disabled, exit 2 = unknown key (including a
# reserved/disallowed subcommand — see below), exit 3 = environment failure
# (config dir unresolvable, or config-keys.psv itself missing/unreadable —
# a partial stow-relink or interrupted `git pull`). Prints the effective
# value on stdout for a human — the exit code is the sole authority,
# matching the phrasing CLAUDE.md already uses for
# autonomous-shipping-active.sh; never trust stdout alone. The
# schema-readability check runs before unknown-key detection so a missing
# config-keys.psv is never misreported as a typo'd key name — every row
# lookup against an unreadable schema file would otherwise find no match
# and return the same "unknown key" signal a genuine typo produces. Both
# checks run before config-dir resolution: neither needs the config dir
# resolved at all, so there is no reason to resolve it first.
#
# This script's own exit code 2 (unknown key) is a DIFFERENT meaning from
# _config_enabled's exit code 2 (config dir unresolvable, which this script
# reports as its own exit code 3 instead) — see _config.sh's header for the
# matching note on that side.
#
# No `set` subcommand and no other writing verb — this is
# read-only, full stop, the same "no set subcommand" property the query
# CLI's own design requires: shipping a writer here would open the exact
# distribution vector `enforce-config-write-shape.sh` exists to close.
# Delegates to _config.sh's _config_enabled, which is the single bash
# definition of config-key resolution — this script adds no logic of its
# own beyond argument validation and the exit-code mapping above.
# shellcheck source=../hooks/_config.sh
. "$(dirname "$0")/../hooks/_config.sh"

if [ "$#" -ne 1 ]; then
  echo "config-get.sh: usage: config-get.sh <key> — exactly one argument (no writing verb; see docs/config-file.md)" >&2
  exit 2
fi

KEY="$1"

if [ "$KEY" = "set" ]; then
  echo "config-get.sh: unknown subcommand 'set' — this script has no set subcommand and no other writing verb. Use install.sh's interactive opt-in prompt or migrate-legacy-config.sh to change a key." >&2
  exit 2
fi

if [ ! -r "$_CONFIG_SCHEMA_FILE" ]; then
  echo "config-get.sh: schema file not found or unreadable: $_CONFIG_SCHEMA_FILE -- a partial stow-relink or interrupted git pull, not a typo'd key name" >&2
  exit 3
fi

if ! _config_schema_field "$KEY" type >/dev/null; then
  echo "config-get.sh: unknown key '$KEY'" >&2
  exit 2
fi

VALUE=$(_config_value "$KEY")
STATUS=$?
if [ "$STATUS" -eq 2 ]; then
  echo "config-get.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty)" >&2
  exit 3
fi

printf '%s\n' "$VALUE"
[ "$VALUE" != "false" ]
