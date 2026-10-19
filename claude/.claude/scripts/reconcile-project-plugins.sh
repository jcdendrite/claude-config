#!/usr/bin/env bash
# reconcile-project-plugins.sh — install a repo's still-missing enabledPlugins
# into the per-account plugin registry, in the background.
#
# Invoked as `reconcile-project-plugins.sh <resolved-repo-root>` by
# bootstrap-project-plugins.sh's SessionStart hook (detached, with stdin/
# stdout/stderr redirected to /dev/null, per that hook's own invocation
# contract) once the hook's fast guard has already decided a launch is
# warranted. This script never trusts that decision blindly: it re-reads
# <resolved-repo-root>/.claude/settings.json and the registry itself via the
# same _lib_missing_plugin_ids helper the hook calls, rather than accepting a
# parsed id list over argv, so a hand-run or stale-hook invocation is still
# safe and a future change to the missing-id logic can't silently diverge
# between the two callers.
#
# Mechanisms implemented here (see .claude/plans/project-plugin-registry-
# bootstrap.md for the full design): M4 (marketplace-registered-and-source-
# type precondition, restricted to github/directory/git sources, plus a
# credential-bearing-plugin denylist), M5 (detached/capped/fd-isolated
# invocation contract and a total per-run wall-clock ceiling), M6 (the
# `-s local` scope ladder, gated on a runtime check-ignore-plus-not-already-
# tracked guard), M7 (never passes -y/--yes/--accept-command), M8 (a
# PID-plus-process-start-time lock, keyed on the config dir, that survives a
# recycled-PID wraparound), M9 (a hash-keyed failure stamp plus a shared,
# append-locked audit log), M10 (a `false` in the repo's sibling
# settings.local.json drops that id; an unreadable local file installs
# nothing).
#
# Known accepted gap (M9): a transient install failure suppresses retry for
# that plugin id until the repo's settings file changes (which changes the
# failure stamp's hash and so its filename) or the stamp file under
# <config-dir>/plugin-bootstrap/ is deleted by hand. There is no attempt
# counter or TTL -- a TTL is a guess about when the world changed, while the
# settings-hash key is a fact about when the input changed.
#
# Known accepted gap (audit log): the audit-log append itself has no
# timeout, unlike every `claude` subprocess call in this script -- a
# filesystem that hangs I/O (e.g. a stale network mount under
# <config-dir>) can block the write indefinitely. Unlike a `claude` call,
# a blocking file write has no child process for _lib_capped_for's
# timeout(1)/gtimeout(1) to kill, so the existing per-call and wall-clock
# caps bound only the two `claude` subprocess calls, not this one.
set -euo pipefail

if ! resolved="$(readlink -f -- "$0" 2>/dev/null)"; then
  resolved="$0"
fi
# shellcheck source=../hooks/_lib.sh
. "$(dirname -- "$resolved")/../hooks/_lib.sh"

TARGET_REPO_ROOT="${1:?reconcile-project-plugins.sh requires the resolved repo root as \$1}"

# M5: refuse to launch anything at all when the two binaries every
# downstream step depends on are missing, rather than letting the first
# capped call fail with a generic "command not found".
command -v claude >/dev/null 2>&1 || exit 0
if ! command -v timeout >/dev/null 2>&1 && ! command -v gtimeout >/dev/null 2>&1; then
  exit 0
fi

CONFIG_DIR=$(_lib_config_dir) || exit 0

SETTINGS_FILE="$TARGET_REPO_ROOT/.claude/settings.json"
REGISTRY_FILE="$CONFIG_DIR/plugins/installed_plugins.json"
BOOTSTRAP_DIR="$CONFIG_DIR/plugin-bootstrap"
mkdir -p -- "$BOOTSTRAP_DIR" 2>/dev/null || exit 0

AUDIT_LOG="$BOOTSTRAP_DIR/audit.log"
AUDIT_LOG_LOCK="$BOOTSTRAP_DIR/audit.log.lock"
# Set by _audit_log the first time it acquires AUDIT_LOG_LOCK, so a run that
# calls _audit_log many times (once per id) still calls
# _lib_acquire_append_lock at most once per process, per that primitive's
# own documented contract.
_AUDIT_LOCK_ACQUIRED=""
RECONCILER_LOCK="$BOOTSTRAP_DIR/reconciler.lock"
MARKER_FILE="$BOOTSTRAP_DIR/.reconciler-start"

# M5's total wall-clock ceiling: 600s (10 minutes), 20x the per-call 30s cap
# below -- see the plan's M5 mechanism text for the full grounding (repo
# precedent, not a vendor SLA, since none documents this call's duration).
_RECONCILER_WALL_CLOCK_CEILING_SECONDS=600
# 30s per capped `claude` call: double this repo's existing 15s cap for the
# closest comparable network-fetching subprocess call (`git fetch` in
# cleanup-merged-branches.sh), doubled because an install can involve both a
# marketplace-metadata read and a plugin-content fetch.
_RECONCILER_CLAUDE_CALL_CAP_SECONDS=30

# M4's credential-bearing-plugin denylist. Nothing ties this to plugin
# metadata -- a future credential-bearing plugin added anywhere is
# auto-installable unless it's added here by hand too.
_RECONCILER_CREDENTIAL_DENYLIST=("issue-triage@claude-config")

_reconciler_is_denylisted() {
  local id="$1" denied
  for denied in "${_RECONCILER_CREDENTIAL_DENYLIST[@]}"; do
    [ "$id" = "$denied" ] && return 0
  done
  return 1
}

# _audit_log PLUGIN_ID_OR_DASH DECISION OUTCOME
# One JSON-lines entry per decision, matching track-permission-prompts.sh's
# own JSON-lines log convention. PLUGIN_ID_OR_DASH is "-" for a decision
# that isn't specific to one id (M4's marketplace-listing failure). The repo
# root and plugin id are both repo-controlled, so both are stripped of
# control characters and length-capped at 4096 bytes (POSIX PATH_MAX) before
# formatting -- bounding untrusted-input field length on its own merits,
# independent of the whole-line dedup match below, which has no length
# assumption of its own. The plugin id is stripped even though every call
# site but one (wall_clock_ceiling_reached, reached before
# _lib_validate_plugin_id ever runs) already passes a validated id, so this
# stays safe regardless of call-site ordering.
#
# Acquires AUDIT_LOG_LOCK directly via _lib_acquire_append_lock, at most
# once per process (guarded by _AUDIT_LOCK_ACQUIRED), rather than through
# _lib_append_line_locked -- that primitive's own lock helper is documented
# to be called at most once per process, and this function is called many
# times in one reconciler run (once per id). Every exit path returns 0: a
# failing append degrades to "this one decision goes unlogged", never a
# set -e abort of the run, so every call site above can stay a bare,
# unguarded statement.
_audit_log() {
  local plugin_id="$1" decision="$2" outcome="$3"
  local repo_field id_field line
  repo_field=$(printf '%s' "$TARGET_REPO_ROOT" | tr -d '[:cntrl:]')
  repo_field="${repo_field:0:4096}"
  id_field=$(printf '%s' "$plugin_id" | tr -d '[:cntrl:]')
  id_field="${id_field:0:4096}"
  # shellcheck disable=SC2016 # single-quoted on purpose: $timestamp/$pid/$repo_root/$plugin_id/$decision/$outcome are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them.
  line=$(_lib_jq -n -c \
    --arg timestamp "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --arg pid "$$" \
    --arg repo_root "$repo_field" \
    --arg plugin_id "$id_field" \
    --arg decision "$decision" \
    --arg outcome "$outcome" \
    '{timestamp: $timestamp, pid: $pid, repo_root: $repo_root, plugin_id: $plugin_id, decision: $decision, outcome: $outcome}' 2>/dev/null) || return 0
  [ -n "$line" ] || return 0

  if [ -z "$_AUDIT_LOCK_ACQUIRED" ]; then
    _AUDIT_LOCK_ACQUIRED=1
    _lib_acquire_append_lock "$AUDIT_LOG_LOCK" || true
  fi

  # M9's own size cap: truncate before appending once the log exceeds
  # _LIB_SIZE_THRESHOLD_BYTES (_lib.sh, 5 MB -- the same threshold
  # _lib_missing_plugin_ids already applies to SETTINGS_FILE), rather than
  # letting a shared, multi-writer, never-swept log grow without bound.
  local audit_log_size
  audit_log_size=$(stat -c%s -- "$AUDIT_LOG" 2>/dev/null) \
    || audit_log_size=$(stat -f%z -- "$AUDIT_LOG" 2>/dev/null) \
    || audit_log_size=""
  if [ -n "$audit_log_size" ] && [ "$audit_log_size" -gt "$_LIB_SIZE_THRESHOLD_BYTES" ] 2>/dev/null; then
    : > "$AUDIT_LOG" 2>/dev/null || true
  fi

  if [ -f "$AUDIT_LOG" ] && grep -qFx -e "$line" -- "$AUDIT_LOG" 2>/dev/null; then
    touch -- "$AUDIT_LOG" 2>/dev/null || true
    return 0
  fi
  printf '%s\n' "$line" >> "$AUDIT_LOG" || true
}

# _reconciler_pid_start_time PID
# LC_ALL=C TZ=UTC is load-bearing, not cosmetic: `ps -o lstart=`'s format and
# value are locale- and timezone-dependent, so two reconcilers launched
# under different ambient LC_TIME/TZ would otherwise stamp and re-query
# different strings for the same still-live holder.
_reconciler_pid_start_time() {
  LC_ALL=C TZ=UTC ps -o lstart= -p "$1" 2>/dev/null
}

# _reconciler_acquire_lock
# One lock per config dir (the contended resource is the per-account
# registry, not the repo), keyed on PID plus process start time rather than
# bare PID, so a PID recycled after wraparound to an unrelated live process
# is correctly evicted instead of read as "still held" (M8). Deliberately
# sets no EXIT trap of its own: _lib_acquire_append_lock (called by
# _audit_log, at most once per process) sets its own `trap ... EXIT`
# internally, and this repo has exactly one EXIT trap slot per process -- a
# second `trap ... EXIT` silently replaces the first (shell-script-
# conventions.md). The caller below cleans up this lock explicitly instead.
# Returns 0 once acquired, 1 if held by a still-live process (own start time
# re-queried and matches its stamp) or if this process's own start time
# couldn't be determined at all.
_reconciler_acquire_lock() {
  local my_pid=$$ my_start
  my_start=$(_reconciler_pid_start_time "$my_pid")
  [ -n "$my_start" ] || return 1

  local attempt
  for attempt in 1 2; do
    if (set -o noclobber; printf '%s\t%s\n' "$my_pid" "$my_start" > "$RECONCILER_LOCK") 2>/dev/null; then
      return 0
    fi
    local holder_pid="" holder_start=""
    IFS=$'\t' read -r holder_pid holder_start < "$RECONCILER_LOCK" 2>/dev/null || true
    if [ -z "$holder_pid" ]; then
      # Empty read-back: either the winner already released the lock in the
      # narrow window between our failed noclobber create and this read, or
      # a write is still landing. Either way this is evidence the lock may
      # already be free, not evidence it's held -- retry the noclobber
      # create on the next attempt instead of giving up.
      continue
    fi
    if kill -0 "$holder_pid" 2>/dev/null; then
      local current_start
      current_start=$(_reconciler_pid_start_time "$holder_pid")
      if [ -n "$current_start" ] && [ "$current_start" = "$holder_start" ]; then
        return 1  # genuinely still held by the same process
      fi
      # kill -0 succeeded but the start time no longer matches: the PID was
      # recycled to an unrelated live process. Fall through and evict.
    fi
    rm -f -- "$RECONCILER_LOCK" 2>/dev/null
  done
  return 1
}

# _reconciler_marker_epoch
# Re-stats MARKER_FILE's mtime fresh on every call rather than caching it
# once -- M5's own text computes each id's elapsed time as "now minus that
# marker file's mtime" at the moment of that id's own attempt, not as of
# the run's start, so a cached value would never reflect any change to the
# file's mtime after it was first read.
_reconciler_marker_epoch() {
  stat -c%Y -- "$MARKER_FILE" 2>/dev/null \
    || stat -f%m -- "$MARKER_FILE" 2>/dev/null \
    || printf ''
}

# _reconciler_process_repo
# The whole critical section, run only once M8's lock is held. Uses `return`
# rather than `exit` throughout so the caller can unconditionally release
# the lock exactly once, regardless of which branch below finishes the run.
_reconciler_process_repo() {
  # M5: created only after the lock is won -- a losing contender never
  # touches this file, so its mtime is never reset by ordinary contention.
  touch -- "$MARKER_FILE" 2>/dev/null || return 0
  [ -n "$(_reconciler_marker_epoch)" ] || return 0

  # M4: one marketplace listing per run, capped, fail-closed on any failure,
  # timeout, unparseable output, or empty result -- these all skip every id
  # for this run, distinct from a per-id "marketplace not registered" skip.
  local marketplace_json marketplace_names
  marketplace_json=$(_lib_capped_for "$_RECONCILER_CLAUDE_CALL_CAP_SECONDS" claude plugin marketplace list --json 2>/dev/null) || marketplace_json=""
  marketplace_names=""
  if [ -n "$marketplace_json" ]; then
    marketplace_names=$(printf '%s' "$marketplace_json" | _lib_jq -r '.[].name' 2>/dev/null) || marketplace_names=""
  fi
  if [ -z "$marketplace_names" ]; then
    _audit_log "-" "m4_marketplace_list_failed" "skipped_all"
    return 0
  fi

  local settings_content settings_hash
  settings_content=$(cat -- "$SETTINGS_FILE" 2>/dev/null) || settings_content=""
  settings_hash=$(_lib_hash_diff_text "$settings_content") || settings_hash=""

  local id
  while IFS= read -r id; do
    [ -n "$id" ] || continue

    local marker_epoch now_epoch
    marker_epoch=$(_reconciler_marker_epoch)
    now_epoch=$(date +%s) || now_epoch=""
    if [ -z "$marker_epoch" ] || [ -z "$now_epoch" ] || [ $((now_epoch - marker_epoch)) -ge "$_RECONCILER_WALL_CLOCK_CEILING_SECONDS" ]; then
      _audit_log "$id" "wall_clock_ceiling_reached" "skipped"
      continue
    fi

    # M3, re-validated here (the reconciler's own point of use) independent
    # of whatever validation the hook already did -- catches a hand-run or
    # stale-hook invocation too.
    if ! _lib_validate_plugin_id "$id"; then
      _audit_log "$id" "m3_invalid_id_skip" "skipped"
      continue
    fi

    if _reconciler_is_denylisted "$id"; then
      _audit_log "$id" "m4_credential_denylist_skip" "skipped"
      continue
    fi

    local marketplace_name
    marketplace_name="${id##*@}"
    if ! printf '%s\n' "$marketplace_names" | grep -qFx -- "$marketplace_name"; then
      _audit_log "$id" "m4_marketplace_not_registered" "skipped"
      continue
    fi

    # Confirmed live against this account's own registered marketplaces
    # (2026-09-28, CLI 2.1.283): `claude plugin marketplace list --json`
    # reports the source type as a plain top-level `.source` string on each
    # entry; that attestation covers the field shape only, not which source
    # types exist. A repo's own settings.json `extraKnownMarketplaces` uses a
    # different, nested shape (`.value.source.source`) for the same concept --
    # don't conflate the two when reading this filter.
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

    # M6: `-s local` is the chosen scope, but only once a repo's own
    # tracked tree is confirmed clean of the write it produces.
    # check-ignore alone already fails closed here: git's own documented
    # default behavior is that a tracked path is reported "not ignored"
    # regardless of any matching .gitignore rule (git-check-ignore(1):
    # "tracked files are not shown at all since they are not subject to
    # exclude rules"), so check-ignore run alone would already skip an
    # already-tracked-and-matching path via m6_not_ignored below. ls-files
    # runs first as defense in depth, not because order changes whether the
    # install proceeds -- it logs the more specific, more actionable
    # "already tracked" decision instead of the generic "not ignored" one
    # check-ignore alone would produce for the same case.
    if _lib_capped git -C "$TARGET_REPO_ROOT" ls-files --error-unmatch -- .claude/settings.local.json >/dev/null 2>&1; then
      _audit_log "$id" "m6_already_tracked" "skipped"
      continue
    fi
    if ! _lib_capped git -C "$TARGET_REPO_ROOT" check-ignore -q -- .claude/settings.local.json 2>/dev/null; then
      _audit_log "$id" "m6_not_ignored" "skipped"
      continue
    fi

    # M9: a hash-keyed failure stamp, never named from repo-controlled text
    # directly -- the filename is a hash of the id plus the settings
    # file's own content hash, so it changes (and the old stamp goes
    # stale) the moment the repo's settings change, with no TTL guess
    # involved.
    local stamp_key stamp_file=""
    stamp_key=$(_lib_hash_diff_text "${id}$(printf '\x1e')${settings_hash}") || stamp_key=""
    if [ -n "$stamp_key" ]; then
      stamp_file="$BOOTSTRAP_DIR/failure-stamp.$stamp_key"
      if [ -f "$stamp_file" ]; then
        _audit_log "$id" "m9_stamp_suppressed" "skipped"
        continue
      fi
    fi

    # M7: never -y/--yes/--accept-command. M3: -- immediately before the
    # repo-controlled id, defense in depth against it being read as a flag.
    # Row 2: `claude plugin install` has no cwd-override flag, so a
    # project-/local-scope install must run with the target repo as cwd --
    # a subshell `cd`, not the main script's own, so a later iteration's
    # `git -C` calls above are never affected by where this one landed.
    if (cd -- "$TARGET_REPO_ROOT" && _lib_capped_for "$_RECONCILER_CLAUDE_CALL_CAP_SECONDS" claude plugin install -s local -- "$id") >/dev/null 2>&1; then
      _audit_log "$id" "install_succeeded" "success"
    else
      [ -n "$stamp_file" ] && { touch -- "$stamp_file" 2>/dev/null || true; }
      _audit_log "$id" "install_failed" "failure"
    fi
  done <<< "$_missing_ids"
}

# M1/M2-equivalent: independently re-derive the missing-id set rather than
# trusting an argv-passed list (M5). The registry-corruption exit status (2)
# is handled the same way the hook's own M2 rule would, and the unreadable-
# local-opt-out status (3) fails closed the same way.
_missing_ids_status=0
_missing_ids=$(_lib_missing_plugin_ids "$SETTINGS_FILE" "$REGISTRY_FILE") || _missing_ids_status=$?
case "$_missing_ids_status" in
  0) ;;
  1) exit 0 ;;  # symlinked/oversized settings file -- same as absent, no log (M1)
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

if _reconciler_acquire_lock; then
  _reconciler_process_repo
  rm -f -- "$RECONCILER_LOCK"
else
  _audit_log "-" "lock_held" "skipped"
fi
