#!/usr/bin/env bash
# Move a durable /handoff or /brief continuity file out of
# ~/.claude/handoffs/ or ~/.claude/briefs/ into a fresh per-user temp path,
# then launch a new interactive `claude` session with it loaded via
# --append-system-prompt-file. This is the "resume" half of the
# durable-handoff design: the write side lives in the handoff/brief SKILL.md
# files; this script is the mechanical, no-model-judgment consume+load step.
#
# Usage:
#   resume-context.sh [--cwd <dir>] <continuity-file-path>
#   resume-context.sh --consume-only <continuity-file-path>
#
# --consume-only performs the move only, without resolving or launching a
# launcher. Used by consume-durable-continuity-file-on-read.sh so the same
# move logic is never duplicated between the explicit-resume path and the
# same-session (`/clear` + manual Read) resume path.
#
# --cwd <dir> launches the new session with <dir> as its working directory
# instead of inheriting the invoking shell's. handoff/SKILL.md §7 and
# brief/SKILL.md §7.5 write this flag into the resume command when the
# continuity file names a worktree, so the target directory travels with the
# command itself rather than depending on the invoker also running a
# separate `cd` first — the failure mode this closes is a resume launched
# from the wrong directory (main checkout, `~`, a different worktree)
# silently landing set-session-title-from-branch.sh on the wrong branch or
# no branch at all. Rejected together with --consume-only, since that mode
# never launches a session for a cwd to apply to. Validated as an existing
# directory before any file is moved.
#
# Env overrides:
#   RESUME_CONTEXT_LAUNCHER  command to exec instead of `claude`. Used by tests
#                            to avoid the real claude binary; also usable in a
#                            real shell to front `claude` with a wrapper, e.g.
#                            RESUME_CONTEXT_LAUNCHER=claude-auto resumes in
#                            auto mode.
#   RESUME_CONTEXT_TMPDIR    temp-dir root instead of ${TMPDIR:-/tmp}. Tests
#                            only — never touch the real shared /tmp otherwise.
#
# Destination visibility:
# - Launch mode prints the move and a reload hint to stderr before exec'ing
#   the launcher. This is best-effort UX, not the recovery guarantee: it may
#   not survive `exec` into an alt-screen TUI, and is lost entirely under a
#   piped or `-p` invocation. It's fine either way — a successful exec means
#   you're already resuming, and the not-found branch below is the
#   dependable recovery path if you need to look back later. This line
#   includes the original source path (which can embed the continuity
#   file's slug), so it assumes the invoking terminal/session isn't itself
#   being captured to a shared or lower-trust log (script(1), tmux/terminal
#   logging, CI log capture on a shared runner).
# - Consume-only mode's stdout contract is exactly the destination path and
#   nothing else (a single line, no trailing content) — a downstream
#   PostToolUse hook parses this via command substitution.
# - The not-found branch's hint is a static message pointing at the temp-dir
#   root; it performs no directory listing or file-existence check of its
#   own, so it stays truthful post-reboot (an empty glob then correctly
#   reads as "nothing recoverable").
# - The legacy-location fallback (below) prints the resolved legacy path to
#   stderr on use — same sensitivity class as the not-found hint above.
#
# Known limitations:
# - Shell *aliases* for `claude` are not visible here (aliases aren't
#   inherited by non-interactive scripts) — command -v resolves `claude` on
#   PATH.
# - No atomicity is claimed for the move: crossing filesystems (home disk to
#   a tmpfs /tmp) makes `mv` degrade to copy-then-unlink on EXDEV, and
#   permission/attribute finalization happens at the end of that copy, not
#   the start — a failure partway through can leave the destination with
#   partial content, not necessarily an empty file. No data loss either way
#   (the source is only unlinked after a successful copy), and the explicit
#   `chmod 600` below closes the destination's mode regardless of how much
#   content landed — but do not assume "empty" as the only partial-failure
#   shape.
# - The destination filename uses a fixed, non-descriptive prefix (not the
#   source's basename) specifically so it does not leak the continuity
#   file's slug via `ls` on a shared multi-user machine, where /tmp is often
#   world-traversable (1777) even though the file's own 0600 permissions
#   block content reads. ~/.claude/handoffs/ and ~/.claude/briefs/ are
#   additionally protected at the directory level by install.sh's one-time
#   `chmod 700 "$HOME/.claude"` (see install.sh for scope and the
#   symlink-skip caveat) — enforced at install time, not by this script,
#   and only if that step actually ran.
#   Content exposure is still a strict improvement over the pre-fix
#   /tmp/<slug>-handoff.md, which carried no permission hardening at all.
# - A symlink at a glob-matching path inside the durable directory is
#   rejected outright (see the check below), not moved-then-chmodded: `mv`
#   preserves symlink-ness on a same-filesystem rename, and `chmod` (unlike
#   `mv`) dereferences symlinks by default — chmodding a moved symlink would
#   silently narrow permissions on whatever arbitrary file it points to,
#   not on the continuity file itself.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: resume-context.sh [--cwd <dir>] <continuity-file-path>
       resume-context.sh --consume-only <continuity-file-path>
EOF
}

# Prints the reload command a human would run to load a moved continuity
# file back into a session's system prompt. Kept in one place (rather than
# inlined at each call site) so the reload string can't drift between the
# launch-mode announcement and any future caller. Uses the literal `claude`,
# not $LAUNCHER: this is a hint for a human to run manually, potentially in a
# later shell/session where $RESUME_CONTEXT_LAUNCHER won't be set.
print_recovery_hint() {
  local dest="$1"
  printf 'resume-context.sh: reload with: claude --append-system-prompt-file %s\n' "$dest" >&2
}

CONSUME_ONLY=0
LAUNCH_CWD=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --consume-only)
      CONSUME_ONLY=1
      shift
      ;;
    --cwd)
      if [ "$#" -lt 2 ]; then
        printf 'resume-context.sh: --cwd requires a directory argument\n' >&2
        exit 1
      fi
      LAUNCH_CWD=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      usage
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

SRC=$1

if [ -n "$LAUNCH_CWD" ] && [ "$CONSUME_ONLY" -eq 1 ]; then
  printf 'resume-context.sh: --cwd is not valid with --consume-only (that mode never launches)\n' >&2
  exit 1
fi

if [ -n "$LAUNCH_CWD" ] && [ ! -d "$LAUNCH_CWD" ]; then
  printf 'resume-context.sh: --cwd target is not a directory: %s\n' "$LAUNCH_CWD" >&2
  exit 1
fi

# mktemp's bare positional TEMPLATE form (no -p/--tmpdir flag) is the base
# invocation documented by both GNU coreutils and BSD/macOS mktemp(1) — no
# GNU-only extension relied on here. Hoisted above the not-found check so
# both the not-found hint and the move below share one definition.
TMPDIR_ROOT="${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}"

# Also check the legacy $HOME/.claude path before giving up, mirroring this
# repo's "union, not swap" guard-config fallbacks. Trailing slash stripped
# to match _lib_config_dir's ${VAR%/} convention (_lib.sh:114).
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CONFIG_DIR="${CONFIG_DIR%/}"
if [ ! -f "$SRC" ] && [ "$CONFIG_DIR" != "$HOME/.claude" ]; then
  case "$SRC" in
    "$CONFIG_DIR"/handoffs/*|"$CONFIG_DIR"/briefs/*)
      LEGACY_SRC="$HOME/.claude${SRC#"$CONFIG_DIR"}"
      if [ -f "$LEGACY_SRC" ]; then
        printf 'resume-context.sh: not found under %s; found it at the legacy location instead: %s\n' "$CONFIG_DIR" "$LEGACY_SRC" >&2
        SRC="$LEGACY_SRC"
      fi
      ;;
  esac
fi

if [ ! -f "$SRC" ]; then
  printf 'resume-context.sh: source file not found: %s\n' "$SRC" >&2
  printf 'resume-context.sh: it may already have been consumed — moved copies are at\n' >&2
  printf 'resume-context.sh:   %s/resume-context.* (newest first: ls -t)\n' "$TMPDIR_ROOT" >&2
  printf 'resume-context.sh: those are cleared on reboot; if none remain, it is unrecoverable.\n' >&2
  exit 1
fi

# Reject symlinks outright: continuity files are always plain files written
# directly by the handoff/brief skills, never symlinks. `mv` preserves a
# symlink's identity on a same-filesystem rename, and the later `chmod 600`
# would then dereference it — silently narrowing permissions on whatever
# arbitrary file the symlink points to, not on a continuity file at all.
if [ -L "$SRC" ]; then
  printf 'resume-context.sh: refusing to move a symlink: %s\n' "$SRC" >&2
  exit 1
fi

# Resolve the launcher before the move (launch mode only) so a missing
# launcher is caught before any filesystem side effect.
LAUNCHER=""
if [ "$CONSUME_ONLY" -eq 0 ]; then
  if ! LAUNCHER=$(command -v "${RESUME_CONTEXT_LAUNCHER:-claude}"); then
    printf 'resume-context.sh: launcher not found on PATH: %s\n' "${RESUME_CONTEXT_LAUNCHER:-claude}" >&2
    exit 1
  fi
fi

DEST=$(mktemp "$TMPDIR_ROOT/resume-context.XXXXXX")

if ! mv -- "$SRC" "$DEST"; then
  printf 'resume-context.sh: failed to move %s to %s\n' "$SRC" "$DEST" >&2
  exit 1
fi

# mv/rename(2) replaces DEST's inode with SRC's, so DEST ends up with SRC's
# permissions (whatever the writing skill left them at), not mktemp's 0600
# placeholder mode — verified empirically: a same-filesystem mv silently
# discards mktemp's protection. Re-assert 0600 explicitly rather than relying
# on mktemp's initial mode surviving the move.
if ! chmod 600 "$DEST"; then
  printf 'resume-context.sh: failed to set permissions on %s\n' "$DEST" >&2
  exit 1
fi

if [ "$CONSUME_ONLY" -eq 1 ]; then
  printf '%s\n' "$DEST"
  exit 0
fi

printf 'resume-context.sh: moved %s -> %s\n' "$SRC" "$DEST" >&2
print_recovery_hint "$DEST"

# Applied after the move, not before: SRC/DEST are already resolved (SRC may
# have been relative to the pre-cd cwd), and DEST is always absolute (mktemp
# against $TMPDIR_ROOT), so changing directory here cannot affect either.
if [ -n "$LAUNCH_CWD" ]; then
  cd -- "$LAUNCH_CWD" || {
    printf 'resume-context.sh: failed to cd into %s\n' "$LAUNCH_CWD" >&2
    exit 1
  }
fi

exec "$LAUNCHER" --append-system-prompt-file "$DEST" "Continue from the handoff or brief file loaded into your system prompt. If it contains a task-list resume directive, track its pending and in-progress items from the file (not from memory) as you resume — using your session's task-list tool if one is available, otherwise inline. A missing task-list tool is not a blocker."
