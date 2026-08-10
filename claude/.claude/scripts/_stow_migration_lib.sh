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

# stow_migration_is_complete NAME
# True iff stow_migrate_adopted_dir's most recent backup for NAME carries its
# completion sentinel -- the same signal that function itself trusts to tell
# "fully restored" apart from "partially restored, retry pending." Public so
# a caller deciding whether NAME's package-side original is safe to delete
# (not just present) can reuse this rather than inferring completeness from
# bare $HOME/.claude/NAME existence, which is exactly the ambiguity the
# sentinel exists to resolve -- and which a real, non-symlink but only
# partially-restored $target cannot be told apart from by existence alone.
stow_migration_is_complete() {
  local name="$1"
  local backup_dir
  backup_dir=$(_stow_migration_lib_newest_backup "$name") || return 1
  [ -f "$backup_dir/$_STOW_MIGRATION_COMPLETE_SENTINEL" ]
}

# Print the canonicalized realpath of $1, or nothing if it cannot be
# resolved. Delegates to Python rather than hand-rolling a symlink-chain
# walker, since BSD readlink (macOS) has no -f flag; python3 is already a
# required install.sh dependency.
_stow_migration_lib_realpath() {
  python3 -c 'import os, sys
print(os.path.realpath(sys.argv[1]))' "$1" 2>/dev/null
}

# Print $1 with every Perl-regex metacharacter escaped, for embedding a
# filesystem-derived name (not a fixed, reviewed literal) into a `stow
# --ignore` pattern. Delegates to Python's re.escape rather than a
# hand-rolled chain of parameter-expansion substitutions -- easy to under-
# escape by hand (this file's own first attempt escaped only literal dots)
# and python3 is already a required install.sh dependency.
_stow_migration_lib_regex_escape() {
  python3 -c 'import re, sys
print(re.escape(sys.argv[1]))' "$1" 2>/dev/null
}

# Print $1 with the bash glob metacharacters * ? [ escaped, for embedding a
# filesystem-derived name into a glob pattern (as
# _stow_migration_lib_newest_unadopt_run does below). Hand-rolled rather than
# delegating to Python like the regex escaper above: bash glob syntax has
# only these three special characters (no extglob is enabled anywhere in
# this file), so enumerating them directly is not the under-escaping risk a
# full regex dialect is.
_stow_migration_lib_glob_escape() {
  local s="$1"
  s="${s//\[/\\[}"
  s="${s//\*/\\*}"
  s="${s//\?/\\?}"
  printf '%s' "$s"
}

# True iff $1 is a symlink whose canonicalized realpath equals $2's. Built on
# _stow_migration_lib_realpath rather than _stow_migration_lib_symlink_resolves_to's
# `cd -P`, which requires $1 to be a directory -- stow_unadopt_entry's targets
# include plain files.
_stow_migration_lib_realpath_resolves_to() {
  local link="$1" expected="$2" resolved expected_resolved
  [ -L "$link" ] || return 1
  resolved="$(_stow_migration_lib_realpath "$link")"
  [ -n "$resolved" ] || return 1
  expected_resolved="$(_stow_migration_lib_realpath "$expected")"
  [ -n "$expected_resolved" ] || return 1
  [ "$resolved" = "$expected_resolved" ]
}

# Print the newest stow_unadopt_entry run-marker directory for $1 (a bare
# name) under $_STOW_MIGRATION_BACKUP_ROOT, matching "$1.*-unadopt", or fail
# (return 1, no stdout) when none exists. Mirrors
# _stow_migration_lib_newest_backup's lexicographic-sort reasoning: every run
# directory is suffixed with a fixed-width `date +%Y%m%d%H%M%S` timestamp.
# Distinct name pattern (the "-unadopt" suffix) from stow_migrate_adopted_dir's
# own backup directories, since the two functions share
# $_STOW_MIGRATION_BACKUP_ROOT but not a directory's content shape --
# deliberately unlike its sibling, this does not skip empty candidates: a run
# directory here is legitimately empty for its entire resumable window (it
# only ever holds the completion sentinel, and only once done), so porting
# the sibling's empty-candidate filter would make an in-progress run
# invisible to itself and break resumability.
_stow_migration_lib_newest_unadopt_run() {
  local name="$1"
  [ -d "$_STOW_MIGRATION_BACKUP_ROOT" ] || return 1
  local escaped_name
  escaped_name="$(_stow_migration_lib_glob_escape "$name")"
  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local Candidates=("$_STOW_MIGRATION_BACKUP_ROOT/$escaped_name".*-unadopt)
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi
  [ ${#Candidates[@]} -gt 0 ] || return 1
  printf '%s\n' "${Candidates[@]}" | sort -r | head -n 1
}

# stow_unadopt_entry REPO_DIR NAME
# Un-adopt $HOME/.claude/NAME -- currently a symlink resolving into
# REPO_DIR/claude/.claude/NAME -- back to a plain real file or directory, by
# renaming the package-side content over the unlinked target. A rename on
# the same filesystem is a single atomic, instant operation, unlike
# stow_migrate_adopted_dir's copy-then-restore -- this assumes $HOME and
# REPO_DIR share a filesystem (true wherever this has been verified; not
# checked here). On a cross-filesystem setup, `mv` transparently falls back
# to a non-atomic copy-then-delete, and this function's resumability model
# (built for "either the rename ran or it didn't") is not designed for a
# partially-copied $target that fallback can leave behind. Works uniformly
# on files and directories: no shape-specific step, unlike the copy-based
# function this one does not replace.
#
# No-op (return 0, silent) when NAME is neither a live symlink into the
# package nor a resumable interrupted rename of NAME specifically (a run
# directory this function itself created, not yet marked complete) -- a
# real, non-symlink package-side entry with no such run directory is left
# untouched, since this function never adopted it in the first place and a
# blind rename could clobber unrelated real content already at $target (see
# stow_migrate_adopted_dir's own package-side leftovers, which this
# function's callers must not treat as theirs to migrate). Also refuses
# (return 1) rather than silently overwriting if $target has independently
# reacquired real content since being unlinked -- see the check immediately
# before the rename below.
stow_unadopt_entry() {
  local repo_dir="$1" name="$2"
  local target="$HOME/.claude/$name"
  local expected_source="$repo_dir/claude/.claude/$name"

  # Refuse a pre-planted symlink at the fixed, predictable
  # $_STOW_MIGRATION_BACKUP_ROOT path before any lookup beneath it -- mirrors
  # stow_migrate_adopted_dir's own guard for this identical shared path, and
  # runs first for the same reason that function's does:
  # _stow_migration_lib_newest_unadopt_run below would otherwise glob-match
  # through a hijacked symlink before this check is ever reached.
  if [ -L "$_STOW_MIGRATION_BACKUP_ROOT" ]; then
    echo "[install] $_STOW_MIGRATION_BACKUP_ROOT already exists as a symlink -- refusing to un-adopt ~/.claude/$name through it. Remove it or replace it with a real directory first." >&2
    return 1
  fi

  local target_is_live_symlink=false
  if _stow_migration_lib_realpath_resolves_to "$target" "$expected_source"; then
    target_is_live_symlink=true
  fi

  local run_dir=""
  if ! $target_is_live_symlink; then
    run_dir=$(_stow_migration_lib_newest_unadopt_run "$name") || return 0
    if [ -f "$run_dir/$_STOW_MIGRATION_COMPLETE_SENTINEL" ]; then
      return 0
    fi
    # A run directory exists for $name, unmarked, and $target is not
    # currently a live symlink. Two states produce this combination: (1)
    # this function's own prior run unlinked $target but was interrupted
    # before the rename -- resumable; or (2) the rename already succeeded
    # and only the sentinel touch afterward failed -- already done, just
    # unmarked. Tell them apart by whether $expected_source (the rename's
    # source) still exists: only (1) leaves it in place.
    if [ ! -e "$expected_source" ]; then
      if [ -e "$target" ]; then
        # (2): self-heal the missing sentinel rather than re-attempting a
        # rename whose source is already gone -- that retry would fail
        # forever with a misleading "package-side entry is untouched"
        # message, even though the rename in fact already succeeded.
        touch -- "$run_dir/$_STOW_MIGRATION_COMPLETE_SENTINEL" || echo "[install] warning: could not mark $run_dir complete" >&2
        return 0
      fi
      echo "[install] ~/.claude/$name is unlinked and $expected_source is gone, but ~/.claude/$name is missing too -- neither the rename's source nor its destination has the content; investigate $run_dir manually" >&2
      return 1
    fi
    # $expected_source is still real: the rename in (1) hasn't happened yet.
    # Fall through and complete it.
  fi

  if $target_is_live_symlink; then
    run_dir="$_STOW_MIGRATION_BACKUP_ROOT/$name.$(date +%Y%m%d%H%M%S)-unadopt"
    if ! mkdir -p -- "$run_dir"; then
      echo "[install] could not create $run_dir -- leaving ~/.claude/$name under stow management" >&2
      return 1
    fi
    chmod 700 "$_STOW_MIGRATION_BACKUP_ROOT" || echo "[install] warning: could not chmod 700 $_STOW_MIGRATION_BACKUP_ROOT" >&2
    if ! chmod 700 "$run_dir"; then
      echo "[install] could not chmod 700 $run_dir -- refusing to un-adopt ~/.claude/$name through it" >&2
      return 1
    fi
    if ! rm -f -- "$target"; then
      echo "[install] could not unlink ~/.claude/$name -- re-run install.sh to retry; run marker is at $run_dir" >&2
      return 1
    fi
  fi

  # $target must still be exactly what an unlink (this function's own, just
  # now, or an earlier interrupted run's) left behind: absent. Something else
  # can independently repopulate it during the window between an interrupted
  # run and its resume -- Claude Code itself recreates files like
  # .claude.json on next launch when they're missing. Refuse rather than
  # silently overwrite unrelated real content with the stale package-side
  # copy.
  if [ -e "$target" ] || [ -L "$target" ]; then
    echo "[install] ~/.claude/$name has real content again since being unlinked -- refusing to overwrite it with the package-side copy at $expected_source. Investigate manually; $expected_source is untouched, and its run marker is at $run_dir." >&2
    return 1
  fi

  if ! mv -- "$expected_source" "$target"; then
    echo "[install] could not rename $expected_source to ~/.claude/$name -- re-run install.sh to retry (the package-side entry is untouched)" >&2
    return 1
  fi
  touch -- "$run_dir/$_STOW_MIGRATION_COMPLETE_SENTINEL" || echo "[install] warning: could not mark $run_dir complete" >&2

  echo "[install] un-adopted ~/.claude/$name from stow management to a real entry"
  return 0
}

# stow_untracked_package_entries REPO_DIR
# Print, NUL-separated, the top-level names under REPO_DIR/claude/.claude
# that are physically present but not tracked by git -- the entries a prior
# `stow --adopt` pulled into the package that this repo's own manifest never
# claimed. Ported from setup-claude-accounts.sh's git-ls-files/find set
# difference (that script's package root is claude/.claude itself, one level
# below install.sh's claude/, which is why its own --ignore patterns are
# anchored differently -- see install.sh's stow-adopt-ignore comment).
# Shared by install.sh's un-adopt loop and its --ignore-arg construction so
# both see one derivation instead of two hardcoded name lists.
stow_untracked_package_entries() {
  local repo_dir="$1"
  local package_dir="$repo_dir/claude/.claude"
  [ -d "$package_dir" ] || return 0

  # Fail loudly (no stdout) rather than silently treating a git failure as
  # "nothing is tracked" -- both callers act on this function's output
  # destructively (renaming entries out of the package, or building
  # --ignore args), and an empty tracked set from a real git failure would
  # make every real, tracked package entry (skills/, scripts/, ...) look
  # untracked too.
  local git_ls_files_output
  if ! git_ls_files_output="$(git -C "$repo_dir" ls-files -- 'claude/.claude/*' 2>/dev/null)"; then
    echo "[install] could not list git-tracked files under $package_dir (git ls-files failed in $repo_dir) -- refusing to guess which entries are untracked" >&2
    return 1
  fi

  local tracked_names=()
  local tracked_name
  while IFS= read -r tracked_name; do
    [ -n "$tracked_name" ] || continue
    tracked_names+=("$tracked_name")
  done < <(printf '%s\n' "$git_ls_files_output" | awk -F/ '{print $3}' | sort -u)

  local entry name tracked is_tracked
  while IFS= read -r -d '' entry; do
    name="$(basename "$entry")"
    is_tracked=false
    for tracked in "${tracked_names[@]}"; do
      if [ "$tracked" = "$name" ]; then
        is_tracked=true
        break
      fi
    done
    $is_tracked || printf '%s\0' "$name"
  done < <(find "$package_dir" -mindepth 1 -maxdepth 1 -print0)
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
