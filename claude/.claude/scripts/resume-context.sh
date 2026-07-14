#!/usr/bin/env bash
# Move a durable /handoff or /brief continuity file out of
# ~/.claude/handoffs/ or ~/.claude/briefs/ into a fresh per-user temp path,
# then launch a new interactive `claude` session with it loaded via
# --append-system-prompt-file. This is the "resume" half of the
# durable-handoff design: the write side lives in the handoff/brief SKILL.md
# files; this script is the mechanical, no-model-judgment consume+load step.
#
# Usage:
#   resume-context.sh <continuity-file-path>
#   resume-context.sh --consume-only <continuity-file-path>
#
# --consume-only performs the move only, without resolving or launching a
# launcher. Used by consume-durable-continuity-file-on-read.sh so the same
# move logic is never duplicated between the explicit-resume path and the
# same-session (`/clear` + manual Read) resume path.
#
# Env overrides (tests only — never touch the real claude binary or the
# real shared /tmp otherwise):
#   RESUME_CONTEXT_LAUNCHER  command to exec instead of `claude`
#   RESUME_CONTEXT_TMPDIR    temp-dir root instead of ${TMPDIR:-/tmp}
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
#   additionally protected at the directory level — the handoff/brief
#   SKILL.md write recipes chmod those directories 700 (owner-only
#   traversal) — but that protection is enforced by the writing skill, not
#   by this script; it only holds if that chmod step actually ran.
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
Usage: resume-context.sh [--consume-only] <continuity-file-path>
EOF
}

CONSUME_ONLY=0
if [ "${1:-}" = "--consume-only" ]; then
  CONSUME_ONLY=1
  shift
fi

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

SRC=$1

if [ ! -f "$SRC" ]; then
  printf 'resume-context.sh: source file not found: %s\n' "$SRC" >&2
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

# mktemp's bare positional TEMPLATE form (no -p/--tmpdir flag) is the base
# invocation documented by both GNU coreutils and BSD/macOS mktemp(1) — no
# GNU-only extension relied on here.
TMPDIR_ROOT="${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}"
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
  exit 0
fi

exec "$LAUNCHER" --append-system-prompt-file "$DEST" "Continue from the handoff/brief file loaded into your system prompt."
