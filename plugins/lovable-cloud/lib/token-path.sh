#!/bin/bash
# Single source of the migration-token directory path.
# Source this file (do NOT invoke it) to set MIGRATION_TOKEN_DIR.
# Generator and hooks both source this so the path is defined in one place.
# No realpath, no symlink normalization — the CLAUDE_CONFIG_DIR/$HOME-based
# literal expression must be byte-identical between the generator (Bash tool
# call) and the hooks (harness-spawned subprocesses) to prevent a false-deny
# outage where a token is written to a path the hook cannot read.
# shellcheck disable=SC2034 # set for the generator (new-migration) and the
# hooks (validate-migration-filename.sh, consume-migration-token.sh) that
# source this file and reference $MIGRATION_TOKEN_DIR
MIGRATION_TOKEN_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/lovable-cloud/migration-tokens"
