#!/bin/bash
# _direnv-lib.sh — shared direnv-export helper.
#
# Sourced by cleanup-merged-branches.sh and ci-watch.sh. Not executable on
# its own; source it, do not invoke it directly.
#
# Provides:
#   direnv_export_bash — print the export script direnv's shell hook would
#                         apply on `cd` into the current directory, or
#                         nothing if direnv isn't installed or the call
#                         fails. Each consumer decides what to do with that
#                         output (eval it in its own scope, eval it inside a
#                         subshell to contain the exports, etc.) — this
#                         function only resolves the payload.

direnv_export_bash() {
  command -v direnv >/dev/null 2>&1 || return 0
  local direnv_exports
  # </dev/null: an .envrc that reads stdin would otherwise hang, consuming
  # whatever TTY or pipe the caller's own stdin is attached to.
  # 2>/dev/null: direnv's own diagnostic noise (e.g. an un-`allow`ed .envrc
  # warning) must not be mistaken for the export payload.
  # direnv's stdout carries secret values verbatim — never printed here,
  # and callers must not log it either.
  direnv_exports=$(direnv export bash </dev/null 2>/dev/null) || return 0
  printf '%s' "$direnv_exports"
}
