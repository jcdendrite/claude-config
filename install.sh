#!/bin/bash
# Mutates $HOME (stow symlinks) and this repo's tracked settings (marketplace
# registration) -- never `source`/`.` this file; to exercise one block in
# isolation, extract it via its INSTALL_TEST_FIXTURE markers instead (see
# claude/.claude/hooks/tests/test_install_sh_python_floor.py for the pattern).
set -e

echo "=== claude-config Setup ==="

# INSTALL_TEST_FIXTURE: presence-check — start
missing=()
for cmd in stow git gh jq sha256sum claude python3; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing dependencies: ${missing[*]}"
  echo "Install them via your system package manager, then re-run."
  exit 1
fi
# INSTALL_TEST_FIXTURE: presence-check — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under a stubbed `python3` on PATH. Keep both
# markers on their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: python-floor-check — start
# See parse-manifest-dependencies.py's module docstring for why 3.11.
# Hooks resolve python3 from PATH at execution time, so this is checked
# here too -- a below-floor interpreter must fail loudly at install
# rather than degrade a hook silently later.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "python3 on PATH does not meet the Python >= 3.11 this repo requires."
  echo "Stock macOS /usr/bin/python3, Ubuntu 22.04 LTS, and Debian 11 all ship an older python3 by default."
  echo "Install a newer interpreter (e.g. via Homebrew or pyenv on macOS; your distro's python3.11+ package or pyenv on Linux) and make sure it resolves first on PATH, then re-run."
  exit 1
fi
# INSTALL_TEST_FIXTURE: python-floor-check — end

# pwd -P (not pwd) canonicalizes away any symlink in the invocation path, so
# REPO_DIR matches the canonicalized form the marketplace-registration check
# below compares against — otherwise a symlink-adjacent invocation of this
# script could make REPO_DIR byte-differ from the marketplace's recorded
# .path even though they name the same directory, thrashing (remove+re-add)
# the registration on every run.
REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$REPO_DIR"
mkdir -p "$HOME/.local/bin"
# Both target directories are created before stow runs so stow links their
# contents entry by entry. Without this, stow tree-folds a target that does
# not yet exist into a single symlink pointing back into this checkout — which
# would put every file Claude Code writes at runtime inside the git clone.
mkdir -p "$HOME/.claude"

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME with a stubbed `pgrep`
# on PATH. Keep both markers on their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: session-concurrency-check — start
# Computed once, before any migration below touches ~/.claude: the CLI
# writes projects/, history.jsonl, .claude.json, and this migration's own
# plans/handoffs/briefs targets continuously while a session is live, so
# every migration that mutates those paths gates on this one check rather
# than each re-checking independently. Deliberately outside every OTHER
# INSTALL_TEST_FIXTURE block below: the hook test suite extracts and runs
# those in isolation, on this very machine, where a real `claude` process is
# almost always running (this session's own) -- a pgrep check inside one of
# those blocks would see that real process and wrongly skip the migration
# under test. Left unset (empty, not exported) when a test runs one of those
# other blocks standalone, which never sets this, so an unset value means
# "proceed" and matches those tests' existing fixtures; real installs always
# compute it fresh right here, before any block runs.
CLAUDE_SESSION_MAY_BE_ACTIVE=""
if ! command -v pgrep >/dev/null 2>&1; then
  CLAUDE_SESSION_MAY_BE_ACTIVE="pgrep not found -- cannot verify no Claude Code session is running"
elif pgrep -u "$(id -u)" -x claude >/dev/null 2>&1; then
  CLAUDE_SESSION_MAY_BE_ACTIVE="a Claude Code session is currently running for this user"
fi

# True iff a `claude` process is running for this user right now. Used to
# re-check immediately before each entry a migration loop below mutates, not
# only once before the whole loop -- $CLAUDE_SESSION_MAY_BE_ACTIVE above
# already gates entry into any such loop on pgrep being available at all, so
# this doesn't re-handle the pgrep-missing case.
_claude_session_is_active_now() {
  pgrep -u "$(id -u)" -x claude >/dev/null 2>&1
}
# INSTALL_TEST_FIXTURE: session-concurrency-check — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: stow-adoption-migration — start
# plans/, handoffs/, briefs/ are the only stow-adopted paths the Write/Edit
# tools target from an arbitrary skill invocation (handoff/brief writes,
# plan-mode's default plan path) — migrate them off stow management first so
# a symlink resolving into this checkout can no longer collide with worktree
# enforcement, then tell stow to leave them alone going forward.
#
# Sourced from its own known repo-relative path, not ~/.claude/scripts/...,
# which doesn't exist yet at this point in a fresh install (migration runs
# before stow does). Not relocate-claude-config.sh itself — sourcing that
# would turn on set -u/pipefail for the rest of install.sh, fatal on an
# empty array under macOS system bash 3.2.
# shellcheck source=claude/.claude/scripts/_stow_migration_lib.sh
. "$REPO_DIR/claude/.claude/scripts/_stow_migration_lib.sh"

STOW_MIGRATION_FAILURES=()
if [ -n "$CLAUDE_SESSION_MAY_BE_ACTIVE" ]; then
  echo "[install] warning: $CLAUDE_SESSION_MAY_BE_ACTIVE -- skipping the plans/handoffs/briefs migration below to avoid racing its writes to ~/.claude. Quit every Claude Code session and re-run install.sh." >&2
else
  for name in plans handoffs briefs; do
    # Re-checked per entry, not only once above: narrows the window a
    # session starting mid-loop has to race this specific entry's
    # unlink/copy, down from the whole loop to roughly one entry's worth.
    if _claude_session_is_active_now; then
      echo "[install] warning: a Claude Code session started during this migration -- stopping before '$name' and the rest; re-run install.sh once no session is running." >&2
      break
    fi
    if ! stow_migrate_adopted_dir "$REPO_DIR" "$name"; then
      STOW_MIGRATION_FAILURES+=("$name")
    fi
    # Repairs a prior run's per-entry adoption regardless of the migration
    # outcome above -- see stow_repair_nested_adoption's own comment.
    stow_repair_nested_adoption "$REPO_DIR" "$name"
  done
  if [ ${#STOW_MIGRATION_FAILURES[@]} -gt 0 ]; then
    echo "[install] warning: could not migrate the following off stow management: ${STOW_MIGRATION_FAILURES[*]} — re-run install.sh to retry; stow keeps managing them as symlinks in the meantime" >&2
  fi
fi
# INSTALL_TEST_FIXTURE: stow-adoption-migration — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: stale-migration-copy-cleanup — start
# Caller must check `[ -t 0 ]` before invoking this -- it has no TTY guard of
# its own, the same contract this file's own later _prompt_sentinel_opt_in
# uses. Confined to rm -rf-ing only a path already confirmed real and
# non-symlink by _report_and_clean_stale_migration_copy below.
_prompt_delete_stale_migration_copy() {
  local stale_path="$1" target="$2"
  local answer
  read -r -p "  delete $stale_path now that $target holds the real copy? [y/N] " answer || answer=""
  case "$answer" in
    [Yy]*) rm -rf -- "$stale_path" && echo "  → deleted $stale_path" ;;
    *) echo "  ✓ leaving $stale_path in place" ;;
  esac
}

# stow_migrate_adopted_dir above backs up ~/.claude/<name>'s content before
# restoring it as a real directory, but never removes the package-side
# original it copied from -- that original still physically sits inside this
# checkout at claude/.claude/<name>, gitignored but otherwise unprotected
# from a `git add -f`, `git clean -x`, or an archived checkout (the same
# exposure the un-adopt loop below closes for everything else). Report each
# one and offer to delete it now that ~/.claude/<name> holds its own real,
# *complete* copy -- stow_migration_is_complete, not bare existence, decides
# that: a real but only partially-restored $target (an interrupted
# stow_migrate_adopted_dir run) is indistinguishable from a complete one by
# existence alone, and offering deletion in that state would delete the only
# remaining copy of whatever the partial restore left out.
_report_and_clean_stale_migration_copy() {
  local repo_dir="$1" name="$2"
  local stale_path="$repo_dir/claude/.claude/$name"
  local target="$HOME/.claude/$name"
  [ -e "$stale_path" ] && [ ! -L "$stale_path" ] || return 0
  stow_migration_is_complete "$name" || return 0
  echo "[install] $stale_path still holds a real copy left behind by migrating ~/.claude/$name off stow management." >&2
  if [ -t 0 ]; then
    _prompt_delete_stale_migration_copy "$stale_path" "$target"
  else
    echo "[install]   not an interactive terminal -- leaving it in place; re-run install.sh from a terminal to be offered deletion." >&2
  fi
}
for name in plans handoffs briefs; do
  _report_and_clean_stale_migration_copy "$REPO_DIR" "$name"
done
# INSTALL_TEST_FIXTURE: stale-migration-copy-cleanup — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: un-adopt-loop — start
# Every other name a prior `stow --adopt` pulled into claude/.claude/ (session
# transcripts, .claude.json, plugin caches, review markers -- see this repo's
# README "Claude multi-account setup") gets un-adopted the same way, by
# rename rather than copy: there are too many such names, and too much
# content behind them, to hardcode a list the way plans/handoffs/briefs are
# above. Gates on the same $CLAUDE_SESSION_MAY_BE_ACTIVE check the
# plans/handoffs/briefs migration above does, for the identical reason: a
# concurrent write racing stow_unadopt_entry's own unlink-then-rename on the
# same path could silently clobber either side.
#
# stow_untracked_package_entries's NUL-delimited output can't round-trip
# through a plain `$(...)` capture (bash mangles embedded NULs), and its exit
# status -- distinguishing "git failed" from "genuinely nothing untracked" --
# can't be recovered through a `while read -r -d '' ... done < <(...)`
# process substitution. A temp file gets both: the function's own exit
# status via a normal `if cmd > file; then`, and NUL-safe iteration via
# `read -r -d ''` against the file afterward.
if [ -n "$CLAUDE_SESSION_MAY_BE_ACTIVE" ]; then
  echo "[install] warning: $CLAUDE_SESSION_MAY_BE_ACTIVE -- skipping the un-adopt loop below to avoid racing its writes to ~/.claude. Quit every Claude Code session and re-run install.sh." >&2
elif ! untracked_entries_file="$(mktemp)"; then
  echo "[install] warning: could not create a temp file -- skipping the un-adopt loop below" >&2
elif ! stow_untracked_package_entries "$REPO_DIR" > "$untracked_entries_file"; then
  echo "[install] warning: could not determine untracked claude/.claude/ entries -- skipping the un-adopt loop below" >&2
  rm -f -- "$untracked_entries_file"
else
  STOW_UNADOPT_FAILURES=()
  while IFS= read -r -d '' name; do
    [ -n "$name" ] || continue
    # Re-checked per entry, not only once above: narrows the window a
    # session starting mid-loop has to race this specific entry's
    # unlink/rename, down from the whole loop to roughly one entry's worth.
    if _claude_session_is_active_now; then
      echo "[install] warning: a Claude Code session started during the un-adopt loop -- stopping before '$name' and the rest; re-run install.sh once no session is running." >&2
      break
    fi
    if ! stow_unadopt_entry "$REPO_DIR" "$name"; then
      STOW_UNADOPT_FAILURES+=("$name")
    fi
  done < "$untracked_entries_file"
  rm -f -- "$untracked_entries_file"
  if [ ${#STOW_UNADOPT_FAILURES[@]} -gt 0 ]; then
    echo "[install] warning: could not un-adopt the following back to ~/.claude: ${STOW_UNADOPT_FAILURES[*]} — see the per-entry messages above; some can be fixed by re-running install.sh, others (e.g. neither side holding the content) need manual investigation first. Stow keeps ignoring them as real package-side content in the meantime." >&2
  fi
fi
# INSTALL_TEST_FIXTURE: un-adopt-loop — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: skills-package-migration — start
# An existing install's ~/.claude/skills symlink may still resolve into
# claude/.claude/skills, this repo's superseded skills location, leaving it
# dangling. Removing it here lets the stow call below lay down the new
# symlink at the same target instead of refusing over a pre-existing
# conflicting link.
# No CLAUDE_SESSION_MAY_BE_ACTIVE gate: a link resolving into the old path is
# already dangling by the time this runs. A live session has therefore
# already lost its skills regardless, so removing it can only help.
#
# Set (=1) whenever real, non-stow-managed content sits at ~/.claude/skills.
# The stow-adopt-ignore block's manifest loop below checks this and skips
# stowing the claude-skills row, so stow never unfolds into that real
# content and merges its symlinks alongside it.
skills_migration_blocks_adopt=""
if _stow_migration_lib_realpath_resolves_to "$HOME/.claude/skills" "$REPO_DIR/claude/.claude/skills"; then
  if ! rm -- "$HOME/.claude/skills"; then
    echo "[install] could not remove the stale ~/.claude/skills symlink into the old claude/.claude/skills location -- investigate and re-run install.sh; the claude-skills package will not be stowed until this is resolved" >&2
    skills_migration_blocks_adopt=1
  else
    echo "[install] removed ~/.claude/skills, a stale symlink into the old claude/.claude/skills location" >&2
  fi
elif _stow_migration_lib_realpath_resolves_to "$HOME/.claude/skills" "$REPO_DIR/claude-skills/skills"; then
  echo "[install] ~/.claude/skills already resolves to claude-skills/skills -- nothing to migrate" >&2
elif [ -L "$HOME/.claude/skills" ]; then
  echo "[install] ~/.claude/skills is a symlink pointing somewhere other than claude/.claude/skills -- leaving it in place; if it's stale, remove it and re-run install.sh" >&2
  skills_migration_blocks_adopt=1
elif [ -d "$HOME/.claude/skills" ]; then
  echo "[install] ~/.claude/skills is a real directory, not the stow-managed symlink this install expects -- move or remove it, then re-run install.sh" >&2
  skills_migration_blocks_adopt=1
elif [ -e "$HOME/.claude/skills" ]; then
  echo "[install] ~/.claude/skills exists but is neither a symlink nor a directory -- investigate and remove it before re-running install.sh" >&2
  skills_migration_blocks_adopt=1
else
  echo "[install] ~/.claude/skills does not exist yet -- nothing to migrate" >&2
fi

# Checked unconditionally on every run, not only inside the branch above
# that found a stale old-path symlink to remove.
# Mirrors _report_and_clean_stale_migration_copy's unconditional per-run
# re-check for plans/handoffs/briefs above.
# Non-empty, not just -e: an empty leftover directory holds nothing worth
# warning about.
if [ -n "$(ls -A -- "$REPO_DIR/claude/.claude/skills" 2>/dev/null)" ]; then
  echo "[install] warning: $REPO_DIR/claude/.claude/skills still holds real content -- this repo's skills package moved to claude-skills/skills, so nothing here reads that old location any more. Move or delete it manually." >&2
fi
# INSTALL_TEST_FIXTURE: skills-package-migration — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them against a real `stow` binary. Keep both markers
# on their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: stow-adopt-ignore — start
# stow_untracked_package_entries and _stow_migration_lib_regex_escape below
# come from _stow_migration_lib.sh, already sourced above; the hook test
# suite's extraction of this block sources it again itself rather than
# repeating that line here, which confuses shellcheck's forward-reference
# analysis for the calls above.
# --ignore values are anchored Perl regexes matched against each item's path
# relative to the package root (claude/), not its basename — '^plans$' never
# matches '.claude/plans' and silently fails to protect it once '.claude'
# itself is unfolded (forced real by the mkdir -p above). No --adopt here any
# more: without it, stow refuses the whole invocation outright if a tracked
# package path collides with a real, non-symlink file at the target, so these
# --ignore args exist only to keep a not-yet-migrated (or declined-deletion)
# entry from aborting stow for every other package entry too. A git failure
# here degrades to an empty --ignore list rather than skipping the stow call
# outright: at worst stow then refuses (a loud, safe failure), never a
# silent one.
#
# plans/handoffs/briefs seeded explicitly, not derived from
# stow_untracked_package_entries: that function deliberately never reports
# these three (they have their own dedicated migration path above, and must
# stay off the generic un-adopt loop's reach), but a declined-deletion or
# not-yet-migrated package-side leftover for one of them still needs the
# same --ignore protection every other entry gets here, or stow would walk
# into it and adopt it file-by-file.
stow_ignore_args=(--ignore='^\.claude/plans$' --ignore='^\.claude/handoffs$' --ignore='^\.claude/briefs$')
if untracked_entries_file="$(mktemp)" && stow_untracked_package_entries "$REPO_DIR" > "$untracked_entries_file"; then
  while IFS= read -r -d '' name; do
    [ -n "$name" ] || continue
    # Checked via `if ! var=...; then` rather than inlined into the
    # `+=(...)` below: a compound-assignment simple command is not an
    # if/&&/||-condition, so under `set -e` a failing command substitution
    # inside it would abort the whole script instead of just skipping this
    # one --ignore arg.
    if ! escaped_name="$(_stow_migration_lib_regex_escape "$name")"; then
      echo "[install] warning: could not regex-escape '$name' for a stow --ignore pattern -- proceeding without an --ignore for it; stow will refuse outright if it's still real, unmigrated content" >&2
      continue
    fi
    stow_ignore_args+=(--ignore="^\\.claude/${escaped_name}\$")
  done < "$untracked_entries_file"
else
  echo "[install] warning: could not determine untracked claude/.claude/ entries -- proceeding with no --ignore args; stow will refuse outright if any are still real, unmigrated content" >&2
fi
rm -f -- "$untracked_entries_file"

# stow-packages.sh is the single source of truth for the package list (see
# its own header comment).
# stow_ignore_args above is attached only to the row named "claude", matched
# by field rather than position so a package stow-packages.sh appends later
# doesn't inherit it by accident.
stow_packages_file="$(mktemp)"
# This file's sole `trap ... EXIT` -- a second one elsewhere would silently
# overwrite this cleanup instead of composing with it (see
# claude/.claude/rules/shell-script-conventions.md).
trap 'rm -f -- "$stow_packages_file"' EXIT
if ! "$REPO_DIR/claude/.claude/scripts/stow-packages.sh" > "$stow_packages_file"; then
  echo "[install] error: could not enumerate stow packages via stow-packages.sh" >&2
  exit 1
fi
claude_package_seen=""
while IFS=$'\t' read -r package_dir stow_target_rel; do
  [ -n "$package_dir" ] || continue
  stow_target="$HOME"
  [ "$stow_target_rel" != "." ] && stow_target="$HOME/$stow_target_rel"
  if [ "$package_dir" = "claude" ]; then
    claude_package_seen=1
    stow -v "${stow_ignore_args[@]}" -t "$stow_target" "$package_dir"
  elif [ "$package_dir" = "claude-skills" ] && [ -n "$skills_migration_blocks_adopt" ]; then
    echo "[install] skipping stow of claude-skills -- ~/.claude/skills is real, non-stow-managed content (see the warning above); move or remove it, then re-run install.sh" >&2
  else
    stow -v -t "$stow_target" "$package_dir"
  fi
done < "$stow_packages_file"
if [ -z "$claude_package_seen" ]; then
  echo "[install] error: stow-packages.sh's manifest has no row named 'claude' -- refusing to continue without stowing the mandatory package" >&2
  exit 1
fi
if [ -n "$skills_migration_blocks_adopt" ]; then
  echo "[install] error: claude-skills was not stowed -- ~/.claude/skills is real, non-stow-managed content (see the warning printed above); move or remove it, then re-run install.sh" >&2
  exit 1
fi
# INSTALL_TEST_FIXTURE: stow-adopt-ignore — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: repo-relocation-manifest — start
# Record this checkout's location so relocate-claude-config can find it
# later without depending on a live ~/.claude symlink (which its own repair
# mode may need to work around). Single-line, idempotent overwrite.
printf '%s\n' "$REPO_DIR" > "$HOME/.claude-config-source"

# Real file copy (not stow) — relocate-claude-config's whole purpose is to
# keep working when the exact symlink chain it repairs has already failed,
# so it cannot itself be a stow-managed symlink into this checkout.
install -m 755 -- "$REPO_DIR/claude/.claude/scripts/relocate-claude-config.sh" "$HOME/.local/bin/relocate-claude-config"
# INSTALL_TEST_FIXTURE: repo-relocation-manifest — end

# Harden ~/.claude and ~/.claude.json against other local accounts. $HOME is
# commonly 755 (set once at account creation, not by umask), and under the
# widespread umask 0002 anything created beneath it lands group-writable —
# ~/.claude at 775. Claude Code narrows only a few paths of its own
# (.credentials.json, projects/, sessions/, ide/, daemon/), leaving
# file-history/, plans/, shell-snapshots/ and the rest at that default.
# chmod 700 on ~/.claude is a single choke point: clearing the search bit
# blocks path resolution into every subdirectory at once, so no per-directory
# recipe is needed and directories added by future releases are covered too.
#
# ~/.claude.json needs its own chmod because it sits at $HOME level, outside
# ~/.claude/ — it indexes every project directory ever opened. Current Claude
# Code releases create it 0600 and preserve its mode across their
# temp-file-plus-rename rewrites, so this is a one-time repair of files left
# at 664 by older releases, and the repair holds.
#
# chmod is skipped when ~/.claude is a symlink: chmod dereferences, so a
# tree-folded ~/.claude left by an earlier install would narrow this
# checkout's own directory rather than a private one. Failures only warn, so
# hardening never blocks the plugin registration below.
#
# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: continuity-hardening — start
if [ -L "$HOME/.claude" ]; then
  echo "[install] warning: ~/.claude is a symlink to $(readlink "$HOME/.claude") — skipping chmod 700 so the link target is not narrowed. Claude Code is storing its state inside that path; replace the symlink with a real directory to get owner-only permissions." >&2
elif [ -d "$HOME/.claude" ]; then
  chmod 700 "$HOME/.claude" || echo "[install] warning: could not chmod 700 ~/.claude" >&2
fi
if [ -f "$HOME/.claude.json" ]; then
  chmod 600 "$HOME/.claude.json" || echo "[install] warning: could not chmod 600 ~/.claude.json" >&2
fi
# INSTALL_TEST_FIXTURE: continuity-hardening — end

# Sourced from its own known repo-relative path, not ~/.claude/hooks/...,
# which doesn't exist yet at this point in a fresh install (this repo isn't
# stowed yet). Deletes the inline $CLAUDE_CONFIG_DIR resolver this script
# used to carry -- _lib_config_dir is now the single bash definition of
# config-dir resolution, shared with every hook via _lib.sh.
# shellcheck source=claude/.claude/hooks/_config.sh
. "$REPO_DIR/claude/.claude/hooks/_config.sh"

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: machine-level-opt-ins — start
# Caller must check `[ -t 0 ]` before invoking this — it has no TTY guard of
# its own and will block on `read` forever against an open, never-closed
# stdin. configure_machine_level_opt_ins below is the only sanctioned caller.
# Writes via _config_set to the resolved config dir for every promptable
# key. KEY is a config-keys.psv key name, not a caller-supplied path, so no
# path-confinement check is needed.
_prompt_sentinel_opt_in() {
  local key="$1" human_name="$2" description="$3" answer current
  current=$(_config_value "$key") || current="false"
  if [ "$current" != "false" ]; then
    printf '%s is currently ENABLED on this machine (%s).\n' "$human_name" "$current"
    printf '%s\n' "$description"
    # `|| answer=""` treats read's EOF (e.g. Ctrl-D) the same as a bare
    # Enter (no-op) instead of letting set -e abort the rest of install.sh
    # — including marketplace/plugin registration below — with no diagnostic.
    read -r -p "Keep it enabled? [Y/n] " answer || answer=""
    case "$answer" in
      [Nn]*)
        if _config_set "$key" false; then
          # config-dir-or-home resolution can still resolve ENABLED via
          # $HOME/.claude's own legacy file even after this write sets an
          # explicit false row at the resolved config dir -- report the
          # actual resolved state.
          # Checks the legacy file directly with `[ -f ]` rather than calling
          # `_config_key_source`, which lives in a different
          # `INSTALL_TEST_FIXTURE` span than this function's own and would be
          # unresolved when that span is extracted alone.
          local resolved
          resolved=$(_config_value "$key") || resolved="false"
          if [ "$resolved" = "false" ]; then
            echo "  → disabled"
          else
            local home_dir legacy_filename legacy_path
            home_dir="${HOME%/}/.claude"
            legacy_filename=$(_config_schema_field "$key" legacy-filename)
            legacy_path="$home_dir/$legacy_filename"
            if [ -f "$legacy_path" ]; then
              printf '  ! wrote %s = false, but %s still resolves ENABLED (%s) -- %s still exists and forces it on via the config-dir-or-home union; remove that file to actually disable %s.\n' \
                "$key" "$human_name" "$resolved" "$legacy_path" "$human_name"
            else
              printf '  ! wrote %s = false, but %s still resolves ENABLED (%s) -- check %s/claude-config.toml for a disagreeing row.\n' \
                "$key" "$human_name" "$resolved" "$home_dir"
            fi
          fi
        else
          echo "  ! could not write $key -- see claude-config.toml" >&2
        fi
        ;;
      *) echo "  ✓ keeping $human_name enabled" ;;
    esac
  else
    printf '%s is currently disabled on this machine.\n' "$human_name"
    printf '%s\n' "$description"
    read -r -p "Enable it now? [y/N] " answer || answer=""
    case "$answer" in
      [Yy]*)
        if _config_set "$key" true; then
          echo "  → enabled"
        else
          echo "  ! could not write $key -- see claude-config.toml" >&2
        fi
        ;;
      *) echo "  ✓ leaving $human_name disabled" ;;
    esac
  fi
}

# Drives the prompt from config-keys.psv itself (sourced via _config.sh
# above): a key is promptable iff its prompt-description column is
# non-empty, so adding a new promptable key needs a schema edit here, not a
# second array. Reads config-keys.psv into an array first, then iterates
# with `for`/here-string parsing (never `while read ... < file`) -- the
# loop body below calls `read -r -p` via _prompt_sentinel_opt_in, and a
# `while read` loop redirected from the schema file would steal that same
# fd 0 away from the terminal for the loop's whole duration.
configure_machine_level_opt_ins() {
  if [ ! -t 0 ]; then
    echo ""
    echo "=== Machine-level opt-ins ==="
    echo "  (skipped — not an interactive terminal; existing settings are unchanged)"
    return 0
  fi
  echo ""
  echo "=== Machine-level opt-ins ==="
  local -a schema_lines=()
  local line
  while IFS= read -r line; do
    schema_lines+=("$line")
  done < "$_CONFIG_SCHEMA_FILE"
  local key type default resolution legacy_probe legacy_import legacy_filename \
    legacy_polarity human_name docs_anchor prompt_description
  for line in "${schema_lines[@]}"; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    IFS='|' read -r key type default resolution legacy_probe legacy_import legacy_filename \
      legacy_polarity human_name docs_anchor prompt_description <<< "$line"
    [ -n "$prompt_description" ] || continue
    _prompt_sentinel_opt_in "$key" "$human_name" "$prompt_description"
  done
}
# INSTALL_TEST_FIXTURE: machine-level-opt-ins — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: sentinel-inventory — start
# The four repo-scope committed markers only -- every machine/account-scope
# row moved to config-keys.psv (see the Context section of
# .claude/plans/sentinel-config-migration.md for why: a committed file is
# the git-native idiom for a repo-level toggle, and moving these into the
# per-machine state file would break exactly the property they exist for).
# Flat, pipe-delimited rows rather than an associative array: the system
# bash on macOS (and this machine's default) is 3.2, which has no
# `declare -A`. Schema (no surrounding whitespace around any `|` --
# IFS='|' read -r would otherwise bake leading/trailing spaces into every
# field): path-template|human-name|docs-anchor. Every row's default state
# is "disabled" (presence-enables) -- no row here carries the opt-out
# polarity account-scope rows used to need.
REPO_MARKER_INVENTORY=(
  ".claude/worktree-required|Worktree enforcement (committed, this repo)|README.md § Worktree enforcement"
  ".claude/worktree-optout|Worktree enforcement opt-out (this repo)|README.md § Worktree enforcement"
  ".claude/autonomous-shipping-optout|Autonomous-shipping opt-out (this repo)|README.md § Autonomous shipping"
  ".claude/session-title-disabled|Branch-based session-title suppression (this repo)|docs/hooks.md § Utility hooks"
)

# Whether $1 (a zero-based REPO_MARKER_INVENTORY index) was prompted by
# configure_machine_level_opt_ins during this run -- always false today,
# since no repo-scope row is ever machine-promptable, kept for the same
# reason report_sentinel_inventory's schema-key loop below needs no
# equivalent: a repo marker's CTA is never suppressed by anything this
# script prompts about.
_sentinel_index_prompted_this_run() {
  case " ${SENTINEL_INVENTORY_PROMPTED_INDICES:-} " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Prints "ENABLED" when $1 exists, else "disabled" -- the same two labels
# this reporter's own CTA text already uses.
_sentinel_state_label() {
  if [ -f "$1" ]; then
    printf 'ENABLED'
  else
    printf 'disabled'
  fi
}

_report_repo_sentinel() {
  local sentinel_index="$1" path_template="$2" human_name="$3" docs_anchor="$4"
  local repo_path="$REPO_DIR/$path_template"
  local state
  state="$(_sentinel_state_label "$repo_path")"
  printf '  %s: %s (%s)\n' "$human_name" "$state" "$path_template"
  printf '    docs: %s\n' "$docs_anchor"
  if [ "$state" = "disabled" ] && ! _sentinel_index_prompted_this_run "$sentinel_index"; then
    printf '    → to enable: touch %s\n' "$path_template"
  fi
}

# KEY's source this run: "config file" when claude-config.toml itself
# carries a conforming row, else "legacy file" when its legacy sentinel is
# present, else "default" -- install.sh's own report-only mirror of
# _config_location_value's precedence (state-file row authoritative;
# legacy file consulted only when the key is entirely absent from the
# state file), not a second implementation of that precedence.
_config_key_source() {
  local key="$1" dir="$2"
  local state_file="$dir/$_CONFIG_STATE_FILENAME"
  if _config_read_key_from_file "$key" "$state_file" >/dev/null; then
    printf 'config file'
    return 0
  fi
  local legacy_filename
  legacy_filename=$(_config_schema_field "$key" legacy-filename)
  if [ -f "$dir/$legacy_filename" ]; then
    printf 'legacy file'
    return 0
  fi
  printf 'default'
}

# Schema-driven, replacing the old scope=machine-promptable/machine/account
# reporters: every key's effective value now resolves through
# _config_value, so one function reports all fifteen. For a
# config-dir-or-home key (worktree_required, autonomous_shipping), also
# prints $HOME/.claude's own value/source alongside the resolved config
# dir's when the two differ -- the union means either can be the reason a
# key reads as enabled, and the old reporter's "DIVERGED" display existed
# for exactly this diagnosability.
_report_config_key() {
  local key="$1" human_name="$2" docs_anchor="$3" resolution="$4"
  local value status
  value=$(_config_value "$key") && status=0 || status=$?
  if [ "$status" -ne 0 ]; then
    # shellcheck disable=SC2016 # single-quoted deliberately — $HOME must stay
    # unexpanded here, naming the literal env var in the diagnostic message.
    printf '  %s: could not resolve (CLAUDE_CONFIG_DIR is a relative path, or $HOME is unset/empty)\n' "$human_name"
    printf '    docs: %s\n' "$docs_anchor"
    return 0
  fi
  local source
  if [ -n "$_REPORT_CONFIG_DIR" ]; then
    source=$(_config_key_source "$key" "$_REPORT_CONFIG_DIR")
  else
    # Only reachable for worktree_required, the sole key whose schema row
    # carries legacy-probe-on-resolution-failure: true -- _config_value
    # still resolved via a raw $HOME/.claude probe even though the config
    # dir itself could not be resolved at all, so there is no config-dir-side
    # state/legacy file to check here.
    source="legacy file"
  fi
  printf '  %s: %s (source: %s)\n' "$human_name" "$value" "$source"
  printf '    docs: %s\n' "$docs_anchor"
  if [ "$resolution" = "config-dir-or-home" ] && [ -n "$_REPORT_CONFIG_DIR" ] \
     && [ -n "$_REPORT_HOME_DIR" ] && [ "$_REPORT_HOME_DIR" != "${_REPORT_CONFIG_DIR%/}" ]; then
    local home_value home_source
    home_value=$(_config_location_value "$key" "$_REPORT_HOME_DIR")
    home_source=$(_config_key_source "$key" "$_REPORT_HOME_DIR")
    printf '    %s: %s (source: %s)\n' "$_REPORT_HOME_DIR" "$home_value" "$home_source"
  fi
}

# Read-only: creates and removes nothing. Reports every config-keys.psv key
# (schema order), then the four repo markers. Called after
# configure_machine_level_opt_ins so a just-prompted repo-marker row's hint
# can be suppressed -- no config key needs that suppression, since a
# hand-edited claude-config.toml row, not a raw touch/rm target, is the
# sanctioned way to change a non-promptable key.
report_sentinel_inventory() {
  echo ""
  echo "=== Opt-in sentinel inventory ==="
  _REPORT_CONFIG_DIR=$(_lib_config_dir) || _REPORT_CONFIG_DIR=""
  _REPORT_HOME_DIR=""
  [ -n "${HOME:-}" ] && _REPORT_HOME_DIR="${HOME%/}/.claude"

  local -a schema_lines=()
  local line
  while IFS= read -r line; do
    schema_lines+=("$line")
  done < "$_CONFIG_SCHEMA_FILE"
  local key type default resolution legacy_probe legacy_import legacy_filename \
    legacy_polarity human_name docs_anchor prompt_description
  for line in "${schema_lines[@]}"; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    IFS='|' read -r key type default resolution legacy_probe legacy_import legacy_filename \
      legacy_polarity human_name docs_anchor prompt_description <<< "$line"
    _report_config_key "$key" "$human_name" "$docs_anchor" "$resolution"
  done

  local sentinel_index=0 entry path_template human_name_repo docs_anchor_repo
  for entry in "${REPO_MARKER_INVENTORY[@]}"; do
    IFS='|' read -r path_template human_name_repo docs_anchor_repo <<< "$entry"
    _report_repo_sentinel "$sentinel_index" "$path_template" "$human_name_repo" "$docs_anchor_repo"
    sentinel_index=$((sentinel_index + 1))
  done
}
# INSTALL_TEST_FIXTURE: sentinel-inventory — end

# INSTALL_TEST_FIXTURE: legacy-config-migration — start
# Executed, not sourced: sourcing would turn on set -u/pipefail for the
# rest of this script, fatal on an empty array under macOS system bash 3.2
# (the same reason given above for not sourcing _stow_migration_lib.sh).
# Runs before the interactive opt-in prompts below so a freshly-imported
# legacy value is what those prompts (and the reporter) see. A failed
# migration does not abort the rest of install.sh -- matching the shape at
# this file's project-scope-plugin-install step below.
"$REPO_DIR/claude/.claude/scripts/migrate-legacy-config.sh" \
  || echo "[install] warning: legacy config migration failed, run claude/.claude/scripts/migrate-legacy-config.sh directly to retry" >&2
# INSTALL_TEST_FIXTURE: legacy-config-migration — end

configure_machine_level_opt_ins
report_sentinel_inventory

SETTINGS_FILE="$HOME/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
  "$REPO_DIR/claude/.claude/scripts/register-marketplace.sh"

  echo ""
  echo "=== Installing this repo's own project-scope plugins ==="
  PROJECT_SETTINGS_FILE="$REPO_DIR/.claude/settings.json"
  if [ -f "$PROJECT_SETTINGS_FILE" ]; then
    # INSTALL_TEST_FIXTURE: project-plugin-match — start
    # Whether $1 (a "name@marketplace" plugin id) is already installed at
    # project scope for $REPO_DIR, given $2 as a "$id\t$projectPath" TSV of
    # existing scope=="project" entries. Canonicalizes each entry's path
    # the same way ensure_local_bin_on_path's readlink -f fallback does:
    # check the assignment's own exit status, not `cmd1 || cmd2`-inside-
    # `$()`, which can concatenate BSD readlink's partial stdout with the
    # fallback on a dangling target.
    _project_plugin_already_installed() {
      local plugin_id="$1" existing_tsv="$2" entry_id entry_path entry_path_real
      while IFS=$'\t' read -r entry_id entry_path; do
        [ -z "$entry_id" ] && continue
        [ "$entry_id" != "$plugin_id" ] && continue
        if ! entry_path_real="$(readlink -f -- "$entry_path" 2>/dev/null)"; then
          entry_path_real="$entry_path"
        fi
        [ "$entry_path_real" = "$REPO_DIR" ] && return 0
      done <<< "$existing_tsv"
      return 1
    }
    # INSTALL_TEST_FIXTURE: project-plugin-match — end

    # INSTALL_TEST_FIXTURE: project-scope-plugin-install — start
    if ! existing_project_plugins="$(claude plugin list --json 2>/dev/null | jq -r '.[] | select(.scope == "project") | "\(.id)\t\(.projectPath)"')"; then
      echo "[install] warning: could not read installed project-scope plugins via 'claude plugin list --json' — proceeding as if none are installed" >&2
      existing_project_plugins=""
    fi
    if ! enabled_project_plugins="$(jq -r '.enabledPlugins // {} | to_entries[] | select(.value == true) | .key' "$PROJECT_SETTINGS_FILE")"; then
      echo "[install] warning: could not parse enabledPlugins from $PROJECT_SETTINGS_FILE — skipping project-scope plugin install" >&2
      enabled_project_plugins=""
    fi
    while read -r plugin; do
      [ -z "$plugin" ] && continue
      if [ "$plugin" = "issue-triage@claude-config" ]; then
        echo "  ! $plugin carries live gh credentials and unrestricted Bash — see its plugin.json description before running /issue-triage"
      fi
      if _project_plugin_already_installed "$plugin" "$existing_project_plugins"; then
        echo "  ✓ $plugin (already installed)"
      else
        echo "  → installing $plugin"
        claude plugin install "$plugin" -s project || \
          echo "[install] warning: failed to install $plugin at project scope" >&2
      fi
    done <<< "$enabled_project_plugins"
    # INSTALL_TEST_FIXTURE: project-scope-plugin-install — end
  fi
fi

check_private_projects_file() {
  local file="$HOME/.claude/private-projects.md"
  if [ ! -e "$file" ]; then
    echo ""
    echo "TIP: Create ~/.claude/private-projects.md and add \"@private-projects.md\""
    echo "     to ~/.claude/CLAUDE.md to enable redaction of project names you don't"
    echo "     want leaking in commits/PRs. See README section 'Private-project redaction'."
  elif ! grep -Evq '^[[:space:]]*(#|$)' "$file" 2>/dev/null; then
    echo ""
    echo "NOTE: ~/.claude/private-projects.md exists but has no project names yet"
    echo "      (only comments or blank lines) — a valid placeholder if you have"
    echo "      nothing to list. Add names as you identify projects to redact."
  fi
}

check_output_preferences_file() {
  local file="$HOME/.claude/output-preferences.md"
  if [ ! -e "$file" ]; then
    echo ""
    echo "TIP: Create ~/.claude/output-preferences.md to customize response tone,"
    echo "     formatting, and communication style. It's loaded automatically at"
    echo "     session start. See README section 'Output preferences'."
  fi
}

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: transcript-config-dirs — start
# declared_transcript_roots() (_config_dir.py) always reads
# ~/.claude/transcript-config-dirs, never a CLAUDE_CONFIG_DIR-relative path —
# a profile whose CLAUDE_CONFIG_DIR diverges from ~/.claude has no other way
# to declare itself there.
check_transcript_config_dirs() {
  local _resolved_config_dir _resolved_default_dir roots_file
  _resolved_config_dir="$(cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" 2>/dev/null && pwd -P)" || _resolved_config_dir=""
  _resolved_default_dir="$(cd "$HOME/.claude" 2>/dev/null && pwd -P)" || _resolved_default_dir=""
  roots_file="$HOME/.claude/transcript-config-dirs"
  if [ -n "$_resolved_config_dir" ] && [ "$_resolved_config_dir" != "$_resolved_default_dir" ]; then
    echo ""
    echo "TIP: this profile's CLAUDE_CONFIG_DIR ($_resolved_config_dir) differs from"
    echo "     the default ~/.claude ($_resolved_default_dir). transcript-analysis.py's"
    echo "     multi-account corpus union reads declared roots from"
    echo "     ~/.claude/transcript-config-dirs only, never from this profile's own"
    echo "     config dir — add this profile there, from the default profile, to"
    echo "     include it in the union. See docs/transcript-analysis.md's 'Corpus"
    echo "     scope: the declared-roots file' section."
    # Only a diverged profile has something to add to the roots file; a
    # non-diverged (default) profile gets no nudge here even when the file is
    # also absent there, since a single-account machine has nothing to add.
    if [ ! -e "$roots_file" ]; then
      echo "     ~/.claude/transcript-config-dirs doesn't exist on this machine yet."
    fi
  fi

  if [ -L "$HOME/.claude" ]; then
    echo ""
    echo "TIP: ~/.claude is a symlink to $(readlink "$HOME/.claude") — creating"
    echo "     ~/.claude/transcript-config-dirs writes into that resolved location,"
    echo "     not a separate directory."
  fi

  if [ -e "$roots_file" ] && ! grep -Evq '^[[:space:]]*(#|$)' "$roots_file" 2>/dev/null; then
    echo ""
    echo "WARNING: ~/.claude/transcript-config-dirs exists but contains no usable"
    echo "         entries (only comments or blank lines). Either populate it or"
    echo "         delete it — an empty file is the confusing state."
  fi
}
# INSTALL_TEST_FIXTURE: transcript-config-dirs — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: local-bin-path — start
# Whether $1 already has a non-comment line mentioning $2 — excludes
# comment-only lines (same reason check_private_projects_file above excludes
# comment/blank lines) so a stale "# TODO: add ~/.local/bin" note doesn't
# false-positive as already-configured.
_file_has_active_reference() {
  local file="$1" needle="$2"
  [ -f "$file" ] && grep -v '^[[:space:]]*#' -- "$file" 2>/dev/null | grep -Fq -- "$needle"
}

# Shared by every failure branch below: undoes the just-written append,
# preferring the pre-append backup over a bare rm so a first-run failure
# (no backup exists yet) doesn't leave a half-written rc file behind either.
_undo_local_bin_append() {
  local rc_file="$1" backup="$2" message="$3"
  if [ -n "$backup" ]; then
    mv -- "$backup" "$rc_file"
    echo "[install] warning: $message; restored from backup." >&2
  else
    rm -f -- "$rc_file"
    echo "[install] warning: $message; removed the file." >&2
  fi
}

ensure_local_bin_on_path() {
  # shellcheck disable=SC2016 # single-quoted deliberately — $HOME must stay
  # unexpanded here so it is evaluated when the rc file is later sourced, not
  # when install.sh runs.
  local export_line='export PATH="$HOME/.local/bin:$PATH"'
  local shell_name rc_file resolved companion backup

  for shell_name in zsh bash; do
    rc_file="$HOME/.${shell_name}rc"

    if [ -L "$rc_file" ]; then
      # BSD readlink -f (macOS) can print a partial path to stdout AND exit
      # non-zero for a dangling symlink, unlike GNU readlink — check the exit
      # status of the assignment itself rather than trusting a non-empty
      # capture, so a dangling link falls back to the symlink's own path
      # instead of keeping BSD's partial garbage.
      if ! resolved="$(readlink -f -- "$rc_file" 2>/dev/null)"; then
        resolved="$rc_file"
      fi
      companion="${rc_file}.local"
      # Direct-match checked before the companion heuristic: a resolved
      # target that already has ~/.local/bin needs nothing further,
      # regardless of whether it also happens to mention the companion's
      # basename (e.g. in a comment).
      if _file_has_active_reference "$resolved" '.local/bin'; then
        echo "  ✓ $HOME/.${shell_name}rc already has ~/.local/bin on PATH (via $resolved)"
        continue
      elif [ ! -L "$companion" ] && _file_has_active_reference "$resolved" "$(basename "$companion")"; then
        rc_file="$companion"
        echo "  → managing ~/.local/bin PATH setup in $companion (sourced by $HOME/.${shell_name}rc)"
      else
        echo "[install] warning: $HOME/.${shell_name}rc is a symlink to $resolved — not writing PATH setup through it. Add '$export_line' to whichever file manages your $shell_name startup, then restart your shell." >&2
        continue
      fi
    fi

    if _file_has_active_reference "$rc_file" '.local/bin'; then
      echo "  ✓ $rc_file already has ~/.local/bin on PATH"
      continue
    fi

    command -v "$shell_name" >/dev/null 2>&1 || continue

    backup=""
    if [ -f "$rc_file" ]; then
      backup="${rc_file}.bak.$(date +%Y%m%d%H%M%S)"
      if ! cp -- "$rc_file" "$backup"; then
        echo "[install] warning: could not back up $rc_file; skipping PATH setup for it" >&2
        continue
      fi
    fi

    # Negating a redirected `{ }` group directly (`if ! { ...; } >> file`)
    # does not propagate the group's own redirect-open failure on bash —
    # verified on both macOS system bash 3.2 and bash 5.3. Using the
    # un-negated group as the if-condition itself detects the failure
    # correctly and is still exempt from `set -e` as an if-condition.
    if {
      printf '\n# BEGIN claude-config: ensure ~/.local/bin on PATH\n'
      printf '%s\n' "$export_line"
      printf '# END claude-config: ensure ~/.local/bin on PATH\n'
    } >> "$rc_file"; then
      :
    else
      _undo_local_bin_append "$rc_file" "$backup" "could not append to $rc_file"
      continue
    fi

    if ! "$shell_name" -n "$rc_file" 2>/dev/null; then
      _undo_local_bin_append "$rc_file" "$backup" "appending to $rc_file produced invalid $shell_name syntax"
      continue
    fi
    if [ -n "$backup" ]; then
      rm -f -- "$backup"
    fi
  done
}
# INSTALL_TEST_FIXTURE: local-bin-path — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: contributor-intent-prompt — start
_print_default_contributor_hint() {
  echo ""
  echo "Done. Optional (contributors): run the hook test suite:"
  echo "  ./install-dev.sh   # creates .venv from requirements-dev.txt"
  echo "  .venv/bin/pytest claude/.claude/"
}

# Caller must check `[ -t 0 ]` before invoking this — it has no TTY guard of
# its own and will block on `read` forever against an open, never-closed
# stdin. prompt_contributor_intent below is the only sanctioned caller.
_prompt_contributor_intent() {
  local answer
  read -r -p "Planning to contribute code or tests to claude-config? [y/N] " answer || answer=""
  case "$answer" in
    [Yy]*)
      echo ""
      echo "Contributor setup requires ~/.claude/private-projects.md to exist first"
      echo "(see docs/private-project-redaction.md \"Opt-in: enable the blocklist\"),"
      echo "then:"
      echo "  ./install-dev.sh   # creates .venv from requirements-dev.txt"
      echo "  .venv/bin/pytest claude/.claude/"
      ;;
    *) _print_default_contributor_hint ;;
  esac
}

prompt_contributor_intent() {
  if [ ! -t 0 ]; then
    _print_default_contributor_hint
    return 0
  fi
  _prompt_contributor_intent
}
# INSTALL_TEST_FIXTURE: contributor-intent-prompt — end

if ! command -v timeout >/dev/null 2>&1 && ! command -v gtimeout >/dev/null 2>&1; then
  # shellcheck disable=SC2016 # single-quoted for literal display text — the
  # backtick-quoted tokens are markdown-style formatting, not command
  # substitution; there is no shell expansion intended in either message.
  printf '[install] warning: GNU coreutils `timeout` not in PATH; guard hooks will run jq and git checks (e.g. the agent-reviews/ ignore-state check) without timeout protection.\n' >&2
  # shellcheck disable=SC2016 # single-quoted for literal display text — the
  # backtick-quoted tokens are markdown-style formatting, not command
  # substitution; there is no shell expansion intended in this message.
  printf '[install] hint: install via `brew install coreutils` (macOS) or `apt install coreutils` (debian). On macOS, coreutils installs `gtimeout` by default — either re-run with `--with-default-names` (older brew) or symlink `gtimeout` to `timeout` in PATH.\n' >&2
fi

check_private_projects_file
check_output_preferences_file
check_transcript_config_dirs
ensure_local_bin_on_path

if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo ""
  echo "NOTE: this python3 lacks ensurepip, so 'python3 -m venv' produces a venv with no pip."
  echo "      On Debian/Ubuntu, install it first: sudo apt install python3.12-venv"
fi

prompt_contributor_intent
