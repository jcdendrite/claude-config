#!/bin/bash
# hook-class: informational
# PostToolUse Read hook: when a /handoff or /brief continuity file under
# ~/.claude/handoffs/ or ~/.claude/briefs/ is read, move it out via
# resume-context.sh --consume-only. This is the same-session resume path:
# the writing session runs /clear and the engineer then types "Read <path>
# and continue" as an ordinary message in the same process, with no new
# `claude` process and no resume-context.sh invocation — that Read tool
# call is the only mechanical trigger available to consume the file, so
# this hook exists to keep the durable directory's steady state near empty
# for that path too (resume-context.sh's own launch mode already handles
# the fresh-process resume path).
#
# Fail-open, never blocks: PostToolUse cannot deny, and a crashed or
# blocked consume must never break the Read call it followed. Any failure
# (missing script, already-consumed source, timeout) is swallowed.
#
# On a successful consume, resume-context.sh's destination is surfaced on
# two channels: a `systemMessage` for the human (shown to the user, not the
# model) and `hookSpecificOutput.additionalContext` for the model (delivered
# next to the tool result) — without the second channel, the agent that just
# had its own file moved out from under it has no way to learn where it
# went. Both emit from the same jq call and share its `-n --arg dest`
# guard; a missing destination, missing jq, or any failure building either
# message emits nothing and falls through to `exit 0`.
#
# Kill-switch: touching ~/.claude/.consume-durable-continuity-disabled
# suppresses this hook entirely, mirroring
# nudge-handoff-near-context-cap.sh's ~/.claude/.handoff-nudge-disabled
# convention. On-by-default; this is the local opt-out lever, since the
# hook (unlike its plugin-scoped precedent, consume-migration-token.sh)
# ships in the globally-stowed claude/.claude/hooks/ and fires for every
# stow user unconditionally.
#
# Timeout: the move, and record_consumed_destination's index append, run
# synchronously inside this PostToolUse call. If $HOME is network-backed
# (NFS/CIFS), a hung mount could otherwise block the triggering Read call
# indefinitely. The index append adds a second, independently configurable
# hang surface: $RESUME_CONTEXT_TMPDIR/$TMPDIR, distinct from $HOME, can
# itself be network-backed, and a hung mount there can block any of the
# extra syscalls that append reaches (mkdir, chmod, find, date, wc, tr), not
# only mv. Wrapped in `timeout ${RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS:-5}`
# when available (falls back to a bare, unguarded call on BSD/macOS systems
# lacking timeout(1) — a latency backstop, not a correctness boundary,
# matching _lib.sh's existing _lib_jq/git_capped precedent; the env override
# exists so tests can inject a short timeout without a real multi-second
# sleep). `timeout` sends its signal only to its direct child (the
# resume-context.sh shell); that shell can be blocked in wait4() on an
# already-forked child — `mv`, or one of record_consumed_destination's own
# children listed above — that is itself stuck in a hung-mount syscall, and
# the signal does not propagate down into that grandchild. Accepted as a
# named, narrow residual: it requires a hung
# network-backed $HOME or tmpdir root, and the visible symptom (the Read
# call returning promptly) already matches this hook's documented goal. Not
# a one-shot cost, though — every hook fire against a still-hung mount
# (repeated Reads while the mount stays stuck) leaks another orphaned child
# process, reparented to init, still holding a file handle against the hung
# mount; this is unbounded process accumulation over a long hung-mount
# window, not a single stray process. No bound is implemented for it — the
# same "requires a hung network-backed $HOME or tmpdir root" scoping
# applies, and per-source-path locking to close it would be meaningfully
# more machinery than this hook's narrow purpose warrants.
#
# Known gaps:
# - Also fires on a plain inspection read of a continuity file (checking
#   whether an old handoff is still relevant), not only genuine resumes —
#   the file is moved out on that read too. Inspecting without consuming
#   needs a shell `cat` via `!` instead of Read. Not worked around: a
#   PostToolUse hook cannot distinguish "this Read was a resume" from
#   "this Read was a peek" from the tool call alone, and adding that
#   distinction would mean tracking session intent, reintroducing the
#   guessing problem this hook exists to avoid.
# - Path match is a case-sensitive glob (matching log-routing-read.sh's
#   approach). A path differing only in case that still resolves to the
#   same file on a case-insensitive filesystem (default macOS APFS) won't
#   match. Low severity: fail-open just skips the consume.
# - Path match is literal, not symlink-resolved. A Read via a path that
#   resolves into ~/.claude/handoffs/ through a symlink but doesn't
#   textually match the glob is a no-op. A symlink placed AT a
#   glob-matching path (rather than merely traversing through one) is a
#   distinct case handled by resume-context.sh itself, which rejects a
#   symlink source outright rather than moving-then-chmodding it.
# - resume-context.sh moves the file, then chmods it, and only prints the
#   destination once both succeed — so a chmod failure leaves the file
#   moved with empty stdout here, and neither output channel reports it.
#   Index append runs before the chmod, so a chmod failure alone doesn't
#   stop the destination from being recorded (docs/scripts.md).
#   Recoverability still requires the index write itself to succeed — it's
#   a separate, best-effort step that can fail silently on its own guards.
#
# Defense-in-depth: filters tool_name and file_path itself; does not rely
# solely on the settings.json matcher condition.
set -uo pipefail

# An unresolvable config dir leaves no kill-switch/continuity-directory/
# script location to check, so this hook fails open (Read proceeds
# unconsumed) rather than guess.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
CONFIG_DIR=$(_lib_config_dir) || exit 0

if [ -f "$CONFIG_DIR/.consume-durable-continuity-disabled" ]; then
  exit 0
fi

# Read stdin directly. PostToolUse does not need a deny response.
# Fail-open on malformed input: an orphaned continuity file is harmless; a
# crashed consume hook that exits non-zero would break the Read tool call.
INPUT=$(cat) || exit 0

TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[ "$TOOL_NAME" = "Read" ] || exit 0

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$FILE_PATH" ] || exit 0

case "$FILE_PATH" in
  "$CONFIG_DIR"/handoffs/*-handoff.md | "$CONFIG_DIR"/briefs/*-task.md) ;;
  *) exit 0 ;;
esac

RESUME_SCRIPT="$CONFIG_DIR/scripts/resume-context.sh"
TIMEOUT_SECONDS="${RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS:-5}"

if command -v timeout >/dev/null 2>&1; then
  DEST=$(timeout "$TIMEOUT_SECONDS" "$RESUME_SCRIPT" --consume-only "$FILE_PATH" 2>/dev/null) || DEST=""
else
  DEST=$("$RESUME_SCRIPT" --consume-only "$FILE_PATH" 2>/dev/null) || DEST=""
fi

if [ -n "$DEST" ] && command -v jq >/dev/null 2>&1; then
  # shellcheck disable=SC2016 # single-quoted on purpose: $dest is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
  _lib_jq -n --arg dest "$DEST" \
    '{
      systemMessage: ("Continuity file moved to " + $dest + ". Reload with: claude --append-system-prompt-file " + $dest),
      hookSpecificOutput: {
        hookEventName: "PostToolUse",
        additionalContext: ("The /handoff or /brief continuity file you just read has been consumed: it no longer exists at the path you read, and now lives at " + $dest + ". Its contents are in this tool result.")
      }
    }' \
    2>/dev/null || true
fi

exit 0
