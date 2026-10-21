#!/usr/bin/env bash
# hook-class: informational
# tier-threat-model: cooperative, untrusted-input, irreversible
# SessionStart hook: when the opened repo's checked-in .claude/settings.json
# enables plugins missing from the per-account plugin registry, launches
# reconcile-project-plugins.sh detached to install them for the next session.
# Prints nothing to stdout and always exits 0, so it never blocks session startup.
# The reconciler receives the resolved repo root as $1, because a detached child
# inherits the launching shell's cwd, not the payload `.cwd`.
# The check order, trust layers, audit-log shape, and accepted residuals live in
# docs/hooks.md § Utility hooks.

set -uo pipefail

INPUT=$(cat 2>/dev/null)

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

SOURCE=$(printf '%s' "$INPUT" | _lib_jq -r '.source // empty' 2>/dev/null)
[[ "$SOURCE" == "startup" ]] || exit 0

_config_enabled plugin_bootstrap || exit 0

CONFIG_DIR=$(_lib_config_dir) || exit 0

PAYLOAD_CWD=$(printf '%s' "$INPUT" | _lib_jq -r '.cwd // empty' 2>/dev/null)
case "$PAYLOAD_CWD" in
  /*) ;;
  *) exit 0 ;;
esac
PAYLOAD_CWD="${PAYLOAD_CWD%/}"
[ -n "$PAYLOAD_CWD" ] || exit 0

# _checked_in_settings_present DIR
# Returns 0 when DIR/.claude/settings.json exists. A symlink, even a dangling
# one, counts as present: _lib_missing_plugin_ids refuses it later (status 1).
_checked_in_settings_present() {
  [ -e "$1/.claude/settings.json" ] || [ -L "$1/.claude/settings.json" ]
}

if _checked_in_settings_present "$PAYLOAD_CWD"; then
  REPO_ROOT="$PAYLOAD_CWD"
else
  REPO_ROOT=$(git -C "$PAYLOAD_CWD" rev-parse --show-toplevel 2>/dev/null) || exit 0
fi
[ -n "$REPO_ROOT" ] || exit 0
# $HOME/.claude/settings.json is the account's own file, not a checked-in one.
if [ "$REPO_ROOT" -ef "${HOME:-}" ]; then
  exit 0
fi
_checked_in_settings_present "$REPO_ROOT" || exit 0

# _trust_record_path
# Prints the file the persisted trust record is read from: the `.claude.json`
# inside CLAUDE_CONFIG_DIR when it is set, else the one in HOME.
_trust_record_path() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    printf '%s' "${CLAUDE_CONFIG_DIR%/}/.claude.json"
  else
    printf '%s' "${HOME:-}/.claude.json"
  fi
}

RESOLVED_REPO_ROOT=$(_lib_realpath_m "$REPO_ROOT") || exit 0

# _path_is_repo_controlled PATH
# Returns 0 when PATH resolves to REPO_ROOT or below it, or does not resolve.
# A path the repo can write is repo-controlled, so nothing read from it can vouch for the repo.
_path_is_repo_controlled() {
  local resolved_path
  resolved_path=$(_lib_realpath_m "$1") || return 0
  case "${resolved_path%/}/" in
    "${RESOLVED_REPO_ROOT%/}/"*) return 0 ;;
  esac
  return 1
}

# The config dir and the trust record are both read as account state, so a repo-controlled path for either refuses the launch.
! _path_is_repo_controlled "$CONFIG_DIR" || exit 0
! _path_is_repo_controlled "$(_trust_record_path)" || exit 0

SETTINGS_FILE="$REPO_ROOT/.claude/settings.json"
REGISTRY_FILE="$CONFIG_DIR/plugins/installed_plugins.json"
BOOTSTRAP_DIR="$CONFIG_DIR/plugin-bootstrap"
AUDIT_LOG="$BOOTSTRAP_DIR/audit.log"
AUDIT_LOG_LOCK="$BOOTSTRAP_DIR/audit.log.lock"
# Derived from this hook's own location, like _lib.sh, never from the config dir.
RECONCILER="${0%/*}/../scripts/reconcile-project-plugins.sh"

# _audit_sanitize VALUE
# Prints VALUE without C0, DEL, or C1 control characters. Used only for the
# repo root, which the kernel bounds at PATH_MAX (4096 bytes on Linux), so VALUE
# is short enough for jq's argv and its gsub. Prints nothing if jq fails, so an
# unsanitized value never reaches the log.
_audit_sanitize() {
  # shellcheck disable=SC2016 # single-quoted on purpose: $value is a jq binding, not a shell variable.
  _lib_jq -n -j --arg value "$1" '$value | gsub("\\p{Cc}"; "")' 2>/dev/null
}

# _audit_append_line DECISION OUTCOME
# Appends one JSON line in the reconciler's audit-log shape, with "-" as the
# plugin id since none of this hook's decisions is id-specific. The caller
# exits right after, which releases the lock taken here.
# The log is emptied past _LIB_SIZE_THRESHOLD_BYTES only while the lock is held, as the reconciler does.
# A failed append leaves the decision unlogged and never changes the exit status.
_audit_append_line() {
  local decision="$1" outcome="$2" line
  # shellcheck disable=SC2016 # single-quoted on purpose: $timestamp/$pid/$repo_root/$decision/$outcome are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them.
  line=$(_lib_jq -n -c \
    --arg timestamp "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --arg pid "$$" \
    --arg repo_root "$(_audit_sanitize "$REPO_ROOT")" \
    --arg plugin_id "-" \
    --arg decision "$decision" \
    --arg outcome "$outcome" \
    '{timestamp: $timestamp, pid: $pid, repo_root: $repo_root, plugin_id: $plugin_id, decision: $decision, outcome: $outcome}' 2>/dev/null) || return 0
  [ -n "$line" ] || return 0
  mkdir -p -- "$BOOTSTRAP_DIR" 2>/dev/null || return 0
  if _lib_acquire_append_lock "$AUDIT_LOG_LOCK" 2>/dev/null; then
    if [ -f "$AUDIT_LOG" ] && _lib_file_exceeds_size_cap "$AUDIT_LOG"; then
      : 2>/dev/null > "$AUDIT_LOG" || true
    fi
  fi
  printf '%s\n' "$line" 2>/dev/null >> "$AUDIT_LOG" || true
}

# _audit_log DECISION OUTCOME
# Runs _audit_append_line under umask 077, so the bootstrap directory and its files are owner-only, and restores the caller's umask.
_audit_log() {
  local original_umask
  original_umask=$(umask)
  umask 077
  _audit_append_line "$@"
  umask "$original_umask"
}

# _repo_is_trusted
# Returns 0 only when the persisted trust record for REPO_ROOT is the boolean
# `true`; a string "true", a missing file or field, and a malformed file all
# return 1.
_repo_is_trusted() {
  local trust_record_file
  trust_record_file=$(_trust_record_path)
  [ -f "$trust_record_file" ] || return 1
  local trusted
  # shellcheck disable=SC2016 # single-quoted on purpose: $repo is a jq --arg binding, not a shell variable.
  trusted=$(_lib_jq -r --arg repo "$REPO_ROOT" \
    '(.projects[$repo].hasTrustDialogAccepted) == true' -- "$trust_record_file" 2>/dev/null) || return 1
  [ "$trusted" = "true" ]
}

# Longest CLAUDE_CODE_ENTRYPOINT value, in bytes, logged as itself. Known values are short names such as `cli` and `sdk-cli`.
ENTRYPOINT_LOGGED_MAX_LENGTH=64

# _entrypoint_observed
# Prints the audit-log description of CLAUDE_CODE_ENTRYPOINT: `entrypoint=unset`
# when it is unset or empty, `entrypoint="<value>"` when it is at most
# ENTRYPOINT_LOGGED_MAX_LENGTH ASCII letters, digits, `.`, `_`, or `-`, and
# `entrypoint=unrecognized` otherwise. The environment value is never logged as
# free text.
# The body is a subshell that sets LC_ALL=C, so the length counts bytes and the
# bracket ranges match only ASCII (see _lib_passes_path_char_allowlist).
_entrypoint_observed() (
  LC_ALL=C
  observed="${CLAUDE_CODE_ENTRYPOINT-}"
  if [ -z "$observed" ]; then
    printf 'entrypoint=unset'
  elif [ "${#observed}" -gt "$ENTRYPOINT_LOGGED_MAX_LENGTH" ]; then
    printf 'entrypoint=unrecognized'
  else
    case "$observed" in
      *[!A-Za-z0-9._-]*) printf 'entrypoint=unrecognized' ;;
      *) printf 'entrypoint="%s"' "$observed" ;;
    esac
  fi
)

if ! _repo_is_trusted; then
  [ "${CLAUDE_CODE_ENTRYPOINT-}" = "cli" ] && _audit_log "m1a_not_trusted" "skipped"
  exit 0
fi

if [ "${CLAUDE_CODE_ENTRYPOINT-}" != "cli" ]; then
  _audit_log "m1a_non_interactive" "skipped $(_entrypoint_observed)"
  exit 0
fi

MISSING_IDS_STATUS=0
MISSING_IDS=$(_lib_missing_plugin_ids "$SETTINGS_FILE" "$REGISTRY_FILE" 2>/dev/null) || MISSING_IDS_STATUS=$?
case "$MISSING_IDS_STATUS" in
  0) ;;
  2)
    _audit_log "registry_unparseable_or_version_mismatch" "skipped"
    exit 0
    ;;
  3)
    _audit_log "m10_local_optout_unreadable" "skipped"
    exit 0
    ;;
  *) exit 0 ;;
esac

HAS_VALID_MISSING_ID=0
while IFS= read -r missing_id; do
  if [ -n "$missing_id" ] && _lib_validate_plugin_id "$missing_id"; then
    HAS_VALID_MISSING_ID=1
    break
  fi
done <<< "$MISSING_IDS"
[ "$HAS_VALID_MISSING_ID" = "1" ] || exit 0

# A missing id that can never become an install (credential-denylisted,
# unsupported source type) re-launches the reconciler every interactive session.
# That cost is an accepted edge at this repo's user scale.
[ -x "$RECONCILER" ] || exit 0
"$RECONCILER" "$REPO_ROOT" </dev/null >/dev/null 2>&1 &
exit 0
