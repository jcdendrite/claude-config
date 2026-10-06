#!/bin/bash
set -euo pipefail

# The dotted env paths Claude Code may write into the live file that an
# overlay env object, which never carries forward, would otherwise silently
# drop. Kept in sync with guard-settings-session-keys.sh's GUARDED_KEYS_JSON
# dotted entries via --print-guarded-keys -- see test_render_settings.py.
# The script assumes /effort and /config write env.CLAUDE_CODE_EFFORT_LEVEL and
# env.ANTHROPIC_MODEL directly into the live settings.json.
# That assumption is unverified against a live Claude Code session, so the
# script re-applies those paths defensively regardless.
# Defined here, ahead of the direct-invocation mode below, so that mode
# never depends on code that runs later in the script.
RULE4_DOTTED_PATHS_JSON='["env.CLAUDE_CODE_EFFORT_LEVEL", "env.ANTHROPIC_MODEL"]'

# Base-owned top-level keys: base always wins and the prior file never
# contributes them. Only keys settings.base.json itself defines belong here,
# since a base-owned key that base does not define is deleted on every render.
# Declared ahead of the direct-invocation modes below for the same reason as
# RULE4_DOTTED_PATHS_JSON.
BASE_OWNED_KEYS_JSON='["permissions", "hooks", "statusLine", "skillOverrides"]'

# An overlay env key must be one of these exact names: the telemetry variables
# listed in docs/security-hardening.md, plus the variables Claude Code writes into the
# live settings.json (see RULE4_DOTTED_PATHS_JSON).
# A name pattern would also admit variables that run commands, redirect
# requests, or carry credentials.
# Declared ahead of the direct-invocation modes below for the same reason as
# RULE4_DOTTED_PATHS_JSON.
OVERLAY_ENV_ALLOWED_NAMES_JSON='["ANTHROPIC_MODEL", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_ENABLE_TELEMETRY", "DISABLE_BUG_COMMAND", "DISABLE_ERROR_REPORTING", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_TELEMETRY"]'

# Direct-invocation modes for tests: each prints one constant as JSON and exits
# before any render logic runs, mirroring guard-settings-session-keys.sh's own
# --print-guarded-keys mode.
if [[ "${1:-}" == "--print-rule4-dotted-paths" ]]; then
  printf '%s\n' "$RULE4_DOTTED_PATHS_JSON"
  exit 0
fi
if [[ "${1:-}" == "--print-base-owned-keys" ]]; then
  printf '%s\n' "$BASE_OWNED_KEYS_JSON"
  exit 0
fi
if [[ "${1:-}" == "--print-env-allowed-names" ]]; then
  printf '%s\n' "$OVERLAY_ENV_ALLOWED_NAMES_JSON"
  exit 0
fi

if [[ $# -gt 0 ]]; then
  echo "render-settings.sh: unexpected argument '$1' -- this script takes no arguments; set CLAUDE_CONFIG_DIR to render another profile" >&2
  exit 2
fi

# Renders $CLAUDE_CONFIG_DIR/settings.json (or $HOME/.claude/settings.json
# when unset) by merging settings.base.json with an optional overlay.
# The overlay is settings.overlay.json in the same directory. No overlay means
# settings.json is base only.
#
# Overlay top-level keys are restricted to {autoMode, env,
# skillListingBudgetFraction}, plus a conditionally-admissible `permissions`
# carrying only `defaultMode` -- any other key or shape is rejected outright,
# not merged. See docs/auto-mode.md for the full contract.
#
# Every top-level key base and the overlay don't claim carries forward from
# the prior settings.json (theme/model/effortLevel and friends), since
# Claude Code writes those directly into the live file rather than into
# base or overlay. Only top-level keys absent from the merged result carry
# forward, so base and overlay stay authoritative for every key they set.
#
# Replaces settings.json via mktemp+mv (never cp/redirect) so a symlinked
# target is replaced, not written through. Exits without writing when the
# merged result equals the prior settings.json and that file is a regular,
# non-symlink file.
#
# Usage: render-settings.sh (CLAUDE_CONFIG_DIR picks the profile)

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
base_file="$config_dir/settings.base.json"
overlay_file="$config_dir/settings.overlay.json"
target="$config_dir/settings.json"

# Overlay-allowed top-level keys: never carried forward, so deleting one
# from the overlay actually takes effect on the next render.
OVERLAY_ALLOWED_KEYS_JSON='["autoMode", "env", "skillListingBudgetFraction"]'
# defaultMode enum: default and plan are accepted.
# bypassPermissions and acceptEdits are refused outright.
# auto is refused until the shipped ask-review-permissions.sh's reason text is
# verified under it (the "Untested" list in docs/security-hardening.md).
# dontAsk is refused because it is untested (the "Untested" list in
# docs/security-hardening.md). The docs say it denies whatever would otherwise
# prompt.
ACCEPTED_DEFAULT_MODES_JSON='["default", "plan"]'

if [[ ! -f "$base_file" ]]; then
  echo "render-settings.sh: $base_file not found -- cannot render settings.json without it" >&2
  exit 1
fi

# Succeeds when $1 holds a JSON object and fails when it does not. Exits when
# jq itself could not run (status 126 or 127), since that says nothing about
# the file.
require_json_object() {
  local file="$1"
  local jq_status=0
  jq -e 'type == "object"' -- "$file" >/dev/null 2>&1 || jq_status=$?
  case "$jq_status" in
    0) return 0 ;;
    126 | 127)
      echo "render-settings.sh: jq could not run (exit $jq_status; missing, not executable, or a broken install) -- install or repair jq, then re-render; $file was not examined" >&2
      exit 1
      ;;
    *) return 1 ;;
  esac
}

# Every overlay refusal exits before any write, so the prior settings.json keeps
# its deny rules and hooks while base changes wait on the overlay.
# $1 names the problem and the optional $2 names the way out.
refuse_overlay() {
  echo "render-settings.sh: $1 -- refusing to render${2:+; $2}; an existing settings.json keeps its previous deny rules and hooks, and base changes are not delivered until the overlay is fixed" >&2
  exit 1
}

if ! require_json_object "$base_file"; then
  echo "render-settings.sh: $base_file is not valid JSON or not a JSON object -- refusing to render" >&2
  exit 1
fi

overlay_json='{}'
if [[ -e "$overlay_file" ]]; then
  if [[ ! -r "$overlay_file" ]]; then
    refuse_overlay "$overlay_file exists but is not readable"
  fi

  # Tighten the overlay to mode 600 so other local accounts can't read it.
  # No `--` here, because BSD chmod rejects it.
  chmod 600 "$overlay_file" 2>/dev/null || echo "render-settings.sh: warning: could not chmod 600 $overlay_file" >&2

  if ! require_json_object "$overlay_file"; then
    refuse_overlay "$overlay_file is not valid JSON or not a JSON object"
  fi

  # Overlay keys outside the closed set are rejected, not merged.
  # `permissions` is conditionally admissible, so it joins the set here.
  if ! jq -e --argjson allowed "$OVERLAY_ALLOWED_KEYS_JSON" '(keys - $allowed - ["permissions"]) == []' -- "$overlay_file" >/dev/null 2>&1; then
    bad_keys="$(jq -r --argjson allowed "$OVERLAY_ALLOWED_KEYS_JSON" '(keys - $allowed - ["permissions"]) | join(", ")' -- "$overlay_file")"
    allowed_keys="$(jq -r '. + ["permissions"] | sort | join(", ")' <<<"$OVERLAY_ALLOWED_KEYS_JSON")"
    refuse_overlay "$overlay_file has top-level keys outside {$allowed_keys}: $bad_keys" \
      "set another key such as model or enabledPlugins with /config or by editing $target, which carries it forward"
  fi

  # permissions is conditionally admissible: only {defaultMode}, nothing else.
  if jq -e 'has("permissions")' -- "$overlay_file" >/dev/null 2>&1; then
    if ! jq -e '.permissions | type == "object"' -- "$overlay_file" >/dev/null 2>&1; then
      refuse_overlay "$overlay_file has a non-object permissions value"
    fi
    if ! jq -e '(.permissions | keys) == ["defaultMode"]' -- "$overlay_file" >/dev/null 2>&1; then
      extra_keys="$(jq -r '(.permissions | keys) - ["defaultMode"] | join(", ")' -- "$overlay_file")"
      if [[ -n "$extra_keys" ]]; then
        refuse_overlay "$overlay_file has permissions keys outside {defaultMode}: $extra_keys"
      else
        refuse_overlay "$overlay_file has a permissions object but does not set defaultMode"
      fi
    fi
    # The type check comes first because jq's index() with an array operand
    # searches for a subarray, so ["plan"] would otherwise match "plan".
    if ! jq -e '.permissions.defaultMode | type == "string"' -- "$overlay_file" >/dev/null 2>&1; then
      refuse_overlay "$overlay_file has a non-string permissions.defaultMode"
    fi
    if ! jq -e --argjson accepted "$ACCEPTED_DEFAULT_MODES_JSON" '.permissions.defaultMode as $m | ($accepted | index($m)) != null' -- "$overlay_file" >/dev/null 2>&1; then
      bad_mode="$(jq -r '.permissions.defaultMode' -- "$overlay_file")"
      refuse_overlay "$overlay_file sets permissions.defaultMode=\"$bad_mode\", which is not accepted" \
        "use per-session \`claude --permission-mode $bad_mode\` or project-scope .claude/settings.local.json instead"
    fi
  fi

  # env keys must be on the exact-name allowlist and their values strings.
  if jq -e 'has("env")' -- "$overlay_file" >/dev/null 2>&1; then
    if ! jq -e '.env | type == "object"' -- "$overlay_file" >/dev/null 2>&1; then
      refuse_overlay "$overlay_file has a non-object env value"
    fi
    if ! jq -e --argjson allowed "$OVERLAY_ENV_ALLOWED_NAMES_JSON" '[.env | keys[] | select(. as $name | ($allowed | index($name)) == null)] == []' -- "$overlay_file" >/dev/null 2>&1; then
      bad_names="$(jq -r --argjson allowed "$OVERLAY_ENV_ALLOWED_NAMES_JSON" '[.env | keys[] | select(. as $name | ($allowed | index($name)) == null)] | join(", ")' -- "$overlay_file")"
      allowed_names="$(jq -r 'join(", ")' <<<"$OVERLAY_ENV_ALLOWED_NAMES_JSON")"
      refuse_overlay "$overlay_file has env key(s) outside the allowed set {$allowed_names}: $bad_names" \
        "export any other variable from your shell profile; for a credential, prefer a secret-store-backed credential helper (docs/security-hardening.md)"
    fi
    if ! jq -e '[.env[] | type == "string"] | all' -- "$overlay_file" >/dev/null 2>&1; then
      bad_values="$(jq -r '[.env | to_entries[] | select(.value | type != "string") | .key] | join(", ")' -- "$overlay_file")"
      refuse_overlay "$overlay_file has non-string env value(s) for: $bad_values"
    fi
  fi

  overlay_json="$(jq -c '.' -- "$overlay_file")"
fi

base_json="$(jq -c '.' -- "$base_file")"

# $target here is this script's own prior output, not user-supplied input to
# validate: a missing, empty, unparseable, or non-object prior file means
# nothing to carry forward, not a render failure.
prev_json='{}'
prior_is_object=false
if [[ -f "$target" ]]; then
  if prior_candidate="$(jq -c -s 'if length == 1 and (.[0] | type) == "object" then .[0] else empty end' -- "$target" 2>/dev/null)" \
     && [[ -n "$prior_candidate" ]]; then
    prev_json="$prior_candidate"
    prior_is_object=true
  fi
fi

# Rule 1: base-owned keys always win.
# Rule 2: overlay-allowed keys never carry forward at the top level, so
#   deleting one from the overlay takes effect.
# Rule 3: every other top-level key absent from the merged result carries
#   forward from the prior render.
# Rule 4: the dotted env paths Claude Code may write directly are
#   re-applied afterward wherever base/overlay didn't already set them.
# A prior-file value of literal null is treated as absent for rule 3's
# carry-forward. All merging is shallow (jq `+`, never `*`) -- a deep merge
# would resurrect a nested path base deliberately removed.
# permissions.defaultMode is a single named nested exception layered on top
# of rule 1's wholesale permissions merge, not a general reopening of
# `permissions`.
#
# Carry-forward preserves whatever top-level key it finds, so it is a
# functionality mechanism rather than an integrity control.
# Anything able to plant a key in settings.json can equally rewrite the rc
# block that invokes this script, so no rule here bounds such a writer.
if ! render_output="$(jq -n \
    --argjson base "$base_json" \
    --argjson overlay "$overlay_json" \
    --argjson prev "$prev_json" \
    --argjson baseOwned "$BASE_OWNED_KEYS_JSON" \
    --argjson overlayAllowed "$OVERLAY_ALLOWED_KEYS_JSON" \
    --argjson rule4Paths "$RULE4_DOTTED_PATHS_JSON" \
    '
    def path_present($obj; $path):
      ($path | split(".")) as $segs
      | reduce $segs[] as $seg ({present: true, cur: $obj};
          if .present and (.cur | type) == "object" and (.cur | has($seg))
          then {present: true, cur: .cur[$seg]}
          else {present: false, cur: null}
          end)
      | .present;
    def path_get($obj; $path): $obj | getpath($path | split("."));
    def path_set($obj; $path; $val): $obj | setpath($path | split("."); $val);

    # $overlay comes from a later read of the file than the validation checks,
    # so no check except this strip applies to it: the strip drops any base-owned
    # key a rewrite in between added, and every other key passes unchecked.
    ($overlay | del(.permissions) | with_entries(select(.key as $k | ($baseOwned | index($k)) == null))) as $overlayNonPerm
    | ($base + $overlayNonPerm) as $m12
    | ($m12 | keys) as $m12Keys
    | ($prev | keys) as $prevKeys
    | ($prevKeys - ($baseOwned + $overlayAllowed) - $m12Keys) as $rule3Keys
    | ($prev | with_entries(select(.key as $k | ($rule3Keys | index($k) != null) and (.value != null)))) as $rule3Contribution
    | ($m12 + $rule3Contribution) as $m123
    | (if ($overlay | has("permissions")) and ($overlay.permissions | has("defaultMode"))
       then ($m123 | .permissions.defaultMode = $overlay.permissions.defaultMode)
       else $m123 end) as $mAfter11
    | (reduce $rule4Paths[] as $p ($mAfter11;
         if path_present(.; $p) then .
         elif path_present($prev; $p) then path_set(.; $p; path_get($prev; $p))
         else . end)) as $final
    | ([$prevKeys[] | select(. as $k
          | ($prev[$k] != null)
          and (($final | has($k)) | not)
          and (($k == "env" and $prev[$k] == {}) | not))] | sort) as $droppedKeys
    | (if ($prev.permissions | type) == "object" then $prev.permissions else {} end) as $prevPermissions
    | (if ($final.permissions | type) == "object" then $final.permissions else {} end) as $finalPermissions
    | (if ($final.permissions | type) == "object"
       then [$prevPermissions | to_entries[] | .key as $field
        | if (.value | type) == "array"
          then (.value - (if ($finalPermissions[$field] | type) == "array" then $finalPermissions[$field] else [] end))[]
               | "permissions." + $field + "[" + tojson + "]"
          elif ($finalPermissions | has($field)) then empty
          else "permissions." + $field
          end]
       else [] end) as $droppedPermissionEntries
    | (if ($prev.env | type) == "object" then $prev.env else {} end) as $prevEnv
    | (if ($final.env | type) == "object"
       then [$prevEnv | keys[] | select(. as $k | ($prevEnv[$k] != null) and (($final.env | has($k)) | not))]
       else [] end) as $droppedEnvKeys
    | (if ($overlay | has("env")) then
         [$overlay.env | keys[] as $k | select(
            ($prevEnv | has($k) | not) or ($prevEnv[$k] != $overlay.env[$k])
         ) | $k]
       else [] end) as $envChangedKeys
    | ([$overlayNonPerm | keys[] | select(. != "env") | select(. as $k | $prev[$k] != $overlay[$k])] | sort) as $overlayKeysChanged
    | {
        result: $final,
        changed: ($final != $prev),
        carried: ($rule3Contribution | keys | sort),
        droppedKeys: $droppedKeys,
        droppedPermissionEntries: $droppedPermissionEntries,
        droppedEnvKeys: ($droppedEnvKeys | sort),
        envChanged: ($envChangedKeys | sort),
        overlayKeysChanged: $overlayKeysChanged,
        defaultModeSet: (($overlay | has("permissions")) and ($overlay.permissions | has("defaultMode"))),
        defaultModeValue: ($overlay.permissions.defaultMode // null)
      }
    ' 2>/dev/null)"; then
  echo "render-settings.sh: merge of $overlay_file onto $base_file failed" >&2
  exit 1
fi

merged_json="$(jq -c '.result' <<<"$render_output")"

# Disclosure on stderr, emitted only when this render's output differs from the
# prior one. It names:
# - the top-level keys that carried forward
# - the prior top-level keys, permissions entries, and env keys the render
#   dropped; a wholly dropped env or permissions object is named once, as a
#   top-level key, and an empty env object counts as nothing dropped
# - the overlay env keys newly applied or changed
# - the other overlay keys newly applied or changed, by name only
# - an overlay-set permissions.defaultMode
# It lists env keys by name only, never by value, since an env value can be a
# credential. Dropped permissions entries print their rule text, which is what
# lets a user recover a dropped rule.
render_changed="$(jq -r '.changed' <<<"$render_output")"
if [[ "$render_changed" == "true" ]]; then
  segments=()

  carried_list="$(jq -r '.carried | join(", ")' <<<"$render_output")"
  if [[ -n "$carried_list" ]]; then
    segments+=("carried forward top-level keys: $carried_list")
  fi

  dropped_keys_list="$(jq -r '.droppedKeys | join(", ")' <<<"$render_output")"
  if [[ -n "$dropped_keys_list" ]]; then
    segments+=("dropped top-level keys: $dropped_keys_list")
  fi

  dropped_permissions_list="$(jq -r '.droppedPermissionEntries | join(", ")' <<<"$render_output")"
  if [[ -n "$dropped_permissions_list" ]]; then
    segments+=("dropped permissions entries: $dropped_permissions_list")
  fi

  dropped_env_list="$(jq -r '.droppedEnvKeys | map("env." + .) | join(", ")' <<<"$render_output")"
  if [[ -n "$dropped_env_list" ]]; then
    segments+=("dropped env keys: $dropped_env_list")
  fi

  env_changed_list="$(jq -r '.envChanged | map("env." + .) | join(", ")' <<<"$render_output")"
  if [[ -n "$env_changed_list" ]]; then
    segments+=("overlay env keys applied or changed: $env_changed_list")
  fi

  overlay_keys_changed_list="$(jq -r '.overlayKeysChanged | join(", ")' <<<"$render_output")"
  if [[ -n "$overlay_keys_changed_list" ]]; then
    segments+=("overlay keys applied or changed: $overlay_keys_changed_list")
  fi

  if [[ "$(jq -r '.defaultModeSet' <<<"$render_output")" == "true" ]]; then
    default_mode_value="$(jq -r '.defaultModeValue' <<<"$render_output")"
    segments+=("overlay set permissions.defaultMode=$default_mode_value")
  fi

  if [[ ${#segments[@]} -gt 0 ]]; then
    disclosure="$(printf '%s; ' "${segments[@]}")"
    disclosure="${disclosure%; }"
    echo "render-settings.sh: this render changed settings.json -- $disclosure" >&2
  fi
fi

# Skipping an identical render keeps the file's inode and mtime stable and
# leaves no window in which an app write to $target could be replaced.
if [[ "$render_changed" == "false" && "$prior_is_object" == "true" && -f "$target" && ! -L "$target" ]]; then
  exit 0
fi

# mktemp in $target's own directory so the final mv is a same-filesystem
# rename, and so mv-onto-target replaces a symlink instead of writing
# through it (see header comment).
if ! tmp_target="$(mktemp "$target.XXXXXX" 2>&1)"; then
  # mktemp writes only the path to stdout on success and only its own
  # diagnostic to stderr on failure, never both. Merging the streams here is
  # therefore safe: on this failure branch, $tmp_target holds mktemp's own
  # error text (permission denied, ENOSPC, missing parent dir) instead of a
  # temp path.
  echo "render-settings.sh: could not create a temp file next to $target -- $tmp_target -- refusing to render" >&2
  exit 1
fi
# A SIGKILL escalation from the caller's timeout skips this trap and leaves the
# gitignored temp file behind; the leak is one file per killed render.
trap 'rm -f -- "$tmp_target"' EXIT
printf '%s\n' "$merged_json" > "$tmp_target"

# Re-parses the temp file's own written content, not the $merged_json shell
# variable, so a bad render never goes live. There is nothing to restore on
# failure since settings.json is fully regenerable.
if ! jq -e 'type == "object"' -- "$tmp_target" >/dev/null 2>&1; then
  echo "render-settings.sh: rendered output at $tmp_target failed re-validation -- refusing to replace $target" >&2
  exit 1
fi

# A directory at $target would make the mv below move the rendered file inside it instead of replacing it.
if [[ -d "$target" ]]; then
  echo "render-settings.sh: $target is a directory -- refusing to render" >&2
  exit 1
fi

mv -- "$tmp_target" "$target"
