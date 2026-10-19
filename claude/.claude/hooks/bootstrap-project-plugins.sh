#!/usr/bin/env bash
# hook-class: informational
# tier-threat-model: cooperative, untrusted-input, irreversible
# SessionStart hook: when the opened repo's checked-in .claude/settings.json
# enables plugins that are missing from the per-account plugin registry, launch
# reconcile-project-plugins.sh detached to install them, so later sessions in
# that repo load the plugins' skills in the first snapshot instead of seconds
# late. The install lands for the next session, never the current one.
#
# Silent: prints nothing to stdout on any path, and nothing to stderr except
# _config_enabled's warnings (a malformed claude-config.toml line, a missing
# config-keys.psv row, an unreadable config-keys.psv). Exit 0 always -- this hook
# must not block session startup.
#
# Fast guard, no `claude` spawn. The hook launches the reconciler only when
# every check below passes, in this order:
# 1. Machine-level switch: `plugin_bootstrap` is not `false` in
#    claude-config.toml (an unresolvable config dir also exits).
# 2. The payload `.cwd` resolves to a repo root: `.cwd` itself when it holds a
#    .claude/settings.json, else `git rev-parse --show-toplevel`. That root must
#    hold a checked-in .claude/settings.json (a symlink counts as present) and
#    must not be $HOME, whose .claude/settings.json is account-level. Otherwise
#    the hook exits before reading the trust file and writes no audit line.
# 3. Trust layer 1: the vendor's persisted per-repo trust record,
#    `.projects["<repo-root>"].hasTrustDialogAccepted` in the account's
#    .claude.json, is exactly `true`. A missing file, field, or any other
#    shape is untrusted. The vendor's runtime trust check returns true for
#    every non-interactive session, so only the persisted record is read.
# 4. Trust layer 2: CLAUDE_CODE_ENTRYPOINT is exactly `cli` (an interactive
#    session). Unset, empty, or any other value is no-launch. This stops the
#    owner's own headless runs and scripts; it is not a defense against
#    repo-controlled content, since a trusted repo can trigger an install in
#    an interactive session too.
# 5. _lib_missing_plugin_ids reports status 0 and at least one missing id
#    that passes _lib_validate_plugin_id. That validator rejects an id over
#    _LIB_PLUGIN_ID_MAX_LENGTH (256) bytes. Every other status is no-launch:
#    1 (symlinked, non-regular, or oversized checked-in settings file, or more
#    than _LIB_ENABLED_PLUGIN_IDS_MAX (100) enabled ids) is
#    silent; 2 (registry present but unparseable, or version != 2) and 3
#    (unreadable settings.local.json, so a local opt-out cannot be ruled out)
#    are logged.
#
# Audit log: this hook's own refusals append one JSON line to
# <config-dir>/plugin-bootstrap/audit.log, the log the reconciler also writes.
# A layer-1 refusal is logged only for an interactive session, and a layer-2
# refusal only after layer 1 passed, so scripted headless runs in untrusted
# directories write no line. The repo root is stripped of C0, DEL, and C1
# control characters. The observed CLAUDE_CODE_ENTRYPOINT is logged only when it
# is at most 64 bytes of ASCII letters, digits, `.`, `_`, or `-`, else as
# `unrecognized`.
#
# An `m1a_not_trusted` line has five causes: the trust record says untrusted,
# the record is absent, the record is unreadable or torn mid-rewrite, the jq cap
# killed the read, or the vendor keys trust under a string other than this
# hook's exact repo-root string.
#
# Audit lock: the size check, truncation, and append run under
# audit.log.lock, as in the reconciler. The reconciler takes the lock once and
# holds it until it exits. This hook takes it for one append and releases it
# at exit. When bounded retries fail, the hook appends without the lock and
# does not truncate, leaving truncation to the next locked writer.
#
# Silent no-run: no audit line is written when `jq` is absent, when the
# reconciler is not executable, when `mkdir -p` of the audit directory fails
# (the hook's own refusals go unlogged; the reconciler exits), when the
# reconciler's marker `touch` fails, or when the launched reconciler finds
# `claude` or both `timeout` and `gtimeout` missing from PATH or the config dir
# unusable. docs/hooks.md gives the check order.
#
# Uncapped calls: the stdin `cat`, the config-resolution reads, `git
# rev-parse`, every `[ -e/-f/-x/-ef ]` test, `stat`, `grep`, the audit append
# and truncate, and the in-process bash compute (the id-count and id-length
# bounds in _lib.sh limit it). The `.claude.json` read and every other jq call
# are capped only when `timeout` or `gtimeout` is on PATH, and no cap can bound
# a process stuck in uninterruptible I/O. The settings.json registration has no
# `timeout`, so a stalled project mount holds session start until the harness
# default; that residual is accepted.
#
# Accepted residuals: the detached launch has no `setsid`, so a terminal hangup
# may deliver SIGHUP to a running reconciler. The audit lock is keyed on a bare
# PID, so after a killed holder whose PID is recycled the lock does not evict
# and no writer truncates until that PID exits.
#
# Launch contract: the reconciler runs detached with stdin, stdout, and stderr
# redirected to /dev/null and receives the resolved repo root as $1. A
# detached child inherits the launching shell's cwd, not the payload `.cwd`,
# so the root is passed explicitly.
#
# The account's .claude.json is read from $CLAUDE_CONFIG_DIR/.claude.json when
# that variable is set, else from $HOME/.claude.json, the same rule
# statusline-command.sh applies (the default config dir is $HOME/.claude).
# A symlinked .claude.json is followed, since the vendor owns that file.
# Layer 1 matches the exact repo-root string, so a trust record stored under
# any other spelling of the path does not match and the hook stays inert.
# Whether the vendor keys trust for a linked worktree or a repo subdirectory
# under that worktree's or repo root's own path is unverified. If it keys
# elsewhere, layer 1 fails closed and each interactive session writes one
# m1a_not_trusted line.

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

SETTINGS_FILE="$REPO_ROOT/.claude/settings.json"
REGISTRY_FILE="$CONFIG_DIR/plugins/installed_plugins.json"
BOOTSTRAP_DIR="$CONFIG_DIR/plugin-bootstrap"
AUDIT_LOG="$BOOTSTRAP_DIR/audit.log"
AUDIT_LOG_LOCK="$BOOTSTRAP_DIR/audit.log.lock"
RECONCILER="$CONFIG_DIR/scripts/reconcile-project-plugins.sh"

# _audit_sanitize VALUE
# Prints VALUE without C0, DEL, or C1 control characters. Used only for the
# repo root, which the kernel bounds at PATH_MAX (4096 bytes on Linux), so VALUE
# is short enough for jq's argv and its gsub. Prints nothing if jq fails, so an
# unsanitized value never reaches the log.
_audit_sanitize() {
  # shellcheck disable=SC2016 # single-quoted on purpose: $value is a jq binding, not a shell variable.
  _lib_jq -n -j --arg value "$1" '$value | gsub("\\p{Cc}"; "")' 2>/dev/null
}

# _audit_log DECISION OUTCOME
# Appends one JSON line in the reconciler's audit-log shape, with "-" as the
# plugin id since none of this hook's decisions is id-specific. The caller
# exits right after, which releases the lock taken here. The log is emptied
# past _LIB_SIZE_THRESHOLD_BYTES only while the lock is held, as the reconciler
# does. A failed append leaves the decision unlogged and never changes the
# exit status.
_audit_log() {
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

# _repo_is_trusted
# Returns 0 only when the persisted trust record for REPO_ROOT is the boolean
# `true`; a string "true", a missing file or field, and a malformed file all
# return 1.
_repo_is_trusted() {
  local trust_record_file
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    trust_record_file="${CLAUDE_CONFIG_DIR%/}/.claude.json"
  else
    trust_record_file="${HOME:-}/.claude.json"
  fi
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
