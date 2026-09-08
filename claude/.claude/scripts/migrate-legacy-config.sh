#!/usr/bin/env bash
# One-time (safe-to-rerun) recovery import of every legacy sentinel file
# into claude-config.toml, plus an interactive per-file offer to delete
# each one afterward. Executed by install.sh via subprocess -- never
# sourced (sourcing would turn on set -u/pipefail for the rest of
# install.sh, fatal on an empty array under macOS system bash 3.2, the
# same reason install.sh:99-101 gives for not sourcing
# relocate-claude-config.sh) -- and safe to run standalone between full
# install.sh runs to materialize claude-config.toml on its own, since
# claude/.claude/** goes live on `git pull` with no install.sh re-run.
#
# Two phases, always in this order:
#   1. Non-interactive import, then schema-default scaffold. Import is
#      fully non-interactive for nine of the fourteen keys; the five
#      enforcement-critical keys (worktree_required, autonomous_shipping,
#      round_consult_gate, commit_stall_block, authorization_boundary_restore)
#      require a `[ -t 0 ]`-gated `[y/N]` confirmation before import writes
#      anything -- security load-bearing, not hang-prevention, since a
#      state-file row for one of these five is then protected from later
#      Claude-Code-mediated reversal by enforce-config-write-shape.sh. A
#      key's legacy-file read failing, or its enforcement-critical import
#      being declined or skipped (non-TTY), excludes it from the scaffold
#      pass that follows, so scaffold never backfills a schema default over
#      a value that either failed to read this run or was never confirmed.
#   2. Interactive per-file delete offer, `[ -t 0 ]`-gated once for the
#      whole phase -- hang-prevention only, not a security control, since
#      nothing here stops an agent from running `rm` on a legacy sentinel
#      directly. Never offered for a key that wasn't actually imported this
#      run (an existing state-file row already governs, or import was
#      deferred), and never offered at all for $HOME/.claude/worktree-required
#      specifically, since that file stays load-bearing as
#      worktree_required's resolution-failure fallback after migration.
set -euo pipefail

# ${BASH_SOURCE[0]}, not $0: a test sources this file directly (per the
# BASH_SOURCE guard at the bottom) to call a helper function without
# running main, and $0 inside a sourced file reflects the invoking shell's
# own argv[0] rather than this file's path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../hooks/_config.sh
. "$SCRIPT_DIR/../hooks/_config.sh"

# The same five keys config-keys.psv's own header comment names as
# enforcement-critical -- a fixed identity list here, not a schema column,
# since each one requires a claude-hook-review pass on any future change to
# its resolution, legacy-probe-on-resolution-failure, or
# legacy-import-locations column.
_MIGRATE_ENFORCEMENT_CRITICAL_KEYS=" worktree_required autonomous_shipping round_consult_gate commit_stall_block authorization_boundary_restore "

# _migrate_probe_legacy_location TYPE LEGACY_POLARITY PATH
# Populates _MIGRATE_DERIVED_VALUE and returns 0 (PATH exists, value
# derived), 1 (PATH does not exist), or 2 (PATH exists but its value could
# not be determined). Content is read via `cat` even for a presence-only
# key, whose derived value never depends on that content, so an unreadable
# legacy file is treated the same ambiguous way regardless of polarity --
# don't guess "absent" for a file that genuinely exists but can't be read.
# For a content-matches key (pr_cost_disclosure), empty content is a
# legitimate "disabled" derived value (matching _config_location_value's own
# read-time handling), while non-empty content that doesn't match the
# expected literal is treated as a failure here -- a hand-typo'd
# pr-cost-disclosure legacy file should defer rather than permanently
# import "disabled" via scaffold, so a later fix to the typo still takes
# effect on a subsequent run.
_migrate_probe_legacy_location() {
  local type="$1" legacy_polarity="$2" path="$3"
  _MIGRATE_DERIVED_VALUE=""
  [ -f "$path" ] || return 1
  local content
  if ! content=$(cat -- "$path" 2>/dev/null); then
    return 2
  fi
  case "$legacy_polarity" in
    presence-enables) _MIGRATE_DERIVED_VALUE="true"; return 0 ;;
    presence-disables) _MIGRATE_DERIVED_VALUE="false"; return 0 ;;
    content-matches)
      local mode expected
      mode=$(_config_trim "$content")
      mode=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
      expected="${type#enum:}"
      if [ -z "$mode" ]; then
        _MIGRATE_DERIVED_VALUE="false"
        return 0
      fi
      if [ "$mode" = "$expected" ]; then
        _MIGRATE_DERIVED_VALUE="$expected"
        return 0
      fi
      return 2
      ;;
    *) return 2 ;;
  esac
}

# _migrate_add_record PATH KEY VALUE OUTCOME HOME_PATH
# Appends one |-delimited record (path, key, this location's own derived
# value, outcome, load-bearing flag) to _MIGRATE_RECORDS, driving the
# delete-confirmation phase below. load_bearing is true only for
# worktree_required's literal $HOME/.claude copy: its schema row's
# legacy-probe-on-resolution-failure column keeps that one file load-bearing
# as a fallback probed directly when config-dir resolution itself fails, so
# it is never offered for deletion regardless of outcome -- never the
# resolved-config-dir copy of the same key, and never any other key's
# legacy file.
_migrate_add_record() {
  local path="$1" key="$2" value="$3" outcome="$4" home_path="$5"
  local load_bearing="false"
  if [ "$key" = "worktree_required" ] && [ -n "$home_path" ] && [ "$path" = "$home_path" ]; then
    load_bearing="true"
  fi
  _MIGRATE_RECORDS+=("$path|$key|$value|$outcome|$load_bearing")
}

# Caller must check $_MIGRATE_TTY before invoking this -- hangs forever on
# `read` against a closed/non-interactive stdin otherwise, the same
# contract install.sh's own _prompt_sentinel_opt_in documents. Unlike the
# delete-confirmation prompt below, this one is security load-bearing:
# declining leaves the key resolving via its legacy fallback exactly as it
# did before this script ran, so nothing is granted by running this script
# rather than not running it, and everything is granted by a confirmed
# "y".
_migrate_prompt_import_enforcement_critical() {
  local human_name="$1" value="$2" answer
  printf '%s: a legacy file says this should be %s.\n' "$human_name" "$value"
  read -r -p "Import this into claude-config.toml now? [y/N] " answer || answer=""
  case "$answer" in
    [Yy]*) return 0 ;;
    *) return 1 ;;
  esac
}

# Caller must check `[ -t 0 ]` before invoking this -- same contract as
# install.sh's own _prompt_delete_stale_migration_copy, which this mirrors.
# Hang-prevention only, not a security control: nothing here stops an agent
# from running `rm` on a legacy sentinel directly.
_migrate_prompt_delete_legacy_file() {
  local path="$1" key="$2" value="$3" outcome="$4" answer
  printf '  %s -- %s = %s (%s)\n' "$path" "$key" "$value" "$outcome"
  read -r -p "  delete $path now that its value is durably imported? [y/N] " answer || answer=""
  case "$answer" in
    [Yy]*) rm -f -- "$path" && echo "  → deleted $path" ;;
    *) echo "  ✓ leaving $path in place" ;;
  esac
}

# _migrate_process_key KEY TYPE LEGACY_IMPORT LEGACY_FILENAME LEGACY_POLARITY HUMAN_NAME
# Appends zero or more records to _MIGRATE_RECORDS, writes KEY's value via
# _config_set when this run's import decision says to (only when KEY has no
# existing state-file row, so a hand-edit or an earlier import is never
# overwritten by a later run), and appends KEY to _MIGRATE_SCAFFOLD_EXCLUDE
# when its enforcement-critical import was deferred (a non-TTY invocation or
# a declined [y/N] confirmation) or its legacy-file read failed this run
# (any key) -- so the _config_scaffold call that runs once after every key
# has gone through this function never backfills a schema default over a
# value that either failed to read this run or was never confirmed. Relies
# on the caller having already set $_MIGRATE_CONFIG_DIR, $_MIGRATE_HOME_DIR,
# $_MIGRATE_STATE_FILE, and $_MIGRATE_TTY.
#
# At most two locations exist per key (the resolved config dir, and --
# only for a config-dir-and-home key -- $HOME/.claude), so they are handled
# as two explicit variables rather than a generic array: the config-dir
# copy is always primary (it wins on disagreement between the two legacy
# locations -- for a diverged user it's the one that actually governed
# pre-migration behavior, since the old `_report_account_sentinel` resolver
# read $CLAUDE_CONFIG_DIR when set and absolute, else $HOME/.claude, never
# both -- and it's also the only copy any config-dir-only key's regular
# reads ever consult), so a read failure THERE defers the whole key
# regardless of home's own status, while a read failure at home ALONE
# (config-dir found cleanly) still lets config-dir's value import -- home
# was never authoritative over config-dir to begin with, so its own
# unreadable copy is recorded but does not block the key.
_migrate_process_key() {
  local key="$1" type="$2" legacy_import="$3" legacy_filename="$4" legacy_polarity="$5" human_name="$6"

  # config-keys.psv's own schema-validation gap: neither _config_schema_field
  # nor schema() rejects an out-of-subset legacy-import-locations value --
  # rejected loudly here instead of silently defaulting to a location count
  # (config-dir alone, never checking $HOME) that could under-count a
  # diverged user's real legacy value.
  case "$legacy_import" in
    config-dir | config-dir-and-home) ;;
    *)
      echo "migrate-legacy-config.sh: warning: $key has an unrecognized legacy-import-locations value in config-keys.psv: '$legacy_import' -- treating as 'config-dir' (checking only the resolved config dir, never \$HOME/.claude)" >&2
      legacy_import="config-dir"
      ;;
  esac

  local config_dir_path="$_MIGRATE_CONFIG_DIR/$legacy_filename"
  # literal_home_path is the load-bearing check's own input (the
  # delete-confirmation phase below never offers $HOME/.claude/worktree-required
  # for deletion at all, regardless of outcome): computed whenever $HOME is
  # known, regardless of whether it's also walked as a distinct second
  # location below. worktree_required's raw $HOME/.claude copy stays
  # load-bearing as a fallback probed directly when config-dir resolution
  # itself fails, even on a machine where CLAUDE_CONFIG_DIR is unset today,
  # since a later CLAUDE_CONFIG_DIR misconfiguration would make
  # _config_value fall back to probing exactly that literal path regardless
  # of what governed it at import time.
  local literal_home_path=""
  [ -n "$_MIGRATE_HOME_DIR" ] && literal_home_path="$_MIGRATE_HOME_DIR/$legacy_filename"
  local home_path=""
  if [ "$legacy_import" = "config-dir-and-home" ] && [ -n "$literal_home_path" ] \
     && [ "$literal_home_path" != "$config_dir_path" ]; then
    home_path="$literal_home_path"
  fi

  # `cmd && status=0 || status=$?` (not `cmd; status=$?`), throughout: a
  # bare failing command not inside if/&&/||/! aborts the script
  # immediately under `set -e`, and a not-found location (return 1) is an
  # expected, not exceptional, result.
  local config_dir_status config_dir_value=""
  _migrate_probe_legacy_location "$type" "$legacy_polarity" "$config_dir_path" \
    && config_dir_status=0 || config_dir_status=$?
  [ "$config_dir_status" -eq 0 ] && config_dir_value="$_MIGRATE_DERIVED_VALUE"

  local home_status=1 home_value=""
  if [ -n "$home_path" ]; then
    _migrate_probe_legacy_location "$type" "$legacy_polarity" "$home_path" \
      && home_status=0 || home_status=$?
    [ "$home_status" -eq 0 ] && home_value="$_MIGRATE_DERIVED_VALUE"
  fi

  # Nothing present, nothing unreadable at either location: nothing to
  # record, nothing to defer -- _config_scaffold fills this key's default
  # normally.
  if [ "$config_dir_status" -eq 1 ] && [ "$home_status" -eq 1 ]; then
    return 0
  fi

  local existing_value="" has_existing_row=1
  if existing_value=$(_config_read_key_from_file "$key" "$_MIGRATE_STATE_FILE"); then
    has_existing_row=0
  fi

  if [ "$has_existing_row" -eq 0 ]; then
    # A key with an existing row is never touched via the legacy path
    # again, whether that row came from a hand-edit, a prior import, or
    # predates this script ever running at all.
    [ "$config_dir_status" -ne 1 ] && _migrate_add_record "$config_dir_path" "$key" "$config_dir_value" \
      "skipped-existing-row($existing_value)" "$literal_home_path"
    [ "$home_status" -ne 1 ] && _migrate_add_record "$home_path" "$key" "$home_value" \
      "skipped-existing-row($existing_value)" "$literal_home_path"
    return 0
  fi

  if [ "$config_dir_status" -eq 2 ]; then
    # The primary (resolved-config-dir) location itself is unreadable --
    # defer the whole key regardless of home's status: don't silently fall
    # back to a secondary location this key's own read-time resolver may
    # never even consult.
    _MIGRATE_SCAFFOLD_EXCLUDE="$_MIGRATE_SCAFFOLD_EXCLUDE $key"
    _migrate_add_record "$config_dir_path" "$key" "" "deferred-pending-confirmation" "$literal_home_path"
    [ "$home_status" -ne 1 ] && _migrate_add_record "$home_path" "$key" "$home_value" \
      "deferred-pending-confirmation" "$literal_home_path"
    return 0
  fi

  if [ "$config_dir_status" -eq 1 ] && [ "$home_status" -eq 2 ]; then
    # No config-dir copy at all, and home's own copy is unreadable --
    # nothing usable to import this run.
    _MIGRATE_SCAFFOLD_EXCLUDE="$_MIGRATE_SCAFFOLD_EXCLUDE $key"
    _migrate_add_record "$home_path" "$key" "" "deferred-pending-confirmation" "$literal_home_path"
    return 0
  fi

  # A usable value exists: config-dir's own copy when present, else home's
  # copy alone (config-dir absent, home found).
  local winner_value
  if [ "$config_dir_status" -eq 0 ]; then
    winner_value="$config_dir_value"
  else
    winner_value="$home_value"
  fi

  local should_import=0
  case "$_MIGRATE_ENFORCEMENT_CRITICAL_KEYS" in
    *" $key "*)
      if [ "$_MIGRATE_TTY" -eq 1 ] \
         && _migrate_prompt_import_enforcement_critical "$human_name" "$winner_value"; then
        should_import=1
      fi
      ;;
    *) should_import=1 ;;
  esac

  if [ "$should_import" -eq 1 ] && _config_set "$key" "$winner_value"; then
    [ "$config_dir_status" -eq 0 ] && _migrate_add_record "$config_dir_path" "$key" "$config_dir_value" \
      "imported" "$literal_home_path"
    if [ "$home_status" -eq 0 ]; then
      if [ "$home_value" = "$winner_value" ]; then
        _migrate_add_record "$home_path" "$key" "$home_value" "imported" "$literal_home_path"
      else
        _migrate_add_record "$home_path" "$key" "$home_value" "lost-precedence($winner_value)" "$literal_home_path"
      fi
    elif [ "$home_status" -eq 2 ]; then
      _migrate_add_record "$home_path" "$key" "" "deferred-pending-confirmation" "$literal_home_path"
    fi
    return 0
  fi

  if [ "$should_import" -eq 1 ]; then
    echo "migrate-legacy-config.sh: warning: could not write $key -- claude-config.toml may have malformed content; leaving it for a manual fix" >&2
  fi

  _MIGRATE_SCAFFOLD_EXCLUDE="$_MIGRATE_SCAFFOLD_EXCLUDE $key"
  [ "$config_dir_status" -eq 0 ] && _migrate_add_record "$config_dir_path" "$key" "$config_dir_value" \
    "deferred-pending-confirmation" "$literal_home_path"
  [ "$home_status" -eq 0 ] && _migrate_add_record "$home_path" "$key" "$home_value" \
    "deferred-pending-confirmation" "$literal_home_path"
  # Explicit, not left to the last statement's own truth value: a bash
  # function returns its last-executed command's exit status by default,
  # and the `[ ... ] && cmd` line directly above is 1 (not 0) whenever
  # home_status != 0 -- the common case for the seven config-dir-only
  # keys -- which would then abort main's own calling loop under `set -e`,
  # since that loop calls this function as a bare statement.
  return 0
}

# Phase 2: offer to delete each legacy file _migrate_process_key actually
# imported this run. Reads $_MIGRATE_TTY and $_MIGRATE_RECORDS from the
# caller's scope (main's own locals, visible here via bash's dynamic
# scoping) rather than taking them as parameters -- a separate function
# from main, matching _migrate_process_key's own extraction above, so a
# test can source this file, set both as plain variables, and call this
# function directly with piped stdin, bypassing the `[ -t 0 ]` gate without
# a pty.
_migrate_run_delete_confirmation_phase() {
  local _migrate_record _migrate_rec_path _migrate_rec_key _migrate_rec_value \
    _migrate_rec_outcome _migrate_rec_load_bearing

  if [ "${#_MIGRATE_RECORDS[@]}" -eq 0 ]; then
    return 0
  fi
  echo ""
  echo "=== Delete now-imported legacy config files? ==="
  if [ "$_MIGRATE_TTY" -ne 1 ]; then
    echo "  not an interactive terminal -- leaving every legacy file in place; re-run from a terminal to be offered deletion." >&2
    return 0
  fi
  for _migrate_record in "${_MIGRATE_RECORDS[@]}"; do
    IFS='|' read -r _migrate_rec_path _migrate_rec_key _migrate_rec_value \
      _migrate_rec_outcome _migrate_rec_load_bearing <<< "$_migrate_record"
    [ "$_migrate_rec_load_bearing" = "true" ] && continue
    case "$_migrate_rec_outcome" in
      imported | lost-precedence*) ;;
      *) continue ;;
    esac
    _migrate_prompt_delete_legacy_file "$_migrate_rec_path" "$_migrate_rec_key" \
      "$_migrate_rec_value" "$_migrate_rec_outcome"
  done
}

# main is only invoked when this file is executed, never when it is
# sourced (guard at the bottom of the file) -- lets a test source this file
# to call a helper function above directly (e.g. the interactive prompts,
# with piped stdin bypassing their own [ -t 0 ] gate), the same way
# relocate-claude-config.sh's own main/BASH_SOURCE guard already does.
main() {
  local _MIGRATE_TTY=0
  [ -t 0 ] && _MIGRATE_TTY=1

  local _MIGRATE_CONFIG_DIR
  if ! _MIGRATE_CONFIG_DIR=$(_lib_config_dir); then
    echo "migrate-legacy-config.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) -- nothing imported" >&2
    exit 1
  fi
  local _MIGRATE_STATE_FILE="$_MIGRATE_CONFIG_DIR/$_CONFIG_STATE_FILENAME"

  local _MIGRATE_HOME_DIR=""
  if [ -n "${HOME:-}" ]; then
    _MIGRATE_HOME_DIR="${HOME%/}/.claude"
  fi

  local _MIGRATE_SCAFFOLD_EXCLUDE=""
  local -a _MIGRATE_RECORDS=()

  # Read config-keys.psv into an array first, then iterate the array with
  # `for`/here-string parsing (never `while read ... < file`) -- the loop
  # body below calls `read -r -p` for the enforcement-critical import
  # prompt, and a `while read` loop redirected from a file would steal that
  # same fd 0 away from the terminal for the loop's whole duration.
  local -a _migrate_schema_lines=()
  local _migrate_line=""
  while IFS= read -r _migrate_line; do
    _migrate_schema_lines+=("$_migrate_line")
  done < "$_CONFIG_SCHEMA_FILE"

  local _migrate_key _migrate_type _migrate_default _migrate_resolution \
    _migrate_legacy_probe _migrate_legacy_import _migrate_legacy_filename \
    _migrate_legacy_polarity _migrate_human_name _migrate_docs_anchor \
    _migrate_prompt_description
  for _migrate_line in "${_migrate_schema_lines[@]}"; do
    case "$_migrate_line" in
      ''|'#'*) continue ;;
    esac
    IFS='|' read -r _migrate_key _migrate_type _migrate_default _migrate_resolution \
      _migrate_legacy_probe _migrate_legacy_import _migrate_legacy_filename \
      _migrate_legacy_polarity _migrate_human_name _migrate_docs_anchor \
      _migrate_prompt_description <<< "$_migrate_line"
    _migrate_process_key "$_migrate_key" "$_migrate_type" "$_migrate_legacy_import" \
      "$_migrate_legacy_filename" "$_migrate_legacy_polarity" "$_migrate_human_name"
  done

  if ! _config_scaffold "$_MIGRATE_SCAFFOLD_EXCLUDE"; then
    echo "migrate-legacy-config.sh: error: could not scaffold claude-config.toml with schema defaults -- it may have malformed content; fix it by hand or delete it, then re-run" >&2
    exit 1
  fi

  if [ -n "${MIGRATE_LEGACY_CONFIG_DEBUG_RECORDS_FILE:-}" ]; then
    : > "$MIGRATE_LEGACY_CONFIG_DEBUG_RECORDS_FILE"
    if [ "${#_MIGRATE_RECORDS[@]}" -gt 0 ]; then
      printf '%s\n' "${_MIGRATE_RECORDS[@]}" >> "$MIGRATE_LEGACY_CONFIG_DEBUG_RECORDS_FILE"
    fi
  fi

  _migrate_run_delete_confirmation_phase

  echo ""
  echo "=== Legacy config files still present ==="
  local _migrate_any_remaining=0
  if [ "${#_MIGRATE_RECORDS[@]}" -gt 0 ]; then
    for _migrate_record in "${_MIGRATE_RECORDS[@]}"; do
      IFS='|' read -r _migrate_rec_path _migrate_rec_key _migrate_rec_value \
        _migrate_rec_outcome _migrate_rec_load_bearing <<< "$_migrate_record"
      [ -e "$_migrate_rec_path" ] || continue
      _migrate_any_remaining=1
      if [ "$_migrate_rec_load_bearing" = "true" ]; then
        printf '  %s (never offered for deletion -- see docs/config-file.md): rm "%s"\n' \
          "$_migrate_rec_path" "$_migrate_rec_path"
      else
        printf '  %s\n' "$_migrate_rec_path"
      fi
    done
  fi
  # `[ ... ] && echo` here would leak the test's own exit status as this
  # function's (and so the whole script's) exit status when the condition
  # is false, since this is the last statement in the function --
  # `if`/`fi` with no `else` always returns 0.
  if [ "$_migrate_any_remaining" -eq 0 ]; then
    echo "  (none)"
  fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
