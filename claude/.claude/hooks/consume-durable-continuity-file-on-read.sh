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
# Kill-switch: touching ~/.claude/.consume-durable-continuity-disabled
# suppresses this hook entirely, mirroring
# nudge-handoff-near-context-cap.sh's ~/.claude/.handoff-nudge-disabled
# convention. On-by-default; this is the local opt-out lever, since the
# hook (unlike its plugin-scoped precedent, consume-migration-token.sh)
# ships in the globally-stowed claude/.claude/hooks/ and fires for every
# stow user unconditionally.
#
# Timeout: the move runs synchronously inside this PostToolUse call. If
# $HOME is network-backed (NFS/CIFS), a hung mount could otherwise block
# the triggering Read call indefinitely. Wrapped in `timeout
# ${RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS:-5}` when available (falls back to
# a bare, unguarded call on BSD/macOS systems lacking timeout(1) — a
# latency backstop, not a correctness boundary, matching _lib.sh's existing
# _lib_jq/git_capped precedent; the env override exists so tests can inject
# a short timeout without a real multi-second sleep). `timeout` sends its
# signal only to its direct child (the resume-context.sh shell); that shell
# can be blocked in wait4() on an already-forked `mv` child that is itself
# stuck in a hung-mount rename/copy syscall, and the signal does not
# propagate down into that grandchild `mv`. Accepted as a named, narrow
# residual: it requires a hung network-backed $HOME, and the visible
# symptom (the Read call returning promptly) already matches this hook's
# documented goal. Not a one-shot cost, though — every hook fire against a
# still-hung mount (repeated Reads while the mount stays stuck) leaks
# another orphaned `mv`, reparented to init, each still holding a file
# handle against the hung mount; this is unbounded process accumulation
# over a long hung-mount window, not a single stray process. No bound is
# implemented for it — the same "requires a hung network-backed $HOME"
# scoping applies, and per-source-path locking to close it would be
# meaningfully more machinery than this hook's narrow purpose warrants.
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
#
# Defense-in-depth: filters tool_name and file_path itself; does not rely
# solely on the settings.json matcher condition.
set -uo pipefail

if [ -f "$HOME/.claude/.consume-durable-continuity-disabled" ]; then
  exit 0
fi

# Read stdin directly. PostToolUse does not need a deny response.
# Fail-open on malformed input: an orphaned continuity file is harmless; a
# crashed consume hook that exits non-zero would break the Read tool call.
INPUT=$(cat) || exit 0

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[ "$TOOL_NAME" = "Read" ] || exit 0

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$FILE_PATH" ] || exit 0

case "$FILE_PATH" in
  "$HOME"/.claude/handoffs/*-handoff.md | "$HOME"/.claude/briefs/*-task.md) ;;
  *) exit 0 ;;
esac

RESUME_SCRIPT="$HOME/.claude/scripts/resume-context.sh"
TIMEOUT_SECONDS="${RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS:-5}"

if command -v timeout >/dev/null 2>&1; then
  timeout "$TIMEOUT_SECONDS" "$RESUME_SCRIPT" --consume-only "$FILE_PATH" >/dev/null 2>&1 || true
else
  "$RESUME_SCRIPT" --consume-only "$FILE_PATH" >/dev/null 2>&1 || true
fi

exit 0
