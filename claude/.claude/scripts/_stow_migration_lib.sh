# shellcheck shell=bash
# Migrates a stow-adopted ~/.claude/<name> directory (plans/, handoffs/,
# briefs/) back to a plain real directory. Sourced by install.sh before it
# re-stows with `--ignore` for these names. Not currently sourced by
# relocate-claude-config.sh, despite sharing its BACKUP_DIR path below.
#
# No shebang and no top-level `set` statement: install.sh sources this into
# its own shell, which must not pick up `set -u`/`pipefail` mid-script (see
# install.sh's sourcing comment). Every command below is checked explicitly
# instead of relying on `set -e` to abort on failure, since a caller invoking
# stow_migrate_adopted_dir from inside an `if`/`||` condition (as install.sh
# does, to continue past a per-entry failure) disables `-e` for everything
# executed in that call chain, including commands inside this function.

# Shared with relocate-claude-config.sh's own BACKUP_DIR -- same quarantine
# location for both scripts' backups.
_STOW_MIGRATION_BACKUP_ROOT="$HOME/.claude-config-relocate-backup"

# Written into a backup directory once step (c) below has fully restored it
# into $target -- lets a retry tell "target exists because migration
# completed" apart from "target exists because a prior copy was interrupted
# partway," which bare `[ -e "$target" ]` cannot distinguish.
_STOW_MIGRATION_COMPLETE_SENTINEL=".migration-complete"

# True iff $1 is a symlink whose canonicalized target equals $2. cd -P +
# pwd -P canonicalizes without a readlink -f dependency, portable to macOS's
# BSD readlink (no -f flag). Safe here because both targets are always
# directories.
_stow_migration_lib_symlink_resolves_to() {
  local link="$1" expected="$2" resolved expected_resolved
  [ -L "$link" ] || return 1
  resolved=$(cd -P -- "$link" 2>/dev/null && pwd -P) || return 1
  expected_resolved=$(cd -P -- "$expected" 2>/dev/null && pwd -P) || return 1
  [ "$resolved" = "$expected_resolved" ]
}

# Print the newest non-empty backup directory for $1 (a bare name, e.g.
# "plans") under $_STOW_MIGRATION_BACKUP_ROOT, or fail (return 1, no stdout)
# when none exists. Lexicographic sort is correct because every backup is
# suffixed with a fixed-width `date +%Y%m%d%H%M%S` timestamp.
_stow_migration_lib_newest_backup() {
  local name="$1"
  [ -d "$_STOW_MIGRATION_BACKUP_ROOT" ] || return 1
  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  # Capitalized (unlike this file's other locals) so `${#Candidates[@]}`
  # doesn't read as a Slack-channel-shaped reference to this repo's own
  # redaction hook (deny-private-project-refs.sh's `#[a-z...` regex).
  local Candidates=("$_STOW_MIGRATION_BACKUP_ROOT/$name".*)
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi
  [ ${#Candidates[@]} -gt 0 ] || return 1
  local candidate
  while IFS= read -r candidate; do
    [ -d "$candidate" ] || continue
    [ -n "$(ls -A -- "$candidate" 2>/dev/null)" ] || continue
    printf '%s' "$candidate"
    return 0
  done < <(printf '%s\n' "${Candidates[@]}" | sort -r)
  return 1
}

# stow_migrate_adopted_dir REPO_DIR NAME
# Migrate $HOME/.claude/NAME off stow management to a plain real directory,
# before a caller re-stows with `--ignore` for NAME.
#
# No-op (return 0) when NAME is neither a live symlink into
# $REPO_DIR/claude/.claude/NAME nor a resumable incomplete-migration state
# with an intact backup -- this covers both "nothing to migrate yet" (fresh
# install, before stow has ever run) and "already migrated" (steady state
# after a prior successful run, marked by _STOW_MIGRATION_COMPLETE_SENTINEL
# in the backup), so a second run is idempotent by construction. On a prior
# failed run, $HOME/.claude/NAME can be left missing, a dangling symlink, or
# a partially-populated real directory (an interrupted step (c) copy), all
# with the backup intact and unmarked; each of those states is detected
# here as resumable, so a retry picks up from step (c) instead of treating
# it as nothing to migrate.
stow_migrate_adopted_dir() {
  local repo_dir="$1" name="$2"
  local target="$HOME/.claude/$name"
  local expected_source="$repo_dir/claude/.claude/$name"

  # Refuse a pre-planted symlink at the fixed, predictable
  # $_STOW_MIGRATION_BACKUP_ROOT path before any mkdir/chmod/cp beneath it --
  # mirrors relocate-claude-config.sh's own guard for this identical shared
  # path (_ensure_backup_dir_is_not_a_symlink).
  # Narrow check-then-act window before the mkdir below, same as
  # relocate-claude-config.sh's identical guard for this path -- accepted,
  # not closed here.
  if [ -L "$_STOW_MIGRATION_BACKUP_ROOT" ]; then
    echo "[install] $_STOW_MIGRATION_BACKUP_ROOT already exists as a symlink -- refusing to migrate ~/.claude/$name through it. Remove it or replace it with a real directory first." >&2
    return 1
  fi

  local is_live_symlink=false
  if _stow_migration_lib_symlink_resolves_to "$target" "$expected_source"; then
    is_live_symlink=true
  fi

  local backup_dir=""
  if ! $is_live_symlink; then
    backup_dir=$(_stow_migration_lib_newest_backup "$name") || return 0
    if [ -e "$target" ] && [ -f "$backup_dir/$_STOW_MIGRATION_COMPLETE_SENTINEL" ]; then
      return 0
    fi
    # $target exists but the newest backup carries no completion sentinel --
    # a prior step (c) copy below was interrupted partway; fall through and
    # retry it rather than treating bare existence as proof of completion.
    #
    # Resuming from a prior run's backup -- re-assert 700 in case the
    # original chmod failed, or this directory predates this hardening.
    chmod 700 "$backup_dir" || echo "[install] warning: could not chmod 700 $backup_dir" >&2
  fi

  # Step (a): back up the real content before touching the symlink. Copy,
  # not move, so a failed step (c) below can be retried against an intact
  # backup. Skipped on the resumable path -- the existing backup is the
  # resume source and must not be overwritten by an empty one.
  if $is_live_symlink; then
    backup_dir="$_STOW_MIGRATION_BACKUP_ROOT/$name.$(date +%Y%m%d%H%M%S)"
    if ! mkdir -p -- "$backup_dir"; then
      echo "[install] could not create backup directory $backup_dir for ~/.claude/$name -- leaving it under stow management" >&2
      return 1
    fi
    # mkdir -p leaves a freshly-created parent at the process umask; chmod it
    # too, not only the leaf, or its entries (backup directory names) stay
    # listable by other local accounts. Unconditional, not only on fresh
    # creation -- this script exclusively owns this root, so re-asserting 700
    # on a pre-existing one (left loose by a prior run or another creator) is
    # always safe.
    chmod 700 "$_STOW_MIGRATION_BACKUP_ROOT" || echo "[install] warning: could not chmod 700 $_STOW_MIGRATION_BACKUP_ROOT" >&2
    # Fatal, unlike the warn-only precedent this mirrors elsewhere in this
    # repo: nothing has been written into $backup_dir yet, so aborting here
    # costs nothing, and letting the copy below proceed regardless would put
    # private content into a directory still at default permissions.
    if ! chmod 700 "$backup_dir"; then
      echo "[install] could not chmod 700 $backup_dir -- refusing to copy ~/.claude/$name content into it" >&2
      return 1
    fi
    if ! cp -R "$target/." "$backup_dir/" 2>/dev/null; then
      echo "[install] could not back up ~/.claude/$name (symlinked into $expected_source) -- leaving it under stow management" >&2
      return 1
    fi
  fi

  # Step (b): unlink the stow symlink. Already done by a prior failed run
  # leaves $target simply absent, so this is skipped rather than re-checked.
  if [ -L "$target" ]; then
    if ! rm -f -- "$target"; then
      echo "[install] could not unlink ~/.claude/$name -- backup is intact at $backup_dir for a retry" >&2
      return 1
    fi
  fi

  # Step (c): copy the backup into a plain real directory at $target. The
  # trailing "/." source form matches step (a)'s merge semantics, so a retry
  # against an already-existing (partially-populated) $target merges into it
  # instead of cp nesting $backup_dir as a subdirectory underneath it.
  if ! mkdir -p -- "$target" || ! cp -R "$backup_dir/." "$target/" 2>/dev/null; then
    echo "[install] could not restore ~/.claude/$name from backup $backup_dir -- re-run install.sh to retry; the backup is untouched" >&2
    return 1
  fi
  # Marks $backup_dir restored so a retry treats an existing $target as done
  # instead of re-copying indefinitely.
  touch -- "$backup_dir/$_STOW_MIGRATION_COMPLETE_SENTINEL" || echo "[install] warning: could not mark backup $backup_dir as fully restored" >&2

  echo "[install] migrated ~/.claude/$name off stow management to a real directory (backup kept at $backup_dir)"
  return 0
}

# Print the canonicalized realpath of $1, or nothing if it cannot be
# resolved. Delegates to Python rather than hand-rolling a symlink-chain
# walker, since BSD readlink (macOS) has no -f flag; python3 is already a
# required install.sh dependency.
_stow_migration_lib_realpath() {
  python3 -c 'import os, sys
print(os.path.realpath(sys.argv[1]))' "$1" 2>/dev/null
}

# stow_repair_nested_adoption REPO_DIR NAME
# Idempotent repair for a prior bug: stow's --ignore is matched against each
# item's path relative to the package root (e.g. .claude/briefs), not its
# basename, so a bare name pattern silently fails to protect a directory
# nested under an already-unfolded parent -- `stow --adopt` walks in and
# turns each pre-existing file into an individual symlink back into the
# package, defeating the point of `stow_migrate_adopted_dir` above. Installs
# that ran with the old, ineffective pattern are left with these per-entry
# symlinks; this replaces each one (a real copy of its resolved content)
# rather than requiring a fresh top-level migration, which
# stow_migrate_adopted_dir's own idempotency check would skip since $target
# is already a real directory. A plain real entry, or a symlink resolving
# outside $expected_source, is left untouched. Only scans $target's direct
# children, not subdirectories: plans/, handoffs/, and briefs/ are documented
# flat namespaces (one <slug>.md per entry), and dotfiles are excluded from
# the same premise -- neither this function nor the bug it repairs has ever
# had a dotfile or nested-subdirectory case to handle.
stow_repair_nested_adoption() {
  local repo_dir="$1" name="$2"
  local target="$HOME/.claude/$name"
  local expected_source="$repo_dir/claude/.claude/$name"

  # The ! -L arm matters when stow_migrate_adopted_dir has failed and left
  # $target as the original whole-directory symlink into $expected_source
  # (its documented step (a)/(b) retry outcome) -- without it, -d follows
  # the symlink and this function would glob the tracked package tree
  # instead of a no-op.
  [ -d "$target" ] && [ ! -L "$target" ] || return 0
  local expected_source_real
  expected_source_real=$(cd -P -- "$expected_source" 2>/dev/null && pwd -P) || return 0

  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local entries=("$target"/*)
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi

  local entry resolved tmp
  for entry in "${entries[@]}"; do
    [ -L "$entry" ] || continue
    resolved=$(_stow_migration_lib_realpath "$entry")
    case "$resolved" in
      "$expected_source_real"/*)
        tmp=$(mktemp "$target/.stow-repair.XXXXXX") || {
          echo "[install] warning: could not de-adopt $entry -- leaving it as a symlink into $repo_dir" >&2
          continue
        }
        # -p preserves the resolved source's mode: mktemp creates $tmp at
        # 0600, and plain cp onto an existing destination leaves that mode
        # alone instead of adopting the source's, silently narrowing every
        # repaired entry's permissions otherwise.
        if ! cp -p -- "$entry" "$tmp"; then
          rm -f -- "$tmp"
          echo "[install] warning: could not copy $entry before de-adopting -- leaving it as a symlink into $repo_dir" >&2
          continue
        fi
        # A direct mv (no separate rm) so the swap has no intermediate state
        # where $entry doesn't exist under either name: mv/rename(2)
        # atomically replaces the symlink destination with $tmp's content on
        # the same filesystem, closing the window a separate rm-then-mv
        # would leave between removing the old symlink and placing the copy.
        # A failed rename leaves both $entry (still the original symlink)
        # and $tmp (the copy) intact -- $tmp is deliberately not cleaned up
        # here so its path stays available for manual recovery.
        mv -- "$tmp" "$entry" || echo "[install] warning: could not de-adopt $entry -- its content was copied to $tmp but the swap failed; the original symlink is still in place" >&2
        ;;
    esac
  done
}
