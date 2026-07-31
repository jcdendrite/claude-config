#!/bin/bash
# Canonical source for ~/.claude/scripts/relocate-claude-config.sh (stowed)
# and for ~/.local/bin/relocate-claude-config (installed as a REAL FILE COPY
# by install.sh, not a stow-managed symlink like every other ~/.local/bin/*
# wrapper — this script's whole purpose is to keep working when the exact
# symlink chain it repairs has already failed, and a stow-managed wrapper
# would inherit that same failure).
#
# The single supported command to relocate this claude-config checkout, or
# to repair ~/.claude after it was already moved outside any Claude Code
# session — unstows, moves, and re-stows so every symlink under ~/.claude/
# and ~/.local/bin/ keeps resolving. Deliberately does NOT source _lib.sh or
# otherwise depend on ~/.claude/ being intact: repairing that dependency is
# exactly this script's job. See README "Repo relocation" and docs/scripts.md.
#
# Threat model: <new-path> is semi-trusted, not only a deliberate human
# argument — deny-repo-relocation.sh's own denial message names this script
# as the sanctioned escape hatch, so a prompt-injected agent could be
# steered into invoking it with an attacker-influenced destination.
# Destination validation (validate_destination_common below) is where this
# script's actual security investment goes: canonicalize <new-path>'s
# parent, refuse an already-existing or dangling-symlink destination,
# refuse a destination outside $HOME without an explicit opt-in, and pass
# `--` before every positional path argument handed to mv/stow/install so a
# dash-prefixed path can never be parsed as a flag.
#
# Usage:
#   relocate-claude-config <new-path>
#   relocate-claude-config --repair <new-path>
#   relocate-claude-config [--repair] --allow-outside-home <new-path>
#
# Exit codes:
#   0  success
#   1  a precondition failed (bad current-repo state, failed validation,
#      claude-config not found)
#   2  bad arguments

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
MANIFEST="$HOME/.claude-config-source"
BACKUP_DIR="$HOME/.claude-config-relocate-backup"

usage() {
  cat >&2 <<EOF
Usage:
  $SCRIPT_NAME <new-path>              Relocate this checkout to <new-path>
  $SCRIPT_NAME --repair <new-path>     Repair ~/.claude after an out-of-band move to <new-path>

Flags:
  --allow-outside-home   Permit a destination outside \$HOME (either mode)
  --                     End of flags; everything after is the destination
                         path verbatim (needed for a dash-prefixed path)
EOF
}

err() {
  printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
}

# ---------------------------------------------------------------------------
# Small, independently testable helper functions. Kept separate from the
# stow/claude/mv call-outs below so each can be sourced and exercised
# directly (mirrors _lib.sh's own testing convention).
# ---------------------------------------------------------------------------

# True iff ~/.claude is itself a symlink — the legacy tree-folded form an
# older stow run (predating install.sh's mkdir -p guard) can leave behind,
# where the whole ~/.claude directory is one symlink into
# <repo>/claude/.claude, rather than a real directory whose individual
# entries are symlinks.
claude_is_tree_folded() {
  [ -L "$HOME/.claude" ]
}

# Canary check: does $1 look like a legitimate claude-config checkout?
repo_looks_legitimate() {
  [ -f "$1/.claude-plugin/marketplace.json" ]
}

# Print the single-line repo path recorded in the manifest, or fail (return
# 1, no stdout) when the manifest is absent, empty, or unreadable.
read_manifest() {
  [ -f "$MANIFEST" ] || return 1
  local value
  value=$(head -n 1 -- "$MANIFEST" 2>/dev/null | tr -d '[:space:]') || value=""
  [ -n "$value" ] || return 1
  printf '%s' "$value"
}

write_manifest() {
  printf '%s\n' "$1" > "$MANIFEST"
}

# Canonicalize $1 to an absolute, symlink-resolved path, printing nothing
# and returning 1 on failure. Same readlink -f + cd/pwd -P fallback pattern
# as deny-repo-relocation.sh's _relocation_resolve_source; duplicated rather
# than sourced from _lib.sh per this script's header (must keep working even
# when ~/.claude is broken).
_readlink_f() {
  local target="$1" resolved
  if command -v readlink >/dev/null 2>&1; then
    resolved=$(readlink -f -- "$target" 2>/dev/null) || return 1
  else
    resolved=$(cd -- "$(dirname -- "$target")" 2>/dev/null \
      && printf '%s/%s' "$(pwd -P)" "$(basename -- "$target")") || return 1
  fi
  [ -n "$resolved" ] || return 1
  printf '%s' "$resolved"
}

# Print the canonicalized form of $1, or $1 itself unchanged when it cannot
# be resolved (e.g. it no longer exists) — comparison callers treat a
# resolution failure as "not equal to anything real" naturally, with no
# separate unresolved state to handle.
canonicalize_for_comparison() {
  local resolved
  resolved=$(_readlink_f "$1") || resolved=""
  if [ -n "$resolved" ]; then
    printf '%s' "$resolved"
  else
    printf '%s' "$1"
  fi
}

# Resolve the LIVE ~/.claude symlink target, handling both the fresh
# per-entry-symlink form stow leaves today and the legacy tree-folded form.
# Prints the resolved repo dir, or fails (return 1, no stdout) when neither
# form yields a usable, existing directory. Shared by resolve_current_repo_dir
# below as both its cross-validation signal and its own fallback candidate.
_resolve_live_claude_repo_dir() {
  local candidate

  if claude_is_tree_folded; then
    candidate=$(_readlink_f "$HOME/.claude") || candidate=""
    candidate="${candidate%/claude/.claude}"
    if [ -n "$candidate" ] && [ -d "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
    return 1
  fi

  if [ -d "$HOME/.claude" ] && [ -L "$HOME/.claude/hooks" ]; then
    candidate=$(_readlink_f "$HOME/.claude/hooks") || candidate=""
    candidate="${candidate%/claude/.claude/hooks}"
    if [ -n "$candidate" ] && [ -d "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  fi

  return 1
}

# Resolve the current claude-config checkout's absolute path. Tries the
# manifest first, cross-validated against a LIVE ~/.claude symlink
# resolution whenever one is available. The manifest alone is not a
# trustworthy root of trust — it's a bare dotfile at $HOME, outside any git
# repo and unrestricted by this repo's hooks, so a prompt-injected agent
# could point it at an attacker-built directory. Requiring agreement with
# the live symlink closes that: an attacker would also need to control the
# live symlink target. No live probe available (e.g. ~/.claude isn't
# stow-managed yet) is not itself a mismatch — the manifest is used alone.
#
# Return codes:
#   0  resolved (stdout: the repo path)
#   1  neither source available — no message printed, caller reports its own
#   2  both sources available but disagree — fails closed; this function has
#      already printed the explanation via err(), caller should not also
#      print its generic "could not locate" message
#
# "Live" is load-bearing: only the primary (non-repair) mode calls this,
# where symlinks are expected to still resolve; --repair mode never does,
# since the old location is already gone by definition.
resolve_current_repo_dir() {
  local manifest_candidate="" live_candidate=""

  if manifest_candidate=$(read_manifest) && [ -d "$manifest_candidate" ]; then
    :
  else
    manifest_candidate=""
  fi

  live_candidate=$(_resolve_live_claude_repo_dir) || live_candidate=""

  if [ -n "$manifest_candidate" ] && [ -n "$live_candidate" ]; then
    local manifest_real live_real
    manifest_real=$(canonicalize_for_comparison "$manifest_candidate")
    live_real=$(canonicalize_for_comparison "$live_candidate")
    if [ "$manifest_real" != "$live_real" ]; then
      err "$MANIFEST records '$manifest_candidate', which disagrees with the live ~/.claude symlink target '$live_candidate' — refusing to trust either one over the other. If $MANIFEST is stale, fix or remove it; if ~/.claude itself may have been tampered with, investigate before re-running."
      return 2
    fi
    printf '%s' "$manifest_candidate"
    return 0
  fi

  if [ -n "$manifest_candidate" ]; then
    printf '%s' "$manifest_candidate"
    return 0
  fi

  if [ -n "$live_candidate" ]; then
    printf '%s' "$live_candidate"
    return 0
  fi

  return 1
}

# Checks shared by both modes: <new-path> is not itself a dangling symlink,
# its parent directory exists, and (absent --allow-outside-home) it resolves
# under $HOME. Existence of <new-path> ITSELF is a mode-specific question —
# primary mode requires it to NOT yet exist (mv creates it); --repair mode
# requires it to already exist as the already-relocated checkout, verified
# separately by the canary check — so that check is not here.
validate_destination_common() {
  local new_path="$1"

  if [ -L "$new_path" ] && [ ! -e "$new_path" ]; then
    err "destination '$new_path' is a dangling symlink — remove it first or choose a different destination"
    return 1
  fi

  local parent parent_real
  parent="$(dirname -- "$new_path")"
  parent_real=$(_readlink_f "$parent") || parent_real=""
  if [ -z "$parent_real" ]; then
    err "destination's parent directory '$parent' does not exist or is unreachable — create it first"
    return 1
  fi

  # Resolved separately from parent_real above: --repair mode allows
  # new_path to already exist, and a LIVE symlink there (unlike the dangling
  # case above) would pass both the dangling check and a parent-only outside-
  # $HOME check while still pointing somewhere the parent check never sees.
  local new_path_real=""
  if [ -e "$new_path" ]; then
    new_path_real=$(_readlink_f "$new_path") || new_path_real=""
  fi

  if ! $ALLOW_OUTSIDE_HOME; then
    local home_real
    home_real=$(_readlink_f "$HOME") || home_real="$HOME"

    case "$parent_real" in
      "$home_real"|"$home_real"/*) ;;
      *)
        err "destination '$new_path' resolves outside \$HOME ('$home_real') — pass --allow-outside-home to permit this deliberately"
        return 1
        ;;
    esac

    if [ -n "$new_path_real" ]; then
      case "$new_path_real" in
        "$home_real"|"$home_real"/*) ;;
        *)
          err "destination '$new_path' is a symlink resolving outside \$HOME ('$home_real') to '$new_path_real' — pass --allow-outside-home to permit this deliberately"
          return 1
          ;;
      esac
    fi
  fi
}

validate_destination_for_relocate() {
  local new_path="$1"
  if [ -e "$new_path" ]; then
    err "destination '$new_path' already exists — refusing to overwrite it"
    return 1
  fi
  validate_destination_common "$new_path"
}

validate_destination_for_repair() {
  validate_destination_common "$1"
}

# Refuse a pre-planted symlink at the fixed, predictable BACKUP_DIR path —
# quarantined entries must land in a real directory this script creates and
# the user can find, not wherever a symlink there happens to already point.
_ensure_backup_dir_is_not_a_symlink() {
  if [ -L "$BACKUP_DIR" ]; then
    err "'$BACKUP_DIR' already exists as a symlink — refusing to quarantine into it. Remove it or replace it with a real directory first."
    exit 1
  fi
}

# Quarantine-move (never delete) every symlink directly inside $1 whose
# target does not exist; a live symlink or real file/directory is left
# untouched. Quarantine instead of delete because a dangling-symlink check
# alone can false-positive on a target that's merely temporarily
# unreachable (unmounted volume, offline share, mid-sync cloud path), not
# genuinely broken — quarantining keeps a wrongly-triggered repair
# reversible. Echoes true/false on stdout (not a bare return status) so the
# caller's `&&`-chained bookkeeping reads cleanly regardless of shell option
# state.
_quarantine_dangling_children() {
  local dir="$1" found=false
  _ensure_backup_dir_is_not_a_symlink
  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local entry
  for entry in "$dir"/*; do
    if [ -L "$entry" ] && [ ! -e "$entry" ]; then
      mkdir -p -- "$BACKUP_DIR"
      echo "[relocate-claude-config] quarantining dangling $entry -> $BACKUP_DIR/$(basename -- "$entry").$$"
      mv -- "$entry" "$BACKUP_DIR/$(basename -- "$entry").$$"
      found=true
    fi
  done
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi
  $found
}

# --repair only. Quarantine-moves every dangling symlink under BOTH
# ~/.claude (handling both the tree-folded and per-entry forms) AND
# ~/.local/bin — `stow --adopt` refuses outright when a dangling entry is in
# its way, so leaving ~/.local/bin unquarantined would make --repair's own
# re-stow step fail. No tree-folded form applies to ~/.local/bin
# (install.sh's mkdir -p always precedes stow), so only the per-entry case
# is relevant there.
quarantine_dangling_claude_entries() {
  local quarantined_any=false
  _ensure_backup_dir_is_not_a_symlink

  if claude_is_tree_folded; then
    if [ ! -e "$HOME/.claude" ]; then
      mkdir -p -- "$BACKUP_DIR"
      echo "[relocate-claude-config] quarantining dangling ~/.claude -> $BACKUP_DIR/.claude.$$"
      mv -- "$HOME/.claude" "$BACKUP_DIR/.claude.$$"
      quarantined_any=true
    fi
  elif [ -d "$HOME/.claude" ]; then
    _quarantine_dangling_children "$HOME/.claude" && quarantined_any=true
  fi

  if [ -d "$HOME/.local/bin" ]; then
    _quarantine_dangling_children "$HOME/.local/bin" && quarantined_any=true
  fi

  if ! $quarantined_any; then
    echo "[relocate-claude-config] no dangling stow-managed entries found to quarantine"
  fi
}

# Determine the marketplace's current registration state for claude-config
# by comparing the canonicalized recorded .path (from `claude plugin
# marketplace list --json` — not .repo, which is github-source-only) against
# the canonicalized new repo path: no-op if already correct, remove+add if
# registered under a different path, add if not registered at all — three
# distinct idempotent states, not a blind remove-then-add.
sync_marketplace_registration() {
  local new_repo_path="$1"

  if ! command -v claude >/dev/null 2>&1; then
    err "'claude' CLI not found on PATH — skipping marketplace registration sync. Once it's available, run: claude plugin marketplace add \"$new_repo_path\" --scope user"
    return 0
  fi

  local new_repo_real
  new_repo_real=$(canonicalize_for_comparison "$new_repo_path")

  local recorded_path
  recorded_path=$(claude plugin marketplace list --json 2>/dev/null \
    | jq -r '.[] | select(.name == "claude-config") | .path // empty' 2>/dev/null) || recorded_path=""

  if [ -z "$recorded_path" ]; then
    echo "[relocate-claude-config] registering claude-config marketplace at $new_repo_real"
    if ! claude plugin marketplace add --scope user -- "$new_repo_real"; then
      err "marketplace registration failed adding '$new_repo_real' — the relocation itself still succeeded. Run manually once resolved: claude plugin marketplace add \"$new_repo_real\" --scope user"
    fi
    return 0
  fi

  local recorded_real
  recorded_real=$(canonicalize_for_comparison "$recorded_path")

  if [ "$recorded_real" = "$new_repo_real" ]; then
    echo "[relocate-claude-config] claude-config marketplace already registered at the correct path"
    return 0
  fi

  echo "[relocate-claude-config] re-registering claude-config marketplace: $recorded_path -> $new_repo_real"
  if ! claude plugin marketplace remove claude-config; then
    err "marketplace re-registration failed removing the stale entry at '$recorded_path' — the relocation itself still succeeded. Run manually once resolved: claude plugin marketplace remove claude-config && claude plugin marketplace add \"$new_repo_real\" --scope user"
    return 0
  fi
  if ! claude plugin marketplace add --scope user -- "$new_repo_real"; then
    err "marketplace re-registration failed adding '$new_repo_real' after removing the stale entry — the relocation itself still succeeded. Run manually: claude plugin marketplace add \"$new_repo_real\" --scope user"
  fi
}

finish_relocation() {
  local new_path="$1"
  sync_marketplace_registration "$new_path"
  write_manifest "$new_path"
  install -m 755 -- "$new_path/claude/.claude/scripts/relocate-claude-config.sh" "$HOME/.local/bin/relocate-claude-config"
}

relocate_repo() {
  local new_path="$1"

  validate_destination_for_relocate "$new_path" || exit 1

  local current_repo
  if current_repo=$(resolve_current_repo_dir); then
    :
  else
    local resolve_status=$?
    # resolve_status 2: resolve_current_repo_dir already printed the
    # specific mismatch explanation via err() — printing the generic
    # "could not locate" message on top would be misleading (the checkout
    # WAS located, twice, disagreeingly).
    if [ "$resolve_status" -ne 2 ]; then
      err "could not locate the current claude-config checkout ($MANIFEST is missing or stale, and no live ~/.claude symlink was found). Nothing to relocate — if the checkout has already been moved outside a Claude Code session, use '$SCRIPT_NAME --repair <new-path>' instead."
    fi
    exit 1
  fi

  if ! repo_looks_legitimate "$current_repo"; then
    err "'$current_repo' does not look like a claude-config checkout (missing .claude-plugin/marketplace.json) — refusing to unstow it."
    exit 1
  fi

  echo "[relocate-claude-config] unstowing $current_repo"
  ( cd -- "$current_repo" && stow -D -t "$HOME" claude )

  echo "[relocate-claude-config] moving $current_repo -> $new_path"
  if ! mv -- "$current_repo" "$new_path"; then
    err "mv failed while relocating '$current_repo' -> '$new_path' — restoring the previous stow state at '$current_repo' so ~/.claude and ~/.local/bin keep working. Diagnose the failure (disk space, permissions, a cross-device rename) and re-run once resolved."
    mkdir -p "$HOME/.claude" "$HOME/.local/bin"
    ( cd -- "$current_repo" && stow -v --adopt -t "$HOME" claude )
    exit 1
  fi

  mkdir -p "$HOME/.claude" "$HOME/.local/bin"
  echo "[relocate-claude-config] re-stowing at $new_path"
  ( cd -- "$new_path" && stow -v --adopt -t "$HOME" claude )

  finish_relocation "$new_path"

  cat <<EOF

Done. This checkout now lives at: $new_path

Reminders:
  - Other open shells or Claude Code sessions still have the OLD path as
    their working directory — reopen them, or 'cd $new_path' manually.
  - Do not run this command while other Claude Code sessions are active:
    every hook under ~/.claude/hooks/ is briefly absent between the unstow
    and re-stow steps above, and a concurrent session's tool call in that
    window gets a hard deny (fails closed, but visibly disruptive).
EOF
}

repair_repo() {
  local new_path="$1"

  validate_destination_for_repair "$new_path" || exit 1

  if ! repo_looks_legitimate "$new_path"; then
    err "'$new_path' does not look like a claude-config checkout (missing .claude-plugin/marketplace.json) — refusing to repair ~/.claude against it. Move the real checkout there first, or point --repair at its actual location."
    exit 1
  fi

  quarantine_dangling_claude_entries

  mkdir -p "$HOME/.claude" "$HOME/.local/bin"
  echo "[relocate-claude-config] stowing at $new_path"
  ( cd -- "$new_path" && stow -v --adopt -t "$HOME" claude )

  finish_relocation "$new_path"

  cat <<EOF

Done. ~/.claude and ~/.local/bin now point at: $new_path
EOF
}

main() {
  REPAIR_MODE=false
  ALLOW_OUTSIDE_HOME=false
  NEW_PATH=""
  local end_of_flags=false

  while [ "$#" -gt 0 ]; do
    if $end_of_flags; then
      [ -z "$NEW_PATH" ] && NEW_PATH="$1"
      shift
      continue
    fi
    case "$1" in
      --repair) REPAIR_MODE=true; shift ;;
      --allow-outside-home) ALLOW_OUTSIDE_HOME=true; shift ;;
      --) end_of_flags=true; shift ;;
      -h|--help) usage; exit 0 ;;
      -*)
        err "unknown flag '$1'"
        usage
        exit 2
        ;;
      *)
        [ -z "$NEW_PATH" ] && NEW_PATH="$1"
        shift
        ;;
    esac
  done

  if [ -z "$NEW_PATH" ]; then
    usage
    exit 2
  fi

  if $REPAIR_MODE; then
    repair_repo "$NEW_PATH"
  else
    relocate_repo "$NEW_PATH"
  fi
}

# Only run when executed, not when sourced — lets tests source this file to
# exercise the helper functions above directly, the same way _lib.sh is
# tested, without also triggering argument parsing and the live mv/stow/
# claude call-outs.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
