#!/bin/bash
# hook-class: informational
# PostToolUse Write|Edit|MultiEdit hook: chmod any file the tool wrote or
# edited under ~/.claude/handoffs/ or ~/.claude/briefs/ to 0600 (owner
# read/write only), so a durable /handoff or /brief continuity file lands
# owner-only mechanically rather than depending on the skill's own
# touch/chmod recipe landing against the exact path later written by a
# separate Write call — the substitution of the same slug placeholder into
# two independent commands is where that recipe silently drifted in
# practice. The 0700 directory mode — set by the skill's own mkdir/chmod
# recipe, which this hook cannot replace since it can't create the
# directory — remains the control that actually blocks another local
# account from resolving the path at all; this hook hardens the file mode
# as defense-in-depth for when a file leaves the directory (rsync, tarball,
# backup, cp).
#
# Coverage boundary: only files written by the Write, Edit, or MultiEdit
# tool trigger this hook. A file created by the Bash tool (cat >, cp,
# sed -i, a script) or by any non-Claude writer (an editor, a git GUI,
# another terminal) never produces a matching tool call and is a
# structural no-op for this hook — it lands at the umask default and stays
# there.
#
# Fail-open, never blocks: PostToolUse cannot deny, and a crashed or
# no-op hook must never break the Write/Edit/MultiEdit call it followed.
#
# No kill-switch: unlike the sibling consume-on-read hook (which moves the
# user's file and can be surprising), tightening the mode of a file inside
# the user's own 0700 directory has no legitimate failure mode to escape,
# and a kill-switch on a hardening control is an anti-feature.
#
# Known gaps:
# - Bash-authored or non-Claude-authored files in either directory are
#   never chmodded by this hook (see Coverage boundary above).
# - Path match is a case-sensitive prefix comparison (matching
#   consume-durable-continuity-file-on-read.sh's approach). A path
#   differing only in case that still resolves to the same file on a
#   case-insensitive filesystem (default macOS APFS) won't match.
# - The match compares realpath's resolved output against the literal,
#   unresolved "$HOME/.claude/handoffs"/"$HOME/.claude/briefs" prefix. If
#   $HOME itself (or an ancestor of .claude/) is a symlink, the resolved
#   path won't textually match the literal prefix and the hook silently
#   skips the file — fail-open, not a crash.
# - The cheap pre-filter requires the *unresolved* path to be textually
#   prefixed too, so a path that only resolves into either directory after
#   traversal (e.g. via ".." segments or a symlinked parent) is skipped
#   rather than hardened. Fail-open in the same direction as the gap above,
#   and tool calls in practice carry already-absolute, already-clean paths.
# - Between realpath resolving the path and chmod acting on it, the resolved
#   path could in principle be swapped. Out of the declared threat model:
#   the 0700 directory admits only the owner, so any actor able to win that
#   race can already chmod the file directly.
# - Sub-second post-write window: the file exists at the umask default
#   between the tool's write and this hook's chmod call. Irrelevant while
#   the containing directory is 0700, but real and worth naming.
#
# Defense-in-depth: filters tool_name and file_path itself; does not rely
# solely on the settings.json matcher condition.
set -uo pipefail

# Read stdin directly. PostToolUse does not need a deny response.
# Fail-open on malformed input: a file left at the umask default is
# harmless (the 0700 directory still blocks other accounts); a crashed
# hook that exits non-zero would break the tool call it followed.
INPUT=$(cat) || exit 0

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
case "$TOOL_NAME" in
  Write | Edit | MultiEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$FILE_PATH" ] || exit 0

# Cheap textual pre-filter before spending a realpath subprocess. This hook
# fires on every Write/Edit/MultiEdit on the machine, and the overwhelming
# majority target neither directory; without this, each one pays a fork.
# Necessary condition only — the authoritative check is the post-realpath
# match below, which is what rejects a path that is merely textually
# prefixed but resolves elsewhere.
case "$FILE_PATH" in
  "$HOME"/.claude/handoffs/* | "$HOME"/.claude/briefs/*) ;;
  *) exit 0 ;;
esac

RESOLVED=$(realpath "$FILE_PATH" 2>/dev/null) || exit 0

case "$RESOLVED" in
  "$HOME"/.claude/handoffs/* | "$HOME"/.claude/briefs/*) ;;
  *) exit 0 ;;
esac

# Skip symlinks: chmod dereferences a symlink, so chmodding a symlink
# planted at a matching path would narrow permissions on an arbitrary
# target instead. Checked against the literal FILE_PATH (not RESOLVED,
# which realpath has already fully dereferenced and so never reports as a
# symlink) — this is the only point at which the check can be meaningful.
[ -f "$FILE_PATH" ] || exit 0
[ ! -L "$FILE_PATH" ] || exit 0

chmod 600 "$RESOLVED" 2>/dev/null || true

exit 0
