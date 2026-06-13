#!/bin/bash
# Single source of the migration-token directory path.
# Source this file (do NOT invoke it) to set MIGRATION_TOKEN_DIR.
# Generator and hooks both source this so the path is defined in one place.
# No realpath, no symlink normalization — the $HOME-based literal expression
# must be byte-identical between the generator (Bash tool call) and the hooks
# (harness-spawned subprocesses) to prevent a false-deny outage where a token
# is written to a path the hook cannot read.
MIGRATION_TOKEN_DIR="${HOME}/.claude/lovable-cloud/migration-tokens"
