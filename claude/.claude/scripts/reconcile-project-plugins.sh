#!/usr/bin/env bash
# reconcile-project-plugins.sh — install a repo's still-missing enabledPlugins
# into the per-account plugin registry, in the background.
#
# Invoked as `reconcile-project-plugins.sh <repo-root>` by
# bootstrap-project-plugins.sh's SessionStart hook, detached with stdin, stdout,
# and stderr redirected to /dev/null.
# The script re-derives the missing-id set from the repo's settings and the
# registry via _lib_missing_plugin_ids_for_content instead of accepting an id
# list over argv, so a hand-run or stale-hook invocation still re-derives and re-validates every id itself.
# Such an invocation performs no trust check, which is why the hook is the only supported caller.
# docs/hooks.md § Utility hooks and docs/scripts.md describe the full contract.
#
# The audit-log append and the lock-file open (`exec 9>>`) have no timeout.
# A filesystem that hangs I/O can block either one indefinitely, because a blocking open or write has no child process for timeout(1) to kill.
set -euo pipefail

if ! resolved="$(readlink -f -- "$0" 2>/dev/null)"; then
  resolved="$0"
fi
# shellcheck source=../hooks/_lib.sh
. "$(dirname -- "$resolved")/../hooks/_lib.sh"

TARGET_REPO_ROOT="${1:?reconcile-project-plugins.sh requires the repo root as \$1}"

# The runtime directory and files record the absolute path of every repo opened, so the umask makes them owner-only (0700 and 0600).
# _reconciler_claude_call restores the original umask, so the files `claude` writes keep their usual modes.
_RECONCILER_ORIGINAL_UMASK=$(umask)
umask 077

CONFIG_DIR=$(_lib_config_dir) || exit 0

SETTINGS_FILE="$TARGET_REPO_ROOT/.claude/settings.json"
REGISTRY_FILE="$CONFIG_DIR/plugins/installed_plugins.json"
BOOTSTRAP_DIR="$CONFIG_DIR/plugin-bootstrap"
mkdir -p -- "$BOOTSTRAP_DIR" 2>/dev/null || exit 0

AUDIT_LOG="$BOOTSTRAP_DIR/audit.log"
AUDIT_LOG_LOCK="$BOOTSTRAP_DIR/audit.log.lock"
# _audit_log sets _AUDIT_LOCK_ATTEMPTED the first time it tries AUDIT_LOG_LOCK, so many calls in one run take the lock at most once, as _lib_acquire_append_lock requires.
# _AUDIT_LOCK_HELD is set only when that attempt succeeded.
_AUDIT_LOCK_ATTEMPTED=""
_AUDIT_LOCK_HELD=""
RECONCILER_LOCK="$BOOTSTRAP_DIR/reconciler.lock"
MARKER_FILE="$BOOTSTRAP_DIR/.reconciler-start"

# Wall-clock ceiling for one run: 600s (10 minutes), 20x the per-call cap below.
# The ceiling is checked only before each id, so an install that starts just under it can still run past it, for its capped git checks and its 30s cap.
# This is repo precedent, not a vendor figure, because no vendor documentation gives this call's duration.
_RECONCILER_WALL_CLOCK_CEILING_SECONDS=600
# 30s per capped `claude` call: double this repo's 15s cap for the closest comparable network-fetching call (`git fetch` in cleanup-merged-branches.sh).
# An install can read marketplace metadata and fetch plugin content, hence the doubling.
_RECONCILER_CLAUDE_CALL_CAP_SECONDS=30

# Plugin names that carry credentials and so are never auto-installed, whichever marketplace carries them.
_RECONCILER_CREDENTIAL_DENYLIST=("issue-triage")

# _reconciler_is_denylisted PLUGIN_ID
# Returns 0 when the name half of PLUGIN_ID matches a denylist entry, ignoring ASCII case, so the list binds whichever marketplace carries the name.
# The body is a subshell that sets LC_ALL=C, so the case fold touches only A-Z.
_reconciler_is_denylisted() (
  LC_ALL=C
  folded_name="${1%%@*}"
  folded_name="${folded_name,,}"
  for denied in "${_RECONCILER_CREDENTIAL_DENYLIST[@]}"; do
    [ "$folded_name" = "${denied,,}" ] && exit 0
  done
  exit 1
)

# _audit_sanitize VALUE
# Prints VALUE without C0, DEL, or C1 control characters. Used only for the
# repo root, which the kernel bounds at PATH_MAX (4096 bytes on Linux), so VALUE
# is short enough for jq's argv and its gsub. Prints nothing and returns
# non-zero if jq fails, so an unsanitized value never reaches the log.
_audit_sanitize() {
  # shellcheck disable=SC2016 # single-quoted on purpose: $value is a jq binding, not a shell variable.
  _lib_jq -n -j --arg value "$1" '$value | gsub("\\p{Cc}"; "")' 2>/dev/null
}

# The sanitized repo root is the same for every audit line in a run, so it is computed once. A failed sanitize leaves it empty; the guard keeps that from tripping set -e.
_AUDIT_REPO_FIELD=$(_audit_sanitize "$TARGET_REPO_ROOT") || _AUDIT_REPO_FIELD=""

# _audit_id_field PLUGIN_ID_OR_DASH
# Prints the audit-log text for a plugin id.
# "-" and an id that passes _lib_validate_plugin_id are printed verbatim.
# That validator accepts at most _LIB_PLUGIN_ID_MAX_LENGTH ASCII bytes from a fixed alphabet, so a printed id has no control characters.
# Any other value is repo-controlled text and prints as `invalid-id(len=N)`.
# N is the value's length in the shell's substring units (bytes in the C locale, characters otherwise).
_audit_id_field() {
  local plugin_id="$1"
  if [ "$plugin_id" = "-" ] || _lib_validate_plugin_id "$plugin_id"; then
    printf '%s' "$plugin_id"
  else
    printf 'invalid-id(len=%d)' "${#plugin_id}"
  fi
}

# _audit_log PLUGIN_ID_OR_DASH DECISION OUTCOME
# Appends one JSON-lines entry per decision, matching track-permission-prompts.sh's JSON-lines log convention.
# PLUGIN_ID_OR_DASH is "-" for a decision that is not specific to one id, such as a marketplace-listing failure.
# The plugin id is repo-controlled and can reach here unvalidated, so it goes through _audit_id_field.
# The repo root is the once-computed _AUDIT_REPO_FIELD.
#
# AUDIT_LOG_LOCK is taken through _lib_acquire_append_lock, at most once per process, because that primitive sets the process's single EXIT trap.
# Every exit path returns 0, so a call site can stay a bare statement under set -e.
# A failing sanitize degrades to an empty repo root, and a failing append leaves that one decision unlogged.
_audit_log() {
  local plugin_id="$1" decision="$2" outcome="$3"
  local id_field line
  id_field=$(_audit_id_field "$plugin_id")
  # shellcheck disable=SC2016 # single-quoted on purpose: $timestamp/$pid/$repo_root/$plugin_id/$decision/$outcome are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them.
  line=$(_lib_jq -n -c \
    --arg timestamp "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --arg pid "$$" \
    --arg repo_root "$_AUDIT_REPO_FIELD" \
    --arg plugin_id "$id_field" \
    --arg decision "$decision" \
    --arg outcome "$outcome" \
    '{timestamp: $timestamp, pid: $pid, repo_root: $repo_root, plugin_id: $plugin_id, decision: $decision, outcome: $outcome}' 2>/dev/null) || return 0
  [ -n "$line" ] || return 0

  if [ -z "$_AUDIT_LOCK_ATTEMPTED" ]; then
    _AUDIT_LOCK_ATTEMPTED=1
    if _lib_acquire_append_lock "$AUDIT_LOG_LOCK"; then
      _AUDIT_LOCK_HELD=1
    fi
  fi

  # Only the lock holder truncates, because an unlocked truncate could drop a concurrent holder's lines.
  # A writer that lost the lock leaves an oversized log to the next locked writer.
  if [ -n "$_AUDIT_LOCK_HELD" ] && [ -f "$AUDIT_LOG" ] && _lib_file_exceeds_size_cap "$AUDIT_LOG"; then
    : 2>/dev/null > "$AUDIT_LOG" || true
  fi

  printf '%s\n' "$line" 2>/dev/null >> "$AUDIT_LOG" || true
}

# Refuse to run when a binary every later step depends on is missing, rather than failing the first capped call with "command not found".
# The audit line is the only trace, so the bootstrap directory must already be writable.
if ! command -v claude >/dev/null 2>&1; then
  _audit_log "-" "preflight_failed" "missing_claude"
  exit 0
fi
if ! command -v timeout >/dev/null 2>&1 && ! command -v gtimeout >/dev/null 2>&1; then
  _audit_log "-" "preflight_failed" "missing_timeout_and_gtimeout"
  exit 0
fi
# Failure stamps are keyed by a sha256 digest.
if ! command -v sha256sum >/dev/null 2>&1; then
  _audit_log "-" "preflight_failed" "missing_sha256sum"
  exit 0
fi

# _RECONCILER_LOCK_HELD_STATUS is the exit status the flock probe uses for "another process holds the lock", distinct from python3's 1 for any other failure and the shell's 127 for a missing python3.
_RECONCILER_LOCK_HELD_STATUS=3

# _reconciler_acquire_lock
# One lock per config dir, because the contended resource is the per-account registry, not the repo.
# Takes a non-blocking exclusive flock on RECONCILER_LOCK through fd 9, which stays open for the life of the process, so the kernel releases the lock at exit however the process ends.
# The lock file is never unlinked and its content is never read, because the flock is the lock.
# python3 makes the flock(2) call, because bash has no flock builtin. It shares fd 9 with the shell, so the lock outlives the python3 process.
# This function sets no EXIT trap, because _audit_log's _lib_acquire_append_lock owns the process's single EXIT trap slot (shell-script-conventions.md).
# Returns 0 once held, 1 when another process holds it, and 2 when the lock cannot be attempted: python3 is missing, or the lock file cannot be opened or locked.
_reconciler_acquire_lock() {
  { exec 9>> "$RECONCILER_LOCK"; } 2>/dev/null || return 2
  local flock_status=0
  # The probe runs from BOOTSTRAP_DIR, because a version-manager shim for python3 reads .python-version from the cwd, which is otherwise the repo.
  # -I keeps PYTHONPATH, the user site directory, and the cwd entry out of Python startup, so a PYTHONPATH entry or a planted sitecustomize.py cannot replace fcntl.
  # The subshell inherits fd 9.
  # A failed cd (status 1) and a cap kill (124 or 137) both fall into the unavailable arm below.
  ( cd -- "$BOOTSTRAP_DIR" && _lib_capped python3 -I -c 'import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(int(sys.argv[1]))' "$_RECONCILER_LOCK_HELD_STATUS" ) 2>/dev/null || flock_status=$?
  case "$flock_status" in
    0) return 0 ;;
    "$_RECONCILER_LOCK_HELD_STATUS") return 1 ;;
    *) return 2 ;;
  esac
}

# _reconciler_claude_call CLAUDE_ARGS...
# Runs `claude CLAUDE_ARGS...` under the per-call cap and the umask this process started with, so the files the CLI writes keep their usual modes.
# The body is a subshell, so the restored umask does not leak into the reconciler's own files.
# This helper and _reconciler_git close the lock descriptor, so a child that outlives its call cannot keep the lock.
# The subshell closes it first, before `claude` starts.
# GIT_TERMINAL_PROMPT=0 makes a git child fail instead of prompting on the terminal.
# Prompts that ssh raises are not covered.
_reconciler_claude_call() (
  umask "$_RECONCILER_ORIGINAL_UMASK" || exit 1
  exec 9>&-
  GIT_TERMINAL_PROMPT=0 _lib_capped_for "$_RECONCILER_CLAUDE_CALL_CAP_SECONDS" claude "$@"
)

# _reconciler_git GIT_ARGS...
# Runs `git -C TARGET_REPO_ROOT GIT_ARGS...` under the default cap with the lock descriptor closed, so a daemon git starts cannot inherit the lock.
# The redirection applies to the call's children only, so the shell keeps the lock.
_reconciler_git() {
  _lib_capped git -C "$TARGET_REPO_ROOT" "$@" 9>&-
}

# _reconciler_marker_epoch
# Prints MARKER_FILE's mtime in epoch seconds.
# The marker is touched once per run, at the start of `_reconciler_process_repo`, so the caller's now-minus-mtime is time since that touch, never a per-id time.
# It re-stats on every call, so a marker removed mid-run yields an empty result and the caller fails closed.
_reconciler_marker_epoch() {
  stat -c%Y -- "$MARKER_FILE" 2>/dev/null \
    || stat -f%m -- "$MARKER_FILE" 2>/dev/null \
    || printf ''
}

# _reconciler_stamp_file PLUGIN_ID
# Prints the failure-stamp path for PLUGIN_ID under the current settings content, or nothing when the hash fails.
# The name is a hash of the id plus the settings content hash, never repo-controlled text.
# The name therefore changes, and the old stamp goes stale, when the repo's settings change.
_reconciler_stamp_file() {
  local stamp_key
  stamp_key=$(_lib_hash_diff_text "${1}$(printf '\x1e')${_settings_hash}") || return 0
  printf '%s' "$BOOTSTRAP_DIR/failure-stamp.$stamp_key"
}

# _reconciler_process_repo
# The whole critical section, run only once the lock is held.
_reconciler_process_repo() {
  # Created only after the lock is won, so a losing contender never resets the marker's mtime.
  touch -- "$MARKER_FILE" 2>/dev/null || return 0
  [ -n "$(_reconciler_marker_epoch)" ] || return 0

  # Free local filters run first, so a repo whose ids are all unfixable never spawns the `claude` CLI.
  local id stamp_file
  local -a candidate_ids=()
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    if ! _lib_validate_plugin_id "$id"; then
      _audit_log "$id" "m3_invalid_id_skip" "skipped"
      continue
    fi
    if _reconciler_is_denylisted "$id"; then
      _audit_log "$id" "m4_credential_denylist_skip" "skipped"
      continue
    fi
    stamp_file=$(_reconciler_stamp_file "$id")
    if [ -n "$stamp_file" ] && [ -f "$stamp_file" ]; then
      _audit_log "$id" "m9_stamp_suppressed" "skipped"
      continue
    fi
    candidate_ids+=("$id")
  done <<< "$_missing_ids"
  [ "${#candidate_ids[@]}" -gt 0 ] || return 0

  # One marketplace listing per run, fail-closed on a failure, timeout, unparseable output, or empty result.
  # That skips every candidate for the run, which is distinct from a per-id "marketplace not registered" skip.
  local marketplace_json marketplace_names
  marketplace_json=$(_reconciler_claude_call plugin marketplace list --json 2>/dev/null) || marketplace_json=""
  marketplace_names=""
  if [ -n "$marketplace_json" ]; then
    marketplace_names=$(printf '%s' "$marketplace_json" | _lib_jq -r '.[].name' 2>/dev/null) || marketplace_names=""
  fi
  if [ -z "$marketplace_names" ]; then
    _audit_log "-" "m4_marketplace_list_failed" "skipped_all"
    return 0
  fi

  for id in "${candidate_ids[@]}"; do
    local marker_epoch now_epoch
    marker_epoch=$(_reconciler_marker_epoch)
    now_epoch=$(date +%s) || now_epoch=""
    # Once the ceiling is hit it stays hit, so log it once and end the run rather than once per remaining id.
    if [ -z "$marker_epoch" ] || [ -z "$now_epoch" ] || [ $((now_epoch - marker_epoch)) -ge "$_RECONCILER_WALL_CLOCK_CEILING_SECONDS" ]; then
      _audit_log "$id" "wall_clock_ceiling_reached" "skipped"
      break
    fi

    local marketplace_name
    marketplace_name="${id##*@}"
    if ! printf '%s\n' "$marketplace_names" | grep -qFx -- "$marketplace_name"; then
      _audit_log "$id" "m4_marketplace_not_registered" "skipped"
      continue
    fi

    # `claude plugin marketplace list --json` reports the source type as a plain top-level `.source` string on each entry (confirmed against CLI 2.1.283).
    # A repo's settings.json `extraKnownMarketplaces` uses a different, nested shape (`.value.source.source`) for the same concept.
    local source_type
    # shellcheck disable=SC2016 # single-quoted on purpose: $name is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
    source_type=$(printf '%s' "$marketplace_json" | _lib_jq -r --arg name "$marketplace_name" '[.[] | select(.name == $name) | .source][0] // empty' 2>/dev/null) || source_type=""
    # Allowlist, not denylist: an unknown or future source type, or a missing field, is skipped.
    case "$source_type" in
      github|directory|git) ;;
      *)
        _audit_log "$id" "m4_source_type_not_allowed" "skipped"
        continue
        ;;
    esac

    # `-s local` writes the repo's .claude/settings.local.json, so the install runs only when that file is git-ignored and untracked.
    # `ls-files` runs first only to log the specific "already tracked" decision; `check-ignore` alone also refuses a tracked path.
    if _reconciler_git ls-files --error-unmatch -- .claude/settings.local.json >/dev/null 2>&1; then
      _audit_log "$id" "m6_already_tracked" "skipped"
      continue
    fi
    if ! _reconciler_git check-ignore -q -- .claude/settings.local.json 2>/dev/null; then
      _audit_log "$id" "m6_not_ignored" "skipped"
      continue
    fi

    # `claude plugin install` has no cwd-override flag, so a local-scope install must run with the target repo as cwd.
    # The `cd` is in a subshell, so a later iteration's `git -C` calls are unaffected.
    # `--` precedes the repo-controlled id so it cannot be read as a flag, and no -y/--yes/--accept-command is ever passed.
    # stdin is /dev/null so a CLI that reads stdin cannot consume the remaining ids.
    local install_status=0
    (cd -- "$TARGET_REPO_ROOT" && _reconciler_claude_call plugin install -s local -- "$id") </dev/null >/dev/null 2>&1 || install_status=$?
    if [ "$install_status" -eq 0 ]; then
      _audit_log "$id" "install_succeeded" "success"
    elif _lib_status_consistent_with_cap_kill "$install_status"; then
      # A cap kill is likely transient, so it writes no failure stamp and the next session retries.
      _audit_log "$id" "install_timed_out" "failure exit=$install_status"
    else
      stamp_file=$(_reconciler_stamp_file "$id")
      [ -z "$stamp_file" ] || touch -- "$stamp_file" 2>/dev/null || true
      _audit_log "$id" "install_failed" "failure exit=$install_status"
    fi
  done
}

# The lock is taken before the settings and registry reads, so the missing-id set is derived while no other reconciler can change the registry.
_reconciler_lock_status=0
_reconciler_acquire_lock || _reconciler_lock_status=$?
case "$_reconciler_lock_status" in
  0) ;;
  2)
    _audit_log "-" "lock_unavailable" "skipped"
    exit 0
    ;;
  *)
    _audit_log "-" "lock_held" "skipped"
    exit 0
    ;;
esac

# The settings bytes are read once. The same bytes feed the missing-id set and the failure-stamp hash.
# A settings file the guard refuses is treated as absent, with no log line.
_settings_content=$(_lib_read_guarded_settings "$SETTINGS_FILE") || exit 0
_settings_hash=$(_lib_hash_diff_text "$_settings_content") || _settings_hash=""

_missing_ids_status=0
_missing_ids=$(_lib_missing_plugin_ids_for_content "$_settings_content" "$SETTINGS_FILE" "$REGISTRY_FILE") || _missing_ids_status=$?
case "$_missing_ids_status" in
  0) ;;
  1) exit 0 ;;  # too many enabled ids or several top-level JSON values -- same as absent, no log
  2)
    _audit_log "-" "registry_unparseable_or_version_mismatch" "skipped"
    exit 0
    ;;
  3)
    # An unreadable settings.local.json may hold an opt-out we cannot see, so nothing installs.
    _audit_log "-" "m10_local_optout_unreadable" "skipped"
    exit 0
    ;;
  *) exit 0 ;;
esac
[ -n "$_missing_ids" ] || exit 0

_reconciler_process_repo
