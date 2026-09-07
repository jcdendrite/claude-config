#!/bin/bash
# Shared config-key reader/writer for every bash consumer: hooks (sourced
# transitively via _lib.sh, which sources this file), install.sh (sourced
# directly by repo-relative path, since install.sh runs before stow exists),
# and migrate-legacy-config.sh.
# This file is the only definition of how a config key resolves to a value
# -- config-keys.psv is its schema, claude-config.toml is its state file, and
# _config.py is the Python-runtime counterpart pinned to identical behavior
# by test_config_parser_parity.py. Keep this file dependency-free of the
# rest of _lib.sh (no _lib_realpath_m/_lib_capped/etc.) so install.sh can
# source it alone, before _lib.sh is stowed into place.
#
# Deliberately no `set -uo pipefail` here: `source` runs in the caller's own
# shell, so a `set` here would silently change the caller's own options for
# the rest of its run. Matches the same reasoning install.sh:99-101 gives
# for executing rather than sourcing its own migration scripts.
#
# Every empty-array iteration below is guarded with a `${#arr[@]}` count
# check first, never a bare `"${arr[@]}"` on a possibly-empty array.
# Expanding an unset array under `set -u` is an error on bash before 4.4
# (macOS system bash is 3.2), and this file cannot assume its caller has
# already turned `-u` off.
_CONFIG_SCHEMA_FILE="$(dirname "${BASH_SOURCE[0]}")/config-keys.psv"
_CONFIG_STATE_FILENAME="claude-config.toml"

# Prints the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set,
# else $HOME/.claude. $CLAUDE_CONFIG_DIR must be absolute -- a relative
# value resolves differently per invocation cwd, which is the exact
# path-mismatch bug this function exists to fix. Returns 1 with no stdout
# when CLAUDE_CONFIG_DIR is relative, or when CLAUDE_CONFIG_DIR is
# unset/empty and $HOME is also unset/empty.
#
# Call-site contract (load-bearing): bare interpolation,
# "$(_lib_config_dir)/whatever", is unsafe — under `set -e`, a failing
# *nested* command substitution does not abort the script, so a resolver
# failure silently collapses to "/whatever" (root-anchored) instead of being
# caught. Every call site must capture and check the exit status first:
#   config_dir=$(_lib_config_dir) || { <fail-open-or-deny per this caller>; }
#
# Moved here from _lib.sh: _lib.sh sources this file, so every existing
# _lib.sh caller keeps working unchanged, and this is now the single bash
# definition of config-dir resolution rather than one of three.
_lib_config_dir() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    case "$CLAUDE_CONFIG_DIR" in
      /*) ;;
      *) return 1 ;;  # relative values resolve differently per invocation
                      # cwd — the exact read/write path-mismatch bug this
                      # function fixes, just triggered a different way.
    esac
    printf '%s\n' "${CLAUDE_CONFIG_DIR%/}"
    return 0
  fi
  local home_norm="${HOME%/}"
  [ -n "$home_norm" ] || return 1
  printf '%s\n' "$home_norm/.claude"
}

# _config_trim STRING
# Strips leading/trailing ASCII whitespace only (space, tab, CR, LF, VT,
# FF) -- deliberately NOT a Unicode-aware trim. A value with a trailing
# NBSP (U+00A0) or ideographic space (U+3000) is left untouched by this
# trim and correctly fails the value-subset check in _config_line_key_value
# below, matching _config.py's explicit _ASCII_WHITESPACE-based trim (never
# Python's bare .strip(), which would additionally eat those two characters
# and silently accept a value a stricter trim would reject) -- the same
# divergence-avoidance _config_dir.py already documents one level over.
#
# `local LC_ALL=C` is load-bearing, not decorative: outside the C locale,
# glibc's iswspace() admits NBSP (U+00A0) and ideographic space (U+3000)
# into `[:space:]`/`[![:space:]]` bracket-expression matches.
# Scoped to this function's own local shell-variable shadow (reverts on
# return, per bash's normal `local` dynamic-scoping rules) rather than
# exported globally -- same reasoning set-session-title-from-branch.sh's
# own LC_ALL=C scoping gives for its bracket-range matching.
_config_trim() {
  local LC_ALL=C
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# _config_line_key_value LINE KEY_VAR VALUE_VAR
# Parses one line of claude-config.toml's `key = value` grammar. The value
# subset is exactly `true`, `false`, or a bare `[a-z0-9_-]+` token -- no
# quoting, no escapes, no arrays, no tables, so a TOML table/array/
# multi-line-string line always fails this check rather than being
# partially understood. A leading UTF-8 BOM and a trailing CR (tolerating a
# CRLF line mixed into an otherwise-LF file) are the caller's job, stripped
# once against the whole file's content before this function ever sees a
# single line — see _config_read_key_from_file for where that happens.
#
# Exit 0: KEY_VAR/VALUE_VAR populated via `printf -v` (bash-3.1+-safe).
# Exit 1: blank line or a full-line `#` comment — not a parse error, never
# warned by any caller.
# Exit 2: anything else — the caller decides whether/how to warn (skip-and-
# warn per line, not a whole-document rejection).
#
# The value is case-folded via `tr '[:upper:]' '[:lower:]'` before the
# subset check, so a hand-authored `TRUE`/`True`/`FALSE` still resolves.
# `tr`, not `${value,,}` (bash 4+): macOS system bash is 3.2, matching
# install.sh:626's own idiom.
# This repo's own key names are already lowercase snake_case, so only the
# value needs folding; the key is matched case-sensitively.
#
# `local LC_ALL=C` is load-bearing for the two bracket-range regex checks
# below (`[A-Za-z0-9_-]`, `[a-z0-9_-]`): outside the C locale, bracket-range
# matching is collation-order and can admit non-ASCII characters into a
# range like `[a-z]` -- the same hazard set-session-title-from-branch.sh's
# own header already documents for its allowlist regex, applying here to
# this file's own key/value grammar check.
_config_line_key_value() {
  local LC_ALL=C
  local line="$1" key_var="$2" value_var="$3"
  line="${line%$'\r'}"
  local trimmed
  trimmed=$(_config_trim "$line")
  [ -z "$trimmed" ] && return 1
  case "$trimmed" in
    '#'*) return 1 ;;
  esac
  case "$trimmed" in
    *'='*) ;;
    *) return 2 ;;
  esac
  local key="${trimmed%%=*}" value="${trimmed#*=}"
  key=$(_config_trim "$key")
  value=$(_config_trim "$value")
  [[ "$key" =~ ^[A-Za-z0-9_-]+$ ]] || return 2
  value=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
  [[ "$value" =~ ^[a-z0-9_-]+$ ]] || return 2
  printf -v "$key_var" '%s' "$key"
  printf -v "$value_var" '%s' "$value"
  return 0
}

# _config_file_lines FILE
# Emits FILE's content one line at a time, having stripped exactly one
# leading UTF-8 BOM from the whole content first. No-op (emits nothing) if
# FILE does not exist or cannot be read. Shared by every function below that
# needs to walk a state file's raw lines.
_config_file_lines() {
  local file="$1"
  [ -f "$file" ] || return 0
  local content
  content=$(cat -- "$file" 2>/dev/null) || return 0
  content="${content#$'\xEF\xBB\xBF'}"
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s\n' "$line"
  done <<< "$content"
}

# _config_read_key_from_file KEY STATE_FILE
# Prints KEY's value from STATE_FILE to stdout and returns 0 if any
# conforming row for KEY exists; returns 1 (nothing printed) if the file is
# absent or has no conforming row for KEY. A duplicate KEY row is not
# rejected -- the LAST occurrence wins, matching this file's own
# last-write-wins rewrite semantics in _config_set. Every non-blank,
# non-comment line that fails the value-subset grammar is warned once, to
# stderr, truncated to 80 chars -- a hand-edit typo affects only that one
# key, not the whole document -- regardless of whether that malformed line
# belongs to KEY or a different key, since this function already has to
# walk the whole file.
#
# A row for KEY whose value passes the generic grammar check but fails
# KEY's own config-keys.psv `type` (e.g. a `bool` key's value being neither
# `true` nor `false`) is warned and skipped the same way, not returned as
# authoritative -- otherwise a hand-edit typo on a bool key (`autonomous_shipping
# = notabool`) would resolve as "enabled" under _config_enabled's own
# any-value-but-false rule, the wrong direction for a key whose off-state
# contract is security-relevant.
_config_read_key_from_file() {
  local key="$1" state_file="$2"
  local key_type
  key_type=$(_config_schema_field "$key" type)
  local line key_out value_out status found=1 result=""
  while IFS= read -r line; do
    _config_line_key_value "$line" key_out value_out
    status=$?
    if [ "$status" -eq 2 ]; then
      printf '_config.sh: warning: skipping malformed line in %s: %s\n' "$state_file" "${line:0:80}" >&2
      continue
    fi
    [ "$status" -eq 0 ] || continue
    if [ "$key_out" = "$key" ]; then
      case "$key_type" in
        bool)
          case "$value_out" in
            true|false) ;;
            *)
              printf '_config.sh: warning: skipping malformed line in %s: %s\n' "$state_file" "${line:0:80}" >&2
              continue
              ;;
          esac
          ;;
        enum:*)
          case "$value_out" in
            false|"${key_type#enum:}") ;;
            *)
              printf '_config.sh: warning: skipping malformed line in %s: %s\n' "$state_file" "${line:0:80}" >&2
              continue
              ;;
          esac
          ;;
      esac
      result="$value_out"
      found=0
    fi
  done < <(_config_file_lines "$state_file")
  [ "$found" -eq 0 ] || return 1
  printf '%s' "$result"
}

# _config_schema_field KEY FIELD
# Prints one column of KEY's config-keys.psv row. FIELD is one of: type,
# default, resolution, legacy-probe-on-resolution-failure,
# legacy-import-locations, legacy-filename, legacy-polarity, human-name,
# docs-anchor, prompt-description. Returns 1 (nothing printed) if KEY has no
# schema row, or FIELD is not one of the names above. Also returns 1 (with a
# distinct stderr warning naming the schema file) when config-keys.psv itself
# is missing or unreadable -- a partial stow-relink or interrupted `git pull`
# -- rather than silently reusing the same-return-code "unknown key" signal
# with no indication the real cause is infrastructure, not the key name.
#
# Uses install.sh:479's own `IFS='|' read -r' idiom -- reads directly from
# the schema file rather than a bash array, since config-keys.psv is a real
# file, not an inline SENTINEL_INVENTORY literal.
_config_schema_field() {
  local key="$1" field="$2"
  if [ ! -r "$_CONFIG_SCHEMA_FILE" ]; then
    printf '_config.sh: warning: schema file not found or unreadable: %s\n' "$_CONFIG_SCHEMA_FILE" >&2
    return 1
  fi
  local row_key type default resolution legacy_probe legacy_import legacy_filename legacy_polarity human_name docs_anchor prompt_description
  while IFS='|' read -r row_key type default resolution legacy_probe legacy_import legacy_filename legacy_polarity human_name docs_anchor prompt_description; do
    case "$row_key" in
      ''|'#'*) continue ;;
    esac
    [ "$row_key" = "$key" ] || continue
    case "$field" in
      type) printf '%s' "$type" ;;
      default) printf '%s' "$default" ;;
      resolution) printf '%s' "$resolution" ;;
      legacy-probe-on-resolution-failure) printf '%s' "$legacy_probe" ;;
      legacy-import-locations) printf '%s' "$legacy_import" ;;
      legacy-filename) printf '%s' "$legacy_filename" ;;
      legacy-polarity) printf '%s' "$legacy_polarity" ;;
      human-name) printf '%s' "$human_name" ;;
      docs-anchor) printf '%s' "$docs_anchor" ;;
      prompt-description) printf '%s' "$prompt_description" ;;
      *) return 1 ;;
    esac
    return 0
  done < "$_CONFIG_SCHEMA_FILE"
  return 1
}

# _config_location_value KEY DIR
# KEY's effective value as seen from DIR alone: DIR is assumed already
# resolved (no resolution-failure handling here — see _config_value for
# that). Precedence: DIR/claude-config.toml is checked first, and
# any conforming row there for KEY is authoritative; only when KEY is
# entirely absent from that file does DIR's own legacy file get consulted
# (interpreted per KEY's legacy-polarity); only when that legacy file is
# also absent does the schema default apply. Always prints a value; DIR
# itself is never treated as unresolvable here (a real I/O failure on a
# state/legacy file degrades the same way an absent file does, via `[ -f ]`
# returning false on EACCES/ESTALE).
_config_location_value() {
  local key="$1" dir="$2"
  local state_file="$dir/$_CONFIG_STATE_FILENAME"
  local value
  if value=$(_config_read_key_from_file "$key" "$state_file"); then
    printf '%s' "$value"
    return 0
  fi
  local legacy_filename legacy_polarity
  legacy_filename=$(_config_schema_field "$key" legacy-filename)
  legacy_polarity=$(_config_schema_field "$key" legacy-polarity)
  local legacy_path="$dir/$legacy_filename"
  case "$legacy_polarity" in
    presence-enables)
      if [ -f "$legacy_path" ]; then printf 'true'; else printf 'false'; fi
      return 0
      ;;
    presence-disables)
      if [ -f "$legacy_path" ]; then printf 'false'; else printf 'true'; fi
      return 0
      ;;
    content-matches)
      if [ -f "$legacy_path" ]; then
        local raw mode type expected
        raw=$(cat -- "$legacy_path" 2>/dev/null) || raw=""
        # [:space:], not [:blank:] — install.sh:531-547 and
        # pr-cost-section.sh:20-26 diverge on this trim class: [:space:]
        # strips a trailing CR, so a CRLF-authored one-line sentinel is read
        # identically here regardless of which legacy location produced it.
        mode=$(_config_trim "$raw")
        mode=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
        type=$(_config_schema_field "$key" type)
        expected="${type#enum:}"
        if [ "$mode" = "$expected" ]; then
          printf '%s' "$expected"
          return 0
        fi
      fi
      printf 'false'
      return 0
      ;;
  esac
  _config_schema_field "$key" default
}

# _config_value KEY [CONFIG_DIR_OVERRIDE]
# Prints KEY's effective value ("true", "false", or an enum literal) to
# stdout. Exit 0: resolved (value printed). Exit 2: the config dir could not
# be resolved and KEY's schema row does not authorize a raw $HOME/.claude
# probe on that failure (legacy-probe-on-resolution-failure) — see
# _config_enabled below for why this 2 is a different meaning from
# config-get.sh's own exit code 2. Exit 1: KEY has no schema row at all —
# no legitimate caller in this repo reaches this path (every call site
# passes a hardcoded literal key name, and config-get.sh checks for an
# unknown key itself before ever calling in here), so this is a defensive
# fallback, not a validated contract.
#
# CONFIG_DIR_OVERRIDE lets a caller that already has its own config dir (or
# needs a specific one — e.g. transcript-analysis.py's --all-accounts loop,
# via _config.py's config_dir_override parameter) skip re-resolving it and
# skip the config-dir-or-home union below entirely, mirroring
# _lib_autonomous_shipping_sentinel_present's own CONFIG_DIR argument.
#
# Union semantics: for a config-dir-or-home key, the value is OR'd
# across BOTH locations' own independently-resolved effective value (each
# going through its own state-file-then-legacy-then-default chain via
# _config_location_value) — not "first location found wins." An explicit
# `false` row in the config dir's state file must not defeat a `true`
# produced by $HOME/.claude's legacy file, the same invariant
# _lib_worktree_enforcement_active already preserves today. Only run when
# CONFIG_DIR_OVERRIDE is unset: an override caller has already opted out of
# the union (see above).
_config_value() {
  local key="$1" config_dir_override="${2:-}"
  local resolution legacy_probe
  resolution=$(_config_schema_field "$key" resolution) || return 1
  legacy_probe=$(_config_schema_field "$key" legacy-probe-on-resolution-failure)

  local primary_dir=""
  if [ -n "$config_dir_override" ]; then
    primary_dir="$config_dir_override"
  else
    primary_dir=$(_lib_config_dir 2>/dev/null) || primary_dir=""
  fi

  if [ -n "$primary_dir" ]; then
    local primary_value
    primary_value=$(_config_location_value "$key" "$primary_dir")
    if [ -z "$config_dir_override" ] && [ "$resolution" = "config-dir-or-home" ] && [ -n "${HOME:-}" ]; then
      local home_dir="${HOME%/}/.claude"
      if [ "$home_dir" != "${primary_dir%/}" ]; then
        local home_value
        home_value=$(_config_location_value "$key" "$home_dir")
        if [ "$primary_value" = "true" ] || [ "$home_value" = "true" ]; then
          printf 'true'
        else
          printf '%s' "$primary_value"
        fi
        return 0
      fi
    fi
    printf '%s' "$primary_value"
    return 0
  fi

  # Primary resolution failed. Only worktree_required's row carries
  # legacy-probe-on-resolution-failure: true today — every other
  # key (including autonomous_shipping, deliberately) returns 2 here rather
  # than granting on a resolution failure, since that would be the wrong
  # direction for a mechanism that removes a human checkpoint.
  if [ -z "$config_dir_override" ] && [ "$resolution" = "config-dir-or-home" ] && [ "$legacy_probe" = "true" ] && [ -n "${HOME:-}" ]; then
    printf '%s' "$(_config_location_value "$key" "${HOME%/}/.claude")"
    return 0
  fi
  return 2
}

# _config_enabled KEY [CONFIG_DIR_OVERRIDE]
# Boolean wrapper over _config_value: 0 (true/enabled), 1 (false/disabled),
# 2 (config dir unresolvable — propagated from _config_value unchanged). Any
# resolved value other than the literal "false" counts as enabled — an
# enum-typed key's only "off" value is "false", so e.g. pr_cost_disclosure
# resolving to "dollars" is enabled.
#
# This exit code 2 (config dir unresolvable) is a DIFFERENT meaning from
# config-get.sh's own exit code 2 (unknown key) -- config-get.sh always
# checks for an unknown key first, before ever calling into this file's
# functions, so the two 2s never collide in practice, but a future edit to
# either file's exit-code table must not transpose them.
_config_enabled() {
  local key="$1" config_dir_override="${2:-}"
  local value status
  value=$(_config_value "$key" "$config_dir_override")
  status=$?
  [ "$status" -eq 0 ] || return "$status"
  [ "$value" = "false" ] && return 1
  return 0
}

# _config_set KEY VALUE [CONFIG_DIR_OVERRIDE]
# The only writer. Preserves comments, blank lines, and key order: rewrites
# KEY's own line in place at its existing position if present (normalizing
# that one line to `key = value`), else appends a new `key = value` line.
# Refuses to write at all (returns 1, target file left byte-for-byte
# unchanged) when the existing file contains any line outside the
# value-subset grammar -- a write against content this reader doesn't fully
# understand risks silently discarding it.
#
# Also refuses (returns 1, no write) when VALUE doesn't match KEY's own
# declared config-keys.psv type: a `bool` key accepts only `true`/`false`;
# an `enum:X` key accepts only `false` or the literal `X`.
#
# Atomicity: mktemp targets the SAME directory as the state file, not a
# bare `mktemp` (which defaults to $TMPDIR, commonly a different filesystem
# — silently defeating `mv`'s same-filesystem atomicity via EXDEV or a
# copy+unlink fallback). Matches pr-diff-against-base.sh:69 and
# _stow_migration_lib.sh:551's existing precedent.
#
# Rejects an empty or exactly-`/` resolved config dir before any
# mkdir/write, failing the same way an unresolvable config dir already
# fails (exit 2) — neither _lib_config_dir nor config_dir() validates its
# own output beyond "absolute," and this is the first thing in this repo
# that routes a WRITE through that resolver.
#
# Sanctioned callers only: install.sh's interactive [y/N] path and
# migrate-legacy-config.sh's import phase. Enforced by
# enforce-config-write-shape.sh at the tool-call boundary, not by this
# function itself.
_config_set() {
  local key="$1" value="$2" config_dir_override="${3:-}"
  local key_type
  key_type=$(_config_schema_field "$key" type) || return 1
  case "$key_type" in
    bool)
      case "$value" in
        true|false) ;;
        *) return 1 ;;
      esac
      ;;
    enum:*)
      case "$value" in
        false|"${key_type#enum:}") ;;
        *) return 1 ;;
      esac
      ;;
    *) return 1 ;;
  esac
  local config_dir
  if [ -n "$config_dir_override" ]; then
    config_dir="$config_dir_override"
  else
    config_dir=$(_lib_config_dir) || return 2
  fi
  case "$config_dir" in
    ""|/) return 2 ;;
  esac
  local state_file="$config_dir/$_CONFIG_STATE_FILENAME"
  mkdir -p -- "$(dirname -- "$state_file")" || return 1

  local -a existing_lines=()
  local line
  while IFS= read -r line; do
    existing_lines+=("$line")
  done < <(_config_file_lines "$state_file")

  local i key_out value_out status found=1
  for ((i = 0; i < ${#existing_lines[@]}; i++)); do
    _config_line_key_value "${existing_lines[$i]}" key_out value_out
    status=$?
    [ "$status" -eq 2 ] && return 1
    if [ "$status" -eq 0 ] && [ "$key_out" = "$key" ]; then
      found=0
    fi
  done

  local tmp_file
  tmp_file=$(mktemp "$(dirname -- "$state_file")/.claude-config.XXXXXX") || return 1
  {
    for ((i = 0; i < ${#existing_lines[@]}; i++)); do
      _config_line_key_value "${existing_lines[$i]}" key_out value_out
      status=$?
      if [ "$status" -eq 0 ] && [ "$key_out" = "$key" ]; then
        printf '%s = %s\n' "$key" "$value"
      else
        printf '%s\n' "${existing_lines[$i]}"
      fi
    done
    [ "$found" -eq 0 ] || printf '%s = %s\n' "$key" "$value"
  } > "$tmp_file"
  mv -- "$tmp_file" "$state_file"
}

# _config_scaffold [EXCLUDE_LIST] [CONFIG_DIR_OVERRIDE]
# Additive-only: fills in a schema default for every key with no existing
# row in the target state file, regardless of how any existing row got
# there (hand-edit, prior import, prior scaffold) — never overwrites.
# Function-level contract, not left to caller-side "first run" gating alone.
#
# EXCLUDE_LIST is an optional space-separated (bash-3.2-safe) list of keys
# to leave absent even though they have no row — migrate-legacy-config.sh
# is the only caller that passes one, populated from a legacy-file read
# failure (any key) or a deferred enforcement-critical import gated on a
# TTY confirmation.
#
# Same mkdir-p and atomic same-directory mktemp-then-mv guarantee as
# _config_set.
_config_scaffold() {
  local exclude_list="${1:-}" config_dir_override="${2:-}"
  local config_dir
  if [ -n "$config_dir_override" ]; then
    config_dir="$config_dir_override"
  else
    config_dir=$(_lib_config_dir) || return 2
  fi
  case "$config_dir" in
    ""|/) return 2 ;;
  esac
  local state_file="$config_dir/$_CONFIG_STATE_FILENAME"
  mkdir -p -- "$(dirname -- "$state_file")" || return 1

  local -a existing_lines=()
  local line
  while IFS= read -r line; do
    existing_lines+=("$line")
  done < <(_config_file_lines "$state_file")

  local i key_out value_out status
  local -a present_keys=()
  for ((i = 0; i < ${#existing_lines[@]}; i++)); do
    _config_line_key_value "${existing_lines[$i]}" key_out value_out
    status=$?
    [ "$status" -eq 2 ] && return 1
    [ "$status" -eq 0 ] && present_keys+=("$key_out")
  done

  local tmp_file
  tmp_file=$(mktemp "$(dirname -- "$state_file")/.claude-config.XXXXXX") || return 1
  {
    for ((i = 0; i < ${#existing_lines[@]}; i++)); do
      printf '%s\n' "${existing_lines[$i]}"
    done
    local schema_key type default resolution legacy_probe legacy_import legacy_filename legacy_polarity human_name docs_anchor prompt_description
    local already excluded present_key
    # One pass over config-keys.psv, not a per-key _config_schema_field call
    # inside this loop (which would re-read this 14-row file once per key,
    # 14 total re-reads for one scaffold call) -- same field list as
    # _config_schema_field's own read, so key and default come off the same
    # line here instead of a second file scan.
    while IFS='|' read -r schema_key type default resolution legacy_probe legacy_import legacy_filename legacy_polarity human_name docs_anchor prompt_description; do
      case "$schema_key" in
        ''|'#'*) continue ;;
      esac
      already=1
      # Count-guarded (${#arr[@]} first): expanding "${present_keys[@]}"
      # directly when the array is empty (a fresh/absent state file) is an
      # unbound-variable error under `set -u` on bash before 4.4.
      if [ "${#present_keys[@]}" -gt 0 ]; then
        for present_key in "${present_keys[@]}"; do
          [ "$present_key" = "$schema_key" ] && already=0 && break
        done
      fi
      [ "$already" -eq 0 ] && continue
      excluded=1
      case " $exclude_list " in
        *" $schema_key "*) excluded=0 ;;
      esac
      [ "$excluded" -eq 0 ] && continue
      printf '%s = %s\n' "$schema_key" "$default"
    done < "$_CONFIG_SCHEMA_FILE"
  } > "$tmp_file"
  mv -- "$tmp_file" "$state_file"
}
