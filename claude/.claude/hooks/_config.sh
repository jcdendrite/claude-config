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
#
# Every resolution-chain call (_config_value/_config_enabled/
# _config_schema_field/_config_read_key_from_file/_config_file_lines) does
# unwrapped filesystem I/O on every call, from this repo's 11+ hook/script
# call sites -- accepted for local-disk/single-machine deployment; a stuck
# read blocks the calling hook, the same tradeoff enforce-config-write-shape.sh/
# enforce-marker-script-shape.sh already disclose.
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
# _lib.sh sources this file, so existing _lib.sh callers are unaffected;
# this is the single bash definition of config-dir resolution.
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
# Sets _CONFIG_TRIM_RESULT (global) to STRING with leading/trailing ASCII
# whitespace stripped only (space, tab, CR, LF, VT, FF); deliberately not
# Unicode-aware.
# A trailing NBSP (U+00A0) or ideographic space (U+3000) survives the trim
# and correctly fails the value-subset check in _config_line_key_value below.
# Matches _config.py's explicit _ASCII_WHITESPACE trim rather than Python's
# bare .strip(), which would eat those two characters and silently accept a
# value this trim rejects -- the same divergence-avoidance _config_dir.py
# documents for a different check.
#
# `local LC_ALL=C` is load-bearing, not decorative: outside the C locale,
# glibc's iswspace() admits NBSP (U+00A0) and ideographic space (U+3000)
# into `[:space:]`/`[![:space:]]` bracket-expression matches.
# Scoped to this function's own local shell-variable shadow (reverts on
# return, per bash's normal `local` dynamic-scoping rules) rather than
# exported globally -- same reasoning set-session-title-from-branch.sh's
# own LC_ALL=C scoping gives for its bracket-range matching.
#
# Sets a global rather than printing for $(...) capture -- this is called
# per state-file line (up to 3 times each) by _config_line_key_value below,
# and a command-substitution fork per call is pure overhead; same
# fork-avoidance rationale as _lib.sh's _lib_pattern_component_count.
_config_trim() {
  local LC_ALL=C
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  _CONFIG_TRIM_RESULT="$s"
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
  _config_trim "$line"
  trimmed="$_CONFIG_TRIM_RESULT"
  [ -z "$trimmed" ] && return 1
  case "$trimmed" in
    '#'*) return 1 ;;
  esac
  case "$trimmed" in
    *'='*) ;;
    *) return 2 ;;
  esac
  local key="${trimmed%%=*}" value="${trimmed#*=}"
  _config_trim "$key"
  key="$_CONFIG_TRIM_RESULT"
  _config_trim "$value"
  value="$_CONFIG_TRIM_RESULT"
  [[ "$key" =~ ^[A-Za-z0-9_-]+$ ]] || return 2
  value=$(tr '[:upper:]' '[:lower:]' <<< "$value")
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
  # Bash's own `$(<file)` read, not `cat -- "$file"` -- reads FILE directly
  # in the command-substitution subshell rather than forking a separate
  # `cat` process. Braced with its own `2>/dev/null` (not appended to the
  # assignment line): bash reports a `$(<file)` read failure before normal
  # per-command stderr redirection would apply to it, so an inline
  # `2>/dev/null` on the assignment leaks the diagnostic instead of
  # suppressing it.
  { content=$(<"$file"); } 2>/dev/null || return 0
  content="${content#$'\xEF\xBB\xBF'}"
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s\n' "$line"
  done <<< "$content"
}

# _config_read_key_from_file KEY STATE_FILE [KNOWN_KEYS KEY_TYPE]
# Prints KEY's value from STATE_FILE to stdout and returns 0 if any
# conforming row for KEY exists; returns 1 (nothing printed) if the file is
# absent or has no conforming row for KEY. Last KEY row wins on duplicates,
# matching _config_set's own rewrite semantics. A grammar-invalid or
# wrong-type row is warned (stderr, truncated 80 chars) and skipped, never
# treated as authoritative -- a hand-edit typo on a `bool` key must not
# silently resolve as enabled under _config_enabled's any-value-but-false
# rule. An unrecognized key (grammatically valid but no config-keys.psv row)
# is warned separately from a malformed line. When config-keys.psv itself is
# unreadable, membership is treated as unknown, not "no keys known," so every
# grammatically-valid row is treated as recognized and key_type is left
# empty (matching neither the `bool` nor `enum:*` type-check case, so KEY's
# row still resolves instead of being misreported as absent).
#
# KNOWN_KEYS/KEY_TYPE are optional (both or neither, detected via `$# -eq 4`)
# and let a caller that already resolved both skip re-deriving them --
# today only _config_value's config-dir-or-home union, which calls this
# function once per location for the same KEY. Every existing 2-arg caller
# (install.sh, migrate-legacy-config.sh, this file's own tests) is
# unaffected.
_config_read_key_from_file() {
  [ "$#" -eq 2 ] || [ "$#" -eq 4 ] || {
    echo "_config_read_key_from_file: expected 2 or 4 args, got $#" >&2
    return 1
  }
  local key="$1" state_file="$2"
  local known_keys known_keys_status key_type
  if [ "$#" -eq 4 ]; then
    known_keys="$3"
    key_type="$4"
    if [ -n "$known_keys" ]; then known_keys_status=0; else known_keys_status=1; fi
  else
    # Computed once here, not via a per-line _config_schema_field call below
    # (which would re-read config-keys.psv once per state-file line).
    known_keys=$(_config_schema_known_keys)
    known_keys_status=$?
    key_type=""
    [ "$known_keys_status" -eq 0 ] && key_type=$(_config_schema_field "$key" type)
  fi
  local line key_out value_out status found=1 result=""
  while IFS= read -r line; do
    _config_line_key_value "$line" key_out value_out
    status=$?
    if [ "$status" -eq 2 ]; then
      printf '_config.sh: warning: skipping malformed line in %s: %s\n' "$state_file" "${line:0:80}" >&2
      continue
    fi
    [ "$status" -eq 0 ] || continue
    if [ "$known_keys_status" -eq 0 ]; then
      case "$known_keys" in
        *" $key_out "*) ;;
        *)
          printf '_config.sh: warning: skipping line for unrecognized key in %s: %s\n' "$state_file" "${line:0:80}" >&2
          continue
          ;;
      esac
    fi
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
# schema row, or FIELD is not one of the names above. Returns 3 (nothing
# printed, plus a distinct stderr warning naming the schema file) when
# config-keys.psv itself is missing or unreadable -- a partial stow-relink or
# interrupted `git pull` -- a genuinely different exit code from "unknown
# key," not just a distinct message, so a caller can map it to its own
# fail-closed direction instead of silently treating infrastructure failure
# the same as a misspelled key.
#
# Uses install.sh:479's own `IFS='|' read -r' idiom -- reads directly from
# the schema file rather than a bash array, since config-keys.psv is a real
# file, not an inline SENTINEL_INVENTORY literal.
_config_schema_field() {
  local key="$1" field="$2"
  if [ ! -r "$_CONFIG_SCHEMA_FILE" ]; then
    printf '_config.sh: warning: schema file not found or unreadable: %s\n' "$_CONFIG_SCHEMA_FILE" >&2
    return 3
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

# _config_schema_known_keys
# Prints every config-keys.psv key as a space-padded " key1 key2 ... " list,
# for a cheap `case " $list " in *" $key "*)` membership test against many
# candidate keys -- reads the schema file once per call, not once per
# candidate, matching _config_scaffold's own single-pass-over-config-keys.psv
# discipline (see that function's own comment on the same N-re-reads cost).
# Returns 1 (nothing printed), with the same distinct stderr warning
# _config_schema_field gives, when config-keys.psv itself is missing or
# unreadable -- callers must treat that as "membership unknown", not "no
# keys are known", since the latter would reject every row as unrecognized.
# Unlike _config_schema_field, this doesn't distinguish that case with its
# own exit 3: no caller today branches on this function's failure mode
# beyond a bare non-zero check, so there is nothing yet for a distinct code
# to be read by.
_config_schema_known_keys() {
  if [ ! -r "$_CONFIG_SCHEMA_FILE" ]; then
    printf '_config.sh: warning: schema file not found or unreadable: %s\n' "$_CONFIG_SCHEMA_FILE" >&2
    return 1
  fi
  local row_key rest list=" "
  while IFS='|' read -r row_key rest; do
    case "$row_key" in
      ''|'#'*) continue ;;
    esac
    list="$list$row_key "
  done < "$_CONFIG_SCHEMA_FILE"
  # A readable-but-empty (or all-comment, or mid-write-truncated) schema
  # file parses zero rows -- degrade to the same "membership unknown" signal
  # as unreadable, not "zero keys known": the latter would reject every row
  # as unrecognized during the exact truncation race this guard exists to
  # cover, just with the file still passing the bare [ -r ] check.
  if [ "$list" = " " ]; then
    printf '_config.sh: warning: schema file has no recognized key rows: %s\n' "$_CONFIG_SCHEMA_FILE" >&2
    return 1
  fi
  printf '%s' "$list"
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
#
# KNOWN_KEYS, KEY_TYPE, LEGACY_FILENAME, LEGACY_POLARITY are optional, and
# must be passed all four or none. They let a caller that has already
# computed KEY's own schema row pass it straight through to (and further
# down into _config_read_key_from_file) instead of re-forking
# _config_schema_known_keys/_config_schema_field for the same key.
# Same precomputed-args pattern as _config_read_key_from_file above,
# threaded one level further into it. Detected via `$# -eq 6`.
_config_location_value() {
  [ "$#" -eq 2 ] || [ "$#" -eq 6 ] || {
    echo "_config_location_value: expected 2 or 6 args, got $#" >&2
    return 1
  }
  local key="$1" dir="$2"
  local known_keys="" key_type="" legacy_filename="" legacy_polarity=""
  local precomputed=""
  if [ "$#" -eq 6 ]; then
    precomputed=1
    known_keys="$3"
    key_type="$4"
    legacy_filename="$5"
    legacy_polarity="$6"
  fi
  local state_file="$dir/$_CONFIG_STATE_FILENAME"
  local value status
  if [ -n "$precomputed" ]; then
    value=$(_config_read_key_from_file "$key" "$state_file" "$known_keys" "$key_type")
  else
    value=$(_config_read_key_from_file "$key" "$state_file")
  fi
  status=$?
  if [ "$status" -eq 0 ]; then
    printf '%s' "$value"
    return 0
  fi
  if [ -z "$precomputed" ]; then
    legacy_filename=$(_config_schema_field "$key" legacy-filename)
    legacy_polarity=$(_config_schema_field "$key" legacy-polarity)
  fi
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
        # Bash's own `$(<file)` read -- see _config_file_lines's own comment
        # on why the `2>/dev/null` must brace-wrap the assignment rather
        # than trail it.
        { raw=$(<"$legacy_path"); } 2>/dev/null || raw=""
        # [:space:], not [:blank:] — install.sh:531-547 and
        # pr-cost-section.sh:20-26 diverge on this trim class: [:space:]
        # strips a trailing CR, so a CRLF-authored one-line sentinel is read
        # identically here regardless of which legacy location produced it.
        _config_trim "$raw"
        mode="$_CONFIG_TRIM_RESULT"
        mode=$(tr '[:upper:]' '[:lower:]' <<< "$mode")
        type="$key_type"
        [ -n "$precomputed" ] || type=$(_config_schema_field "$key" type)
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
# fallback, not a validated contract. Exit 3: config-keys.psv itself is
# missing or unreadable. This is propagated from _config_schema_field's own
# exit 3 unchanged, never collapsed into 1. A caller must not treat it the
# same as "KEY has no schema row," since the right failure direction differs
# per key -- see _lib_worktree_enforcement_active/_lib_round_consult_gate_disabled
# in _lib.sh for the enforcement-critical keys that must fail closed on it.
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
  local resolution resolution_status legacy_probe
  resolution=$(_config_schema_field "$key" resolution)
  resolution_status=$?
  [ "$resolution_status" -eq 0 ] || return "$resolution_status"
  legacy_probe=$(_config_schema_field "$key" legacy-probe-on-resolution-failure)

  local primary_dir=""
  if [ -n "$config_dir_override" ]; then
    primary_dir="$config_dir_override"
  else
    primary_dir=$(_lib_config_dir 2>/dev/null) || primary_dir=""
  fi

  # A config-dir-or-home key with no override and a diverged $HOME is about
  # to call _config_location_value twice (primary_dir, then $HOME/.claude)
  # for the identical key. Precompute KEY's own schema row once here and
  # thread it through both calls as plain scalars, never a possibly-empty
  # array -- this file's own header already documents expanding one under
  # `set -u` as an error on bash before 4.4. union_active stays empty for
  # every other shape (single-location keys, or a caller-supplied
  # CONFIG_DIR_OVERRIDE), so _config_location_value falls back to its
  # plain 2-arg self-deriving form.
  local home_dir="" union_active=""
  local known_keys="" key_type="" legacy_filename="" legacy_polarity=""
  if [ -n "$primary_dir" ] && [ -z "$config_dir_override" ] \
     && [ "$resolution" = "config-dir-or-home" ] && [ -n "${HOME:-}" ]; then
    home_dir="${HOME%/}/.claude"
    if [ "$home_dir" != "${primary_dir%/}" ]; then
      union_active=1
      known_keys=$(_config_schema_known_keys) || known_keys=""
      key_type=$(_config_schema_field "$key" type)
      legacy_filename=$(_config_schema_field "$key" legacy-filename)
      legacy_polarity=$(_config_schema_field "$key" legacy-polarity)
    fi
  fi

  if [ -n "$primary_dir" ]; then
    local primary_value
    if [ -n "$union_active" ]; then
      local primary_status home_value home_status
      primary_value=$(_config_location_value "$key" "$primary_dir" \
        "$known_keys" "$key_type" "$legacy_filename" "$legacy_polarity")
      primary_status=$?
      home_value=$(_config_location_value "$key" "$home_dir" \
        "$known_keys" "$key_type" "$legacy_filename" "$legacy_polarity")
      home_status=$?
      # Unreachable today, since both calls above always pass exactly 6
      # args -- but a future edit to this block that miscounts one must not
      # let the resulting empty stdout fall through to the "true" checks
      # below, where _config_enabled's any-value-but-false rule would read
      # it as enabled regardless of which key this is.
      # $legacy_probe is "true" only for worktree_required.
      # worktree_required's failure-safe default is "enabled", so
      # enforcement stays armed.
      # Every other config-dir-or-home key's failure-safe default is
      # "false", so a resolution failure never grants.
      if [ "$primary_status" -ne 0 ] || [ "$home_status" -ne 0 ]; then
        if [ "$legacy_probe" = "true" ]; then
          printf 'true'
        else
          printf 'false'
        fi
        return 0
      fi
      # Assumes every config-dir-or-home key is bool, comparing against the
      # literal "true".
      # A future enum-typed config-dir-or-home key would silently defeat
      # this union.
      # Verify no config-keys.psv row combines config-dir-or-home with a
      # non-bool type before adding one.
      if [ "$primary_value" = "true" ] || [ "$home_value" = "true" ]; then
        printf 'true'
      else
        printf '%s' "$primary_value"
      fi
      return 0
    fi
    primary_value=$(_config_location_value "$key" "$primary_dir")
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
# 2 (config dir unresolvable), 3 (config-keys.psv unreadable) — 2 and 3 are
# both propagated from _config_value unchanged, never collapsed into 1. Any
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
# Additive-only: fills in a schema default for a key with no existing row
# in the target state file AND no config-keys.psv legacy-polarity value —
# never overwrites. A key whose legacy-polarity is `presence-enables`,
# `presence-disables`, or `content-matches` is left absent instead when it
# has no existing row, so _config_location_value's own legacy-file branch
# (only consulted when the key is entirely absent from the state file)
# stays reachable rather than being permanently shadowed by an explicit
# default row that duplicates the same value. Function-level contract, not
# left to caller-side "first run" gating alone.
#
# EXCLUDE_LIST is an optional space-separated (bash-3.2-safe) list of keys
# to leave absent even though they have no row — migrate-legacy-config.sh
# is the only caller that passes one, populated from a legacy-file read
# failure (any key) or a deferred enforcement-critical import gated on a
# TTY confirmation. A key on this list already has no row for a reason
# distinct from its legacy-polarity (an in-progress import decision, not a
# schema property), so it is checked first and short-circuits the
# legacy-polarity check below.
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
    # inside this loop (which would re-read this 15-row file once per key,
    # 15 total re-reads for one scaffold call) -- same field list as
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
      # A key with a legacy-polarity value has a legacy file that must stay
      # reachable for as long as the key itself has no row -- writing its
      # default here would make that file permanently inert with no
      # warning, since _config_location_value only ever consults it when
      # the key is entirely absent from the state file.
      case "$legacy_polarity" in
        presence-enables | presence-disables | content-matches) continue ;;
      esac
      printf '%s = %s\n' "$schema_key" "$default"
    done < "$_CONFIG_SCHEMA_FILE"
  } > "$tmp_file"
  mv -- "$tmp_file" "$state_file"
}
