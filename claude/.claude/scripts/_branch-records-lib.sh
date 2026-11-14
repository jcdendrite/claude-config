#!/usr/bin/env bash
# _branch-records-lib.sh — copies a worktree's agent-reviews/ into the branch-records archive before a cleanup script removes the worktree.
#
# Sourced by cleanup-merged-branches.sh and cleanup-idle-open-pr-worktrees.sh, after hooks/_lib.sh.
# Not executable on its own; source it, do not invoke it.
# Neither function depends on the caller's `pipefail`.
# Call both functions in the current shell, not in a command substitution or pipeline, because their outputs and the snapshot counter live in the caller's shell.
# Only commands run through `_lib_capped` and `_lib_jq` are capped, so a stall in any other step, a shell test or a redirection included, hangs the caller until interrupted.
# docs/agent-branch-records.md is the home of the archive layout and the reason table.
#
# Provides:
#   branch_records_status           — whether the archive is disabled, ready or unavailable
#   branch_records_archive_worktree — a verified snapshot of one worktree's agent-reviews/

_BRANCH_RECORDS_SENTINEL_NAME='.agent-branch-records-root'
_BRANCH_RECORDS_MANIFEST_SCHEMA_VERSION=1

# One fixed one-line string per refusal cause. The docs' reason table quotes each value in backticks.
_BRANCH_RECORDS_REASON_CONFIG='the config directory or the archive setting could not be resolved'
_BRANCH_RECORDS_REASON_SENTINEL='the archive root is missing its readiness marker'
_BRANCH_RECORDS_REASON_STALLED='a filesystem call under the archive root did not answer in time'
_BRANCH_RECORDS_REASON_ROOT_NOT_WRITABLE='the archive root is not writable'
_BRANCH_RECORDS_REASON_ACCOUNT_NOT_WRITABLE="this account's archive directory is not writable"
_BRANCH_RECORDS_REASON_LEDGER_KEY="the worktree's repository root or ledger key could not be resolved"
_BRANCH_RECORDS_REASON_UNSUPPORTED_ENTRY='agent-reviews is not a plain directory, or holds an entry that is neither a regular file nor a directory'
_BRANCH_RECORDS_REASON_DIRECTORY='an archive directory could not be created'
_BRANCH_RECORDS_REASON_SNAPSHOT_MKDIR='the snapshot directory could not be created'
_BRANCH_RECORDS_REASON_COPY_FAILED='copying agent-reviews failed'
_BRANCH_RECORDS_REASON_COPY_MISMATCH='the snapshot copy of agent-reviews differs from the original'
_BRANCH_RECORDS_REASON_MANIFEST='the snapshot manifest could not be written'
_BRANCH_RECORDS_REASON_KEY_UNREADABLE='the archive setting in claude-config.toml could not be read as true or false'

# Outputs, read by the sourcing scripts.
BRANCH_RECORDS_STATE=''
BRANCH_RECORDS_REASON=''
BRANCH_RECORDS_ACCOUNT_DIR=''
BRANCH_RECORDS_SNAPSHOT=''

# Per-shell counter that keeps two snapshot names apart within one second.
_branch_records_snapshot_seq=0

# _branch_records_canonical_dir DIR
# The cd runs in a subshell so the caller's cwd and CDPATH are untouched.
_branch_records_canonical_dir() {
  local resolved
  resolved=$(CDPATH='' cd -P -- "$1" 2>/dev/null && pwd -P) || return 1
  [ -n "$resolved" ] || return 1
  printf '%s' "$resolved"
}

# _branch_records_encode_component PATH
# Prints PATH as one path component. `%` is replaced before `/`, so a literal `%2F` in PATH stays distinct from an encoded slash.
_branch_records_encode_component() {
  local percent='%' slash='/' encoded="$1"
  encoded=${encoded//"$percent"/%25}
  encoded=${encoded//"$slash"/%2F}
  printf '%s' "$encoded"
}

# _branch_records_mark_unavailable REASON
_branch_records_mark_unavailable() {
  BRANCH_RECORDS_STATE=unavailable
  BRANCH_RECORDS_REASON="$1"
}

# _branch_records_toml_confirms_off CONFIG_DIR
# Returns 0 when CONFIG_DIR's claude-config.toml provably does not exist.
# Returns 0 when the file was read and holds no line containing the text agent_branch_records that _config_line_key_value cannot parse, and no line for the key whose value is other than true or false.
# A failed lookup proves absence only when the nearest existing ancestor is a searchable directory, because EACCES, ELOOP and ENOTDIR also fail it.
# Every other outcome returns 1, except these three residuals, which return 0:
# - A stat error such as EIO, ESTALE or ETIMEDOUT under a searchable parent, which reads as absence.
# - A transient read failure inside the config reader's own read, which is not detected here.
# - A misspelled key name, which is not detected.
_branch_records_toml_confirms_off() {
  local state_file="$1/$_CONFIG_STATE_FILENAME" entry="$1/$_CONFIG_STATE_FILENAME" parent
  while [ ! -e "$entry" ] && [ ! -L "$entry" ]; do
    # `${entry%/*}` of `/` is empty and `${parent:-/}` restores `/`, so only this line ends the walk if stat of `/` fails.
    [ "$entry" != / ] || return 1
    parent=${entry%/*}
    [ "$parent" != "$entry" ] || return 1
    entry=${parent:-/}
  done
  if [ "$entry" != "$state_file" ]; then
    [ -d "$entry" ] && [ -x "$entry" ] || return 1
    return 0
  fi
  [ -f "$state_file" ] || return 1

  # Not `$(<file)`, which exits a `set -e` caller on a read failure even inside `||`.
  # The braces let the redirect also hide bash's null-byte warning, which an inline `2>/dev/null` on the assignment does not.
  local content
  { content=$(cat -- "$state_file"); } 2>/dev/null || return 1
  # Same single-BOM strip as _config_file_lines, which cannot report a read failure.
  content="${content#$'\xEF\xBB\xBF'}"

  local line key_out value_out line_status
  while IFS= read -r line || [ -n "$line" ]; do
    line_status=0
    _config_line_key_value "$line" key_out value_out || line_status=$?
    case "$line_status" in
      0)
        if [ "$key_out" = agent_branch_records ] && [ "$value_out" != true ] && [ "$value_out" != false ]; then
          return 1
        fi
        ;;
      1) ;;
      *)
        case "$line" in
          *agent_branch_records*) return 1 ;;
        esac
        ;;
    esac
  done <<< "$content"
  return 0
}

# branch_records_status
# Always returns 0. Sets BRANCH_RECORDS_STATE (disabled, ready or unavailable),
# BRANCH_RECORDS_REASON (unavailable only) and BRANCH_RECORDS_ACCOUNT_DIR (ready only). Creates nothing.
branch_records_status() {
  BRANCH_RECORDS_STATE=disabled
  BRANCH_RECORDS_REASON=''
  BRANCH_RECORDS_ACCOUNT_DIR=''

  local key_status=0 config_dir canonical_config_dir
  _config_enabled agent_branch_records || key_status=$?
  if [ "$key_status" -gt 1 ] || ! config_dir=$(_lib_config_dir); then
    _branch_records_mark_unavailable "$_BRANCH_RECORDS_REASON_CONFIG"
    return 0
  fi
  if [ "$key_status" -eq 1 ]; then
    _branch_records_toml_confirms_off "$config_dir" || _branch_records_mark_unavailable "$_BRANCH_RECORDS_REASON_KEY_UNREADABLE"
    return 0
  fi

  canonical_config_dir=$(_branch_records_canonical_dir "$config_dir") || {
    _branch_records_mark_unavailable "$_BRANCH_RECORDS_REASON_CONFIG"
    return 0
  }

  local root="${HOME:-}/.local/state/agent-branch-records"
  local account_dir
  account_dir="$root/$(_branch_records_encode_component "$canonical_config_dir")"

  local probe_status=0
  _lib_capped test -f "$root/$_BRANCH_RECORDS_SENTINEL_NAME" >/dev/null 2>&1 || probe_status=$?
  if [ "$probe_status" -ne 0 ]; then
    if _lib_status_consistent_with_cap_kill "$probe_status"; then
      _branch_records_mark_unavailable "$_BRANCH_RECORDS_REASON_STALLED"
    else
      _branch_records_mark_unavailable "$_BRANCH_RECORDS_REASON_SENTINEL"
    fi
    return 0
  fi

  # Once the account directory exists no step writes the root, so the root is probed only before that.
  local write_target="$root" not_writable_reason="$_BRANCH_RECORDS_REASON_ROOT_NOT_WRITABLE"
  if [ -d "$account_dir" ]; then
    write_target="$account_dir"
    not_writable_reason="$_BRANCH_RECORDS_REASON_ACCOUNT_NOT_WRITABLE"
  fi
  probe_status=0
  _lib_capped test -w "$write_target" >/dev/null 2>&1 || probe_status=$?
  if [ "$probe_status" -ne 0 ]; then
    if _lib_status_consistent_with_cap_kill "$probe_status"; then
      _branch_records_mark_unavailable "$_BRANCH_RECORDS_REASON_STALLED"
    else
      _branch_records_mark_unavailable "$not_writable_reason"
    fi
    return 0
  fi

  BRANCH_RECORDS_STATE=ready
  BRANCH_RECORDS_ACCOUNT_DIR="$account_dir"
  return 0
}

# _branch_records_abandon_snapshot SNAPSHOT_DIR REASON
# Removes the partial copy this call's mkdir created, ignoring the removal's own status so REASON stands.
# Returns 1.
_branch_records_abandon_snapshot() {
  rm -rf -- "${1:?}" >/dev/null 2>&1 || true
  BRANCH_RECORDS_REASON="$2"
  return 1
}

# _branch_records_tree_is_plain DIR
# Runs without -L, so a symlink is reported as itself and is refused.
_branch_records_tree_is_plain() {
  local scan_status=0 unsupported_entries
  unsupported_entries=$(find "$1" ! -type f ! -type d -print 2>/dev/null) || scan_status=$?
  [ "$scan_status" -eq 0 ] && [ -z "$unsupported_entries" ]
}

# _branch_records_find_reusable_snapshot KEY_DIR TIP_SHA LEDGER_KEY AGENT_REVIEWS SOURCE_DIR
# Prints the newest complete snapshot under KEY_DIR that still matches the worktree.
# Returns 0 on a match, 1 on a miss, and 2 when a capped call was killed at the cap.
_branch_records_find_reusable_snapshot() {
  local key_dir="$1" tip_sha="$2" ledger_key="$3" agent_reviews="$4" source_dir="$5"

  local manifests find_status=0
  manifests=$(_lib_capped find "$key_dir" -mindepth 2 -maxdepth 2 -name manifest.json -type f 2>/dev/null) || find_status=$?
  if [ "$find_status" -ne 0 ]; then
    if _lib_status_consistent_with_cap_kill "$find_status"; then
      return 2
    fi
    return 1
  fi

  local sorted_manifests candidate
  sorted_manifests=$(LC_ALL=C sort <<<"$manifests") || return 1
  candidate=${sorted_manifests##*$'\n'}
  [ -n "$candidate" ] || return 1

  # The selected output is the manifest's stored agent_reviews, which equals the current value whenever the comparison holds.
  local stored_agent_reviews jq_status=0
  # shellcheck disable=SC2016 # single-quoted on purpose: the $names are jq --arg variables, and double quotes would expand them in the shell.
  stored_agent_reviews=$(_lib_jq -e -r --arg tip_sha "$tip_sha" --arg ledger_key "$ledger_key" --arg agent_reviews "$agent_reviews" \
    'select(.tip_sha == $tip_sha and .ledger_key == $ledger_key and .agent_reviews == $agent_reviews) | .agent_reviews' \
    "$candidate" 2>/dev/null) || jq_status=$?
  if [ "$jq_status" -ne 0 ]; then
    if _lib_status_consistent_with_cap_kill "$jq_status"; then
      return 2
    fi
    return 1
  fi

  local snapshot_dir=${candidate%/manifest.json}
  if [ "$stored_agent_reviews" = copied ]; then
    diff -r "$snapshot_dir/agent-reviews" "$source_dir" >/dev/null 2>&1 || return 1
  fi
  printf '%s' "$snapshot_dir"
}

# branch_records_archive_worktree WORKTREE BRANCH MAIN_WORKTREE TRIGGER
# BRANCH is the name that refs/heads/<BRANCH> resolves, with neither a refs/heads/ nor a heads/ prefix, because the ledger key hashes it verbatim.
# Returns 0 with BRANCH_RECORDS_SNAPSHOT empty when the archive is disabled, which means nothing was archived.
# Otherwise returns 0 with BRANCH_RECORDS_SNAPSHOT naming a complete snapshot of the worktree's current state.
# Otherwise returns 1 and sets BRANCH_RECORDS_REASON.
# A snapshot is complete once its manifest.json exists, which is written last.
# shellcheck disable=SC2034 # BRANCH_RECORDS_REASON and BRANCH_RECORDS_SNAPSHOT are read by the sourcing scripts, not by this lib.
branch_records_archive_worktree() {
  local worktree="${1-}" branch="${2-}" main_worktree="${3-}" trigger="${4-}"
  BRANCH_RECORDS_SNAPSHOT=''
  BRANCH_RECORDS_REASON=''

  branch_records_status
  case "$BRANCH_RECORDS_STATE" in
    disabled) return 0 ;;
    ready) ;;
    *) return 1 ;;
  esac
  local account_dir="$BRANCH_RECORDS_ACCOUNT_DIR"

  if [ -z "$worktree" ] || [ -z "$branch" ] || [ -z "$main_worktree" ] || [ -z "$trigger" ]; then
    BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_LEDGER_KEY"
    return 1
  fi

  local source_dir="$worktree/agent-reviews" agent_reviews
  if [ -L "$source_dir" ]; then
    BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_UNSUPPORTED_ENTRY"
    return 1
  elif [ -d "$source_dir" ]; then
    agent_reviews=copied
    if ! _branch_records_tree_is_plain "$source_dir"; then
      BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_UNSUPPORTED_ENTRY"
      return 1
    fi
  elif [ -e "$source_dir" ]; then
    BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_UNSUPPORTED_ENTRY"
    return 1
  else
    # A failed stat of agent-reviews or .git for want of search permission on the worktree or an ancestor reads as absent.
    # The same permissions stop git worktree remove deleting the entry, so the findings stay on disk.
    # With an unsearchable ancestor git may still unregister the worktree.
    agent_reviews=absent
  fi

  # Without a .git, git would walk up to an enclosing repository and hash the wrong root.
  local ledger_repo_root ledger_key tip_sha
  if [ -e "$worktree/.git" ]; then
    ledger_repo_root=$(_lib_capped git -C "$worktree" rev-parse --show-toplevel 2>/dev/null) || ledger_repo_root=''
    if [ -z "$ledger_repo_root" ]; then
      BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_LEDGER_KEY"
      return 1
    fi
  else
    ledger_repo_root="$worktree"
  fi
  ledger_key=$(_lib_reviewer_round_state_key_for_branch "$ledger_repo_root" "$branch") || ledger_key=''
  if [ -z "$ledger_key" ]; then
    BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_LEDGER_KEY"
    return 1
  fi
  tip_sha=$(_lib_capped git -C "$main_worktree" rev-parse --verify -q "refs/heads/$branch^{commit}" 2>/dev/null) || tip_sha=''

  local branches_dir="$account_dir/branches"
  local key_dir="$branches_dir/$ledger_key"
  local archive_dir
  for archive_dir in "$account_dir" "$branches_dir" "$key_dir"; do
    [ -d "$archive_dir" ] && continue
    # A concurrent first run may have just made it, so a failed mkdir only counts when the directory is still missing.
    ( umask 077; _lib_capped mkdir -- "$archive_dir" ) >/dev/null 2>&1 || [ -d "$archive_dir" ] || {
      BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_DIRECTORY"
      return 1
    }
  done
  _lib_capped chmod -- 700 "$account_dir" >/dev/null 2>&1 || true

  # Every miss falls through to a fresh copy, so a removal never rests on a doubtful snapshot.
  local reusable_snapshot reuse_status=0
  reusable_snapshot=$(_branch_records_find_reusable_snapshot "$key_dir" "$tip_sha" "$ledger_key" "$agent_reviews" "$source_dir") || reuse_status=$?
  case "$reuse_status" in
    0)
      BRANCH_RECORDS_SNAPSHOT="$reusable_snapshot"
      return 0
      ;;
    1) ;;
    *)
      BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_STALLED"
      return 1
      ;;
  esac

  local stamp snapshot_dir
  stamp=$(date -u +%Y%m%dT%H%M%SZ) || stamp=''
  if [ -z "$stamp" ]; then
    BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_SNAPSHOT_MKDIR"
    return 1
  fi
  _branch_records_snapshot_seq=$((_branch_records_snapshot_seq + 1))
  snapshot_dir="$key_dir/$stamp.$$.$_branch_records_snapshot_seq"
  # mkdir without -p creates the path or fails, so exit 0 shows this call owns the directory.
  ( umask 077; _lib_capped mkdir -- "$snapshot_dir" ) >/dev/null 2>&1 || {
    BRANCH_RECORDS_REASON="$_BRANCH_RECORDS_REASON_SNAPSHOT_MKDIR"
    return 1
  }

  if [ "$agent_reviews" = copied ]; then
    cp -RPp "$source_dir" "$snapshot_dir/agent-reviews" >/dev/null 2>&1 \
      || { _branch_records_abandon_snapshot "$snapshot_dir" "$_BRANCH_RECORDS_REASON_COPY_FAILED"; return 1; }
    # An entry added after the first scan is copied as itself, and diff -r follows links, so the copy is scanned again.
    _branch_records_tree_is_plain "$snapshot_dir/agent-reviews" \
      || { _branch_records_abandon_snapshot "$snapshot_dir" "$_BRANCH_RECORDS_REASON_UNSUPPORTED_ENTRY"; return 1; }
    diff -r "$source_dir" "$snapshot_dir/agent-reviews" >/dev/null 2>&1 \
      || { _branch_records_abandon_snapshot "$snapshot_dir" "$_BRANCH_RECORDS_REASON_COPY_MISMATCH"; return 1; }
  fi

  local archived_at manifest_tmp="$snapshot_dir/manifest.json.tmp"
  archived_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) || archived_at=''
  if [ -z "$archived_at" ]; then
    _branch_records_abandon_snapshot "$snapshot_dir" "$_BRANCH_RECORDS_REASON_MANIFEST"
    return 1
  fi
  # A snapshot counts as complete once the manifest is renamed into place, and its content is not read back.
  # shellcheck disable=SC2016 # single-quoted on purpose: the $names are jq --arg variables, and double quotes would expand them in the shell.
  _lib_jq -n \
    --argjson schema_version "$_BRANCH_RECORDS_MANIFEST_SCHEMA_VERSION" \
    --arg archived_at "$archived_at" \
    --arg trigger "$trigger" \
    --arg branch "$branch" \
    --arg tip_sha "$tip_sha" \
    --arg worktree_path "$worktree" \
    --arg main_worktree_path "$main_worktree" \
    --arg ledger_repo_root "$ledger_repo_root" \
    --arg ledger_key "$ledger_key" \
    --arg agent_reviews "$agent_reviews" \
    '{schema_version: $schema_version, archived_at: $archived_at, trigger: $trigger, branch: $branch,
      tip_sha: $tip_sha, worktree_path: $worktree_path, main_worktree_path: $main_worktree_path,
      ledger_repo_root: $ledger_repo_root, ledger_key: $ledger_key, agent_reviews: $agent_reviews}' \
    > "$manifest_tmp" 2>/dev/null \
    || { _branch_records_abandon_snapshot "$snapshot_dir" "$_BRANCH_RECORDS_REASON_MANIFEST"; return 1; }
  _lib_capped chmod -R -- go-rwx "$snapshot_dir" >/dev/null 2>&1 || true
  _lib_capped mv -- "$manifest_tmp" "$snapshot_dir/manifest.json" >/dev/null 2>&1 \
    || { _branch_records_abandon_snapshot "$snapshot_dir" "$_BRANCH_RECORDS_REASON_MANIFEST"; return 1; }

  BRANCH_RECORDS_SNAPSHOT="$snapshot_dir"
  return 0
}
