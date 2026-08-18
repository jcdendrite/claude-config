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
#
# A relative CLAUDE_CONFIG_DIR resolves differently per invocation cwd — the
# same path-mismatch bug _lib_config_dir() (claude/.claude/hooks/_lib.sh)
# rejects — so it sets MIGRATION_TOKEN_DIR empty here rather than silently
# producing byte-identical-but-wrong strings across call sites; every caller
# already guards against empty (see each call site's own comment).
if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
  case "$CLAUDE_CONFIG_DIR" in
    /*) MIGRATION_TOKEN_DIR="${CLAUDE_CONFIG_DIR%/}/lovable-cloud/migration-tokens" ;;
    *) MIGRATION_TOKEN_DIR="" ;;
  esac
elif [ -n "${HOME:-}" ]; then
  MIGRATION_TOKEN_DIR="${HOME%/}/.claude/lovable-cloud/migration-tokens"
else
  MIGRATION_TOKEN_DIR=""
fi
